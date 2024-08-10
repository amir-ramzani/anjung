"""Payment application (LLD 6.3 to 6.5). Every path is one transaction with a global idempotency guard,
so a duplicate webhook, a retry or a double click can never credit money twice."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.errors import Conflict, InvalidState, NotFound
from app.core.ids import new_id
from app.domains.billing.common import GATEWAY, SYSTEM, Actor, Item, derive_status
from app.domains.billing.ledger import build_entries, deposit_received_effects
from app.domains.notifications.events import publish
from app.integrations.payments.base import PaymentEvent
from app.repositories import keys
from app.repositories.billing import reindexed
from app.repositories.dynamo import NOT_EXISTS, Delete, Put, TxCancelled, Update
from app.worker.envelope import make_envelope

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 6
WEBHOOK_TTL = timedelta(days=30)


class DuplicatePayment(Exception):
    """The idempotency guard already exists: this money was handled before."""


def payment_item(
    org_id: str, pid: str, fields: dict[str, Any], now: str, *, parked: bool = False
) -> Item:
    inv = fields.get("invoice_id")
    return {
        "PK": keys.unmatched_pk(org_id) if parked or not inv else keys.invoice_pk(org_id, inv),
        "SK": f"PAY#{pid}",
        "GSI1PK": keys.entity_gsi1pk(org_id, "PAY", pid),
        "GSI1SK": keys.META,
        "entity_type": "payment",
        "schema_version": 1,
        "org_id": org_id,
        "id": pid,
        "currency": "MYR",
        "invoice_id": None,
        "lease_id": None,
        "provider": None,
        "gateway_reference": None,
        "gateway_txn_id": None,
        "applied_minor": 0,
        "excess_minor": 0,
        **fields,
        "version": 1,
        "created_at": now,
    }


def _whevt(provider: str, event_id: str, now: Any) -> Put:
    return Put(
        item={
            "PK": keys.webhook_event_pk(provider, event_id),
            "SK": keys.META,
            "entity_type": "webhook_event",
            "ttl": int((now + WEBHOOK_TTL).timestamp()),
        },
        condition=NOT_EXISTS,
    )


def _payref(provider: str, ref: str, org_id: str, pid: str) -> Put:
    return Put(
        item={
            "PK": keys.payref_pk(provider, ref),
            "SK": keys.META,
            "entity_type": "payment_ref",
            "org_id": org_id,
            "payment_id": pid,
        },
        condition=NOT_EXISTS,
    )


def settle(
    c: Container,
    org_id: str,
    invoice_id: str,
    amount: int,
    *,
    method: str,
    received_at: str,
    actor: Actor,
    guards: list[Put],
    audit_action: str,
    payment_id: str | None = None,
    provider: str | None = None,
    gateway_reference: str | None = None,
    gateway_txn_id: str | None = None,
    unmatched: Item | None = None,
    audit_extra: dict[str, Any] | None = None,
    require_unpaid: bool = False,
) -> Item:
    """Apply `amount` to an invoice. Underpayment leaves it partially paid, overpayment becomes lease credit."""
    repo = c.store.billing(org_id)
    pid = payment_id or new_id()
    for _ in range(MAX_ATTEMPTS):
        inv = repo.get_invoice(invoice_id)
        if inv is None:
            raise NotFound()
        if require_unpaid and inv["status"] not in ("open", "partially_paid", "overdue"):
            raise InvalidState("This invoice is not awaiting payment")
        lease = c.store.leases(org_id).get(inv["lease_id"])
        if lease is None:
            raise NotFound()
        now = c.clock.now()
        outstanding = (
            0 if inv["status"] == "void" else max(inv["total_minor"] - inv["paid_minor"], 0)
        )
        applied = min(amount, outstanding)
        excess = amount - applied
        paid = inv["paid_minor"] + applied
        status = (
            inv["status"]
            if inv["status"] == "void"
            else derive_status(inv["status"], inv["total_minor"], paid)
        )
        new_inv = reindexed(
            {
                **inv,
                "paid_minor": paid,
                "status": status,
                "version": inv["version"] + 1,
                "updated_at": iso(now),
            },
            org_id,
        )
        payment = payment_item(
            org_id,
            pid,
            {
                "invoice_id": invoice_id,
                "lease_id": inv["lease_id"],
                "method": method,
                "status": "succeeded",
                "amount_minor": amount,
                "applied_minor": applied,
                "excess_minor": excess,
                "provider": provider,
                "gateway_reference": gateway_reference,
                "gateway_txn_id": gateway_txn_id,
                "received_at": received_at,
                "recorded_by": actor.id,
            },
            iso(now),
        )
        entries: list[dict[str, Any]] = [
            {
                "type": "payment",
                "amount_minor": -amount,
                "invoice_id": invoice_id,
                "payment_id": pid,
                "memo": f"{method} payment",
            }
        ]
        held = lease.get("deposits_held", [])
        if status == "paid" and inv["status"] != "paid" and inv.get("kind") == "deposit":
            more, held = deposit_received_effects(new_inv, lease)
            entries += more
        ledger_ops, balance = build_entries(
            repo, lease, entries, lease["balance_minor"], actor, now
        )
        ops: list[Any] = list(guards)
        if unmatched is not None:
            ops.append(
                Delete(
                    key=(unmatched["PK"], unmatched["SK"]),
                    condition="#s = :u",
                    names={"#s": "status"},
                    values={":u": "unmatched"},
                )
            )
        first = len(ops)
        ops.append(Put(item=payment, condition=NOT_EXISTS))
        ops.append(repo.invoice_replace_op(new_inv, inv["version"]))
        ops.append(
            Update(
                key=(lease["PK"], lease["SK"]),
                expression="SET balance_minor = :bal, credit_minor = :cred, version = :nv, "
                "updated_at = :now, deposits_held = :held",
                condition="#v = :v",
                names={"#v": "version"},
                values={
                    ":bal": balance,
                    ":cred": lease["credit_minor"] + excess,
                    ":nv": lease["version"] + 1,
                    ":v": lease["version"],
                    ":now": iso(now),
                    ":held": held,
                },
            )
        )
        if applied:
            ops.append(
                Put(
                    item={
                        "PK": keys.invoice_pk(org_id, invoice_id),
                        "SK": f"ALLOC#{new_id()}",
                        "entity_type": "allocation",
                        "org_id": org_id,
                        "invoice_id": invoice_id,
                        "source": "payment",
                        "payment_id": pid,
                        "amount_minor": applied,
                        "created_at": iso(now),
                    }
                )
            )
        ops += ledger_ops
        ops.append(
            c.store.audit(org_id).put_op(
                actor_id=actor.id,
                actor_role=actor.role,
                action=audit_action,
                entity_type="payment",
                entity_id=pid,
                summary={
                    "invoice_id": invoice_id,
                    "amount_minor": amount,
                    "applied_minor": applied,
                    "excess_minor": excess,
                    "method": method,
                    **(audit_extra or {}),
                },
                now=now,
                request_id=actor.request_id,
                ip=actor.ip,
            )
        )
        try:
            c.store.table.transact(ops)
        except TxCancelled as e:
            if any(e.failed(i) for i in range(len(guards))):
                raise DuplicatePayment() from None
            if unmatched is not None and e.failed(len(guards)):
                raise Conflict("This payment was already allocated") from None
            if e.failed(first + 1) or e.failed(first + 2) or e.conflicted:
                continue  # invoice or lease moved: re-read and retry
            raise
        _after_payment(c, org_id, invoice_id, pid, now)
        return payment
    raise Conflict("The invoice is busy, retry")


def _after_payment(c: Container, org_id: str, invoice_id: str, pid: str, now: Any) -> None:
    """After commit: receipt, and later the payment_received notification (phase 4)."""
    c.jobs.enqueue(
        "housekeeping",
        make_envelope(
            "generate_receipt", org_id, {"invoice_id": invoice_id, "payment_id": pid}, now
        ),
    )
    inv = c.store.billing(org_id).get_invoice(invoice_id)
    if inv is not None:
        publish(
            c,
            org_id,
            "payment_received",
            tenant_ids=inv["tenant_ids"],
            entity_id=invoice_id,
            offset=pid,
        )


def record_unmatched(c: Container, org_id: str, ev: PaymentEvent, reason: str) -> Item:
    """Money that cannot be tied to an invoice is kept, never dropped (LLD 6.4)."""
    pid = new_id()
    now = c.clock.now()
    item = payment_item(
        org_id,
        pid,
        {
            "method": "duitnow_qr",
            "status": "unmatched",
            "amount_minor": ev.amount.amount_minor,
            "provider": ev.provider,
            "gateway_reference": ev.reference,
            "gateway_txn_id": ev.gateway_txn_id,
            "received_at": iso(ev.paid_at),
            "recorded_by": "gateway",
            "unmatched_reason": reason,
        },
        iso(now),
        parked=True,
    )
    c.store.table.transact(
        [
            _whevt(ev.provider, ev.event_id, now),
            _payref(ev.provider, ev.gateway_txn_id, org_id, pid),
            Put(item=item, condition=NOT_EXISTS),
            c.store.audit(org_id).put_op(
                actor_id="gateway",
                actor_role="gateway",
                action="payment.unmatched",
                entity_type="payment",
                entity_id=pid,
                summary={"reason": reason, "amount_minor": ev.amount.amount_minor},
                now=now,
                request_id="job",
                ip=None,
            ),
        ]
    )
    log.warning(
        "unmatched payment", extra={"org_id": org_id, "entity_id": pid, "error_code": reason}
    )
    return item


def record_failed(
    c: Container, org_id: str, ev: PaymentEvent, reason: str, inv: Item | None
) -> None:
    pid = new_id()
    now = c.clock.now()
    item = payment_item(
        org_id,
        pid,
        {
            "invoice_id": inv["id"] if inv else None,
            "lease_id": inv["lease_id"] if inv else None,
            "method": "duitnow_qr",
            "status": "failed",
            "amount_minor": ev.amount.amount_minor,
            "provider": ev.provider,
            "gateway_reference": ev.reference,
            "gateway_txn_id": ev.gateway_txn_id,
            "received_at": iso(ev.paid_at),
            "recorded_by": "gateway",
            "failure_reason": reason,
        },
        iso(now),
        parked=inv is None,
    )
    c.store.table.transact(
        [
            _whevt(ev.provider, ev.event_id, now),
            Put(item=item, condition=NOT_EXISTS),
            c.store.audit(org_id).put_op(
                actor_id="gateway",
                actor_role="gateway",
                action="payment.failed",
                entity_type="payment",
                entity_id=pid,
                summary={"reason": reason},
                now=now,
                request_id="job",
                ip=None,
            ),
        ]
    )
    log.error(
        "payment failed or rejected",
        extra={"org_id": org_id, "entity_id": pid, "error_code": reason},
    )


def apply_event(c: Container, ev: PaymentEvent) -> str:
    """Process one normalised gateway event. Returns what happened (for logs and tests)."""
    from app.core import tracing

    with tracing.span(
        "payments.apply_event",
        attributes={"payment.provider": ev.provider, "payment.status": ev.status},
    ) as sp:
        outcome = _apply_event(c, ev)
        sp.set_attribute("payment.outcome", outcome)
        return outcome


def _apply_event(c: Container, ev: PaymentEvent) -> str:
    qr = c.store.table.get(keys.qrref_pk(ev.reference), keys.META)
    now = c.clock.now()
    try:
        if ev.status == "reversed":
            return _reverse(c, ev, qr)
        if qr is None:
            record_unmatched(c, c.settings.platform_org_id, ev, "unknown_reference")
            return "unmatched"
        org_id = qr["org_id"]
        inv = c.store.billing(org_id).get_invoice(qr["invoice_id"])
        if inv is None:
            record_unmatched(c, org_id, ev, "invoice_not_found")
            return "unmatched"
        if ev.status == "failed":
            record_failed(c, org_id, ev, "gateway_failed", inv)
            return "failed"
        if ev.amount.currency.value != inv["currency"] or ev.amount.amount_minor <= 0:
            record_failed(c, org_id, ev, "currency_or_amount_invalid", inv)
            return "failed"
        pid = new_id()
        settle(
            c,
            org_id,
            inv["id"],
            ev.amount.amount_minor,
            method="duitnow_qr",
            received_at=iso(ev.paid_at),
            actor=GATEWAY,
            payment_id=pid,
            provider=ev.provider,
            gateway_reference=ev.reference,
            gateway_txn_id=ev.gateway_txn_id,
            audit_action="payment.apply",
            guards=[
                _whevt(ev.provider, ev.event_id, now),
                _payref(ev.provider, ev.gateway_txn_id, org_id, pid),
            ],
        )
        return "applied"
    except DuplicatePayment:
        return "duplicate"
    except TxCancelled as e:  # a duplicate on the unmatched/failed paths
        if e.failed(0) or e.failed(1):
            return "duplicate"
        raise


def _reverse(c: Container, ev: PaymentEvent, qr: Item | None) -> str:
    ref = c.store.table.get(keys.payref_pk(ev.provider, ev.gateway_txn_id), keys.META)
    if ref is None:
        log.error("reversal for an unknown payment", extra={"error_code": "reversal_unknown"})
        return "unknown_payment"
    org_id = ref["org_id"]
    repo = c.store.billing(org_id)
    for _ in range(MAX_ATTEMPTS):
        pay = repo.get_payment(ref["payment_id"])
        if pay is None or pay["status"] == "reversed":
            return "duplicate"
        now = c.clock.now()
        reversed_pay = {
            **pay,
            "status": "reversed",
            "version": pay["version"] + 1,
            "reversed_at": iso(now),
        }
        ops: list[Any] = [
            _whevt(ev.provider, ev.event_id, now),
            Put(
                item=reversed_pay,
                condition="#v = :v",
                names={"#v": "version"},
                values={":v": pay["version"]},
            ),
        ]
        if pay["status"] == "succeeded" and pay["invoice_id"]:
            inv = repo.get_invoice(pay["invoice_id"])
            lease = c.store.leases(org_id).get(pay["lease_id"])
            if inv is None or lease is None:
                return "unknown_payment"
            paid = max(inv["paid_minor"] - pay["applied_minor"], 0)
            status = (
                inv["status"]
                if inv["status"] == "void"
                else derive_status(inv["status"], inv["total_minor"], paid)
            )
            if status == "paid":
                status = "open"
            new_inv = reindexed(
                {
                    **inv,
                    "paid_minor": paid,
                    "status": status,
                    "version": inv["version"] + 1,
                    "updated_at": iso(now),
                },
                org_id,
            )
            ledger_ops, balance = build_entries(
                repo,
                lease,
                [
                    {
                        "type": "payment_reversal",
                        "amount_minor": pay["amount_minor"],
                        "invoice_id": inv["id"],
                        "payment_id": pay["id"],
                        "memo": "payment reversed by the gateway",
                    }
                ],
                lease["balance_minor"],
                GATEWAY,
                now,
            )
            ops += [
                repo.invoice_replace_op(new_inv, inv["version"]),
                Update(
                    key=(lease["PK"], lease["SK"]),
                    expression="SET balance_minor = :bal, credit_minor = :cred, version = :nv, updated_at = :now",
                    condition="#v = :v",
                    names={"#v": "version"},
                    values={
                        ":bal": balance,
                        ":cred": max(lease["credit_minor"] - pay["excess_minor"], 0),
                        ":nv": lease["version"] + 1,
                        ":v": lease["version"],
                        ":now": iso(now),
                    },
                ),
                *ledger_ops,
            ]
        ops.append(
            c.store.audit(org_id).put_op(
                actor_id="gateway",
                actor_role="gateway",
                action="payment.reverse",
                entity_type="payment",
                entity_id=pay["id"],
                summary={"amount_minor": pay["amount_minor"]},
                now=now,
                request_id="job",
                ip=None,
            )
        )
        try:
            c.store.table.transact(ops)
            return "reversed"
        except TxCancelled as e:
            if e.failed(0):
                return "duplicate"
            continue
    return "conflict"


__all__ = ["SYSTEM", "DuplicatePayment", "apply_event", "settle", "record_unmatched"]

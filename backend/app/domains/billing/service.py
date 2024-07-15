from __future__ import annotations

import base64
import hashlib
import re
from datetime import date
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.context import RequestContext, Role
from app.core.errors import AppError, Forbidden, InvalidState, NotFound, ValidationFailed
from app.core.ids import new_id
from app.domains.billing.common import Actor, Item, period_of
from app.domains.billing.invoicing import NewInvoice, create_invoice, line
from app.domains.billing.ledger import build_entries
from app.domains.billing.payments import DuplicatePayment, _payref, settle
from app.domains.billing.runs import run_to_api, start_run
from app.domains.billing.schemas import (
    AllocateRequest,
    ChargeCreate,
    InvoiceRunCreate,
    InvoiceVoid,
    ManualPaymentCreate,
)
from app.domains.scope import Scope
from app.repositories import keys
from app.repositories.billing import reindexed
from app.repositories.dynamo import NOT_EXISTS, ConditionFailed, Delete, TxCancelled, Update
from app.worker.envelope import make_envelope

STAFF = {Role.ORG_ADMIN, Role.PROPERTY_MANAGER}
UNPAID = ("open", "partially_paid", "overdue")


def _m(minor: int) -> dict[str, Any]:
    return {"amount_minor": minor, "currency": "MYR"}


def invoice_to_api(it: Item) -> Item:
    qr = it.get("qr")
    outstanding = 0 if it["status"] == "void" else max(it["total_minor"] - it["paid_minor"], 0)
    return {
        **{
            k: it[k]
            for k in (
                "id",
                "number",
                "lease_id",
                "tenant_ids",
                "period",
                "status",
                "issue_date",
                "due_date",
                "created_at",
                "updated_at",
                "version",
            )
        },
        "line_items": [
            {
                "description": x["description"],
                "type": x["type"],
                "amount": _m(x["amount_minor"]),
                "period_start": x.get("period_start"),
                "period_end": x.get("period_end"),
                "category": x.get("category"),
            }
            for x in it["line_items"]
        ],
        "total": _m(it["total_minor"]),
        "paid": _m(it["paid_minor"]),
        "outstanding": _m(outstanding),
        "qr": None if not qr else {**qr, "amount": _m(qr["amount_minor"])},
    }


def ledger_to_api(it: Item) -> Item:
    return {
        **{
            k: it[k]
            for k in (
                "id",
                "lease_id",
                "type",
                "invoice_id",
                "payment_id",
                "memo",
                "created_at",
                "created_by",
            )
        },
        "amount": _m(it["amount_minor"]),
        "balance_after": _m(it["balance_after_minor"]),
    }


def payment_to_api(it: Item) -> Item:
    return {
        **{
            k: it[k]
            for k in (
                "id",
                "invoice_id",
                "lease_id",
                "method",
                "status",
                "provider",
                "gateway_reference",
                "received_at",
                "recorded_by",
                "created_at",
            )
        },
        "amount": _m(it["amount_minor"]),
    }


class BillingService:
    def __init__(self, c: Container, ctx: RequestContext) -> None:
        self._c, self._ctx = c, ctx
        self._repo = c.store.billing(ctx.org_id)
        self._audit = c.store.audit(ctx.org_id)
        self._actor = Actor.of(ctx)
        self._scope = Scope(c, ctx)

    @property
    def _is_staff(self) -> bool:
        return bool(self._ctx.roles & STAFF)

    # ---- access: tenants reach only invoices and ledgers of their own (non-draft) leases
    def _lease(self, lease_id: str) -> Item:
        lease = self._c.store.leases(self._ctx.org_id).get(lease_id)
        if lease is None or not self._scope.lease_ok(lease):
            raise NotFound()
        if not self._is_staff:
            if lease["status"] == "draft":
                raise NotFound()
            if self._ctx.tenant_id not in lease["tenant_ids"]:
                raise Forbidden()
        return lease

    def invoice(self, iid: str) -> Item:
        inv = self._repo.get_invoice(iid)
        if inv is None or not self._scope.invoice_ok(inv):
            raise NotFound()
        if not self._is_staff and self._ctx.tenant_id not in inv["tenant_ids"]:
            raise Forbidden()
        return inv

    # ---- reads
    def list_invoices(
        self,
        limit: int,
        cursor: str | None,
        status: str | None,
        period: str | None,
        lease_id: str | None,
        due_before: date | None,
    ) -> tuple[list[Item], str | None]:
        def accept(it: Item) -> bool:
            return (
                (status is None or it["status"] == status)
                and (period is None or it["period"] == period)
                and (lease_id is None or it["lease_id"] == lease_id)
                and (due_before is None or it["due_date"] < str(due_before))
                and self._scope.invoice_ok(it)  # a manager: invoices of their properties' leases
            )

        if lease_id:
            self._lease(lease_id)  # access check
            return self._repo._page(  # noqa: SLF001 - repository method with a filter
                pk=keys.lease_pk(self._ctx.org_id, lease_id),
                sk_prefix="INV#",
                limit=limit,
                cursor=cursor,
                accept=accept,
            )
        if self._is_staff:
            return self._repo.invoices_page(limit, cursor, accept)
        return self._tenant_invoices(limit, cursor, accept)

    def _tenant_invoices(
        self, limit: int, cursor: str | None, accept: Any
    ) -> tuple[list[Item], str | None]:
        """A tenant has a handful of leases, so collect, sort newest first and page by offset."""
        tid = self._ctx.tenant_id
        if tid is None:
            raise Forbidden()
        leases = self._c.store.leases(self._ctx.org_id)
        found = leases.get_many([x["lease_id"] for x in leases.all_links(tid)])
        items: list[Item] = []
        for lease in found:
            if lease["status"] != "draft":
                items += [i for i in self._repo.all_invoices_of_lease(lease["id"]) if accept(i)]
        items.sort(key=lambda i: (i["issue_date"], i["id"]), reverse=True)
        scope = f"tenant-invoices:{tid}"
        start = 0
        if cursor:
            start = int(self._c.store.cursors.decode(cursor, pk=scope, index=None)["offset"])
        page = items[start : start + limit]
        nxt = None
        if start + limit < len(items):
            nxt = self._c.store.cursors.encode(key={"offset": start + limit}, index=None, pk=scope)
        return page, nxt

    def ledger(
        self, lease_id: str, limit: int, cursor: str | None
    ) -> tuple[list[Item], str | None]:
        self._lease(lease_id)
        return self._repo.ledger_page(lease_id, limit, cursor)

    def statement(self, lease_id: str, frm: date, to: date) -> Item:
        self._lease(lease_id)
        if to < frm:
            raise ValidationFailed(details=[{"field": "to", "issue": "must not be before from"}])
        entries = self._repo.all_ledger(lease_id)
        opening = 0
        inside: list[Item] = []
        closing = 0
        for e in entries:
            d = e["created_at"][:10]
            if d < str(frm):
                opening = closing = e["balance_after_minor"]
            elif d <= str(to):
                inside.append(e)
                closing = e["balance_after_minor"]
        return {
            "lease_id": lease_id,
            "from": frm,
            "to": to,
            "opening_balance": _m(opening),
            "closing_balance": _m(closing),
            "entries": [ledger_to_api(e) for e in inside],
        }

    def unmatched(self, limit: int, cursor: str | None) -> tuple[list[Item], str | None]:
        return self._repo.unmatched_page(limit, cursor)

    def runs(self, limit: int, cursor: str | None) -> tuple[list[Item], str | None]:
        items, nxt = self._repo.runs_page(limit, cursor)
        return [run_to_api(i) for i in items], nxt

    # ---- QR (LLD 7.2)
    def create_qr(self, iid: str) -> Item:
        inv = self.invoice(iid)
        if inv["status"] not in UNPAID:
            raise InvalidState("This invoice is not awaiting payment")
        outstanding = inv["total_minor"] - inv["paid_minor"]
        now = self._c.clock.now()
        qr: Item | None = inv.get("qr")
        if qr and qr["amount_minor"] == outstanding and qr["expires_at"] > iso(now):
            return qr  # still valid for the amount owed: no new QR, no rate-limit charge
        self._c.qr_limiter.check(f"qr:{self._ctx.org_id}:{self._ctx.user_id}")
        provider = self._c.payments.get(self._c.settings.payment_provider)
        if provider is None:
            raise AppError("No payment provider is configured", code="internal_error")
        rules = provider.reference_rules
        from app.core.money import Money

        reference = ""
        expires_at = now + rules.qr_validity
        for _ in range(5):
            digest = hashlib.sha256(f"{iid}{new_id()}".encode()).digest()
            reference = ("ANJ" + base64.b32encode(digest).decode().rstrip("="))[
                : min(23, rules.max_length)
            ]
            if not re.match(rules.charset, reference):
                raise AppError(
                    "Payment reference does not satisfy the provider's rules", code="internal_error"
                )
            try:
                self._c.store.table.put(
                    {
                        "PK": keys.qrref_pk(reference),
                        "SK": keys.META,
                        "entity_type": "qr_reference",
                        "org_id": self._ctx.org_id,
                        "lease_id": inv["lease_id"],
                        "invoice_id": iid,
                        "expires_at": iso(expires_at),
                        "ttl": int(expires_at.timestamp()) + 30 * 86400,
                    },  # late payments still match
                    condition=NOT_EXISTS,
                )
                break
            except ConditionFailed:
                continue  # a collision on the 23-char reference: retry with a new nonce
        try:
            result = provider.create_qr(
                reference=reference,
                amount=Money(outstanding),
                expires_at=expires_at,
                description=f"Invoice {inv['number']}",
            )
        except Exception:
            self._c.store.table.transact([Delete(key=(keys.qrref_pk(reference), keys.META))])
            raise AppError(
                "The payment provider is unavailable, try again", code="internal_error"
            ) from None
        summary = {
            "reference": result.reference,
            "qr_payload": result.qr_payload,
            "qr_image_url": result.qr_image_url,
            "amount_minor": outstanding,
            "expires_at": iso(result.expires_at),
            "provider": result.provider,
        }
        for _ in range(3):  # attach to the invoice; a concurrent payment just bumps the version
            current = self._repo.get_invoice(iid)
            if current is None:
                break
            try:
                self._c.store.table.transact(
                    [
                        self._repo.invoice_replace_op(
                            {
                                **current,
                                "qr": summary,
                                "version": current["version"] + 1,
                                "updated_at": iso(now),
                            },
                            current["version"],
                        )
                    ]
                )
                break
            except TxCancelled:
                continue
        return summary

    # ---- void
    def void(self, iid: str, body: InvoiceVoid) -> Item:
        for _ in range(4):
            inv = self.invoice(iid)
            if inv["status"] == "void":
                raise InvalidState("The invoice is already void")
            if inv["status"] == "paid" or inv["paid_minor"] > 0:
                raise InvalidState("Reverse the payments before voiding this invoice")
            lease = self._c.store.leases(self._ctx.org_id).get(inv["lease_id"])
            if lease is None:
                raise NotFound()
            now = self._c.clock.now()
            new = reindexed(
                {
                    **inv,
                    "status": "void",
                    "void_reason": body.reason,
                    "version": inv["version"] + 1,
                    "updated_at": iso(now),
                },
                self._ctx.org_id,
            )
            ledger_ops, balance = build_entries(
                self._repo,
                lease,
                [
                    {
                        "type": "adjustment",
                        "amount_minor": -inv["total_minor"],
                        "invoice_id": iid,
                        "memo": f"void invoice {inv['number']}",
                    }
                ],
                lease["balance_minor"],
                self._actor,
                now,
            )
            ops: list[Any] = [
                self._repo.invoice_replace_op(new, inv["version"]),
                Update(
                    key=(lease["PK"], lease["SK"]),
                    expression="SET balance_minor = :bal, version = :nv, updated_at = :now",
                    condition="#v = :v",
                    names={"#v": "version"},
                    values={
                        ":bal": balance,
                        ":nv": lease["version"] + 1,
                        ":v": lease["version"],
                        ":now": iso(now),
                    },
                ),
                *ledger_ops,
                self._audit.put_for(
                    self._ctx,
                    now,
                    "invoice.void",
                    "invoice",
                    iid,
                    {"total_minor": inv["total_minor"]},
                ),
            ]
            try:
                self._c.store.table.transact(ops)
                return new
            except TxCancelled as e:
                if e.failed(0) or e.failed(1) or e.conflicted:
                    continue
                raise
        raise InvalidState("The invoice is busy, retry")

    # ---- manual payment, allocation
    def manual_payment(self, iid: str, body: ManualPaymentCreate, key: str) -> Item:
        inv = self.invoice(iid)
        pid = new_id()
        guard = _payref("manual", f"{self._ctx.org_id}#{key}", self._ctx.org_id, pid)
        try:
            return settle(
                self._c,
                self._ctx.org_id,
                inv["id"],
                body.amount.amount_minor,
                method=body.method,
                received_at=iso(body.received_at),
                actor=self._actor,
                guards=[guard],
                payment_id=pid,
                gateway_reference=body.reference,
                audit_action="payment.manual_record",
                audit_extra={"reason": body.reason},
                require_unpaid=True,
            )
        except DuplicatePayment:
            ref = self._c.store.table.get(
                keys.payref_pk("manual", f"{self._ctx.org_id}#{key}"), keys.META
            )
            existing = self._repo.get_payment(ref["payment_id"]) if ref else None
            if existing is None:
                raise
            return existing  # same Idempotency-Key: same payment, never a second one

    def allocate(self, payment_id: str, body: AllocateRequest) -> Item:
        pay = self._repo.get_payment(payment_id)
        if pay is None:
            raise NotFound()
        if pay["status"] != "unmatched":
            raise InvalidState("Only an unmatched payment can be allocated")
        if body.amount is not None and body.amount.amount_minor != pay["amount_minor"]:
            raise ValidationFailed(
                details=[
                    {"field": "amount", "issue": "partial allocation is not supported, omit it"}
                ]
            )
        if self._repo.get_invoice(body.invoice_id) is None:
            raise ValidationFailed(details=[{"field": "invoice_id", "issue": "unknown invoice"}])
        return settle(
            self._c,
            self._ctx.org_id,
            body.invoice_id,
            pay["amount_minor"],
            method=pay["method"],
            received_at=pay["received_at"],
            actor=self._actor,
            guards=[],
            payment_id=pay["id"],
            provider=pay["provider"],
            gateway_reference=pay["gateway_reference"],
            gateway_txn_id=pay.get("gateway_txn_id"),
            unmatched=pay,
            audit_action="payment.allocate",
            audit_extra={"reason": body.reason},
        )

    # ---- runs and one-off charges
    def create_run(self, body: InvoiceRunCreate) -> Item:
        run = start_run(self._c, self._ctx.org_id, body.period)
        self._c.jobs.enqueue(
            "invoicing",
            make_envelope(
                "invoice_run",
                self._ctx.org_id,
                {"run_id": run["id"], "period": body.period, "lease_ids": body.lease_ids},
                self._c.clock.now(),
            ),
        )
        return run_to_api(self._repo.get_run(run["period"], run["id"]) or run)

    def charge(self, lease_id: str, body: ChargeCreate, key: str) -> Item:
        lease = self._lease(lease_id)
        if lease["status"] not in ("active", "terminated", "expired"):
            raise InvalidState("Charges can be raised on active, terminated or expired leases")
        digest = hashlib.sha256(f"{self._ctx.org_id}:{key}".encode()).hexdigest()[:20]
        kind = f"charge#{digest}"  # the same Idempotency-Key always hits the same INVKEY lock
        period = period_of(body.due_date)
        inv = create_invoice(
            self._c,
            self._ctx.org_id,
            NewInvoice(
                lease_id=lease_id,
                period=period,
                kind=kind,
                due_date=body.due_date,
                lines=[
                    line(
                        "one_off",
                        body.description,
                        body.amount.amount_minor,
                        category=body.category,
                    )
                ],
                audit_action="lease.charge",
                audit_extra={"category": body.category},
            ),
            self._actor,
        )
        if inv is None:  # replay: return the invoice that the first call created
            lock = self._c.store.table.get(
                keys.lease_pk(self._ctx.org_id, lease_id), keys.invkey_sk(period, kind)
            )
            existing = self._repo.get_invoice(lock["invoice_id"]) if lock else None
            if existing is None:
                raise InvalidState("This charge was already raised")
            return existing
        return inv

"""Scheduled money jobs (LLD 6.6 to 6.8): overdue and late fees, lease expiry, termination effects,
integrity check and settlement reconciliation. All are idempotent and safe to re-run."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.dates import today_kl
from app.core.ids import new_id
from app.core.money import Money
from app.domains.billing.common import (
    SYSTEM,
    Actor,
    Item,
    derive_status,
    month_bounds,
    period_of,
    rent_for_period,
    terms_for,
)
from app.domains.billing.ledger import DEPOSIT_TYPES, build_entries
from app.domains.notifications.events import publish
from app.repositories import keys
from app.repositories.billing import reindexed
from app.repositories.dynamo import Put, TxCancelled, Update
from app.repositories.leases import without_active_index

log = logging.getLogger(__name__)
ATTEMPTS = 5


def _lease_update(lease: Item, now: Any, balance: int, **extra: Any) -> Update:
    sets = "balance_minor = :bal, version = :nv, updated_at = :now"
    values: dict[str, Any] = {
        ":bal": balance,
        ":nv": lease["version"] + 1,
        ":v": lease["version"],
        ":now": iso(now),
    }
    for i, (k, v) in enumerate(extra.items()):
        sets += f", {k} = :x{i}"
        values[f":x{i}"] = v
    return Update(
        key=(lease["PK"], lease["SK"]),
        expression=f"SET {sets}",
        condition="#v = :v",
        names={"#v": "version"},
        values=values,
    )


# ---- overdue and late fees ------------------------------------------------------------------
def whole_months(due: date, today: date) -> int:
    n = (today.year - due.year) * 12 + today.month - due.month
    return max(n - (1 if today.day < due.day else 0), 0)


def late_fee_amount(rule: Item, inv: Item, n: int) -> int:
    """Fixed: once. Percent per month: basis points of what is still owed on the rent lines."""
    if rule["type"] == "fixed":
        return int(rule["fixed_amount"]["amount_minor"]) if n == 1 else 0
    base = inv["total_minor"] - sum(
        x["amount_minor"] for x in inv["line_items"] if x["type"] == "late_fee"
    )
    owed = max(base - inv["paid_minor"], 0)
    return Money(owed).times_ratio(int(rule["percent_bp"]), 10000).amount_minor


def _apply_late_fee(
    c: Container, org_id: str, inv_id: str, n: int, rule: Item, period: str
) -> bool:
    repo = c.store.billing(org_id)
    for _ in range(ATTEMPTS):
        inv = repo.get_invoice(inv_id)
        if inv is None or inv["status"] not in ("open", "partially_paid", "overdue"):
            return False
        fee = late_fee_amount(rule, inv, n)
        if fee <= 0:
            return False
        lease = c.store.leases(org_id).get(inv["lease_id"])
        if lease is None:
            return False
        now = c.clock.now()
        total = inv["total_minor"] + fee
        new_inv = reindexed(
            {
                **inv,
                "total_minor": total,
                "version": inv["version"] + 1,
                "updated_at": iso(now),
                "line_items": [
                    *inv["line_items"],
                    {
                        "type": "late_fee",
                        "amount_minor": fee,
                        "description": f"Late fee (month {n} overdue)",
                    },
                ],
                "status": derive_status(inv["status"], total, inv["paid_minor"]),
            },
            org_id,
        )
        ledger_ops, balance = build_entries(
            repo,
            lease,
            [
                {
                    "type": "charge_late_fee",
                    "amount_minor": fee,
                    "invoice_id": inv_id,
                    "memo": f"late fee, month {n}",
                }
            ],
            lease["balance_minor"],
            SYSTEM,
            now,
        )
        try:
            c.store.table.transact(
                [
                    repo.invkey_op(
                        inv["lease_id"], period, f"late_fee#{n}", inv_id
                    ),  # once per invoice and month
                    repo.invoice_replace_op(new_inv, inv["version"]),
                    _lease_update(lease, now, balance),
                    *ledger_ops,
                    c.store.audit(org_id).put_op(
                        actor_id="system",
                        actor_role="system",
                        action="invoice.late_fee",
                        entity_type="invoice",
                        entity_id=inv_id,
                        summary={"month": n, "fee_minor": fee},
                        now=now,
                        request_id="job",
                        ip=None,
                    ),
                ]
            )
            return True
        except TxCancelled as e:
            if e.failed(0):
                return False  # this month's fee was already charged
            if e.failed(1) or e.failed(2) or e.conflicted:
                continue
            raise
    return False


def overdue_sweep(c: Container, org_id: str) -> dict[str, int]:
    meta = c.store.org(org_id).get_meta() or {}
    rule = (meta.get("settings") or {}).get("late_fee") or {"type": "none", "grace_days": 0}
    today = today_kl(c.clock)
    cutoff = today - timedelta(days=rule.get("grace_days", 0) + 1)  # due + grace has passed
    repo = c.store.billing(org_id)
    marked = fees = 0
    for inv in repo.unpaid_due_on_or_before(str(cutoff)):
        if inv["status"] in ("open", "partially_paid"):
            for _ in range(ATTEMPTS):
                cur = repo.get_invoice(inv["id"])
                if cur is None or cur["status"] not in ("open", "partially_paid"):
                    break
                try:
                    c.store.table.transact(
                        [
                            repo.invoice_replace_op(
                                reindexed(
                                    {
                                        **cur,
                                        "status": "overdue",
                                        "version": cur["version"] + 1,
                                        "updated_at": iso(c.clock.now()),
                                    },
                                    org_id,
                                ),
                                cur["version"],
                            )
                        ]
                    )
                    marked += 1
                    publish(
                        c,
                        org_id,
                        "invoice_overdue",
                        tenant_ids=cur["tenant_ids"],
                        entity_id=cur["id"],
                        offset="overdue",
                    )
                    break
                except TxCancelled:
                    continue
        if rule["type"] != "none" and inv.get("kind") == "rent":
            due = date.fromisoformat(inv["due_date"])
            months = whole_months(due, today)
            for n in [1] if rule["type"] == "fixed" else range(1, months + 1):
                if (rule["type"] == "fixed" or n <= months) and _apply_late_fee(
                    c, org_id, inv["id"], n, rule, inv["period"]
                ):
                    fees += 1
    return {"marked_overdue": marked, "late_fees": fees}


# ---- lease expiry and termination effects ---------------------------------------------------
def expire_leases(c: Container, org_id: str) -> int:
    """Fixed-term active leases past their end date become expired and release the unit."""
    today = str(today_kl(c.clock))
    n = 0
    repo = c.store.billing(org_id)
    leases = c.store.leases(org_id)
    for lease in repo.active_leases():
        if lease["end_date"] is None or lease["end_date"] >= today:
            continue
        for _ in range(ATTEMPTS):
            cur = leases.get(lease["id"])
            if cur is None or cur["status"] != "active":
                break
            now = c.clock.now()
            new = without_active_index(
                {**cur, "status": "expired", "version": cur["version"] + 1, "updated_at": iso(now)}
            )
            lock = leases.get_lock(cur["unit_id"])
            remaining = [p for p in (lock["periods"] if lock else []) if p["lease_id"] != cur["id"]]
            ops: list[Any] = [
                leases.replace_op(new, cur["version"]),
                leases.lock_op(cur["unit_id"], remaining, lock),
            ]
            unit = c.store.units(org_id).get(cur["unit_id"])
            if unit is not None and unit["current_lease_id"] == cur["id"]:
                nxt = (
                    min(remaining, key=lambda p: p["start_date"])["lease_id"] if remaining else None
                )
                ops.append(
                    leases.unit_update_op(
                        unit,
                        status="occupied" if nxt else "vacant",
                        current_lease_id=nxt,
                        now=iso(now),
                    )
                )
            if unit is not None and unit["type"] == "room":
                parent = c.store.units(org_id).get(unit["parent_unit_id"])
                if parent is not None:
                    ops.append(leases.parent_rooms_op(parent, -1, iso(now)))
            ops.append(
                c.store.audit(org_id).put_op(
                    actor_id="system",
                    actor_role="system",
                    action="lease.expire",
                    entity_type="lease",
                    entity_id=cur["id"],
                    summary={"end_date": cur["end_date"]},
                    now=now,
                    request_id="job",
                    ip=None,
                )
            )
            try:
                c.store.table.transact(ops)
                n += 1
                break
            except TxCancelled:
                continue
    return n


def termination_effects(c: Container, org_id: str, lease_id: str) -> dict[str, int]:
    """After a lease is terminated (LLD 6.6, 6.10): void unissued periods, prorate the final period,
    settle the deposits in the ledger. Marked done in the same transaction as the deposit entries."""
    leases = c.store.leases(org_id)
    lease = leases.get(lease_id)
    if lease is None or lease["status"] != "terminated" or lease.get("termination_effects_done"):
        return {"voided": 0, "credited": 0, "deposit_entries": 0}
    td = date.fromisoformat(lease["terminated_at"])
    repo = c.store.billing(org_id)
    voided = credited = 0
    for inv in repo.all_invoices_of_lease(lease_id):
        if inv.get("kind") != "rent" or inv["status"] == "void":
            continue
        if inv["period"] > period_of(td):
            if inv["status"] in ("open", "overdue") and inv["paid_minor"] == 0:
                voided += _void_system(c, org_id, inv["id"], "lease terminated before this period")
        elif inv["period"] == period_of(td) and td < month_bounds(inv["period"])[1]:
            credited += _prorate_final(c, org_id, inv["id"], lease)
    entries = _settle_deposits(c, org_id, lease_id)
    return {"voided": voided, "credited": credited, "deposit_entries": entries}


def _void_system(c: Container, org_id: str, inv_id: str, reason: str) -> int:
    repo = c.store.billing(org_id)
    for _ in range(ATTEMPTS):
        inv = repo.get_invoice(inv_id)
        lease = c.store.leases(org_id).get(inv["lease_id"]) if inv else None
        if inv is None or lease is None or inv["status"] == "void" or inv["paid_minor"] > 0:
            return 0
        now = c.clock.now()
        new = reindexed(
            {
                **inv,
                "status": "void",
                "void_reason": reason,
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
                    "type": "adjustment",
                    "amount_minor": -inv["total_minor"],
                    "invoice_id": inv_id,
                    "memo": f"void invoice {inv['number']}",
                }
            ],
            lease["balance_minor"],
            SYSTEM,
            now,
        )
        try:
            c.store.table.transact(
                [
                    repo.invoice_replace_op(new, inv["version"]),
                    _lease_update(lease, now, balance),
                    *ledger_ops,
                ]
            )
            return 1
        except TxCancelled:
            continue
    return 0


def _prorate_final(c: Container, org_id: str, inv_id: str, lease: Item) -> int:
    """The final period was invoiced in full: credit the days after the termination date."""
    repo = c.store.billing(org_id)
    for _ in range(ATTEMPTS):
        inv = repo.get_invoice(inv_id)
        cur_lease = c.store.leases(org_id).get(lease["id"])
        if inv is None or cur_lease is None or inv["status"] == "void":
            return 0
        first = month_bounds(inv["period"])[0]
        rent_now, _, _ = rent_for_period(
            lease, inv["period"], terms_for(lease, first)["monthly_rent_minor"]
        )
        rent_line = next((x for x in inv["line_items"] if x["type"] == "rent"), None)
        if rent_line is None or rent_line["amount_minor"] <= rent_now:
            return 0
        delta = rent_line["amount_minor"] - rent_now
        now = c.clock.now()
        lines = [
            (
                {
                    **x,
                    "amount_minor": rent_now,
                    "description": x["description"] + " (to termination date)",
                }
                if x is rent_line
                else x
            )
            for x in inv["line_items"]
        ]
        total = inv["total_minor"] - delta
        paid = min(inv["paid_minor"], total)
        excess = inv["paid_minor"] - paid
        new = reindexed(
            {
                **inv,
                "line_items": lines,
                "total_minor": total,
                "paid_minor": paid,
                "status": derive_status(inv["status"], total, paid),
                "version": inv["version"] + 1,
                "updated_at": iso(now),
            },
            org_id,
        )
        ledger_ops, balance = build_entries(
            repo,
            cur_lease,
            [
                {
                    "type": "adjustment",
                    "amount_minor": -delta,
                    "invoice_id": inv_id,
                    "memo": "final period prorated to the termination date",
                }
            ],
            cur_lease["balance_minor"],
            SYSTEM,
            now,
        )
        try:
            c.store.table.transact(
                [
                    repo.invoice_replace_op(new, inv["version"]),
                    Update(
                        key=(cur_lease["PK"], cur_lease["SK"]),
                        expression="SET balance_minor = :bal, credit_minor = :cred, version = :nv, updated_at = :now",
                        condition="#v = :v",
                        names={"#v": "version"},
                        values={
                            ":bal": balance,
                            ":cred": cur_lease["credit_minor"] + excess,
                            ":nv": cur_lease["version"] + 1,
                            ":v": cur_lease["version"],
                            ":now": iso(now),
                        },
                    ),
                    *ledger_ops,
                    *_excess_allocation(org_id, inv_id, excess, now),
                ]
            )
            return 1
        except TxCancelled:
            continue
    return 0


def _excess_allocation(org_id: str, inv_id: str, excess: int, now: Any) -> list[Put]:
    """Paid money that no longer belongs to the invoice moves to lease credit: record it as a negative
    allocation so that the invoice's `paid` always equals the sum of its allocations."""
    if not excess:
        return []
    return [
        Put(
            item={
                "PK": keys.invoice_pk(org_id, inv_id),
                "SK": f"ALLOC#{new_id()}",
                "entity_type": "allocation",
                "org_id": org_id,
                "invoice_id": inv_id,
                "source": "proration_credit",
                "amount_minor": -excess,
                "created_at": iso(now),
            }
        )
    ]


def _settle_deposits(c: Container, org_id: str, lease_id: str) -> int:
    repo = c.store.billing(org_id)
    for _ in range(ATTEMPTS):
        lease = c.store.leases(org_id).get(lease_id)
        if lease is None or lease.get("termination_effects_done"):
            return 0
        settle = lease.get("deposit_settlement") or {"deducted": [], "returned": []}
        entries = [
            {
                "type": "adjustment",
                "amount_minor": -d["amount_minor"],
                "memo": f"{d['type']} deposit kept",
            }
            for d in settle["deducted"]
        ]
        entries += [
            {
                "type": "deposit_returned",
                "amount_minor": -r["amount_minor"],
                "memo": f"{r['type']} deposit returned",
            }
            for r in settle["returned"]
        ]
        now = c.clock.now()
        ledger_ops, balance = build_entries(
            repo, lease, entries, lease["balance_minor"], SYSTEM, now
        )
        try:
            c.store.table.transact(
                [_lease_update(lease, now, balance, termination_effects_done=True), *ledger_ops]
            )
            return len(entries)
        except TxCancelled:
            continue
    return 0


# ---- integrity and reconciliation -----------------------------------------------------------
def integrity_check(c: Container, org_id: str) -> list[dict[str, Any]]:
    """Ledger sum versus the lease balance, and invoice paid versus its allocations. Never auto-corrects."""
    repo = c.store.billing(org_id)
    problems: list[dict[str, Any]] = []
    leases = c.store.leases(org_id)
    cursor = None
    while True:
        page, cursor = leases.page(100, cursor, None)
        for lease in page:
            if lease["status"] == "draft":
                continue
            ledger = repo.all_ledger(lease["id"])
            total = sum(e["amount_minor"] for e in ledger if e["type"] not in DEPOSIT_TYPES)
            if total != lease["balance_minor"]:
                problems.append(
                    {
                        "kind": "balance_mismatch",
                        "lease_id": lease["id"],
                        "ledger": total,
                        "balance": lease["balance_minor"],
                    }
                )
            for inv in repo.all_invoices_of_lease(lease["id"]):
                allocated = sum(a["amount_minor"] for a in repo.allocations_of_invoice(inv["id"]))
                reversed_applied = sum(
                    p["applied_minor"]
                    for p in repo.payments_of_invoice(inv["id"])
                    if p["status"] == "reversed"
                )
                if inv["paid_minor"] != allocated - reversed_applied and inv["status"] != "void":
                    problems.append(
                        {
                            "kind": "invoice_paid_mismatch",
                            "invoice_id": inv["id"],
                            "paid": inv["paid_minor"],
                            "allocated": allocated - reversed_applied,
                        }
                    )
        if cursor is None:
            break
    for p in problems:
        log.error(
            "integrity.mismatch",
            extra={"org_id": org_id, "error_code": p["kind"], "detail": json.dumps(p)},
        )
    return problems


def reconcile_payments(
    c: Container, day: date, provider_key: str | None = None
) -> list[dict[str, Any]]:
    """Provider settlement report versus the payments we recorded (LLD 6.8). The report goes to S3."""
    provider = c.payments[provider_key or c.settings.payment_provider]
    issues: list[dict[str, Any]] = []
    for rec in provider.fetch_settlements(day):
        ref = c.store.table.get(keys.payref_pk(provider.key, rec.gateway_txn_id), keys.META)
        if ref is None:
            issues.append(
                {
                    "kind": "missing_payment",
                    "txn": rec.gateway_txn_id,
                    "amount_minor": rec.amount.amount_minor,
                }
            )
            continue
        org_id = ref["org_id"]  # from our own PAYREF record, never from the report
        pay = c.store.billing(org_id).get_payment(ref["payment_id"])
        if (
            pay is not None
            and pay["status"] != "reversed"
            and pay["amount_minor"] != rec.amount.amount_minor
        ):
            issues.append(
                {
                    "kind": "amount_mismatch",
                    "txn": rec.gateway_txn_id,
                    "settled": rec.amount.amount_minor,
                    "recorded": pay["amount_minor"],
                }
            )
    if c.storage is not None:
        c.storage.put(
            f"reports/reconciliation/{provider.key}/{day}.json",
            json.dumps({"day": str(day), "issues": issues}).encode(),
            "application/json",
        )
    for i in issues:
        log.error(
            "reconciliation.mismatch", extra={"error_code": i["kind"], "detail": json.dumps(i)}
        )
    return issues


__all__ = ["Actor", "without_active_index"]

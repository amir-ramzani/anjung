"""Invoice creation and the daily sweep (LLD 6.2). Idempotent per (lease, period, kind)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.dates import today_kl
from app.core.ids import new_id
from app.domains.billing.common import (
    SYSTEM,
    Actor,
    Item,
    derive_status,
    due_date_for,
    month_bounds,
    period_of,
    periods_between,
    rent_for_period,
    terms_for,
)
from app.domains.billing.ledger import build_entries, charge_entries, deposit_received_effects
from app.domains.notifications.events import publish
from app.repositories.billing import invoice_item
from app.repositories.dynamo import NOT_EXISTS, Put, TxCancelled, Update

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 5


@dataclass
class NewInvoice:
    lease_id: str
    period: str
    kind: str  # rent | deposit | charge#<ulid>
    due_date: date
    lines: list[Item]
    audit_action: str
    rent_through: str | None = None  # the sweep records how far rent has been invoiced
    audit_extra: dict[str, Any] = field(default_factory=dict)


def line(type_: str, description: str, amount: int, **extra: Any) -> Item:
    return {"type": type_, "description": description, "amount_minor": amount, **extra}


def create_invoice(c: Container, org_id: str, nv: NewInvoice, actor: Actor = SYSTEM) -> Item | None:
    """One transaction: INVKEY lock, invoice, ledger entries, lease balance and credit, invoice number,
    credit allocation and audit. Returns None when the (lease, period, kind) invoice already exists."""
    repo = c.store.billing(org_id)
    leases = c.store.leases(org_id)
    for _ in range(MAX_ATTEMPTS):
        lease = leases.get(nv.lease_id)
        if lease is None:
            raise LookupError("lease not found")
        now = c.clock.now()
        issue = today_kl(c.clock)
        total = sum(x["amount_minor"] for x in nv.lines)
        credit = min(lease["credit_minor"], max(total, 0))
        status = derive_status("open", total, credit) if total > 0 else "paid"
        seq = repo.counter_value(issue.year)
        iid = new_id()
        item = invoice_item(
            org_id,
            iid,
            lease,
            {
                "number": f"INV-{issue.year}-{seq + 1:06d}",
                "period": nv.period,
                "kind": nv.kind,
                "status": status,
                "issue_date": str(issue),
                "due_date": str(nv.due_date),
                "line_items": nv.lines,
                "total_minor": total,
                "paid_minor": credit,
            },
            iso(now),
        )
        entries = charge_entries(iid, nv.lines)
        extra_entries: list[dict[str, Any]] = []
        held = lease.get("deposits_held", [])
        if status == "paid" and nv.kind == "deposit":  # credit alone covered the deposit
            extra_entries, held = deposit_received_effects(item, lease)
        ledger_ops, balance = build_entries(
            repo, lease, entries + extra_entries, lease["balance_minor"], actor, now
        )
        values: dict[str, Any] = {
            ":bal": balance,
            ":cred": lease["credit_minor"] - credit,
            ":nv": lease["version"] + 1,
            ":v": lease["version"],
            ":now": iso(now),
            ":held": held,
        }
        expr = "SET balance_minor = :bal, credit_minor = :cred, version = :nv, updated_at = :now, deposits_held = :held"
        if nv.rent_through and nv.rent_through > lease.get("rent_through", ""):
            expr += ", rent_through = :rt"
            values[":rt"] = nv.rent_through
        ops: list[Any] = [
            repo.invkey_op(nv.lease_id, nv.period, nv.kind, iid),  # 0: duplicate guard
            Put(item=item, condition=NOT_EXISTS),  # 1
            Update(  # 2: version-checked lease update
                key=(lease["PK"], lease["SK"]),
                expression=expr,
                condition="#v = :v",
                names={"#v": "version"},
                values=values,
            ),
            repo.counter_op(
                issue.year, seq
            ),  # 3: gap-free numbering, one invoice at a time per org
            *ledger_ops,
        ]
        if credit:
            ops.append(_alloc_put(org_id, iid, credit, now))
        ops.append(
            c.store.audit(org_id).put_op(
                actor_id=actor.id,
                actor_role=actor.role,
                action=nv.audit_action,
                entity_type="invoice",
                entity_id=iid,
                summary={
                    "lease_id": nv.lease_id,
                    "period": nv.period,
                    "kind": nv.kind,
                    "total_minor": total,
                    **nv.audit_extra,
                },
                now=now,
                request_id=actor.request_id,
                ip=actor.ip,
            )
        )
        try:
            c.store.table.transact(ops)
            if status != "paid":  # after commit: tell the tenants, with a pay link
                publish(c, org_id, "invoice_issued", tenant_ids=lease["tenant_ids"], entity_id=iid)
            return item
        except TxCancelled as e:
            if e.failed(0):
                return None  # already invoiced: a normal skip
            if e.failed(2) or e.failed(3) or e.conflicted:
                continue  # lease or counter moved under us: re-read and retry
            raise
    raise RuntimeError("could not create invoice after retries")


def _alloc_put(org_id: str, invoice_id: str, amount: int, now: Any) -> Put:
    from app.repositories import keys

    return Put(
        item={
            "PK": keys.invoice_pk(org_id, invoice_id),
            "SK": f"ALLOC#{new_id()}",
            "entity_type": "allocation",
            "org_id": org_id,
            "invoice_id": invoice_id,
            "source": "credit",
            "amount_minor": amount,
            "created_at": iso(now),
        }
    )


# ---- what is due ----------------------------------------------------------------------------
def lines_for_period(lease: Item, period: str) -> list[Item]:
    first, _ = month_bounds(period)
    terms = terms_for(lease, first)
    rent, used, in_month = rent_for_period(lease, period, terms["monthly_rent_minor"])
    f, last = month_bounds(period)
    note = f" (prorated {used}/{in_month} days)" if used < in_month else ""
    label = f"Rent {first.strftime('%b %Y')}{note}"
    out = [line("rent", label, rent, period_start=str(f), period_end=str(last))]
    for rc in terms.get("recurring_charges", []):
        if rc.get("active", True):
            out.append(
                line(
                    "recurring",
                    rc["name"],
                    rc["amount_minor"],
                    period_start=str(f),
                    period_end=str(last),
                )
            )
    return out


def periods_to_invoice(
    lease: Item, today: date, lead_days: int, only: str | None = None
) -> list[str]:
    """Periods whose due date is within the lead window and that are not yet invoiced through."""
    start_p = period_of(date.fromisoformat(lease["start_date"]))
    if only:
        return [only] if _covers(lease, only) else []
    horizon = today + timedelta(days=lead_days)
    last_p = period_of(horizon)
    if lease["end_date"]:
        last_p = min(last_p, period_of(date.fromisoformat(lease["end_date"])))
    first_p = start_p
    done = lease.get("rent_through")
    if done:
        from app.domains.billing.common import next_period

        first_p = max(first_p, next_period(done))
    out = []
    for p in periods_between(first_p, last_p) if first_p <= last_p else []:
        due = due_date_for(p, terms_for(lease, month_bounds(p)[0])["due_day"])
        if due <= horizon:
            out.append(p)
    return out


def _covers(lease: Item, period: str) -> bool:
    first, last = month_bounds(period)
    if date.fromisoformat(lease["start_date"]) > last:
        return False
    return not (lease["end_date"] and date.fromisoformat(lease["end_date"]) < first)


def generate_for_lease(
    c: Container, org_id: str, lease: Item, today: date, lead_days: int, only: str | None = None
) -> tuple[int, int]:
    """(created, skipped) for one lease. Re-running creates nothing new."""
    created = skipped = 0
    if lease["deposits"] and only is None and create_deposit_invoice(c, org_id, lease, SYSTEM):
        created += 1  # normally made at activation, this is the safety net
    for p in periods_to_invoice(lease, today, lead_days, only):
        terms = terms_for(lease, month_bounds(p)[0])
        nv = NewInvoice(
            lease_id=lease["id"],
            period=p,
            kind="rent",
            due_date=due_date_for(p, terms["due_day"]),
            lines=lines_for_period(lease, p),
            audit_action="invoice.generate",
            rent_through=p,
        )
        if create_invoice(c, org_id, nv) is None:
            skipped += 1
        else:
            created += 1
    return created, skipped


def deposit_invoice_lines(lease: Item) -> list[Item]:
    return [
        line(
            "deposit",
            f"{d['type'].replace('_', ' ').capitalize()} deposit",
            d["amount_minor"],
            deposit_type=d["type"],
        )
        for d in lease["deposits"]
    ]


def create_deposit_invoice(c: Container, org_id: str, lease: Item, actor: Actor) -> Item | None:
    if not lease["deposits"]:
        return None
    start = date.fromisoformat(lease["start_date"])
    return create_invoice(
        c,
        org_id,
        NewInvoice(
            lease_id=lease["id"],
            period=period_of(start),
            kind="deposit",
            due_date=start,  # the deposit is due when the tenancy starts
            lines=deposit_invoice_lines(lease),
            audit_action="invoice.deposit",
        ),
        actor,
    )

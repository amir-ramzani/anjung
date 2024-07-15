"""Ledger sign convention (LLD 6.1): positive entries increase what the tenant owes, negative ones
decrease it. `lease.balance_minor` is the sum of the ledger EXCLUDING deposit_received and
deposit_returned, which only record deposits held for the tenant (LLD 6.10). Entries are append-only."""

from __future__ import annotations

from typing import Any

from app.core.clock import iso
from app.core.ids import new_id
from app.domains.billing.common import Actor, Item
from app.repositories.billing import BillingRepository, ledger_item
from app.repositories.dynamo import Put

DEPOSIT_TYPES = ("deposit_received", "deposit_returned")
LINE_TO_LEDGER = {
    "rent": "charge_rent",
    "recurring": "charge_recurring",
    "late_fee": "charge_late_fee",
    "deposit": "charge_other",
    "one_off": "charge_other",
    "other": "charge_other",
}


def build_entries(
    repo: BillingRepository,
    lease: Item,
    entries: list[dict[str, Any]],
    start_balance: int,
    actor: Actor,
    now: Any,
) -> tuple[list[Put], int]:
    """Ledger puts for `entries` in order, each carrying its running balance. Returns the new balance."""
    puts: list[Put] = []
    balance = start_balance
    for e in entries:
        if e["type"] not in DEPOSIT_TYPES:
            balance += e["amount_minor"]
        puts.append(
            repo.ledger_put_op(
                ledger_item(
                    repo.org_id,
                    new_id(),
                    lease["id"],
                    {
                        "type": e["type"],
                        "amount_minor": e["amount_minor"],
                        "balance_after_minor": balance,
                        "invoice_id": e.get("invoice_id"),
                        "payment_id": e.get("payment_id"),
                        "memo": e.get("memo"),
                        "created_by": actor.id,
                    },
                    iso(now),
                )
            )
        )
    return puts, balance


def charge_entries(invoice_id: str, lines: list[Item]) -> list[dict[str, Any]]:
    """One ledger entry per kind of line (rent, recurring, ...), in a stable order."""
    sums: dict[str, int] = {}
    for line in lines:
        t = LINE_TO_LEDGER[line["type"]]
        sums[t] = sums.get(t, 0) + line["amount_minor"]
    return [
        {"type": t, "amount_minor": a, "invoice_id": invoice_id} for t, a in sums.items() if a != 0
    ]


def deposit_received_effects(invoice: Item, lease: Item) -> tuple[list[dict[str, Any]], list[Item]]:
    """When a deposit invoice is fully paid: one `deposit_received` entry per type, and the new
    `deposits_held` list. These entries never touch the balance."""
    entries: list[dict[str, Any]] = []
    held = {d["type"]: dict(d) for d in lease.get("deposits_held", [])}
    for line in invoice["line_items"]:
        if line["type"] != "deposit":
            continue
        t = line["deposit_type"]
        entries.append(
            {
                "type": "deposit_received",
                "amount_minor": line["amount_minor"],
                "invoice_id": invoice["id"],
                "memo": f"{t} deposit",
            }
        )
        cur = held.get(t, {"type": t, "amount_minor": 0, "description": line.get("description")})
        held[t] = {**cur, "amount_minor": cur["amount_minor"] + line["amount_minor"]}
    return entries, list(held.values())

"""Invoices, ledger, payments, QR references and counters (LLD 4.2, section 6)."""

from __future__ import annotations

from typing import Any

from app.repositories import keys
from app.repositories.base import Item, OrgRepository
from app.repositories.dynamo import NOT_EXISTS, Delete, Put, Update

UNPAID = ("open", "partially_paid", "overdue")
_VER = "#v = :v"
_NAMES = {"#v": "version"}


def invoice_item(org_id: str, iid: str, lease: Item, fields: dict[str, Any], now: str) -> Item:
    """`fields`: number, period, kind, issue_date, due_date, line_items, total_minor, paid_minor, status."""
    item: Item = {
        "PK": keys.lease_pk(org_id, lease["id"]),
        "SK": keys.invoice_sk(fields["period"], iid),
        "GSI1PK": keys.entity_gsi1pk(org_id, "INV", iid),
        "GSI1SK": keys.META,
        # org-wide listing, newest first (a stable key: status changes never rewrite it)
        "GSI4PK": keys.all_invoices_gsi4pk(org_id),
        "GSI4SK": f"{fields['issue_date']}#{iid}",
        "entity_type": "invoice",
        "schema_version": 1,
        "org_id": org_id,
        "id": iid,
        "lease_id": lease["id"],
        "tenant_ids": lease["tenant_ids"],
        "currency": "MYR",
        "qr": None,
        **fields,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    return with_unpaid_index(item, org_id) if item["status"] in UNPAID else item


def with_unpaid_index(item: Item, org_id: str) -> Item:
    """GSI3 is sparse: only invoices still owing money (LLD 4.5)."""
    return {
        **item,
        "GSI3PK": keys.unpaid_gsi3pk(org_id),
        "GSI3SK": f"{item['due_date']}#{item['lease_id']}#{item['id']}",
    }


def without_unpaid_index(item: Item) -> Item:
    return {k: v for k, v in item.items() if k not in ("GSI3PK", "GSI3SK")}


def reindexed(item: Item, org_id: str) -> Item:
    return (
        with_unpaid_index(item, org_id) if item["status"] in UNPAID else without_unpaid_index(item)
    )


def ledger_item(org_id: str, eid: str, lease_id: str, fields: dict[str, Any], now: str) -> Item:
    return {
        "PK": keys.lease_pk(org_id, lease_id),
        "SK": keys.ledger_sk(eid),
        "entity_type": "ledger",
        "schema_version": 1,
        "org_id": org_id,
        "id": eid,
        "lease_id": lease_id,
        "currency": "MYR",
        "invoice_id": None,
        "payment_id": None,
        "memo": None,
        **fields,
        "created_at": now,
    }


class BillingRepository(OrgRepository):
    # ---- invoices
    def get_invoice(self, iid: str) -> Item | None:
        items, _ = self._t.query(
            pk=keys.entity_gsi1pk(self.org_id, "INV", iid), sk_prefix=keys.META, index="GSI1"
        )
        return items[0] if items else None

    def invoices_of_lease(
        self, lease_id: str, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[Item], str | None]:
        return self._page(
            pk=keys.lease_pk(self.org_id, lease_id),
            sk_prefix="INV#",
            limit=limit,
            cursor=cursor,
        )

    def all_invoices_of_lease(self, lease_id: str) -> list[Item]:
        out: list[Item] = []
        cursor = None
        while True:
            page, cursor = self.invoices_of_lease(lease_id, 100, cursor)
            out += page
            if cursor is None:
                return out

    def invoices_page(
        self, limit: int, cursor: str | None, accept: Any
    ) -> tuple[list[Item], str | None]:
        return self._page(
            pk=keys.all_invoices_gsi4pk(self.org_id),
            sk_prefix=None,
            index="GSI4",
            limit=limit,
            cursor=cursor,
            forward=False,
            accept=accept,
        )

    def unpaid_due_on_or_before(self, due: str) -> list[Item]:
        """GSI3: unpaid invoices whose due date is `due` or earlier (sparse index)."""
        out: list[Item] = []
        start = None
        while True:
            items, start = self._t.query(
                pk=keys.unpaid_gsi3pk(self.org_id), index="GSI3", start_key=start
            )
            out += [i for i in items if i["GSI3SK"][:10] <= due]
            if start is None or (items and items[-1]["GSI3SK"][:10] > due):
                return out

    def invoice_replace_op(self, item: Item, expected_version: int) -> Put:
        return Put(item=item, condition=_VER, names=_NAMES, values={":v": expected_version})

    # ---- invoice keys and numbers
    def invkey_op(self, lease_id: str, period: str, kind: str, invoice_id: str) -> Put:
        return Put(
            item={
                "PK": keys.lease_pk(self.org_id, lease_id),
                "SK": keys.invkey_sk(period, kind),
                "entity_type": "invoice_key",
                "org_id": self.org_id,
                "invoice_id": invoice_id,
            },
            condition=NOT_EXISTS,
        )

    def counter_value(self, year: int) -> int:
        item = self._t.get(self.pk, keys.invoice_counter_sk(year))
        return int(item["value"]) if item else 0

    def counter_op(self, year: int, current: int) -> Put | Update:
        """Serialises invoice numbering per org and year (LLD 6.2): read, +1, conditional write."""
        if current == 0:
            return Put(
                item={
                    "PK": self.pk,
                    "SK": keys.invoice_counter_sk(year),
                    "entity_type": "counter",
                    "org_id": self.org_id,
                    "value": 1,
                },
                condition=NOT_EXISTS,
            )
        return Update(
            key=(self.pk, keys.invoice_counter_sk(year)),
            expression="SET #val = :next",
            condition="#val = :cur",
            names={"#val": "value"},
            values={":next": current + 1, ":cur": current},
        )

    # ---- ledger
    def ledger_page(
        self, lease_id: str, limit: int, cursor: str | None
    ) -> tuple[list[Item], str | None]:
        return self._page(
            pk=keys.lease_pk(self.org_id, lease_id), sk_prefix="LEDGER#", limit=limit, cursor=cursor
        )

    def all_ledger(self, lease_id: str) -> list[Item]:
        out: list[Item] = []
        cursor = None
        while True:
            page, cursor = self.ledger_page(lease_id, 100, cursor)
            out += page
            if cursor is None:
                return out

    def ledger_put_op(self, item: Item) -> Put:
        return Put(
            item=item, condition=NOT_EXISTS
        )  # append-only: there is no update or delete path

    # ---- payments
    def get_payment(self, pid: str) -> Item | None:
        items, _ = self._t.query(
            pk=keys.entity_gsi1pk(self.org_id, "PAY", pid), sk_prefix=keys.META, index="GSI1"
        )
        return items[0] if items else None

    def unmatched_page(self, limit: int, cursor: str | None) -> tuple[list[Item], str | None]:
        return self._page(
            pk=keys.unmatched_pk(self.org_id),
            sk_prefix="PAY#",
            limit=limit,
            cursor=cursor,
            accept=lambda it: it["status"] == "unmatched",
        )

    def payments_of_invoice(self, iid: str) -> list[Item]:
        items, _ = self._t.query(pk=keys.invoice_pk(self.org_id, iid), sk_prefix="PAY#")
        return items

    def allocations_of_invoice(self, iid: str) -> list[Item]:
        items, _ = self._t.query(pk=keys.invoice_pk(self.org_id, iid), sk_prefix="ALLOC#")
        return items

    # ---- runs
    def runs_page(self, limit: int, cursor: str | None) -> tuple[list[Item], str | None]:
        return self._page(
            pk=self.pk, sk_prefix="INVRUN#", limit=limit, cursor=cursor, forward=False
        )

    def get_run(self, period: str, run_id: str) -> Item | None:
        return self._t.get(self.pk, keys.invoice_run_sk(period, run_id))

    def run_put(self, item: Item) -> None:
        self._t.put(item)

    def active_leases(self) -> list[Item]:
        """GSI4: the sparse list of active leases of this org."""
        out: list[Item] = []
        start = None
        while True:
            items, start = self._t.query(
                pk=keys.active_leases_gsi4pk(self.org_id), index="GSI4", start_key=start
            )
            out += items
            if start is None:
                return out


__all__ = ["BillingRepository", "Delete", "invoice_item", "ledger_item", "reindexed"]

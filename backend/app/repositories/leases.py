from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.repositories import keys
from app.repositories.base import Item, OrgRepository
from app.repositories.dynamo import NOT_EXISTS, Delete, Put, Update

_VER = "#v = :v"
_NAMES = {"#v": "version"}


def lease_item(org_id: str, lid: str, fields: dict[str, Any], now: str) -> Item:
    return {
        "PK": keys.org_pk(org_id),
        "SK": keys.lease_sk(lid),
        "GSI1PK": keys.entity_gsi1pk(org_id, "UNIT", fields["unit_id"]),
        "GSI1SK": keys.lease_gsi1sk(fields["start_date"], lid),
        "entity_type": "lease",
        "schema_version": 1,
        "org_id": org_id,
        "id": lid,
        "currency": "MYR",
        "balance_minor": 0,
        "credit_minor": 0,
        "terminated_at": None,
        "amendments": [],
        "deposits_held": [],  # deposits received and not yet returned: filled by payments (phase 3)
        **fields,
        "status": "draft",
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }


def with_active_index(item: Item, org_id: str) -> Item:
    """GSI4 holds only active leases (sparse), LLD 4.5."""
    return {**item, "GSI4PK": keys.active_leases_gsi4pk(org_id), "GSI4SK": item["id"]}


def without_active_index(item: Item) -> Item:
    return {k: v for k, v in item.items() if k not in ("GSI4PK", "GSI4SK")}


class LeaseRepository(OrgRepository):
    def get(self, lid: str) -> Item | None:
        return self._t.get(self.pk, keys.lease_sk(lid))

    def get_many(self, ids: list[str]) -> list[Item]:
        return self._t.batch_get([(self.pk, keys.lease_sk(i)) for i in ids])

    def page(
        self,
        limit: int,
        cursor: str | None,
        status: str | None,
        also: Callable[[Item], bool] | None = None,
    ) -> tuple[list[Item], str | None]:
        return self._page(
            pk=self.pk,
            sk_prefix="LEASE#",
            limit=limit,
            cursor=cursor,
            accept=lambda it: (
                (status is None or it["status"] == status) and (also is None or also(it))
            ),
        )

    def page_for_unit(
        self, uid: str, limit: int, cursor: str | None, status: str | None
    ) -> tuple[list[Item], str | None]:
        return self._page(
            pk=keys.entity_gsi1pk(self.org_id, "UNIT", uid),
            sk_prefix="LEASE#",
            index="GSI1",
            limit=limit,
            cursor=cursor,
            accept=lambda it: status is None or it["status"] == status,
        )

    def page_links(self, tid: str, limit: int, cursor: str | None) -> tuple[list[Item], str | None]:
        return self._page(
            pk=self.pk, sk_prefix=keys.tenant_lease_prefix(tid), limit=limit, cursor=cursor
        )

    def all_links(self, tid: str) -> list[Item]:
        out: list[Item] = []
        cursor = None
        while True:
            page, cursor = self.page_links(tid, 100, cursor)
            out += page
            if cursor is None:
                return out

    def create_op(self, item: Item) -> Put:
        return Put(item=item, condition=NOT_EXISTS)

    def replace_op(self, item: Item, expected_version: int) -> Put:
        return Put(item=item, condition=_VER, names=_NAMES, values={":v": expected_version})

    def link_put_op(self, tid: str, lid: str) -> Put:
        return Put(
            item={
                "PK": self.pk,
                "SK": keys.tenant_lease_sk(tid, lid),
                "GSI5PK": keys.subject_gsi5pk(self.org_id, tid),
                "GSI5SK": f"LEASELINK#{lid}",
                "entity_type": "tenant_lease",
                "org_id": self.org_id,
                "tenant_id": tid,
                "lease_id": lid,
            },
            condition=NOT_EXISTS,
        )

    def link_delete_op(self, tid: str, lid: str) -> Delete:
        return Delete(key=(self.pk, keys.tenant_lease_sk(tid, lid)))

    # ---- unit lock: the optimistic guard against overlapping leases (LLD 4.2, plan P2-03)
    def get_lock(self, uid: str) -> Item | None:
        return self._t.get(self.pk, keys.unit_lock_sk(uid))

    def lock_op(self, uid: str, periods: list[dict[str, str]], current: Item | None) -> Put:
        item = {
            "PK": self.pk,
            "SK": keys.unit_lock_sk(uid),
            "entity_type": "unit_lock",
            "org_id": self.org_id,
            "unit_id": uid,
            "periods": periods,
            "lease_version": (current["lease_version"] + 1) if current else 1,
        }
        if current is None:
            return Put(item=item, condition=NOT_EXISTS)
        return Put(
            item=item,
            condition="lease_version = :lv",
            values={":lv": current["lease_version"]},
        )

    def unit_update_op(
        self, unit: Item, *, status: str, current_lease_id: str | None, now: str
    ) -> Update:
        return Update(
            key=(unit["PK"], unit["SK"]),
            expression="SET #s = :s, current_lease_id = :c, version = version + :one, updated_at = :now",
            condition=_VER,
            names={"#s": "status", "#v": "version"},
            values={
                ":s": status,
                ":c": current_lease_id,
                ":one": 1,
                ":now": now,
                ":v": unit["version"],
            },
        )

    def parent_rooms_op(self, parent: Item, delta: int, now: str) -> Update:
        """Keep a whole unit's `active_room_leases` in step with its room leases (LLD 5.5).
        Version-checked, so it also fails if the letting mode changed after we looked."""
        remaining = parent["active_room_leases"] + delta
        return Update(
            key=(parent["PK"], parent["SK"]),
            expression=(
                "SET #s = :s, version = version + :one, updated_at = :now ADD active_room_leases :d"
            ),
            condition="#v = :v AND letting_mode = :rooms AND active_room_leases >= :min",
            names={"#s": "status", "#v": "version"},
            values={
                ":s": "occupied" if remaining > 0 else "vacant",
                ":one": 1,
                ":now": now,
                ":d": delta,
                ":v": parent["version"],
                ":rooms": "rooms",
                ":min": 1 if delta < 0 else 0,
            },
        )

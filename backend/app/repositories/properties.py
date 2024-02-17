"""Properties and units. Property SK is PROP#p, unit SK is PROP#p#UNIT#u (LLD 4.2, 4.3)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.repositories import keys
from app.repositories.base import Item, OrgRepository
from app.repositories.dynamo import NOT_EXISTS, Check, Delete, Put, Update

_VER_COND = "#v = :v"
_VER_NAMES = {"#v": "version"}


def property_item(org_id: str, pid: str, fields: dict[str, Any], now: str) -> Item:
    return {
        "PK": keys.org_pk(org_id),
        "SK": keys.prop_sk(pid),
        "GSI1PK": keys.entity_gsi1pk(org_id, "PROP", pid),
        "GSI1SK": keys.META,
        "entity_type": "property",
        "schema_version": 1,
        "org_id": org_id,
        "id": pid,
        **fields,
        "status": "active",
        "unit_count": 0,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }


def label_scope(unit: Item) -> str:
    """Labels are unique per property for whole units, per parent unit for rooms, and per property for
    parking bays (in their own namespace, so unit A-1 and bay A-1 can both exist)."""
    if unit["type"] == "room":
        return str(unit["parent_unit_id"])
    if unit["type"] == "parking":
        return f"BAY#{unit['property_id']}"
    return str(unit["property_id"])


def unit_item(org_id: str, pid: str, uid: str, fields: dict[str, Any], now: str) -> Item:
    """A whole unit, a room (type=room, parent_unit_id) or a parking bay (type=parking, optionally allocated
    to a whole unit through parent_unit_id). Rooms and bays are units everywhere else. A bay sits at property
    level (PROP#p#UNIT#u) like a whole unit; its allocation is only an attribute."""
    is_room = fields.get("type") == "room"
    extra: dict[str, Any] = (
        {"letting_mode": None}
        if is_room or fields.get("type") == "parking"
        else {"letting_mode": fields.get("letting_mode") or "whole", "active_room_leases": 0}
    )
    return {
        "PK": keys.org_pk(org_id),
        "SK": keys.room_sk(pid, fields["parent_unit_id"], uid)
        if is_room
        else keys.unit_sk(pid, uid),
        "GSI1PK": keys.entity_gsi1pk(org_id, "UNIT", uid),
        "GSI1SK": keys.META,
        "entity_type": "unit",
        "schema_version": 1,
        "org_id": org_id,
        "id": uid,
        "property_id": pid,
        **{k: v for k, v in fields.items() if k != "letting_mode"},
        **extra,
        "status": "vacant",
        "current_lease_id": None,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }


def _norm(item: Item | None) -> Item | None:
    """Items written before contract v0.9.0 say `archived`; the API and the code say `inactive`."""
    if item is not None and item.get("status") == "archived":
        return {**item, "status": "inactive"}
    return item


def _norm_all(items: list[Item]) -> list[Item]:
    return [i for i in (_norm(x) for x in items) if i is not None]


class PropertyRepository(OrgRepository):
    def get(self, pid: str) -> Item | None:
        return _norm(self._t.get(self.pk, keys.prop_sk(pid)))

    def get_many(self, ids: list[str]) -> list[Item]:
        return _norm_all(self._t.batch_get([(self.pk, keys.prop_sk(i)) for i in ids]))

    def page(
        self,
        limit: int,
        cursor: str | None,
        status: str | None,
        also: Callable[[Item], bool] | None = None,
    ) -> tuple[list[Item], str | None]:
        # unit items share the PROP# prefix, so filter on entity_type
        items, nxt = self._page(
            pk=self.pk,
            sk_prefix="PROP#",
            limit=limit,
            cursor=cursor,
            accept=lambda it: (
                it["entity_type"] == "property"
                and (status is None or (_norm(it) or {}).get("status") == status)
                and (also is None or also(it))
            ),
        )
        return _norm_all(items), nxt

    def create_op(self, item: Item) -> Put:
        return Put(item=item, condition=NOT_EXISTS)

    def replace_op(self, item: Item, expected_version: int) -> Put:
        return Put(
            item=item, condition=_VER_COND, names=_VER_NAMES, values={":v": expected_version}
        )

    def remove_unit_op(self, pid: str, now: str) -> Update:
        """A whole unit was deleted (made by mistake): the opposite of add_unit_op."""
        return Update(
            key=(self.pk, keys.prop_sk(pid)),
            expression="SET unit_count = unit_count - :one, version = version + :one, updated_at = :now",
            condition="attribute_exists(PK) AND unit_count > :zero",
            values={":one": 1, ":zero": 0, ":now": now},
        )

    def add_unit_op(self, pid: str, now: str) -> Update:
        """Bumps `version` so a concurrent property update cannot overwrite unit_count."""
        return Update(
            key=(self.pk, keys.prop_sk(pid)),
            expression="SET unit_count = unit_count + :one, version = version + :one, updated_at = :now",
            condition="attribute_exists(PK) AND #s = :active",
            names={"#s": "status"},
            values={":one": 1, ":now": now, ":active": "active"},
        )


class UnitRepository(OrgRepository):
    def get(self, uid: str) -> Item | None:
        items, _ = self._t.query(
            pk=keys.entity_gsi1pk(self.org_id, "UNIT", uid), sk_prefix=keys.META, index="GSI1"
        )
        return _norm(items[0]) if items else None

    def page(
        self,
        pid: str,
        limit: int,
        cursor: str | None,
        status: str | None,
        unit_type: str | None = None,
        parent_id: str | None = None,
    ) -> tuple[list[Item], str | None]:
        """Whole units and rooms share the PROP#p#UNIT# prefix, a room sorts right after its parent."""
        items, nxt = self._page(
            pk=self.pk,
            sk_prefix=keys.room_prefix(pid, parent_id) if parent_id else keys.unit_prefix(pid),
            limit=limit,
            cursor=cursor,
            accept=lambda it: (
                (status is None or (_norm(it) or {}).get("status") == status)
                and (unit_type is None or it["type"] == unit_type)
            ),
        )
        return _norm_all(items), nxt

    def rooms_of(self, pid: str, parent_id: str) -> list[Item]:
        out: list[Item] = []
        cursor = None
        while True:
            page, cursor = self.page(pid, 100, cursor, None, parent_id=parent_id)
            out += page
            if cursor is None:
                return out

    def parent_check_op(self, parent: Item) -> Check:
        """A room may only be created under a live whole unit that is let by rooms (LLD 5.5)."""
        return Check(
            key=(parent["PK"], parent["SK"]),
            condition="#t = :wu AND letting_mode = :rooms AND NOT #s IN (:arch, :inactive)",
            names={"#t": "type", "#s": "status"},
            values={
                ":wu": "whole_unit",
                ":rooms": "rooms",
                ":arch": "archived",
                ":inactive": "inactive",
            },
        )

    def parent_live_check_op(self, parent: Item) -> Check:
        """A bay may only be allocated to a whole unit that is still on (contract v0.10.0)."""
        return Check(
            key=(parent["PK"], parent["SK"]),
            condition="#t = :wu AND NOT #s IN (:arch, :inactive)",
            names={"#t": "type", "#s": "status"},
            values={":wu": "whole_unit", ":arch": "archived", ":inactive": "inactive"},
        )

    def property_active_check_op(self, pid: str) -> Check:
        return Check(
            key=(self.pk, keys.prop_sk(pid)),
            condition="#s = :active",
            names={"#s": "status"},
            values={":active": "active"},
        )

    def letting_mode_guard_op(self, uid: str) -> Check:
        """No active or future lease on the unit itself (its UNITLOCK has no periods)."""
        return Check(
            key=(self.pk, keys.unit_lock_sk(uid)),
            condition="attribute_not_exists(PK) OR size(periods) = :zero",
            values={":zero": 0},
        )

    def replace_if_no_room_leases_op(self, item: Item, expected_version: int) -> Put:
        return Put(
            item=item,
            condition="#v = :v AND active_room_leases = :zero",
            names={"#v": "version"},
            values={":v": expected_version, ":zero": 0},
        )

    def all_for_property(self, pid: str) -> list[Item]:
        out: list[Item] = []
        cursor = None
        while True:
            page, cursor = self.page(pid, 100, cursor, None)
            out += page
            if cursor is None:
                return out

    def create_op(self, item: Item) -> Put:
        return Put(item=item, condition=NOT_EXISTS)

    def replace_op(self, item: Item, expected_version: int) -> Put:
        return Put(
            item=item, condition=_VER_COND, names=_VER_NAMES, values={":v": expected_version}
        )

    def label_lock_op(self, scope: str, label: str, uid: str) -> Put:
        return Put(
            item={
                "PK": self.pk,
                "SK": keys.unit_label_lock_sk(scope, label),
                "entity_type": "unit_label_lock",
                "org_id": self.org_id,
                "unit_id": uid,
            },
            condition=NOT_EXISTS,
        )

    def label_unlock_op(self, scope: str, label: str) -> Delete:
        """Labels are unique per property for whole units and per parent unit for rooms."""
        return Delete(key=(self.pk, keys.unit_label_lock_sk(scope, label)))

from __future__ import annotations

import contextlib
from typing import Any

from app.container import Container
from app.core.access import staff_access
from app.core.clock import iso
from app.core.concurrency import retry_on_version_conflict
from app.core.context import RequestContext, Role
from app.core.dates import today_kl
from app.core.errors import (
    Conflict,
    Forbidden,
    InvalidState,
    NotFound,
    QuotaExceeded,
    ValidationFailed,
)
from app.core.ids import new_id
from app.domains.lifecycle import (
    INACTIVE,
    Plan,
    delete_documents,
    documents_of,
    keys_of,
    lease_ever_on,
    maintenance_on,
    remove_files,
    status_in,
    unit_lock_keys,
    write_all,
)
from app.domains.organisations import plan
from app.domains.properties.schemas import (
    PropertyCreate,
    PropertyUpdate,
    UnitCreate,
    UnitUpdate,
)
from app.domains.scope import Scope
from app.repositories import keys
from app.repositories.audit import diff_summary
from app.repositories.dynamo import Delete, TxCancelled
from app.repositories.properties import label_scope, property_item, unit_item


class PropertyService:
    def __init__(self, c: Container, ctx: RequestContext) -> None:
        self._c, self._ctx = c, ctx
        self._props = c.store.properties(ctx.org_id)
        self._units = c.store.units(ctx.org_id)
        self._audit = c.store.audit(ctx.org_id)
        self._scope = Scope(c, ctx)

    # ---- unit limits (contract v0.13.0): the organisation's plan, and a property manager's own limit.
    # Both count whole units that are on, in properties that are on; rooms, bays and empty properties are free.
    def quota(self) -> dict[str, Any] | None:
        """The caller's units against their limit, or None for an org admin."""
        if not self._scope.restricted:
            return None
        member = self._c.store.members(self._ctx.org_id).get(self._ctx.user_id) or {}
        used = plan.unit_usage(self._c, self._ctx.org_id)[1].get(self._ctx.user_id, 0)
        return {"used": used, "limit": plan.member_quota(member)}

    def _check_units(self, prop: dict[str, Any], adding: int) -> None:
        """Refuse `adding` more units in this property past the plan or its manager's limit."""
        manager_id = prop.get("manager_id")
        manager = self._c.store.members(self._ctx.org_id).get(manager_id) if manager_id else None
        plan.check_units(
            self._c,
            self._ctx.org_id,
            self._c.store.org(self._ctx.org_id).get_meta(),
            adding,
            manager_id,
            manager,
            own=manager_id == self._ctx.user_id,
        )

    def _counted_units(self, pid: str) -> int:
        return sum(1 for u in self._units.all_for_property(pid) if plan.is_counted(u))

    def create(self, body: PropertyCreate) -> dict[str, Any]:
        # an empty property is free: units are counted as they are added (contract v0.13.0)
        now = self._c.clock.now()
        pid = new_id()
        # the creator manages it; an admin can reassign it (manager scope, app/domains/scope.py)
        fields = {**body.model_dump(), "manager_id": self._ctx.user_id}
        item = property_item(self._ctx.org_id, pid, fields, iso(now))
        self._c.store.table.transact(
            [
                self._props.create_op(item),
                self._audit.put_for(
                    self._ctx, now, "property.create", "property", pid, {"type": body.type}
                ),
            ]
        )
        return {**item, "units_active": 0}  # a new property has no units yet

    def get(self, pid: str) -> dict[str, Any]:
        item = self._props.get(pid)
        if item is None or not self._scope.property_ok(pid):
            raise NotFound()
        return self._with_active(item)

    def _with_active(self, item: dict[str, Any]) -> dict[str, Any]:
        """Adds `units_active`, the whole units that are on, so the list shows what the plan counts
        (`unit_count` also includes units that are off). One query per property (LLD open item 16)."""
        return {**item, "units_active": self._counted_units(item["id"])}

    def page(
        self, limit: int, cursor: str | None, status: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        me = self._ctx.user_id
        mine = (lambda p: p.get("manager_id") == me) if self._scope.restricted else None
        items, nxt = self._props.page(limit, cursor, status, also=mine)
        return [self._with_active(p) for p in items], nxt

    def _check_manager(self, user_id: str) -> dict[str, Any]:
        """Only an admin reassigns, and only to an active manager or admin of this organisation."""
        if Role.ORG_ADMIN not in self._ctx.roles:
            raise Forbidden("Only an administrator can change who manages a property")
        member = self._c.store.members(self._ctx.org_id).get(user_id)
        if (
            member is None
            or staff_access(member, today_kl(self._c.clock)) in ("disabled", "expired")
            or not {"org_admin", "property_manager"} & set(member.get("roles", []))
        ):
            raise ValidationFailed(
                details=[
                    {"field": "manager_id", "issue": "not an active manager of this organisation"}
                ]
            )
        return member

    def update(self, pid: str, body: PropertyUpdate) -> dict[str, Any]:
        changes = body.model_dump(exclude_none=True)
        if "manager_id" in changes:
            target = self._check_manager(changes["manager_id"])
            current = self._props.get(pid)
            if current is not None and current.get("manager_id") != changes["manager_id"]:
                # the new manager takes the property's units on: the organisation's total does not change
                used = plan.unit_usage(self._c, self._ctx.org_id)[1].get(changes["manager_id"], 0)
                quota = plan.member_quota(target) if plan._limited(target) else None
                adding = self._counted_units(pid) if current["status"] == "active" else 0
                if quota is not None and used + adding > quota:
                    who = target.get("name") or target.get("email") or "This manager"
                    raise QuotaExceeded(
                        f"{who} already manages {used} of {quota} units. Raise their limit first.",
                        params={"scope": "manager", "limit": quota, "used": used, "name": who},
                    )

        def attempt() -> dict[str, Any]:
            old = self.get(pid)
            if old["status"] == INACTIVE:
                raise InvalidState("Reactivate the property before editing it")
            now = self._c.clock.now()
            new = {**old, **changes, "version": old["version"] + 1, "updated_at": iso(now)}
            self._c.store.table.transact(
                [
                    self._props.replace_op(new, old["version"]),
                    self._audit.put_for(
                        self._ctx,
                        now,
                        "property.update",
                        "property",
                        pid,
                        diff_summary(
                            old, new, ["name", "type", "address", "manager_id"], opaque=("address",)
                        ),
                    ),
                ]
            )
            return new

        return retry_on_version_conflict(attempt)

    # ---- lifecycle (contract v0.9.0): deactivate, reactivate, delete
    def _busy(self, units: list[dict[str, Any]]) -> list[str]:
        """Labels of units with a lease that is live or still to come (active, upcoming or draft)."""
        leases = self._c.store.leases(self._ctx.org_id)
        out: list[str] = []
        for u in units:
            if u["status"] == "occupied" or u.get("active_room_leases", 0) > 0:
                out.append(u["label"])
                continue
            lock = leases.get_lock(u["id"])
            if (lock and lock["periods"]) or leases.page_for_unit(u["id"], 1, None, "draft")[0]:
                out.append(u["label"])
        return out

    @staticmethod
    def _listing(labels: list[str]) -> str:
        return ", ".join(labels[:5]) + (f" and {len(labels) - 5} more" if len(labels) > 5 else "")

    def _set_status(
        self, item: dict[str, Any], status: str, now: Any, **extra: Any
    ) -> dict[str, Any]:
        new = {k: v for k, v in item.items() if k != "deactivated_with"}
        new.update(status=status, version=item["version"] + 1, updated_at=iso(now), **extra)
        return new

    def deactivate(self, pid: str) -> dict[str, Any]:
        """Hide the property and its units from lists and new leases. Nothing is lost; reactivate undoes it."""

        def attempt() -> dict[str, Any]:
            old = self.get(pid)
            if old["status"] == INACTIVE:
                return old
            units = self._units.all_for_property(pid)
            busy = self._busy(units)
            if busy:
                raise InvalidState(
                    f"End or delete the leases on {self._listing(busy)} before deactivating this property"
                )
            now = self._c.clock.now()
            new = self._set_status(old, INACTIVE, now)
            live = [u for u in units if u["status"] != INACTIVE]
            self._c.store.table.transact(
                [
                    self._props.replace_op(new, old["version"]),
                    self._audit.put_for(
                        self._ctx,
                        now,
                        "property.deactivate",
                        "property",
                        pid,
                        {"units_deactivated": len(live)},
                    ),
                ]
            )
            for u in live:  # cascade; each write is version-checked and safe to repeat
                off = self._set_status(u, INACTIVE, now, deactivated_with=pid)
                # if concurrently changed, skip: the unit is unreachable via an inactive property
                with contextlib.suppress(TxCancelled):
                    self._c.store.table.transact([self._units.replace_op(off, u["version"])])
            return new

        return retry_on_version_conflict(attempt)

    def reactivate(self, pid: str) -> dict[str, Any]:
        """Undo deactivate. Units switched off together with the property come back; ones deactivated on
        their own stay off. The unit limits (plan, manager) apply to the units that come back."""

        def attempt() -> dict[str, Any]:
            old = self.get(pid)
            if old["status"] == "active":
                return old
            units = [
                u
                for u in self._units.all_for_property(pid)
                if u["status"] == INACTIVE and u.get("deactivated_with") == pid
            ]
            self._check_units(old, sum(1 for u in units if u["type"] == "whole_unit"))
            now = self._c.clock.now()
            new = self._set_status(old, "active", now)
            self._c.store.table.transact(
                [
                    self._props.replace_op(new, old["version"]),
                    self._audit.put_for(
                        self._ctx,
                        now,
                        "property.reactivate",
                        "property",
                        pid,
                        {"units_reactivated": len(units)},
                    ),
                ]
            )
            for u in units:
                with contextlib.suppress(TxCancelled):
                    self._c.store.table.transact(
                        [self._units.replace_op(self._set_status(u, "vacant", now), u["version"])]
                    )
            return new

        return retry_on_version_conflict(attempt)

    def _property_deletion(
        self, pid: str
    ) -> tuple[Plan, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        prop = self.get(pid)
        org = self._ctx.org_id
        units = self._units.all_for_property(pid)
        plan = Plan()
        with_leases = [u["label"] for u in units if lease_ever_on(self._c, org, u["id"])]
        if with_leases:
            plan.block(
                "lease_history",
                f"{self._listing(with_leases)} {'has' if len(with_leases) == 1 else 'have'} lease records, "
                "which are kept as financial records. Deactivate the property instead: it is deleted "
                "automatically when the retention period ends.",
                what="property",
                units=", ".join(with_leases[:5]),  # labels, then how many more
                more=max(len(with_leases) - 5, 0),
                count=len(with_leases),
            )
        requests = maintenance_on(self._c, org, property_id=pid, unit_ids={u["id"] for u in units})
        if requests:
            plan.block(
                "maintenance_history",
                f"The property has {requests} maintenance request(s). Delete them first, or deactivate "
                "the property instead.",
                what="property",
                count=requests,
            )
        docs = documents_of(self._c, org, "property", pid)
        for u in units:
            docs += documents_of(self._c, org, "unit", u["id"])
        plan.count("units", sum(1 for u in units if u["type"] == "whole_unit"))
        plan.count("rooms", sum(1 for u in units if u["type"] == "room"))
        plan.count("documents", len(docs))
        return plan, prop, units, docs

    def deletion(self, pid: str) -> dict[str, Any]:
        return self._property_deletion(pid)[0].view()

    def delete(self, pid: str, reason: str) -> dict[str, Any]:
        """Remove a property that never had a lease (made by mistake), with its units, rooms and files."""
        plan, prop, units, docs = self._property_deletion(pid)
        plan.require_not_blocked()
        now = self._c.clock.now()
        ops: list[Any] = [
            self._audit.put_for(
                self._ctx,
                now,
                "property.delete",
                "property",
                pid,
                {"reason": reason, **plan.affects},
            ),
            Delete(
                key=(prop["PK"], prop["SK"]),
                condition="#v = :v",
                names={"#v": "version"},
                values={":v": prop["version"]},
            ),
        ]
        ops += self._unit_removal_ops(units)
        ops += delete_documents(self._c, docs)
        write_all(self._c, ops)
        remove_files(self._c, docs)
        return {"outcome": "removed", "privacy_request_id": None}

    def _unit_removal_ops(self, units: list[dict[str, Any]]) -> list[Any]:
        ops: list[Any] = keys_of(units)
        for u in units:  # labels are unique per property, or per parent unit for rooms
            ops.append(self._units.label_unlock_op(label_scope(u), u["label"]))
        ops += unit_lock_keys(self._ctx.org_id, [u["id"] for u in units])
        return ops

    # ---- units
    def _active_property(self, pid: str) -> dict[str, Any]:
        prop = self.get(pid)
        if prop["status"] != "active":
            raise InvalidState("The property is inactive")
        return prop

    def create_unit(self, pid: str, body: UnitCreate) -> dict[str, Any]:
        prop = self._active_property(pid)
        if body.type == "whole_unit":
            self._check_units(prop, 1)  # rooms and bays are free (contract v0.13.0)
        now = self._c.clock.now()
        uid = new_id()
        item = unit_item(self._ctx.org_id, pid, uid, body.model_dump(), iso(now))
        if body.type == "parking":
            return self._create_bay(pid, uid, body, item, now)
        if body.type == "room":
            return self._create_room(pid, uid, body, item, now)
        try:
            self._c.store.table.transact(
                [
                    self._props.add_unit_op(pid, iso(now)),
                    self._units.create_op(item),
                    self._units.label_lock_op(pid, body.label, uid),
                    self._audit.put_for(
                        self._ctx,
                        now,
                        "unit.create",
                        "unit",
                        uid,
                        {
                            "property_id": pid,
                            "type": "whole_unit",
                            "letting_mode": item["letting_mode"],
                        },
                    ),
                ]
            )
        except TxCancelled as e:
            if e.failed(2):
                raise Conflict("A unit with this label already exists in the property") from None
            if e.failed(0):
                raise InvalidState("The property is inactive") from None
            raise
        return item

    # ---- parking bays (contract v0.10.0)
    def _bays_of(self, unit: dict[str, Any]) -> list[dict[str, Any]]:
        """Bays allocated to this whole unit."""
        return [
            u
            for u in self._units.all_for_property(unit["property_id"])
            if u["type"] == "parking" and u.get("parent_unit_id") == unit["id"]
        ]

    def _allocation_parent(self, pid: str, parent_id: str) -> dict[str, Any]:
        parent = self._units.get(parent_id)
        if parent is None or parent["property_id"] != pid or parent["type"] != "whole_unit":
            raise ValidationFailed(
                details=[{"field": "parent_unit_id", "issue": "not a whole unit in this property"}]
            )
        if parent["status"] == INACTIVE:
            raise InvalidState("The unit is inactive")
        return parent

    def _check_allocation(self, bay: dict[str, Any], parent_id: str | None) -> None:
        if bay["type"] != "parking":
            raise ValidationFailed(
                details=[
                    {"field": "parent_unit_id", "issue": "only a parking bay can be re-allocated"}
                ]
            )
        if parent_id is not None:
            self._allocation_parent(bay["property_id"], parent_id)
        if parent_id != bay.get("parent_unit_id") and self._busy([bay]):
            raise InvalidState("End or delete the bay's lease before changing its allocation")

    def _create_bay(
        self, pid: str, uid: str, body: UnitCreate, item: dict[str, Any], now: Any
    ) -> dict[str, Any]:
        ops: list[Any] = [self._units.property_active_check_op(pid)]
        if body.parent_unit_id:
            parent = self._allocation_parent(pid, body.parent_unit_id)
            ops.append(self._units.parent_live_check_op(parent))
        ops += [
            self._units.create_op(item),
            self._units.label_lock_op(label_scope(item), body.label, uid),
            self._audit.put_for(
                self._ctx,
                now,
                "unit.create",
                "unit",
                uid,
                {"property_id": pid, "type": "parking", "parent_unit_id": body.parent_unit_id},
            ),
        ]
        try:
            self._c.store.table.transact(ops)
        except TxCancelled as e:
            if e.failed(len(ops) - 2):
                raise Conflict("A bay with this label already exists in the property") from None
            raise InvalidState("The property or unit changed, retry") from None
        return item

    def _create_room(
        self, pid: str, uid: str, body: UnitCreate, item: dict[str, Any], now: Any
    ) -> dict[str, Any]:
        parent = self._units.get(body.parent_unit_id or "")
        if parent is None or parent["property_id"] != pid:
            raise ValidationFailed(
                details=[{"field": "parent_unit_id", "issue": "unknown unit in this property"}]
            )
        if parent["type"] != "whole_unit":
            raise ValidationFailed(
                details=[{"field": "parent_unit_id", "issue": "rooms cannot contain rooms"}]
            )
        if parent["status"] == INACTIVE:
            raise InvalidState("The parent unit is inactive")
        if parent["letting_mode"] != "rooms":
            raise InvalidState(
                "The parent unit is let as a whole: set its letting_mode to rooms first"
            )
        try:
            self._c.store.table.transact(
                [
                    self._units.property_active_check_op(pid),
                    self._units.parent_check_op(parent),  # re-checked atomically with the insert
                    self._units.create_op(item),
                    self._units.label_lock_op(parent["id"], body.label, uid),
                    self._audit.put_for(
                        self._ctx,
                        now,
                        "unit.create",
                        "unit",
                        uid,
                        {"property_id": pid, "type": "room", "parent_unit_id": parent["id"]},
                    ),
                ]
            )
        except TxCancelled as e:
            if e.failed(3):
                raise Conflict("A room with this label already exists in the unit") from None
            if e.failed(0) or e.failed(1):
                raise InvalidState("The parent unit changed, retry") from None
            raise
        return item

    def list_units(
        self,
        pid: str,
        limit: int,
        cursor: str | None,
        status: str | None,
        unit_type: str | None = None,
        parent_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        self.get(pid)
        if parent_id:
            parent = self._units.get(parent_id)
            if parent is None or parent["property_id"] != pid:
                raise NotFound()
        return self._units.page(pid, limit, cursor, status, unit_type, parent_id)

    def get_unit(self, uid: str) -> dict[str, Any]:
        unit = self._units.get(uid)
        if unit is None:
            raise NotFound()
        if self._ctx.roles & {Role.ORG_ADMIN, Role.PROPERTY_MANAGER}:
            self._scope.require_property(unit["property_id"])
        else:
            # a tenant may only see the unit of one of their own (non-draft) leases
            leases = self._c.store.leases(self._ctx.org_id)
            tid = self._ctx.tenant_id
            ids = [link["lease_id"] for link in leases.all_links(tid)] if tid else []
            if not any(
                x["unit_id"] == uid and x["status"] != "draft" for x in leases.get_many(ids)
            ):
                raise Forbidden()
        return unit

    def update_unit(self, uid: str, body: UnitUpdate) -> dict[str, Any]:
        changes = body.model_dump(exclude_unset=True)
        for k in ("status", "label", "default_rent", "letting_mode"):
            if k in changes and changes[k] is None:
                del changes[k]
        # status `inactive` (or its old name `archived`) is deactivation, and leaving it is reactivation
        status = status_in(changes.pop("status", None))
        if status == INACTIVE:
            if changes:
                self._update_unit(uid, changes)
            return self.deactivate_unit(uid)
        if status is not None and self.get_unit(uid)["status"] == INACTIVE:
            self.reactivate_unit(uid)  # comes back as vacant
            status = None if status == "vacant" else status
        if status is not None:
            changes["status"] = status
        if not changes:
            return self.get_unit(uid)
        return self._update_unit(uid, changes)

    def _update_unit(self, uid: str, changes: dict[str, Any]) -> dict[str, Any]:
        def attempt() -> dict[str, Any]:
            old = self.get_unit(uid)
            self._active_property(old["property_id"])
            if "status" in changes and old["status"] == "occupied":
                raise InvalidState("An occupied unit's status follows its lease")
            mode_change = (
                "letting_mode" in changes and changes["letting_mode"] != old["letting_mode"]
            )
            if "letting_mode" in changes and old["type"] != "whole_unit":
                raise ValidationFailed(
                    details=[{"field": "letting_mode", "issue": "applies to whole units only"}]
                )
            if "parent_unit_id" in changes:
                self._check_allocation(old, changes["parent_unit_id"])
            if mode_change:
                lock = self._c.store.leases(self._ctx.org_id).get_lock(uid)
                if old["active_room_leases"] > 0 or (lock and lock["periods"]):
                    raise InvalidState(
                        "The letting mode cannot change while the unit or its rooms have an active or future lease"
                    )
                if changes["letting_mode"] == "whole":  # its bays would come with it again
                    busy = self._busy(self._bays_of(old))
                    if busy:
                        raise InvalidState(
                            f"Bays {self._listing(busy)} are let separately. End or delete those "
                            "leases before letting the unit as a whole"
                        )
            now = self._c.clock.now()
            new = {**old, **changes, "version": old["version"] + 1, "updated_at": iso(now)}
            scope = label_scope(old)
            ops: list[Any] = [
                self._units.replace_if_no_room_leases_op(new, old["version"])
                if mode_change
                else self._units.replace_op(new, old["version"])
            ]
            if mode_change:
                ops.append(self._units.letting_mode_guard_op(uid))
            relabel = keys.unit_label_lock_sk(scope, old["label"]) != keys.unit_label_lock_sk(
                scope, new["label"]
            )
            label_idx = -1
            guard_idx = 1 if mode_change else -1
            if relabel:
                ops += [
                    self._units.label_unlock_op(scope, old["label"]),
                    self._units.label_lock_op(scope, new["label"], uid),
                ]
                label_idx = len(ops) - 1
            ops.append(
                self._audit.put_for(
                    self._ctx,
                    now,
                    "unit.update",
                    "unit",
                    uid,
                    diff_summary(
                        old,
                        new,
                        [
                            "label",
                            "bedrooms",
                            "bathrooms",
                            "size_sqft",
                            "default_rent",
                            "status",
                            "letting_mode",
                        ],
                    ),
                )
            )
            try:
                self._c.store.table.transact(ops)
            except TxCancelled as e:
                if relabel and e.failed(label_idx):
                    raise Conflict("A unit with this label already exists here") from None
                if mode_change and e.failed(guard_idx):
                    raise InvalidState("The unit now has an active or future lease") from None
                raise
            return new

        return retry_on_version_conflict(attempt, attempts=4, index=0)

    # ---- unit lifecycle (contract v0.9.0)
    def deactivate_unit(self, uid: str) -> dict[str, Any]:
        """A whole unit and its rooms are switched off together."""

        def attempt() -> dict[str, Any]:
            old = self.get_unit(uid)
            if old["status"] == INACTIVE:
                return old
            rooms = (
                self._units.rooms_of(old["property_id"], uid) if old["type"] == "whole_unit" else []
            )
            busy = self._busy([old, *rooms])
            if busy:
                raise InvalidState(
                    f"End or delete the leases on {self._listing(busy)} before deactivating"
                )
            live_rooms = [r for r in rooms if r["status"] != INACTIVE]
            if len(live_rooms) > 80:
                raise InvalidState("Deactivate some rooms first, this unit has too many")
            now = self._c.clock.now()
            new = self._set_status(old, INACTIVE, now)
            ops: list[Any] = [self._units.replace_op(new, old["version"])]
            ops += [
                self._units.replace_op(
                    self._set_status(r, INACTIVE, now, deactivated_with=uid), r["version"]
                )
                for r in live_rooms
            ]
            ops.append(
                self._audit.put_for(
                    self._ctx,
                    now,
                    "unit.deactivate",
                    "unit",
                    uid,
                    {"rooms_deactivated": len(live_rooms)} if live_rooms else {},
                )
            )
            self._c.store.table.transact(ops)
            return new

        return retry_on_version_conflict(attempt, attempts=4, index=0)

    def reactivate_unit(self, uid: str) -> dict[str, Any]:
        """Undo deactivate. Rooms switched off with their unit come back with it."""

        def attempt() -> dict[str, Any]:
            old = self.get_unit(uid)
            if old["status"] != INACTIVE:
                return old
            prop = self.get(old["property_id"])
            if prop["status"] != "active":
                raise InvalidState("Reactivate the property first")
            if old["type"] == "whole_unit":
                self._check_units(prop, 1)
            if old["type"] == "room":
                parent = self._units.get(old["parent_unit_id"])
                if parent is None or parent["status"] == INACTIVE:
                    raise InvalidState("Reactivate the room's unit first")
            rooms = (
                [
                    r
                    for r in self._units.rooms_of(old["property_id"], uid)
                    if r["status"] == INACTIVE and r.get("deactivated_with") == uid
                ]
                if old["type"] == "whole_unit"
                else []
            )
            now = self._c.clock.now()
            new = self._set_status(old, "vacant", now)
            ops: list[Any] = [self._units.replace_op(new, old["version"])]
            ops += [
                self._units.replace_op(self._set_status(r, "vacant", now), r["version"])
                for r in rooms[:80]
            ]
            ops.append(
                self._audit.put_for(
                    self._ctx,
                    now,
                    "unit.reactivate",
                    "unit",
                    uid,
                    {"rooms_reactivated": len(rooms)} if rooms else {},
                )
            )
            self._c.store.table.transact(ops)
            return new

        return retry_on_version_conflict(attempt, attempts=4, index=0)

    def _unit_deletion(
        self, uid: str
    ) -> tuple[Plan, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        unit = self.get_unit(uid)
        org = self._ctx.org_id
        rooms = (
            self._units.rooms_of(unit["property_id"], uid) if unit["type"] == "whole_unit" else []
        )
        plan = Plan()
        what = "room" if unit["type"] == "room" else "unit"
        if any(lease_ever_on(self._c, org, u["id"]) for u in [unit, *rooms]):
            plan.block(
                "lease_history",
                f"This {what} or its rooms have lease records, which are kept as financial records. "
                f"Deactivate the {what} instead: it is deleted automatically when the retention period ends.",
                what=what,
            )
        requests = maintenance_on(
            self._c, org, property_id="", unit_ids={u["id"] for u in [unit, *rooms]}
        )
        if requests:
            plan.block(
                "maintenance_history",
                f"This {what} has {requests} maintenance request(s). Delete them first, or deactivate "
                f"the {what} instead.",
                what=what,
                count=requests,
            )
        docs: list[dict[str, Any]] = []
        for u in [unit, *rooms]:
            docs += documents_of(self._c, org, "unit", u["id"])
        plan.count("rooms", len(rooms))
        plan.count("documents", len(docs))
        return plan, unit, rooms, docs

    def unit_deletion(self, uid: str) -> dict[str, Any]:
        return self._unit_deletion(uid)[0].view()

    def delete_unit(self, uid: str, reason: str) -> dict[str, Any]:
        """Remove a unit or room that never had a lease (made by mistake), with its rooms and files."""
        plan, unit, rooms, docs = self._unit_deletion(uid)
        plan.require_not_blocked()
        now = self._c.clock.now()
        ops: list[Any] = [
            self._audit.put_for(
                self._ctx,
                now,
                "unit.delete",
                "unit",
                uid,
                {"reason": reason, "type": unit["type"], **plan.affects},
            )
        ]
        if unit["type"] == "whole_unit":
            ops.append(self._props.remove_unit_op(unit["property_id"], iso(now)))
            # bays allocated to it stay, as property bays
            for bay in self._bays_of(unit):
                freed = {**bay, "parent_unit_id": None, "version": bay["version"] + 1}
                ops.append(self._units.replace_op(freed, bay["version"]))
        ops += self._unit_removal_ops([unit, *rooms])
        ops += delete_documents(self._c, docs)
        write_all(self._c, ops)
        remove_files(self._c, docs)
        return {"outcome": "removed", "privacy_request_id": None}

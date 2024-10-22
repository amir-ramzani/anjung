from __future__ import annotations

from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.concurrency import retry_on_version_conflict
from app.core.context import RequestContext, Role
from app.core.errors import Forbidden, InvalidState, NotFound, ValidationFailed
from app.core.ids import new_id
from app.domains.lifecycle import (
    Plan,
    delete_documents,
    documents_of,
    keys_of,
    partition,
    remove_files,
    write_all,
)
from app.domains.maintenance.schemas import (
    CommentCreate,
    MaintenanceRequestCreate,
    MaintenanceRequestUpdate,
)
from app.domains.notifications.events import publish
from app.domains.places import PlaceNames
from app.domains.scope import Scope
from app.repositories import keys
from app.repositories.audit import diff_summary
from app.repositories.dynamo import Delete
from app.repositories.maintenance import comment_item, request_item, with_status_index

STAFF = {Role.ORG_ADMIN, Role.PROPERTY_MANAGER}
TERMINAL = ("closed", "cancelled")
TRANSITIONS = {  # LLD 5.3
    "open": {"triaged", "cancelled"},
    "triaged": {"assigned", "cancelled"},
    "assigned": {"in_progress", "cancelled"},
    "in_progress": {"resolved"},
    "resolved": {"closed", "in_progress"},
    "closed": set(),
    "cancelled": set(),
}
Item = dict[str, Any]


class MaintenanceService:
    def __init__(self, c: Container, ctx: RequestContext) -> None:
        self._c, self._ctx = c, ctx
        self._repo = c.store.maintenance(ctx.org_id)
        self._audit = c.store.audit(ctx.org_id)
        self._scope = Scope(c, ctx)

    @property
    def _is_staff(self) -> bool:
        return bool(self._ctx.roles & STAFF)

    @property
    def _is_tenant(self) -> bool:
        return Role.TENANT in self._ctx.roles and not self._is_staff

    # ---- who may name what (LLD 5.3 Location)
    def _tenant_scope(self) -> tuple[set[str], set[str], set[str]]:
        """(units and rooms the tenant leases now, their parent whole units, their properties)."""
        tid = self._ctx.tenant_id
        if tid is None:
            raise Forbidden()
        leases = self._c.store.leases(self._ctx.org_id)
        units = self._c.store.units(self._ctx.org_id)
        leased: set[str] = set()
        parents: set[str] = set()
        props: set[str] = set()
        for lease in leases.get_many([x["lease_id"] for x in leases.all_links(tid)]):
            if lease["status"] != "active":
                continue
            leased.add(lease["unit_id"])
            props.add(lease["property_id"])
            u = units.get(lease["unit_id"])
            if u is not None and u["parent_unit_id"]:
                parents.add(u["parent_unit_id"])
        return leased, parents, props

    def _check_attachments(self, ids: list[str]) -> None:
        docs = self._c.store.documents(self._ctx.org_id)
        for did in ids:
            d = docs.get(did)
            if d is None or d["status"] == "infected":
                raise ValidationFailed(
                    details=[{"field": "attachment_ids", "issue": "unknown or unusable document"}]
                )
            if not self._is_staff and not (
                d["owner_type"] == "tenant" and d["owner_id"] == self._ctx.tenant_id
            ):
                raise Forbidden()

    # ---- output: who reported it, where, and who is on it (names resolved when read)
    def present(self, items: list[Item]) -> list[Item]:
        names = PlaceNames(self._c, self._ctx)
        places = names.places(items)
        staff_reporters = {
            x["created_by"] for x in items if not x.get("tenant_id") and x.get("created_by")
        }
        assignees = {x["assigned_to"] for x in items if x.get("assigned_to")}
        people = names.people(
            {x["tenant_id"] for x in items if x.get("tenant_id")}, staff_reporters | assignees
        )
        return [
            {
                **x,
                **places.get(x["id"], {}),
                "reported_by_name": people.get(x.get("tenant_id") or x.get("created_by") or ""),
                "assigned_to_name": people.get(x.get("assigned_to") or ""),
            }
            for x in items
        ]

    def present_one(self, item: Item) -> Item:
        return self.present([item])[0]

    # ---- reads
    def get(self, rid: str) -> Item:
        item = self._repo.get(rid)
        if item is None or not self._scope.property_ok(item.get("property_id")):
            raise NotFound()
        if self._is_tenant and item.get("tenant_id") != self._ctx.tenant_id:
            raise Forbidden()
        return item

    def page(
        self,
        limit: int,
        cursor: str | None,
        status: str | None,
        unit_id: str | None,
        property_id: str | None,
    ) -> tuple[list[Item], str | None]:
        def accept(it: Item) -> bool:
            return (
                (status is None or it["status"] == status)
                and (unit_id is None or it["unit_id"] == unit_id)
                and (property_id is None or it["property_id"] == property_id)
                and self._scope.property_ok(it.get("property_id"))
            )

        if self._is_tenant:
            if self._ctx.tenant_id is None:
                raise Forbidden()
            return self._repo.page_for_tenant(self._ctx.tenant_id, limit, cursor, accept)
        if status:
            return self._repo.page_by_status(status, limit, cursor, accept)
        return self._repo.page_all(limit, cursor, accept)

    # ---- create
    def create(self, body: MaintenanceRequestCreate) -> Item:
        unit = self._c.store.units(self._ctx.org_id).get(body.unit_id) if body.unit_id else None
        if body.unit_id and unit is None:
            raise ValidationFailed(details=[{"field": "unit_id", "issue": "unknown unit"}])
        if body.property_id:
            prop = self._c.store.properties(self._ctx.org_id).get(body.property_id)
            if prop is None:
                raise ValidationFailed(
                    details=[{"field": "property_id", "issue": "unknown property"}]
                )
        if unit is not None and body.area == "shared_area" and unit["type"] != "whole_unit":
            raise ValidationFailed(
                details=[{"field": "unit_id", "issue": "a shared area names the whole unit"}]
            )
        property_id = unit["property_id"] if unit else body.property_id
        if not self._scope.property_ok(
            property_id
        ):  # a manager raises requests on their own properties
            field = "unit_id" if unit else "property_id"
            raise ValidationFailed(details=[{"field": field, "issue": f"unknown {field[:-3]}"}])
        if self._is_tenant:
            leased, parents, props = self._tenant_scope()
            ok = (
                (unit is not None and body.area == "unit" and unit["id"] in leased)
                or (
                    unit is not None
                    and body.area == "shared_area"
                    and unit["id"] in (parents | leased)
                )
                or (unit is None and body.property_id in props)
            )
            if not ok:
                raise Forbidden()  # a tenant names only what they lease, its whole unit, or its property
        self._check_attachments(body.attachment_ids)
        now = self._c.clock.now()
        rid = new_id()
        item = request_item(
            self._ctx.org_id,
            rid,
            {
                "area": body.area,
                "property_id": property_id,
                "unit_id": unit["id"] if unit else None,
                "tenant_id": self._ctx.tenant_id if self._is_tenant else None,
                "title": body.title,
                "description": body.description,
                "category": body.category,
                "priority": body.priority,
                "attachment_ids": body.attachment_ids,
                "created_by": self._ctx.user_id,
            },
            iso(now),
        )
        self._c.store.table.transact(
            [
                self._repo.create_op(item),
                self._audit.put_for(
                    self._ctx,
                    now,
                    "maintenance.create",
                    "maintenance_request",
                    rid,
                    {"area": body.area, "category": body.category, "priority": body.priority},
                ),
            ]
        )
        return item

    # ---- triage, assign, resolve
    def _assignee_ok(self, user_id: str) -> None:
        m = self._c.store.members(self._ctx.org_id).get(user_id)
        if (
            m is None
            or m["status"] == "disabled"
            or not set(m["roles"]) & {"org_admin", "property_manager"}
        ):
            raise ValidationFailed(
                details=[{"field": "assigned_to", "issue": "must be an active staff user"}]
            )

    def update(self, rid: str, body: MaintenanceRequestUpdate) -> Item:
        fields = body.model_fields_set
        changed_status: list[str] = []

        def attempt() -> Item:
            old = self.get(rid)
            new = {**old}
            status = body.status or old["status"]
            if "assigned_to" in fields:
                if body.assigned_to is not None:
                    self._assignee_ok(body.assigned_to)
                new["assigned_to"] = body.assigned_to
                if body.status is None and old["status"] == "triaged" and body.assigned_to:
                    status = "assigned"  # assigning a triaged request is the next step
            if status != old["status"]:
                if status not in TRANSITIONS[old["status"]]:
                    raise InvalidState(f"A {old['status']} request cannot become {status}")
                if status == "assigned" and not new.get("assigned_to"):
                    raise ValidationFailed(
                        details=[{"field": "assigned_to", "issue": "required to assign"}]
                    )
            if old["status"] in TERMINAL and fields - {"status"}:
                raise InvalidState("A closed or cancelled request cannot be edited")
            if body.priority:
                new["priority"] = body.priority
            if body.category:
                new["category"] = body.category
            now = self._c.clock.now()
            new["status"] = status
            if status == "resolved" and old["status"] != "resolved":
                new["resolved_at"] = iso(now)
            if old["status"] == "resolved" and status == "in_progress":
                new["resolved_at"] = None  # reopened
            new["updated_at"], new["version"] = iso(now), old["version"] + 1
            new = with_status_index(new, self._ctx.org_id)
            self._c.store.table.transact(
                [
                    self._repo.replace_op(new, old["version"]),
                    self._audit.put_for(
                        self._ctx,
                        now,
                        "maintenance.update",
                        "maintenance_request",
                        rid,
                        diff_summary(old, new, ["status", "priority", "category", "assigned_to"]),
                    ),
                ]
            )
            changed_status[:] = [status] if status != old["status"] else []
            return new

        new = retry_on_version_conflict(attempt)
        if changed_status and new.get("tenant_id"):
            publish(
                self._c,
                self._ctx.org_id,
                "maintenance_update",
                tenant_ids=[new["tenant_id"]],
                entity_id=rid,
                params={"status": new["status"], "title": new["title"]},
                offset=new["status"],
            )
        return new

    # ---- comments
    def comments(self, rid: str, limit: int, cursor: str | None) -> tuple[list[Item], str | None]:
        self.get(rid)
        hide_internal = not self._is_staff and Role.PLATFORM_ADMIN not in self._ctx.roles
        return self._repo.comments(
            rid, limit, cursor, lambda it: not (hide_internal and it["internal"])
        )

    def comment(self, rid: str, body: CommentCreate) -> Item:
        req = self.get(rid)
        if req["status"] in TERMINAL:
            raise InvalidState("This request is closed")
        if body.internal and self._is_tenant:
            raise ValidationFailed(
                details=[{"field": "internal", "issue": "tenants cannot write internal notes"}]
            )
        self._check_attachments(body.attachment_ids)
        now = self._c.clock.now()
        item = comment_item(
            self._ctx.org_id,
            rid,
            new_id(),
            {
                "author_id": self._ctx.user_id,
                "author_role": self._ctx.actor_role,
                "body": body.body,
                "internal": body.internal,
                "attachment_ids": body.attachment_ids,
            },
            iso(now),
        )
        self._c.store.table.transact(
            [
                self._repo.create_op(item),
                self._audit.put_for(
                    self._ctx,
                    now,
                    "maintenance.comment",
                    "maintenance_request",
                    rid,
                    {"internal": body.internal},
                ),
            ]
        )
        if not body.internal and not self._is_tenant and req.get("tenant_id"):
            publish(
                self._c,
                self._ctx.org_id,
                "maintenance_update",
                tenant_ids=[req["tenant_id"]],
                entity_id=rid,
                params={"status": req["status"], "title": req["title"], "comment": True},
                offset=f"comment-{item['id']}",
            )
        return item

    # ---- delete (contract v0.9.0): administrators only, once the request is closed or cancelled
    def _deletion(self, rid: str) -> tuple[Plan, Item, list[Item], list[Item]]:
        if Role.ORG_ADMIN not in self._ctx.roles:
            raise Forbidden("Only an administrator can delete maintenance requests")
        req = self.get(rid)
        plan = Plan()
        if req["status"] not in ("closed", "cancelled"):
            plan.block("not_closed", "Close or cancel the request before deleting it.")
        comments = partition(self._c, keys.mr_comment_pk(self._ctx.org_id, rid))
        docs = documents_of(self._c, self._ctx.org_id, "maintenance_request", rid)
        plan.count(
            "comments", sum(1 for x in comments if x["entity_type"] == "maintenance_comment")
        )
        plan.count("documents", len(docs))
        return plan, req, comments, docs

    def deletion(self, rid: str) -> dict[str, Any]:
        return self._deletion(rid)[0].view()

    def delete(self, rid: str, reason: str) -> dict[str, Any]:
        plan, req, comments, docs = self._deletion(rid)
        plan.require_not_blocked()
        now = self._c.clock.now()
        write_all(
            self._c,
            [
                self._audit.put_for(
                    self._ctx,
                    now,
                    "maintenance.delete",
                    "maintenance_request",
                    rid,
                    {"reason": reason, **plan.affects},
                ),
                Delete(key=(req["PK"], req["SK"])),
                *keys_of(comments),
                *delete_documents(self._c, docs),
            ],
        )
        remove_files(self._c, docs)
        return {"outcome": "removed", "privacy_request_id": None}

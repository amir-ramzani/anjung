from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.concurrency import retry_on_version_conflict
from app.core.context import RequestContext, Role
from app.core.errors import Forbidden, InvalidState, NotFound, ValidationFailed
from app.core.ids import new_id
from app.domains.privacy.export import build_export
from app.domains.privacy.schemas import PrivacyRequestCreate, PrivacyRequestUpdate
from app.repositories.privacy import privacy_request_item
from app.worker.envelope import make_envelope

DUE_DAYS = 21  # PDPA: respond to a data access request within 21 days
TRANSITIONS = {
    "received": {"in_progress", "completed", "rejected"},
    "in_progress": {"completed", "rejected"},
    "completed": set(),
    "rejected": set(),
}


class PrivacyService:
    def __init__(self, c: Container, ctx: RequestContext) -> None:
        self._c, self._ctx = c, ctx
        self._repo = c.store.privacy(ctx.org_id)
        self._audit = c.store.audit(ctx.org_id)

    @property
    def _is_admin(self) -> bool:
        return Role.ORG_ADMIN in self._ctx.roles

    def _own_tenant_id(self) -> str:
        if not self._ctx.tenant_id:
            raise Forbidden()
        return self._ctx.tenant_id

    # ---- export
    def _export(self, tenant_id: str, reveal: bool, action: str) -> dict[str, Any]:
        if self._c.store.tenants(self._ctx.org_id).get(tenant_id) is None:
            raise NotFound()
        out = build_export(self._c, self._ctx.org_id, tenant_id, reveal_identity=reveal)
        self._c.store.table.transact(
            [self._audit.put_for(self._ctx, self._c.clock.now(), action, "tenant", tenant_id)]
        )
        return out

    def export_own(self) -> dict[str, Any]:
        return self._export(self._own_tenant_id(), True, "privacy.export_own")

    def export_for(self, tenant_id: str) -> dict[str, Any]:
        return self._export(tenant_id, False, "privacy.export")

    # ---- requests
    def create(self, body: PrivacyRequestCreate) -> dict[str, Any]:
        if self._is_admin:
            if not body.tenant_id:
                raise ValidationFailed(
                    details=[{"field": "tenant_id", "issue": "required for staff"}]
                )
            tid = body.tenant_id
            if self._c.store.tenants(self._ctx.org_id).get(tid) is None:
                raise ValidationFailed(details=[{"field": "tenant_id", "issue": "unknown tenant"}])
        else:
            tid = self._own_tenant_id()
            if body.tenant_id and body.tenant_id != tid:
                raise Forbidden()
        now = self._c.clock.now()
        rid = new_id()
        item = privacy_request_item(
            self._ctx.org_id,
            rid,
            {
                "tenant_id": tid,
                "type": body.type,
                "details": body.details,
                "due_at": iso(now + timedelta(days=DUE_DAYS)),
            },
            iso(now),
        )
        self._c.store.table.transact(
            [
                self._repo.create_op(item),
                self._audit.put_for(
                    self._ctx, now, "privacy.request", "tenant", tid, {"type": body.type}
                ),
            ]
        )
        return item

    def get(self, rid: str) -> dict[str, Any]:
        item = self._repo.get(rid)
        if item is None:
            raise NotFound()
        if not self._is_admin and item["tenant_id"] != self._own_tenant_id():
            raise Forbidden()
        return item

    def page(self, limit: int, cursor: str | None) -> tuple[list[dict[str, Any]], str | None]:
        return self._repo.page(limit, cursor, None if self._is_admin else self._own_tenant_id())

    def update(self, rid: str, body: PrivacyRequestUpdate) -> dict[str, Any]:
        def attempt() -> dict[str, Any]:
            old = self.get(rid)
            if body.status not in TRANSITIONS[old["status"]]:
                raise InvalidState(f"A {old['status']} request cannot become {body.status}")
            now = self._c.clock.now()
            status = body.status
            anonymise = old["type"] == "deletion" and status == "completed"
            if anonymise:
                self._guard_deletion(old["tenant_id"])
                status = "in_progress"  # the job completes it once the data is gone
            new = {
                **old,
                "status": status,
                "decision_note": body.decision_note or old.get("decision_note"),
                "completed_at": iso(now) if status == "completed" else None,
                "version": old["version"] + 1,
            }
            self._c.store.table.transact(
                [
                    self._repo.replace_op(new, old["version"]),
                    self._audit.put_for(
                        self._ctx,
                        now,
                        "privacy.update",
                        "tenant",
                        old["tenant_id"],
                        {"type": old["type"], "from": old["status"], "to": body.status},
                    ),
                ]
            )
            if anonymise:
                self._c.jobs.enqueue(
                    "housekeeping",
                    make_envelope(
                        "anonymise_tenant",
                        self._ctx.org_id,
                        {"tenant_id": old["tenant_id"], "privacy_request_id": rid},
                        now,
                    ),
                )
            return new

        updated = retry_on_version_conflict(attempt)
        from app.domains.notifications.events import publish

        publish(
            self._c,
            self._ctx.org_id,
            "privacy_request_update",
            tenant_ids=[updated["tenant_id"]],
            entity_id=rid,
            params={"status": updated["status"]},
            offset=updated["status"],
        )
        return self.get(rid)  # an inline job may already have completed it

    def _guard_deletion(self, tenant_id: str) -> None:
        leases = self._c.store.leases(self._ctx.org_id)
        found = leases.get_many([x["lease_id"] for x in leases.all_links(tenant_id)])
        if any(x["status"] in ("draft", "active") for x in found):
            raise InvalidState(
                "The tenant has an active or draft lease. End it first, or reject the request with a reason"
            )

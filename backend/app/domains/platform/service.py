"""Organisation lifecycle (LLD 11.4). The only domain that targets an org from a path."""

from __future__ import annotations

import logging
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.concurrency import retry_on_version_conflict
from app.core.context import RequestContext
from app.core.dates import today_kl
from app.core.errors import Conflict, InvalidState, NotFound, ValidationFailed
from app.core.ids import new_id
from app.domains.organisations import plan
from app.domains.organisations.plan import PlanUpdate
from app.domains.platform.schemas import (
    OrganizationCreate,
    OrganizationDetailsUpdate,
    PlatformOrganizationUpdate,
)
from app.repositories import keys
from app.repositories.dynamo import NOT_EXISTS, Put, Update
from app.repositories.members import member_item
from app.repositories.organisations import default_settings, org_item, registry_item

log = logging.getLogger(__name__)

ALLOWED = {
    "active": {"suspended", "closed"},
    "suspended": {"active", "closed"},
    "closed": set(),
}


class PlatformService:
    def __init__(self, c: Container, ctx: RequestContext) -> None:
        self._c, self._ctx = c, ctx
        self._platform = c.store.platform()

    def create(self, body: OrganizationCreate) -> dict[str, Any]:
        now = self._c.clock.now()
        org_id = new_id()
        patch = body.settings.model_dump(exclude_none=True) if body.settings else {}
        org = org_item(org_id, body.name, {**default_settings(), **patch}, iso(now))
        if body.plan is not None:
            org["plan"] = plan.merged(org, body.plan)
        brand_ops: list[Any] = []
        if body.branding is not None:
            from app.domains.organisations import branding

            org["branding"], brand_ops = branding.plan(self._c, org_id, org, body.branding)
        from app.domains.organisations import staff_invite

        by_link = staff_invite.by_link(self._c)  # our welcome email, else Cognito's (v0.15.0)
        sub = self._c.identity.create_user(
            body.initial_admin.email, body.initial_admin.name, send_email=not by_link
        )
        member = member_item(
            org_id,
            sub,
            body.initial_admin.email.lower(),
            body.initial_admin.name,
            ["org_admin"],
            iso(now),
        )
        summary = {"name": body.name, "reason": body.reason}
        link_ops: list[Any] = []
        if by_link:
            token, expires, link_ops = staff_invite.link_ops(self._c, org_id, member, now)
        try:
            self._c.store.table.transact(
                [
                    Put(item=org, condition=NOT_EXISTS),
                    Put(item=registry_item(org), condition=NOT_EXISTS),
                    Put(item=member, condition=NOT_EXISTS),
                    self._c.store.audit(self._ctx.org_id).put_for(
                        self._ctx, now, "org.create", "organisation", org_id, summary
                    ),
                    self._c.store.audit(org_id).put_for(
                        self._ctx, now, "org.create", "organisation", org_id, summary
                    ),
                    *brand_ops,
                    *link_ops,
                ]
            )
        except Exception:
            self._c.identity.delete_user(sub)  # roll back step 2
            raise
        self._c.identity.add_to_group(sub, "org_admin")
        if by_link:
            try:
                staff_invite.send(self._c, org_id, member, token, expires)
            except Conflict:
                # the organisation exists; its admin can be sent a new link (resendUserInvite)
                log.warning("welcome email to the first admin of %s not sent", org_id)
        return self._out(org)

    def _out(self, org: dict[str, Any]) -> dict[str, Any]:
        """With the plan as the platform sees it (contract v0.12.0)."""
        from app.domains.organisations import branding

        return {
            **org,
            "branding": branding.view(org) if org.get("branding") else None,
            "plan": plan.view(self._c, org["id"], org, today_kl(self._c.clock)),
        }

    def page(
        self, limit: int, cursor: str | None, status: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        reg, nxt = self._platform.list_registry(limit, cursor, status)
        by_id = {o["id"]: o for o in self._platform.get_orgs([r["org_id"] for r in reg])}
        return [self._out(by_id[r["org_id"]]) for r in reg if r["org_id"] in by_id], nxt

    def get(self, org_id: str) -> dict[str, Any]:
        org = self._platform.get_org(org_id)
        if org is None:
            raise NotFound()
        return self._out(org)

    def set_status(self, org_id: str, body: PlatformOrganizationUpdate) -> dict[str, Any]:
        def attempt() -> dict[str, Any]:
            old = self.get(org_id)
            if org_id == self._c.settings.platform_org_id:
                raise InvalidState("The platform organisation cannot be suspended or closed")
            if body.status not in ALLOWED[old["status"]]:
                raise InvalidState(
                    f"Cannot move an organisation from {old['status']} to {body.status}"
                )
            now = self._c.clock.now()
            new = {
                **old,
                "status": body.status,
                "version": old["version"] + 1,
                "updated_at": iso(now),
            }
            summary = {"from": old["status"], "to": body.status, "reason": body.reason}
            self._c.store.table.transact(
                [
                    self._c.store.org(org_id).replace_op(new, old["version"]),
                    Update(
                        key=(keys.REGISTRY_PK, org_id),
                        expression="SET #s = :s",
                        names={"#s": "status"},
                        values={":s": body.status},
                    ),
                    self._c.store.audit(self._ctx.org_id).put_for(
                        self._ctx, now, f"org.{body.status}", "organisation", org_id, summary
                    ),
                    self._c.store.audit(org_id).put_for(
                        self._ctx, now, f"org.{body.status}", "organisation", org_id, summary
                    ),
                ]
            )
            self._c.org_status.invalidate(org_id)
            return self._out(new)

        return retry_on_version_conflict(attempt)

    def update_details(self, org_id: str, body: OrganizationDetailsUpdate) -> dict[str, Any]:
        """Correct a company's name and branding (contract v0.17.0): the same rules as the org admin's own
        update, audited in both organisations with the reason. The logo is the org admin's to upload."""
        from app.domains.organisations.service import details_change

        if body.branding is not None and "logo_document_id" in body.branding.model_fields_set:
            raise ValidationFailed(
                details=[{"field": "branding.logo_document_id", "issue": "is set by the org admin"}]
            )

        def attempt() -> dict[str, Any]:
            old = self._platform.get_org(org_id)
            if old is None or org_id == self._c.settings.platform_org_id:
                raise NotFound()
            if old["status"] == "closed":
                raise InvalidState("A closed organisation cannot be changed")
            new, extra_ops, summary = details_change(self._c, org_id, old, body.name, body.branding)
            now = self._c.clock.now()
            new["updated_at"], new["version"] = iso(now), old["version"] + 1
            summary["reason"] = body.reason
            self._c.store.table.transact(
                [
                    self._c.store.org(org_id).replace_op(new, old["version"]),
                    self._c.store.audit(self._ctx.org_id).put_for(
                        self._ctx, now, "org.update", "organisation", org_id, summary
                    ),
                    self._c.store.audit(org_id).put_for(
                        self._ctx, now, "org.update", "organisation", org_id, summary
                    ),
                    *extra_ops,
                ]
            )
            self._c.org_status.invalidate(org_id)
            return self._out(new)

        return retry_on_version_conflict(attempt)

    def resend_admin_invite(self, org_id: str, reason: str) -> int:
        """Each org admin still `invited` gets a fresh link (the old one stops working), or Cognito's
        invitation again when email sending is not set up. Audited in both organisations."""
        from app.domains.organisations import staff_invite

        org = self._platform.get_org(org_id)
        if org is None or org_id == self._c.settings.platform_org_id:
            raise NotFound()
        repo = self._c.store.members(org_id)
        members, cursor = [], None
        while True:
            page, cursor = repo.page(100, cursor)
            members += page
            if cursor is None:
                break
        waiting = [
            m for m in members if "org_admin" in m.get("roles", []) and m["status"] == "invited"
        ]
        if not waiting:
            raise InvalidState("Every admin of this organisation has already signed in")
        by_link = staff_invite.by_link(self._c)
        for m in waiting:
            now = self._c.clock.now()
            new = {**m, "version": m["version"] + 1, "updated_at": iso(now)}
            ops: list[Any] = []
            if by_link:
                token, expires, ops = staff_invite.link_ops(self._c, org_id, new, now)
            summary = {"reason": reason, "channel": "email" if by_link else "cognito"}
            self._c.store.table.transact(
                [
                    repo.replace_op(new, m["version"]),
                    self._c.store.audit(self._ctx.org_id).put_for(
                        self._ctx, now, "user.invite_resend", "user", m["user_id"], summary
                    ),
                    self._c.store.audit(org_id).put_for(
                        self._ctx, now, "user.invite_resend", "user", m["user_id"], summary
                    ),
                    *ops,
                ]
            )
            if by_link:
                staff_invite.send(self._c, org_id, new, token, expires)
            else:
                self._c.identity.resend_invite(new["email"])
        return len(waiting)

    def set_plan(self, org_id: str, body: PlanUpdate) -> dict[str, Any]:
        """The company's property limit and end date (contract v0.12.0). Lowering the limit below what is
        in use removes nothing: it only stops new properties."""

        def attempt() -> dict[str, Any]:
            old = self._platform.get_org(org_id)
            if old is None:
                raise NotFound()
            if org_id == self._c.settings.platform_org_id:
                raise InvalidState("The platform organisation has no plan")
            now = self._c.clock.now()
            new = {
                **old,
                "plan": plan.merged(old, body),
                "version": old["version"] + 1,
                "updated_at": iso(now),
            }
            summary = {
                "reason": body.reason,
                "unit_limit": new["plan"].get("unit_limit"),
                "active_until": new["plan"].get("active_until"),
            }
            self._c.store.table.transact(
                [
                    self._c.store.org(org_id).replace_op(new, old["version"]),
                    self._c.store.audit(self._ctx.org_id).put_for(
                        self._ctx, now, "org.plan", "organisation", org_id, summary
                    ),
                    self._c.store.audit(org_id).put_for(
                        self._ctx, now, "org.plan", "organisation", org_id, summary
                    ),
                ]
            )
            self._c.org_status.invalidate(org_id)
            return self._out(new)

        return retry_on_version_conflict(attempt)

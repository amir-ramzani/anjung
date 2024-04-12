from __future__ import annotations

import logging
from typing import Any

from app.container import Container
from app.core.access import staff_access
from app.core.clock import iso
from app.core.concurrency import retry_on_version_conflict
from app.core.context import RequestContext
from app.core.dates import today_kl
from app.core.errors import Forbidden, InvalidState, NotFound, ValidationFailed
from app.domains.organisations import branding, plan, staff_invite
from app.domains.organisations.schemas import OrganizationUpdate, UserInvite, UserUpdate
from app.repositories.audit import diff_summary
from app.repositories.dynamo import Update
from app.repositories.members import member_item

log = logging.getLogger(__name__)


class OrganisationService:
    def __init__(self, c: Container, ctx: RequestContext) -> None:
        self._c, self._ctx = c, ctx
        self._repo = c.store.org(ctx.org_id)

    def _meta(self) -> dict[str, Any]:
        meta = self._repo.get_meta()
        if meta is None:
            raise NotFound()
        return meta

    def get(self) -> dict[str, Any]:
        meta = self._meta()
        return self._out(meta)

    def _out(self, meta: dict[str, Any]) -> dict[str, Any]:
        return {
            **meta,
            "branding": branding.view(meta) if meta.get("branding") else None,
            "plan": plan.view(self._c, self._ctx.org_id, meta, today_kl(self._c.clock)),
        }

    def update(self, body: OrganizationUpdate) -> dict[str, Any]:
        changes = body.model_dump(exclude_unset=True, exclude_none=False)

        def attempt() -> dict[str, Any]:
            old = self._meta()
            new, extra_ops, summary = details_change(
                self._c, self._ctx.org_id, old, changes.get("name"), body.branding
            )
            if changes.get("settings"):
                patch = {k: v for k, v in changes["settings"].items() if v is not None}
                new["settings"] = {**old["settings"], **patch}
            now = iso(self._c.clock.now())
            new["updated_at"], new["version"] = now, old["version"] + 1
            summary["settings_changed"] = sorted(
                k for k in new["settings"] if new["settings"][k] != old["settings"].get(k)
            )
            self._c.store.table.transact(
                [
                    self._repo.replace_op(new, old["version"]),
                    self._c.store.audit(self._ctx.org_id).put_for(
                        self._ctx,
                        self._c.clock.now(),
                        "org.update",
                        "organisation",
                        self._ctx.org_id,
                        summary,
                    ),
                    *extra_ops,  # the handle registry and the organisation registry's name
                ]
            )
            self._c.org_status.invalidate(self._ctx.org_id)
            return self._out(new)

        return retry_on_version_conflict(attempt)


def details_change(
    c: Container,
    org_id: str,
    old: dict[str, Any],
    name: str | None,
    brand: branding.BrandingIn | None,
) -> tuple[dict[str, Any], list[Any], dict[str, Any]]:
    """The name and branding part of an organisation update, shared by the org admin
    (`updateOrganization`) and the platform admin (`updateOrganizationDetails`, v0.17.0).
    Returns the new item (version not yet bumped), the extra transaction operations (handle registry,
    organisation registry name) and the audit summary (what changed, never the values of branding)."""
    new = {**old}
    ops: list[Any] = []
    if brand is not None:
        new["branding"], ops = branding.plan(c, org_id, old, brand)
    if name is not None:
        new["name"] = name
    summary: dict[str, Any] = diff_summary(old, new, ["name"])
    if new.get("branding") is not None:
        before = old.get("branding") or {}
        summary["branding_changed"] = sorted(
            k for k in {**before, **new["branding"]} if before.get(k) != new["branding"].get(k)
        )
    if new["name"] != old["name"]:
        from app.repositories.keys import REGISTRY_PK

        ops.append(
            Update(
                key=(REGISTRY_PK, org_id),
                expression="SET #n = :n",
                names={"#n": "name"},
                values={":n": new["name"]},
            )
        )
    return new, ops, summary


class UserService:
    """Staff accounts of the caller's organisation."""

    def __init__(self, c: Container, ctx: RequestContext) -> None:
        self._c, self._ctx = c, ctx
        self._repo = c.store.members(ctx.org_id)
        self._audit = c.store.audit(ctx.org_id)

    def _counts(self) -> dict[str, int]:
        return plan.unit_usage(self._c, self._ctx.org_id)[1]

    def view(self, member: dict[str, Any], counts: dict[str, int] | None = None) -> dict[str, Any]:
        """API shape: the stored member, what its access allows today, and how many units it manages."""
        counts = self._counts() if counts is None else counts
        return {
            **member,
            "access": staff_access(member, today_kl(self._c.clock)),
            "unit_quota": plan.member_quota(member),
            "units_managed": counts.get(member["user_id"], 0),
        }

    def page(self, limit: int, cursor: str | None) -> tuple[list[dict[str, Any]], str | None]:
        items, nxt = self._repo.page(limit, cursor)
        counts = self._counts()  # one pass over the properties for the whole page
        return [self.view(m, counts) for m in items], nxt

    def _check_plan(self, user_id: str | None, quota: int | None, until: str | None) -> None:
        """Within the organisation's plan (contract v0.12.0): managers' limits add up to at most the
        organisation's, and staff access ends no later than the organisation's."""
        meta = self._c.store.org(self._ctx.org_id).get_meta()
        plan.check_staff_until(meta, until)
        if quota is not None:
            members, cursor = [], None
            while True:
                page, cursor = self._repo.page(100, cursor)
                members += page
                if cursor is None:
                    break
            plan.check_manager_quota(meta, members, user_id, quota)

    def invite(self, body: UserInvite) -> dict[str, Any]:
        self._check_plan(
            None, body.unit_quota, body.active_until.isoformat() if body.active_until else None
        )
        now = self._c.clock.now()
        by_link = staff_invite.by_link(
            self._c
        )  # our welcome email, else Cognito's (contract v0.15.0)
        sub = self._c.identity.create_user(
            body.email, body.name, send_email=not by_link
        )  # UserExists -> 409
        item = member_item(
            self._ctx.org_id, sub, body.email.lower(), body.name, list(body.roles), iso(now)
        )
        item["active_from"] = body.active_from.isoformat() if body.active_from else None
        item["active_until"] = body.active_until.isoformat() if body.active_until else None
        item["unit_quota"] = body.unit_quota
        link_ops: list[Any] = []
        if by_link:
            token, expires, link_ops = staff_invite.link_ops(self._c, self._ctx.org_id, item, now)
        try:
            self._c.store.table.transact(
                [
                    self._repo.put_new_op(item),
                    self._audit.put_for(
                        self._ctx,
                        now,
                        "user.invite",
                        "user",
                        sub,
                        {"roles": list(body.roles), "channel": "email" if by_link else "cognito"},
                    ),
                    *link_ops,
                ]
            )
        except Exception:
            self._c.identity.delete_user(sub)  # roll back so a retry can succeed
            raise
        for role in body.roles:
            self._c.identity.add_to_group(sub, role)
        if by_link:
            # the user exists either way; a failed send is a 409 telling the admin to resend
            staff_invite.send(self._c, self._ctx.org_id, item, token, expires)
        return self.view(item)

    def resend_invite(self, user_id: str) -> dict[str, Any]:
        """A new welcome email with a fresh link (contract v0.15.0). Only for staff who have not yet
        signed in; the previous link stops working."""

        def attempt() -> tuple[dict[str, Any], str | None, Any]:
            old = self._target(user_id)
            if old["status"] != "invited":
                raise InvalidState("This user has already signed in")
            now = self._c.clock.now()
            new = {**old, "updated_at": iso(now), "version": old["version"] + 1}
            token: str | None = None
            expires: Any = None
            ops: list[Any] = []
            if staff_invite.by_link(self._c):
                token, expires, ops = staff_invite.link_ops(self._c, self._ctx.org_id, new, now)
            self._c.store.table.transact(
                [
                    self._repo.replace_op(new, old["version"]),
                    self._audit.put_for(
                        self._ctx,
                        now,
                        "user.invite_resend",
                        "user",
                        user_id,
                        {"channel": "email" if token else "cognito"},
                    ),
                    *ops,
                ]
            )
            return new, token, expires

        new, token, expires = retry_on_version_conflict(attempt)
        if token:
            staff_invite.send(self._c, self._ctx.org_id, new, token, expires)
        else:
            self._c.identity.resend_invite(new["email"])
        return self.view(new)

    def _target(self, user_id: str) -> dict[str, Any]:
        member = self._repo.get(user_id)
        if member is None:
            raise NotFound()
        if user_id == self._ctx.user_id:
            raise InvalidState("You cannot change or remove your own account")
        if not set(member["roles"]) <= {"org_admin", "property_manager"}:
            raise Forbidden()
        return member

    def update(self, user_id: str, body: UserUpdate) -> dict[str, Any]:
        optional = {"active_from", "active_until", "unit_quota"}  # explicit null clears these
        changes = body.model_dump(exclude_none=True, exclude=optional)
        for k in ("active_from", "active_until"):
            if k in body.model_fields_set:
                v = getattr(body, k)
                changes[k] = v.isoformat() if v else None
        if "unit_quota" in body.model_fields_set:
            changes["unit_quota"] = body.unit_quota

        def attempt() -> tuple[dict[str, Any], dict[str, Any]]:
            old = self._target(user_id)
            new = {**old, **changes}
            if "unit_quota" in changes:
                new.pop("property_quota", None)  # the pre-v0.13.0 name
            start, end = new.get("active_from"), new.get("active_until")
            self._check_plan(
                user_id,
                changes.get("unit_quota"),
                changes.get("active_until") if "active_until" in changes else None,
            )
            if start and end and end < start:
                raise ValidationFailed(
                    details=[{"field": "active_until", "issue": "must not be before active_from"}]
                )
            now = self._c.clock.now()
            new["updated_at"], new["version"] = iso(now), old["version"] + 1
            self._c.store.table.transact(
                [
                    self._repo.replace_op(new, old["version"]),
                    self._audit.put_for(
                        self._ctx,
                        now,
                        "user.update",
                        "user",
                        user_id,
                        diff_summary(
                            old,
                            new,
                            ["roles", "status", "active_from", "active_until", "unit_quota"],
                        ),
                    ),
                ]
            )
            return old, new

        old, new = retry_on_version_conflict(attempt)
        idp = self._c.identity
        if set(new["roles"]) != set(old["roles"]):
            for r in set(old["roles"]) - set(new["roles"]):
                idp.remove_from_group(user_id, r)
            for r in set(new["roles"]) - set(old["roles"]):
                idp.add_to_group(user_id, r)
            idp.global_sign_out(user_id)  # old tokens carry the old roles (LLD 11.5)
        if new["status"] != old["status"]:
            idp.set_enabled(user_id, new["status"] != "disabled")
            if new["status"] == "disabled":
                idp.global_sign_out(user_id)
        return self.view(new)

    def delete(self, user_id: str) -> None:
        def attempt() -> None:
            old = self._target(user_id)
            self._c.store.table.transact(
                [
                    self._repo.delete_op(user_id, old["version"]),
                    self._audit.put_for(
                        self._ctx,
                        self._c.clock.now(),
                        "user.delete",
                        "user",
                        user_id,
                        {"roles": old["roles"]},
                    ),
                ]
            )

        retry_on_version_conflict(attempt)
        self._c.identity.delete_user(user_id)

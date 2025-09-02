"""The set-password link (contract v0.8.0; staff since v0.15.0). Public: whoever holds the link is the
invited person, so the token is long and random, stored only as a hash, works once, and only the
newest one works."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request, Response

from app.container import Container
from app.core.clock import iso
from app.core.errors import NotFound
from app.domains.organisations import branding
from app.domains.tenants.schemas import InviteAccept, InviteInfo
from app.lambdas.post_confirmation.handler import link_tenant
from app.repositories import keys
from app.repositories.dynamo import Delete, Update

router = APIRouter(tags=["Invites"])
Token = Annotated[str, Path(min_length=20, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]
GONE = "This link has expired or has already been used. Ask your landlord or administrator for a new one."


def _valid(c: Container, token: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """(link, person, org) or 404: the tenant, or the staff member (`link.kind == "staff"`). Unknown,
    used, replaced and expired links all look the same."""
    pk = keys.invite_token_pk(token)
    link = c.store.table.get(pk, keys.META)
    if link is None or link["expires_at"] < iso(c.clock.now()):
        raise NotFound(GONE)
    org = c.store.org(link["org_id"]).get_meta() or {}
    if org.get("status") != "active":
        raise NotFound(GONE)
    if link.get("kind") == "staff":
        member = c.store.members(link["org_id"]).get(link["sub"])
        if (
            member is None
            or member["status"] != "invited"  # signed in already, or disabled
            or member.get("invite_token_pk") != pk  # replaced by a newer link, or used
        ):
            raise NotFound(GONE)
        return link, {**member, "full_name": member.get("name") or member["email"]}, org
    tenant = c.store.tenants(link["org_id"]).get(link["tenant_id"])
    if (
        tenant is None
        or tenant["status"] != "invited"
        or tenant.get("linked")
        or tenant.get("pending_sub") != link["sub"]
        or tenant.get("invite_token_pk") != pk  # replaced by a newer link
    ):
        raise NotFound(GONE)
    return link, tenant, org


def _limit(c: Container, request: Request) -> None:
    c.invite_limiter.check(request.client.host if request.client else "unknown")


@router.get("/invites/{token}", response_model=InviteInfo, operation_id="getInvite")
def get_invite(token: Token, request: Request) -> Any:
    c: Container = request.app.state.container
    _limit(c, request)
    link, person, org = _valid(c, token)
    brand = branding.public(org)
    return {
        "kind": link.get("kind", "tenant"),
        "full_name": person["full_name"],
        "email": person["email"],
        "org_name": brand["display_name"] if brand else org.get("name", ""),
        "expires_at": link["expires_at"],
        "brand": brand,
    }


@router.post("/invites/{token}/accept", status_code=204, operation_id="acceptInvite")
def accept_invite(token: Token, body: InviteAccept, request: Request) -> Response:
    """Sets the password, and links a tenant's account. They then sign in as usual."""
    c: Container = request.app.state.container
    _limit(c, request)
    link, person, _ = _valid(c, token)
    c.identity.set_password(link["sub"], body.password)  # weak password: 422, the link stays valid
    now = c.clock.now()
    staff = link.get("kind") == "staff"
    if staff:
        used: Any = Update(  # the link is spent; the first sign-in makes them active
            key=(keys.org_pk(link["org_id"]), keys.member_sk(link["sub"])),
            expression="REMOVE invite_token_pk",
            condition="invite_token_pk = :pk",
            values={":pk": keys.invite_token_pk(token)},
        )
    elif not link_tenant(c.store.table, link["sub"], link["email"], now):
        raise NotFound(GONE)
    audit = c.store.audit(link["org_id"])
    c.store.table.transact(
        [
            Delete(key=(keys.invite_token_pk(token), keys.META)),
            *([used] if staff else []),
            audit.put_op(
                actor_id=link["sub"],
                actor_role=(
                    ("org_admin" if "org_admin" in person["roles"] else "property_manager")
                    if staff
                    else "tenant"
                ),
                action="user.invite_accept" if staff else "tenant.invite_accept",
                entity_type="user" if staff else "tenant",
                entity_id=link["sub"] if staff else person["id"],
                summary={"via": "set_password_link"},
                now=now,
                request_id=request.state.request_id,
                ip=request.client.host if request.client else None,
            ),
        ]
    )
    return Response(status_code=204)

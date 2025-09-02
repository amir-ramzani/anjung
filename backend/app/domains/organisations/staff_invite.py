"""Welcome emails for staff (contract v0.15.0): the same one-time set-password link tenants get
(`/invite/<token>`), sent as the app's own branded `invite_staff` email instead of Cognito's invitation
with a temporary password. Without an email channel, Cognito's email is used as before."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.dates import display_date
from app.core.errors import Conflict
from app.domains.organisations import branding
from app.integrations.channels import Message
from app.repositories import keys
from app.repositories.dynamo import NOT_EXISTS, Delete, Put

ROLE = {
    "en": {"org_admin": "an administrator", "property_manager": "a property manager"},
    "ms": {"org_admin": "pentadbir", "property_manager": "pengurus hartanah"},
}


def by_link(c: Container) -> bool:
    """Our own welcome email when email sending is set up; otherwise Cognito's invitation."""
    return "email" in c.channels


def link_ops(
    c: Container, org_id: str, member: dict[str, Any], now: datetime
) -> tuple[str, datetime, list[Put | Delete]]:
    """A fresh token for `member` (which the caller saves with `invite_token_pk` set), and the ops
    that store it and drop the one it replaces. Only the newest link works."""
    token = secrets.token_urlsafe(32)
    expires = now + timedelta(days=c.settings.invite_ttl_days)
    old = member.get("invite_token_pk")
    member["invite_token_pk"] = keys.invite_token_pk(token)
    ops: list[Put | Delete] = [
        Put(
            item={
                "PK": member["invite_token_pk"],
                "SK": keys.META,
                "entity_type": "staff_invite_link",
                "kind": "staff",
                "org_id": org_id,
                "sub": member["user_id"],
                "email": member["email"],
                "expires_at": iso(expires),
                "ttl": int(expires.timestamp()),
            },
            condition=NOT_EXISTS,
        )
    ]
    if old:
        ops.append(Delete(key=(old, keys.META)))
    return token, expires, ops


def url(c: Container, token: str) -> str:
    return f"{c.settings.app_base_url.rstrip('/')}/invite/{token}"


def send(c: Container, org_id: str, member: dict[str, Any], token: str, expires: datetime) -> None:
    """Send the welcome email now (never queued, so the link is never stored). Like the plan-ending
    email, it goes to staff, so it is audited (`user.invite`) rather than in the tenant notification log.
    Raises Conflict when the address is on the bounce list or the send fails."""
    from app.domains.notifications.templates import brand_wrap, render

    org = c.store.org(org_id).get_meta() or {}
    repo = c.store.notifications(org_id)
    if repo.suppressed("email", member["email"]):
        raise Conflict(
            "Emails to this address bounced or were marked as spam, so none are sent. "
            "Remove this user and invite them with a corrected address.",
            code="address_suppressed",
            params={"scope": "staff"},
        )
    locale = (org.get("settings") or {}).get("default_locale", "en")
    brand = branding.public(org)
    roles = member.get("roles") or []
    params = {
        "name": member.get("name") or member["email"],
        "org_name": brand["display_name"] if brand else org.get("name", ""),
        "role": ROLE.get(locale, ROLE["en"])[
            "org_admin" if "org_admin" in roles else "property_manager"
        ],
        "email": member["email"],
        "expires": display_date(expires),
    }
    subject, text, html = render("invite_staff", locale, {**params, "link": url(c, token)})
    if brand:
        base = (c.settings.public_api_url or c.settings.app_base_url).rstrip("/")
        logo = f"{base}{brand['logo_url']}" if brand.get("logo_url") else None
        text, html = brand_wrap(text, html, brand, locale, logo)
    try:
        pid = c.channels["email"].send(
            Message(
                member["email"],
                subject,
                text,
                html,
                "invite_staff",
                locale,
                from_name=brand["display_name"] if brand else None,
                reply_to=brand.get("contact_email") if brand else None,
            )
        )
    except Exception:  # noqa: BLE001
        raise Conflict(
            "The welcome email could not be sent. The user was added: use Resend invitation to try again."
        ) from None
    repo.message_lookup_put(
        pid, "", ""
    )  # a bounce or complaint suppresses the address (no log entry)

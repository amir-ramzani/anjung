"""Organisation branding (contract v0.11.0, improvement plan phase A).

Each company is its own organisation (decision B1), so the brand lives on the organisation. A handle gives the
company its own sign-in page at `/login/<handle>` (decision B2; subdomains later). Branding is presentation only,
never a security boundary: signing in works the same from any page.

The handle registry (`BRANDHANDLE#<handle>`) keeps handles unique across the platform and resolves a handle without
a sign-in. When a handle changes, the old one keeps pointing at the organisation for `HANDLE_GRACE_DAYS`, so links in
messages already sent still open the right page; after that anyone may take it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from pydantic import EmailStr, Field, StringConstraints

from app.container import Container
from app.core.clock import iso
from app.core.errors import Conflict, ValidationFailed
from app.core.ids import ID_PATTERN
from app.domains.common import Request, Response
from app.repositories import keys
from app.repositories.dynamo import Put

Item = dict[str, Any]
HANDLE_GRACE_DAYS = 30
Handle = Annotated[str, StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])$")]
Colour = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
Phone = Annotated[str, StringConstraints(pattern=r"^\+[1-9][0-9]{7,14}$")]
RESERVED = {
    "admin", "anjung", "api", "app", "assets", "auth", "billing", "dev", "help", "invite", "login",
    "mail", "platform", "root", "staging", "static", "status", "support", "system", "www",
}  # fmt: skip
LOGO_TYPES = ("image/png", "image/jpeg", "image/webp")
LOGO_MAX_BYTES = 512 * 1024


class WelcomeText(Request):
    en: str | None = Field(default=None, max_length=300)
    ms: str | None = Field(default=None, max_length=300)


class BrandingIn(Request):
    """A partial update: fields left out stay as they are; null clears a field."""

    enabled: bool | None = None
    handle: Handle | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    company_name: str | None = Field(default=None, max_length=200)
    company_reg_no: str | None = Field(default=None, max_length=50)
    accent_colour: Colour | None = None
    contact_email: EmailStr | None = None
    contact_phone: Phone | None = None
    welcome_text: WelcomeText | None = None
    login_page: bool | None = None  # show the branded sign-in page at /login/<handle>
    logo_document_id: str | None = Field(default=None, pattern=ID_PATTERN)


class WelcomeTextOut(Response):
    en: str | None = None
    ms: str | None = None


class Branding(Response):
    enabled: bool = False
    handle: str | None = None
    display_name: str | None = None
    company_name: str | None = None
    company_reg_no: str | None = None
    accent_colour: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    welcome_text: WelcomeTextOut | None = None
    login_page: bool = True
    logo_document_id: str | None = None
    logo_url: str | None = None  # public path of the logo, changes when the logo does


class PublicBrand(Response):
    """What anyone may see about a brand: the sign-in and set-password pages, and signed-in users."""

    handle: str
    display_name: str
    company_name: str | None = None
    company_reg_no: str | None = None
    accent_colour: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    welcome_text: WelcomeTextOut | None = None
    login_page: bool = True
    logo_url: str | None = None


def logo_path(handle: str, document_id: str) -> str:
    return f"/v1/brands/{handle}/logo?v={document_id}"


def view(meta: Item) -> Item:
    """`Organization.branding` for staff."""
    b = dict(meta.get("branding") or {})
    if b.get("handle") and b.get("logo_document_id"):
        b["logo_url"] = logo_path(b["handle"], b["logo_document_id"])
    return b


def public(meta: Item) -> Item | None:
    """The brand others may see, or None when branding is off or unfinished."""
    b = meta.get("branding") or {}
    if not b.get("enabled") or not b.get("handle"):
        return None
    out = {
        k: b.get(k)
        for k in (
            "handle",
            "company_name",
            "company_reg_no",
            "accent_colour",
            "contact_email",
            "contact_phone",
            "welcome_text",
        )
    }
    out["display_name"] = b.get("display_name") or meta.get("name", "")
    out["login_page"] = b.get("login_page", True)
    out["logo_url"] = (
        logo_path(b["handle"], b["logo_document_id"]) if b.get("logo_document_id") else None
    )
    return out


def by_handle(c: Container, handle: str) -> tuple[Item, Item] | None:
    """(organisation, public brand) for a live handle, else None. A retired handle resolves until its grace ends."""
    ref = c.store.table.get(keys.brand_handle_pk(handle), keys.META)
    if ref is None:
        return None
    if ref.get("retired_until") and ref["retired_until"] < iso(c.clock.now()):
        return None
    meta = c.store.org(ref["org_id"]).get_meta()
    if meta is None or meta.get("status") != "active":
        return None
    brand = public(meta)
    return (meta, brand) if brand else None


def plan(c: Container, org_id: str, old: Item, body: BrandingIn) -> tuple[Item, list[Any]]:
    """The new branding for an organisation and the registry writes that go with it (same transaction)."""
    before: Item = dict(old.get("branding") or {"enabled": False})
    data = body.model_dump(exclude_unset=True)
    new = {**before}
    if "welcome_text" in data:
        wt = data.pop("welcome_text") or {}
        new["welcome_text"] = {**(before.get("welcome_text") or {}), **wt} if wt else None
    new.update(data)
    if new.get("enabled") and not new.get("handle"):
        raise ValidationFailed(
            "Choose a handle before switching branding on",
            details=[{"field": "branding.handle", "issue": "required when branding is enabled"}],
        )
    if data.get("logo_document_id"):
        _check_logo(c, org_id, data["logo_document_id"])
    ops: list[Any] = []
    now = c.clock.now()
    old_h, new_h = before.get("handle"), new.get("handle")
    if new_h != old_h:
        if new_h:
            if new_h in RESERVED:
                raise ValidationFailed(
                    details=[{"field": "branding.handle", "issue": "this handle is reserved"}]
                )
            taken = c.store.table.get(keys.brand_handle_pk(new_h), keys.META)
            if (
                taken
                and taken["org_id"] != org_id
                and not (taken.get("retired_until") and taken["retired_until"] < iso(now))
            ):
                raise Conflict("This handle is already taken. Choose another one")
            ops.append(
                Put(
                    item=_registry(new_h, org_id, None),
                    condition="attribute_not_exists(PK) OR org_id = :o OR retired_until < :now",
                    values={":o": org_id, ":now": iso(now)},
                )
            )
        if old_h:  # the old handle keeps working for a while, then anyone may take it
            until = iso(now + timedelta(days=HANDLE_GRACE_DAYS))
            ops.append(Put(item=_registry(old_h, org_id, until)))
    return new, ops


def _registry(handle: str, org_id: str, retired_until: str | None) -> Item:
    return {
        "PK": keys.brand_handle_pk(handle),
        "SK": keys.META,
        "entity_type": "brand_handle",
        "org_id": org_id,
        "handle": handle,
        "retired_until": retired_until,
    }


def _check_logo(c: Container, org_id: str, document_id: str) -> None:
    doc = c.store.documents(org_id).get(document_id)
    issue = None
    if doc is None or doc["owner_type"] != "organization" or doc["type"] != "logo":
        issue = "not a logo uploaded for this organisation"
    elif doc["status"] != "clean":
        issue = "the logo has not passed the malware scan yet"
    elif doc["content_type"] not in LOGO_TYPES or doc["size_bytes"] > LOGO_MAX_BYTES:
        issue = "a logo must be a PNG, JPEG or WebP image of at most 512 KB"
    if issue:
        raise ValidationFailed(details=[{"field": "branding.logo_document_id", "issue": issue}])

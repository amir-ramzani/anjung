from datetime import datetime
from typing import Annotated, Literal

from pydantic import EmailStr, Field, StringConstraints, field_validator

from app.domains.common import Request, Response
from app.domains.organisations.branding import PublicBrand

Phone = Annotated[str, StringConstraints(pattern=r"^\+[1-9][0-9]{7,14}$")]
Locale = Literal["en", "ms"]
Purpose = Literal[
    "lease_administration",
    "payment_processing",
    "whatsapp_messaging",
    "email_messaging",
    "marketing",
]


class EmailPref(Request):
    enabled: bool
    address: EmailStr | None = None


class WhatsAppPref(Request):
    enabled: bool
    number: Phone | None = None
    opted_in_at: datetime | None = None


class NotificationPreferences(Request):
    email: EmailPref | None = None
    whatsapp: WhatsAppPref | None = None
    locale: Locale | None = None


class IdentityDocumentIn(Request):
    type: Literal["nric", "passport"]
    number: str = Field(min_length=1, max_length=32)


class IdentityDocumentOut(Response):
    type: Literal["nric", "passport"]
    masked_number: str


class TenantRental(Response):
    lease_id: str
    status: Literal["draft", "active"]
    property_name: str | None
    unit_label: str | None
    parent_unit_label: str | None  # a room's unit, None for a whole unit
    unit_type: Literal["whole_unit", "room", "parking"] | None = None  # contract v0.10.0


class Tenant(Response):
    id: str
    full_name: str
    email: str  # a placeholder after anonymisation, so no format check on output
    phone: str | None = None
    identity_document: IdentityDocumentOut | None = None
    status: Literal["invited", "active", "former", "anonymised"]
    linked: bool
    notification_preferences: NotificationPreferences | None = None
    created_at: datetime
    updated_at: datetime
    rentals: list[TenantRental] | None = None  # tenant endpoints only (not the privacy export)
    # renting now, a lease not started yet, only ended leases, or none; separate from the account status
    tenancy: Literal["current", "upcoming", "past", "none"] | None = None


class TenantCreate(Request):
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone: Phone | None = None
    identity_document: IdentityDocumentIn | None = None
    notification_preferences: NotificationPreferences | None = None


class TenantUpdate(Request):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: Phone | None = None
    status: Literal["active", "former"] | None = None


class TenantPage(Response):
    items: list[Tenant]
    next_cursor: str | None


class ConsentRecord(Response):
    purpose: Purpose
    policy_version: str
    granted: bool
    recorded_at: datetime
    source: Literal["portal", "staff_on_behalf", "import"]


class ConsentCreate(Request):
    purpose: Purpose
    policy_version: str = Field(min_length=1, max_length=50)
    granted: bool


class ConsentRecordPage(Response):
    items: list[ConsentRecord]
    next_cursor: str | None


InviteChannel = Literal["email", "whatsapp", "link"]


class TenantInviteRequest(Request):
    """email: the app's welcome email with a one-time set-password link (contract v0.14.0; Cognito's
    invitation email before). whatsapp: the same link over WhatsApp. link: the link is returned for
    staff to share themselves."""

    channel: InviteChannel = "email"


class TenantInviteResult(Response):
    channel: InviteChannel
    expires_at: datetime
    link: str | None = None  # channel "link" only: the one-time set-password link


class InviteInfo(Response):
    """What the set-password page shows. Public: the link itself is the credential."""

    kind: Literal["tenant", "staff"] = "tenant"  # who the link is for (contract v0.15.0)
    full_name: str
    email: str  # they sign in with this
    org_name: str
    expires_at: datetime
    brand: PublicBrand | None = None  # the company's branding, when on (contract v0.11.0)


STRENGTH_RULES = "at least 12 characters, with upper and lower case letters, a number and a symbol"


class InviteAccept(Request):
    password: str = Field(min_length=12, max_length=256)

    @field_validator("password")
    @classmethod
    def _rules(cls, v: str) -> str:  # same rules as the Cognito user pool (infra stacks_auth)
        if not (
            any(c.islower() for c in v)
            and any(c.isupper() for c in v)
            and any(c.isdigit() for c in v)
            and any(not c.isalnum() and not c.isspace() for c in v)
        ):
            raise ValueError(f"password needs {STRENGTH_RULES}")
        return v

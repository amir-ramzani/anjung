from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import EmailStr, Field, StringConstraints, model_validator

from app.domains.common import Money, Request, Response
from app.domains.organisations.branding import Branding, BrandingIn, PublicBrand
from app.domains.organisations.plan import OrgPlan

Locale = Literal["en", "ms"]
Role = Literal["platform_admin", "org_admin", "property_manager", "tenant", "vendor"]
AssignableRole = Literal["org_admin", "property_manager"]
Hhmm = Annotated[str, StringConstraints(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]


class LateFeeRule(Request):
    type: Literal["none", "fixed", "percent_per_month"]
    grace_days: int = Field(default=0, ge=0, le=60)
    fixed_amount: Money | None = None
    percent_bp: int | None = Field(default=None, ge=0, le=10000)

    @model_validator(mode="after")
    def _needs(self) -> "LateFeeRule":
        if self.type == "fixed" and self.fixed_amount is None:
            raise ValueError("fixed_amount is required for a fixed late fee")
        if self.type == "percent_per_month" and self.percent_bp is None:
            raise ValueError("percent_bp is required for a percent_per_month late fee")
        return self


class QuietHours(Request):
    start: Hhmm | None = None
    end: Hhmm | None = None


class OrgSettings(Request):
    default_due_day: int | None = Field(default=None, ge=1, le=28)
    invoice_lead_days: int | None = Field(default=None, ge=0, le=30)
    reminder_offsets_days: list[Annotated[int, Field(ge=-30, le=60)]] | None = None
    quiet_hours: QuietHours | None = None
    late_fee: LateFeeRule | None = None
    default_locale: Locale | None = None
    require_signed_agreement: bool | None = (
        None  # default on: tenants sign before a lease is activated
    )


class OrgSettingsOut(Response):
    default_due_day: int | None = None
    invoice_lead_days: int | None = None
    reminder_offsets_days: list[int] | None = None
    quiet_hours: QuietHours | None = None
    late_fee: dict[str, object] | None = None
    default_locale: Locale | None = None
    require_signed_agreement: bool | None = None


class Organization(Response):
    id: str
    name: str = Field(max_length=200)
    status: Literal["active", "suspended", "closed"]
    settings: OrgSettingsOut
    created_at: datetime
    updated_at: datetime
    branding: Branding | None = None  # contract v0.11.0
    plan: OrgPlan | None = None  # set by the platform admin (contract v0.12.0)


class OrganizationUpdate(Request):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    settings: OrgSettings | None = None
    branding: BrandingIn | None = None  # partial: fields left out stay as they are


class Me(Response):
    # the organisation's brand when branding is on (contract v0.11.0)
    branding: PublicBrand | None = None
    # staff only: the organisation's plan, for the usage line and the read-only banner (contract v0.12.0)
    plan: OrgPlan | None = None
    user_id: str
    org_id: str
    roles: list[Role]
    tenant_id: str | None = None
    email: str
    locale: Locale | None = None
    # staff: the access dates an admin set (shown on their dashboard); None for no limit
    active_from: date | None = None
    active_until: date | None = None


class User(Response):
    user_id: str
    email: EmailStr
    name: str | None = None
    roles: list[Role]
    status: Literal["invited", "active", "disabled"]
    # access window (inclusive Malaysian dates); null = no limit on that side
    active_from: date | None = None
    active_until: date | None = None
    # what the account allows today: invited and active can use the app, the others cannot
    access: Literal["invited", "active", "scheduled", "expired", "disabled"]
    # property managers: most units at once in the properties they manage; None = no limit (contract v0.13.0)
    unit_quota: int | None = None
    units_managed: int  # units (whole units that are on) in the properties they manage now
    created_at: datetime


def _check_window(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise ValueError("active_until must not be before active_from")


class UserInvite(Request):
    email: EmailStr
    name: str | None = Field(default=None, max_length=200)
    roles: list[AssignableRole] = Field(min_length=1)
    active_from: date | None = None
    active_until: date | None = None
    unit_quota: int | None = Field(default=None, ge=0, le=100000)

    @model_validator(mode="after")
    def _unique(self) -> "UserInvite":
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("roles must be unique")
        _check_window(self.active_from, self.active_until)
        return self


class UserUpdate(Request):
    roles: list[AssignableRole] | None = Field(default=None, min_length=1)
    status: Literal["active", "disabled"] | None = None
    # explicit null removes that limit; omit to leave it unchanged
    active_from: date | None = None
    active_until: date | None = None
    unit_quota: int | None = Field(default=None, ge=0, le=100000)

    @model_validator(mode="after")
    def _unique(self) -> "UserUpdate":
        if self.roles and len(set(self.roles)) != len(self.roles):
            raise ValueError("roles must be unique")
        _check_window(self.active_from, self.active_until)
        return self


class UserPage(Response):
    items: list[User]
    next_cursor: str | None

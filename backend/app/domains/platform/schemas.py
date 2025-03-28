from typing import Literal

from pydantic import EmailStr, Field

from app.domains.common import Request, Response
from app.domains.organisations.branding import BrandingIn
from app.domains.organisations.plan import PlanIn
from app.domains.organisations.schemas import Organization, OrgSettings


class InitialAdmin(Request):
    email: EmailStr
    name: str | None = Field(default=None, max_length=200)


class OrganizationCreate(Request):
    name: str = Field(min_length=1, max_length=200)
    settings: OrgSettings | None = None
    initial_admin: InitialAdmin
    reason: str = Field(min_length=1, max_length=500)
    branding: BrandingIn | None = (
        None  # optional; the org admin can change it later (contract v0.11.0)
    )
    plan: PlanIn | None = None  # the company's property limit and end date (contract v0.12.0)


class PlatformOrganizationUpdate(Request):
    status: Literal["active", "suspended", "closed"]
    reason: str = Field(min_length=1, max_length=500)


class OrganizationDetailsUpdate(Request):
    """What the platform admin may correct for a company (contract v0.17.0). Fields left out stay as they are;
    settings and the logo stay with the org admin."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    branding: BrandingIn | None = None
    reason: str = Field(min_length=1, max_length=500)


class AdminInviteResend(Request):
    reason: str = Field(min_length=1, max_length=500)


class AdminInviteResult(Response):
    """How many org admins who have not signed in yet were sent a new welcome email (v0.15.0)."""

    sent: int


class OrganizationPage(Response):
    items: list[Organization]
    next_cursor: str | None

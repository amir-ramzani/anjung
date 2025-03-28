from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.container import Container
from app.core.auth import require_roles
from app.core.context import RequestContext, Role
from app.domains.common import CursorQ, IdPath, LimitQ
from app.domains.organisations.plan import PlanUpdate
from app.domains.organisations.schemas import Organization
from app.domains.platform.schemas import (
    AdminInviteResend,
    AdminInviteResult,
    OrganizationCreate,
    OrganizationDetailsUpdate,
    OrganizationPage,
    PlatformOrganizationUpdate,
)
from app.domains.platform.service import PlatformService

router = APIRouter(prefix="/platform/organizations", tags=["Platform"])
_ADMIN = Depends(require_roles(Role.PLATFORM_ADMIN))


def _svc(request: Request, ctx: RequestContext) -> PlatformService:
    c: Container = request.app.state.container
    return PlatformService(c, ctx)


@router.get("", response_model=OrganizationPage, operation_id="listOrganizations")
def list_orgs(
    request: Request,
    status: Annotated[Literal["active", "suspended", "closed"] | None, Query()] = None,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = _ADMIN,
) -> Any:
    items, nxt = _svc(request, ctx).page(limit, cursor, status)
    return {"items": items, "next_cursor": nxt}


@router.post("", response_model=Organization, status_code=201, operation_id="createOrganization")
def create_org(body: OrganizationCreate, request: Request, ctx: RequestContext = _ADMIN) -> Any:
    return _svc(request, ctx).create(body)


@router.get("/{org_id}", response_model=Organization, operation_id="getPlatformOrganization")
def get_org(org_id: IdPath, request: Request, ctx: RequestContext = _ADMIN) -> Any:
    return _svc(request, ctx).get(org_id)


@router.patch("/{org_id}", response_model=Organization, operation_id="updatePlatformOrganization")
def patch_org(
    org_id: IdPath,
    body: PlatformOrganizationUpdate,
    request: Request,
    ctx: RequestContext = _ADMIN,
) -> Any:
    return _svc(request, ctx).set_status(org_id, body)


@router.patch("/{org_id}/plan", response_model=Organization, operation_id="updateOrganizationPlan")
def patch_plan(
    org_id: IdPath, body: PlanUpdate, request: Request, ctx: RequestContext = _ADMIN
) -> Any:
    """The company's property limit and end date (contract v0.12.0). Audited with the reason."""
    return _svc(request, ctx).set_plan(org_id, body)


@router.patch(
    "/{org_id}/details", response_model=Organization, operation_id="updateOrganizationDetails"
)
def patch_details(
    org_id: IdPath, body: OrganizationDetailsUpdate, request: Request, ctx: RequestContext = _ADMIN
) -> Any:
    """Correct a company's name and branding (contract v0.17.0). Audited with the reason."""
    return _svc(request, ctx).update_details(org_id, body)


@router.post(
    "/{org_id}/admin-invite",
    response_model=AdminInviteResult,
    status_code=202,
    operation_id="resendOrganizationAdminInvite",
)
def resend_admin_invite(
    org_id: IdPath, body: AdminInviteResend, request: Request, ctx: RequestContext = _ADMIN
) -> Any:
    """A new welcome email for the organisation's admins who have not signed in yet (v0.15.0)."""
    return {"sent": _svc(request, ctx).resend_admin_invite(org_id, body.reason)}

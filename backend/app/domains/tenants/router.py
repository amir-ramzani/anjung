from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import EmailStr

from app.container import Container
from app.core.auth import require_roles
from app.core.context import RequestContext, Role
from app.domains.common import CursorQ, IdPath, LimitQ
from app.domains.lifecycle import DeletionPreview, DeletionResult, ReasonQ
from app.domains.tenants.schemas import (
    ConsentCreate,
    ConsentRecord,
    ConsentRecordPage,
    Tenant,
    TenantCreate,
    TenantInviteRequest,
    TenantInviteResult,
    TenantPage,
    TenantUpdate,
)
from app.domains.tenants.service import TenantService

router = APIRouter(tags=["Tenants"])
_STAFF = Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER))
_ANY = Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER, Role.TENANT))


def _svc(request: Request, ctx: RequestContext) -> TenantService:
    c: Container = request.app.state.container
    return TenantService(c, ctx)


def _out(svc: TenantService, item: dict[str, Any]) -> dict[str, Any]:
    return svc.view_with_rentals([item])[0]


@router.get("/tenants", response_model=TenantPage, operation_id="listTenants")
def list_tenants(
    request: Request,
    status: Annotated[Literal["invited", "active", "former", "anonymised"] | None, Query()] = None,
    email: Annotated[EmailStr | None, Query()] = None,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = _STAFF,
) -> Any:
    svc = _svc(request, ctx)
    items, nxt = svc.page(limit, cursor, status, email)
    return {"items": svc.view_with_rentals(items), "next_cursor": nxt}


@router.post("/tenants", response_model=Tenant, status_code=201, operation_id="createTenant")
def create_tenant(body: TenantCreate, request: Request, ctx: RequestContext = _STAFF) -> Any:
    svc = _svc(request, ctx)
    return _out(svc, svc.create(body))


@router.get("/tenants/{tenant_id}", response_model=Tenant, operation_id="getTenant")
def get_tenant(tenant_id: IdPath, request: Request, ctx: RequestContext = _ANY) -> Any:
    svc = _svc(request, ctx)
    return _out(svc, svc.get(tenant_id))


@router.patch("/tenants/{tenant_id}", response_model=Tenant, operation_id="updateTenant")
def update_tenant(
    tenant_id: IdPath, body: TenantUpdate, request: Request, ctx: RequestContext = _STAFF
) -> Any:
    svc = _svc(request, ctx)
    return _out(svc, svc.update(tenant_id, body))


@router.get(
    "/tenants/{tenant_id}/deletion",
    response_model=DeletionPreview,
    operation_id="previewTenantDeletion",
)
def tenant_deletion(tenant_id: IdPath, request: Request, ctx: RequestContext = _STAFF) -> Any:
    return _svc(request, ctx).deletion(tenant_id)


@router.delete("/tenants/{tenant_id}", response_model=DeletionResult, operation_id="deleteTenant")
def delete_tenant(
    tenant_id: IdPath, reason: ReasonQ, request: Request, ctx: RequestContext = _STAFF
) -> Any:
    return _svc(request, ctx).delete(tenant_id, reason)


@router.post(
    "/tenants/{tenant_id}/invite",
    response_model=TenantInviteResult,
    status_code=202,
    operation_id="inviteTenant",
)
def invite_tenant(
    tenant_id: IdPath,
    request: Request,
    body: Annotated[TenantInviteRequest | None, Body()] = None,
    ctx: RequestContext = _STAFF,
) -> Any:
    return _svc(request, ctx).invite(tenant_id, (body or TenantInviteRequest()).channel)


@router.get(
    "/tenants/{tenant_id}/consents",
    response_model=ConsentRecordPage,
    operation_id="listTenantConsents",
)
def list_consents(
    tenant_id: IdPath,
    request: Request,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = _ANY,
) -> Any:
    items, nxt = _svc(request, ctx).consents(tenant_id, limit, cursor)
    return {"items": items, "next_cursor": nxt}


@router.post(
    "/tenants/{tenant_id}/consents",
    response_model=ConsentRecord,
    status_code=201,
    operation_id="recordTenantConsent",
)
def record_consent(
    tenant_id: IdPath, body: ConsentCreate, request: Request, ctx: RequestContext = _ANY
) -> Any:
    return _svc(request, ctx).record_consent(tenant_id, body)

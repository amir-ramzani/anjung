from typing import Any

from fastapi import APIRouter, Depends, Request

from app.container import Container
from app.core.auth import require_roles
from app.core.context import RequestContext, Role
from app.domains.common import CursorQ, IdPath, LimitQ
from app.domains.privacy.schemas import (
    PrivacyExport,
    PrivacyRequest,
    PrivacyRequestCreate,
    PrivacyRequestPage,
    PrivacyRequestUpdate,
)
from app.domains.privacy.service import PrivacyService

router = APIRouter(tags=["Privacy"])
_ADMIN = Depends(require_roles(Role.ORG_ADMIN))
_TENANT = Depends(require_roles(Role.TENANT))
_ADMIN_OR_TENANT = Depends(require_roles(Role.ORG_ADMIN, Role.TENANT))


def _svc(request: Request, ctx: RequestContext) -> PrivacyService:
    c: Container = request.app.state.container
    return PrivacyService(c, ctx)


@router.get("/privacy/export", response_model=PrivacyExport, operation_id="exportOwnData")
def export_own(request: Request, ctx: RequestContext = _TENANT) -> Any:
    return _svc(request, ctx).export_own()


@router.get(
    "/tenants/{tenant_id}/privacy-export",
    response_model=PrivacyExport,
    operation_id="exportTenantData",
)
def export_tenant(tenant_id: IdPath, request: Request, ctx: RequestContext = _ADMIN) -> Any:
    return _svc(request, ctx).export_for(tenant_id)


@router.get(
    "/privacy/requests", response_model=PrivacyRequestPage, operation_id="listPrivacyRequests"
)
def list_requests(
    request: Request,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = _ADMIN_OR_TENANT,
) -> Any:
    items, nxt = _svc(request, ctx).page(limit, cursor)
    return {"items": items, "next_cursor": nxt}


@router.post(
    "/privacy/requests",
    response_model=PrivacyRequest,
    status_code=201,
    operation_id="createPrivacyRequest",
)
def create_request(
    body: PrivacyRequestCreate, request: Request, ctx: RequestContext = _ADMIN_OR_TENANT
) -> Any:
    return _svc(request, ctx).create(body)


@router.get(
    "/privacy/requests/{privacy_request_id}",
    response_model=PrivacyRequest,
    operation_id="getPrivacyRequest",
)
def get_request(
    privacy_request_id: IdPath, request: Request, ctx: RequestContext = _ADMIN_OR_TENANT
) -> Any:
    return _svc(request, ctx).get(privacy_request_id)


@router.patch(
    "/privacy/requests/{privacy_request_id}",
    response_model=PrivacyRequest,
    operation_id="updatePrivacyRequest",
)
def update_request(
    privacy_request_id: IdPath,
    body: PrivacyRequestUpdate,
    request: Request,
    ctx: RequestContext = _ADMIN,
) -> Any:
    return _svc(request, ctx).update(privacy_request_id, body)

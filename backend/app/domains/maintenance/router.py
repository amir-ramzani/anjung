from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.container import Container
from app.core.auth import require_roles
from app.core.context import RequestContext, Role
from app.core.ids import ID_PATTERN
from app.domains.common import CursorQ, IdPath, LimitQ
from app.domains.lifecycle import DeletionPreview, DeletionResult, ReasonQ
from app.domains.maintenance.schemas import (
    Comment,
    CommentCreate,
    CommentPage,
    MaintenanceRequest,
    MaintenanceRequestCreate,
    MaintenanceRequestPage,
    MaintenanceRequestUpdate,
    Status,
)
from app.domains.maintenance.service import MaintenanceService

router = APIRouter(tags=["Maintenance"])
_ALL = Depends(
    require_roles(Role.PLATFORM_ADMIN, Role.ORG_ADMIN, Role.PROPERTY_MANAGER, Role.TENANT)
)
_CREATE = Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER, Role.TENANT))
_STAFF = Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER))
_ADMIN = Depends(require_roles(Role.ORG_ADMIN))


def _svc(request: Request, ctx: RequestContext) -> MaintenanceService:
    c: Container = request.app.state.container
    return MaintenanceService(c, ctx)


@router.get(
    "/maintenance-requests",
    response_model=MaintenanceRequestPage,
    operation_id="listMaintenanceRequests",
)
def list_requests(
    request: Request,
    status: Annotated[Status | None, Query()] = None,
    unit_id: Annotated[str | None, Query(pattern=ID_PATTERN)] = None,
    property_id: Annotated[str | None, Query(pattern=ID_PATTERN)] = None,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = _ALL,
) -> Any:
    svc = _svc(request, ctx)
    items, nxt = svc.page(limit, cursor, status, unit_id, property_id)
    return {"items": svc.present(items), "next_cursor": nxt}


@router.post(
    "/maintenance-requests",
    response_model=MaintenanceRequest,
    status_code=201,
    operation_id="createMaintenanceRequest",
)
def create_request(
    body: MaintenanceRequestCreate, request: Request, ctx: RequestContext = _CREATE
) -> Any:
    svc = _svc(request, ctx)
    return svc.present_one(svc.create(body))


@router.get(
    "/maintenance-requests/{request_id}",
    response_model=MaintenanceRequest,
    operation_id="getMaintenanceRequest",
)
def get_request(request_id: IdPath, request: Request, ctx: RequestContext = _ALL) -> Any:
    svc = _svc(request, ctx)
    return svc.present_one(svc.get(request_id))


@router.patch(
    "/maintenance-requests/{request_id}",
    response_model=MaintenanceRequest,
    operation_id="updateMaintenanceRequest",
)
def update_request(
    request_id: IdPath,
    body: MaintenanceRequestUpdate,
    request: Request,
    ctx: RequestContext = _STAFF,
) -> Any:
    svc = _svc(request, ctx)
    return svc.present_one(svc.update(request_id, body))


@router.get(
    "/maintenance-requests/{request_id}/comments",
    response_model=CommentPage,
    operation_id="listComments",
)
def list_comments(
    request_id: IdPath,
    request: Request,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = _ALL,
) -> Any:
    items, nxt = _svc(request, ctx).comments(request_id, limit, cursor)
    return {"items": items, "next_cursor": nxt}


@router.post(
    "/maintenance-requests/{request_id}/comments",
    response_model=Comment,
    status_code=201,
    operation_id="createComment",
)
def create_comment(
    request_id: IdPath, body: CommentCreate, request: Request, ctx: RequestContext = _ALL
) -> Any:
    return _svc(request, ctx).comment(request_id, body)


@router.get(
    "/maintenance-requests/{request_id}/deletion",
    response_model=DeletionPreview,
    operation_id="previewMaintenanceRequestDeletion",
)
def maintenance_deletion(request_id: IdPath, request: Request, ctx: RequestContext = _ADMIN) -> Any:
    return _svc(request, ctx).deletion(request_id)


@router.delete(
    "/maintenance-requests/{request_id}",
    response_model=DeletionResult,
    operation_id="deleteMaintenanceRequest",
)
def delete_maintenance_request(
    request_id: IdPath, reason: ReasonQ, request: Request, ctx: RequestContext = _ADMIN
) -> Any:
    return _svc(request, ctx).delete(request_id, reason)

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.container import Container
from app.core.auth import require_roles
from app.core.context import RequestContext, Role
from app.domains.common import CursorQ, IdPath, LimitQ
from app.domains.leases.schemas import (
    AgreementPaper,
    AgreementSend,
    AgreementSign,
    Id,
    Lease,
    LeaseCreate,
    LeasePage,
    LeaseRenew,
    LeaseTerminate,
    LeaseUpdate,
)
from app.domains.leases.service import LeaseService
from app.domains.lifecycle import DeletionPreview, DeletionResult, ReasonQ

router = APIRouter(tags=["Leases"])
_STAFF = Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER))
_ANY = Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER, Role.TENANT))
_TENANT = Depends(require_roles(Role.TENANT))


def _svc(request: Request, ctx: RequestContext) -> LeaseService:
    c: Container = request.app.state.container
    return LeaseService(c, ctx)


@router.get("/leases", response_model=LeasePage, operation_id="listLeases")
def list_leases(
    request: Request,
    status: Annotated[Literal["draft", "active", "terminated", "expired"] | None, Query()] = None,
    unit_id: Annotated[Id | None, Query()] = None,
    tenant_id: Annotated[Id | None, Query()] = None,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = _ANY,
) -> Any:
    svc = _svc(request, ctx)
    items, nxt = svc.page(limit, cursor, status, unit_id, tenant_id)
    return {"items": svc.present(items), "next_cursor": nxt}


@router.post("/leases", response_model=Lease, status_code=201, operation_id="createLease")
def create_lease(body: LeaseCreate, request: Request, ctx: RequestContext = _STAFF) -> Any:
    svc = _svc(request, ctx)
    return svc.present_one(svc.create(body))


@router.get("/leases/{lease_id}", response_model=Lease, operation_id="getLease")
def get_lease(lease_id: IdPath, request: Request, ctx: RequestContext = _ANY) -> Any:
    svc = _svc(request, ctx)
    return svc.present_one(svc.get(lease_id))


@router.patch("/leases/{lease_id}", response_model=Lease, operation_id="updateLease")
def update_lease(
    lease_id: IdPath, body: LeaseUpdate, request: Request, ctx: RequestContext = _STAFF
) -> Any:
    svc = _svc(request, ctx)
    return svc.present_one(svc.update(lease_id, body))


@router.get(
    "/leases/{lease_id}/deletion",
    response_model=DeletionPreview,
    operation_id="previewLeaseDeletion",
)
def lease_deletion(lease_id: IdPath, request: Request, ctx: RequestContext = _STAFF) -> Any:
    return _svc(request, ctx).deletion(lease_id)


@router.delete("/leases/{lease_id}", response_model=DeletionResult, operation_id="deleteLease")
def delete_lease(
    lease_id: IdPath, reason: ReasonQ, request: Request, ctx: RequestContext = _STAFF
) -> Any:
    return _svc(request, ctx).delete(lease_id, reason)


@router.post("/leases/{lease_id}/activate", response_model=Lease, operation_id="activateLease")
def activate_lease(lease_id: IdPath, request: Request, ctx: RequestContext = _STAFF) -> Any:
    svc = _svc(request, ctx)
    return svc.present_one(svc.activate(lease_id))


@router.post("/leases/{lease_id}/terminate", response_model=Lease, operation_id="terminateLease")
def terminate_lease(
    lease_id: IdPath, body: LeaseTerminate, request: Request, ctx: RequestContext = _STAFF
) -> Any:
    svc = _svc(request, ctx)
    return svc.present_one(svc.terminate(lease_id, body))


@router.post(
    "/leases/{lease_id}/renew", response_model=Lease, status_code=201, operation_id="renewLease"
)
def renew_lease(
    lease_id: IdPath, body: LeaseRenew, request: Request, ctx: RequestContext = _STAFF
) -> Any:
    svc = _svc(request, ctx)
    return svc.present_one(svc.renew(lease_id, body))


# ---- tenancy agreement: send to the tenants, each signs, then staff activate (contract v0.7.0)
@router.post(
    "/leases/{lease_id}/agreement/send", response_model=Lease, operation_id="sendLeaseAgreement"
)
def send_agreement(
    lease_id: IdPath, body: AgreementSend, request: Request, ctx: RequestContext = _STAFF
) -> Any:
    svc = _svc(request, ctx)
    return svc.present_one(svc.send_agreement(lease_id, body))


@router.post(
    "/leases/{lease_id}/agreement/sign", response_model=Lease, operation_id="signLeaseAgreement"
)
def sign_agreement(
    lease_id: IdPath, body: AgreementSign, request: Request, ctx: RequestContext = _TENANT
) -> Any:
    svc = _svc(request, ctx)
    return svc.present_one(svc.sign_agreement(lease_id, body, request.headers.get("user-agent")))


@router.post(
    "/leases/{lease_id}/agreement/paper",
    response_model=Lease,
    operation_id="recordPaperSignature",
)
def paper_agreement(
    lease_id: IdPath, body: AgreementPaper, request: Request, ctx: RequestContext = _STAFF
) -> Any:
    svc = _svc(request, ctx)
    return svc.present_one(svc.paper_agreement(lease_id, body))

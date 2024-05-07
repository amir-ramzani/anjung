from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.container import Container
from app.core.auth import require_roles
from app.core.context import RequestContext, Role
from app.core.ids import ID_PATTERN
from app.domains.common import CursorQ, IdPath, LimitQ
from app.domains.lifecycle import DeletionPreview, DeletionResult, ReasonQ, status_in
from app.domains.properties.schemas import (
    Property,
    PropertyCreate,
    PropertyPage,
    PropertyUpdate,
    Unit,
    UnitCreate,
    UnitPage,
    UnitUpdate,
)
from app.domains.properties.service import PropertyService

router = APIRouter(tags=["Properties"])
_STAFF = Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER))
_STAFF_OR_TENANT = Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER, Role.TENANT))


def _svc(request: Request, ctx: RequestContext) -> PropertyService:
    c: Container = request.app.state.container
    return PropertyService(c, ctx)


@router.get("/properties", response_model=PropertyPage, operation_id="listProperties")
def list_properties(
    request: Request,
    status: Annotated[Literal["active", "inactive", "archived"] | None, Query()] = None,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = _STAFF,
) -> Any:
    svc = _svc(request, ctx)
    items, nxt = svc.page(limit, cursor, status_in(status))
    return {"items": items, "next_cursor": nxt, "quota": svc.quota()}


@router.post("/properties", response_model=Property, status_code=201, operation_id="createProperty")
def create_property(body: PropertyCreate, request: Request, ctx: RequestContext = _STAFF) -> Any:
    return _svc(request, ctx).create(body)


@router.get("/properties/{property_id}", response_model=Property, operation_id="getProperty")
def get_property(property_id: IdPath, request: Request, ctx: RequestContext = _STAFF) -> Any:
    return _svc(request, ctx).get(property_id)


@router.patch("/properties/{property_id}", response_model=Property, operation_id="updateProperty")
def update_property(
    property_id: IdPath, body: PropertyUpdate, request: Request, ctx: RequestContext = _STAFF
) -> Any:
    return _svc(request, ctx).update(property_id, body)


@router.post(
    "/properties/{property_id}/deactivate",
    response_model=Property,
    operation_id="deactivateProperty",
)
def deactivate_property(property_id: IdPath, request: Request, ctx: RequestContext = _STAFF) -> Any:
    return _svc(request, ctx).deactivate(property_id)


@router.post(
    "/properties/{property_id}/reactivate",
    response_model=Property,
    operation_id="reactivateProperty",
)
def reactivate_property(property_id: IdPath, request: Request, ctx: RequestContext = _STAFF) -> Any:
    return _svc(request, ctx).reactivate(property_id)


@router.get(
    "/properties/{property_id}/deletion",
    response_model=DeletionPreview,
    operation_id="previewPropertyDeletion",
)
def property_deletion(property_id: IdPath, request: Request, ctx: RequestContext = _STAFF) -> Any:
    return _svc(request, ctx).deletion(property_id)


@router.delete(
    "/properties/{property_id}", response_model=DeletionResult, operation_id="deleteProperty"
)
def delete_property(
    property_id: IdPath, reason: ReasonQ, request: Request, ctx: RequestContext = _STAFF
) -> Any:
    return _svc(request, ctx).delete(property_id, reason)


@router.get("/properties/{property_id}/units", response_model=UnitPage, operation_id="listUnits")
def list_units(
    property_id: IdPath,
    request: Request,
    status: Annotated[
        Literal["vacant", "occupied", "maintenance", "inactive", "archived"] | None, Query()
    ] = None,
    type: Annotated[Literal["whole_unit", "room", "parking"] | None, Query()] = None,
    parent_unit_id: Annotated[str | None, Query(pattern=ID_PATTERN)] = None,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = _STAFF,
) -> Any:
    items, nxt = _svc(request, ctx).list_units(
        property_id, limit, cursor, status_in(status), type, parent_unit_id
    )
    return {"items": items, "next_cursor": nxt}


@router.post(
    "/properties/{property_id}/units",
    response_model=Unit,
    status_code=201,
    operation_id="createUnit",
)
def create_unit(
    property_id: IdPath, body: UnitCreate, request: Request, ctx: RequestContext = _STAFF
) -> Any:
    return _svc(request, ctx).create_unit(property_id, body)


@router.get("/units/{unit_id}", response_model=Unit, operation_id="getUnit")
def get_unit(unit_id: IdPath, request: Request, ctx: RequestContext = _STAFF_OR_TENANT) -> Any:
    return _svc(request, ctx).get_unit(unit_id)


@router.patch("/units/{unit_id}", response_model=Unit, operation_id="updateUnit")
def update_unit(
    unit_id: IdPath, body: UnitUpdate, request: Request, ctx: RequestContext = _STAFF
) -> Any:
    return _svc(request, ctx).update_unit(unit_id, body)


@router.post("/units/{unit_id}/deactivate", response_model=Unit, operation_id="deactivateUnit")
def deactivate_unit(unit_id: IdPath, request: Request, ctx: RequestContext = _STAFF) -> Any:
    return _svc(request, ctx).deactivate_unit(unit_id)


@router.post("/units/{unit_id}/reactivate", response_model=Unit, operation_id="reactivateUnit")
def reactivate_unit(unit_id: IdPath, request: Request, ctx: RequestContext = _STAFF) -> Any:
    return _svc(request, ctx).reactivate_unit(unit_id)


@router.get(
    "/units/{unit_id}/deletion", response_model=DeletionPreview, operation_id="previewUnitDeletion"
)
def unit_deletion(unit_id: IdPath, request: Request, ctx: RequestContext = _STAFF) -> Any:
    return _svc(request, ctx).unit_deletion(unit_id)


@router.delete("/units/{unit_id}", response_model=DeletionResult, operation_id="deleteUnit")
def delete_unit(
    unit_id: IdPath, reason: ReasonQ, request: Request, ctx: RequestContext = _STAFF
) -> Any:
    return _svc(request, ctx).delete_unit(unit_id, reason)

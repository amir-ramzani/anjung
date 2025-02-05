from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi import Response as HttpResponse
from pydantic import Field

from app.container import Container
from app.core.auth import require_roles
from app.core.context import RequestContext, Role
from app.core.dates import today_kl
from app.core.errors import ValidationFailed
from app.core.ids import ID_PATTERN
from app.domains.billing.schemas import Period
from app.domains.common import MoneyOut, Response
from app.domains.reports.service import ReportService, cached, to_csv
from app.domains.scope import Scope

router = APIRouter(tags=["Reports"])
_STAFF = Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER))
PropQ = Annotated[str | None, Query(pattern=ID_PATTERN)]
# `csv`: the same report as a spreadsheet download (contract v0.15.0)
FormatQ = Annotated[Literal["json", "csv"], Query(alias="format")]


class ArrearsRow(Response):
    lease_id: str
    tenant_id: str
    unit_id: str
    property_id: str
    outstanding: MoneyOut
    oldest_due_date: str
    days_overdue: int
    bucket: str


class ArrearsReport(Response):
    as_of: str
    rows: list[ArrearsRow]
    total_outstanding: MoneyOut


class OccupancyRow(Response):
    property_id: str
    units_total: int
    units_occupied: int
    units_vacant: int
    occupancy_rate: float
    rooms_total: int | None = None
    rooms_occupied: int | None = None
    # parking bays let on their own leases (contract v0.10.0); not part of units_total or the rate
    bays_total: int | None = None
    bays_occupied: int | None = None


class OccupancyReport(Response):
    as_of: str
    rows: list[OccupancyRow]


class IncomeRow(Response):
    period: str
    property_id: str | None
    invoiced: MoneyOut
    collected: MoneyOut
    outstanding: MoneyOut


class IncomeReport(Response):
    as_of: str
    rows: list[IncomeRow]


class MaintenanceRow(Response):
    property_id: str
    open: int
    open_urgent: int  # open with priority high or urgent
    oldest_open_days: int | None
    resolved: int  # resolved (or since closed) in the period
    avg_days_to_resolve: float | None
    cancelled: int


class MaintenanceReport(Response):
    as_of: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None
    rows: list[MaintenanceRow]


def _allowed(request: Request, ctx: RequestContext) -> frozenset[str] | None:
    """A property manager's reports cover only the properties they manage (manager scope)."""
    scope = Scope(request.app.state.container, ctx)
    return scope.property_ids if scope.restricted else None


def _svc(request: Request, ctx: RequestContext, allowed: frozenset[str] | None) -> ReportService:
    c: Container = request.app.state.container
    return ReportService(c, ctx.org_id, allowed)


def _out(
    report: str, data: Any, fmt: str, request: Request, ctx: RequestContext, allowed: Any
) -> Any:
    """JSON as before, or a CSV download named after the report and today's date."""
    if fmt != "csv":
        return data
    svc = _svc(request, ctx, allowed)
    day = today_kl(request.app.state.container.clock).isoformat()
    return HttpResponse(
        content=to_csv(report, data, svc.names(data["rows"])).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="anjung-{report}-{day}.csv"'},
    )


def _period_range(from_: str | None, to: str | None) -> None:
    if from_ and to and to < from_:
        raise ValidationFailed(details=[{"field": "to", "issue": "must not be before from"}])


@router.get("/reports/arrears", response_model=ArrearsReport, operation_id="getArrearsReport")
def arrears(
    request: Request, property_id: PropQ = None, fmt: FormatQ = "json", ctx: RequestContext = _STAFF
) -> Any:
    allowed = _allowed(
        request, ctx
    )  # part of the cache key: a manager never gets the org-wide copy
    data = cached(
        (ctx.org_id, "arrears", property_id, allowed),
        lambda: _svc(request, ctx, allowed).arrears(property_id),
    )
    return _out("arrears", data, fmt, request, ctx, allowed)


@router.get("/reports/occupancy", response_model=OccupancyReport, operation_id="getOccupancyReport")
def occupancy(request: Request, fmt: FormatQ = "json", ctx: RequestContext = _STAFF) -> Any:
    allowed = _allowed(request, ctx)
    data = cached(
        (ctx.org_id, "occupancy", allowed), lambda: _svc(request, ctx, allowed).occupancy()
    )
    return _out("occupancy", data, fmt, request, ctx, allowed)


@router.get("/reports/income", response_model=IncomeReport, operation_id="getIncomeReport")
def income(
    request: Request,
    from_: Annotated[Period | None, Query(alias="from")] = None,
    to: Annotated[Period | None, Query()] = None,
    property_id: PropQ = None,
    fmt: FormatQ = "json",
    ctx: RequestContext = _STAFF,
) -> Any:
    _period_range(from_, to)
    allowed = _allowed(request, ctx)
    data = cached(
        (ctx.org_id, "income", from_, to, property_id, allowed),
        lambda: _svc(request, ctx, allowed).income(from_, to, property_id),
    )
    return _out("income", data, fmt, request, ctx, allowed)


@router.get(
    "/reports/maintenance", response_model=MaintenanceReport, operation_id="getMaintenanceReport"
)
def maintenance(
    request: Request,
    from_: Annotated[Period | None, Query(alias="from")] = None,
    to: Annotated[Period | None, Query()] = None,
    property_id: PropQ = None,
    fmt: FormatQ = "json",
    ctx: RequestContext = _STAFF,
) -> Any:
    """Per property: open requests now, and those resolved in the period (contract v0.15.0)."""
    _period_range(from_, to)
    allowed = _allowed(request, ctx)
    data = cached(
        (ctx.org_id, "maintenance", from_, to, property_id, allowed),
        lambda: _svc(request, ctx, allowed).maintenance(from_, to, property_id),
    )
    return _out("maintenance", data, fmt, request, ctx, allowed)

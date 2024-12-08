from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.container import Container
from app.core.auth import require_roles
from app.core.context import RequestContext, Role
from app.domains.common import CursorQ, LimitQ, Response

router = APIRouter(tags=["Audit"])


class AuditEntry(Response):
    id: str
    actor_id: str
    actor_role: str | None = None
    action: str
    entity_type: str
    entity_id: str
    summary: dict[str, Any] | None = None
    ip: str | None = None
    request_id: str
    created_at: datetime


class AuditEntryPage(Response):
    items: list[AuditEntry]
    next_cursor: str | None


@router.get("/audit-log", response_model=AuditEntryPage, operation_id="listAuditLog")
def list_audit_log(
    request: Request,
    entity_type: Annotated[str | None, Query(max_length=50)] = None,
    entity_id: Annotated[str | None, Query(max_length=100)] = None,
    month: Annotated[str | None, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")] = None,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = Depends(require_roles(Role.ORG_ADMIN)),
) -> Any:
    c: Container = request.app.state.container
    repo = c.store.audit(ctx.org_id)
    if not (entity_type and entity_id) and month is None:
        month = c.clock.now().strftime("%Y-%m")
    rows, nxt = repo.page(
        month=month, entity_type=entity_type, entity_id=entity_id, limit=limit, cursor=cursor
    )
    items = [{**r, "entity_type": r["target_type"]} for r in rows]
    return {"items": items, "next_cursor": nxt}

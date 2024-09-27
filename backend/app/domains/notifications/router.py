from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.container import Container
from app.core.auth import require_roles
from app.core.clock import iso
from app.core.context import RequestContext, Role
from app.core.errors import BadRequest
from app.domains.common import CursorQ, LimitQ
from app.domains.notifications.preferences import PreferenceService
from app.domains.notifications.schemas import NotificationLogEntryPage
from app.domains.scope import Scope
from app.domains.tenants.schemas import NotificationPreferences

router = APIRouter(tags=["Notifications"])
_TENANT = Depends(require_roles(Role.TENANT))
_STAFF = Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER))


def _c(request: Request) -> Container:
    c: Container = request.app.state.container
    return c


@router.get(
    "/notifications/preferences",
    response_model=NotificationPreferences,
    response_model_exclude_none=True,
    operation_id="getNotificationPreferences",
)
def get_prefs(request: Request, ctx: RequestContext = _TENANT) -> Any:
    return PreferenceService(_c(request), ctx).get()


@router.put(
    "/notifications/preferences",
    response_model=NotificationPreferences,
    response_model_exclude_none=True,
    operation_id="putNotificationPreferences",
)
def put_prefs(
    body: NotificationPreferences, request: Request, ctx: RequestContext = _TENANT
) -> Any:
    return PreferenceService(_c(request), ctx).put(body)


@router.get(
    "/notifications/log",
    response_model=NotificationLogEntryPage,
    operation_id="listNotificationLog",
)
def list_log(
    request: Request,
    tenant_id: Annotated[str | None, Query()] = None,
    channel: Annotated[Literal["email", "whatsapp"] | None, Query()] = None,
    status: Annotated[
        Literal["queued", "sent", "delivered", "read", "failed", "suppressed"] | None, Query()
    ] = None,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = _STAFF,
) -> Any:
    """Newest first across this month and the previous one (older entries are in the archive)."""
    c = _c(request)
    repo = c.store.notifications(ctx.org_id)
    now = c.clock.now()
    months = [iso(now)[:7], iso(now.replace(day=1) - __import__("datetime").timedelta(days=1))[:7]]
    idx, inner = 0, None
    if cursor:
        try:
            state = c.store.cursors.decode(cursor, pk=f"notif-log:{ctx.org_id}", index=None)
            idx, inner = int(state["m"]), state.get("k")
        except (KeyError, ValueError, TypeError):
            raise BadRequest("Invalid cursor") from None
    scope = Scope(c, ctx)
    seen: dict[str, bool] = {}

    def visible(tid: str | None) -> bool:  # manager scope: messages to tenants they can see
        if not scope.restricted:
            return True
        if tid is None:
            return False
        if tid not in seen:
            seen[tid] = scope.tenant_id_ok(tid)
        return seen[tid]

    out: list[dict[str, Any]] = []
    while idx < len(months) and len(out) < limit:
        page, nxt = repo.page(
            months[idx],
            limit - len(out),
            inner,
            lambda it: (
                (not tenant_id or it["tenant_id"] == tenant_id)
                and (not channel or it["channel"] == channel)
                and (not status or it["status"] == status)
                and visible(it.get("tenant_id"))
            ),
        )
        out += page
        if nxt is not None:  # this month has more: remember where we stopped
            return _page(c, ctx, out, idx, nxt)
        idx, inner = idx + 1, None
    return {"items": [_entry(i) for i in out], "next_cursor": None}


def _page(
    c: Container, ctx: RequestContext, out: list[dict[str, Any]], idx: int, inner: str
) -> dict[str, Any]:
    cur = c.store.cursors.encode(
        key={"m": idx, "k": inner}, index=None, pk=f"notif-log:{ctx.org_id}"
    )
    return {"items": [_entry(i) for i in out], "next_cursor": cur}


def _entry(i: dict[str, Any]) -> dict[str, Any]:
    return {
        **{
            k: i[k]
            for k in (
                "id",
                "channel",
                "template",
                "locale",
                "status",
                "provider_message_id",
                "error",
                "created_at",
            )
        },
        "recipient_tenant_id": i["tenant_id"],
    }

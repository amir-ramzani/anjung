from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from app.container import Container
from app.core.auth import require_roles
from app.core.context import RequestContext, Role
from app.core.dates import today_kl
from app.domains.common import CursorQ, LimitQ
from app.domains.organisations import branding, plan
from app.domains.organisations.schemas import (
    Me,
    Organization,
    OrganizationUpdate,
    User,
    UserInvite,
    UserPage,
    UserUpdate,
)
from app.domains.organisations.service import OrganisationService, UserService

router = APIRouter(tags=["Organisation"])

ALL = (Role.PLATFORM_ADMIN, Role.ORG_ADMIN, Role.PROPERTY_MANAGER, Role.TENANT)


def _c(request: Request) -> Container:
    c: Container = request.app.state.container
    return c


@router.get("/me", response_model=Me, operation_id="getMe")
def get_me(request: Request, ctx: Annotated[RequestContext, Depends(require_roles(*ALL))]) -> Me:
    member = (
        _c(request).store.members(ctx.org_id).get(ctx.user_id)
        if ctx.roles & {Role.ORG_ADMIN, Role.PROPERTY_MANAGER}
        else None
    ) or {}
    c = _c(request)
    meta = c.store.org(ctx.org_id).get_meta() or {}
    staff = bool(ctx.roles & {Role.ORG_ADMIN, Role.PROPERTY_MANAGER})
    return Me(
        branding=branding.public(meta),
        plan=plan.view(c, ctx.org_id, meta, today_kl(c.clock)) if staff else None,
        active_from=member.get("active_from"),
        active_until=member.get("active_until"),
        user_id=ctx.user_id,
        org_id=ctx.org_id,
        roles=sorted(r.value for r in ctx.roles),
        tenant_id=ctx.tenant_id,
        email=ctx.email or "",
        locale=ctx.locale,
    )


@router.get("/org", response_model=Organization, operation_id="getOrganization")
def get_org(
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER))],
) -> Any:
    return OrganisationService(_c(request), ctx).get()


@router.patch("/org", response_model=Organization, operation_id="updateOrganization")
def update_org(
    body: OrganizationUpdate,
    request: Request,
    ctx: Annotated[RequestContext, Depends(require_roles(Role.ORG_ADMIN))],
) -> Any:
    return OrganisationService(_c(request), ctx).update(body)


users = APIRouter(tags=["Users"])
_ADMIN = Depends(require_roles(Role.ORG_ADMIN))


@users.get("/users", response_model=UserPage, operation_id="listUsers")
def list_users(
    request: Request,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = _ADMIN,
) -> Any:
    items, nxt = UserService(_c(request), ctx).page(limit, cursor)
    return {"items": items, "next_cursor": nxt}


@users.post("/users/invite", response_model=User, status_code=201, operation_id="inviteUser")
def invite_user(body: UserInvite, request: Request, ctx: RequestContext = _ADMIN) -> Any:
    return UserService(_c(request), ctx).invite(body)


@users.post(
    "/users/{user_id}/invite", response_model=User, status_code=202, operation_id="resendUserInvite"
)
def resend_user_invite(user_id: str, request: Request, ctx: RequestContext = _ADMIN) -> Any:
    return UserService(_c(request), ctx).resend_invite(user_id)


@users.patch("/users/{user_id}", response_model=User, operation_id="updateUser")
def update_user(
    user_id: str, body: UserUpdate, request: Request, ctx: RequestContext = _ADMIN
) -> Any:
    return UserService(_c(request), ctx).update(user_id, body)


@users.delete("/users/{user_id}", status_code=204, operation_id="deleteUser")
def delete_user(user_id: str, request: Request, ctx: RequestContext = _ADMIN) -> Response:
    UserService(_c(request), ctx).delete(user_id)
    return Response(status_code=204)

"""JWT validation and RBAC (LLD sections 3.1 and 3.2)."""

import json
import re
import threading
import time
from collections.abc import Callable
from typing import Any

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.access import blocked_reason
from app.core.context import RequestContext, Role
from app.core.dates import today_kl
from app.core.errors import AccountInactive, Forbidden, PlanEnded, PlanReadOnly, Unauthorized

KeyResolver = Callable[[str], Any]


def jwks_key_resolver(jwks_url: str) -> KeyResolver:
    client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)  # refetches on unknown kid
    return lambda token: client.get_signing_key_from_jwt(token).key


class TokenVerifier:
    def __init__(self, issuer: str, client_ids: list[str], key_resolver: KeyResolver) -> None:
        self._issuer, self._client_ids, self._resolve = issuer, set(client_ids), key_resolver

    def verify(self, token: str) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._resolve(token),
                algorithms=["RS256"],
                issuer=self._issuer,
                options={
                    "require": ["exp", "iat", "iss", "sub", "client_id", "token_use"],
                    "verify_aud": False,  # Cognito access tokens carry client_id, not aud
                },
                leeway=30,
            )
        except (jwt.PyJWTError, ValueError):
            raise Unauthorized() from None
        not_access = claims["token_use"] != "access"  # noqa: S105
        if not_access or claims["client_id"] not in self._client_ids:
            raise Unauthorized()
        return claims


def parse_roles(raw: Any) -> frozenset[Role]:
    """`roles` is a JSON array, or a JSON string of one. Anything unknown is rejected."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = [p.strip() for p in raw.strip("[]").split(",") if p.strip()]
    if not isinstance(raw, list) or not raw:
        raise Forbidden()
    try:
        return frozenset(Role(r) for r in raw)
    except ValueError:
        raise Forbidden() from None


class OrgStatusCache:
    """Per-process cache so suspension blocks API calls within `ttl` seconds (LLD 11.4)."""

    def __init__(self, loader: Callable[[str], dict[str, Any] | None], ttl: float) -> None:
        self._loader, self._ttl = loader, ttl
        self._data: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self._lock = threading.Lock()

    def get(self, org_id: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(org_id)
            if hit and now - hit[0] < self._ttl:
                return hit[1]
        value = self._loader(org_id)
        with self._lock:
            self._data[org_id] = (now, value)
        return value

    def invalidate(self, org_id: str) -> None:
        with self._lock:
            self._data.pop(org_id, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_bearer = HTTPBearer(auto_error=False)
_READS = {"GET", "HEAD", "OPTIONS"}


def _check_plan(request: Request, org: dict[str, Any], roles: frozenset[Role], today: Any) -> None:
    """The organisation's plan (contract v0.12.0): read-only for staff after its end date, then closed to all."""
    from app.domains.organisations import plan

    state = plan.status(org, today)
    if state == "active" or Role.PLATFORM_ADMIN in roles:
        return
    if state == "ended":
        raise PlanEnded(plan.ended_message(org))
    if (
        roles & {Role.ORG_ADMIN, Role.PROPERTY_MANAGER}
        and request.method not in _READS
        and not any(re.match(p, request.url.path) for p in plan.READ_ONLY_ALLOWED)
    ):
        until = plan.stored(org).get("active_until") or ""
        raise PlanReadOnly(
            plan.read_only_message(org),
            params={"date": until, "until": str(plan.read_only_end(org) or "")},
        )


def get_context(
    request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> RequestContext:
    if creds is None:
        raise Unauthorized()
    container = request.app.state.container
    claims = container.verifier.verify(creds.credentials)
    org_id = claims.get("org_id")
    if not org_id or not claims.get("roles"):
        raise Forbidden()  # tokens without tenancy claims are never valid for the API
    roles = parse_roles(claims["roles"])
    org = container.org_status.get(org_id)
    if org is None or org.get("status") != "active":
        raise Forbidden()
    _check_plan(request, org, roles, today_kl(container.clock))
    # staff access can be disabled or time-limited by an admin: checked on every request, so it takes
    # effect at once rather than when the token expires (app/core/access.py)
    if roles & {Role.ORG_ADMIN, Role.PROPERTY_MANAGER} and Role.PLATFORM_ADMIN not in roles:
        member = container.store.members(org_id).get(claims["sub"])
        reason = blocked_reason(member, today_kl(container.clock))
        if reason:
            raise AccountInactive(reason)
    ctx = RequestContext(
        user_id=claims["sub"],
        org_id=org_id,
        roles=roles,
        tenant_id=claims.get("tenant_id"),
        email=claims.get("email"),
        request_id=request.state.request_id,
        ip=request.client.host if request.client else None,
        locale=(org.get("settings") or {}).get("default_locale", "en"),
    )
    request.state.log_fields = {
        "org_id": ctx.org_id,
        "user_id": ctx.user_id,
        "role": ctx.actor_role,
    }
    return ctx


def require_roles(*allowed: Role) -> Callable[..., RequestContext]:
    def dep(ctx: RequestContext = Depends(get_context)) -> RequestContext:
        if not (ctx.roles & set(allowed)):
            raise Forbidden()
        return ctx

    dep.allowed_roles = frozenset(allowed)  # type: ignore[attr-defined]  # read by the contract test
    return dep

"""FastAPI app factory, middleware and router mounting."""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.container import Container
from app.core import tracing
from app.core.config import Settings
from app.core.errors import (
    AppError,
    BadRequest,
    Forbidden,
    NotFound,
    Unauthorized,
    ValidationFailed,
    issue_code,
)
from app.core.ids import new_id
from app.core.logging import configure_logging
from app.domains.audit.router import router as audit_router
from app.domains.billing.router import router as billing_router
from app.domains.billing.router import webhooks as webhooks_router
from app.domains.documents.router import router as documents_router
from app.domains.leases.router import router as leases_router
from app.domains.maintenance.router import router as maintenance_router
from app.domains.notifications.router import router as notifications_router
from app.domains.organisations.brands import router as brands_router
from app.domains.organisations.router import router as org_router
from app.domains.organisations.router import users as users_router
from app.domains.platform.router import router as platform_router
from app.domains.privacy.router import router as privacy_router
from app.domains.properties.router import router as properties_router
from app.domains.reports.router import router as reports_router
from app.domains.tenants.invites import router as invites_router
from app.domains.tenants.router import router as tenants_router

log = logging.getLogger("anjung.http")


def _issue_code(e: Any) -> dict[str, Any]:
    """Pydantic's error type as the detail's `code`, with its limits as `params` (contract v0.16.0). A
    validator's own message (`value_error`) gets a code made from its fixed text."""
    if e["type"] == "value_error":
        msg = str(e["msg"]).removeprefix("Value error, ")
        if msg.startswith("value is not a valid email address"):  # the reason after it varies
            return {"code": "invalid_email"}
        if msg.startswith("password needs "):  # the rules, spelled out
            return {"code": "weak_password"}
        return {"code": issue_code(msg)}
    ctx = {
        k: v
        for k, v in (e.get("ctx") or {}).items()
        if isinstance(v, str | int | float) and not isinstance(v, bool) and k != "error"
    }
    return {"code": e["type"], **({"params": ctx} if ctx else {})}


def _error(request: Request, err: AppError) -> JSONResponse:
    rid = getattr(request.state, "request_id", "unknown")
    return JSONResponse(err.to_body(rid), status_code=err.status_code, headers=err.headers)


def create_app(container: Container | None = None) -> FastAPI:
    if container is None:
        settings = Settings()  # raises on missing or malformed config
        configure_logging(settings.log_level, settings.env)
        container = Container.build(settings)
    settings = container.settings
    tracing.configure("anjung-api", settings.otel_exporter_otlp_endpoint)
    prod = settings.env == "prod"
    app = FastAPI(
        title="Anjung API",
        version="0.3.1",
        docs_url=None if prod else "/docs",
        redoc_url=None,
        openapi_url=None if prod else "/openapi.json",
    )
    app.state.container = container

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
            expose_headers=["X-Request-Id"],
        )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = new_id()
        request.state.log_fields = {}
        start = time.perf_counter()
        # continue the caller's trace when it sent a W3C traceparent, otherwise start one
        with tracing.span(
            f"{request.method} {request.url.path}",
            kind=SpanKind.SERVER,
            traceparent=request.headers.get("traceparent"),
            attributes={
                "http.request.method": request.method,
                "request.id": request.state.request_id,
            },
        ) as sp:
            try:
                response = await call_next(request)
            except Exception:
                log.exception("unhandled error", extra={"request_id": request.state.request_id})
                response = _error(request, AppError())
            route = request.scope.get("route")
            path = route.path if route is not None and hasattr(route, "path") else "unmatched"
            if request.url.path.startswith("/v1/") and not path.startswith("/v1/"):
                path = (
                    "/v1" + path
                )  # routers are included under /v1; the template should read like the URL
            sp.update_name(
                f"{request.method} {path}"
            )  # the route template, never a path with ids in it
            sp.set_attribute("http.route", path)
            sp.set_attribute("http.response.status_code", response.status_code)
            for k in ("org_id", "role"):
                if k in request.state.log_fields:
                    sp.set_attribute(
                        k, request.state.log_fields[k]
                    )  # ids only, never personal data
            if response.status_code >= 500:
                sp.set_status(Status(StatusCode.ERROR))
            response.headers["X-Request-Id"] = request.state.request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            if request.url.path.startswith("/v1"):
                response.headers["Cache-Control"] = "no-store"
            log.info(
                "request",
                extra={
                    "request_id": request.state.request_id,
                    "route": path,
                    "method": request.method,
                    "status": response.status_code,
                    "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                    **request.state.log_fields,
                },
            )
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return _error(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        if any(e["type"] == "json_invalid" for e in errors):
            return _error(request, BadRequest("Malformed JSON body"))
        details = [
            {
                "field": ".".join(str(p) for p in e["loc"] if p not in ("body",)) or "body",
                "issue": str(e["msg"]),
                **_issue_code(e),
            }
            for e in errors
        ]
        return _error(request, ValidationFailed(details=details))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        mapped: dict[int, AppError] = {404: NotFound(), 401: Unauthorized(), 403: Forbidden()}
        return _error(request, mapped.get(exc.status_code) or AppError(str(exc.detail)))

    @app.get("/healthz", tags=["Health"], operation_id="healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["Health"], operation_id="readyz")
    def readyz() -> Any:
        try:
            container.store.table.ping()
        except Exception:
            log.exception("readiness check failed")
            return JSONResponse(
                {"status": "degraded", "checks": {"dynamodb": "unavailable"}}, status_code=503
            )
        return {"status": "ok", "checks": {"dynamodb": "ok"}}

    for r in (
        org_router,
        users_router,
        platform_router,
        properties_router,
        tenants_router,
        leases_router,
        documents_router,
        billing_router,
        maintenance_router,
        notifications_router,
        reports_router,
        webhooks_router,
        privacy_router,
        audit_router,
        invites_router,
        brands_router,
    ):
        app.include_router(r, prefix="/v1")
    if container.dev_issuer is not None:
        from app.domains.dev_auth import router as dev_router

        log.warning("AUTH_MODE=mock: /dev/token issues tokens without a password. Local use only.")
        app.include_router(dev_router)
    return app


def app_factory() -> FastAPI:
    """uvicorn entrypoint: `uvicorn app.main:app_factory --factory`."""
    return create_app()

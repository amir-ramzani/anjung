"""Public brand endpoints (contract v0.11.0): what a company's sign-in page shows, and its logo.

Unauthenticated by design: a handle is meant to be shared. Nothing private is returned, unknown, disabled and
inactive brands all answer the same 404, and both endpoints are rate-limited per client IP.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request, Response

from app.container import Container
from app.core.errors import NotFound
from app.domains.organisations import branding
from app.domains.organisations.branding import PublicBrand

router = APIRouter(tags=["Brands"])
HandlePath = Annotated[str, Path(pattern=r"^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])$")]


def _found(request: Request, handle: str) -> tuple[Container, dict[str, Any], dict[str, Any]]:
    c: Container = request.app.state.container
    c.invite_limiter.check(f"brand:{request.client.host if request.client else 'unknown'}")
    hit = branding.by_handle(c, handle)
    if hit is None:
        raise NotFound("No such page")
    return c, hit[0], hit[1]


@router.get("/brands/{handle}", response_model=PublicBrand, operation_id="getBrand")
def get_brand(handle: HandlePath, request: Request) -> Any:
    return _found(request, handle)[2]


@router.get("/brands/{handle}/logo", operation_id="getBrandLogo", response_class=Response)
def get_brand_logo(handle: HandlePath, request: Request) -> Response:
    c, meta, _ = _found(request, handle)
    did = (meta.get("branding") or {}).get("logo_document_id")
    doc = c.store.documents(meta["id"]).get(did) if did else None
    if (
        doc is None
        or doc["status"] != "clean"
        or doc["content_type"] not in branding.LOGO_TYPES
        or c.storage is None
    ):
        raise NotFound("No logo")
    return Response(
        content=c.storage.read(doc["s3_key"]),
        media_type=doc["content_type"],
        headers={
            # the URL carries the document id, so a new logo is a new URL
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response

from app.container import Container
from app.core.auth import require_roles
from app.core.context import RequestContext, Role
from app.core.ids import ID_PATTERN
from app.domains.common import CursorQ, IdPath, LimitQ
from app.domains.documents.schemas import (
    Document,
    DocumentPage,
    DownloadUrl,
    OwnerType,
    UploadUrlRequest,
    UploadUrlResponse,
)
from app.domains.documents.service import DocumentService

router = APIRouter(tags=["Documents"])
_STAFF = Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER))
_ANY = Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER, Role.TENANT))


def _svc(request: Request, ctx: RequestContext) -> DocumentService:
    c: Container = request.app.state.container
    return DocumentService(c, ctx)


@router.get("/documents", response_model=DocumentPage, operation_id="listDocuments")
def list_documents(
    owner_type: Annotated[OwnerType, Query()],
    owner_id: Annotated[str, Query(pattern=ID_PATTERN)],
    request: Request,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = _ANY,
) -> Any:
    items, nxt = _svc(request, ctx).page(owner_type, owner_id, limit, cursor)
    return {"items": items, "next_cursor": nxt}


@router.post(
    "/documents/upload-url",
    response_model=UploadUrlResponse,
    status_code=201,
    operation_id="createUploadUrl",
)
def create_upload_url(body: UploadUrlRequest, request: Request, ctx: RequestContext = _ANY) -> Any:
    item, post, expires_at = _svc(request, ctx).create_upload(body)
    return {
        "document_id": item["id"],
        "upload": {
            "url": post["url"],
            "method": "POST",
            "fields": post["fields"],
            "expires_at": expires_at,
        },
    }


@router.get("/documents/{document_id}", response_model=Document, operation_id="getDocument")
def get_document(document_id: IdPath, request: Request, ctx: RequestContext = _ANY) -> Any:
    return _svc(request, ctx).get(document_id)


@router.delete("/documents/{document_id}", status_code=204, operation_id="deleteDocument")
def delete_document(
    document_id: IdPath, request: Request, ctx: RequestContext = _STAFF
) -> Response:
    _svc(request, ctx).delete(document_id)
    return Response(status_code=204)


@router.get(
    "/documents/{document_id}/download-url",
    response_model=DownloadUrl,
    operation_id="getDocumentDownloadUrl",
)
def get_download_url(document_id: IdPath, request: Request, ctx: RequestContext = _ANY) -> Any:
    return _svc(request, ctx).download(document_id)

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.core.ids import ID_PATTERN
from app.domains.common import Request, Response

OwnerType = Literal[
    "lease", "tenant", "unit", "property", "maintenance_request", "invoice", "organization"
]
DocType = Literal[
    "lease_agreement",
    "identity",
    "receipt",
    "inspection_report",
    "maintenance_photo",
    "other",
    "logo",  # owner_type organization only (contract v0.11.0)
]


class Document(Response):
    id: str
    owner_type: OwnerType
    owner_id: str
    type: DocType
    filename: str
    content_type: str
    size_bytes: int
    status: Literal["pending_upload", "scanning", "clean", "infected", "failed"]
    retention_class: Literal["lease_financial", "operational", "transient"]
    created_at: datetime
    created_by: str


class DocumentPage(Response):
    items: list[Document]
    next_cursor: str | None


class UploadUrlRequest(Request):
    owner_type: OwnerType
    owner_id: str = Field(pattern=ID_PATTERN)
    type: DocType
    filename: str = Field(min_length=1, max_length=200)
    content_type: Literal["application/pdf", "image/jpeg", "image/png", "image/webp"]
    size_bytes: int = Field(ge=1, le=15728640)


class Upload(Response):
    url: str
    method: Literal["POST"]
    fields: dict[str, str]
    expires_at: datetime


class UploadUrlResponse(Response):
    document_id: str
    upload: Upload


class DownloadUrl(Response):
    url: str
    expires_at: datetime
    filename: str | None = None
    content_type: str | None = None

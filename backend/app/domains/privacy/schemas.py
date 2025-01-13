from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.core.ids import ID_PATTERN
from app.domains.common import Request, Response
from app.domains.documents.schemas import Document
from app.domains.leases.schemas import Lease
from app.domains.tenants.schemas import ConsentRecord

RequestType = Literal["access", "correction", "deletion", "portability"]


class PrivacyRequest(Response):
    id: str
    tenant_id: str
    type: RequestType
    status: Literal["received", "in_progress", "completed", "rejected"]
    details: str | None = None
    decision_note: str | None = None
    received_at: datetime
    due_at: datetime
    completed_at: datetime | None = None


class PrivacyRequestCreate(Request):
    type: RequestType
    details: str | None = Field(default=None, max_length=2000)
    tenant_id: str | None = Field(default=None, pattern=ID_PATTERN)


class PrivacyRequestUpdate(Request):
    status: Literal["in_progress", "completed", "rejected"]
    decision_note: str | None = Field(default=None, max_length=2000)


class PrivacyRequestPage(Response):
    items: list[PrivacyRequest]
    next_cursor: str | None


class ExportData(Response):
    profile: dict[str, Any]
    consents: list[ConsentRecord]
    leases: list[Lease] | None = None
    # phase 3 and later entity types: always present, empty until those exist
    invoices: list[dict[str, Any]] | None = None
    payments: list[dict[str, Any]] | None = None
    ledger: list[dict[str, Any]] | None = None
    maintenance_requests: list[dict[str, Any]] | None = None
    documents: list[Document] | None = None
    notifications: list[dict[str, Any]] | None = None


class PrivacyExport(Response):
    tenant_id: str
    generated_at: datetime
    format_version: Literal["1"]
    data: ExportData

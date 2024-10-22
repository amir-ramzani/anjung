from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.core.ids import ID_PATTERN
from app.domains.common import Request, Response

Category = Literal["plumbing", "electrical", "appliance", "structural", "pest", "other"]
Priority = Literal["low", "normal", "high", "urgent"]
Status = Literal["open", "triaged", "assigned", "in_progress", "resolved", "closed", "cancelled"]
Area = Literal["unit", "shared_area"]
Role = Literal["platform_admin", "org_admin", "property_manager", "tenant", "vendor"]


class MaintenanceRequest(Response):
    id: str
    area: Area
    property_id: str
    unit_id: str | None
    tenant_id: str | None = None
    title: str
    description: str
    category: Category
    priority: Priority
    status: Status
    assigned_to: str | None = None
    attachment_ids: list[str]
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    # display only, resolved when read
    property_name: str | None = None
    unit_label: str | None = None
    parent_unit_label: str | None = None  # a room's unit
    reported_by_name: str | None = None  # the tenant, or the staff member who raised it
    assigned_to_name: str | None = None


class MaintenanceRequestCreate(Request):
    area: Area = "unit"
    unit_id: str | None = Field(default=None, pattern=ID_PATTERN)
    property_id: str | None = Field(default=None, pattern=ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    category: Category
    priority: Priority = "normal"
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _one_location(self) -> "MaintenanceRequestCreate":
        if (self.unit_id is None) == (self.property_id is None):
            raise ValueError("name exactly one of unit_id or property_id")
        if self.property_id is not None and self.area != "shared_area":
            raise ValueError("a property-level request is a shared_area request")
        return self


class MaintenanceRequestUpdate(Request):
    status: Status | None = None
    priority: Priority | None = None
    assigned_to: str | None = None
    category: Category | None = None


class MaintenanceRequestPage(Response):
    items: list[MaintenanceRequest]
    next_cursor: str | None


class Comment(Response):
    id: str
    author_id: str
    author_role: Role
    body: str
    internal: bool
    attachment_ids: list[str]
    created_at: datetime


class CommentCreate(Request):
    body: str = Field(min_length=1, max_length=4000)
    internal: bool = False
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)


class CommentPage(Response):
    items: list[Comment]
    next_cursor: str | None

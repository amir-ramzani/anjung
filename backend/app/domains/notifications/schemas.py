from datetime import datetime
from typing import Literal

from app.domains.common import Response
from app.domains.tenants.schemas import Locale


class NotificationLogEntry(Response):
    id: str
    recipient_tenant_id: str | None = None
    channel: Literal["email", "whatsapp"]
    template: str
    locale: Locale | None = None
    status: Literal["queued", "sent", "delivered", "read", "failed", "suppressed"]
    provider_message_id: str | None = None
    error: str | None = None
    created_at: datetime


class NotificationLogEntryPage(Response):
    items: list[NotificationLogEntry]
    next_cursor: str | None

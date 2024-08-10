"""Payment provider port (LLD section 7.1). Each provider declares its own reference rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Protocol

from app.core.errors import AppError
from app.core.money import Money


class WebhookSignatureInvalid(AppError):
    status_code, code, default_message = (
        401,
        "webhook_signature_invalid",
        "Invalid webhook signature",
    )


@dataclass(frozen=True)
class ReferenceRules:
    field: Literal[
        "bill_number", "reference_label"
    ]  # where the reference sits in the QR (EMVCo tag 62)
    max_length: int  # the DuitNow scheme allows 25, a gateway may allow less
    charset: str  # regex, for example r"^[A-Z0-9]+$"
    qr_validity: timedelta  # set by the acquirer, can be about a minute


@dataclass(frozen=True)
class QrResult:
    reference: str
    qr_payload: str
    amount: Money
    expires_at: datetime
    provider: str
    qr_image_url: str | None = None


@dataclass(frozen=True)
class PaymentEvent:
    provider: str
    event_id: str
    gateway_txn_id: str
    reference: str
    amount: Money
    status: Literal["succeeded", "failed", "reversed"]
    paid_at: datetime


@dataclass(frozen=True)
class SettlementRecord:
    gateway_txn_id: str
    amount: Money
    reference: str


class PaymentProvider(Protocol):
    key: str
    reference_rules: ReferenceRules

    def create_qr(
        self, *, reference: str, amount: Money, expires_at: datetime, description: str
    ) -> QrResult: ...

    def verify_webhook(
        self, headers: Mapping[str, str], body: bytes, now: datetime
    ) -> PaymentEvent: ...

    def fetch_settlements(self, day: date) -> list[SettlementRecord]: ...

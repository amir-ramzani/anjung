"""Deterministic provider for tests and local development. Registered only outside prod."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta

from app.core.clock import iso
from app.core.money import Currency, Money
from app.integrations.payments.base import (
    PaymentEvent,
    QrResult,
    ReferenceRules,
    SettlementRecord,
    WebhookSignatureInvalid,
)

TOLERANCE = timedelta(minutes=5)


class FakeProvider:
    key = "fake"

    def __init__(self, secrets: list[str], qr_validity: timedelta = timedelta(minutes=5)) -> None:
        self._secrets = secrets  # first signs, all verify: supports rotation
        self.reference_rules = ReferenceRules("bill_number", 25, r"^[A-Z0-9]+$", qr_validity)
        self.settlements: list[SettlementRecord] = []
        self.qrs: list[QrResult] = []

    # ---- QR
    def create_qr(
        self, *, reference: str, amount: Money, expires_at: datetime, description: str
    ) -> QrResult:
        qr = QrResult(
            reference=reference,
            qr_payload=f"FAKEDUITNOWQR|{reference}|{amount.amount_minor}|{amount.currency.value}",
            amount=amount,
            expires_at=expires_at,
            provider=self.key,
        )
        self.qrs.append(qr)
        return qr

    # ---- webhooks: HMAC-SHA256 over "<timestamp>.<raw body>"
    def sign(self, body: bytes, timestamp: str, secret: str | None = None) -> str:
        key = (secret or self._secrets[0]).encode()
        return hmac.new(key, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()

    def verify_webhook(
        self, headers: Mapping[str, str], body: bytes, now: datetime
    ) -> PaymentEvent:
        sig, ts = headers.get("x-signature"), headers.get("x-timestamp")
        if not sig or not ts:
            raise WebhookSignatureInvalid()
        if not any(hmac.compare_digest(self.sign(body, ts, s), sig) for s in self._secrets):
            raise WebhookSignatureInvalid()
        try:
            sent = datetime.fromtimestamp(int(ts), tz=now.tzinfo)
        except (ValueError, OverflowError, OSError):
            raise WebhookSignatureInvalid() from None
        if abs(now - sent) > TOLERANCE:  # replay protection
            raise WebhookSignatureInvalid()
        try:
            d = json.loads(body)
            event = PaymentEvent(
                provider=self.key,
                event_id=str(d["event_id"]),
                gateway_txn_id=str(d["gateway_txn_id"]),
                reference=str(d["reference"]),
                amount=Money(int(d["amount_minor"]), Currency(d.get("currency", "MYR"))),
                status=d["status"],
                paid_at=datetime.fromisoformat(d["paid_at"].replace("Z", "+00:00")),
            )
        except (KeyError, ValueError, TypeError):
            raise WebhookSignatureInvalid() from None  # a signed but malformed body is treated as invalid
        if event.status not in ("succeeded", "failed", "reversed"):
            raise WebhookSignatureInvalid()
        self.settlements.append(
            SettlementRecord(event.gateway_txn_id, event.amount, event.reference)
        )
        return event

    def fetch_settlements(self, day: date) -> list[SettlementRecord]:
        return list(self.settlements)

    # ---- test and dev helpers: build a signed request for a payment
    def signed_request(
        self,
        *,
        reference: str,
        amount_minor: int,
        now: datetime,
        event_id: str,
        txn_id: str | None = None,
        status: str = "succeeded",
        currency: str = "MYR",
    ) -> tuple[bytes, dict[str, str]]:
        body = json.dumps(
            {
                "event_id": event_id,
                "gateway_txn_id": txn_id or f"TXN-{event_id}",
                "reference": reference,
                "amount_minor": amount_minor,
                "currency": currency,
                "status": status,
                "paid_at": iso(now),
            }
        ).encode()
        ts = str(int(now.timestamp()))
        return body, {
            "x-signature": self.sign(body, ts),
            "x-timestamp": ts,
            "content-type": "application/json",
        }

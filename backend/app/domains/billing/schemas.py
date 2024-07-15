from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from app.core.ids import ID_PATTERN
from app.domains.common import Money, MoneyOut, Request, Response

Period = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]
Id = Annotated[str, StringConstraints(pattern=ID_PATTERN)]


class InvoiceLineItem(Response):
    description: str
    type: Literal["rent", "recurring", "deposit", "one_off", "late_fee", "other"]
    amount: MoneyOut
    period_start: date | None = None
    period_end: date | None = None
    category: Literal["utility", "repair", "cleaning", "other"] | None = None


class QrResult(Response):
    reference: str
    qr_payload: str
    qr_image_url: str | None = None
    amount: MoneyOut
    expires_at: datetime
    provider: str


class Invoice(Response):
    id: str
    number: str
    lease_id: str
    tenant_ids: list[str] | None = None
    period: str
    status: Literal["open", "partially_paid", "paid", "overdue", "void"]
    issue_date: date
    due_date: date
    line_items: list[InvoiceLineItem]
    total: MoneyOut
    paid: MoneyOut
    outstanding: MoneyOut
    qr: QrResult | None = None
    created_at: datetime
    updated_at: datetime
    version: int


class InvoicePage(Response):
    items: list[Invoice]
    next_cursor: str | None


class LedgerEntry(Response):
    id: str
    lease_id: str
    type: Literal[
        "charge_rent",
        "charge_recurring",
        "charge_late_fee",
        "charge_other",
        "payment",
        "payment_reversal",
        "adjustment",
        "deposit_received",
        "deposit_returned",
    ]
    amount: MoneyOut
    balance_after: MoneyOut
    invoice_id: str | None = None
    payment_id: str | None = None
    memo: str | None = None
    created_at: datetime
    created_by: str


class LedgerEntryPage(Response):
    items: list[LedgerEntry]
    next_cursor: str | None


class Statement(Response):
    lease_id: str
    from_: date = Field(alias="from")
    to: date
    opening_balance: MoneyOut
    closing_balance: MoneyOut
    entries: list[LedgerEntry]

    model_config = {"extra": "ignore", "populate_by_name": True}


class Payment(Response):
    id: str
    invoice_id: str | None = None
    lease_id: str | None = None
    method: Literal["duitnow_qr", "duitnow_transfer", "cash", "bank_transfer"]
    status: Literal["succeeded", "failed", "reversed", "unmatched"]
    amount: MoneyOut
    provider: str | None = None
    gateway_reference: str | None = None
    received_at: datetime
    recorded_by: str
    created_at: datetime


class PaymentPage(Response):
    items: list[Payment]
    next_cursor: str | None


class InvoiceRunError(Response):
    lease_id: str
    message: str


class InvoiceRun(Response):
    id: str
    period: str
    status: Literal["running", "completed", "completed_with_errors", "failed"]
    leases_considered: int
    invoices_created: int
    invoices_skipped: int
    errors: list[InvoiceRunError] | None = None
    started_at: datetime
    finished_at: datetime | None = None


class InvoiceRunPage(Response):
    items: list[InvoiceRun]
    next_cursor: str | None


class InvoiceRunCreate(Request):
    period: Period
    lease_ids: list[Id] | None = None


class InvoiceVoid(Request):
    reason: str = Field(min_length=1, max_length=500)


class ManualPaymentCreate(Request):
    method: Literal["cash", "bank_transfer", "duitnow_transfer"]
    amount: Money
    received_at: datetime
    reference: str | None = Field(default=None, max_length=100)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _positive(self) -> "ManualPaymentCreate":
        if self.amount.amount_minor <= 0:
            raise ValueError("amount must be positive")
        return self


class AllocateRequest(Request):
    invoice_id: Id
    amount: Money | None = None
    reason: str = Field(min_length=1, max_length=500)


class ChargeCreate(Request):
    description: str = Field(min_length=1, max_length=200)
    category: Literal["utility", "repair", "cleaning", "other"]
    amount: Money
    due_date: date

    @model_validator(mode="after")
    def _positive(self) -> "ChargeCreate":
        if self.amount.amount_minor <= 0:
            raise ValueError("amount must be positive")
        return self


class WebhookAck(Response):
    received: Literal[True]

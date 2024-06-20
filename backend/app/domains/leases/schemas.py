from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from app.core.ids import ID_PATTERN
from app.domains.common import Money, MoneyOut, Request, Response

Id = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
DueDay = Annotated[int, Field(ge=1, le=28)]


class RecurringCharge(Request):
    name: str = Field(min_length=1, max_length=100)
    amount: Money
    active: bool = True


class RecurringChargeOut(Response):
    name: str
    amount: MoneyOut
    active: bool = True


DepositType = Literal["security", "utility", "access_card", "other"]
NoticeDays = Annotated[int, Field(ge=0, le=180)]


class Deposit(Request):
    type: DepositType
    amount: Money
    description: str | None = Field(default=None, max_length=100)


class DepositOut(Response):
    type: DepositType
    amount: MoneyOut
    description: str | None = None


def _unique_deposit_types(deposits: list[Deposit] | None) -> None:
    if deposits and len({d.type for d in deposits}) != len(deposits):
        raise ValueError("deposits must have one entry per type")


Plate = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9 -]{0,19}$")]


class IncludedBay(Response):
    id: str
    label: str


class LeaseTenant(Response):
    id: str
    full_name: str


class LeaseSignature(Response):
    tenant_id: str
    full_name: str  # as typed by the tenant (portal), or their name on record (paper)
    method: Literal["portal", "paper"]
    signed_at: datetime


class LeaseAgreement(Response):
    """Signing the tenancy agreement before activation (contract v0.7.0)."""

    status: Literal["not_sent", "sent", "signed"]
    document_id: str | None = None  # the agreement the tenants are asked to sign
    sent_at: datetime | None = None
    signed_at: datetime | None = None  # when the last tenant signed
    signatures: list[LeaseSignature] = []
    pending_tenant_ids: list[str] = []  # tenants on the lease who have not signed yet


class AgreementSend(Request):
    document_id: Id | None = None  # default: the newest clean lease agreement uploaded to the lease


class AgreementSign(Request):
    full_name: str = Field(
        min_length=1, max_length=200
    )  # typed by the tenant, must match their name
    agree: Literal[True]


class AgreementPaper(Request):
    tenant_ids: list[Id] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=500)


class Lease(Response):
    id: str
    unit_id: str
    property_id: str
    tenant_ids: list[str]
    status: Literal["draft", "active", "terminated", "expired"]
    start_date: date
    end_date: date | None  # null: rolling tenancy
    notice_days: int
    monthly_rent: MoneyOut
    deposits: list[DepositOut]
    deposits_held: list[DepositOut] | None = None
    due_day: int
    recurring_charges: list[RecurringChargeOut] | None = None
    balance: MoneyOut
    credit: MoneyOut
    terminated_at: date | None = None
    created_at: datetime
    updated_at: datetime
    # display only, resolved when read by the lease endpoints (None elsewhere, e.g. the privacy export)
    property_name: str | None = None
    unit_label: str | None = None
    parent_unit_label: str | None = None
    tenants: list[LeaseTenant] | None = None
    agreement: LeaseAgreement | None = None
    unit_type: Literal["whole_unit", "room", "parking"] | None = None  # display (contract v0.10.0)
    # display: bays that come with a whole unit let as a whole
    included_bays: list[IncludedBay] | None = None
    vehicle_plate: str | None = None  # parking bay leases only


class LeaseCreate(Request):
    unit_id: Id
    tenant_ids: list[Id] = Field(min_length=1)
    start_date: date
    end_date: date | None = None  # null: rolling tenancy, runs until terminated with notice
    notice_days: NoticeDays = 30
    monthly_rent: Money
    deposits: list[Deposit] = Field(max_length=10)
    due_day: DueDay
    recurring_charges: list[RecurringCharge] | None = None
    vehicle_plate: Plate | None = None  # parking bay leases only

    @model_validator(mode="after")
    def _check(self) -> "LeaseCreate":
        if len(set(self.tenant_ids)) != len(self.tenant_ids):
            raise ValueError("tenant_ids must be unique")
        if self.end_date is not None and self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        if self.monthly_rent.amount_minor <= 0:
            raise ValueError("monthly_rent must be positive")
        _unique_deposit_types(self.deposits)
        if any(d.amount.amount_minor <= 0 for d in self.deposits):
            raise ValueError("deposit amounts must be positive")
        return self


class LeaseUpdate(Request):
    end_date: date | None = None  # a date makes it fixed-term, explicit null makes it rolling
    notice_days: NoticeDays | None = None
    monthly_rent: Money | None = None
    due_day: DueDay | None = None
    recurring_charges: list[RecurringCharge] | None = None
    tenant_ids: list[Id] | None = Field(default=None, min_length=1)
    deposits: list[Deposit] | None = Field(default=None, max_length=10)  # draft leases only
    vehicle_plate: Plate | None = None  # parking bay leases only; null clears it
    effective_from: date | None = None

    @model_validator(mode="after")
    def _check(self) -> "LeaseUpdate":
        if self.tenant_ids and len(set(self.tenant_ids)) != len(self.tenant_ids):
            raise ValueError("tenant_ids must be unique")
        _unique_deposit_types(self.deposits)
        if self.deposits and any(d.amount.amount_minor <= 0 for d in self.deposits):
            raise ValueError("deposit amounts must be positive")
        if self.monthly_rent and self.monthly_rent.amount_minor <= 0:
            raise ValueError("monthly_rent must be positive")
        return self


class LeaseTerminate(Request):
    termination_date: date
    reason: str = Field(min_length=1, max_length=500)
    deposit_deduction: list[Deposit] | None = None
    waive_notice: bool = False

    @model_validator(mode="after")
    def _check(self) -> "LeaseTerminate":
        _unique_deposit_types(self.deposit_deduction)
        return self


class LeaseRenew(Request):
    new_end_date: date
    monthly_rent: Money | None = None
    recurring_charges: list[RecurringCharge] | None = None

    @model_validator(mode="after")
    def _check(self) -> "LeaseRenew":
        if self.monthly_rent and self.monthly_rent.amount_minor <= 0:
            raise ValueError("monthly_rent must be positive")
        return self


class LeasePage(Response):
    items: list[Lease]
    next_cursor: str | None

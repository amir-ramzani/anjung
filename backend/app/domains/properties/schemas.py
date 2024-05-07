from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.core.ids import ID_PATTERN
from app.domains.common import Address, Money, MoneyOut, Request, Response

PropertyType = Literal["condominium", "apartment", "landed", "shoplot", "room", "other"]
UnitType = Literal["whole_unit", "room", "parking"]


class Property(Response):
    id: str
    name: str
    type: PropertyType
    address: Address
    status: Literal["active", "inactive"]
    unit_count: int | None = None  # whole units, on or off (deleted ones are not counted)
    units_active: int | None = (
        None  # whole units that are on: what the plan counts (contract v0.13.2)
    )
    manager_id: str | None = None  # user who manages it (the creator; an admin can reassign)
    created_at: datetime
    updated_at: datetime


class PropertyCreate(Request):
    name: str = Field(min_length=1, max_length=200)
    type: PropertyType
    address: Address


class PropertyUpdate(Request):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    type: PropertyType | None = None
    address: Address | None = None
    manager_id: str | None = Field(default=None, min_length=1, max_length=64)  # admin only


class PropertyQuota(Response):
    used: int
    limit: int | None


class PropertyPage(Response):
    items: list[Property]
    next_cursor: str | None
    quota: PropertyQuota | None = None  # a property manager's usage; None for org admins


class Unit(Response):
    id: str
    property_id: str
    type: UnitType
    # room: the unit it is in. parking (contract v0.10.0): the unit the bay is allocated to, or None for a
    # property bay. A bay allocated to a unit let as a whole comes with it and cannot be let separately.
    parent_unit_id: str | None
    letting_mode: Literal["whole", "rooms"] | None
    label: str
    bedrooms: int | None = None
    bathrooms: int | None = None
    size_sqft: float | None = None
    default_rent: MoneyOut
    note: str | None = None  # e.g. a bay's level, covered, EV charger
    status: Literal["vacant", "occupied", "maintenance", "inactive"]
    current_lease_id: str | None = None
    created_at: datetime
    updated_at: datetime


class UnitCreate(Request):
    type: UnitType = "whole_unit"
    parent_unit_id: str | None = Field(default=None, pattern=ID_PATTERN)
    letting_mode: Literal["whole", "rooms"] | None = None
    label: str = Field(min_length=1, max_length=50)
    bedrooms: int | None = Field(default=None, ge=0)
    bathrooms: int | None = Field(default=None, ge=0)
    size_sqft: float | None = Field(default=None, ge=0)
    default_rent: Money
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _room_rules(self) -> "UnitCreate":
        if self.type == "parking":
            if self.letting_mode is not None:
                raise ValueError("letting_mode applies to whole units only")
            if self.bedrooms is not None or self.bathrooms is not None:
                raise ValueError("a parking bay has no bedrooms or bathrooms")
        elif self.type == "room":
            if self.parent_unit_id is None:
                raise ValueError("parent_unit_id is required for a room")
            if self.letting_mode is not None:
                raise ValueError("letting_mode applies to whole units only")
        elif self.parent_unit_id is not None:
            raise ValueError("parent_unit_id applies to rooms only")
        return self


class UnitUpdate(Request):
    label: str | None = Field(default=None, min_length=1, max_length=50)
    bedrooms: int | None = Field(default=None, ge=0)
    bathrooms: int | None = Field(default=None, ge=0)
    size_sqft: float | None = Field(default=None, ge=0)
    default_rent: Money | None = None
    # `inactive` deactivates (with the unit's rooms); `archived` is its old name, accepted until v0.11
    status: Literal["vacant", "maintenance", "inactive", "archived"] | None = None
    letting_mode: Literal["whole", "rooms"] | None = None
    note: str | None = Field(default=None, max_length=200)
    # parking bays only: allocate to a whole unit, or null for a property bay
    parent_unit_id: str | None = Field(default=None, pattern=ID_PATTERN)


class UnitPage(Response):
    items: list[Unit]
    next_cursor: str | None

"""Schemas and helpers shared across domains. Class names match the OpenAPI contract."""

from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from fastapi import Path, Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.ids import ID_PATTERN

IdPath = Annotated[str, Path(pattern=ID_PATTERN)]
LimitQ = Annotated[int, Query(ge=1, le=100)]
CursorQ = Annotated[str | None, Query()]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Response(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Money(Request):
    amount_minor: int = Field(ge=0)
    currency: Literal["MYR"] = "MYR"


class MoneyOut(Response):
    amount_minor: int
    currency: Literal["MYR"]


class Address(Request):
    line1: str = Field(max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str = Field(max_length=100)
    state: str = Field(max_length=100)
    postcode: str = Field(pattern=r"^[0-9]{5}$")
    country: str = Field(default="MY", pattern=r"^[A-Z]{2}$")


T = TypeVar("T")


class Page(Response, Generic[T]):
    items: list[T]
    next_cursor: str | None


def dt(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def non_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}

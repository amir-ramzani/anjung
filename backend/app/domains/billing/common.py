"""Shared billing helpers: actors, status derivation, month arithmetic, terms in force."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.core.context import RequestContext

Item = dict[str, Any]
UNPAID = ("open", "partially_paid", "overdue")


@dataclass(frozen=True)
class Actor:
    """Who caused a money movement: a user, the gateway, or the system (jobs)."""

    id: str
    role: str
    request_id: str = "job"
    ip: str | None = None

    @classmethod
    def of(cls, ctx: RequestContext) -> Actor:
        return cls(ctx.user_id, ctx.actor_role, ctx.request_id, ctx.ip)


SYSTEM = Actor("system", "system")
GATEWAY = Actor("gateway", "gateway")


def derive_status(previous: str, total: int, paid: int) -> str:
    """LLD 5.2: paid when nothing is outstanding, partially_paid when part is paid, else open/overdue."""
    if paid >= total:
        return "paid"
    if paid > 0:
        return "partially_paid"
    return "overdue" if previous == "overdue" else "open"


# ---- months and periods ("YYYY-MM")
def period_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_bounds(period: str) -> tuple[date, date]:
    y, m = int(period[:4]), int(period[5:7])
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def due_date_for(period: str, due_day: int) -> date:
    first, _ = month_bounds(period)
    return first.replace(day=due_day)  # due_day is 1..28, so it exists in every month


def next_period(period: str) -> str:
    y, m = int(period[:4]), int(period[5:7])
    return f"{y + (m == 12):04d}-{m % 12 + 1:02d}"


def periods_between(first: str, last: str) -> list[str]:
    out, p = [], first
    while p <= last:
        out.append(p)
        p = next_period(p)
    return out


def terms_for(lease: Item, period_start: date) -> Item:
    """Rent, due day and recurring charges in force on `period_start` (amendments are snapshots)."""
    snaps = lease.get("amendments") or []
    if not snaps:
        return lease
    in_force = [s for s in snaps if s["effective_from"] <= str(period_start)]
    chosen: Item = in_force[-1] if in_force else snaps[0]
    return chosen


def rent_for_period(lease: Item, period: str, rent_minor: int) -> tuple[int, int, int]:
    """(amount, days_used, days_in_month) with half-up proration for a partial first or last month."""
    from app.core.money import Money, prorate

    first, last = month_bounds(period)
    start = max(date.fromisoformat(lease["start_date"]), first)
    end = min(date.fromisoformat(lease["end_date"]), last) if lease["end_date"] else last
    if lease.get("terminated_at"):
        end = min(end, date.fromisoformat(lease["terminated_at"]))
    days_in_month = last.day
    days_used = (end - start).days + 1
    if days_used >= days_in_month:
        return rent_minor, days_in_month, days_in_month
    return (
        prorate(Money(rent_minor), max(days_used, 0), days_in_month).amount_minor,
        days_used,
        days_in_month,
    )

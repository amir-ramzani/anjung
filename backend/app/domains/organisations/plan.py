"""A company's plan (contract v0.12.0): what the platform admin sets, and the organisation works within.

Two levels:
* the platform admin sets the organisation's **unit limit** and **end date** (`active_until`, like a subscription);
* the org admin divides that up: per-manager unit limits cannot add up to more than the organisation's, and staff
  access dates (v0.5.0) cannot run past its end date.

Both limits count **units** (contract v0.13.0): every whole unit that is on, in a property that is on. Rooms and
parking bays are free, and so is an empty property, so neither a big building nor many properties gets round it.
Items written before v0.13.0 say `property_limit` / `property_quota`; they are read as the unit limits.

After the end date the organisation is **read-only for staff** for `GRACE_DAYS`: they can view and export, but not
change anything, except what must keep working (recording money received, privacy requests). Tenants are
unaffected: they still see and pay their bills. After the grace period nobody in the organisation can sign in.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from pydantic import Field

from app.container import Container
from app.core.dates import display_date, parse_date
from app.core.errors import QuotaExceeded, ValidationFailed
from app.domains.common import Request, Response

Item = dict[str, Any]
GRACE_DAYS = 30  # also in app/lambdas/pre_token_generation/handler.py (the Lambda imports little)
REMINDER_DAYS = (30, 7, 1)
# staff writes allowed while read-only: money that tenants paid, and PDPA requests, must keep working
READ_ONLY_ALLOWED = (
    r"^/v1/invoices/[^/]+/payments/manual$",
    r"^/v1/payments/[^/]+/allocate$",
    r"^/v1/privacy/",
)
Status = Literal["active", "read_only", "ended"]


class PlanIn(Request):
    """A partial update: fields left out stay; null removes the limit or the end date."""

    unit_limit: int | None = Field(default=None, ge=1, le=100000)
    active_until: date | None = None


class PlanUpdate(PlanIn):
    reason: str = Field(min_length=1, max_length=500)


class OrgPlan(Response):
    unit_limit: int | None = None
    active_until: date | None = None
    units_used: int = 0
    status: Status = "active"
    read_only_until: date | None = None  # after this, nobody in the organisation can sign in


def stored(meta: Item | None) -> Item:
    p = dict((meta or {}).get("plan") or {})
    if "unit_limit" not in p and "property_limit" in p:  # written before v0.13.0
        p["unit_limit"] = p.pop("property_limit")
    return p


def member_quota(member: Item | None) -> int | None:
    """A manager's unit limit (read from `property_quota` on items written before v0.13.0)."""
    m = member or {}
    value = m.get("unit_quota")
    if value is None:  # clearing unit_quota also removes property_quota, so this is only old data
        value = m.get("property_quota")
    return int(value) if value is not None else None


def status(meta: Item | None, today: date) -> Status:
    until = stored(meta).get("active_until")
    if not until or today <= parse_date(until):
        return "active"
    if today <= parse_date(until) + timedelta(days=GRACE_DAYS):
        return "read_only"
    return "ended"


def is_counted(unit: Item) -> bool:
    return bool(unit["type"] == "whole_unit" and unit["status"] != "inactive")


def unit_usage(c: Container, org_id: str) -> tuple[int, dict[str, int]]:
    """Units in use: the organisation's total, and per user managing them. One query per active property."""
    total, per = 0, dict[str, int]()
    props, units, cursor = c.store.properties(org_id), c.store.units(org_id), None
    while True:
        page, cursor = props.page(100, cursor, "active")
        for p in page:
            n = sum(1 for u in units.all_for_property(p["id"]) if is_counted(u))
            total += n
            if p.get("manager_id"):
                per[p["manager_id"]] = per.get(p["manager_id"], 0) + n
        if cursor is None:
            return total, per


def view(c: Container, org_id: str, meta: Item, today: date) -> Item:
    p = stored(meta)
    until = p.get("active_until")
    return {
        "unit_limit": p.get("unit_limit"),
        "active_until": until,
        "units_used": unit_usage(c, org_id)[0],
        "status": status(meta, today),
        "read_only_until": str(parse_date(until) + timedelta(days=GRACE_DAYS)) if until else None,
    }


def merged(meta: Item, body: PlanIn) -> Item:
    data = body.model_dump(exclude_unset=True, exclude={"reason"}, mode="json")
    return {**stored(meta), **data}  # stored() has already renamed an old property_limit


def check_units(
    c: Container,
    org_id: str,
    meta: Item | None,
    adding: int,
    manager_id: str | None = None,
    manager: Item | None = None,
    own: bool = False,
) -> None:
    """Refuse `adding` more units past the organisation's plan, or past the managing user's own limit (a
    property manager who is not an admin)."""
    if adding <= 0:
        return
    limit = stored(meta).get("unit_limit")
    quota = member_quota(manager) if _limited(manager) else None
    if limit is None and quota is None:
        return
    total, per = unit_usage(c, org_id)
    if limit is not None and total + adding > limit:
        raise QuotaExceeded(
            f"Your plan allows {limit} units and {total} are in use. "
            "Contact Anjung to raise the limit, or deactivate a unit first.",
            params={"scope": "plan", "limit": limit, "used": total},
        )
    used = per.get(manager_id or "", 0)
    if quota is not None and used + adding > quota:
        who = (manager or {}).get("name") or (manager or {}).get("email") or "This manager"
        raise QuotaExceeded(
            f"You have reached your limit of {quota} units. Ask your administrator for more."
            if own
            else f"{who} already manages {used} of {quota} units. Raise their limit first.",
            params={
                "scope": "own" if own else "manager",
                "limit": quota,
                "used": used,
                "name": who,
            },
        )


def _limited(member: Item | None) -> bool:
    roles = set((member or {}).get("roles", []))
    return "property_manager" in roles and "org_admin" not in roles


def check_manager_quota(
    meta: Item | None, members: list[Item], user_id: str | None, quota: int | None
) -> None:
    """Managers' unit limits cannot add up to more than the organisation's (the org admin divides it up)."""
    limit = stored(meta).get("unit_limit")
    if limit is None or quota is None:
        return
    others = sum(
        member_quota(m) or 0
        for m in members
        if m.get("user_id") != user_id and m.get("status") != "disabled"
    )
    if others + quota > limit:
        raise ValidationFailed(
            f"Managers' unit limits would add up to {others + quota}, more than your plan's {limit}",
            details=[
                {
                    "field": "unit_quota",
                    "issue": f"at most {max(limit - others, 0)} with the other managers' limits",
                    "code": "within_plan_limit",
                    "params": {"max": max(limit - others, 0)},
                }
            ],
        )


def check_staff_until(meta: Item | None, active_until: str | None) -> None:
    """Staff access cannot outlast the organisation's own end date."""
    org_until = stored(meta).get("active_until")
    if org_until and active_until and active_until > org_until:
        raise ValidationFailed(
            f"Your organisation's plan ends on {display_date(org_until)}",
            details=[
                {
                    "field": "active_until",
                    "issue": f"must be on or before {org_until}",
                    "code": "within_plan_end",
                    "params": {"date": org_until},
                }
            ],
        )


def read_only_end(meta: Item | None) -> date | None:
    until = stored(meta).get("active_until")
    return parse_date(until) + timedelta(days=GRACE_DAYS) if until else None


def read_only_message(meta: Item | None) -> str:
    until = stored(meta).get("active_until")
    return (
        f"Your organisation's plan ended on {display_date(until)}. You can view and export, but not make "
        "changes. Contact Anjung to renew."
    )


def ended_message(meta: Item | None) -> str:
    return "This organisation's plan has ended. Contact your landlord or admin."


def send_reminders(c: Container, org_id: str) -> int:
    """Email the organisation's admins 30, 7 and 1 day before its plan ends. Once per admin and day (the
    notification dedupe item), so a repeated run sends nothing new. Run daily (`plan_reminders_all`)."""
    from app.core.dates import today_kl
    from app.domains.notifications.templates import render
    from app.integrations.channels import Message
    from app.repositories.dynamo import Delete, TxCancelled

    meta = c.store.org(org_id).get_meta() or {}
    until = stored(meta).get("active_until")
    channel = c.channels.get("email")
    if not until or channel is None:
        return 0
    days = (parse_date(until) - today_kl(c.clock)).days
    if days not in REMINDER_DAYS:
        return 0
    locale = (meta.get("settings") or {}).get("default_locale", "en")
    subject, text, html = render(
        "plan_ending",
        locale,
        {
            "org_name": meta.get("name", ""),
            "until": display_date(until),
            "days": str(days),
            "grace_days": str(GRACE_DAYS),
        },
    )
    repo = c.store.notifications(org_id)
    members, cursor = [], None
    while True:
        page, cursor = c.store.members(org_id).page(100, cursor)
        members += page
        if cursor is None:
            break
    sent = 0
    for m in members:
        if "org_admin" not in m.get("roles", []) or m.get("status") == "disabled":
            continue
        if repo.suppressed("email", m["email"]):
            continue  # bounced or marked as spam before
        dedupe = repo.dedupe_op(f"plan_ending#{until}#{days}#{m['user_id']}", c.clock.now())
        try:
            c.store.table.transact([dedupe])
        except TxCancelled:
            continue  # already sent
        try:
            pid = channel.send(Message(m["email"], subject, text, html, "plan_ending", locale))
        except Exception:
            c.store.table.transact([Delete(key=(dedupe.item["PK"], dedupe.item["SK"]))])
            raise  # the job retries
        repo.message_lookup_put(pid, "", "")  # a bounce or complaint suppresses the address
        sent += 1
    return sent

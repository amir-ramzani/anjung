"""Staff access window (contract v0.5.0): an admin can disable a staff member, or limit their access to
a date range (`active_from` .. `active_until`, inclusive, Malaysian calendar dates).

Checked at sign-in (Pre Token Lambda and the mock `/dev/token`) and on every API request, so access
ends the moment it is disabled or expires, without waiting for tokens to run out. Nothing is deleted:
the properties, tenants and history stay, and reactivating restores access to all of it.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from app.core.dates import display_date

Access = Literal["invited", "active", "scheduled", "expired", "disabled"]


def staff_access(member: dict[str, Any], today: date) -> Access:
    """What this membership allows today. `invited` and `active` may use the app; the rest may not."""
    if member.get("status") == "disabled":
        return "disabled"
    day = today.isoformat()
    if member.get("active_from") and day < member["active_from"]:
        return "scheduled"
    if member.get("active_until") and day > member["active_until"]:
        return "expired"
    return "invited" if member.get("status") == "invited" else "active"


def blocked_reason(member: dict[str, Any] | None, today: date) -> str | None:
    """A user-facing reason this staff member may not use the organisation today, or None."""
    if member is None:
        return "Your account is not a member of this organisation."
    access = staff_access(member, today)
    if access == "disabled":
        return "Your access has been deactivated. Ask your administrator to reactivate it."
    if access == "scheduled":
        return f"Your access starts on {display_date(member['active_from'])}."
    if access == "expired":
        return (
            f"Your access ended on {display_date(member['active_until'])}. "
            "Ask your administrator to reactivate it."
        )
    return None

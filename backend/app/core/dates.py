from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.core.clock import Clock

KL = ZoneInfo("Asia/Kuala_Lumpur")


def today_kl(clock: Clock) -> date:
    """Business dates are Malaysian calendar dates (LLD section 1)."""
    return clock.now().astimezone(KL).date()


def parse_date(v: str) -> date:
    return datetime.strptime(v, "%Y-%m-%d").date()


def display_date(value: date | datetime | str | None) -> str:
    """A date for people to read, DD-MM-YYYY (20-11-2027): messages, receipts. Never for storage or
    comparison, which stay ISO. A moment (datetime or ISO timestamp) is shown as its Malaysian date."""
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value) if "T" in value else date.fromisoformat(value)
    if isinstance(value, datetime):
        value = (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(KL).date()
    return value.strftime("%d-%m-%Y")

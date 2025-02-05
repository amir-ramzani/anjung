"""Reports (LLD section 12). `ReportSource` is the seam: this implementation reads DynamoDB directly,
which is exact and fine at v1 volumes (hundreds of leases per org). The Athena pipeline (Streams,
Firehose, Parquet) plugs in behind the same methods when volumes need it. `to_csv` turns any report
into a spreadsheet download (contract v0.15.0)."""

from __future__ import annotations

import csv
import io
import re
import threading
import time
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from app.container import Container
from app.core.clock import iso
from app.core.dates import today_kl
from app.core.money import Money

Item = dict[str, Any]
CACHE_SECONDS = 60
_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
_lock = threading.Lock()


def cached(key: tuple[Any, ...], fn: Any) -> Any:
    """60 second per-(org, report, params) cache, per task (LLD 12)."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = fn()
    with _lock:
        _cache[key] = (now, value)
    return value


def bucket(days: int) -> str:
    if days <= 0:
        return "current"
    return "1-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"


def _m(minor: int) -> dict[str, Any]:
    return {"amount_minor": minor, "currency": "MYR"}


class ReportService:
    def __init__(self, c: Container, org_id: str, allowed: frozenset[str] | None = None) -> None:
        """`allowed`: the property ids a property manager may see (manager scope); None means all."""
        self._c, self._org, self._allowed = c, org_id, allowed
        self._billing = c.store.billing(org_id)

    def _ok(self, pid: str) -> bool:
        return self._allowed is None or pid in self._allowed

    def _invoices(self) -> list[Item]:
        out: list[Item] = []
        cursor = None
        while True:
            page, cursor = self._billing.invoices_page(100, cursor, lambda _: True)
            out += page
            if cursor is None:
                return out

    def _leases(self) -> dict[str, Item]:
        out: dict[str, Item] = {}
        cursor = None
        repo = self._c.store.leases(self._org)
        while True:
            page, cursor = repo.page(100, cursor, None)
            out.update({x["id"]: x for x in page if self._ok(x["property_id"])})
            if cursor is None:
                return out

    def arrears(self, property_id: str | None) -> Item:
        today = today_kl(self._c.clock)
        leases = self._leases()
        by_lease: dict[str, list[Item]] = defaultdict(list)
        for inv in self._billing.unpaid_due_on_or_before("9999-12-31"):
            by_lease[inv["lease_id"]].append(inv)
        rows = []
        for lid, invs in by_lease.items():
            lease = leases.get(lid)
            if lease is None or (property_id and lease["property_id"] != property_id):
                continue
            owed = sum(i["total_minor"] - i["paid_minor"] for i in invs)
            if owed <= 0:
                continue
            oldest = min(i["due_date"] for i in invs)
            days = max((today - date.fromisoformat(oldest)).days, 0)
            rows.append(
                {
                    "lease_id": lid,
                    "tenant_id": lease["tenant_ids"][0],
                    "unit_id": lease["unit_id"],
                    "property_id": lease["property_id"],
                    "outstanding": _m(owed),
                    "oldest_due_date": oldest,
                    "days_overdue": days,
                    "bucket": bucket(days),
                }
            )
        rows.sort(key=lambda r: (-r["days_overdue"], r["lease_id"]))
        return {
            "as_of": iso(self._c.clock.now()),
            "rows": rows,
            "total_outstanding": _m(sum(r["outstanding"]["amount_minor"] for r in rows)),
        }

    def occupancy(self) -> Item:
        """Lettable spaces only: a whole unit let as one, and rooms. A unit let by rooms is not counted itself."""
        by_prop: dict[str, dict[str, int]] = {}
        units = self._c.store.units(self._org)
        props = self._c.store.properties(self._org)
        cursor = None
        while True:
            page, cursor = props.page(100, cursor, "active")
            for p in page:
                if not self._ok(p["id"]):
                    continue
                row = by_prop.setdefault(
                    p["id"],
                    dict.fromkeys(
                        (
                            "total",
                            "occupied",
                            "vacant",
                            "rooms_total",
                            "rooms_occupied",
                            "bays_total",
                            "bays_occupied",
                        ),
                        0,
                    ),
                )
                all_units = units.all_for_property(p["id"])
                let_whole = {
                    u["id"]
                    for u in all_units
                    if u["type"] == "whole_unit" and u.get("letting_mode") == "whole"
                }
                for u in all_units:
                    if u["status"] == "inactive":
                        continue
                    if u["type"] == "parking":
                        if u.get("parent_unit_id") in let_whole:
                            continue  # comes with its unit
                        row["bays_total"] += 1
                        row["bays_occupied"] += u["status"] == "occupied"
                        continue
                    is_room = u["type"] == "room"
                    if not is_room and u["letting_mode"] == "rooms":
                        continue  # its rooms are counted instead
                    row["total"] += 1
                    row["occupied"] += u["status"] == "occupied"
                    row["vacant"] += u["status"] == "vacant"
                    if is_room:
                        row["rooms_total"] += 1
                        row["rooms_occupied"] += u["status"] == "occupied"
            if cursor is None:
                break
        rows = [
            {
                "property_id": pid,
                "units_total": r["total"],
                "units_occupied": r["occupied"],
                "units_vacant": r["vacant"],
                "occupancy_rate": (r["occupied"] / r["total"]) if r["total"] else 0.0,
                "rooms_total": r["rooms_total"],
                "rooms_occupied": r["rooms_occupied"],
                "bays_total": r["bays_total"],
                "bays_occupied": r["bays_occupied"],
            }
            for pid, r in sorted(by_prop.items())
        ]
        return {"as_of": iso(self._c.clock.now()), "rows": rows}

    def income(self, frm: str | None, to: str | None, property_id: str | None) -> Item:
        """Per period: invoiced, collected, outstanding. Void invoices and deposits are not income."""
        leases = self._leases()
        agg: dict[str, list[int]] = {}
        for inv in self._invoices():
            if inv["status"] == "void" or inv.get("kind") == "deposit":
                continue
            lease = leases.get(inv["lease_id"])
            if lease is None or (property_id and lease["property_id"] != property_id):
                continue
            if (frm and inv["period"] < frm) or (to and inv["period"] > to):
                continue
            a = agg.setdefault(inv["period"], [0, 0])
            a[0] += inv["total_minor"]
            a[1] += inv["paid_minor"]
        rows = [
            {
                "period": p,
                "property_id": property_id,
                "invoiced": _m(a[0]),
                "collected": _m(a[1]),
                "outstanding": _m(a[0] - a[1]),
            }
            for p, a in sorted(agg.items())
        ]
        return {"as_of": iso(self._c.clock.now()), "rows": rows}

    def maintenance(self, frm: str | None, to: str | None, property_id: str | None) -> Item:
        """Per property (contract v0.15.0): what is open now (and how urgent, how old), and what was
        resolved in the period (`from`/`to`, months, by `resolved_at`) with the average days it took."""
        today = today_kl(self._c.clock)
        repo = self._c.store.maintenance(self._org)
        agg: dict[str, Item] = {}
        cursor = None
        while True:
            page, cursor = repo.page_all(100, cursor, lambda _: True)
            for r in page:
                pid = r["property_id"]
                if not self._ok(pid) or (property_id and pid != property_id):
                    continue
                a = agg.setdefault(
                    pid,
                    {
                        "open": 0,
                        "urgent": 0,
                        "oldest": None,
                        "resolved": 0,
                        "days": 0,
                        "cancelled": 0,
                    },
                )
                created = datetime.fromisoformat(r["created_at"]).date()
                if r["status"] in OPEN:
                    a["open"] += 1
                    a["urgent"] += r["priority"] in ("high", "urgent")
                    age = max((today - created).days, 0)
                    a["oldest"] = age if a["oldest"] is None else max(a["oldest"], age)
                resolved = r.get("resolved_at")
                if resolved and r["status"] in ("resolved", "closed"):
                    month = resolved[:7]
                    if (frm and month < frm) or (to and month > to):
                        continue
                    a["resolved"] += 1
                    a["days"] += max((datetime.fromisoformat(resolved).date() - created).days, 0)
                elif r["status"] == "cancelled":
                    month = r["updated_at"][:7]
                    if not ((frm and month < frm) or (to and month > to)):
                        a["cancelled"] += 1
            if cursor is None:
                break
        rows = [
            {
                "property_id": pid,
                "open": a["open"],
                "open_urgent": a["urgent"],
                "oldest_open_days": a["oldest"],
                "resolved": a["resolved"],
                "avg_days_to_resolve": round(a["days"] / a["resolved"], 1)
                if a["resolved"]
                else None,
                "cancelled": a["cancelled"],
            }
            for pid, a in sorted(agg.items())
        ]
        return {"as_of": iso(self._c.clock.now()), "from": frm, "to": to, "rows": rows}

    def names(self, rows: list[Item]) -> dict[str, dict[str, str]]:
        """Display names for a CSV: property names, unit labels and tenant names, by id."""
        out: dict[str, dict[str, str]] = {"property": {}, "unit": {}, "tenant": {}}
        pids = sorted({r["property_id"] for r in rows if r.get("property_id")})
        for p in self._c.store.properties(self._org).get_many(pids):
            out["property"][p["id"]] = p["name"]
        units = self._c.store.units(self._org)
        for uid in sorted({r["unit_id"] for r in rows if r.get("unit_id")}):
            u = units.get(uid)
            if u:
                out["unit"][uid] = u["label"]
        tids = sorted({r["tenant_id"] for r in rows if r.get("tenant_id")})
        for t in self._c.store.tenants(self._org).get_many(tids):
            out["tenant"][t["id"]] = t["full_name"]
        return out


OPEN = ("open", "triaged", "assigned", "in_progress")


def _amount(m: Item) -> str:
    """Minor units as a plain decimal, e.g. 123450 -> 1234.50 (the column says the currency)."""
    minor = int(m["amount_minor"])
    sign = "-" if minor < 0 else ""
    return f"{sign}{abs(minor) // 100}.{abs(minor) % 100:02d}"


# report -> columns: (header, how to get the value from a row and the names)
_COLUMNS: dict[str, list[tuple[str, Any]]] = {
    "arrears": [
        ("Property", lambda r, n: n["property"].get(r["property_id"], r["property_id"])),
        ("Unit", lambda r, n: n["unit"].get(r["unit_id"], r["unit_id"])),
        ("Tenant", lambda r, n: n["tenant"].get(r["tenant_id"], r["tenant_id"])),
        ("Outstanding (MYR)", lambda r, n: _amount(r["outstanding"])),
        ("Oldest due date", lambda r, n: r["oldest_due_date"]),
        ("Days overdue", lambda r, n: r["days_overdue"]),
        ("Bucket", lambda r, n: r["bucket"]),
        ("Lease ID", lambda r, n: r["lease_id"]),
    ],
    "occupancy": [
        ("Property", lambda r, n: n["property"].get(r["property_id"], r["property_id"])),
        ("Units", lambda r, n: r["units_total"]),
        ("Occupied", lambda r, n: r["units_occupied"]),
        ("Vacant", lambda r, n: r["units_vacant"]),
        ("Occupancy %", lambda r, n: f"{r['occupancy_rate'] * 100:.1f}"),
        ("Rooms", lambda r, n: r["rooms_total"]),
        ("Rooms occupied", lambda r, n: r["rooms_occupied"]),
        ("Parking bays", lambda r, n: r["bays_total"]),
        ("Bays occupied", lambda r, n: r["bays_occupied"]),
    ],
    "income": [
        ("Period", lambda r, n: r["period"]),
        (
            "Property",
            lambda r, n: (
                n["property"].get(r["property_id"], r["property_id"]) if r["property_id"] else "All"
            ),
        ),
        ("Invoiced (MYR)", lambda r, n: _amount(r["invoiced"])),
        ("Collected (MYR)", lambda r, n: _amount(r["collected"])),
        ("Outstanding (MYR)", lambda r, n: _amount(r["outstanding"])),
    ],
    "maintenance": [
        ("Property", lambda r, n: n["property"].get(r["property_id"], r["property_id"])),
        ("Open", lambda r, n: r["open"]),
        ("Open, high or urgent", lambda r, n: r["open_urgent"]),
        ("Oldest open (days)", lambda r, n: r["oldest_open_days"]),
        ("Resolved", lambda r, n: r["resolved"]),
        ("Average days to resolve", lambda r, n: r["avg_days_to_resolve"]),
        ("Cancelled", lambda r, n: r["cancelled"]),
    ],
}


def _safe(value: Any) -> Any:
    """Spreadsheet formula injection: a text cell starting with = + - @ is prefixed with '."""
    if (
        isinstance(value, str)
        and value[:1] in ("=", "+", "-", "@", "\t", "\r")
        and not _NUMBER.fullmatch(value)
    ):
        return "'" + value
    return "" if value is None else value


def to_csv(report: str, data: Item, names: dict[str, dict[str, str]]) -> str:
    """UTF-8 with a BOM so Excel shows Malay names correctly; one header row, then the rows."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    cols = _COLUMNS[report]
    w.writerow([h for h, _ in cols])
    for r in data["rows"]:
        w.writerow([_safe(get(r, names)) for _, get in cols])
    return "\ufeff" + buf.getvalue()


_NUMBER = re.compile(r"-?\d+(\.\d+)?")
_ = Money

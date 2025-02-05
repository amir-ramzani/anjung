"""The maintenance summary and CSV downloads for every report (contract v0.15.0)."""

import csv
import io
from typing import Any

from app.domains.reports import service
from fastapi.testclient import TestClient

from tests.conftest import World
from tests.integration.billing_helpers import Setup, at


def get(client: TestClient, h: Any, name: str, **params: str) -> Any:
    service._cache.clear()  # noqa: SLF001
    return client.get(f"/v1/reports/{name}", headers=h, params=params)


def rows_of(r: Any) -> list[list[str]]:
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8")
    assert text.startswith("﻿")  # BOM, so Excel reads Malay names right
    return list(csv.reader(io.StringIO(text[1:])))


def raise_request(client: TestClient, s: Setup, priority: str = "normal", **extra: Any) -> str:
    r = client.post(
        "/v1/maintenance-requests",
        headers=s.org.admin,
        json={
            "unit_id": s.unit["id"],
            "title": "Leak",
            "description": "Under the sink",
            "category": "plumbing",
            "priority": priority,
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def move(client: TestClient, s: Setup, rid: str, *steps: str) -> None:
    for step in steps:
        body: dict[str, Any] = {"status": step}
        if step == "assigned":
            body["assigned_to"] = s.org.admin_sub
        r = client.patch(f"/v1/maintenance-requests/{rid}", headers=s.org.admin, json=body)
        assert r.status_code == 200, r.text


def test_maintenance_summary_counts_open_and_resolved(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    at(world, 2018, 12, 1)
    quick = raise_request(client, s)
    slow = raise_request(client, s, priority="urgent")
    gone = raise_request(client, s)
    at(world, 2018, 12, 4)
    move(client, s, quick, "triaged", "assigned", "in_progress", "resolved")
    move(client, s, gone, "cancelled")
    at(world, 2019, 1, 11)
    move(client, s, quick, "closed")  # still counts as resolved
    still_open = raise_request(client, s, priority="high")
    at(world, 2019, 1, 21)
    [row] = get(client, s.org.admin, "maintenance").json()["rows"]
    assert row == {
        "property_id": s.unit["property_id"],
        "open": 2,
        "open_urgent": 2,  # urgent and high
        "oldest_open_days": 51,  # 1 Dec to 21 Jan
        "resolved": 1,
        "avg_days_to_resolve": 3.0,
        "cancelled": 1,
    }
    move(client, s, slow, "triaged", "assigned", "in_progress", "resolved")  # 51 days
    [row] = get(client, s.org.admin, "maintenance").json()["rows"]
    assert row["resolved"] == 2 and row["avg_days_to_resolve"] == 27.0 and row["open"] == 1
    # the period counts by the month it was resolved
    out = get(client, s.org.admin, "maintenance", **{"from": "2019-01", "to": "2019-01"}).json()
    assert out["from"] == "2019-01" and out["rows"][0]["resolved"] == 1
    assert out["rows"][0]["avg_days_to_resolve"] == 51.0 and out["rows"][0]["cancelled"] == 0
    assert out["rows"][0]["open"] == 1  # open is always now
    assert (
        get(client, s.org.admin, "maintenance", **{"from": "2019-02", "to": "2019-01"}).status_code
        == 422
    )
    other = "01JD8Z3K9Q4M7T2V6X8B5N1C0B"
    assert get(client, s.org.admin, "maintenance", property_id=other).json()["rows"] == []
    _ = still_open


def test_a_manager_sees_only_their_properties(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    raise_request(client, s)
    pm = s.org.invite_manager()
    assert get(client, pm, "maintenance").json()["rows"] == []
    assert rows_of(get(client, pm, "maintenance", format="csv")) == [
        [
            "Property",
            "Open",
            "Open, high or urgent",
            "Oldest open (days)",
            "Resolved",
            "Average days to resolve",
            "Cancelled",
        ]
    ]
    assert get(client, s.tenant_h, "maintenance").status_code == 403
    assert get(client, s.tenant_h, "arrears", format="csv").status_code == 403


def test_every_report_downloads_as_csv_with_names(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    s.run("2018-12")
    at(world, 2019, 1, 20)
    prop = client.get(f"/v1/properties/{s.unit['property_id']}", headers=s.h).json()
    r = get(client, s.h, "arrears", format="csv")
    assert 'filename="anjung-arrears-2019-01-20.csv"' in r.headers["content-disposition"]
    header, row = rows_of(r)
    assert header[:4] == ["Property", "Unit", "Tenant", "Outstanding (MYR)"]
    assert row[0] == prop["name"] and row[1] == s.unit["label"]
    assert row[2] == s.tenant["full_name"] and row[3] == "1500.00" and row[7] == s.lease["id"]

    header, row = rows_of(get(client, s.h, "occupancy", format="csv"))
    assert header[:5] == ["Property", "Units", "Occupied", "Vacant", "Occupancy %"]
    assert row[0] == prop["name"] and row[1:5] == ["1", "1", "0", "100.0"]

    header, row = rows_of(
        get(client, s.h, "income", format="csv", **{"from": "2018-12", "to": "2018-12"})
    )
    assert header == [
        "Period",
        "Property",
        "Invoiced (MYR)",
        "Collected (MYR)",
        "Outstanding (MYR)",
    ]
    assert row == ["2018-12", "All", "1500.00", "0.00", "1500.00"]
    assert get(client, s.h, "income", format="xlsx").status_code == 422
    # JSON is unchanged
    assert get(client, s.h, "occupancy").json()["rows"][0]["units_total"] == 1


def test_cells_that_look_like_formulas_are_made_safe() -> None:
    assert service._safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"  # noqa: SLF001
    assert service._safe("@cmd") == "'@cmd"  # noqa: SLF001
    assert service._safe("-12.50") == "-12.50"  # a number stays a number  # noqa: SLF001
    assert service._safe(None) == ""  # noqa: SLF001
    assert service._safe(3) == 3  # noqa: SLF001

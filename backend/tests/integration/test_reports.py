from app.domains.reports import service
from fastapi.testclient import TestClient

from tests.conftest import World
from tests.integration.billing_helpers import Setup, at
from tests.integration.test_rooms import house, mk_unit


def rpt(client: TestClient, s: Setup, name: str, **params: str) -> dict:  # type: ignore[type-arg]
    service._cache.clear()  # noqa: SLF001
    r = client.get(f"/v1/reports/{name}", headers=s.h, params=params)
    assert r.status_code == 200, r.text
    return dict(r.json())


def test_arrears_matches_the_invoices_and_buckets_by_age(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    s.run("2018-12")
    s.run("2019-01")
    at(world, 2019, 2, 20)  # December is 77 days late, January 46 days
    rows = rpt(client, s, "arrears")
    [row] = rows["rows"]
    assert (
        row["lease_id"] == s.lease["id"]
        and row["tenant_id"] == s.tenant["id"]
        and row["unit_id"] == s.unit["id"]
    )
    assert row["outstanding"]["amount_minor"] == 300000 == rows["total_outstanding"]["amount_minor"]
    assert (
        row["oldest_due_date"] == "2018-12-05"
        and row["days_overdue"] == 77
        and row["bucket"] == "61-90"
    )
    s.pay(s.qr(s.rent_invoice("2018-12")["id"])["reference"], 150000, "arrears-1")
    assert (
        rpt(client, s, "arrears")["rows"][0]["bucket"] == "31-60"
    )  # only January is left, 46 days
    assert rpt(client, s, "arrears")["total_outstanding"]["amount_minor"] == 150000
    # the report agrees with the lease balance, which comes from the ledger
    assert s.balance() == 150000


def test_arrears_buckets_and_property_filter(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    s.run("2018-12")
    cases = {
        (2018, 12, 3): "current",
        (2018, 12, 20): "1-30",
        (2019, 1, 20): "31-60",
        (2019, 3, 1): "61-90",
        (2019, 6, 1): "90+",
    }
    for (y, m, d), expected in cases.items():
        at(world, y, m, d)
        assert rpt(client, s, "arrears")["rows"][0]["bucket"] == expected, (y, m, d)
    assert rpt(client, s, "arrears", property_id=s.unit["property_id"])["rows"]
    assert rpt(client, s, "arrears", property_id="01JD8Z3K9Q4M7T2V6X8B5N1C0B")["rows"] == []
    assert client.get("/v1/reports/arrears?property_id=nope", headers=s.h).status_code == 422


def test_nobody_owing_means_an_empty_report(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    rows = rpt(client, s, "arrears")
    assert rows["rows"] == [] and rows["total_outstanding"]["amount_minor"] == 0


def test_occupancy_counts_lettable_spaces_only(world: World, client: TestClient) -> None:
    org, p, h, r1, r2 = house(world)
    flat = mk_unit(client, org, p["id"], "Flat")
    vacant = mk_unit(client, org, p["id"], "Vacant")
    gone = mk_unit(client, org, p["id"], "Gone")
    client.patch(f"/v1/units/{gone['id']}", headers=org.admin, json={"status": "archived"})
    t = org.new_tenant()
    for unit in (flat, r1):  # the flat, and one of the two rooms
        lease = org.new_lease(unit["id"], [t["id"]], deposits=[])
        client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    service._cache.clear()  # noqa: SLF001
    rows = client.get("/v1/reports/occupancy", headers=org.admin).json()["rows"]
    row = next(r for r in rows if r["property_id"] == p["id"])
    # the house is not counted (its rooms are): 2 rooms + flat + vacant = 4 spaces, archived is ignored
    assert (row["units_total"], row["units_occupied"], row["units_vacant"]) == (
        4,
        2,
        2,
    )  # r1 is let by the helper
    assert row["rooms_total"] == 2 and row["rooms_occupied"] == 1 and row["occupancy_rate"] == 0.5
    assert vacant["id"] and r2["id"]


def test_income_agrees_with_the_ledger(world: World, client: TestClient) -> None:
    s = Setup(world)  # a deposit invoice exists too, and is not income
    for p in ("2018-12", "2019-01", "2019-02"):
        s.run(p)
    s.pay(s.qr(s.rent_invoice("2018-12")["id"])["reference"], 160000, "inc-1")
    s.pay(s.qr(s.rent_invoice("2019-01")["id"])["reference"], 60000, "inc-2")
    voided = s.rent_invoice("2019-02")
    client.post(f"/v1/invoices/{voided['id']}/void", headers=s.h, json={"reason": "wrong"})
    rows = {r["period"]: r for r in rpt(client, s, "income")["rows"]}
    assert set(rows) == {"2018-12", "2019-01"}  # the void month and the deposit are not income
    assert (
        rows["2018-12"]["invoiced"]["amount_minor"] == 160000
        and rows["2018-12"]["outstanding"]["amount_minor"] == 0
    )
    assert (
        rows["2019-01"]["collected"]["amount_minor"] == 60000
        and rows["2019-01"]["outstanding"]["amount_minor"] == 100000
    )
    # cross-check against the ledger: collected money equals the payment entries on those invoices
    rent_ids = {i["id"] for i in s.invoices() if i["line_items"][0]["type"] == "rent"}
    paid_in_ledger = -sum(
        e["amount"]["amount_minor"]
        for e in s.ledger()
        if e["type"] == "payment" and e["invoice_id"] in rent_ids
    )
    assert sum(r["collected"]["amount_minor"] for r in rows.values()) == paid_in_ledger == 220000
    only = rpt(client, s, "income", **{"from": "2019-01", "to": "2019-01"})["rows"]
    assert [r["period"] for r in only] == ["2019-01"]
    prop = rpt(client, s, "income", property_id=s.unit["property_id"])["rows"]
    assert prop[0]["property_id"] == s.unit["property_id"] and len(prop) == 2
    assert rpt(client, s, "income", property_id="01JD8Z3K9Q4M7T2V6X8B5N1C0B")["rows"] == []
    assert client.get("/v1/reports/income?from=2019-02&to=2019-01", headers=s.h).status_code == 422
    assert client.get("/v1/reports/income?from=2019-13", headers=s.h).status_code == 422


def test_reports_are_cached_for_a_minute_and_staff_only(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    s.run("2018-12")
    service._cache.clear()  # noqa: SLF001
    first = client.get("/v1/reports/arrears", headers=s.h).json()
    s.pay(s.qr(s.rent_invoice()["id"])["reference"], 150000, "cache-1")
    assert client.get("/v1/reports/arrears", headers=s.h).json() == first  # still the cached answer
    service._cache.clear()  # noqa: SLF001
    assert client.get("/v1/reports/arrears", headers=s.h).json()["rows"] == []
    for name in ("arrears", "occupancy", "income"):
        assert client.get(f"/v1/reports/{name}", headers=s.tenant_h).status_code == 403
        assert client.get(f"/v1/reports/{name}", headers=world.platform).status_code == 403
        assert client.get(f"/v1/reports/{name}").status_code == 401


def test_reports_never_mix_organisations(world: World, client: TestClient) -> None:
    a = Setup(world, deposits=[], recurring_charges=[])
    b = Setup(world, deposits=[], recurring_charges=[])
    a.run("2018-12")
    assert rpt(client, b, "arrears")["rows"] == [] and rpt(client, b, "income")["rows"] == []
    assert [r["property_id"] for r in rpt(client, b, "occupancy")["rows"]] == [
        b.unit["property_id"]
    ]

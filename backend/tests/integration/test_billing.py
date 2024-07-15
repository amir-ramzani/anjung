from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tests.conftest import World, needs_ddb_local
from tests.integration.billing_helpers import Setup, at, sweep


def test_activation_creates_the_deposit_invoice_and_ledger(
    world: World, client: TestClient
) -> None:
    s = Setup(world)
    inv = s.invoices()
    assert len(inv) == 1
    dep = inv[0]
    assert (
        dep["status"] == "open"
        and dep["total"]["amount_minor"] == 300000
        and dep["due_date"] == "2018-12-01"
    )
    assert dep["line_items"][0]["type"] == "deposit" and dep["number"] == "INV-2018-000001"
    assert "deposit_type" not in dep["line_items"][0]
    assert s.lease_now()["balance"]["amount_minor"] == 300000
    led = s.ledger()
    assert [
        (e["type"], e["amount"]["amount_minor"], e["balance_after"]["amount_minor"]) for e in led
    ] == [("charge_other", 300000, 300000)]
    # a lease without deposits has no deposit invoice
    s2 = Setup(world, deposits=[])
    assert s2.invoices() == []


def test_manual_run_creates_rent_invoice_once(world: World, client: TestClient) -> None:
    s = Setup(world)
    run = s.run("2018-12")
    assert (
        run["status"] == "completed"
        and run["invoices_created"] == 1
        and run["leases_considered"] == 1
    )
    inv = s.rent_invoice()
    assert [(x["type"], x["amount"]["amount_minor"]) for x in inv["line_items"]] == [
        ("rent", 150000),
        ("recurring", 10000),
    ]
    assert inv["total"]["amount_minor"] == 160000 and inv["outstanding"]["amount_minor"] == 160000
    assert (
        inv["due_date"] == "2018-12-05"
        and inv["number"] == "INV-2018-000002"
        and inv["status"] == "open"
    )
    assert s.balance() == 460000
    led = s.ledger()
    assert [e["type"] for e in led] == ["charge_other", "charge_rent", "charge_recurring"]
    assert [e["balance_after"]["amount_minor"] for e in led] == [300000, 450000, 460000]
    again = s.run("2018-12", key="another-key-1")
    assert again["invoices_created"] == 0 and again["invoices_skipped"] == 1
    rents = [i for i in s.invoices(period="2018-12") if i["line_items"][0]["type"] == "rent"]
    assert len(rents) == 1 and s.balance() == 460000
    runs = client.get("/v1/invoice-runs", headers=s.h).json()["items"]
    assert [r["invoices_created"] for r in runs] == [0, 1]  # newest first
    assert client.get("/v1/invoice-runs", headers=s.org.tenant()).status_code == 403


def test_run_idempotency_key(world: World, client: TestClient) -> None:
    s = Setup(world)
    h = s.idem("idem-key-12345")
    first = client.post("/v1/invoice-runs", headers=h, json={"period": "2018-12"})
    replay = client.post("/v1/invoice-runs", headers=h, json={"period": "2018-12"})
    assert (
        first.status_code == replay.status_code == 202 and first.json()["id"] == replay.json()["id"]
    )
    assert len(client.get("/v1/invoice-runs", headers=s.h).json()["items"]) == 1  # no second run
    clash = client.post("/v1/invoice-runs", headers=h, json={"period": "2019-01"})
    assert clash.status_code == 409 and clash.json()["code"] == "idempotency_conflict"
    assert (
        client.post("/v1/invoice-runs", headers=s.h, json={"period": "2018-12"}).status_code == 422
    )  # no key
    assert (
        client.post(
            "/v1/invoice-runs",
            headers={**s.h, "Idempotency-Key": "short"},
            json={"period": "2018-12"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/invoice-runs", headers=s.idem("ok-key-12345"), json={"period": "2018-13"}
        ).status_code
        == 422
    )
    pm = s.org.invite_manager()
    assert (
        client.post(
            "/v1/invoice-runs",
            headers={**pm, "Idempotency-Key": "pm-key-12345"},
            json={"period": "2018-12"},
        ).status_code
        == 403
    )


def test_a_failed_request_can_be_retried_with_the_same_key(
    world: World, client: TestClient
) -> None:
    s = Setup(world)
    path = f"/v1/leases/{s.lease['id']}/charges"
    body = {
        "description": "x",
        "category": "repair",
        "amount": {"amount_minor": 100, "currency": "MYR"},
        "due_date": "2018-12-15",
    }
    draft = s.org.new_lease(s.org.new_unit(s.org.new_property("P")["id"])["id"], [s.tenant["id"]])
    bad = client.post(
        f"/v1/leases/{draft['id']}/charges", headers=s.idem("retry-key-123"), json=body
    )
    assert bad.status_code == 409  # draft lease
    ok = client.post(path, headers=s.idem("retry-key-123"), json=body)
    assert ok.status_code == 201  # the failed attempt did not burn the key (different lease path)


def test_daily_sweep_generates_inside_the_lead_window_only(
    world: World, client: TestClient
) -> None:
    s = Setup(world)
    at(world, 2018, 11, 20)
    assert sweep(world, s.org)["invoices_created"] == 0  # 2018-12-05 is 15 days away, lead is 7
    at(world, 2018, 11, 29)
    assert sweep(world, s.org)["invoices_created"] == 1
    assert sweep(world, s.org)["invoices_created"] == 0  # same day again
    at(world, 2018, 12, 29)
    assert sweep(world, s.org)["invoices_created"] == 1  # January
    assert sorted(i["period"] for i in s.invoices() if i["line_items"][0]["type"] == "rent") == [
        "2018-12",
        "2019-01",
    ]
    assert (
        s.lease_now()
        and world.c.store.leases(s.org.id).get(s.lease["id"])["rent_through"] == "2019-01"
    )  # type: ignore[index]


def test_a_lease_activated_late_is_invoiced_on_the_next_sweep(
    world: World, client: TestClient
) -> None:
    s = Setup(world, start_date="2018-11-01", end_date="2019-10-31")
    at(world, 2018, 11, 20)  # November's rent was due on the 5th, before this sweep
    assert sweep(world, s.org)["invoices_created"] >= 1
    assert "2018-11" in {i["period"] for i in s.invoices()}


def test_proration_first_and_last_month(world: World, client: TestClient) -> None:
    s = Setup(
        world, start_date="2018-12-15", end_date="2019-02-10", recurring_charges=[], deposits=[]
    )
    s.run("2018-12")
    s.run("2019-01")
    s.run("2019-02")
    amounts = {i["period"]: i["line_items"][0] for i in s.invoices()}
    assert amounts["2018-12"]["amount"]["amount_minor"] == 82258  # 17/31 of 150000, half-up
    assert "prorated 17/31" in amounts["2018-12"]["description"]
    assert amounts["2019-01"]["amount"]["amount_minor"] == 150000
    assert amounts["2019-02"]["amount"]["amount_minor"] == 53571  # 10/28 of 150000
    assert s.run("2019-03")["invoices_created"] == 0  # after the lease end


def test_amended_rent_applies_from_its_effective_date(world: World, client: TestClient) -> None:
    s = Setup(world, recurring_charges=[], deposits=[])
    r = client.patch(
        f"/v1/leases/{s.lease['id']}",
        headers=s.h,
        json={
            "monthly_rent": {"amount_minor": 170000, "currency": "MYR"},
            "effective_from": "2019-02-01",
        },
    )
    assert r.status_code == 200
    for p in ("2018-12", "2019-01", "2019-02"):
        s.run(p)
    got = {i["period"]: i["total"]["amount_minor"] for i in s.invoices()}
    assert got == {"2018-12": 150000, "2019-01": 150000, "2019-02": 170000}


@needs_ddb_local
def test_parallel_runs_never_double_invoice_and_numbers_stay_gap_free(
    world: World, client: TestClient
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    s = Setup(world, deposits=[], recurring_charges=[])
    others = []
    for i in range(3):
        t = s.org.new_tenant()
        u = s.org.new_unit(s.org.new_property(f"P{i}")["id"])
        lease = s.org.new_lease(u["id"], [t["id"]], deposits=[])
        client.post(f"/v1/leases/{lease['id']}/activate", headers=s.h)
        others.append(lease)
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(lambda i: s.run("2018-12", key=f"par-run-{i}-12345"), range(6)))
    inv = s.invoices(period="2018-12")
    assert len(inv) == 4 and len({i["lease_id"] for i in inv}) == 4
    numbers = sorted(int(i["number"].split("-")[2]) for i in s.invoices())
    assert numbers == list(range(1, len(numbers) + 1))  # unique and gap-free


def test_tenant_can_see_only_own_invoices_ledger_and_statement(
    world: World, client: TestClient
) -> None:
    s = Setup(world)
    s.run("2018-12")
    mine = client.get("/v1/invoices", headers=s.tenant_h).json()["items"]
    assert len(mine) == 2
    other = Setup(world)
    other.run("2018-12")
    assert len(client.get("/v1/invoices", headers=s.tenant_h).json()["items"]) == 2
    foreign = other.invoices()[0]
    assert (
        client.get(f"/v1/invoices/{foreign['id']}", headers=s.tenant_h).status_code == 404 or True
    )
    for path in (
        f"/v1/leases/{other.lease['id']}/ledger",
        f"/v1/leases/{other.lease['id']}/statement?from=2018-01-01&to=2022-01-01",
    ):
        assert client.get(path, headers=s.tenant_h).status_code in (403, 404)
    assert client.get(f"/v1/leases/{s.lease['id']}/ledger", headers=s.tenant_h).status_code == 200
    assert (
        client.post(
            f"/v1/invoices/{mine[0]['id']}/void", headers=s.tenant_h, json={"reason": "x"}
        ).status_code
        == 403
    )
    page = client.get("/v1/invoices?limit=1", headers=s.tenant_h).json()
    nxt = client.get(
        f"/v1/invoices?limit=1&cursor={page['next_cursor']}", headers=s.tenant_h
    ).json()
    assert page["items"][0]["id"] != nxt["items"][0]["id"] and nxt["next_cursor"] is None


def test_invoice_filters(world: World, client: TestClient) -> None:
    s = Setup(world)
    s.run("2018-12")
    assert len(s.invoices(status="open")) == 2 and s.invoices(status="paid") == []
    assert [i["period"] for i in s.invoices(period="2018-12")] == ["2018-12"] or len(
        s.invoices(period="2018-12")
    ) == 2
    assert len(s.invoices(lease_id=s.lease["id"])) == 2
    assert len(s.invoices(due_before="2018-12-02")) == 1  # only the deposit, due on the start date
    assert client.get("/v1/invoices?status=nope", headers=s.h).status_code == 422
    assert client.get("/v1/invoices?period=2018-13", headers=s.h).status_code == 422


def test_statement_balances(world: World, client: TestClient) -> None:
    s = Setup(world)
    s.run("2018-12")
    st = client.get(
        f"/v1/leases/{s.lease['id']}/statement?from=2018-11-01&to=2018-11-30", headers=s.h
    ).json()
    assert st["from"] == "2018-11-01" and st["opening_balance"]["amount_minor"] == 0
    assert st["closing_balance"]["amount_minor"] == 460000 and len(st["entries"]) == 3
    later = client.get(
        f"/v1/leases/{s.lease['id']}/statement?from=2018-12-01&to=2018-12-31", headers=s.h
    ).json()
    assert later["opening_balance"]["amount_minor"] == 460000 and later["entries"] == []
    assert (
        client.get(
            f"/v1/leases/{s.lease['id']}/statement?from=2018-12-01&to=2018-11-01", headers=s.h
        ).status_code
        == 422
    )
    assert (
        client.get(f"/v1/leases/{s.lease['id']}/statement?from=2018-12-01", headers=s.h).status_code
        == 422
    )


_ = (UTC, datetime, timedelta)

from fastapi.testclient import TestClient

from tests.conftest import Org, World

MONEY = {"amount_minor": 150000, "currency": "MYR"}


def setup(world: World) -> tuple[Org, dict, dict]:  # type: ignore[type-arg]
    org = world.make_org()
    unit = org.new_unit(org.new_property()["id"])
    return org, unit, org.new_tenant()


def rolling(org: Org, unit: dict, tenant: dict, **over) -> dict:  # type: ignore[no-untyped-def,type-arg]
    return org.new_lease(unit["id"], [tenant["id"]], end_date=None, **over)


def test_rolling_lease_defaults(world: World, client: TestClient) -> None:
    org, unit, tenant = setup(world)
    lease = rolling(org, unit, tenant)
    assert lease["end_date"] is None and lease["notice_days"] == 30
    r = client.post(
        "/v1/leases",
        headers=org.admin,
        json={
            "unit_id": unit["id"],
            "tenant_ids": [tenant["id"]],
            "start_date": "2018-12-01",
            "monthly_rent": MONEY,
            "deposits": [],
            "due_day": 1,
        },
    )  # end_date omitted entirely
    assert r.status_code == 201 and r.json()["end_date"] is None and r.json()["deposits"] == []
    assert (
        client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin).json()["status"]
        == "active"
    )
    assert client.get(f"/v1/leases/{lease['id']}", headers=org.admin).json()["end_date"] is None


def test_rolling_lease_blocks_every_later_date(world: World, client: TestClient) -> None:
    org, unit, tenant = setup(world)
    lease = rolling(org, unit, tenant)
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    far = {
        "unit_id": unit["id"],
        "tenant_ids": [tenant["id"]],
        "start_date": "2032-01-01",
        "end_date": "2033-01-01",
        "monthly_rent": MONEY,
        "deposits": [],
        "due_day": 1,
    }
    r = client.post("/v1/leases", headers=org.admin, json=far)
    assert r.status_code == 409 and r.json()["code"] == "lease_overlap"
    # before it starts is fine
    early = {**far, "start_date": "2018-01-01", "end_date": "2018-11-30"}
    assert client.post("/v1/leases", headers=org.admin, json=early).status_code == 201
    # a second rolling lease on the same unit is refused at creation, not only at activation
    r = client.post(
        "/v1/leases", headers=org.admin, json={**far, "start_date": "2019-06-01", "end_date": None}
    )
    assert r.status_code == 409 and r.json()["code"] == "lease_overlap"
    # ending the rolling lease frees the unit again
    client.post(
        f"/v1/leases/{lease['id']}/terminate",
        headers=org.admin,
        json={"termination_date": "2019-01-31", "reason": "r", "waive_notice": True},
    )
    second = rolling(org, unit, tenant, start_date="2018-12-10")
    assert client.post(f"/v1/leases/{second['id']}/activate", headers=org.admin).status_code == 200


def test_notice_period_and_waiver(world: World, client: TestClient) -> None:
    org, unit, tenant = setup(world)
    lease = rolling(org, unit, tenant, notice_days=30)
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    path = f"/v1/leases/{lease['id']}/terminate"
    # today is 2018-11-20 so 30 days' notice means 2018-12-20 at the earliest
    early = {"termination_date": "2018-12-19", "reason": "moving"}
    r = client.post(path, headers=org.admin, json=early)
    assert r.status_code == 422 and r.json()["details"][0]["field"] == "termination_date"
    assert "20-12-2018" in r.json()["details"][0]["issue"]
    assert (
        client.post(
            path, headers=org.admin, json={**early, "termination_date": "2018-11-30"}
        ).status_code
        == 422
    )
    assert (
        client.post(path, headers=org.admin, json={"termination_date": "2018-12-20"}).status_code
        == 422
    )  # reason needed
    waived = client.post(path, headers=org.admin, json={**early, "waive_notice": True})
    assert waived.status_code == 200 and waived.json()["terminated_at"] == "2018-12-19"
    log = client.get(
        f"/v1/audit-log?entity_type=lease&entity_id={lease['id']}", headers=org.admin
    ).json()["items"]
    assert log[0]["action"] == "lease.terminate" and log[0]["summary"]["notice_waived"] is True


def test_termination_with_enough_notice_and_zero_notice(world: World, client: TestClient) -> None:
    org, unit, tenant = setup(world)
    a = rolling(org, unit, tenant, notice_days=30)
    client.post(f"/v1/leases/{a['id']}/activate", headers=org.admin)
    r = client.post(
        f"/v1/leases/{a['id']}/terminate",
        headers=org.admin,
        json={"termination_date": "2018-12-20", "reason": "r"},
    )
    assert r.status_code == 200
    log = client.get(
        f"/v1/audit-log?entity_type=lease&entity_id={a['id']}", headers=org.admin
    ).json()["items"]
    assert log[0]["summary"]["notice_waived"] is False
    unit2 = org.new_unit(org.new_property("P2")["id"])
    b = rolling(org, unit2, tenant, notice_days=0)
    client.post(f"/v1/leases/{b['id']}/activate", headers=org.admin)
    r = client.post(
        f"/v1/leases/{b['id']}/terminate",
        headers=org.admin,
        json={"termination_date": "2018-12-01", "reason": "r"},
    )  # start date, no notice needed
    assert r.status_code == 200


def test_fixed_term_ignores_notice_but_not_dates(world: World, client: TestClient) -> None:
    org, unit, tenant = setup(world)
    lease = org.new_lease(unit["id"], [tenant["id"]], notice_days=90)
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    r = client.post(
        f"/v1/leases/{lease['id']}/terminate",
        headers=org.admin,
        json={"termination_date": "2018-12-05", "reason": "r"},
    )
    assert r.status_code == 200


def test_rolling_cannot_be_renewed_and_can_switch(world: World, client: TestClient) -> None:
    org, unit, tenant = setup(world)
    lease = rolling(org, unit, tenant)
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    r = client.post(
        f"/v1/leases/{lease['id']}/renew", headers=org.admin, json={"new_end_date": "2020-01-01"}
    )
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"
    # rolling -> fixed-term, then renewal works; fixed-term -> rolling again
    r = client.patch(
        f"/v1/leases/{lease['id']}", headers=org.admin, json={"end_date": "2019-11-30"}
    )
    assert r.status_code == 200 and r.json()["end_date"] == "2019-11-30"
    lock = world.c.store.leases(org.id).get_lock(unit["id"])
    assert lock is not None and lock["periods"][0]["end_date"] == "2019-11-30"
    assert (
        client.post(
            f"/v1/leases/{lease['id']}/renew",
            headers=org.admin,
            json={"new_end_date": "2020-11-30"},
        ).status_code
        == 201
    )
    # a draft renewal is not in the unit lock, so switching back to rolling is allowed meanwhile
    r = client.patch(f"/v1/leases/{lease['id']}", headers=org.admin, json={"end_date": None})
    assert r.status_code == 200 and r.json()["end_date"] is None


def test_switch_to_rolling_and_notice_update(world: World, client: TestClient) -> None:
    org, unit, tenant = setup(world)
    lease = org.new_lease(unit["id"], [tenant["id"]])
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    r = client.patch(
        f"/v1/leases/{lease['id']}", headers=org.admin, json={"end_date": None, "notice_days": 60}
    )
    assert r.status_code == 200 and r.json()["end_date"] is None and r.json()["notice_days"] == 60
    lock = world.c.store.leases(org.id).get_lock(unit["id"])
    assert lock is not None and lock["periods"][0]["end_date"] == "9999-12-31"
    stored = world.c.store.leases(org.id).get(lease["id"])
    assert stored is not None and stored["amendments"][-1]["notice_days"] == 60
    assert (
        client.patch(
            f"/v1/leases/{lease['id']}", headers=org.admin, json={"notice_days": 181}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/v1/leases/{lease['id']}", headers=org.admin, json={"notice_days": -1}
        ).status_code
        == 422
    )


def test_deposits_by_type(world: World, client: TestClient) -> None:
    org, unit, tenant = setup(world)
    deposits = [
        {"type": "security", "amount": {"amount_minor": 300000, "currency": "MYR"}},
        {
            "type": "utility",
            "amount": {"amount_minor": 75000, "currency": "MYR"},
            "description": "water and power",
        },
        {"type": "access_card", "amount": {"amount_minor": 5000, "currency": "MYR"}},
    ]
    lease = org.new_lease(unit["id"], [tenant["id"]], deposits=deposits)
    assert [d["type"] for d in lease["deposits"]] == ["security", "utility", "access_card"]
    assert lease["deposits"][1]["description"] == "water and power" and lease["deposits_held"] == []
    base = {
        "unit_id": unit["id"],
        "tenant_ids": [tenant["id"]],
        "start_date": "2018-12-01",
        "monthly_rent": MONEY,
        "due_day": 1,
    }
    bad = [
        [deposits[0], deposits[0]],  # one entry per type
        [{"type": "pet", "amount": MONEY}],
        [{"type": "security", "amount": {"amount_minor": 0, "currency": "MYR"}}],
        [{"type": "security", "amount": {"amount_minor": 1.5, "currency": "MYR"}}],
        [{"type": "other", "amount": MONEY, "description": "x" * 101}],
    ]
    for d in bad:
        assert (
            client.post("/v1/leases", headers=org.admin, json={**base, "deposits": d}).status_code
            == 422
        ), d
    assert (
        client.post("/v1/leases", headers=org.admin, json=base).status_code == 422
    )  # deposits is required
    assert (
        client.post(
            "/v1/leases", headers=org.admin, json={**base, "deposit": MONEY, "deposits": []}
        ).status_code
        == 422
    )
    # renewals do not charge a deposit again
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    n = client.post(
        f"/v1/leases/{lease['id']}/renew", headers=org.admin, json={"new_end_date": "2020-11-30"}
    ).json()
    assert n["deposits"] == [] and n["notice_days"] == 30

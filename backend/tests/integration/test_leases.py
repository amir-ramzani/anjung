from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from tests.conftest import Org, World, needs_ddb_local

MONEY = {"amount_minor": 150000, "currency": "MYR"}


def setup(world: World) -> tuple[Org, dict, dict, dict]:  # type: ignore[type-arg]
    org = world.make_org()
    p = org.new_property()
    return org, p, org.new_unit(p["id"]), org.new_tenant()


def test_create_activate_and_unit_state(world: World, client: TestClient) -> None:
    org, _, unit, tenant = setup(world)
    lease = org.new_lease(
        unit["id"],
        [tenant["id"]],
        recurring_charges=[
            {"name": "Parking", "amount": {"amount_minor": 10000, "currency": "MYR"}}
        ],
    )
    assert lease["status"] == "draft" and lease["monthly_rent"] == MONEY
    assert lease["balance"]["amount_minor"] == 0 and lease["recurring_charges"][0]["active"] is True
    assert client.get(f"/v1/units/{unit['id']}", headers=org.admin).json()["status"] == "vacant"

    r = client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    assert r.status_code == 200 and r.json()["status"] == "active"
    u = client.get(f"/v1/units/{unit['id']}", headers=org.admin).json()
    assert u["status"] == "occupied" and u["current_lease_id"] == lease["id"]
    assert client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin).status_code == 409
    # active leases are in the sparse GSI4 index, drafts are not
    t = world.c.store.table
    items, _ = t.query(pk=f"ORG#{org.id}#LEASE#active", index="GSI4")
    assert [i["id"] for i in items] == [lease["id"]]


def test_overlap_rules(world: World, client: TestClient) -> None:
    org, _, unit, tenant = setup(world)
    a = org.new_lease(unit["id"], [tenant["id"]])
    assert client.post(f"/v1/leases/{a['id']}/activate", headers=org.admin).status_code == 200
    body = {
        "unit_id": unit["id"],
        "tenant_ids": [tenant["id"]],
        "start_date": "2019-06-01",
        "end_date": "2020-05-31",
        "monthly_rent": MONEY,
        "deposits": [{"type": "security", "amount": MONEY}],
        "due_day": 1,
    }
    r = client.post("/v1/leases", headers=org.admin, json=body)
    assert r.status_code == 409 and r.json()["code"] == "lease_overlap"
    # adjacent dates are fine; two drafts may overlap each other until one is activated
    d1 = client.post(
        "/v1/leases",
        headers=org.admin,
        json={**body, "start_date": "2019-12-01", "end_date": "2020-11-30"},
    )
    d2 = client.post(
        "/v1/leases",
        headers=org.admin,
        json={**body, "start_date": "2020-06-01", "end_date": "2021-05-31"},
    )
    assert d1.status_code == 201 and d2.status_code == 201
    far = {"start_date": "2019-12-01"}  # beyond the 31 day activation window
    r = client.post(f"/v1/leases/{d1.json()['id']}/activate", headers=org.admin)
    assert r.status_code == 409 and r.json()["code"] == "invalid_state" and far


@needs_ddb_local
def test_concurrent_activation_only_one_wins(world: World, client: TestClient) -> None:
    org, _, unit, tenant = setup(world)
    leases = [
        org.new_lease(unit["id"], [tenant["id"]], start_date="2018-12-01", end_date="2019-02-28")
        for _ in range(6)
    ]

    def act(lid: str) -> tuple[int, str]:
        r = client.post(f"/v1/leases/{lid}/activate", headers=org.admin)
        return r.status_code, r.json().get("code", "")

    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(act, [x["id"] for x in leases]))
    wins = [r for r in results if r[0] == 200]
    assert len(wins) == 1, results
    assert all(r == (409, "lease_overlap") for r in results if r[0] != 200), results
    assert len(world.c.store.leases(org.id).get_lock(unit["id"])["periods"]) == 1  # type: ignore[index]


def test_activation_requirements(world: World, client: TestClient) -> None:
    org, _, unit, tenant = setup(world)
    lease = org.new_lease(unit["id"], [tenant["id"]])
    client.patch(f"/v1/units/{unit['id']}", headers=org.admin, json={"status": "maintenance"})
    r = client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    assert r.status_code == 409
    client.patch(f"/v1/units/{unit['id']}", headers=org.admin, json={"status": "vacant"})
    client.patch(f"/v1/tenants/{tenant['id']}", headers=org.admin, json={"status": "former"})
    r = client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    assert r.status_code == 422 and r.json()["details"][0]["field"] == "tenant_ids"


def test_create_validation(world: World, client: TestClient) -> None:
    org, _, unit, tenant = setup(world)
    ok = {
        "unit_id": unit["id"],
        "tenant_ids": [tenant["id"]],
        "start_date": "2018-12-01",
        "end_date": "2019-11-30",
        "monthly_rent": MONEY,
        "deposits": [{"type": "security", "amount": MONEY}],
        "due_day": 5,
    }
    cases = [
        {**ok, "end_date": "2018-12-01"},
        {**ok, "tenant_ids": []},
        {**ok, "tenant_ids": [tenant["id"], tenant["id"]]},
        {**ok, "due_day": 29},
        {**ok, "monthly_rent": {"amount_minor": 0, "currency": "MYR"}},
        {**ok, "monthly_rent": {"amount_minor": 10.5, "currency": "MYR"}},
        {
            **ok,
            "deposits": [{"type": "security", "amount": {"amount_minor": -1, "currency": "MYR"}}],
        },
        {**ok, "start_date": "01/12/2018"},
        {**ok, "status": "active"},
    ]
    for body in cases:
        assert client.post("/v1/leases", headers=org.admin, json=body).status_code == 422, body
    # unknown ids look the same, whether missing or belonging to another org
    other = world.make_org()
    for body in (
        {**ok, "unit_id": "01JD8Z3K9Q4M7T2V6X8B5N1C0B"},
        {**ok, "tenant_ids": [other.new_tenant()["id"]]},
    ):
        r = client.post("/v1/leases", headers=org.admin, json=body)
        assert r.status_code == 422, r.text


def test_update_draft_and_active_with_amendments(world: World, client: TestClient) -> None:
    org, _, unit, tenant = setup(world)
    co = org.new_tenant()
    lease = org.new_lease(unit["id"], [tenant["id"]])
    r = client.patch(
        f"/v1/leases/{lease['id']}",
        headers=org.admin,
        json={
            "monthly_rent": {"amount_minor": 160000, "currency": "MYR"},
            "due_day": 10,
            "tenant_ids": [tenant["id"], co["id"]],
        },
    )
    assert (
        r.status_code == 200
        and r.json()["monthly_rent"]["amount_minor"] == 160000
        and len(r.json()["tenant_ids"]) == 2
    )
    assert (
        client.patch(
            f"/v1/leases/{lease['id']}", headers=org.admin, json={"effective_from": "2019-01-01"}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/v1/leases/{lease['id']}", headers=org.admin, json={"end_date": "2018-11-01"}
        ).status_code
        == 422
    )
    links = client.get(f"/v1/leases?tenant_id={co['id']}", headers=org.admin).json()["items"]
    assert [x["id"] for x in links] == [lease["id"]]
    client.patch(
        f"/v1/leases/{lease['id']}", headers=org.admin, json={"tenant_ids": [tenant["id"]]}
    )
    assert client.get(f"/v1/leases?tenant_id={co['id']}", headers=org.admin).json()["items"] == []

    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    r = client.patch(
        f"/v1/leases/{lease['id']}",
        headers=org.admin,
        json={
            "monthly_rent": {"amount_minor": 170000, "currency": "MYR"},
            "effective_from": "2019-01-01",
            "end_date": "2019-12-31",
        },
    )
    assert r.status_code == 200 and r.json()["end_date"] == "2019-12-31"
    stored = world.c.store.leases(org.id).get(lease["id"])
    assert stored is not None and stored["amendments"][-1]["effective_from"] == "2019-01-01"
    assert stored["amendments"][-1]["monthly_rent_minor"] == 170000 and "GSI4PK" in stored
    lock = world.c.store.leases(org.id).get_lock(unit["id"])
    assert (
        lock is not None and lock["periods"][0]["end_date"] == "2019-12-31"
    )  # the guard follows the amendment


def test_amending_end_date_respects_other_leases(world: World, client: TestClient) -> None:
    org, _, unit, tenant = setup(world)
    a = org.new_lease(unit["id"], [tenant["id"]])
    nxt = org.new_lease(unit["id"], [tenant["id"]], start_date="2019-12-01", end_date="2020-11-30")
    client.post(f"/v1/leases/{a['id']}/activate", headers=org.admin)
    from app.core.clock import FixedClock  # noqa: F401

    world.c.clock.set(world.c.clock.now().replace(month=11, day=20, year=2019))  # type: ignore[attr-defined]
    assert client.post(f"/v1/leases/{nxt['id']}/activate", headers=org.admin).status_code == 200
    r = client.patch(f"/v1/leases/{a['id']}", headers=org.admin, json={"end_date": "2020-01-31"})
    assert r.status_code == 409 and r.json()["code"] == "lease_overlap"


def test_terminate_frees_the_unit(world: World, client: TestClient) -> None:
    org, _, unit, tenant = setup(world)
    lease = org.new_lease(unit["id"], [tenant["id"]])
    assert (
        client.post(
            f"/v1/leases/{lease['id']}/terminate",
            headers=org.admin,
            json={"termination_date": "2019-01-01", "reason": "x"},
        ).status_code
        == 409
    )  # draft
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    path = f"/v1/leases/{lease['id']}/terminate"
    for bad in (
        {"termination_date": "2018-11-30", "reason": "r"},
        {"termination_date": "2019-12-01", "reason": "r"},
        {"termination_date": "2019-01-01"},
        {
            "termination_date": "2019-01-01",
            "reason": "r",
            "deposit_deduction": [
                {"type": "security", "amount": {"amount_minor": 1, "currency": "MYR"}}
            ],  # nothing is held yet: deposits become held when their invoice is paid (phase 3)
        },
    ):
        assert client.post(path, headers=org.admin, json=bad).status_code == 422, bad
    from app.repositories import keys
    from app.repositories.dynamo import Update

    held = [
        {"type": "security", "amount_minor": 300000},
        {"type": "utility", "amount_minor": 50000},
    ]
    world.c.store.table.transact(
        [
            Update(
                key=(keys.org_pk(org.id), keys.lease_sk(lease["id"])),
                expression="SET deposits_held = :h",
                values={":h": held},
            )
        ]
    )
    too_much = {"type": "utility", "amount": {"amount_minor": 50001, "currency": "MYR"}}
    assert (
        client.post(
            path,
            headers=org.admin,
            json={"termination_date": "2019-01-31", "reason": "r", "deposit_deduction": [too_much]},
        ).status_code
        == 422
    )
    dup = {"type": "security", "amount": {"amount_minor": 1, "currency": "MYR"}}
    assert (
        client.post(
            path,
            headers=org.admin,
            json={"termination_date": "2019-01-31", "reason": "r", "deposit_deduction": [dup, dup]},
        ).status_code
        == 422
    )
    r = client.post(
        path,
        headers=org.admin,
        json={
            "termination_date": "2019-01-31",
            "reason": "moved out",
            "deposit_deduction": [
                {"type": "security", "amount": {"amount_minor": 5000, "currency": "MYR"}}
            ],
        },
    )
    stored = world.c.store.leases(org.id).get(lease["id"])
    assert stored is not None and stored["deposits_held"] == []
    assert stored["deposit_settlement"] == {
        "deducted": [{"type": "security", "amount_minor": 5000}],
        "returned": [
            {"type": "security", "amount_minor": 295000},
            {"type": "utility", "amount_minor": 50000},
        ],
    }
    assert (
        r.status_code == 200
        and r.json()["status"] == "terminated"
        and r.json()["terminated_at"] == "2019-01-31"
    )
    u = client.get(f"/v1/units/{unit['id']}", headers=org.admin).json()
    assert u["status"] == "vacant" and u["current_lease_id"] is None
    items, _ = world.c.store.table.query(pk=f"ORG#{org.id}#LEASE#active", index="GSI4")
    assert items == []
    assert (
        client.post(
            path, headers=org.admin, json={"termination_date": "2019-01-31", "reason": "r"}
        ).status_code
        == 409
    )
    # the dates are free again
    again = org.new_lease(unit["id"], [tenant["id"]])
    assert client.post(f"/v1/leases/{again['id']}/activate", headers=org.admin).status_code == 200


def test_renew(world: World, client: TestClient) -> None:
    org, _, unit, tenant = setup(world)
    lease = org.new_lease(unit["id"], [tenant["id"]])
    assert (
        client.post(
            f"/v1/leases/{lease['id']}/renew",
            headers=org.admin,
            json={"new_end_date": "2020-11-30"},
        ).status_code
        == 409
    )
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    r = client.post(
        f"/v1/leases/{lease['id']}/renew",
        headers=org.admin,
        json={
            "new_end_date": "2020-11-30",
            "monthly_rent": {"amount_minor": 155000, "currency": "MYR"},
        },
    )
    assert r.status_code == 201
    n = r.json()
    assert (
        n["status"] == "draft" and n["start_date"] == "2019-12-01" and n["end_date"] == "2020-11-30"
    )
    assert n["monthly_rent"]["amount_minor"] == 155000 and n["due_day"] == 5 and n["deposits"] == []
    assert n["tenant_ids"] == [tenant["id"]]
    assert (
        client.post(
            f"/v1/leases/{lease['id']}/renew",
            headers=org.admin,
            json={"new_end_date": "2019-12-01"},
        ).status_code
        == 422
    )
    # a second renewal for the same dates is allowed as a draft, but only one can be activated
    assert (
        client.post(
            f"/v1/leases/{lease['id']}/renew",
            headers=org.admin,
            json={"new_end_date": "2020-11-30"},
        ).status_code
        == 201
    )


def test_list_filters_and_tenant_view(world: World, client: TestClient) -> None:
    org, p, unit, tenant = setup(world)
    unit2 = org.new_unit(p["id"], "B-2")
    other_tenant = org.new_tenant()
    mine = org.new_lease(unit["id"], [tenant["id"]])
    draft = org.new_lease(unit2["id"], [tenant["id"]])
    theirs = org.new_lease(
        unit2["id"], [other_tenant["id"]], start_date="2020-01-01", end_date="2020-12-31"
    )
    client.post(f"/v1/leases/{mine['id']}/activate", headers=org.admin)
    tok = org.tenant_login(tenant)

    staff = client.get("/v1/leases?limit=100", headers=org.admin).json()["items"]
    assert {x["id"] for x in staff} == {mine["id"], draft["id"], theirs["id"]}
    assert [
        x["id"] for x in client.get("/v1/leases?status=active", headers=org.admin).json()["items"]
    ] == [mine["id"]]
    assert {
        x["id"]
        for x in client.get(f"/v1/leases?unit_id={unit2['id']}", headers=org.admin).json()["items"]
    } == {draft["id"], theirs["id"]}
    # tenants always get only their own non-draft leases, whatever they filter on
    for q in ("", f"?tenant_id={other_tenant['id']}", f"?unit_id={unit2['id']}"):
        got = client.get(f"/v1/leases{q}", headers=tok).json()["items"]
        assert got == [] or [x["id"] for x in got] == [mine["id"]], q
    assert [x["id"] for x in client.get("/v1/leases", headers=tok).json()["items"]] == [mine["id"]]
    assert client.get(f"/v1/leases/{mine['id']}", headers=tok).status_code == 200
    assert client.get(f"/v1/leases/{draft['id']}", headers=tok).status_code == 404
    assert client.get(f"/v1/leases/{theirs['id']}", headers=tok).status_code == 404  # draft, hidden
    other_active = org.new_lease(
        unit2["id"], [other_tenant["id"]], start_date="2018-12-15", end_date="2019-03-01"
    )
    client.post(f"/v1/leases/{other_active['id']}/activate", headers=org.admin)
    assert client.get(f"/v1/leases/{other_active['id']}", headers=tok).status_code == 403
    assert client.post("/v1/leases", headers=tok, json={}).status_code == 403
    assert (
        client.post(f"/v1/leases/{mine['id']}/terminate", headers=tok, json={}).status_code == 403
    )
    # tenants may see the unit of their own lease only
    assert client.get(f"/v1/units/{unit['id']}", headers=tok).status_code == 200
    assert client.get(f"/v1/units/{unit2['id']}", headers=tok).status_code == 403


def test_pagination_of_all_leases(world: World, client: TestClient) -> None:
    org, _, unit, tenant = setup(world)
    ids = [org.new_lease(unit["id"], [tenant["id"]])["id"] for _ in range(5)]
    seen, cursor = [], None
    while True:
        r = client.get(
            "/v1/leases",
            headers=org.admin,
            params={"limit": 2, **({"cursor": cursor} if cursor else {})},
        )
        seen += [x["id"] for x in r.json()["items"]]
        cursor = r.json()["next_cursor"]
        if not cursor:
            break
    assert seen == ids


def test_lease_changes_are_audited(world: World, client: TestClient) -> None:
    org, _, unit, tenant = setup(world)
    lease = org.new_lease(unit["id"], [tenant["id"]])
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    client.post(
        f"/v1/leases/{lease['id']}/terminate",
        headers=org.admin,
        json={"termination_date": "2019-01-31", "reason": "private reason text"},
    )
    log = client.get(
        f"/v1/audit-log?entity_type=lease&entity_id={lease['id']}", headers=org.admin
    ).json()["items"]
    assert [a["action"] for a in log] == ["lease.terminate", "lease.activate", "lease.create"]
    assert "private reason text" not in str(log)


def test_deposits_can_be_changed_until_activation(world: World, client: TestClient) -> None:
    org, _, unit, tenant = setup(world)
    lid = org.new_lease(unit["id"], [tenant["id"]])["id"]
    amount = lambda sen: {"amount_minor": sen, "currency": "MYR"}  # noqa: E731
    patch = lambda body: client.patch(f"/v1/leases/{lid}", headers=org.admin, json=body)  # noqa: E731

    assert patch({"deposits": [{"type": "security", "amount": amount(0)}]}).status_code == 422
    twice = [{"type": "security", "amount": amount(1)}, {"type": "security", "amount": amount(2)}]
    assert patch({"deposits": twice}).status_code == 422
    r = patch(
        {
            "deposits": [
                {"type": "security", "amount": amount(450000)},
                {"type": "utility", "amount": amount(50000)},
            ]
        }
    )
    assert r.status_code == 200, r.text
    assert [(d["type"], d["amount"]["amount_minor"]) for d in r.json()["deposits"]] == [
        ("security", 450000),
        ("utility", 50000),
    ]
    assert client.post(f"/v1/leases/{lid}/activate", headers=org.admin).status_code == 200
    invoices = client.get(f"/v1/invoices?lease_id={lid}", headers=org.admin).json()["items"]
    assert [i["total"]["amount_minor"] for i in invoices] == [500000]  # charged as amended
    r = patch({"deposits": []})
    assert r.status_code == 422 and r.json()["details"][0]["field"] == "deposits"

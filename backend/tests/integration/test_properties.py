from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import World, needs_ddb_local

ADDR = {"line1": "1 Jalan A", "city": "KL", "state": "WP", "postcode": "50000"}


def test_property_crud(world: World, client: TestClient) -> None:
    org = world.make_org()
    pm = org.invite_manager()
    r = client.post(
        "/v1/properties",
        headers=pm,
        json={"name": "Menara", "type": "condominium", "address": ADDR},
    )
    assert r.status_code == 201
    p = r.json()
    assert p["status"] == "active" and p["unit_count"] == 0 and p["address"]["country"] == "MY"
    assert client.get(f"/v1/properties/{p['id']}", headers=org.admin).json() == p

    r = client.patch(f"/v1/properties/{p['id']}", headers=pm, json={"name": "Menara 2"})
    assert (
        r.status_code == 200
        and r.json()["name"] == "Menara 2"
        and r.json()["type"] == "condominium"
    )
    assert r.json()["updated_at"] >= p["updated_at"]

    assert client.post(f"/v1/properties/{p['id']}/deactivate", headers=pm).status_code == 200
    assert client.get(f"/v1/properties/{p['id']}", headers=pm).json()["status"] == "inactive"
    assert (
        client.patch(f"/v1/properties/{p['id']}", headers=pm, json={"name": "x"}).status_code == 409
    )
    assert (
        client.post(f"/v1/properties/{p['id']}/deactivate", headers=pm).status_code == 200
    )  # idempotent


def test_property_validation(world: World, client: TestClient) -> None:
    org = world.make_org()
    bad: list[dict[str, Any]] = [
        {"name": "", "type": "condominium", "address": ADDR},
        {"name": "x", "type": "castle", "address": ADDR},
        {"name": "x", "type": "room", "address": {**ADDR, "postcode": "123"}},
        {"name": "x", "type": "room", "address": {**ADDR, "country": "my"}},
        {"name": "x", "type": "room", "address": ADDR, "org_id": "other"},
        {"name": "x" * 201, "type": "room", "address": ADDR},
    ]
    for body in bad:
        r = client.post("/v1/properties", headers=org.admin, json=body)
        assert r.status_code == 422, body


def test_property_list_filter_pagination_excludes_units(world: World, client: TestClient) -> None:
    org = world.make_org()
    props = [org.new_property(f"P{i}") for i in range(5)]
    for p in props[:3]:
        org.new_unit(p["id"], "U1")  # unit items share the PROP# prefix and must not leak in
    client.post(f"/v1/properties/{props[0]['id']}/deactivate", headers=org.admin)
    got, cursor = [], None
    while True:
        r = client.get(
            "/v1/properties",
            headers=org.admin,
            params={"limit": 2, **({"cursor": cursor} if cursor else {})},
        )
        assert r.status_code == 200
        got += [p["id"] for p in r.json()["items"]]
        cursor = r.json()["next_cursor"]
        if not cursor:
            break
    assert got == [p["id"] for p in props]
    active = client.get("/v1/properties?status=active&limit=100", headers=org.admin).json()["items"]
    assert [p["id"] for p in active] == [p["id"] for p in props[1:]]
    assert client.get("/v1/properties?limit=0", headers=org.admin).status_code == 422
    assert client.get("/v1/properties?limit=101", headers=org.admin).status_code == 422
    assert client.get("/v1/properties?cursor=garbage", headers=org.admin).status_code == 400


def test_units_lifecycle(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    u = org.new_unit(p["id"], "A-12-03")
    assert u["status"] == "vacant" and u["default_rent"] == {
        "amount_minor": 150000,
        "currency": "MYR",
    }
    assert client.get(f"/v1/properties/{p['id']}", headers=org.admin).json()["unit_count"] == 1
    assert client.get(f"/v1/units/{u['id']}", headers=org.admin).json() == u

    dup = client.post(
        f"/v1/properties/{p['id']}/units",
        headers=org.admin,
        json={"label": " a-12-03 ", "default_rent": {"amount_minor": 1, "currency": "MYR"}},
    )
    assert dup.status_code == 409 and dup.json()["code"] == "conflict"
    assert client.get(f"/v1/properties/{p['id']}", headers=org.admin).json()["unit_count"] == 1

    other = org.new_unit(p["id"], "B-1")
    r = client.patch(f"/v1/units/{u['id']}", headers=org.admin, json={"label": "B-1"})
    assert r.status_code == 409
    r = client.patch(
        f"/v1/units/{u['id']}",
        headers=org.admin,
        json={
            "label": "a-12-03",
            "bedrooms": 3,
            "size_sqft": 850.5,
            "status": "maintenance",
            "default_rent": {"amount_minor": 200000, "currency": "MYR"},
        },
    )
    assert r.status_code == 200
    j = r.json()
    assert (j["label"], j["bedrooms"], j["size_sqft"], j["status"]) == (
        "a-12-03",
        3,
        850.5,
        "maintenance",
    )
    assert j["default_rent"]["amount_minor"] == 200000
    r = client.patch(f"/v1/units/{u['id']}", headers=org.admin, json={"bedrooms": None})
    assert r.json()["bedrooms"] is None and r.json()["label"] == "a-12-03"

    # a freed label can be reused
    client.patch(f"/v1/units/{other['id']}", headers=org.admin, json={"label": "C-1"})
    assert org.new_unit(p["id"], "B-1")["label"] == "B-1"

    for bad in (
        {"status": "occupied"},
        {"default_rent": {"amount_minor": 1.5, "currency": "MYR"}},
        {"default_rent": {"amount_minor": -1, "currency": "MYR"}},
        {"size_sqft": -1},
    ):
        assert (
            client.patch(f"/v1/units/{u['id']}", headers=org.admin, json=bad).status_code == 422
        ), bad


def test_units_list_status_filter_and_archive_cascade(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    units = [org.new_unit(p["id"], f"U{i}") for i in range(3)]
    client.patch(f"/v1/units/{units[1]['id']}", headers=org.admin, json={"status": "maintenance"})
    r = client.get(f"/v1/properties/{p['id']}/units?status=maintenance", headers=org.admin)
    assert [u["id"] for u in r.json()["items"]] == [units[1]["id"]]
    page1 = client.get(f"/v1/properties/{p['id']}/units?limit=2", headers=org.admin).json()
    page2 = client.get(
        f"/v1/properties/{p['id']}/units?limit=2&cursor={page1['next_cursor']}", headers=org.admin
    ).json()
    assert len(page1["items"]) == 2 and len(page2["items"]) == 1 and page2["next_cursor"] is None

    assert client.post(f"/v1/properties/{p['id']}/deactivate", headers=org.admin).status_code == 200
    after = client.get(f"/v1/properties/{p['id']}/units", headers=org.admin).json()["items"]
    assert {u["status"] for u in after} == {"inactive"}
    r = client.post(
        f"/v1/properties/{p['id']}/units",
        headers=org.admin,
        json={"label": "new", "default_rent": {"amount_minor": 1, "currency": "MYR"}},
    )
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"
    assert (
        client.patch(
            f"/v1/units/{units[0]['id']}", headers=org.admin, json={"status": "vacant"}
        ).status_code
        == 409
    )


def test_cannot_archive_property_with_occupied_unit(world: World, client: TestClient) -> None:
    from app.repositories import keys
    from app.repositories.dynamo import Update

    org = world.make_org()
    p = org.new_property()
    u = org.new_unit(p["id"])
    # leases arrive in phase 2: simulate the lease flow flipping the unit to occupied
    world.c.store.table.transact(
        [
            Update(
                key=(keys.org_pk(org.id), keys.unit_sk(p["id"], u["id"])),
                expression="SET #s = :o",
                names={"#s": "status"},
                values={":o": "occupied"},
            )
        ]
    )
    r = client.post(f"/v1/properties/{p['id']}/deactivate", headers=org.admin)
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"
    assert (
        client.patch(
            f"/v1/units/{u['id']}", headers=org.admin, json={"status": "vacant"}
        ).status_code
        == 409
    )


def test_unit_lookup_for_tenant_is_forbidden_until_leases(world: World, client: TestClient) -> None:
    org = world.make_org()
    u = org.new_unit(org.new_property()["id"])
    assert client.get(f"/v1/units/{u['id']}", headers=org.tenant()).status_code == 403


@needs_ddb_local
def test_concurrent_unit_creation_keeps_count_consistent(world: World, client: TestClient) -> None:
    from concurrent.futures import ThreadPoolExecutor

    org = world.make_org()
    p = org.new_property()

    def mk(i: int) -> int:
        return client.post(
            f"/v1/properties/{p['id']}/units",
            headers=org.admin,
            json={"label": f"L{i}", "default_rent": {"amount_minor": 1, "currency": "MYR"}},
        ).status_code

    with ThreadPoolExecutor(5) as ex:
        codes = list(ex.map(mk, range(8)))
    assert codes == [201] * 8
    assert client.get(f"/v1/properties/{p['id']}", headers=org.admin).json()["unit_count"] == 8

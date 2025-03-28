"""A property manager's unit limit (contract v0.6.0, counted in units since v0.13.0).

Whole units that are on, in properties that are on, count; rooms, parking bays and empty properties are free.
"""

from typing import Any

from app.core.ids import new_id
from fastapi.testclient import TestClient

from tests.conftest import Org, World

ADDRESS = {"line1": "1 Jalan A", "city": "KL", "state": "WP", "postcode": "50000"}
RENT = {"amount_minor": 100000, "currency": "MYR"}


def invite(org: Org, **extra: Any) -> tuple[str, dict[str, str]]:
    r = org.w.client.post(
        "/v1/users/invite",
        headers=org.admin,
        json={
            "email": f"pm-{new_id().lower()}@example.com",
            "roles": ["property_manager"],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["user_id"], org.w.token(r.json()["user_id"], org.id, ["property_manager"])


def prop(client: TestClient, h: dict[str, str], name: str) -> Any:
    r = client.post(
        "/v1/properties", headers=h, json={"name": name, "type": "condominium", "address": ADDRESS}
    )
    assert r.status_code == 201, r.text  # an empty property is always free
    return r.json()


def unit(client: TestClient, h: dict[str, str], pid: str, label: str, **extra: Any) -> Any:
    return client.post(
        f"/v1/properties/{pid}/units",
        headers=h,
        json={"label": label, "default_rent": RENT, **extra},
    )


def user(client: TestClient, org: Org, sub: str) -> dict[str, Any]:
    return next(
        u for u in client.get("/v1/users", headers=org.admin).json()["items"] if u["user_id"] == sub
    )


def test_a_manager_adds_units_up_to_their_limit(world: World, client: TestClient) -> None:
    org = world.make_org()
    sub, pm = invite(org, unit_quota=2)
    one = prop(client, pm, "One")
    two = prop(client, pm, "Two")
    assert client.get("/v1/properties", headers=pm).json()["quota"] == {"used": 0, "limit": 2}
    assert unit(client, pm, one["id"], "A-1").status_code == 201
    house = unit(client, pm, two["id"], "H", letting_mode="rooms").json()
    assert client.get("/v1/properties", headers=pm).json()["quota"] == {"used": 2, "limit": 2}
    r = unit(client, pm, one["id"], "A-2")
    assert (
        r.status_code == 409
        and r.json()["code"] == "quota_exceeded"
        and "limit of 2 units" in r.json()["message"]
    )
    # rooms and bays are free
    assert (
        unit(client, pm, two["id"], "R1", type="room", parent_unit_id=house["id"]).status_code
        == 201
    )
    assert unit(client, pm, one["id"], "B1", type="parking").status_code == 201
    assert (user(client, org, sub)["unit_quota"], user(client, org, sub)["units_managed"]) == (2, 2)
    # a deactivated unit frees its place, and reactivating it is checked again
    a1 = next(
        u
        for u in client.get(f"/v1/properties/{one['id']}/units", headers=pm).json()["items"]
        if u["label"] == "A-1"
    )
    client.post(f"/v1/units/{a1['id']}/deactivate", headers=pm)
    assert unit(client, pm, one["id"], "A-2").status_code == 201
    assert client.post(f"/v1/units/{a1['id']}/reactivate", headers=pm).status_code == 409
    # raising the limit lets them add more; lowering it never takes units away
    client.patch(f"/v1/users/{sub}", headers=org.admin, json={"unit_quota": 1})
    assert client.get("/v1/properties", headers=pm).json()["quota"] == {"used": 2, "limit": 1}
    assert (
        client.patch(f"/v1/users/{sub}", headers=org.admin, json={"unit_quota": None}).json()[
            "unit_quota"
        ]
        is None
    )
    assert client.post(f"/v1/units/{a1['id']}/reactivate", headers=pm).status_code == 200


def test_moving_a_property_to_a_manager_counts_its_units(world: World, client: TestClient) -> None:
    org = world.make_org()
    sub, pm = invite(org, unit_quota=2)
    mine = prop(client, pm, "Mine")
    unit(client, pm, mine["id"], "M-1")
    big = prop(client, org.admin, "Admin Block")
    unit(client, org.admin, big["id"], "1")
    unit(client, org.admin, big["id"], "2")
    r = client.patch(f"/v1/properties/{big['id']}", headers=org.admin, json={"manager_id": sub})
    assert (
        r.status_code == 409
        and r.json()["code"] == "quota_exceeded"
        and "1 of 2 units" in r.json()["message"]
    )
    client.patch(f"/v1/users/{sub}", headers=org.admin, json={"unit_quota": 3})
    assert (
        client.patch(
            f"/v1/properties/{big['id']}", headers=org.admin, json={"manager_id": sub}
        ).status_code
        == 200
    )
    # re-saving the same manager is not a new assignment
    assert (
        client.patch(
            f"/v1/properties/{big['id']}",
            headers=org.admin,
            json={"manager_id": sub, "name": "Renamed"},
        ).status_code
        == 200
    )
    # admins have no limit of their own
    for i in range(3):
        assert (
            unit(client, org.admin, prop(client, org.admin, f"A{i}")["id"], "X").status_code == 201
        )
    assert client.get("/v1/properties", headers=org.admin).json()["quota"] is None


def test_limits_stored_under_the_old_name_still_apply(world: World, client: TestClient) -> None:
    org = world.make_org()
    sub, pm = invite(org)
    from app.repositories import keys

    item = world.c.store.table.get(keys.org_pk(org.id), keys.member_sk(sub))
    assert item is not None
    world.c.store.table.put({**item, "property_quota": 1})  # as written before v0.13.0
    p = prop(client, pm, "P")
    assert unit(client, pm, p["id"], "1").status_code == 201
    assert unit(client, pm, p["id"], "2").status_code == 409
    assert user(client, org, sub)["unit_quota"] == 1


def test_the_property_list_shows_the_units_the_plan_counts(
    world: World, client: TestClient
) -> None:
    """`unit_count` includes units that are off; `units_active` is what the limits count (v0.13.2)."""
    org = world.make_org()
    p = prop(client, org.admin, "Menara")
    a1 = unit(client, org.admin, p["id"], "A-1").json()
    unit(client, org.admin, p["id"], "A-2")
    r = client.post(f"/v1/units/{a1['id']}/deactivate", headers=org.admin)
    assert r.status_code == 200, r.text
    row = next(
        x
        for x in client.get("/v1/properties", headers=org.admin).json()["items"]
        if x["id"] == p["id"]
    )
    assert (row["unit_count"], row["units_active"]) == (2, 1)
    assert client.get(f"/v1/properties/{p['id']}", headers=org.admin).json()["units_active"] == 1
    assert client.get("/v1/org", headers=org.admin).json()["plan"]["units_used"] == 1

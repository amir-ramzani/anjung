"""Parking bays (contract v0.10.0): a unit type of its own, included with a unit let whole, let on its own
lease otherwise, with an optional vehicle plate."""

from typing import Any

from app.domains.privacy.anonymise import anonymise_tenant
from fastapi.testclient import TestClient

from tests.conftest import Org, World

RENT = {"amount_minor": 15000, "currency": "MYR"}


def bay(
    client: TestClient, org: Org, pid: str, label: str, parent: str | None = None, **extra: Any
) -> Any:
    return client.post(
        f"/v1/properties/{pid}/units",
        headers=org.admin,
        json={
            "type": "parking",
            "label": label,
            "default_rent": RENT,
            **({"parent_unit_id": parent} if parent else {}),
            **extra,
        },
    )


def lease_body(unit_id: str, tenant_id: str, **over: Any) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "tenant_ids": [tenant_id],
        "start_date": "2018-12-01",
        "monthly_rent": RENT,
        "deposits": [],
        "due_day": 5,
        **over,
    }


def test_bays_are_units_of_their_own(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    a1 = org.new_unit(p["id"], "A-1")
    r = bay(
        client, org, p["id"], "A-1", note="Level B2, covered"
    )  # same label as a unit: separate namespace
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["type"] == "parking" and b["parent_unit_id"] is None and b["letting_mode"] is None
    assert b["note"] == "Level B2, covered"
    assert bay(client, org, p["id"], "A-1").status_code == 409  # bay labels are unique per property
    assert bay(client, org, p["id"], "B-9", a1["id"]).json()["parent_unit_id"] == a1["id"]
    assert bay(client, org, p["id"], "X", bedrooms=1).status_code == 422
    assert bay(client, org, p["id"], "Y", letting_mode="whole").status_code == 422
    # a bay cannot be allocated to a room or another bay
    assert bay(client, org, p["id"], "Z", b["id"]).status_code == 422
    listed = client.get(f"/v1/properties/{p['id']}/units?type=parking", headers=org.admin).json()[
        "items"
    ]
    assert sorted(x["label"] for x in listed) == ["A-1", "B-9"]
    # bays do not count as units on the property
    assert client.get(f"/v1/properties/{p['id']}", headers=org.admin).json()["unit_count"] == 1


def test_a_bay_comes_with_a_unit_let_whole(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    a1 = org.new_unit(p["id"], "A-1")
    b = bay(client, org, p["id"], "B2-115", a1["id"]).json()
    t = org.new_tenant()
    r = client.post("/v1/leases", headers=org.admin, json=lease_body(b["id"], t["id"]))
    assert r.status_code == 422 and "comes with A-1" in r.text
    whole = org.new_lease(a1["id"], [t["id"]])
    got = client.get(f"/v1/leases/{whole['id']}", headers=org.admin).json()
    assert got["unit_type"] == "whole_unit"
    assert got["included_bays"] == [{"id": b["id"], "label": "B2-115"}]


def test_a_bay_of_a_unit_let_by_the_room_is_let_on_its_own_lease(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    p = org.new_property()
    h = client.post(
        f"/v1/properties/{p['id']}/units",
        headers=org.admin,
        json={"label": "H", "letting_mode": "rooms", "default_rent": RENT},
    ).json()
    b = bay(client, org, p["id"], "P1-07", h["id"]).json()
    t = org.new_tenant()
    r = client.post(
        "/v1/leases",
        headers=org.admin,
        json=lease_body(b["id"], t["id"], vehicle_plate="wxy  1234"),
    )
    assert r.status_code == 201, r.text
    lease = r.json()
    assert lease["vehicle_plate"] == "WXY 1234" and lease["unit_type"] == "parking"
    assert lease["parent_unit_label"] is None  # a bay is not a room of its unit
    assert client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin).status_code == 200
    assert client.get(f"/v1/units/{b['id']}", headers=org.admin).json()["status"] == "occupied"
    # the unit cannot go back to letting whole while its bay is let separately
    r = client.patch(f"/v1/units/{h['id']}", headers=org.admin, json={"letting_mode": "whole"})
    assert r.status_code == 409 and "P1-07" in r.json()["message"]
    # nor can the bay move while it is let
    assert (
        client.patch(
            f"/v1/units/{b['id']}", headers=org.admin, json={"parent_unit_id": None}
        ).status_code
        == 409
    )
    # changing the plate on an active lease is not an amendment of the terms
    upd = client.patch(
        f"/v1/leases/{lease['id']}", headers=org.admin, json={"vehicle_plate": "ABC 1"}
    )
    assert upd.status_code == 200 and upd.json()["vehicle_plate"] == "ABC 1"
    occ = client.get("/v1/reports/occupancy", headers=org.admin).json()["rows"][0]
    assert occ["bays_total"] == 1 and occ["bays_occupied"] == 1 and occ["units_total"] == 0
    rentals = client.get(f"/v1/tenants/{t['id']}", headers=org.admin).json()["rentals"]
    assert rentals[0]["unit_type"] == "parking"


def test_plates_only_on_bay_leases_and_erased_with_the_tenant(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    p = org.new_property()
    a1 = org.new_unit(p["id"], "A-1")
    b = bay(client, org, p["id"], "V1").json()
    t = org.new_tenant()
    r = client.post(
        "/v1/leases", headers=org.admin, json=lease_body(a1["id"], t["id"], vehicle_plate="ABC 1")
    )
    assert r.status_code == 422 and r.json()["details"][0]["field"] == "vehicle_plate"
    assert (
        client.post(
            "/v1/leases",
            headers=org.admin,
            json=lease_body(b["id"], t["id"], vehicle_plate="ABC 1!"),
        ).status_code
        == 422
    )
    lease = client.post(
        "/v1/leases", headers=org.admin, json=lease_body(b["id"], t["id"], vehicle_plate="JJJ 7")
    ).json()
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    tok = org.tenant_login(t)
    exported = client.get("/v1/privacy/export", headers=tok).json()["data"]
    assert "JJJ 7" in str(exported)
    client.post(
        f"/v1/leases/{lease['id']}/terminate",
        headers=org.admin,
        json={"termination_date": "2018-12-31", "reason": "sold the car"},
    )
    anonymise_tenant(world.c, org.id, t["id"])
    assert (
        client.get(f"/v1/leases/{lease['id']}", headers=org.admin).json()["vehicle_plate"] is None
    )


def test_reallocating_and_deleting_around_bays(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    a1, a2 = org.new_unit(p["id"], "A-1"), org.new_unit(p["id"], "A-2")
    b = bay(client, org, p["id"], "B1", a1["id"]).json()
    r = client.patch(f"/v1/units/{b['id']}", headers=org.admin, json={"parent_unit_id": a2["id"]})
    assert r.status_code == 200 and r.json()["parent_unit_id"] == a2["id"]
    assert (
        client.patch(
            f"/v1/units/{a1['id']}", headers=org.admin, json={"parent_unit_id": a2["id"]}
        ).status_code
        == 422
    )
    # deleting the unit keeps its bay, as a property bay
    assert (
        client.delete(
            f"/v1/units/{a2['id']}", headers=org.admin, params={"reason": "typo"}
        ).status_code
        == 200
    )
    assert client.get(f"/v1/units/{b['id']}", headers=org.admin).json()["parent_unit_id"] is None
    # and the bay itself can be deleted, freeing its label
    assert (
        client.delete(
            f"/v1/units/{b['id']}", headers=org.admin, params={"reason": "typo"}
        ).status_code
        == 200
    )
    assert bay(client, org, p["id"], "B1").status_code == 201

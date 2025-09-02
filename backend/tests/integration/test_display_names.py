"""Display names on leases and tenants (contract v0.3.2): who rents which unit or room."""

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import World
from tests.integration.test_rooms import mk_unit, room


def test_leases_carry_property_unit_room_and_tenant_names(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property("Pelangi")
    whole = org.new_unit(p["id"], "A-1")
    house = mk_unit(client, org, p["id"], "D-5-8", letting_mode="rooms")
    master = room(client, org, p["id"], house["id"], "Master").json()
    siti = org.new_tenant(full_name="Siti Aminah")
    ali = org.new_tenant(full_name="Ali Hassan")

    created = org.new_lease(whole["id"], [siti["id"], ali["id"]])
    assert (created["property_name"], created["unit_label"], created["parent_unit_label"]) == (
        "Pelangi",
        "A-1",
        None,
    )
    assert created["tenants"] == [
        {"id": siti["id"], "full_name": "Siti Aminah"},
        {"id": ali["id"], "full_name": "Ali Hassan"},
    ]

    on_room = org.new_lease(master["id"], [ali["id"]])
    got = client.get(f"/v1/leases/{on_room['id']}", headers=org.admin).json()
    assert (got["property_name"], got["unit_label"], got["parent_unit_label"]) == (
        "Pelangi",
        "Master",
        "D-5-8",
    )

    # names are resolved when read: a rename shows at once, in the list too
    assert (
        client.patch(
            f"/v1/units/{whole['id']}", headers=org.admin, json={"label": "A-01"}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/v1/tenants/{siti['id']}", headers=org.admin, json={"full_name": "Siti A."}
        ).status_code
        == 200
    )
    listed = {x["id"]: x for x in client.get("/v1/leases", headers=org.admin).json()["items"]}
    assert listed[created["id"]]["unit_label"] == "A-01"
    assert listed[created["id"]]["tenants"][0]["full_name"] == "Siti A."
    assert listed[on_room["id"]]["parent_unit_label"] == "D-5-8"


def test_tenants_carry_their_current_and_upcoming_rentals(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property("Pelangi")
    house = mk_unit(client, org, p["id"], "D-5-8", letting_mode="rooms")
    master = room(client, org, p["id"], house["id"], "Master").json()
    other = org.new_unit(p["id"], "B-2")
    siti = org.new_tenant(full_name="Siti Aminah")
    idle = org.new_tenant(full_name="No Lease")

    active = org.new_lease(master["id"], [siti["id"]])
    assert client.post(f"/v1/leases/{active['id']}/activate", headers=org.admin).status_code == 200
    draft = org.new_lease(other["id"], [siti["id"]], start_date="2019-12-01", end_date="2020-11-30")

    items = {t["id"]: t for t in client.get("/v1/tenants", headers=org.admin).json()["items"]}
    assert items[siti["id"]]["rentals"] == [
        {
            "lease_id": active["id"],
            "status": "active",
            "property_name": "Pelangi",
            "unit_label": "Master",
            "parent_unit_label": "D-5-8",
            "unit_type": "room",
        },
        {
            "lease_id": draft["id"],
            "status": "draft",
            "property_name": "Pelangi",
            "unit_label": "B-2",
            "parent_unit_label": None,
            "unit_type": "whole_unit",
        },
    ]
    assert items[idle["id"]]["rentals"] == []
    one = client.get(f"/v1/tenants/{siti['id']}", headers=org.admin).json()
    assert [r["lease_id"] for r in one["rentals"]] == [active["id"], draft["id"]]

    # a tenant sees their own record without the draft (drafts are internal)
    tok = org.tenant_login(siti)
    mine = client.get(f"/v1/tenants/{siti['id']}", headers=tok).json()
    assert [r["lease_id"] for r in mine["rentals"]] == [active["id"]]


def test_privacy_export_does_not_name_co_tenants(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    unit = org.new_unit(p["id"])
    siti = org.new_tenant(full_name="Siti Aminah")
    ali = org.new_tenant(full_name="Ali Hassan")
    lease = org.new_lease(unit["id"], [siti["id"], ali["id"]])
    assert client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin).status_code == 200
    export = client.get(f"/v1/tenants/{siti['id']}/privacy-export", headers=org.admin)
    assert export.status_code == 200, export.text
    assert "Ali Hassan" not in export.text


def test_maintenance_requests_say_who_reported_what_and_where(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    p = org.new_property("Pelangi")
    house = mk_unit(client, org, p["id"], "D-5-8", letting_mode="rooms")
    master = room(client, org, p["id"], house["id"], "Master").json()
    ali = org.new_tenant(full_name="Ali Hassan")
    lease = org.new_lease(master["id"], [ali["id"]], start_date="2018-11-01")
    assert client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin).status_code == 200
    tok = org.tenant_login(ali)
    r = client.post(
        "/v1/maintenance-requests",
        headers=tok,
        json={
            "area": "unit",
            "unit_id": master["id"],
            "title": "Aircon leaking",
            "description": "Drips at night",
            "category": "appliance",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["reported_by_name"] == "Ali Hassan"

    listed = client.get("/v1/maintenance-requests", headers=org.admin).json()["items"][0]
    assert (
        listed["reported_by_name"],
        listed["property_name"],
        listed["parent_unit_label"],
        listed["unit_label"],
    ) == (
        "Ali Hassan",
        "Pelangi",
        "D-5-8",
        "Master",
    )
    me = client.get("/v1/me", headers=org.admin).json()["user_id"]
    path = f"/v1/maintenance-requests/{listed['id']}"
    assert client.patch(path, headers=org.admin, json={"status": "triaged"}).status_code == 200
    assigned = client.patch(path, headers=org.admin, json={"assigned_to": me}).json()
    assert assigned["assigned_to_name"]  # the admin's name or email

    shared = client.post(  # a property-level request raised by staff
        "/v1/maintenance-requests",
        headers=org.admin,
        json={
            "area": "shared_area",
            "property_id": p["id"],
            "title": "Lift",
            "description": "Stuck",
            "category": "other",
        },
    ).json()
    assert (shared["property_name"], shared["unit_label"], shared["reported_by_name"]) == (
        "Pelangi",
        None,
        assigned["assigned_to_name"],
    )


def test_tenancy_follows_the_leases_not_the_account(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    siti = org.new_tenant(full_name="Siti Aminah")
    nobody = org.new_tenant(full_name="No Lease")
    lease = org.new_lease(org.new_unit(p["id"], "A-1")["id"], [siti["id"]], start_date="2018-11-01")

    def tenancy(t: dict[str, Any]) -> tuple[str, str]:
        x = client.get(f"/v1/tenants/{t['id']}", headers=org.admin).json()
        return x["status"], x["tenancy"]

    assert tenancy(nobody) == ("invited", "none")
    assert tenancy(siti) == ("invited", "upcoming")
    assert client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin).status_code == 200
    assert tenancy(siti)[1] == "current"
    r = client.post(
        f"/v1/leases/{lease['id']}/terminate",
        headers=org.admin,
        json={"termination_date": "2018-11-30", "reason": "moving out", "waive_notice": True},
    )
    assert r.status_code == 200, r.text
    assert tenancy(siti) == (
        "invited",
        "past",
    )  # the account is untouched: final bills still reach them
    assert client.get(f"/v1/tenants/{siti['id']}", headers=org.admin).json()["rentals"] == []

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import Org, World, needs_ddb_local

RENT = {"amount_minor": 80000, "currency": "MYR"}


def mk_unit(client: TestClient, org: Org, pid: str, label: str, **extra: Any) -> dict[str, Any]:
    r = client.post(
        f"/v1/properties/{pid}/units",
        headers=org.admin,
        json={"label": label, "default_rent": RENT, **extra},
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def room(client: TestClient, org: Org, pid: str, parent: str, label: str) -> Any:
    return client.post(
        f"/v1/properties/{pid}/units",
        headers=org.admin,
        json={"label": label, "default_rent": RENT, "type": "room", "parent_unit_id": parent},
    )


def house(world: World) -> tuple[Org, dict, dict, dict, dict]:  # type: ignore[type-arg]
    """A whole unit let by rooms with two rooms."""
    org = world.make_org()
    p = org.new_property()
    h = mk_unit(world.client, org, p["id"], "House 1", letting_mode="rooms")
    r1, r2 = (room(world.client, org, p["id"], h["id"], f"Room {i}").json() for i in (1, 2))
    return org, p, h, r1, r2


def test_unit_defaults_and_room_creation(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    whole = mk_unit(client, org, p["id"], "A-1")
    assert (
        whole["type"] == "whole_unit"
        and whole["letting_mode"] == "whole"
        and whole["parent_unit_id"] is None
    )
    r = room(client, org, p["id"], whole["id"], "Room 1")
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"  # let as a whole
    assert (
        client.patch(
            f"/v1/units/{whole['id']}", headers=org.admin, json={"letting_mode": "rooms"}
        ).json()["letting_mode"]
        == "rooms"
    )
    r = room(client, org, p["id"], whole["id"], "Room 1")
    assert r.status_code == 201
    rm = r.json()
    assert (rm["type"], rm["parent_unit_id"], rm["letting_mode"], rm["status"]) == (
        "room",
        whole["id"],
        None,
        "vacant",
    )
    assert client.get(f"/v1/units/{rm['id']}", headers=org.admin).json() == rm
    assert (
        room(client, org, p["id"], whole["id"], " room 1 ").status_code == 409
    )  # unique within the parent
    other = mk_unit(client, org, p["id"], "House 2", letting_mode="rooms")
    assert (
        room(client, org, p["id"], other["id"], "Room 1").status_code == 201
    )  # same label, other house
    # rooms belong to their unit: the property counts whole units
    assert client.get(f"/v1/properties/{p['id']}", headers=org.admin).json()["unit_count"] == 3 - 1


def test_room_validation(world: World, client: TestClient) -> None:
    org, p, h, r1, _ = house(world)
    other_p = org.new_property("Other")
    foreign = world.make_org()
    fh = mk_unit(client, foreign, foreign.new_property()["id"], "F", letting_mode="rooms")
    base = {"label": "X", "default_rent": RENT}
    for body in (
        {**base, "type": "room"},
        {**base, "type": "room", "parent_unit_id": h["id"], "letting_mode": "rooms"},
        {**base, "type": "whole_unit", "parent_unit_id": h["id"]},
        {**base, "type": "room", "parent_unit_id": r1["id"]},  # rooms cannot contain rooms
        {**base, "type": "room", "parent_unit_id": "01JD8Z3K9Q4M7T2V6X8B5N1C0B"},
        {**base, "type": "room", "parent_unit_id": fh["id"]},  # another org
        {**base, "type": "suite"},
        {**base, "letting_mode": "bedspaces"},
    ):
        r = client.post(f"/v1/properties/{p['id']}/units", headers=org.admin, json=body)
        assert r.status_code == 422, body
    r = client.post(
        f"/v1/properties/{other_p['id']}/units",
        headers=org.admin,
        json={**base, "type": "room", "parent_unit_id": h["id"]},
    )
    assert r.status_code == 422  # the parent is in a different property
    assert (
        client.patch(
            f"/v1/units/{r1['id']}", headers=org.admin, json={"letting_mode": "whole"}
        ).status_code
        == 422
    )
    for field in ({"type": "whole_unit"}, {"parent_unit_id": h["id"]}):
        assert (
            client.patch(f"/v1/units/{r1['id']}", headers=org.admin, json=field).status_code == 422
        )


def test_list_filters(world: World, client: TestClient) -> None:
    org, p, h, r1, r2 = house(world)
    whole = mk_unit(client, org, p["id"], "Flat")
    url = f"/v1/properties/{p['id']}/units"
    everything = client.get(f"{url}?limit=100", headers=org.admin).json()["items"]
    assert [u["id"] for u in everything] == [
        h["id"],
        r1["id"],
        r2["id"],
        whole["id"],
    ]  # rooms follow their unit
    assert [u["id"] for u in client.get(f"{url}?type=room", headers=org.admin).json()["items"]] == [
        r1["id"],
        r2["id"],
    ]
    assert [
        u["id"] for u in client.get(f"{url}?type=whole_unit", headers=org.admin).json()["items"]
    ] == [h["id"], whole["id"]]
    assert [
        u["id"]
        for u in client.get(f"{url}?parent_unit_id={h['id']}", headers=org.admin).json()["items"]
    ] == [r1["id"], r2["id"]]
    assert (
        client.get(f"{url}?parent_unit_id={whole['id']}", headers=org.admin).json()["items"] == []
    )
    assert (
        client.get(
            f"{url}?parent_unit_id=01JD8Z3K9Q4M7T2V6X8B5N1C0B", headers=org.admin
        ).status_code
        == 404
    )
    seen, cursor = [], None
    while True:
        page = client.get(
            url, headers=org.admin, params={"limit": 1, **({"cursor": cursor} if cursor else {})}
        ).json()
        seen += [u["id"] for u in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == [u["id"] for u in everything]


def test_room_leases_drive_parent_status(world: World, client: TestClient) -> None:
    org, p, h, r1, r2 = house(world)
    t1, t2 = org.new_tenant(), org.new_tenant()
    l1 = org.new_lease(r1["id"], [t1["id"]])
    l2 = org.new_lease(r2["id"], [t2["id"]])
    store = world.c.store.units(org.id)
    for lease in (l1, l2):
        assert (
            client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin).status_code == 200
        )
    parent = store.get(h["id"])
    assert (
        parent is not None and parent["active_room_leases"] == 2 and parent["status"] == "occupied"
    )
    assert parent["current_lease_id"] is None
    assert (
        client.get(f"/v1/units/{r1['id']}", headers=org.admin).json()["current_lease_id"]
        == l1["id"]
    )
    term = {"termination_date": "2019-02-01", "reason": "left"}
    assert (
        client.post(f"/v1/leases/{l1['id']}/terminate", headers=org.admin, json=term).status_code
        == 200
    )
    parent = store.get(h["id"])
    assert (
        parent is not None and parent["active_room_leases"] == 1 and parent["status"] == "occupied"
    )
    assert client.get(f"/v1/units/{r1['id']}", headers=org.admin).json()["status"] == "vacant"
    assert (
        client.post(f"/v1/leases/{l2['id']}/terminate", headers=org.admin, json=term).status_code
        == 200
    )
    parent = store.get(h["id"])
    assert parent is not None and parent["active_room_leases"] == 0 and parent["status"] == "vacant"
    # two tenants in different rooms of one house at the same time, and the dates are free again
    again = org.new_lease(r1["id"], [t1["id"]])
    assert client.post(f"/v1/leases/{again['id']}/activate", headers=org.admin).status_code == 200


def test_letting_mode_blocks_wrong_leases(world: World, client: TestClient) -> None:
    org, p, h, r1, _ = house(world)
    t = org.new_tenant()
    body = {
        "unit_id": h["id"],
        "tenant_ids": [t["id"]],
        "start_date": "2018-12-01",
        "end_date": "2019-11-30",
        "monthly_rent": RENT,
        "deposits": [],
        "due_day": 5,
    }
    r = client.post("/v1/leases", headers=org.admin, json=body)
    assert r.status_code == 422 and r.json()["details"][0]["field"] == "unit_id"  # let by rooms
    # a whole-unit draft created while the unit is let as a whole, then the mode flips
    flat = mk_unit(client, org, p["id"], "Flat")
    draft = org.new_lease(flat["id"], [t["id"]])
    client.patch(f"/v1/units/{flat['id']}", headers=org.admin, json={"letting_mode": "rooms"})
    r = client.post(f"/v1/leases/{draft['id']}/activate", headers=org.admin)
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"
    # a room whose house switched back to whole cannot be let
    rl = org.new_lease(r1["id"], [t["id"]])
    client.patch(f"/v1/units/{h['id']}", headers=org.admin, json={"letting_mode": "whole"})
    assert client.post(f"/v1/leases/{rl['id']}/activate", headers=org.admin).status_code == 409
    assert (
        client.post("/v1/leases", headers=org.admin, json={**body, "unit_id": r1["id"]}).status_code
        == 422
    )


def test_letting_mode_cannot_change_while_let(world: World, client: TestClient) -> None:
    org, p, h, r1, _ = house(world)
    t = org.new_tenant()
    lease = org.new_lease(r1["id"], [t["id"]])
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    r = client.patch(f"/v1/units/{h['id']}", headers=org.admin, json={"letting_mode": "whole"})
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"
    assert (
        client.patch(
            f"/v1/units/{h['id']}", headers=org.admin, json={"letting_mode": "rooms"}
        ).status_code
        == 200
    )  # no-op
    client.post(
        f"/v1/leases/{lease['id']}/terminate",
        headers=org.admin,
        json={"termination_date": "2019-02-01", "reason": "r"},
    )
    assert (
        client.patch(
            f"/v1/units/{h['id']}", headers=org.admin, json={"letting_mode": "whole"}
        ).status_code
        == 200
    )
    # a whole unit with a (future) lease of its own cannot switch either
    flat = mk_unit(client, org, p["id"], "Flat")
    wl = org.new_lease(flat["id"], [t["id"]])
    client.post(f"/v1/leases/{wl['id']}/activate", headers=org.admin)
    assert (
        client.patch(
            f"/v1/units/{flat['id']}", headers=org.admin, json={"letting_mode": "rooms"}
        ).status_code
        == 409
    )


@needs_ddb_local
def test_whole_unit_and_room_never_let_together_under_parallel_requests(
    world: World, client: TestClient
) -> None:
    """The letting mode flip races with a room activation. Exactly one may win, never both."""
    org = world.make_org()
    p = org.new_property()
    t = org.new_tenant()
    outcomes = set()
    for i in range(6):
        h = mk_unit(client, org, p["id"], f"H{i}", letting_mode="rooms")
        rm = room(client, org, p["id"], h["id"], "R").json()
        lease = org.new_lease(rm["id"], [t["id"]])

        def activate(lid: str = lease["id"]) -> int:
            return client.post(f"/v1/leases/{lid}/activate", headers=org.admin).status_code

        def flip(uid: str = h["id"]) -> int:
            return client.patch(
                f"/v1/units/{uid}", headers=org.admin, json={"letting_mode": "whole"}
            ).status_code

        with ThreadPoolExecutor(2) as ex:
            a, f = ex.submit(activate), ex.submit(flip)
            ra, rf = a.result(), f.result()
        parent = world.c.store.units(org.id).get(h["id"])
        assert parent is not None
        if ra == 200:
            assert (
                rf == 409
                and parent["letting_mode"] == "rooms"
                and parent["active_room_leases"] == 1
            )
        else:
            assert (
                (ra, rf) == (409, 200)
                and parent["letting_mode"] == "whole"
                and parent["active_room_leases"] == 0
            )
        outcomes.add(ra)
    assert outcomes <= {200, 409}


def test_archive_unit_cascades_to_rooms_and_property_archive_respects_rooms(
    world: World, client: TestClient
) -> None:
    org, p, h, r1, r2 = house(world)
    assert (
        client.patch(
            f"/v1/units/{h['id']}", headers=org.admin, json={"status": "archived"}
        ).status_code
        == 200
    )
    kids = client.get(
        f"/v1/properties/{p['id']}/units?parent_unit_id={h['id']}", headers=org.admin
    ).json()["items"]
    assert {k["status"] for k in kids} == {"inactive"}
    assert room(client, org, p["id"], h["id"], "Room 3").status_code == 409  # archived parent
    org2, p2, h2, ra, _ = house(world)
    t = org2.new_tenant()
    lease = org2.new_lease(ra["id"], [t["id"]])
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org2.admin)
    assert (
        client.patch(
            f"/v1/units/{h2['id']}", headers=org2.admin, json={"status": "archived"}
        ).status_code
        == 409
    )
    assert (
        client.post(f"/v1/properties/{p2['id']}/deactivate", headers=org2.admin).status_code == 409
    )  # occupied room


def test_room_tenant_sees_own_room_only(world: World, client: TestClient) -> None:
    org, p, h, r1, r2 = house(world)
    t = org.new_tenant()
    lease = org.new_lease(r1["id"], [t["id"]])
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    tok = org.tenant_login(t)
    assert client.get(f"/v1/units/{r1['id']}", headers=tok).status_code == 200
    assert client.get(f"/v1/units/{r2['id']}", headers=tok).status_code == 403
    assert client.get(f"/v1/units/{h['id']}", headers=tok).status_code == 403

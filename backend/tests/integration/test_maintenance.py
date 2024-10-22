from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import Org, World
from tests.integration.billing_helpers import Setup


def mk(client: TestClient, h: dict[str, str], **over: Any) -> Any:
    body = {
        "title": "Leaking tap",
        "description": "Kitchen tap drips",
        "category": "plumbing",
        **over,
    }
    return client.post("/v1/maintenance-requests", headers=h, json=body)


def let_house(world: World) -> tuple[Org, dict, dict, dict, dict, dict[str, str]]:  # type: ignore[type-arg]
    """A house let by rooms, a tenant in room 1, and the tenant's token."""
    org = world.make_org()
    p = org.new_property()
    h = world.client.post(
        f"/v1/properties/{p['id']}/units",
        headers=org.admin,
        json={
            "label": "House",
            "default_rent": {"amount_minor": 1, "currency": "MYR"},
            "letting_mode": "rooms",
        },
    ).json()
    rooms = [
        world.client.post(
            f"/v1/properties/{p['id']}/units",
            headers=org.admin,
            json={
                "label": f"Room {i}",
                "default_rent": {"amount_minor": 1, "currency": "MYR"},
                "type": "room",
                "parent_unit_id": h["id"],
            },
        ).json()
        for i in (1, 2)
    ]
    t = org.new_tenant()
    lease = org.new_lease(rooms[0]["id"], [t["id"]], deposits=[])
    world.client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    return org, p, h, rooms[0], rooms[1], org.tenant_login(t)


def test_staff_create_triage_assign_resolve_close(world: World, client: TestClient) -> None:
    org = world.make_org()
    pm_sub = world.c.identity  # noqa: F841
    pm = org.invite_manager()
    mine = client.post(  # the manager's own property (manager scope, contract v0.4.0)
        "/v1/properties",
        headers=pm,
        json={
            "name": "PM Tower",
            "type": "condominium",
            "address": {"line1": "1 Jalan A", "city": "KL", "state": "WP", "postcode": "50000"},
        },
    ).json()
    u = org.new_unit(mine["id"])
    r = mk(client, org.admin, unit_id=u["id"], priority="high")
    assert r.status_code == 201
    req = r.json()
    assert (
        req["status"] == "open" and req["area"] == "unit" and req["property_id"] == u["property_id"]
    )
    assert req["unit_id"] == u["id"] and req["tenant_id"] is None and req["attachment_ids"] == []
    p = f"/v1/maintenance-requests/{req['id']}"
    assert (
        client.patch(p, headers=pm, json={"status": "assigned"}).status_code == 409
    )  # cannot skip triage
    assert client.patch(p, headers=pm, json={"status": "triaged"}).json()["status"] == "triaged"
    sub = next(
        x["user_id"]
        for x in client.get("/v1/users", headers=org.admin).json()["items"]
        if x["user_id"] != org.admin_sub
    )
    a = client.patch(p, headers=org.admin, json={"assigned_to": sub})
    assert (
        a.json()["status"] == "assigned" and a.json()["assigned_to"] == sub
    )  # assigning moves a triaged request on
    assert client.patch(p, headers=org.admin, json={"assigned_to": "not-a-user"}).status_code == 422
    assert (
        client.patch(p, headers=org.admin, json={"status": "in_progress"}).json()["status"]
        == "in_progress"
    )
    done = client.patch(p, headers=org.admin, json={"status": "resolved"}).json()
    assert done["status"] == "resolved" and done["resolved_at"]
    reopened = client.patch(p, headers=org.admin, json={"status": "in_progress"}).json()
    assert reopened["resolved_at"] is None
    client.patch(p, headers=org.admin, json={"status": "resolved"})
    assert (
        client.patch(p, headers=org.admin, json={"status": "closed"}).json()["status"] == "closed"
    )
    assert (
        client.patch(p, headers=org.admin, json={"priority": "low"}).status_code == 409
    )  # terminal
    assert client.patch(p, headers=org.admin, json={"status": "open"}).status_code == 409
    by_status = client.get("/v1/maintenance-requests?status=closed", headers=org.admin).json()[
        "items"
    ]
    assert [x["id"] for x in by_status] == [req["id"]]
    assert (
        client.get("/v1/maintenance-requests?status=open", headers=org.admin).json()["items"] == []
    )  # GSI4 follows status


def test_cancel_only_before_work_starts(world: World, client: TestClient) -> None:
    org = world.make_org()
    u = org.new_unit(org.new_property()["id"])
    a, b = (
        mk(client, org.admin, unit_id=u["id"]).json(),
        mk(client, org.admin, unit_id=u["id"]).json(),
    )
    assert (
        client.patch(
            f"/v1/maintenance-requests/{a['id']}", headers=org.admin, json={"status": "cancelled"}
        ).status_code
        == 200
    )
    for s in ("triaged",):
        client.patch(f"/v1/maintenance-requests/{b['id']}", headers=org.admin, json={"status": s})
    assert (
        client.patch(
            f"/v1/maintenance-requests/{b['id']}", headers=org.admin, json={"status": "cancelled"}
        ).status_code
        == 200
    )


def test_create_validation(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    u = org.new_unit(p["id"])
    for body in (
        {"unit_id": u["id"], "property_id": p["id"]},  # exactly one location
        {},  # none
        {"property_id": p["id"]},  # property-level must be a shared area
        {"unit_id": u["id"], "category": "magic"},
        {"unit_id": u["id"], "priority": "asap"},
        {"unit_id": u["id"], "title": ""},
        {"unit_id": u["id"], "attachment_ids": ["01JD8Z3K9Q4M7T2V6X8B5N1C0B"]},
        {"unit_id": "01JD8Z3K9Q4M7T2V6X8B5N1C0B"},
    ):
        assert mk(client, org.admin, **body).status_code == 422, body
    assert (
        mk(client, org.admin, property_id=p["id"], area="shared_area").status_code == 201
    )  # the lift, the car park
    other = world.make_org()
    assert (
        mk(client, org.admin, unit_id=other.new_unit(other.new_property()["id"])["id"]).status_code
        == 422
    )  # not yours


def test_room_and_shared_area_rules(world: World, client: TestClient) -> None:
    org, p, house, room1, room2, tok = let_house(world)
    mine = mk(client, tok, unit_id=room1["id"])
    assert (
        mine.status_code == 201
        and mine.json()["tenant_id"] is not None
        and mine.json()["area"] == "unit"
    )
    assert mk(client, tok, unit_id=room2["id"]).status_code == 403  # somebody else's room
    kitchen = mk(client, tok, unit_id=house["id"], area="shared_area", title="Shared kitchen sink")
    assert (
        kitchen.status_code == 201
        and kitchen.json()["area"] == "shared_area"
        and kitchen.json()["unit_id"] == house["id"]
    )
    assert (
        mk(client, tok, unit_id=house["id"]).status_code == 403
    )  # area=unit must be the tenant's own room
    assert (
        mk(client, tok, unit_id=room1["id"], area="shared_area").status_code == 422
    )  # a shared area names the whole unit
    building = mk(client, tok, property_id=p["id"], area="shared_area", title="Lift out of order")
    assert (
        building.status_code == 201
        and building.json()["unit_id"] is None
        and building.json()["property_id"] == p["id"]
    )
    other_p = org.new_property("Elsewhere")
    assert mk(client, tok, property_id=other_p["id"], area="shared_area").status_code == 403
    # staff see all of it, and can list per property
    all_ = client.get("/v1/maintenance-requests?limit=100", headers=org.admin).json()["items"]
    assert len(all_) == 3
    assert (
        len(
            client.get(f"/v1/maintenance-requests?property_id={p['id']}", headers=org.admin).json()[
                "items"
            ]
        )
        == 3
    )
    assert (
        len(
            client.get(f"/v1/maintenance-requests?unit_id={room1['id']}", headers=org.admin).json()[
                "items"
            ]
        )
        == 1
    )


def test_a_tenant_sees_only_their_own_requests(world: World, client: TestClient) -> None:
    org, p, house, room1, room2, tok = let_house(world)
    t2 = org.new_tenant()
    lease2 = org.new_lease(room2["id"], [t2["id"]], deposits=[])
    client.post(f"/v1/leases/{lease2['id']}/activate", headers=org.admin)
    tok2 = org.tenant_login(t2)
    r1 = mk(client, tok, unit_id=room1["id"]).json()
    r2 = mk(client, tok2, unit_id=room2["id"]).json()
    shared = mk(client, tok, unit_id=house["id"], area="shared_area").json()
    assert {
        x["id"] for x in client.get("/v1/maintenance-requests", headers=tok).json()["items"]
    } == {r1["id"], shared["id"]}
    assert [
        x["id"] for x in client.get("/v1/maintenance-requests", headers=tok2).json()["items"]
    ] == [r2["id"]]
    assert client.get(f"/v1/maintenance-requests/{r2['id']}", headers=tok).status_code == 403
    assert (
        client.get(f"/v1/maintenance-requests/{shared['id']}", headers=tok2).status_code == 403
    )  # not the other room mates'
    assert (
        client.patch(
            f"/v1/maintenance-requests/{r1['id']}", headers=tok, json={"status": "cancelled"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/v1/maintenance-requests/{r2['id']}/comments", headers=tok, json={"body": "hi"}
        ).status_code
        == 403
    )
    nxt = client.get("/v1/maintenance-requests?limit=1", headers=tok).json()
    assert nxt["next_cursor"] and len(nxt["items"]) == 1


def test_comments_internal_notes_and_terminal_state(world: World, client: TestClient) -> None:
    org, p, house, room1, room2, tok = let_house(world)
    req = mk(client, tok, unit_id=room1["id"]).json()
    path = f"/v1/maintenance-requests/{req['id']}/comments"
    assert client.post(path, headers=tok, json={"body": "It is getting worse"}).status_code == 201
    note = client.post(
        path, headers=org.admin, json={"body": "Plumber booked, tenant is rude", "internal": True}
    )
    assert (
        note.status_code == 201
        and note.json()["internal"] is True
        and note.json()["author_role"] == "org_admin"
    )
    pub = client.post(path, headers=org.admin, json={"body": "A plumber comes Tuesday"}).json()
    assert (
        client.post(path, headers=tok, json={"body": "secret", "internal": True}).status_code == 422
    )
    staff_view = client.get(path, headers=org.admin).json()["items"]
    tenant_view = client.get(path, headers=tok).json()["items"]
    assert len(staff_view) == 3 and [c["id"] for c in tenant_view if c["internal"]] == []
    assert pub["id"] in [c["id"] for c in tenant_view] and len(tenant_view) == 2
    assert client.post(path, headers=tok, json={"body": ""}).status_code == 422
    client.patch(
        f"/v1/maintenance-requests/{req['id']}", headers=org.admin, json={"status": "cancelled"}
    )
    assert client.post(path, headers=org.admin, json={"body": "late"}).status_code == 409


def test_attachments(world: World, client: TestClient) -> None:
    org, p, house, room1, room2, tok = let_house(world)
    me = client.get("/v1/me", headers=tok).json()["tenant_id"]
    photo = client.post(
        "/v1/documents/upload-url",
        headers=tok,
        json={
            "owner_type": "tenant",
            "owner_id": me,
            "type": "maintenance_photo",
            "filename": "tap.jpg",
            "content_type": "image/jpeg",
            "size_bytes": 100,
        },
    ).json()["document_id"]
    r = mk(client, tok, unit_id=room1["id"], attachment_ids=[photo])
    assert r.status_code == 201 and r.json()["attachment_ids"] == [photo]
    foreign = client.post(
        "/v1/documents/upload-url",
        headers=org.admin,
        json={
            "owner_type": "property",
            "owner_id": p["id"],
            "type": "other",
            "filename": "x.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
        },
    ).json()["document_id"]
    assert (
        mk(client, tok, unit_id=room1["id"], attachment_ids=[foreign]).status_code == 403
    )  # not the tenant's file
    # photos uploaded against the request itself
    up = client.post(
        "/v1/documents/upload-url",
        headers=tok,
        json={
            "owner_type": "maintenance_request",
            "owner_id": r.json()["id"],
            "type": "maintenance_photo",
            "filename": "after.jpg",
            "content_type": "image/jpeg",
            "size_bytes": 100,
        },
    )
    assert up.status_code == 201
    other = world.make_org()
    assert (
        client.post(
            "/v1/documents/upload-url",
            headers=other.admin,
            json={
                "owner_type": "maintenance_request",
                "owner_id": r.json()["id"],
                "type": "maintenance_photo",
                "filename": "x.jpg",
                "content_type": "image/jpeg",
                "size_bytes": 1,
            },
        ).status_code
        == 404
    )


def test_requests_are_in_the_subject_export_and_audit_has_no_text(
    world: World, client: TestClient
) -> None:
    org, p, house, room1, room2, tok = let_house(world)
    mk(
        client,
        tok,
        unit_id=room1["id"],
        title="Mould CANARY-9d1e",
        description="private details CANARY-9d1e",
    )
    export = client.get("/v1/privacy/export", headers=tok).json()["data"]
    assert [m["title"] for m in export["maintenance_requests"]] == ["Mould CANARY-9d1e"]
    log = str(client.get("/v1/audit-log?limit=100", headers=org.admin).json())
    assert "CANARY-9d1e" not in log


def test_platform_admin_is_read_only_and_cross_org_empty(world: World, client: TestClient) -> None:
    s = Setup(world)
    r = mk(client, s.h, unit_id=s.unit["id"]).json()
    assert (
        client.get("/v1/maintenance-requests", headers=world.platform).json()["items"] == []
    )  # their org, not this one
    assert (
        client.get(f"/v1/maintenance-requests/{r['id']}", headers=world.platform).status_code == 404
    )
    assert (
        client.post("/v1/maintenance-requests", headers=world.platform, json={}).status_code == 403
    )


def test_resolved_requests_close_themselves_after_seven_days(
    world: World, client: TestClient
) -> None:
    from datetime import timedelta

    from app.domains.maintenance.jobs import autoclose

    org, p, house, room1, room2, tok = let_house(world)
    a = mk(client, tok, unit_id=room1["id"]).json()
    b = mk(client, tok, unit_id=room1["id"]).json()
    for r in (a, b):
        for status in ("triaged",):
            client.patch(
                f"/v1/maintenance-requests/{r['id']}", headers=org.admin, json={"status": status}
            )
    sub = next(x["user_id"] for x in client.get("/v1/users", headers=org.admin).json()["items"])
    for r in (a, b):
        client.patch(
            f"/v1/maintenance-requests/{r['id']}", headers=org.admin, json={"assigned_to": sub}
        )
        client.patch(
            f"/v1/maintenance-requests/{r['id']}", headers=org.admin, json={"status": "in_progress"}
        )
        client.patch(
            f"/v1/maintenance-requests/{r['id']}", headers=org.admin, json={"status": "resolved"}
        )
    world.c.clock.set(world.c.clock.now() + timedelta(days=6))
    assert autoclose(world.c, org.id) == 0  # not yet seven days
    client.patch(
        f"/v1/maintenance-requests/{b['id']}", headers=org.admin, json={"status": "in_progress"}
    )  # reopened
    world.c.clock.set(world.c.clock.now() + timedelta(days=2))
    assert autoclose(world.c, org.id) == 1  # only the one still resolved
    assert (
        client.get(f"/v1/maintenance-requests/{a['id']}", headers=org.admin).json()["status"]
        == "closed"
    )
    assert (
        client.get(f"/v1/maintenance-requests/{b['id']}", headers=org.admin).json()["status"]
        == "in_progress"
    )
    assert autoclose(world.c, org.id) == 0
    log = client.get("/v1/audit-log?limit=100", headers=org.admin).json()["items"]
    assert any(e["action"] == "maintenance.autoclose" and e["actor_id"] == "system" for e in log)

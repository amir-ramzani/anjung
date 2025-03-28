"""Deactivate, reactivate and delete, the same way in every module (contract v0.9.0)."""

from datetime import timedelta
from typing import Any

from app.domains.privacy.retention import purge_closed_orgs, purge_org
from app.repositories import keys
from fastapi.testclient import TestClient

from tests.conftest import Org, World
from tests.integration.test_documents import upload_and_scan

WHY = {"reason": "added by mistake"}


def upload(world: World, org: Org, owner_type: str, owner_id: str, doc_type: str = "other") -> str:
    r = world.client.post(
        "/v1/documents/upload-url",
        headers=org.admin,
        json={
            "owner_type": owner_type,
            "owner_id": owner_id,
            "type": doc_type,
            "filename": "f.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
        },
    )
    assert r.status_code == 201, r.text
    did = str(r.json()["document_id"])
    upload_and_scan(world, org, did)
    return did


def room(client: TestClient, org: Org, pid: str, parent: str, label: str) -> dict[str, Any]:
    r = client.post(
        f"/v1/properties/{pid}/units",
        headers=org.admin,
        json={
            "type": "room",
            "parent_unit_id": parent,
            "label": label,
            "default_rent": {"amount_minor": 50000, "currency": "MYR"},
        },
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def house(client: TestClient, org: Org, pid: str, label: str = "House") -> dict[str, Any]:
    r = client.post(
        f"/v1/properties/{pid}/units",
        headers=org.admin,
        json={
            "label": label,
            "letting_mode": "rooms",
            "default_rent": {"amount_minor": 150000, "currency": "MYR"},
        },
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


# ---- properties and units: deactivate and reactivate


def test_deactivate_and_reactivate_a_property_with_its_units(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    p = org.new_property()
    a, b = org.new_unit(p["id"], "A"), org.new_unit(p["id"], "B")
    # B was switched off on its own before: it stays off when the property comes back
    assert (
        client.post(f"/v1/units/{b['id']}/deactivate", headers=org.admin).json()["status"]
        == "inactive"
    )
    r = client.post(f"/v1/properties/{p['id']}/deactivate", headers=org.admin)
    assert r.status_code == 200 and r.json()["status"] == "inactive"
    units = {
        u["label"]: u["status"]
        for u in client.get(f"/v1/properties/{p['id']}/units", headers=org.admin).json()["items"]
    }
    assert units == {"A": "inactive", "B": "inactive"}
    assert (
        client.get("/v1/properties?status=archived", headers=org.admin).json()["items"][0]["id"]
        == p["id"]
    )  # old name still filters
    assert (
        client.post(f"/v1/units/{a['id']}/reactivate", headers=org.admin).status_code == 409
    )  # property first

    r = client.post(f"/v1/properties/{p['id']}/reactivate", headers=org.admin)
    assert r.status_code == 200 and r.json()["status"] == "active"
    units = {
        u["label"]: u["status"]
        for u in client.get(f"/v1/properties/{p['id']}/units", headers=org.admin).json()["items"]
    }
    assert units == {"A": "vacant", "B": "inactive"}
    assert (
        client.post(f"/v1/units/{b['id']}/reactivate", headers=org.admin).json()["status"]
        == "vacant"
    )
    actions = {
        e["action"]
        for e in client.get("/v1/audit-log?limit=100", headers=org.admin).json()["items"]
    }
    assert {
        "property.deactivate",
        "property.reactivate",
        "unit.deactivate",
        "unit.reactivate",
    } <= actions


def test_a_draft_or_live_lease_blocks_deactivation(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    u = org.new_unit(p["id"])
    lease = org.new_lease(u["id"], [org.new_tenant()["id"]])
    for path in (f"/v1/properties/{p['id']}/deactivate", f"/v1/units/{u['id']}/deactivate"):
        r = client.post(path, headers=org.admin)
        assert r.status_code == 409 and "A-1" in r.json()["message"]
    assert (
        client.delete(f"/v1/leases/{lease['id']}", headers=org.admin, params=WHY).status_code == 200
    )
    assert client.post(f"/v1/units/{u['id']}/deactivate", headers=org.admin).status_code == 200


def test_a_unit_and_its_rooms_switch_off_and_on_together(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    h = house(client, org, p["id"])
    r1, r2 = room(client, org, p["id"], h["id"], "R1"), room(client, org, p["id"], h["id"], "R2")
    client.post(f"/v1/units/{r2['id']}/deactivate", headers=org.admin)
    # the old PATCH form still works and means deactivate
    r = client.patch(f"/v1/units/{h['id']}", headers=org.admin, json={"status": "archived"})
    assert r.status_code == 200 and r.json()["status"] == "inactive"
    assert client.get(f"/v1/units/{r1['id']}", headers=org.admin).json()["status"] == "inactive"
    assert (
        client.post(f"/v1/units/{r1['id']}/reactivate", headers=org.admin).status_code == 409
    )  # its unit first
    r = client.patch(f"/v1/units/{h['id']}", headers=org.admin, json={"status": "vacant"})
    assert r.json()["status"] == "vacant"
    assert client.get(f"/v1/units/{r1['id']}", headers=org.admin).json()["status"] == "vacant"
    assert client.get(f"/v1/units/{r2['id']}", headers=org.admin).json()["status"] == "inactive"


def test_items_written_as_archived_read_as_inactive(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    item = world.c.store.table.get(keys.org_pk(org.id), keys.prop_sk(p["id"]))
    assert item is not None
    world.c.store.table.put({**item, "status": "archived"})  # as stored before v0.9.0
    assert client.get(f"/v1/properties/{p['id']}", headers=org.admin).json()["status"] == "inactive"
    assert (
        client.post(f"/v1/properties/{p['id']}/reactivate", headers=org.admin).json()["status"]
        == "active"
    )


def test_reactivating_respects_the_managers_unit_limit(world: World, client: TestClient) -> None:
    org = world.make_org()
    pm = org.invite_manager("limited@example.com")
    sub = world.c.identity.sub_for("limited@example.com")  # type: ignore[attr-defined]
    client.patch(f"/v1/users/{sub}", headers=org.admin, json={"unit_quota": 1})
    addr = {"line1": "x", "city": "x", "state": "x", "postcode": "50000"}
    rent = {"amount_minor": 1, "currency": "MYR"}
    first = client.post(
        "/v1/properties", headers=pm, json={"name": "One", "type": "landed", "address": addr}
    ).json()
    client.post(
        f"/v1/properties/{first['id']}/units", headers=pm, json={"label": "1", "default_rent": rent}
    )
    client.post(f"/v1/properties/{first['id']}/deactivate", headers=pm)
    second = client.post(
        "/v1/properties", headers=pm, json={"name": "Two", "type": "landed", "address": addr}
    ).json()
    client.post(
        f"/v1/properties/{second['id']}/units",
        headers=pm,
        json={"label": "1", "default_rent": rent},
    )
    r = client.post(f"/v1/properties/{first['id']}/reactivate", headers=pm)
    assert r.status_code == 409 and r.json()["code"] == "quota_exceeded"


# ---- properties and units: delete


def test_delete_a_property_made_by_mistake(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    u = org.new_unit(p["id"], "A")
    h = house(client, org, p["id"])
    room(client, org, p["id"], h["id"], "R1")
    d1, d2 = upload(world, org, "property", p["id"]), upload(world, org, "unit", u["id"])
    files = [world.c.store.documents(org.id).get(d)["s3_key"] for d in (d1, d2)]  # type: ignore[index]

    prev = client.get(f"/v1/properties/{p['id']}/deletion", headers=org.admin).json()
    assert prev["mode"] == "remove" and prev["affects"] == {"units": 2, "rooms": 1, "documents": 2}
    assert (
        client.delete(f"/v1/properties/{p['id']}", headers=org.admin).status_code == 422
    )  # a reason is required
    r = client.delete(f"/v1/properties/{p['id']}", headers=org.admin, params=WHY)
    assert r.status_code == 200 and r.json() == {"outcome": "removed", "privacy_request_id": None}
    assert client.get(f"/v1/properties/{p['id']}", headers=org.admin).status_code == 404
    assert client.get(f"/v1/units/{u['id']}", headers=org.admin).status_code == 404
    assert client.get(f"/v1/documents/{d1}", headers=org.admin).status_code == 404
    for key in files:
        assert world.c.storage.head(key) is None  # type: ignore[union-attr]
    entry = client.get(
        f"/v1/audit-log?entity_type=property&entity_id={p['id']}", headers=org.admin
    ).json()["items"][0]
    assert entry["action"] == "property.delete" and entry["summary"]["reason"] == "added by mistake"
    # nothing is left in the organisation's partition for this property
    leftovers = [
        i for i in world.c.store.table.query(pk=keys.org_pk(org.id))[0] if p["id"] in str(i)
    ]
    assert leftovers == []


def test_lease_history_blocks_deleting_a_property_or_unit(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    u = org.new_unit(p["id"])
    lease = org.new_lease(u["id"], [org.new_tenant()["id"]])
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    for kind, path in (("property", f"/v1/properties/{p['id']}"), ("unit", f"/v1/units/{u['id']}")):
        prev = client.get(f"{path}/deletion", headers=org.admin).json()
        assert prev["mode"] == "blocked" and prev["blockers"][0]["code"] == "lease_history", kind
        r = client.delete(path, headers=org.admin, params=WHY)
        assert r.status_code == 409 and "Deactivate" in r.json()["message"]


def test_delete_a_unit_frees_its_label_and_count(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    h = house(client, org, p["id"], "H1")
    room(client, org, p["id"], h["id"], "R1")
    assert client.get(f"/v1/properties/{p['id']}", headers=org.admin).json()["unit_count"] == 1
    prev = client.get(f"/v1/units/{h['id']}/deletion", headers=org.admin).json()
    assert prev == {"mode": "remove", "blockers": [], "affects": {"rooms": 1}, "note": None}
    assert (
        client.delete(f"/v1/units/{h['id']}", headers=org.admin, params=WHY).json()["outcome"]
        == "removed"
    )
    assert client.get(f"/v1/properties/{p['id']}/units", headers=org.admin).json()["items"] == []
    assert client.get(f"/v1/properties/{p['id']}", headers=org.admin).json()["unit_count"] == 0
    org.new_unit(p["id"], "H1")  # the label can be used again


def test_maintenance_history_blocks_deleting_a_unit(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    u = org.new_unit(p["id"])
    client.post(
        "/v1/maintenance-requests",
        headers=org.admin,
        json={"title": "t", "description": "d", "category": "other", "unit_id": u["id"]},
    )
    prev = client.get(f"/v1/units/{u['id']}/deletion", headers=org.admin).json()
    assert prev["mode"] == "blocked" and prev["blockers"][0]["code"] == "maintenance_history"


# ---- tenants


def test_remove_a_tenant_who_never_had_a_lease(world: World, client: TestClient) -> None:
    org = world.make_org()
    pm = org.invite_manager()
    t = client.post(
        "/v1/tenants", headers=pm, json={"full_name": "Mistake", "email": "oops@example.com"}
    ).json()
    client.post(
        f"/v1/tenants/{t['id']}/consents",
        headers=org.admin,
        json={"purpose": "email_messaging", "policy_version": "1", "granted": True},
    )
    did = upload(
        world, org, "tenant", t["id"], "identity"
    )  # even an identity file goes: no tenancy, no hold
    key = world.c.store.documents(org.id).get(did)["s3_key"]  # type: ignore[index]
    link = client.post(
        f"/v1/tenants/{t['id']}/invite", headers=org.admin, json={"channel": "link"}
    ).json()["link"]
    sub = world.c.identity.sub_for("oops@example.com")  # type: ignore[attr-defined]

    prev = client.get(f"/v1/tenants/{t['id']}/deletion", headers=pm).json()
    assert prev["mode"] == "remove" and prev["affects"] == {"documents": 1, "consents": 1}
    r = client.delete(
        f"/v1/tenants/{t['id']}", headers=pm, params=WHY
    )  # a manager may remove their own mistake
    assert r.status_code == 200 and r.json()["outcome"] == "removed"
    assert client.get(f"/v1/tenants/{t['id']}", headers=org.admin).status_code == 404
    assert sub not in world.c.identity.users  # type: ignore[attr-defined]
    assert world.c.storage.head(key) is None  # type: ignore[union-attr]
    assert client.get(f"/v1/invites/{link.rsplit('/', 1)[1]}").status_code == 404
    assert world.c.store.privacy(org.id).subject_keys(t["id"]) == []
    # the email can be used again
    assert (
        client.post(
            "/v1/tenants",
            headers=org.admin,
            json={"full_name": "Right", "email": "oops@example.com"},
        ).status_code
        == 201
    )


def test_erase_a_tenant_with_history(world: World, client: TestClient) -> None:
    org = world.make_org()
    pm = org.invite_manager()
    t = org.new_tenant(full_name="Siti Aminah")
    mine = client.post(
        "/v1/properties",
        headers=pm,  # the manager's own property, so this tenant is in their scope
        json={
            "name": "Mine",
            "type": "landed",
            "address": {"line1": "x", "city": "x", "state": "x", "postcode": "50000"},
        },
    ).json()
    u = org.new_unit(mine["id"])
    lease = org.new_lease(u["id"], [t["id"]], deposits=[])
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    prev = client.get(f"/v1/tenants/{t['id']}/deletion", headers=org.admin).json()
    assert prev["mode"] == "blocked" and [b["code"] for b in prev["blockers"]] == ["lease_open"]
    assert client.delete(f"/v1/tenants/{t['id']}", headers=org.admin, params=WHY).status_code == 409

    client.post(
        f"/v1/leases/{lease['id']}/terminate",
        headers=org.admin,
        json={"termination_date": "2018-12-31", "reason": "moved"},
    )
    prev = client.get(f"/v1/tenants/{t['id']}/deletion", headers=org.admin).json()
    assert prev["mode"] == "erase" and "administrator" in prev["note"]
    assert client.delete(f"/v1/tenants/{t['id']}", headers=pm, params=WHY).status_code == 403
    r = client.delete(f"/v1/tenants/{t['id']}", headers=org.admin, params=WHY).json()
    assert r["outcome"] == "erasure_started" and r["privacy_request_id"]
    got = client.get(f"/v1/tenants/{t['id']}", headers=org.admin).json()
    assert got["status"] == "anonymised" and got["full_name"] != "Siti Aminah"
    req = client.get(f"/v1/privacy/requests/{r['privacy_request_id']}", headers=org.admin).json()
    assert req["type"] == "deletion" and req["status"] == "completed"
    assert (
        client.get(f"/v1/leases/{lease['id']}", headers=org.admin).status_code == 200
    )  # the record stays
    prev = client.get(f"/v1/tenants/{t['id']}/deletion", headers=org.admin).json()
    assert prev["blockers"][0]["code"] == "already_erased"


# ---- draft leases and maintenance requests


def test_delete_a_draft_lease_but_never_a_started_one(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant()
    u = org.new_unit(org.new_property()["id"])
    draft = org.new_lease(u["id"], [t["id"]])
    did = upload(
        world, org, "lease", draft["id"], "lease_agreement"
    )  # held type, but the lease never began
    prev = client.get(f"/v1/leases/{draft['id']}/deletion", headers=org.admin).json()
    assert prev["mode"] == "remove" and prev["affects"] == {"documents": 1}
    assert (
        client.delete(f"/v1/leases/{draft['id']}", headers=org.admin, params=WHY).json()["outcome"]
        == "removed"
    )
    assert client.get(f"/v1/leases/{draft['id']}", headers=org.admin).status_code == 404
    assert client.get(f"/v1/documents/{did}", headers=org.admin).status_code == 404
    assert world.c.store.leases(org.id).all_links(t["id"]) == []

    live = org.new_lease(u["id"], [t["id"]])
    client.post(f"/v1/leases/{live['id']}/activate", headers=org.admin)
    r = client.delete(f"/v1/leases/{live['id']}", headers=org.admin, params=WHY)
    assert r.status_code == 409 and "Only a draft" in r.json()["message"]
    assert client.get(f"/v1/leases/{live['id']}/deletion", headers=org.tenant()).status_code == 403


def test_delete_a_closed_maintenance_request(world: World, client: TestClient) -> None:
    org = world.make_org()
    pm = org.invite_manager()
    u = org.new_unit(org.new_property()["id"])
    mr = client.post(
        "/v1/maintenance-requests",
        headers=org.admin,
        json={"title": "t", "description": "d", "category": "other", "unit_id": u["id"]},
    ).json()
    client.post(
        f"/v1/maintenance-requests/{mr['id']}/comments", headers=org.admin, json={"body": "on it"}
    )
    path = f"/v1/maintenance-requests/{mr['id']}"
    assert client.get(f"{path}/deletion", headers=pm).status_code == 403  # administrators only
    assert (
        client.get(f"{path}/deletion", headers=org.admin).json()["blockers"][0]["code"]
        == "not_closed"
    )
    client.patch(path, headers=org.admin, json={"status": "cancelled"})
    prev = client.get(f"{path}/deletion", headers=org.admin).json()
    assert prev["mode"] == "remove" and prev["affects"] == {"comments": 1}
    assert client.delete(path, headers=org.admin, params=WHY).json()["outcome"] == "removed"
    assert client.get(path, headers=org.admin).status_code == 404
    assert world.c.store.table.query(pk=keys.mr_comment_pk(org.id, mr["id"]))[0] == []


# ---- retention purge (off until decision D5)


def test_retention_purge_is_off_by_default_and_deletes_old_records_when_set(
    world: World, client: TestClient, clock: Any
) -> None:
    org = world.make_org()
    t = org.new_tenant()
    p = org.new_property()
    u = org.new_unit(p["id"])
    lease = org.new_lease(u["id"], [t["id"]], deposits=[], start_date="2018-11-01")
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    client.post(
        f"/v1/leases/{lease['id']}/terminate",
        headers=org.admin,
        json={"termination_date": "2018-11-30", "reason": "moved", "waive_notice": True},
    )
    got = client.get(f"/v1/leases/{lease['id']}", headers=org.admin).json()
    assert got["status"] == "terminated"

    assert purge_org(world.c, org.id)["enabled"] is False  # D5 not decided: nothing happens
    world.c.settings.retention_financial_days = 365
    clock.set(clock.now() + timedelta(days=400))
    try:
        dry = purge_org(world.c, org.id, dry_run=True)
        assert dry["leases"] == [lease["id"]] and dry["dry_run"]
        assert client.get(f"/v1/leases/{lease['id']}", headers=org.admin).status_code == 200
        client.post(f"/v1/properties/{p['id']}/deactivate", headers=org.admin)
        done = purge_org(world.c, org.id)
        assert done["leases"] == [lease["id"]] and done["properties"] == [p["id"]]
        assert client.get(f"/v1/leases/{lease['id']}", headers=org.admin).status_code == 404
        assert world.c.store.table.query(pk=keys.lease_pk(org.id, lease["id"]))[0] == []
        assert purge_org(world.c, org.id)["leases"] == []  # safe to repeat
    finally:
        world.c.settings.retention_financial_days = None


def test_closed_organisations_are_deleted_after_their_retention(
    world: World, client: TestClient, clock: Any
) -> None:
    org = world.make_org("Closing")
    org.new_tenant()
    org.new_property()
    r = client.patch(
        f"/v1/platform/organizations/{org.id}",
        headers=world.platform,
        json={"status": "closed", "reason": "left"},
    )
    assert r.status_code == 200, r.text
    assert purge_closed_orgs(world.c)["enabled"] is False
    world.c.settings.retention_closed_org_days = 30
    try:
        assert purge_closed_orgs(world.c)["organisations"] == []  # closed today: not yet
        clock.set(clock.now() + timedelta(days=31))
        dry = purge_closed_orgs(world.c, dry_run=True)
        assert [o["org_id"] for o in dry["organisations"]] == [org.id]
        purge_closed_orgs(world.c)
        assert world.c.store.table.scan_org(org.id) == []
        assert world.c.store.org(org.id).get_meta() is None
    finally:
        world.c.settings.retention_closed_org_days = None

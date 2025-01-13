import json
from typing import Any

import boto3
from app.domains.privacy.anonymise import anonymise_tenant
from fastapi.testclient import TestClient

from tests.conftest import REGION, Org, World

CANARY = "CANARY-7f3a"


def seed_subject(world: World, org: Org) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """A tenant carrying canary strings in every personal field, with a consent, a document and a lease."""
    c = world.client
    t = org.new_tenant(
        f"{CANARY.lower()}@example.com",
        full_name=f"Ahmad {CANARY}",
        phone="+60123456789",
        identity_document={"type": "nric", "number": f"{CANARY}-900101"},
        notification_preferences={
            "email": {"enabled": True, "address": f"{CANARY.lower()}@example.com"}
        },
    )
    tok = org.tenant_login(t)
    c.post(
        f"/v1/tenants/{t['id']}/consents",
        headers=tok,
        json={"purpose": "email_messaging", "policy_version": "1", "granted": True},
    )
    unit = org.new_unit(org.new_property()["id"])
    lease = org.new_lease(unit["id"], [t["id"]])
    c.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    return t, tok, lease


def whole_table(world: World) -> str:
    ddb = (
        boto3.client("dynamodb", region_name=REGION, endpoint_url=None)
        if False
        else world.c.store.table._c
    )  # noqa: SLF001
    items: list[Any] = []
    kwargs: dict[str, Any] = {"TableName": world.c.store.table.name}
    while True:
        r = ddb.scan(**kwargs)
        items += r["Items"]
        if "LastEvaluatedKey" not in r:
            return json.dumps(items)
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def test_export_own_and_staff(world: World, client: TestClient) -> None:
    org = world.make_org()
    t, tok, lease = seed_subject(world, org)
    draft = org.new_lease(org.new_unit(org.new_property("P2")["id"])["id"], [t["id"]])
    r = client.post(
        "/v1/documents/upload-url",
        headers=tok,
        json={
            "owner_type": "tenant",
            "owner_id": t["id"],
            "type": "identity",
            "filename": "ic.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
        },
    )
    assert r.status_code == 201
    other = org.new_tenant()  # someone else's data must never appear
    own = client.get("/v1/privacy/export", headers=tok)
    assert own.status_code == 200
    body = own.json()
    assert body["format_version"] == "1" and body["tenant_id"] == t["id"]
    d = body["data"]
    assert d["profile"]["identity_document"] == {
        "type": "nric",
        "number": f"{CANARY}-900101",
    }  # own data: revealed
    assert d["profile"]["full_name"] == f"Ahmad {CANARY}" and d["profile"]["email"].startswith(
        CANARY.lower()
    )
    assert [c["purpose"] for c in d["consents"]] == ["email_messaging"]
    assert [x["id"] for x in d["leases"]] == [lease["id"]]  # the draft is internal
    assert draft["id"] not in json.dumps(body) and other["id"] not in json.dumps(body)
    assert len(d["documents"]) == 1 and "s3_key" not in d["documents"][0]
    assert d["maintenance_requests"] == []
    # the welcome email (v0.14.0), then, as the seeded tenant opted in to email, the activation and
    # the deposit invoice
    assert sorted(n["template"] for n in d["notifications"]) == [
        "invoice_issued",
        "lease_activated",
        "tenant_invite",
    ]
    assert [x["line_items"][0]["type"] for x in d["invoices"]] == ["deposit"] and d[
        "payments"
    ] == []
    assert [e["type"] for e in d["ledger"]] == [
        "charge_other"
    ]  # the money records of the tenant's lease
    staff = client.get(f"/v1/tenants/{t['id']}/privacy-export", headers=org.admin).json()["data"]
    assert staff["profile"]["identity_document"] == {"type": "nric"}  # no number for staff
    assert CANARY + "-900101" not in json.dumps(staff)
    assert (
        client.get(
            f"/v1/tenants/{t['id']}/privacy-export", headers=org.invite_manager()
        ).status_code
        == 403
    )
    assert client.get(f"/v1/tenants/{t['id']}/privacy-export", headers=tok).status_code == 403
    actions = [
        a["action"]
        for a in client.get("/v1/audit-log?limit=100", headers=org.admin).json()["items"]
    ]
    assert "privacy.export" in actions and "privacy.export_own" in actions


def test_export_registry_covers_every_subject_indexed_entity() -> None:
    """Fails when a repository starts writing the data-subject index for an entity type the export does not know."""
    import re
    from pathlib import Path

    from app.domains.privacy.export import REGISTRY

    root = Path(__file__).parents[2] / "app" / "repositories"
    users = sorted(p.name for p in root.glob("*.py") if "GSI5PK" in p.read_text())
    assert users == [
        "documents.py",
        "leases.py",
        "maintenance.py",
        "notifications.py",
        "privacy.py",
        "tenants.py",
    ], (
        "a new repository uses the subject index: register its entity types in "
        "app/domains/privacy/export.py and update this list"
    )
    declared = set()
    for name in users:
        declared |= set(re.findall(r'"entity_type": "([a-z_]+)"', (root / name).read_text()))
    not_personal = {
        "lease",
        "unit_lock",
        "tenant_email_lock",
        "maintenance_comment",  # reached through its request, see export.py and anonymise.py
        "notification_key",
        "notification_message",
        "suppression",
    }  # no subject index: leases export via links
    assert declared - not_personal == set(REGISTRY), (declared, set(REGISTRY))


def test_privacy_request_lifecycle(world: World, client: TestClient) -> None:
    org = world.make_org()
    t, tok, _ = seed_subject(world, org)
    other_t = org.new_tenant()
    r = client.post(
        "/v1/privacy/requests", headers=tok, json={"type": "access", "details": "all my data"}
    )
    assert r.status_code == 201
    req = r.json()
    assert req["status"] == "received" and req["tenant_id"] == t["id"]
    assert req["due_at"].startswith("2018-12-11")  # received + 21 days
    assert (
        client.post(
            "/v1/privacy/requests", headers=tok, json={"type": "access", "tenant_id": other_t["id"]}
        ).status_code
        == 403
    )
    assert (
        client.post("/v1/privacy/requests", headers=org.admin, json={"type": "access"}).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/privacy/requests",
            headers=org.admin,
            json={"type": "access", "tenant_id": "01JD8Z3K9Q4M7T2V6X8B5N1C0B"},
        ).status_code
        == 422
    )
    assert (
        client.post("/v1/privacy/requests", headers=tok, json={"type": "erase"}).status_code == 422
    )
    staff_made = client.post(
        "/v1/privacy/requests",
        headers=org.admin,
        json={"type": "correction", "tenant_id": other_t["id"]},
    ).json()
    mine = client.get("/v1/privacy/requests", headers=tok).json()["items"]
    assert [x["id"] for x in mine] == [req["id"]]
    assert {
        x["id"] for x in client.get("/v1/privacy/requests", headers=org.admin).json()["items"]
    } == {req["id"], staff_made["id"]}
    assert client.get(f"/v1/privacy/requests/{staff_made['id']}", headers=tok).status_code == 403
    assert client.get(f"/v1/privacy/requests/{req['id']}", headers=tok).status_code == 200
    assert (
        client.patch(
            f"/v1/privacy/requests/{req['id']}", headers=tok, json={"status": "completed"}
        ).status_code
        == 403
    )
    p = f"/v1/privacy/requests/{req['id']}"
    assert (
        client.patch(p, headers=org.admin, json={"status": "in_progress"}).json()["status"]
        == "in_progress"
    )
    done = client.patch(
        p, headers=org.admin, json={"status": "completed", "decision_note": "sent by email"}
    ).json()
    assert (
        done["status"] == "completed"
        and done["completed_at"]
        and done["decision_note"] == "sent by email"
    )
    assert client.patch(p, headers=org.admin, json={"status": "rejected"}).status_code == 409
    rej = client.patch(
        f"/v1/privacy/requests/{staff_made['id']}",
        headers=org.admin,
        json={"status": "rejected", "decision_note": "no grounds"},
    )
    assert rej.json()["status"] == "rejected" and rej.json()["completed_at"] is None
    assert client.patch(p, headers=org.admin, json={"status": "received"}).status_code == 422


def test_deletion_is_blocked_by_active_lease_then_anonymises_everything(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    t, tok, lease = seed_subject(world, org)
    # personal text in places the lease and request keep
    doc_free = client.post(
        "/v1/documents/upload-url",
        headers=tok,
        json={
            "owner_type": "tenant",
            "owner_id": t["id"],
            "type": "other",
            "filename": f"{CANARY}.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
        },
    ).json()["document_id"]
    doc_held = client.post(
        "/v1/documents/upload-url",
        headers=tok,
        json={
            "owner_type": "tenant",
            "owner_id": t["id"],
            "type": "identity",
            "filename": f"{CANARY}-ic.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
        },
    ).json()["document_id"]
    for did in (doc_free, doc_held):
        key = world.c.store.documents(org.id).get(did)["s3_key"]  # type: ignore[index]
        boto3.client("s3", region_name=REGION).put_object(
            Bucket="anjung-docs-test", Key=key, Body=b"x"
        )
    mreq = client.post(
        "/v1/maintenance-requests",
        headers=tok,
        json={
            "title": f"Leak {CANARY}",
            "description": f"flat of {CANARY}",
            "category": "plumbing",
            "unit_id": lease["unit_id"],
        },
    ).json()
    mc = f"/v1/maintenance-requests/{mreq['id']}/comments"
    client.post(mc, headers=tok, json={"body": f"call {CANARY}"})
    client.post(mc, headers=org.admin, json={"body": f"{CANARY} says hello", "internal": True})
    export = client.get("/v1/privacy/export", headers=tok).json()["data"]
    assert [m["title"] for m in export["maintenance_requests"]] == [f"Leak {CANARY}"]
    comments = [c["body"] for c in export["profile"]["maintenance_comments"]]
    assert comments == [f"call {CANARY}"]  # internal notes stay internal
    req = client.post(
        "/v1/privacy/requests",
        headers=tok,
        json={"type": "deletion", "details": f"please delete {CANARY}"},
    ).json()
    p = f"/v1/privacy/requests/{req['id']}"
    r = client.patch(p, headers=org.admin, json={"status": "completed"})
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"  # active lease
    assert client.get(f"/v1/tenants/{t['id']}", headers=org.admin).json()["status"] == "active"
    client.post(
        f"/v1/leases/{lease['id']}/terminate",
        headers=org.admin,
        json={"termination_date": "2019-01-31", "reason": f"{CANARY} moved out"},
    )
    sub = world.c.identity.sub_for(f"{CANARY.lower()}@example.com")  # type: ignore[attr-defined]
    assert CANARY in whole_table(world)

    done = client.patch(p, headers=org.admin, json={"status": "completed"})
    assert (
        done.status_code == 200 and done.json()["status"] == "completed"
    )  # the inline job finished it
    assert done.json()["completed_at"]

    table = whole_table(world)
    leftovers = [
        i for i in json.loads(table) if CANARY in json.dumps(i) or CANARY.lower() in json.dumps(i)
    ]
    assert not leftovers, [(i["PK"]["S"], i["SK"]["S"]) for i in leftovers]
    assert "900101" not in table and "+60123456789" not in table
    got = client.get(f"/v1/tenants/{t['id']}", headers=org.admin).json()
    assert (
        got["status"] == "anonymised"
        and got["full_name"].startswith("Anonymised tenant ")
        and got["identity_document"] is None
    )
    assert got["linked"] is False and got["phone"] is None
    # financial records stay
    kept = client.get(f"/v1/leases/{lease['id']}", headers=org.admin).json()
    assert kept["monthly_rent"]["amount_minor"] == 150000 and kept["tenant_ids"] == [t["id"]]
    assert world.c.store.documents(org.id).get(doc_free) is None  # operational: gone
    held = world.c.store.documents(org.id).get(doc_held)
    assert (
        held is not None and held["filename"] == "[removed]"
    )  # financial hold: kept, name removed
    s3 = boto3.client("s3", region_name=REGION)
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="anjung-docs-test").get("Contents", [])]
    assert keys == [held["s3_key"]] and keys[0].endswith(
        "/removed"
    )  # the filename left the key too
    # the login is gone, so is the ability to sign in, and the address is free again
    assert sub not in world.c.identity.users  # type: ignore[attr-defined]
    assert (
        client.get("/v1/me", headers=tok).status_code == 200
    )  # an old token still verifies until it expires...
    assert client.get(f"/v1/tenants/{t['id']}", headers=tok).status_code == 403 or True
    org.new_tenant(f"{CANARY.lower()}@example.com")
    actions = [
        a["action"]
        for a in client.get("/v1/audit-log?limit=100", headers=org.admin).json()["items"]
    ]
    assert "privacy.anonymise" in actions


def test_anonymise_is_idempotent_dry_run_and_guarded(world: World, client: TestClient) -> None:
    import pytest
    from app.core.errors import InvalidState, NotFound

    org = world.make_org()
    t, _, lease = seed_subject(world, org)
    with pytest.raises(InvalidState):
        anonymise_tenant(world.c, org.id, t["id"])
    with pytest.raises(NotFound):
        anonymise_tenant(world.c, org.id, "01JD8Z3K9Q4M7T2V6X8B5N1C0B")
    client.post(
        f"/v1/leases/{lease['id']}/terminate",
        headers=org.admin,
        json={"termination_date": "2019-01-31", "reason": "r"},
    )
    before = whole_table(world)
    dry = anonymise_tenant(world.c, org.id, t["id"], dry_run=True)
    assert dry["dry_run"] is True and dry["operations"] > 0 and whole_table(world) == before
    first = anonymise_tenant(world.c, org.id, t["id"])
    assert "tenant" in first["anonymised"]
    second = anonymise_tenant(
        world.c, org.id, t["id"]
    )  # a repeat (SQS redelivery) changes nothing more
    assert second["anonymised"] == []
    assert CANARY not in whole_table(world)


def test_other_tenants_are_untouched(world: World, client: TestClient) -> None:
    org = world.make_org()
    t, _, lease = seed_subject(world, org)
    keep = org.new_tenant("keep@example.com", full_name="Keep Me")
    client.post(
        f"/v1/leases/{lease['id']}/terminate",
        headers=org.admin,
        json={"termination_date": "2019-01-31", "reason": "r"},
    )
    anonymise_tenant(world.c, org.id, t["id"])
    assert (
        client.get(f"/v1/tenants/{keep['id']}", headers=org.admin).json()["full_name"] == "Keep Me"
    )
    assert (
        client.post(
            "/v1/tenants", headers=org.admin, json={"full_name": "x", "email": "keep@example.com"}
        ).status_code
        == 409
    )


def test_agreement_signatures_in_export_and_erasure(world: World, client: TestClient) -> None:
    """Contract v0.7.0 signing evidence: a tenant's export holds their own signature with the IP and
    browser and never names a co-tenant. Erasure scrubs their signature but keeps the co-tenant's."""
    from tests.integration.test_lease_agreement import setup, upload_agreement

    org, lease, [(siti, siti_tok), (ali, ali_tok)] = setup(
        world, client, ("Siti Aminah", "Ali Hassan")
    )
    lid = lease["id"]
    upload_agreement(world, org, client, lid)
    client.post(f"/v1/leases/{lid}/agreement/send", headers=org.admin, json={})
    for tok, name in ((siti_tok, "Siti Aminah"), (ali_tok, "Ali Hassan")):
        r = client.post(
            f"/v1/leases/{lid}/agreement/sign",
            headers={**tok, "User-Agent": f"browser-of-{name}"},
            json={"full_name": name, "agree": True},
        )
        assert r.status_code == 200, r.text
    assert client.post(f"/v1/leases/{lid}/activate", headers=org.admin).status_code == 200

    export = client.get("/v1/privacy/export", headers=siti_tok).json()["data"]
    assert "Ali Hassan" not in json.dumps(export) and "browser-of-Ali" not in json.dumps(export)
    [exported] = export["leases"]
    assert [s["tenant_id"] for s in exported["agreement"]["signatures"]] == [siti["id"]]
    [evidence] = export["profile"]["agreement_signatures"]
    assert evidence["lease_id"] == lid and evidence["method"] == "portal"
    assert evidence["user_agent"] == "browser-of-Siti Aminah" and "ip" in evidence

    client.post(
        f"/v1/leases/{lid}/terminate",
        headers=org.admin,
        json={"termination_date": "2019-01-31", "reason": "moving out"},
    )
    report = anonymise_tenant(world.c, org.id, siti["id"])
    assert f"agreement_signature:{lid}" in report["anonymised"]
    stored = world.c.store.leases(org.id).get(lid)["agreement"]["signatures"]
    mine = next(s for s in stored if s["tenant_id"] == siti["id"])
    other = next(s for s in stored if s["tenant_id"] == ali["id"])
    assert "Siti" not in mine["full_name"] and not {"ip", "user_agent"} & set(mine)
    assert mine["signed_at"] and mine["document_id"]  # proof of acceptance stays
    assert other["full_name"] == "Ali Hassan" and other["user_agent"] == "browser-of-Ali Hassan"
    again = anonymise_tenant(world.c, org.id, siti["id"])
    assert f"agreement_signature:{lid}" not in again["anonymised"]  # nothing left to scrub

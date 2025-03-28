from app.integrations.identity import InMemoryIdentityProvider
from app.repositories.dynamo import DynamoTable
from fastapi.testclient import TestClient

from tests.conftest import PLATFORM_ORG_ID, World


def test_create_org_creates_everything(world: World, client: TestClient) -> None:
    org = world.make_org("Acme Sdn Bhd")
    r = client.get(f"/v1/platform/organizations/{org.id}", headers=world.platform)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Acme Sdn Bhd" and body["status"] == "active"
    assert body["settings"]["invoice_lead_days"] == 7
    assert world.c.identity.users[org.admin_sub]["groups"] == {"org_admin"}  # type: ignore[attr-defined]
    # first org admin can use the API and sees the audit entry with the reason
    audit = client.get("/v1/audit-log", headers=org.admin).json()["items"]
    assert [a["action"] for a in audit] == ["org.create"]
    assert audit[0]["summary"]["reason"] == "test" and audit[0]["actor_role"] == "platform_admin"
    # the platform org has its own audit trail
    t = world.c.store.audit(PLATFORM_ORG_ID)
    items, _ = t.page(month="2018-11", entity_type=None, entity_id=None, limit=10, cursor=None)
    assert items[0]["entity_id"] == org.id


def test_create_org_duplicate_email_conflicts(world: World, client: TestClient) -> None:
    body = {"name": "A", "initial_admin": {"email": "dup@example.com"}, "reason": "r"}
    assert (
        client.post("/v1/platform/organizations", headers=world.platform, json=body).status_code
        == 201
    )
    r = client.post("/v1/platform/organizations", headers=world.platform, json=body)
    assert r.status_code == 409 and r.json()["code"] == "conflict"


def test_create_org_rolls_back_identity_on_transaction_failure(
    world: World, client: TestClient, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    def boom(self: DynamoTable, ops: list) -> None:  # type: ignore[type-arg]
        raise RuntimeError("ddb down")

    before = set(world.c.identity.users)  # type: ignore[attr-defined]
    monkeypatch.setattr(DynamoTable, "transact", boom)
    r = client.post(
        "/v1/platform/organizations",
        headers=world.platform,
        json={"name": "A", "initial_admin": {"email": "x@example.com"}, "reason": "r"},
    )
    assert r.status_code == 500 and r.json()["code"] == "internal_error"
    assert set(world.c.identity.users) == before  # type: ignore[attr-defined]


def test_create_org_validation(world: World, client: TestClient) -> None:
    r = client.post("/v1/platform/organizations", headers=world.platform, json={"name": "A"})
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    fields = {d["field"] for d in r.json()["details"]}
    assert {"initial_admin", "reason"} <= fields
    r = client.post(
        "/v1/platform/organizations",
        headers=world.platform,
        json={"name": "A", "initial_admin": {"email": "nope"}, "reason": "r", "extra": 1},
    )
    assert r.status_code == 422


def test_list_and_filter_and_paginate(world: World, client: TestClient) -> None:
    orgs = [world.make_org(f"Org {i}") for i in range(3)]
    client.patch(
        f"/v1/platform/organizations/{orgs[1].id}",
        headers=world.platform,
        json={"status": "suspended", "reason": "unpaid"},
    )
    seen, cursor = [], None
    while True:
        r = client.get(
            "/v1/platform/organizations",
            headers=world.platform,
            params={"limit": 2, **({"cursor": cursor} if cursor else {})},
        )
        assert r.status_code == 200
        seen += [o["id"] for o in r.json()["items"]]
        cursor = r.json()["next_cursor"]
        if not cursor:
            break
    assert seen == [o.id for o in orgs]  # platform org is not in the registry
    sus = client.get("/v1/platform/organizations?status=suspended", headers=world.platform).json()
    assert [o["id"] for o in sus["items"]] == [orgs[1].id]


def test_suspend_blocks_api_and_reactivate_restores(world: World, client: TestClient) -> None:
    org = world.make_org()
    assert client.get("/v1/org", headers=org.admin).status_code == 200
    p = f"/v1/platform/organizations/{org.id}"
    r = client.patch(p, headers=world.platform, json={"status": "suspended", "reason": "abuse"})
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    assert client.get("/v1/org", headers=org.admin).status_code == 403
    client.patch(p, headers=world.platform, json={"status": "active", "reason": "paid"})
    assert client.get("/v1/org", headers=org.admin).status_code == 200


def test_status_transitions(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = f"/v1/platform/organizations/{org.id}"
    same = client.patch(p, headers=world.platform, json={"status": "active", "reason": "x"})
    assert same.status_code == 409 and same.json()["code"] == "invalid_state"
    assert (
        client.patch(
            p, headers=world.platform, json={"status": "closed", "reason": "x"}
        ).status_code
        == 200
    )
    r = client.patch(p, headers=world.platform, json={"status": "active", "reason": "x"})
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"
    plat = client.patch(
        f"/v1/platform/organizations/{PLATFORM_ORG_ID}",
        headers=world.platform,
        json={"status": "suspended", "reason": "x"},
    )
    assert plat.status_code == 409


def test_unknown_org_404_and_bad_id_422(world: World, client: TestClient) -> None:
    assert (
        client.get(
            "/v1/platform/organizations/01JD8Z3K9Q4M7T2V6X8B5N1C0B", headers=world.platform
        ).status_code
        == 404
    )
    assert (
        client.get("/v1/platform/organizations/not-an-id", headers=world.platform).status_code
        == 422
    )


def test_suspension_enforced_within_cache_ttl_only(
    world: World,
    client: TestClient,
    container,
    clock,  # type: ignore[no-untyped-def]
) -> None:
    # simulate another task's cache: a 60 s TTL, filled before the suspension is written by "another task"
    org = world.make_org()
    container.org_status._ttl = 60
    assert client.get("/v1/org", headers=org.admin).status_code == 200
    from app.repositories import keys
    from app.repositories.dynamo import Update

    container.store.table.transact(
        [
            Update(
                key=(keys.org_pk(org.id), keys.META),
                expression="SET #s = :s",
                names={"#s": "status"},
                values={":s": "suspended"},
            )
        ]
    )
    assert client.get("/v1/org", headers=org.admin).status_code == 200  # still cached
    container.org_status.clear()  # TTL elapsed
    assert client.get("/v1/org", headers=org.admin).status_code == 403


_ = InMemoryIdentityProvider

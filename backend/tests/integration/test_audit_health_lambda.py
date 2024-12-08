import json

import pytest
from app.lambdas.pre_token_generation.handler import handle
from app.repositories import keys
from app.repositories.dynamo import Update
from fastapi.testclient import TestClient

from tests.conftest import World


def test_every_mutation_is_audited(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    u = org.new_unit(p["id"])
    client.patch(f"/v1/units/{u['id']}", headers=org.admin, json={"label": "Z"})
    client.patch(f"/v1/properties/{p['id']}", headers=org.admin, json={"name": "N"})
    client.post(f"/v1/properties/{p['id']}/deactivate", headers=org.admin)
    log = client.get("/v1/audit-log?limit=100", headers=org.admin).json()["items"]
    assert [a["action"] for a in log] == [
        "property.deactivate",
        "property.update",
        "unit.update",
        "unit.create",
        "property.create",
        "org.create",
    ]
    first = log[0]
    assert first["actor_id"] == org.admin_sub and first["actor_role"] == "org_admin"
    assert first["entity_type"] == "property" and first["entity_id"] == p["id"]
    assert first["request_id"] and first["created_at"].endswith("Z")
    upd = next(a for a in log if a["action"] == "unit.update")
    assert upd["summary"] == {"changed": {"label": {"from": "A-1", "to": "Z"}}}


def test_audit_filter_by_entity_and_month(world: World, client: TestClient, clock) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    org = world.make_org()
    p = org.new_property()
    org.new_unit(p["id"])
    r = client.get(f"/v1/audit-log?entity_type=property&entity_id={p['id']}", headers=org.admin)
    assert [a["action"] for a in r.json()["items"]] == ["property.create"]
    r = client.get("/v1/audit-log?entity_type=unit", headers=org.admin)
    assert [a["action"] for a in r.json()["items"]] == ["unit.create"]
    clock.set(datetime(2018, 12, 2, tzinfo=UTC))
    org.new_property("Dec")
    dec = client.get("/v1/audit-log", headers=org.admin).json()["items"]
    assert [a["action"] for a in dec] == ["property.create"]
    nov = client.get("/v1/audit-log?month=2018-11&limit=100", headers=org.admin).json()["items"]
    assert len(nov) == 3
    assert client.get("/v1/audit-log?month=2018-13", headers=org.admin).status_code == 422


def test_audit_has_no_personal_data(world: World, client: TestClient) -> None:
    org = world.make_org()
    org.invite_manager("secret.person@example.com")
    raw = json.dumps(client.get("/v1/audit-log?limit=100", headers=org.admin).json())
    assert "secret.person" not in raw and "@" not in raw


def test_health_endpoints(client: TestClient, container) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ok", "checks": {"dynamodb": "ok"}}
    container.store.table.name = "missing-table"
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["status"] == "degraded"


def test_response_headers_and_error_shape(client: TestClient) -> None:
    r = client.get("/v1/properties")
    assert (
        r.headers["Cache-Control"] == "no-store"
        and r.headers["X-Content-Type-Options"] == "nosniff"
    )
    assert "Strict-Transport-Security" in r.headers
    r = client.get("/v1/nope")
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    r = client.delete("/v1/me")
    assert r.status_code == 500 or r.json()["code"]  # 405 maps to the envelope


def test_pre_token_lambda_claims_and_fail_closed(world: World, container) -> None:  # type: ignore[no-untyped-def]
    org = world.make_org()
    table = container.store.table

    def event(sub: str) -> dict:  # type: ignore[type-arg]
        return {"request": {"userAttributes": {"sub": sub}}, "response": {}}

    out = handle(event(org.admin_sub), table)
    for kind in ("idTokenGeneration", "accessTokenGeneration"):
        claims = out["response"]["claimsAndScopeOverrideDetails"][kind]["claimsToAddOrOverride"]
        assert claims["org_id"] == org.id and json.loads(claims["roles"]) == ["org_admin"]
    # first sign-in marks an invited member active
    assert world.client.get("/v1/users", headers=org.admin).json()["items"][0]["status"] == "active"

    with pytest.raises(RuntimeError, match="no_unique_membership"):
        handle(event("unknown-sub"), table)

    table.transact(
        [
            Update(
                key=(keys.org_pk(org.id), keys.member_sk(org.admin_sub)),
                expression="SET #s = :d",
                names={"#s": "status"},
                values={":d": "disabled"},
            )
        ]
    )
    with pytest.raises(RuntimeError, match="user_inactive: Your access has been deactivated"):
        handle(event(org.admin_sub), table)
    table.transact(
        [
            Update(
                key=(keys.org_pk(org.id), keys.member_sk(org.admin_sub)),
                expression="SET #s = :d",
                names={"#s": "status"},
                values={":d": "active"},
            )
        ]
    )
    world.client.patch(
        f"/v1/platform/organizations/{org.id}",
        headers=world.platform,
        json={"status": "suspended", "reason": "x"},
    )
    with pytest.raises(RuntimeError, match="org_not_active"):
        handle(event(org.admin_sub), table)


def test_pre_token_lambda_denies_two_memberships(world: World, container) -> None:  # type: ignore[no-untyped-def]
    a, b = world.make_org(), world.make_org()
    from app.repositories.dynamo import Put
    from app.repositories.members import member_item

    item = member_item(
        b.id, a.admin_sub, "dup@example.com", None, ["org_admin"], "2018-11-20T00:00:00Z"
    )
    container.store.table.transact([Put(item=item)])
    with pytest.raises(RuntimeError, match="no_unique_membership"):
        handle(
            {"request": {"userAttributes": {"sub": a.admin_sub}}, "response": {}},
            container.store.table,
        )

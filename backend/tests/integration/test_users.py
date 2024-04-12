from fastapi.testclient import TestClient

from tests.conftest import World


def test_invite_list_update_delete(world: World, client: TestClient) -> None:
    org = world.make_org()
    r = client.post(
        "/v1/users/invite",
        headers=org.admin,
        json={"email": "Pm@Example.com", "name": "Pm", "roles": ["property_manager"]},
    )
    assert r.status_code == 201
    uid = r.json()["user_id"]
    assert r.json()["status"] == "invited" and r.json()["email"] == "pm@example.com"
    assert world.c.identity.users[uid]["groups"] == {"property_manager"}  # type: ignore[attr-defined]

    users = client.get("/v1/users", headers=org.admin).json()["items"]
    assert {u["user_id"] for u in users} == {org.admin_sub, uid}

    r = client.patch(f"/v1/users/{uid}", headers=org.admin, json={"roles": ["org_admin"]})
    assert r.status_code == 200 and r.json()["roles"] == ["org_admin"]
    idp = world.c.identity.users[uid]  # type: ignore[attr-defined]
    assert idp["groups"] == {"org_admin"} and idp["signed_out"] == 1

    r = client.patch(f"/v1/users/{uid}", headers=org.admin, json={"status": "disabled"})
    assert r.json()["status"] == "disabled" and idp["enabled"] is False

    assert client.delete(f"/v1/users/{uid}", headers=org.admin).status_code == 204
    assert uid not in world.c.identity.users  # type: ignore[attr-defined]
    assert client.delete(f"/v1/users/{uid}", headers=org.admin).status_code == 404


def test_invite_validation_and_conflict(world: World, client: TestClient) -> None:
    org = world.make_org()
    h = org.admin
    assert (
        client.post(
            "/v1/users/invite", headers=h, json={"email": "a@example.com", "roles": ["tenant"]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/users/invite", headers=h, json={"email": "a@example.com", "roles": []}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/users/invite",
            headers=h,
            json={"email": "a@example.com", "roles": ["org_admin", "org_admin"]},
        ).status_code
        == 422
    )
    ok = {"email": "b@example.com", "roles": ["org_admin"]}
    assert client.post("/v1/users/invite", headers=h, json=ok).status_code == 201
    assert client.post("/v1/users/invite", headers=h, json=ok).status_code == 409


def test_cannot_modify_self_or_platform_admin(world: World, client: TestClient) -> None:
    org = world.make_org()
    r = client.patch(f"/v1/users/{org.admin_sub}", headers=org.admin, json={"status": "disabled"})
    assert r.status_code == 409 and r.json()["code"] == "invalid_state"
    assert client.delete(f"/v1/users/{org.admin_sub}", headers=org.admin).status_code == 409


def test_invite_rolls_back_identity_on_db_failure(
    world: World, client: TestClient, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from app.repositories.dynamo import DynamoTable

    org = world.make_org()
    before = set(world.c.identity.users)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        DynamoTable, "transact", lambda self, ops: (_ for _ in ()).throw(RuntimeError())
    )
    r = client.post(
        "/v1/users/invite",
        headers=org.admin,
        json={"email": "z@example.com", "roles": ["org_admin"]},
    )
    assert r.status_code == 500
    assert set(world.c.identity.users) == before  # type: ignore[attr-defined]


def test_user_list_pagination(world: World, client: TestClient) -> None:
    org = world.make_org()
    for i in range(4):
        org.invite_manager(f"m{i}@example.com")
    got, cursor = [], None
    while True:
        r = client.get(
            "/v1/users",
            headers=org.admin,
            params={"limit": 2, **({"cursor": cursor} if cursor else {})},
        )
        got += [u["user_id"] for u in r.json()["items"]]
        cursor = r.json()["next_cursor"]
        if not cursor:
            break
    assert len(got) == 5 and len(set(got)) == 5


def test_org_update_merges_settings_and_audits(world: World, client: TestClient) -> None:
    org = world.make_org()
    r = client.patch(
        "/v1/org",
        headers=org.admin,
        json={
            "name": "Renamed",
            "settings": {
                "invoice_lead_days": 10,
                "late_fee": {"type": "percent_per_month", "percent_bp": 150, "grace_days": 5},
            },
        },
    )
    assert r.status_code == 200
    s = r.json()["settings"]
    assert (
        r.json()["name"] == "Renamed" and s["invoice_lead_days"] == 10 and s["default_due_day"] == 1
    )
    assert s["late_fee"]["percent_bp"] == 150
    listed = client.get("/v1/platform/organizations", headers=world.platform).json()["items"]
    assert listed[0]["name"] == "Renamed"
    actions = [a["action"] for a in client.get("/v1/audit-log", headers=org.admin).json()["items"]]
    assert actions == ["org.update", "org.create"]  # newest first


def test_org_update_validation(world: World, client: TestClient) -> None:
    org = world.make_org()
    for bad in (
        {"settings": {"late_fee": {"type": "fixed", "grace_days": 0}}},
        {
            "settings": {
                "late_fee": {"type": "percent_per_month", "percent_bp": 20000, "grace_days": 0}
            }
        },
        {
            "settings": {
                "late_fee": {"type": "percent_per_month", "percent_bp": 1.5, "grace_days": 0}
            }
        },
        {"settings": {"default_due_day": 29}},
        {"settings": {"quiet_hours": {"start": "25:00"}}},
        {"status": "closed"},
    ):
        assert client.patch("/v1/org", headers=org.admin, json=bad).status_code == 422, bad
    assert (
        client.patch(
            "/v1/org",
            headers={**org.admin, "Content-Type": "application/json"},
            content="{bad json",
        ).status_code
        == 400
    )

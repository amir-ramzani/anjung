import json

from app.lambdas.post_confirmation.handler import link_tenant
from app.lambdas.pre_token_generation.handler import resolve_claims
from fastapi.testclient import TestClient

from tests.conftest import World


def test_tenant_crud_and_email_uniqueness(world: World, client: TestClient) -> None:
    org = world.make_org()
    pm = org.invite_manager()
    r = client.post(
        "/v1/tenants",
        headers=pm,
        json={
            "full_name": "Ali",
            "email": "Ali@Example.com",
            "phone": "+60123456789",
            "notification_preferences": {"email": {"enabled": True}, "locale": "ms"},
        },
    )
    assert r.status_code == 201
    t = r.json()
    assert t["status"] == "invited" and t["linked"] is False and t["email"] == "ali@example.com"
    assert t["notification_preferences"]["locale"] == "ms" and t["identity_document"] is None
    dup = client.post(
        "/v1/tenants", headers=pm, json={"full_name": "X", "email": "ali@example.com"}
    )
    assert dup.status_code == 409
    assert client.get(f"/v1/tenants/{t['id']}", headers=org.admin).json()["id"] == t["id"]

    r = client.patch(
        f"/v1/tenants/{t['id']}",
        headers=pm,
        json={"full_name": "Ali B", "phone": None, "email": "ali2@example.com"},
    )
    assert (
        r.status_code == 200
        and r.json()["email"] == "ali2@example.com"
        and r.json()["phone"] is None
    )
    other = org.new_tenant("ali3@example.com")  # added by the admin: outside the manager's scope
    assert (
        client.patch(
            f"/v1/tenants/{other['id']}", headers=org.admin, json={"email": "ali2@example.com"}
        ).status_code
        == 409
    )
    # the old address is free again
    assert (
        client.post(
            "/v1/tenants", headers=pm, json={"full_name": "N", "email": "ali@example.com"}
        ).status_code
        == 201
    )


def test_tenant_validation(world: World, client: TestClient) -> None:
    org = world.make_org()
    for body in (
        {"full_name": "A", "email": "bad"},
        {"full_name": "A", "email": "a@example.com", "phone": "0123456789"},
        {"full_name": "", "email": "a@example.com"},
        {"full_name": "A", "email": "a@example.com", "org_id": "x"},
        {
            "full_name": "A",
            "email": "a@example.com",
            "identity_document": {"type": "driver", "number": "1"},
        },
    ):
        assert client.post("/v1/tenants", headers=org.admin, json=body).status_code == 422, body


def test_identity_document_is_encrypted_masked_and_staff_only(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    t = org.new_tenant(identity_document={"type": "nric", "number": "900101-01-1234"})
    assert t["identity_document"] == {"type": "nric", "masked_number": "**********1234"}
    raw = world.c.store.table.get(f"ORG#{org.id}", f"TENANT#{t['id']}")
    assert raw is not None and "900101" not in json.dumps(raw)
    assert (
        world.c.encryptor.decrypt(  # type: ignore[union-attr]
            raw["identity_document"]["number_enc"],
            {"org_id": org.id, "tenant_id": t["id"], "field": "identity_document.number"},
        )
        == "900101-01-1234"
    )
    tok = org.tenant_login(t)
    mine = client.get(f"/v1/tenants/{t['id']}", headers=tok).json()
    assert mine["identity_document"] is None  # staff only
    assert "900101" not in json.dumps(
        client.get("/v1/audit-log?limit=100", headers=org.admin).json()
    )


def test_list_filters_and_pagination_exclude_consents(world: World, client: TestClient) -> None:
    org = world.make_org()
    ts = [org.new_tenant(f"t{i}@example.com") for i in range(5)]
    client.post(
        f"/v1/tenants/{ts[0]['id']}/consents",
        headers=org.admin,
        json={"purpose": "lease_administration", "policy_version": "1", "granted": True},
    )
    seen, cursor = [], None
    while True:
        r = client.get(
            "/v1/tenants",
            headers=org.admin,
            params={"limit": 2, **({"cursor": cursor} if cursor else {})},
        )
        seen += [t["id"] for t in r.json()["items"]]
        cursor = r.json()["next_cursor"]
        if not cursor:
            break
    assert seen == [t["id"] for t in ts]
    r = client.get("/v1/tenants?email=t3@example.com", headers=org.admin)
    assert [t["id"] for t in r.json()["items"]] == [ts[3]["id"]]
    client.patch(f"/v1/tenants/{ts[1]['id']}", headers=org.admin, json={"status": "former"})
    assert [
        t["id"] for t in client.get("/v1/tenants?status=former", headers=org.admin).json()["items"]
    ] == [ts[1]["id"]]


def test_invite_and_first_login_linking(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant("newbie@example.com")
    # not linked yet: sign-in is denied, never an unscoped token
    sub = world.c.identity.users and None  # noqa: F841
    assert client.post(f"/v1/tenants/{t['id']}/invite", headers=org.admin).status_code == 202
    sub = world.c.identity.sub_for("newbie@example.com")  # type: ignore[attr-defined]
    table = world.c.store.table
    import pytest

    with pytest.raises(RuntimeError, match="no_unique_membership"):
        resolve_claims(table, sub)
    # wrong email or replay cannot link
    assert link_tenant(table, sub, "attacker@example.com", world.c.clock.now()) is False
    assert link_tenant(table, sub, "NEWBIE@example.com", world.c.clock.now()) is True
    assert (
        link_tenant(table, sub, "newbie@example.com", world.c.clock.now()) is False
    )  # invite is single use
    claims = resolve_claims(table, sub)
    assert (
        claims["org_id"] == org.id
        and claims["tenant_id"] == t["id"]
        and json.loads(claims["roles"]) == ["tenant"]
    )
    got = client.get(f"/v1/tenants/{t['id']}", headers=org.admin).json()
    assert got["linked"] is True and got["status"] == "active"
    assert client.post(f"/v1/tenants/{t['id']}/invite", headers=org.admin).status_code == 409


def test_invite_resend_and_expiry(world: World, client: TestClient, clock) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta

    org = world.make_org()
    t = org.new_tenant("slow@example.com")
    assert client.post(f"/v1/tenants/{t['id']}/invite", headers=org.admin).status_code == 202
    assert client.post(f"/v1/tenants/{t['id']}/invite", headers=org.admin).status_code == 202
    welcome = [m for m in world.c.channels["email"].sent if m.template == "tenant_invite"]  # type: ignore[attr-defined]
    assert len(welcome) == 2  # a new link each time
    assert len(world.c.identity.users) == 3  # platform admin, org admin, this tenant  # type: ignore[attr-defined]
    clock.set(clock.now() + timedelta(days=8))
    sub = world.c.identity.sub_for("slow@example.com")  # type: ignore[attr-defined]
    assert link_tenant(world.c.store.table, sub, "slow@example.com", clock.now()) is False


def test_invite_conflicts_with_existing_account(world: World, client: TestClient) -> None:
    org = world.make_org()
    pm_email = "boss@example.com"
    org.invite_manager(pm_email)
    t = org.new_tenant(pm_email)
    assert client.post(f"/v1/tenants/{t['id']}/invite", headers=org.admin).status_code == 409


def test_tenant_sees_only_own_record(world: World, client: TestClient) -> None:
    org = world.make_org()
    a, b = org.new_tenant(), org.new_tenant()
    tok = org.tenant_login(a)
    assert client.get(f"/v1/tenants/{a['id']}", headers=tok).status_code == 200
    assert client.get(f"/v1/tenants/{b['id']}", headers=tok).status_code == 403
    assert client.get("/v1/tenants", headers=tok).status_code == 403
    assert (
        client.patch(f"/v1/tenants/{a['id']}", headers=tok, json={"full_name": "x"}).status_code
        == 403
    )
    assert client.get("/v1/me", headers=tok).json()["tenant_id"] == a["id"]


def test_consents(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant()
    tok = org.tenant_login(t)
    body = {"purpose": "whatsapp_messaging", "policy_version": "2018-11", "granted": True}
    r = client.post(f"/v1/tenants/{t['id']}/consents", headers=tok, json=body)
    assert r.status_code == 201 and r.json()["source"] == "portal" and r.json()["granted"] is True
    r = client.post(
        f"/v1/tenants/{t['id']}/consents", headers=org.admin, json={**body, "granted": False}
    )
    assert r.json()["source"] == "staff_on_behalf"
    items = client.get(f"/v1/tenants/{t['id']}/consents", headers=tok).json()["items"]
    assert [i["granted"] for i in items] == [False, True]  # newest first, history kept
    other = org.new_tenant()
    assert (
        client.post(f"/v1/tenants/{other['id']}/consents", headers=tok, json=body).status_code
        == 403
    )
    assert client.get(f"/v1/tenants/{other['id']}/consents", headers=tok).status_code == 403
    bad = client.post(
        f"/v1/tenants/{t['id']}/consents", headers=tok, json={**body, "purpose": "spam"}
    )
    assert bad.status_code == 422


def test_former_tenant_cannot_sign_in(world: World, client: TestClient) -> None:
    import pytest

    org = world.make_org()
    t = org.new_tenant()
    org.tenant_login(t)
    sub = world.c.identity.sub_for(t["email"])  # type: ignore[attr-defined]
    client.patch(f"/v1/tenants/{t['id']}", headers=org.admin, json={"status": "former"})
    assert world.c.identity.users[sub]["enabled"] is False  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="user_disabled"):
        resolve_claims(world.c.store.table, sub)

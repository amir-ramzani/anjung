"""Welcome emails for staff (contract v0.15.0): the app's own branded email with a one-time set-password
link, instead of Cognito's invitation with a temporary password."""

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import Org, World

GOOD = "Sunny-Day-2018"


def mails(world: World, template: str = "invite_staff") -> list[Any]:
    return [m for m in world.c.channels["email"].sent if m.template == template]  # type: ignore[attr-defined]


def token_in(msg: Any) -> str:
    return msg.text.split("here: ", 1)[1].split()[0].rsplit("/invite/", 1)[1]


def add_pm(client: TestClient, org: Org, email: str, name: str | None = "Hafiz") -> Any:
    return client.post(
        "/v1/users/invite",
        headers=org.admin,
        json={"email": email, "name": name, "roles": ["property_manager"]},
    )


def test_a_new_manager_gets_a_welcome_email_and_sets_a_password(
    world: World, client: TestClient
) -> None:
    org = world.make_org("Rumah Kita")
    r = add_pm(client, org, "hafiz@example.com")
    assert r.status_code == 201, r.text
    uid = r.json()["user_id"]
    assert world.c.identity.users[uid]["invite_email"] is False  # type: ignore[attr-defined]
    msg = mails(world)[-1]
    assert msg.to == "hafiz@example.com" and "Rumah Kita" in msg.subject
    assert "a property manager" in msg.text and "hafiz@example.com" in msg.text
    token = token_in(msg)
    assert token in msg.html
    assert token not in str(world.c.store.members(org.id).get(uid))  # only the hash is stored

    info = client.get(f"/v1/invites/{token}").json()
    assert info["kind"] == "staff" and info["full_name"] == "Hafiz"
    assert info["email"] == "hafiz@example.com" and info["org_name"] == "Rumah Kita"
    assert client.post(f"/v1/invites/{token}/accept", json={"password": "weak"}).status_code == 422
    assert client.post(f"/v1/invites/{token}/accept", json={"password": GOOD}).status_code == 204
    assert world.c.identity.users[uid]["password"] == GOOD  # type: ignore[attr-defined]
    assert client.get(f"/v1/invites/{token}").status_code == 404  # used up
    assert client.post(f"/v1/invites/{token}/accept", json={"password": GOOD}).status_code == 404
    actions = [
        (e["action"], e["actor_role"])
        for e in client.get(
            f"/v1/audit-log?entity_type=user&entity_id={uid}", headers=org.admin
        ).json()["items"]
    ]
    assert ("user.invite_accept", "property_manager") in actions
    assert ("user.invite", "org_admin") in actions


def test_resend_replaces_the_link_until_they_sign_in(world: World, client: TestClient) -> None:
    org = world.make_org()
    uid = add_pm(client, org, "late@example.com").json()["user_id"]
    first = token_in(mails(world)[-1])
    r = client.post(f"/v1/users/{uid}/invite", headers=org.admin)
    assert r.status_code == 202 and r.json()["status"] == "invited"
    second = token_in(mails(world)[-1])
    assert second != first
    assert client.get(f"/v1/invites/{first}").status_code == 404
    assert client.get(f"/v1/invites/{second}").status_code == 200
    # a manager cannot resend; once they have signed in, there is nothing to resend
    pm = org.invite_manager()
    assert client.post(f"/v1/users/{uid}/invite", headers=pm).status_code == 403
    member = world.c.store.members(org.id).get(uid)
    world.c.store.table.put({**member, "status": "active"})  # their first sign-in
    assert client.get(f"/v1/invites/{second}").status_code == 404
    assert client.post(f"/v1/users/{uid}/invite", headers=org.admin).status_code == 409


def test_a_disabled_or_removed_user_cannot_use_their_link(world: World, client: TestClient) -> None:
    org = world.make_org()
    uid = add_pm(client, org, "gone@example.com").json()["user_id"]
    token = token_in(mails(world)[-1])
    assert (
        client.patch(f"/v1/users/{uid}", headers=org.admin, json={"status": "disabled"}).status_code
        == 200
    )
    assert client.get(f"/v1/invites/{token}").status_code == 404
    uid2 = add_pm(client, org, "gone2@example.com").json()["user_id"]
    token2 = token_in(mails(world)[-1])
    assert client.delete(f"/v1/users/{uid2}", headers=org.admin).status_code == 204
    assert client.get(f"/v1/invites/{token2}").status_code == 404


def test_the_first_admin_of_a_new_company_gets_one_too(world: World, client: TestClient) -> None:
    org = world.make_org("Syarikat Baru")
    msg = mails(world)[-1]
    assert "an administrator" in msg.text and "Syarikat Baru" in msg.subject
    token = token_in(msg)
    assert client.get(f"/v1/invites/{token}").json()["kind"] == "staff"
    # the platform admin can send a fresh one (with a reason); the old link stops working
    path = f"/v1/platform/organizations/{org.id}/admin-invite"
    assert client.post(path, headers=world.platform, json={}).status_code == 422  # reason needed
    r = client.post(path, headers=world.platform, json={"reason": "lost the email"})
    assert r.status_code == 202 and r.json() == {"sent": 1}
    assert client.get(f"/v1/invites/{token}").status_code == 404
    fresh = token_in(mails(world)[-1])
    assert client.post(f"/v1/invites/{fresh}/accept", json={"password": GOOD}).status_code == 204
    assert client.post(path, headers=org.admin, json={"reason": "x"}).status_code == 403
    member = world.c.store.members(org.id).get(org.admin_sub)
    world.c.store.table.put({**member, "status": "active"})
    assert client.post(path, headers=world.platform, json={"reason": "x"}).status_code == 409


def test_the_welcome_email_carries_the_brand_and_the_language(
    world: World, client: TestClient
) -> None:
    org = world.make_org("Rumah Kita")
    client.patch(
        "/v1/org",
        headers=org.admin,
        json={
            "settings": {"default_locale": "ms"},
            "branding": {
                "enabled": True,
                "handle": "rumah-kita-staff",
                "display_name": "Rumah Kita Homes",
                "contact_email": "help@example.com",
            },
        },
    ).raise_for_status()
    add_pm(client, org, "ms@example.com").raise_for_status()
    msg = mails(world)[-1]
    assert msg.locale == "ms" and "pengurus hartanah" in msg.text
    assert msg.from_name == "Rumah Kita Homes" and msg.reply_to == "help@example.com"
    assert "Rumah Kita Homes" in msg.subject


def test_no_email_to_a_bounced_address_or_when_sending_fails(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    uid = add_pm(client, org, "bounce@example.com").json()["user_id"]
    world.c.store.notifications(org.id).suppress_put("email", "bounce@example.com", "bounce", "x")
    before = len(mails(world))
    r = client.post(f"/v1/users/{uid}/invite", headers=org.admin)
    assert r.status_code == 409 and r.json()["code"] == "address_suppressed"
    assert len(mails(world)) == before
    # a failed send: the user is added, and the admin is told to resend
    world.c.channels["email"].fail_with = RuntimeError("down")  # type: ignore[attr-defined]
    r = add_pm(client, org, "later@example.com")
    assert r.status_code == 409 and "Resend invitation" in r.json()["message"]
    world.c.channels["email"].fail_with = None  # type: ignore[attr-defined]
    users = client.get("/v1/users", headers=org.admin).json()["items"]
    later = next(u for u in users if u["email"] == "later@example.com")
    assert client.post(f"/v1/users/{later['user_id']}/invite", headers=org.admin).status_code == 202


def test_without_email_sending_cognito_sends_its_invitation(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    email = world.c.channels.pop("email")
    try:
        uid = add_pm(client, org, "cognito@example.com").json()["user_id"]
        assert world.c.identity.users[uid]["invite_email"] is True  # type: ignore[attr-defined]
        assert client.post(f"/v1/users/{uid}/invite", headers=org.admin).status_code == 202
        assert "cognito@example.com" in world.c.identity.resent  # type: ignore[attr-defined]
    finally:
        world.c.channels["email"] = email

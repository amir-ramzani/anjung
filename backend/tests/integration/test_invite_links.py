"""Set-password links for tenants (contract v0.8.0): shared by staff, or sent by the app over WhatsApp."""

from datetime import timedelta
from typing import Any

from app.lambdas.pre_token_generation.handler import resolve_claims
from fastapi.testclient import TestClient

from tests.conftest import Org, World

GOOD = "Sunny-Day-2018"


def invite(client: TestClient, org: Org, tid: str, channel: str) -> Any:
    return client.post(f"/v1/tenants/{tid}/invite", headers=org.admin, json={"channel": channel})


def token_of(link: str) -> str:
    assert "/invite/" in link
    return link.rsplit("/invite/", 1)[1]


def test_staff_share_a_link_and_the_tenant_sets_a_password(
    world: World, client: TestClient
) -> None:
    org = world.make_org("Rumah Kita")
    t = org.new_tenant("newbie@example.com", full_name="Nur Aina")
    r = invite(client, org, t["id"], "link")
    assert r.status_code == 202, r.text
    out = r.json()
    assert out["channel"] == "link" and out["expires_at"] and out["link"].startswith("http")
    token = token_of(out["link"])
    sub = world.c.identity.sub_for("newbie@example.com")  # type: ignore[attr-defined]
    assert world.c.identity.users[sub]["invite_email"] is False  # type: ignore[attr-defined]
    # only the hash is stored
    raw = str(world.c.store.table.get(f"ORG#{org.id}", f"TENANT#{t['id']}"))
    assert token not in raw

    info = client.get(f"/v1/invites/{token}")
    assert info.status_code == 200
    assert info.json() | {"expires_at": None} == {
        "kind": "tenant",
        "full_name": "Nur Aina",
        "email": "newbie@example.com",
        "org_name": "Rumah Kita",
        "expires_at": None,
        "brand": None,  # branding is off for this organisation
    }
    accept = lambda pw: client.post(f"/v1/invites/{token}/accept", json={"password": pw})  # noqa: E731
    assert accept("short").status_code == 422
    assert accept("longenoughbutweak").status_code == 422  # no upper case, number or symbol
    assert client.get(f"/v1/invites/{token}").status_code == 200  # still usable
    assert accept(GOOD).status_code == 204
    assert world.c.identity.users[sub]["password"] == GOOD  # type: ignore[attr-defined]

    got = client.get(f"/v1/tenants/{t['id']}", headers=org.admin).json()
    assert got["linked"] is True and got["status"] == "active"
    assert resolve_claims(world.c.store.table, sub)["tenant_id"] == t["id"]
    # used up
    assert client.get(f"/v1/invites/{token}").status_code == 404
    assert accept(GOOD).status_code == 404
    assert invite(client, org, t["id"], "link").status_code == 409
    actions = [
        (e["action"], e["actor_role"])
        for e in client.get(
            f"/v1/audit-log?entity_type=tenant&entity_id={t['id']}", headers=org.admin
        ).json()["items"]
    ]
    assert ("tenant.invite_accept", "tenant") in actions and (
        "tenant.invite",
        "org_admin",
    ) in actions


def test_only_the_newest_link_works_and_links_expire(
    world: World, client: TestClient, clock: Any
) -> None:
    org = world.make_org()
    t = org.new_tenant()
    first = token_of(invite(client, org, t["id"], "link").json()["link"])
    second = token_of(invite(client, org, t["id"], "link").json()["link"])
    assert client.get(f"/v1/invites/{first}").status_code == 404
    assert client.get(f"/v1/invites/{second}").status_code == 200
    assert client.post(f"/v1/invites/{first}/accept", json={"password": GOOD}).status_code == 404
    clock.set(clock.now() + timedelta(days=8))
    r = client.get(f"/v1/invites/{second}")
    assert r.status_code == 404 and "expired" in r.json()["message"]
    assert client.get("/v1/invites/not-a-real-token-at-all-xyz").status_code == 404


def test_email_and_link_can_be_mixed(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant("mixed@example.com")
    assert (
        client.post(f"/v1/tenants/{t['id']}/invite", headers=org.admin).json()["channel"] == "email"
    )
    sub = world.c.identity.sub_for("mixed@example.com")  # type: ignore[attr-defined]
    # never Cognito's own invitation (temporary password): our welcome email has the link (v0.14.0)
    assert world.c.identity.users[sub]["invite_email"] is False  # type: ignore[attr-defined]
    emailed = token_of(world.c.channels["email"].sent[-1].text.split("here: ", 1)[1].split()[0])  # type: ignore[attr-defined]
    token = token_of(invite(client, org, t["id"], "link").json()["link"])  # same Cognito user
    assert (
        len([u for u in world.c.identity.users.values() if u["email"] == "mixed@example.com"]) == 1
    )  # type: ignore[attr-defined]
    assert client.get(f"/v1/invites/{emailed}").status_code == 404  # the new link replaced it
    assert invite(client, org, t["id"], "email").status_code == 202
    assert not getattr(world.c.identity, "resent", [])  # no Cognito email either
    assert client.get(f"/v1/invites/{token}").status_code == 404
    again = token_of(world.c.channels["email"].sent[-1].text.split("here: ", 1)[1].split()[0])  # type: ignore[attr-defined]
    assert client.post(f"/v1/invites/{again}/accept", json={"password": GOOD}).status_code == 204


def test_the_welcome_email_has_the_link_and_the_brand(world: World, client: TestClient) -> None:
    org = world.make_org("Rumah Kita")
    t = org.new_tenant("welcome@example.com")
    r = invite(client, org, t["id"], "email")
    assert r.status_code == 202 and r.json()["link"] is None  # staff never see it
    msg = world.c.channels["email"].sent[-1]  # type: ignore[attr-defined]
    assert msg.to == "welcome@example.com" and msg.template == "tenant_invite"
    assert "Rumah Kita" in msg.subject and "welcome" in msg.text
    token = token_of(msg.text.split("here: ", 1)[1].split()[0])
    assert 'href="http' in msg.html and token in msg.html
    log = client.get("/v1/notifications/log", headers=org.admin).json()["items"]
    entry = next(e for e in log if e["template"] == "tenant_invite")
    assert entry["channel"] == "email" and entry["status"] == "sent" and token not in str(entry)
    assert client.post(f"/v1/invites/{token}/accept", json={"password": GOOD}).status_code == 204
    # a failed send says so, and the tenant can be invited again
    other = org.new_tenant("bounce@example.com")
    world.c.channels["email"].fail_with = RuntimeError("down")  # type: ignore[attr-defined]
    r = invite(client, org, other["id"], "email")
    assert r.status_code == 409 and "email could not be sent" in r.json()["message"]
    world.c.channels["email"].fail_with = None  # type: ignore[attr-defined]
    assert invite(client, org, other["id"], "email").status_code == 202


def test_no_welcome_email_to_an_address_that_bounced(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant("gone@example.com")
    world.c.store.notifications(org.id).suppress_put("email", "gone@example.com", "bounce", "x")
    before = len(world.c.channels["email"].sent)  # type: ignore[attr-defined]
    r = invite(client, org, t["id"], "email")
    assert r.status_code == 409 and r.json()["code"] == "address_suppressed"
    assert "bounced" in r.json()["message"]
    assert len(world.c.channels["email"].sent) == before  # type: ignore[attr-defined]
    assert world.c.store.tenants(org.id).get(t["id"]).get("invite_token_pk") is None  # no link made
    # staff can still share the link themselves
    assert invite(client, org, t["id"], "link").json()["link"]


def test_the_app_sends_the_link_over_whatsapp(world: World, client: TestClient) -> None:
    org = world.make_org("Rumah Kita")
    no_phone = org.new_tenant()
    r = invite(client, org, no_phone["id"], "whatsapp")
    assert r.status_code == 422 and "phone" in r.json()["message"]

    t = org.new_tenant(phone="+60123456789")
    r = invite(client, org, t["id"], "whatsapp")
    assert r.status_code == 422 and "consent" in r.json()["message"]
    assert client.get(f"/v1/tenants/{t['id']}", headers=org.admin).json().get("linked") is False
    client.post(
        f"/v1/tenants/{t['id']}/consents",
        headers=org.admin,
        json={"purpose": "whatsapp_messaging", "policy_version": "2018-01", "granted": True},
    )
    r = invite(client, org, t["id"], "whatsapp")
    assert r.status_code == 202 and r.json()["link"] is None  # staff never see it
    sent = world.c.channels["whatsapp"].sent  # type: ignore[attr-defined]
    msg = sent[-1]
    assert msg.to == "+60123456789" and msg.template == "tenant_invite"
    assert "Rumah Kita" in msg.text and "Siti Aminah" in msg.text
    token = token_of(msg.text.split("here: ", 1)[1].split()[0])
    # the notification log records the send, never the link
    log = client.get("/v1/notifications/log", headers=org.admin).json()["items"]
    entry = next(e for e in log if e["template"] == "tenant_invite")
    assert entry["status"] == "sent" and token not in str(entry)
    assert client.post(f"/v1/invites/{token}/accept", json={"password": GOOD}).status_code == 204


def test_whatsapp_not_set_up_or_failing(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant(phone="+60123456789")
    client.post(
        f"/v1/tenants/{t['id']}/consents",
        headers=org.admin,
        json={"purpose": "whatsapp_messaging", "policy_version": "2018-01", "granted": True},
    )
    channel = world.c.channels["whatsapp"]
    channel.fail_with = RuntimeError("provider down")  # type: ignore[attr-defined]
    r = invite(client, org, t["id"], "whatsapp")
    assert r.status_code == 409 and "share the link" in r.json()["message"]
    channel.fail_with = None  # type: ignore[attr-defined]
    del world.c.channels["whatsapp"]  # production before decision D3
    try:
        r = invite(client, org, t["id"], "whatsapp")
        assert r.status_code == 409 and "not set up" in r.json()["message"]
        assert invite(client, org, t["id"], "link").status_code == 202  # sharing still works
    finally:
        world.c.channels["whatsapp"] = channel


def test_tenant_and_other_staff_cannot_use_invite_links(world: World, client: TestClient) -> None:
    org, other = world.make_org(), world.make_org("Other")
    t = org.new_tenant()
    assert invite(client, other, t["id"], "link").status_code == 404
    assert (
        client.post(
            f"/v1/tenants/{t['id']}/invite", headers=org.admin, json={"channel": "sms"}
        ).status_code
        == 422
    )

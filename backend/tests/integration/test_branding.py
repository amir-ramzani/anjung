"""Organisation branding (contract v0.11.0): each company is its own organisation with its own brand."""

from datetime import timedelta
from typing import Any

from app.domains.billing.receipts import _header
from fastapi.testclient import TestClient

from tests.conftest import Org, World
from tests.integration.billing_helpers import Setup
from tests.integration.test_documents import upload_and_scan
from tests.integration.test_notifications import opt_in, sent

BRAND = {
    "enabled": True,
    "handle": "rumah-kita",
    "display_name": "Rumah Kita",
    "company_name": "Rumah Kita Sdn Bhd",
    "company_reg_no": "202301012345 (1234567-X)",
    "accent_colour": "#7c3aed",
    "contact_email": "help@example.com",
    "contact_phone": "+60312345678",
    "welcome_text": {"en": "Welcome, tenants of Rumah Kita", "ms": "Selamat datang"},
}


def brand(client: TestClient, org: Org, **over: Any) -> Any:
    return client.patch("/v1/org", headers=org.admin, json={"branding": {**BRAND, **over}})


def upload_logo(world: World, org: Org, content_type: str = "image/png", size: int = 1000) -> Any:
    return world.client.post(
        "/v1/documents/upload-url",
        headers=org.admin,
        json={
            "owner_type": "organization",
            "owner_id": org.id,
            "type": "logo",
            "filename": "logo.png",
            "content_type": content_type,
            "size_bytes": size,
        },
    )


def test_set_branding_and_show_it_publicly(world: World, client: TestClient) -> None:
    org = world.make_org("Acme Holdings")
    assert (
        client.patch("/v1/org", headers=org.admin, json={"branding": {"enabled": True}}).status_code
        == 422
    )
    assert brand(client, org, handle="admin").status_code == 422  # reserved
    assert brand(client, org, handle="Bad_Handle").status_code == 422
    r = brand(client, org)
    assert r.status_code == 200, r.text
    b = r.json()["branding"]
    assert (
        b["enabled"]
        and b["handle"] == "rumah-kita"
        and b["company_reg_no"] == BRAND["company_reg_no"]
    )
    pub = client.get("/v1/brands/rumah-kita")
    assert pub.status_code == 200
    assert (
        pub.json()["display_name"] == "Rumah Kita"
        and pub.json()["welcome_text"]["ms"] == "Selamat datang"
    )
    assert "logo_document_id" not in pub.json() and "enabled" not in pub.json()
    # a partial update keeps the rest
    r = client.patch(
        "/v1/org", headers=org.admin, json={"branding": {"welcome_text": {"en": "Hello"}}}
    )
    assert r.json()["branding"]["welcome_text"] == {"en": "Hello", "ms": "Selamat datang"}
    assert r.json()["branding"]["company_name"] == "Rumah Kita Sdn Bhd"
    # everyone in the organisation sees it in /v1/me
    assert client.get("/v1/me", headers=org.admin).json()["branding"]["handle"] == "rumah-kita"
    assert (
        client.get("/v1/me", headers=org.tenant_login(org.new_tenant())).json()["branding"][
            "display_name"
        ]
        == "Rumah Kita"
    )
    # managers cannot change it
    assert (
        client.patch(
            "/v1/org", headers=org.invite_manager(), json={"branding": {"enabled": False}}
        ).status_code
        == 403
    )
    actions = {
        e["action"] for e in client.get("/v1/audit-log?limit=50", headers=org.admin).json()["items"]
    }
    assert "org.update" in actions


def test_handles_are_unique_and_a_changed_one_keeps_working_for_a_while(
    world: World, client: TestClient, clock: Any
) -> None:
    a, b = world.make_org("A"), world.make_org("B")
    assert brand(client, a).status_code == 200
    r = brand(client, b)
    assert r.status_code == 409 and "taken" in r.json()["message"]
    assert brand(client, a, handle="rumah-kita-2").status_code == 200
    assert (
        client.get("/v1/brands/rumah-kita").json()["handle"] == "rumah-kita-2"
    )  # old links still open
    assert brand(client, b).status_code == 409  # still held during the grace period
    clock.set(clock.now() + timedelta(days=31))
    assert client.get("/v1/brands/rumah-kita").status_code == 404
    assert brand(client, b).status_code == 200  # now free
    assert client.get("/v1/brands/rumah-kita").json()["display_name"] == "Rumah Kita"


def test_disabled_unknown_and_inactive_brands_look_the_same(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    brand(client, org)
    assert client.get("/v1/brands/nobody-here").status_code == 404
    client.patch("/v1/org", headers=org.admin, json={"branding": {"enabled": False}})
    assert client.get("/v1/brands/rumah-kita").status_code == 404
    assert client.get("/v1/me", headers=org.admin).json()["branding"] is None
    client.patch("/v1/org", headers=org.admin, json={"branding": {"enabled": True}})
    client.patch(
        f"/v1/platform/organizations/{org.id}",
        headers=world.platform,
        json={"status": "suspended", "reason": "unpaid"},
    )
    assert client.get("/v1/brands/rumah-kita").status_code == 404


def test_the_logo_is_a_scanned_image_served_publicly(world: World, client: TestClient) -> None:
    org = world.make_org()
    brand(client, org)
    assert upload_logo(world, org, "application/pdf").status_code == 422
    assert upload_logo(world, org, size=600 * 1024).status_code == 422
    # a logo only for the caller's own organisation, and only by an admin
    other = world.make_org("Other")
    r = client.post(
        "/v1/documents/upload-url",
        headers=other.admin,
        json={
            "owner_type": "organization",
            "owner_id": org.id,
            "type": "logo",
            "filename": "l.png",
            "content_type": "image/png",
            "size_bytes": 10,
        },
    )
    assert r.status_code == 404
    pm = org.invite_manager()
    r = client.post(
        "/v1/documents/upload-url",
        headers=pm,
        json={
            "owner_type": "organization",
            "owner_id": org.id,
            "type": "logo",
            "filename": "l.png",
            "content_type": "image/png",
            "size_bytes": 10,
        },
    )
    assert r.status_code == 404
    did = upload_logo(world, org).json()["document_id"]
    pending = client.patch(
        "/v1/org", headers=org.admin, json={"branding": {"logo_document_id": did}}
    )
    assert pending.status_code == 422  # not scanned yet
    upload_and_scan(world, org, did)
    r = client.patch("/v1/org", headers=org.admin, json={"branding": {"logo_document_id": did}})
    assert (
        r.status_code == 200
        and r.json()["branding"]["logo_url"] == f"/v1/brands/rumah-kita/logo?v={did}"
    )
    pub = client.get("/v1/brands/rumah-kita").json()
    assert pub["logo_url"] == f"/v1/brands/rumah-kita/logo?v={did}"
    img = client.get(pub["logo_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert (
        img.headers["x-content-type-options"] == "nosniff"
        and "default-src 'none'" in img.headers["content-security-policy"]
    )
    # a document of another kind cannot become the logo
    lease_doc = client.post(
        "/v1/documents/upload-url",
        headers=org.admin,
        json={
            "owner_type": "property",
            "owner_id": org.new_property()["id"],
            "type": "other",
            "filename": "x.png",
            "content_type": "image/png",
            "size_bytes": 10,
        },
    ).json()["document_id"]
    assert (
        client.patch(
            "/v1/org", headers=org.admin, json={"branding": {"logo_document_id": lease_doc}}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/documents/upload-url",
            headers=org.admin,
            json={
                "owner_type": "property",
                "owner_id": org.new_property()["id"],
                "type": "logo",
                "filename": "x.png",
                "content_type": "image/png",
                "size_bytes": 10,
            },
        ).status_code
        == 422
    )


def test_emails_carry_the_brand(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    brand(client, s.org)
    opt_in(client, s)
    s.run("2018-12")
    [msg] = sent(world)
    assert msg.from_name == "Rumah Kita" and msg.reply_to == "help@example.com"
    assert "Rumah Kita Sdn Bhd, Registration no. 202301012345 (1234567-X)" in msg.text
    assert (
        "Sent by Rumah Kita through Anjung." in msg.text and "/pay/" in msg.text
    )  # the message itself is unchanged
    assert "#7c3aed" in msg.html and "Rumah Kita" in msg.html


def test_branding_without_it_changes_nothing(world: World, client: TestClient) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    opt_in(client, s)
    s.run("2018-12")
    [msg] = sent(world)
    assert msg.from_name is None and msg.reply_to is None and "through Anjung" not in msg.text


def test_the_set_password_page_and_whatsapp_invite_use_the_brand(
    world: World, client: TestClient
) -> None:
    org = world.make_org("Acme Holdings")
    brand(client, org)
    t = org.new_tenant(phone="+60123456789")
    link = client.post(
        f"/v1/tenants/{t['id']}/invite", headers=org.admin, json={"channel": "link"}
    ).json()["link"]
    info = client.get(f"/v1/invites/{link.rsplit('/', 1)[1]}").json()
    assert info["org_name"] == "Rumah Kita" and info["brand"]["handle"] == "rumah-kita"
    client.post(
        f"/v1/tenants/{t['id']}/consents",
        headers=org.admin,
        json={"purpose": "whatsapp_messaging", "policy_version": "1", "granted": True},
    )
    client.post(f"/v1/tenants/{t['id']}/invite", headers=org.admin, json={"channel": "whatsapp"})
    assert "Rumah Kita has set up your tenant account" in world.c.channels["whatsapp"].sent[-1].text  # type: ignore[attr-defined]
    # the welcome email carries the brand, like the other emails (v0.14.0)
    other = org.new_tenant("brand-welcome@example.com")
    client.post(f"/v1/tenants/{other['id']}/invite", headers=org.admin, json={"channel": "email"})
    mail = world.c.channels["email"].sent[-1]  # type: ignore[attr-defined]
    assert mail.template == "tenant_invite" and mail.from_name == "Rumah Kita"
    assert "Rumah Kita Sdn Bhd" in mail.html


def test_the_platform_can_brand_a_company_when_creating_it(
    world: World, client: TestClient
) -> None:
    r = client.post(
        "/v1/platform/organizations",
        headers=world.platform,
        json={
            "name": "Sewa Mudah",
            "initial_admin": {"email": "boss@example.com"},
            "reason": "new customer",
            "branding": {"enabled": True, "handle": "sewa-mudah", "company_reg_no": "1234-X"},
        },
    )
    assert r.status_code == 201, r.text
    pub = client.get("/v1/brands/sewa-mudah").json()
    assert pub["display_name"] == "Sewa Mudah" and pub["company_reg_no"] == "1234-X"


def test_receipt_header_names_the_company() -> None:
    assert _header("Acme", None) == ["Acme"]
    b = {
        "display_name": "Rumah Kita",
        "company_name": "Rumah Kita Sdn Bhd",
        "company_reg_no": "1234-X",
        "contact_phone": "+60312345678",
        "contact_email": None,
    }
    assert _header("Acme", b) == ["Rumah Kita Sdn Bhd", "Registration no. 1234-X", "+60312345678"]

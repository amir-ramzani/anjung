"""The platform admin corrects a company's name and branding (contract v0.17.0)."""

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import PLATFORM_ORG_ID, World

BRAND = {
    "enabled": True,
    "handle": "rumah-kita",
    "display_name": "Rumah Kita",
    "contact_email": "help@example.com",
}


def details(client: TestClient, world: World, org_id: str, **body: Any) -> Any:
    return client.patch(
        f"/v1/platform/organizations/{org_id}/details",
        headers=world.platform,
        json={"reason": "typo in the name", **body},
    )


def test_the_platform_admin_corrects_the_name_and_branding(
    world: World, client: TestClient
) -> None:
    org = world.make_org("Acme Sdn Bdh")
    r = details(client, world, org.id, name="Acme Sdn Bhd", branding=BRAND)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Acme Sdn Bhd"
    assert r.json()["branding"]["handle"] == "rumah-kita" and r.json()["branding"]["enabled"]
    # the company sees it, the branded sign-in page works, the list shows the new name
    assert client.get("/v1/org", headers=org.admin).json()["name"] == "Acme Sdn Bhd"
    assert client.get("/v1/brands/rumah-kita").json()["display_name"] == "Rumah Kita"
    listed = client.get("/v1/platform/organizations", headers=world.platform).json()["items"]
    assert [o["name"] for o in listed if o["id"] == org.id] == ["Acme Sdn Bhd"]
    # partial: only what is sent changes
    r = details(client, world, org.id, branding={"handle": "rumah-kita-2"})
    assert r.json()["branding"]["display_name"] == "Rumah Kita"
    assert r.json()["name"] == "Acme Sdn Bhd"
    # the old handle keeps working for a while, like a change by the org admin
    assert client.get("/v1/brands/rumah-kita").json()["handle"] == "rumah-kita-2"


def test_audited_in_both_organisations_with_the_reason(world: World, client: TestClient) -> None:
    org = world.make_org("Acme")
    assert details(client, world, org.id, name="Acme Bhd").status_code == 200
    audit = client.get("/v1/audit-log", headers=org.admin).json()["items"]
    entry = next(a for a in audit if a["action"] == "org.update")
    assert entry["summary"]["reason"] == "typo in the name"
    assert entry["actor_role"] == "platform_admin"
    items, _ = world.c.store.audit(PLATFORM_ORG_ID).page(
        month="2018-11", entity_type=None, entity_id=None, limit=20, cursor=None
    )
    assert any(i["action"] == "org.update" and i["entity_id"] == org.id for i in items)


def test_same_rules_as_the_org_admin(world: World, client: TestClient) -> None:
    a, b = world.make_org("A"), world.make_org("B")
    assert details(client, world, a.id, branding=BRAND).status_code == 200
    taken = details(client, world, b.id, branding={**BRAND, "display_name": "B"})
    assert taken.status_code == 409
    assert details(client, world, b.id, branding={"handle": "admin"}).status_code == 422
    assert details(client, world, b.id, branding={"enabled": True}).status_code == 422  # no handle
    # the logo is uploaded by the org admin, never set here
    logo = details(client, world, b.id, branding={"logo_document_id": "01JD8Z3K9Q4M7T2V6X8B5N1C0A"})
    assert logo.status_code == 422
    assert logo.json()["details"][0]["code"] == "is_set_by_the_org_admin"  # translated in the SPA
    assert details(client, world, b.id, name="").status_code == 422
    r = client.patch(
        f"/v1/platform/organizations/{b.id}/details", headers=world.platform, json={"name": "X"}
    )
    assert r.status_code == 422  # a reason is required


def test_not_for_closed_or_platform_organisations_nor_other_roles(
    world: World, client: TestClient
) -> None:
    org = world.make_org("Acme")
    assert details(client, world, PLATFORM_ORG_ID, name="X").status_code == 404
    assert details(client, world, "01JD8Z3K9Q4M7T2V6X8B5N1C0B", name="X").status_code == 404
    r = client.patch(
        f"/v1/platform/organizations/{org.id}/details",
        headers=org.admin,
        json={"name": "X", "reason": "r"},
    )
    assert r.status_code == 403  # an org admin uses PATCH /v1/org instead
    client.patch(
        f"/v1/platform/organizations/{org.id}",
        headers=world.platform,
        json={"status": "closed", "reason": "r"},
    )
    assert details(client, world, org.id, name="X").status_code == 409

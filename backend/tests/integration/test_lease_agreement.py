"""Tenancy agreement (contract v0.7.0): sent to the tenants, each signs in the portal (or on paper),
and only then can staff activate the lease."""

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import Org, World
from tests.integration.test_documents import upload_and_scan


def setup(
    world: World, client: TestClient, names: tuple[str, ...] = ("Siti Aminah",)
) -> tuple[Org, dict[str, Any], list[tuple[dict[str, Any], dict[str, str]]]]:
    org = world.make_org(signed_agreements=True)
    unit = org.new_unit(org.new_property()["id"])
    people = [org.new_tenant(full_name=n) for n in names]
    lease = org.new_lease(unit["id"], [p["id"] for p in people], start_date="2018-12-01")
    return org, lease, [(p, org.tenant_login(p)) for p in people]


def upload_agreement(world: World, org: Org, client: TestClient, lease_id: str) -> str:
    r = client.post(
        "/v1/documents/upload-url",
        headers=org.admin,
        json={
            "owner_type": "lease",
            "owner_id": lease_id,
            "type": "lease_agreement",
            "filename": "agreement.pdf",
            "content_type": "application/pdf",
            "size_bytes": 1000,
        },
    )
    assert r.status_code == 201, r.text
    did = r.json()["document_id"]
    assert upload_and_scan(world, org, did) == "clean"
    return str(did)


def test_every_tenant_signs_before_the_lease_can_be_activated(
    world: World, client: TestClient
) -> None:
    org, lease, [(siti, siti_tok), (ali, ali_tok)] = setup(
        world, client, ("Siti Aminah", "Ali Hassan")
    )
    lid = lease["id"]
    assert lease["agreement"]["status"] == "not_sent"
    activate = lambda: client.post(f"/v1/leases/{lid}/activate", headers=org.admin)  # noqa: E731
    r = activate()
    assert r.status_code == 409 and "sign" in r.json()["message"]
    assert (
        client.get(f"/v1/leases/{lid}", headers=siti_tok).status_code == 404
    )  # a draft stays internal

    r = client.post(f"/v1/leases/{lid}/agreement/send", headers=org.admin, json={})
    assert r.status_code == 409 and "Lease agreement" in r.json()["message"]  # nothing uploaded yet
    did = upload_agreement(world, org, client, lid)
    sent = client.post(f"/v1/leases/{lid}/agreement/send", headers=org.admin, json={}).json()
    assert sent["agreement"]["status"] == "sent" and sent["agreement"]["document_id"] == did
    assert sorted(sent["agreement"]["pending_tenant_ids"]) == sorted([siti["id"], ali["id"]])

    # the tenant now sees the draft and can read the agreement
    assert client.get(f"/v1/leases/{lid}", headers=siti_tok).json()["agreement"]["status"] == "sent"
    assert [x["id"] for x in client.get("/v1/leases", headers=siti_tok).json()["items"]] == [lid]
    docs = client.get(f"/v1/documents?owner_type=lease&owner_id={lid}", headers=siti_tok)
    assert docs.status_code == 200 and [d["id"] for d in docs.json()["items"]] == [did]

    sign = lambda tok, name: client.post(  # noqa: E731
        f"/v1/leases/{lid}/agreement/sign", headers=tok, json={"full_name": name, "agree": True}
    )
    assert sign(siti_tok, "Someone Else").status_code == 422  # must type their own name
    assert (
        client.post(
            f"/v1/leases/{lid}/agreement/sign", headers=siti_tok, json={"full_name": "Siti Aminah"}
        ).status_code
        == 422
    )  # must agree
    one = sign(siti_tok, "  siti   AMINAH ").json()  # spacing and case do not matter
    assert one["agreement"]["status"] == "sent" and one["agreement"]["pending_tenant_ids"] == [
        ali["id"]
    ]
    assert one["agreement"]["signatures"][0]["method"] == "portal"
    assert "ip" not in one["agreement"]["signatures"][0]  # kept for the record, not shown
    assert sign(siti_tok, "Siti Aminah").status_code == 409  # once is enough
    assert (
        client.post(
            f"/v1/leases/{lid}/agreement/sign",
            headers=org.admin,
            json={"full_name": "x", "agree": True},
        ).status_code
        == 403
    )
    assert activate().status_code == 409  # Ali has not signed

    done = sign(ali_tok, "Ali Hassan").json()["agreement"]
    assert done["status"] == "signed" and done["signed_at"] and done["pending_tenant_ids"] == []
    assert activate().json()["status"] == "active"
    actions = [
        e["action"]
        for e in client.get(
            f"/v1/audit-log?entity_type=lease&entity_id={lid}", headers=org.admin
        ).json()["items"]
    ]
    assert {"lease.agreement_send", "lease.agreement_sign"} <= set(actions)


def test_changing_the_terms_after_sending_means_signing_again(
    world: World, client: TestClient
) -> None:
    org, lease, [(siti, tok)] = setup(world, client)
    lid = lease["id"]
    upload_agreement(world, org, client, lid)
    client.post(f"/v1/leases/{lid}/agreement/send", headers=org.admin, json={})
    client.post(
        f"/v1/leases/{lid}/agreement/sign",
        headers=tok,
        json={"full_name": "Siti Aminah", "agree": True},
    )
    r = client.patch(
        f"/v1/leases/{lid}",
        headers=org.admin,
        json={"monthly_rent": {"amount_minor": 160000, "currency": "MYR"}},
    )
    assert r.status_code == 200 and r.json()["agreement"]["status"] == "not_sent"
    assert (
        client.get(f"/v1/leases/{lid}", headers=tok).status_code == 404
    )  # back to internal until resent
    assert client.post(f"/v1/leases/{lid}/activate", headers=org.admin).status_code == 409
    # the deposits are terms too
    client.post(f"/v1/leases/{lid}/agreement/send", headers=org.admin, json={})
    client.post(
        f"/v1/leases/{lid}/agreement/sign",
        headers=tok,
        json={"full_name": "Siti Aminah", "agree": True},
    )
    r = client.patch(
        f"/v1/leases/{lid}",
        headers=org.admin,
        json={"deposits": [{"type": "security", "amount": {"amount_minor": 1, "currency": "MYR"}}]},
    )
    assert r.status_code == 200 and r.json()["agreement"]["status"] == "not_sent"


def test_staff_can_record_a_paper_signature(world: World, client: TestClient) -> None:
    org, lease, [(siti, _)] = setup(world, client)
    lid = lease["id"]
    paper = {"tenant_ids": [siti["id"]], "note": "signed at the office"}
    assert (
        client.post(f"/v1/leases/{lid}/agreement/paper", headers=org.admin, json=paper).status_code
        == 409
    )  # upload the scan first
    upload_agreement(world, org, client, lid)
    a = client.post(f"/v1/leases/{lid}/agreement/paper", headers=org.admin, json=paper).json()[
        "agreement"
    ]
    assert a["status"] == "signed" and a["signatures"][0]["method"] == "paper"
    assert a["signatures"][0]["full_name"] == "Siti Aminah"
    assert (
        client.post(
            f"/v1/leases/{lid}/agreement/paper",
            headers=org.admin,
            json={"tenant_ids": ["01JD8Z3K9Q4M7T2V6X8B5N1C0A"]},
        ).status_code
        == 422
    )
    assert client.post(f"/v1/leases/{lid}/activate", headers=org.admin).json()["status"] == "active"


def test_an_organisation_can_switch_the_rule_off(world: World, client: TestClient) -> None:
    org, lease, _ = setup(world, client)
    assert client.get("/v1/org", headers=org.admin).json()["settings"].get(
        "require_signed_agreement"
    ) in (None, True)
    client.patch(
        "/v1/org", headers=org.admin, json={"settings": {"require_signed_agreement": False}}
    )
    assert (
        client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin).json()["status"]
        == "active"
    )

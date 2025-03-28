"""Manager scope (contract v0.4.0): a property manager sees only what they manage; an org admin sees all."""

from typing import Any

from app.core.ids import new_id
from fastapi.testclient import TestClient

from tests.conftest import Org, World

ADDRESS = {"line1": "1 Jalan A", "city": "KL", "state": "WP", "postcode": "50000"}


def manager(org: Org) -> tuple[str, dict[str, str]]:
    """Invite a property manager: (user id, token)."""
    r = org.w.client.post(
        "/v1/users/invite",
        headers=org.admin,
        json={"email": f"pm-{new_id().lower()}@example.com", "roles": ["property_manager"]},
    )
    assert r.status_code == 201, r.text
    sub = r.json()["user_id"]
    return sub, org.w.token(sub, org.id, ["property_manager"])


def prop(client: TestClient, h: dict[str, str], name: str) -> dict[str, Any]:
    r = client.post(
        "/v1/properties", headers=h, json={"name": name, "type": "condominium", "address": ADDRESS}
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def unit(client: TestClient, h: dict[str, str], pid: str, label: str = "A-1") -> dict[str, Any]:
    r = client.post(
        f"/v1/properties/{pid}/units",
        headers=h,
        json={"label": label, "default_rent": {"amount_minor": 150000, "currency": "MYR"}},
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def tenant(client: TestClient, h: dict[str, str], name: str) -> dict[str, Any]:
    r = client.post(
        "/v1/tenants",
        headers=h,
        json={"full_name": name, "email": f"t-{new_id().lower()}@example.com"},
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def lease(client: TestClient, h: dict[str, str], uid: str, tids: list[str]) -> Any:
    return client.post(
        "/v1/leases",
        headers=h,
        json={
            "unit_id": uid,
            "tenant_ids": tids,
            "start_date": "2018-12-01",
            "end_date": "2019-11-30",
            "monthly_rent": {"amount_minor": 150000, "currency": "MYR"},
            "deposits": [
                {"type": "security", "amount": {"amount_minor": 300000, "currency": "MYR"}}
            ],
            "due_day": 5,
        },
    )


def ids(r: Any) -> set[str]:
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()["items"]}


def test_each_manager_sees_only_their_own_portfolio(world: World, client: TestClient) -> None:
    org = world.make_org()
    a_id, a = manager(org)
    _, b = manager(org)

    pa = prop(client, a, "A Tower")
    pb = prop(client, b, "B Court")
    padmin = prop(client, org.admin, "Admin House")
    assert pa["manager_id"] == a_id  # the creator manages it

    assert ids(client.get("/v1/properties", headers=a)) == {pa["id"]}
    assert ids(client.get("/v1/properties", headers=b)) == {pb["id"]}
    assert ids(client.get("/v1/properties", headers=org.admin)) == {
        pa["id"],
        pb["id"],
        padmin["id"],
    }
    for path in (
        f"/v1/properties/{pb['id']}",
        f"/v1/properties/{pb['id']}/units",
        f"/v1/properties/{padmin['id']}",
    ):
        assert client.get(path, headers=a).status_code == 404, path  # looks like it does not exist
    assert (
        client.patch(f"/v1/properties/{pb['id']}", headers=a, json={"name": "x"}).status_code == 404
    )
    assert (
        client.post(
            f"/v1/properties/{pb['id']}/units",
            headers=a,
            json={"label": "Z", "default_rent": {"amount_minor": 1, "currency": "MYR"}},
        ).status_code
        == 404
    )

    ua = unit(client, a, pa["id"])
    ub = unit(client, b, pb["id"])
    assert client.get(f"/v1/units/{ub['id']}", headers=a).status_code == 404
    assert client.patch(f"/v1/units/{ub['id']}", headers=a, json={"label": "x"}).status_code == 404

    # tenants: their own, plus anyone renting in their properties
    ta = tenant(client, a, "Tenant of A")
    tb = tenant(client, b, "Tenant of B")
    t_admin = tenant(client, org.admin, "Added by admin")
    assert ids(client.get("/v1/tenants", headers=a)) == {ta["id"]}
    assert client.get(f"/v1/tenants/{tb['id']}", headers=a).status_code == 404
    assert (
        client.patch(f"/v1/tenants/{tb['id']}", headers=a, json={"full_name": "x"}).status_code
        == 404
    )
    assert ids(client.get("/v1/tenants", headers=org.admin)) == {ta["id"], tb["id"], t_admin["id"]}

    # a manager leases only their own units, to tenants they can see
    assert lease(client, a, ub["id"], [ta["id"]]).status_code == 422
    assert lease(client, a, ua["id"], [tb["id"]]).status_code == 422
    la = lease(
        client, org.admin, ua["id"], [ta["id"], t_admin["id"]]
    ).json()  # admin puts its tenant in A's unit
    lb = lease(client, b, ub["id"], [tb["id"]]).json()
    assert (
        client.get(f"/v1/tenants/{t_admin['id']}", headers=a).status_code == 200
    )  # now rents in A's property
    assert client.get(f"/v1/tenants/{t_admin['id']}", headers=b).status_code == 404
    assert ids(client.get("/v1/leases", headers=a)) == {la["id"]}
    assert client.get(f"/v1/leases/{lb['id']}", headers=a).status_code == 404
    assert client.get(f"/v1/leases?tenant_id={tb['id']}", headers=a).json()["items"] == []
    assert client.get(f"/v1/leases?unit_id={ub['id']}", headers=a).json()["items"] == []

    # invoices and ledgers follow the lease
    for x, h in ((la, a), (lb, b)):
        assert client.post(f"/v1/leases/{x['id']}/activate", headers=h).status_code == 200
    inv_a = ids(client.get("/v1/invoices", headers=a))
    inv_b = ids(client.get("/v1/invoices", headers=b))
    assert inv_a and inv_b and not inv_a & inv_b
    assert ids(client.get("/v1/invoices", headers=org.admin)) == inv_a | inv_b
    one_b = next(iter(inv_b))
    assert client.get(f"/v1/invoices/{one_b}", headers=a).status_code == 404
    assert client.get(f"/v1/leases/{lb['id']}/ledger", headers=a).status_code == 404
    assert client.get(f"/v1/invoices?lease_id={lb['id']}", headers=a).status_code == 404

    # maintenance
    mr = client.post(
        "/v1/maintenance-requests",
        headers=b,
        json={
            "area": "unit",
            "unit_id": ub["id"],
            "title": "Leak",
            "description": "Kitchen sink",
            "category": "plumbing",
            "priority": "normal",
        },
    )
    assert mr.status_code == 201, mr.text
    assert client.get(f"/v1/maintenance-requests/{mr.json()['id']}", headers=a).status_code == 404
    assert client.get("/v1/maintenance-requests", headers=a).json()["items"] == []
    assert len(client.get("/v1/maintenance-requests", headers=b).json()["items"]) == 1
    assert (
        client.post(
            "/v1/maintenance-requests",
            headers=a,
            json={
                "area": "unit",
                "unit_id": ub["id"],
                "title": "x",
                "description": "x",
                "category": "plumbing",
                "priority": "normal",
            },
        ).status_code
        == 422
    )

    # documents of someone else's property look missing
    assert (
        client.get(f"/v1/documents?owner_type=property&owner_id={pb['id']}", headers=a).status_code
        == 404
    )

    # reports cover their own properties only
    occ = client.get("/v1/reports/occupancy", headers=a).json()["rows"]
    assert {r["property_id"] for r in occ} == {pa["id"]}
    arrears = client.get("/v1/reports/arrears", headers=a).json()["rows"]
    assert {r["lease_id"] for r in arrears} <= {la["id"]}
    admin_occ = client.get("/v1/reports/occupancy", headers=org.admin).json()["rows"]
    assert {r["property_id"] for r in admin_occ} == {pa["id"], pb["id"], padmin["id"]}

    # org-wide money views are admin only
    assert client.get("/v1/payments/unmatched", headers=a).status_code == 403
    assert client.get("/v1/invoice-runs", headers=a).status_code == 403
    assert client.get("/v1/payments/unmatched", headers=org.admin).status_code == 200


def test_an_admin_reassigns_a_property(world: World, client: TestClient) -> None:
    org = world.make_org()
    a_id, a = manager(org)
    b_id, b = manager(org)
    p = prop(client, org.admin, "Admin House")
    assert client.get(f"/v1/properties/{p['id']}", headers=a).status_code == 404

    r = client.patch(f"/v1/properties/{p['id']}", headers=org.admin, json={"manager_id": a_id})
    assert r.status_code == 200 and r.json()["manager_id"] == a_id
    assert client.get(f"/v1/properties/{p['id']}", headers=a).status_code == 200

    # managers cannot hand properties around, even their own
    assert (
        client.patch(f"/v1/properties/{p['id']}", headers=a, json={"manager_id": b_id}).status_code
        == 403
    )
    # only to an active manager or admin of this organisation
    for bad in ("no-such-user", world.make_org().admin_sub):
        assert (
            client.patch(
                f"/v1/properties/{p['id']}", headers=org.admin, json={"manager_id": bad}
            ).status_code
            == 422
        )

    assert (
        client.patch(
            f"/v1/properties/{p['id']}", headers=org.admin, json={"manager_id": b_id}
        ).status_code
        == 200
    )
    assert client.get(f"/v1/properties/{p['id']}", headers=a).status_code == 404
    assert ids(client.get("/v1/properties", headers=b)) == {p["id"]}
    audit = client.get("/v1/audit-log?entity_type=property", headers=org.admin)
    assert audit.status_code == 200 and any(
        "manager_id" in str(e.get("summary") or e.get("changes") or e)
        for e in audit.json()["items"]
    )


def test_a_tenant_list_never_reveals_another_managers_property(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    _, a = manager(org)
    _, b = manager(org)
    ua = unit(client, a, prop(client, a, "A Tower")["id"])
    ub = unit(client, b, prop(client, b, "B Court")["id"])
    shared = tenant(client, org.admin, "Rents from both")
    assert lease(client, org.admin, ua["id"], [shared["id"]]).status_code == 201
    assert lease(client, org.admin, ub["id"], [shared["id"]]).status_code == 201
    seen_by_a = client.get(f"/v1/tenants/{shared['id']}", headers=a).json()
    assert [r["property_name"] for r in seen_by_a["rentals"]] == ["A Tower"]

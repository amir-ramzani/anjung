"""Cross-organisation suite (plan P1-11, LLD 16.1).

Every route must be registered below. A new endpoint without an entry fails
`test_every_route_is_registered`, so nobody can ship an endpoint that skipped tenancy review.

* FOREIGN_ID: takes the ID of a resource. Org B calls it with org A's ID: no data, no mutation.
* SELF_SCOPED: takes no foreign ID. Org B's call must never return or change org A data.
* PLATFORM: platform-only. Every other role gets 403.
* PUBLIC: unauthenticated by design.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from app.core.ids import new_id
from fastapi.testclient import TestClient

from tests.conftest import Org, World
from tests.contract.test_contract import flatten_routes


@dataclass(frozen=True)
class Case:
    method: str
    path: str
    body: Any = None  # dict, or a callable taking the ids when the body carries foreign IDs
    expect: int = 404
    ok: tuple[int, ...] = (200, 201, 204)  # what the same call returns for the owning org
    use: dict[str, str] = field(default_factory=dict)  # path param -> key in the seeded ids
    query: Any = field(default_factory=dict)  # dict, or a callable taking the ids
    idem: bool = False  # the operation needs an Idempotency-Key header

    def url(self, ids: dict[str, str]) -> str:
        return self.path.format(**{**ids, **{k: ids[v] for k, v in self.use.items()}})

    def payload(self, ids: dict[str, str]) -> Any:
        return self.body(ids) if callable(self.body) else self.body


def call(
    world: World, case: Case, ids: dict[str, str], headers: dict[str, str], payload: Any = None
) -> Any:
    h = {**headers, **({"Idempotency-Key": f"key-{new_id()}"} if case.idem else {})}
    return world.client.request(
        case.method,
        case.url(ids),
        headers=h,
        json=payload,
        params=case.query(ids) if callable(case.query) else case.query,
    )


RENT = {"amount_minor": 1, "currency": "MYR"}
CONSENT = {"purpose": "marketing", "policy_version": "1", "granted": True}
ADDR = {"line1": "x", "city": "x", "state": "x", "postcode": "50000"}

FOREIGN_ID = [
    Case("GET", "/v1/properties/{property_id}"),
    Case("PATCH", "/v1/properties/{property_id}", {"name": "pwned"}),
    Case(
        "DELETE",
        "/v1/properties/{property_id}",
        use={"property_id": "empty_property_id"},
        query={"reason": "made by mistake"},
    ),
    Case(
        "POST", "/v1/properties/{property_id}/deactivate", use={"property_id": "empty_property_id"}
    ),
    Case(
        "POST", "/v1/properties/{property_id}/reactivate", use={"property_id": "empty_property_id"}
    ),
    Case("GET", "/v1/properties/{property_id}/deletion"),
    Case("POST", "/v1/units/{unit_id}/deactivate", use={"unit_id": "empty_unit_id"}),
    Case("POST", "/v1/units/{unit_id}/reactivate", use={"unit_id": "empty_unit_id"}),
    Case("GET", "/v1/units/{unit_id}/deletion"),
    Case(
        "DELETE",
        "/v1/units/{unit_id}",
        use={"unit_id": "empty_unit_id"},
        query={"reason": "made by mistake"},
    ),
    Case("GET", "/v1/tenants/{tenant_id}/deletion"),
    Case(
        "DELETE",
        "/v1/tenants/{tenant_id}",
        use={"tenant_id": "spare_tenant_id"},
        query={"reason": "made by mistake"},
    ),
    Case("GET", "/v1/leases/{lease_id}/deletion"),
    Case("DELETE", "/v1/leases/{lease_id}", query={"reason": "made by mistake"}),
    Case("GET", "/v1/maintenance-requests/{request_id}/deletion"),
    Case(
        "DELETE",
        "/v1/maintenance-requests/{request_id}",
        query={"reason": "made by mistake"},
        ok=(409,),  # still open: blocked for the owner too
    ),
    Case("GET", "/v1/properties/{property_id}/units"),
    Case("POST", "/v1/properties/{property_id}/units", {"label": "pwn", "default_rent": RENT}),
    Case("GET", "/v1/units/{unit_id}"),
    Case("PATCH", "/v1/units/{unit_id}", {"label": "pwned", "status": "maintenance"}),
    Case("PATCH", "/v1/users/{user_id}", {"status": "disabled"}),
    Case("DELETE", "/v1/users/{user_id}"),
    Case("POST", "/v1/users/{user_id}/invite", ok=(202, 409)),  # 409 once they have signed in
    Case("GET", "/v1/tenants/{tenant_id}"),
    Case("PATCH", "/v1/tenants/{tenant_id}", {"full_name": "pwned"}),
    Case("POST", "/v1/tenants/{tenant_id}/invite", ok=(202,)),
    Case("GET", "/v1/tenants/{tenant_id}/consents"),
    Case("POST", "/v1/tenants/{tenant_id}/consents", CONSENT),
    Case("GET", "/v1/leases/{lease_id}"),
    Case("PATCH", "/v1/leases/{lease_id}", {"due_day": 7}),
    Case("POST", "/v1/leases/{lease_id}/activate"),
    # the owner gets 409 (no agreement uploaded in this setup): still a real call, not a bad request
    Case("POST", "/v1/leases/{lease_id}/agreement/send", {}, ok=(200, 409)),
    Case(
        "POST",
        "/v1/leases/{lease_id}/agreement/paper",
        lambda ids: {"tenant_ids": [ids["tenant_id"]]},
        ok=(200, 409),
    ),
    Case(
        "POST",
        "/v1/leases/{lease_id}/terminate",
        {"termination_date": "2019-01-31", "reason": "r"},
        use={"lease_id": "active_lease_id"},
    ),
    Case(
        "POST",
        "/v1/leases/{lease_id}/renew",
        {"new_end_date": "2020-11-30"},
        use={"lease_id": "active_lease_id"},
    ),
    Case("GET", "/v1/tenants/{tenant_id}/privacy-export"),
    Case("GET", "/v1/privacy/requests/{privacy_request_id}"),
    Case("PATCH", "/v1/privacy/requests/{privacy_request_id}", {"status": "in_progress"}),
    Case(
        "POST",
        "/v1/privacy/requests",
        lambda i: {"type": "access", "tenant_id": i["tenant_id"]},
        expect=422,
    ),
    Case("GET", "/v1/invoices/{invoice_id}"),
    Case("POST", "/v1/invoices/{invoice_id}/qr"),
    Case(
        "POST",
        "/v1/invoices/{invoice_id}/void",
        {"reason": "r"},
        use={"invoice_id": "void_invoice_id"},
    ),
    Case(
        "POST",
        "/v1/invoices/{invoice_id}/payments/manual",
        {
            "method": "cash",
            "amount": {"amount_minor": 100, "currency": "MYR"},
            "received_at": "2018-11-20T03:00:00Z",
            "reason": "r",
        },
        idem=True,
    ),
    Case("GET", "/v1/invoices/{invoice_id}/receipt", use={"invoice_id": "paid_invoice_id"}),
    Case(
        "POST",
        "/v1/payments/{payment_id}/allocate",
        lambda i: {"invoice_id": i["invoice_id"], "reason": "r"},
        idem=True,
    ),
    Case(
        "POST",
        "/v1/leases/{lease_id}/charges",
        {
            "description": "d",
            "category": "repair",
            "amount": {"amount_minor": 500, "currency": "MYR"},
            "due_date": "2018-12-15",
        },
        idem=True,
        use={"lease_id": "active_lease_id"},
    ),
    Case("GET", "/v1/leases/{lease_id}/ledger", use={"lease_id": "active_lease_id"}),
    Case(
        "GET",
        "/v1/leases/{lease_id}/statement",
        use={"lease_id": "active_lease_id"},
        query={"from": "2018-01-01", "to": "2022-01-01"},
    ),
    Case("GET", "/v1/maintenance-requests/{request_id}"),
    Case("PATCH", "/v1/maintenance-requests/{request_id}", {"status": "triaged"}),
    Case("GET", "/v1/maintenance-requests/{request_id}/comments"),
    Case("POST", "/v1/maintenance-requests/{request_id}/comments", {"body": "hello"}),
    Case(
        "POST",
        "/v1/maintenance-requests",
        lambda i: {"title": "t", "description": "d", "category": "other", "unit_id": i["unit_id"]},
        expect=422,
    ),
    Case(
        "GET",
        "/v1/documents",
        query=lambda i: {"owner_type": "tenant", "owner_id": i["tenant_id"]},
    ),
    Case("GET", "/v1/documents/{document_id}"),
    Case("DELETE", "/v1/documents/{document_id}"),
    Case("GET", "/v1/documents/{document_id}/download-url"),
    # foreign IDs carried in the body
    Case(
        "POST",
        "/v1/documents/upload-url",
        lambda i: {
            "owner_type": "tenant",
            "owner_id": i["tenant_id"],
            "type": "other",
            "filename": "a.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
        },
    ),
    Case(
        "POST",
        "/v1/leases",
        lambda i: {
            "unit_id": i["unit_id"],
            "tenant_ids": [i["tenant_id"]],
            "start_date": "2022-01-01",
            "end_date": "2022-12-31",
            "monthly_rent": RENT,
            "deposits": [],
            "due_day": 1,
        },
        expect=422,
    ),
]
SELF_SCOPED = [
    Case("GET", "/v1/me"),
    Case("GET", "/v1/org"),
    Case("PATCH", "/v1/org", {"name": "B renamed"}),
    Case("GET", "/v1/users"),
    Case("POST", "/v1/users/invite", {"email": "b-new@example.com", "roles": ["org_admin"]}),
    Case("GET", "/v1/properties"),
    Case("POST", "/v1/properties", {"name": "B prop", "type": "room", "address": ADDR}),
    Case("GET", "/v1/audit-log"),
    Case("GET", "/v1/tenants"),
    Case("POST", "/v1/tenants", {"full_name": "B tenant", "email": "b-tenant@example.com"}),
    Case("GET", "/v1/leases"),
    Case("GET", "/v1/privacy/requests"),
    Case("GET", "/v1/invoices"),
    Case("GET", "/v1/payments/unmatched"),
    Case("GET", "/v1/invoice-runs"),
    Case("POST", "/v1/invoice-runs", {"period": "2018-12"}, idem=True),
    Case("GET", "/v1/maintenance-requests"),
    Case("GET", "/v1/notifications/log"),
    Case("GET", "/v1/reports/arrears"),
    Case("GET", "/v1/reports/occupancy"),
    Case("GET", "/v1/reports/income"),
    Case("GET", "/v1/reports/maintenance"),
]
TENANT_ONLY = [
    Case("GET", "/v1/privacy/export"),
    Case("GET", "/v1/notifications/preferences"),
    Case("PUT", "/v1/notifications/preferences", {"email": {"enabled": False}}),
]
# tenant-only, and about one record: staff get 403, a tenant of another organisation gets 404
TENANT_FOREIGN_ID = [
    Case("POST", "/v1/leases/{lease_id}/agreement/sign", {"full_name": "x", "agree": True}),
]
PLATFORM = [
    Case("GET", "/v1/platform/organizations"),
    Case(
        "POST",
        "/v1/platform/organizations",
        {"name": "x", "initial_admin": {"email": "e@example.com"}, "reason": "r"},
    ),
    Case("GET", "/v1/platform/organizations/{org_id}"),
    Case("PATCH", "/v1/platform/organizations/{org_id}", {"status": "suspended", "reason": "r"}),
    Case("PATCH", "/v1/platform/organizations/{org_id}/plan", {"unit_limit": 1, "reason": "r"}),
    Case("POST", "/v1/platform/organizations/{org_id}/admin-invite", {"reason": "r"}),
    Case("PATCH", "/v1/platform/organizations/{org_id}/details", {"name": "N", "reason": "r"}),
]
PUBLIC = {
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("POST", "/v1/webhooks/payments/{provider}"),  # signed by the provider, see test_webhooks
    # the set-password link is the credential; the org comes from the stored link, see test_invite_links
    ("GET", "/v1/invites/{token}"),
    ("POST", "/v1/invites/{token}/accept"),
    # a company's sign-in page: public on purpose, see test_branding
    ("GET", "/v1/brands/{handle}"),
    ("GET", "/v1/brands/{handle}/logo"),
}


def key(c: Case) -> tuple[str, str]:
    return c.method, c.path


def test_every_route_is_registered(container) -> None:  # type: ignore[no-untyped-def]
    from app.main import create_app

    from tests.contract.test_contract import CONTRACT

    app_routes = set(flatten_routes(create_app(container)))
    registered = {
        key(c) for c in FOREIGN_ID + SELF_SCOPED + PLATFORM + TENANT_ONLY + TENANT_FOREIGN_ID
    } | PUBLIC
    assert app_routes == registered, (
        f"unregistered: {sorted(app_routes - registered)}, stale: {sorted(registered - app_routes)}"
    )
    assert registered <= {(m.upper(), p) for p, i in CONTRACT["paths"].items() for m in i}


@pytest.fixture
def two_orgs(world: World) -> tuple[Org, Org, dict[str, str]]:
    from app.lambdas.scan_result.handler import on_object_created, on_scan_result

    a, b = world.make_org("Org A"), world.make_org("Org B")
    p = a.new_property("A-secret")
    u = a.new_unit(p["id"], "A-unit")
    u2 = a.new_unit(p["id"], "A-unit-2")
    t = a.new_tenant("a-tenant@example.com")
    draft = a.new_lease(u["id"], [t["id"]])
    active = a.new_lease(u2["id"], [t["id"]])
    assert (
        world.client.post(f"/v1/leases/{active['id']}/activate", headers=a.admin).status_code == 200
    )
    a.invite_manager()
    sub = [
        x["user_id"]
        for x in world.client.get("/v1/users", headers=a.admin).json()["items"]
        if x["user_id"] != a.admin_sub
    ][0]
    up = world.client.post(
        "/v1/documents/upload-url",
        headers=a.admin,
        json={
            "owner_type": "tenant",
            "owner_id": t["id"],
            "type": "other",
            "filename": "a.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
        },
    ).json()
    doc = world.c.store.documents(a.id).get(up["document_id"])
    assert doc is not None
    import boto3

    boto3.client("s3", region_name="ap-southeast-1").put_object(
        Bucket="anjung-docs-test", Key=doc["s3_key"], Body=b"x"
    )
    on_object_created(world.c.store.table, doc["s3_key"], world.c.clock)
    on_scan_result(world.c.store.table, world.c.storage, doc["s3_key"], "clean", world.c.clock)  # type: ignore[arg-type]
    c = world.client
    c.post(
        "/v1/invoice-runs",
        headers={**a.admin, "Idempotency-Key": "seed-run-1"},
        json={"period": "2018-12"},
    )
    rent = [
        i
        for i in c.get("/v1/invoices?limit=100", headers=a.admin).json()["items"]
        if i["period"] == "2018-12" and i["line_items"][0]["type"] == "rent"
    ][0]

    def charge(key: str) -> dict[str, Any]:
        r = c.post(
            f"/v1/leases/{active['id']}/charges",
            headers={**a.admin, "Idempotency-Key": key},
            json={
                "description": "seed",
                "category": "repair",
                "amount": {"amount_minor": 1000, "currency": "MYR"},
                "due_date": "2018-12-15",
            },
        )
        assert r.status_code == 201, r.text
        return dict(r.json())

    void_inv, paid_inv = charge("seed-charge-1"), charge("seed-charge-2")
    pr = c.post(
        f"/v1/invoices/{paid_inv['id']}/payments/manual",
        headers={**a.admin, "Idempotency-Key": "seed-pay-1"},
        json={
            "method": "cash",
            "amount": {"amount_minor": 1000, "currency": "MYR"},
            "received_at": "2018-11-20T03:00:00Z",
            "reason": "seed",
        },
    )
    assert pr.status_code == 201, pr.text
    from datetime import UTC, datetime

    from app.core.money import Money
    from app.domains.billing.payments import record_unmatched
    from app.integrations.payments.base import PaymentEvent

    unmatched = record_unmatched(
        world.c,
        a.id,
        PaymentEvent(
            "fake",
            "evt-seed",
            "txn-seed",
            "NOSUCHREF",
            Money(5000),
            "succeeded",
            datetime(2018, 11, 20, tzinfo=UTC),
        ),
        "unknown_reference",
    )
    ids = {
        "invoice_id": rent["id"],
        "void_invoice_id": void_inv["id"],
        "paid_invoice_id": paid_inv["id"],
        "payment_id": unmatched["id"],
        "property_id": p["id"],
        "unit_id": u["id"],
        "user_id": sub,
        "org_id": a.id,
        "tenant_id": t["id"],
        "lease_id": draft["id"],
        "active_lease_id": active["id"],
        "document_id": up["document_id"],
        "empty_property_id": a.new_property("empty")["id"],
        "empty_unit_id": a.new_unit(p["id"], "A-unit-empty")["id"],
        "spare_tenant_id": a.new_tenant("a-spare@example.com")["id"],
        "request_id": world.client.post(
            "/v1/maintenance-requests",
            headers=a.admin,
            json={"title": "t", "description": "d", "category": "other", "unit_id": u["id"]},
        ).json()["id"],
        "privacy_request_id": world.client.post(
            "/v1/privacy/requests", headers=a.admin, json={"type": "access", "tenant_id": t["id"]}
        ).json()["id"],
    }
    return a, b, ids


def snapshot(client: TestClient, a: Org) -> dict[str, Any]:
    h = a.admin
    return {
        "org": client.get("/v1/org", headers=h).json(),
        "users": client.get("/v1/users", headers=h).json(),
        "properties": client.get("/v1/properties?limit=100", headers=h).json(),
        "units": [
            client.get(f"/v1/properties/{p['id']}/units", headers=h).json()
            for p in client.get("/v1/properties?limit=100", headers=h).json()["items"]
        ],
        "audit": client.get("/v1/audit-log?limit=100", headers=h).json(),
        "tenants": client.get("/v1/tenants?limit=100", headers=h).json(),
        "leases": client.get("/v1/leases?limit=100", headers=h).json(),
        "invoices": client.get("/v1/invoices?limit=100", headers=h).json(),
        "unmatched": client.get("/v1/payments/unmatched", headers=h).json(),
        "runs": client.get("/v1/invoice-runs", headers=h).json(),
        "maintenance": client.get("/v1/maintenance-requests?limit=100", headers=h).json(),
    }


@pytest.mark.parametrize("case", FOREIGN_ID, ids=lambda c: f"{c.method} {c.path}")
def test_foreign_id_returns_404_and_changes_nothing(
    case: Case, world: World, two_orgs: tuple[Org, Org, dict[str, str]]
) -> None:
    a, b, ids = two_orgs
    before = snapshot(world.client, a)
    users_before = set(world.c.identity.users)  # type: ignore[attr-defined]
    path = case.url(ids)
    payload = case.payload(ids)
    r = call(world, case, ids, b.admin, payload)
    assert r.status_code == case.expect, f"{r.status_code} {r.text}"
    assert r.json()["code"] == ("not_found" if case.expect == 404 else "validation_error")
    for v in ids.values():
        assert v not in r.text or v in path
    assert snapshot(world.client, a) == before
    assert set(world.c.identity.users) == users_before  # type: ignore[attr-defined]
    # same call as org A works, so the 404 above was about tenancy and not a bad request
    ok = call(world, case, ids, a.admin, payload)
    assert ok.status_code in case.ok, f"control call failed: {ok.status_code} {ok.text}"


@pytest.mark.parametrize("case", SELF_SCOPED, ids=lambda c: f"{c.method} {c.path}")
def test_self_scoped_never_touches_other_org(
    case: Case, world: World, two_orgs: tuple[Org, Org, dict[str, str]]
) -> None:
    a, b, ids = two_orgs
    before = snapshot(world.client, a)
    r = call(world, case, ids, b.admin, case.body)
    assert r.status_code in (200, 201, 202), r.text
    leaked = [
        v for v in (a.id, a.admin_sub, "A-secret", "A-unit", *ids.values()) if v and v in r.text
    ]
    assert not leaked, f"org A data in org B response: {leaked}"
    assert snapshot(world.client, a) == before


@pytest.mark.parametrize("case", PLATFORM, ids=lambda c: f"{c.method} {c.path}")
def test_platform_routes_reject_every_other_role(
    case: Case, world: World, two_orgs: tuple[Org, Org, dict[str, str]]
) -> None:
    a, _, ids = two_orgs
    tokens = {
        "org_admin": a.admin,
        "property_manager": a.invite_manager(),
        "tenant": a.tenant(),
        "vendor": world.token("v", a.id, ["vendor"]),
        "no_auth": {},
    }
    for role, headers in tokens.items():
        r = call(world, case, ids, headers, case.body)
        assert r.status_code == (401 if role == "no_auth" else 403), f"{role}: {r.status_code}"
    assert (
        world.client.get(f"/v1/platform/organizations/{a.id}", headers=world.platform).json()[
            "status"
        ]
        == "active"
    )


def test_cursor_from_another_org_is_rejected(
    world: World, two_orgs: tuple[Org, Org, dict[str, str]]
) -> None:
    a, b, _ = two_orgs
    for i in range(3):
        a.new_property(f"extra{i}")
        b.new_property(f"extra{i}")
    cursor = world.client.get("/v1/properties?limit=1", headers=a.admin).json()["next_cursor"]
    assert (
        world.client.get(f"/v1/properties?limit=1&cursor={cursor}", headers=a.admin).status_code
        == 200
    )
    r = world.client.get(f"/v1/properties?limit=1&cursor={cursor}", headers=b.admin)
    assert r.status_code == 400 and r.json()["code"] == "bad_request"
    # and a cursor for one list cannot drive another list in the same org
    assert world.client.get(f"/v1/users?cursor={cursor}", headers=a.admin).status_code == 400


def test_unit_ids_cannot_be_reached_through_another_orgs_property(
    world: World, two_orgs: tuple[Org, Org, dict[str, str]]
) -> None:
    a, b, ids = two_orgs
    bp = b.new_property()
    # B's property id with A's unit id still resolves only inside B
    assert world.client.get(f"/v1/units/{ids['unit_id']}", headers=b.admin).status_code == 404
    assert (
        world.client.get(f"/v1/properties/{bp['id']}/units", headers=b.admin).json()["items"] == []
    )
    assert world.client.get(f"/v1/properties/{bp['id']}/units", headers=a.admin).status_code == 404


def test_suspended_org_is_blocked_but_others_are_not(
    world: World, two_orgs: tuple[Org, Org, dict[str, str]]
) -> None:
    a, b, _ = two_orgs
    world.client.patch(
        f"/v1/platform/organizations/{a.id}",
        headers=world.platform,
        json={"status": "suspended", "reason": "x"},
    )
    assert world.client.get("/v1/properties", headers=a.admin).status_code == 403
    assert world.client.get("/v1/properties", headers=b.admin).status_code == 200


@pytest.mark.parametrize("case", TENANT_ONLY, ids=lambda c: f"{c.method} {c.path}")
def test_tenant_only_routes_reject_staff_and_only_return_own_data(
    case: Case, world: World, two_orgs: tuple[Org, Org, dict[str, str]]
) -> None:
    a, b, ids = two_orgs
    for headers in (a.admin, a.invite_manager(), world.platform):
        assert world.client.request(case.method, case.url(ids), headers=headers).status_code == 403
    assert world.client.request(case.method, case.url(ids)).status_code == 401
    mine = b.new_tenant("b-own@example.com")
    r = call(world, case, ids, b.tenant_login(mine), case.body)
    assert r.status_code == 200
    assert not [v for v in (a.id, ids["tenant_id"], "a-tenant@example.com") if v in r.text]
    # a tenant of org A reaches only org A's data, whatever the token claims about the tenant of org B
    own = world.c.store.tenants(a.id).get(ids["tenant_id"])
    other = call(world, case, ids, a.tenant_login(own), case.body)  # type: ignore[arg-type]
    assert other.status_code == 200
    if "tenant_id" in other.json():
        assert other.json()["tenant_id"] == ids["tenant_id"]


@pytest.mark.parametrize("case", TENANT_FOREIGN_ID, ids=lambda c: f"{c.method} {c.path}")
def test_tenant_actions_on_another_orgs_record_return_404(
    case: Case, world: World, two_orgs: tuple[Org, Org, dict[str, str]]
) -> None:
    a, b, ids = two_orgs
    for headers in (a.admin, a.invite_manager(), b.admin):
        assert call(world, case, ids, headers, case.body).status_code == 403
    before = snapshot(world.client, a)
    outsider = b.tenant_login(b.new_tenant("b-signer@example.com"))
    r = call(world, case, ids, outsider, case.body)
    assert r.status_code == 404 and r.json()["code"] == "not_found", r.text
    assert snapshot(world.client, a) == before

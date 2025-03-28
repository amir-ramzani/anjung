"""A company's plan (contract v0.12.0): the platform admin sets the limits, the org admin divides them up."""

from datetime import timedelta
from typing import Any

from app.domains.organisations.plan import send_reminders
from app.lambdas.pre_token_generation.handler import resolve_claims
from fastapi.testclient import TestClient

from tests.conftest import Org, World
from tests.integration.billing_helpers import Setup

ADDR = {"line1": "x", "city": "x", "state": "x", "postcode": "50000"}


def set_plan(world: World, org: Org, **plan: Any) -> Any:
    return world.client.patch(
        f"/v1/platform/organizations/{org.id}/plan",
        headers=world.platform,
        json={**plan, "reason": "new contract"},
    )


def new_property(client: TestClient, headers: dict[str, str], name: str = "P") -> Any:
    return client.post(
        "/v1/properties", headers=headers, json={"name": name, "type": "landed", "address": ADDR}
    )


def test_the_platform_sets_the_plan_and_the_org_sees_it(world: World, client: TestClient) -> None:
    org = world.make_org()
    assert client.get("/v1/org", headers=org.admin).json()["plan"] == {
        "unit_limit": None,
        "active_until": None,
        "units_used": 0,
        "status": "active",
        "read_only_until": None,
    }
    r = set_plan(world, org, unit_limit=2, active_until="2019-12-31")
    assert r.status_code == 200, r.text
    assert (
        r.json()["plan"]["unit_limit"] == 2 and r.json()["plan"]["read_only_until"] == "2020-01-30"
    )
    # partial: the end date stays when only the limit changes
    assert set_plan(world, org, unit_limit=3).json()["plan"]["active_until"] == "2019-12-31"
    me = client.get("/v1/me", headers=org.admin).json()
    assert me["plan"]["unit_limit"] == 3
    # an org admin cannot change their own plan
    assert (
        client.patch("/v1/org", headers=org.admin, json={"plan": {"unit_limit": 99}}).status_code
        == 422
    )
    assert (
        client.patch(
            f"/v1/platform/organizations/{org.id}/plan",
            headers=org.admin,
            json={"unit_limit": 99, "reason": "x"},
        ).status_code
        == 403
    )
    audit = [
        e["action"] for e in client.get("/v1/audit-log?limit=20", headers=org.admin).json()["items"]
    ]
    assert "org.plan" in audit
    # tenants do not see the plan
    assert client.get("/v1/me", headers=org.tenant_login(org.new_tenant())).json()["plan"] is None


def test_the_plan_can_be_set_when_creating_the_company(world: World, client: TestClient) -> None:
    r = client.post(
        "/v1/platform/organizations",
        headers=world.platform,
        json={
            "name": "Sewa",
            "initial_admin": {"email": "boss@example.com"},
            "reason": "new customer",
            "plan": {"unit_limit": 5, "active_until": "2019-06-30"},
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["plan"]["unit_limit"] == 5 and r.json()["plan"]["status"] == "active"
    listed = client.get("/v1/platform/organizations", headers=world.platform).json()["items"]
    assert next(o for o in listed if o["name"] == "Sewa")["plan"]["active_until"] == "2019-06-30"


def test_the_unit_limit_covers_the_whole_organisation(world: World, client: TestClient) -> None:
    org = world.make_org()
    pm = org.invite_manager()
    set_plan(world, org, unit_limit=3)
    a = new_property(client, org.admin, "A").json()
    b = new_property(client, pm, "B").json()
    for _ in range(5):
        new_property(client, org.admin, "Empty")  # empty properties are free
    add = lambda h, pid, label, **x: client.post(  # noqa: E731
        f"/v1/properties/{pid}/units",
        headers=h,
        json={"label": label, "default_rent": {"amount_minor": 1, "currency": "MYR"}, **x},
    )
    assert add(org.admin, a["id"], "A-1").status_code == 201
    house = add(pm, b["id"], "H", letting_mode="rooms").json()
    assert add(org.admin, a["id"], "A-2").status_code == 201
    r = add(pm, b["id"], "B-1")  # the plan is the company's: managers and admins alike
    assert (
        r.status_code == 409
        and r.json()["code"] == "quota_exceeded"
        and "plan allows 3 units" in r.json()["message"]
    )
    # rooms and parking bays are free
    assert add(pm, b["id"], "R1", type="room", parent_unit_id=house["id"]).status_code == 201
    assert add(pm, b["id"], "P1", type="parking").status_code == 201
    assert client.get("/v1/org", headers=org.admin).json()["plan"]["units_used"] == 3
    # a deactivated property frees its units; bringing it back is checked again
    client.post(f"/v1/properties/{b['id']}/deactivate", headers=org.admin)
    assert add(org.admin, a["id"], "A-3").status_code == 201
    assert client.post(f"/v1/properties/{b['id']}/reactivate", headers=org.admin).status_code == 409
    # lowering the limit below what is in use removes nothing
    assert set_plan(world, org, unit_limit=1).json()["plan"]["units_used"] == 3


def test_the_org_admin_divides_the_plan_up(world: World, client: TestClient) -> None:
    org = world.make_org()
    set_plan(world, org, unit_limit=10, active_until="2019-06-30")
    a = client.post(
        "/v1/users/invite",
        headers=org.admin,
        json={"email": "a@example.com", "roles": ["property_manager"], "unit_quota": 6},
    )
    assert a.status_code == 201, a.text
    b = client.post(
        "/v1/users/invite",
        headers=org.admin,
        json={"email": "b@example.com", "roles": ["property_manager"], "unit_quota": 5},
    )
    assert b.status_code == 422 and "at most 4" in b.json()["details"][0]["issue"]
    assert (
        client.post(
            "/v1/users/invite",
            headers=org.admin,
            json={"email": "b@example.com", "roles": ["property_manager"], "unit_quota": 4},
        ).status_code
        == 201
    )
    # changing a manager's own limit counts only the others
    assert (
        client.patch(
            f"/v1/users/{a.json()['user_id']}", headers=org.admin, json={"unit_quota": 6}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/v1/users/{a.json()['user_id']}", headers=org.admin, json={"unit_quota": 7}
        ).status_code
        == 422
    )
    # staff access cannot outlast the organisation's
    late = client.post(
        "/v1/users/invite",
        headers=org.admin,
        json={
            "email": "c@example.com",
            "roles": ["property_manager"],
            "active_until": "2019-07-01",
        },
    )
    assert late.status_code == 422 and late.json()["details"][0]["field"] == "active_until"
    assert (
        client.patch(
            f"/v1/users/{a.json()['user_id']}",
            headers=org.admin,
            json={"active_until": "2019-06-30"},
        ).status_code
        == 200
    )


def test_after_the_end_date_staff_can_read_and_tenants_carry_on(
    world: World, client: TestClient, clock: Any
) -> None:
    s = Setup(world, deposits=[], recurring_charges=[])
    s.run("2018-12")
    inv = s.rent_invoice()
    set_plan(world, s.org, active_until=str((clock.now() - timedelta(days=1)).date()))
    world.c.org_status.clear()
    assert client.get("/v1/org", headers=s.org.admin).json()["plan"]["status"] == "read_only"
    assert client.get("/v1/leases", headers=s.org.admin).status_code == 200  # reading works
    r = new_property(client, s.org.admin)
    assert (
        r.status_code == 403
        and r.json()["code"] == "plan_read_only"
        and "view and export" in r.json()["message"]
    )
    # money received can still be recorded, and privacy requests answered
    pay = client.post(
        f"/v1/invoices/{inv['id']}/payments/manual",
        headers={**s.org.admin, "Idempotency-Key": "plan-ro-1"},
        json={
            "amount": {"amount_minor": 1000, "currency": "MYR"},
            "method": "cash",
            "received_at": "2018-12-02T03:00:00Z",
            "reason": "paid at the office",
        },
    )
    assert pay.status_code in (200, 201), pay.text
    # tenants are unaffected
    assert client.get("/v1/invoices", headers=s.tenant_h).status_code == 200
    assert (
        client.post(
            "/v1/maintenance-requests",
            headers=s.tenant_h,
            json={"title": "t", "description": "d", "category": "other", "unit_id": s.unit["id"]},
        ).status_code
        == 201
    )


def test_after_the_grace_period_the_organisation_is_closed(
    world: World, client: TestClient, clock: Any
) -> None:
    org = world.make_org()
    t = org.new_tenant()
    tok = org.tenant_login(t)
    set_plan(world, org, active_until=str((clock.now() - timedelta(days=31)).date()))
    world.c.org_status.clear()
    for h in (org.admin, tok):
        r = client.get("/v1/me", headers=h)
        assert r.status_code == 403 and r.json()["code"] == "plan_ended"
    sub = world.c.identity.sub_for(t["email"])  # type: ignore[attr-defined]
    try:
        resolve_claims(world.c.store.table, sub, clock.now().date())
        raise AssertionError("sign-in should be refused")
    except RuntimeError as e:
        assert str(e) == "plan_ended"
    # the platform can renew, and everything works again
    set_plan(world, org, active_until="2022-01-01")
    world.c.org_status.clear()
    assert client.get("/v1/me", headers=org.admin).status_code == 200


def test_org_admins_are_reminded_before_the_end(
    world: World, client: TestClient, clock: Any
) -> None:
    org = world.make_org("Rumah Kita")
    org.invite_manager()  # managers are not emailed
    until = (clock.now() + timedelta(days=7)).date()
    set_plan(world, org, active_until=str(until))
    email = world.c.channels["email"]
    before = len(email.sent)  # type: ignore[attr-defined]
    assert send_reminders(world.c, org.id) == 1
    assert send_reminders(world.c, org.id) == 0  # once per day and admin
    msg = email.sent[-1]  # type: ignore[attr-defined]
    assert len(email.sent) == before + 1 and msg.template == "plan_ending"  # type: ignore[attr-defined]
    assert "Rumah Kita" in msg.subject and "7 day" in msg.subject and "view and export" in msg.text
    clock.set(clock.now() + timedelta(days=1))  # 6 days left: no reminder that day
    assert send_reminders(world.c, org.id) == 0

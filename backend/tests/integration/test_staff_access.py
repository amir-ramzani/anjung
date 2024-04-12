"""Staff access window (contract v0.5.0): disable, or limit to dates; enforced on every request."""

from datetime import UTC, date, datetime

import pytest
from app.core.ids import new_id
from app.lambdas.pre_token_generation.handler import resolve_claims
from fastapi.testclient import TestClient

from tests.conftest import Org, World


def invite(org: Org, **extra: object) -> tuple[str, dict[str, str]]:
    r = org.w.client.post(
        "/v1/users/invite",
        headers=org.admin,
        json={
            "email": f"pm-{new_id().lower()}@example.com",
            "roles": ["property_manager"],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    sub = r.json()["user_id"]
    return sub, org.w.token(sub, org.id, ["property_manager"])


def on(world: World, y: int, m: int, d: int) -> None:  # midday in Kuala Lumpur
    world.c.clock.set(datetime(y, m, d, 4, 0, tzinfo=UTC))  # type: ignore[attr-defined]


def inactive(r: object) -> bool:
    return r.status_code == 403 and r.json()["code"] == "account_inactive"  # type: ignore[attr-defined]


def test_access_ends_after_the_last_day_and_comes_back_when_extended(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    sub, pm = invite(org, active_until="2018-12-31")  # the clock starts on 2018-11-20
    assert client.get("/v1/properties", headers=pm).status_code == 200
    user = next(
        u for u in client.get("/v1/users", headers=org.admin).json()["items"] if u["user_id"] == sub
    )
    assert (user["active_until"], user["access"]) == ("2018-12-31", "invited")

    on(world, 2018, 12, 31)  # the last day still counts
    assert client.get("/v1/properties", headers=pm).status_code == 200
    on(world, 2019, 1, 1)
    r = client.get("/v1/properties", headers=pm)  # the same token: no waiting for it to expire
    assert inactive(r) and "31-12-2018" in r.json()["message"]
    assert inactive(client.get("/v1/me", headers=pm))
    with pytest.raises(RuntimeError, match="user_inactive"):
        resolve_claims(world.c.store.table, sub, date(2019, 1, 1))  # and no new sign-in either
    listed = client.get("/v1/users", headers=org.admin).json()["items"]
    assert next(u for u in listed if u["user_id"] == sub)["access"] == "expired"

    r = client.patch(f"/v1/users/{sub}", headers=org.admin, json={"active_until": "2019-12-31"})
    assert r.status_code == 200 and r.json()["access"] == "invited"
    assert client.get("/v1/properties", headers=pm).status_code == 200
    assert resolve_claims(world.c.store.table, sub, date(2019, 1, 1))["org_id"] == org.id

    r = client.patch(
        f"/v1/users/{sub}", headers=org.admin, json={"active_until": None}
    )  # no end date
    assert r.status_code == 200 and r.json()["active_until"] is None


def test_deactivate_is_immediate_and_reactivate_restores_their_portfolio(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    sub, pm = invite(org)
    p = client.post(
        "/v1/properties",
        headers=pm,
        json={
            "name": "PM Tower",
            "type": "condominium",
            "address": {"line1": "1 Jalan A", "city": "KL", "state": "WP", "postcode": "50000"},
        },
    ).json()

    assert (
        client.patch(f"/v1/users/{sub}", headers=org.admin, json={"status": "disabled"}).json()[
            "access"
        ]
        == "disabled"
    )
    for path in (
        "/v1/properties",
        f"/v1/properties/{p['id']}",
        "/v1/tenants",
        "/v1/leases",
        "/v1/invoices",
        "/v1/maintenance-requests",
        "/v1/reports/occupancy",
    ):
        assert inactive(client.get(path, headers=pm)), path
    assert inactive(
        client.post(
            "/v1/tenants", headers=pm, json={"full_name": "X", "email": "x-tenant@example.com"}
        )
    )
    # nothing was taken away: the admin still sees it under their name
    assert client.get(f"/v1/properties/{p['id']}", headers=org.admin).json()["manager_id"] == sub

    assert (
        client.patch(f"/v1/users/{sub}", headers=org.admin, json={"status": "active"}).json()[
            "access"
        ]
        == "active"
    )
    assert [x["id"] for x in client.get("/v1/properties", headers=pm).json()["items"]] == [p["id"]]


def test_access_can_start_later_and_dates_are_checked(world: World, client: TestClient) -> None:
    org = world.make_org()
    sub, pm = invite(org, active_from="2018-12-01", active_until="2019-11-30")
    r = client.get("/v1/properties", headers=pm)
    assert inactive(r) and "01-12-2018" in r.json()["message"]
    assert (
        next(
            u
            for u in client.get("/v1/users", headers=org.admin).json()["items"]
            if u["user_id"] == sub
        )["access"]
        == "scheduled"
    )
    on(world, 2018, 12, 1)
    assert client.get("/v1/properties", headers=pm).status_code == 200

    bad = client.patch(
        f"/v1/users/{sub}", headers=org.admin, json={"active_until": "2018-11-30"}
    )  # before the start
    assert bad.status_code == 422
    assert (
        client.post(
            "/v1/users/invite",
            headers=org.admin,
            json={
                "email": "x@example.com",
                "roles": ["property_manager"],
                "active_from": "2019-01-02",
                "active_until": "2019-01-01",
            },
        ).status_code
        == 422
    )
    # an expired or disabled manager cannot be given a property
    client.patch(f"/v1/users/{sub}", headers=org.admin, json={"status": "disabled"})
    prop = client.post(
        "/v1/properties",
        headers=org.admin,
        json={
            "name": "H",
            "type": "landed",
            "address": {"line1": "1", "city": "KL", "state": "WP", "postcode": "50000"},
        },
    ).json()
    assert (
        client.patch(
            f"/v1/properties/{prop['id']}", headers=org.admin, json={"manager_id": sub}
        ).status_code
        == 422
    )


def test_admins_are_covered_too_but_cannot_lock_themselves_out(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    r = client.post(
        "/v1/users/invite",
        headers=org.admin,
        json={"email": "boss2@example.com", "roles": ["org_admin"], "active_until": "2018-11-30"},
    )
    assert r.status_code == 201
    boss2 = world.token(r.json()["user_id"], org.id, ["org_admin"])
    assert client.get("/v1/users", headers=boss2).status_code == 200
    on(world, 2018, 12, 1)
    assert inactive(client.get("/v1/users", headers=boss2))
    assert (
        client.patch(
            f"/v1/users/{org.admin_sub}", headers=org.admin, json={"status": "disabled"}
        ).status_code
        == 409
    )


def test_staff_see_their_own_access_dates(world: World, client: TestClient) -> None:
    org = world.make_org()
    _, pm = invite(org, active_until="2019-11-20")
    assert client.get("/v1/me", headers=pm).json()["active_until"] == "2019-11-20"
    me = client.get("/v1/me", headers=org.admin).json()
    assert me["active_until"] is None and me["active_from"] is None

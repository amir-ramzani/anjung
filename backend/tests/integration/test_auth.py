import time
from collections.abc import Callable

import pytest
from app.core.config import Settings
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from tests.conftest import ISSUER, PLATFORM_ORG_ID, World, bearer


def me(client: TestClient, token: str | None):  # type: ignore[no-untyped-def]
    return client.get("/v1/me", headers=bearer(token) if token else {})


def test_no_token_is_401_with_envelope(client: TestClient) -> None:
    r = me(client, None)
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "unauthorized" and body["request_id"] == r.headers["X-Request-Id"]


def test_valid_token(world: World, client: TestClient, make_token: Callable[..., str]) -> None:
    org = world.make_org()
    r = me(
        client,
        make_token(sub=org.admin_sub, org_id=org.id, roles=["org_admin"], email="a@example.com"),
    )
    assert r.status_code == 200
    assert r.json() == {
        "user_id": org.admin_sub,
        "org_id": org.id,
        "roles": ["org_admin"],
        "tenant_id": None,
        "email": "a@example.com",
        "locale": "en",
        "active_from": None,  # no access dates set by an admin
        "active_until": None,
        "branding": None,  # the organisation has not switched branding on
        "plan": {  # no limits set by the platform
            "unit_limit": None,
            "active_until": None,
            "units_used": 0,
            "status": "active",
            "read_only_until": None,
        },
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": int(time.time()) - 3600},
        {"iss": "https://evil.example/pool"},
        {"client_id": "other-client"},
        {"token_use": "id"},
        {"client_id": None},
    ],
)
def test_bad_tokens_rejected(
    world: World, client: TestClient, make_token: Callable[..., str], overrides: dict
) -> None:
    org = world.make_org()
    assert me(client, make_token(org_id=org.id, **overrides)).status_code == 401


def test_wrong_signature_and_alg_none(world: World, client: TestClient, make_token) -> None:  # type: ignore[no-untyped-def]
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # the test key resolver always returns the real public key, so a token signed by another key fails
    assert me(client, make_token(key=other)).status_code == 401
    assert me(client, "not.a.jwt").status_code == 401
    assert me(client, "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0.").status_code == 401


def test_missing_tenancy_claims_forbidden(world: World, client: TestClient, make_token) -> None:  # type: ignore[no-untyped-def]
    assert me(client, make_token(org_id=None)).status_code == 403
    assert me(client, make_token(roles=None)).status_code == 403
    assert me(client, make_token(roles=[])).status_code == 403
    assert me(client, make_token(roles=["superuser"])).status_code == 403


def test_roles_may_be_json_string(world: World, client: TestClient, make_token) -> None:  # type: ignore[no-untyped-def]
    org = world.make_org()
    r = me(client, make_token(sub=org.admin_sub, org_id=org.id, roles='["org_admin"]'))
    assert r.status_code == 200 and r.json()["roles"] == ["org_admin"]


def test_staff_token_without_membership_is_refused(
    world: World, client: TestClient, make_token
) -> None:  # type: ignore[no-untyped-def]
    org = (
        world.make_org()
    )  # a well-formed staff token, but this person is not (or no longer) a member
    r = me(client, make_token(sub="not-a-member", org_id=org.id, roles=["property_manager"]))
    assert r.status_code == 403 and r.json()["code"] == "account_inactive"


def test_unknown_org_forbidden(world: World, client: TestClient, make_token) -> None:  # type: ignore[no-untyped-def]
    assert me(client, make_token(org_id="01JD8Z3K9Q4M7T2V6X8B5N1C0B")).status_code == 403


def test_rbac_role_matrix(world: World, client: TestClient) -> None:
    org = world.make_org()
    pm = org.invite_manager()
    assert client.get("/v1/org", headers=pm).status_code == 200
    assert client.patch("/v1/org", headers=pm, json={"name": "x"}).status_code == 403
    assert client.get("/v1/users", headers=pm).status_code == 403
    assert client.get("/v1/audit-log", headers=pm).status_code == 403
    assert client.get("/v1/platform/organizations", headers=org.admin).status_code == 403
    assert client.get("/v1/properties", headers=org.tenant()).status_code == 403
    assert client.get("/v1/org", headers=world.platform).status_code == 403


def test_issuer_constant_matches_settings(settings: Settings) -> None:
    assert settings.issuer == ISSUER and PLATFORM_ORG_ID

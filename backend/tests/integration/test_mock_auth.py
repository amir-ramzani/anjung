import pytest
from app.bootstrap import bootstrap_platform
from app.container import Container
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.conftest import PLATFORM_ORG_ID


def test_mock_mode_full_flow(settings: Settings, ddb) -> None:  # type: ignore[no-untyped-def]
    c = Container.build(settings.model_copy(update={"auth_mode": "mock"}), ddb_client=ddb)
    client = TestClient(create_app(c))
    bootstrap_platform(
        c.store.table,
        c.identity,
        c.clock,
        platform_org_id=PLATFORM_ORG_ID,
        admin_email="root@example.com",
    )

    def login(email: str) -> dict[str, str]:
        r = client.post("/dev/token", json={"email": email})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    root = login("root@example.com")
    r = client.post(
        "/v1/platform/organizations",
        headers=root,
        json={"name": "Acme", "initial_admin": {"email": "boss@example.com"}, "reason": "local"},
    )
    assert r.status_code == 201
    dup = client.post(
        "/v1/platform/organizations",
        headers=root,
        json={"name": "Acme2", "initial_admin": {"email": "Boss@example.com"}, "reason": "local"},
    )
    assert dup.status_code == 409
    boss = login("boss@example.com")
    assert client.get("/v1/me", headers=boss).json()["roles"] == ["org_admin"]
    nobody = client.post("/dev/token", json={"email": "nobody@example.com"})
    assert nobody.status_code == 403 and nobody.json()["code"] == "sign_in_denied"
    assert nobody.json()["details"] == [{"field": "email", "issue": "no_unique_membership"}]
    assert nobody.json()["message"].startswith("No account uses this email")  # not the raw code
    client.patch(
        f"/v1/platform/organizations/{r.json()['id']}",
        headers=root,
        json={"status": "suspended", "reason": "x"},
    )
    suspended = client.post("/dev/token", json={"email": "boss@example.com"})
    assert suspended.status_code == 403
    assert suspended.json()["details"][0]["issue"] == "org_not_active"


def test_mock_mode_is_refused_outside_local(settings: Settings) -> None:
    with pytest.raises(ValidationError):
        settings.model_copy(update={"env": "prod", "auth_mode": "mock"}).model_validate(
            {**settings.model_dump(), "env": "prod", "auth_mode": "mock"}
        )


def test_dev_route_absent_in_cognito_mode(client: TestClient) -> None:
    assert client.post("/dev/token", json={"email": "a@example.com"}).status_code == 404


def test_dev_helpers_simulate_the_gateway_and_show_the_outbox(settings: Settings, ddb) -> None:  # type: ignore[no-untyped-def]
    c = Container.build(settings.model_copy(update={"auth_mode": "mock"}), ddb_client=ddb)
    client = TestClient(create_app(c))
    assert set(client.get("/dev/outbox").json()) == {"email", "whatsapp"}
    r = client.post("/dev/pay", json={"reference": "ANJNOSUCH", "amount_minor": 100})
    assert r.status_code == 200 and r.json() == {"webhook_status": 200, "body": {"received": True}}
    parked, _ = c.store.table.query(pk=f"ORG#{PLATFORM_ORG_ID}#UNMATCHED", sk_prefix="PAY#")
    assert len(parked) == 1  # the unknown reference went through the real webhook and processor
    assert client.post("/dev/jobs/integrity_check", json={}).json() == {
        "enqueued": "integrity_check"
    }

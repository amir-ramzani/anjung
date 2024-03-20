import os
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import boto3
import jwt
import pytest
from app.bootstrap import bootstrap_platform
from app.container import Container
from app.core import tracing  # noqa: E402
from app.core.clock import FixedClock
from app.core.config import Settings
from app.core.ids import new_id
from app.integrations.identity import InMemoryIdentityProvider
from app.main import create_app
from app.repositories.schema import table_definition
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from moto import mock_aws
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

SPANS = (
    InMemorySpanExporter()
)  # every span of every test lands here, so tests can assert what a trace looked like
tracing.configure("anjung-test", extra_exporters=[SPANS])

REGION = "ap-southeast-1"
POOL = "ap-southeast-1_TEST"
CLIENT_ID = "spa-client"
PLATFORM_ORG_ID = "01JD8Z3K9Q4M7T2V6X8B5N1C0A"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"


@pytest.fixture(autouse=True)
def _clear_spans() -> None:
    SPANS.clear()


@pytest.fixture(autouse=True)
def _clear_report_cache() -> None:
    from app.domains.reports import service

    service._cache.clear()  # noqa: SLF001 - reports cache for 60 s per task; tests must not see each other's


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SECURITY_TOKEN"):
        monkeypatch.setenv(k, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


@pytest.fixture(scope="session")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


needs_ddb_local = pytest.mark.skipif(
    not os.environ.get("ANJUNG_TEST_DDB_ENDPOINT"),
    reason="atomicity under parallel requests is only meaningful on DynamoDB Local "
    "(moto does not isolate concurrent transactions). Set ANJUNG_TEST_DDB_ENDPOINT.",
)


@pytest.fixture
def table_name() -> str:
    """Unique per test when running against DynamoDB Local, so tests never share data."""
    return (
        f"anjung-test-{new_id().lower()}"
        if os.environ.get("ANJUNG_TEST_DDB_ENDPOINT")
        else "anjung-main"
    )


@pytest.fixture
def settings(table_name: str) -> Settings:
    return Settings(
        table_name=table_name,
        env="test",
        aws_region=REGION,
        cognito_pool_id=POOL,
        cognito_client_ids=[CLIENT_ID],  # type: ignore[arg-type]
        platform_org_id=PLATFORM_ORG_ID,
        cursor_hmac_keys=["k" * 40],  # type: ignore[arg-type]
        org_status_cache_ttl_seconds=0,
        docs_bucket="anjung-docs-test",
    )


@pytest.fixture
def ddb(table_name: str) -> Iterator[Any]:
    """DynamoDB: moto by default. Set ANJUNG_TEST_DDB_ENDPOINT (for example http://localhost:8000)
    to run against DynamoDB Local instead. Transactions there are truly atomic, so the parallel
    tests (lease activation, letting mode) are only conclusive in that mode."""
    endpoint = os.environ.get("ANJUNG_TEST_DDB_ENDPOINT")
    local = (
        boto3.client("dynamodb", region_name=REGION, endpoint_url=endpoint) if endpoint else None
    )
    # a client created before moto starts is not intercepted, so it talks to the real endpoint
    with mock_aws():
        client = local or boto3.client("dynamodb", region_name=REGION)
        client.create_table(**table_definition(table_name))
        boto3.client("s3", region_name=REGION).create_bucket(
            Bucket="anjung-docs-test",
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        try:
            yield client
        finally:
            if local:
                local.delete_table(TableName=table_name)


@pytest.fixture
def identity() -> InMemoryIdentityProvider:
    return InMemoryIdentityProvider()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2018, 11, 20, 3, 0, tzinfo=UTC))


@pytest.fixture
def container(
    settings: Settings,
    ddb: Any,
    identity: InMemoryIdentityProvider,
    clock: FixedClock,
    rsa_key: rsa.RSAPrivateKey,
) -> Container:
    return Container.build(
        settings,
        ddb_client=ddb,
        identity=identity,
        key_resolver=lambda _token: rsa_key.public_key(),
        clock=clock,
        s3_client=boto3.client("s3", region_name=REGION),
    )


@pytest.fixture
def client(container: Container) -> TestClient:
    return TestClient(create_app(container), raise_server_exceptions=False)


@pytest.fixture
def make_token(rsa_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    def _make(
        sub: str = "user-1",
        org_id: str | None = PLATFORM_ORG_ID,
        roles: Any = ("org_admin",),
        *,
        key: Any = None,
        **overrides: Any,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": sub,
            "iss": ISSUER,
            "client_id": CLIENT_ID,
            "token_use": "access",
            "iat": now,
            "exp": now + 1800,
        }
        if org_id is not None:
            claims["org_id"] = org_id
        if roles is not None:
            claims["roles"] = roles if isinstance(roles, str) else list(roles)
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or rsa_key, algorithm="RS256", headers={"kid": "test"})

    return _make


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class World:
    """Seeded platform org plus helpers to create organisations through the real API."""

    def __init__(self, client: TestClient, container: Container, make_token: Callable[..., str]):
        self.client, self.c, self._mk = client, container, make_token
        sub = bootstrap_platform(
            container.store.table,
            container.identity,
            container.clock,
            platform_org_id=PLATFORM_ORG_ID,
            admin_email="root@example.com",
        )
        self.platform_sub = sub
        self.platform = bearer(
            make_token(sub=sub, org_id=PLATFORM_ORG_ID, roles=["platform_admin"])
        )

    def token(self, sub: str, org_id: str, roles: list[str], **claims: Any) -> dict[str, str]:
        return bearer(self._mk(sub=sub, org_id=org_id, roles=roles, **claims))

    def make_org(self, name: str = "Acme", *, signed_agreements: bool = False) -> "Org":
        """`signed_agreements`: keep the default rule that tenants sign before activation (contract
        v0.7.0). Off for most tests, which activate leases directly; test_lease_agreement turns it on."""
        email = f"admin-{new_id().lower()}@example.com"
        r = self.client.post(
            "/v1/platform/organizations",
            headers=self.platform,
            json={"name": name, "initial_admin": {"email": email}, "reason": "test"},
        )
        assert r.status_code == 201, r.text
        org_id = r.json()["id"]
        sub = self.c.identity.sub_for(email)  # type: ignore[attr-defined]
        org = Org(self, org_id, sub)
        if not signed_agreements:  # written directly so it leaves no audit entry for tests to count
            meta = self.c.store.org(org_id).get_meta()
            assert meta is not None
            meta["settings"] = {**(meta.get("settings") or {}), "require_signed_agreement": False}
            self.c.store.table.put(meta)
        return org


class Org:
    def __init__(self, world: World, org_id: str, admin_sub: str) -> None:
        self.w, self.id, self.admin_sub = world, org_id, admin_sub
        self.admin = world.token(admin_sub, org_id, ["org_admin"])

    def invite_manager(self, email: str | None = None) -> dict[str, str]:
        email = email or f"pm-{new_id().lower()}@example.com"
        r = self.w.client.post(
            "/v1/users/invite",
            headers=self.admin,
            json={"email": email, "roles": ["property_manager"]},
        )
        assert r.status_code == 201, r.text
        return self.w.token(r.json()["user_id"], self.id, ["property_manager"])

    def tenant(self) -> dict[str, str]:
        return self.w.token(new_id(), self.id, ["tenant"])

    def new_property(self, name: str = "Menara") -> dict[str, Any]:
        r = self.w.client.post(
            "/v1/properties",
            headers=self.admin,
            json={
                "name": name,
                "type": "condominium",
                "address": {"line1": "1 Jalan A", "city": "KL", "state": "WP", "postcode": "50000"},
            },
        )
        assert r.status_code == 201, r.text
        return dict(r.json())

    def new_tenant(self, email: str | None = None, **extra: Any) -> dict[str, Any]:
        r = self.w.client.post(
            "/v1/tenants",
            headers=self.admin,
            json={
                "full_name": "Siti Aminah",
                "email": email or f"t-{new_id().lower()}@example.com",
                **extra,
            },
        )
        assert r.status_code == 201, r.text
        return dict(r.json())

    def tenant_login(self, tenant: dict[str, Any]) -> dict[str, str]:
        """Invite the tenant and complete first login (links the Cognito sub), like the Post Confirmation trigger."""
        from app.lambdas.post_confirmation.handler import link_tenant

        r = self.w.client.post(f"/v1/tenants/{tenant['id']}/invite", headers=self.admin)
        assert r.status_code == 202, r.text
        sub = self.w.c.identity.sub_for(tenant["email"])  # type: ignore[attr-defined]
        assert link_tenant(self.w.c.store.table, sub, tenant["email"], self.w.c.clock.now())
        return self.w.token(sub, self.id, ["tenant"], tenant_id=tenant["id"])

    def new_lease(self, unit_id: str, tenant_ids: list[str], **over: Any) -> dict[str, Any]:
        body = {
            "unit_id": unit_id,
            "tenant_ids": tenant_ids,
            "start_date": "2018-12-01",
            "end_date": "2019-11-30",
            "monthly_rent": {"amount_minor": 150000, "currency": "MYR"},
            "deposits": [
                {"type": "security", "amount": {"amount_minor": 300000, "currency": "MYR"}}
            ],
            "due_day": 5,
            **over,
        }
        r = self.w.client.post("/v1/leases", headers=self.admin, json=body)
        assert r.status_code == 201, r.text
        return dict(r.json())

    def new_unit(self, property_id: str, label: str = "A-1") -> dict[str, Any]:
        r = self.w.client.post(
            f"/v1/properties/{property_id}/units",
            headers=self.admin,
            json={"label": label, "default_rent": {"amount_minor": 150000, "currency": "MYR"}},
        )
        assert r.status_code == 201, r.text
        return dict(r.json())


@pytest.fixture
def world(client: TestClient, container: Container, make_token: Callable[..., str]) -> World:
    return World(client, container, make_token)

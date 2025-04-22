"""Per-environment settings (plan section 1: dev, staging, prod; region decision D4 in P0-07)."""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_REGION = "ap-southeast-5"  # Malaysia (decision D4): tenant data stays in-country


@dataclass(frozen=True)
class EnvConfig:
    name: str
    prod: bool
    region: str = DEFAULT_REGION
    nat_gateways: int = 1
    interface_endpoints: bool = False
    api_cpu: int = 256
    api_memory: int = 512
    api_desired: int = 1
    api_min: int = 1
    api_max: int = 2
    worker_cpu: int = 256
    worker_memory: int = 512
    worker_max: int = 2
    log_retention_days: int = 30
    spa_origin: str = "http://localhost:5173"
    callback_urls: list[str] = field(
        default_factory=lambda: ["http://localhost:5173/auth/callback"]
    )
    logout_urls: list[str] = field(default_factory=lambda: ["http://localhost:5173/"])
    cognito_domain_prefix: str = ""
    certificate_arn: str | None = None  # without one, the ALB serves HTTP: acceptable for dev only
    alarm_email: str | None = None
    # the app's sender (SES). Unset: no real email; outside prod the app records messages in its log instead.
    # The domain must be verified in SES first (plan P4-04); anjung.my is a placeholder until P0-13
    ses_from_address: str | None = None
    postman_client: bool = True
    platform_org_id: str = "01JD8Z3K9Q4M7T2V6X8B5N1C0A"
    enable_fake_provider: bool = True

    @property
    def stack_prefix(self) -> str:
        return f"Anjung-{self.name.capitalize()}"


ENVS: dict[str, EnvConfig] = {
    "dev": EnvConfig(name="dev", prod=False, cognito_domain_prefix="anjung-dev"),
    "staging": EnvConfig(
        name="staging",
        prod=False,
        nat_gateways=2,
        interface_endpoints=True,
        api_cpu=512,
        api_memory=1024,
        api_desired=2,
        api_min=2,
        api_max=4,
        worker_cpu=512,
        worker_memory=1024,
        worker_max=3,
        spa_origin="https://app.staging.anjung.my",
        callback_urls=["https://app.staging.anjung.my/auth/callback"],
        logout_urls=["https://app.staging.anjung.my/"],
        cognito_domain_prefix="anjung-staging",
        ses_from_address="no-reply@mail.staging.anjung.my",
    ),
    "prod": EnvConfig(
        name="prod",
        prod=True,
        nat_gateways=2,
        interface_endpoints=True,
        api_cpu=512,
        api_memory=1024,
        api_desired=2,
        api_min=2,
        api_max=6,
        worker_cpu=512,
        worker_memory=1024,
        worker_max=4,
        log_retention_days=90,
        spa_origin="https://app.anjung.my",
        callback_urls=["https://app.anjung.my/auth/callback"],
        logout_urls=["https://app.anjung.my/"],
        cognito_domain_prefix="anjung",
        ses_from_address="no-reply@mail.anjung.my",
        postman_client=False,
        enable_fake_provider=False,
    ),
}

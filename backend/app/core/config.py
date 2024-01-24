"""Settings validated at startup. The service refuses to start on missing or bad values."""

from typing import Annotated, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.ids import is_id


def _split(v: object) -> object:
    if isinstance(v, str):
        return [p.strip() for p in v.split(",") if p.strip()]
    return v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    env: Literal["local", "dev", "staging", "prod", "test"] = "local"
    aws_region: str = "ap-southeast-5"
    table_name: str = "anjung-main"
    dynamodb_endpoint_url: str | None = None
    log_level: str = "INFO"

    auth_mode: Literal["cognito", "mock"] = "cognito"  # mock: local development only
    cognito_pool_id: str
    cognito_client_ids: Annotated[list[str], NoDecode]
    cognito_suppress_invite_email: bool = False
    platform_org_id: str
    cursor_hmac_keys: Annotated[list[str], NoDecode]  # first signs, all verify
    cors_allow_origins: Annotated[list[str], NoDecode] = []
    org_status_cache_ttl_seconds: int = 60
    kms_key_id: str | None = None
    docs_bucket: str | None = None
    s3_endpoint_url: str | None = None  # local MinIO; unset on AWS
    s3_public_endpoint_url: str | None = None  # host used in presigned URLs (browser reachable)
    invite_ttl_days: int = 7
    # retention purge (decision D5): None = off. Days after a lease ends / an organisation closes
    retention_financial_days: int | None = None
    retention_closed_org_days: int | None = None
    payment_provider: str = "fake"  # key of the provider that creates new QR codes
    fake_webhook_secrets: Annotated[list[str], NoDecode] = ["local-fake-webhook-secret"]  # noqa: RUF012
    fake_qr_validity_seconds: int = 300
    qr_rate_per_minute: int = 20
    app_base_url: str = "http://localhost:5173"  # pay links point here
    # where the API is reachable from a mail client (logo images in emails); defaults to app_base_url,
    # which serves /v1 on the same origin locally and on the NAS
    public_api_url: str | None = None
    ses_from_address: str | None = (
        None  # set to use Amazon SES, otherwise a recording fake is used locally
    )
    ses_configuration_set: str | None = None
    otel_exporter_otlp_endpoint: str | None = (
        None  # the collector sidecar, e.g. http://localhost:4318. Unset: no export
    )
    queue_urls_json: str | None = (
        None  # {"housekeeping": url, ...}. Unset: jobs run inline (local, test)
    )

    _split_lists = field_validator(
        "cognito_client_ids",
        "cursor_hmac_keys",
        "cors_allow_origins",
        "fake_webhook_secrets",
        mode="before",
    )(_split)

    @model_validator(mode="after")
    def _mock_is_local_only(self) -> "Settings":
        if self.auth_mode == "mock" and self.env not in ("local", "test"):
            raise ValueError("AUTH_MODE=mock is only allowed when ENV is local or test")
        if self.env == "prod" and self.payment_provider == "fake":
            raise ValueError("the fake payment provider is not allowed in prod")
        return self

    @field_validator("platform_org_id")
    @classmethod
    def _platform_id(cls, v: str) -> str:
        if not is_id(v):
            raise ValueError("platform_org_id must be a ULID")
        return v

    @field_validator("cursor_hmac_keys")
    @classmethod
    def _keys(cls, v: list[str]) -> list[str]:
        if not v or any(len(k) < 32 for k in v):
            raise ValueError("cursor_hmac_keys needs at least one key of 32+ characters")
        return v

    @field_validator("cognito_client_ids")
    @classmethod
    def _clients(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("cognito_client_ids must not be empty")
        return v

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.aws_region}.amazonaws.com/{self.cognito_pool_id}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"

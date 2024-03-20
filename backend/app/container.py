"""Dependency container held on `app.state.container`."""

import json
from dataclasses import dataclass, field
from typing import Any

import boto3

from app.core.auth import KeyResolver, OrgStatusCache, TokenVerifier, jwks_key_resolver
from app.core.clock import Clock, SystemClock
from app.core.config import Settings
from app.core.crypto import FieldEncryptor, LocalFieldEncryptor
from app.core.pagination import CursorCodec
from app.core.ratelimit import RateLimiter
from app.integrations.channels import FakeChannel, NotificationChannel, SesChannel
from app.integrations.identity import CognitoIdentityProvider, IdentityProvider
from app.integrations.mock_auth import DevTokenIssuer, MockIdentityProvider
from app.integrations.payments.base import PaymentProvider
from app.integrations.storage import DocumentStorage
from app.repositories.dynamo import DynamoTable
from app.repositories.store import Store
from app.worker.envelope import JobQueue


@dataclass
class Container:
    settings: Settings
    store: Store
    clock: Clock
    identity: IdentityProvider
    verifier: TokenVerifier
    org_status: OrgStatusCache
    encryptor: FieldEncryptor | None = None
    dev_issuer: DevTokenIssuer | None = None
    storage: DocumentStorage | None = None
    jobs: JobQueue = None  # type: ignore[assignment]  # set in build()
    payments: dict[str, PaymentProvider] = field(default_factory=dict)
    channels: dict[str, NotificationChannel] = field(default_factory=dict)
    qr_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(20))
    # the public set-password link endpoints, per client IP
    invite_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(30))

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        ddb_client: Any | None = None,
        identity: IdentityProvider | None = None,
        key_resolver: KeyResolver | None = None,
        clock: Clock | None = None,
        s3_client: Any | None = None,
    ) -> "Container":
        ddb = ddb_client or boto3.client(
            "dynamodb", region_name=settings.aws_region, endpoint_url=settings.dynamodb_endpoint_url
        )
        store = Store(
            DynamoTable(ddb, settings.table_name),
            CursorCodec([k.encode() for k in settings.cursor_hmac_keys]),
        )
        clock = clock or SystemClock()
        dev_issuer = None
        if settings.auth_mode == "mock":
            dev_issuer = DevTokenIssuer(
                store.table, settings.issuer, settings.cognito_client_ids[0], clock
            )
            identity = identity or MockIdentityProvider(store.table)
            key_resolver = key_resolver or dev_issuer.key_resolver
        if identity is None:
            identity = CognitoIdentityProvider(
                boto3.client("cognito-idp", region_name=settings.aws_region),
                settings.cognito_pool_id,
                settings.cognito_suppress_invite_email,
            )
        encryptor = None
        if settings.kms_key_id:
            kms = boto3.client("kms", region_name=settings.aws_region)
            encryptor = FieldEncryptor(kms, settings.kms_key_id)
        elif settings.env in ("local", "test"):
            encryptor = LocalFieldEncryptor(settings.cursor_hmac_keys[0].encode())
        storage = None
        if settings.docs_bucket:
            s3 = s3_client or boto3.client(
                "s3", region_name=settings.aws_region, endpoint_url=settings.s3_endpoint_url
            )
            signer = s3
            if settings.s3_public_endpoint_url and s3_client is None:
                signer = boto3.client(
                    "s3",
                    region_name=settings.aws_region,
                    endpoint_url=settings.s3_public_endpoint_url,
                )
            storage = DocumentStorage(s3, signer, settings.docs_bucket, clock, settings.kms_key_id)
        container = cls(
            settings=settings,
            store=store,
            clock=clock,
            identity=identity,
            verifier=TokenVerifier(
                settings.issuer,
                settings.cognito_client_ids,
                key_resolver or jwks_key_resolver(settings.jwks_url),
            ),
            org_status=OrgStatusCache(
                lambda org_id: store.org(org_id).get_meta(), settings.org_status_cache_ttl_seconds
            ),
            encryptor=encryptor,
            dev_issuer=dev_issuer,
            storage=storage,
        )
        container.jobs = _build_jobs(container, settings)
        container.qr_limiter = RateLimiter(settings.qr_rate_per_minute)
        if settings.ses_from_address:
            container.channels["email"] = SesChannel(
                boto3.client("sesv2", region_name=settings.aws_region),
                settings.ses_from_address,
                settings.ses_configuration_set,
            )
        elif settings.env != "prod":  # local and test: record messages instead of sending them
            container.channels["email"] = FakeChannel("email")
        if settings.env != "prod":
            container.channels["whatsapp"] = FakeChannel(
                "whatsapp"
            )  # the real route waits on decision D3
        if (
            settings.env != "prod"
        ):  # the fake provider is not registered in prod: its webhook returns 404
            from datetime import timedelta

            from app.integrations.payments.fake import FakeProvider

            container.payments["fake"] = FakeProvider(
                settings.fake_webhook_secrets, timedelta(seconds=settings.fake_qr_validity_seconds)
            )
        return container


def _build_jobs(c: "Container", settings: Settings) -> JobQueue:
    from app.worker.handlers.housekeeping import build_handlers
    from app.worker.queue import InlineJobQueue, SqsJobQueue

    if settings.queue_urls_json:
        sqs = boto3.client("sqs", region_name=settings.aws_region)
        return SqsJobQueue(sqs, json.loads(settings.queue_urls_json))
    return InlineJobQueue(lambda: build_handlers(c))

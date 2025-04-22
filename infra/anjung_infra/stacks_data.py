from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy, SecretValue, Stack
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as sm
from aws_cdk import aws_ses as ses
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from anjung_infra import table_spec
from anjung_infra.config import EnvConfig

# logical name -> (visibility timeout seconds, maxReceiveCount, fifo). LLD 8.1
QUEUES: dict[str, tuple[int, int, bool]] = {
    "payment_events": (60, 5, True),
    "invoicing": (300, 3, False),
    "notifications": (60, 5, False),
    "housekeeping": (900, 3, False),
}


class DataStack(Stack):
    """Table, key, document bucket, queues and secrets. Everything here is RETAINed: data outlives stacks."""

    def __init__(self, scope: Construct, id: str, *, cfg: EnvConfig, **kw: object) -> None:
        super().__init__(scope, id, **kw)  # type: ignore[arg-type]
        self.key = kms.Key(
            self,
            "Key",
            alias=f"alias/anjung-{cfg.name}",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
            description="Anjung data key",
        )

        # ---- DynamoDB, generated from the backend's table definition
        spec = table_spec.load()
        self.table = ddb.Table(
            self,
            "Table",
            table_name="anjung-main",
            partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="SK", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            deletion_protection=True,
            encryption=ddb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=self.key,
            stream=ddb.StreamViewType.NEW_AND_OLD_IMAGES,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.RETAIN,
        )
        for g in spec["GlobalSecondaryIndexes"]:
            proj = g["Projection"]
            ptype = {
                "ALL": ddb.ProjectionType.ALL,
                "KEYS_ONLY": ddb.ProjectionType.KEYS_ONLY,
                "INCLUDE": ddb.ProjectionType.INCLUDE,
            }[proj["ProjectionType"]]
            pk, sk = (k["AttributeName"] for k in g["KeySchema"])
            self.table.add_global_secondary_index(
                index_name=g["IndexName"],
                partition_key=ddb.Attribute(name=pk, type=ddb.AttributeType.STRING),
                sort_key=ddb.Attribute(name=sk, type=ddb.AttributeType.STRING),
                projection_type=ptype,
                non_key_attributes=proj.get("NonKeyAttributes"),
            )

        # ---- documents: private, encrypted, versioned (LLD 10)
        self.docs_bucket = s3.Bucket(
            self,
            "Docs",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.key,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.POST, s3.HttpMethods.GET],
                    allowed_origins=[cfg.spa_origin],
                    allowed_headers=["*"],
                    max_age=3000,
                )
            ],
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="old-versions", noncurrent_version_expiration=Duration.days(365)
                ),
                s3.LifecycleRule(
                    id="quarantine", prefix="quarantine/", expiration=Duration.days(30)
                ),
                s3.LifecycleRule(
                    id="abort-uploads", abort_incomplete_multipart_upload_after=Duration.days(1)
                ),
            ],
        )

        # ---- queues, each with a 14 day DLQ (alarms live in the observability stack)
        self.queues: dict[str, sqs.Queue] = {}
        self.dlqs: dict[str, sqs.Queue] = {}
        for name, (visibility, receives, fifo) in QUEUES.items():
            suffix = ".fifo" if fifo else ""
            dlq = sqs.Queue(
                self,
                f"{name}Dlq",
                queue_name=f"anjung-{cfg.name}-{name}-dlq{suffix}",
                fifo=fifo or None,
                retention_period=Duration.days(14),
                encryption=sqs.QueueEncryption.SQS_MANAGED,
                enforce_ssl=True,
            )
            self.dlqs[name] = dlq
            self.queues[name] = sqs.Queue(
                self,
                name,
                queue_name=f"anjung-{cfg.name}-{name}{suffix}",
                fifo=fifo or None,
                content_based_deduplication=None,
                visibility_timeout=Duration.seconds(visibility),
                encryption=sqs.QueueEncryption.SQS_MANAGED,
                enforce_ssl=True,
                dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=receives, queue=dlq),
            )

        # ---- email feedback (LLD 9.2): SES → SNS → notifications queue, where the worker turns each
        # bounce or complaint into a `ses_feedback` job that suppresses the address
        self.email_feedback = sns.Topic(
            self,
            "EmailFeedback",
            topic_name=f"anjung-{cfg.name}-email-feedback",
            master_key=self.key,  # the events carry email addresses
            enforce_ssl=True,
        )
        # the event destination below adds the topic policy (SES of this account and configuration set)
        self.key.add_to_resource_policy(  # SES encrypts what it publishes to the topic
            iam.PolicyStatement(
                principals=[iam.ServicePrincipal("ses.amazonaws.com")],
                actions=["kms:GenerateDataKey*", "kms:Decrypt"],
                resources=["*"],
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            )
        )
        self.email_feedback.add_subscription(
            subs.SqsSubscription(self.queues["notifications"], raw_message_delivery=True)
        )
        self.email_config = ses.ConfigurationSet(
            self,
            "Email",
            configuration_set_name=f"anjung-{cfg.name}",
            reputation_metrics=True,
        )
        self.email_config.add_event_destination(
            "Feedback",
            destination=ses.EventDestination.sns_topic(self.email_feedback),
            events=[ses.EmailSendingEvent.BOUNCE, ses.EmailSendingEvent.COMPLAINT],
        )

        # ---- secrets (values for the gateway are set by hand, never in code)
        self.cursor_secret = sm.Secret(
            self,
            "CursorKey",
            secret_name=f"anjung/{cfg.name}/cursor",
            generate_secret_string=sm.SecretStringGenerator(
                secret_string_template="{}",
                generate_string_key="keys",
                password_length=48,
                exclude_punctuation=True,
            ),
        )
        self.payment_secret = sm.Secret(
            self,
            "Payment",
            secret_name=f"anjung/{cfg.name}/payment",
            secret_string_value=SecretValue.unsafe_plain_text(
                '{"api_key": "REPLACE_ME", "webhook_secret": "REPLACE_ME"}'
            ),
            description="Gateway API key and webhook secret. Replace after the gateway decision (D2).",
        )
        self.fake_webhook_secret = None
        if cfg.enable_fake_provider:
            self.fake_webhook_secret = sm.Secret(
                self,
                "FakeWebhook",
                secret_name=f"anjung/{cfg.name}/fake-webhook",
                generate_secret_string=sm.SecretStringGenerator(
                    password_length=40, exclude_punctuation=True
                ),
            )

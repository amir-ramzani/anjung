from __future__ import annotations

import json

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_applicationautoscaling as appscaling
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as patterns
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_scheduler as scheduler
from constructs import Construct

from anjung_infra.config import EnvConfig
from anjung_infra.stacks_auth import AuthStack
from anjung_infra.stacks_data import DataStack

# LLD 14.2: neither role can Scan or DeleteTable. DeleteItem is needed for lock and idempotency
# items inside transactions (unit labels, tenant emails, idempotency keys, documents).
TABLE_ACTIONS = [
    "dynamodb:GetItem",
    "dynamodb:Query",
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
    "dynamodb:DeleteItem",
    "dynamodb:BatchGetItem",
    "dynamodb:ConditionCheckItem",
]
WORKERS = ["payment_events", "invoicing", "notifications", "housekeeping"]
# Mail may only leave from our own domain or a subdomain of it (e.g. no-reply@mail.anjung.my).
SENDER_PATTERNS = ["*@anjung.my", "*@*.anjung.my"]

# LLD 8.4. Times are Asia/Kuala_Lumpur. (queue, job type, cron). `*_all` jobs fan out per organisation.
SCHEDULES: list[tuple[str, str, str, str]] = [
    ("invoice-sweep", "invoicing", "invoice_sweep_all", "cron(0 2 * * ? *)"),
    ("overdue-sweep", "housekeeping", "overdue_sweep_all", "cron(0 3 * * ? *)"),
    ("lease-expiry", "housekeeping", "lease_expiry_all", "cron(30 3 * * ? *)"),
    ("integrity-check", "housekeeping", "integrity_check_all", "cron(0 4 * * ? *)"),
    ("reconcile-payments", "housekeeping", "reconcile_payments", "cron(0 5 * * ? *)"),
    ("maintenance-autoclose", "housekeeping", "maintenance_autoclose_all", "cron(0 6 * * ? *)"),
    ("reminder-sweep", "notifications", "reminder_sweep_all", "cron(0 9 * * ? *)"),
    ("release-deferred", "notifications", "release_deferred_all", "rate(15 minutes)"),
    # contract v0.9.0: both do nothing until the retention periods are set (decision D5)
    ("retention-purge", "housekeeping", "retention_purge_all", "cron(30 4 ? * SUN *)"),
    ("closed-org-purge", "housekeeping", "closed_org_purge", "cron(0 5 ? * SUN *)"),
    # contract v0.12.0: plan reminders to org admins, 30, 7 and 1 day before the plan ends
    ("plan-reminders", "housekeeping", "plan_reminders_all", "cron(0 8 * * ? *)"),
]


class AppStack(Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        cfg: EnvConfig,
        vpc: ec2.IVpc,
        data: DataStack,
        auth: AuthStack,
        image_tag: str = "latest",
        **kw: object,
    ) -> None:
        super().__init__(scope, id, **kw)  # type: ignore[arg-type]
        retention = (
            logs.RetentionDays.THREE_MONTHS
            if cfg.log_retention_days >= 90
            else logs.RetentionDays.ONE_MONTH
        )
        self.log_groups: dict[str, logs.LogGroup] = {}

        def log_group(name: str) -> logs.LogGroup:
            g = logs.LogGroup(
                self, f"{name}Logs", retention=retention, removal_policy=RemovalPolicy.RETAIN
            )
            self.log_groups[name] = g
            return g

        self.repo = ecr.Repository(
            self,
            "Repo",
            repository_name=f"anjung-{cfg.name}",
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.IMMUTABLE
            if cfg.prod
            else ecr.TagMutability.MUTABLE,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[ecr.LifecycleRule(max_image_count=30)],
        )
        self.cluster = ecs.Cluster(
            self, "Cluster", vpc=vpc, container_insights_v2=ecs.ContainerInsights.ENABLED
        )
        image = ecs.ContainerImage.from_ecr_repository(self.repo, image_tag)

        queue_urls = {n: q.queue_url for n, q in data.queues.items()}
        queue_urls |= {f"{n}-dlq": q.queue_url for n, q in data.dlqs.items()}
        env = {
            "ENV": cfg.name,
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",  # the collector sidecar below
            "AWS_REGION": self.region,
            "TABLE_NAME": data.table.table_name,
            "DOCS_BUCKET": data.docs_bucket.bucket_name,
            "COGNITO_POOL_ID": auth.pool.user_pool_id,
            "COGNITO_CLIENT_IDS": ",".join(auth.client_ids),
            "PLATFORM_ORG_ID": cfg.platform_org_id,
            "QUEUE_URLS_JSON": self.to_json_string(queue_urls),
            "CORS_ALLOW_ORIGINS": cfg.spa_origin,
            "APP_BASE_URL": cfg.spa_origin,
            "PAYMENT_PROVIDER": "fake" if cfg.enable_fake_provider else "gateway_a",
            "LOG_LEVEL": "INFO",
        }
        if cfg.ses_from_address:  # without it, no real email (outside prod the app logs messages)
            env["SES_FROM_ADDRESS"] = cfg.ses_from_address
            # every send names the configuration set, so its bounces and complaints come back
            env["SES_CONFIGURATION_SET"] = data.email_config.configuration_set_name
        secrets = {"CURSOR_HMAC_KEYS": ecs.Secret.from_secrets_manager(data.cursor_secret, "keys")}
        if data.fake_webhook_secret is not None:
            secrets["FAKE_WEBHOOK_SECRETS"] = ecs.Secret.from_secrets_manager(
                data.fake_webhook_secret
            )

        def send_email(r: iam.Role) -> None:
            r.add_to_policy(
                iam.PolicyStatement(  # only from our own domain (LLD 14.2)
                    actions=["ses:SendEmail", "ses:SendRawEmail"],
                    resources=["*"],
                    conditions={"StringLike": {"ses:FromAddress": SENDER_PATTERNS}},
                )
            )

        def role(name: str) -> iam.Role:
            r = iam.Role(
                self, f"{name}TaskRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com")
            )
            r.add_to_policy(
                iam.PolicyStatement(
                    actions=TABLE_ACTIONS,  # TransactWriteItems is authorised by the per-item actions
                    resources=[data.table.table_arn, f"{data.table.table_arn}/index/*"],
                )
            )
            data.key.grant_encrypt_decrypt(r)  # table, bucket and secrets share the data key
            data.docs_bucket.grant_read_write(r, "org/*")
            data.docs_bucket.grant_read_write(r, "reports/*")
            data.cursor_secret.grant_read(r)
            return r

        otel_logs = log_group("otel")

        def with_collector(td: ecs.TaskDefinition, name: str) -> None:
            """AWS Distro for OpenTelemetry sidecar: receives OTLP on localhost, forwards spans to X-Ray (P5-05).
            Not essential: if it dies, requests still work and spans are dropped."""
            td.task_role.add_to_principal_policy(
                iam.PolicyStatement(
                    actions=[
                        "xray:PutTraceSegments",
                        "xray:PutTelemetryRecords",
                        "xray:GetSamplingRules",
                        "xray:GetSamplingTargets",
                        "xray:GetSamplingStatisticsSummaries",
                    ],
                    resources=[
                        "*"
                    ],  # X-Ray write actions do not support resource-level permissions
                )
            )
            td.add_container(
                "otel-collector",
                image=ecs.ContainerImage.from_registry(
                    "public.ecr.aws/aws-observability/aws-otel-collector:v0.41.1"
                ),
                command=["--config=/etc/ecs/ecs-default-config.yaml"],
                essential=False,
                memory_reservation_mib=128,
                logging=ecs.LogDrivers.aws_logs(stream_prefix=f"otel-{name}", log_group=otel_logs),
            )

        # ---- API
        api_role = role("Api")
        for q in data.queues.values():
            q.grant_send_messages(api_role)
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "cognito-idp:AdminCreateUser",
                    "cognito-idp:AdminDeleteUser",
                    "cognito-idp:AdminAddUserToGroup",
                    "cognito-idp:AdminRemoveUserFromGroup",
                    "cognito-idp:AdminDisableUser",
                    "cognito-idp:AdminEnableUser",
                    "cognito-idp:AdminUserGlobalSignOut",
                ],
                resources=[auth.pool.user_pool_arn],
            )
        )
        data.payment_secret.grant_read(api_role)
        if data.fake_webhook_secret is not None:
            data.fake_webhook_secret.grant_read(api_role)
        # welcome emails with a set-password link (tenants and staff) are sent at once by the API, not
        # queued, so the link never sits in a queue (LLD 11.2, 11.3); everything else goes through the worker
        send_email(api_role)

        self.api = patterns.ApplicationLoadBalancedFargateService(
            self,
            "Api",
            cluster=self.cluster,
            cpu=cfg.api_cpu,
            memory_limit_mib=cfg.api_memory,
            desired_count=cfg.api_desired,
            task_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            public_load_balancer=True,
            assign_public_ip=False,
            min_healthy_percent=100,
            max_healthy_percent=200,
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            protocol=elbv2.ApplicationProtocol.HTTPS
            if cfg.certificate_arn
            else elbv2.ApplicationProtocol.HTTP,
            certificate=None,
            redirect_http=bool(cfg.certificate_arn),
            task_image_options=patterns.ApplicationLoadBalancedTaskImageOptions(
                image=image,
                container_port=8080,
                environment=env,
                secrets=secrets,
                task_role=api_role,
                log_driver=ecs.LogDrivers.aws_logs(stream_prefix="api", log_group=log_group("api")),
                command=[
                    "uvicorn",
                    "app.main:app_factory",
                    "--factory",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8080",
                    "--workers",
                    "1",
                    "--loop",
                    "uvloop",
                ],
            ),
        )
        with_collector(self.api.task_definition, "api")
        self.api.target_group.configure_health_check(
            path="/readyz",
            interval=Duration.seconds(15),
            unhealthy_threshold_count=3,
            healthy_threshold_count=2,
        )
        self.api.target_group.set_attribute("deregistration_delay.timeout_seconds", "30")
        scaling = self.api.service.auto_scale_task_count(
            min_capacity=cfg.api_min, max_capacity=cfg.api_max
        )
        scaling.scale_on_cpu_utilization("Cpu", target_utilization_percent=60)
        scaling.scale_on_request_count(
            "Requests", requests_per_target=200, target_group=self.api.target_group
        )
        self.alb = self.api.load_balancer

        # ---- workers: one small service per queue (LLD 8, 14.1)
        self.workers: dict[str, ecs.FargateService] = {}
        for name in WORKERS:
            wrole = role(f"Worker{name}")
            data.queues[name].grant_consume_messages(wrole)
            data.dlqs[name].grant_send_messages(wrole)
            for q in data.queues.values():
                q.grant_send_messages(wrole)  # jobs enqueue follow-up jobs
            data.payment_secret.grant_read(wrole)
            wrole.add_to_policy(
                iam.PolicyStatement(
                    actions=[
                        "cognito-idp:AdminDeleteUser",
                        "cognito-idp:AdminDisableUser",
                        "cognito-idp:AdminUserGlobalSignOut",
                    ],
                    resources=[auth.pool.user_pool_arn],
                )
            )
            send_email(wrole)
            td = ecs.FargateTaskDefinition(
                self,
                f"Worker{name}Task",
                cpu=cfg.worker_cpu,
                memory_limit_mib=cfg.worker_memory,
                task_role=wrole,
            )
            td.add_container(
                "worker",
                image=image,
                environment=env,
                secrets=secrets,
                stop_timeout=Duration.seconds(120),
                command=["python", "-m", "app.worker.main", name],
                logging=ecs.LogDrivers.aws_logs(
                    stream_prefix=f"worker-{name}", log_group=log_group(name)
                ),
            )
            with_collector(td, name)
            svc = ecs.FargateService(
                self,
                f"Worker{name}",
                cluster=self.cluster,
                task_definition=td,
                desired_count=1,
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
                min_healthy_percent=100,
                max_healthy_percent=200,
                assign_public_ip=False,
            )
            wscale = svc.auto_scale_task_count(min_capacity=1, max_capacity=cfg.worker_max)
            wscale.scale_on_metric(
                f"Depth{name}",
                metric=data.queues[name].metric_approximate_number_of_messages_visible(
                    period=Duration.minutes(1)
                ),
                scaling_steps=[
                    appscaling.ScalingInterval(upper=10, change=-1),
                    appscaling.ScalingInterval(lower=100, change=+1),
                    appscaling.ScalingInterval(lower=1000, change=+2),
                ],
                adjustment_type=appscaling.AdjustmentType.CHANGE_IN_CAPACITY,
            )
            self.workers[name] = svc
        self.worker_task_roles = [w.task_definition.task_role for w in self.workers.values()]

        # ---- schedules: EventBridge Scheduler puts a standard envelope on the queue (LLD 8.4)
        sched_role = iam.Role(
            self, "SchedulerRole", assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com")
        )
        for q in data.queues.values():
            q.grant_send_messages(sched_role)
        for name, queue, job, cron in SCHEDULES:
            body = json.dumps(
                {
                    "type": job,
                    "org_id": cfg.platform_org_id,
                    "payload": {},
                    "version": 1,
                    "occurred_at": "<aws.scheduler.scheduled-time>",
                    "message_id": "<aws.scheduler.execution-id>",
                    "trace_id": "",
                }
            )
            scheduler.CfnSchedule(
                self,
                f"Sched{name}",
                name=f"anjung-{cfg.name}-{name}",
                schedule_expression=cron,
                schedule_expression_timezone="Asia/Kuala_Lumpur",
                flexible_time_window={"mode": "OFF"},
                target=scheduler.CfnSchedule.TargetProperty(
                    arn=data.queues[queue].queue_arn,
                    role_arn=sched_role.role_arn,
                    input=body,
                    retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(
                        maximum_retry_attempts=3
                    ),
                ),
            )

from __future__ import annotations

from aws_cdk import Duration, Stack
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as actions
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

from anjung_infra.config import EnvConfig
from anjung_infra.stacks_app import AppStack
from anjung_infra.stacks_data import DataStack

RUNBOOK = "docs/runbooks"  # every alarm names its runbook (plan P5-04)


class ObservabilityStack(Stack):
    """The alarms of LLD 14.4. Each one carries a runbook reference in its description."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        cfg: EnvConfig,
        data: DataStack,
        app: AppStack,
        **kw: object,
    ) -> None:
        super().__init__(scope, id, **kw)  # type: ignore[arg-type]
        topic = sns.Topic(self, "Alarms", topic_name=f"anjung-{cfg.name}-alarms")
        if cfg.alarm_email:
            topic.add_subscription(subs.EmailSubscription(cfg.alarm_email))
        self.topic = topic
        self.alarms: dict[str, cw.Alarm] = {}

        def alarm(
            key: str,
            metric: cw.IMetric,
            threshold: float,
            op: cw.ComparisonOperator,
            runbook: str,
            periods: int = 1,
            missing: cw.TreatMissingData = cw.TreatMissingData.NOT_BREACHING,
        ) -> None:
            a = cw.Alarm(
                self,
                key,
                metric=metric,
                threshold=threshold,
                comparison_operator=op,
                evaluation_periods=periods,
                treat_missing_data=missing,
                alarm_description=f"See {RUNBOOK}/{runbook}.md",
                alarm_name=f"anjung-{cfg.name}-{key}",
            )
            a.add_alarm_action(actions.SnsAction(topic))
            self.alarms[key] = a

        gt = cw.ComparisonOperator.GREATER_THAN_THRESHOLD
        lt = cw.ComparisonOperator.LESS_THAN_THRESHOLD
        alb = app.alb
        # ---- API
        errors = alb.metrics.http_code_target(
            elbv2.HttpCodeTarget.TARGET_5XX_COUNT, period=Duration.minutes(5)
        )
        total = alb.metrics.request_count(period=Duration.minutes(5))
        alarm(
            "api-5xx",
            cw.MathExpression(
                expression="IF(total > 0, 100 * errors / total, 0)",
                using_metrics={"errors": errors, "total": total},
                period=Duration.minutes(5),
            ),
            1,
            gt,
            "api-errors",
        )
        alarm(
            "api-latency-p95",
            alb.metrics.target_response_time(statistic="p95", period=Duration.minutes(5)),
            1,
            gt,
            "api-latency",
            periods=2,
        )
        # ---- queues
        for name, dlq in data.dlqs.items():
            alarm(
                f"dlq-{name}",
                dlq.metric_approximate_number_of_messages_visible(period=Duration.minutes(1)),
                0,
                gt,
                "dead-letter-queue",
            )
            age_limit = (
                600 if name == "payment_events" else 3600
            )  # seconds: 10 min for money, 60 min for the rest
            alarm(
                f"age-{name}",
                data.queues[name].metric_approximate_age_of_oldest_message(
                    period=Duration.minutes(5)
                ),
                age_limit,
                gt,
                "queue-backlog",
            )
        # ---- DynamoDB
        alarm(
            "dynamodb-throttles",
            cw.Metric(
                namespace="AWS/DynamoDB",
                metric_name="ThrottledRequests",
                dimensions_map={"TableName": data.table.table_name},
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            0,
            gt,
            "dynamodb-throttling",
            periods=1,
        )
        # ---- tasks: running below desired for 5 minutes
        for name, svc in {"api": app.api.service, **app.workers}.items():
            running = cw.Metric(
                namespace="ECS/ContainerInsights",
                metric_name="RunningTaskCount",
                statistic="Minimum",
                dimensions_map={
                    "ClusterName": app.cluster.cluster_name,
                    "ServiceName": svc.service_name,
                },
                period=Duration.minutes(1),
            )
            alarm(
                f"tasks-{name}",
                running,
                1,
                lt,
                "task-health",
                periods=5,
                missing=cw.TreatMissingData.BREACHING,
            )
        # ---- SES reputation
        alarm(
            "ses-bounce-rate",
            cw.Metric(
                namespace="AWS/SES",
                metric_name="Reputation.BounceRate",
                statistic="Maximum",
                period=Duration.hours(1),
            ),
            0.05,
            gt,
            "email-reputation",
        )
        alarm(
            "ses-complaint-rate",
            cw.Metric(
                namespace="AWS/SES",
                metric_name="Reputation.ComplaintRate",
                statistic="Maximum",
                period=Duration.hours(1),
            ),
            0.001,
            gt,
            "email-reputation",
        )

        # ---- log-derived metrics: the application logs one JSON line per event (LLD 15.4)
        self.log_metric_filters = {
            "webhook-signature-failures": (
                '{ ($.route = "/webhooks/payments/{provider}") && ($.status = 401) }',
                5,
                "webhook-failures",
            ),
            "integrity-mismatch": ('{ $.msg = "integrity.mismatch" }', 0, "ledger-integrity"),
            "reconciliation-mismatch": (
                '{ $.msg = "reconciliation.mismatch" }',
                0,
                "reconciliation",
            ),
            "invoice-run-errors": (
                '{ $.msg = "invoice run completed with errors" }',
                0,
                "invoice-run-failure",
            ),
            "payment-unmatched": ('{ $.msg = "unmatched payment" }', 0, "unmatched-payments"),
        }
        for key, (pattern, threshold, runbook) in self.log_metric_filters.items():
            for group_name, group in app.log_groups.items():
                if group_name not in ("api", "payment_events", "housekeeping"):
                    continue
                m = logs.MetricFilter(
                    self,
                    f"mf-{key}-{group_name}",
                    log_group=group,
                    metric_namespace="Anjung",
                    metric_name=key.replace("-", "_"),
                    filter_pattern=logs.FilterPattern.literal(pattern),
                    metric_value="1",
                    default_value=0,
                )
                alarm(
                    f"{key}-{group_name}",
                    m.metric(statistic="Sum", period=Duration.minutes(5)),
                    threshold,
                    gt,
                    runbook,
                )
        self.dashboard = cw.Dashboard(self, "Dashboard", dashboard_name=f"anjung-{cfg.name}")
        self.dashboard.add_widgets(
            cw.GraphWidget(
                title="API 5xx and latency",
                left=[errors],
                right=[alb.metrics.target_response_time()],
            ),
            cw.GraphWidget(
                title="Dead letter queues",
                left=[
                    q.metric_approximate_number_of_messages_visible() for q in data.dlqs.values()
                ],
            ),
            cw.GraphWidget(
                title="Queue age",
                left=[q.metric_approximate_age_of_oldest_message() for q in data.queues.values()],
            ),
        )

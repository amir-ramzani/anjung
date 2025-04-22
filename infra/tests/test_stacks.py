"""The stacks against the design (LLD 14, plan P0 and P5). These run on `cdk synth` output, no AWS account needed."""

import json
from pathlib import Path
from typing import Any

from aws_cdk.assertions import Match

from anjung_infra import table_spec

ROOT = Path(__file__).resolve().parents[2]


def resources(t: Any, type_: str) -> dict[str, Any]:
    return dict(t.find_resources(type_))


def statements(t: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in resources(t, "AWS::IAM::Policy").values():
        out += r["Properties"]["PolicyDocument"]["Statement"]
    return out


def actions_of(stmt: dict[str, Any]) -> list[str]:
    a = stmt["Action"]
    return [a] if isinstance(a, str) else list(a)


def test_all_six_stacks_exist_with_the_naming_convention(dev: Any) -> None:
    assert {s.stack_name for s in dev.stacks.values()} == {
        f"Anjung-Dev-{n}" for n in ("Network", "Data", "Auth", "App", "Edge", "Observability")
    }


# ---- data -----------------------------------------------------------------------------------
def test_table_is_generated_from_the_backend_definition(dev: Any) -> None:
    spec = table_spec.load()
    [table] = resources(dev.t["data"], "AWS::DynamoDB::Table").values()
    p = table["Properties"]
    assert p["TableName"] == "anjung-main" and p["BillingMode"] == "PAY_PER_REQUEST"
    wanted = {g["IndexName"]: g for g in spec["GlobalSecondaryIndexes"]}
    got = {g["IndexName"]: g for g in p["GlobalSecondaryIndexes"]}
    assert set(got) == set(wanted) == {f"GSI{i}" for i in range(1, 6)}
    for name, g in got.items():
        assert g["KeySchema"] == wanted[name]["KeySchema"]
        assert g["Projection"]["ProjectionType"] == wanted[name]["Projection"]["ProjectionType"]
    assert sorted(got["GSI2"]["Projection"]["NonKeyAttributes"]) == sorted(
        wanted["GSI2"]["Projection"]["NonKeyAttributes"]
    )
    assert p["TimeToLiveSpecification"] == {"AttributeName": "ttl", "Enabled": True}
    assert p["StreamSpecification"]["StreamViewType"] == "NEW_AND_OLD_IMAGES"
    assert p["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"] is True
    assert p["DeletionProtectionEnabled"] is True and p["SSESpecification"]["SSEType"] == "KMS"
    assert table["DeletionPolicy"] == "Retain"


def test_key_rotates_and_data_is_retained(dev: Any) -> None:
    [key] = resources(dev.t["data"], "AWS::KMS::Key").values()
    assert key["Properties"]["EnableKeyRotation"] is True and key["DeletionPolicy"] == "Retain"
    for bucket in resources(dev.t["data"], "AWS::S3::Bucket").values():
        assert bucket["DeletionPolicy"] == "Retain"


def test_no_bucket_is_public_and_all_enforce_tls(dev: Any) -> None:
    for stack in ("data", "edge"):
        buckets = resources(dev.t[stack], "AWS::S3::Bucket")
        assert buckets
        for b in buckets.values():
            assert b["Properties"]["PublicAccessBlockConfiguration"] == {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            }
        policies = json.dumps(resources(dev.t[stack], "AWS::S3::BucketPolicy"))
        assert "aws:SecureTransport" in policies


def test_documents_bucket_is_kms_encrypted_versioned_and_lifecycled(dev: Any) -> None:
    [docs] = resources(dev.t["data"], "AWS::S3::Bucket").values()
    p = docs["Properties"]
    assert p["VersioningConfiguration"]["Status"] == "Enabled"
    assert (
        p["BucketEncryption"]["ServerSideEncryptionConfiguration"][0][
            "ServerSideEncryptionByDefault"
        ]["SSEAlgorithm"]
        == "aws:kms"
    )
    ids = {r["Id"] for r in p["LifecycleConfiguration"]["Rules"]}
    assert {"old-versions", "quarantine", "abort-uploads"} <= ids
    assert p["CorsConfiguration"]["CorsRules"][0]["AllowedMethods"] == ["POST", "GET"]


def test_queues_have_dead_letter_queues_and_the_right_shape(dev: Any) -> None:
    queues = resources(dev.t["data"], "AWS::SQS::Queue")
    by_name = {q["Properties"]["QueueName"]: q["Properties"] for q in queues.values()}
    for name in ("payment_events", "invoicing", "notifications", "housekeeping"):
        suffix = ".fifo" if name == "payment_events" else ""
        main, dlq = by_name[f"anjung-dev-{name}{suffix}"], by_name[f"anjung-dev-{name}-dlq{suffix}"]
        assert "RedrivePolicy" in main and dlq["MessageRetentionPeriod"] == 14 * 86400
        assert main.get("FifoQueue") is (True if name == "payment_events" else None)
    assert by_name["anjung-dev-payment_events.fifo"]["RedrivePolicy"]["maxReceiveCount"] == 5
    assert by_name["anjung-dev-invoicing"]["VisibilityTimeout"] == 300
    assert by_name["anjung-dev-housekeeping"]["VisibilityTimeout"] == 900


# ---- auth -----------------------------------------------------------------------------------
def test_cognito_plan_trigger_mfa_and_clients(dev: Any, prod: Any) -> None:
    [pool] = resources(dev.t["auth"], "AWS::Cognito::UserPool").values()
    p = pool["Properties"]
    assert p["UserPoolTier"] == "ESSENTIALS" and p["MfaConfiguration"] == "OPTIONAL"
    assert (
        p["EnabledMfas"] == ["SOFTWARE_TOKEN_MFA"]
        and p["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is True
    )
    assert p["Policies"]["PasswordPolicy"]["MinimumLength"] >= 12
    cfg = p["LambdaConfig"]["PreTokenGenerationConfig"]
    assert cfg["LambdaVersion"] == "V2_0"  # access-token customisation needs V2 (LLD 11.1)
    assert "PostConfirmation" in p["LambdaConfig"]
    names = {
        c["Properties"]["ClientName"]
        for c in resources(dev.t["auth"], "AWS::Cognito::UserPoolClient").values()
    }
    assert names == {"spa", "smoke", "postman"}
    prod_names = {
        c["Properties"]["ClientName"]
        for c in resources(prod.t["auth"], "AWS::Cognito::UserPoolClient").values()
    }
    assert prod_names == {"spa", "smoke"}  # no Postman in prod (LLD 16.3)
    groups = {
        g["Properties"]["GroupName"]
        for g in resources(dev.t["auth"], "AWS::Cognito::UserPoolGroup").values()
    }
    assert groups == {"platform_admin", "org_admin", "property_manager", "tenant"}


def test_spa_client_uses_pkce_code_flow_and_the_smoke_client_cannot_use_the_browser(
    dev: Any,
) -> None:
    clients = {
        c["Properties"]["ClientName"]: c["Properties"]
        for c in resources(dev.t["auth"], "AWS::Cognito::UserPoolClient").values()
    }
    assert (
        clients["spa"]["AllowedOAuthFlows"] == ["code"]
        and "GenerateSecret" not in clients["spa"]
        or clients["spa"].get("GenerateSecret") is False
    )
    assert "ALLOW_ADMIN_USER_PASSWORD_AUTH" in clients["smoke"]["ExplicitAuthFlows"]
    assert "AllowedOAuthFlows" not in clients["smoke"]


def test_trigger_lambdas_exist_in_the_backend_and_are_fast(dev: Any) -> None:
    fns = {
        f["Properties"]["Handler"]: f["Properties"]
        for f in resources(dev.t["auth"], "AWS::Lambda::Function").values()
        if "Handler" in f["Properties"]
    }
    assert set(fns) == {
        "app.lambdas.pre_token_generation.handler.handler",
        "app.lambdas.post_confirmation.handler.handler",
    }
    for handler, props in fns.items():
        module = ROOT / "backend" / (handler.rsplit(".", 1)[0].replace(".", "/") + ".py")
        assert module.exists(), handler
        assert props["Timeout"] <= 5 and props["Runtime"] == "python3.12"


def test_trigger_lambdas_have_only_what_they_need(dev: Any) -> None:
    for s in statements(dev.t["auth"]):
        for a in actions_of(s):
            assert a not in ("dynamodb:Scan", "dynamodb:DeleteTable", "dynamodb:*", "*"), a


# ---- application ----------------------------------------------------------------------------
def test_no_role_can_scan_or_drop_the_table_or_use_wildcards(dev: Any, prod: Any) -> None:
    for synth in (dev, prod):
        for s in statements(synth.t["app"]):
            for a in actions_of(s):
                assert a not in (
                    "dynamodb:Scan",
                    "dynamodb:DeleteTable",
                    "dynamodb:*",
                    "*",
                    "s3:*",
                    "sqs:*",
                ), a
            if any(a.startswith("dynamodb:") for a in actions_of(s)):
                assert s["Resource"] != "*"


def test_api_and_workers_send_email_only_from_our_domain(dev: Any) -> None:
    ses = [s for s in statements(dev.t["app"]) if any(a.startswith("ses:") for a in actions_of(s))]
    # one per worker service, and the API: welcome emails with a set-password link are sent at once,
    # not queued (LLD 11.2, 11.3)
    assert len(ses) == 5
    for s in ses:
        assert sorted(actions_of(s)) == ["ses:SendEmail", "ses:SendRawEmail"]
        assert s["Condition"] == {
            "StringLike": {"ses:FromAddress": ["*@anjung.my", "*@*.anjung.my"]}
        }
    api_policy = next(
        r
        for r in resources(dev.t["app"], "AWS::IAM::Policy").values()
        if "ApiTaskRole" in json.dumps(r["Properties"]["Roles"])
    )
    assert "ses:SendEmail" in json.dumps(api_policy)


def test_the_sender_address_reaches_the_api_and_workers(dev: Any, staging: Any, prod: Any) -> None:
    for synth, sender in (
        (dev, None),  # dev records messages in the log until SES is set up there
        (staging, "no-reply@mail.staging.anjung.my"),
        (prod, "no-reply@mail.anjung.my"),
    ):
        for td in resources(synth.t["app"], "AWS::ECS::TaskDefinition").values():
            [c] = [
                x for x in td["Properties"]["ContainerDefinitions"] if x["Name"] != "otel-collector"
            ]
            env = {e["Name"]: e["Value"] for e in c["Environment"]}
            assert env.get("SES_FROM_ADDRESS") == sender


def test_api_service_is_deployed_safely(dev: Any, prod: Any) -> None:
    for synth, desired in ((dev, 1), (prod, 2)):
        services = resources(synth.t["app"], "AWS::ECS::Service")
        api = next(
            s["Properties"] for s in services.values() if s["Properties"].get("LoadBalancers")
        )
        assert api["DesiredCount"] == desired
        assert api["DeploymentConfiguration"]["DeploymentCircuitBreaker"] == {
            "Enable": True,
            "Rollback": True,
        }
        assert api["DeploymentConfiguration"]["MinimumHealthyPercent"] == 100
        assert api["NetworkConfiguration"]["AwsvpcConfiguration"]["AssignPublicIp"] == "DISABLED"
        assert len(services) == 5  # api plus four workers
    [tg] = resources(dev.t["app"], "AWS::ElasticLoadBalancingV2::TargetGroup").values()
    assert (
        tg["Properties"]["HealthCheckPath"] == "/readyz"
        and tg["Properties"]["HealthCheckIntervalSeconds"] == 15
    )


def test_tasks_run_the_same_image_with_settings_and_secrets_injected(dev: Any) -> None:
    for td in resources(dev.t["app"], "AWS::ECS::TaskDefinition").values():
        [c] = [x for x in td["Properties"]["ContainerDefinitions"] if x["Name"] != "otel-collector"]
        env = {e["Name"] for e in c["Environment"]}
        assert {
            "TABLE_NAME",
            "DOCS_BUCKET",
            "COGNITO_POOL_ID",
            "COGNITO_CLIENT_IDS",
            "PLATFORM_ORG_ID",
            "QUEUE_URLS_JSON",
            "CORS_ALLOW_ORIGINS",
        } <= env
        assert "CURSOR_HMAC_KEYS" in {
            s["Name"] for s in c["Secrets"]
        }  # a secret, never a plain variable
        assert "CURSOR_HMAC_KEYS" not in env
        assert "Cpu" in td["Properties"] and c["LogConfiguration"]["LogDriver"] == "awslogs"


def test_workers_stop_gracefully_and_start_the_right_queue(dev: Any) -> None:
    cmds = []
    for td in resources(dev.t["app"], "AWS::ECS::TaskDefinition").values():
        [c] = [x for x in td["Properties"]["ContainerDefinitions"] if x["Name"] != "otel-collector"]
        if c["Command"][:3] == ["python", "-m", "app.worker.main"]:
            assert c["StopTimeout"] == 120  # LLD 14.1
            cmds.append(c["Command"][3])
    assert sorted(cmds) == ["housekeeping", "invoicing", "notifications", "payment_events"]


def test_schedules_follow_the_design_in_malaysian_time(dev: Any) -> None:
    sched = [s["Properties"] for s in resources(dev.t["app"], "AWS::Scheduler::Schedule").values()]
    assert len(sched) == 11  # 8 from the design, then the purges (v0.9.0) and plan reminders (v0.12.0)
    assert all(s["ScheduleExpressionTimezone"] == "Asia/Kuala_Lumpur" for s in sched)
    exprs = {s["Name"].split("anjung-dev-")[1]: s["ScheduleExpression"] for s in sched}
    assert (
        exprs["invoice-sweep"] == "cron(0 2 * * ? *)"
        and exprs["overdue-sweep"] == "cron(0 3 * * ? *)"
    )
    assert exprs["retention-purge"] == "cron(30 4 ? * SUN *)"
    assert exprs["plan-reminders"] == "cron(0 8 * * ? *)"
    assert (
        exprs["reminder-sweep"] == "cron(0 9 * * ? *)"
        and exprs["reconcile-payments"] == "cron(0 5 * * ? *)"
    )
    types = {json.loads(s["Target"]["Input"])["type"] for s in sched}
    from_backend = "".join(
        p.read_text() for p in (ROOT / "backend" / "app" / "worker" / "handlers").glob("*.py")
    )
    for job in types:
        assert f'"{job}"' in from_backend, (
            f"no worker handler registers {job}"
        )  # a schedule for a missing job is silent


def test_the_api_is_behind_a_waf_and_cdn_has_security_headers(dev: Any) -> None:
    assert resources(dev.t["edge"], "AWS::WAFv2::WebACLAssociation")
    [acl] = resources(dev.t["edge"], "AWS::WAFv2::WebACL").values()
    rules = {r["Name"] for r in acl["Properties"]["Rules"]}
    assert {"AWSManagedRulesCommonRuleSet", "RateLimitPerIp", "RateLimitWebhookPerIp"} <= rules
    [policy] = resources(dev.t["edge"], "AWS::CloudFront::ResponseHeadersPolicy").values()
    sec = policy["Properties"]["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"]
    assert {
        "StrictTransportSecurity",
        "ContentTypeOptions",
        "FrameOptions",
        "ReferrerPolicy",
        "ContentSecurityPolicy",
    } <= set(sec)
    [dist] = resources(dev.t["edge"], "AWS::CloudFront::Distribution").values()
    assert (
        dist["Properties"]["DistributionConfig"]["DefaultCacheBehavior"]["ViewerProtocolPolicy"]
        == "redirect-to-https"
    )
    assert resources(dev.t["edge"], "AWS::CloudFront::OriginAccessControl")


def test_production_differs_from_dev_where_it_should(dev: Any, prod: Any) -> None:
    assert not [
        e
        for e in json.dumps(prod.t["app"].to_json()).split('"Name": "PAYMENT_PROVIDER"')[1:2]
        if '"Value": "fake"' in e[:80]
    ]
    groups = [
        g["Properties"]["RetentionInDays"]
        for g in resources(prod.t["app"], "AWS::Logs::LogGroup").values()
    ]
    assert groups and all(d == 90 for d in groups)
    assert all(
        g["Properties"]["RetentionInDays"] == 30
        for g in resources(dev.t["app"], "AWS::Logs::LogGroup").values()
    )
    [repo] = resources(prod.t["app"], "AWS::ECR::Repository").values()
    assert (
        repo["Properties"]["ImageTagMutability"] == "IMMUTABLE"
        and repo["Properties"]["ImageScanningConfiguration"]["ScanOnPush"]
    )
    [pool] = resources(prod.t["auth"], "AWS::Cognito::UserPool").values()
    assert pool["Properties"]["DeletionProtection"] == "ACTIVE"
    secrets = {
        s["Properties"]["Name"]
        for s in resources(prod.t["data"], "AWS::SecretsManager::Secret").values()
    }
    assert "anjung/prod/fake-webhook" not in secrets and "anjung/dev/fake-webhook" in {
        s["Properties"]["Name"]
        for s in resources(dev.t["data"], "AWS::SecretsManager::Secret").values()
    }


def test_interface_endpoints_keep_traffic_off_the_nat_in_staging_and_prod(
    dev: Any, staging: Any
) -> None:
    def endpoints(synth: Any) -> int:
        return len(
            [
                e
                for e in resources(synth.t["network"], "AWS::EC2::VPCEndpoint").values()
                if e["Properties"].get("VpcEndpointType") == "Interface"
            ]
        )

    assert endpoints(dev) == 0 and endpoints(staging) == 7
    for synth in (dev, staging):  # gateway endpoints everywhere
        gateways = [
            e["Properties"]["ServiceName"]
            for e in resources(synth.t["network"], "AWS::EC2::VPCEndpoint").values()
            if e["Properties"].get("VpcEndpointType") == "Gateway"
        ]
        assert len(gateways) == 2


# ---- observability --------------------------------------------------------------------------
def test_every_alarm_points_at_a_runbook_that_exists(dev: Any) -> None:
    alarms = resources(dev.t["observability"], "AWS::CloudWatch::Alarm")
    assert len(alarms) >= 25
    for a in alarms.values():
        desc = a["Properties"]["AlarmDescription"]
        slug = desc.split("docs/runbooks/")[1].removesuffix(".md")
        assert (ROOT / "docs" / "runbooks" / f"{slug}.md").exists(), desc
        assert a["Properties"]["AlarmActions"], "an alarm nobody hears is not an alarm"


def test_the_alarms_of_lld_14_4_are_all_present(dev: Any) -> None:
    names = {
        a["Properties"]["AlarmName"].removeprefix("anjung-dev-")
        for a in resources(dev.t["observability"], "AWS::CloudWatch::Alarm").values()
    }
    for q in ("payment_events", "invoicing", "notifications", "housekeeping"):
        assert {f"dlq-{q}", f"age-{q}"} <= names
    assert {
        "api-5xx",
        "api-latency-p95",
        "dynamodb-throttles",
        "tasks-api",
        "ses-bounce-rate",
        "ses-complaint-rate",
    } <= names
    for key in (
        "webhook-signature-failures",
        "integrity-mismatch",
        "reconciliation-mismatch",
        "invoice-run-errors",
        "payment-unmatched",
    ):
        assert any(n.startswith(key) for n in names), key


def test_queue_age_limits_match_the_design(dev: Any) -> None:
    alarms = {
        a["Properties"]["AlarmName"]: a["Properties"]
        for a in resources(dev.t["observability"], "AWS::CloudWatch::Alarm").values()
    }
    assert alarms["anjung-dev-age-payment_events"]["Threshold"] == 600  # 10 minutes for money
    assert alarms["anjung-dev-age-housekeeping"]["Threshold"] == 3600
    assert alarms["anjung-dev-api-5xx"]["Threshold"] == 1
    assert alarms["anjung-dev-dlq-payment_events"]["Threshold"] == 0


def test_log_metric_filters_match_what_the_backend_logs(dev: Any) -> None:
    backend = "".join(p.read_text() for p in (ROOT / "backend" / "app").rglob("*.py"))
    filters = [
        f["Properties"]["FilterPattern"]
        for f in resources(dev.t["observability"], "AWS::Logs::MetricFilter").values()
    ]
    for needle in (
        "integrity.mismatch",
        "reconciliation.mismatch",
        "invoice run completed with errors",
        "unmatched payment",
    ):
        assert any(needle in f for f in filters) and needle in backend, needle
    assert Match.any_value()


def test_api_and_workers_ship_traces_through_a_collector_sidecar(dev: Any) -> None:
    app = dev.t["app"]
    tasks = resources(app, "AWS::ECS::TaskDefinition")
    assert len(tasks) >= 2
    for tid, td in tasks.items():
        containers = {c["Name"]: c for c in td["Properties"]["ContainerDefinitions"]}
        assert "otel-collector" in containers, tid
        assert (
            containers["otel-collector"]["Essential"] is False
        )  # tracing must never take the service down
        main = next(c for n, c in containers.items() if n != "otel-collector")
        env = {e["Name"]: e["Value"] for e in main["Environment"]}
        assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://localhost:4318"
    actions = [
        a
        for pol in resources(app, "AWS::IAM::Policy").values()
        for st in pol["Properties"]["PolicyDocument"]["Statement"]
        for a in (st["Action"] if isinstance(st["Action"], list) else [st["Action"]])
    ]
    assert actions.count("xray:PutTraceSegments") == len(tasks)


def test_ses_bounces_and_complaints_reach_the_notifications_queue(dev: Any, prod: Any) -> None:
    data = dev.t["data"]
    [cs] = resources(data, "AWS::SES::ConfigurationSet").values()
    assert cs["Properties"]["Name"] == "anjung-dev"
    [dest] = resources(data, "AWS::SES::ConfigurationSetEventDestination").values()
    ed = dest["Properties"]["EventDestination"]
    assert sorted(ed["MatchingEventTypes"]) == ["bounce", "complaint"] and ed["Enabled"] is True
    [topic] = resources(data, "AWS::SNS::Topic").values()
    assert "KmsMasterKeyId" in topic["Properties"]  # the events carry email addresses
    [sub] = resources(data, "AWS::SNS::Subscription").values()
    p = sub["Properties"]
    assert p["Protocol"] == "sqs" and p["RawMessageDelivery"] is True
    assert "notifications" in json.dumps(p["Endpoint"])
    # only SES of this account may publish, and use the key for it
    [tp] = resources(data, "AWS::SNS::TopicPolicy").values()
    ses_stmts = [
        s
        for s in tp["Properties"]["PolicyDocument"]["Statement"]
        if s.get("Principal") == {"Service": "ses.amazonaws.com"}
    ]
    assert ses_stmts and all(
        "aws:sourceaccount" in json.dumps(s["Condition"]).lower() for s in ses_stmts
    )
    [key] = resources(data, "AWS::KMS::Key").values()
    assert any(
        s.get("Principal") == {"Service": "ses.amazonaws.com"}
        for s in key["Properties"]["KeyPolicy"]["Statement"]
    )
    # sends name the configuration set wherever a sender is configured
    for td in resources(prod.t["app"], "AWS::ECS::TaskDefinition").values():
        [c] = [x for x in td["Properties"]["ContainerDefinitions"] if x["Name"] != "otel-collector"]
        env = {e["Name"]: e["Value"] for e in c["Environment"]}
        assert "SES_CONFIGURATION_SET" in env

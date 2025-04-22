from __future__ import annotations

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cloudfront as cf
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_wafv2 as waf
from constructs import Construct

from anjung_infra.config import EnvConfig


def _managed(name: str, priority: int) -> waf.CfnWebACL.RuleProperty:
    return waf.CfnWebACL.RuleProperty(
        name=name,
        priority=priority,
        override_action=waf.CfnWebACL.OverrideActionProperty(none={}),
        statement=waf.CfnWebACL.StatementProperty(
            managed_rule_group_statement=waf.CfnWebACL.ManagedRuleGroupStatementProperty(
                vendor_name="AWS", name=name
            )
        ),
        visibility_config=waf.CfnWebACL.VisibilityConfigProperty(
            cloud_watch_metrics_enabled=True, metric_name=name, sampled_requests_enabled=True
        ),
    )


class EdgeStack(Stack):
    """SPA on CloudFront (private bucket, OAC, security headers) and a regional WAF on the API load balancer."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        cfg: EnvConfig,
        alb: elbv2.IApplicationLoadBalancer,
        **kw: object,
    ) -> None:
        super().__init__(scope, id, **kw)  # type: ignore[arg-type]
        self.spa_bucket = s3.Bucket(
            self,
            "Spa",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,
            versioned=True,
        )
        headers = cf.ResponseHeadersPolicy(
            self,
            "Headers",
            security_headers_behavior=cf.ResponseSecurityHeadersBehavior(
                strict_transport_security=cf.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365),
                    include_subdomains=True,
                    override=True,
                ),
                content_type_options=cf.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cf.ResponseHeadersFrameOptions(
                    frame_option=cf.HeadersFrameOption.DENY, override=True
                ),
                referrer_policy=cf.ResponseHeadersReferrerPolicy(
                    referrer_policy=cf.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True,
                ),
                content_security_policy=cf.ResponseHeadersContentSecurityPolicy(
                    content_security_policy="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                    "connect-src 'self' https://*.amazoncognito.com https://*.amazonaws.com; frame-ancestors 'none'",
                    override=True,
                ),
            ),
        )
        self.distribution = cf.Distribution(
            self,
            "Cdn",
            default_root_object="index.html",
            minimum_protocol_version=cf.SecurityPolicyProtocol.TLS_V1_2_2021,
            default_behavior=cf.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.spa_bucket),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=headers,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED,
            ),
            # single page app: unknown paths serve the shell and let the router decide
            error_responses=[
                cf.ErrorResponse(
                    http_status=s,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                )
                for s in (403, 404)
            ],
        )
        CfnOutput(self, "SpaUrl", value=f"https://{self.distribution.distribution_domain_name}")

        # ---- WAF on the API: managed rules plus a per-IP rate limit (LLD 15.2). The webhook gets a tighter one.
        acl = waf.CfnWebACL(
            self,
            "ApiAcl",
            scope="REGIONAL",
            default_action=waf.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=waf.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="anjung-api",
                sampled_requests_enabled=True,
            ),
            rules=[
                _managed("AWSManagedRulesCommonRuleSet", 1),
                _managed("AWSManagedRulesKnownBadInputsRuleSet", 2),
                waf.CfnWebACL.RuleProperty(
                    name="RateLimitPerIp",
                    priority=3,
                    action=waf.CfnWebACL.RuleActionProperty(block={}),
                    statement=waf.CfnWebACL.StatementProperty(
                        rate_based_statement=waf.CfnWebACL.RateBasedStatementProperty(
                            limit=2000, aggregate_key_type="IP"
                        )
                    ),
                    visibility_config=waf.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="rate-limit-ip",
                        sampled_requests_enabled=True,
                    ),
                ),
                waf.CfnWebACL.RuleProperty(
                    name="RateLimitWebhookPerIp",
                    priority=4,
                    action=waf.CfnWebACL.RuleActionProperty(block={}),
                    statement=waf.CfnWebACL.StatementProperty(
                        rate_based_statement=waf.CfnWebACL.RateBasedStatementProperty(
                            limit=300,
                            aggregate_key_type="IP",
                            scope_down_statement=waf.CfnWebACL.StatementProperty(
                                byte_match_statement=waf.CfnWebACL.ByteMatchStatementProperty(
                                    search_string="/v1/webhooks/",
                                    positional_constraint="STARTS_WITH",
                                    field_to_match=waf.CfnWebACL.FieldToMatchProperty(uri_path={}),
                                    text_transformations=[
                                        waf.CfnWebACL.TextTransformationProperty(
                                            priority=0, type="NONE"
                                        )
                                    ],
                                )
                            ),
                        )
                    ),
                    visibility_config=waf.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="rate-limit-webhook",
                        sampled_requests_enabled=True,
                    ),
                ),
            ],
        )
        waf.CfnWebACLAssociation(
            self, "AclAssoc", resource_arn=alb.load_balancer_arn, web_acl_arn=acl.attr_arn
        )
        self.acl = acl

from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from anjung_infra.config import EnvConfig
from anjung_infra.stacks_data import DataStack

BACKEND = Path(__file__).resolve().parents[2] / "backend"
GROUPS = ["platform_admin", "org_admin", "property_manager", "tenant"]


class AuthStack(Stack):
    """Cognito (Essentials plan, V2 Pre Token Generation trigger), groups and app clients (LLD 11, plan P0-08)."""

    def __init__(
        self, scope: Construct, id: str, *, cfg: EnvConfig, data: DataStack, **kw: object
    ) -> None:
        super().__init__(scope, id, **kw)  # type: ignore[arg-type]

        def fn(name: str, handler: str, actions: list[str]) -> lambda_.Function:
            f = lambda_.Function(
                self,
                name,
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler=handler,
                timeout=Duration.seconds(5),
                code=lambda_.Code.from_asset(
                    str(BACKEND), exclude=["*", "!app", "!app/**", "**/__pycache__"]
                ),
                environment={"TABLE_NAME": data.table.table_name},
                memory_size=256,
                log_group=logs.LogGroup(
                    self,
                    f"{name}Logs",
                    retention=logs.RetentionDays.ONE_MONTH,
                    removal_policy=RemovalPolicy.DESTROY,
                ),
            )
            f.add_to_role_policy(
                iam.PolicyStatement(
                    actions=actions,
                    resources=[data.table.table_arn, f"{data.table.table_arn}/index/GSI2"],
                )
            )
            data.key.grant_encrypt_decrypt(f)
            return f

        # Failing closed: any exception makes Cognito deny the sign-in, so no unscoped token is ever issued
        pre_token = fn(
            "PreToken",
            "app.lambdas.pre_token_generation.handler.handler",
            ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:UpdateItem"],
        )
        post_confirm = fn(
            "PostConfirmation",
            "app.lambdas.post_confirmation.handler.handler",
            ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"],
        )

        self.pool = cognito.UserPool(
            self,
            "Pool",
            user_pool_name=f"anjung-{cfg.name}",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            feature_plan=cognito.FeaturePlan.ESSENTIALS,
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True)
            ),
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_digits=True,
                require_lowercase=True,
                require_uppercase=True,
                require_symbols=True,
                temp_password_validity=Duration.days(7),
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            deletion_protection=cfg.prod,
            removal_policy=RemovalPolicy.RETAIN,
            advanced_security_mode=None,
        )
        self.pool.add_trigger(
            cognito.UserPoolOperation.PRE_TOKEN_GENERATION_CONFIG,
            pre_token,
            lambda_version=cognito.LambdaVersion.V2_0,
        )
        self.pool.add_trigger(cognito.UserPoolOperation.POST_CONFIRMATION, post_confirm)
        for g in GROUPS:
            cognito.CfnUserPoolGroup(
                self, f"Group{g}", user_pool_id=self.pool.user_pool_id, group_name=g
            )
        self.pool.add_domain(
            "Domain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=cfg.cognito_domain_prefix),
        )

        self.spa_client = self.pool.add_client(
            "Spa",
            user_pool_client_name="spa",
            generate_secret=False,
            prevent_user_existence_errors=True,
            auth_flows=cognito.AuthFlow(user_srp=True),
            access_token_validity=Duration.minutes(30),
            id_token_validity=Duration.minutes(30),
            refresh_token_validity=Duration.days(7),
            enable_token_revocation=True,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
                callback_urls=cfg.callback_urls,
                logout_urls=cfg.logout_urls,
            ),
        )
        # server-side only, used by the post-deploy smoke test (LLD 16.2)
        self.smoke_client = self.pool.add_client(
            "Smoke",
            user_pool_client_name="smoke",
            generate_secret=False,
            prevent_user_existence_errors=True,
            auth_flows=cognito.AuthFlow(admin_user_password=True),
            access_token_validity=Duration.minutes(30),
            disable_o_auth=True,  # no browser flows: CDK would otherwise enable implicit and code grants
        )
        self.client_ids = [
            self.spa_client.user_pool_client_id,
            self.smoke_client.user_pool_client_id,
        ]
        if cfg.postman_client:  # dev and staging only: prod has no Postman access (LLD 16.3)
            postman = self.pool.add_client(
                "Postman",
                user_pool_client_name="postman",
                generate_secret=False,
                prevent_user_existence_errors=True,
                o_auth=cognito.OAuthSettings(
                    flows=cognito.OAuthFlows(authorization_code_grant=True),
                    scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
                    callback_urls=["https://oauth.pstmn.io/v1/callback"],
                ),
            )
            self.client_ids.append(postman.user_pool_client_id)

        CfnOutput(self, "PoolId", value=self.pool.user_pool_id)
        CfnOutput(self, "SpaClientId", value=self.spa_client.user_pool_client_id)
        CfnOutput(self, "SmokeClientId", value=self.smoke_client.user_pool_client_id)

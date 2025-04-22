from __future__ import annotations

from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from constructs import Construct

from anjung_infra.config import EnvConfig


class NetworkStack(Stack):
    """VPC with private subnets for the tasks. Gateway endpoints keep S3 and DynamoDB traffic off the NAT,
    interface endpoints (staging, prod) do the same for the other AWS APIs (LLD 14.3)."""

    def __init__(self, scope: Construct, id: str, *, cfg: EnvConfig, **kw: object) -> None:
        super().__init__(scope, id, **kw)  # type: ignore[arg-type]
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=cfg.nat_gateways,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=22
                ),
            ],
            flow_logs={"all": ec2.FlowLogOptions(traffic_type=ec2.FlowLogTrafficType.REJECT)},
        )
        self.vpc.add_gateway_endpoint("S3", service=ec2.GatewayVpcEndpointAwsService.S3)
        self.vpc.add_gateway_endpoint("Ddb", service=ec2.GatewayVpcEndpointAwsService.DYNAMODB)
        if cfg.interface_endpoints:
            for name, svc in {
                "EcrApi": ec2.InterfaceVpcEndpointAwsService.ECR,
                "EcrDkr": ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
                "Logs": ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
                "Secrets": ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
                "Sqs": ec2.InterfaceVpcEndpointAwsService.SQS,
                "Kms": ec2.InterfaceVpcEndpointAwsService.KMS,
                "Sts": ec2.InterfaceVpcEndpointAwsService.STS,
            }.items():
                self.vpc.add_interface_endpoint(name, service=svc)

#!/usr/bin/env python3
"""CDK entrypoint. Stacks are named Anjung-<Env>-<Stack> and wired with typed props, not CloudFormation
exports, so each can be deployed on its own (LLD 14.5).

    cdk synth -c env=dev
    cdk deploy "Anjung-Dev-*" -c env=dev -c image_tag=<git sha>
"""

import os

import aws_cdk as cdk

from anjung_infra.config import ENVS
from anjung_infra.stacks_app import AppStack
from anjung_infra.stacks_auth import AuthStack
from anjung_infra.stacks_data import DataStack
from anjung_infra.stacks_edge import EdgeStack
from anjung_infra.stacks_network import NetworkStack
from anjung_infra.stacks_observability import ObservabilityStack


def build(app: cdk.App, env_name: str, image_tag: str = "latest") -> dict[str, cdk.Stack]:
    cfg = ENVS[env_name]
    env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=cfg.region)
    p = cfg.stack_prefix
    network = NetworkStack(app, f"{p}-Network", cfg=cfg, env=env)
    data = DataStack(app, f"{p}-Data", cfg=cfg, env=env)
    auth = AuthStack(app, f"{p}-Auth", cfg=cfg, data=data, env=env)
    application = AppStack(
        app,
        f"{p}-App",
        cfg=cfg,
        vpc=network.vpc,
        data=data,
        auth=auth,
        image_tag=image_tag,
        env=env,
    )
    edge = EdgeStack(app, f"{p}-Edge", cfg=cfg, alb=application.alb, env=env)
    obs = ObservabilityStack(
        app, f"{p}-Observability", cfg=cfg, data=data, app=application, env=env
    )
    for stack in (network, data, auth, application, edge, obs):
        cdk.Tags.of(stack).add("app", "anjung")
        cdk.Tags.of(stack).add("env", cfg.name)
    return {
        "network": network,
        "data": data,
        "auth": auth,
        "app": application,
        "edge": edge,
        "observability": obs,
    }


if __name__ == "__main__":
    app = cdk.App()
    build(
        app,
        app.node.try_get_context("env") or "dev",
        app.node.try_get_context("image_tag") or "latest",
    )
    app.synth()

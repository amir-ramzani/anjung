import os
import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION", "1")

import app as anjung_app  # noqa: E402


class Synth:
    def __init__(self, env: str) -> None:
        self.app = cdk.App()
        self.stacks = anjung_app.build(self.app, env)
        self.t = {name: Template.from_stack(stack) for name, stack in self.stacks.items()}


@pytest.fixture(scope="session")
def dev() -> Synth:
    return Synth("dev")


@pytest.fixture(scope="session")
def prod() -> Synth:
    return Synth("prod")


@pytest.fixture(scope="session")
def staging() -> Synth:
    return Synth("staging")

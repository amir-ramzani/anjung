"""`python -m app.cli seed-demo`: demo data on example.com, and a plain message when it cannot sign in."""

from typing import Any

import pytest
from app.bootstrap import bootstrap_platform
from app.container import Container
from app.core.config import Settings
from app.seed import seed


@pytest.fixture
def mock_settings(settings: Settings, ddb: Any) -> Settings:
    return settings.model_copy(update={"auth_mode": "mock"})


def _bootstrap(s: Settings, email: str) -> None:
    c = Container.build(s)
    bootstrap_platform(
        c.store.table, c.identity, c.clock, platform_org_id=s.platform_org_id, admin_email=email
    )


def test_seeds_the_demo_on_example_com(mock_settings: Settings) -> None:
    _bootstrap(mock_settings, "root@example.com")
    notes = "\n".join(seed(mock_settings))
    assert "boss@example.com" in notes and "/login/demo-example-com" in notes
    assert "already exists" in "\n".join(seed(mock_settings))  # safe to run again


def test_a_platform_admin_that_does_not_exist_gets_a_plain_message(
    mock_settings: Settings,
) -> None:
    _bootstrap(mock_settings, "root@anjung-corp.com")  # an older database
    with pytest.raises(SystemExit) as e:
        seed(mock_settings)
    msg = str(e.value)
    assert "root@example.com is not a platform admin here" in msg
    assert "--platform-admin <email>" in msg
    assert "already exists" not in "\n".join(  # the hint works
        seed(mock_settings, platform_admin="root@anjung-corp.com")
    )


def test_an_invalid_address_gets_a_plain_message(mock_settings: Settings) -> None:
    _bootstrap(mock_settings, "root@example.com")
    with pytest.raises(SystemExit) as e:
        seed(mock_settings, platform_admin="root@anjung.local")
    assert str(e.value) == "root@anjung.local is not a valid email address."


def test_a_company_member_is_not_a_platform_admin(mock_settings: Settings) -> None:
    _bootstrap(mock_settings, "root@example.com")
    seed(mock_settings)
    with pytest.raises(SystemExit) as e:
        seed(mock_settings, platform_admin="boss@example.com", domain="demo2.example.com")
    assert "boss@example.com is not a platform admin here" in str(e.value)

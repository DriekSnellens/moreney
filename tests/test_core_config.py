"""Tests for configuration management."""

import pytest
from pydantic import SecretStr

from bot.core.config import Settings
from bot.core.enums import ExecutionMode
from bot.core.exceptions import ConfigurationError


def test_default_settings_are_paper_mode() -> None:
    settings = Settings(execution_mode="paper")
    assert settings.execution_mode == ExecutionMode.PAPER


def test_execution_mode_normalized_from_env_casing() -> None:
    settings = Settings(execution_mode="PAPER")
    assert settings.execution_mode == ExecutionMode.PAPER


def test_live_mode_requires_credentials() -> None:
    settings = Settings(execution_mode="live")
    with pytest.raises(ConfigurationError, match="EXCHANGE_API_KEY"):
        settings.require_live_credentials()


def test_live_mode_with_credentials_passes() -> None:
    settings = Settings(
        execution_mode="live",
        exchange_api_key=SecretStr("key"),
        exchange_api_secret=SecretStr("secret"),
    )
    settings.require_live_credentials()


def test_secrets_are_not_plain_strings() -> None:
    settings = Settings(exchange_api_key=SecretStr("super-secret"))
    assert settings.exchange_api_key is not None
    assert "super-secret" not in repr(settings.exchange_api_key)
    assert settings.exchange_api_key.get_secret_value() == "super-secret"

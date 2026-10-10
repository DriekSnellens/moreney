"""Per-account dashboard session cookies and home routing."""

from starlette.responses import Response

from bot.core.config import Settings
from bot.core.enums import ExecutionMode
from bot.main import live_operator_redirect
from bot.paper.auth import (
    COOKIE_NAME,
    clear_session_cookie,
    session_cookie_name,
    set_session_cookie,
)


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_invalid_cookie_name_falls_back() -> None:
    assert session_cookie_name(None) == COOKIE_NAME
    assert session_cookie_name(_settings(dashboard_cookie_name="")) == COOKIE_NAME
    assert session_cookie_name(_settings(dashboard_cookie_name="bad name")) == COOKIE_NAME


def test_account_cookie_name_is_set_and_cleared() -> None:
    settings = _settings(
        dashboard_cookie_name="moreney_dash_peter",
        dashboard_session_secret="test-secret",
        dashboard_basic_auth_username="peter",
    )
    assert session_cookie_name(settings) == "moreney_dash_peter"
    response = Response()
    set_session_cookie(response, settings, "peter")
    assert response.headers["set-cookie"].startswith("moreney_dash_peter=")
    cleared = Response()
    clear_session_cookie(cleared, settings)
    assert cleared.headers["set-cookie"].startswith("moreney_dash_peter=")


def test_momentum_home_opens_weekly_rs_page() -> None:
    home = _settings(
        execution_mode=ExecutionMode.LIVE,
        paper_trading_enabled=False,
        dashboard_momentum_home=True,
    )
    assert (
        live_operator_redirect(home, momentum_running=False, micro_running=False)
        == "/live/momentum"
    )
    assert live_operator_redirect(home, momentum_running=False, micro_running=True) is None
    legacy = _settings(
        execution_mode=ExecutionMode.LIVE,
        paper_trading_enabled=False,
        dashboard_momentum_home=False,
    )
    assert live_operator_redirect(legacy, momentum_running=False, micro_running=False) is None
    assert (
        live_operator_redirect(legacy, momentum_running=True, micro_running=False)
        == "/live/momentum"
    )

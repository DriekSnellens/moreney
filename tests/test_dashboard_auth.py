"""Public dashboard requires login when DASHBOARD_BASIC_AUTH_ENABLED=true."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from bot.core.config import get_settings
from bot.live.micro_engine import reset_micro_engine
from bot.live.micro_session_manager import reset_micro_session_manager
from bot.live.service import reset_live_service
from bot.main import app, reset_risk_singletons
from bot.paper.auth import COOKIE_NAME


@pytest.fixture(autouse=True)
def _auth_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_PERSIST_PATH", str(tmp_path / "dash-auth.json"))
    monkeypatch.setenv("DASHBOARD_BASIC_AUTH_ENABLED", "true")
    monkeypatch.setenv("DASHBOARD_BASIC_AUTH_USERNAME", "alice")
    monkeypatch.setenv("DASHBOARD_BASIC_AUTH_PASSWORD", "secret")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    get_settings.cache_clear()
    reset_risk_singletons()
    reset_live_service()
    reset_micro_engine()
    reset_micro_session_manager()
    yield
    get_settings.cache_clear()
    reset_risk_singletons()


@pytest.fixture()
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as session:
        yield session


async def test_health_stays_public(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200


async def test_html_dashboard_redirects_to_login(client: httpx.AsyncClient) -> None:
    res = await client.get("/live/momentum")
    assert res.status_code == 303
    assert res.headers["location"].startswith("/login?")


async def test_json_status_and_sell_are_unauthorized(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api")).status_code == 401
    assert (await client.get("/live/momentum/ledger")).status_code == 401
    assert (await client.post("/live/momentum/sell", json={})).status_code == 401
    assert (await client.post("/live/momentum/start", json={})).status_code == 401


async def test_login_cookie_unlocks_api(client: httpx.AsyncClient) -> None:
    bad = await client.post(
        "/login",
        data={"username": "alice", "password": "nope", "next": "/live/momentum"},
    )
    assert bad.status_code == 401
    assert COOKIE_NAME not in client.cookies

    ok = await client.post(
        "/login",
        data={"username": "alice", "password": "secret", "next": "/live/momentum"},
    )
    assert ok.status_code == 303
    assert ok.headers["location"] == "/live/momentum"
    assert COOKIE_NAME in client.cookies

    api = await client.get("/api")
    assert api.status_code == 200
    assert api.json()["dashboard_basic_auth_enabled"] is True


async def test_basic_auth_still_unlocks_json(client: httpx.AsyncClient) -> None:
    res = await client.get("/api", auth=("alice", "secret"))
    assert res.status_code == 200


async def test_https_login_sets_secure_cookie() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://moreney.ai",
        follow_redirects=False,
    ) as client:
        ok = await client.post(
            "/login",
            data={"username": "alice", "password": "secret", "next": "/live/momentum"},
        )
        assert ok.status_code == 303
        cookie = ok.headers.get("set-cookie", "")
        assert "Secure" in cookie
        assert COOKIE_NAME in cookie


async def test_root_redirects_to_mix_when_donchian_running(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Running:
        def running(self) -> bool:
            return True

    monkeypatch.setattr("bot.main.get_donchian_desk_manager", lambda: _Running())
    res = await client.get("/", auth=("alice", "secret"))
    assert res.status_code == 303
    assert res.headers["location"] == "/live/momentum"
    login = await client.post(
        "/login",
        data={"username": "alice", "password": "secret", "next": "/"},
    )
    assert login.status_code == 303
    assert login.headers["location"] == "/live/momentum"

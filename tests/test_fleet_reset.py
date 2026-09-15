"""Fleet-wide paper reset fan-out."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.config import Settings
from bot.paper.fleet import reset_fleet


def _settings() -> Settings:
    return Settings(
        execution_mode="paper",
        paper_fleet_urls="http://127.0.0.1:8007,http://127.0.0.1:8008",
        paper_fleet_labels="200 EUR,500 EUR",
    )


@pytest.mark.asyncio
async def test_reset_fleet_requires_confirm() -> None:
    result = await reset_fleet(_settings(), confirm=False)
    assert result["reset"] is False
    assert result["reason"] == "confirmation_required"
    assert result["results"] == []


@pytest.mark.asyncio
async def test_reset_fleet_resets_and_restarts_each_instance() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
            self._payload = payload
            self.status_code = status

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self) -> dict[str, Any]:
            return self._payload

    async def fake_post(url: str, json: dict[str, Any] | None = None) -> FakeResponse:
        calls.append((url, json or {}))
        if url.endswith("/paper/reset"):
            return FakeResponse({"reset": True, "starting_equity": "200"})
        if url.endswith("/paper/start"):
            return FakeResponse({"started": True, "running": True})
        return FakeResponse({}, status=404)

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=fake_post)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    with patch("bot.paper.fleet.httpx.AsyncClient", return_value=fake_client):
        result = await reset_fleet(_settings(), confirm=True, restart=True)

    assert result["reset"] is True
    assert result["ok_count"] == 2
    assert result["configured_count"] == 2
    assert result["real_exchange_accounts_affected"] is False
    assert all(row["ok"] and row["reset"] and row["restarted"] for row in result["results"])
    assert any(url.endswith("/paper/reset") for url, _ in calls)
    assert any(url.endswith("/paper/start") for url, _ in calls)

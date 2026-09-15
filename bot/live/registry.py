"""Phase 2 — multi-venue exchange client registry (fail-closed trading)."""

from __future__ import annotations

import os
from typing import Any

from pydantic import SecretStr

from bot.core.config import Settings
from bot.core.interfaces import ExchangeClient
from bot.exchanges.factory import create_exchange_client
from bot.funding.multi_venue import parse_venue_list, venue_credential_env_names


class MultiVenueRegistry:
    """Lazy per-venue ExchangeClient factory.

    Clients are created with ``enable_trading=False`` unless the caller
    explicitly opts in *and* global live gates allow it.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._clients: dict[str, ExchangeClient] = {}

    def configured_venues(self) -> list[str]:
        raw = getattr(self._settings, "live_trading_venues", None) or getattr(
            self._settings, "funding_venues", "bitvavo,kraken,binance,okx"
        )
        return parse_venue_list(str(raw))

    def credential_status(self) -> dict[str, dict[str, bool]]:
        """Presence flags only — never secret values."""
        out: dict[str, dict[str, bool]] = {}
        for venue in self.configured_venues():
            names = venue_credential_env_names(venue)
            out[venue] = {
                "api_key_present": bool(os.environ.get(names["api_key"])),
                "api_secret_present": bool(os.environ.get(names["api_secret"])),
                "passphrase_present": bool(os.environ.get(names["passphrase"])),
            }
            if (self._settings.exchange_name or "").strip().lower() == venue:
                out[venue]["api_key_present"] = out[venue]["api_key_present"] or bool(
                    self._settings.exchange_api_key
                )
                out[venue]["api_secret_present"] = out[venue][
                    "api_secret_present"
                ] or bool(self._settings.exchange_api_secret)
        return out

    def _overlay_settings(self, venue: str) -> Settings | None:
        names = venue_credential_env_names(venue)
        api_key = os.environ.get(names["api_key"])
        api_secret = os.environ.get(names["api_secret"])
        passphrase = os.environ.get(names["passphrase"])
        if not api_key or not api_secret:
            if (self._settings.exchange_name or "").strip().lower() == venue:
                if self._settings.exchange_api_key and self._settings.exchange_api_secret:
                    return self._settings.model_copy(update={"exchange_name": venue})
            return None
        return self._settings.model_copy(
            update={
                "exchange_name": venue,
                "exchange_api_key": SecretStr(api_key),
                "exchange_api_secret": SecretStr(api_secret),
                "exchange_passphrase": SecretStr(passphrase) if passphrase else None,
            }
        )

    def get_client(self, venue: str, *, enable_trading: bool = False) -> ExchangeClient | None:
        key = venue.strip().lower()
        cache_key = f"{key}:{'trade' if enable_trading else 'ro'}"
        if cache_key in self._clients:
            return self._clients[cache_key]
        overlay = self._overlay_settings(key)
        if overlay is None:
            return None
        client = create_exchange_client(overlay, enable_trading=enable_trading)
        self._clients[cache_key] = client
        return client

    async def close_all(self) -> None:
        for client in list(self._clients.values()):
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()

    def status(self) -> dict[str, Any]:
        return {
            "venues": self.configured_venues(),
            "credentials": self.credential_status(),
            "trading_enabled_on_clients": False,
            "note": "Registry creates read-only clients by default.",
        }

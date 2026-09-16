"""Per-venue API credential probes (presence + optional read-only health).

Never returns secret values. Never enables trading or withdrawals.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import SecretStr

from bot.core.config import Settings
from bot.exchanges.factory import create_exchange_client
from bot.funding.multi_venue import parse_venue_list, venue_credential_env_names

logger = logging.getLogger(__name__)

# Operator-facing setup hints (no secrets).
VENUE_SETUP: dict[str, dict[str, str]] = {
    "bitvavo": {
        "env": "BITVAVO_API_KEY + BITVAVO_API_SECRET",
        "permissions": "View / trading balances only — disable withdraw",
        "funding": "SEPA deposit in Bitvavo UI (main funding venue)",
    },
    "kraken": {
        "env": "KRAKEN_API_KEY + KRAKEN_API_SECRET",
        "permissions": "Query funds + Query open orders; no Withdraw funds",
        "funding": "Manual transfer from Bitvavo or SEPA if available",
    },
    "binance": {
        "env": "BINANCE_API_KEY + BINANCE_API_SECRET",
        "permissions": "Enable Reading; disable withdrawals / internal transfer if possible",
        "funding": "Manual crypto/fiat transfer from funding venue",
    },
    "okx": {
        "env": "OKX_API_KEY + OKX_API_SECRET + OKX_API_PASSPHRASE",
        "permissions": "Read / Trade only; no Withdraw",
        "funding": "Manual transfer from funding venue",
    },
    "coinbase": {
        "env": "COINBASE_API_KEY + COINBASE_API_SECRET (+ passphrase if required)",
        "permissions": "View + Trade; no Transfer",
        "funding": "Manual transfer from funding venue",
    },
}


def resolve_credentials(settings: Settings, venue: str) -> dict[str, Any]:
    """Return presence flags and env var names for one venue (no secret values)."""
    names = venue_credential_env_names(venue)
    api_key = os.environ.get(names["api_key"])
    api_secret = os.environ.get(names["api_secret"])
    passphrase = os.environ.get(names["passphrase"])
    from_primary = False
    if not api_key or not api_secret:
        if (settings.exchange_name or "").strip().lower() == venue.strip().lower():
            if settings.exchange_api_key and settings.exchange_api_secret:
                from_primary = True
                api_key = "present"
                api_secret = "present"
                if settings.exchange_passphrase:
                    passphrase = "present"
    setup = VENUE_SETUP.get(venue.strip().lower(), {
        "env": f"{venue.upper()}_API_KEY + {venue.upper()}_API_SECRET",
        "permissions": "Read/trade only; disable withdraw",
        "funding": "Manual transfer from main funding venue",
    })
    return {
        "venue": venue.strip().lower(),
        "api_key_present": bool(api_key),
        "api_secret_present": bool(api_secret),
        "passphrase_present": bool(passphrase),
        "passphrase_required": venue.strip().lower() in {"okx", "coinbase"},
        "configured": bool(api_key) and bool(api_secret),
        "source": "primary_exchange" if from_primary else ("env" if api_key else "missing"),
        "env_names": names,
        "setup": setup,
    }


def credential_report(settings: Settings, venues: list[str] | None = None) -> dict[str, Any]:
    wanted = venues or parse_venue_list(
        str(
            getattr(settings, "live_observe_venues", None)
            or getattr(settings, "funding_venues", "bitvavo,kraken,binance,okx")
            or ""
        )
    )
    rows = [resolve_credentials(settings, v) for v in wanted]
    configured = [r for r in rows if r["configured"]]
    missing = [r["venue"] for r in rows if not r["configured"]]
    return {
        "venues": rows,
        "configured_count": len(configured),
        "missing_venues": missing,
        "ready_for_observe": len(configured) > 0,
        "places_orders": False,
        "withdrawals_supported": False,
        "note": (
            "Set per-venue API keys in the environment. "
            "Use read/trade permissions only — never enable withdraw on the key."
        ),
    }


async def probe_venue_health(
    settings: Settings,
    venue: str,
    *,
    client_factory: Any | None = None,
) -> dict[str, Any]:
    """Read-only health/auth probe. Never enables trading."""
    info = resolve_credentials(settings, venue)
    if not info["configured"]:
        return {
            **info,
            "probed": False,
            "healthy": False,
            "authenticated": False,
            "error": "credentials_not_configured",
        }

    names = info["env_names"]
    api_key = os.environ.get(names["api_key"])
    api_secret = os.environ.get(names["api_secret"])
    passphrase = os.environ.get(names["passphrase"])
    if not api_key or not api_secret:
        # Primary exchange fallback
        api_key = (
            settings.exchange_api_key.get_secret_value()
            if settings.exchange_api_key
            else None
        )
        api_secret = (
            settings.exchange_api_secret.get_secret_value()
            if settings.exchange_api_secret
            else None
        )
        passphrase = (
            settings.exchange_passphrase.get_secret_value()
            if settings.exchange_passphrase
            else None
        )

    factory = client_factory or create_exchange_client
    client = None
    try:
        overlay = settings.model_copy(
            update={
                "exchange_name": venue.strip().lower(),
                "exchange_api_key": SecretStr(api_key) if api_key else None,
                "exchange_api_secret": SecretStr(api_secret) if api_secret else None,
                "exchange_passphrase": SecretStr(passphrase) if passphrase else None,
            }
        )
        client = factory(overlay, enable_trading=False)
        health = await client.health_check()
        return {
            **info,
            "probed": True,
            "healthy": bool(getattr(health, "healthy", False)),
            "authenticated": bool(getattr(health, "authenticated", False)),
            "latency_ms": getattr(health, "latency_ms", None),
            "message": getattr(health, "message", "") or "ok",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Credential probe failed for %s: %s", venue, type(exc).__name__)
        return {
            **info,
            "probed": True,
            "healthy": False,
            "authenticated": False,
            "error": type(exc).__name__,
        }
    finally:
        if client is not None and hasattr(client, "close"):
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass


async def probe_all_venues(
    settings: Settings,
    venues: list[str] | None = None,
    *,
    client_factory: Any | None = None,
) -> dict[str, Any]:
    report = credential_report(settings, venues)
    probes = []
    for row in report["venues"]:
        if row["configured"]:
            probes.append(
                await probe_venue_health(
                    settings, row["venue"], client_factory=client_factory
                )
            )
        else:
            probes.append(
                {
                    **row,
                    "probed": False,
                    "healthy": False,
                    "authenticated": False,
                    "error": "credentials_not_configured",
                }
            )
    authed = sum(1 for p in probes if p.get("authenticated"))
    return {
        **report,
        "probes": probes,
        "authenticated_count": authed,
        "observe_viable": authed > 0 or report["configured_count"] > 0,
    }

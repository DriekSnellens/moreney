"""Multi-venue balance readers (paper ledger + optional live exchange APIs)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

import httpx
from pydantic import SecretStr

from bot.core.config import Settings
from bot.core.enums import ExecutionMode
from bot.core.models import PortfolioSnapshot
from bot.funding.models import VenueAssetBalance, VenueBalanceSnapshot

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_BITVAVO_PUBLIC = "https://api.bitvavo.com/v2"


async def fetch_public_eur_prices(
    *,
    base_url: str = _BITVAVO_PUBLIC,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Decimal]:
    """Public Bitvavo last prices for *-EUR markets (marks live inventory)."""
    owns = client is None
    http = client or httpx.AsyncClient(timeout=8.0)
    out: dict[str, Decimal] = {}
    try:
        resp = await http.get(f"{base_url.rstrip('/')}/ticker/price")
        resp.raise_for_status()
        payload = resp.json()
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if not isinstance(row, dict):
                continue
            market = str(row.get("market") or "")
            if not market.endswith("-EUR"):
                continue
            try:
                price = Decimal(str(row.get("price") or 0))
            except Exception:  # noqa: BLE001
                continue
            if price > 0:
                out[market[:-4].upper()] = price
    except Exception as exc:  # noqa: BLE001
        logger.warning("public EUR price fetch failed: %s", type(exc).__name__)
    finally:
        if owns:
            try:
                await http.aclose()
            except Exception:  # noqa: BLE001
                pass
    return out


def summarize_venue_snapshot(
    snap: VenueBalanceSnapshot,
    *,
    quote: str = "EUR",
    top_n: int = 5,
) -> dict[str, Any]:
    """Split free quote vs marked inventory so dust EUR is not mistaken for bag size."""
    quote_u = quote.upper()
    free_quote = _ZERO
    locked_quote = _ZERO
    inventory = _ZERO
    unmarked = 0
    tops: list[dict[str, Any]] = []
    for bal in snap.balances or []:
        asset = str(bal.asset or "").upper()
        if not asset:
            continue
        if asset == quote_u:
            free_quote += Decimal(str(bal.available or 0))
            locked_quote += Decimal(str(bal.locked or 0))
            continue
        value = bal.value_eur
        if value is None:
            if Decimal(str(bal.total or 0)) > 0:
                unmarked += 1
            continue
        inventory += value
        tops.append(
            {
                "asset": asset,
                "total": str(bal.total),
                "value_eur": str(value),
            }
        )
    tops.sort(key=lambda r: Decimal(str(r["value_eur"])), reverse=True)
    total = free_quote + locked_quote + inventory
    return {
        "venue": snap.venue,
        "online": bool(snap.online),
        "error": snap.error,
        "free_quote_eur": str(free_quote),
        "locked_quote_eur": str(locked_quote),
        "inventory_mtm_eur": str(inventory),
        "total_value_eur": str(total),
        "unmarked_assets": unmarked,
        "top_inventory": tops[: max(0, int(top_n))],
    }


def parse_venue_list(raw: str) -> list[str]:
    return [p.strip().lower() for p in (raw or "").split(",") if p.strip()]


def parse_target_weights(raw: str) -> dict[str, Decimal]:
    """Parse ``bitvavo:0.4,kraken:0.3,binance:0.3`` style weights."""
    out: dict[str, Decimal] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        venue, weight = part.split(":", 1)
        venue = venue.strip().lower()
        try:
            out[venue] = Decimal(weight.strip())
        except Exception:  # noqa: BLE001
            continue
    return out


def venue_credential_env_names(venue: str) -> dict[str, str]:
    """Env var names for per-venue keys (never returns secret values)."""
    key = venue.strip().upper().replace("-", "_")
    return {
        "api_key": f"{key}_API_KEY",
        "api_secret": f"{key}_API_SECRET",
        "passphrase": f"{key}_API_PASSPHRASE",
    }


def ledger_to_venue_snapshots(
    ledger_export: dict[str, Any],
    *,
    source: str = "paper",
    prices_eur: dict[str, Decimal] | None = None,
) -> list[VenueBalanceSnapshot]:
    """Convert VenueLedger.export() into funding venue snapshots."""
    prices = prices_eur or {}
    quote = str(ledger_export.get("quote") or "EUR").upper()
    balances = ledger_export.get("balances") or {}
    now = datetime.now(timezone.utc)
    snapshots: list[VenueBalanceSnapshot] = []
    for venue, assets in balances.items():
        rows: list[VenueAssetBalance] = []
        total_eur = _ZERO
        for asset, amount in (assets or {}).items():
            amt = Decimal(str(amount))
            asset_u = str(asset).upper()
            if asset_u == quote:
                value = amt
            elif asset_u in prices and prices[asset_u] > 0:
                value = amt * prices[asset_u]
            else:
                value = None
            if value is not None:
                total_eur += value
            rows.append(
                VenueAssetBalance(
                    venue=str(venue).lower(),
                    asset=asset_u,
                    available=amt,
                    locked=_ZERO,
                    reserved=_ZERO,
                    total=amt,
                    value_eur=value,
                    source=source,
                )
            )
        snapshots.append(
            VenueBalanceSnapshot(
                venue=str(venue).lower(),
                balances=rows,
                total_value_eur=total_eur,
                online=True,
                error=None,
                source=source,
                as_of=now,
            )
        )
    return snapshots


def portfolio_snapshot_to_venue(
    venue: str,
    snap: PortfolioSnapshot,
    *,
    source: str = "live",
    prices_eur: dict[str, Decimal] | None = None,
    quote: str = "EUR",
) -> VenueBalanceSnapshot:
    prices = prices_eur or {}
    quote_u = quote.upper()
    rows: list[VenueAssetBalance] = []
    total_eur = _ZERO
    for bal in snap.balances:
        asset_u = bal.asset.upper()
        available = bal.free
        locked = bal.locked
        total = bal.total
        if asset_u == quote_u:
            value = total
        elif asset_u in prices and prices[asset_u] > 0:
            value = total * prices[asset_u]
        else:
            value = None
        if value is not None:
            total_eur += value
        rows.append(
            VenueAssetBalance(
                venue=venue.lower(),
                asset=asset_u,
                available=available,
                locked=locked,
                reserved=locked,
                total=total,
                value_eur=value,
                source=source,
            )
        )
    return VenueBalanceSnapshot(
        venue=venue.lower(),
        balances=rows,
        total_value_eur=total_eur,
        online=True,
        error=None,
        source=source,
        as_of=snap.as_of,
    )


def _resolve_venue_credentials(
    settings: Settings, venue: str
) -> tuple[str | None, str | None, str | None]:
    env_names = venue_credential_env_names(venue)
    api_key = os.environ.get(env_names["api_key"]) or None
    api_secret = os.environ.get(env_names["api_secret"]) or None
    passphrase = os.environ.get(env_names["passphrase"]) or None
    if not api_key and not api_secret:
        if (settings.exchange_name or "").strip().lower() == venue:
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
    return api_key, api_secret, passphrase


async def fetch_live_venue_balances(
    settings: Settings,
    venues: list[str],
    *,
    client_factory: Callable[..., Any] | None = None,
    prices_eur: dict[str, Decimal] | None = None,
) -> list[VenueBalanceSnapshot]:
    """Fetch balances per venue; one failing venue does not fail the portfolio.

    Trading is never enabled on these clients.
    """
    from bot.exchanges.factory import create_exchange_client

    factory = client_factory or create_exchange_client
    results: list[VenueBalanceSnapshot] = []
    quote = (settings.paper_quote_asset or "EUR").upper()

    for venue in venues:
        api_key, api_secret, passphrase = _resolve_venue_credentials(settings, venue)
        if not api_key or not api_secret:
            results.append(
                VenueBalanceSnapshot(
                    venue=venue,
                    balances=[],
                    total_value_eur=_ZERO,
                    online=False,
                    error="credentials_not_configured",
                    source="live",
                )
            )
            continue

        client = None
        try:
            overlay = settings.model_copy(
                update={
                    "exchange_name": venue,
                    "exchange_api_key": SecretStr(api_key),
                    "exchange_api_secret": SecretStr(api_secret),
                    "exchange_passphrase": SecretStr(passphrase) if passphrase else None,
                }
            )
            client = factory(overlay, enable_trading=False)
            snap = await client.get_balances()
            results.append(
                portfolio_snapshot_to_venue(
                    venue, snap, source="live", prices_eur=prices_eur, quote=quote
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Live balance fetch failed for %s: %s", venue, type(exc).__name__)
            results.append(
                VenueBalanceSnapshot(
                    venue=venue,
                    balances=[],
                    total_value_eur=_ZERO,
                    online=False,
                    error=type(exc).__name__,
                    source="live",
                )
            )
        finally:
            if client is not None and hasattr(client, "close"):
                try:
                    await client.close()
                except Exception:  # noqa: BLE001
                    pass

    return results


def is_paper_mode(settings: Settings) -> bool:
    return settings.execution_mode == ExecutionMode.PAPER

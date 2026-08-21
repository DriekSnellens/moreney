"""Micro-live unlock checklist + order dry-run (never places orders)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from bot.core.config import Settings
from bot.core.enums import OpportunitySide
from bot.core.models import OrderRequest
from bot.live.micro import MicroLivePolicy
from bot.live.production_flags import PRODUCTION_EXECUTION_ENABLED


def unlock_checklist(settings: Settings) -> dict[str, Any]:
    """Show which operator flags are still blocking micro-live."""
    policy = MicroLivePolicy(settings)
    flags = [
        {
            "id": "LIVE_TRADING_ENABLED",
            "set": bool(getattr(settings, "live_trading_enabled", False)),
            "required": True,
            "hint": "Master switch for any live order path",
        },
        {
            "id": "LIVE_MICRO_ENABLED",
            "set": bool(getattr(settings, "live_micro_enabled", False)),
            "required": True,
            "hint": "Restricts live to micro allowlist + limits",
        },
        {
            "id": "LIVE_ORDERS_UNLOCKED",
            "set": bool(getattr(settings, "live_orders_unlocked", False)),
            "required": True,
            "hint": "Final operator unlock after Phase 0+1 pass",
        },
        {
            "id": "LIVE_ALLOW_WITHOUT_RESEARCH_UNLOCK",
            "set": bool(getattr(settings, "live_allow_without_research_unlock", False)),
            "required": not bool(PRODUCTION_EXECUTION_ENABLED),
            "hint": "Needed while research PRODUCTION_EXECUTION_ENABLED=false",
        },
        {
            "id": "AUTOMATIC_WITHDRAWALS_ENABLED",
            "set": not bool(getattr(settings, "automatic_withdrawals_enabled", False)),
            "required": True,
            "hint": "Must remain false (inverted: passed when withdrawals off)",
        },
    ]
    missing = [f["id"] for f in flags if f["required"] and not f["set"]]
    can_place, reason = policy.can_place_orders()
    return {
        "can_place_orders": can_place,
        "block_reason": None if can_place else reason,
        "flags": flags,
        "missing": missing,
        "policy": policy.status(),
        "production_execution_enabled": bool(PRODUCTION_EXECUTION_ENABLED),
        "places_orders_via_this_endpoint": False,
        "note": (
            "Unlock only via environment/config — this API never flips flags. "
            "Run dry-run before enabling."
        ),
    }


def dry_run_order(
    settings: Settings,
    *,
    venue: str,
    symbol: str,
    side: str = "buy",
    quantity: Decimal | float | str = "0.001",
    limit_price: Decimal | float | str | None = None,
    notional_eur: Decimal | float | str | None = None,
) -> dict[str, Any]:
    """Validate a hypothetical order against micro policy — no exchange call."""
    policy = MicroLivePolicy(settings)
    qty = Decimal(str(quantity))
    px = Decimal(str(limit_price)) if limit_price is not None else Decimal("0")
    if notional_eur is not None:
        notional = Decimal(str(notional_eur))
    elif px > 0:
        notional = px * qty
    else:
        notional = qty  # treat bare quantity as notional when price unknown

    ok, detail = policy.validate_order(
        venue=venue,
        symbol=symbol,
        notional_eur=notional,
        open_orders=0,
        daily_loss_eur=Decimal("0"),
    )
    side_enum = (
        OpportunitySide.BUY
        if str(side).lower().startswith("b")
        else OpportunitySide.SELL
    )
    # Build request only to prove shape — never submit.
    _ = OrderRequest(
        opportunity_id=uuid4(),
        symbol=symbol,
        side=side_enum,
        quantity=qty if qty > 0 else Decimal("0.001"),
        limit_price=px if px > 0 else None,
        metadata={"venue": venue.strip().lower(), "dry_run": True},
    )
    return {
        "would_submit": False,
        "policy_allows": ok,
        "detail": detail,
        "venue": venue.strip().lower(),
        "symbol": symbol.upper(),
        "side": side_enum.value,
        "quantity": str(qty),
        "notional_eur": str(notional),
        "checklist": unlock_checklist(settings),
        "withdrawals_supported": False,
    }

"""Phase 3 — micro-live allowlist and strict risk limits."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.core.config import Settings
from bot.funding.multi_venue import parse_venue_list
from bot.research.shadow_validation.protocol import PRODUCTION_EXECUTION_ENABLED


class MicroLivePolicy:
    """Gates for tiny real-money trading. Fail closed by default."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._settings, "live_micro_enabled", False))

    @property
    def live_trading_enabled(self) -> bool:
        return bool(getattr(self._settings, "live_trading_enabled", False))

    def allowed_venues(self) -> list[str]:
        raw = getattr(self._settings, "live_micro_venues", "") or ""
        venues = parse_venue_list(str(raw))
        if venues:
            return venues
        # Fallback: first two funding venues
        return parse_venue_list(getattr(self._settings, "funding_venues", "bitvavo,kraken"))[:2]

    def allowed_symbols(self) -> list[str]:
        raw = getattr(self._settings, "live_micro_symbols", "BTCEUR,ETHEUR") or ""
        return [s.strip().upper() for s in str(raw).split(",") if s.strip()]

    def max_notional_eur(self) -> Decimal:
        return Decimal(str(getattr(self._settings, "live_micro_max_notional_eur", 50) or 50))

    def max_daily_loss_eur(self) -> Decimal:
        return Decimal(str(getattr(self._settings, "live_micro_max_daily_loss_eur", 25) or 25))

    def max_open_orders(self) -> int:
        return int(getattr(self._settings, "live_micro_max_open_orders", 1) or 1)

    def can_place_orders(self) -> tuple[bool, str]:
        """Return (allowed, reason). Requires ALL independent gates."""
        if not self.live_trading_enabled:
            return False, "LIVE_TRADING_ENABLED=false"
        if not self.enabled:
            return False, "LIVE_MICRO_ENABLED=false"
        if bool(getattr(self._settings, "automatic_withdrawals_enabled", False)):
            return False, "automatic_withdrawals_enabled must be false"
        if not bool(getattr(self._settings, "live_orders_unlocked", False)):
            return False, "LIVE_ORDERS_UNLOCKED=false"
        # Research protocol flag must also be flipped in code for full production —
        # this settings unlock is an additional operator gate only.
        if not bool(PRODUCTION_EXECUTION_ENABLED) and not bool(
            getattr(self._settings, "live_allow_without_research_unlock", False)
        ):
            return (
                False,
                "PRODUCTION_EXECUTION_ENABLED=false "
                "(set LIVE_ALLOW_WITHOUT_RESEARCH_UNLOCK=true only for controlled micro)",
            )
        return True, "ok"

    def validate_order(
        self,
        *,
        venue: str,
        symbol: str,
        notional_eur: Decimal,
        open_orders: int = 0,
        daily_loss_eur: Decimal = Decimal("0"),
    ) -> tuple[bool, str]:
        ok, reason = self.can_place_orders()
        if not ok:
            return False, reason
        v = venue.strip().lower()
        if v not in self.allowed_venues():
            return False, f"venue {v} not in micro allowlist"
        sym = symbol.strip().upper().replace("/", "").replace("-", "")
        allowed = {s.replace("/", "").replace("-", "") for s in self.allowed_symbols()}
        if sym not in allowed:
            return False, f"symbol {symbol} not in micro allowlist"
        if notional_eur > self.max_notional_eur():
            return False, f"notional {notional_eur} exceeds max {self.max_notional_eur()}"
        if open_orders >= self.max_open_orders():
            return False, "max open orders reached"
        if daily_loss_eur >= self.max_daily_loss_eur():
            return False, "daily loss limit reached"
        return True, "ok"

    def status(self) -> dict[str, Any]:
        allowed, reason = self.can_place_orders()
        return {
            "live_trading_enabled": self.live_trading_enabled,
            "live_micro_enabled": self.enabled,
            "can_place_orders": allowed,
            "block_reason": None if allowed else reason,
            "allowed_venues": self.allowed_venues(),
            "allowed_symbols": self.allowed_symbols(),
            "max_notional_eur": str(self.max_notional_eur()),
            "max_daily_loss_eur": str(self.max_daily_loss_eur()),
            "max_open_orders": self.max_open_orders(),
            "production_execution_enabled": bool(PRODUCTION_EXECUTION_ENABLED),
            "withdrawals_supported": False,
        }

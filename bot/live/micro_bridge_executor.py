"""Bridge PaperRunner's PaperExecutor path to LiveMicroEngine with a capital pocket.

PaperRunner stays paper-only in source. This adapter is wired only by the
full-bot micro session: same strategy → GOE → profitability → risk pipeline,
but marketable fills on allowlisted venues go live within a € pocket that
recycles after sells (not a one-shot spend counter). Maker/post-only quotes
stay paper unless live_maker is enabled.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from bot.core.config import Settings
from bot.core.enums import EntryQualityRecommendation, OpportunitySide, OrderSide, OrderStatus, OrderType
from bot.core.models import ExecutionResult, OrderRequest, ProfitabilityResult, TradeOpportunity
from bot.execution.paper_executor import PaperExecutor
from bot.live.micro_engine import LiveMicroEngine
from bot.portfolio.models import Fill, Order
from bot.portfolio.portfolio import PaperPortfolio
from bot.portfolio.venue_ledger import infer_base_asset
from bot.strategies.entry_quality import (
    EntryQualityAssessment,
    EntryQualityDiagnostics,
    apply_size_multiplier,
    config_from_settings,
    evaluate_entry_quality,
)
from bot.strategies.opportunity_economics import (
    EconomicDiagnostics,
    MFERecord,
    adaptive_trail_should_hold,
    compute_mfe_record,
    config_capital_efficiency_from_settings,
    config_venue_economics_from_settings,
    underwater_recovery_metrics,
)
from bot.strategies.opportunity_engine import (
    config_from_settings as opportunity_engine_config_from_settings,
    evaluate as evaluate_opportunity_engine,
)

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_MIN_LIVE_NOTIONAL = Decimal("5")
_FILL_POLL_SECONDS = 1.5
_FILL_POLL_INTERVAL = 0.15
_DEFAULT_RESTING_MAX_AGE_SEC = 90.0
_PERSIST_DEBOUNCE_SEC = 2.5
_QUOTE_FEE_CURRENCIES = frozenset({"EUR", "USDT", "USDC", "USD"})


def _buy_lot_qty_and_unit(
    *,
    amount: Decimal,
    price: Decimal,
    fee_amt: Decimal,
    fee_cur: str,
    base: str,
    quote: str,
) -> tuple[Decimal, Decimal]:
    """Fee-aware buy lot: (lot_qty, unit_cost in quote).

    OKX often charges maker fees in the base asset. Treating those as quote
    understates EUR cost and can authorize a false "profitable" sell.
    """
    if amount <= 0 or price <= 0:
        return amount, price
    fee_amt = max(_ZERO, Decimal(str(fee_amt or 0)))
    fee_cur_u = str(fee_cur or "").strip().upper()
    base_u = str(base or "").strip().upper()
    quote_u = str(quote or "").strip().upper()
    notional = amount * price
    if fee_amt <= 0:
        return amount, price

    quote_aliases = _QUOTE_FEE_CURRENCIES | ({quote_u} if quote_u else set())
    treat_as_base = fee_cur_u == base_u
    if not treat_as_base and fee_cur_u not in quote_aliases:
        # Missing/unknown fee currency: if fee looks too large vs quote-fee
        # expectations but small vs base size, treat as base (OKX pattern).
        quote_fee_floor = notional * Decimal("0.0005")
        if (
            fee_amt >= quote_fee_floor * 2
            and fee_amt / amount <= Decimal("0.01")
            and fee_amt < notional
        ):
            treat_as_base = True

    if treat_as_base:
        received = amount - fee_amt
        if received <= 0:
            return amount, (notional / amount) if amount > 0 else price
        return received, notional / received

    return amount, (notional + fee_amt) / amount


class MicroBudgetLiveExecutor(PaperExecutor):
    """PaperExecutor subclass: live taker fills + paper maker, € capital pocket."""

    name = "micro_budget_live"

    def __init__(
        self,
        settings: Settings,
        *,
        portfolio: PaperPortfolio,
        live_engine: LiveMicroEngine,
        budget_eur: Decimal,
        execute_venues: set[str] | None = None,
        exclude_bases: set[str] | None = None,
        live_maker: bool = False,
        allowed_bases: set[str] | None = None,
    ) -> None:
        super().__init__(settings, portfolio=portfolio)
        self._live = live_engine
        self._budget = Decimal(str(budget_eur))
        self._turnover = _ZERO  # lifetime traded notional (stats only)
        self._execute_venues = {
            v.strip().lower() for v in (execute_venues or {"bitvavo"}) if v.strip()
        }
        self._exclude_bases = {
            b.strip().upper() for b in (exclude_bases or set()) if b.strip()
        }
        self._allowed_bases = (
            {b.strip().upper() for b in allowed_bases if b and str(b).strip()}
            if allowed_bases is not None
            else None
        )
        self._live_maker = bool(live_maker)
        self.skips: dict[str, int] = {}
        self.live_trades: list[dict[str, Any]] = []
        self.recent_live_fills: list[dict[str, Any]] = []
        self._last_sync: dict[str, Any] | None = None
        self._resting: list[dict[str, Any]] = []
        self.live_fill_count = 0
        self.live_transaction_count = 0  # legacy alias of session counters
        self.session_live_fill_count = 0
        self.session_live_transaction_count = 0
        self.backfill_mirrored_count = 0
        self.realized_trade_pnl_eur = _ZERO  # closed-trade PnL after fees
        self._persist_path = Path(
            str(
                getattr(
                    settings,
                    "live_micro_bridge_persist_path",
                    "./data/live_micro_bridge_state.json",
                )
            )
        )
        self._long_hold_bases = {
            b.strip().upper()
            for b in str(
                getattr(settings, "live_micro_long_hold_bases", "ETH") or "ETH"
            ).split(",")
            if b.strip()
        }
        self.portfolio_value_eur: Decimal | None = None
        self.starting_portfolio_eur: Decimal | None = None
        self.session_start_realized_eur: Decimal | None = None
        # FIFO lots for realized PnL: base -> [(qty, unit_cost_eur)]
        self._cost_lots: dict[str, list[list[Decimal]]] = {}
        self._lots_seeded_venues: set[str] = set()
        # Only session fills / exchange trade history count as trusted cost basis.
        # Mark-seeded lots must not authorize a sell (would allow selling below true buy).
        self._trusted_cost_keys: set[str] = set()
        self._mirrored_trade_ids: set[str] = set()
        self._exit_cooldown_mono: dict[str, float] = {}
        self._session_started_ms: float | None = None
        self._resting_max_age_sec = float(
            getattr(settings, "live_micro_resting_max_age_sec", _DEFAULT_RESTING_MAX_AGE_SEC)
            or _DEFAULT_RESTING_MAX_AGE_SEC
        )
        self._bal_cache: dict[str, list[Any]] = {}
        self._bal_cache_mono: dict[str, float] = {}
        self._bal_cache_sec = 2.5
        self._venue_raw_balances: dict[str, list[Any]] = {}
        self._last_sync_by_venue: dict[str, dict[str, Any]] = {}
        self._mark_fetched_at: dict[str, float] = {}
        self._mark_ttl_sec = float(
            getattr(settings, "live_micro_mark_ttl_sec", 5.0) or 5.0
        )
        self._last_orphan_sweep_mono = 0.0
        self._orphan_sweep_sec = 60.0
        self._persist_debounce_sec = _PERSIST_DEBOUNCE_SEC
        self._last_persist_mono = 0.0
        self._persist_dirty = False
        # Trailing take-profit (soft/hard + ATR) on session buys.
        from bot.live.trail_policy import MarkSeries, parse_corr_group

        self._trail: dict[str, dict[str, Any]] = {}
        self._session_lots: dict[str, list[list[Decimal]]] = {}
        self._mark_series: dict[str, MarkSeries] = {}
        self._alerts: list[dict[str, Any]] = []
        self._trail_enabled = bool(
            getattr(settings, "paper_trail_take_profit_enabled", False)
        )
        self._trail_session_only = bool(
            getattr(settings, "paper_trail_session_buys_only", True)
        )
        self._soft_arm_floor = Decimal(
            str(getattr(settings, "paper_trail_soft_arm_pct", 0.12) or 0.12)
        )
        self._soft_dd_floor = Decimal(
            str(getattr(settings, "paper_trail_soft_drawdown_pct", 0.08) or 0.08)
        )
        # 0.0 is valid: skip early soft clip and let the soft trail exit the full bag.
        _soft_partial_raw = getattr(settings, "paper_trail_soft_partial_pct", 0.25)
        self._soft_partial = Decimal(
            str(0.25 if _soft_partial_raw is None else _soft_partial_raw)
        )
        self._recovery_be_partial = Decimal(
            str(getattr(settings, "paper_trail_recovery_be_partial_pct", 0) or 0)
        )
        _be_harvest_raw = getattr(settings, "paper_trail_be_harvest_partial_pct", None)
        if _be_harvest_raw is None or _be_harvest_raw == 0:
            _be_harvest_raw = getattr(
                settings, "paper_trail_recovery_be_partial_pct", 0
            )
        self._be_harvest_partial = Decimal(str(_be_harvest_raw or 0))
        self._be_harvest_min_gain = Decimal(
            str(getattr(settings, "paper_trail_be_harvest_min_gain_pct", 0.0005) or 0)
        )
        self._be_harvest_cooldown = float(
            getattr(settings, "live_micro_be_harvest_cooldown_sec", 15.0) or 15.0
        )
        self._cut_loss_below_be_pct = Decimal(
            str(getattr(settings, "live_micro_cut_loss_below_be_pct", 0) or 0)
        )
        self._early_cut_loss_below_be_pct = Decimal(
            str(getattr(settings, "live_micro_early_cut_loss_below_be_pct", 0) or 0)
        )
        self._early_cut_new_bases_only = bool(
            getattr(settings, "live_micro_early_cut_new_bases_only", True)
        )
        self._early_cut_momentum_max = Decimal(
            str(
                getattr(settings, "live_micro_early_cut_momentum_max_return", 0) or 0
            )
        )
        self._cut_loss_new_bases_only = bool(
            getattr(settings, "live_micro_cut_loss_new_bases_only", False)
        )
        self._momentum_exit_above_be_pct = Decimal(
            str(getattr(settings, "live_micro_momentum_exit_above_be_pct", 0.005) or 0)
        )
        self._momentum_exit_min = Decimal(
            str(getattr(settings, "live_micro_momentum_exit_min_return", 0.003) or 0)
        )
        self._okx_buy_improve_bps = Decimal(
            str(getattr(settings, "live_micro_okx_buy_improve_bps", 0) or 0)
        ) / Decimal("10000")
        self._trail_partial_min_frac = Decimal(
            str(getattr(settings, "live_micro_trail_partial_min_frac", 0.45) or 0.45)
        )
        self._hard_arm_floor = Decimal(
            str(
                getattr(settings, "paper_trail_hard_arm_pct", None)
                or getattr(settings, "paper_trail_arm_gain_pct", 0.30)
                or 0.30
            )
        )
        self._hard_dd_floor = Decimal(
            str(
                getattr(settings, "paper_trail_hard_drawdown_pct", None)
                or getattr(settings, "paper_trail_drawdown_pct", 0.12)
                or 0.12
            )
        )
        self._hard_partial = Decimal(
            str(getattr(settings, "paper_trail_hard_partial_pct", 0.25) or 0.25)
        )
        # Legacy aliases used by snapshot / older tests.
        self._trail_arm_gain = self._hard_arm_floor
        self._trail_drawdown = self._hard_dd_floor
        self._trail_partial_enabled = True
        self._trail_partial_pct = self._soft_partial
        self._atr_enabled = bool(getattr(settings, "paper_trail_atr_enabled", True))
        self._atr_samples = int(getattr(settings, "paper_trail_atr_samples", 48) or 48)
        self._atr_arm_mult = Decimal(
            str(getattr(settings, "paper_trail_atr_arm_mult", 2.5) or 2.5)
        )
        self._atr_dd_mult = Decimal(
            str(getattr(settings, "paper_trail_atr_dd_mult", 1.0) or 1.0)
        )
        self._ladder_enabled = bool(
            getattr(settings, "paper_ladder_buy_enabled", False)
        )
        raw_ladder = str(
            getattr(settings, "paper_ladder_buy_pcts", "0.01,0.02,0.03") or ""
        )
        self._ladder_pcts: list[Decimal] = []
        for part in raw_ladder.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                self._ladder_pcts.append(Decimal(part))
            except Exception:  # noqa: BLE001
                continue
        if not self._ladder_pcts:
            self._ladder_pcts = [Decimal("0.01"), Decimal("0.02"), Decimal("0.03")]
        self._time_stop_enabled = bool(
            getattr(settings, "paper_time_stop_enabled", False)
        )
        self._time_stop_sec = float(
            getattr(settings, "paper_time_stop_sec", 86400) or 86400
        )
        self._dust_policy = str(
            getattr(settings, "paper_dust_policy", "off") or "off"
        ).strip().lower()
        self._dust_exit_slack = Decimal(
            str(getattr(settings, "paper_dust_exit_slack_bps", 0) or 0)
        ) / Decimal("10000")
        self._regime_block_buys = bool(
            getattr(settings, "paper_regime_block_buys", True)
        )
        self._buys_blocked = False
        self._buys_blocked_new_bases_only = False
        self._underwater_blocked_bases: dict[str, set[str]] = {}
        self._underwater_new_bases_only = True
        self._daily_kill_active = False
        self._daily_kill_eur = Decimal(
            str(getattr(settings, "paper_daily_kill_eur", 50) or 50)
        )
        self._alert_pct_to_arm = Decimal(
            str(getattr(settings, "paper_alert_pct_to_arm", 0.05) or 0.05)
        )
        self._momentum_enabled = bool(
            getattr(settings, "paper_buy_momentum_enabled", False)
        )
        self._momentum_min = Decimal(
            str(getattr(settings, "paper_buy_momentum_min_return", 0) or 0)
        )
        self._momentum_samples = int(
            getattr(settings, "paper_buy_momentum_samples", 12) or 12
        )
        self._ring_momentum_min = Decimal(
            str(getattr(settings, "live_micro_ring_momentum_min_return", 0) or 0)
        )
        self._ring_soft_max_active_eur = Decimal(
            str(getattr(settings, "live_micro_ring_soft_max_active_eur", 500) or 500)
        )
        self._low_util_rising_n = int(
            getattr(settings, "live_micro_low_util_rising_n", 2) or 0
        )
        self._low_util_buy_resting_max_age_sec = float(
            getattr(settings, "live_micro_low_util_buy_resting_max_age_sec", 60.0)
            or 60.0
        )
        self._buy_resting_max_age_sec = float(
            getattr(settings, "live_micro_buy_resting_max_age_sec", 45.0) or 45.0
        )
        self._max_resting_buys_per_symbol = int(
            getattr(settings, "live_micro_max_resting_buys_per_symbol", 2) or 1
        )
        self._cancel_buy_on_flat_momentum = bool(
            getattr(settings, "live_micro_cancel_buy_on_flat_momentum", True)
        )
        self._momentum_require_last_n_rising = int(
            getattr(settings, "live_micro_momentum_require_last_n_rising", 3) or 0
        )
        self._trail_hold_while_rising = bool(
            getattr(settings, "live_micro_trail_hold_while_rising", True)
        )
        self._trail_hold_rising_n = int(
            getattr(settings, "live_micro_trail_hold_rising_n", 2) or 0
        )
        self._ring_soft_block_underwater_eur = Decimal(
            str(
                getattr(settings, "live_micro_ring_soft_block_underwater_eur", 25)
                or 25
            )
        )
        self._entry_min_low_util_rising_n = int(
            getattr(settings, "live_micro_entry_min_low_util_rising_n", 3) or 3
        )
        self._entry_short_momentum_samples = int(
            getattr(settings, "live_micro_entry_short_momentum_samples", 6) or 6
        )
        self._entry_short_momentum_min = Decimal(
            str(
                getattr(settings, "live_micro_entry_short_momentum_min_return", 0.001)
                or 0.001
            )
        )
        self._corr_sector_momentum_block = int(
            getattr(settings, "live_micro_corr_sector_momentum_block", 2) or 0
        )
        self._buy_quality_underwater_count = int(
            getattr(settings, "live_micro_buy_quality_underwater_count", 4) or 0
        )
        self._buy_quality_pause_sec = float(
            getattr(settings, "live_micro_buy_quality_pause_sec", 2700.0) or 2700.0
        )
        self._block_underwater_cross_venue = bool(
            getattr(settings, "live_micro_block_underwater_cross_venue", True)
        )
        self._buy_quality_pause_until = 0.0
        self._entry_quality_config = config_from_settings(settings)
        self._entry_quality_enabled = bool(self._entry_quality_config.enabled)
        self._entry_quality_diagnostics = EntryQualityDiagnostics()
        self._economic_diagnostics = EconomicDiagnostics()
        self._opportunity_diagnostics = None
        self._capital_efficiency_config = config_capital_efficiency_from_settings(settings)
        self._venue_economics_config = config_venue_economics_from_settings(settings)
        self._capital_efficiency_enabled = bool(
            self._capital_efficiency_config.enabled
        )
        self._mfe_analytics_enabled = bool(
            getattr(settings, "live_micro_mfe_analytics_enabled", True)
        )
        self._adaptive_trail_enabled = bool(
            getattr(settings, "live_micro_adaptive_trail_enabled", True)
        )
        self._opportunity_engine_config = opportunity_engine_config_from_settings(
            settings
        )
        self._opportunity_engine_enabled = bool(
            self._opportunity_engine_config.enabled
        )
        self._recent_session_buy_keys: list[str] = []
        self._corr_group = parse_corr_group(
            str(getattr(settings, "live_micro_corr_group", "") or "")
        )
        self._max_per_corr = int(
            getattr(settings, "live_micro_max_per_corr_group", 2) or 2
        )
        self._focus_bases = {
            part.strip().upper()
            for part in str(
                getattr(settings, "live_micro_focus_bases", "") or ""
            ).split(",")
            if part.strip()
        }
        self._new_buy_focus_only = bool(
            getattr(settings, "live_micro_new_buy_focus_only", False)
        )
        self._low_util_relax_focus = bool(
            getattr(settings, "live_micro_low_util_relax_focus", False)
        )
        self._winner_add_enabled = bool(
            getattr(settings, "live_micro_winner_add_enabled", False)
        )
        self._winner_add_max = int(
            getattr(settings, "live_micro_winner_add_max", 2) or 0
        )
        self._winner_add_clip_eur = Decimal(
            str(getattr(settings, "live_micro_winner_add_clip_eur", 55) or 55)
        )
        self._winner_add_cooldown_sec = float(
            getattr(settings, "live_micro_winner_add_cooldown_sec", 60.0) or 60.0
        )
        self._position_opened_mono: dict[str, float] = {}
        self._position_opened_at: dict[str, float] = {}
        self._max_alt_bases = int(
            getattr(settings, "live_micro_max_alt_bases", 0) or 0
        )
        self._block_cross_venue_duplicate_bases = bool(
            getattr(settings, "live_micro_block_cross_venue_duplicate_bases", True)
        )
        self._consolidate_duplicates = bool(
            getattr(settings, "live_micro_consolidate_duplicate_bases", True)
        )
        self._consolidate_primary = str(
            getattr(settings, "live_micro_consolidate_primary_venue", "bitvavo")
            or "bitvavo"
        ).strip().lower()
        self._first_clip_eur = Decimal(
            str(getattr(settings, "live_micro_first_clip_eur", 0) or 0)
        )
        self._add_clip_eur = Decimal(
            str(getattr(settings, "live_micro_add_clip_eur", 0) or 0)
        )
        self._active_ring_eur = Decimal(
            str(getattr(settings, "live_micro_active_ring_eur", 1000) or 1000)
        )
        # A: velocity sleeve — working capital; vault = rest (strict never-loss).
        _sleeve = getattr(settings, "live_micro_velocity_sleeve_eur", None)
        self._velocity_sleeve_eur = Decimal(
            str(_sleeve if _sleeve is not None else self._active_ring_eur)
            or self._active_ring_eur
        )
        self._sleeve_daily_loss_cap = Decimal(
            str(
                getattr(settings, "live_micro_velocity_sleeve_daily_loss_cap_eur", 25)
                or 25
            )
        )
        self._sleeve_realized_eur = _ZERO
        self._sleeve_paused = False
        # D: exit engine — aggressive BE+ / soft-armed fill seeking.
        self._exit_engine_enabled = bool(
            getattr(settings, "live_micro_exit_engine_enabled", True)
        )
        self._exit_resting_max_age_sec = float(
            getattr(settings, "live_micro_exit_resting_max_age_sec", 8.0) or 8.0
        )
        self._exit_engine_cooldown_sec = float(
            getattr(settings, "live_micro_exit_cooldown_sec", 3.0) or 3.0
        )
        self._exit_touch_improve_bps = Decimal(
            str(getattr(settings, "live_micro_exit_touch_improve_bps", 2.0) or 2.0)
        )
        self._exit_soft_armed_work = bool(
            getattr(settings, "live_micro_exit_soft_armed_work", True)
        )
        self._exit_soft_armed_partial = Decimal(
            str(
                getattr(settings, "live_micro_exit_soft_armed_partial_pct", 0.75)
                or 0.75
            )
        )
        self._exit_taker_cushion_bps = Decimal(
            str(getattr(settings, "live_micro_exit_taker_cushion_bps", 5.0) or 5.0)
        )
        self._exit_taker_after_maker_fails = int(
            getattr(settings, "live_micro_exit_taker_after_maker_fails", 1) or 1
        )
        self._winnable_gap_alert_eur = Decimal(
            str(getattr(settings, "live_micro_winnable_gap_alert_eur", 3.0) or 3.0)
        )
        self._daily_baseline_reset_utc = bool(
            getattr(settings, "live_micro_daily_baseline_reset_utc", True)
        )
        self._exit_maker_fail_counts: dict[str, int] = {}
        self._utc_day_marker = ""
        self._winnable_gap_alert_sent = False
        self._okx_ring_clip_eur = Decimal(
            str(getattr(settings, "live_micro_okx_ring_clip_eur", 50) or 50)
        )
        # Session counters for exit-engine / sleeve observability.
        self._exit_quote_counts: dict[str, int] = {}
        self._exit_fill_counts: dict[str, int] = {}
        self._exit_pending_counts: dict[str, int] = {}
        self._exit_reject_counts: dict[str, int] = {}
        self._block_underwater_adds = bool(
            getattr(settings, "live_micro_block_underwater_adds", True)
        )
        self._block_buys_when_holding_base = bool(
            getattr(settings, "live_micro_block_buys_when_holding_base", True)
        )
        self._MarkSeries = MarkSeries
        self._try_load_persisted_state()

    def _is_long_hold(self, base: str) -> bool:
        return str(base or "").strip().upper() in self._long_hold_bases

    def _balance_qty(self, venue: str, base: str) -> Decimal:
        venue_l = venue.strip().lower()
        base_u = str(base or "").strip().upper()
        for bal in self._venue_raw_balances.get(venue_l) or []:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if asset != base_u:
                continue
            return Decimal(str(getattr(bal, "free", 0) or 0)) + Decimal(
                str(getattr(bal, "locked", 0) or 0)
            )
        sync = self._last_sync_by_venue.get(venue_l) or {}
        ledger = sync.get("ledger") or {}
        if base_u in ledger:
            try:
                return Decimal(str(ledger.get(base_u) or 0))
            except Exception:  # noqa: BLE001
                pass
        mapped = sync.get("balances") or {}
        if base_u in mapped:
            try:
                return Decimal(str(mapped.get(base_u) or 0))
            except Exception:  # noqa: BLE001
                pass
        bal = self._portfolio.state.balances.get(base_u)
        if bal is not None and bal.total > 0:
            return Decimal(str(bal.total))
        return _ZERO

    def _blocked_sells_session(self) -> int:
        return int(self.skips.get("sell_below_break_even", 0) or 0) + int(
            self.skips.get("time_stop_below_be", 0) or 0
        )

    def _bag_winnable_eur(
        self,
        venue: str,
        base: str,
        *,
        cost: Decimal,
        mark: Decimal,
        qty: Decimal,
    ) -> Decimal:
        """EUR profit sellable now: mark above fee-aware break-even only."""
        if qty <= 0 or mark <= 0 or cost <= 0:
            return _ZERO
        be = self._break_even_sell_price(venue, base)
        if be is None or mark < be:
            return _ZERO
        return (mark - be) * qty

    def _mtm_summary(self) -> dict[str, str]:
        unrealized = _ZERO
        winnable = _ZERO
        locked = _ZERO
        micro_locked = _ZERO
        long_hold_locked = _ZERO
        seen: set[str] = set()
        for trail_key, st in self._trail.items():
            try:
                cost = Decimal(str(st.get("cost") or 0))
                mark = Decimal(str(st.get("last_mark") or 0))
            except Exception:  # noqa: BLE001
                continue
            if cost <= 0 or mark <= 0:
                continue
            venue = str(st.get("venue") or trail_key.split(":", 1)[0])
            base = str(st.get("base") or trail_key.split(":", 1)[-1])
            qty = self._balance_qty(venue, base)
            if qty <= 0:
                qty = Decimal(str(st.get("session_qty") or 0))
            if qty <= 0:
                continue
            seen.add(trail_key)
            notional = qty * mark
            locked += notional
            unrealized += (mark - cost) * qty
            winnable += self._bag_winnable_eur(
                venue, base, cost=cost, mark=mark, qty=qty
            )
            if self._is_long_hold(base):
                long_hold_locked += notional
            else:
                micro_locked += notional
        for venue in sorted(self._execute_venues):
            for bal in self._venue_raw_balances.get(venue) or []:
                asset = str(getattr(bal, "asset", "") or "").upper()
                if not asset or asset == self._quote or asset in self._exclude_bases:
                    continue
                if not self._is_long_hold(asset):
                    continue
                trail_key = self._lots_key(venue, asset)
                if trail_key in seen:
                    continue
                qty = self._balance_qty(venue, asset)
                if qty <= 0:
                    continue
                symbol = f"{asset}{self._quote}"
                mark = Decimal(
                    str(
                        self._portfolio.state.mark_prices.get(symbol)
                        or self._unit_cost(venue, asset)
                        or 0
                    )
                )
                cost = self._unit_cost(venue, asset) or mark
                if mark <= 0:
                    continue
                notional = qty * mark
                locked += notional
                long_hold_locked += notional
                if cost > 0:
                    unrealized += (mark - cost) * qty
                    winnable += self._bag_winnable_eur(
                        venue, asset, cost=cost, mark=mark, qty=qty
                    )
        baseline = self.session_start_realized_eur
        if baseline is not None:
            session_delta = self.realized_trade_pnl_eur - baseline
        else:
            session_delta = self.realized_trade_pnl_eur
        winnable_gap = max(_ZERO, winnable - max(_ZERO, session_delta))
        return {
            "unrealized_mtm_eur": str(unrealized.quantize(Decimal("0.01"))),
            "winnable_mtm_eur": str(winnable.quantize(Decimal("0.01"))),
            "winnable_gap_eur": str(winnable_gap.quantize(Decimal("0.01"))),
            "locked_notional_eur": str(locked.quantize(Decimal("0.01"))),
            "micro_locked_notional_eur": str(micro_locked.quantize(Decimal("0.01"))),
            "long_hold_notional_eur": str(long_hold_locked.quantize(Decimal("0.01"))),
            "blocked_sells_session": str(self._blocked_sells_session()),
        }

    def _serialize_lots(
        self, lots: dict[str, list[list[Decimal]]]
    ) -> dict[str, list[list[str]]]:
        out: dict[str, list[list[str]]] = {}
        for key, rows in lots.items():
            out[key] = [[str(qty), str(unit)] for qty, unit in rows]
        return out

    def _deserialize_lots(
        self, raw: dict[str, list[list[str]]] | None
    ) -> dict[str, list[list[Decimal]]]:
        out: dict[str, list[list[Decimal]]] = {}
        for key, rows in (raw or {}).items():
            parsed: list[list[Decimal]] = []
            for row in rows or []:
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    continue
                parsed.append([Decimal(str(row[0])), Decimal(str(row[1]))])
            if parsed:
                out[str(key)] = parsed
        return out

    def export_runtime_state(self) -> dict[str, Any]:
        resting: list[dict[str, Any]] = []
        for row in self._resting:
            opp = row.get("opportunity_id")
            resting.append(
                {
                    **{k: v for k, v in row.items() if k != "opportunity_id"},
                    "opportunity_id": str(opp) if opp is not None else None,
                    "quantity": str(row.get("quantity") or 0),
                    "price": str(row.get("price") or 0),
                    "placed_at": float(
                        row.get("placed_at") or row.get("placed_mono") or time.time()
                    ),
                }
            )

        def _jsonify(value: Any) -> Any:
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, dict):
                return {str(k): _jsonify(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_jsonify(v) for v in value]
            return value

        return {
            "version": 1,
            "saved_at": time.time(),
            "session_started_ms": self._session_started_ms,
            "trail": _jsonify(self._trail),
            "resting": resting,
            "mirrored_trade_ids": sorted(self._mirrored_trade_ids),
            "session_lots": self._serialize_lots(self._session_lots),
            "position_opened_at": dict(self._position_opened_at),
            "skips": dict(self.skips),
            "session_live_fill_count": int(self.session_live_fill_count),
            "session_live_transaction_count": int(self.session_live_transaction_count),
            "backfill_mirrored_count": int(self.backfill_mirrored_count),
            "live_fill_count": int(self.live_fill_count),
            "live_transaction_count": int(self.live_transaction_count),
            "realized_trade_pnl_eur": str(self.realized_trade_pnl_eur),
            "sleeve_realized_eur": str(self._sleeve_realized_eur),
            "sleeve_paused": bool(self._sleeve_paused),
        }

    def _try_load_persisted_state(self) -> bool:
        return self.load_persisted_state(self._persist_path)

    def load_persisted_state(self, path: Path | str | None = None) -> bool:
        p = Path(path) if path is not None else self._persist_path
        if not p.exists():
            return False
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("micro bridge persist load failed path=%s err=%s", p, exc)
            return False
        if not isinstance(raw, dict):
            return False
        self._trail = {
            str(k): (v if isinstance(v, dict) else {})
            for k, v in (raw.get("trail") or {}).items()
        }
        self._recent_session_buy_keys = [
            key
            for key, st in self._trail.items()
            if isinstance(st, dict) and st.get("new_session_base")
        ]
        self._resting = []
        for row in raw.get("resting") or []:
            if not isinstance(row, dict):
                continue
            self._resting.append(
                {
                    **row,
                    "quantity": Decimal(str(row.get("quantity") or 0)),
                    "price": Decimal(str(row.get("price") or 0)),
                    "placed_mono": time.monotonic(),
                }
            )
        self._mirrored_trade_ids = {
            str(x) for x in (raw.get("mirrored_trade_ids") or []) if str(x)
        }
        self._session_lots = self._deserialize_lots(raw.get("session_lots"))
        self._position_opened_at = {
            str(k): float(v)
            for k, v in (raw.get("position_opened_at") or {}).items()
            if v is not None
        }
        now = time.time()
        self._position_opened_mono = {
            key: time.monotonic() - max(0.0, now - opened)
            for key, opened in self._position_opened_at.items()
        }
        self.skips = {
            str(k): int(v)
            for k, v in (raw.get("skips") or {}).items()
            if str(k)
        }
        self.session_live_fill_count = int(raw.get("session_live_fill_count") or 0)
        self.session_live_transaction_count = int(
            raw.get("session_live_transaction_count") or 0
        )
        self.backfill_mirrored_count = int(raw.get("backfill_mirrored_count") or 0)
        self.live_fill_count = self.session_live_fill_count
        self.live_transaction_count = self.session_live_transaction_count
        try:
            self.realized_trade_pnl_eur = Decimal(
                str(raw.get("realized_trade_pnl_eur") or 0)
            )
        except Exception:  # noqa: BLE001
            self.realized_trade_pnl_eur = _ZERO
        try:
            self._sleeve_realized_eur = Decimal(
                str(raw.get("sleeve_realized_eur") or 0)
            )
        except Exception:  # noqa: BLE001
            self._sleeve_realized_eur = _ZERO
        self._sleeve_paused = bool(raw.get("sleeve_paused"))
        self._check_sleeve_loss_cap()
        if raw.get("session_started_ms") is not None:
            self._session_started_ms = float(raw.get("session_started_ms"))
        logger.info(
            "micro bridge state loaded path=%s trail=%s resting=%s session_fills=%s",
            p,
            len(self._trail),
            len(self._resting),
            self.session_live_transaction_count,
        )
        self._sanitize_persisted_trails()
        return True

    def persist_runtime_state(self, *, force: bool = False) -> None:
        path = self._persist_path
        now = time.monotonic()
        if (
            not force
            and now - self._last_persist_mono < self._persist_debounce_sec
        ):
            self._persist_dirty = True
            return
        self._persist_dirty = False
        self._last_persist_mono = now
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(self.export_runtime_state(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp.replace(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("micro bridge persist failed path=%s err=%s", path, exc)

    def flush_runtime_state(self) -> None:
        """Force-write debounced bridge state (session shutdown / after fills)."""
        if self._persist_dirty or self._last_persist_mono <= 0:
            self.persist_runtime_state(force=True)

    def set_buys_blocked(self, blocked: bool, *, new_bases_only: bool = False) -> None:
        """Regime guard: when True, reject BUY orders on all venues (sells/trails still run).

        Used for reduce-only, toxic HMM, and daily-kill — not per-venue underwater.
        """
        self._buys_blocked = bool(blocked) or self._daily_kill_active
        self._buys_blocked_new_bases_only = (
            bool(new_bases_only) and bool(blocked) and not self._daily_kill_active
        )

    def set_underwater_base_blocks(
        self,
        blocked_bases: dict[str, set[str] | frozenset[str] | list[str]] | None,
        *,
        new_bases_only: bool = True,
    ) -> None:
        """Block buys only on specific underwater bases — other coins keep scanning."""
        self._underwater_blocked_bases = {
            v.strip().lower(): {str(b).upper() for b in bases if str(b).strip()}
            for v, bases in (blocked_bases or {}).items()
            if str(v).strip() and bases
        }
        self._underwater_new_bases_only = bool(new_bases_only)

    def set_underwater_venue_blocks(
        self,
        blocked_venues: set[str] | frozenset[str],
        *,
        new_bases_only: bool = True,
    ) -> None:
        """Legacy alias — prefer set_underwater_base_blocks (whole-venue block removed)."""
        self.set_underwater_base_blocks({}, new_bases_only=new_bases_only)

    def _base_underwater_blocked(self, venue: str, base: str) -> bool:
        v = venue.strip().lower()
        b = str(base or "").upper()
        return bool(b) and b in self._underwater_blocked_bases.get(v, set())

    def underwater_bases(
        self,
        *,
        min_notional_eur: Decimal | float = 25,
        venue: str | None = None,
    ) -> dict[str, set[str]]:
        """Underwater micro bases by venue (mark < cost, notional ≥ floor)."""
        floor = Decimal(str(min_notional_eur or 0))
        venue_filter = venue.strip().lower() if venue else None
        out: dict[str, set[str]] = {}
        for trail_key, st in self._trail.items():
            if not isinstance(st, dict):
                continue
            try:
                cost = Decimal(str(st.get("cost") or 0))
                mark = Decimal(str(st.get("last_mark") or 0))
            except Exception:  # noqa: BLE001
                continue
            if cost <= 0 or mark <= 0 or mark >= cost:
                continue
            base = str(st.get("base") or trail_key.split(":", 1)[-1]).upper()
            if self._is_long_hold(base):
                continue
            bag_venue = str(st.get("venue") or trail_key.split(":", 1)[0]).strip().lower()
            if venue_filter is not None and bag_venue != venue_filter:
                continue
            qty = self._balance_qty(bag_venue, base)
            if qty * mark < floor:
                continue
            out.setdefault(bag_venue, set()).add(base)
        return out

    def underwater_bag_count(
        self,
        *,
        min_notional_eur: Decimal | float = 25,
        venue: str | None = None,
    ) -> int:
        """Count micro bags with mark < cost and meaningful notional (cash throttle)."""
        floor = Decimal(str(min_notional_eur or 0))
        venue_filter = venue.strip().lower() if venue else None
        n = 0
        for trail_key, st in self._trail.items():
            if not isinstance(st, dict):
                continue
            try:
                cost = Decimal(str(st.get("cost") or 0))
                mark = Decimal(str(st.get("last_mark") or 0))
            except Exception:  # noqa: BLE001
                continue
            if cost <= 0 or mark <= 0 or mark >= cost:
                continue
            base = str(st.get("base") or trail_key.split(":", 1)[-1])
            if self._is_long_hold(base):
                continue
            bag_venue = str(st.get("venue") or trail_key.split(":", 1)[0])
            if venue_filter is not None and bag_venue.strip().lower() != venue_filter:
                continue
            qty = self._balance_qty(bag_venue, base)
            if qty * mark < floor:
                continue
            n += 1
        return n

    def _push_alert(self, kind: str, message: str, **extra: Any) -> None:
        base = str(extra.get("base") or "")
        # Dedupe noisy near-arm / same-kind alerts within 5 minutes.
        now = time.time()
        for prev in reversed(self._alerts[-20:]):
            if (
                prev.get("kind") == kind
                and str(prev.get("base") or "") == base
                and now - float(prev.get("ts") or 0) < 300
            ):
                return
        row = {
            "ts": now,
            "kind": kind,
            "message": message,
            **extra,
        }
        self._alerts.append(row)
        if len(self._alerts) > 50:
            self._alerts = self._alerts[-50:]
        logger.warning("MICRO_ALERT kind=%s %s", kind, message)

    def _check_daily_kill(self) -> None:
        if self._daily_kill_eur <= 0:
            return
        baseline = self.session_start_realized_eur
        if baseline is not None:
            pnl = self.realized_trade_pnl_eur - baseline
        else:
            pnl = self.realized_trade_pnl_eur
        if pnl <= -self._daily_kill_eur:
            if not self._daily_kill_active:
                self._daily_kill_active = True
                self._buys_blocked = True
                self._push_alert(
                    "daily_kill",
                    f"session realized {pnl} <= -{self._daily_kill_eur}; buys blocked",
                )

    def _check_sleeve_loss_cap(self) -> None:
        """A: pause new sleeve buys when sleeve realized hits daily loss cap."""
        if self._sleeve_daily_loss_cap <= 0:
            self._sleeve_paused = False
            return
        if self._sleeve_realized_eur <= -self._sleeve_daily_loss_cap:
            if not self._sleeve_paused:
                self._sleeve_paused = True
                self._push_alert(
                    "sleeve_loss_cap",
                    f"sleeve realized {self._sleeve_realized_eur} "
                    f"<= -{self._sleeve_daily_loss_cap}; sleeve buys paused "
                    f"(vault / never-loss exits unchanged)",
                )
        else:
            self._sleeve_paused = False

    @staticmethod
    def _exit_fail_key(venue: str, base: str) -> str:
        return f"{venue.strip().lower()}:{base.upper()}"

    def _bump_exit_maker_fail(self, venue: str, base: str) -> int:
        key = self._exit_fail_key(venue, base)
        n = int(self._exit_maker_fail_counts.get(key, 0)) + 1
        self._exit_maker_fail_counts[key] = n
        return n

    def _clear_exit_maker_fail(self, venue: str, base: str) -> None:
        self._exit_maker_fail_counts.pop(self._exit_fail_key(venue, base), None)

    def _should_force_taker_exit(self, venue: str, base: str) -> bool:
        if self._exit_taker_after_maker_fails <= 0:
            return False
        key = self._exit_fail_key(venue, base)
        return int(self._exit_maker_fail_counts.get(key, 0)) >= self._exit_taker_after_maker_fails

    def maybe_utc_day_rollover(self) -> bool:
        """Reset sleeve daily cap and session PnL baseline at UTC midnight."""
        if not self._daily_baseline_reset_utc:
            return False
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if self._utc_day_marker == today:
            return False
        self._utc_day_marker = today
        self._sleeve_realized_eur = _ZERO
        self._sleeve_paused = False
        self.session_start_realized_eur = self.realized_trade_pnl_eur
        if self.portfolio_value_eur is not None:
            self.starting_portfolio_eur = self.portfolio_value_eur
        self._session_started_ms = time.time() * 1000.0
        self._daily_kill_active = False
        self._winnable_gap_alert_sent = False
        self._exit_maker_fail_counts.clear()
        self._push_alert(
            "daily_baseline_reset",
            f"UTC day {today}: sleeve + session baseline reset "
            f"(realized={self.realized_trade_pnl_eur})",
        )
        logger.info(
            "DAILY_BASELINE_RESET day=%s realized=%s portfolio=%s",
            today,
            self.realized_trade_pnl_eur,
            self.starting_portfolio_eur,
        )
        self.persist_runtime_state()
        return True

    def check_winnable_gap_alert(self) -> None:
        """Warn when winnable MTM is high but not yet converting to realized."""
        if self._winnable_gap_alert_eur <= 0:
            return
        mtm = self._mtm_summary()
        winnable = Decimal(str(mtm.get("winnable_mtm_eur") or 0))
        if winnable < self._winnable_gap_alert_eur:
            self._winnable_gap_alert_sent = False
            return
        if self._winnable_gap_alert_sent:
            return
        baseline = self.session_start_realized_eur
        if baseline is not None:
            session_delta = self.realized_trade_pnl_eur - baseline
        else:
            session_delta = self.realized_trade_pnl_eur
        self._winnable_gap_alert_sent = True
        self._push_alert(
            "winnable_gap",
            f"winnable €{float(winnable):.2f} vs session realized Δ "
            f"€{float(session_delta):.2f} — check exit fills",
        )

    def _invalidate_bal_cache(self, venue: str | None = None) -> None:
        if venue is None:
            self._bal_cache.clear()
            self._bal_cache_mono.clear()
            return
        key = venue.strip().lower()
        self._bal_cache.pop(key, None)
        self._bal_cache_mono.pop(key, None)

    @staticmethod
    def _lots_key(venue: str, base: str) -> str:
        return f"{venue.strip().lower()}:{base.upper()}"

    def _aggressive_buy_price(
        self, venue: str, px: Decimal, order_book: Any
    ) -> Decimal:
        """Maker buys: join/improve best bid when post-only safe (all venues)."""
        improve_bps = self._okx_buy_improve_bps
        if improve_bps <= 0:
            return px
        best_bid = _ZERO
        best_ask = _ZERO
        if order_book is not None:
            try:
                if order_book.bids:
                    best_bid = Decimal(str(order_book.bids[0].price))
                if order_book.asks:
                    best_ask = Decimal(str(order_book.asks[0].price))
            except Exception:  # noqa: BLE001
                pass
        if best_bid <= 0:
            return px
        touch = (best_bid * (Decimal("1") + improve_bps)).quantize(
            Decimal("0.00000001")
        )
        if best_ask > 0 and touch >= best_ask:
            touch = (best_ask * Decimal("0.9999")).quantize(Decimal("0.00000001"))
        return max(px, touch, best_bid)

    def _venue_budget_remaining(self, venue: str) -> Decimal:
        """Per-venue deployable EUR — each exchange gets its own pocket cap."""
        key = venue.strip().lower()
        ledger = self._portfolio.venue_ledger
        live_eur = _ZERO
        if ledger is not None:
            live_eur = ledger.available(key, self._quote)
        if live_eur <= 0:
            live_eur = self._live_free_sync(key, self._quote)
        if live_eur <= 0 and len(self._execute_venues) == 1:
            live_eur = self.free_quote_eur
        if live_eur > 0:
            return min(live_eur, self._budget)
        return _ZERO

    def _live_free_sync(self, venue: str, asset: str) -> Decimal:
        """Sync read of cached venue balances (no await)."""
        key = asset.upper()
        for bal in self._bal_cache.get(venue.strip().lower(), []):
            if str(getattr(bal, "asset", "")).upper() == key:
                return Decimal(str(getattr(bal, "free", 0) or 0))
        return _ZERO

    def _rebuild_aggregate_from_venues(self) -> dict[str, str]:
        """Merge all cached venue balances into the paper pocket."""
        from bot.core.models import Balance

        venue_maps: dict[str, list[Balance]] = {}
        for v, raw in self._venue_raw_balances.items():
            venue_maps[v] = [
                Balance(
                    asset=str(getattr(b, "asset", "") or ""),
                    free=Decimal(str(getattr(b, "free", 0) or 0)),
                    locked=Decimal(str(getattr(b, "locked", 0) or 0)),
                )
                for b in raw
            ]
        if not venue_maps:
            return {}
        return self._portfolio.sync_live_balances_from_venues(
            venue_maps,
            quote_available_cap=self._budget,
            allowed_bases=self._allowed_bases,
            exclude_bases=self._exclude_bases,
        )

    @property
    def free_quote_eur(self) -> Decimal:
        """Available EUR cash in the micro pocket (recycles after sells)."""
        try:
            return Decimal(str(self._portfolio.available(self._quote)))
        except Exception:  # noqa: BLE001
            return _ZERO

    @property
    def budget_remaining(self) -> Decimal:
        """Capital still free to deploy on buys — sum of per-venue pockets."""
        if len(self._execute_venues) > 1:
            total = sum(
                (self._venue_budget_remaining(v) for v in self._execute_venues),
                _ZERO,
            )
            return total
        free = self.free_quote_eur
        if free < 0:
            return _ZERO
        return free if free <= self._budget else self._budget

    def _momentum_display(self, symbol: str) -> dict[str, Any]:
        """Rolling mark momentum for dashboard arrows (↑ / ↓ / →)."""
        series = self._series_for(symbol)
        mom = series.momentum_return()
        if len(series) < 2 or mom is None:
            return {
                "direction": "flat",
                "arrow": "→",
                "return_pct": None,
                "samples": len(series),
            }
        mom_f = float(mom)
        up_threshold = float(self._momentum_min)
        down_threshold = float(self._momentum_exit_min)
        if mom_f >= up_threshold:
            direction, arrow = "up", "↑"
        elif mom_f <= -down_threshold:
            direction, arrow = "down", "↓"
        else:
            direction, arrow = "flat", "→"
        return {
            "direction": direction,
            "arrow": arrow,
            "return_pct": f"{mom_f * 100:.2f}",
            "samples": len(series),
        }

    def _portfolio_holdings_overview(self) -> list[dict[str, Any]]:
        """Compact held-inventory list for the live dashboard."""
        items: list[dict[str, Any]] = []
        for trail_key, st in self._trail_states_public().items():
            base = str(st.get("base") or "")
            venue = str(st.get("venue") or "")
            try:
                notional = Decimal(str(st.get("notional_eur") or 0))
            except Exception:  # noqa: BLE001
                continue
            if not base or notional <= 0:
                continue
            symbol = f"{base}{self._quote}"
            mom = self._momentum_display(symbol)
            row: dict[str, Any] = {
                "key": trail_key,
                "base": base,
                "venue": venue,
                "notional_eur": str(notional.quantize(Decimal("0.01"))),
                "gain_pct": st.get("gain_pct"),
                "role": st.get("role") or "micro_recycle",
                "momentum_direction": mom["direction"],
                "momentum_arrow": mom["arrow"],
                "momentum_return_pct": mom["return_pct"],
                "momentum_samples": mom["samples"],
            }
            be = self._break_even_sell_price(venue, base)
            mark = Decimal(str(st.get("last_mark") or 0))
            opened = self._position_opened_at.get(trail_key)
            age_sec = None
            if opened:
                age_sec = Decimal(str(max(0.0, time.time() - float(opened))))
            if be and mark > 0 and mark < be:
                recovery = underwater_recovery_metrics(
                    mark=mark,
                    break_even=be,
                    notional_eur=notional,
                    age_seconds=age_sec,
                    expected_hold_seconds=Decimal(
                        str(getattr(self._settings, "live_micro_expected_hold_seconds", 1800))
                    ),
                )
                row.update(recovery)
            items.append(row)
        items.sort(
            key=lambda row: Decimal(str(row.get("notional_eur") or 0)),
            reverse=True,
        )
        return items

    def snapshot_bridge(self) -> dict[str, Any]:
        return {
            "budget_eur": str(self._budget),
            "free_quote_eur": str(self.free_quote_eur),
            "remaining_eur": str(self.budget_remaining),
            "turnover_eur": str(self._turnover),
            "portfolio_value_eur": (
                str(self.portfolio_value_eur) if self.portfolio_value_eur is not None else None
            ),
            "starting_portfolio_eur": (
                str(self.starting_portfolio_eur)
                if self.starting_portfolio_eur is not None
                else None
            ),
            "session_start_realized_eur": (
                str(self.session_start_realized_eur)
                if self.session_start_realized_eur is not None
                else None
            ),
            # Operator PnL = realized trade profit after fees (not mark-to-market).
            "netto_winst_eur": str(self.realized_trade_pnl_eur),
            "realized_trade_pnl_eur": str(self.realized_trade_pnl_eur),
            "execute_venues": sorted(self._execute_venues),
            "exclude_bases": sorted(self._exclude_bases),
            "live_maker": self._live_maker,
            "skips": dict(self.skips),
            "live_trade_count": len(self.live_trades),
            "live_fill_count": int(self.session_live_fill_count),
            "live_transaction_count": int(self.session_live_transaction_count),
            "session_live_fill_count": int(self.session_live_fill_count),
            "session_live_transaction_count": int(self.session_live_transaction_count),
            "backfill_mirrored_count": int(self.backfill_mirrored_count),
            "resting_orders": len(self._resting),
            "long_hold_bases": sorted(self._long_hold_bases),
            **self._mtm_summary(),
            "capital_model": "pocket",
            "trail_take_profit": {
                "enabled": self._trail_enabled,
                "session_buys_only": self._trail_session_only,
                "soft_arm_pct": str(self._soft_arm_floor),
                "hard_arm_pct": str(self._hard_arm_floor),
                "arm_gain_pct": str(self._hard_arm_floor),
                "drawdown_pct": str(self._hard_dd_floor),
                "partial_enabled": True,
                "partial_pct": str(self._soft_partial),
                "atr_enabled": self._atr_enabled,
                "time_stop_sec": self._time_stop_sec if self._time_stop_enabled else None,
                "ladder_buy": self._ladder_enabled,
                "buys_blocked": self._buys_blocked,
                "underwater_blocked_bases": {
                    v: sorted(bases)
                    for v, bases in sorted(self._underwater_blocked_bases.items())
                },
                "daily_kill_active": self._daily_kill_active,
                "dust_policy": self._dust_policy,
                "momentum_enabled": self._momentum_enabled,
                "cut_loss_below_be_pct": str(self._cut_loss_below_be_pct),
                "early_cut_loss_below_be_pct": str(self._early_cut_loss_below_be_pct),
                "early_cut_new_bases_only": self._early_cut_new_bases_only,
                "early_cut_momentum_max_return": str(self._early_cut_momentum_max),
                "cut_loss_new_bases_only": self._cut_loss_new_bases_only,
                "momentum_exit_above_be_pct": str(self._momentum_exit_above_be_pct),
                "momentum_exit_min_return": str(self._momentum_exit_min),
                "momentum_min_return": str(self._momentum_min),
                "ring_momentum_min_return": str(self._ring_momentum_min),
                "ring_soft_max_active_eur": str(self._ring_soft_max_active_eur),
                "ring_soft_block_underwater_eur": str(self._ring_soft_block_underwater_eur),
                "entry_short_momentum_samples": self._entry_short_momentum_samples,
                "entry_short_momentum_min_return": str(self._entry_short_momentum_min),
                "entry_min_low_util_rising_n": self._entry_min_low_util_rising_n,
                "corr_sector_momentum_block": self._corr_sector_momentum_block,
                "low_util_rising_n": self._low_util_rising_n,
                "low_util_buy_resting_max_age_sec": self._low_util_buy_resting_max_age_sec,
                "buy_resting_max_age_sec": self._buy_resting_max_age_sec,
                "max_resting_buys_per_symbol": self._max_resting_buys_per_symbol,
                "low_util_relax_focus": self._low_util_relax_focus,
                "winner_add_enabled": self._winner_add_enabled,
                "winner_add_max": self._winner_add_max,
                "winner_add_clip_eur": str(self._winner_add_clip_eur),
                "winner_add_cooldown_sec": self._winner_add_cooldown_sec,
                "cancel_buy_on_flat_momentum": self._cancel_buy_on_flat_momentum,
                "momentum_require_last_n_rising": self._momentum_require_last_n_rising,
                "trail_hold_while_rising": self._trail_hold_while_rising,
                "trail_hold_rising_n": self._trail_hold_rising_n,
                "buy_quality_pause_active": self._buy_quality_paused(),
                "block_underwater_cross_venue": self._block_underwater_cross_venue,
                "corr_group": sorted(self._corr_group),
                "max_per_corr_group": self._max_per_corr,
                "states": self._trail_states_public(),
                "alerts": list(self._alerts[-10:]),
            },
            "alerts": list(self._alerts[-10:]),
            "max_alt_bases": self._max_alt_bases,
            "block_cross_venue_duplicate_bases": self._block_cross_venue_duplicate_bases,
            "consolidate_duplicate_bases": self._consolidate_duplicates,
            "consolidate_primary_venue": self._consolidate_primary,
            "duplicate_bases_by_venue": self._duplicate_bases_by_venue(),
            "first_clip_eur": str(self._first_clip_eur),
            "add_clip_eur": str(self._add_clip_eur),
            "active_ring_eur": str(self._active_ring_eur),
            "velocity_sleeve_eur": str(self._velocity_sleeve_eur),
            "sleeve_realized_eur": str(self._sleeve_realized_eur),
            "sleeve_daily_loss_cap_eur": str(self._sleeve_daily_loss_cap),
            "sleeve_paused": bool(self._sleeve_paused),
            "exit_engine": {
                "enabled": self._exit_engine_enabled,
                "resting_max_age_sec": self._exit_resting_max_age_sec,
                "cooldown_sec": self._exit_engine_cooldown_sec,
                "touch_improve_bps": str(self._exit_touch_improve_bps),
                "taker_cushion_bps": str(self._exit_taker_cushion_bps),
                "taker_after_maker_fails": self._exit_taker_after_maker_fails,
                "mark_ttl_sec": self._mark_ttl_sec,
                "maker_fail_counts": dict(self._exit_maker_fail_counts),
                "soft_armed_work": self._exit_soft_armed_work,
                "soft_armed_partial_pct": str(self._exit_soft_armed_partial),
                "quotes": dict(self._exit_quote_counts),
                "fills": dict(self._exit_fill_counts),
                "pending": dict(self._exit_pending_counts),
                "rejects": dict(self._exit_reject_counts),
            },
            "okx_ring_clip_eur": str(self._okx_ring_clip_eur),
            "active_book_notional_by_venue": {
                v: str(self._active_book_notional(v))
                for v in sorted(self._execute_venues)
            },
            "underwater_book_notional_by_venue": {
                v: str(self._underwater_book_notional(v))
                for v in sorted(self._execute_venues)
            },
            "corr_group_momentum_down_count": self._corr_group_momentum_down_count(),
            "held_alt_bases": sorted(self._held_alt_bases()),
            "held_alt_bases_by_venue": {
                v: sorted(self._held_alt_bases(v)) for v in sorted(self._execute_venues)
            },
            "portfolio_holdings": self._portfolio_holdings_overview(),
            "last_sync": self._last_sync,
            "last_sync_by_venue": dict(self._last_sync_by_venue),
            "diagnostics": {
                "realized_net_pnl_eur": str(self.realized_trade_pnl_eur),
                "live_fills": int(self.session_live_fill_count),
                "live_transactions": int(self.session_live_transaction_count),
                "session_live_fills": int(self.session_live_fill_count),
                "session_live_transactions": int(self.session_live_transaction_count),
                "backfill_mirrored": int(self.backfill_mirrored_count),
                **self._mtm_summary(),
                "recent_live_trades": list(self.live_trades[-12:]),
                "recent_live_fills": list(self.recent_live_fills[-12:]),
                "skip_leaders": sorted(
                    ((k, v) for k, v in self.skips.items()),
                    key=lambda kv: kv[1],
                    reverse=True,
                )[:12],
                "why_idle": self._why_idle_hints(),
                **self.economic_diagnostics_snapshot(),
            },
        }

    def _why_idle_hints(self) -> list[str]:
        """Operator-facing blockers — ordered by severity / current truth."""
        hints: list[str] = []
        held = self._held_alt_bases()
        held_by_venue = {
            v: self._held_alt_bases(v) for v in sorted(self._execute_venues)
        }

        ks = getattr(self, "_kill_switch", None)
        if ks is not None:
            try:
                state = getattr(getattr(ks, "state", None), "value", None) or str(
                    getattr(ks, "state", "")
                )
                if str(state).lower() in {"paused", "emergency_stop"}:
                    reason = getattr(ks, "reason", None) or "unknown"
                    hints.append(
                        f"RISK_KILL_SWITCH_{str(state).upper()}: {reason}"
                    )
            except Exception:  # noqa: BLE001
                pass

        if self._daily_kill_active:
            hints.append("DAILY_KILL")
        if self._buys_blocked:
            if self._buys_blocked_new_bases_only:
                hints.append("BUYS_BLOCKED_REGIME (new bases only)")
            else:
                hints.append("BUYS_BLOCKED_REGIME")
        if self._underwater_blocked_bases:
            mode = "new bases only" if self._underwater_new_bases_only else "all buys"
            bits = [
                f"{v}:{','.join(sorted(bases))}"
                for v, bases in sorted(self._underwater_blocked_bases.items())
                if bases
            ]
            hints.append(
                "UNDERWATER_BASE_BLOCK "
                + "; ".join(bits)
                + f" ({mode})"
            )

        resting_n = len(self._resting)
        if resting_n > 0:
            by_venue: dict[str, int] = {}
            for row in self._resting:
                v = str(row.get("venue") or "?").lower()
                by_venue[v] = by_venue.get(v, 0) + 1
            venue_bits = ",".join(f"{v}={n}" for v, n in sorted(by_venue.items()))
            hints.append(f"RESTING_ORDERS n={resting_n} ({venue_bits})")

        # Current underwater bags (not just lifetime skip counts).
        underwater: list[str] = []
        long_hold: list[str] = []
        waiting_arm: list[str] = []
        for trail_key, st in self._trail.items():
            try:
                cost = Decimal(str(st.get("cost") or 0))
                mark = Decimal(str(st.get("last_mark") or 0))
            except Exception:  # noqa: BLE001
                continue
            if cost <= 0 or mark <= 0:
                continue
            base = str(st.get("base") or trail_key.split(":", 1)[-1])
            gain = (mark - cost) / cost
            qty = self._balance_qty(
                str(st.get("venue") or trail_key.split(":", 1)[0]), base
            )
            notional = qty * mark if qty > 0 and mark > 0 else _ZERO
            notional_bit = f"€{float(notional):.0f}" if notional > 0 else ""
            label = f"{trail_key}:{float(gain * 100):+.2f}%"
            if notional_bit:
                label += f"({notional_bit})"
            if self._is_long_hold(base):
                long_hold.append(label)
                continue
            if gain < 0:
                underwater.append(label)
            elif not st.get("soft_armed"):
                soft = Decimal(str(st.get("soft_arm") or self._soft_arm_floor))
                need = soft - gain
                if need > 0:
                    waiting_arm.append(
                        f"{trail_key}:need+{float(need * 100):.2f}%"
                    )
        if long_hold:
            hints.append(
                "LONG_HOLD_OUTSIDE_MICRO "
                + ", ".join(long_hold[:6])
                + ("…" if len(long_hold) > 6 else "")
            )
        if underwater:
            hints.append(
                "HOLDING_BELOW_COST "
                + ", ".join(underwater[:8])
                + ("…" if len(underwater) > 8 else "")
            )
        be_skips = int(self.skips.get("sell_below_break_even", 0) or 0)
        ts_skips = int(self.skips.get("time_stop_below_be", 0) or 0)
        if underwater and (be_skips or ts_skips):
            hints.append(
                f"SELLS_BLOCKED_NEVER_LOSS sell_be={be_skips} time_stop_be={ts_skips}"
            )
        elif not underwater and be_skips > 1000:
            hints.append(
                f"SELLS_BLOCKED_NEVER_LOSS (sessie-totaal sell_be={be_skips}; "
                "geen bags onder water nu)"
            )
        mtm = self._mtm_summary()
        if Decimal(str(mtm.get("micro_locked_notional_eur") or 0)) > 0:
            hints.append(
                "MICRO_CAPITAL_LOCKED "
                f"micro=€{mtm.get('micro_locked_notional_eur')} "
                f"long_hold=€{mtm.get('long_hold_notional_eur')}"
            )
        if waiting_arm:
            hints.append(
                "WAITING_SOFT_ARM "
                + ", ".join(waiting_arm[:6])
                + ("…" if len(waiting_arm) > 6 else "")
            )

        if len(self._execute_venues) > 1:
            for venue, vheld in sorted(held_by_venue.items()):
                if self._max_alt_bases > 0 and len(vheld) > self._max_alt_bases:
                    hints.append(
                        f"OVER_MAX_ALT_BASES@{venue} held={sorted(vheld)} "
                        f"max={self._max_alt_bases}"
                    )
                elif self._max_alt_bases > 0 and len(vheld) >= self._max_alt_bases:
                    hints.append(
                        f"AT_MAX_ALT_BASES@{venue} held={sorted(vheld)} "
                        "(adds to existing only)"
                    )
        elif self._max_alt_bases > 0 and len(held) > self._max_alt_bases:
            hints.append(
                f"OVER_MAX_ALT_BASES held={sorted(held)} max={self._max_alt_bases}"
            )
        elif self._max_alt_bases > 0 and len(held) >= self._max_alt_bases:
            hints.append(
                f"AT_MAX_ALT_BASES held={sorted(held)} (adds to existing only)"
            )

        if self.skips.get("fees_eat_edge", 0) > 0:
            hints.append(f"FEES_EAT_EDGE n={self.skips.get('fees_eat_edge')}")
        if self.skips.get("momentum_block", 0) > 0:
            hints.append(f"MOMENTUM_BLOCK n={self.skips.get('momentum_block')}")
        if self.skips.get("focus_base_required", 0) > 0:
            hints.append(
                f"FOCUS_BASE_REQUIRED n={self.skips.get('focus_base_required')}"
            )
        if self.skips.get("cross_venue_duplicate_base", 0) > 0:
            hints.append(
                f"CROSS_VENUE_DUPLICATE_BASE n={self.skips.get('cross_venue_duplicate_base')}"
            )
        dupes = self._duplicate_bases_by_venue()
        if dupes:
            hints.append(
                "DUPLICATE_BASES "
                + ", ".join(f"{b}@{','.join(sorted(v))}" for b, v in sorted(dupes.items()))
            )
        if self.skips.get("consolidation_secondary_buy", 0) > 0:
            hints.append(
                f"CONSOLIDATION_SECONDARY_BUY n={self.skips.get('consolidation_secondary_buy')}"
            )
        if self.skips.get("corr_group_cap", 0) > 0:
            hints.append(f"CORR_GROUP_CAP n={self.skips.get('corr_group_cap')}")
        # Lifetime skip totals are noisy after wind-down — only surface when active.
        policy_n = int(self.skips.get("policy_blocked", 0) or 0)
        if policy_n > 0 and (
            self._buys_blocked or bool(self._underwater_blocked_bases)
        ):
            hints.append(f"POLICY_BLOCKED n={policy_n}")
        exec_n = int(self.skips.get("execution_error", 0) or 0)
        if exec_n > 0:
            hints.append(f"EXECUTION_ERROR n={exec_n}")
        if self.skips.get("budget_exhausted", 0) > 0:
            hints.append(f"BUDGET_EXHAUSTED n={self.skips.get('budget_exhausted')}")

        # Per-venue free cash so OKX under-deployment is visible.
        venue_cash: list[str] = []
        for venue in sorted(self._execute_venues):
            try:
                rem = self._venue_budget_remaining(venue)
            except Exception:  # noqa: BLE001
                rem = None
            if rem is not None:
                venue_cash.append(f"{venue}=€{float(rem):.0f}")
        if venue_cash:
            hints.append("VENUE_CASH " + " ".join(venue_cash))

        # Active deploy ring: focus (not underwater) notional vs target.
        if self._active_ring_eur > 0:
            ring_bits: list[str] = []
            for venue in sorted(self._execute_venues):
                active = self._active_book_notional(venue)
                free = self._venue_budget_remaining(venue)
                need = active < self._active_ring_eur and free >= Decimal("50")
                ring_bits.append(
                    f"{venue}=€{float(active):.0f}/€{float(self._active_ring_eur):.0f}"
                    f"{' NEED' if need else ' OK'}"
                )
            hints.append("ACTIVE_RING " + " ".join(ring_bits))

        # A+D: velocity sleeve loss cap + exit engine status.
        if self._velocity_sleeve_eur > 0 or self._sleeve_daily_loss_cap > 0:
            sleeve_bits = [
                f"size=€{float(self._velocity_sleeve_eur):.0f}",
                f"pnl=€{float(self._sleeve_realized_eur):.2f}",
                f"cap=-€{float(self._sleeve_daily_loss_cap):.0f}",
            ]
            if self._sleeve_paused:
                sleeve_bits.append("PAUSED")
            hints.append("VELOCITY_SLEEVE " + " ".join(sleeve_bits))
        if self._exit_engine_enabled:
            q = self._exit_quote_counts
            f = self._exit_fill_counts
            p = self._exit_pending_counts
            hints.append(
                "EXIT_ENGINE on "
                f"reprice={self._exit_resting_max_age_sec:.0f}s "
                f"cd={self._exit_engine_cooldown_sec:.0f}s "
                f"work={'on' if self._exit_soft_armed_work else 'off'} "
                f"q={sum(v for k,v in q.items() if not str(k).startswith('reason:'))} "
                f"fill={sum(v for k,v in f.items() if not str(k).startswith('reason:'))} "
                f"pend={sum(v for k,v in p.items() if not str(k).startswith('reason:'))}"
            )

        if not hints:
            hints.append("SCANNING_NO_PASSING_EDGE")
        return hints

    def _trail_states_public(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for trail_key, st in sorted(self._trail.items()):
            cost = Decimal(str(st.get("cost") or 0))
            mark = Decimal(str(st.get("last_mark") or 0))
            gain = ((mark - cost) / cost) if cost > 0 and mark > 0 else _ZERO
            soft_arm = Decimal(str(st.get("soft_arm") or self._soft_arm_floor))
            hard_arm = Decimal(str(st.get("hard_arm") or self._hard_arm_floor))
            next_arm = soft_arm if not st.get("soft_armed") else hard_arm
            to_arm = next_arm - gain
            venue = str(st.get("venue") or trail_key.split(":", 1)[0])
            base = str(st.get("base") or trail_key.split(":", 1)[-1])
            opened = self._position_opened_mono.get(trail_key)
            opened_at = self._position_opened_at.get(trail_key)
            if opened_at:
                age = max(0.0, time.time() - opened_at)
            elif opened is not None:
                age = time.monotonic() - opened
            else:
                age = None
            qty = self._balance_qty(venue, base)
            if qty <= 0:
                qty = Decimal(str(st.get("session_qty") or 0))
            notional = (qty * mark) if qty > 0 and mark > 0 else _ZERO
            unrealized = (mark - cost) * qty if qty > 0 and cost > 0 and mark > 0 else _ZERO
            winnable = self._bag_winnable_eur(
                venue, base, cost=cost, mark=mark, qty=qty
            )
            be = self._break_even_sell_price(venue, base)
            role = "long_hold" if self._is_long_hold(base) else "micro_recycle"
            out[trail_key] = {
                "venue": venue,
                "base": base,
                "role": role,
                "armed": bool(st.get("soft_armed") or st.get("hard_armed")),
                "soft_armed": bool(st.get("soft_armed")),
                "hard_armed": bool(st.get("hard_armed")),
                "partial_done": bool(st.get("soft_partial_done")),
                "hard_partial_done": bool(st.get("hard_partial_done")),
                "winner_add_count": int(st.get("winner_add_count") or 0),
                "be_harvest_partial_done": bool(
                    st.get("be_harvest_partial_done")
                    or st.get("recovery_be_partial_done")
                ),
                "peak": str(st.get("peak") or ""),
                "cost": str(cost) if cost > 0 else "",
                "mark": str(mark) if mark > 0 else "",
                "qty": str(qty) if qty > 0 else "",
                "notional_eur": str(notional.quantize(Decimal("0.01"))) if notional > 0 else "",
                "unrealized_eur": str(unrealized.quantize(Decimal("0.01"))) if qty > 0 else "",
                "winnable_eur": str(winnable.quantize(Decimal("0.01"))) if qty > 0 else "",
                "at_break_even": bool(be is not None and mark >= be),
                "gain_pct": f"{float(gain * 100):.2f}",
                "pct_to_arm": f"{float(to_arm * 100):.2f}",
                "soft_arm_pct": f"{float(soft_arm * 100):.2f}",
                "hard_arm_pct": f"{float(hard_arm * 100):.2f}",
                "atr_pct": str(st.get("atr") or ""),
                "session_qty": str(st.get("session_qty") or ""),
                "triggered": bool(st.get("triggered")),
                "recovery_armed": bool(st.get("recovery_armed")),
                "below_be": bool(st.get("below_be")),
                "new_session_base": bool(st.get("new_session_base")),
                "age_sec": round(age, 1) if age is not None else None,
            }
        for venue in sorted(self._execute_venues):
            for bal in self._venue_raw_balances.get(venue) or []:
                asset = str(getattr(bal, "asset", "") or "").upper()
                if not asset or asset == self._quote or asset in self._exclude_bases:
                    continue
                if not self._is_long_hold(asset):
                    continue
                trail_key = self._lots_key(venue, asset)
                if trail_key in out:
                    continue
                qty = self._balance_qty(venue, asset)
                if qty <= 0:
                    continue
                symbol = f"{asset}{self._quote}"
                mark = Decimal(
                    str(self._portfolio.state.mark_prices.get(symbol) or 0)
                )
                cost = self._unit_cost(venue, asset) or mark
                if mark <= 0:
                    continue
                gain = ((mark - cost) / cost) if cost > 0 else _ZERO
                notional = qty * mark
                unrealized = (mark - cost) * qty if cost > 0 else _ZERO
                winnable = self._bag_winnable_eur(
                    venue, asset, cost=cost, mark=mark, qty=qty
                )
                be = self._break_even_sell_price(venue, asset)
                opened_at = self._position_opened_at.get(trail_key)
                age = max(0.0, time.time() - opened_at) if opened_at else None
                out[trail_key] = {
                    "venue": venue,
                    "base": asset,
                    "role": "long_hold",
                    "armed": False,
                    "soft_armed": False,
                    "hard_armed": False,
                    "partial_done": False,
                    "hard_partial_done": False,
                    "peak": "",
                    "cost": str(cost) if cost > 0 else "",
                    "mark": str(mark),
                    "qty": str(qty),
                    "notional_eur": str(notional.quantize(Decimal("0.01"))),
                    "unrealized_eur": str(unrealized.quantize(Decimal("0.01"))),
                    "winnable_eur": str(winnable.quantize(Decimal("0.01"))),
                    "at_break_even": bool(be is not None and mark >= be),
                    "gain_pct": f"{float(gain * 100):.2f}",
                    "pct_to_arm": "—",
                    "soft_arm_pct": "—",
                    "hard_arm_pct": "—",
                    "atr_pct": "",
                    "session_qty": "0",
                    "triggered": False,
                    "age_sec": round(age, 1) if age is not None else None,
                }
        return out

    def _session_unit_cost(self, venue: str, base: str) -> Decimal | None:
        lots = self._session_lots.get(self._lots_key(venue, base)) or []
        total_qty = _ZERO
        total_cost = _ZERO
        for qty, unit in lots:
            if qty <= 0 or unit <= 0:
                continue
            total_qty += qty
            total_cost += qty * unit
        if total_qty <= 0:
            return None
        return total_cost / total_qty

    def _session_qty(self, venue: str, base: str) -> Decimal:
        return sum(
            (
                qty
                for qty, _unit in (
                    self._session_lots.get(self._lots_key(venue, base)) or []
                )
                if qty > 0
            ),
            _ZERO,
        )

    def _series_for(self, symbol: str) -> Any:
        series = self._mark_series.get(symbol)
        if series is None:
            series = self._MarkSeries(maxlen=max(self._atr_samples, self._momentum_samples))
            self._mark_series[symbol] = series
        return series

    def mark_history(self, symbol: str) -> list[Decimal]:
        """Rolling mid marks for entry quality (oldest → newest)."""
        return self._series_for(symbol).marks()

    def _refresh_economic_capital_metrics(self) -> None:
        """Update capital deployed/locked for profit-efficiency dashboard."""
        deployed = _ZERO
        locked = _ZERO
        for v in sorted(self._execute_venues):
            deployed += self._active_book_notional(v)
            locked += self._underwater_book_notional(v)
        self._economic_diagnostics._capital_deployed_eur = deployed  # noqa: SLF001
        self._economic_diagnostics._capital_locked_eur = locked  # noqa: SLF001

    def economic_diagnostics_snapshot(self) -> dict[str, Any]:
        self._refresh_economic_capital_metrics()
        eq_extra = self._entry_quality_diagnostics.snapshot()
        opp = getattr(self, "_opportunity_diagnostics", None)
        if opp is not None:
            return opp.snapshot(entry_quality_extra={
                **eq_extra,
                **self._economic_diagnostics.snapshot(),
            })
        return self._economic_diagnostics.snapshot(entry_quality_extra=eq_extra)

    def entry_quality_diagnostics_snapshot(self) -> dict[str, Any]:
        return self._entry_quality_diagnostics.snapshot()

    def _profitability_stub_for_entry_quality(
        self,
        *,
        qty: Decimal,
        px: Decimal,
        meta: dict[str, Any],
    ) -> ProfitabilityResult:
        notional = qty * px
        net_eur = meta.get("net_profit_eur")
        net_ret = meta.get("net_return")
        try:
            net_profit = Decimal(str(net_eur)) if net_eur is not None else _ZERO
        except Exception:  # noqa: BLE001
            net_profit = _ZERO
        try:
            net_return = Decimal(str(net_ret)) if net_ret is not None else _ZERO
        except Exception:  # noqa: BLE001
            net_return = _ZERO
        fee_est = notional * Decimal("0.002") if notional > 0 else _ZERO
        return ProfitabilityResult(
            opportunity_id=uuid4(),
            gross_profit_usd=net_profit + fee_est,
            fees_usd=fee_est,
            slippage_usd=_ZERO,
            funding_usd=_ZERO,
            execution_buffer_usd=notional * Decimal("0.001"),
            net_profit_usd=net_profit,
            net_return=net_return,
            is_profitable=net_profit > 0,
            trade_allowed=True,
        )

    def _assess_entry_quality_buy(
        self,
        *,
        symbol: str,
        qty: Decimal,
        px: Decimal,
        meta: dict[str, Any],
    ) -> EntryQualityAssessment:
        profitability = self._profitability_stub_for_entry_quality(
            qty=qty, px=px, meta=meta
        )
        opportunity = TradeOpportunity(
            strategy_name=str(meta.get("strategy") or "maker_inventory"),
            symbol=symbol,
            side=OpportunitySide.BUY,
            quantity=qty,
            entry_price=px,
            metadata=meta,
        )
        assessment = evaluate_entry_quality(
            opportunity=opportunity,
            profitability=profitability,
            marks=self.mark_history(symbol),
            config=self._entry_quality_config,
        )
        if self._opportunity_engine_enabled and self._opportunity_diagnostics is not None:
            opp_assessment = evaluate_opportunity_engine(
                opportunity=opportunity,
                profitability=profitability,
                marks=self.mark_history(symbol),
                entry_config=self._entry_quality_config,
                capital_config=self._capital_efficiency_config,
                venue_config=self._venue_economics_config,
                engine_config=self._opportunity_engine_config,
            )
            self._opportunity_diagnostics.record(opp_assessment)
        return assessment

    def _note_position_opened(self, venue: str, base: str) -> None:
        key = self._lots_key(venue, base)
        if key not in self._position_opened_mono:
            self._position_opened_mono[key] = time.monotonic()
            self._position_opened_at[key] = time.time()

    def _resting_count_for(self, venue: str) -> int:
        venue_l = venue.strip().lower()
        return sum(
            1
            for row in self._resting
            if str(row.get("venue") or "").strip().lower() == venue_l
            and str(row.get("side") or "buy").lower().startswith("b")
        )

    def _resting_buys_for(self, venue: str, symbol: str) -> int:
        venue_l = venue.strip().lower()
        sym = symbol.strip().upper()
        return sum(
            1
            for row in self._resting
            if str(row.get("venue") or "").strip().lower() == venue_l
            and str(row.get("symbol") or "").strip().upper() == sym
            and str(row.get("side") or "buy").lower().startswith("b")
        )

    def _focus_inventory_notional(
        self, venue: str, *, above_be_only: bool
    ) -> Decimal:
        """Sum focus-base inventory notional (optionally only at/above break-even)."""
        venue_l = venue.strip().lower()
        stuck = {
            str(b).upper()
            for b in self._underwater_blocked_bases.get(venue_l, set())
        }
        total = _ZERO
        min_n = Decimal(
            str(getattr(self._settings, "paper_maker_min_notional_eur", 10) or 10)
        ) * Decimal("0.5")
        for bal in self._venue_raw_balances.get(venue_l) or []:
            base = str(getattr(bal, "asset", "") or "").upper()
            if not base or base == self._quote or base in self._exclude_bases:
                continue
            if self._is_long_hold(base):
                continue
            if self._focus_bases and base not in self._focus_bases:
                continue
            if above_be_only and base in stuck:
                continue
            qty = Decimal(str(getattr(bal, "free", 0) or 0)) + Decimal(
                str(getattr(bal, "locked", 0) or 0)
            )
            if qty <= 0:
                continue
            symbol = f"{base}{self._quote}"
            mark = self._portfolio.state.mark_prices.get(symbol) or _ZERO
            if mark <= 0:
                continue
            if above_be_only:
                be = self._break_even_sell_price(venue, base)
                if be is not None and mark < be:
                    continue
            notional = qty * mark
            if notional >= min_n:
                total += notional
        return total

    def _active_book_notional(self, venue: str) -> Decimal:
        """Focus-base inventory not underwater — counts toward the deploy ring."""
        return self._focus_inventory_notional(venue, above_be_only=True)

    def _underwater_book_notional(self, venue: str) -> Decimal:
        """Focus-base inventory below break-even (stuck capital)."""
        total = self._focus_inventory_notional(venue, above_be_only=False)
        active = self._active_book_notional(venue)
        return max(_ZERO, total - active)

    def _held_alt_bases(
        self,
        venue: str | None = None,
        *,
        min_notional_eur: Decimal | float | None = None,
    ) -> set[str]:
        """Distinct non-quote assets with meaningful inventory.

        When *venue* is set, only that exchange's balances count toward the
        concentration cap — Bitvavo holdings must not block OKX new-base buys.

        ``min_notional_eur`` overrides the default maker min notional (used by
        cross-venue uniqueness with a lower floor so sub-clip bags still count).
        """
        held: set[str] = set()
        if min_notional_eur is None:
            min_notional = Decimal(
                str(getattr(self._settings, "paper_maker_min_notional_eur", 10) or 10)
            )
        else:
            min_notional = Decimal(str(min_notional_eur))
        venue_l = venue.strip().lower() if venue else None

        def _maybe_add(base: str, qty: Decimal) -> None:
            if not base or base == self._quote or base in self._exclude_bases:
                return
            if self._is_long_hold(base):
                return
            if qty <= 0:
                return
            symbol = f"{base}{self._quote}"
            mark = self._portfolio.state.mark_prices.get(symbol) or _ZERO
            if mark > 0 and qty * mark < min_notional:
                return  # dust — don't burn a concentration slot
            if mark <= 0 and qty < Decimal("0.001"):
                return
            held.add(base)

        if venue_l:
            for bal in self._venue_raw_balances.get(venue_l) or []:
                asset = str(getattr(bal, "asset", "") or "").upper()
                qty = Decimal(str(getattr(bal, "free", 0) or 0)) + Decimal(
                    str(getattr(bal, "locked", 0) or 0)
                )
                _maybe_add(asset, qty)
            return held

        venues = sorted(self._execute_venues) if self._execute_venues else []
        if venues:
            for v in venues:
                held |= self._held_alt_bases(v, min_notional_eur=min_notional)
            return held

        # Single-venue / pre-sync fallback: aggregate paper pocket balances.
        for symbol, pos in self._portfolio.state.positions.items():
            if pos.quantity <= 0:
                continue
            base = infer_base_asset(symbol)
            if not base or base == self._quote or base in self._exclude_bases:
                continue
            if self._is_long_hold(base):
                continue
            mark = self._portfolio.state.mark_prices.get(symbol) or pos.average_entry_price
            if mark and pos.quantity * mark >= min_notional:
                held.add(base)
            elif pos.quantity > 0 and (mark is None or mark <= 0):
                held.add(base)
        for asset, bal in self._portfolio.state.balances.items():
            _maybe_add(str(asset or "").upper(), Decimal(str(bal.total or 0)))
        return held

    def _resting_buy_bases(self, venue: str | None = None) -> set[str]:
        """Bases with an open resting buy (fills may still be pending)."""
        venue_l = venue.strip().lower() if venue else None
        out: set[str] = set()
        for row in self._resting:
            if not str(row.get("side") or "buy").lower().startswith("b"):
                continue
            row_venue = str(row.get("venue") or "").strip().lower()
            if venue_l is not None and row_venue != venue_l:
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            base = infer_base_asset(symbol, self._quote)
            if not base or base == self._quote or base in self._exclude_bases:
                continue
            if self._is_long_hold(base):
                continue
            out.add(base)
        return out

    def _trail_claimed_bases(
        self,
        venue: str | None = None,
        *,
        min_notional_eur: Decimal | float = _MIN_LIVE_NOTIONAL,
    ) -> set[str]:
        """Bases with trail inventory ≥ floor (covers brief post-fill sync lag)."""
        floor = Decimal(str(min_notional_eur or 0))
        venue_l = venue.strip().lower() if venue else None
        out: set[str] = set()
        for trail_key, st in self._trail.items():
            if not isinstance(st, dict):
                continue
            bag_venue = str(st.get("venue") or trail_key.split(":", 1)[0]).strip().lower()
            if venue_l is not None and bag_venue != venue_l:
                continue
            base = str(st.get("base") or trail_key.split(":", 1)[-1]).upper()
            if not base or base == self._quote or base in self._exclude_bases:
                continue
            if self._is_long_hold(base):
                continue
            try:
                mark = Decimal(str(st.get("last_mark") or 0))
            except Exception:  # noqa: BLE001
                mark = _ZERO
            qty = self._balance_qty(bag_venue, base)
            if qty <= 0:
                try:
                    qty = Decimal(str(st.get("session_qty") or 0))
                except Exception:  # noqa: BLE001
                    qty = _ZERO
            if mark > 0 and qty * mark >= floor:
                out.add(base)
            elif qty > 0 and mark <= 0:
                out.add(base)
        return out

    def _bases_claimed_for_cross_venue(self, venue: str | None = None) -> set[str]:
        """Inventory + resting buys + trail bags — uniqueness across venues.

        Uses ``_MIN_LIVE_NOTIONAL`` (not the maker clip floor) so a €60 TAO bag
        still blocks the other venue after first_clip was raised to €100.
        """
        floor = _MIN_LIVE_NOTIONAL
        return (
            self._held_alt_bases(venue, min_notional_eur=floor)
            | self._resting_buy_bases(venue)
            | self._trail_claimed_bases(venue, min_notional_eur=floor)
        )

    def _seed_cost_lots_from_balances(self, venue: str, bals: list[Any]) -> None:
        """Seed provisional FIFO lots at mark (untrusted — not safe for sells)."""
        key = venue.strip().lower()
        if key in self._lots_seeded_venues:
            return
        for bal in bals:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if not asset or asset == self._quote:
                continue
            qty = Decimal(str(getattr(bal, "free", 0) or 0)) + Decimal(
                str(getattr(bal, "locked", 0) or 0)
            )
            if qty <= 0:
                continue
            symbol = f"{asset}{self._quote}"
            mark = self._portfolio.state.mark_prices.get(symbol)
            if mark is None or mark <= 0:
                continue
            lot_key = self._lots_key(venue, asset)
            # Mark seed is provisional only — never trusted for profitable-sell gate.
            self._cost_lots.setdefault(lot_key, []).append([qty, Decimal(str(mark))])
            self._note_position_opened(venue, asset)
        self._lots_seeded_venues.add(key)

    def _has_trusted_cost(self, venue: str, base: str) -> bool:
        return self._lots_key(venue, base) in self._trusted_cost_keys

    def _mark_cost_trusted(self, venue: str, base: str) -> None:
        self._trusted_cost_keys.add(self._lots_key(venue, base))

    async def _hydrate_cost_basis_from_trades(self, venue: str) -> dict[str, Any]:
        """Replace mark-seeded lots with FIFO cost rebuilt from exchange fills."""
        venue = venue.strip().lower()
        client = self._trading_client(venue)
        if client is None:
            return {"ok": False, "reason": "no_client"}
        get_ex = getattr(client, "_get_exchange", None)
        if not callable(get_ex):
            return {"ok": False, "reason": "no_exchange"}
        try:
            exchange = await get_ex()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "exchange_unavailable", "error": str(exc)[:160]}

        # Ensure lot keys exist for every held balance (even if mark-seed skipped).
        bals = self._venue_raw_balances.get(venue) or []
        for bal in bals:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if not asset or asset == self._quote:
                continue
            qty = Decimal(str(getattr(bal, "free", 0) or 0)) + Decimal(
                str(getattr(bal, "locked", 0) or 0)
            )
            if qty <= 0:
                continue
            lot_key = self._lots_key(venue, asset)
            if lot_key not in self._cost_lots:
                symbol_i = f"{asset}{self._quote}"
                mark = self._portfolio.state.mark_prices.get(symbol_i)
                if mark is None or mark <= 0:
                    try:
                        ticker = await client.fetch_ticker(symbol_i)
                        mark = Decimal(
                            str(
                                getattr(ticker, "last", None)
                                or getattr(ticker, "bid", None)
                                or getattr(ticker, "ask", None)
                                or 0
                            )
                        )
                    except Exception:  # noqa: BLE001
                        mark = _ZERO
                if mark and mark > 0:
                    self._cost_lots[lot_key] = [[qty, mark]]
                    self._note_position_opened(venue, asset)
                else:
                    # Placeholder so trade rebuild can still run.
                    self._cost_lots[lot_key] = [[qty, _ZERO]]

        hydrated: list[str] = []
        for lot_key, lots in list(self._cost_lots.items()):
            if not lot_key.startswith(f"{venue}:"):
                continue
            if lot_key in self._trusted_cost_keys:
                continue
            base = lot_key.split(":", 1)[1]
            held = sum((q for q, _u in lots if q > 0), _ZERO)
            # Prefer live balance qty when available.
            for bal in bals:
                if str(getattr(bal, "asset", "") or "").upper() == base:
                    bal_qty = Decimal(str(getattr(bal, "free", 0) or 0)) + Decimal(
                        str(getattr(bal, "locked", 0) or 0)
                    )
                    if bal_qty > 0:
                        held = bal_qty
                    break
            if held <= 0:
                continue
            symbol = f"{base}/{self._quote}"
            try:
                raw = await exchange.fetch_my_trades(symbol, limit=100)
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "cost hydrate trades failed venue=%s base=%s err=%s",
                    venue,
                    base,
                    type(exc).__name__,
                )
                continue
            rebuilt: list[list[Decimal]] = []
            for trade in sorted(raw or [], key=lambda t: int(t.get("timestamp") or 0)):
                side = str(trade.get("side") or "").lower()
                amt = Decimal(str(trade.get("amount") or 0))
                px = Decimal(str(trade.get("price") or 0))
                if amt <= 0 or px <= 0:
                    continue
                fee_info = trade.get("fee") or {}
                fee_amt = Decimal(str(fee_info.get("cost") or 0))
                fee_cur = str(fee_info.get("currency") or "").upper()
                if side == "buy":
                    lot_qty, unit = _buy_lot_qty_and_unit(
                        amount=amt,
                        price=px,
                        fee_amt=fee_amt,
                        fee_cur=fee_cur,
                        base=base,
                        quote=self._quote,
                    )
                    if lot_qty <= 0 or unit <= 0:
                        continue
                    rebuilt.append([lot_qty, unit])
                elif side == "sell":
                    remaining = amt
                    while remaining > 0 and rebuilt:
                        lq, lc = rebuilt[0]
                        take = min(remaining, lq)
                        lq -= take
                        remaining -= take
                        if lq <= 0:
                            rebuilt.pop(0)
                        else:
                            rebuilt[0][0] = lq
            rebuilt_qty = sum((q for q, _u in rebuilt if q > 0), _ZERO)
            if rebuilt_qty <= 0:
                # Fallback: OKX conversion / bill ledger (manual buys may not appear in trades).
                try:
                    ledger = await exchange.fetch_ledger(base, limit=50)
                except Exception:  # noqa: BLE001
                    ledger = []
                try:
                    eur_ledger = await exchange.fetch_ledger(self._quote, limit=50)
                except Exception:  # noqa: BLE001
                    eur_ledger = []
                # Pair same-timestamp SOL credit with EUR debit.
                eur_by_ts: dict[int, Decimal] = {}
                for entry in eur_ledger or []:
                    ts = int(entry.get("timestamp") or 0)
                    amt = Decimal(str(entry.get("amount") or 0))
                    direction = str(entry.get("direction") or "").lower()
                    if ts and amt < 0 and direction in {"", "out", "debit"}:
                        eur_by_ts[ts] = eur_by_ts.get(ts, _ZERO) + (-amt)
                    elif ts and amt > 0 and direction == "out":
                        eur_by_ts[ts] = eur_by_ts.get(ts, _ZERO) + amt
                for entry in sorted(ledger or [], key=lambda e: int(e.get("timestamp") or 0)):
                    ts = int(entry.get("timestamp") or 0)
                    amt = Decimal(str(entry.get("amount") or 0))
                    direction = str(entry.get("direction") or "").lower()
                    if amt <= 0:
                        continue
                    if direction and direction not in {"in", "credit"}:
                        continue
                    spent = eur_by_ts.get(ts)
                    if spent is None or spent <= 0:
                        continue
                    rebuilt.append([amt, spent / amt])
                rebuilt_qty = sum((q for q, _u in rebuilt if q > 0), _ZERO)
                if rebuilt_qty <= 0:
                    continue
            # Incomplete trade history must NOT become trusted cost (never-loss).
            if rebuilt_qty < held * Decimal("0.98"):
                logger.info(
                    "cost hydrate incomplete venue=%s base=%s rebuilt=%s held=%s",
                    venue,
                    base,
                    rebuilt_qty,
                    held,
                )
                continue
            # Keep lots covering current held qty (drop excess oldest if needed).
            if rebuilt_qty > held * Decimal("1.001"):
                need = held
                trimmed: list[list[Decimal]] = []
                for q, u in reversed(rebuilt):
                    if need <= 0:
                        break
                    take = min(q, need)
                    trimmed.append([take, u])
                    need -= take
                rebuilt = list(reversed(trimmed))
            self._cost_lots[lot_key] = rebuilt
            self._trusted_cost_keys.add(lot_key)
            hydrated.append(base)
            self._sync_paper_entry_from_lots(venue, base)
            logger.info(
                "cost basis hydrated venue=%s base=%s lots=%s unit=%s",
                venue,
                base,
                len(rebuilt),
                self._unit_cost(venue, base),
            )
        return {"ok": True, "venue": venue, "hydrated": hydrated}

    def _trade_fee_quote(
        self,
        trade: dict[str, Any],
        *,
        base: str,
        amt: Decimal,
        px: Decimal,
    ) -> tuple[Decimal, str]:
        fee_info = trade.get("fee") or {}
        fee_amt = Decimal(str(fee_info.get("cost") or 0))
        fee_cur = str(fee_info.get("currency") or self._quote).upper()
        if fee_cur == base and fee_amt > 0:
            fee_quote = fee_amt * px
        elif fee_cur == self._quote:
            fee_quote = fee_amt
        else:
            fee_quote = fee_amt if fee_amt > 0 else (amt * px * Decimal("0.001"))
        return fee_quote, fee_cur

    def _trade_order_id(self, trade: dict[str, Any]) -> str:
        info = trade.get("info") or {}
        return str(
            trade.get("order")
            or trade.get("orderId")
            or info.get("orderId")
            or info.get("order_id")
            or ""
        )

    def _note_live_fill_event(
        self,
        *,
        venue: str,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        source: str = "mirror",
        exchange_order_id: str | None = None,
        trade_id: str | None = None,
    ) -> None:
        """Increment session fill counters and keep operator-visible fill feed."""
        self.session_live_fill_count += 1
        self.session_live_transaction_count += 1
        self.live_fill_count = self.session_live_fill_count
        self.live_transaction_count = self.session_live_transaction_count
        notional = (qty * price).quantize(Decimal("0.01"))
        self.recent_live_fills.append(
            {
                "ts": datetime.now(UTC).isoformat(),
                "venue": venue,
                "symbol": symbol,
                "side": side,
                "qty": str(qty),
                "price": str(price),
                "notional_eur": str(notional),
                "source": source,
                "exchange_order_id": exchange_order_id,
                "trade_id": trade_id,
            }
        )
        if len(self.recent_live_fills) > 24:
            self.recent_live_fills = self.recent_live_fills[-24:]

    def _maybe_note_fill_display(
        self,
        *,
        venue: str,
        base: str,
        trade: dict[str, Any],
        source: str,
    ) -> None:
        """Show already-mirrored trades on the dashboard without double-counting PnL."""
        tid = str(trade.get("id") or "")
        if not tid or any(str(f.get("trade_id") or "") == tid for f in self.recent_live_fills):
            return
        side = str(trade.get("side") or "").lower()
        amt = Decimal(str(trade.get("amount") or 0))
        px = Decimal(str(trade.get("price") or 0))
        if amt <= 0 or px <= 0 or side not in {"buy", "sell"}:
            return
        ts_ms = int(trade.get("timestamp") or 0)
        started_ms = float(self._session_started_ms or 0)
        since_ms = max(0.0, started_ms - 6 * 3600 * 1000) if started_ms else 0.0
        if since_ms and ts_ms and ts_ms < since_ms:
            return
        ts_iso = (
            datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).isoformat()
            if ts_ms
            else datetime.now(UTC).isoformat()
        )
        symbol = f"{base}{self._quote}"
        notional = (amt * px).quantize(Decimal("0.01"))
        self.recent_live_fills.append(
            {
                "ts": ts_iso,
                "venue": venue,
                "symbol": symbol,
                "side": side,
                "qty": str(amt),
                "price": str(px),
                "notional_eur": str(notional),
                "source": source,
                "exchange_order_id": self._trade_order_id(trade) or None,
                "trade_id": tid,
            }
        )
        if len(self.recent_live_fills) > 24:
            self.recent_live_fills = self.recent_live_fills[-24:]

    def _mirror_exchange_trade(
        self,
        *,
        venue: str,
        base: str,
        trade: dict[str, Any],
        source: str,
        since_ms: float = 0,
        order_id_filter: str | None = None,
        placed_after_ms: float | None = None,
    ) -> bool:
        """Mirror one exchange trade into PnL + session fill counters (deduped)."""
        from bot.core.enums import OrderSide as _OrderSide

        tid = str(trade.get("id") or "")
        if not tid:
            return False
        mirror_key = f"{venue}:{tid}"
        if mirror_key in self._mirrored_trade_ids:
            self._maybe_note_fill_display(venue=venue, base=base, trade=trade, source=source)
            return False
        ts = int(trade.get("timestamp") or 0)
        if since_ms and ts and ts < since_ms:
            return False
        trade_oid = self._trade_order_id(trade)
        if order_id_filter:
            if trade_oid and trade_oid != order_id_filter:
                return False
            if not trade_oid and placed_after_ms and ts and ts < placed_after_ms - 60_000:
                return False
        side = str(trade.get("side") or "").lower()
        amt = Decimal(str(trade.get("amount") or 0))
        px = Decimal(str(trade.get("price") or 0))
        if amt <= 0 or px <= 0 or side not in {"buy", "sell"}:
            return False
        started_ms = float(self._session_started_ms or 0)
        if side == "sell" and started_ms and ts and ts < started_ms:
            self._mirrored_trade_ids.add(mirror_key)
            return False
        fee_quote, fee_cur = self._trade_fee_quote(trade, base=base, amt=amt, px=px)
        symbol = f"{base}{self._quote}"
        if side == "buy" and self._has_trusted_cost(venue, base):
            self._mirrored_trade_ids.add(mirror_key)
            self.backfill_mirrored_count += 1
            return False
        try:
            self._record_realized_fill(
                side=_OrderSide.BUY if side == "buy" else _OrderSide.SELL,
                symbol=symbol,
                qty=amt,
                price=px,
                fee=fee_quote,
                venue=venue,
                fee_currency=fee_cur,
            )
        except TypeError:
            self._record_realized_fill(
                side=_OrderSide.BUY if side == "buy" else _OrderSide.SELL,
                symbol=symbol,
                qty=amt,
                price=px,
                fee=fee_quote,
                venue=venue,
            )
        self._note_live_fill_event(
            venue=venue,
            symbol=symbol,
            side=side,
            qty=amt,
            price=px,
            source=source,
            exchange_order_id=trade_oid or order_id_filter,
            trade_id=tid,
        )
        self._mirrored_trade_ids.add(mirror_key)
        self.backfill_mirrored_count += 1
        logger.info(
            "FILL_%s venue=%s base=%s side=%s qty=%s px=%s trade=%s",
            source.upper(),
            venue,
            base,
            side,
            amt,
            px,
            tid,
        )
        return True

    async def _mirror_trades_for_resting_order(
        self, venue: str, row: dict[str, Any]
    ) -> int:
        """Catch fills on orders the exchange marked cancelled/closed before poll."""
        venue = venue.strip().lower()
        symbol = str(row.get("symbol") or "")
        oid = str(row.get("exchange_order_id") or "")
        if not symbol:
            return 0
        base = infer_base_asset(symbol)
        client = self._trading_client(venue)
        if client is None:
            return 0
        get_ex = getattr(client, "_get_exchange", None)
        if not callable(get_ex):
            return 0
        try:
            exchange = await get_ex()
            ccxt_symbol = f"{base}/{self._quote}"
            raw = await exchange.fetch_my_trades(ccxt_symbol, limit=40)
        except Exception:  # noqa: BLE001
            return 0
        placed_at = float(row.get("placed_at") or 0)
        placed_ms = placed_at * 1000.0 if placed_at else None
        mirrored = 0
        for trade in sorted(raw or [], key=lambda t: int(t.get("timestamp") or 0)):
            if self._mirror_exchange_trade(
                venue=venue,
                base=base,
                trade=trade,
                source="resting_backfill",
                order_id_filter=oid or None,
                placed_after_ms=placed_ms,
            ):
                mirrored += 1
        return mirrored

    async def _backfill_fills_from_trades(self, venue: str) -> dict[str, Any]:
        """Mirror recent exchange fills into pocket PnL / fill counters.

        Resting-order polls can miss fills (fetch_order races). Trade history is
        the source of truth for session fills on both Bitvavo and OKX.
        """
        venue = venue.strip().lower()
        client = self._trading_client(venue)
        if client is None:
            return {"ok": False, "reason": "no_client"}
        get_ex = getattr(client, "_get_exchange", None)
        if not callable(get_ex):
            return {"ok": False, "reason": "no_exchange"}
        try:
            exchange = await get_ex()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "exchange_unavailable", "error": str(exc)[:160]}

        started_ms = float(self._session_started_ms or 0)
        since_ms = max(0.0, started_ms - 6 * 3600 * 1000) if started_ms else 0.0
        mirrored = 0
        bases: set[str] = set()
        for bal in self._venue_raw_balances.get(venue) or []:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if asset and asset != self._quote:
                bases.add(asset)
        for lot_key in list(self._cost_lots):
            if lot_key.startswith(f"{venue}:"):
                bases.add(lot_key.split(":", 1)[1])

        for base in sorted(bases):
            symbol = f"{base}/{self._quote}"
            try:
                raw = await exchange.fetch_my_trades(symbol, limit=50)
            except Exception:  # noqa: BLE001
                continue
            for trade in sorted(raw or [], key=lambda t: int(t.get("timestamp") or 0)):
                if self._mirror_exchange_trade(
                    venue=venue,
                    base=base,
                    trade=trade,
                    source="backfill",
                    since_ms=since_ms,
                ):
                    mirrored += 1
        self.recent_live_fills.sort(key=lambda f: str(f.get("ts") or ""))
        if len(self.recent_live_fills) > 24:
            self.recent_live_fills = self.recent_live_fills[-24:]
        return {"ok": True, "venue": venue, "mirrored": mirrored}


    def _record_realized_fill(
        self,
        *,
        side: OrderSide,
        symbol: str,
        qty: Decimal,
        price: Decimal,
        fee: Decimal,
        venue: str = "",
        fee_currency: str | None = None,
        fill_meta: dict[str, Any] | None = None,
    ) -> None:
        """Update FIFO lots / realized PnL for a live mirrored fill."""
        if qty <= 0 or price <= 0:
            return
        base = infer_base_asset(symbol)
        lot_key = self._lots_key(venue or "bitvavo", base)
        lots = self._cost_lots.setdefault(lot_key, [])
        if side == OrderSide.BUY:
            was_new_base = len(lots) == 0
            lot_qty, unit = _buy_lot_qty_and_unit(
                amount=qty,
                price=price,
                fee_amt=fee,
                fee_cur=str(fee_currency or self._quote),
                base=base,
                quote=self._quote,
            )
            lots.append([lot_qty, unit])
            self._session_lots.setdefault(lot_key, []).append([lot_qty, unit])
            trail_st = self._trail.setdefault(lot_key, {})
            trail_st["sleeve"] = True  # A: session buys belong to velocity sleeve
            if self._mfe_analytics_enabled:
                trail_st["entry_price"] = str(price)
                trail_st["mfe_price"] = str(price)
                trail_st["mae_price"] = str(price)
                trail_st["entry_mono"] = time.monotonic()
            meta = fill_meta or {}
            for key in (
                "extension_pct",
                "headroom_pct",
                "trend_continuity",
                "entry_quality_score",
            ):
                val = meta.get(key)
                if val is not None:
                    trail_st[f"entry_{key}"] = str(val)
            if was_new_base:
                # Tag for early-cut / cut-loss — free capital if entry goes bad.
                trail_st["new_session_base"] = True
            self._note_position_opened(venue or "bitvavo", base)
            self._mark_cost_trusted(venue or "bitvavo", base)
            self._note_session_buy_for_quality(venue or "bitvavo", base)
            self._refresh_buy_quality_circuit_breaker()
            return
        remaining = qty
        # Quote-denominated fee reduces proceeds; base fee is already outside qty.
        fee_cur = str(fee_currency or self._quote).upper()
        if fee_cur == base.upper() and fee > 0:
            proceeds = qty * price
        else:
            proceeds = qty * price - fee
        cost = _ZERO
        while remaining > 0 and lots:
            lot_qty, lot_cost = lots[0]
            take = min(remaining, lot_qty)
            cost += take * lot_cost
            lot_qty -= take
            remaining -= take
            if lot_qty <= 0:
                lots.pop(0)
            else:
                lots[0][0] = lot_qty
        # Mirror consume on session lots (trail inventory).
        sess = self._session_lots.setdefault(lot_key, [])
        sess_before = sum((Decimal(str(row[0])) for row in sess), _ZERO)
        left = qty
        while left > 0 and sess:
            sq, _sc = sess[0]
            take = min(left, sq)
            sq -= take
            left -= take
            if sq <= 0:
                sess.pop(0)
            else:
                sess[0][0] = sq
        sess_after = sum((Decimal(str(row[0])) for row in sess), _ZERO)
        sess_consumed = sess_before - sess_after
        if remaining > 0:
            cost += remaining * price
        trade_pnl = proceeds - cost
        self.realized_trade_pnl_eur += trade_pnl
        if self._mfe_analytics_enabled:
            trail_st = self._trail.get(lot_key) or {}
            entry_px = Decimal(str(trail_st.get("entry_price") or cost or price))
            mfe_px = Decimal(str(trail_st.get("mfe_price") or price))
            mae_px = Decimal(str(trail_st.get("mae_price") or price))
            entry_mono = trail_st.get("entry_mono")
            hold_sec: Decimal | None = None
            if entry_mono is not None:
                hold_sec = Decimal(str(max(0.0, time.monotonic() - float(entry_mono))))
            notional = qty * price if qty * price > 0 else proceeds
            mfe_rec = compute_mfe_record(
                entry_price=entry_px,
                exit_price=price,
                mfe_price=mfe_px,
                mae_price=mae_px,
                cost_basis=cost if cost > 0 else entry_px,
                realized_net_eur=trade_pnl,
                notional=notional,
                holding_seconds=hold_sec,
            )
            self._economic_diagnostics.record_mfe(mfe_rec)
            self._economic_diagnostics._sum_realized_net_eur += trade_pnl  # noqa: SLF001
            if hold_sec is not None and hold_sec > 0 and trade_pnl > 0:
                self._economic_diagnostics.record_net_per_hour(
                    trade_pnl / (hold_sec / Decimal("3600"))
                )
        # A: attribute PnL to velocity sleeve when session inventory was sold.
        if sess_consumed > 0 or bool(
            (self._trail.get(lot_key) or {}).get("sleeve")
            or (self._trail.get(lot_key) or {}).get("new_session_base")
        ):
            self._sleeve_realized_eur += trade_pnl
            self._check_sleeve_loss_cap()
        self._check_daily_kill()
        if not lots:
            self._position_opened_mono.pop(lot_key, None)
            self._trail.pop(lot_key, None)
        if not sess:
            self._trail.pop(lot_key, None)

    def _bump_skip(self, key: str) -> None:
        self.skips[key] = self.skips.get(key, 0) + 1

    def _resolve_venue(self, order_request: OrderRequest) -> str:
        """Pick the live venue for an order — never hardcode Bitvavo for EUR.

        Prefer explicit venue metadata, then buy/sell exchange from the
        opportunity, then the cash-richest execute venue. Missing venue must
        not silently route every *EUR pair to Bitvavo and starve OKX.
        """
        meta = order_request.metadata or {}
        venue = str(meta.get("venue") or meta.get("exchange") or "").strip().lower()
        if venue:
            return venue
        side = order_request.side
        side_l = str(side.value if hasattr(side, "value") else side).lower()
        if side_l.startswith("s"):
            venue = str(
                meta.get("sell_exchange") or meta.get("buy_exchange") or ""
            ).strip().lower()
        else:
            venue = str(
                meta.get("buy_exchange") or meta.get("sell_exchange") or ""
            ).strip().lower()
        if venue:
            return venue
        candidates = sorted(self._execute_venues)
        if not candidates:
            return ""
        best = ""
        best_score = Decimal("-1")
        for cand in candidates:
            # Prefer real free EUR on the venue (cache) so a full €2k+€2k
            # pocket does not collapse to alphabetical Bitvavo.
            live = self._live_free_sync(cand, self._quote)
            pocket = self._venue_budget_remaining(cand)
            score = live if live > 0 else pocket
            if score > best_score:
                best_score = score
                best = cand
        return best

    def _trading_client(self, venue: str) -> Any | None:
        registry = getattr(self._live, "_registry", None)
        if registry is None:
            return None
        return registry.get_client(venue, enable_trading=True)

    async def reconcile_from_exchange(self, venue: str = "bitvavo") -> dict[str, Any]:
        """Pull live balances into the paper pocket + venue ledger for strategy sizing."""
        venue = venue.strip().lower()
        client = self._trading_client(venue)
        if client is None:
            return {"ok": False, "reason": "no_client", "venue": venue}
        try:
            snap = await client.get_balances()
        except Exception as exc:  # noqa: BLE001
            logger.warning("micro reconcile balance fetch failed: %s", type(exc).__name__)
            return {"ok": False, "reason": "balance_fetch_failed", "error": str(exc)[:200]}

        bals = list(snap.balances or [])
        self._bal_cache[venue] = bals
        self._bal_cache_mono[venue] = time.monotonic()
        self._venue_raw_balances[venue] = bals

        if self._portfolio.venue_ledger is None:
            self._portfolio.init_venue_ledger(
                sorted(self._execute_venues), starting_quote=_ZERO
            )
        else:
            self._portfolio.venue_ledger.ensure_venues(sorted(self._execute_venues))

        ledger_balances: dict[str, Decimal] = {}
        for bal in bals:
            asset = str(bal.asset or "").upper()
            if not asset:
                continue
            if asset != self._quote:
                if asset in self._exclude_bases:
                    continue
                if self._allowed_bases is not None and asset not in self._allowed_bases:
                    continue
            free = Decimal(str(bal.free or 0))
            if free > 0:
                if asset == self._quote:
                    ledger_balances[asset] = min(free, self._budget)
                else:
                    ledger_balances[asset] = free
        self._portfolio.venue_ledger.replace_balances(venue, ledger_balances)

        mapped = self._rebuild_aggregate_from_venues()
        portfolio_value = await self.refresh_portfolio_value()
        if self.starting_portfolio_eur is None and portfolio_value is not None:
            self.starting_portfolio_eur = portfolio_value
        if self.session_start_realized_eur is None:
            self.session_start_realized_eur = self.realized_trade_pnl_eur
        if self._session_started_ms is None:
            self._session_started_ms = time.time() * 1000.0
        # Seed EVERY execute venue (not only the first sync) so OKX lots exist.
        self._seed_cost_lots_from_balances(venue, bals)
        # Prefer real exchange fills over mark seeds before any auto-sell.
        try:
            hydrate = await self._hydrate_cost_basis_from_trades(venue)
            venue_sync_hydrate = hydrate
        except Exception as exc:  # noqa: BLE001
            venue_sync_hydrate = {"ok": False, "error": str(exc)[:160]}
        try:
            backfill = await self._backfill_fills_from_trades(venue)
            venue_sync_hydrate = {
                **(venue_sync_hydrate if isinstance(venue_sync_hydrate, dict) else {}),
                "fill_backfill": backfill,
            }
        except Exception as exc:  # noqa: BLE001
            logger.info("fill backfill failed venue=%s err=%s", venue, type(exc).__name__)

        venue_sync = {
            "ok": True,
            "venue": venue,
            "balances": mapped,
            "ledger": {k: str(v) for k, v in sorted(ledger_balances.items())},
            "venue_budget_remaining": str(self._venue_budget_remaining(venue)),
            "free_quote_eur": str(self._venue_budget_remaining(venue)),
            "remaining_eur": str(self.budget_remaining),
            "portfolio_value_eur": (
                str(self.portfolio_value_eur) if self.portfolio_value_eur is not None else None
            ),
            "cost_hydrate": venue_sync_hydrate,
        }
        self._last_sync_by_venue[venue] = venue_sync
        self._last_sync = venue_sync
        logger.info(
            "MICRO_SYNC venue=%s venue_eur=%s portfolio=%s total_remaining=%s assets=%s ledger=%s",
            venue,
            self._venue_budget_remaining(venue),
            self.portfolio_value_eur,
            self.budget_remaining,
            sorted(mapped.keys()),
            sorted(ledger_balances.keys()),
        )
        self.persist_runtime_state()
        return dict(venue_sync)

    async def _fetch_balances_cached(self, venue: str) -> list[Any]:
        venue = venue.strip().lower()
        now = time.monotonic()
        cached_mono = self._bal_cache_mono.get(venue, 0.0)
        if (
            venue in self._bal_cache
            and now - cached_mono < self._bal_cache_sec
        ):
            return self._bal_cache[venue]
        client = self._trading_client(venue)
        if client is None:
            return self._bal_cache.get(venue, [])
        snap = await client.get_balances()
        bals = list(snap.balances or [])
        self._bal_cache[venue] = bals
        self._bal_cache_mono[venue] = now
        self._venue_raw_balances[venue] = bals
        return bals

    async def refresh_portfolio_value(
        self,
        *,
        venue: str | None = None,
        balances: list[Any] | None = None,
    ) -> Decimal | None:
        """Mark portfolio to EUR across all execute venues (cash + crypto × last/bid)."""
        venues = [venue.strip().lower()] if venue else sorted(self._execute_venues)
        total = _ZERO
        now = time.monotonic()
        for v in venues:
            client = self._trading_client(v)
            if client is None:
                continue
            bals = balances if venue and balances is not None else None
            if bals is None:
                try:
                    bals = await self._fetch_balances_cached(v)
                except Exception:  # noqa: BLE001
                    continue
            for bal in bals:
                asset = str(getattr(bal, "asset", "") or "").upper()
                if not asset:
                    continue
                qty = Decimal(str(getattr(bal, "free", 0) or 0)) + Decimal(
                    str(getattr(bal, "locked", 0) or 0)
                )
                if qty <= 0:
                    continue
                if asset == self._quote:
                    if venue is None and v in self._venue_raw_balances:
                        # Cap each venue's EUR when summing total portfolio value.
                        qty = min(qty, self._budget)
                    total += qty
                    continue
                symbol = f"{asset}{self._quote}"
                mark = self._portfolio.state.mark_prices.get(symbol)
                fetched_at = self._mark_fetched_at.get(symbol, 0.0)
                stale = now - fetched_at >= self._mark_ttl_sec
                if mark is None or mark <= 0 or stale:
                    try:
                        ticker = await client.fetch_ticker(symbol)
                        mark = Decimal(
                            str(ticker.last or ticker.bid or ticker.ask or 0)
                        )
                        if mark > 0:
                            self._portfolio.set_mark_price(symbol, mark)
                            self._mark_fetched_at[symbol] = now
                    except Exception:  # noqa: BLE001
                        mark = self._portfolio.state.mark_prices.get(symbol)
                if mark is not None and mark > 0:
                    total += qty * mark
        if total > 0:
            self.portfolio_value_eur = total
            # Keep risk drawdown peak tied to real venue MTM (not paper ghosts).
            try:
                self._portfolio.set_live_mtm_cap(total)
            except Exception:  # noqa: BLE001
                logger.exception("failed to set live MTM drawdown cap")
        return self.portfolio_value_eur

    async def _live_free(self, venue: str, asset: str) -> Decimal:
        try:
            bals = await self._fetch_balances_cached(venue)
        except Exception:  # noqa: BLE001
            return _ZERO
        key = asset.upper()
        for bal in bals:
            if str(getattr(bal, "asset", "")).upper() == key:
                return Decimal(str(getattr(bal, "free", 0) or 0))
        return _ZERO

    async def _poll_fill(
        self,
        *,
        venue: str,
        symbol: str,
        exchange_order_id: str | None,
        fallback_price: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Poll exchange order briefly; return (filled_qty, avg_price)."""
        if not exchange_order_id:
            return _ZERO, fallback_price
        client = self._trading_client(venue)
        if client is None or not hasattr(client, "fetch_order"):
            return _ZERO, fallback_price

        deadline = time.monotonic() + _FILL_POLL_SECONDS
        last_filled = _ZERO
        last_avg = fallback_price
        while True:
            try:
                order = await client.fetch_order(str(exchange_order_id), symbol)
            except Exception:  # noqa: BLE001
                break
            last_filled = Decimal(str(order.filled_quantity or 0))
            avg = order.average_price or order.price or fallback_price
            last_avg = Decimal(str(avg or fallback_price))
            status = order.status
            status_val = status.value if hasattr(status, "value") else str(status)
            if last_filled > 0:
                return last_filled, last_avg if last_avg > 0 else fallback_price
            if str(status_val).lower() in {
                "filled",
                "closed",
                "cancelled",
                "canceled",
                "rejected",
                "failed",
            }:
                return last_filled, last_avg if last_avg > 0 else fallback_price
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(_FILL_POLL_INTERVAL)
        return last_filled, last_avg if last_avg > 0 else fallback_price

    def _track_resting(
        self,
        *,
        venue: str,
        symbol: str,
        side: str,
        exchange_order_id: str | None,
        quantity: Decimal,
        price: Decimal,
        strategy: str,
        opportunity_id: Any,
    ) -> None:
        if not exchange_order_id:
            return
        self._resting.append(
            {
                "venue": venue,
                "symbol": symbol,
                "side": side,
                "exchange_order_id": str(exchange_order_id),
                "quantity": Decimal(str(quantity)),
                "price": Decimal(str(price)),
                "strategy": strategy,
                "opportunity_id": opportunity_id,
                "placed_mono": time.monotonic(),
                "placed_at": time.time(),
            }
        )
        self.persist_runtime_state(force=True)

    async def _prune_resting_buys(self, venue: str) -> int:
        """Keep at most N resting buys per symbol; drop buys for held bases."""
        client = self._trading_client(venue)
        if client is None:
            return 0
        venue_l = venue.strip().lower()
        held = self._held_alt_bases(venue)
        by_symbol: dict[str, list[dict[str, Any]]] = {}
        cancelled = 0
        still: list[dict[str, Any]] = []

        for row in list(self._resting):
            row_venue = str(row.get("venue") or "").strip().lower()
            if row_venue and row_venue != venue_l:
                still.append(row)
                continue
            side_raw = str(row.get("side") or "buy").lower()
            if not side_raw.startswith("b"):
                still.append(row)
                continue
            sym = str(row.get("symbol") or "").upper()
            if not sym:
                continue
            base = infer_base_asset(sym)
            if self._block_buys_when_holding_base and base in held:
                oid = str(row.get("exchange_order_id") or "")
                if oid:
                    try:
                        await client.cancel_order(oid, sym)
                        cancelled += 1
                        self._invalidate_bal_cache()
                        self._bump_skip("held_base_resting_cancelled")
                    except Exception:  # noqa: BLE001
                        still.append(row)
                        continue
                continue
            by_symbol.setdefault(sym, []).append(row)

        keep_n = max(1, self._max_resting_buys_per_symbol)
        for sym, rows in by_symbol.items():
            if len(rows) <= keep_n:
                still.extend(rows)
                continue
            rows.sort(
                key=lambda r: Decimal(str(r.get("price") or 0)),
                reverse=True,
            )
            still.extend(rows[:keep_n])
            for extra in rows[keep_n:]:
                oid = str(extra.get("exchange_order_id") or "")
                if not oid:
                    continue
                try:
                    await client.cancel_order(oid, sym)
                    cancelled += 1
                    self._invalidate_bal_cache()
                    self._bump_skip("duplicate_resting_cancelled")
                except Exception:  # noqa: BLE001
                    still.append(extra)

        self._resting = still
        if cancelled:
            self.persist_runtime_state()
        return cancelled

    async def _resting_order_snapshots(
        self,
        client: Any,
        rows: list[dict[str, Any]],
    ) -> dict[str, tuple[Decimal, Decimal, str]]:
        """Parallel fetch_order for resting rows on one venue."""

        async def _one(row: dict[str, Any]) -> tuple[str, tuple[Decimal, Decimal, str]]:
            oid = str(row.get("exchange_order_id") or "")
            symbol = str(row.get("symbol") or "")
            fallback = Decimal(str(row.get("price") or 0))
            if not oid or not symbol:
                return oid, (_ZERO, fallback, "open")
            try:
                order = await client.fetch_order(oid, symbol)
                filled = Decimal(str(order.filled_quantity or 0))
                avg = Decimal(
                    str(order.average_price or order.price or row.get("price") or 0)
                )
                status = order.status
                status_val = status.value if hasattr(status, "value") else str(status)
                return oid, (filled, avg, str(status_val))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "resting fetch_order failed id=%s symbol=%s err=%s",
                    oid,
                    symbol,
                    f"{type(exc).__name__}: {exc}"[:180],
                )
                return oid, (_ZERO, fallback, "open")

        if not rows:
            return {}
        pairs = await asyncio.gather(*[_one(row) for row in rows])
        return {oid: snap for oid, snap in pairs if oid}

    async def manage_resting_orders(self, venue: str = "bitvavo") -> dict[str, Any]:
        """Poll resting live orders: mirror fills, cancel stale quotes, free capital."""
        pruned = await self._prune_resting_buys(venue)
        client = self._trading_client(venue)
        if client is None:
            return {"ok": False, "reason": "no_client"}
        mirrored = 0
        cancelled = 0
        still: list[dict[str, Any]] = []
        terminal_dropped: list[dict[str, Any]] = []
        now = time.monotonic()
        max_age = self._resting_max_age_sec
        venue_l = venue.strip().lower()
        tracked_ids = {
            str(r.get("exchange_order_id"))
            for r in self._resting
            if str(r.get("venue") or "").strip().lower() == venue_l
        }
        venue_rows: list[dict[str, Any]] = []
        for row in list(self._resting):
            row_venue = str(row.get("venue") or "").strip().lower()
            if row_venue and row_venue != venue_l:
                continue
            oid = str(row.get("exchange_order_id") or "")
            symbol = str(row.get("symbol") or "")
            if oid and symbol:
                venue_rows.append(row)
        snapshots = (
            await self._resting_order_snapshots(client, venue_rows)
            if client is not None
            else {}
        )

        for row in list(self._resting):
            row_venue = str(row.get("venue") or "").strip().lower()
            # Critical: never poll Bitvavo ids on OKX (or vice versa) — that
            # yields ExchangeError spam, drops fill mirrors, and hits max-open.
            if row_venue and row_venue != venue_l:
                still.append(row)
                continue
            oid = str(row.get("exchange_order_id") or "")
            symbol = str(row.get("symbol") or "")
            if not oid or not symbol:
                continue
            filled = _ZERO
            avg = Decimal(str(row.get("price") or 0))
            status_val = "open"
            snap = snapshots.get(oid)
            if snap is not None:
                filled, avg, status_val = snap

            if filled > 0 and avg > 0:
                from bot.core.models import OrderRequest
                from uuid import UUID

                side_raw = str(row.get("side") or "buy").lower()
                opp_side = (
                    OpportunitySide.BUY if side_raw.startswith("b") else OpportunitySide.SELL
                )
                opp_id = row.get("opportunity_id")
                try:
                    opp_uuid = opp_id if isinstance(opp_id, UUID) else uuid4()
                except Exception:  # noqa: BLE001
                    opp_uuid = uuid4()
                req = OrderRequest(
                    opportunity_id=opp_uuid,
                    symbol=symbol,
                    side=opp_side,
                    quantity=filled,
                    limit_price=avg,
                    metadata={"venue": venue, "exchange": venue},
                )
                await self._mirror_live_fill(
                    req,
                    filled_qty=filled,
                    average_price=avg,
                    venue=venue,
                    strategy=str(row.get("strategy") or "maker_inventory"),
                    exchange_order_id=oid,
                )
                mirrored += 1
                remaining_open = str(status_val).lower() in {"open", "submitted", "pending", "partial"}
                if remaining_open and filled < Decimal(str(row.get("quantity") or filled)):
                    # partial — keep tracking remainder
                    row["quantity"] = Decimal(str(row.get("quantity") or 0)) - filled
                    if row["quantity"] > 0:
                        still.append(row)
                continue

            age = now - float(row.get("placed_mono") or now)
            terminal = str(status_val).lower() in {
                "cancelled",
                "canceled",
                "rejected",
                "failed",
                "expired",
                "filled",
                "closed",
            }
            if terminal:
                terminal_dropped.append(row)
                continue
            # Never leave a loss-making sell resting on either venue.
            side_raw = str(row.get("side") or "buy").lower()
            if side_raw.startswith("s"):
                base = infer_base_asset(symbol)
                px = Decimal(str(row.get("price") or 0))
                ok_sell, gate_reason, be = self._sell_allowed_at(venue, base, px)
                if not ok_sell:
                    try:
                        await client.cancel_order(oid, symbol)
                        cancelled += 1
                        self._invalidate_bal_cache()
                        self._bump_skip("sell_below_break_even_cancelled")
                        logger.info(
                            "MICRO_LOSS_SELL_CANCEL venue=%s symbol=%s id=%s reason=%s be=%s px=%s",
                            venue,
                            symbol,
                            oid,
                            gate_reason,
                            be,
                            px,
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning("loss-sell cancel failed id=%s", oid)
                        still.append(row)
                    continue
            # Rising-tape: cancel resting buys when momentum goes flat/down.
            # Low-util: keep quotes working while ring is still thinly deployed.
            if (
                side_raw.startswith("b")
                and self._cancel_buy_on_flat_momentum
                and self._momentum_flat_or_down_for_cancel(symbol)
            ):
                try:
                    await client.cancel_order(oid, symbol)
                    cancelled += 1
                    self._invalidate_bal_cache()
                    self._bump_skip("buy_momentum_cancelled")
                    logger.info(
                        "MICRO_BUY_MOMENTUM_CANCEL venue=%s symbol=%s id=%s",
                        venue,
                        symbol,
                        oid,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("buy-momentum cancel failed id=%s", oid)
                    still.append(row)
                continue
            # D: trail/exit sells reprice fast so soft-armed spikes can fill.
            strategy = str(row.get("strategy") or "")
            row_max_age = max_age
            if side_raw.startswith("b") and self._buy_resting_max_age_sec > 0:
                row_max_age = min(
                    max_age, self._buy_resting_max_age_for_venue(venue)
                )
            if (
                self._exit_engine_enabled
                and side_raw.startswith("s")
                and (
                    strategy.startswith("trail_")
                    or strategy in {"dust_exit", "time_stop_breakeven"}
                )
            ):
                row_max_age = min(max_age, self._exit_resting_max_age_sec)
            if age >= row_max_age:
                try:
                    await client.cancel_order(oid, symbol)
                    cancelled += 1
                    self._invalidate_bal_cache()
                    self._bump_skip("stale_quote_cancelled")
                    if (
                        side_raw.startswith("s")
                        and strategy.startswith("trail_")
                    ):
                        base = infer_base_asset(symbol)
                        fails = self._bump_exit_maker_fail(venue, base)
                        logger.info(
                            "EXIT_MAKER_STALE venue=%s base=%s fails=%s strategy=%s",
                            venue,
                            base,
                            fails,
                            strategy,
                        )
                    logger.info(
                        "MICRO_STALE_CANCEL venue=%s symbol=%s id=%s age=%.1fs max=%.1fs strategy=%s",
                        venue,
                        symbol,
                        oid,
                        age,
                        row_max_age,
                        strategy,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("stale cancel failed id=%s", oid)
                    still.append(row)
                continue
            still.append(row)

        for row in terminal_dropped:
            try:
                n = await self._mirror_trades_for_resting_order(venue_l, row)
                mirrored += n
            except Exception:  # noqa: BLE001
                logger.warning(
                    "resting terminal fill backfill failed venue=%s id=%s",
                    venue_l,
                    row.get("exchange_order_id"),
                )

        self._resting = still
        tracked_ids = {str(r.get("exchange_order_id")) for r in self._resting}

        # Orphan sweep is throttled — avoid cancelling fresh quotes we just tracked.
        do_orphan = (
            max_age <= 0
            or now - self._last_orphan_sweep_mono >= self._orphan_sweep_sec
        )
        if do_orphan:
            self._last_orphan_sweep_mono = now
            try:
                open_orders = await client.fetch_open_orders()
            except Exception:  # noqa: BLE001
                open_orders = []
            for order in open_orders or []:
                oid = str(getattr(order, "id", None) or "")
                if not oid or oid in tracked_ids:
                    continue
                symbol = (
                    str(getattr(order, "symbol", "") or "")
                    .upper()
                    .replace("/", "")
                    .replace("-", "")
                )
                if not symbol:
                    continue
                try:
                    await client.cancel_order(oid, symbol)
                    cancelled += 1
                    self._invalidate_bal_cache()
                    self._bump_skip("orphan_open_cancelled")
                except Exception:  # noqa: BLE001
                    logger.warning("orphan cancel failed id=%s", oid)

        live_exec = getattr(self._live, "executor", None)
        if live_exec is not None and hasattr(live_exec, "refresh_open_order_count"):
            try:
                resting_here = self._resting_count_for(venue_l)
                if hasattr(live_exec, "note_open_orders_for"):
                    live_exec.note_open_orders_for(venue_l, resting_here)
                elif cancelled and hasattr(live_exec, "note_open_orders"):
                    live_exec.note_open_orders(resting_here)
                await live_exec.refresh_open_order_count(venue, force=bool(cancelled))
            except Exception:  # noqa: BLE001
                pass
        if mirrored:
            await self.reconcile_from_exchange(venue)
            self.persist_runtime_state(force=True)
        else:
            self.persist_runtime_state()
        return {
            "ok": True,
            "mirrored": mirrored,
            "cancelled": cancelled,
            "pruned": pruned,
            "resting": len(self._resting),
        }

    def _sync_paper_entry_from_lots(self, venue: str, base: str) -> None:
        """Align paper average entry with trusted live lots (prevents phantom daily loss)."""
        unit = self._unit_cost(venue, base)
        if unit is None or unit <= 0:
            return
        symbol = f"{base.upper()}{self._quote}"
        pos = self._portfolio.state.positions.get(symbol)
        if pos is None or pos.quantity <= 0:
            return
        pos.average_entry_price = unit

    def mark_session_baseline(self) -> None:
        """Reset session MTM/realized baselines at micro session start."""
        self.starting_portfolio_eur = self.portfolio_value_eur
        self.session_start_realized_eur = self.realized_trade_pnl_eur
        self._session_started_ms = time.time() * 1000.0
        self._daily_kill_active = False
        self._sleeve_realized_eur = _ZERO
        self._sleeve_paused = False
        self._utc_day_marker = datetime.now(UTC).strftime("%Y-%m-%d")
        self._winnable_gap_alert_sent = False

    def reset_operator_dashboard(self) -> dict[str, Any]:
        """Zero cumulative KPIs and chart history for a clean operator slate."""
        from bot.live.dashboard_history import clear_history
        from bot.live.dashboard_pnl import clear_calendar_pnl_cache, set_operator_pnl_anchor

        self.realized_trade_pnl_eur = _ZERO
        self.session_start_realized_eur = _ZERO
        self.starting_portfolio_eur = self.portfolio_value_eur
        self.session_live_fill_count = 0
        self.session_live_transaction_count = 0
        self.live_fill_count = 0
        self.live_transaction_count = 0
        self.backfill_mirrored_count = 0
        self.live_trades.clear()
        self.recent_live_fills.clear()
        self.skips.clear()
        self._daily_kill_active = False
        self._sleeve_realized_eur = _ZERO
        self._sleeve_paused = False
        self.set_buys_blocked(False)
        self.set_underwater_base_blocks({})
        self._session_started_ms = time.time() * 1000.0
        self.reset_paper_realized_after_inventory_sync()
        clear_history()
        clear_calendar_pnl_cache()
        anchor = set_operator_pnl_anchor()
        self.persist_runtime_state()
        logger.info(
            "OPERATOR_DASHBOARD_RESET portfolio=%s realized=0 fills=0 anchor=%s",
            self.starting_portfolio_eur,
            anchor.isoformat(),
        )
        return {
            "ok": True,
            "realized_trade_pnl_eur": "0",
            "starting_portfolio_eur": str(self.starting_portfolio_eur or ""),
            "session_start_realized_eur": "0",
            "session_live_transaction_count": 0,
            "operator_anchor_utc": anchor.isoformat(),
        }

    def reset_trading_cycle(self) -> dict[str, Any]:
        """Fresh cycle after wind-down: unblock buys and prune dust trails.

        Preserves cumulative realized PnL and dashboard chart history so wins
        stay visible after inventory is fully wound down.
        """
        realized = self.realized_trade_pnl_eur
        session_start = self.session_start_realized_eur
        tx_count = self.session_live_transaction_count
        fill_count = self.session_live_fill_count

        self.skips.clear()
        self._daily_kill_active = False
        self._sleeve_paused = False
        # Keep sleeve_realized across cycle reset so daily loss cap still applies.
        self._check_sleeve_loss_cap()
        self.set_buys_blocked(False)
        self.set_underwater_base_blocks({})
        self.starting_portfolio_eur = self.portfolio_value_eur
        self._session_started_ms = time.time() * 1000.0

        prune: list[str] = []
        for trail_key, st in list(self._trail.items()):
            venue = str(st.get("venue") or trail_key.split(":", 1)[0])
            base = str(st.get("base") or trail_key.split(":", 1)[-1])
            qty = self._balance_qty(venue, base)
            mark = Decimal(str(st.get("last_mark") or 0))
            if qty * mark < _MIN_LIVE_NOTIONAL:
                prune.append(trail_key)
        for key in prune:
            self._trail.pop(key, None)
            self._position_opened_mono.pop(key, None)
        self.persist_runtime_state()
        logger.info(
            "TRADING_CYCLE_RESET portfolio=%s realized=%s pruned_trails=%s "
            "(KPIs/history preserved)",
            self.starting_portfolio_eur,
            realized,
            len(prune),
        )
        return {
            "ok": True,
            "realized_trade_pnl_eur": str(realized),
            "starting_portfolio_eur": str(self.starting_portfolio_eur or ""),
            "session_start_realized_eur": str(session_start),
            "session_live_transaction_count": tx_count,
            "session_live_fill_count": fill_count,
            "pruned_trails": len(prune),
            "preserved_kpis": True,
        }

    def maybe_reset_after_wind_down(self) -> bool:
        """When micro inventory is gone, start a clean trading cycle."""
        if self._held_alt_bases():
            return False
        mtm = self._mtm_summary()
        locked = Decimal(str(mtm.get("micro_locked_notional_eur") or 0))
        if locked >= _MIN_LIVE_NOTIONAL:
            return False
        dirty = (
            bool(self.skips)
            or self._daily_kill_active
            or self._buys_blocked
            or bool(self._underwater_blocked_bases)
        )
        if not dirty:
            return False
        self.reset_trading_cycle()
        return True

    async def reconcile_dashboard_since(self, since: datetime) -> dict[str, Any]:
        """Rebuild dashboard KPIs and chart history from exchange fills since ``since``."""
        from bot.live.dashboard_reconcile import reconcile_dashboard_since

        for venue in sorted(self._execute_venues):
            try:
                await self.reconcile_from_exchange(venue)
            except Exception:  # noqa: BLE001
                logger.exception("pre-reconcile sync failed venue=%s", venue)
        return await reconcile_dashboard_since(self, since)

    def reset_paper_realized_after_inventory_sync(self) -> None:
        """Inventory sync is not a trade — clear phantom paper realized PnL."""
        try:
            self._portfolio.state.stats.realized_pnl = _ZERO
        except Exception:  # noqa: BLE001
            logger.exception("failed to reset paper realized after sync")

    def reset_session_risk_baseline(self, *, tracker: Any | None = None) -> Decimal:
        """Rewind drawdown peak to current equity at micro session start."""
        equity = self._portfolio.reset_drawdown_baseline()
        if tracker is not None and hasattr(tracker, "reset_drawdown_baseline"):
            tracker.reset_drawdown_baseline(equity=equity)
        return equity

    def _unit_cost(self, venue: str, base: str) -> Decimal | None:

        lots = self._cost_lots.get(self._lots_key(venue, base)) or []
        total_qty = _ZERO
        total_cost = _ZERO
        for qty, unit in lots:
            if qty <= 0 or unit <= 0:
                continue
            total_qty += qty
            total_cost += qty * unit
        if total_qty > 0:
            return total_cost / total_qty
        symbol = f"{base.upper()}{self._quote}"
        pos = self._portfolio.state.positions.get(symbol)
        if pos is not None and pos.quantity > 0 and pos.average_entry_price > 0:
            return Decimal(str(pos.average_entry_price))
        return None

    def _break_even_sell_price(
        self, venue: str, base: str, *, taker: bool = False
    ) -> Decimal | None:
        """Min sell price that nets profit after fees + buffer. Requires trusted cost."""
        if not self._has_trusted_cost(venue, base):
            return None
        unit = self._unit_cost(venue, base)
        if unit is None or unit <= 0:
            return None
        from bot.core.venue_fees import venue_maker_fee, venue_taker_fee

        fee = venue_taker_fee(venue) if taker else venue_maker_fee(venue)
        denom = Decimal("1") - fee
        if denom <= 0:
            return None
        be = unit / denom
        buffer_bps = Decimal(
            str(getattr(self._settings, "paper_maker_sell_profit_buffer_bps", 0) or 0)
        )
        if buffer_bps > 0:
            be *= Decimal("1") + buffer_bps / Decimal("10000")
        return be

    def _time_stop_floor_price(self, venue: str, base: str) -> Decimal | None:
        """Legacy helper: BE + optional min-profit buffer (tests / diagnostics)."""
        be = self._break_even_sell_price(venue, base)
        if be is None:
            return None
        extra_bps = Decimal(
            str(getattr(self._settings, "paper_time_stop_min_profit_bps", 0) or 0)
        )
        if extra_bps > 0:
            be *= Decimal("1") + extra_bps / Decimal("10000")
        return be

    def _maybe_recovery_arm_from_loss(
        self,
        st: dict[str, Any],
        *,
        venue: str,
        base: str,
        mark: Decimal,
        be: Decimal | None,
    ) -> bool:
        """Track underwater bags; on loss→BE cross arm recovery (no immediate sell).

        Rising through BE must not sell. Exit later on trail drawdown or when
        price falls back to the BE floor after having traded above it.
        """
        if be is None or be <= 0:
            return False
        if mark < be:
            st["below_be"] = True
            # Allow a fresh BE+ harvest once the bag recovers.
            st["be_harvest_partial_done"] = False
            st["recovery_be_partial_done"] = False
            return False
        if not st.get("below_be"):
            return False
        st["below_be"] = False
        return self._recovery_arm_trail(
            st, venue=venue, base=base, mark=mark, be=be
        )

    def _recovery_arm_trail(
        self,
        st: dict[str, Any],
        *,
        venue: str,
        base: str,
        mark: Decimal,
        be: Decimal,
    ) -> bool:
        """Arm trail when a bag recovers to BE from a loss — do not dump flat.

        Lets the position grow; exits later via trail drawdown or falling back to BE.
        Returns True when this call newly sets recovery_armed.
        """
        if mark < be:
            return False
        already = bool(st.get("recovery_armed"))
        if already and st.get("soft_armed"):
            return False
        if not st.get("soft_armed"):
            st["soft_armed"] = True
            st["armed"] = True
            peak = Decimal(str(st.get("peak") or 0))
            st["peak"] = mark if peak <= 0 else max(peak, mark)
            st["newly_soft"] = False  # never soft-partial on the BE touch itself
            st["newly_armed"] = True
            st["time_stop_due"] = False
            soft_arm, soft_dd, _hard_arm, _hard_dd = self._scaled_arms(base, be)
            st["drawdown"] = str(soft_dd)
            st["soft_arm"] = str(soft_arm)
        st["recovery_armed"] = True
        if already:
            return False
        # Fresh recovery → allow immediate BE+ harvest (don't wait for old done flags).
        st["be_harvest_partial_done"] = False
        st["recovery_be_partial_done"] = False
        soft_dd = Decimal(str(st.get("drawdown") or self._soft_dd_floor))
        self._push_alert(
            "recovery_arm",
            f"{base} recovery-arm at BE mark={mark} be={be} "
            f"(trail dd={float(soft_dd * 100):.1f}%; BE+ harvest + pullback floor)",
            base=base,
        )
        logger.info(
            "TRAIL_RECOVERY_ARM venue=%s base=%s mark=%s be=%s soft_dd=%.2f%%",
            venue,
            base,
            mark,
            be,
            float(soft_dd * 100),
        )
        return True

    async def _profitable_exit_quote(
        self,
        venue: str,
        base: str,
        mark: Decimal,
        *,
        aggressive: bool = False,
        force_taker: bool = False,
    ) -> tuple[Decimal | None, bool, str]:
        """Pick a fillable exit price that still clears fee-aware break-even.

        Returns (limit_price, post_only, reason). When the bid is already above
        taker break-even, hit the bid (taker) so trail exits actually fill.

        ``aggressive`` (exit engine): join inside the spread near the bid touch
        instead of resting at the ask — captures short soft-armed spikes.
        Never quotes below maker BE; taker only when bid ≥ taker BE.
        """
        be_maker = self._break_even_sell_price(venue, base, taker=False)
        be_taker = self._break_even_sell_price(venue, base, taker=True)
        if be_maker is None or be_taker is None:
            return None, True, "no_break_even"
        if mark < be_maker:
            return None, True, "mark_below_maker_be"

        best_bid = _ZERO
        best_ask = _ZERO
        client = self._trading_client(venue)
        symbol = f"{base.upper()}{self._quote}"
        if client is not None:
            try:
                ticker = await client.fetch_ticker(symbol)
                best_bid = Decimal(str(getattr(ticker, "bid", None) or 0))
                best_ask = Decimal(str(getattr(ticker, "ask", None) or 0))
            except Exception:  # noqa: BLE001
                pass
        if best_bid <= 0:
            best_bid = mark
        if best_ask <= 0:
            best_ask = mark

        # Escalate to taker after repeated stale maker exit attempts.
        if force_taker and best_bid >= be_taker:
            return best_bid, False, "hit_bid_taker"
        if force_taker and mark >= be_taker:
            return max(be_taker, best_bid), False, "limit_taker_be"

        # Bid already clears taker BE → take liquidity for a sure profitable fill.
        if best_bid >= be_taker:
            return best_bid, False, "hit_bid_taker"

        use_aggressive = bool(aggressive and self._exit_engine_enabled)
        if use_aggressive:
            cushion = self._exit_taker_cushion_bps / Decimal("10000")
            # Mark already pays taker fees with cushion → work a fillable limit
            # at taker-BE (can take when bid catches up; never below taker BE).
            if mark >= be_taker * (Decimal("1") + cushion):
                return max(be_taker, best_bid), False, "limit_taker_be"
            # Inside-spread maker near bid touch (fill-seeking, still post-only).
            improve = best_bid * (self._exit_touch_improve_bps / Decimal("10000"))
            tick = best_bid * Decimal("0.00005")
            step = max(improve, tick)
            touch_px = best_bid + step
            if best_ask > best_bid:
                # Stay maker: do not cross the ask.
                ask_cap = best_ask - min(step, (best_ask - best_bid) / Decimal("2"))
                if ask_cap > best_bid:
                    touch_px = min(touch_px, ask_cap)
            maker_px = max(be_maker, touch_px)
            if maker_px <= best_bid:
                maker_px = max(be_maker, best_bid + step)
            return maker_px, True, "rest_touch_maker"

        # Passive: rest as maker at/above maker BE, near the touch.
        maker_px = max(be_maker, min(best_ask, mark))
        if maker_px <= best_bid:
            maker_px = max(be_maker, best_bid + (best_bid * Decimal("0.0001")))
        return maker_px, True, "rest_maker_be"

    def _sell_allowed_at(
        self, venue: str, base: str, price: Decimal
    ) -> tuple[bool, str, Decimal | None]:
        """Gate every auto-sell: trusted cost and price >= fee-aware break-even."""
        if price <= 0:
            return False, "invalid_price", None
        if not self._has_trusted_cost(venue, base):
            return False, "sell_no_trusted_cost", None
        be = self._break_even_sell_price(venue, base)
        if be is None:
            return False, "sell_no_break_even", None
        if price < be:
            return False, "sell_below_break_even", be
        return True, "ok", be

    def _momentum_exit_target_price(
        self, venue: str, base: str
    ) -> Decimal | None:
        """Minimum sell target on downward momentum (BE + cushion)."""
        if self._momentum_exit_above_be_pct <= 0:
            return None
        be = self._break_even_sell_price(venue, base)
        if be is None or be <= 0:
            return None
        return be * (Decimal("1") + self._momentum_exit_above_be_pct)

    def _cut_loss_floor_price(self, venue: str, base: str) -> Decimal | None:
        """Stop floor: fee-aware BE minus configured pct (e.g. 4% under BE)."""
        if self._cut_loss_below_be_pct <= 0:
            return None
        be = self._break_even_sell_price(venue, base)
        if be is None or be <= 0:
            return None
        return be * (Decimal("1") - self._cut_loss_below_be_pct)

    def _early_cut_loss_floor_price(self, venue: str, base: str) -> Decimal | None:
        """Momentum early stop: BE minus smaller pct (e.g. 1% under BE)."""
        if self._early_cut_loss_below_be_pct <= 0:
            return None
        be = self._break_even_sell_price(venue, base)
        if be is None or be <= 0:
            return None
        return be * (Decimal("1") - self._early_cut_loss_below_be_pct)

    def _cut_loss_floor_for_reason(
        self, venue: str, base: str, reason: str
    ) -> Decimal | None:
        if reason == "trail_early_cut_loss":
            return self._early_cut_loss_floor_price(venue, base)
        return self._cut_loss_floor_price(venue, base)

    def _cut_loss_eligible(
        self, st: dict[str, Any], *, venue: str, base: str
    ) -> bool:
        if self._cut_loss_below_be_pct <= 0 or self._is_long_hold(base):
            return False
        if self._cut_loss_new_bases_only and not st.get("new_session_base"):
            return False
        if not self._has_trusted_cost(venue, base):
            return False
        return True

    def _early_cut_eligible(
        self, st: dict[str, Any], *, venue: str, base: str
    ) -> bool:
        """Early cut: free new-session bags that fail quickly (vault untouched)."""
        if self._early_cut_loss_below_be_pct <= 0 or self._is_long_hold(base):
            return False
        if self._early_cut_new_bases_only and not st.get("new_session_base"):
            return False
        if not self._has_trusted_cost(venue, base):
            return False
        return True

    def _momentum_flat_or_down(self, symbol: str) -> bool:
        """True when rolling mark return ≤ early-cut momentum max (default 0 = flat/down)."""
        if not self._momentum_enabled:
            return False
        series = self._series_for(symbol)
        need = max(3, min(6, self._momentum_samples // 2))
        if len(series) < need:
            return False
        mom = series.momentum_return()
        if mom is None:
            return False
        return mom <= self._early_cut_momentum_max

    async def _cut_loss_exit_quote(
        self,
        venue: str,
        base: str,
        mark: Decimal,
        *,
        floor: Decimal | None = None,
    ) -> tuple[Decimal | None, bool, str]:
        """Taker exit at/below cut-loss floor — frees capital on stuck new entries."""
        if floor is None:
            floor = self._cut_loss_floor_price(venue, base)
        if floor is None:
            return None, False, "no_cut_loss_floor"
        if mark > floor:
            return None, False, "above_cut_loss_floor"
        best_bid = _ZERO
        client = self._trading_client(venue)
        symbol = f"{base.upper()}{self._quote}"
        if client is not None:
            try:
                ticker = await client.fetch_ticker(symbol)
                best_bid = Decimal(str(getattr(ticker, "bid", None) or 0))
            except Exception:  # noqa: BLE001
                pass
        if best_bid <= 0:
            best_bid = mark
        return best_bid, False, "hit_bid_cut_loss"

    def _scaled_arms(self, base: str, cost: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        from bot.live.trail_policy import scale_thresholds

        symbol = f"{base.upper()}{self._quote}"
        atr = self._series_for(symbol).atr_pct()
        th = scale_thresholds(
            atr=atr,
            soft_arm_floor=self._soft_arm_floor,
            soft_dd_floor=self._soft_dd_floor,
            hard_arm_floor=self._hard_arm_floor,
            hard_dd_floor=self._hard_dd_floor,
            atr_arm_mult=self._atr_arm_mult,
            atr_dd_mult=self._atr_dd_mult,
            atr_enabled=self._atr_enabled,
        )
        return th.soft_arm, th.soft_dd, th.hard_arm, th.hard_dd

    def _corr_held_count(
        self, *, venue: str | None = None, adding: str | None = None
    ) -> int:
        """Count corr-group holdings that still consume concentration slots.

        Underwater/stuck bags are excluded — they belong to the stuck book and
        must not block active-ring deployment into other corr focus bases.
        """
        if not self._corr_group:
            return 0
        held = self._held_alt_bases(venue)
        venue_l = (venue or "").strip().lower()
        stuck = {
            str(b).upper()
            for b in self._underwater_blocked_bases.get(venue_l, set())
        } if venue_l else set()
        if stuck:
            held = {b for b in held if b not in stuck}
        if adding:
            held = set(held) | {adding.upper()}
        return len(held & self._corr_group)

    def _ring_needs_deploy(self, venue: str) -> bool:
        """True when active-book notional is below ring and free EUR remains."""
        if self._active_ring_eur <= 0:
            return False
        free = self._venue_budget_remaining(venue)
        if free < Decimal("50"):
            return False
        return self._active_book_notional(venue) < self._active_ring_eur

    def _ring_soft_momentum_eligible(self, venue: str) -> bool:
        """Softer momentum only while active book is still thinly deployed."""
        if not self._ring_needs_deploy(venue):
            return False
        if (
            self._ring_soft_block_underwater_eur > 0
            and self._underwater_book_notional(venue)
            >= self._ring_soft_block_underwater_eur
        ):
            return False
        if self._ring_soft_max_active_eur <= 0:
            return True
        return self._active_book_notional(venue) < self._ring_soft_max_active_eur

    def _momentum_floor_for_buy(self, venue: str) -> Decimal:
        """Softer momentum floor only while active book is thinly deployed."""
        if self._ring_soft_momentum_eligible(venue):
            return self._ring_momentum_min
        return self._momentum_min

    def _momentum_ok(
        self,
        symbol: str,
        *,
        require_history: bool = False,
        min_return: Decimal | None = None,
        low_util: bool = False,
    ) -> bool:
        """True when rolling mark return is at/above the configured floor.

        ``require_history`` (new-base entries): block until enough samples exist
        so cold symbols cannot slip through as "unknown momentum".
        Full mode: last N marks strictly rising. Low-util: mostly rising (2/3).
        """
        if not self._momentum_enabled:
            return True
        floor = self._momentum_min if min_return is None else min_return
        series = self._series_for(symbol)
        need = max(3, min(6, self._momentum_samples // 2))
        if len(series) < need:
            return not require_history
        mom = series.momentum_return()
        if mom is None:
            return not require_history
        if mom < floor:
            return False
        rising_n = self._momentum_require_last_n_rising
        if rising_n <= 0:
            return True
        if low_util and self._low_util_rising_n > 0:
            # Shorter rising window while ring is thinly deployed — never below entry floor.
            rising_n = max(self._low_util_rising_n, self._entry_min_low_util_rising_n)
            return series.last_n_rising(rising_n)
        return series.last_n_rising(rising_n)

    def _entry_momentum_ok(
        self,
        symbol: str,
        *,
        min_return: Decimal,
        low_util: bool,
    ) -> bool:
        """Stricter new-base entry: full + short-window momentum and rising tape."""
        if not self._momentum_ok(
            symbol,
            require_history=True,
            min_return=min_return,
            low_util=low_util,
        ):
            return False
        if self._entry_short_momentum_min <= 0:
            return True
        series = self._series_for(symbol)
        short = series.momentum_return_last(self._entry_short_momentum_samples)
        if short is None:
            return False
        return short >= self._entry_short_momentum_min

    def _corr_group_momentum_down_count(self) -> int:
        """Held corr-group bases with flat/down rolling momentum (sector weakness)."""
        if not self._corr_group:
            return 0
        seen: set[str] = set()
        down = 0
        for venue in self._execute_venues:
            for base in self._held_alt_bases(venue):
                b = str(base or "").upper()
                if b in seen or b not in self._corr_group:
                    continue
                seen.add(b)
                if self._momentum_flat_or_down_for_cancel(f"{b}{self._quote}"):
                    down += 1
        return down

    def _corr_sector_blocks_new_buy(self, base: str) -> bool:
        if self._corr_sector_momentum_block <= 0:
            return False
        if str(base or "").upper() not in self._corr_group:
            return False
        return (
            self._corr_group_momentum_down_count()
            >= self._corr_sector_momentum_block
        )

    def _buy_resting_max_age_for_venue(self, venue: str) -> float:
        if (
            self._ring_soft_momentum_eligible(venue)
            and self._low_util_buy_resting_max_age_sec > 0
        ):
            return max(self._buy_resting_max_age_sec, self._low_util_buy_resting_max_age_sec)
        return self._buy_resting_max_age_sec

    def _momentum_flat_or_down_for_cancel(self, symbol: str) -> bool:
        """True when rolling return ≤ 0 (cancel resting buys)."""
        if not self._momentum_enabled:
            return False
        series = self._series_for(symbol)
        need = max(3, min(6, self._momentum_samples // 2))
        if len(series) < need:
            return False
        mom = series.momentum_return()
        if mom is None:
            return False
        return mom <= 0

    def _buy_quality_paused(self) -> bool:
        return time.monotonic() < self._buy_quality_pause_until

    def _note_session_buy_for_quality(self, venue: str, base: str) -> None:
        key = self._lots_key(venue, base)
        if key not in self._recent_session_buy_keys:
            self._recent_session_buy_keys.append(key)
        if len(self._recent_session_buy_keys) > 20:
            self._recent_session_buy_keys = self._recent_session_buy_keys[-20:]

    def _refresh_buy_quality_circuit_breaker(self) -> None:
        """Pause new buys when too many recent session bags are underwater."""
        if self._buy_quality_underwater_count <= 0:
            return
        if self._buy_quality_paused():
            return
        underwater = 0
        for key in self._recent_session_buy_keys[-12:]:
            parts = key.split(":", 1)
            if len(parts) != 2:
                continue
            venue, base = parts[0], parts[1]
            qty = self._balance_qty(venue, base)
            if qty <= 0:
                continue
            be = self._break_even_sell_price(venue, base)
            if be is None or be <= 0:
                continue
            symbol = f"{base}{self._quote}"
            mark = self._portfolio.state.mark_prices.get(symbol.upper())
            if mark is None or mark <= 0:
                st = self._trail.get(key) or {}
                try:
                    mark = Decimal(str(st.get("mark") or 0))
                except Exception:  # noqa: BLE001
                    mark = _ZERO
            if mark <= 0:
                continue
            notional = qty * mark
            if notional < Decimal("10"):
                continue
            if mark < be:
                underwater += 1
        if underwater >= self._buy_quality_underwater_count:
            self._buy_quality_pause_until = (
                time.monotonic() + self._buy_quality_pause_sec
            )
            self._bump_skip("buy_quality_pause")
            self._push_alert(
                "buy_quality_pause",
                f"{underwater} recent session bags underwater — "
                f"new buys paused {self._buy_quality_pause_sec / 60:.0f}m",
            )

    def _is_underwater_on_other_venue(self, venue: str, base: str) -> bool:
        """True when base is held below BE on a different execute venue."""
        if not self._block_underwater_cross_venue:
            return False
        b = str(base or "").upper()
        if not b or self._is_long_hold(b):
            return False
        venue_l = venue.strip().lower()
        for other in self._execute_venues:
            if other == venue_l:
                continue
            qty = self._balance_qty(other, b)
            if qty <= 0:
                continue
            be = self._break_even_sell_price(other, b)
            if be is None or be <= 0:
                continue
            symbol = f"{b}{self._quote}"
            mark = self._portfolio.state.mark_prices.get(symbol.upper())
            if mark is None or mark <= 0:
                st = self._trail.get(self._lots_key(other, b)) or {}
                try:
                    mark = Decimal(str(st.get("mark") or 0))
                except Exception:  # noqa: BLE001
                    mark = _ZERO
            if mark > 0 and mark < be and qty * mark >= Decimal("10"):
                return True
        return False

    def _momentum_down(self, symbol: str) -> bool:
        """True when rolling mark return is at/below the negative momentum floor."""
        if not self._momentum_enabled:
            return False
        series = self._series_for(symbol)
        need = max(3, min(6, self._momentum_samples // 2))
        if len(series) < need:
            return False
        mom = series.momentum_return()
        if mom is None:
            return False
        return mom <= -self._momentum_exit_min

    _TRAIL_HOLD_RISING_REASONS = frozenset(
        {
            "trail_soft_partial",
            "trail_hard_partial",
            "trail_be_harvest",
            "trail_exit_work",
        }
    )

    def _momentum_still_rising(self, symbol: str) -> bool:
        """True when the last N marks are still climbing (growth tape)."""
        rising_n = self._trail_hold_rising_n
        if rising_n <= 0:
            return False
        return self._series_for(symbol).last_n_rising(rising_n)

    def _defer_harvest_while_rising(
        self,
        symbol: str,
        *,
        mark: Decimal,
        be: Decimal | None,
        st: dict[str, Any],
        reason: str,
    ) -> bool:
        """Defer BE+ harvests while price momentum is still rising (wait for pullback)."""
        if not self._trail_hold_while_rising:
            return False
        if reason not in self._TRAIL_HOLD_RISING_REASONS:
            return False
        if st.get("triggered"):
            return False
        if be is None or mark < be:
            return False
        if self._momentum_still_rising(symbol):
            return True
        if self._adaptive_trail_enabled and self._mfe_analytics_enabled:
            marks = self.mark_history(symbol)
            ext_raw = st.get("entry_extension_pct")
            cont_raw = st.get("entry_continuity")
            hr_raw = st.get("entry_headroom_pct")
            try:
                ext = Decimal(str(ext_raw)) if ext_raw is not None else None
            except Exception:  # noqa: BLE001
                ext = None
            try:
                cont = Decimal(str(cont_raw)) if cont_raw is not None else None
            except Exception:  # noqa: BLE001
                cont = None
            try:
                hr = Decimal(str(hr_raw)) if hr_raw is not None else None
            except Exception:  # noqa: BLE001
                hr = None
            if adaptive_trail_should_hold(
                symbol=symbol,
                marks=marks,
                extension_pct=ext,
                continuity=cont,
                headroom_pct=hr,
                enabled=True,
            ):
                self._economic_diagnostics.adaptive_trail_hold += 1
                return True
        return False

    def _is_new_base_buy(self, venue: str, base: str) -> bool:
        return base.upper() not in self._held_alt_bases(venue)

    def _is_cross_venue_duplicate_base(self, venue: str, base: str) -> bool:
        """True when opening a base already held/claimed on another execute venue."""
        if not self._block_cross_venue_duplicate_bases:
            return False
        b = str(base or "").upper()
        if not b or b in self._exclude_bases or self._is_long_hold(b):
            return False
        if b in self._bases_claimed_for_cross_venue(venue):
            return False  # same-venue top-up / add / own resting
        return b in self._bases_claimed_for_cross_venue(None)

    def _duplicate_bases_by_venue(self) -> dict[str, list[str]]:
        """Bases claimed on more than one execute venue → venue list."""
        by_base: dict[str, set[str]] = {}
        for v in sorted(self._execute_venues):
            for b in self._bases_claimed_for_cross_venue(v):
                by_base.setdefault(b, set()).add(v)
        return {
            b: sorted(vs) for b, vs in sorted(by_base.items()) if len(vs) > 1
        }

    def _primary_venue_for_base(self, base: str) -> str | None:
        dupes = self._duplicate_bases_by_venue()
        b = str(base or "").upper()
        venues = dupes.get(b)
        if not venues:
            return None
        primary = self._consolidate_primary.strip().lower()
        if primary in venues:
            return primary
        return venues[0]

    def _is_consolidation_secondary(self, venue: str, base: str) -> bool:
        """True when this venue should wind down a duplicate base (sell-only)."""
        if not self._consolidate_duplicates:
            return False
        primary = self._primary_venue_for_base(base)
        if primary is None:
            return False
        return venue.strip().lower() != primary

    def _trail_partial_qty(
        self,
        *,
        cap: Decimal,
        partial_pct: Decimal,
        mark: Decimal,
        notional_floor: Decimal,
    ) -> Decimal:
        """Scale partial qty up to meet maker min-notional when possible."""
        if cap <= 0 or mark <= 0 or partial_pct <= 0:
            return _ZERO
        qty = (cap * partial_pct).quantize(Decimal("0.00000001"))
        if qty * mark >= notional_floor:
            return qty
        need = (notional_floor / mark).quantize(Decimal("0.00000001"))
        if need > cap:
            return cap
        return need

    def _partial_done_key(self, reason: str) -> str | None:
        return {
            "trail_soft_partial": "soft_partial_done",
            "trail_hard_partial": "hard_partial_done",
            "trail_be_harvest": "be_harvest_partial_done",
            "trail_recovery_be_partial": "be_harvest_partial_done",
            "trail_consolidation_wind_down": "consolidation_wind_down_done",
        }.get(reason)

    def _clear_partial_done(self, st: dict[str, Any], reason: str) -> None:
        key = self._partial_done_key(reason)
        if key:
            st[key] = False
            if key == "soft_partial_done":
                st["partial_done"] = False
        if reason == "trail_drawdown":
            st["triggered"] = False

    def _set_partial_done(self, st: dict[str, Any], reason: str) -> None:
        key = self._partial_done_key(reason)
        if key:
            st[key] = True
            if key == "soft_partial_done":
                st["partial_done"] = True
        if reason in {"trail_be_harvest", "trail_recovery_be_partial"}:
            st["recovery_be_partial_done"] = True

    def _be_harvest_already_done(self, st: dict[str, Any]) -> bool:
        return bool(
            st.get("be_harvest_partial_done") or st.get("recovery_be_partial_done")
        )

    def _bump_exit_stat(self, bucket: dict[str, int], key: str) -> None:
        bucket[key] = int(bucket.get(key, 0) or 0) + 1

    def _exit_cooldown_sec(self, reason: str) -> float:
        if reason in {
            "trail_be_harvest",
            "trail_recovery_be_partial",
            "trail_soft_partial",
            "trail_hard_partial",
            "trail_momentum_be_exit",
            "trail_exit_work",
        }:
            if self._exit_engine_enabled:
                return min(self._be_harvest_cooldown, self._exit_engine_cooldown_sec)
            return self._be_harvest_cooldown
        if self._exit_engine_enabled and reason.startswith("trail_"):
            return self._exit_engine_cooldown_sec
        return 45.0

    def _soft_partial_would_fire(
        self,
        st: dict[str, Any],
        *,
        gain_now: Decimal,
        soft_arm_now: Decimal,
    ) -> bool:
        return bool(
            st.get("soft_armed")
            and not st.get("recovery_armed")
            and self._trail_partial_enabled
            and self._soft_partial > 0
            and not st.get("soft_partial_done")
            and gain_now >= soft_arm_now
        )

    def _buy_clip_cap_eur(self, venue: str, base: str) -> Decimal | None:
        """Entry clip; winner-adds use the dedicated winner clip size."""
        if (
            self._winner_add_enabled
            and not self._is_new_base_buy(venue, base)
            and self._winner_add_eligible(venue, base, require_soft_arm=True)
        ):
            return self._winner_add_clip_eur if self._winner_add_clip_eur > 0 else None
        first = self._first_clip_eur
        if first <= 0:
            add = self._add_clip_eur
            return add if add > 0 else None
        return first

    def _winner_add_eligible(
        self,
        venue: str,
        base: str,
        *,
        require_soft_arm: bool = True,
        mark: Decimal | None = None,
        be: Decimal | None = None,
    ) -> bool:
        """True when we may scale into a soft-armed BE+ bag."""
        if not self._winner_add_enabled or self._winner_add_max <= 0:
            return False
        if self._buys_blocked or self._sleeve_paused or self._daily_kill_active:
            return False
        if self._is_long_hold(base) or self._base_underwater_blocked(venue, base):
            return False
        trail_key = self._lots_key(venue, base)
        st = self._trail.get(trail_key) or {}
        if require_soft_arm and not st.get("soft_armed"):
            return False
        if st.get("triggered") or st.get("recovery_armed"):
            return False
        if int(st.get("winner_add_count") or 0) >= self._winner_add_max:
            return False
        last = float(st.get("winner_add_last_mono") or 0)
        if last > 0 and (time.monotonic() - last) < self._winner_add_cooldown_sec:
            return False
        if be is None:
            be = self._break_even_sell_price(venue, base)
        if be is None:
            return False
        if mark is None:
            symbol = f"{base.upper()}{self._quote}"
            mark = self._portfolio.state.mark_prices.get(symbol.upper())
        if mark is None or mark <= 0 or mark < be:
            return False
        return True

    async def _maybe_submit_winner_add(
        self,
        *,
        venue: str,
        base: str,
        symbol: str,
        mark: Decimal,
        be: Decimal | None,
        st: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Bridge-submitted scale-in on soft-armed BE+ winners (B3)."""
        if not self._winner_add_eligible(
            venue, base, require_soft_arm=True, mark=mark, be=be
        ):
            return None
        if self._resting_buys_for(venue, symbol) >= 1:
            return None
        clip = self._winner_add_clip_eur
        if clip < _MIN_LIVE_NOTIONAL:
            return None
        live_eur = await self._live_free(venue, self._quote)
        spend = min(clip, live_eur, self._venue_budget_remaining(venue))
        if spend < _MIN_LIVE_NOTIONAL:
            self._bump_skip("winner_add_no_quote")
            return None
        # Prefer touch bid slightly under mark (maker); still above adverse floor.
        px = (mark * Decimal("0.9995")).quantize(Decimal("0.00000001"))
        if px <= 0:
            return None
        qty = (spend / px).quantize(Decimal("0.00000001"))
        if qty <= 0:
            return None
        req = OrderRequest(
            opportunity_id=uuid4(),
            symbol=symbol,
            side=OpportunitySide.BUY,
            quantity=qty,
            limit_price=px,
            metadata={
                "venue": venue,
                "exchange": venue,
                "post_only": True,
                "winner_add": True,
                "strategy": "winner_add",
            },
        )
        result = await self.execute(
            req, strategy="winner_add", order_type=OrderType.LIMIT
        )
        st["winner_add_last_mono"] = time.monotonic()
        if result.status in {
            OrderStatus.SUBMITTED,
            OrderStatus.PENDING,
            OrderStatus.OPEN,
            OrderStatus.FILLED,
            OrderStatus.PARTIALLY_FILLED,
        }:
            st["winner_add_count"] = int(st.get("winner_add_count") or 0) + 1
            self._bump_skip("winner_add_submitted")
            logger.info(
                "WINNER_ADD venue=%s base=%s qty=%s px=%s mark=%s status=%s count=%s",
                venue,
                base,
                qty,
                px,
                mark,
                result.status.value,
                st.get("winner_add_count"),
            )
            return {
                "action": "winner_add",
                "base": base,
                "status": str(result.status.value),
                "qty": str(qty),
                "price": str(px),
            }
        self._bump_skip("winner_add_rejected")
        return {
            "action": "winner_add_rejected",
            "base": base,
            "status": str(result.status.value),
        }

    @staticmethod
    def _is_trail_mark_spike(
        *,
        prev_mark: Decimal,
        mark: Decimal,
        cost: Decimal,
        soft_arm: Decimal,
    ) -> bool:
        """True when a print looks like a bad ticker spike (arming filter)."""
        if mark <= 0:
            return False
        if prev_mark > 0 and mark >= prev_mark * Decimal("1.08"):
            return True
        if cost > 0:
            gain = (mark - cost) / cost
            if gain >= max(soft_arm * Decimal("5"), Decimal("0.10")):
                return True
        return False

    @staticmethod
    def _is_trail_peak_spike(*, prev_mark: Decimal, mark: Decimal) -> bool:
        """One-tick jump too large to trust as a new trail peak."""
        return prev_mark > 0 and mark >= prev_mark * Decimal("1.08")

    def _maybe_raise_trail_peak(
        self,
        st: dict[str, Any],
        *,
        base: str,
        cost: Decimal,
        mark: Decimal,
        prev_mark: Decimal,
        soft_arm: Decimal,
        peak: Decimal,
    ) -> Decimal:
        """Raise peak only on believable marks — never on one-tick spikes."""
        del soft_arm  # arming uses soft_arm; peak raises use one-tick filter only
        if mark <= peak:
            return peak
        if self._is_trail_peak_spike(prev_mark=prev_mark, mark=mark):
            logger.warning(
                "TRAIL_PEAK_SPIKE_IGNORED base=%s cost=%s prev=%s mark=%s peak=%s",
                base,
                cost,
                prev_mark,
                mark,
                peak,
            )
            self._bump_skip("trail_peak_spike")
            return peak
        st["peak"] = mark
        return mark

    def _sanitize_trail_peak(
        self,
        st: dict[str, Any],
        *,
        base: str,
        cost: Decimal,
        mark: Decimal,
        soft_arm: Decimal,
        hard_arm: Decimal,
        active_dd: Decimal,
    ) -> Decimal:
        """Rewind ghost peaks that keep trail permanently triggered under BE."""
        peak = Decimal(str(st.get("peak") or 0))
        if peak <= 0 or cost <= 0 or mark <= 0:
            return peak
        changed = False
        max_gain = max(hard_arm * Decimal("4"), Decimal("0.25"))
        max_peak = cost * (Decimal("1") + max_gain)
        if peak > max_peak:
            peak = max_peak
            changed = True
        # Peak so far above mark that trigger is sticky / exit would be < BE.
        sticky = peak > mark * (Decimal("1") + active_dd + Decimal("0.02"))
        if sticky and mark < cost * (Decimal("1") + soft_arm):
            peak = mark
            changed = True
        if changed:
            st["peak"] = peak
            self._bump_skip("trail_peak_rewound")
            logger.warning(
                "TRAIL_PEAK_REWOUND base=%s cost=%s mark=%s peak=%s",
                base,
                cost,
                mark,
                peak,
            )
        return peak

    def _sanitize_persisted_trails(self) -> None:
        """Clamp polluted peaks loaded from disk before the first live cycle."""
        for trail_key, st in list(self._trail.items()):
            if not isinstance(st, dict):
                continue
            try:
                cost = Decimal(str(st.get("cost") or 0))
                mark = Decimal(str(st.get("last_mark") or 0))
            except Exception:  # noqa: BLE001
                continue
            if cost <= 0 or mark <= 0:
                continue
            soft_arm, soft_dd, hard_arm, hard_dd = self._scaled_arms(
                str(st.get("base") or trail_key.split(":")[-1]), cost
            )
            active_dd = hard_dd if st.get("hard_armed") else soft_dd
            self._sanitize_trail_peak(
                st,
                base=str(st.get("base") or ""),
                cost=cost,
                mark=mark,
                soft_arm=soft_arm,
                hard_arm=hard_arm,
                active_dd=active_dd,
            )

    def _trail_update_state(
        self, venue: str, base: str, *, cost: Decimal, mark: Decimal
    ) -> dict[str, Any]:
        """Soft/hard arm vs session cost; ATR-scaled; peak drawdown trigger."""
        trail_key = self._lots_key(venue, base)
        soft_arm, soft_dd, hard_arm, hard_dd = self._scaled_arms(base, cost)
        atr = self._series_for(f"{base.upper()}{self._quote}").atr_pct()
        st = self._trail.setdefault(
            trail_key,
            {
                "venue": venue,
                "base": base.upper(),
                "soft_armed": False,
                "hard_armed": False,
                "armed": False,
                "peak": _ZERO,
                "cost": cost,
                "last_mark": mark,
                "triggered": False,
                "newly_soft": False,
                "newly_hard": False,
                "newly_armed": False,
                "soft_partial_done": False,
                "hard_partial_done": False,
                "recovery_be_partial_done": False,
                "be_harvest_partial_done": False,
                "consolidation_wind_down_done": False,
                "partial_done": False,
                "time_stop_due": False,
                "recovery_armed": False,
                "below_be": False,
                "new_session_base": False,
                "soft_arm": str(soft_arm),
                "hard_arm": str(hard_arm),
                "drawdown": str(soft_dd),
                "atr": str(atr),
                "session_qty": str(self._session_qty(venue, base)),
            },
        )
        st["venue"] = venue
        st["base"] = base.upper()
        st["cost"] = cost
        prev_mark = Decimal(str(st.get("last_mark") or 0))
        st["last_mark"] = mark
        st["soft_arm"] = str(soft_arm)
        st["hard_arm"] = str(hard_arm)
        st["atr"] = str(atr)
        st["session_qty"] = str(self._session_qty(venue, base))
        if self._mfe_analytics_enabled and mark > 0:
            mfe_px = Decimal(str(st.get("mfe_price") or mark))
            mae_px = Decimal(str(st.get("mae_price") or mark))
            if mark > mfe_px:
                st["mfe_price"] = str(mark)
            if mae_px <= 0 or mark < mae_px:
                st["mae_price"] = str(mark)
        st["newly_soft"] = False
        st["newly_hard"] = False
        st["newly_armed"] = False
        st["time_stop_due"] = False
        st["triggered"] = False
        if cost <= 0 or mark <= 0:
            return st
        gain = (mark - cost) / cost
        st["gain"] = str(gain)

        # Reject one-tick mark spikes (bad ticker/print) before arming trail.
        mark_spike = self._is_trail_mark_spike(
            prev_mark=prev_mark, mark=mark, cost=cost, soft_arm=soft_arm
        )
        if mark_spike and not st.get("soft_armed") and gain >= soft_arm:
            logger.warning(
                "TRAIL_MARK_SPIKE_IGNORED base=%s cost=%s prev=%s mark=%s gain=%.2f%%",
                base,
                cost,
                prev_mark,
                mark,
                float(gain * 100),
            )
            self._bump_skip("trail_mark_spike")

        if not st.get("soft_armed") and not mark_spike:
            be = self._break_even_sell_price(venue, base)
            at_net_profit = (
                be is not None
                and mark >= be
                and self._has_trusted_cost(venue, base)
            )
            if gain >= soft_arm or at_net_profit:
                st["soft_armed"] = True
                st["armed"] = True
                st["peak"] = mark
                st["newly_soft"] = True
                st["newly_armed"] = True
                # One BE-harvest attempt per soft-arm cycle (no 15s chunk spam).
                st["be_harvest_partial_done"] = False
                st["recovery_be_partial_done"] = False
                st["drawdown"] = str(soft_dd)
                arm_label = (
                    "net-profit"
                    if at_net_profit and gain < soft_arm
                    else f"+{float(gain * 100):.1f}%"
                )
                self._push_alert(
                    "soft_arm",
                    f"{base} soft-arm {arm_label} "
                    f"(trail dd {float(soft_dd * 100):.1f}% from peak)",
                    base=base,
                )
                logger.info(
                    "TRAIL_SOFT_ARM base=%s cost=%s mark=%s gain=%.2f%% arm=%.2f%% "
                    "net_profit=%s",
                    base,
                    cost,
                    mark,
                    float(gain * 100),
                    float(soft_arm * 100),
                    at_net_profit,
                )
        elif not st.get("soft_armed") and soft_arm > 0:
            to_arm = soft_arm - gain
            if 0 < to_arm <= self._alert_pct_to_arm:
                self._push_alert(
                    "near_soft_arm",
                    f"{base} near soft-arm gain={float(gain * 100):.1f}% "
                    f"need={float(soft_arm * 100):.0f}%",
                    base=base,
                )

        if st.get("soft_armed") and not st.get("hard_armed") and gain >= hard_arm:
            peak_spike = self._is_trail_peak_spike(prev_mark=prev_mark, mark=mark)
            if peak_spike:
                logger.warning(
                    "TRAIL_HARD_ARM_SPIKE_IGNORED base=%s prev=%s mark=%s",
                    base,
                    prev_mark,
                    mark,
                )
                self._bump_skip("trail_peak_spike")
            else:
                st["hard_armed"] = True
                st["newly_hard"] = True
                st["drawdown"] = str(hard_dd)
                peak_now = Decimal(str(st.get("peak") or 0))
                self._maybe_raise_trail_peak(
                    st,
                    base=base,
                    cost=cost,
                    mark=mark,
                    prev_mark=prev_mark,
                    soft_arm=soft_arm,
                    peak=peak_now,
                )
                self._push_alert(
                    "hard_arm",
                    f"{base} hard-arm +{float(gain * 100):.1f}%",
                    base=base,
                )
                logger.info(
                    "TRAIL_HARD_ARM base=%s cost=%s mark=%s gain=%.2f%% arm=%.2f%%",
                    base,
                    cost,
                    mark,
                    float(gain * 100),
                    float(hard_arm * 100),
                )

        if not st.get("soft_armed"):
            if self._time_stop_enabled:
                opened = self._position_opened_mono.get(trail_key)
                if opened is not None and (
                    time.monotonic() - opened >= self._time_stop_sec
                ):
                    st["time_stop_due"] = True
            return st

        peak = Decimal(str(st.get("peak") or 0))
        peak = self._maybe_raise_trail_peak(
            st,
            base=base,
            cost=cost,
            mark=mark,
            prev_mark=prev_mark,
            soft_arm=soft_arm,
            peak=peak,
        )
        active_dd = hard_dd if st.get("hard_armed") else soft_dd
        peak = self._sanitize_trail_peak(
            st,
            base=base,
            cost=cost,
            mark=mark,
            soft_arm=soft_arm,
            hard_arm=hard_arm,
            active_dd=active_dd,
        )
        st["drawdown"] = str(active_dd)
        # Never arm a drawdown exit while still below unit cost — never-loss
        # would reject the sell and only spam trail_fire / BE skips.
        if (
            peak > 0
            and mark >= cost
            and mark <= peak * (Decimal("1") - active_dd)
        ):
            st["triggered"] = True
            self._push_alert(
                "trail_fire",
                f"{base} trail fire peak={peak} mark={mark} "
                f"dd={float(active_dd * 100):.1f}%",
                base=base,
            )
            logger.info(
                "TRAIL_TRIGGER base=%s cost=%s peak=%s mark=%s dd=%.2f%% hard=%s",
                base,
                cost,
                peak,
                mark,
                float((Decimal("1") - mark / peak) * 100),
                bool(st.get("hard_armed")),
            )
        return st

    async def _mark_price(self, venue: str, symbol: str) -> Decimal | None:
        mark = self._portfolio.state.mark_prices.get(symbol)
        now = time.monotonic()
        fetched_at = self._mark_fetched_at.get(symbol, 0.0)
        if mark is not None and mark > 0 and now - fetched_at < self._mark_ttl_sec:
            m = Decimal(str(mark))
            self._series_for(symbol).push(m)
            return m
        client = self._trading_client(venue)
        if client is None:
            if mark and mark > 0:
                m = Decimal(str(mark))
                self._series_for(symbol).push(m)
                return m
            return None
        try:
            ticker = await client.fetch_ticker(symbol)
            mark = Decimal(str(ticker.last or ticker.bid or ticker.ask or 0))
            if mark > 0:
                self._portfolio.set_mark_price(symbol, mark)
                self._mark_fetched_at[symbol] = now
                self._series_for(symbol).push(Decimal(str(mark)))
                return mark
        except Exception:  # noqa: BLE001
            pass
        if mark is not None and mark > 0:
            m = Decimal(str(mark))
            self._series_for(symbol).push(m)
            return m
        return None

    async def _cancel_resting_for_symbol(self, venue: str, symbol: str) -> int:
        client = self._trading_client(venue)
        if client is None:
            return 0
        cancelled = 0
        still: list[dict[str, Any]] = []
        venue_l = venue.strip().lower()
        for row in list(self._resting):
            row_venue = str(row.get("venue") or "").strip().lower()
            if row_venue and row_venue != venue_l:
                still.append(row)
                continue
            if str(row.get("symbol") or "").upper() != symbol.upper():
                still.append(row)
                continue
            oid = str(row.get("exchange_order_id") or "")
            if not oid:
                continue
            try:
                await client.cancel_order(oid, symbol)
                cancelled += 1
                self._invalidate_bal_cache()
            except Exception:  # noqa: BLE001
                still.append(row)
        self._resting = still
        return cancelled

    def _max_order_notional_eur(self) -> Decimal:
        raw = getattr(self._settings, "live_micro_max_notional_eur", 150) or 150
        try:
            val = Decimal(str(raw))
        except Exception:  # noqa: BLE001
            val = Decimal("150")
        return val if val > 0 else Decimal("150")

    def _clip_qty_to_max_notional(
        self, qty: Decimal, price: Decimal
    ) -> tuple[Decimal, bool]:
        """Cap exit size so policy max-notional cannot reject profitable sells.

        Returns (clipped_qty, was_clipped).
        """
        if qty <= 0 or price <= 0:
            return qty, False
        max_n = self._max_order_notional_eur()
        notional = qty * price
        if notional <= max_n:
            return qty, False
        clipped = (max_n / price).quantize(Decimal("0.00000001"))
        if clipped <= 0:
            return _ZERO, True
        return clipped, True

    async def _submit_exit_sell(
        self,
        *,
        venue: str,
        symbol: str,
        qty: Decimal,
        mark: Decimal,
        reason: str,
        limit_price: Decimal | None = None,
        post_only: bool = False,
    ) -> ExecutionResult:
        px = limit_price
        if px is None or px <= 0:
            px = (mark * Decimal("0.998")).quantize(Decimal("0.00000001"))
        req = OrderRequest(
            opportunity_id=uuid4(),
            symbol=symbol,
            side=OpportunitySide.SELL,
            quantity=qty,
            limit_price=px,
            metadata={
                "venue": venue,
                "exchange": venue,
                "trail_take_profit": True,
                "post_only": post_only,
                "strategy": reason,
                "exit_reason": reason,
            },
        )
        return await self.execute(
            req, strategy=reason, order_type=OrderType.LIMIT
        )

    async def _refresh_free(
        self, venue: str, symbol: str, asset: str, locked: Decimal
    ) -> Decimal:
        if locked > 0:
            await self._cancel_resting_for_symbol(venue, symbol)
            self._invalidate_bal_cache(venue)
        return await self._live_free(venue, asset)

    async def check_trailing_take_profits(
        self, venue: str = "bitvavo"
    ) -> dict[str, Any]:
        """Soft/hard trail + recovery-arm at BE (no flat BE dump after time-stop)."""
        if not self._trail_enabled and not self._time_stop_enabled:
            return {"ok": True, "enabled": False, "triggered": []}
        venue = venue.strip().lower()
        bals = await self._fetch_balances_cached(venue)
        triggered: list[dict[str, Any]] = []
        armed_now: list[str] = []
        for bal in bals:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if not asset or asset == self._quote:
                continue
            if asset in self._exclude_bases:
                continue
            if self._is_long_hold(asset):
                symbol = f"{asset}{self._quote}"
                mark = await self._mark_price(venue, symbol)
                cost = self._unit_cost(venue, asset)
                if mark is not None and cost is not None and cost > 0 and mark > 0:
                    self._trail_update_state(venue, asset, cost=cost, mark=mark)
                # Long-hold: do not stop the function early.
                # Exits still go through the never-loss / break-even gates.
            if self._allowed_bases is not None and asset not in self._allowed_bases:
                continue
            trail_key = self._lots_key(venue, asset)
            free = Decimal(str(getattr(bal, "free", 0) or 0))
            locked = Decimal(str(getattr(bal, "locked", 0) or 0))
            if free + locked <= 0:
                self._trail.pop(trail_key, None)
                self._position_opened_mono.pop(trail_key, None)
                self._session_lots.pop(trail_key, None)
                continue

            # Trail only on session buys (not mark-seeded pre-session bags).
            cost = (
                self._session_unit_cost(venue, asset)
                if self._trail_session_only
                else self._unit_cost(venue, asset)
            )
            session_qty = (
                self._session_qty(venue, asset)
                if self._trail_session_only
                else (free + locked)
            )
            if cost is None or cost <= 0 or session_qty <= 0:
                # Aged bags with trusted cost: allow recovery-arm / trail exits even
                # when session lots are empty (never dump flat at BE).
                blend = self._unit_cost(venue, asset)
                if not (
                    self._time_stop_enabled
                    and blend is not None
                    and blend > 0
                    and self._has_trusted_cost(venue, asset)
                    and (free + locked) > 0
                ):
                    continue
                self._note_position_opened(venue, asset)
                opened = self._position_opened_mono.get(trail_key)
                if opened is None or (
                    time.monotonic() - opened < self._time_stop_sec
                ):
                    continue
                cost = blend
                session_qty = free + locked

            # Untrusted / mark-seeded cost must never arm or exit a trail.
            if not self._has_trusted_cost(venue, asset):
                self._bump_skip("trail_no_trusted_cost")
                continue

            symbol = f"{asset}{self._quote}"
            mark = await self._mark_price(venue, symbol)
            if mark is None or mark <= 0:
                continue
            self._note_position_opened(venue, asset)
            st = self._trail_update_state(venue, asset, cost=cost, mark=mark)
            be = self._break_even_sell_price(venue, asset)

            # Loss → BE (rising through): arm recovery trail, do not sell yet.
            self._maybe_recovery_arm_from_loss(
                st, venue=venue, base=asset, mark=mark, be=be
            )

            # Aged bag finally at/above BE → recovery-arm trail (let it grow).
            if (
                st.get("time_stop_due")
                and be is not None
                and mark >= be
                and not st.get("recovery_armed")
            ):
                self._recovery_arm_trail(
                    st, venue=venue, base=asset, mark=mark, be=be
                )
                continue
            if st.get("time_stop_due") and (be is None or mark < be):
                cut_floor_early = self._cut_loss_floor_price(venue, asset)
                if not (
                    cut_floor_early is not None
                    and self._cut_loss_eligible(st, venue=venue, base=asset)
                    and mark <= cut_floor_early
                ):
                    self._bump_skip("time_stop_below_be")
                    continue

            if st.get("soft_armed") and not st.get("triggered"):
                armed_now.append(f"{venue}:{asset}")

            sell_qty = _ZERO
            reason = ""
            limit_px: Decimal | None = None
            post_only = False

            soft_arm_now = Decimal(str(st.get("soft_arm") or self._soft_arm_floor))
            gain_now = Decimal(str(st.get("gain") or 0))
            early_floor = self._early_cut_loss_floor_price(venue, asset)
            if (
                early_floor is not None
                and self._early_cut_eligible(st, venue=venue, base=asset)
                and be is not None
                and mark < be
                and self._momentum_enabled
                and self._momentum_flat_or_down(symbol)
                and mark <= early_floor
            ):
                free = await self._refresh_free(venue, symbol, asset, locked)
                sell_qty = min(
                    free,
                    self._session_qty(venue, asset)
                    if self._trail_session_only
                    else free,
                )
                reason = "trail_early_cut_loss"
            cut_floor = self._cut_loss_floor_price(venue, asset)
            if (
                not reason
                and cut_floor is not None
                and self._cut_loss_eligible(st, venue=venue, base=asset)
                and mark <= cut_floor
            ):
                free = await self._refresh_free(venue, symbol, asset, locked)
                sell_qty = min(
                    free,
                    self._session_qty(venue, asset)
                    if self._trail_session_only
                    else free,
                )
                reason = "trail_cut_loss"
            elif (
                not reason
                and be is not None
                and self._momentum_enabled
                and self._momentum_down(symbol)
                and not st.get("momentum_be_exit_done")
            ):
                mom_target = self._momentum_exit_target_price(venue, asset)
                if mom_target is not None and mark >= be:
                    free = await self._refresh_free(venue, symbol, asset, locked)
                    sell_qty = min(
                        free,
                        self._session_qty(venue, asset)
                        if self._trail_session_only
                        else free,
                    )
                    reason = "trail_momentum_be_exit"
                    limit_px = mom_target if mark >= mom_target else None
            elif (
                st.get("soft_armed")
                and not st.get("recovery_armed")
                and self._trail_partial_enabled
                and self._soft_partial > 0
                and not st.get("soft_partial_done")
                and gain_now >= soft_arm_now
            ):
                # Retry every cycle until a soft partial lands (not only the
                # arming tick — large bags used to fail max-notional once and
                # never retry because newly_soft is one-shot).
                # soft_partial=0 → skip; full bag waits for soft/hard drawdown exit.
                # Recovery-from-loss bags never soft-partial at BE; they ride for
                # profit and only floor-exit on pullback to BE / trail drawdown.
                free = await self._refresh_free(venue, symbol, asset, locked)
                cap = min(
                    free,
                    self._session_qty(venue, asset)
                    if self._trail_session_only
                    else free,
                )
                maker_min = Decimal(
                    str(getattr(self._settings, "paper_maker_min_notional_eur", 10) or 10)
                )
                partial_min = max(
                    _MIN_LIVE_NOTIONAL,
                    maker_min * self._trail_partial_min_frac,
                )
                sell_qty = self._trail_partial_qty(
                    cap=cap,
                    partial_pct=self._soft_partial,
                    mark=mark,
                    notional_floor=partial_min,
                )
                reason = "trail_soft_partial"
            elif (
                st.get("hard_armed")
                and self._trail_partial_enabled
                and not st.get("hard_partial_done")
            ):
                free = await self._refresh_free(venue, symbol, asset, locked)
                cap = min(
                    free,
                    self._session_qty(venue, asset)
                    if self._trail_session_only
                    else free,
                )
                maker_min = Decimal(
                    str(getattr(self._settings, "paper_maker_min_notional_eur", 10) or 10)
                )
                partial_min = max(
                    _MIN_LIVE_NOTIONAL,
                    maker_min * self._trail_partial_min_frac,
                )
                sell_qty = self._trail_partial_qty(
                    cap=cap,
                    partial_pct=self._hard_partial,
                    mark=mark,
                    notional_floor=partial_min,
                )
                reason = "trail_hard_partial"
            elif (
                self._consolidate_duplicates
                and self._is_consolidation_secondary(venue, asset)
                and be is not None
                and mark >= be
                and not st.get("consolidation_wind_down_done")
            ):
                # Wind down OKX duplicate at BE+; Bitvavo remains primary bag.
                free = await self._refresh_free(venue, symbol, asset, locked)
                cap = min(
                    free,
                    self._session_qty(venue, asset)
                    if self._trail_session_only
                    else free,
                )
                sell_qty = cap
                reason = "trail_consolidation_wind_down"
            elif st.get("triggered"):
                free = await self._refresh_free(venue, symbol, asset, locked)
                sell_qty = min(
                    free,
                    self._session_qty(venue, asset)
                    if self._trail_session_only
                    else free,
                )
                reason = "trail_drawdown"
            elif (
                be is not None
                and mark >= be
                and self._be_harvest_partial > 0
                and not self._be_harvest_already_done(st)
                and gain_now
                >= (
                    min(self._be_harvest_min_gain, Decimal("0.00015"))
                    if st.get("recovery_armed")
                    else self._be_harvest_min_gain
                )
                and not self._soft_partial_would_fire(
                    st, gain_now=gain_now, soft_arm_now=soft_arm_now
                )
            ):
                # Fee-positive harvest at BE+ (recovery bags, small MTM wins).
                # Recovery bags use a slightly lower min-gain so underwater→BE+
                # recycles capital before the next dip.
                free = await self._refresh_free(venue, symbol, asset, locked)
                cap = min(
                    free,
                    self._session_qty(venue, asset)
                    if self._trail_session_only
                    else free,
                )
                maker_min = Decimal(
                    str(getattr(self._settings, "paper_maker_min_notional_eur", 10) or 10)
                )
                partial_min = max(
                    _MIN_LIVE_NOTIONAL,
                    maker_min * self._trail_partial_min_frac,
                )
                sell_qty = self._trail_partial_qty(
                    cap=cap,
                    partial_pct=self._be_harvest_partial,
                    mark=mark,
                    notional_floor=partial_min,
                )
                reason = "trail_be_harvest"
            elif (
                self._exit_engine_enabled
                and self._exit_soft_armed_work
                and st.get("soft_armed")
                and be is not None
                and mark >= be
                and gain_now >= self._be_harvest_min_gain
                # B3: let soft partial / runner window run first; then work remainder.
                and (
                    self._soft_partial <= 0
                    or st.get("soft_partial_done")
                )
            ):
                # D: keep working BE+ inventory at touch while soft-armed
                # (do not wait for drawdown — spikes die in seconds).
                free = await self._refresh_free(venue, symbol, asset, locked)
                cap = min(
                    free,
                    self._session_qty(venue, asset)
                    if self._trail_session_only
                    else free,
                )
                maker_min = Decimal(
                    str(getattr(self._settings, "paper_maker_min_notional_eur", 10) or 10)
                )
                partial_min = max(
                    _MIN_LIVE_NOTIONAL,
                    maker_min * self._trail_partial_min_frac,
                )
                if self._exit_soft_armed_partial >= Decimal("1"):
                    sell_qty = cap
                else:
                    sell_qty = self._trail_partial_qty(
                        cap=cap,
                        partial_pct=self._exit_soft_armed_partial,
                        mark=mark,
                        notional_floor=partial_min,
                    )
                reason = "trail_exit_work"
            elif (
                st.get("recovery_armed")
                and be is not None
                and Decimal(str(st.get("peak") or 0)) > be
                and mark <= be
            ):
                # Grew above BE after recovery-arm, then fell back to BE → exit.
                free = await self._refresh_free(venue, symbol, asset, locked)
                sell_qty = min(
                    free,
                    self._session_qty(venue, asset)
                    if self._trail_session_only
                    else free,
                )
                reason = "trail_recovery_be"
                limit_px = max(be, mark * Decimal("0.999"))
                post_only = True
            else:
                # B3: no exit this tick → maybe scale into soft-armed BE+ winner.
                if not st.get("triggered"):
                    add = await self._maybe_submit_winner_add(
                        venue=venue,
                        base=asset,
                        symbol=symbol,
                        mark=mark,
                        be=be,
                        st=st,
                    )
                    if add is not None:
                        triggered.append(
                            {
                                "venue": venue,
                                "base": asset,
                                "reason": "winner_add",
                                "detail": add,
                            }
                        )
                continue

            if self._defer_harvest_while_rising(
                symbol, mark=mark, be=be, st=st, reason=reason
            ):
                self._bump_skip("trail_hold_rising")
                continue

            maker_min = Decimal(
                str(getattr(self._settings, "paper_maker_min_notional_eur", 10) or 10)
            )
            partial_min = max(
                _MIN_LIVE_NOTIONAL,
                maker_min * self._trail_partial_min_frac,
            )
            notional_floor = (
                partial_min
                if reason
                in {
                    "trail_soft_partial",
                    "trail_hard_partial",
                    "trail_be_harvest",
                    "trail_recovery_be_partial",
                    "trail_exit_work",
                }
                else _MIN_LIVE_NOTIONAL
            )
            # Sell the intended size in one order — do not slice to buy-side
            # max_notional (that only burns extra fees on exits).
            if sell_qty <= 0 or sell_qty * mark < notional_floor:
                self._bump_skip("trail_dust")
                continue

            if reason in {"trail_cut_loss", "trail_early_cut_loss"}:
                cut_floor = self._cut_loss_floor_for_reason(venue, asset, reason)
                exit_px, exit_post_only, quote_reason = await self._cut_loss_exit_quote(
                    venue, asset, mark, floor=cut_floor
                )
            else:
                # D: aggressive touch quotes for all profitable trail exits.
                force_taker = self._should_force_taker_exit(venue, asset)
                exit_px, exit_post_only, quote_reason = await self._profitable_exit_quote(
                    venue,
                    asset,
                    mark,
                    aggressive=self._exit_engine_enabled,
                    force_taker=force_taker,
                )
            if exit_px is None:
                self._bump_skip(f"exit_quote_{quote_reason}")
                continue

            if reason == "trail_momentum_be_exit":
                mom_target = self._momentum_exit_target_price(venue, asset)
                if mom_target is not None:
                    exit_px = max(exit_px, mom_target)
                    limit_px = max(limit_px or _ZERO, mom_target)

            if reason not in {"trail_cut_loss", "trail_early_cut_loss"}:
                ok_sell, gate_reason, be = self._sell_allowed_at(venue, asset, exit_px)
                if not ok_sell:
                    self._bump_skip(gate_reason)
                    continue

            cooldown_key = f"{venue}:{asset}:{reason}"
            last_try = self._exit_cooldown_mono.get(cooldown_key, 0.0)
            if time.monotonic() - last_try < self._exit_cooldown_sec(reason):
                self._bump_skip("exit_cooldown")
                continue

            if limit_px is None or limit_px < exit_px:
                limit_px = exit_px
            if reason in {"trail_cut_loss", "trail_early_cut_loss"}:
                limit_px = exit_px
                post_only = exit_post_only
            elif reason != "trail_recovery_be":
                limit_px = max(limit_px, exit_px)
                post_only = exit_post_only
            else:
                limit_px = max(limit_px, exit_px)
            if sell_qty <= 0 or sell_qty * limit_px < notional_floor:
                self._bump_skip("trail_dust")
                continue
            self._exit_cooldown_mono[cooldown_key] = time.monotonic()
            self._bump_exit_stat(self._exit_quote_counts, quote_reason)
            self._bump_exit_stat(self._exit_quote_counts, f"reason:{reason}")
            logger.info(
                "TRAIL_EXIT_QUOTE venue=%s base=%s reason=%s quote=%s px=%s post_only=%s qty=%s",
                venue,
                asset,
                reason,
                quote_reason,
                limit_px,
                post_only,
                sell_qty,
            )

            result = await self._submit_exit_sell(
                venue=venue,
                symbol=symbol,
                qty=sell_qty,
                mark=mark,
                reason=reason,
                limit_price=limit_px,
                post_only=post_only,
            )
            row = {
                "venue": venue,
                "base": asset,
                "symbol": symbol,
                "reason": reason,
                "quote": quote_reason,
                "qty": str(sell_qty),
                "mark": str(mark),
                "cost": str(cost),
                "status": result.status.value,
                "order_id": str(result.order_id) if result.order_id else None,
                "error": result.message,
            }
            triggered.append(row)
            if result.status == OrderStatus.REJECTED:
                self._bump_exit_stat(self._exit_reject_counts, quote_reason)
                self._bump_exit_stat(self._exit_reject_counts, f"reason:{reason}")
                if quote_reason in {"rest_touch_maker", "rest_maker_be"}:
                    self._bump_exit_maker_fail(venue, asset)
                if reason != "trail_be_harvest":
                    self._clear_partial_done(st, reason)
                self._bump_skip(f"{reason}_reject")
            elif reason == "trail_be_harvest":
                # Lock after submit — resting fills must not re-trigger 35% spam.
                self._set_partial_done(st, reason)
                status_l = str(result.status.value).lower()
                if status_l in {"filled", "partially_filled"}:
                    self._clear_exit_maker_fail(venue, asset)
                    self._bump_exit_stat(self._exit_fill_counts, quote_reason)
                    self._bump_exit_stat(self._exit_fill_counts, f"reason:{reason}")
                else:
                    self._bump_exit_stat(self._exit_pending_counts, quote_reason)
                    self._bump_exit_stat(self._exit_pending_counts, f"reason:{reason}")
                logger.info(
                    "TRAIL_EXIT venue=%s base=%s reason=%s qty=%s mark=%s status=%s quote=%s",
                    venue,
                    asset,
                    reason,
                    sell_qty,
                    mark,
                    result.status.value,
                    quote_reason,
                )
            elif result.status in {
                OrderStatus.FILLED,
                OrderStatus.PARTIALLY_FILLED,
            }:
                self._clear_exit_maker_fail(venue, asset)
                self._bump_exit_stat(self._exit_fill_counts, quote_reason)
                self._bump_exit_stat(self._exit_fill_counts, f"reason:{reason}")
                self._set_partial_done(st, reason)
                if reason == "trail_momentum_be_exit":
                    st["momentum_be_exit_done"] = True
                logger.info(
                    "TRAIL_EXIT venue=%s base=%s reason=%s qty=%s mark=%s status=%s quote=%s",
                    venue,
                    asset,
                    reason,
                    sell_qty,
                    mark,
                    result.status.value,
                    quote_reason,
                )
                if reason in {
                    "trail_drawdown",
                    "trail_recovery_be",
                    "trail_consolidation_wind_down",
                    "trail_cut_loss",
                    "trail_early_cut_loss",
                    "trail_momentum_be_exit",
                    "time_stop_breakeven",
                }:
                    # Full exit path — clear trail when remaining is dust.
                    rem_free = await self._live_free(venue, asset)
                    if rem_free * mark < notional_floor:
                        self._trail.pop(trail_key, None)
                        self._position_opened_mono.pop(trail_key, None)
                    else:
                        st["triggered"] = False
            else:
                self._bump_exit_stat(self._exit_pending_counts, quote_reason)
                self._bump_exit_stat(self._exit_pending_counts, f"reason:{reason}")
                logger.info(
                    "TRAIL_EXIT venue=%s base=%s reason=%s qty=%s mark=%s status=%s quote=%s",
                    venue,
                    asset,
                    reason,
                    sell_qty,
                    mark,
                    result.status.value,
                    quote_reason,
                )

        return {
            "ok": True,
            "enabled": self._trail_enabled,
            "venue": venue,
            "armed": armed_now,
            "triggered": triggered,
            "alerts": list(self._alerts[-10:]),
            "states": self._trail_states_public(),
        }

    async def manage_dust_positions(
        self, venue: str = "bitvavo"
    ) -> dict[str, Any]:
        """Top up sub-min positions, else exit near break-even; trim over-cap bags."""
        policy = self._dust_policy
        if policy in {"", "off", "none"}:
            return {"ok": True, "policy": policy, "actions": []}
        min_notional = Decimal(
            str(getattr(self._settings, "paper_maker_min_notional_eur", 40) or 40)
        )
        bals = await self._fetch_balances_cached(venue)
        actions: list[dict[str, Any]] = []
        sized: list[tuple[str, Decimal, Decimal, Decimal]] = []
        for bal in bals:
            asset = str(getattr(bal, "asset", "") or "").upper()
            if not asset or asset == self._quote or asset in self._exclude_bases:
                continue
            if self._allowed_bases is not None and asset not in self._allowed_bases:
                continue
            free = Decimal(str(getattr(bal, "free", 0) or 0))
            if free <= 0:
                continue
            symbol = f"{asset}{self._quote}"
            mark = await self._mark_price(venue, symbol)
            if mark is None or mark <= 0:
                continue
            notional = free * mark
            if notional < _MIN_LIVE_NOTIONAL:
                continue
            sized.append((asset, free, mark, notional))

        held = {a for a, _f, _m, n in sized if n >= min_notional * Decimal("0.5")}
        over_cap = self._max_alt_bases > 0 and len(held) > self._max_alt_bases

        for asset, free, mark, notional in sized:
            symbol = f"{asset}{self._quote}"
            need_eur = min_notional - notional
            is_sub_min = notional < min_notional
            is_trim_target = False
            if over_cap and sized:
                smallest = min(sized, key=lambda r: r[3])
                is_trim_target = asset == smallest[0] and notional <= (
                    min_notional * Decimal("2")
                )
            if not is_sub_min and not is_trim_target:
                continue

            did = None
            if (
                is_sub_min
                and policy in {"top_up", "top_up_or_exit"}
                and not self._buys_blocked
                and not self._base_underwater_blocked(venue, asset)
                and not self._daily_kill_active
            ):
                can_add = asset not in held and (
                    self._max_alt_bases <= 0 or len(held) < self._max_alt_bases
                )
                live_eur = await self._live_free(venue, self._quote)
                spend = min(
                    need_eur * Decimal("1.01"),
                    live_eur,
                    self._venue_budget_remaining(venue),
                )
                if can_add and spend >= _MIN_LIVE_NOTIONAL:
                    qty = (spend / mark).quantize(Decimal("0.00000001"))
                    px = (mark * Decimal("0.999")).quantize(Decimal("0.00000001"))
                    req = OrderRequest(
                        opportunity_id=uuid4(),
                        symbol=symbol,
                        side=OpportunitySide.BUY,
                        quantity=qty,
                        limit_price=px,
                        metadata={
                            "venue": venue,
                            "exchange": venue,
                            "post_only": True,
                            "dust_top_up": True,
                            "ladder_leg": True,
                            "strategy": "dust_top_up",
                        },
                    )
                    result = await self.execute(
                        req, strategy="dust_top_up", order_type=OrderType.LIMIT
                    )
                    did = {
                        "action": "top_up",
                        "base": asset,
                        "status": str(result.status),
                        "qty": str(qty),
                    }
                    self._bump_skip("dust_top_up")
            if did is None and policy in {"exit_breakeven", "top_up_or_exit"}:
                # No slack below break-even — user rule: always sell at a profit after fees.
                be = self._break_even_sell_price(venue, asset)
                floor = be
                if floor is not None and mark >= floor:
                    reason = (
                        "inventory_trim_breakeven"
                        if is_trim_target and not is_sub_min
                        else "dust_exit_breakeven"
                    )
                    result = await self._submit_exit_sell(
                        venue=venue,
                        symbol=symbol,
                        qty=free,
                        mark=mark,
                        reason=reason,
                        limit_price=max(be or mark, mark * Decimal("0.999")),
                        post_only=True,
                    )
                    did = {
                        "action": (
                            "inventory_trim"
                            if is_trim_target and not is_sub_min
                            else "exit_breakeven"
                        ),
                        "base": asset,
                        "status": str(result.status),
                        "qty": str(free),
                        "be": str(be) if be is not None else None,
                        "floor": str(floor),
                    }
                    self._bump_skip(
                        "inventory_trim"
                        if is_trim_target and not is_sub_min
                        else "dust_exit_breakeven"
                    )
            if did is not None:
                actions.append(did)
                logger.info("DUST_POLICY %s", did)
        return {"ok": True, "policy": policy, "actions": actions}

    async def execute(
        self,
        order_request: OrderRequest,
        *,
        order_book: Any = None,
        strategy: str = "",
        order_type: OrderType = OrderType.LIMIT,
    ) -> ExecutionResult:
        meta = dict(order_request.metadata or {})
        post_only = bool(meta.get("post_only"))
        if post_only and not self._live_maker:
            return await super().execute(
                order_request,
                order_book=order_book,
                strategy=strategy,
                order_type=order_type,
            )

        if meta.get("buy_only") and not bool(
            getattr(self._settings, "paper_maker_allow_buy_only", True)
        ):
            self._bump_skip("buy_only_disabled")
            return await self._reject_before_live(
                order_request,
                reason="BUY_ONLY_DISABLED",
                message="winst-mode rejects buy-only quotes",
            )

        symbol = order_request.symbol.upper().replace("/", "").replace("-", "")
        base = infer_base_asset(symbol)
        if base in self._exclude_bases:
            self._bump_skip("excluded_base")
            return await self._reject_before_live(
                order_request, reason="EXCLUDED_BASE", message=f"base {base} excluded"
            )

        venue = self._resolve_venue(order_request)
        if venue not in self._execute_venues:
            self._bump_skip("venue_not_live")
            return await self._reject_before_live(
                order_request,
                reason="VENUE_NOT_LIVE",
                message=f"venue {venue or 'unknown'} has no live keys in this session",
            )

        remaining = self._venue_budget_remaining(venue)
        side_is_buy = order_request.side == OpportunitySide.BUY
        if side_is_buy and self._daily_kill_active:
            self._bump_skip("daily_kill")
            return await self._reject_before_live(
                order_request,
                reason="DAILY_KILL",
                message=(
                    f"realized PnL {self.realized_trade_pnl_eur} "
                    f"hit -{self._daily_kill_eur} EUR kill; buys blocked"
                ),
            )
        if side_is_buy and self._sleeve_paused and not meta.get("dust_top_up"):
            self._bump_skip("sleeve_loss_cap")
            return await self._reject_before_live(
                order_request,
                reason="SLEEVE_LOSS_CAP",
                message=(
                    f"sleeve realized {self._sleeve_realized_eur} "
                    f"hit -{self._sleeve_daily_loss_cap} EUR cap; "
                    f"sleeve buys paused (vault holds)"
                ),
            )
        if (
            side_is_buy
            and self._regime_block_buys
            and self._buys_blocked
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
        ):
            new_base = self._is_new_base_buy(venue, base)
            if self._buys_blocked_new_bases_only:
                if new_base:
                    self._bump_skip("regime_block_buys")
                    return await self._reject_before_live(
                        order_request,
                        reason="REGIME_BLOCK_BUYS_NEW",
                        message=(
                            "new-base buys blocked while underwater bags pile up "
                            "(adds to existing still allowed)"
                        ),
                    )
            else:
                self._bump_skip("regime_block_buys")
                return await self._reject_before_live(
                    order_request,
                    reason="REGIME_BLOCK_BUYS",
                    message="buys blocked while regime is reduce-only/toxic",
                )
        if (
            side_is_buy
            and self._regime_block_buys
            and self._base_underwater_blocked(venue, base)
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
        ):
            new_base = self._is_new_base_buy(venue, base)
            if self._underwater_new_bases_only:
                if new_base:
                    self._bump_skip("underwater_base_block")
                    return await self._reject_before_live(
                        order_request,
                        reason="UNDERWATER_BASE_BLOCK",
                        message=(
                            f"new-base buy blocked for {venue}:{base} while below cost "
                            "(other bases on venue still allowed)"
                        ),
                    )
            else:
                self._bump_skip("underwater_base_block")
                return await self._reject_before_live(
                    order_request,
                    reason="UNDERWATER_BASE_BLOCK",
                    message=f"buys blocked for {venue}:{base} while below cost",
                )
        if side_is_buy and remaining < _MIN_LIVE_NOTIONAL:
            self._bump_skip("budget_exhausted")
            return await self._reject_before_live(
                order_request,
                reason="BUDGET_EXHAUSTED",
                message=f"micro pocket free EUR {remaining}",
            )
        # Trend profile: at most N distinct alt bases — add to existing, don't spray.
        if side_is_buy and self._max_alt_bases > 0:
            held = self._held_alt_bases(venue)
            if base not in held and len(held) >= self._max_alt_bases:
                self._bump_skip("max_alt_bases")
                return await self._reject_before_live(
                    order_request,
                    reason="MAX_ALT_BASES",
                    message=(
                        f"{venue} already holding {sorted(held)} "
                        f"(max {self._max_alt_bases} bases for trail concentration)"
                    ),
                )
        if (
            side_is_buy
            and self._is_consolidation_secondary(venue, base)
            and not meta.get("trail_take_profit")
        ):
            self._bump_skip("consolidation_secondary_buy")
            return await self._reject_before_live(
                order_request,
                reason="CONSOLIDATION_SECONDARY",
                message=(
                    f"{base} duplicate on {venue} — sell-only wind-down "
                    f"(primary {self._primary_venue_for_base(base)})"
                ),
            )
        # One base → one venue: don't open FET on OKX when Bitvavo already holds FET.
        if (
            side_is_buy
            and self._is_cross_venue_duplicate_base(venue, base)
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
        ):
            self._bump_skip("cross_venue_duplicate_base")
            return await self._reject_before_live(
                order_request,
                reason="CROSS_VENUE_DUPLICATE_BASE",
                message=(
                    f"{base} already held on another venue "
                    f"(block opening on {venue})"
                ),
            )
        # Max 1 venue per base while underwater elsewhere.
        if (
            side_is_buy
            and self._is_underwater_on_other_venue(venue, base)
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
            and not meta.get("trail_take_profit")
        ):
            self._bump_skip("underwater_cross_venue_block")
            return await self._reject_before_live(
                order_request,
                reason="UNDERWATER_CROSS_VENUE",
                message=(
                    f"{base} underwater on another venue — "
                    f"block opening on {venue}"
                ),
            )
        # Buy-quality circuit breaker after clustered underwater entries.
        if (
            side_is_buy
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
            and not meta.get("trail_take_profit")
        ):
            self._refresh_buy_quality_circuit_breaker()
            if self._buy_quality_paused():
                self._bump_skip("buy_quality_pause")
                return await self._reject_before_live(
                    order_request,
                    reason="BUY_QUALITY_PAUSE",
                    message="new buys paused after clustered underwater fills",
                )
        # Correlation cluster: max N from ADA/ATOM/NEAR/SOL/XRP group.
        if (
            side_is_buy
            and self._max_per_corr > 0
            and base in self._corr_group
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
        ):
            held = self._held_alt_bases(venue)
            if (
                base not in held
                and self._corr_held_count(venue=venue, adding=base) > self._max_per_corr
            ):
                self._bump_skip("corr_group_cap")
                return await self._reject_before_live(
                    order_request,
                    reason="CORR_GROUP_CAP",
                    message=(
                        f"{venue} corr group already at {self._max_per_corr}: "
                        f"{sorted(held & self._corr_group)}"
                    ),
                )
        if (
            side_is_buy
            and self._new_buy_focus_only
            and self._focus_bases
            and self._is_new_base_buy(venue, base)
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
            and not meta.get("trail_take_profit")
            and not meta.get("winner_add")
        ):
            # Util-B: while active book is thin, allow non-focus new buys.
            relax = self._low_util_relax_focus and self._ring_soft_momentum_eligible(
                venue
            )
            if base.upper() not in self._focus_bases and not relax:
                self._bump_skip("focus_base_required")
                return await self._reject_before_live(
                    order_request,
                    reason="FOCUS_BASE_REQUIRED",
                    message=(
                        f"new base {base} not in focus list "
                        f"(avoid non-focus tunnels)"
                    ),
                )
        if (
            side_is_buy
            and self._momentum_enabled
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
            and not meta.get("trail_take_profit")
            and self._is_new_base_buy(venue, base)
        ):
            # New crypto only: require mark momentum + history (no cold/flat slip-ins).
            # Ring underfill uses a softer floor but still blocks flat/falling marks.
            mom_floor = self._momentum_floor_for_buy(venue)
            ring_relaxed = self._ring_soft_momentum_eligible(venue)
            if self._corr_sector_blocks_new_buy(base):
                self._bump_skip("corr_sector_momentum_block")
                return await self._reject_before_live(
                    order_request,
                    reason="CORR_SECTOR_MOMENTUM_BLOCK",
                    message=(
                        f"corr sector weak ({self._corr_group_momentum_down_count()} "
                        f"bases flat/down ≥ {self._corr_sector_momentum_block})"
                    ),
                )
            if not self._entry_momentum_ok(
                symbol,
                min_return=mom_floor,
                low_util=ring_relaxed,
            ):
                self._bump_skip("momentum_block")
                return await self._reject_before_live(
                    order_request,
                    reason="MOMENTUM_BLOCK",
                    message=(
                        f"new base {base} needs entry momentum "
                        f"(floor={float(mom_floor * 100):.2f}%"
                        f", short≥{float(self._entry_short_momentum_min * 100):.2f}%"
                        f"{'; low-util boost' if ring_relaxed else ''})"
                    ),
                )

        if (
            side_is_buy
            and self._entry_quality_enabled
            and self._is_new_base_buy(venue, base)
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
            and not meta.get("trail_take_profit")
            and not meta.get("winner_add")
        ):
            pre_rec = str(meta.get("entry_quality_recommendation") or "")
            if pre_rec == EntryQualityRecommendation.REJECT.value:
                self._bump_skip("entry_quality_reject")
                return await self._reject_before_live(
                    order_request,
                    reason="ENTRY_QUALITY_REJECT",
                    message=str(meta.get("entry_quality_reject_reason") or "entry_quality"),
                )
            if not pre_rec:
                assessment = self._assess_entry_quality_buy(
                    symbol=symbol,
                    qty=Decimal(str(order_request.quantity or 0)),
                    px=Decimal(str(order_request.limit_price or 0)),
                    meta=meta,
                )
                self._entry_quality_diagnostics.record(assessment)
                if assessment.recommendation == EntryQualityRecommendation.REJECT:
                    self._bump_skip("entry_quality_reject")
                    reason_key = assessment.reject_reason or "entry_quality"
                    if "headroom" in reason_key:
                        self._bump_skip("headroom_reject")
                    if "extension" in reason_key:
                        self._bump_skip("extension_reject")
                    if "continuity" in reason_key:
                        self._bump_skip("continuity_reject")
                    if assessment.headroom_pct is None:
                        self._bump_skip("headroom_unknown")
                    return await self._reject_before_live(
                        order_request,
                        reason="ENTRY_QUALITY_REJECT",
                        message=(
                            f"entry quality {assessment.score} "
                            f"headroom={assessment.headroom_pct} "
                            f"ext={assessment.extension_pct} "
                            f"req={assessment.required_move_pct} "
                            f"({reason_key})"
                        ),
                    )
                if assessment.recommendation == EntryQualityRecommendation.REDUCED_SIZE:
                    self._bump_skip("entry_quality_reduced")
                else:
                    self._bump_skip("entry_quality_normal")
            elif pre_rec == EntryQualityRecommendation.REDUCED_SIZE.value:
                self._bump_skip("entry_quality_reduced")
            elif pre_rec == EntryQualityRecommendation.NORMAL_SIZE.value:
                self._bump_skip("entry_quality_normal")

        px = Decimal(str(order_request.limit_price or 0))
        qty = Decimal(str(order_request.quantity or 0))
        if px <= 0 or qty <= 0:
            self._bump_skip("bad_size")
            return await self._reject_before_live(
                order_request, reason="BAD_SIZE", message="quantity/price required"
            )
        if side_is_buy:
            mult_raw = meta.get("entry_quality_multiplier")
            if mult_raw is not None:
                try:
                    mult = Decimal(str(mult_raw))
                except Exception:  # noqa: BLE001
                    mult = _ONE
                if mult < _ONE:
                    scaled = apply_size_multiplier(qty, mult)
                    if scaled <= 0:
                        self._bump_skip("entry_quality_reject")
                        return await self._reject_before_live(
                            order_request,
                            reason="ENTRY_QUALITY_REJECT",
                            message="entry quality size multiplier zero",
                        )
                    qty = scaled
                    order_request = order_request.model_copy(update={"quantity": qty})
        if side_is_buy and post_only:
            px = self._aggressive_buy_price(venue, px, order_book)
            order_request = order_request.model_copy(update={"limit_price": px})

        if (
            side_is_buy
            and self._block_underwater_adds
            and not self._is_new_base_buy(venue, base)
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
        ):
            be = self._break_even_sell_price(venue, base)
            mark_ref = self._portfolio.state.mark_prices.get(symbol.upper())
            if mark_ref is None or mark_ref <= 0:
                mark_ref = px
            if be is not None and mark_ref < be:
                self._bump_skip("underwater_add_block")
                return await self._reject_before_live(
                    order_request,
                    reason="UNDERWATER_ADD_BLOCK",
                    message=(
                        f"add blocked: {venue}:{base} mark {mark_ref} "
                        f"below break-even {be}"
                    ),
                )
        if (
            side_is_buy
            and self._block_buys_when_holding_base
            and not self._is_new_base_buy(venue, base)
            and not meta.get("dust_top_up")
            and not meta.get("ladder_leg")
            and not meta.get("winner_add")
        ):
            self._bump_skip("holding_base_buy_block")
            return await self._reject_before_live(
                order_request,
                reason="HOLDING_BASE_BUY_BLOCK",
                message=(
                    f"buy blocked: already holding {venue}:{base}; "
                    "scan other bases with momentum"
                ),
            )
        if (
            side_is_buy
            and self._resting_buys_for(venue, symbol) >= self._max_resting_buys_per_symbol
            and not meta.get("dust_top_up")
        ):
            self._bump_skip("duplicate_resting_buy")
            return await self._reject_before_live(
                order_request,
                reason="DUPLICATE_RESTING_BUY",
                message=(
                    f"resting buys already at cap "
                    f"{self._max_resting_buys_per_symbol} for {venue}:{symbol}"
                ),
            )

        # Size against live Bitvavo free balances (paper pocket can lag fills).
        if side_is_buy:
            live_eur = await self._live_free(venue, self._quote)
            spend_cap = min(remaining, live_eur) if live_eur > 0 else remaining
            notional = qty * px
            if notional > spend_cap:
                qty = (spend_cap / px).quantize(Decimal("0.00000001"))
                notional = qty * px
                if qty <= 0 or notional < _MIN_LIVE_NOTIONAL:
                    self._bump_skip("insufficient_live_quote")
                    return await self._reject_before_live(
                        order_request,
                        reason="INSUFFICIENT_LIVE_QUOTE",
                        message=f"live {self._quote} free {live_eur} pocket {remaining}",
                    )
                order_request = order_request.model_copy(update={"quantity": qty})
            # One consolidated clip per entry — floor to first_clip, cap at add_clip.
            clip_cap = self._buy_clip_cap_eur(venue, base)
            if (
                clip_cap is not None
                and clip_cap > 0
                and not meta.get("dust_top_up")
            ):
                target = min(clip_cap, spend_cap)
                if notional < target and target >= _MIN_LIVE_NOTIONAL:
                    qty = (target / px).quantize(Decimal("0.00000001"))
                    notional = qty * px
                    order_request = order_request.model_copy(update={"quantity": qty})
                elif notional > clip_cap:
                    qty = (clip_cap / px).quantize(Decimal("0.00000001"))
                    notional = qty * px
                    if qty <= 0 or notional < _MIN_LIVE_NOTIONAL:
                        self._bump_skip("clip_too_small")
                        return await self._reject_before_live(
                            order_request,
                            reason="CLIP_TOO_SMALL",
                            message=f"buy clip cap €{clip_cap} below min live notional",
                        )
                    order_request = order_request.model_copy(update={"quantity": qty})
            # Ladder entries: first leg joins the strategy bid (touch), deeper
            # legs only as backup. Using mark*(1-dip) previously parked all
            # bids ~1% below market so they never filled.
            if (
                self._ladder_enabled
                and post_only
                and not meta.get("ladder_leg")
                and not meta.get("dust_top_up")
                and len(self._ladder_pcts) >= 2
            ):
                ref = px if px > 0 else await self._mark_price(venue, symbol)
                if ref is None or ref <= 0:
                    ref = px
                # Post-only safety: never cross the ask.
                best_ask = _ZERO
                best_bid = _ZERO
                if order_book is not None:
                    try:
                        if order_book.asks:
                            best_ask = Decimal(str(order_book.asks[0].price))
                        if order_book.bids:
                            best_bid = Decimal(str(order_book.bids[0].price))
                    except Exception:  # noqa: BLE001
                        best_ask = _ZERO
                        best_bid = _ZERO
                if best_bid > 0:
                    ref = max(ref, best_bid)
                leg_qty = (qty / Decimal(len(self._ladder_pcts))).quantize(
                    Decimal("0.00000001")
                )
                if leg_qty * ref >= _MIN_LIVE_NOTIONAL:
                    last_result: ExecutionResult | None = None
                    for dip in self._ladder_pcts:
                        leg_px = (ref * (Decimal("1") - dip)).quantize(
                            Decimal("0.00000001")
                        )
                        if (
                            venue.strip().lower() == "okx"
                            and dip <= 0
                            and best_bid > 0
                        ):
                            leg_px = max(leg_px, best_bid)
                            if self._okx_buy_improve_bps > 0:
                                leg_px = self._aggressive_buy_price(
                                    venue, leg_px, order_book
                                )
                        if best_ask > 0 and leg_px >= best_ask:
                            leg_px = (best_ask * Decimal("0.9999")).quantize(
                                Decimal("0.00000001")
                            )
                        if leg_px <= 0:
                            continue
                        leg_req = order_request.model_copy(
                            update={
                                "id": uuid4(),
                                "quantity": leg_qty,
                                "limit_price": leg_px,
                                "metadata": {
                                    **meta,
                                    "ladder_leg": True,
                                    "ladder_dip_pct": str(dip),
                                    "post_only": True,
                                    "venue": venue,
                                },
                            }
                        )
                        last_result = await self.execute(
                            leg_req,
                            order_book=order_book,
                            strategy=strategy or "ladder_buy",
                            order_type=order_type,
                        )
                    self._bump_skip("ladder_buy")
                    if last_result is not None:
                        return last_result
        else:
            live_base = await self._live_free(venue, base)
            if live_base <= 0:
                self._bump_skip("insufficient_live_base")
                return await self._reject_before_live(
                    order_request,
                    reason="INSUFFICIENT_LIVE_BASE",
                    message=f"live {base} free {live_base}",
                )
            if qty > live_base:
                qty = live_base.quantize(Decimal("0.00000001"))
                order_request = order_request.model_copy(update={"quantity": qty})
            # Hard floor: NEVER sell below fee-adjusted cost + profit buffer.
            # Cut-loss exits (new-base stop) may sell below BE when floor is breached.
            exit_reason = str(meta.get("exit_reason") or strategy or "")
            cut_loss_exit = exit_reason in {"trail_cut_loss", "trail_early_cut_loss"}
            be = self._break_even_sell_price(venue, base)
            if be is None:
                self._bump_skip("sell_no_trusted_cost")
                return await self._reject_before_live(
                    order_request,
                    reason="SELL_NO_TRUSTED_COST",
                    message=(
                        f"no trusted cost basis for {venue}:{base}; "
                        "refusing sell until buy fill or trade history is known"
                    ),
                )
            if cut_loss_exit:
                floor = self._cut_loss_floor_for_reason(venue, base, exit_reason)
                if floor is None:
                    self._bump_skip("cut_loss_not_configured")
                    return await self._reject_before_live(
                        order_request,
                        reason="CUT_LOSS_DISABLED",
                        message=f"cut-loss not enabled for {venue}:{base}",
                    )
                mark_ref = px
                if order_book is not None and order_book.bids:
                    try:
                        mark_ref = Decimal(str(order_book.bids[0].price))
                    except Exception:  # noqa: BLE001
                        pass
                if mark_ref > floor:
                    self._bump_skip("cut_loss_above_floor")
                    return await self._reject_before_live(
                        order_request,
                        reason="CUT_LOSS_ABOVE_FLOOR",
                        message=f"mark {mark_ref} above cut-loss floor {floor}",
                    )
            elif be > px:
                px = be
                order_request = order_request.model_copy(update={"limit_price": px})
            if order_book is not None:
                try:
                    best_bid = (
                        Decimal(str(order_book.bids[0].price))
                        if order_book.bids
                        else _ZERO
                    )
                except Exception:  # noqa: BLE001
                    best_bid = _ZERO
                if cut_loss_exit:
                    if best_bid > 0:
                        px = best_bid
                        order_request = order_request.model_copy(update={"limit_price": px})
                        meta = dict(order_request.metadata or {})
                        meta["post_only"] = False
                        order_request = order_request.model_copy(update={"metadata": meta})
                # If bid is already above break-even, crossing is a profitable fill.
                # Only block when the bid itself is still below break-even.
                elif best_bid > 0 and best_bid < be:
                    self._bump_skip("sell_below_break_even")
                    return await self._reject_before_live(
                        order_request,
                        reason="SELL_BELOW_BREAK_EVEN",
                        message=(
                            f"best bid {best_bid} still below break-even {be}; "
                            "holding for profitable exit"
                        ),
                    )
                if not cut_loss_exit and best_bid > 0 and px < best_bid and best_bid >= be:
                    # Lift limit to the bid so the exit actually fills.
                    px = best_bid
                    order_request = order_request.model_copy(update={"limit_price": px})
                    meta = dict(order_request.metadata or {})
                    meta["post_only"] = False
                    order_request = order_request.model_copy(update={"metadata": meta})
            # Even without a book, refuse a sell priced below break-even.
            if not cut_loss_exit and px < be:
                self._bump_skip("sell_below_break_even")
                return await self._reject_before_live(
                    order_request,
                    reason="SELL_BELOW_BREAK_EVEN",
                    message=f"limit {px} below break-even {be}",
                )
            notional = qty * px
            if notional < _MIN_LIVE_NOTIONAL:
                self._bump_skip("sell_below_min_notional")
                return await self._reject_before_live(
                    order_request,
                    reason="SELL_BELOW_MIN",
                    message=f"sell notional {notional} below {_MIN_LIVE_NOTIONAL}",
                )

        side = "buy" if side_is_buy else "sell"
        payload = {
            "venue": venue,
            "symbol": symbol,
            "side": side,
            "quantity": str(qty),
            "limit_price": str(px) if px > 0 else None,
            "notional_eur": str(notional.quantize(Decimal("0.01"))),
            "confirm": True,
            "post_only": post_only,
            "local_open_orders": self._resting_count_for(venue),
        }
        out = await self._live.submit(payload, confirm=True)
        self._invalidate_bal_cache()
        row = {
            "symbol": symbol,
            "venue": venue,
            "side": side,
            "requested_qty": str(qty),
            "requested_notional": str(notional),
            "source": "live",
            "result": out,
        }
        self.live_trades.append(row)

        if not out.get("executed"):
            self._bump_skip(str(out.get("reason") or "live_not_executed"))
            # Keep pocket honest after rejected/failed attempts.
            await self.reconcile_from_exchange(venue)
            return await self._reject_before_live(
                order_request,
                reason="LIVE_NOT_EXECUTED",
                message=str(out.get("message") or out.get("reason") or "not executed"),
            )

        order_row = out.get("order") or {}
        filled = Decimal(str(order_row.get("filled_quantity") or 0))
        avg = Decimal(str(order_row.get("average_price") or px or 0))
        if filled <= 0:
            filled, avg = await self._poll_fill(
                venue=venue,
                symbol=symbol,
                exchange_order_id=(
                    str(order_row.get("exchange_order_id"))
                    if order_row.get("exchange_order_id")
                    else None
                ),
                fallback_price=px,
            )

        if filled <= 0 or avg <= 0:
            # Resting order accepted — track for fill/cancel; sync locked balances.
            self._bump_skip("live_resting")
            self._track_resting(
                venue=venue,
                symbol=symbol,
                side=side,
                exchange_order_id=(
                    str(order_row.get("exchange_order_id"))
                    if order_row.get("exchange_order_id")
                    else None
                ),
                quantity=qty,
                price=px,
                strategy=strategy,
                opportunity_id=order_request.opportunity_id,
            )
            await self.reconcile_from_exchange(venue)
            return await self._reject_before_live(
                order_request,
                reason="LIVE_RESTING",
                message="order accepted on exchange; waiting for fill (pocket synced)",
            )

        fill_notional = filled * avg
        self._turnover += fill_notional
        return await self._mirror_live_fill(
            order_request,
            filled_qty=filled,
            average_price=avg,
            venue=venue,
            strategy=strategy,
            exchange_order_id=order_row.get("exchange_order_id"),
        )

    async def _reject_before_live(
        self,
        order_request: OrderRequest,
        *,
        reason: str,
        message: str,
    ) -> ExecutionResult:
        side = (
            OrderSide.BUY
            if order_request.side == OpportunitySide.BUY
            else OrderSide.SELL
        )
        order = Order(
            id=order_request.id,
            strategy=str((order_request.metadata or {}).get("strategy") or ""),
            symbol=order_request.symbol,
            side=side,
            order_type=OrderType.LIMIT,
            requested_quantity=order_request.quantity,
            requested_price=order_request.limit_price,
            status=OrderStatus.PENDING,
            exchange="micro_bridge",
            opportunity_id=order_request.opportunity_id,
            client_order_id=order_request.client_order_id,
            metadata={**(order_request.metadata or {}), "executor": self.name},
        )
        self._orders.add(order)
        return self._reject(order, order_request, reason=reason, message=message)

    async def _mirror_live_fill(
        self,
        order_request: OrderRequest,
        *,
        filled_qty: Decimal,
        average_price: Decimal,
        venue: str,
        strategy: str,
        exchange_order_id: Any,
    ) -> ExecutionResult:
        """Keep paper portfolio/risk in sync with the live pocket."""
        side = (
            OrderSide.BUY
            if order_request.side == OpportunitySide.BUY
            else OrderSide.SELL
        )
        order = Order(
            id=order_request.id,
            strategy=strategy or str((order_request.metadata or {}).get("strategy") or ""),
            symbol=order_request.symbol,
            side=side,
            order_type=OrderType.MARKET,
            requested_quantity=filled_qty,
            requested_price=average_price,
            status=OrderStatus.PENDING,
            exchange=venue,
            opportunity_id=order_request.opportunity_id,
            client_order_id=order_request.client_order_id
            or f"micro-mirror-{uuid4().hex[:12]}",
            metadata={
                **(order_request.metadata or {}),
                "executor": self.name,
                "live_mirrored": True,
                "venue": venue,
            },
        )
        self._orders.add(order)
        self._orders.set_status(order.id, OrderStatus.OPEN)

        fee_rate = self._fee_rate_for(order)
        fee = filled_qty * average_price * fee_rate
        fill = Fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=filled_qty,
            price=average_price,
            fee=fee,
            fee_asset=self._quote_asset_for(order),
            slippage=_ZERO,
            exchange=venue,
            metadata={
                "live_mirrored": True,
                "fee_rate": str(fee_rate),
                "exchange_order_id": exchange_order_id,
            },
        )
        self._orders.attach_fill(order.id, fill)
        self._fills.apply(order, fill)
        try:
            self._apply_venue_ledger(order, filled_qty, average_price, fee)
        except Exception:  # noqa: BLE001
            logger.exception("micro_bridge venue ledger sync failed")
        self._portfolio.set_mark_price(order.symbol, average_price)
        self._invalidate_bal_cache()
        self._record_realized_fill(
            side=side,
            symbol=order.symbol,
            qty=filled_qty,
            price=average_price,
            fee=fee,
            venue=venue,
            fill_meta=dict(order_request.metadata or {}),
        )
        self._note_live_fill_event(
            venue=venue,
            symbol=order.symbol,
            side=side.value.lower() if hasattr(side, "value") else str(side).lower(),
            qty=filled_qty,
            price=average_price,
            source="mirror",
            exchange_order_id=str(exchange_order_id) if exchange_order_id else None,
        )

        result = ExecutionResult(
            order_id=order.id,
            opportunity_id=order_request.opportunity_id,
            status=OrderStatus.FILLED,
            filled_quantity=filled_qty,
            average_price=average_price,
            fees_usd=fee,
            message="Live micro fill mirrored into paper pocket",
            metadata={
                "executor": self.name,
                "exchange": venue,
                "real_exchange_order": True,
                "micro_live": True,
                "fee": str(fee),
                "exchange_order_id": exchange_order_id,
            },
        )
        self.history.append(result)
        logger.info(
            "MICRO_LIVE_FILL symbol=%s venue=%s qty=%s px=%s turnover=%s free=%s",
            order.symbol,
            venue,
            filled_qty,
            average_price,
            self._turnover,
            self.budget_remaining,
        )
        return result

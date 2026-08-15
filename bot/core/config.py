"""Centralized configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bot.core.enums import ExecutionMode
from bot.core.exceptions import ConfigurationError


class Settings(BaseSettings):
    """Application settings. Exchange credentials come only from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "staging", "production"] = "development"
    app_debug: bool = False
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    execution_mode: ExecutionMode = ExecutionMode.PAPER

    database_url: str = "postgresql+asyncpg://moreney:moreney@localhost:5432/moreney"
    redis_url: str = "redis://localhost:6379/0"

    risk_max_position_usd: float = Field(default=1000.0, gt=0)
    risk_max_daily_loss_usd: float = Field(default=200.0, gt=0)
    risk_max_open_positions: int = Field(default=5, gt=0)
    risk_min_net_profit_usd: float = Field(default=1.0, ge=0)

    # Percentage / rate limits (env aliases map via field names).
    max_position_percent: float = Field(default=10.0, gt=0, le=100)
    max_total_exposure_percent: float = Field(default=50.0, gt=0, le=100)
    max_daily_loss_percent: float = Field(default=3.0, gt=0, le=100)
    max_drawdown_percent: float = Field(default=5.0, gt=0, le=100)
    max_simultaneous_positions: int | None = Field(default=None, gt=0)
    max_trades_per_minute: int = Field(default=30, gt=0)
    max_slippage_percent: float = Field(default=0.10, ge=0)
    max_market_data_age_ms: float = Field(default=1000.0, gt=0)
    max_execution_latency_ms: float = Field(default=2000.0, gt=0)
    max_abnormal_price_move_percent: float = Field(default=5.0, gt=0)
    min_liquidity_base: float = Field(default=0.01, ge=0)
    risk_consecutive_failure_limit: int = Field(default=5, gt=0)
    risk_warning_daily_loss_percent: float = Field(default=2.0, ge=0)
    risk_warning_drawdown_percent: float = Field(default=3.0, ge=0)
    # When True, kill switch may return to RUNNING only after conditions clear
    # AND an explicit recover() call (no silent auto-resume).
    risk_require_manual_recovery: bool = True

    # Legacy flat fee (used as fallback when maker/taker rates are unset).
    profitability_fee_rate: float = Field(default=0.001, ge=0)
    profitability_maker_fee_rate: float | None = Field(default=None, ge=0)
    profitability_taker_fee_rate: float | None = Field(default=None, ge=0)
    profitability_slippage_bps: float = Field(default=5.0, ge=0)
    profitability_market_impact_factor: float = Field(default=1.0, ge=0)
    profitability_thin_book_penalty_bps: float = Field(default=25.0, ge=0)
    profitability_funding_rate: float = Field(default=0.0001, ge=0)
    profitability_apply_funding: bool = True
    profitability_execution_buffer_bps: float = Field(default=10.0, ge=0)
    profitability_min_net_profit_usd: float = Field(default=1.0, ge=0)
    profitability_min_net_return: float = Field(default=0.001, ge=0)

    # Cross-exchange arbitrage strategy
    arbitrage_min_profit_eur: float = Field(default=1.0, ge=0)
    arbitrage_min_profit_pct: float = Field(default=0.001, ge=0)
    arbitrage_min_liquidity_base: float = Field(default=0.01, gt=0)
    arbitrage_max_quantity: float = Field(default=1.0, gt=0)
    # Size each leg as a % of current equity (0 = use max_quantity only).
    arbitrage_position_pct: float = Field(default=8.0, ge=0, le=100)
    # Minimum seconds between repeat emissions for the same directed pair.
    arbitrage_opportunity_cooldown_ms: float = Field(default=3000.0, ge=0)
    # Emit only the top-N NET edges per symbol per cycle (reduces spam / exposure churn).
    arbitrage_max_emits_per_cycle: int = Field(default=2, ge=1, le=20)
    arbitrage_max_latency_ms: float = Field(default=500.0, gt=0)
    arbitrage_max_book_age_ms: float = Field(default=1000.0, gt=0)

    # Paper trading (completely isolated from live execution)
    paper_trading_enabled: bool = True
    paper_auto_start: bool = False
    paper_starting_eur: float = Field(default=200.0, gt=0)
    paper_fee_rate: float = Field(default=0.001, ge=0)
    paper_slippage_mode: Literal["fixed", "order_book"] = "order_book"
    paper_fixed_slippage_pct: float = Field(default=0.05, ge=0)
    paper_partial_fills_on_thin_book: bool = True
    paper_reject_on_insufficient_liquidity: bool = False
    paper_simulated_latency_ms: float = Field(default=5.0, ge=0)
    paper_quote_asset: str = "EUR"
    paper_cycle_interval_ms: float = Field(default=1000.0, gt=0)
    paper_persist_path: str = "./data/paper_state.json"
    # Track cash/crypto per exchange so paper arb cannot teleport coins.
    paper_venue_inventory: bool = False
    # Total % of starting capital reserved for ALL seeded base inventory combined.
    paper_seed_inventory_pct: float = Field(default=0.0, ge=0, le=90)
    # Cap how many bases get pre-funded (scan can still cover more symbols).
    paper_seed_max_assets: int = Field(default=3, ge=0, le=50)
    # Optional allowlist of symbols to seed (empty = first max_assets from market data).
    paper_seed_symbols: str = "ATOMEUR,DOTEUR,XRPEUR"
    # Extra adverse move applied to the sell book after the buy lands (basis points).
    paper_second_leg_adverse_bps: float = Field(default=0.0, ge=0)
    # Maker (post-only) quoting: capture bid/ask instead of paying taker-taker.
    # Defaults are live-conservative: trade-through fills only, stale edges rejected.
    paper_maker_enabled: bool = True
    paper_maker_rest_ms: float = Field(default=0.0, ge=0)
    # At-touch queue fills (0 = disabled; live makers rarely get full touch size).
    paper_maker_queue_fill_pct: float = Field(default=0.0, ge=0, le=1)
    # Fraction filled when the book prints through a resting quote (price priority).
    # 1.0 = full size; at-touch queue remains a separate knob (default 0).
    paper_maker_trade_through_fill_pct: float = Field(default=0.20, ge=0, le=1)
    paper_maker_max_age_ms: float = Field(default=2500.0, ge=0)
    paper_maker_min_spread_bps: float = Field(default=3.0, ge=0)
    # Gross edge above this is treated as stale/public-feed dislocation, not tradable.
    paper_maker_max_edge_bps: float = Field(default=30.0, ge=0)
    paper_maker_min_profit_eur: float = Field(default=0.15, ge=0)
    # Extra euro floor: equity × bps / 10_000 (0.2 bps of €25k ≈ €0.50). 0 = off.
    paper_maker_min_profit_equity_bps: float = Field(default=0.0, ge=0)
    # Hard NET return floor (0.0025 = 0.25%).
    paper_maker_min_net_return: float = Field(default=0.0025, ge=0)
    # Ignore quotes whose notional is below this euro dust floor.
    paper_maker_min_notional_eur: float = Field(default=10.0, ge=0)
    # Keep a quote only if its NET euro is at least this fraction of the cycle's best.
    paper_maker_keep_vs_best_frac: float = Field(default=0.0, ge=0, le=1)
    # During cooldown, still replace a quote if NET euro improved by this fraction.
    paper_maker_replace_improve_frac: float = Field(default=0.25, ge=0)
    # Hard cap on alt share of equity (rest must stay quote cash).
    paper_max_alt_inventory_pct: float = Field(default=30.0, ge=0, le=90)
    # Below this alt share, only buy deep dips (extra edge vs fair value).
    paper_min_alt_inventory_pct: float = Field(default=10.0, ge=0, le=90)
    # When overweight, tighten ask by this many bps to free capital faster.
    paper_inventory_ask_improve_bps: float = Field(default=4.0, ge=0)
    # When underweight, require this many extra bps below fair value to buy.
    paper_inventory_buy_dip_bps: float = Field(default=8.0, ge=0)
    # Dump guard: cancel buys if mid falls by this % inside the window.
    paper_vol_move_pct: float = Field(default=1.5, ge=0)
    paper_vol_window_sec: float = Field(default=300.0, ge=1)
    paper_vol_cooldown_sec: float = Field(default=120.0, ge=0)
    # Max seconds an alt tranche may sit before a break-even recycle sell.
    paper_max_holding_sec: float = Field(default=7200.0, ge=0)
    # HMM regime detector (hmmlearn): toxic-flow → cancel bids / REDUCE_ONLY.
    paper_hmm_enabled: bool = True
    paper_hmm_min_samples: int = Field(default=80, ge=30)
    # Legacy cycle-based knob (ignored when refit_every_sec is set).
    paper_hmm_refit_every_cycles: int = Field(default=30, ge=1)
    # Retrain every N seconds (default 5h). Keeps CPU off the hot path.
    paper_hmm_refit_every_sec: float = Field(default=18000.0, ge=60)
    # ATR window for normalized volatility (14 = classic ATR).
    paper_hmm_atr_window: int = Field(default=14, ge=2)
    paper_hmm_vol_window: int = Field(default=14, ge=2)  # alias → atr_window
    # Rolling candle history for fit (clamped to 500–1000 inside detector).
    paper_hmm_history_len: int = Field(default=750, ge=60, le=2000)
    # Candle timeframe in seconds (300 = 5m, 900 = 15m).
    paper_hmm_candle_sec: float = Field(default=300.0, ge=60)
    # Hysteresis: toxic after N consecutive dump states OR proba ≥ threshold.
    paper_hmm_toxic_confirm_steps: int = Field(default=2, ge=1, le=10)
    paper_hmm_toxic_proba_threshold: float = Field(default=0.70, ge=0.5, le=0.99)
    paper_hmm_normal_inventory_pct: float = Field(default=0.30, ge=0.05, le=0.90)
    paper_hmm_toxic_inventory_pct: float = Field(default=0.10, ge=0.01, le=0.50)
    # Extra ask-improve bps while HMM says bullish (harvest EUR faster).
    paper_hmm_uptrend_ask_improve_bps: float = Field(default=4.0, ge=0)
    # Same-venue MM when local spread clears fees (trade-through fills keep this honest).
    paper_maker_same_venue: bool = True
    paper_maker_max_open_quotes: int = Field(default=6, ge=1, le=30)
    # Only quote/inventory on these venues. Empty = all market-data venues.
    paper_maker_venues: str = "okx,binance,bitvavo,kraken"
    # Skip pairs whose combined maker fees exceed this (bps).
    paper_maker_max_fee_bps: float = Field(default=35.0, ge=0)
    # Extra life for the unfilled leg after its sibling fills (ms).
    paper_maker_sibling_grace_ms: float = Field(default=8000.0, ge=0)
    # When one maker leg fills and the quote expires, exit leftover inventory as taker.
    paper_maker_one_leg_exit: bool = True
    paper_maker_one_leg_adverse_bps: float = Field(default=6.0, ge=0)
    # Expected adverse selection vs mid/fair value when a maker quote is hit (bps).
    paper_maker_adverse_bps: float = Field(default=4.0, ge=0)
    # Extra bps required beyond maker fees for gross spread (fees_eat_edge gate).
    paper_maker_spread_fee_buffer_bps: float = Field(default=1.0, ge=0)
    # Override execution buffer in post-emit gate (default: 1 bps + adverse).
    paper_maker_gate_buffer_bps: float | None = Field(default=None, ge=0)
    # Use BASEUSDT × EURUSDT as fair-value filter (skip toxic one-sided dislocations).
    paper_maker_fair_value: bool = True
    # FX symbol for USDT-per-EUR (Binance/OKX style).
    paper_maker_fx_symbol: str = "EURUSDT"
    # Book level for maker quotes (0=touch, 1=2nd level, ...).
    paper_maker_book_level: int = Field(default=0, ge=0, le=10)
    # Early taker hedge when one maker leg fills and mid moves against the rest.
    paper_hybrid_hedge: bool = True
    paper_hybrid_adverse_bps: float = Field(default=8.0, ge=0)
    # EUR↔USDT triangle bridge strategy.
    paper_triangle_enabled: bool = True
    paper_triangle_bases: str = "BTC,ETH,ATOM,DOT,XRP"
    # Percent of starting capital converted to USDT float on maker venues.
    paper_seed_usdt_pct: float = Field(default=20.0, ge=0, le=50)
    # Paper venue rebalance of quote cash (simulates withdraw/deposit).
    paper_rebalance_enabled: bool = True
    paper_rebalance_every_cycles: int = Field(default=120, ge=1)
    paper_rebalance_fee_bps: float = Field(default=5.0, ge=0)
    # Fee tier: retail | vip1 | vip2 | vip3 | rebate
    paper_fee_tier: str = "retail"
    # Markout-adaptive adverse haircut.
    paper_markout_enabled: bool = True
    paper_markout_floor_bps: float = Field(default=2.0, ge=0)
    # Observed Bitvavo 5s markout mean ~21 bps on €25k paper; a 15 bps ceiling
    # clipped the gate below measured toxicity. Higher ceiling = more conservative.
    paper_markout_ceiling_bps: float = Field(default=40.0, ge=0)
    # Comma-separated paper instance base URLs for the fleet dashboard.
    paper_fleet_urls: str = (
        "http://127.0.0.1:8007,http://127.0.0.1:8008,"
        "http://127.0.0.1:8009,http://127.0.0.1:8010,"
        "http://127.0.0.1:8006"
    )
    paper_fleet_labels: str = "200 EUR,500 EUR,1000 EUR,5000 EUR,25000 EUR"
    dashboard_basic_auth_enabled: bool = False
    dashboard_basic_auth_username: str = "moreney"
    dashboard_basic_auth_password: SecretStr | None = None
    dashboard_session_secret: SecretStr | None = None

    # --- Global opportunity engine (multi-market architecture) ---
    global_opportunity_engine_enabled: bool = True
    global_tiered_scan_enabled: bool = True
    global_fx_enabled: bool = False
    global_fx_pairs: str = "EURUSD,GBPUSD,USDJPY"
    global_equity_enabled: bool = False
    global_equity_symbols: str = "SPY.US,AAPL.US,SAP.DE"
    # Nasdaq public quote API (US live bid/ask) + Yahoo chart (EU last). No API key.
    global_equity_poll_interval_sec: float = Field(default=15.0, ge=10.0)
    global_funding_strategy_enabled: bool = True
    global_min_funding_bps: float = Field(default=3.0, ge=0)
    global_funding_poll_interval_sec: float = Field(default=60.0, ge=15.0)
    global_funding_exchanges: str = "binance"
    global_fx_z_threshold: float = Field(default=1.5, gt=0)
    global_equity_deviation_bps: float = Field(default=30.0, gt=0)
    global_transfer_fee_bps: float = Field(default=10.0, ge=0)
    global_transfer_latency_bps: float = Field(default=5.0, ge=0)
    global_max_correlation_exposure_pct: float = Field(default=40.0, gt=0, le=100)
    global_max_strategy_exposure_pct: float = Field(default=50.0, gt=0, le=100)
    global_max_venue_exposure_pct: float = Field(default=35.0, gt=0, le=100)
    global_use_global_composite: bool = True
    opportunity_default_win_prob: float = Field(default=0.55, ge=0.05, le=0.95)
    opportunity_default_loss_pct: float = Field(default=0.002, ge=0)
    opportunity_min_expected_value: float = Field(default=0.0, ge=0)
    opportunity_min_score: float = Field(default=0.0, ge=0)
    opportunity_max_executions_per_cycle: int = Field(default=3, ge=1, le=30)
    opportunity_max_candidates_per_cycle: int = Field(default=20, ge=1, le=100)
    opportunity_decay_ms: int = Field(default=5000, ge=0)
    # EV calibration (shrinkage toward 1.0 until enough realized fills exist).
    ev_calibration_prior_strength: int = Field(default=40, ge=1, le=500)
    ev_calibration_min_samples: int = Field(default=20, ge=5, le=500)
    # Early stop is independent of shrinkage (loss containment).
    ev_calibration_early_stop_samples: int = Field(default=8, ge=3, le=100)
    ev_calibration_early_stop_capture: float = Field(default=-0.25, le=0)
    ev_calibration_early_stop_min_loss_eur: float = Field(default=5.0, ge=0)
    paper_markout_min_samples: int = Field(default=20, ge=5, le=500)
    risk_allow_partial_sizing: bool = True
    risk_partial_min_notional_pct: float = Field(default=10.0, ge=1, le=100)

    # Realtime public market data (no private APIs)
    # local = each process owns WebSockets (tests / single-instance)
    # publisher = one process owns WebSockets and writes Redis
    # shared = paper instances hydrate from Redis (no WebSockets)
    market_data_mode: Literal["local", "shared", "publisher"] = "local"
    market_data_redis_poll_ms: float = Field(default=100.0, ge=20.0)
    market_data_exchanges: str = "binance,kraken,coinbase,bitvavo,okx,bybit"
    market_data_symbols: str = (
        "BTCEUR,ETHEUR,BTCUSDT,ETHUSDT,EURUSDT,ADAEUR,ADAUSDT,ATOMEUR,ATOMUSDT,"
        "DOTEUR,DOTUSDT,XRPEUR,XRPUSDT,NEAREUR,NEARUSDT"
    )
    market_data_recording_enabled: bool = False
    market_data_recording_path: str = "./data/market_data"
    market_data_ws_reconnect_base_ms: float = Field(default=500.0, gt=0)
    market_data_ws_reconnect_max_ms: float = Field(default=30000.0, gt=0)
    market_data_heartbeat_interval_ms: float = Field(default=15000.0, gt=0)
    market_data_connection_timeout_ms: float = Field(default=10000.0, gt=0)
    market_data_redis_ttl_seconds: int = Field(default=30, gt=0)
    # Keep only nearest N levels per side (arbitrage needs top-of-book depth, not full L2).
    market_data_book_depth: int = Field(default=50, ge=5, le=500)
    # How often to serialize books/health into the in-process cache (ms). 0 = every event.
    market_data_cache_interval_ms: float = Field(default=250.0, ge=0)

    # Opt-in hot-path latency histograms (mean/p50/p95/p99). No per-event logs.
    perf_instrumentation_enabled: bool = False
    perf_instrumentation_window: int = Field(default=512, ge=32, le=10000)

    exchange_name: str = "stub"
    exchange_api_key: SecretStr | None = None
    exchange_api_secret: SecretStr | None = None
    exchange_passphrase: SecretStr | None = None
    exchange_base_url: str | None = None

    @field_validator("execution_mode", mode="before")
    @classmethod
    def _normalize_execution_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.lower()
        return value

    def require_live_credentials(self) -> None:
        """Fail closed if live mode is selected without exchange credentials."""
        if self.execution_mode != ExecutionMode.LIVE:
            return
        if not self.exchange_api_key or not self.exchange_api_secret:
            raise ConfigurationError(
                "Live execution requires EXCHANGE_API_KEY and EXCHANGE_API_SECRET"
            )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()

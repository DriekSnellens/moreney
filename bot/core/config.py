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
    # Comma-separated paper instance base URLs for the fleet dashboard.
    paper_fleet_urls: str = (
        "http://127.0.0.1:8001,http://127.0.0.1:8002,http://127.0.0.1:8003,http://127.0.0.1:8004"
    )
    paper_fleet_labels: str = "200 EUR,500 EUR,1000 EUR,5000 EUR"
    dashboard_basic_auth_enabled: bool = False
    dashboard_basic_auth_username: str = "moreney"
    dashboard_basic_auth_password: SecretStr | None = None
    dashboard_session_secret: SecretStr | None = None

    # Realtime public market data (no private APIs)
    # local = each process owns WebSockets (tests / single-instance)
    # publisher = one process owns WebSockets and writes Redis
    # shared = paper instances hydrate from Redis (no WebSockets)
    market_data_mode: Literal["local", "shared", "publisher"] = "local"
    market_data_redis_poll_ms: float = Field(default=100.0, ge=20.0)
    market_data_exchanges: str = "binance,kraken,coinbase,bitvavo"
    market_data_symbols: str = "BTCEUR,BTCUSDT"
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

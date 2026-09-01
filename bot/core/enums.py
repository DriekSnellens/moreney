"""Application enums."""

from enum import StrEnum


class ExecutionMode(StrEnum):
    """Order execution mode."""

    PAPER = "paper"
    LIVE = "live"


class OpportunitySide(StrEnum):
    """Directional intent of a trade opportunity."""

    BUY = "buy"
    SELL = "sell"
    LONG = "long"
    SHORT = "short"


class OrderStatus(StrEnum):
    """Lifecycle status of an order / execution attempt."""

    PENDING = "pending"
    OPEN = "open"
    SUBMITTED = "submitted"  # legacy alias for open/submitted
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"
    SIMULATED = "simulated"  # legacy paper status


class OrderType(StrEnum):
    """Order type for paper / future live execution."""

    MARKET = "market"
    LIMIT = "limit"


class OrderSide(StrEnum):
    """Buy / sell side for execution orders."""

    BUY = "buy"
    SELL = "sell"


class RiskDecisionStatus(StrEnum):
    """Outcome of the risk engine gate."""

    APPROVED = "approved"
    REJECTED = "rejected"


class FeeRole(StrEnum):
    """Liquidity role used to select maker vs taker fee rates."""

    MAKER = "maker"
    TAKER = "taker"


class EntryQualityRecommendation(StrEnum):
    """Entry quality sizing recommendation (downward-only modifier)."""

    REJECT = "reject"
    REDUCED_SIZE = "reduced_size"
    NORMAL_SIZE = "normal_size"


class KillSwitchState(StrEnum):
    """Kill-switch lifecycle. PAUSED / EMERGENCY_STOP block new orders."""

    RUNNING = "running"
    WARNING = "warning"
    PAUSED = "paused"
    EMERGENCY_STOP = "emergency_stop"


class OpportunityLifecycleStatus(StrEnum):
    """Lifecycle of a detected paper-trading opportunity (including rejects)."""

    DETECTED = "detected"
    REJECTED = "rejected"
    APPROVED = "approved"
    EXECUTED = "executed"
    FILLED = "filled"
    PROFITABLE = "profitable"
    UNPROFITABLE = "unprofitable"


class RiskRejectReason(StrEnum):
    """Machine-readable rejection codes for structured logging."""

    KILL_SWITCH = "KILL_SWITCH"
    MAX_POSITION_SIZE = "MAX_POSITION_SIZE"
    MAX_POSITION_PERCENT = "MAX_POSITION_PERCENT"
    MAX_TOTAL_EXPOSURE = "MAX_TOTAL_EXPOSURE"
    MAX_DAILY_LOSS = "MAX_DAILY_LOSS"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    MAX_SIMULTANEOUS_POSITIONS = "MAX_SIMULTANEOUS_POSITIONS"
    MAX_TRADES_PER_MINUTE = "MAX_TRADES_PER_MINUTE"
    EXCHANGE_UNHEALTHY = "EXCHANGE_UNHEALTHY"
    INSUFFICIENT_LIQUIDITY = "INSUFFICIENT_LIQUIDITY"
    EXCESSIVE_SLIPPAGE = "EXCESSIVE_SLIPPAGE"
    EXECUTION_LATENCY = "EXECUTION_LATENCY"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    ABNORMAL_PRICE_MOVEMENT = "ABNORMAL_PRICE_MOVEMENT"
    NOT_PROFITABLE = "NOT_PROFITABLE"
    LEVERAGE_FORBIDDEN = "LEVERAGE_FORBIDDEN"
    UNPROFITABLE_MODIFICATION_FORBIDDEN = "UNPROFITABLE_MODIFICATION_FORBIDDEN"
    CORRELATION_LIMIT = "CORRELATION_LIMIT"
    STRATEGY_EXPOSURE_LIMIT = "STRATEGY_EXPOSURE_LIMIT"
    VENUE_EXPOSURE_LIMIT = "VENUE_EXPOSURE_LIMIT"
    MARKET_CLOSED = "MARKET_CLOSED"
    REGIME_MISMATCH = "REGIME_MISMATCH"
    PORTFOLIO_OPPORTUNITY_COST = "PORTFOLIO_OPPORTUNITY_COST"
    EVENT_RISK = "EVENT_RISK"


class AssetClass(StrEnum):
    """Normalized asset class for multi-market opportunity engine."""

    CRYPTO_SPOT = "crypto_spot"
    CRYPTO_PERP = "crypto_perp"
    FX = "fx"
    EQUITY = "equity"
    INDEX = "index"
    COMMODITY = "commodity"
    BOND = "bond"
    FUTURE = "future"


class MarketSessionPhase(StrEnum):
    """Trading session phase for calendar-aware scanning."""

    CLOSED = "closed"
    PRE_MARKET = "pre_market"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"
    ALWAYS_OPEN = "always_open"


class MarketRegime(StrEnum):
    """Coarse market regime labels for strategy weighting."""

    TRENDING = "trending"
    MEAN_REVERTING = "mean_reverting"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    LIQUIDITY_STRESSED = "liquidity_stressed"
    NORMAL = "normal"
    MOMENTUM = "momentum"
    RANGE_BOUND = "range_bound"
    EVENT_DRIVEN = "event_driven"


class OpportunityDecisionAction(StrEnum):
    """Final gate decision for observability."""

    TAKE = "take"
    REJECT = "reject"
    DEFER = "defer"


class FillType(StrEnum):
    """How a paper maker fill was produced. Not all fills are economically equal."""

    QUEUE = "queue"
    TRADE_THROUGH = "trade_through"
    UNKNOWN = "unknown"


class RouteState(StrEnum):
    """Lifecycle of a venue→venue (or key) trading route under calibration."""

    WARMUP = "warmup"
    ACTIVE = "active"
    WATCH = "watch"
    EARLY_STOPPED = "early_stopped"
    HARD_STOPPED = "hard_stopped"


class RouteDecisionReason(StrEnum):
    """Machine-readable why a route is in its current state / was gated."""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NEGATIVE_RAW_CAPTURE = "negative_raw_capture"
    CUMULATIVE_LOSS = "cumulative_loss"
    CALIBRATED_EV_NEGATIVE = "calibrated_ev_negative"
    TOXIC_MARKOUT = "toxic_markout"
    STALE_MARKET_DATA = "stale_market_data"
    RISK_VIOLATION = "risk_violation"
    POSITIVE_EVIDENCE = "positive_evidence"
    EARLY_RAW_LOSS_OVERRIDES_SHRINKAGE = "early_raw_loss_overrides_shrinkage"


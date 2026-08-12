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

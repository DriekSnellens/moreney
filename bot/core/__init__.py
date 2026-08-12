"""Shared domain types, configuration, interfaces, and exceptions."""

from bot.core.config import Settings, get_settings
from bot.core.enums import (
    ExecutionMode,
    FeeRole,
    OpportunitySide,
    OrderStatus,
    RiskDecisionStatus,
)
from bot.core.exceptions import (
    ConfigurationError,
    MoreneyError,
    RiskRejectedError,
    StrategyError,
)
from bot.core.models import (
    ExecutionResult,
    MarketSnapshot,
    OrderRequest,
    PortfolioSnapshot,
    ProfitEstimate,
    ProfitabilityResult,
    RiskDecision,
    TradeOpportunity,
)

__all__ = [
    "ConfigurationError",
    "ExecutionMode",
    "ExecutionResult",
    "FeeRole",
    "MarketSnapshot",
    "MoreneyError",
    "OpportunitySide",
    "OrderRequest",
    "OrderStatus",
    "PortfolioSnapshot",
    "ProfitEstimate",
    "ProfitabilityResult",
    "RiskDecision",
    "RiskDecisionStatus",
    "RiskRejectedError",
    "Settings",
    "StrategyError",
    "TradeOpportunity",
    "get_settings",
]

"""Domain and application exceptions."""


class MoreneyError(Exception):
    """Base exception for the trading system."""


class ConfigurationError(MoreneyError):
    """Raised when configuration is missing or invalid."""


class StrategyError(MoreneyError):
    """Raised when a strategy fails to evaluate market data."""


class RiskRejectedError(MoreneyError):
    """Raised when the risk engine rejects a trade."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ProfitabilityError(MoreneyError):
    """Raised when profitability cannot be computed."""


class ExecutionError(MoreneyError):
    """Raised when order execution fails."""


class ExchangeError(MoreneyError):
    """Raised when an exchange adapter encounters an error."""


class ExchangeAuthError(ExchangeError):
    """Raised when exchange authentication fails."""


class ExchangeRateLimitError(ExchangeError):
    """Raised when exchange rate limits are exhausted after retries."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class ExchangeTransientError(ExchangeError):
    """Raised for retryable network / temporary exchange failures."""


class ExchangeTradingDisabledError(ExchangeError):
    """Raised when live order placement is attempted while trading is disabled."""

"""Retry helpers with exponential backoff and optional jitter.

Secrets must never be included in retry log messages — callers pass redacted
context only.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from bot.core.exceptions import ExchangeRateLimitError, ExchangeTransientError

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Configurable exponential backoff policy."""

    max_attempts: int = 5
    base_delay: float = 0.25
    max_delay: float = 8.0
    exponential_base: float = 2.0
    jitter: bool = True


DEFAULT_RETRY_POLICY = RetryPolicy()


def compute_backoff(
    attempt: int,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    *,
    retry_after: float | None = None,
) -> float:
    """Return sleep seconds for the given 0-based attempt index."""
    if retry_after is not None and retry_after > 0:
        delay = float(retry_after)
    else:
        delay = policy.base_delay * (policy.exponential_base**attempt)
    delay = min(delay, policy.max_delay)
    if policy.jitter:
        delay = delay * (0.5 + random.random())
    return max(delay, 0.0)


async def with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    operation_name: str = "exchange_call",
    is_retryable: Callable[[BaseException], bool] | None = None,
    get_retry_after: Callable[[BaseException], float | None] | None = None,
) -> T:
    """Execute ``operation`` with exponential backoff on transient failures."""
    retryable = is_retryable or _default_is_retryable
    last_error: BaseException | None = None

    for attempt in range(policy.max_attempts):
        try:
            return await operation()
        except Exception as exc:  # noqa: BLE001 — classified below
            last_error = exc
            if not retryable(exc) or attempt >= policy.max_attempts - 1:
                break
            retry_after = get_retry_after(exc) if get_retry_after else None
            delay = compute_backoff(attempt, policy, retry_after=retry_after)
            logger.warning(
                "%s failed (attempt %s/%s): %s; retrying in %.3fs",
                operation_name,
                attempt + 1,
                policy.max_attempts,
                type(exc).__name__,
                delay,
            )
            await asyncio.sleep(delay)

    assert last_error is not None
    if isinstance(last_error, ExchangeRateLimitError | ExchangeTransientError):
        raise last_error
    raise ExchangeTransientError(
        f"{operation_name} failed after {policy.max_attempts} attempts: {type(last_error).__name__}"
    ) from last_error


def _default_is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, ExchangeTransientError | ExchangeRateLimitError | TimeoutError | OSError)

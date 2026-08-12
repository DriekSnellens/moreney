"""Symbol normalization helpers shared by exchange adapters."""

from __future__ import annotations


def to_internal_symbol(symbol: str) -> str:
    """Normalize to compact internal form, e.g. ``BTCUSDT``."""
    return symbol.upper().replace("-", "").replace("_", "").replace("/", "").strip()


def to_ccxt_symbol(symbol: str) -> str:
    """Convert internal/compact symbols to CCXT ``BASE/QUOTE`` when possible.

    Already-slash-separated symbols are uppercased and returned.
    Heuristic quote suffixes cover common USDT/USDC/USD/EUR/BTC pairs.
    """
    cleaned = symbol.upper().strip().replace("-", "/").replace("_", "/")
    if "/" in cleaned:
        base, quote, *rest = cleaned.split("/")
        if rest:
            return cleaned
        return f"{base}/{quote}"

    compact = to_internal_symbol(cleaned)
    quotes = ("USDT", "USDC", "USD", "EUR", "BTC", "ETH", "EURT", "DAI", "BUSD")
    for quote in quotes:
        if compact.endswith(quote) and len(compact) > len(quote):
            return f"{compact[: -len(quote)]}/{quote}"
    return compact

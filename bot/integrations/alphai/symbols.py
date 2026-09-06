"""Map Moreney EUR pairs to AlphaI crypto tickers (`{BASE}-USD`)."""

from __future__ import annotations

from bot.live.micro_session import _LIQUID_EUR_SYMBOLS

# Moreney venue keys that do not strip cleanly to the AlphaI base symbol.
_BASE_ALIASES: dict[str, str] = {
    "DOGEUR": "DOGE",
}

# AlphaI primary ticker → legacy fallbacks when resolving coverage.
_TICKER_FALLBACKS: dict[str, tuple[str, ...]] = {
    "POL-USD": ("MATIC-USD",),
    "RENDER-USD": ("RNDR-USD",),
}


def eur_symbol_to_base(symbol: str) -> str:
    s = symbol.strip().upper().replace("-", "").replace("/", "")
    if s in _BASE_ALIASES:
        return _BASE_ALIASES[s]
    if s.endswith("EUR"):
        return s.removesuffix("EUR")
    return s


def alphai_crypto_ticker(base: str) -> str:
    return f"{base.strip().upper()}-USD"


def alphai_candidates_for_base(base: str) -> tuple[str, ...]:
    primary = alphai_crypto_ticker(base)
    fallbacks = _TICKER_FALLBACKS.get(primary, ())
    out: list[str] = [primary]
    for fb in fallbacks:
        if fb not in out:
            out.append(fb)
    return tuple(out)


LIQUID_EUR_BASES: tuple[str, ...] = tuple(
    dict.fromkeys(eur_symbol_to_base(sym) for sym in _LIQUID_EUR_SYMBOLS)
)

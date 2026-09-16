"""Instrument registry: crypto (live), FX stub, equity (Nasdaq/Yahoo when enabled)."""

from __future__ import annotations

from bot.core.config import Settings
from bot.core.enums import AssetClass
from bot.markets.instrument import NormalizedInstrument


def _crypto_tier(symbol: str) -> int:
    base = symbol.upper()
    if base.startswith("BTC") or base.startswith("ETH"):
        return 1
    if base.endswith("EUR") or base.endswith("USDT"):
        return 1 if base in {"BTCEUR", "ETHEUR", "BTCUSDT", "ETHUSDT", "EURUSDT"} else 2
    return 2


def _correlation_group(symbol: str, asset_class: AssetClass) -> str:
    sym = symbol.upper()
    if asset_class == AssetClass.CRYPTO_SPOT:
        if "BTC" in sym:
            return "crypto_btc_beta"
        if "ETH" in sym:
            return "crypto_eth_beta"
        return "crypto_alt"
    if asset_class == AssetClass.FX:
        if sym.startswith("EUR"):
            return "fx_eur"
        if sym.startswith("USD"):
            return "fx_usd"
        return "fx_cross"
    if asset_class == AssetClass.EQUITY:
        return "equity_us" if sym.endswith(".US") else "equity_eu"
    return "general"


class InstrumentRegistry:
    """Builds the tradable universe from settings."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._instruments: dict[str, NormalizedInstrument] = {}
        self._build()

    def _build(self) -> None:
        for part in str(self._settings.market_data_symbols or "").split(","):
            sym = part.strip().upper().replace("-", "").replace("/", "")
            if not sym:
                continue
            iid = f"crypto:{sym}"
            tier = _crypto_tier(sym)
            self._instruments[iid] = NormalizedInstrument(
                instrument_id=iid,
                symbol=sym,
                asset_class=AssetClass.CRYPTO_SPOT,
                base_asset=sym.replace("EUR", "").replace("USDT", ""),
                quote_asset="USDT" if sym.endswith("USDT") else "EUR",
                venue="multi",
                liquidity_tier=tier,
                correlation_group=_correlation_group(sym, AssetClass.CRYPTO_SPOT),
                scan_interval_ms=500.0 if tier == 1 else 1000.0,
                session_aware=False,
            )

        if getattr(self._settings, "global_fx_enabled", False):
            for pair in str(self._settings.global_fx_pairs or "").split(","):
                sym = pair.strip().upper().replace("/", "")
                if not sym:
                    continue
                iid = f"fx:{sym}"
                self._instruments[iid] = NormalizedInstrument(
                    instrument_id=iid,
                    symbol=sym,
                    asset_class=AssetClass.FX,
                    base_asset=sym[:3],
                    quote_asset=sym[3:],
                    venue="fx_stub",
                    liquidity_tier=1,
                    correlation_group=_correlation_group(sym, AssetClass.FX),
                    scan_interval_ms=2000.0,
                    session_aware=True,
                )

        if getattr(self._settings, "global_equity_enabled", False):
            for sym in str(self._settings.global_equity_symbols or "").split(","):
                text = sym.strip().upper()
                if not text:
                    continue
                iid = f"equity:{text}"
                self._instruments[iid] = NormalizedInstrument(
                    instrument_id=iid,
                    symbol=text,
                    asset_class=AssetClass.EQUITY,
                    base_asset=text.split(".")[0],
                    quote_asset="USD" if text.endswith(".US") else "EUR",
                    venue="nasdaq" if text.endswith(".US") else "yahoo",
                    liquidity_tier=2,
                    correlation_group=_correlation_group(text, AssetClass.EQUITY),
                    scan_interval_ms=5000.0,
                    session_aware=True,
                )

    def all(self) -> list[NormalizedInstrument]:
        return list(self._instruments.values())

    def get(self, instrument_id: str) -> NormalizedInstrument | None:
        return self._instruments.get(instrument_id)

    def by_symbol(self, symbol: str) -> NormalizedInstrument | None:
        sym = symbol.upper()
        for inst in self._instruments.values():
            if inst.symbol == sym:
                return inst
        return None

    def symbols_for_tier(self, tier: int) -> list[str]:
        return [i.symbol for i in self._instruments.values() if i.liquidity_tier == tier]

    def scan_symbols(self) -> list[str]:
        """Ordered symbol list for tiered scanning (Tier-1 first)."""
        ordered = sorted(self.all(), key=lambda i: (i.liquidity_tier, i.symbol))
        return [i.symbol for i in ordered]

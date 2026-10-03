"""Coin-agnostic pre-spike pattern lab (research only).

Question: can generic tape features + AlphaI predict a forward moonshot
with ~60–70% precision? This module measures that on wet daily candles.
"""

from bot.research.moonshot_preimage.engine import run_moonshot_preimage

__all__ = ["run_moonshot_preimage"]

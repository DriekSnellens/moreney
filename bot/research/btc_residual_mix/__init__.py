"""BTC core + residual-weekly alt sleeve (research only).

Same 20d skip-1 excess pick as residual weekly, but a generic BTC fraction
stays in BTC so the book is not 100% one alt. No per-coin branches.
"""

from bot.research.btc_residual_mix.engine import run_btc_residual, run_exit_scan, run_mix_scan

__all__ = ["run_btc_residual", "run_exit_scan", "run_mix_scan"]

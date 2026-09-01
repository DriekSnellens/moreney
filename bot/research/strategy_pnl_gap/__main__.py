"""CLI: python -m bot.research.strategy_pnl_gap"""

from __future__ import annotations

from bot.research.strategy_pnl_gap.runner import run_analysis


def main() -> None:
    run_analysis()
    print("Wrote docs/PAPER_VS_RESEARCH_PNL_GAP_REPORT.md")


if __name__ == "__main__":
    main()

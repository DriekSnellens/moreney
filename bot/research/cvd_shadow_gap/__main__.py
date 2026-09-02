"""CLI: python -m bot.research.cvd_shadow_gap"""

from __future__ import annotations

from pathlib import Path

from bot.research.cvd_shadow_gap.analyze import analyze_shadow_gap, write_report


def main() -> None:
    analysis = analyze_shadow_gap()
    path = write_report(analysis)
    print(f"Wrote {path}")
    print(analysis.verdict)


if __name__ == "__main__":
    main()

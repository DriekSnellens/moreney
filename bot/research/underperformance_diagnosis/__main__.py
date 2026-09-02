"""CLI: python -m bot.research.underperformance_diagnosis"""

from __future__ import annotations

from pathlib import Path

from bot.research.underperformance_diagnosis.analyze import analyze_underperformance
from bot.research.underperformance_diagnosis.loaders import load_all
from bot.research.underperformance_diagnosis.report import write_report


def main() -> None:
    data = load_all(data_dir=Path("./data"))
    analysis = analyze_underperformance(data)
    md, js = write_report(analysis)
    print(f"Wrote {md}")
    print(f"Wrote {js}")
    print(f"Verdict: {analysis.verdict[:200]}...")


if __name__ == "__main__":
    main()

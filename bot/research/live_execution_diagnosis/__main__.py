"""CLI: python -m bot.research.live_execution_diagnosis"""

from __future__ import annotations

from bot.research.live_execution_diagnosis.runner import run_diagnosis


def main() -> None:
    payload = run_diagnosis()
    out = payload.get("inputs") or {}
    print(f"Wrote diagnosis JSON and markdown report")
    print(f"  audit: {out.get('audit_path')}")


if __name__ == "__main__":
    main()

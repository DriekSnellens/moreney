"""Deprecated entrypoint — delegates to causal_walkforward.

Kept so existing docs/commands still work. Do not add non-causal logic here.
"""

from __future__ import annotations

import sys

from bot.opportunity.causal_walkforward import main as causal_main


def main(argv: list[str] | None = None) -> int:
    return causal_main(argv or sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())

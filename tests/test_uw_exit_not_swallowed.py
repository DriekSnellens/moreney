"""UW / cut-loss exits must not be swallowed by the BE+ harvest else-continue."""

from __future__ import annotations

import ast
from pathlib import Path


def test_harvest_chain_gated_on_not_reason() -> None:
    """Regression: underwater trail_uw_recycle was discarded by harvest else:continue.

    The BE+ harvest if/elif chain must only run when no sleeve-loss reason is set.
    """
    src = Path("bot/live/micro_bridge_executor.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if node.name != "check_trailing_take_profits":
            continue
        text = ast.get_source_segment(src, node) or ""
        # Sleeve-loss reasons must reach TRAIL_EXIT_QUOTE without harvest else continue.
        assert "trail_uw_recycle / cut-loss sells for underwater AlphaI rotates" in text
        assert "BE+ harvest chain must not run" in text
        found = True
    assert found


def test_hard_partial_does_not_precede_uw_without_not_reason_guard() -> None:
    """hard_armed elif used to overwrite trail_uw_recycle (no not-reason guard)."""
    src = Path("bot/live/micro_bridge_executor.py").read_text(encoding="utf-8")
    # After the fix, hard_partial assignment sits inside `if not reason:`.
    idx_comment = src.find("BE+ harvest chain must not run")
    idx_hard = src.find('reason = "trail_hard_partial"', idx_comment)
    idx_uw_quote = src.find('reason == "trail_uw_recycle"', idx_comment)
    assert idx_comment > 0
    assert idx_hard > idx_comment
    assert idx_uw_quote > idx_hard

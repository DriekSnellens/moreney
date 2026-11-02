"""Wet residual_full replay with archived AlphaI buy and avoid sets.

Comparison is the overlap the archive actually covers. Days with no archived
rows are left on the residual rank. Nothing here is wired into the live book.
"""

from __future__ import annotations

from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.alphai_crypto_feeds.features import MODES, WINDOWS, allow_map
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import WET, bar_date
from bot.research.factor_scoreboard.engine import LIVE_KNOBS


def _strip(row: dict[str, Any]) -> dict[str, Any]:
    kept = {
        k: v
        for k, v in row.items()
        if k
        in {
            "strategy",
            "pnl_eur",
            "pnl_pct",
            "max_dd_pct",
            "calmar",
            "n_trades",
            "end_eur",
            "end_hold",
        }
    }
    return kept


def decision_dates(ohlc: dict[str, list[list[float]]], *, start: str, end: str) -> list[str]:
    out: list[str] = []
    for row in ohlc.get("BTC") or []:
        day = bar_date(row)
        if start <= day <= end:
            out.append(day)
    return out


def run_replay(
    ohlc: dict[str, list[list[float]]],
    rows: list[dict[str, Any]],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
) -> dict[str, Any]:
    dates = decision_dates(ohlc, start=start, end=end)
    universe = {base for base in DEFAULT_UNIVERSE if base in ohlc}
    knobs = dict(LIVE_KNOBS)
    off = _strip(
        run_btc_residual(
            ohlc,
            start=start,
            end=end,
            book_eur=book_eur,
            model=WET,
            strategy="off",
            **knobs,
        )
    )
    cells: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}
    for days in WINDOWS:
        for mode in MODES:
            allow = allow_map(rows, dates, days=days, mode=mode, universe=universe)
            alt_mode = "gate" if mode == "avoid" else mode
            name = f"w{days}_{mode}"
            row = _strip(
                run_btc_residual(
                    ohlc,
                    start=start,
                    end=end,
                    book_eur=book_eur,
                    model=WET,
                    strategy=name,
                    alt_allow=allow,
                    alt_allow_mode=alt_mode,
                    **knobs,
                )
            )
            covered = len(allow)
            empty_buy = 0
            if mode != "avoid":
                empty_buy = sum(1 for names in allow.values() if not names)
            row.update(
                {
                    "name": name,
                    "window_days": days,
                    "mode": mode,
                    "covered_days": covered,
                    "empty_buy_days": empty_buy,
                    "decision_days": len(dates),
                }
            )
            cells.append(row)
            coverage[name] = {
                "covered_days": covered,
                "empty_buy_days": empty_buy,
                "decision_days": len(dates),
            }
    return {
        "start": start,
        "end": end,
        "book_eur": book_eur,
        "decision_days": len(dates),
        "off": off,
        "cells": cells,
        "coverage": coverage,
    }

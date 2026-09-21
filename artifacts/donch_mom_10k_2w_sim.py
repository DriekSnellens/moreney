#!/usr/bin/env python3
"""Replay last 14 days: €10k Donchian mix + €10k 15m momentum desk.

Independent books (side-by-side). Live mix map on the Donchian book;
live-micro knobs on the 15m book, scaled to €10k. No AlphaI timeline.
Does not change the live engine.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from bot.live.desk_allocator import classify_sma20_50
from bot.live.momentum_desk import BAR_MS, DEFAULT_UNIVERSE, DeskConfig
from bot.live.momentum_donchian import (
    DonchianConfig,
    DonchianPosition,
    evaluate_donchian,
    loop_sleeve_configs,
)
from bot.live.momentum_short_weakest import fetch_daily_ohlc
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).with_suffix(".json")
DAYS = 14
DONCH_BOOK = 10_000.0
MOM_BOOK = 10_000.0
FEE_RT = 0.003


def live_mom_cfg(book: float) -> DeskConfig:
    scale = book / 20_000.0
    return DeskConfig().with_overrides(
        decision_hours_utc=(7, 13, 16),
        decision_interval_sec=0.0,
        refill_on_exit=True,
        clip_eur=book,
        book_eur=book,
        max_positions=1,
        min_excess=0.025,
        entry_fee_buffer_mult=6.0,
        max_chase_ret_24h=0.09,
        chase_near_high=0.008,
        trail_pct=0.05,
        trail_tight_after=0.04,
        trail_tight_pct=0.02,
        hard_stop_pct=0.03,
        hard_stop_eur=round(300.0 * scale, 2),
        early_stop_pct=0.0,
        time_exit_hours=36.0,
        midflat_hours=0.0,
        green_deadline_hours=0.0,
        be_arm_peak_pct=0.02,
        partial_take_pct=0.025,
        partial_frac=0.40,
        fade_eta_sec=0.0,
        day_loss_limit_eur=round(750.0 * scale, 2),
        week_loss_limit_eur=round(2000.0 * scale, 2),
        skip_weekend_entries=False,
        soft_regime_on_weak_tape=True,
        soft_regime_clip_mult=0.5,
        weak_tape_idle_on_double=True,
        soft_regime_idle_on_macro_caution=True,
        soft_regime_fee_buffer_mult=6.0,
        macro_caution_mode="reduce",
        macro_caution_requires_alphai_pick=False,
        requires_alphai_pick=False,
        strong_clip_mult=1.3,
        weak_clip_mult=0.7,
        alphai_clip_mult=1.3,
        alphai_size_mode="conviction",
        fee_rt=FEE_RT,
    )


@dataclass
class _Sleeve:
    cfg: DonchianConfig
    cash: float = 0.0
    book: float = 0.0
    positions: list[DonchianPosition] = field(default_factory=list)
    realized: float = 0.0
    trades: list[dict[str, Any]] = field(default_factory=list)

    def deployed(self) -> float:
        return sum(p.notional_eur for p in self.positions)


def _close_pos(sl: _Sleeve, pos: DonchianPosition, px: float, reason: str, ts_ms: int) -> None:
    ret = pos.long_return(px)
    net = pos.notional_eur * ret - pos.notional_eur * FEE_RT
    proceeds = pos.notional_eur * (px / pos.entry_price) * (1.0 - FEE_RT / 2)
    sl.cash += proceeds
    sl.realized += net
    sl.positions = [p for p in sl.positions if p.holding_id != pos.holding_id]
    sl.trades.append(
        {
            "event": "exit",
            "sleeve": sl.cfg.name,
            "base": pos.base,
            "reason": reason,
            "entry_price": round(pos.entry_price, 6),
            "exit_price": round(px, 6),
            "notional_eur": round(pos.notional_eur, 2),
            "net_eur": round(net, 2),
            "opened_ms": pos.opened_ms,
            "closed_ms": ts_ms,
            "hold_h": round((ts_ms - pos.opened_ms) / 3_600_000, 2),
        }
    )


def _open_pos(sl: _Sleeve, row: Mapping[str, Any], px: float, ts_ms: int) -> None:
    notional = float(row["notional_eur"])
    cost = notional * (1.0 + FEE_RT / 2)
    if cost > sl.cash + 1e-6 or px <= 0:
        return
    sl.cash -= cost
    pos = DonchianPosition(
        base=str(row["base"]),
        entry_price=px,
        notional_eur=notional,
        opened_ms=ts_ms,
        entry_reason=",".join(str(x) for x in (row.get("reasons") or [])),
        sleeve=sl.cfg.name,
        venue="sim",
    )
    sl.positions.append(pos)
    sl.trades.append(
        {
            "event": "entry",
            "sleeve": sl.cfg.name,
            "base": pos.base,
            "reason": pos.entry_reason,
            "entry_price": round(px, 6),
            "notional_eur": round(notional, 2),
            "opened_ms": ts_ms,
        }
    )


def _marks(ohlc: dict[str, list], i: int) -> dict[str, float]:
    out: dict[str, float] = {}
    for b, rows in ohlc.items():
        if 0 <= i < len(rows) and float(rows[i][4]) > 0:
            out[b] = float(rows[i][4])
    return out


def sim_donchian_mix(
    ohlc: dict[str, list[list[float]]],
    *,
    book: float,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    cfgs = {c.name: c for c in loop_sleeve_configs()}
    sleeves = {name: _Sleeve(cfg=cfg) for name, cfg in cfgs.items()}
    sleeves["donch_fri10"].book = sleeves["donch_fri10"].cash = book * 0.5
    sleeves["donch10"].book = sleeves["donch10"].cash = book * 0.5
    btc_rows = ohlc.get("BTC") or []
    if len(btc_rows) < 55:
        raise RuntimeError("need ≥55 BTC daily bars for SMA50 warmup")
    ts = [int(r[0]) for r in btc_rows]
    # Align other bases to BTC index (same UTC day open); missing → skip that name.
    aligned: dict[str, list[list[float]]] = {"BTC": btc_rows}
    btc_ts = {int(r[0]): r for r in btc_rows}
    for base, rows in ohlc.items():
        by = {int(r[0]): r for r in rows}
        aligned[base] = [by[t] if t in by else [t, 0, 0, 0, 0, 0] for t in ts]

    i0 = next((i for i, t in enumerate(ts) if t >= start_ms), None)
    i1 = next((i for i in range(len(ts) - 1, -1, -1) if ts[i] <= end_ms), None)
    if i0 is None or i1 is None or i1 < i0:
        raise RuntimeError("window not in daily bars")

    daily: list[dict[str, Any]] = []
    last_label = ""
    for i in range(i0, i1 + 1):
        now = datetime.fromtimestamp(ts[i] / 1000, UTC) + timedelta(days=1, minutes=5)
        hist = {b: rows[: i + 1] for b, rows in aligned.items()}
        btc_closes = [float(r[4]) for r in aligned["BTC"][: i + 1] if float(r[4]) > 0]
        snap = classify_sma20_50(btc_closes)
        label = str(snap.get("label") or "mid")
        # Side-by-side 10k Donchian = the live risk_on split, always on.
        # Regime flatten-to-short is not part of this 10k sleeve.
        wmap = {"donch_fri10": 0.5, "donch10": 0.5}
        label = str(snap.get("label") or "mid")
        px = _marks(aligned, i)
        idle_cash = 0.0
        for name, sl in sleeves.items():
            if sl.book < sl.cfg.min_notional_eur:
                continue
            held = {p.base for p in sl.positions}
            dec = evaluate_donchian(
                hist,
                btc_closes,
                sl.cfg,
                held=held,
                cash_eur=sl.cash,
                deployed_eur=sl.deployed(),
                now=now,
            )
            for ex in dec.get("exits") or []:
                pos = next((p for p in sl.positions if p.base == ex["base"]), None)
                if pos:
                    _close_pos(
                        sl,
                        pos,
                        px.get(pos.base) or pos.entry_price,
                        str(ex.get("reason") or "exit"),
                        ts[i],
                    )
            for row in dec.get("entries") or []:
                base = str(row["base"])
                if base not in px or px[base] <= 0:
                    continue
                _open_pos(sl, row, px[base], ts[i])
        mtm = 0.0
        for sl in sleeves.values():
            for p in sl.positions:
                mark = px.get(p.base) or p.entry_price
                mtm += p.unrealized_net(mark, FEE_RT)
        realized = sum(sl.realized for sl in sleeves.values())
        cash = sum(sl.cash for sl in sleeves.values()) + idle_cash
        deployed = sum(sl.deployed() for sl in sleeves.values())
        daily.append(
            {
                "day": datetime.fromtimestamp(ts[i] / 1000, UTC).date().isoformat(),
                "regime": label,
                "why": snap.get("why"),
                "realized_eur": round(realized, 2),
                "open_mtm_eur": round(mtm, 2),
                "equity_eur": round(cash + deployed + mtm, 2),
                "deployed_eur": round(deployed, 2),
                "idle_cash_eur": round(idle_cash, 2),
            }
        )
        last_label = label

    last_px = _marks(aligned, i1)
    open_rows = []
    mtm = 0.0
    for sl in sleeves.values():
        for p in sl.positions:
            mark = last_px.get(p.base) or p.entry_price
            u = p.unrealized_net(mark, FEE_RT)
            mtm += u
            open_rows.append(
                {
                    "sleeve": sl.cfg.name,
                    "base": p.base,
                    "entry_price": round(p.entry_price, 6),
                    "mark": round(mark, 6),
                    "notional_eur": round(p.notional_eur, 2),
                    "unrealized_net_eur": round(u, 2),
                    "opened": datetime.fromtimestamp(p.opened_ms / 1000, UTC).isoformat(),
                }
            )
    exits = [t for sl in sleeves.values() for t in sl.trades if t["event"] == "exit"]
    entries = [t for sl in sleeves.values() for t in sl.trades if t["event"] == "entry"]
    realized = sum(sl.realized for sl in sleeves.values())
    by_reason: dict[str, dict[str, float | int]] = defaultdict(lambda: {"n": 0, "net_eur": 0.0})
    by_sleeve: dict[str, dict[str, float | int]] = defaultdict(lambda: {"n": 0, "net_eur": 0.0})
    for t in exits:
        by_reason[str(t["reason"])]["n"] += 1
        by_reason[str(t["reason"])]["net_eur"] += float(t["net_eur"])
        by_sleeve[str(t["sleeve"])]["n"] += 1
        by_sleeve[str(t["sleeve"])]["net_eur"] += float(t["net_eur"])
    wins = sum(1 for t in exits if float(t["net_eur"]) > 0)
    return {
        "book_eur": book,
        "end_regime": last_label,
        "realized_eur": round(realized, 2),
        "open_mtm_eur": round(mtm, 2),
        "total_eur": round(realized + mtm, 2),
        "trades": len(exits),
        "entries": len(entries),
        "win_rate": round(wins / len(exits), 3) if exits else None,
        "by_reason": {
            k: {"n": int(v["n"]), "net_eur": round(float(v["net_eur"]), 2)}
            for k, v in sorted(by_reason.items(), key=lambda kv: -float(kv[1]["net_eur"]))
        },
        "by_sleeve": {
            k: {"n": int(v["n"]), "net_eur": round(float(v["net_eur"]), 2)}
            for k, v in by_sleeve.items()
        },
        "open": open_rows,
        "exits": exits,
        "entries": entries,
        "daily": daily,
        "sleeve_cash": {n: round(s.cash, 2) for n, s in sleeves.items()},
    }


def pack_mom(res: Any, cfg: DeskConfig) -> dict[str, Any]:
    s = res.summary()
    trades = []
    by_reason: dict[str, dict[str, float | int]] = defaultdict(lambda: {"n": 0, "net_eur": 0.0})
    for t in res.closed:
        trades.append(
            {
                "base": t.base,
                "opened": datetime.fromtimestamp(t.opened_ms / 1000, UTC).isoformat(),
                "closed": datetime.fromtimestamp(t.closed_ms / 1000, UTC).isoformat(),
                "hold_h": round((t.closed_ms - t.opened_ms) / 3_600_000, 2),
                "notional_eur": round(t.notional_eur, 2),
                "net_eur": round(t.net_eur, 2),
                "reason": t.reason,
                "entry_reason": t.entry_reason,
                "gross_pct": round(100 * t.gross_return, 2),
            }
        )
        by_reason[t.reason]["n"] += 1
        by_reason[t.reason]["net_eur"] += t.net_eur
    open_mtm = float(s.get("open_mtm_eur") or 0)
    realized = float(s.get("realized_eur") or s.get("total_eur") or 0)
    if "open_mtm_eur" in s and "realized_eur" in s:
        total = realized + open_mtm
    else:
        total = float(s.get("total_eur") or 0)
        realized = total - open_mtm
    return {
        "book_eur": cfg.book_eur,
        "clip_eur": cfg.clip_eur,
        "summary": s,
        "realized_eur": round(realized, 2),
        "open_mtm_eur": round(open_mtm, 2),
        "total_eur": round(total, 2),
        "trades": len(res.closed),
        "win_rate": s.get("win_rate"),
        "by_reason": {
            k: {"n": int(v["n"]), "net_eur": round(float(v["net_eur"]), 2)}
            for k, v in sorted(by_reason.items(), key=lambda kv: -float(kv[1]["net_eur"]))
        },
        "open": list(getattr(res, "open_mtm", []) or []),
        "trades_closed": trades,
    }


def main() -> None:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=DAYS)
    end_ms = int(end.timestamp() * 1000) // BAR_MS * BAR_MS
    start_ms = int(start.timestamp() * 1000)
    print(
        f"window {start.date()} → {end.date()} ({DAYS}d)",
        flush=True,
    )
    mom_cfg = live_mom_cfg(MOM_BOOK)
    print("loading 15m candles…", flush=True)
    candles = load_candles(
        ("BTC", *mom_cfg.universe), days=DAYS + 10, end_ms=end_ms, refresh=False
    )
    print("simulating 15m momentum €10k…", flush=True)
    mom = simulate(candles, mom_cfg, start_ms=start_ms, end_ms=end_ms, alphai=None)
    mom_pack = pack_mom(mom, mom_cfg)

    print("loading daily OHLC…", flush=True)
    ohlc: dict[str, list[list[float]]] = {}
    for base in ("BTC", *DEFAULT_UNIVERSE):
        try:
            ohlc[base] = fetch_daily_ohlc(base, days=90)
            time.sleep(0.12)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {base}: {exc}", flush=True)
    print("simulating Donchian mix €10k…", flush=True)
    donch = sim_donchian_mix(ohlc, book=DONCH_BOOK, start_ms=start_ms, end_ms=end_ms)

    combined_realized = donch["realized_eur"] + mom_pack["realized_eur"]
    combined_mtm = donch["open_mtm_eur"] + mom_pack["open_mtm_eur"]
    combined_total = donch["total_eur"] + mom_pack["total_eur"]
    out = {
        "asof": datetime.now(UTC).isoformat(),
        "window": {
            "days": DAYS,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "capital": {
            "donchian_eur": DONCH_BOOK,
            "momentum_eur": MOM_BOOK,
            "combined_eur": DONCH_BOOK + MOM_BOOK,
        },
        "caveats": [
            "Independent €10k books; combined assumes cash is not shared on Bitvavo.",
            "Donchian: 5k Friday-10/5 + 5k 10/5, always on (no flatten-to-short). Daily close fills, fee_rt=0.3%.",
            "15m desk: Bitvavo 15m close fills (no maker/taker path), live knobs scaled to €10k.",
            "No AlphaI timeline — live 15m overlay (rank/size/avoid/macro) is missing.",
            "Live Monday kick-fill is not modeled; sim enters at the close the live desk missed.",
        ],
        "donchian_10k": donch,
        "momentum_10k": mom_pack,
        "combined_20k": {
            "realized_eur": round(combined_realized, 2),
            "open_mtm_eur": round(combined_mtm, 2),
            "total_eur": round(combined_total, 2),
            "return_on_20k_pct": round(100.0 * combined_total / (DONCH_BOOK + MOM_BOOK), 3),
        },
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({
        "wrote": str(OUT),
        "donchian": {
            "realized": donch["realized_eur"],
            "mtm": donch["open_mtm_eur"],
            "total": donch["total_eur"],
            "trades": donch["trades"],
        },
        "momentum": {
            "realized": mom_pack["realized_eur"],
            "mtm": mom_pack["open_mtm_eur"],
            "total": mom_pack["total_eur"],
            "trades": mom_pack["trades"],
        },
        "combined_total": out["combined_20k"],
    }, indent=2))


if __name__ == "__main__":
    main()

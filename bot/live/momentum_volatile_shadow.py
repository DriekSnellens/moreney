"""Paper shadow of a volatile midcap book — no live orders.

Replays the same Momentum Desk entry/exit rules on a pool *outside* the
core 16, so the operator can see day-by-day would-have buys and P&L without
touching the live book.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import (
    BAR_MS,
    DEFAULT_CLUSTERS,
    DEFAULT_UNIVERSE,
    AlphaIView,
    DeskConfig,
)
from bot.research.momentum_backtest.engine import load_candles, simulate

# Liquid-enough names outside the core 16 (Bitvavo EUR, candle cache).
VOLATILE_POOL: tuple[str, ...] = (
    "HYPE",
    "TAO",
    "WLD",
    "RAY",
    "ONDO",
    "XLM",
    "INJ",
    "HBAR",
    "JUP",
    "PEPE",
    "APT",
    "SEI",
    "ENA",
    "RENDER",
    "TIA",
    "TRX",
    "AAVE",
    "BCH",
)

_VOLATILE_CLUSTERS: dict[str, str] = {
    **dict(DEFAULT_CLUSTERS),
    "HYPE": "PERP",
    "TAO": "AI",
    "WLD": "AI",
    "RAY": "DEFI",
    "ONDO": "DEFI",
    "XLM": "L1",
    "INJ": "L1",
    "HBAR": "L1",
    "JUP": "DEFI",
    "PEPE": "MEME",
    "APT": "L1",
    "SEI": "L1",
    "ENA": "DEFI",
    "RENDER": "AI",
    "TIA": "L1",
    "TRX": "PAY",
    "AAVE": "DEFI",
    "BCH": "PAY",
}

_DEFAULT_ALPHAI_PATH = Path("data/alphai/daily_recommendations.json")


def volatile_universe() -> tuple[str, ...]:
    """Pool minus anything that is already in the live core universe."""
    core = set(DEFAULT_UNIVERSE)
    return tuple(b for b in VOLATILE_POOL if b not in core)


def _norm_base(raw: Any) -> str | None:
    if isinstance(raw, Mapping):
        raw = raw.get("base") or raw.get("symbol") or raw.get("ticker")
    text = str(raw or "").strip().upper().replace("-EUR", "").replace("EUR", "")
    return text or None


def load_shadow_alphai(
    path: str | Path | None = None,
) -> tuple[AlphaIView, dict[str, Any]]:
    """Load AlphaI picks/avoid/watch for the shadow book.

    Watch names with a non-negative score are treated as soft picks so the
    volatile sim leans into AlphaI's directional view, not only the top-N buys.
    """
    p = Path(path) if path else _DEFAULT_ALPHAI_PATH
    meta: dict[str, Any] = {
        "path": str(p),
        "loaded": False,
        "generated_at": None,
        "picks": [],
        "avoid": [],
        "watch": [],
        "macro_caution": False,
        "note": "",
    }
    if not p.exists():
        meta["note"] = "AlphaI-bestand ontbreekt — shadow draait zonder news-bias"
        return AlphaIView(), meta
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        meta["note"] = f"AlphaI onleesbaar: {exc}"
        return AlphaIView(), meta

    base_view = AlphaIView.from_recommendations(raw if isinstance(raw, Mapping) else None)
    picks: set[str] = set(base_view.picks)
    avoid: set[str] = set(base_view.avoid)
    watch: set[str] = set()
    pick_rows: list[dict[str, Any]] = []
    avoid_rows: list[dict[str, Any]] = []
    watch_rows: list[dict[str, Any]] = []

    def _row(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, Mapping):
            b = _norm_base(item)
            return {"base": b, "score": None} if b else None
        b = _norm_base(item)
        if not b:
            return None
        score = item.get("score")
        try:
            score_f = float(score) if score is not None else None
        except (TypeError, ValueError):
            score_f = None
        return {
            "base": b,
            "score": score_f,
            "rank": item.get("rank"),
            "bullish": list(item.get("bullish_headlines") or [])[:2],
            "bearish": list(item.get("bearish_headlines") or [])[:2],
        }

    if isinstance(raw, Mapping):
        for item in raw.get("picks") or []:
            row = _row(item)
            if row:
                picks.add(row["base"])
                pick_rows.append(row)
        for item in raw.get("avoid") or []:
            row = _row(item)
            if row:
                avoid.add(row["base"])
                avoid_rows.append(row)
        for item in raw.get("watch") or []:
            row = _row(item)
            if not row:
                continue
            watch.add(row["base"])
            watch_rows.append(row)
            # Soft-positive watch → treat as pick for the volatile shadow.
            if row["score"] is None or row["score"] >= 0:
                picks.add(row["base"])

    # Never long something AlphaI marks avoid.
    picks -= avoid
    view = AlphaIView(
        picks=frozenset(picks),
        avoid=frozenset(avoid),
        macro_caution=bool(base_view.macro_caution),
    )
    meta.update(
        {
            "loaded": True,
            "generated_at": (raw.get("generated_at") if isinstance(raw, Mapping) else None),
            "picks": pick_rows,
            "avoid": avoid_rows,
            "watch": watch_rows,
            "macro_caution": view.macro_caution,
            "effective_picks": sorted(view.picks),
            "effective_avoid": sorted(view.avoid),
            "note": (
                "AlphaI snapshot over heel venster gezet (geen historische dagfiles). "
                "Picks/watch → clip-boost & tiebreak; avoid → geen entry + strakkere trail."
            ),
        }
    )
    return view, meta


def shadow_universe(alphai: AlphaIView | None = None) -> tuple[str, ...]:
    """Volatile midcaps + non-core AlphaI picks (so news leaders can appear)."""
    core = set(DEFAULT_UNIVERSE)
    out: list[str] = [b for b in VOLATILE_POOL if b not in core]
    if alphai is not None:
        for b in sorted(alphai.picks):
            if b not in core and b not in out:
                out.append(b)
    return tuple(out)


def shadow_config(
    live_cfg: DeskConfig | None = None,
    *,
    alphai: AlphaIView | None = None,
) -> DeskConfig:
    """Live desk rules on the volatile(+AlphaI) pool; stronger pick sizing."""
    uni = shadow_universe(alphai)
    clusters = {k: v for k, v in _VOLATILE_CLUSTERS.items() if k in uni}
    for b in uni:
        clusters.setdefault(b, "OTHER")
    if live_cfg is None:
        return DeskConfig(
            decision_hours_utc=(7, 13),
            clip_eur=1300.0,
            alphai_clip_mult=1.5,
            max_positions=4,
            top_n=2,
            top_n_broad=3,
            min_excess=0.015,
            max_from_high=0.02,
            min_volume_eur=500_000.0,
            day_loss_limit_eur=100.0,
            week_loss_limit_eur=250.0,
            book_eur=4000.0,
            skip_weekend_entries=True,
            alphai_avoid_tightens_trail=True,
            universe=uni,
            clusters=clusters,
        )
    return replace(
        live_cfg,
        universe=uni,
        clusters=clusters,
        min_volume_eur=min(float(live_cfg.min_volume_eur), 500_000.0),
        book_eur=float(live_cfg.book_eur) or 4000.0,
        alphai_clip_mult=max(float(live_cfg.alphai_clip_mult), 1.5),
        alphai_avoid_tightens_trail=True,
    )


def _day_key(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _candle_cache_dir() -> Path:
    """Prefer the shared research cache; fall back to /tmp if not writable."""
    preferred = Path("./data/momentum_candles")
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".shadow_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return preferred
    except OSError:
        fallback = Path("/tmp/moreney_volatile_candles")
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def build_volatile_shadow(
    *,
    days: int = 14,
    live_cfg: DeskConfig | None = None,
    end_ms: int | None = None,
    refresh: bool = False,
    alphai_path: str | Path | None = None,
) -> dict[str, Any]:
    """Replay the volatile pool and return a day-by-day would-have report."""
    alphai, alphai_meta = load_shadow_alphai(alphai_path)
    cfg = shadow_config(live_cfg, alphai=alphai)
    uni = cfg.universe
    if not uni:
        return {
            "ok": False,
            "reason": "empty_volatile_universe",
            "universe": [],
            "alphai": alphai_meta,
        }
    end = end_ms if end_ms is not None else int(datetime.now(UTC).timestamp() * 1000)
    end = end // BAR_MS * BAR_MS
    start = end - max(1, int(days)) * 86_400_000
    candles = load_candles(
        ("BTC", *uni),
        days=max(int(days) + 2, 5),
        end_ms=end,
        refresh=refresh,
        cache_dir=_candle_cache_dir(),
    )
    res = simulate(candles, cfg, start_ms=start, end_ms=end, alphai=alphai)

    # Index decisions / entries / exits by UTC day.
    by_day: dict[str, dict[str, Any]] = {}

    def slot(day: str) -> dict[str, Any]:
        if day not in by_day:
            by_day[day] = {
                "day": day,
                "regime_on": 0,
                "regime_off": 0,
                "would_buy": [],
                "exits": [],
                "day_net_eur": 0.0,
            }
        return by_day[day]

    for d in res.decisions:
        day = _day_key(d.t_ms)
        s = slot(day)
        if d.regime_ok:
            s["regime_on"] += 1
        else:
            s["regime_off"] += 1
        for raw in d.entries:
            token = str(raw)
            base = token.split("@", 1)[0].upper()
            clip = None
            if "@" in token:
                try:
                    clip = float(token.split("@", 1)[1])
                except ValueError:
                    clip = None
            s["would_buy"].append(
                {
                    "base": base,
                    "at": _iso(d.t_ms),
                    "ts_ms": d.t_ms,
                    "notional_eur": clip,
                    "btc_ret": round(float(d.btc_ret), 4) if d.btc_ret is not None else None,
                    "breadth": round(float(d.breadth), 3),
                    "alphai_pick": base in alphai.picks,
                    "alphai_avoid": base in alphai.avoid,
                    "alphai_bias": (
                        "up"
                        if base in alphai.picks
                        else "down"
                        if base in alphai.avoid
                        else "neutral"
                    ),
                }
            )

    # Attach clip / entry reason from closed trades + open MTM.
    trade_by_open: dict[tuple[str, int], Any] = {}
    for t in res.closed:
        trade_by_open[(t.base, t.opened_ms)] = t
        day = _day_key(t.closed_ms)
        s = slot(day)
        s["exits"].append(
            {
                "base": t.base,
                "opened": _iso(t.opened_ms),
                "closed": _iso(t.closed_ms),
                "hold_h": round((t.closed_ms - t.opened_ms) / 3_600_000, 2),
                "notional_eur": round(t.notional_eur, 2),
                "gross_return": round(t.gross_return, 4),
                "peak_return": round(t.peak_return, 4),
                "net_eur": round(t.net_eur, 2),
                "exit_reason": t.reason,
                "entry_reason": t.entry_reason,
            }
        )
        s["day_net_eur"] = round(float(s["day_net_eur"]) + t.net_eur, 2)

    for buy in (b for s in by_day.values() for b in s["would_buy"]):
        # Match simulate entry time (decision bar) to trade open.
        key = (buy["base"], int(buy["ts_ms"]))
        t = trade_by_open.get(key)
        if t is None:
            from bot.research.momentum_backtest.engine import _fmt as _engine_fmt

            opened_label = _engine_fmt(int(buy["ts_ms"]))
            for m in res.open_mtm:
                if m.get("base") == buy["base"] and str(m.get("opened")) == opened_label:
                    buy["status"] = "open"
                    buy["unrealized_net_eur"] = round(float(m.get("net_eur") or 0), 2)
                    break
            else:
                buy["status"] = "planned"
            continue
        buy["notional_eur"] = round(t.notional_eur, 2)
        buy["status"] = "closed"
        buy["net_eur"] = round(t.net_eur, 2)
        buy["exit_reason"] = t.reason
        buy["closed"] = _iso(t.closed_ms)
        buy["entry_reason"] = t.entry_reason

    days_out = [by_day[k] for k in sorted(by_day.keys(), reverse=True)]
    summary = res.summary()
    sleeve_net = round(sum(t.net_eur for t in res.closed), 2)
    pick_trades = [t for t in res.closed if "alphai_pick" in str(t.entry_reason)]
    return {
        "ok": True,
        "shadow": True,
        "label": "Volatile shadow + AlphaI (geen echte orders)",
        "universe": list(uni),
        "alphai": alphai_meta,
        "window": {
            "start": _iso(start),
            "end": _iso(end),
            "days": int(days),
        },
        "config": {
            "decision_hours_utc": list(cfg.decision_hours_utc),
            "clip_eur": cfg.clip_eur,
            "book_eur": cfg.book_eur,
            "min_volume_eur": cfg.min_volume_eur,
            "min_excess": cfg.min_excess,
            "max_from_high": cfg.max_from_high,
            "trail_pct": cfg.trail_pct,
            "hard_stop_pct": cfg.hard_stop_pct,
            "alphai_clip_mult": cfg.alphai_clip_mult,
        },
        "summary": {
            **summary,
            "volatile_net_eur": sleeve_net,
            "open_positions": len(res.open_mtm),
            "alphai_pick_trades": len(pick_trades),
            "alphai_pick_net_eur": round(sum(t.net_eur for t in pick_trades), 2),
        },
        "open": res.open_mtm,
        "days": days_out,
        "trades": [t.as_row() for t in res.closed],
    }


def render_volatile_shadow_html(payload: Mapping[str, Any]) -> str:
    """Standalone HTML body fragment for the volatile shadow page."""
    from html import escape

    if not payload.get("ok"):
        return (
            f'<div class="hint bad">Shadow niet beschikbaar: '
            f"{escape(str(payload.get('reason') or 'onbekend'))}</div>"
        )

    summary = payload.get("summary") or {}
    window = payload.get("window") or {}
    cfg = payload.get("config") or {}
    alphai = payload.get("alphai") or {}
    uni = ", ".join(escape(str(b)) for b in (payload.get("universe") or []))

    def eur(v: Any, *, signed: bool = True) -> str:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return "—"
        return f"{x:+,.2f} €" if signed else f"{x:,.2f} €"

    def cls(v: Any) -> str:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return ""
        return "good" if x > 0 else "bad" if x < 0 else ""

    def bias_tag(b: Mapping[str, Any]) -> str:
        bias = str(b.get("alphai_bias") or "neutral")
        if bias == "up" or b.get("alphai_pick"):
            return " <span class='good'>AlphaI ↑</span>"
        if bias == "down" or b.get("alphai_avoid"):
            return " <span class='bad'>AlphaI ↓</span>"
        return ""

    alphai_html = (
        '<div class="card section"><div class="card-head"><h2>AlphaI bias</h2>'
        f'<span class="muted">{escape(str(alphai.get("generated_at") or "—"))}</span></div>'
        f"<p>Macro caution: <strong>{'ja' if alphai.get('macro_caution') else 'nee'}</strong>"
        f" · pick-clip ×{float(cfg.get('alphai_clip_mult') or 1.5):.1f}</p>"
        f'<p class="muted" style="font-size:.78rem">'
        f"{escape(str(alphai.get('note') or ''))}</p>"
        "<div class='rules' style='margin-top:.5rem'>"
        f"<div><span>Picks (↑)</span>"
        f"{escape(', '.join(alphai.get('effective_picks') or []) or '—')}</div>"
        f"<div><span>Avoid (↓)</span>"
        f"{escape(', '.join(alphai.get('effective_avoid') or []) or '—')}</div>"
        f"<div><span>Pick-trades</span>{summary.get('alphai_pick_trades') or 0} · "
        f"<span class='{cls(summary.get('alphai_pick_net_eur'))}'>"
        f"{eur(summary.get('alphai_pick_net_eur'))}</span></div>"
        "</div></div>"
    )

    rows: list[str] = []
    for day in payload.get("days") or []:
        buys = day.get("would_buy") or []
        exits = day.get("exits") or []
        if not buys and not exits and not day.get("regime_on"):
            continue
        buy_lines = []
        for b in buys:
            net = b.get("net_eur")
            status = escape(str(b.get("status") or ""))
            extra = ""
            if net is not None:
                reason = escape(str(b.get("exit_reason") or ""))
                extra = f" → <span class='{cls(net)}'>{eur(net)}</span> ({reason})"
            elif b.get("unrealized_net_eur") is not None:
                u = b["unrealized_net_eur"]
                extra = f" → open <span class='{cls(u)}'>{eur(u)}</span>"
            buy_lines.append(
                f"<li><strong>{escape(str(b.get('base')))}</strong>{bias_tag(b)} "
                f"@ {escape(str(b.get('at')))} "
                f"· {eur(b.get('notional_eur'), signed=False)} · {status}{extra}</li>"
            )
        exit_lines = []
        for x in exits:
            exit_lines.append(
                f"<li><strong>{escape(str(x.get('base')))}</strong> "
                f"{escape(str(x.get('opened')))} → {escape(str(x.get('closed')))} "
                f"· <span class='{cls(x.get('net_eur'))}'>{eur(x.get('net_eur'))}</span> "
                f"· {escape(str(x.get('exit_reason') or ''))} "
                f"· piek {100 * float(x.get('peak_return') or 0):+.1f}%</li>"
            )
        day_net = eur(day.get("day_net_eur"))
        rows.append(
            '<div class="card section">'
            f'<div class="card-head"><h2>{escape(str(day.get("day")))}</h2>'
            f'<span class="{cls(day.get("day_net_eur"))}">{day_net}</span></div>'
            f'<p class="muted" style="margin:.2rem 0 .6rem">'
            f"regime ON {day.get('regime_on', 0)} · OFF {day.get('regime_off', 0)}</p>"
            + (
                "<h3 style='font-size:.85rem;margin:.6rem 0 .3rem'>Zou kopen</h3>"
                f"<ul style='margin:0;padding-left:1.1rem'>{''.join(buy_lines)}</ul>"
                if buy_lines
                else '<p class="muted">Geen would-buy deze dag.</p>'
            )
            + (
                "<h3 style='font-size:.85rem;margin:.8rem 0 .3rem'>Exits (P&amp;L)</h3>"
                f"<ul style='margin:0;padding-left:1.1rem'>{''.join(exit_lines)}</ul>"
                if exit_lines
                else ""
            )
            + "</div>"
        )

    open_rows = payload.get("open") or []
    open_html = ""
    if open_rows:
        items = "".join(
            f"<li><strong>{escape(str(o.get('base')))}</strong> "
            f"<span class='{cls(o.get('net_eur'))}'>{eur(o.get('net_eur'))}</span></li>"
            for o in open_rows
        )
        open_html = (
            '<div class="card section"><h2>Nog open (MTM)</h2>'
            f"<ul style='margin:0;padding-left:1.1rem'>{items}</ul></div>"
        )

    label = escape(str(payload.get("label")))
    w0 = escape(str(window.get("start")))
    w1 = escape(str(window.get("end")))
    hours = escape(",".join(str(h) for h in (cfg.get("decision_hours_utc") or [])))
    vol_m = float(cfg.get("min_volume_eur") or 0) / 1e6
    return (
        '<div class="hint warn">SHADOW — er worden <strong>geen</strong> '
        "echte orders geplaatst. Desk-regels + AlphaI picks/avoid/macro op een "
        "volatile pool buiten de core 16.</div>"
        f'<div class="card section"><div class="card-head"><h2>{label}</h2>'
        f'<span class="muted">{w0} → {w1}</span></div>'
        f"<p>Universe ({len(payload.get('universe') or [])}): {uni}</p>"
        f"<p>Clip {eur(cfg.get('clip_eur'), signed=False)} · "
        f"book {eur(cfg.get('book_eur'), signed=False)} · "
        f"uren {hours} UTC · min vol {vol_m:.1f}M</p>"
        f"<p>Trades <strong>{summary.get('trades') or 0}</strong> · "
        f"win {summary.get('win_rate') or '—'} · "
        f"gerealiseerd <strong class='{cls(summary.get('realized_eur'))}'>"
        f"{eur(summary.get('realized_eur'))}</strong> · "
        f"maxDD {eur(summary.get('max_drawdown_eur'))} · "
        f"fees {eur(summary.get('fees_eur'), signed=False)}</p>"
        '<p style="margin-top:.6rem">'
        '<a class="muted" href="/live/momentum">← terug naar live desk</a> · '
        '<a class="muted" href="/live/momentum/volatile?format=json">JSON</a> · '
        '<a class="muted" href="/live/momentum/volatile?days=14">14d</a> · '
        '<a class="muted" href="/live/momentum/volatile?days=84">12w</a></p></div>'
        + alphai_html
        + open_html
        + ("".join(rows) if rows else '<p class="muted">Geen beslissingen in dit venster.</p>')
    )


def render_volatile_shadow_page(payload: Mapping[str, Any]) -> str:
    """Full HTML document reusing dashboard CSS tokens."""
    from bot.live.dashboard_v2 import dashboard_css
    from bot.live.momentum_dashboard import _CSS

    body = render_volatile_shadow_html(payload)
    return f"""<!doctype html>
<html lang="nl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Volatile shadow · Moreney</title>
<style>{dashboard_css()}{_CSS}
  ul {{ font-size: .85rem; line-height: 1.45; }}
</style></head>
<body><div class="wrap">
  <div class="topbar"><div>
    <div class="brand">Momentum Desk</div>
    <div class="sub">Volatile shadow · paper only</div>
  </div>
  <span class="pill obs"><span class="dot"></span>SHADOW</span>
  </div>
  {body}
</div></body></html>"""


__all__ = [
    "VOLATILE_POOL",
    "build_volatile_shadow",
    "load_shadow_alphai",
    "render_volatile_shadow_html",
    "render_volatile_shadow_page",
    "shadow_config",
    "shadow_universe",
    "volatile_universe",
]

"""Operator page for the Daily Momentum Desk (server-rendered, auto-refresh)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from fastapi.responses import HTMLResponse

from bot.live.momentum_period_pnl import DeskEarnings, earnings_as_dict

_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@600;700;800&family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=IBM+Plex+Mono:wght@500;600&display=swap');
:root {
  --ink: #101a17;
  --muted: #5a6a63;
  --line: #cfd9d3;
  --line-soft: #e3ebe6;
  --panel: #fbfcfb;
  --good: #0a6b3c;
  --bad: #a51d16;
  --warn: #8a5a00;
  --accent: #0c4f42;
  --accent-2: #176b58;
  --bg1: #f3f6f3;
  --display: "Syne", "Avenir Next", sans-serif;
  --sans: "DM Sans", "Avenir Next", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, monospace;
  --radius: 12px;
  --ease: cubic-bezier(.22,1,.36,1);
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; }
body {
  color: var(--ink);
  font-family: var(--sans);
  background:
    radial-gradient(1000px 480px at 8% -12%, rgba(23,107,88,.14), transparent 58%),
    radial-gradient(720px 360px at 100% 0%, rgba(16,26,23,.06), transparent 52%),
    repeating-linear-gradient(-12deg, transparent, transparent 11px, rgba(16,26,23,.015) 11px, rgba(16,26,23,.015) 12px),
    linear-gradient(180deg, #eef3ef 0%, var(--bg1) 38%, #e7ede8 100%);
  padding-bottom: 5.5rem;
}
.wrap { max-width: 1080px; margin: 0 auto; padding: 1rem 1rem 2.25rem; }
.mono { font-family: var(--mono); }
.muted { color: var(--muted); }
.good { color: var(--good); } .bad { color: var(--bad); } .warn { color: var(--warn); }

@keyframes rise-in {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: none; }
}
@keyframes live-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: .45; }
}

.masthead {
  display: grid; gap: .95rem; margin-bottom: .85rem;
  padding: 1.05rem 1.15rem 1.1rem;
  border-bottom: 1px solid var(--line);
  background:
    linear-gradient(105deg, rgba(255,255,255,.55), rgba(255,255,255,.08) 42%, transparent),
    linear-gradient(180deg, rgba(12,79,66,.045), transparent 70%);
  animation: rise-in .55s var(--ease) both;
}
.masthead-top {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: .85rem; flex-wrap: wrap;
}
.brand {
  margin: 0; font-family: var(--display); font-weight: 800;
  font-size: clamp(2.35rem, 7vw, 3.6rem); letter-spacing: -0.05em; line-height: .92;
  color: var(--accent);
}
.brand-sub {
  margin: .4rem 0 0; color: var(--muted); font-size: .92rem;
  max-width: 36rem; line-height: 1.45;
}
.earn-label {
  margin: 0 0 .4rem; font-size: .68rem; font-weight: 700;
  letter-spacing: .14em; text-transform: uppercase; color: var(--muted);
}
.earn-grid {
  display: grid; gap: 0; grid-template-columns: 1fr;
  border-top: 1px solid var(--line-soft);
}
@media (min-width: 760px) {
  .earn-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .earn-tile + .earn-tile { border-left: 1px solid var(--line-soft); }
}
.earn-tile {
  padding: .7rem .15rem .15rem;
  min-height: auto;
  animation: rise-in .65s var(--ease) both;
}
.earn-tile:nth-child(2) { animation-delay: .06s; }
.earn-tile:nth-child(3) { animation-delay: .12s; }
.earn-tile .period {
  margin: 0; font-size: .72rem; font-weight: 600;
  color: var(--muted); letter-spacing: .04em; text-transform: uppercase;
}
.earn-tile .amount {
  margin: .28rem 0 0; font-family: var(--mono); font-weight: 600;
  font-size: clamp(1.45rem, 4vw, 1.95rem); letter-spacing: -0.035em; line-height: 1.05;
}
.earn-tile .meta { margin: .28rem 0 0; font-size: .72rem; color: var(--muted); }
.earn-foot {
  display: flex; flex-wrap: wrap; gap: .45rem 1.15rem; margin-top: .55rem;
  padding-top: .55rem; border-top: 1px solid var(--line-soft);
  font-size: .8rem; color: var(--muted);
}
.earn-foot strong { color: var(--ink); font-weight: 600; font-family: var(--mono); }

.pill {
  display: inline-flex; align-items: center; gap: .4rem;
  padding: .32rem .65rem; border-radius: 6px; border: 1px solid var(--line);
  background: rgba(255,255,255,.65); font-size: .66rem; font-weight: 700;
  letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
}
.pill .dot { width: .4rem; height: .4rem; border-radius: 1px; background: currentColor; }
.pill.on { color: var(--good); border-color: color-mix(in srgb, var(--good) 32%, var(--line)); }
.pill.on .dot { animation: live-pulse 1.8s ease-in-out infinite; }
.pill.off { color: var(--bad); }
.pill.obs { color: var(--warn); }

.panel {
  margin-top: .85rem;
  padding: .85rem 0 .15rem;
  border-top: 1px solid var(--line);
  animation: rise-in .5s var(--ease) both;
}
.panel-head {
  display: flex; justify-content: space-between; align-items: baseline;
  gap: .6rem; flex-wrap: wrap; margin-bottom: .55rem;
}
.panel-head h2, .fold-head {
  margin: 0; font-family: var(--display); font-weight: 700;
  font-size: 1.02rem; letter-spacing: -0.02em;
}
.panel-head .aside { font-size: .75rem; color: var(--muted); }
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  padding: .85rem .95rem;
}
.card.section { margin-top: .95rem; }
.card-head {
  display: flex; justify-content: space-between; align-items: center;
  gap: .6rem; flex-wrap: wrap;
}
.card-head h2, .section h2 {
  margin: 0; font-family: var(--display); font-weight: 700;
  font-size: 1.02rem; letter-spacing: -0.02em;
}
.section { margin-top: .95rem; }
.section h2 { margin: 0 0 .55rem; }

.pulse {
  display: grid; gap: 0; margin-top: .85rem;
  border: 1px solid var(--line); border-radius: var(--radius);
  background: rgba(255,255,255,.5);
  overflow: hidden;
  animation: rise-in .55s var(--ease) .05s both;
}
@media (min-width: 760px) {
  .pulse { grid-template-columns: repeat(4, minmax(0,1fr)); }
}
.pulse-item {
  padding: .7rem .85rem .75rem;
  border-top: 1px solid var(--line-soft);
}
@media (min-width: 760px) {
  .pulse-item { border-top: 0; }
  .pulse-item + .pulse-item { border-left: 1px solid var(--line-soft); }
}
.pulse-item .label {
  margin: 0; color: var(--muted); font-size: .66rem; font-weight: 700;
  letter-spacing: .07em; text-transform: uppercase;
}
.pulse-item .value {
  margin: .3rem 0 0; font-family: var(--mono);
  font-size: clamp(1.05rem, 2.8vw, 1.35rem); font-weight: 600; letter-spacing: -0.02em;
}
.pulse-item .hint {
  margin: .28rem 0 0; color: var(--muted); font-size: .7rem;
  border: 0; padding: 0; background: none;
}

/* legacy class names kept for tests / older panels */
.hero-grid { display: contents; }
.hero-card { display: contents; }

.stack { display: grid; gap: .85rem; }
@media (min-width: 980px) { .stack.two { grid-template-columns: 1.12fr .88fr; } }
.hint {
  margin: .55rem 0; padding: .5rem .7rem; border-radius: 8px;
  font-size: .8rem; border: 1px solid var(--line); background: rgba(255,255,255,.5);
}
.hint.bad { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 40%, var(--line)); }
.hint.good { color: var(--good); border-color: color-mix(in srgb, var(--good) 40%, var(--line)); }
.hint.warn { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 40%, var(--line)); }

.btn {
  cursor: pointer; font: inherit; font-size: .84rem; padding: .5rem .85rem; min-height: 42px;
  border-radius: 8px; border: 1px solid var(--line); background: rgba(255,255,255,.8);
  color: var(--accent); touch-action: manipulation; font-weight: 600;
  transition: border-color .15s ease, background .15s ease, transform .15s var(--ease);
}
.btn:hover { border-color: var(--accent); transform: translateY(-1px); }
.btn:disabled { opacity: .45; cursor: not-allowed; transform: none; }
.btn.danger { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 42%, var(--line)); }
.btn.primary { background: var(--accent); color: #f3faf7; border-color: var(--accent); }
.btn.primary:hover { background: var(--accent-2); border-color: var(--accent-2); }
.btn.block { width: 100%; display: block; text-align: center; }

.toolbar { display: flex; flex-wrap: wrap; gap: .45rem; margin: .7rem 0 0; }
.toolbar form { display: inline; flex: 1 1 auto; min-width: 8.5rem; }
.toolbar .btn { width: 100%; }

.ops-row {
  display: flex; flex-wrap: wrap; align-items: flex-start; gap: .55rem 1rem;
  margin-top: .65rem;
}
.ops-row .toolbar { margin: 0; flex: 1 1 16rem; }

table.desk { width: 100%; border-collapse: collapse; font-size: .84rem; }
table.desk th, table.desk td {
  padding: .48rem .5rem; text-align: right;
  border-bottom: 1px solid var(--line-soft); white-space: nowrap;
}
table.desk th {
  color: var(--muted); font-weight: 600; font-size: .68rem;
  letter-spacing: .05em; text-transform: uppercase;
}
table.desk td:first-child, table.desk th:first-child { text-align: left; }
table.desk tbody tr:hover td { background: rgba(12,79,66,.03); }
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }

.pos-empty {
  margin: .15rem 0 .35rem; padding: 1.1rem .2rem 1rem;
  color: var(--muted); font-size: .92rem; line-height: 1.45;
  border-left: 3px solid var(--line);
  padding-left: .85rem;
}

.fold {
  margin-top: .85rem; border-top: 1px solid var(--line);
  padding-top: .55rem;
}
.fold > summary {
  list-style: none; cursor: pointer; display: flex; align-items: center;
  justify-content: space-between; gap: .75rem; user-select: none;
  padding: .35rem 0 .45rem;
}
.fold > summary::-webkit-details-marker { display: none; }
.fold > summary .fold-head { pointer-events: none; }
.fold > summary .chev {
  font-size: .72rem; color: var(--muted); letter-spacing: .04em;
  text-transform: uppercase; font-weight: 600;
}
.fold[open] > summary .chev::after { content: "verberg"; }
.fold:not([open]) > summary .chev::after { content: "toon"; }
.fold-body { padding: .35rem 0 .55rem; animation: rise-in .35s var(--ease); }

.rules {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: .55rem .95rem; font-size: .8rem;
}
.rules div span { display: block; color: var(--muted); font-size: .66rem; margin-bottom: .12rem; }
.chips span {
  display: inline-block; margin: .15rem .25rem 0 0; padding: .15rem .5rem;
  border: 1px solid var(--line); border-radius: 6px; font-size: .74rem;
}

.pos-cards { display: none; gap: .55rem; }
.pos-card {
  border: 1px solid var(--line); border-radius: var(--radius);
  padding: .7rem .8rem; background: rgba(255,255,255,.72);
}
.pos-card .row1 {
  display: flex; justify-content: space-between; align-items: baseline;
  gap: .5rem; margin-bottom: .35rem;
}
.pos-card .meta {
  display: grid; grid-template-columns: 1fr 1fr; gap: .25rem .6rem; font-size: .78rem;
}
.pos-card .meta span { color: var(--muted); display: block; font-size: .65rem; }
.pos-card .actions { margin-top: .55rem; }

.sticky-actions {
  position: sticky; bottom: 0; z-index: 20; margin: 1rem -.25rem 0;
  padding: .6rem .7rem calc(.6rem + env(safe-area-inset-bottom));
  background: color-mix(in srgb, var(--bg1) 90%, transparent);
  border-top: 1px solid var(--line); backdrop-filter: blur(10px);
}
.sticky-actions .toolbar { margin: 0; }

.sleeve-split { display: grid; gap: .75rem; }
@media (min-width: 820px) { .sleeve-split { grid-template-columns: 1fr 1fr; } }
.sleeve-earn { font-size: .76rem; color: var(--muted); margin: .3rem 0 .1rem; }
.sleeve-earn b { font-family: var(--mono); font-weight: 600; }

.foot {
  margin-top: 1.15rem; font-size: .72rem; color: var(--muted);
  display: flex; flex-wrap: wrap; gap: .35rem .85rem; align-items: center;
}
.foot a { color: var(--accent); text-decoration: none; font-weight: 600; }
.foot a:hover { text-decoration: underline; }

@media (max-width: 720px) {
  .wrap { padding: .8rem .7rem 0; }
  .masthead { padding: .9rem .15rem 1rem; }
  .desk-wide { display: none; }
  .pos-cards { display: grid; }
  table.desk { font-size: .78rem; }
  table.desk th, table.desk td { padding: .4rem .35rem; }
  .pulse-item + .pulse-item { border-left: 0; }
}
@media (min-width: 721px) {
  .sticky-actions { display: none; }
  body { padding-bottom: 0; }
}
"""



def _fmt_eur(v: Any, *, signed: bool = True) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{x:+,.2f} €" if signed else f"{x:,.2f} €"


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{100 * float(v):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _cls(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    return "good" if x > 0 else "bad" if x < 0 else ""


def _ts(iso: Any) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso))
        return dt.astimezone(UTC).strftime("%d-%m %H:%M UTC")
    except ValueError:
        return escape(str(iso))


def _hero(
    label: str,
    value: str,
    *,
    cls: str = "",
    hint: str = "",
    value_attr: str = "",
) -> str:
    hint_html = f'<div class="hint muted">{escape(hint)}</div>' if hint else ""
    attrs = f" {value_attr}" if value_attr else ""
    return (
        f'<div class="pulse-item hero-card {cls}"><div class="label">{escape(label)}</div>'
        f'<div class="value {cls}"{attrs}>{value}</div>{hint_html}</div>'
    )




def _earnings_masthead(
    earnings: DeskEarnings | None,
    *,
    pill: str,
    venue: str,
    show_volatile: bool = False,
) -> str:
    """First-viewport composition: brand + week/month/all-time net."""
    if earnings is None:
        c_week = c_month = c_all = 0.0
        open_mtm = 0.0
        tw = tm = ta = 0
        core_w = vol_w = 0.0
        as_of = "—"
    else:
        # Core-only masthead uses core sleeve totals when volatile is disabled.
        sleeve = earnings.combined if show_volatile else earnings.core
        c_week, c_month, c_all = sleeve.week_eur, sleeve.month_eur, sleeve.all_time_eur
        open_mtm = earnings.open_mtm_eur
        tw, tm, ta = sleeve.trades_week, sleeve.trades_month, sleeve.trades_all_time
        core_w, vol_w = earnings.core.week_eur, earnings.volatile.week_eur
        as_of = earnings.as_of
    if show_volatile:
        week_meta = f"{tw} trades · core {_fmt_eur(core_w)} · vol {_fmt_eur(vol_w)}"
        brand_sub = f"Momentum desk · core + volatile · {venue}. "
    else:
        week_meta = f"{tw} trades deze week"
        brand_sub = f"Momentum desk · core · {venue}. "
    tiles = [
        ("Deze week", c_week, week_meta),
        ("Deze maand", c_month, f"{tm} trades deze maand"),
        ("Vanaf begin", c_all, f"{ta} trades all-time · netto gesloten"),
    ]
    tiles_html = "".join(
        '<div class="earn-tile">'
        f'<p class="period">{escape(label)}</p>'
        f'<p class="amount {_cls(val)}">{_fmt_eur(val)}</p>'
        f'<p class="meta">{escape(meta)}</p>'
        "</div>"
        for label, val, meta in tiles
    )
    day_eur = 0.0
    if earnings is not None:
        day_eur = (
            earnings.combined.day_eur if show_volatile else earnings.core.day_eur
        )
    return (
        '<section class="masthead">'
        '<div class="masthead-top">'
        "<div>"
        '<h1 class="brand">Moreney</h1>'
        f'<p class="brand-sub">{brand_sub}'
        "Netto = gesloten trades na fees (Europe/Amsterdam).</p>"
        "</div>"
        f"<div>{pill}</div>"
        "</div>"
        '<p class="earn-label">Netto verdiend</p>'
        f'<div class="earn-grid">{tiles_html}</div>'
        '<div class="earn-foot">'
        f"<span>Vandaag <strong class='{_cls(day_eur)}'>"
        f"{_fmt_eur(day_eur)}</strong></span>"
        f"<span>Open MTM <strong class='{_cls(open_mtm)}'>{_fmt_eur(open_mtm)}</strong></span>"
        f'<span class="muted">peil {escape(str(as_of)[:19].replace("T", " "))} NL</span>'
        "</div></section>"
    )


def _sleeve_earnings_line(period: Any) -> str:
    if period is None:
        return ""
    return (
        f'<p class="sleeve-earn">week <b class="{_cls(period.week_eur)}">{_fmt_eur(period.week_eur)}</b>'
        f" · maand <b class='{_cls(period.month_eur)}'>{_fmt_eur(period.month_eur)}</b>"
        f" · begin <b class='{_cls(period.all_time_eur)}'>{_fmt_eur(period.all_time_eur)}</b></p>"
    )


def _positions_table(
    status: Mapping[str, Any],
    *,
    sell_all_path: str | None = "/live/momentum",
    post_sell_action: str | None = None,
    empty_text: str = "Geen open posities — 100% cash tot de volgende beslissing.",
) -> str:
    rows = [
        p
        for p in (status.get("positions") or [])
        if float(p.get("quantity") or 0.0) > 1e-12
    ]
    cfg = status.get("config") or {}
    if not rows:
        return f'<p class="pos-empty muted" data-live="positions-empty">{escape(empty_text)}</p>'
    trail = float(cfg.get("trail_pct") or 0.03)
    tight_after = float(cfg.get("trail_tight_after") or 0.0)
    tight = float(cfg.get("trail_tight_pct") or trail)
    stop = float(cfg.get("hard_stop_pct") or 0.03)
    busy = status.get("manual_exit") or {}
    sell_busy = bool(busy) and not busy.get("done")
    out = [
        '<div class="table-scroll desk-wide" data-live="positions">'
        f'<table class="desk" data-trail="{trail}" data-tight-after="{tight_after}" '
        f'data-tight="{tight}" data-stop="{stop}"><thead><tr>',
        "<th>Base</th><th>Entry</th><th>Mark</th><th>Gross</th>"
        "<th>Peak</th><th>Trail-stop</th><th>Hard-stop</th><th>Net</th><th>Age</th>"
        "<th>Actie</th></tr></thead><tbody>",
    ]
    cards = ['<div class="pos-cards" data-live="position-cards">']
    for p in rows:
        entry = float(p.get("entry_price") or 0)
        peak_ret = float(p.get("peak_return") or 0)
        mark = p.get("mark")
        gross = p.get("gross_return")
        live_peak_ret = peak_ret
        if mark and entry > 0:
            live_peak_ret = max(peak_ret, float(mark) / entry - 1.0)
        peak_px = entry * (1 + live_peak_ret)
        eff_trail = tight if (tight_after > 0 and live_peak_ret >= tight_after) else trail
        trail_px = peak_px * (1 - eff_trail)
        stop_px = entry * (1 - stop)
        hid = escape(str(p.get("holding_id") or p.get("base") or ""))
        sell = _sell_cell(p, disabled=sell_busy, post_action=post_sell_action)
        out.append(
            f'<tr data-holding="{hid}" data-entry="{entry}">'
            f"<td><strong>{escape(str(p.get('base')))}</strong>"
            f" <span class='muted' style='font-size:.7rem'>{escape(str(p.get('venue') or ''))}"
            f"</span><div class='muted' style='font-size:.7rem'>"
            f"{escape(str(p.get('entry_reason') or ''))}</div></td>"
            f"<td class='mono'>{entry:,.4f}</td>"
            f"<td class='mono' data-k='mark'>{(f'{float(mark):,.4f}' if mark else '—')}"
            f"<div class='muted' style='font-size:.65rem' data-k='mark-meta'>"
            f"{escape(str(p.get('mark_source') or '—'))}"
            f"{(' · ' + str(int(p['mark_age_sec'])) + 's') if p.get('mark_age_sec') is not None else ''}"
            f"</div></td>"
            f"<td class='{_cls(gross)}' data-k='gross'>{_fmt_pct(gross)}</td>"
            f"<td data-k='peak'>{_fmt_pct(live_peak_ret)}</td>"
            f"<td class='mono' data-k='trail'>{trail_px:,.4f} "
            f"<span class='muted'>({100 * eff_trail:.1f}%)</span></td>"
            f"<td class='mono'>{stop_px:,.4f}</td>"
            f"<td class='{_cls(p.get('unrealized_net_eur'))}' data-k='net'>"
            f"{_fmt_eur(p.get('unrealized_net_eur'))}</td>"
            f"<td data-k='age'>{float(p.get('age_h') or 0):.1f}h</td>"
            f"<td>{sell}</td>"
            "</tr>"
        )
        cards.append(
            f'<div class="pos-card" data-holding="{hid}" data-entry="{entry}">'
            f'<div class="row1"><div><strong>{escape(str(p.get("base")))}</strong> '
            f'<span class="muted">{escape(str(p.get("venue") or ""))}</span></div>'
            f'<div class="{_cls(p.get("unrealized_net_eur"))}" style="font-weight:600" data-k="net">'
            f"{_fmt_eur(p.get('unrealized_net_eur'))}</div></div>"
            f'<div class="muted" style="font-size:.7rem;margin-bottom:.35rem">'
            f"{escape(str(p.get('entry_reason') or ''))}</div>"
            '<div class="meta">'
            f"<div><span>Entry</span>{entry:,.4f}</div>"
            f"<div><span>Mark</span><span data-k='mark'>"
            f"{(f'{float(mark):,.4f}' if mark else '—')}</span></div>"
            f'<div><span>Gross</span><span class="{_cls(gross)}" data-k="gross">'
            f"{_fmt_pct(gross)}</span></div>"
            f"<div><span>Peak</span><span data-k='peak'>{_fmt_pct(live_peak_ret)}</span></div>"
            f"<div><span>Trail</span><span data-k='trail'>{trail_px:,.4f} "
            f"({100 * eff_trail:.1f}%)</span></div>"
            f"<div><span>Hard stop</span>{stop_px:,.4f}</div>"
            f"<div><span>Age</span><span data-k='age'>{float(p.get('age_h') or 0):.1f}h</span></div>"
            "</div>"
            f'<div class="actions">{sell}</div></div>'
        )
    out.append("</tbody></table></div>")
    cards.append("</div>")
    out.append("".join(cards))
    sell_all = ""
    if rows and not sell_busy and sell_all_path:
        if post_sell_action:
            sell_all = (
                '<div class="toolbar" style="margin-top:.55rem">'
                f'<form method="post" action="{escape(post_sell_action.replace("/sell", "/sell-all"))}">'
                '<input type="hidden" name="redirect" value="1">'
                '<button type="submit" class="btn danger">Verkoop alles…</button></form></div>'
            )
        else:
            sell_all = (
                '<div class="toolbar" style="margin-top:.55rem">'
                f'<form method="get" action="{escape(sell_all_path)}">'
                '<input type="hidden" name="sell_all" value="1">'
                '<button type="submit" class="btn danger">Verkoop alles…</button></form></div>'
            )
    out.append(sell_all)
    out.append(
        "<p class='muted' style='font-size:.72rem;margin-top:.4rem'>Verkoop = maker-order op de "
        "bied, valt na 60 s terug op taker. Wordt in de ledger geboekt als <em>manual</em>. "
        "Marks vernieuwen elke 3s via ticker.</p>"
    )
    return "".join(out)



def _sell_cell(
    p: Mapping[str, Any],
    *,
    disabled: bool,
    confirm_path: str = "/live/momentum",
    sell_param: str = "sell",
    post_action: str | None = None,
) -> str:
    hid = str(p.get("holding_id") or "")
    if not hid:
        return ""
    if p.get("exiting"):
        return "<span class='muted' style='font-size:.75rem'>verkoop bezig…</span>"
    dis = " disabled" if disabled else ""
    if post_action:
        return (
            f'<form method="post" action="{escape(post_action)}" style="display:inline">'
            f'<input type="hidden" name="holding_id" value="{escape(hid)}">'
            '<input type="hidden" name="redirect" value="1">'
            f'<button type="submit" class="btn danger" style="font-size:.78rem;padding:.4rem .7rem;'
            f'min-height:40px"{dis}>Verkoop</button></form>'
        )
    return (
        f'<form method="get" action="{escape(confirm_path)}" style="display:inline">'
        f'<input type="hidden" name="{escape(sell_param)}" value="{escape(hid)}">'
        f'<button type="submit" class="btn danger" style="font-size:.78rem;padding:.4rem .7rem;'
        f'min-height:40px"{dis}>Verkoop</button></form>'
    )


def _sell_confirm_panel(status: Mapping[str, Any], holding_id: str) -> str:
    p = next(
        (x for x in status.get("positions") or [] if str(x.get("holding_id")) == holding_id), None
    )
    if p is None:
        return (
            '<div class="hint bad">Positie niet (meer) gevonden; mogelijk al verkocht. '
            '<a href="/live/momentum">terug</a></div>'
        )
    base = escape(str(p.get("base")))
    mark = p.get("mark")
    qty = float(p.get("quantity") or 0)
    value = qty * float(mark) if mark else None
    return (
        '<div class="card"><h2>Verkoop bevestigen</h2>'
        f"<p><strong>{base}</strong> op {escape(str(p.get('venue') or ''))}: "
        f"{qty:,.6f} stuks, entry {float(p.get('entry_price') or 0):,.4f}, "
        f"mark {(f'{float(mark):,.4f}' if mark else '—')}, waarde "
        f"{(f'{value:,.2f} €' if value is not None else '—')}, "
        f"resultaat nu <span class='{_cls(p.get('unrealized_net_eur'))}'>"
        f"{_fmt_eur(p.get('unrealized_net_eur'))}</span> netto.</p>"
        "<div style='display:flex;gap:.6rem;align-items:center;flex-wrap:wrap'>"
        f'<form method="post" action="/live/momentum/sell?holding_id={escape(holding_id)}" '
        'style="display:inline"><button type="submit" class="btn danger">'
        f"Verkoop {base} als maker (echt geld)</button></form>"
        f'<form method="post" action="/live/momentum/sell?holding_id={escape(holding_id)}'
        '&amp;urgent=1" style="display:inline"><button type="submit" class="btn danger">'
        "Verkoop direct (taker)</button></form>"
        '<a href="/live/momentum" class="muted" style="font-size:.8rem">annuleren</a></div>'
        "<p class='muted' style='font-size:.75rem;margin-top:.5rem'>Maker: order op de bied, "
        "60 s rusten, daarna taker-fallback. Taker: meteen over de spread, hogere fee.</p></div>"
    )


def _sell_all_confirm_panel(status: Mapping[str, Any]) -> str:
    rows = [
        p
        for p in (status.get("positions") or [])
        if float(p.get("quantity") or 0.0) > 1e-12
    ]
    if not rows:
        return (
            '<div class="hint bad">Geen open posities om te verkopen. '
            '<a href="/live/momentum">terug</a></div>'
        )
    lines = []
    total = 0.0
    for p in rows:
        net = float(p.get("unrealized_net_eur") or 0)
        total += net
        lines.append(
            f"<li><strong>{escape(str(p.get('base')))}</strong> "
            f"({escape(str(p.get('venue') or ''))}) · "
            f"<span class='{_cls(net)}'>{_fmt_eur(net)}</span></li>"
        )
    return (
        '<div class="card"><h2>Alles verkopen?</h2>'
        f"<p>{len(rows)} posities, open resultaat nu "
        f"<span class='{_cls(total)}'>{_fmt_eur(total)}</span>.</p>"
        f"<ul style='margin:.4rem 0 .8rem;padding-left:1.1rem'>{''.join(lines)}</ul>"
        "<div class='toolbar'>"
        '<form method="post" action="/live/momentum/sell-all">'
        '<button type="submit" class="btn danger">Alles als maker verkopen</button></form>'
        '<form method="post" action="/live/momentum/sell-all?urgent=1">'
        '<button type="submit" class="btn danger">Alles direct (taker)</button></form>'
        '<form method="get" action="/live/momentum">'
        '<button type="submit" class="btn">Annuleren</button></form>'
        "</div>"
        "<p class='muted' style='font-size:.75rem;margin-top:.5rem'>Orders gaan één voor één. "
        "Tijdens de reeks zijn andere verkopen geblokkeerd.</p></div>"
    )


def _toolbar(
    *, running: bool, has_positions: bool, hold: bool, show_volatile: bool = False
) -> str:
    if not running:
        return ""
    bits = [
        '<div class="toolbar">',
        '<form method="get" action="/live/momentum">'
        '<input type="hidden" name="simulate" value="1">'
        '<button type="submit" class="btn primary">Simuleer</button></form>',
        '<form method="get" action="/live/momentum">'
        '<input type="hidden" name="report" value="1">'
        '<button type="submit" class="btn">Daily report</button></form>',
    ]
    if has_positions:
        bits.append(
            '<form method="get" action="/live/momentum">'
            '<input type="hidden" name="sell_all" value="1">'
            '<button type="submit" class="btn danger">Verkoop alles</button></form>'
        )
    if show_volatile:
        bits.append(
            '<form method="get" action="/live/momentum/volatile">'
            '<button type="submit" class="btn">Volatile</button></form>'
        )
    if hold:
        bits.append(
            '<form method="get" action="/live/momentum">'
            '<button type="submit" class="btn">Sluiten</button></form>'
        )
    bits.append("</div>")
    return "".join(bits)


_KIND_NL = {
    "early_manual": "Te vroeg handmatig verkocht",
    "late_manual": "Handmatig later dan de auto-regel",
    "would_have_exited": "Auto-regel had al verkocht (nog open)",
    "still_open": "Nog open — auto-regel nog niet geraakt",
    "matched": "Exit in lijn met de regel",
}


def _report_panel(report: Mapping[str, Any]) -> str:
    out = [
        '<div class="card section"><div class="card-head"><h2>Daily report</h2>',
        f'<span class="muted">{escape(str(report.get("day") or ""))} UTC</span></div>',
        f'<p style="margin:.3rem 0 .7rem">{escape(str(report.get("summary") or ""))}</p>',
        f'<p>Gerealiseerd vandaag: <strong class="{_cls(report.get("realized_net_eur"))}">'
        f"{_fmt_eur(report.get('realized_net_eur'))}</strong></p>",
    ]
    decs = report.get("decisions") or []
    out.append("<h3 style='font-size:.9rem;margin:1rem 0 .4rem'>Beslissingen</h3>")
    if not decs:
        out.append('<p class="muted">Geen beslissingen in de ledger vandaag.</p>')
    else:
        out.append(
            '<div class="table-scroll"><table class="desk"><thead><tr>'
            "<th>Tijd</th><th>Regime</th><th>BTC</th><th>Breadth</th>"
            "<th>Entries</th><th>Redenen</th></tr></thead><tbody>"
        )
        for d in decs:
            ok = bool(d.get("ok"))
            ents = d.get("entries") or []
            if isinstance(ents, list) and ents and isinstance(ents[0], dict):
                ent_s = ", ".join(str(e.get("base") or e) for e in ents)
            else:
                ent_s = ", ".join(str(e) for e in ents) if ents else "—"
            out.append(
                "<tr>"
                f"<td>{_ts(d.get('ts'))}</td>"
                f"<td class='{'good' if ok else 'bad'}'>{'AAN' if ok else 'UIT'}</td>"
                f"<td>{_fmt_pct(d.get('btc_ret'))}</td>"
                f"<td>{float(d.get('breadth') or 0):.2f}</td>"
                f"<td>{escape(ent_s)}</td>"
                f"<td class='muted' style='text-align:left'>"
                f"{escape(', '.join(d.get('reasons') or []) or '—')}</td></tr>"
            )
        out.append("</tbody></table></div>")

    missed = report.get("missed_entries") or []
    out.append("<h3 style='font-size:.9rem;margin:1rem 0 .4rem'>Gemiste instappen</h3>")
    if not missed:
        out.append('<p class="muted">Geen extra instapmomenten met geldige kandidaten vandaag.</p>')
    else:
        out.append(
            '<div class="table-scroll"><table class="desk"><thead><tr>'
            "<th>Uur</th><th>Coins</th><th>BTC</th><th>Breadth</th>"
            "<th>Note</th><th>Hypo netto</th><th>Hypo exit</th></tr></thead><tbody>"
        )
        for m in missed:
            hypos = m.get("hypothetical") or []
            hypo_net = sum(float(h.get("net_eur") or 0) for h in hypos)
            hypo_ex = (
                ", ".join(
                    f"{h.get('base')} {h.get('reason') or h.get('status')} "
                    f"{_fmt_eur(h.get('net_eur'))}"
                    for h in hypos
                )
                or "—"
            )
            out.append(
                "<tr>"
                f"<td class='mono'>{int(m.get('hour_utc') or 0):02d}:00"
                f"{' ★' if m.get('scheduled') else ''}</td>"
                f"<td>{escape(', '.join(m.get('bases') or []))}</td>"
                f"<td>{_fmt_pct(m.get('btc_ret'))}</td>"
                f"<td>{float(m.get('breadth') or 0):.2f}</td>"
                f"<td class='muted' style='text-align:left'>"
                f"{escape(str(m.get('note') or ''))}</td>"
                f"<td class='{_cls(hypo_net)}'>{_fmt_eur(hypo_net)}</td>"
                f"<td class='muted' style='text-align:left;white-space:normal'>"
                f"{escape(hypo_ex)}</td></tr>"
            )
        out.append("</tbody></table></div>")
        out.append(
            '<p class="muted" style="font-size:.72rem">★ = gepland beslismoment. '
            "Hypo = wat de exit-regel later met die entry gedaan zou hebben.</p>"
        )

    ops = report.get("exit_opportunities") or []
    out.append("<h3 style='font-size:.9rem;margin:1rem 0 .4rem'>Exit-kansen</h3>")
    if not ops:
        out.append('<p class="muted">Geen entries vandaag om exits tegen af te zetten.</p>')
    else:
        out.append(
            '<div class="table-scroll"><table class="desk"><thead><tr>'
            "<th>Coin</th><th>Soort</th><th>Handmatig</th><th>Auto</th>"
            "<th>Δ netto</th><th>Piek</th></tr></thead><tbody>"
        )
        for o in ops:
            kind = str(o.get("kind") or "")
            out.append(
                "<tr>"
                f"<td><strong>{escape(str(o.get('base') or ''))}</strong></td>"
                f"<td style='text-align:left;white-space:normal'>"
                f"{escape(_KIND_NL.get(kind, kind))}</td>"
                f"<td>{_fmt_eur(o.get('actual_net_eur'))}"
                f"<div class='muted' style='font-size:.7rem'>"
                f"{escape(str(o.get('actual_reason') or '—'))} · {_ts(o.get('actual_exit_ts'))}"
                "</div></td>"
                f"<td>{_fmt_eur(o.get('auto_net_eur'))}"
                f"<div class='muted' style='font-size:.7rem'>"
                f"{escape(str(o.get('auto_reason') or '—'))} · {_ts(o.get('auto_exit_ts'))}"
                "</div></td>"
                f"<td class='{_cls(o.get('delta_eur'))}'>{_fmt_eur(o.get('delta_eur'))}</td>"
                f"<td>{_fmt_pct(o.get('peak_return'))}</td></tr>"
            )
        out.append("</tbody></table></div>")
        out.append(
            '<p class="muted" style="font-size:.72rem">Δ netto = handmatig − auto. '
            "Negatief betekent dat de auto-regel meer zou hebben opgeleverd.</p>"
        )
    out.append(
        '<p style="margin-top:.8rem"><a href="/live/momentum" class="muted">terug</a></p></div>'
    )
    return "".join(out)


def _decision_panel(status: Mapping[str, Any]) -> str:
    reg = status.get("last_regime") or {}
    if not reg:
        return (
            '<p class="muted">Nog geen beslissing genomen. Eerste beslismoment: '
            f"<strong>{_ts(status.get('next_decision'))}</strong>.</p>"
        )
    ok = bool(reg.get("ok"))
    soft = bool(reg.get("soft"))
    label = str(reg.get("regime_label") or "")
    if soft:
        pill = '<span class="pill on"><span class="dot"></span>REGIME SOFT</span>'
    elif ok:
        pill = '<span class="pill on"><span class="dot"></span>REGIME ON</span>'
    else:
        pill = '<span class="pill off"><span class="dot"></span>REGIME IDLE</span>'
    reasons = ", ".join(reg.get("reasons") or []) or "—"
    cands = reg.get("candidates") or []
    cand_html = (
        "".join(
            f"<span>{escape(str(c.get('base')))} {_fmt_pct(c.get('excess'))} "
            f"<em class='muted'>{_fmt_pct(c.get('from_high'))} vs high</em></span>"
            for c in cands
        )
        or '<span class="muted">geen kandidaten</span>'
    )
    entries = ", ".join(reg.get("entries") or []) or "—"
    ai = reg.get("alphai") or {}
    ai_html = (
        f"macro caution: <strong>{'ja' if ai.get('macro_caution') else 'nee'}</strong> · "
        f"avoid: {escape(', '.join(ai.get('avoid') or []) or '—')} · "
        f"picks: {escape(', '.join(ai.get('picks') or []) or '—')}"
    )
    block = reg.get("risk_block") or ""
    block_html = f'<div class="warn">Risk-blok: {escape(str(block))}</div>' if block else ""
    label_html = f"<div><span>Label</span>{escape(label)}</div>" if label else ""
    pnl_bits: list[str] = []
    for key in ("strong", "firm", "soft", "weak"):
        bucket = (status.get("regime_pnl") or {}).get(key) or {}
        if not bucket.get("n"):
            continue
        wr = bucket.get("win_rate")
        wr_txt = f"{100 * float(wr):.0f}%" if wr is not None else "—"
        pnl_bits.append(
            f"{key}: {int(bucket['n'])}× {_fmt_eur(bucket.get('net_eur'))} (WR {wr_txt})"
        )
    pnl_html = (
        f"<div><span>Regime PnL</span>{escape(' · '.join(pnl_bits))}</div>" if pnl_bits else ""
    )
    return (
        f"<div>{pill} <span class='muted'>om {_ts(reg.get('at'))}</span></div>"
        f"<div class='rules' style='margin-top:.7rem'>"
        f"{label_html}"
        f"<div><span>BTC 24u</span>{_fmt_pct(reg.get('btc_ret'))}</div>"
        f"<div><span>Breadth</span>{float(reg.get('breadth') or 0):.2f}</div>"
        f"<div><span>Redenen</span>{escape(reasons)}</div>"
        f"<div><span>Entries</span>{escape(entries)}</div>"
        f"{pnl_html}"
        f"</div>"
        f"<div class='chips' style='margin-top:.7rem'>{cand_html}</div>"
        f"<div class='muted' style='margin-top:.6rem;font-size:.78rem'>AlphaI — {ai_html}</div>"
        f"{block_html}"
    )


_SCENARIOS: tuple[float, ...] = (-0.03, 0.0, 0.03, 0.05, 0.08)


def _simulate_button() -> str:
    return (
        '<form method="get" action="/live/momentum" style="display:inline">'
        '<input type="hidden" name="simulate" value="1">'
        '<button type="submit" class="btn">Simuleer beslissing nu</button></form>'
    )


def _expectancy_line(ledger_rows: Sequence[Mapping[str, Any]]) -> str:
    exits = [r for r in ledger_rows if r.get("event") == "exit"]
    if not exits:
        return (
            '<p class="muted" style="font-size:.78rem">Nog geen afgesloten trades van deze desk; '
            "de tabel toont daarom scenario's, geen voorspelling. Winst hangt af van de markt: "
            "de desk begrenst het verlies (hard stop), de winst loopt mee met de trail.</p>"
        )
    nets = [float(r.get("net_eur") or 0) for r in exits]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    avg = sum(nets) / len(nets)
    return (
        f'<p class="muted" style="font-size:.78rem">Historie van deze desk: {len(nets)} trades, '
        f"win {100 * len(wins) / len(nets):.0f}%, gemiddeld {_fmt_eur(avg)} per trade "
        f"(winst gem. {_fmt_eur(sum(wins) / len(wins)) if wins else '—'}, "
        f"verlies gem. {_fmt_eur(sum(losses) / len(losses)) if losses else '—'}).</p>"
    )


def _preview_panel(
    preview: Mapping[str, Any], ledger_rows: Sequence[Mapping[str, Any]], commit: Mapping[str, Any]
) -> str:
    planned = [p for p in preview.get("planned") or []]
    ok = bool(preview.get("ok"))
    soft = bool(preview.get("soft"))
    label = str(preview.get("regime_label") or ("soft" if soft else ("firm" if ok else "weak")))
    regime_state = "AAN" if ok else "IDLE"
    if soft:
        regime_state = "SOFT"
    regime = (
        f"Regime <strong class='{'good' if ok else 'bad'}'>{regime_state}</strong> "
        f"<span class='muted'>({escape(label)})</span> · "
        f"BTC 24h {_fmt_pct(preview.get('btc_ret'))} · breadth "
        f"{100 * float(preview.get('breadth') or 0):.0f}% · bar {_ts(preview.get('at'))}"
    )
    if preview.get("reasons"):
        regime += f" · <span class='bad'>{escape(', '.join(preview['reasons']))}</span>"
    if preview.get("risk_block"):
        regime += f" · <span class='bad'>risico-blok: {escape(str(preview['risk_block']))}</span>"
    ai = preview.get("alphai") or {}
    ai_bits = []
    if ai.get("macro_caution"):
        ai_bits.append("macro caution (clip ×0,7)")
    if ai.get("avoid"):
        ai_bits.append("avoid: " + ", ".join(ai["avoid"]))
    if ai.get("picks"):
        ai_bits.append("picks: " + ", ".join(ai["picks"]))
    ai_line = (
        f"<div class='muted' style='font-size:.78rem'>AlphaI: {escape(' · '.join(ai_bits))}</div>"
        if ai_bits
        else ""
    )
    out = [f"<p style='margin:.2rem 0 .6rem'>{regime}</p>{ai_line}"]
    if not planned:
        out.append(
            '<p class="muted">De desk zou nu <strong>niets kopen</strong>. Er is dus ook niets '
            "te committen.</p>"
        )
    else:
        head = "".join(
            f"<th>{'stop' if s < 0 else 'exit'} {s:+.0%}</th>".replace("+0%", "±0%")
            for s in _SCENARIOS
        )
        out.append(
            '<table class="desk"><thead><tr><th>Coin</th><th>Venue</th><th>Clip</th>'
            f"<th>Prijs</th><th>Break-even</th>{head}</tr></thead><tbody>"
        )
        totals = [0.0] * len(_SCENARIOS)
        for p in planned:
            clip = float(p.get("clip_eur") or 0)
            fee_rt = float(p.get("fee_in_eur") or 0) + float(p.get("fee_out_eur") or 0)
            cells = []
            for i, s in enumerate(_SCENARIOS):
                pnl = clip * s - fee_rt
                totals[i] += pnl
                cells.append(f"<td class='{_cls(pnl)}'>{_fmt_eur(pnl)}</td>")
            price = p.get("price")
            be = p.get("break_even")
            blocked = p.get("blocked")
            out.append(
                "<tr>"
                f"<td><strong>{escape(str(p.get('base')))}</strong>"
                f"<div class='muted' style='font-size:.7rem'>"
                f"{escape(', '.join(p.get('reasons') or []))}</div></td>"
                f"<td>{escape(str(p.get('venue') or '—'))}"
                f"{(' <span class=bad>' + escape(str(blocked)) + '</span>') if blocked else ''}"
                "</td>"
                f"<td class='mono'>{clip:,.0f} €</td>"
                f"<td class='mono'>{(f'{float(price):,.4f}' if price else '—')}</td>"
                f"<td class='mono'>{(f'{float(be):,.4f}' if be else '—')}</td>"
                f"{''.join(cells)}</tr>"
            )
        total_cells = "".join(
            f"<td class='{_cls(t)}'><strong>{_fmt_eur(t)}</strong></td>" for t in totals
        )
        out.append(
            f"<tr><td><strong>Totaal</strong></td><td></td>"
            f"<td class='mono'><strong>{sum(float(p.get('clip_eur') or 0) for p in planned):,.0f} €"
            f"</strong></td><td></td><td></td>{total_cells}</tr></tbody></table>"
        )
        out.append(
            "<p class='muted' style='font-size:.75rem;margin-top:.4rem'>Netto na fees. "
            "Prijs = laatste 15m-close; uitvoering gaat als maker op het live orderboek. "
            f"Hard stop bij {100 * float(planned[0].get('hard_stop_pct') or 0.03):.0f}%, "
            f"trail {100 * float(planned[0].get('trail_pct') or 0.03):.0f}% onder de piek "
            f"({100 * float(planned[0].get('trail_tight_pct') or 0.02):.1f}% zodra "
            f"+{100 * float(planned[0].get('trail_tight_after') or 0.04):.0f}% piek).</p>"
        )
    out.append(_expectancy_line(ledger_rows))
    if preview.get("rejected"):
        rej = " · ".join(
            f"{escape(str(r['base']))} {_fmt_pct(r.get('excess'))} ({escape(str(r.get('why')))})"
            for r in preview["rejected"]
        )
        out.append(f"<div class='muted' style='font-size:.75rem'>Afgewezen leaders: {rej}</div>")
    busy = bool(commit) and not commit.get("done")
    bases = ",".join(str(p.get("base")) for p in planned if not p.get("blocked"))
    if bases and not busy:
        out.append(
            "<div style='margin-top:.8rem;display:flex;gap:.6rem;align-items:center'>"
            f'<form method="post" action="/live/momentum/commit?bases={escape(bases)}'
            f'&amp;at={escape(str(preview.get("at") or ""))}" style="display:inline">'
            '<button type="submit" class="btn danger">Commit: koop nu '
            f"{escape(bases.replace(',', ' + '))} (echt geld)</button></form>"
            '<a href="/live/momentum" class="muted" style="font-size:.8rem">'
            "sluiten zonder kopen</a>"
            "</div>"
        )
    else:
        out.append(
            '<p style="margin-top:.8rem"><a href="/live/momentum" class="muted" '
            'style="font-size:.8rem">terug</a></p>'
        )
    return "".join(out)


def _manual_exit_notice(me: Mapping[str, Any]) -> str:
    if not me:
        return ""
    base = escape(str(me.get("base") or ""))
    is_all = bool(me.get("all") or (me.get("holding_id") == "*"))
    label = "Alles" if is_all else base
    if not me.get("done"):
        return (
            f'<div class="hint warn">Verkoop {label} bezig sinds {_ts(me.get("started_at"))}. '
            "Order rust als maker (tot 60 s per coin), daarna taker.</div>"
        )
    res = me.get("result") or {}
    if res.get("error"):
        return f'<div class="hint bad">Verkoop {label} mislukt: {escape(str(res["error"]))}</div>'
    if is_all:
        sold = int(res.get("sold") or 0)
        failed = int(res.get("failed") or 0)
        cls = "good" if sold and not failed else ("warn" if sold else "bad")
        return (
            f'<div class="hint {cls}">Sell-all klaar om {_ts(me.get("finished_at"))}: '
            f"{sold} verkocht, {failed} mislukt. Zie ledger.</div>"
        )
    if not res.get("ok"):
        why = str(res.get("reason") or "onbekend")
        hint = (
            " — OKX had minder coins vrij dan de desk dacht (fee in de coin zelf). "
            "Probeer opnieuw; de desk clamt nu op de vrije balance."
            if why == "exit_failed"
            else ""
        )
        return f'<div class="hint bad">Verkoop {base} niet uitgevoerd ({escape(why)}){hint}</div>'
    tail = " (gedeeltelijk gevuld, rest blijft open)" if res.get("partial") else ""
    return (
        f'<div class="hint good">{base} verkocht om {_ts(me.get("finished_at"))}{tail}. '
        "Zie ledger.</div>"
    )


def _commit_notice(commit: Mapping[str, Any]) -> str:
    if not commit:
        return ""
    if not commit.get("done"):
        return (
            f'<div class="hint warn">Uitvoering bezig voor '
            f"{escape(', '.join(commit.get('bases') or []))} sinds "
            f"{_ts(commit.get('started_at'))}. Orders rusten als maker (tot 90 s per coin).</div>"
        )
    res = commit.get("result") or {}
    if res.get("error"):
        return f'<div class="hint bad">Commit mislukt: {escape(str(res["error"]))}</div>'
    if res.get("mismatch"):
        return (
            '<div class="hint bad">Commit geweigerd: de desk zou inmiddels '
            f"{escape(', '.join(res.get('planned') or []) or 'niets')} kopen in plaats van "
            f"{escape(', '.join(commit.get('bases') or []))}. Simuleer opnieuw.</div>"
        )
    entered = res.get("entries") or []
    return (
        f'<div class="hint good">Commit uitgevoerd om {_ts(commit.get("finished_at"))}: '
        f"{escape(', '.join(entered)) if entered else 'geen entries'}. Zie ledger.</div>"
    )


def read_ledger_tail(path: str | Path, *, limit: int = 400) -> list[dict[str, Any]]:
    """Read the last ``limit`` JSONL ledger rows (best-effort)."""
    p = Path(path)
    rows: list[dict[str, Any]] = []
    if not p.exists():
        return rows
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines[-max(1, min(int(limit), 2000)) :]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _ledger_table(rows: Sequence[Mapping[str, Any]]) -> str:
    fills = [r for r in rows if r.get("event") in {"entry", "exit", "entry_failed", "exit_failed"}]
    if not fills:
        return '<p class="muted">Nog geen fills.</p>'
    out = [
        '<table class="desk"><thead><tr><th>Tijd</th><th>Event</th><th>Base</th><th>Prijs</th>'
        "<th>Notional</th><th>Fee</th><th>Gross</th><th>Peak</th><th>Net</th><th>Reden</th></tr></thead><tbody>"
    ]
    for r in reversed(fills[-40:]):
        ev = str(r.get("event"))
        ev_cls = "good" if ev == "entry" else ("bad" if ev.endswith("failed") else "")
        out.append(
            "<tr>"
            f"<td>{_ts(r.get('ts'))}</td>"
            f"<td class='{ev_cls}'>{escape(ev)}{' (taker)' if r.get('taker') else ''}</td>"
            f"<td><strong>{escape(str(r.get('base') or ''))}</strong>"
            f" <span class='muted' style='font-size:.7rem'>{escape(str(r.get('venue') or ''))}"
            "</span></td>"
            f"<td class='mono'>{(f'{float(r["price"]):,.4f}' if r.get('price') else '—')}</td>"
            f"<td>{_fmt_eur(r.get('notional_eur'), signed=False)}</td>"
            f"<td>{_fmt_eur(r.get('fee_eur'), signed=False)}</td>"
            f"<td class='{_cls(r.get('gross_return'))}'>{_fmt_pct(r.get('gross_return'))}</td>"
            f"<td>{_fmt_pct(r.get('peak_return'))}</td>"
            f"<td class='{_cls(r.get('net_eur'))}'>{_fmt_eur(r.get('net_eur'))}</td>"
            f"<td class='muted' style='text-align:left;max-width:220px;overflow:hidden;"
            f"text-overflow:ellipsis'>{escape(str(r.get('reason') or ''))}</td>"
            "</tr>"
        )
    out.append("</tbody></table>")
    return "".join(out)


def _rules(cfg: Mapping[str, Any]) -> str:
    """Render live desk knobs. Field names must match ``status.config`` (DeskConfig)."""
    weekdays = " (ma–vr)" if cfg.get("skip_weekend_entries") else ""
    interval = float(cfg.get("decision_interval_sec") or 0.0)
    if interval > 0:
        if interval >= 60 and abs(interval / 60 - round(interval / 60)) < 1e-9:
            cadence = f"elke {interval / 60:.0f} min"
        else:
            cadence = f"elke {interval:.0f}s"
        decision_label = cadence + weekdays
    else:
        hours = ", ".join(f"{int(h):02d}:00" for h in (cfg.get("decision_hours_utc") or [0]))
        decision_label = f"uren {hours}{weekdays} (geen minutenscan)"
    refill = bool(cfg.get("refill_on_exit"))
    fade_eta = float(cfg.get("fade_eta_sec") or 0.0)
    green_h = float(cfg.get("green_deadline_hours") or 0.0)
    midflat_h = float(cfg.get("midflat_hours") or 0.0)
    items = [
        ("Beslismoment (UTC)", decision_label),
        (
            "Refill na exit",
            "aan — bij vrij slot meteen opnieuw beslissen" if refill else "uit",
        ),
        (
            "Clip",
            f"{float(cfg.get('clip_eur') or 0):,.0f} € "
            f"(AlphaI-pick ×{cfg.get('alphai_clip_mult')}, "
            f"brede tape ×{cfg.get('strong_clip_mult')}, "
            f"dunne tape ×{cfg.get('weak_clip_mult')})",
        ),
        ("Max posities", str(cfg.get("max_positions"))),
        (
            "Top-N",
            f"{cfg.get('top_n')} / {cfg.get('top_n_broad')} "
            f"bij breadth ≥ {cfg.get('broad_breadth')}",
        ),
        ("Min excess vs BTC", _fmt_pct(cfg.get("min_excess"))),
        ("Max onder 24u-high", _fmt_pct(cfg.get("max_from_high"))),
        (
            "Regime",
            f"BTC 24u > {_fmt_pct(cfg.get('btc_min_ret'))}, breadth ≥ {cfg.get('min_breadth')}",
        ),
        (
            "Exit-ladder",
            "1) hard stop → 2) fade ETA→0 (live marks) → 3) trail → 4) time-exit",
        ),
        (
            "Trail",
            f"{100 * float(cfg.get('trail_pct') or 0):.1f}% → "
            f"{100 * float(cfg.get('trail_tight_pct') or 0):.1f}% na piek "
            f"≥ {100 * float(cfg.get('trail_tight_after') or 0):.0f}%",
        ),
        ("Hard stop", f"−{100 * float(cfg.get('hard_stop_pct') or 0):.1f}%"),
        (
            "Fade ETA→0",
            (
                f"≤{fade_eta:.0f}s voor "
                f"{float(cfg.get('fade_confirm_sec') or 0):.0f}s "
                f"(smooth {float(cfg.get('fade_smooth_sec') or 0):.0f}s; "
                f"arm ≥{float(cfg.get('fade_min_peak_eur') or 0):.0f}€/"
                f"{100 * float(cfg.get('fade_min_peak_pct') or 0):.1f}%; "
                f"giveback ≥{float(cfg.get('fade_min_giveback_eur') or 0):.0f}€)"
                if fade_eta > 0
                else "uit"
            ),
        ),
        (
            "Time-to-green",
            (
                f"{green_h:.0f}u zonder piek "
                f"≥ {100 * float(cfg.get('green_min_peak') or 0):.1f}%"
                if green_h > 0
                else "uit"
            ),
        ),
        (
            "Midflat",
            f"{midflat_h:.0f}u fee-flat" if midflat_h > 0 else "uit",
        ),
        ("Time-exit", f"{float(cfg.get('time_exit_hours') or 0):.0f}u onder break-even"),
        ("Daglimiet", f"−{float(cfg.get('day_loss_limit_eur') or 0):.0f} €"),
        (
            "Weeklimiet",
            f"−{float(cfg.get('week_loss_limit_eur') or 0):.0f} € → "
            f"{float(cfg.get('pause_hours_after_week_limit') or 0):.0f}u pauze",
        ),
        ("AlphaI macro", str(cfg.get("macro_caution_mode"))),
        (
            "Macro pick-gate",
            (
                "aan"
                if cfg.get("macro_caution_requires_alphai_pick", False)
                else "uit (tape-namen ok onder reduce)"
            ),
        ),
        (
            "Weak-tape survival",
            (
                "soft single-fail AlphaI×"
                f"{cfg.get('soft_regime_clip_mult', 0.5)}; "
                f"double-weak idle={'aan' if cfg.get('weak_tape_idle_on_double', True) else 'uit'}; "
                f"soft+macro idle={'aan' if cfg.get('soft_regime_idle_on_macro_caution', True) else 'uit'}"
                if cfg.get("soft_regime_on_weak_tape", True)
                else "hard block (soft uit)"
            ),
        ),
    ]
    return (
        '<div class="rules" data-live="rules">'
        + "".join(f"<div><span>{escape(k)}</span>{escape(v)}</div>" for k, v in items)
        + "</div>"
    )


def _sleeve_card(
    *,
    title: str,
    role: str,
    status: Mapping[str, Any] | None,
    href: str,
    book_label: str | None = None,
) -> str:
    """Compact dual-sleeve tile for the integrated desk view."""
    st = status or {}
    running = bool(st.get("running"))
    dry = bool(st.get("dry_run"))
    allow_live = st.get("allow_live")
    if not running:
        pill = '<span class="pill off"><span class="dot"></span>STOP</span>'
        mode = "gestopt"
    elif dry or allow_live is False:
        pill = '<span class="pill obs"><span class="dot"></span>PAPER</span>'
        mode = "paper / dry-run"
    else:
        pill = '<span class="pill on"><span class="dot"></span>LIVE</span>'
        mode = "live orders"
    risk = st.get("risk") or {}
    cfg = st.get("config") or {}
    n_pos = len(st.get("positions") or [])
    max_pos = cfg.get("max_positions") or "—"
    hours = cfg.get("decision_hours_utc") or []
    hours_s = ",".join(str(h) for h in hours) if hours else "—"
    interval = float(cfg.get("decision_interval_sec") or 0.0)
    if interval > 0:
        if interval >= 60 and abs(interval / 60 - round(interval / 60)) < 1e-9:
            schedule_s = f"elke {interval / 60:.0f} min"
        else:
            schedule_s = f"elke {interval:.0f}s"
    else:
        schedule_s = f"Uren {hours_s} UTC"
    book = st.get("book_eur")
    if book is None:
        book = cfg.get("book_eur")
    book_left = st.get("book_left_eur")
    book_html = ""
    if book_label and book is not None:
        left_s = (
            f" · vrij {_fmt_eur(book_left, signed=False)}"
            if book_left is not None
            else ""
        )
        book_html = (
            f"<p>{escape(book_label)} {_fmt_eur(book, signed=False)}{left_s}</p>"
        )
    pos_bits: list[str] = []
    for p in (st.get("positions") or [])[:4]:
        base = escape(str(p.get("base") or ""))
        net = p.get("unrealized_net_eur")
        pos_bits.append(f"<li><strong>{base}</strong> · <span class='{_cls(net)}'>{_fmt_eur(net)}</span></li>")
    if not pos_bits:
        pos_bits.append("<li class='muted'>Geen open posities</li>")
    return (
        f'<div class="card"><div class="card-head"><h2>{escape(title)}</h2>{pill}</div>'
        f'<p class="muted" style="margin:0 0 .5rem">{escape(role)} · {escape(mode)}</p>'
        f"<p>Gerealiseerd <strong class='{_cls(st.get('realized_total_eur'))}'>"
        f"{_fmt_eur(st.get('realized_total_eur'))}</strong> · "
        f"day <span class='{_cls(risk.get('day_realized_eur'))}'>"
        f"{_fmt_eur(risk.get('day_realized_eur'))}</span> · "
        f"pos {n_pos}/{max_pos}</p>"
        f"{book_html}"
        f"<p>{escape(schedule_s)} · next <strong>{_ts(st.get('next_decision'))}</strong></p>"
        f"<ul style='margin:.4rem 0 .6rem;padding-left:1.1rem'>{''.join(pos_bits)}</ul></div>"
    )


def _sleeves_panel(
    core: Mapping[str, Any],
    volatile: Mapping[str, Any] | None,
) -> str:
    """One desk, two sleeves: stable core + aggressive volatile."""
    cash = float(core.get("cash_eur") or 0)
    core_exp = float(core.get("exposure_eur") or 0)
    v = volatile or {}
    book = float(v.get("book_eur") or (v.get("config") or {}).get("book_eur") or 0)
    deployed = float(v.get("deployed_eur") or v.get("exposure_eur") or 0)
    reserved = max(0.0, book - deployed) if book else 0.0
    free_shared = max(0.0, cash - core_exp - reserved)
    capital = (
        '<div class="hint" style="margin-bottom:.7rem">'
        "<strong>Kapitaalbeeld</strong> — gedeelde venue-cash, gescheiden boeken. "
        f"Cash {_fmt_eur(cash, signed=False)} · core ingezet {_fmt_eur(core_exp, signed=False)} · "
        f"volatile book {_fmt_eur(book, signed=False)} "
        f"(waarvan vrij {_fmt_eur(reserved, signed=False)}) · "
        f"ongereserveerd ~{_fmt_eur(free_shared, signed=False)}."
        "</div>"
    )
    return (
        '<div class="card section"><div class="card-head">'
        "<h2>Desk sleeves</h2>"
        '<span class="muted">stabiel core · aggressief volatile</span></div>'
        f"{capital}"
        '<div class="stack two">'
        + _sleeve_card(
            title="Core",
            role="Stabiele RS-desk · core-16 · strengere filters",
            status=core,
            href="/live/momentum",
            book_label=None,
        )
        + _sleeve_card(
            title="Volatile",
            role="Agressievere AlphaI midcaps · soft book · eigen risk",
            status=volatile,
            href="/live/momentum/volatile",
            book_label="Soft book",
        )
        + "</div></div>"
    )


def _volatile_actions(volatile: Mapping[str, Any] | None) -> str:
    """Inline paper/live controls for the volatile sleeve on the main desk."""
    st = volatile or {}
    if not st.get("running"):
        return (
            '<form method="post" action="/live/momentum/volatile/start" style="display:inline">'
            '<input type="hidden" name="dry_run" value="false">'
            '<input type="hidden" name="redirect" value="1">'
            '<button type="submit" class="btn primary">Start volatile LIVE</button></form>'
        )
    return (
        '<form method="post" action="/live/momentum/volatile/decide" style="display:inline;margin-right:.35rem">'
        '<input type="hidden" name="execute" value="0">'
        '<input type="hidden" name="redirect" value="1">'
        '<button type="submit" class="btn">Preview</button></form>'
        '<form method="post" action="/live/momentum/volatile/decide" style="display:inline">'
        '<input type="hidden" name="execute" value="1">'
        '<input type="hidden" name="redirect" value="1">'
        '<button type="submit" class="btn primary">Decide</button></form>'
    )



_LIVE_MARKS_JS = r"""
<script>
(function () {
  if (window.__moreneyMarksPoll) return;
  window.__moreneyMarksPoll = true;
  const STATUS_URL = "/live/momentum/status";
  const INTERVAL_MS = 3000;

  function fmtPct(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v) * 100;
    return (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
  }
  function fmtEur(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    const sign = n > 0 ? "+" : "";
    return sign + n.toLocaleString("nl-NL", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " €";
  }
  function fmtPx(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    return Number(v).toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
  }
  function cls(v) {
    const n = Number(v);
    if (!Number.isFinite(n) || n === 0) return "";
    return n > 0 ? "good" : "bad";
  }
  function setText(root, key, text, className) {
    root.querySelectorAll(`[data-k="${key}"]`).forEach((el) => {
      el.textContent = text;
      if (className !== undefined) {
        el.classList.remove("good", "bad");
        if (className) el.classList.add(className);
      }
    });
  }
  function patchHolding(pos, trail, tightAfter, tight) {
    const id = String(pos.holding_id || pos.base || "");
    const nodes = document.querySelectorAll(`[data-holding="${CSS.escape(id)}"]`);
    if (!nodes.length) return;
    const entry = Number(pos.entry_price || 0);
    const mark = pos.mark == null ? null : Number(pos.mark);
    const peakBar = Number(pos.peak_return || 0);
    const gross = pos.gross_return;
    let livePeak = peakBar;
    if (mark && entry > 0) livePeak = Math.max(peakBar, mark / entry - 1);
    const effTrail = (tightAfter > 0 && livePeak >= tightAfter) ? tight : trail;
    const trailPx = entry * (1 + livePeak) * (1 - effTrail);
    nodes.forEach((root) => {
      setText(root, "mark", fmtPx(mark));
      if (pos.mark_source != null || pos.mark_age_sec != null) {
        const meta = [pos.mark_source || "—"]
          .concat(pos.mark_age_sec != null ? [`${Number(pos.mark_age_sec).toFixed(0)}s`] : [])
          .join(" · ");
        setText(root, "mark-meta", meta);
      }
      setText(root, "gross", fmtPct(gross), cls(gross));
      setText(root, "peak", fmtPct(livePeak));
      setText(root, "trail", `${fmtPx(trailPx)} (${(100 * effTrail).toFixed(1)}%)`);
      setText(root, "net", fmtEur(pos.unrealized_net_eur), cls(pos.unrealized_net_eur));
      if (pos.age_h != null) setText(root, "age", `${Number(pos.age_h).toFixed(1)}h`);
    });
  }
  function patchHeroes(status) {
    const open = status.unrealized_net_eur;
    const el = document.querySelector('[data-live="open-pnl"]');
    if (el) {
      el.textContent = fmtEur(open);
      el.classList.remove("good", "bad");
      const c = cls(open);
      if (c) el.classList.add(c);
    }
    const eq = document.querySelector('[data-live="equity"]');
    if (eq && status.equity_eur != null) eq.textContent = fmtEur(status.equity_eur).replace(/^\+/, "");
    const stamp = document.querySelector('[data-live="marks-age"]');
    if (stamp) {
      const age = (status.positions || []).map(p => p.mark_age_sec).filter(v => v != null);
      if (age.length) stamp.textContent = `marks ${Math.max(...age).toFixed(0)}s geleden`;
      else if (status.marks_updated_at) stamp.textContent = "marks live";
    }
    const next = document.querySelector('[data-live="next-decision"]');
    if (next && status.next_decision) {
      const d = new Date(status.next_decision);
      if (!Number.isNaN(d.getTime())) {
        const dd = String(d.getUTCDate()).padStart(2, "0");
        const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
        const hh = String(d.getUTCHours()).padStart(2, "0");
        const mi = String(d.getUTCMinutes()).padStart(2, "0");
        next.textContent = `${dd}-${mm} ${hh}:${mi} UTC`;
      }
    }
  }
  function livePositionIds() {
    const ids = new Set();
    document.querySelectorAll("[data-holding]").forEach((el) => {
      const id = el.getAttribute("data-holding");
      if (id) ids.add(id);
    });
    return ids;
  }
  function statusPositionIds(status) {
    const ids = new Set();
    (status.positions || []).forEach((p) => {
      if (Number(p.quantity || 0) <= 1e-12) return;
      const id = String(p.holding_id || p.base || "");
      if (id) ids.add(id);
    });
    return ids;
  }
  function positionsChanged(status) {
    const live = livePositionIds();
    const next = statusPositionIds(status);
    if (live.size !== next.size) return true;
    for (const id of next) {
      if (!live.has(id)) return true;
    }
    for (const id of live) {
      if (!next.has(id)) return true;
    }
    return false;
  }
  function trailKnobs(status) {
    const cfg = status.config || {};
    // Live WR pack defaults (3% / 4%→2%); never fall back to the old 4% pack.
    const trail = Number(cfg.trail_pct != null ? cfg.trail_pct : 0.03);
    const tightAfter = Number(cfg.trail_tight_after != null ? cfg.trail_tight_after : 0.04);
    const tight = Number(cfg.trail_tight_pct != null ? cfg.trail_tight_pct : 0.02);
    document.querySelectorAll("table.desk[data-trail]").forEach((table) => {
      table.dataset.trail = String(trail);
      table.dataset.tightAfter = String(tightAfter);
      table.dataset.tight = String(tight);
    });
    return { trail, tightAfter, tight };
  }
  function patchRules(status) {
    const html = status.ui && status.ui.rules_html;
    const node = document.querySelector('[data-live="rules"]');
    if (html && node) node.outerHTML = html;
  }
  async function tick() {
    try {
      const res = await fetch(STATUS_URL, { cache: "no-store" });
      if (!res.ok) return;
      const status = await res.json();
      // Open set changed (entry/exit/ghost cleared) → full reload so the
      // positions block at the top matches venue reality immediately.
      if (positionsChanged(status)) {
        window.location.reload();
        return;
      }
      const { trail, tightAfter, tight } = trailKnobs(status);
      (status.positions || []).forEach((p) => patchHolding(p, trail, tightAfter, tight));
      patchHeroes(status);
      patchRules(status);
    } catch (err) {
      /* ignore transient network blips */
    }
  }
  tick();
  setInterval(tick, INTERVAL_MS);
})();
</script>
"""


def render_momentum_dashboard(
    status: Mapping[str, Any],
    ledger_rows: Sequence[Mapping[str, Any]],
    *,
    preview: Mapping[str, Any] | None = None,
    notice: str | None = None,
    sell: str | None = None,
    sell_all: bool = False,
    report: Mapping[str, Any] | None = None,
    volatile: Mapping[str, Any] | None = None,
    earnings: DeskEarnings | None = None,
    volatile_ledger_rows: Sequence[Mapping[str, Any]] | None = None,
    show_volatile: bool = False,
) -> HTMLResponse:
    running = bool(status.get("running"))
    commit = status.get("commit") or {}
    dry = bool(status.get("dry_run"))
    if not running:
        pill = '<span class="pill off"><span class="dot"></span>GESTOPT</span>'
    elif dry:
        pill = '<span class="pill obs"><span class="dot"></span>SHADOW</span>'
    else:
        pill = '<span class="pill on"><span class="dot"></span>LIVE</span>'
    risk = status.get("risk") or {}
    cfg = status.get("config") or {}
    n_pos = sum(
        1
        for p in (status.get("positions") or [])
        if float(p.get("quantity") or 0.0) > 1e-12
    )
    exits = [r for r in ledger_rows if r.get("event") == "exit"]
    wins = sum(1 for r in exits if float(r.get("net_eur") or 0) > 0)
    win_rate = f"{100 * wins / len(exits):.0f}%" if exits else "—"
    fees = sum(
        float(r.get("fee_eur") or 0) for r in ledger_rows if r.get("event") in {"entry", "exit"}
    )
    err = status.get("last_error")
    err_html = f'<div class="hint bad">Laatste fout: {escape(str(err))}</div>' if err else ""
    venue = escape(" + ".join(status.get("venues") or [str(status.get("venue") or "")]))
    cash_by_venue = status.get("cash_by_venue") or {}
    cash_hint = (
        " · ".join(f"{escape(str(k))} {float(v):,.0f} €" for k, v in cash_by_venue.items())
        if len(cash_by_venue) > 1
        else f"cash {_fmt_eur(status.get('cash_eur'), signed=False)}"
    )
    task_err = status.get("task_error")
    if task_err:
        err_html += f'<div class="hint bad">Loop gestopt: {escape(str(task_err))}</div>'
    if notice:
        err_html += f'<div class="hint warn">{escape(notice)}</div>'
    err_html += _commit_notice(commit)
    err_html += _manual_exit_notice(status.get("manual_exit") or {})
    hold_page = bool(preview) or bool(sell) or bool(sell_all) or bool(report)
    refresh_meta = ""  # marks poll via JS; full reload only on demand
    refresh_note = (
        "Geen live-update tijdens bevestiging"
        if hold_page
        else 'Marks live elke 3s · <span data-live="marks-age">—</span>'
    )
    live_js = "" if hold_page else _LIVE_MARKS_JS
    show_vol = bool(show_volatile)
    toolbar = _toolbar(
        running=running, has_positions=n_pos > 0, hold=hold_page, show_volatile=show_vol
    )
    sell_html = _sell_confirm_panel(status, sell) if sell else ""
    sell_all_html = _sell_all_confirm_panel(status) if sell_all else ""
    report_html = _report_panel(report) if report else ""
    preview_html = (
        f'<div class="card section"><div class="card-head"><h2>Simulatie</h2>'
        f"{_simulate_button()}</div>{_preview_panel(preview, ledger_rows, commit)}</div>"
        if preview
        else ""
    )

    earnings_html = _earnings_masthead(earnings, pill=pill, venue=venue, show_volatile=show_vol)

    heroes = "".join(
        [
            _hero(
                "Equity (cash + posities)",
                _fmt_eur(status.get("equity_eur"), signed=False),
                hint=f"{cash_hint} · ingezet {_fmt_eur(status.get('exposure_eur'), signed=False)}",
                value_attr='data-live="equity"',
            ),
            _hero(
                "Core vandaag",
                _fmt_eur(risk.get("day_realized_eur")),
                cls=_cls(risk.get("day_realized_eur")),
                hint=f"limiet −{float(cfg.get('day_loss_limit_eur') or 0):.0f} € · win {win_rate}",
            ),
            _hero(
                "Open resultaat",
                _fmt_eur(earnings.open_mtm_eur if earnings else status.get("unrealized_net_eur")),
                cls=_cls(earnings.open_mtm_eur if earnings else status.get("unrealized_net_eur")),
                hint=f"core {n_pos}/{cfg.get('max_positions')} · fees {fees:,.2f} €",
                value_attr='data-live="open-pnl"',
            ),
            _hero(
                "Volgende beslissing",
                f"<span class='mono' style='font-size:1rem' data-live='next-decision'>"
                f"{_ts(status.get('next_decision'))}</span>",
                hint=(
                    "entries toegestaan"
                    if risk.get("entries_allowed", True)
                    else f"geblokkeerd: {risk.get('block_reason')}"
                ),
            ),
        ]
    )
    core_earn = _sleeve_earnings_line(earnings.core if earnings else None)
    vol_earn = (
        _sleeve_earnings_line(earnings.volatile if earnings else None) if show_vol else ""
    )
    sleeves_html = _sleeves_panel(status, volatile) if show_vol else ""
    if show_vol:
        positions_html = (
            '<section class="panel" id="open-pos">'
            '<div class="stack two">'
            '<div><div class="panel-head"><h2>Core · open posities</h2></div>'
            f"{_positions_table(status)}</div>"
            '<div><div class="panel-head"><h2>Volatile · open posities</h2></div>'
            f"""{_positions_table(
                volatile or {},
                sell_all_path=None,
                post_sell_action="/live/momentum/volatile/sell",
                empty_text="Geen open volatile-posities — soft book staat klaar.",
            )}</div></div></section>"""
        )
        decisions_html = (
            '<section class="panel">'
            '<div class="stack two">'
            '<div><div class="panel-head"><h2>Core · laatste beslissing</h2>'
            f"{_simulate_button() if running and not preview else ''}</div>"
            f"{_decision_panel(status)}</div>"
            '<div><div class="panel-head"><h2>Volatile · laatste beslissing</h2>'
            f"{_volatile_actions(volatile)}</div>{_decision_panel(volatile or {})}</div>"
            "</div></section>"
        )
        vol_ledger_html = (
            f'<details class="fold"><summary><span class="fold-head">Volatile ledger</span>'
            f'<span class="chev"></span></summary><div class="fold-body">'
            f"{_ledger_table(volatile_ledger_rows or [])}</div></details>"
            if volatile_ledger_rows is not None
            else ""
        )
        footer_links = (
            '<a href="/live/momentum/status">core JSON</a>'
            '<a href="/live/momentum/volatile/status">volatile JSON</a>'
            '<a href="/live/momentum/ledger">core ledger</a>'
            '<a href="/live/momentum/volatile/ledger">volatile ledger</a>'
            '<a href="/live/momentum/earnings">earnings</a>'
        )
    else:
        positions_html = (
            '<section class="panel" id="open-pos">'
            '<div class="panel-head">'
            '<h2>Open posities</h2>'
            f'<span class="aside">{n_pos}/{escape(str(cfg.get("max_positions") or "—"))} slots'
            f' · open {_fmt_eur(earnings.open_mtm_eur if earnings else status.get("unrealized_net_eur"))}'
            "</span></div>"
            f"{_positions_table(status)}</section>"
        )
        decisions_html = (
            '<section class="panel"><div class="panel-head"><h2>Laatste beslissing</h2>'
            f"{_simulate_button() if running and not preview else ''}</div>"
            f"{_decision_panel(status)}</section>"
        )
        vol_ledger_html = ""
        footer_links = (
            '<a href="/live/momentum/status">status JSON</a>'
            '<a href="/live/momentum/ledger">ledger</a>'
            '<a href="/live/momentum/earnings">earnings</a>'
        )
    html = f"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0c4f42">
{refresh_meta}
<title>Moreney · Momentum Desk</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
{earnings_html}
{positions_html}
{err_html}
<div class="ops-row">{toolbar}</div>
{sleeves_html}
{core_earn and f'<div class="muted" style="font-size:.78rem;margin:.4rem 0 0">Core netto · </div>{core_earn}' or ''}
{vol_earn and f'<div class="muted" style="font-size:.78rem">Volatile netto · </div>{vol_earn}' or ''}
<div class="pulse hero-grid">{heroes}</div>
{preview_html}
{report_html}
{sell_html}
{sell_all_html}
{decisions_html}
<details class="fold" open>
<summary><span class="fold-head">Core ledger</span><span class="chev"></span></summary>
<div class="fold-body">{_ledger_table(ledger_rows)}</div>
</details>
{vol_ledger_html}
<details class="fold">
<summary><span class="fold-head">Regels (core)</span><span class="chev"></span></summary>
<div class="fold-body">{_rules(cfg)}</div>
</details>
<p class="foot"><span>{refresh_note}</span>{footer_links}</p>
</div>
<div class="sticky-actions">{toolbar}</div>
{live_js}
</body></html>"""
    return HTMLResponse(html)

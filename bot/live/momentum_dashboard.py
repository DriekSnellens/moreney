"""Operator page for the Daily Momentum Desk (server-rendered, auto-refresh)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from html import escape
from typing import Any

from fastapi.responses import HTMLResponse

from bot.live.momentum_period_pnl import DeskEarnings, earnings_as_dict

_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@600;700;800&family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=IBM+Plex+Mono:wght@500;600&display=swap');
:root {
  --ink: #14201c;
  --muted: #5c6b64;
  --line: #d5ddd7;
  --panel: rgba(255,255,255,.78);
  --panel-solid: #f7faf7;
  --good: #0b7a45;
  --bad: #b42318;
  --warn: #9a6700;
  --accent: #0f5c4c;
  --accent-2: #1f7a64;
  --blue: #0f5c4c;
  --bg0: #e8eee9;
  --bg1: #f4f7f4;
  --display: "Syne", "Avenir Next", sans-serif;
  --sans: "DM Sans", "Avenir Next", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, monospace;
  --radius: 14px;
  --shadow: 0 1px 0 rgba(20,32,28,.04);
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; }
body {
  color: var(--ink);
  font-family: var(--sans);
  background:
    radial-gradient(900px 420px at 12% -10%, rgba(31,122,100,.16), transparent 55%),
    radial-gradient(800px 380px at 92% 8%, rgba(20,32,28,.08), transparent 50%),
    linear-gradient(180deg, #eef3ef 0%, var(--bg1) 42%, #e5ebe6 100%);
  padding-bottom: 5.5rem;
}
.wrap { max-width: 1120px; margin: 0 auto; padding: 1.1rem 1rem 2rem; }
.mono { font-family: var(--mono); }
.muted { color: var(--muted); }
.good { color: var(--good); } .bad { color: var(--bad); } .warn { color: var(--warn); }

/* Brand masthead — first viewport composition */
.masthead {
  display: grid; gap: 1.1rem; margin-bottom: 1.25rem;
  padding: 1.15rem 1.2rem 1.25rem;
  border: 1px solid var(--line);
  border-radius: calc(var(--radius) + 4px);
  background:
    linear-gradient(135deg, rgba(255,255,255,.92), rgba(247,250,247,.72)),
    linear-gradient(180deg, rgba(15,92,76,.05), transparent 60%);
  box-shadow: var(--shadow);
}
.masthead-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; flex-wrap: wrap; }
.brand {
  margin: 0; font-family: var(--display); font-weight: 800;
  font-size: clamp(2.1rem, 6vw, 3.4rem); letter-spacing: -0.045em; line-height: .95;
  color: var(--accent);
}
.brand-sub { margin: .45rem 0 0; color: var(--muted); font-size: .95rem; max-width: 34rem; line-height: 1.4; }
.earn-label {
  margin: 0 0 .55rem; font-size: .72rem; font-weight: 700; letter-spacing: .12em;
  text-transform: uppercase; color: var(--muted);
}
.earn-grid {
  display: grid; gap: .75rem;
  grid-template-columns: 1fr;
}
@media (min-width: 760px) {
  .earn-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; }
}
.earn-tile {
  padding: .85rem .95rem .9rem;
  border-radius: var(--radius);
  border: 1px solid var(--line);
  background: rgba(255,255,255,.66);
  min-height: 6.2rem;
}
.earn-tile .period { margin: 0; font-size: .78rem; font-weight: 600; color: var(--muted); letter-spacing: .02em; }
.earn-tile .amount {
  margin: .35rem 0 0; font-family: var(--mono); font-weight: 600;
  font-size: clamp(1.55rem, 4.5vw, 2.15rem); letter-spacing: -0.03em; line-height: 1.05;
}
.earn-tile .meta { margin: .4rem 0 0; font-size: .74rem; color: var(--muted); }
.earn-foot {
  display: flex; flex-wrap: wrap; gap: .55rem 1.1rem; margin-top: .85rem;
  font-size: .8rem; color: var(--muted);
}
.earn-foot strong { color: var(--ink); font-weight: 600; }

.pill {
  display: inline-flex; align-items: center; gap: .35rem;
  padding: .35rem .7rem; border-radius: 8px; border: 1px solid var(--line);
  background: rgba(255,255,255,.7); font-size: .68rem; font-weight: 700;
  letter-spacing: .06em; text-transform: uppercase; color: var(--muted);
}
.pill .dot { width: .42rem; height: .42rem; border-radius: 2px; background: currentColor; }
.pill.on { color: var(--good); border-color: color-mix(in srgb, var(--good) 35%, var(--line)); }
.pill.off { color: var(--bad); }
.pill.obs { color: var(--warn); }

.card, .hero-card {
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  padding: .9rem 1rem; box-shadow: var(--shadow);
}
.card.section { margin-top: 1rem; }
.card-head, .card-head { display: flex; justify-content: space-between; align-items: center; gap: .6rem; flex-wrap: wrap; }
.card-head h2, .section h2 {
  margin: 0; font-family: var(--display); font-weight: 700; font-size: 1.05rem; letter-spacing: -0.02em;
}
.section { margin-top: 1.1rem; }
.section h2 { margin: 0 0 .6rem; }
.hero-grid { display: grid; gap: .65rem; grid-template-columns: repeat(2, minmax(0,1fr)); margin-top: 1rem; }
@media (min-width: 760px) { .hero-grid { grid-template-columns: repeat(3, minmax(0,1fr)); } }
.hero-card .label { margin: 0; color: var(--muted); font-size: .68rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
.hero-card .value { margin: .35rem 0 0; font-family: var(--mono); font-size: clamp(1.15rem, 3.5vw, 1.55rem); font-weight: 600; letter-spacing: -0.02em; }
.hero-card .hint { margin: .35rem 0 0; color: var(--muted); font-size: .74rem; border: 0; padding: 0; background: none; }

.stack { display: grid; gap: 1rem; }
@media (min-width: 980px) { .stack.two { grid-template-columns: 1.15fr .85fr; } }
.hint { margin: .6rem 0; padding: .55rem .75rem; border-radius: 10px; font-size: .82rem; border: 1px solid var(--line); background: rgba(255,255,255,.55); }
.hint.bad { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 40%, var(--line)); }
.hint.good { color: var(--good); border-color: color-mix(in srgb, var(--good) 40%, var(--line)); }
.hint.warn { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 40%, var(--line)); }

.btn { cursor: pointer; font: inherit; font-size: .85rem; padding: .55rem .9rem; min-height: 44px;
  border-radius: 10px; border: 1px solid var(--line); background: rgba(255,255,255,.75);
  color: var(--accent); touch-action: manipulation; font-weight: 600; }
.btn:hover { border-color: var(--accent); }
.btn:disabled { opacity: .45; cursor: not-allowed; }
.btn.danger { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 45%, var(--line)); }
.btn.primary { background: var(--accent); color: #f4faf7; border-color: var(--accent); }
.btn.primary:hover { background: var(--accent-2); border-color: var(--accent-2); }
.btn.block { width: 100%; display: block; text-align: center; }

.toolbar { display: flex; flex-wrap: wrap; gap: .5rem; margin: .85rem 0 0; }
.toolbar form { display: inline; flex: 1 1 auto; min-width: 9rem; }
.toolbar .btn { width: 100%; }

table.desk { width: 100%; border-collapse: collapse; font-size: .85rem; }
table.desk th, table.desk td { padding: .45rem .55rem; text-align: right; border-bottom: 1px solid var(--line); white-space: nowrap; }
table.desk th { color: var(--muted); font-weight: 600; font-size: .72rem; letter-spacing: .04em; text-transform: uppercase; }
table.desk td:first-child, table.desk th:first-child { text-align: left; }
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }

.rules { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: .5rem .9rem; font-size: .8rem; }
.rules div span { display: block; color: var(--muted); font-size: .68rem; }
.chips span { display: inline-block; margin: .15rem .25rem 0 0; padding: .15rem .5rem; border: 1px solid var(--line); border-radius: 8px; font-size: .74rem; }

.pos-cards { display: none; gap: .65rem; }
.pos-card { border: 1px solid var(--line); border-radius: var(--radius); padding: .7rem .8rem; background: rgba(255,255,255,.7); }
.pos-card .row1 { display: flex; justify-content: space-between; align-items: baseline; gap: .5rem; margin-bottom: .35rem; }
.pos-card .meta { display: grid; grid-template-columns: 1fr 1fr; gap: .25rem .6rem; font-size: .78rem; }
.pos-card .meta span { color: var(--muted); display: block; font-size: .65rem; }
.pos-card .actions { margin-top: .55rem; }

.sticky-actions { position: sticky; bottom: 0; z-index: 20; margin: 1rem -.25rem 0;
  padding: .65rem .75rem calc(.65rem + env(safe-area-inset-bottom));
  background: color-mix(in srgb, var(--bg1) 88%, transparent);
  border-top: 1px solid var(--line); backdrop-filter: blur(8px); }
.sticky-actions .toolbar { margin: 0; }

.sleeve-split { display: grid; gap: .75rem; }
@media (min-width: 820px) { .sleeve-split { grid-template-columns: 1fr 1fr; } }
.sleeve-earn { font-size: .78rem; color: var(--muted); margin: .35rem 0 .15rem; }
.sleeve-earn b { font-family: var(--mono); font-weight: 600; }

@media (max-width: 720px) {
  .wrap { padding: .85rem .7rem 0; }
  .masthead { padding: 1rem .9rem 1.05rem; }
  .desk-wide { display: none; }
  .pos-cards { display: grid; }
  table.desk { font-size: .78rem; }
  table.desk th, table.desk td { padding: .4rem .4rem; }
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


def _hero(label: str, value: str, *, cls: str = "", hint: str = "") -> str:
    hint_html = f'<div class="hint muted">{escape(hint)}</div>' if hint else ""
    return (
        f'<div class="hero-card {cls}"><div class="label">{escape(label)}</div>'
        f'<div class="value {cls}">{value}</div>{hint_html}</div>'
    )




def _earnings_masthead(
    earnings: DeskEarnings | None,
    *,
    pill: str,
    venue: str,
) -> str:
    """First-viewport composition: brand + week/month/all-time net."""
    if earnings is None:
        c_week = c_month = c_all = 0.0
        open_mtm = 0.0
        tw = tm = ta = 0
        core_w = vol_w = 0.0
        as_of = "—"
    else:
        c = earnings.combined
        c_week, c_month, c_all = c.week_eur, c.month_eur, c.all_time_eur
        open_mtm = earnings.open_mtm_eur
        tw, tm, ta = c.trades_week, c.trades_month, c.trades_all_time
        core_w, vol_w = earnings.core.week_eur, earnings.volatile.week_eur
        as_of = earnings.as_of
    tiles = [
        ("Deze week", c_week, f"{tw} trades · core {_fmt_eur(core_w)} · vol {_fmt_eur(vol_w)}"),
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
    return (
        '<section class="masthead">'
        '<div class="masthead-top">'
        "<div>"
        '<h1 class="brand">Moreney</h1>'
        f'<p class="brand-sub">Momentum desk · core + volatile · {venue}. '
        "Netto = gesloten trades na fees (Europe/Amsterdam).</p>"
        "</div>"
        f"<div>{pill}</div>"
        "</div>"
        '<p class="earn-label">Netto verdiend</p>'
        f'<div class="earn-grid">{tiles_html}</div>'
        '<div class="earn-foot">'
        f"<span>Vandaag <strong class='{_cls(earnings.combined.day_eur if earnings else 0)}'>"
        f"{_fmt_eur(earnings.combined.day_eur if earnings else 0)}</strong></span>"
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
    rows = status.get("positions") or []
    cfg = status.get("config") or {}
    if not rows:
        return f'<p class="muted">{escape(empty_text)}</p>'
    trail = float(cfg.get("trail_pct") or 0.03)
    tight_after = float(cfg.get("trail_tight_after") or 0.0)
    tight = float(cfg.get("trail_tight_pct") or trail)
    stop = float(cfg.get("hard_stop_pct") or 0.03)
    busy = status.get("manual_exit") or {}
    sell_busy = bool(busy) and not busy.get("done")
    out = [
        '<div class="table-scroll desk-wide"><table class="desk"><thead><tr>',
        "<th>Base</th><th>Entry</th><th>Mark</th><th>Gross</th>"
        "<th>Peak</th><th>Trail-stop</th><th>Hard-stop</th><th>Net</th><th>Age</th>"
        "<th>Actie</th></tr></thead><tbody>",
    ]
    cards = ['<div class="pos-cards">']
    for p in rows:
        entry = float(p.get("entry_price") or 0)
        peak_ret = float(p.get("peak_return") or 0)
        peak_px = entry * (1 + peak_ret)
        eff_trail = tight if (tight_after > 0 and peak_ret >= tight_after) else trail
        trail_px = peak_px * (1 - eff_trail)
        stop_px = entry * (1 - stop)
        mark = p.get("mark")
        sell = _sell_cell(p, disabled=sell_busy, post_action=post_sell_action)
        out.append(
            "<tr>"
            f"<td><strong>{escape(str(p.get('base')))}</strong>"
            f" <span class='muted' style='font-size:.7rem'>{escape(str(p.get('venue') or ''))}"
            f"</span><div class='muted' style='font-size:.7rem'>"
            f"{escape(str(p.get('entry_reason') or ''))}</div></td>"
            f"<td class='mono'>{entry:,.4f}</td>"
            f"<td class='mono'>{(f'{float(mark):,.4f}' if mark else '—')}</td>"
            f"<td class='{_cls(p.get('gross_return'))}'>{_fmt_pct(p.get('gross_return'))}</td>"
            f"<td>{_fmt_pct(peak_ret)}</td>"
            f"<td class='mono'>{trail_px:,.4f} "
            f"<span class='muted'>({100 * eff_trail:.1f}%)</span></td>"
            f"<td class='mono'>{stop_px:,.4f}</td>"
            f"<td class='{_cls(p.get('unrealized_net_eur'))}'>"
            f"{_fmt_eur(p.get('unrealized_net_eur'))}</td>"
            f"<td>{float(p.get('age_h') or 0):.1f}h</td>"
            f"<td>{sell}</td>"
            "</tr>"
        )
        cards.append(
            '<div class="pos-card">'
            f'<div class="row1"><div><strong>{escape(str(p.get("base")))}</strong> '
            f'<span class="muted">{escape(str(p.get("venue") or ""))}</span></div>'
            f'<div class="{_cls(p.get("unrealized_net_eur"))}" style="font-weight:600">'
            f"{_fmt_eur(p.get('unrealized_net_eur'))}</div></div>"
            f'<div class="muted" style="font-size:.7rem;margin-bottom:.35rem">'
            f"{escape(str(p.get('entry_reason') or ''))}</div>"
            '<div class="meta">'
            f"<div><span>Entry</span>{entry:,.4f}</div>"
            f"<div><span>Mark</span>{(f'{float(mark):,.4f}' if mark else '—')}</div>"
            f'<div><span>Gross</span><span class="{_cls(p.get("gross_return"))}">'
            f"{_fmt_pct(p.get('gross_return'))}</span></div>"
            f"<div><span>Peak</span>{_fmt_pct(peak_ret)}</div>"
            f"<div><span>Trail</span>{trail_px:,.4f} ({100 * eff_trail:.1f}%)</div>"
            f"<div><span>Hard stop</span>{stop_px:,.4f}</div>"
            f"<div><span>Age</span>{float(p.get('age_h') or 0):.1f}h</div>"
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
        "bied, valt na 60 s terug op taker. Wordt in de ledger geboekt als <em>manual</em>.</p>"
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
    rows = status.get("positions") or []
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


def _toolbar(*, running: bool, has_positions: bool, hold: bool) -> str:
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
    pill = (
        '<span class="pill on"><span class="dot"></span>REGIME ON</span>'
        if ok
        else '<span class="pill off"><span class="dot"></span>REGIME OFF</span>'
    )
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
    return (
        f"<div>{pill} <span class='muted'>om {_ts(reg.get('at'))}</span></div>"
        f"<div class='rules' style='margin-top:.7rem'>"
        f"<div><span>BTC 24u</span>{_fmt_pct(reg.get('btc_ret'))}</div>"
        f"<div><span>Breadth</span>{float(reg.get('breadth') or 0):.2f}</div>"
        f"<div><span>Redenen</span>{escape(reasons)}</div>"
        f"<div><span>Entries</span>{escape(entries)}</div>"
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
    regime = (
        f"Regime <strong class='{'good' if ok else 'bad'}'>{'AAN' if ok else 'UIT'}</strong> · "
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
    hours = ", ".join(f"{int(h):02d}:00" for h in (cfg.get("decision_hours_utc") or [0]))
    weekdays = " (ma–vr)" if cfg.get("skip_weekend_entries") else ""
    items = [
        ("Beslismoment (UTC)", hours + weekdays),
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
            "Trail",
            f"{100 * float(cfg.get('trail_pct') or 0):.1f}% → "
            f"{100 * float(cfg.get('trail_tight_pct') or 0):.1f}% na piek "
            f"≥ {100 * float(cfg.get('trail_tight_after') or 0):.0f}%",
        ),
        ("Hard stop", f"−{100 * float(cfg.get('hard_stop_pct') or 0):.1f}%"),
        ("Time-exit", f"{float(cfg.get('time_exit_hours') or 0):.0f}u onder break-even"),
        ("Daglimiet", f"−{float(cfg.get('day_loss_limit_eur') or 0):.0f} €"),
        (
            "Weeklimiet",
            f"−{float(cfg.get('week_loss_limit_eur') or 0):.0f} € → "
            f"{float(cfg.get('pause_hours_after_week_limit') or 0):.0f}u pauze",
        ),
        ("AlphaI macro", str(cfg.get("macro_caution_mode"))),
    ]
    return (
        '<div class="rules">'
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
        f"<p>Uren {escape(hours_s)} UTC · next <strong>{_ts(st.get('next_decision'))}</strong></p>"
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
    n_pos = len(status.get("positions") or [])
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
    refresh_meta = "" if hold_page else '<meta http-equiv="refresh" content="20">'
    refresh_note = "Geen auto-refresh tijdens bevestiging" if hold_page else "Ververst elke 20s"
    toolbar = _toolbar(running=running, has_positions=n_pos > 0, hold=hold_page)
    sell_html = _sell_confirm_panel(status, sell) if sell else ""
    sell_all_html = _sell_all_confirm_panel(status) if sell_all else ""
    report_html = _report_panel(report) if report else ""
    preview_html = (
        f'<div class="card section"><div class="card-head"><h2>Simulatie</h2>'
        f"{_simulate_button()}</div>{_preview_panel(preview, ledger_rows, commit)}</div>"
        if preview
        else ""
    )

    earnings_html = _earnings_masthead(earnings, pill=pill, venue=venue)

    heroes = "".join(
        [
            _hero(
                "Equity (cash + posities)",
                _fmt_eur(status.get("equity_eur"), signed=False),
                hint=f"{cash_hint} · ingezet {_fmt_eur(status.get('exposure_eur'), signed=False)}",
            ),
            _hero(
                "Core vandaag",
                _fmt_eur(risk.get("day_realized_eur")),
                cls=_cls(risk.get("day_realized_eur")),
                hint=f"limiet −{float(cfg.get('day_loss_limit_eur') or 0):.0f} € · win {win_rate}",
            ),
            _hero(
                "Open resultaat (beide)",
                _fmt_eur(earnings.open_mtm_eur if earnings else status.get("unrealized_net_eur")),
                cls=_cls(earnings.open_mtm_eur if earnings else status.get("unrealized_net_eur")),
                hint=f"core {n_pos}/{cfg.get('max_positions')} · fees {fees:,.2f} €",
            ),
            _hero(
                "Volgende beslissing",
                f"<span class='mono' style='font-size:1rem'>"
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
    vol_earn = _sleeve_earnings_line(earnings.volatile if earnings else None)
    vol_ledger_html = (
        f'<div class="card section"><h2>Volatile ledger</h2>'
        f"{_ledger_table(volatile_ledger_rows or [])}</div>"
        if volatile_ledger_rows is not None
        else ""
    )
    html = f"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0f5c4c">
{refresh_meta}
<title>Moreney · Momentum Desk</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
{earnings_html}
{err_html}
{toolbar}
{_sleeves_panel(status, volatile)}
{core_earn and f'<div class="muted" style="font-size:.8rem;margin:.35rem 0 0">Core netto · </div>{core_earn}' or ''}
{vol_earn and f'<div class="muted" style="font-size:.8rem">Volatile netto · </div>{vol_earn}' or ''}
<div class="hero-grid">{heroes}</div>
{preview_html}
{report_html}
{sell_html}
{sell_all_html}
<div class="stack two section">
  <div class="card"><div class="card-head"><h2>Core · open posities</h2></div>
  {_positions_table(status)}</div>
  <div class="card"><div class="card-head"><h2>Volatile · open posities</h2></div>
  {_positions_table(
      volatile or {},
      sell_all_path=None,
      post_sell_action="/live/momentum/volatile/sell",
      empty_text="Geen open volatile-posities — soft book staat klaar.",
  )}</div>
</div>
<div class="stack two section">
  <div class="card"><div class="card-head"><h2>Core · laatste beslissing</h2>
  {_simulate_button() if running and not preview else ""}</div>{_decision_panel(status)}</div>
  <div class="card"><div class="card-head"><h2>Volatile · laatste beslissing</h2>
  {_volatile_actions(volatile)}</div>{_decision_panel(volatile or {})}</div>
</div>
<div class="card section"><h2>Core ledger</h2>{_ledger_table(ledger_rows)}</div>
{vol_ledger_html}
<div class="card section"><h2>Regels (core)</h2>{_rules(cfg)}</div>
<p class="muted" style="margin-top:1rem;font-size:.75rem">{refresh_note} ·
<a href="/live/momentum/status" style="color:var(--accent)">core JSON</a> ·
<a href="/live/momentum/volatile/status" style="color:var(--accent)">volatile JSON</a> ·
<a href="/live/momentum/ledger" style="color:var(--accent)">ledger</a> ·
<a href="/live/momentum/earnings" style="color:var(--accent)">earnings JSON</a></p>
</div>
<div class="sticky-actions">{toolbar}</div>
</body></html>"""
    return HTMLResponse(html)

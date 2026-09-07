"""Operator page for the Daily Momentum Desk (server-rendered, auto-refresh)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from html import escape
from typing import Any

from fastapi.responses import HTMLResponse

from bot.live.dashboard_v2 import dashboard_css

_CSS = """
    .mono { font-family: var(--mono); }
    table.desk { width: 100%; border-collapse: collapse; font-size: .85rem; }
    table.desk th, table.desk td { padding: .45rem .55rem; text-align: right;
      border-bottom: 1px solid var(--line); white-space: nowrap; }
    table.desk th { color: var(--muted); font-weight: 500; font-size: .72rem;
      letter-spacing: .04em; text-transform: uppercase; }
    table.desk td:first-child, table.desk th:first-child { text-align: left; }
    .good { color: var(--good); } .bad { color: var(--bad); } .warn { color: var(--warn); }
    .muted { color: var(--muted); }
    .rules { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: .5rem .9rem; font-size: .8rem; }
    .rules div span { display: block; color: var(--muted); font-size: .68rem; }
    .chips span { display: inline-block; margin: .15rem .25rem 0 0; padding: .15rem .5rem;
      border: 1px solid var(--line); border-radius: 999px; font-size: .74rem; }
    .section { margin-top: 1.1rem; }
    .section h2 { font-family: var(--display); font-weight: 500; font-size: 1.05rem;
      margin: 0 0 .6rem; }
    .stack { display: grid; gap: 1rem; }
    @media (min-width: 980px) { .stack.two { grid-template-columns: 1.15fr .85fr; } }
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
    hint_html = f'<div class="hint">{escape(hint)}</div>' if hint else ""
    return (
        f'<div class="hero-card {cls}"><div class="label">{escape(label)}</div>'
        f'<div class="value {cls}">{value}</div>{hint_html}</div>'
    )


def _positions_table(status: Mapping[str, Any]) -> str:
    rows = status.get("positions") or []
    cfg = status.get("config") or {}
    if not rows:
        return '<p class="muted">Geen open posities — 100% cash tot de volgende beslissing.</p>'
    trail = float(cfg.get("trail_pct") or 0.03)
    tight_after = float(cfg.get("trail_tight_after") or 0.0)
    tight = float(cfg.get("trail_tight_pct") or trail)
    stop = float(cfg.get("hard_stop_pct") or 0.03)
    out = [
        '<table class="desk"><thead><tr><th>Base</th><th>Entry</th><th>Mark</th><th>Gross</th>'
        "<th>Peak</th><th>Trail-stop</th><th>Hard-stop</th><th>Net</th><th>Age</th></tr></thead><tbody>"
    ]
    for p in rows:
        entry = float(p.get("entry_price") or 0)
        peak_ret = float(p.get("peak_return") or 0)
        peak_px = entry * (1 + peak_ret)
        eff_trail = tight if (tight_after > 0 and peak_ret >= tight_after) else trail
        trail_px = peak_px * (1 - eff_trail)
        stop_px = entry * (1 - stop)
        mark = p.get("mark")
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
            "</tr>"
        )
    out.append("</tbody></table>")
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
    items = [
        ("Beslismoment (UTC)", hours),
        (
            "Clip",
            f"{float(cfg.get('clip_eur') or 0):,.0f} € "
            f"(AlphaI-pick ×{cfg.get('alphai_clip_mult')})",
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


def render_momentum_dashboard(
    status: Mapping[str, Any], ledger_rows: Sequence[Mapping[str, Any]]
) -> HTMLResponse:
    running = bool(status.get("running"))
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

    heroes = "".join(
        [
            _hero(
                "Equity (cash + posities)",
                _fmt_eur(status.get("equity_eur"), signed=False),
                hint=f"{cash_hint} · ingezet {_fmt_eur(status.get('exposure_eur'), signed=False)}",
            ),
            _hero(
                "Gerealiseerd totaal",
                _fmt_eur(status.get("realized_total_eur")),
                cls=_cls(status.get("realized_total_eur")),
                hint=f"{status.get('trade_count') or 0} trades · win {win_rate} · "
                f"fees {fees:,.2f} €",
            ),
            _hero(
                "Vandaag",
                _fmt_eur(risk.get("day_realized_eur")),
                cls=_cls(risk.get("day_realized_eur")),
                hint=f"limiet −{float(cfg.get('day_loss_limit_eur') or 0):.0f} €",
            ),
            _hero(
                "Deze week",
                _fmt_eur(risk.get("week_realized_eur")),
                cls=_cls(risk.get("week_realized_eur")),
                hint=f"limiet −{float(cfg.get('week_loss_limit_eur') or 0):.0f} €",
            ),
            _hero(
                "Open resultaat",
                _fmt_eur(status.get("unrealized_net_eur")),
                cls=_cls(status.get("unrealized_net_eur")),
                hint=f"{n_pos}/{cfg.get('max_positions')} posities",
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
    html = f"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="20">
<title>Momentum Desk</title>
<style>{dashboard_css()}{_CSS}</style></head>
<body><div class="wrap">
<div class="topbar">
  <div><h1 style="margin:0;font-family:var(--display);font-weight:500">Momentum Desk</h1>
  <div class="tagline">Dagelijkse RS-leaders · één exit-regel · {venue}</div></div>
  <div>{pill}</div>
</div>
{err_html}
<div class="hero-grid" style="margin-top:1rem">{heroes}</div>
<div class="stack two section">
  <div class="card"><h2>Open posities</h2>{_positions_table(status)}</div>
  <div class="card"><h2>Laatste beslissing</h2>{_decision_panel(status)}</div>
</div>
<div class="card section"><h2>Ledger</h2>{_ledger_table(ledger_rows)}</div>
<div class="card section"><h2>Regels</h2>{_rules(cfg)}</div>
<p class="muted" style="margin-top:1rem;font-size:.75rem">Ververst elke 20s ·
<a href="/live/momentum/status" style="color:var(--blue)">status JSON</a> ·
<a href="/live/momentum/ledger" style="color:var(--blue)">ledger JSON</a> ·
<a href="/live/dashboard/legacy" style="color:var(--muted)">oude desk</a></p>
</div></body></html>"""
    return HTMLResponse(html)

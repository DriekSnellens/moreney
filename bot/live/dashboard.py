"""Live-only operator dashboard (no paper trading UI)."""

from __future__ import annotations

import html
import json
from typing import Any

from fastapi.responses import HTMLResponse


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _pill(ok: bool | None, *, yes: str = "ok", no: str = "blok", unk: str = "—") -> str:
    if ok is True:
        return f'<span class="pill ok">{_esc(yes)}</span>'
    if ok is False:
        return f'<span class="pill warn">{_esc(no)}</span>'
    return f'<span class="pill muted">{_esc(unk)}</span>'


def _css() -> str:
    return """
    :root {
      --bg: #0b0f14;
      --surface: #121820;
      --surface-2: #171f2a;
      --text: #eef3f8;
      --muted: #8fa0b8;
      --accent: #4da3ff;
      --good: #34d399;
      --bad: #f87171;
      --warn: #fbbf24;
      --line: #243041;
      --radius: 14px;
      --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      --sans: "IBM Plex Sans", "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: var(--sans);
      color: var(--text);
      min-height: 100vh;
      background:
        radial-gradient(900px 420px at 0% -5%, rgba(77,163,255,.14), transparent 60%),
        radial-gradient(700px 380px at 100% 0%, rgba(52,211,153,.08), transparent 55%),
        var(--bg);
      line-height: 1.45;
    }
    a { color: var(--accent); text-decoration: none; }
    .hero {
      border-bottom: 1px solid var(--line);
      padding: 1.1rem 1rem;
      background: color-mix(in srgb, var(--surface) 75%, transparent);
    }
    .hero-inner {
      max-width: 1100px; margin: 0 auto;
      display: flex; justify-content: space-between; gap: 1rem; align-items: flex-start;
    }
    .eyebrow { margin: 0; color: var(--muted); font-size: .82rem; letter-spacing: .04em; text-transform: uppercase; }
    .brand { margin: .2rem 0; font-size: 1.9rem; letter-spacing: -0.02em; }
    .sub { margin: 0; color: var(--muted); max-width: 36rem; }
    .badge {
      display: inline-flex; align-items: center; padding: .35rem .7rem;
      border-radius: 999px; border: 1px solid var(--line); font-size: .82rem; font-weight: 600;
    }
    .badge.ok { color: var(--good); border-color: color-mix(in srgb, var(--good) 45%, var(--line)); }
    .badge.warn { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 45%, var(--line)); }
    .badge.muted { color: var(--muted); }
    .container { max-width: 1100px; margin: 0 auto; padding: 1rem; display: grid; gap: 1rem; }
    .panel {
      background: var(--surface); border: 1px solid var(--line);
      border-radius: var(--radius); padding: 1rem 1.1rem;
    }
    .panel.highlight {
      background: linear-gradient(180deg, color-mix(in srgb, var(--surface-2) 90%, #1a2940), var(--surface));
    }
    .panel-head { display: flex; justify-content: space-between; gap: .75rem; align-items: center; }
    h2 { margin: 0 0 .75rem; font-size: 1.05rem; }
    .note { color: var(--muted); font-size: .88rem; margin: 0 0 .85rem; }
    .metric-grid {
      display: grid; gap: .65rem;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    }
    .metric-card {
      background: var(--surface-2); border: 1px solid var(--line);
      border-radius: 10px; padding: .7rem .8rem; display: grid; gap: .25rem;
    }
    .metric-card .label { color: var(--muted); font-size: .75rem; font-weight: 600; }
    .metric-card .value { font-family: var(--mono); font-size: 1.05rem; font-weight: 650; }
    .pill {
      display: inline-flex; align-items: center; padding: .15rem .45rem;
      border-radius: 999px; border: 1px solid var(--line); font-size: .75rem; font-weight: 600;
    }
    .pill.ok { color: var(--good); }
    .pill.warn { color: var(--warn); }
    .pill.muted { color: var(--muted); }
    .controls { display: flex; flex-wrap: wrap; gap: .55rem; margin-top: .85rem; }
    .btn {
      appearance: none; border: 1px solid var(--line); background: var(--surface-2);
      color: var(--text); border-radius: 10px; padding: .55rem .9rem; font-weight: 600; cursor: pointer;
    }
    .btn:hover { border-color: color-mix(in srgb, var(--accent) 50%, var(--line)); }
    .btn-danger { border-color: color-mix(in srgb, var(--bad) 45%, var(--line)); color: #fecaca; }
    table { width: 100%; border-collapse: collapse; font-size: .88rem; }
    th { text-align: left; color: var(--muted); font-size: .75rem; padding: .4rem .35rem; border-bottom: 1px solid var(--line); }
    td { padding: .45rem .35rem; border-bottom: 1px solid var(--line); vertical-align: top; }
    td.num { font-family: var(--mono); }
    td.empty { color: var(--muted); }
    pre {
      margin: 0; white-space: pre-wrap; word-break: break-word;
      font-family: var(--mono); font-size: .8rem; color: #d7e2ef;
      background: var(--surface-2); border: 1px solid var(--line);
      border-radius: 10px; padding: .75rem;
    }
    """


def render_live_dashboard(payload: dict[str, Any]) -> HTMLResponse:
    """Primary operator surface: micro session + live readiness + balances."""
    session = payload.get("session") or {}
    engine = payload.get("engine") or {}
    unlock = payload.get("unlock") or {}
    observe = payload.get("observe") or {}
    readiness = payload.get("readiness") or {}
    alerts = payload.get("alerts") or {}

    running = bool(session.get("running") or session.get("task_running"))
    can_place = bool(engine.get("can_place_orders") or unlock.get("can_place_orders"))
    state_pill = "ok" if running else ("warn" if session.get("ok") is False else "muted")
    state_label = "RUNNING" if running else _esc(session.get("message") or "idle")

    bridge = session.get("bridge") or {}
    skips = bridge.get("skips") or {}
    skip_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td class='num'>{_esc(v)}</td></tr>"
        for k, v in sorted(skips.items(), key=lambda kv: (-int(kv[1] or 0), str(kv[0])))
    ) or "<tr><td colspan='2' class='empty'>Geen skips</td></tr>"

    last = session.get("last_live_trade") or {}
    last_txt = _esc(json.dumps(last, default=str)[:800] if last else "—")

    flags = unlock.get("flags") or []
    flag_rows = "".join(
        "<tr>"
        f"<td>{_esc(f.get('id'))}</td>"
        f"<td>{_pill(bool(f.get('set')), yes='aan', no='uit')}</td>"
        f"<td>{_esc(f.get('hint') or '')}</td>"
        "</tr>"
        for f in flags
    ) or "<tr><td colspan='3' class='empty'>Geen flags</td></tr>"

    balances = observe.get("balances") or []
    bal_rows_list: list[str] = []
    for entry in balances:
        if isinstance(entry, dict) and isinstance(entry.get("balances"), list):
            venue = entry.get("venue")
            for b in entry.get("balances") or []:
                bal_rows_list.append(
                    "<tr>"
                    f"<td>{_esc(venue or b.get('venue'))}</td>"
                    f"<td>{_esc(b.get('asset'))}</td>"
                    f"<td class='num'>{_esc(b.get('available') if b.get('available') is not None else b.get('free') or b.get('total'))}</td>"
                    f"<td class='num'>{_esc(b.get('total') or '')}</td>"
                    "</tr>"
                )
        elif isinstance(entry, dict) and entry.get("asset"):
            bal_rows_list.append(
                "<tr>"
                f"<td>{_esc(entry.get('venue'))}</td>"
                f"<td>{_esc(entry.get('asset'))}</td>"
                f"<td class='num'>{_esc(entry.get('available') if entry.get('available') is not None else entry.get('free') or entry.get('total'))}</td>"
                f"<td class='num'>{_esc(entry.get('total') or '')}</td>"
                "</tr>"
            )
    if not bal_rows_list and observe.get("venues"):
        for v in observe.get("venues") or []:
            venue = v.get("venue")
            for b in v.get("balances") or []:
                bal_rows_list.append(
                    "<tr>"
                    f"<td>{_esc(venue)}</td>"
                    f"<td>{_esc(b.get('asset'))}</td>"
                    f"<td class='num'>{_esc(b.get('available') or b.get('free') or b.get('total'))}</td>"
                    f"<td class='num'>{_esc(b.get('total') or '')}</td>"
                    "</tr>"
                )
    bal_rows = "".join(bal_rows_list) or (
        "<tr><td colspan='4' class='empty'>Geen live balances</td></tr>"
    )

    alert_items = alerts.get("alerts") or alerts.get("items") or []
    if isinstance(alert_items, list) and alert_items:
        alert_html = "".join(
            f"<li>{_esc(a.get('message') if isinstance(a, dict) else a)}</li>"
            for a in alert_items[:12]
        )
    else:
        alert_html = "<li class='empty'>Geen alerts</li>"

    funnel = session.get("pipeline_funnel") or {}
    funnel_json = _esc(json.dumps(funnel, default=str)[:2000] if funnel else "—")

    mode_label = (
        "continuous"
        if session.get("continuous") or session.get("remaining_seconds") is None
        else f"{session.get('remaining_seconds')}s left"
    )

    # Warn if Bitvavo free EUR is far below configured pocket.
    try:
        budget = float(session.get("budget_eur") or bridge.get("budget_eur") or 0)
    except (TypeError, ValueError):
        budget = 0.0
    bitvavo_eur = 0.0
    for entry in observe.get("balances") or []:
        if not isinstance(entry, dict):
            continue
        nested = entry.get("balances") if isinstance(entry.get("balances"), list) else None
        rows = nested if nested is not None else ([entry] if entry.get("asset") else [])
        for b in rows:
            if str(b.get("asset") or "").upper() == "EUR" and str(
                b.get("venue") or entry.get("venue") or ""
            ).lower() in {"bitvavo", ""}:
                try:
                    bitvavo_eur = max(bitvavo_eur, float(b.get("available") or b.get("total") or 0))
                except (TypeError, ValueError):
                    pass
    funding_note = ""
    if budget > 0 and bitvavo_eur + 1 < budget:
        gap = budget - bitvavo_eur
        funding_note = (
            f"<p class='note' style='color:var(--warn)'>"
            f"Funding gap: Bitvavo EUR ≈ {_esc(f'{bitvavo_eur:.2f}')}, pocket €{_esc(f'{budget:.0f}')}. "
            f"Stort ~€{_esc(f'{gap:.0f}')} via SEPA → Bitvavo vóór live size matcht."
            f"</p>"
        )

    html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta http-equiv="refresh" content="3"/>
  <title>Moreney — Live</title>
  <style>{_css()}</style>
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      <div>
        <p class="eyebrow">Live trading · Bitvavo micro</p>
        <h1 class="brand">Moreney</h1>
        <p class="sub">Alleen live. Paper-dashboards zijn uitgeschakeld. Refresh 3s.</p>
      </div>
      <div>
        <span class="badge {state_pill}">{state_label}</span>
        {_pill(can_place, yes="orders unlocked", no=_esc(engine.get("block_reason") or unlock.get("block_reason") or "locked"))}
      </div>
    </div>
  </header>

  <main class="container">
    <section class="panel highlight">
      <div class="panel-head">
        <h2>Micro-live sessie</h2>
        <span class="pill {"ok" if running else "muted"}">{_esc(mode_label)}</span>
      </div>
      <p class="note">€ pocket = totaalkapitaal (recyclable). BTC standaard uit. API: <code>/live/micro/session</code></p>
      {funding_note}
      <div class="metric-grid">
        <article class="metric-card"><span class="label">Budget</span><span class="value">{_esc(session.get("budget_eur") or bridge.get("budget_eur") or "—")}</span></article>
        <article class="metric-card"><span class="label">Free EUR</span><span class="value">{_esc(bridge.get("free_quote_eur") or bridge.get("remaining_eur") or "—")}</span></article>
        <article class="metric-card"><span class="label">Turnover</span><span class="value">{_esc(bridge.get("turnover_eur") or "0")}</span></article>
        <article class="metric-card"><span class="label">PnL pocket</span><span class="value">{_esc(session.get("pnl_paper_pocket_eur") or "—")}</span></article>
        <article class="metric-card"><span class="label">Cycles</span><span class="value">{_esc(session.get("paper_cycles") or 0)}</span></article>
        <article class="metric-card"><span class="label">Live fills</span><span class="value">{_esc(session.get("live_trades_executed") or 0)}/{_esc(session.get("live_trades_attempted") or 0)}</span></article>
        <article class="metric-card"><span class="label">Symbols</span><span class="value">{_esc(session.get("symbol_count") or "—")}</span></article>
        <article class="metric-card"><span class="label">Strategy</span><span class="value">{_esc(session.get("strategy") or "—")}</span></article>
        <article class="metric-card"><span class="label">Elapsed</span><span class="value">{_esc(session.get("elapsed_seconds") or 0)}s</span></article>
        <article class="metric-card"><span class="label">Engine armed</span><span class="value">{_esc(engine.get("armed"))}</span></article>
      </div>
      <div class="controls">
        <button type="button" class="btn" onclick="post('/live/micro/session/start', {{minutes:null,budget_eur:2024,exclude_btc:true}})">Start continuous / €2024</button>
        <button type="button" class="btn btn-danger" onclick="post('/live/micro/session/stop')">Stop</button>
        <button type="button" class="btn" onclick="post('/live/micro/arm')">Arm engine</button>
        <button type="button" class="btn" onclick="post('/live/micro/disarm')">Disarm</button>
        <button type="button" class="btn btn-danger" onclick="post('/risk/kill-switch/emergency-stop', {{reason:'dashboard emergency stop'}})">Emergency stop</button>
      </div>
    </section>

    <section class="panel">
      <h2>Live readiness</h2>
      <p class="note">Fase {_esc(readiness.get("active_phase") or "—")}. Withdrawals: uit.</p>
      <div class="metric-grid">
        <article class="metric-card"><span class="label">Can place</span><span class="value">{_pill(bool(readiness.get("can_place_live_orders") or can_place), yes="ja", no="nee")}</span></article>
        <article class="metric-card"><span class="label">Live trading</span><span class="value">{_esc(readiness.get("live_trading_enabled"))}</span></article>
        <article class="metric-card"><span class="label">Observe online</span><span class="value">{_esc(observe.get("venues_online"))}/{_esc(observe.get("venues_total"))}</span></article>
        <article class="metric-card"><span class="label">Portfolio mark</span><span class="value">{_esc(observe.get("total_value_eur") or "—")}</span></article>
      </div>
      <div class="table-wrap" style="margin-top:1rem">
        <table>
          <thead><tr><th>Unlock flag</th><th>Status</th><th>Hint</th></tr></thead>
          <tbody>{flag_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Live balances</h2>
      <p class="note">{_esc(observe.get("note") or "Read-only exchange balances.")}</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Venue</th><th>Asset</th><th>Available</th><th>Total</th></tr></thead>
          <tbody>{bal_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Bridge skips</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Reden</th><th>Count</th></tr></thead>
        <tbody>{skip_rows}</tbody>
      </table></div>
    </section>

    <section class="panel">
      <h2>Laatste live trade</h2>
      <pre>{last_txt}</pre>
    </section>

    <section class="panel">
      <h2>Pipeline funnel</h2>
      <pre>{funnel_json}</pre>
    </section>

    <section class="panel">
      <h2>Alerts</h2>
      <ul>{alert_html}</ul>
    </section>
  </main>
  <script>
    async function post(url, body) {{
      await fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(body || {{}})
      }});
      location.reload();
    }}
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)

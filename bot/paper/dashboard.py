"""Minimal paper-trading dashboard (HTML). Extends the API — no fabricated values."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi.responses import HTMLResponse


def render_dashboard(payload: dict[str, Any]) -> HTMLResponse:
    """Render a live paper dashboard from real runner/API values only."""
    perf = payload.get("performance") or {}
    status = payload.get("status") or {}
    strategies = payload.get("strategies") or []
    pairs = payload.get("exchanges") or []
    opportunities = payload.get("opportunities") or []

    def v(key: str, default: str = "0") -> str:
        val = perf.get(key, default)
        return _fmt(val)

    strategy_rows = "".join(
        f"<tr><td>{_esc(s.get('strategy'))}</td>"
        f"<td>{_esc(s.get('net_pnl'))}</td>"
        f"<td>{_esc(s.get('opportunities'))}</td>"
        f"<td>{_esc(s.get('trades'))}</td>"
        f"<td>{_esc(s.get('win_rate'))}</td></tr>"
        for s in strategies
    ) or "<tr><td colspan='5'>No strategy data yet</td></tr>"

    pair_rows = "".join(
        f"<tr><td>{_esc(p.get('buy_exchange'))} → {_esc(p.get('sell_exchange'))}</td>"
        f"<td>{_esc(p.get('net_pnl'))}</td>"
        f"<td>{_esc(p.get('opportunities'))}</td>"
        f"<td>{_esc(p.get('executed'))}</td>"
        f"<td>{_esc(p.get('win_rate'))}</td></tr>"
        for p in pairs
    ) or "<tr><td colspan='5'>No exchange-pair data yet</td></tr>"

    opp_rows = "".join(
        f"<tr><td>{_esc(o.get('timestamp'))}</td>"
        f"<td>{_esc(o.get('symbol'))}</td>"
        f"<td>{_esc(o.get('buy_exchange'))}</td>"
        f"<td>{_esc(o.get('sell_exchange'))}</td>"
        f"<td>{_esc(o.get('expected_net_profit'))}</td>"
        f"<td>{_esc(o.get('realized_net_profit'))}</td>"
        f"<td>{_esc(o.get('status'))}</td></tr>"
        for o in opportunities[:25]
    ) or "<tr><td colspan='7'>No opportunities recorded</td></tr>"

    running = "RUNNING" if status.get("running") else "STOPPED"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta http-equiv="refresh" content="5"/>
  <title>Moreney Paper Trading</title>
  <style>
    :root {{
      --bg: #0f1419;
      --panel: #1a222d;
      --text: #e7ecf3;
      --muted: #8b9bb4;
      --accent: #3d9cf0;
      --good: #3ecf8e;
      --bad: #f07178;
      --line: #2a3544;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background:
        radial-gradient(1200px 600px at 10% -10%, #1b3a57 0%, transparent 55%),
        radial-gradient(900px 500px at 100% 0%, #243447 0%, transparent 50%),
        var(--bg);
      color: var(--text);
      min-height: 100vh;
    }}
    header {{
      padding: 2rem 1.5rem 1rem;
      border-bottom: 1px solid var(--line);
    }}
    .brand {{
      font-family: "IBM Plex Serif", Georgia, serif;
      font-size: clamp(2rem, 4vw, 3rem);
      letter-spacing: -0.03em;
      margin: 0;
    }}
    .sub {{
      color: var(--muted);
      margin-top: 0.35rem;
      font-size: 0.95rem;
    }}
    .badge {{
      display: inline-block;
      margin-top: 0.75rem;
      padding: 0.25rem 0.6rem;
      border: 1px solid var(--line);
      color: var(--accent);
      font-size: 0.75rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    main {{
      display: grid;
      gap: 1.25rem;
      padding: 1.25rem 1.5rem 2.5rem;
      max-width: 1200px;
      margin: 0 auto;
    }}
    section {{
      background: color-mix(in srgb, var(--panel) 88%, transparent);
      border: 1px solid var(--line);
      padding: 1rem 1.15rem 1.2rem;
    }}
    h2 {{
      margin: 0 0 0.85rem;
      font-size: 0.8rem;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
      font-weight: 600;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 0.85rem;
    }}
    .metric .label {{ color: var(--muted); font-size: 0.75rem; }}
    .metric .value {{
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 1.25rem;
      margin-top: 0.2rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
    }}
    th, td {{
      text-align: left;
      padding: 0.45rem 0.35rem;
      border-bottom: 1px solid var(--line);
      font-family: "IBM Plex Mono", ui-monospace, monospace;
    }}
    th {{ color: var(--muted); font-weight: 500; font-size: 0.72rem; text-transform: uppercase; }}
    .controls {{ display: flex; gap: 0.5rem; flex-wrap: wrap; }}
    button {{
      background: transparent;
      color: var(--text);
      border: 1px solid var(--line);
      padding: 0.45rem 0.9rem;
      cursor: pointer;
      font: inherit;
    }}
    button:hover {{ border-color: var(--accent); color: var(--accent); }}
    .footer {{
      color: var(--muted);
      font-size: 0.75rem;
      padding: 0 1.5rem 2rem;
      max-width: 1200px;
      margin: 0 auto;
    }}
  </style>
</head>
<body>
  <header>
    <h1 class="brand">Moreney</h1>
    <p class="sub">Paper trading dashboard — public market data only. Execution mode: PAPER.</p>
    <span class="badge">{running} · REAL ORDERS = 0</span>
  </header>
  <main>
    <section>
      <h2>Session</h2>
      <div class="controls">
        <button onclick="post('/paper/start')">Start</button>
        <button onclick="post('/paper/stop')">Stop</button>
        <button onclick="resetPaper()">Reset (confirm)</button>
      </div>
    </section>
    <section>
      <h2>Portfolio</h2>
      <div class="grid">
        <div class="metric"><div class="label">Starting capital</div><div class="value">{v('starting_equity')}</div></div>
        <div class="metric"><div class="label">Current equity</div><div class="value">{v('current_equity')}</div></div>
        <div class="metric"><div class="label">Net PnL</div><div class="value">{v('net_pnl')}</div></div>
        <div class="metric"><div class="label">Return %</div><div class="value">{v('return_pct')}</div></div>
        <div class="metric"><div class="label">Drawdown</div><div class="value">{v('current_drawdown')}</div></div>
        <div class="metric"><div class="label">Max drawdown</div><div class="value">{v('maximum_drawdown')}</div></div>
      </div>
    </section>
    <section>
      <h2>Trading</h2>
      <div class="grid">
        <div class="metric"><div class="label">Pairs evaluated</div><div class="value">{v('pairs_evaluated')}</div></div>
        <div class="metric"><div class="label">Edges found</div><div class="value">{v('depth_edges_found')}</div></div>
        <div class="metric"><div class="label">Scan rejects</div><div class="value">{v('scan_rejections')}</div></div>
        <div class="metric"><div class="label">Passed gates</div><div class="value">{v('total_opportunities')}</div></div>
        <div class="metric"><div class="label">Approved</div><div class="value">{v('approved_opportunities')}</div></div>
        <div class="metric"><div class="label">Risk rejected</div><div class="value">{v('rejected_opportunities')}</div></div>
        <div class="metric"><div class="label">Executed</div><div class="value">{v('executed_opportunities')}</div></div>
        <div class="metric"><div class="label">Trades</div><div class="value">{v('trade_count')}</div></div>
        <div class="metric"><div class="label">Win rate</div><div class="value">{v('win_rate')}</div></div>
        <div class="metric"><div class="label">Profit factor</div><div class="value">{v('profit_factor')}</div></div>
      </div>
    </section>
    <section>
      <h2>Costs</h2>
      <div class="grid">
        <div class="metric"><div class="label">Fees</div><div class="value">{v('fees')}</div></div>
        <div class="metric"><div class="label">Slippage</div><div class="value">{v('slippage')}</div></div>
      </div>
    </section>
    <section>
      <h2>Strategy performance</h2>
      <table>
        <thead><tr><th>Strategy</th><th>Net PnL</th><th>Opps</th><th>Trades</th><th>Win rate</th></tr></thead>
        <tbody>{strategy_rows}</tbody>
      </table>
    </section>
    <section>
      <h2>Exchange performance</h2>
      <table>
        <thead><tr><th>Pair</th><th>Net PnL</th><th>Opps</th><th>Executed</th><th>Win rate</th></tr></thead>
        <tbody>{pair_rows}</tbody>
      </table>
    </section>
    <section>
      <h2>Recent opportunities</h2>
      <table>
        <thead>
          <tr>
            <th>Timestamp</th><th>Symbol</th><th>Buy</th><th>Sell</th>
            <th>Expected net</th><th>Realized net</th><th>Status</th>
          </tr>
        </thead>
        <tbody>{opp_rows}</tbody>
      </table>
    </section>
  </main>
  <p class="footer">
    WITHDRAWALS = 0 · LEVERAGE = 0 · EXECUTION MODE = PAPER · Auto-refresh 5s ·
    Data from live paper runner only. · <a href="/logout">Logout</a>
  </p>
  <script>
    async function post(url, body) {{
      await fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(body || {{}})
      }});
      location.reload();
    }}
    async function resetPaper() {{
      if (!confirm('Reset paper portfolio to starting capital? This never affects real exchange accounts.')) return;
      await post('/paper/reset', {{confirm: true}});
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


def render_dashboard_lite(payload: dict[str, Any]) -> HTMLResponse:
    """Render a compact, mobile-first paper dashboard."""
    perf = payload.get("performance") or {}
    status = payload.get("status") or {}
    opportunities = payload.get("opportunities") or []

    def v(key: str, default: str = "0") -> str:
        return _fmt(perf.get(key, default))

    recent = "".join(
        f"<div class='opp'>"
        f"<div><strong>{_esc(o.get('symbol'))}</strong> {_esc(o.get('status'))}</div>"
        f"<div>{_esc(o.get('buy_exchange'))} → {_esc(o.get('sell_exchange'))}</div>"
        f"<div>exp {_esc(o.get('expected_net_profit'))} · real {_esc(o.get('realized_net_profit'))}</div>"
        f"</div>"
        for o in opportunities[:12]
    ) or "<div class='empty'>No opportunities recorded</div>"

    running = "RUNNING" if status.get("running") else "STOPPED"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta http-equiv="refresh" content="5"/>
  <title>Moreney Lite Dashboard</title>
  <style>
    :root {{
      --bg:#0f1419; --card:#1a222d; --text:#e7ecf3; --muted:#8b9bb4; --line:#2a3544; --accent:#3d9cf0;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,Segoe UI,sans-serif; background:var(--bg); color:var(--text); }}
    .wrap {{ max-width:520px; margin:0 auto; padding:1rem; display:grid; gap:.75rem; }}
    .card {{ background:var(--card); border:1px solid var(--line); padding:.9rem; }}
    h1 {{ margin:0 0 .35rem; font-size:1.2rem; }}
    .sub {{ color:var(--muted); font-size:.85rem; }}
    .badge {{ display:inline-block; margin-top:.5rem; color:var(--accent); border:1px solid var(--line); padding:.2rem .5rem; font-size:.75rem; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:.5rem; }}
    .metric .k {{ color:var(--muted); font-size:.72rem; }}
    .metric .v {{ font-family:ui-monospace,monospace; font-size:1rem; }}
    .controls {{ display:flex; gap:.45rem; flex-wrap:wrap; }}
    button {{ background:transparent; color:var(--text); border:1px solid var(--line); padding:.4rem .75rem; }}
    .opp {{ border-top:1px solid var(--line); padding:.5rem 0; font-size:.82rem; }}
    .empty {{ color:var(--muted); font-size:.82rem; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Moreney Lite</h1>
      <div class="sub">Paper mode only · Auto refresh 5s</div>
      <div class="badge">{running} · REAL ORDERS = 0</div>
    </div>
    <div class="card">
      <div class="controls">
        <button onclick="post('/paper/start')">Start</button>
        <button onclick="post('/paper/stop')">Stop</button>
        <button onclick="resetPaper()">Reset</button>
      </div>
    </div>
    <div class="card">
      <div class="grid">
        <div class="metric"><div class="k">Equity</div><div class="v">{v('current_equity')}</div></div>
        <div class="metric"><div class="k">Net PnL</div><div class="v">{v('net_pnl')}</div></div>
        <div class="metric"><div class="k">Opportunities</div><div class="v">{v('total_opportunities')}</div></div>
        <div class="metric"><div class="k">Trades</div><div class="v">{v('trade_count')}</div></div>
        <div class="metric"><div class="k">Win rate</div><div class="v">{v('win_rate')}</div></div>
        <div class="metric"><div class="k">Drawdown</div><div class="v">{v('maximum_drawdown')}</div></div>
      </div>
    </div>
    <div class="card">
      <strong>Recent opportunities</strong>
      {recent}
    </div>
  </div>
  <script>
    async function post(url, body) {{
      await fetch(url, {{ method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(body||{{}}) }});
      location.reload();
    }}
    async function resetPaper() {{
      if (!confirm('Reset paper portfolio?')) return;
      await post('/paper/reset', {{confirm:true}});
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _esc(value: Any) -> str:
    text = "—" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_fleet_dashboard(payload: dict[str, Any]) -> HTMLResponse:
    """Render one page covering all configured paper instances."""
    instances = payload.get("instances") or []
    totals = payload.get("totals") or {}
    online = payload.get("online_count", 0)
    configured = payload.get("configured_count", 0)

    cards = []
    for row in instances:
        if not row.get("ok"):
            cards.append(
                f"""
                <article class="card bad">
                  <header class="card-head">
                    <h2>{_esc(row.get('label'))}</h2>
                    <span class="badge stop">OFFLINE</span>
                  </header>
                  <p class="error">{_esc(row.get('error'))}</p>
                  <p class="links"><a href="{_esc(row.get('dashboard_url'))}">Open instance</a></p>
                </article>
                """
            )
            continue
        running = "RUNNING" if row.get("running") else "STOPPED"
        badge_cls = "run" if row.get("running") else "stop"
        md = row.get("market_data") or {}
        md_bits = []
        for exchange, health in md.items():
            if not isinstance(health, dict):
                continue
            state = "ok" if health.get("synchronized") and not health.get("stale") else "warn"
            md_bits.append(
                f"<span class='md {state}'>{_esc(exchange)}</span>"
            )
        md_html = " ".join(md_bits) or "<span class='muted'>no feeds</span>"
        cards.append(
            f"""
            <article class="card">
              <header class="card-head">
                <div>
                  <h2>{_esc(row.get('label'))}</h2>
                  <div class="sub">start {_esc(row.get('starting_capital'))} · cycles {_esc(row.get('cycle_count'))}</div>
                </div>
                <span class="badge {badge_cls}">{running}</span>
              </header>
              <div class="metrics">
                <div><div class="k">Equity</div><div class="v">{_esc(row.get('equity'))}</div></div>
                <div><div class="k">Net PnL</div><div class="v">{_esc(row.get('net_pnl'))}</div></div>
                <div><div class="k">Trades</div><div class="v">{_esc(row.get('trade_count'))}</div></div>
                <div><div class="k">Passed gates</div><div class="v">{_esc(row.get('total_opportunities'))}</div></div>
                <div><div class="k">Pairs evaluated</div><div class="v">{_esc(row.get('pairs_evaluated'))}</div></div>
                <div><div class="k">Edges found</div><div class="v">{_esc(row.get('depth_edges_found'))}</div></div>
                <div><div class="k">Scan rejects</div><div class="v">{_esc(row.get('scan_rejections'))}</div></div>
                <div><div class="k">Win rate</div><div class="v">{_esc(row.get('win_rate'))}</div></div>
              </div>
              <div class="feeds">{md_html}</div>
              <p class="links">
                <a href="{_esc(row.get('dashboard_url'))}">Full dashboard</a>
                ·
                <a href="{_esc(row.get('dashboard_lite_url'))}">Lite</a>
              </p>
            </article>
            """
        )

    cards_html = "\n".join(cards) or "<p class='muted'>No fleet instances configured.</p>"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta http-equiv="refresh" content="5"/>
  <title>Moreney Fleet Dashboard</title>
  <style>
    :root {{
      --bg:#0f1419; --panel:#1a222d; --text:#e7ecf3; --muted:#8b9bb4;
      --accent:#3d9cf0; --good:#3ecf8e; --bad:#f07178; --line:#2a3544; --warn:#e6b84d;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      font-family:"IBM Plex Sans","Segoe UI",sans-serif;
      color:var(--text);
      background:
        radial-gradient(1100px 520px at 0% -10%, #1b3a57 0%, transparent 55%),
        radial-gradient(900px 480px at 100% 0%, #243447 0%, transparent 50%),
        var(--bg);
      min-height:100vh;
    }}
    .top {{
      padding:1.75rem 1.5rem 1rem;
      border-bottom:1px solid var(--line);
      display:flex;
      justify-content:space-between;
      gap:1rem;
      flex-wrap:wrap;
      align-items:end;
    }}
    .brand {{
      font-family:"IBM Plex Serif",Georgia,serif;
      font-size:clamp(1.8rem,3.5vw,2.6rem);
      margin:0;
      letter-spacing:-0.03em;
    }}
    .sub {{ color:var(--muted); font-size:.9rem; margin-top:.3rem; }}
    .totals {{
      display:grid;
      grid-template-columns:repeat(3, minmax(0,1fr));
      gap:.75rem;
      min-width:min(520px,100%);
    }}
    .totals .box {{
      background:color-mix(in srgb, var(--panel) 90%, transparent);
      border:1px solid var(--line);
      padding:.7rem .8rem;
    }}
    .k {{ color:var(--muted); font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; }}
    .v {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:1.05rem; margin-top:.2rem; }}
    main {{
      padding:1.25rem 1.5rem 2rem;
      display:grid;
      gap:1rem;
      grid-template-columns:repeat(auto-fit, minmax(280px, 1fr));
      max-width:1400px;
      margin:0 auto;
    }}
    .card {{
      background:color-mix(in srgb, var(--panel) 90%, transparent);
      border:1px solid var(--line);
      padding:1rem 1.05rem 1.1rem;
      display:grid;
      gap:.85rem;
    }}
    .card.bad {{ border-color:color-mix(in srgb, var(--bad) 55%, var(--line)); }}
    .card-head {{ display:flex; justify-content:space-between; gap:.75rem; align-items:start; }}
    .card h2 {{ margin:0; font-size:1.15rem; }}
    .badge {{
      border:1px solid var(--line);
      padding:.2rem .55rem;
      font-size:.72rem;
      letter-spacing:.08em;
      text-transform:uppercase;
      white-space:nowrap;
    }}
    .badge.run {{ color:var(--good); }}
    .badge.stop {{ color:var(--bad); }}
    .metrics {{
      display:grid;
      grid-template-columns:1fr 1fr;
      gap:.65rem .75rem;
    }}
    .feeds {{ display:flex; flex-wrap:wrap; gap:.35rem; }}
    .md {{
      border:1px solid var(--line);
      padding:.15rem .45rem;
      font-size:.72rem;
      color:var(--muted);
      text-transform:uppercase;
    }}
    .md.ok {{ color:var(--good); }}
    .md.warn {{ color:var(--warn); }}
    .links, .error {{ margin:0; font-size:.85rem; }}
    .links a {{ color:var(--accent); text-decoration:none; }}
    .error {{ color:var(--bad); }}
    .muted {{ color:var(--muted); }}
    .foot {{
      text-align:center;
      color:var(--muted);
      font-size:.78rem;
      padding:0 1rem 1.5rem;
    }}
    @media (max-width:980px) {{
      .totals {{ grid-template-columns:1fr 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="top">
    <div>
      <h1 class="brand">Moreney Fleet</h1>
      <div class="sub">All paper instances · {online}/{configured} online · auto-refresh 5s · real orders = 0 · <a href="/logout" style="color:var(--accent)">Logout</a></div>
    </div>
    <div class="totals">
      <div class="box"><div class="k">Total equity</div><div class="v">{_esc(totals.get('equity'))}</div></div>
      <div class="box"><div class="k">Total net PnL</div><div class="v">{_esc(totals.get('net_pnl'))}</div></div>
      <div class="box"><div class="k">Pairs evaluated</div><div class="v">{_esc(totals.get('pairs_evaluated'))}</div></div>
      <div class="box"><div class="k">Edges found</div><div class="v">{_esc(totals.get('depth_edges_found'))}</div></div>
      <div class="box"><div class="k">Passed gates</div><div class="v">{_esc(totals.get('total_opportunities'))}</div></div>
      <div class="box"><div class="k">Running</div><div class="v">{_esc(totals.get('running_count'))}/{configured}</div></div>
    </div>
  </div>
  <main>
    {cards_html}
  </main>
  <p class="foot">Paper mode only · withdrawals unsupported · leverage unsupported</p>
</body>
</html>"""
    return HTMLResponse(content=html)

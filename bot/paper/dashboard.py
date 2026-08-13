"""Paper-trading dashboard (HTML). Extends the API — no fabricated values."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from fastapi.responses import HTMLResponse

_TWO = Decimal("0.01")
_HUNDRED = Decimal("100")


def render_dashboard(payload: dict[str, Any]) -> HTMLResponse:
    """Render a live paper dashboard from real runner/API values only."""
    perf = payload.get("performance") or {}
    status = payload.get("status") or {}
    strategies = payload.get("strategies") or []
    pairs = payload.get("exchanges") or []
    opportunities = payload.get("opportunities") or []

    def m(key: str, default: str = "0") -> str:
        return _fmt_money(perf.get(key, default))

    def c(key: str, default: str = "0") -> str:
        return _fmt_count(perf.get(key, default))

    def p(key: str, default: str = "0") -> str:
        return _fmt_pct(perf.get(key, default))

    strategy_rows = "".join(
        f"<tr>"
        f"<td>{_esc(s.get('strategy'))}</td>"
        f"<td class='num {_pnl_class(s.get('net_pnl'))}'>{_esc_fmt(s.get('net_pnl'), 'money')}</td>"
        f"<td class='num'>{_esc_fmt(s.get('opportunities'), 'count')}</td>"
        f"<td class='num'>{_esc_fmt(s.get('trades'), 'count')}</td>"
        f"<td class='num'>{_esc_fmt(s.get('win_rate'), 'pct')}</td>"
        f"</tr>"
        for s in strategies
    ) or "<tr><td colspan='5' class='empty'>No strategy data yet</td></tr>"

    pair_rows = "".join(
        f"<tr>"
        f"<td>{_esc(p.get('buy_exchange'))} → {_esc(p.get('sell_exchange'))}</td>"
        f"<td class='num {_pnl_class(p.get('net_pnl'))}'>{_esc_fmt(p.get('net_pnl'), 'money')}</td>"
        f"<td class='num'>{_esc_fmt(p.get('opportunities'), 'count')}</td>"
        f"<td class='num'>{_esc_fmt(p.get('executed'), 'count')}</td>"
        f"<td class='num'>{_esc_fmt(p.get('win_rate'), 'pct')}</td>"
        f"</tr>"
        for p in pairs
    ) or "<tr><td colspan='5' class='empty'>No exchange-pair data yet</td></tr>"

    opp_rows = "".join(
        f"<tr>"
        f"<td class='ts'>{_esc(_short_ts(o.get('timestamp')))}</td>"
        f"<td><strong>{_esc(o.get('symbol'))}</strong></td>"
        f"<td>{_esc(o.get('buy_exchange'))}</td>"
        f"<td>{_esc(o.get('sell_exchange'))}</td>"
        f"<td class='num'>{_esc_fmt(o.get('expected_net_profit'), 'money')}</td>"
        f"<td class='num {_pnl_class(o.get('realized_net_profit'))}'>"
        f"{_esc_fmt(o.get('realized_net_profit'), 'money')}</td>"
        f"<td><span class='pill'>{_esc(o.get('status'))}</span></td>"
        f"</tr>"
        for o in opportunities[:25]
    ) or "<tr><td colspan='7' class='empty'>No opportunities recorded</td></tr>"

    running = status.get("running")
    status_label = "Running" if running else "Stopped"
    status_cls = "ok" if running else "warn"
    net_pnl = perf.get("net_pnl", 0)
    pnl_cls = _pnl_class(net_pnl)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
  <meta name="theme-color" content="#0b0f14"/>
  <meta http-equiv="refresh" content="5"/>
  <title>Moreney Paper Trading</title>
  <style>{_shared_css()}</style>
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      <div>
        <p class="eyebrow">Paper trading · no live orders</p>
        <h1 class="brand">Moreney</h1>
        <p class="sub">Public market data only · execution mode PAPER</p>
      </div>
      <div class="hero-badges">
        <span class="badge {status_cls}">{status_label}</span>
        <span class="badge muted">Real orders 0</span>
      </div>
    </div>
  </header>

  <main class="container">
    <section class="panel">
      <div class="panel-head">
        <h2>Controls</h2>
        <a class="link-lite" href="/paper/dashboard-lite">Mobile view</a>
      </div>
      <div class="controls">
        <button type="button" class="btn" onclick="post('/paper/start')">Start</button>
        <button type="button" class="btn" onclick="post('/paper/stop')">Stop</button>
        <button type="button" class="btn btn-danger" onclick="resetPaper()">Reset</button>
      </div>
    </section>

    <section class="panel highlight">
      <h2>Portfolio</h2>
      <div class="metric-grid">
        <article class="metric-card">
          <span class="label">Starting capital</span>
          <span class="value">{m('starting_equity')}</span>
        </article>
        <article class="metric-card">
          <span class="label">Current equity</span>
          <span class="value">{m('current_equity')}</span>
        </article>
        <article class="metric-card {pnl_cls}">
          <span class="label">Net PnL</span>
          <span class="value">{m('net_pnl')}</span>
        </article>
        <article class="metric-card">
          <span class="label">Return</span>
          <span class="value">{p('return_pct')}</span>
        </article>
        <article class="metric-card">
          <span class="label">Drawdown</span>
          <span class="value">{p('current_drawdown')}</span>
        </article>
        <article class="metric-card">
          <span class="label">Max drawdown</span>
          <span class="value">{p('maximum_drawdown')}</span>
        </article>
      </div>
    </section>

    <section class="panel">
      <h2>Trading activity</h2>
      <div class="metric-grid compact">
        <article class="metric-card"><span class="label">Pairs evaluated</span><span class="value">{c('pairs_evaluated')}</span></article>
        <article class="metric-card"><span class="label">Edges found</span><span class="value">{c('depth_edges_found')}</span></article>
        <article class="metric-card"><span class="label">Scan rejects</span><span class="value">{c('scan_rejections')}</span></article>
        <article class="metric-card"><span class="label">Passed gates</span><span class="value">{c('total_opportunities')}</span></article>
        <article class="metric-card"><span class="label">Approved</span><span class="value">{c('approved_opportunities')}</span></article>
        <article class="metric-card"><span class="label">Risk rejected</span><span class="value">{c('rejected_opportunities')}</span></article>
        <article class="metric-card"><span class="label">Executed</span><span class="value">{c('executed_opportunities')}</span></article>
        <article class="metric-card"><span class="label">Trades</span><span class="value">{c('trade_count')}</span></article>
        <article class="metric-card"><span class="label">Win rate</span><span class="value">{p('win_rate')}</span></article>
        <article class="metric-card"><span class="label">Profit factor</span><span class="value">{_esc_fmt(perf.get('profit_factor'), 'ratio')}</span></article>
      </div>
    </section>

    <section class="panel">
      <h2>Costs</h2>
      <div class="metric-grid compact">
        <article class="metric-card"><span class="label">Fees</span><span class="value">{m('fees')}</span></article>
        <article class="metric-card"><span class="label">Slippage</span><span class="value">{m('slippage')}</span></article>
        <article class="metric-card"><span class="label">Volume</span><span class="value">{m('trading_volume')}</span></article>
      </div>
    </section>

    <section class="panel">
      <h2>Strategy performance</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Strategy</th><th>Net PnL</th><th>Opps</th><th>Trades</th><th>Win rate</th></tr></thead>
          <tbody>{strategy_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Exchange performance</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Pair</th><th>Net PnL</th><th>Opps</th><th>Executed</th><th>Win rate</th></tr></thead>
          <tbody>{pair_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Recent opportunities</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time</th><th>Symbol</th><th>Buy</th><th>Sell</th>
              <th>Expected</th><th>Realized</th><th>Status</th>
            </tr>
          </thead>
          <tbody>{opp_rows}</tbody>
        </table>
      </div>
    </section>
  </main>

  <footer class="footer">
    Paper only · no withdrawals · no leverage · refreshes every 5s ·
    <a href="/logout">Logout</a>
  </footer>

  <script>{_shared_js()}</script>
</body>
</html>"""
    return HTMLResponse(content=html)


def render_dashboard_lite(payload: dict[str, Any]) -> HTMLResponse:
    """Render a compact, mobile-first paper dashboard."""
    perf = payload.get("performance") or {}
    status = payload.get("status") or {}
    opportunities = payload.get("opportunities") or []

    running = status.get("running")
    status_label = "Running" if running else "Stopped"
    status_cls = "ok" if running else "warn"
    pnl_cls = _pnl_class(perf.get("net_pnl", 0))

    recent = "".join(
        f"<article class='opp-card'>"
        f"<div class='opp-top'>"
        f"<strong>{_esc(o.get('symbol'))}</strong>"
        f"<span class='pill'>{_esc(o.get('status'))}</span>"
        f"</div>"
        f"<div class='opp-route'>{_esc(o.get('buy_exchange'))} → {_esc(o.get('sell_exchange'))}</div>"
        f"<div class='opp-pnl'>"
        f"<span>Exp {_esc_fmt(o.get('expected_net_profit'), 'money')}</span>"
        f"<span class='{_pnl_class(o.get('realized_net_profit'))}'>"
        f"Real {_esc_fmt(o.get('realized_net_profit'), 'money')}</span>"
        f"</div>"
        f"<div class='opp-ts'>{_esc(_short_ts(o.get('timestamp')))}</div>"
        f"</article>"
        for o in opportunities[:12]
    ) or "<p class='empty'>No opportunities recorded</p>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
  <meta name="theme-color" content="#0b0f14"/>
  <meta http-equiv="refresh" content="5"/>
  <title>Moreney Lite</title>
  <style>{_shared_css(lite=True)}</style>
</head>
<body class="lite">
  <header class="hero hero-lite">
    <div class="hero-inner">
      <div>
        <p class="eyebrow">Mobile dashboard</p>
        <h1 class="brand">Moreney</h1>
      </div>
      <span class="badge {status_cls}">{status_label}</span>
    </div>
  </header>

  <main class="container lite-container">
    <section class="panel hero-metric {pnl_cls}">
      <span class="label">Net PnL</span>
      <span class="value-xl">{_fmt_money(perf.get('net_pnl', 0))}</span>
      <span class="sub-metric">Equity {_fmt_money(perf.get('current_equity', 0))}</span>
    </section>

    <section class="panel">
      <div class="metric-grid lite-grid">
        <article class="metric-card"><span class="label">Return</span><span class="value">{_fmt_pct(perf.get('return_pct', 0))}</span></article>
        <article class="metric-card"><span class="label">Trades</span><span class="value">{_fmt_count(perf.get('trade_count', 0))}</span></article>
        <article class="metric-card"><span class="label">Win rate</span><span class="value">{_fmt_pct(perf.get('win_rate', 0))}</span></article>
        <article class="metric-card"><span class="label">Max DD</span><span class="value">{_fmt_pct(perf.get('maximum_drawdown', 0))}</span></article>
        <article class="metric-card"><span class="label">Opportunities</span><span class="value">{_fmt_count(perf.get('total_opportunities', 0))}</span></article>
        <article class="metric-card"><span class="label">Executed</span><span class="value">{_fmt_count(perf.get('executed_opportunities', 0))}</span></article>
      </div>
    </section>

    <section class="panel">
      <div class="controls">
        <button type="button" class="btn" onclick="post('/paper/start')">Start</button>
        <button type="button" class="btn" onclick="post('/paper/stop')">Stop</button>
        <button type="button" class="btn btn-danger" onclick="resetPaper()">Reset</button>
      </div>
      <p class="foot-lite"><a href="/paper/dashboard">Full dashboard</a> · Paper only · 5s refresh</p>
    </section>

    <section class="panel">
      <h2>Recent</h2>
      <div class="opp-list">{recent}</div>
    </section>
  </main>
  <script>{_shared_js(lite=True)}</script>
</body>
</html>"""
    return HTMLResponse(content=html)


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
                <article class="fleet-card offline">
                  <header class="fleet-head">
                    <h3>{_esc(row.get('label'))}</h3>
                    <span class="badge warn">Offline</span>
                  </header>
                  <p class="error">{_esc(row.get('error'))}</p>
                  <a class="text-link" href="{_esc(row.get('dashboard_url'))}">Open instance</a>
                </article>
                """
            )
            continue
        running = row.get("running")
        status_label = "Running" if running else "Stopped"
        badge_cls = "ok" if running else "warn"
        pnl_cls = _pnl_class(row.get("net_pnl"))
        md = row.get("market_data") or {}
        md_bits = []
        for exchange, health in md.items():
            if not isinstance(health, dict):
                continue
            state = "ok" if health.get("synchronized") and not health.get("stale") else "warn"
            md_bits.append(f"<span class='feed {state}'>{_esc(exchange)}</span>")
        md_html = " ".join(md_bits) or "<span class='muted'>No feeds</span>"
        cards.append(
            f"""
            <article class="fleet-card">
              <header class="fleet-head">
                <div>
                  <h3>{_esc(row.get('label'))}</h3>
                  <p class="card-sub">Start {_esc_fmt(row.get('starting_capital'), 'money')} · cycles {_esc_fmt(row.get('cycle_count'), 'count')}</p>
                </div>
                <span class="badge {badge_cls}">{status_label}</span>
              </header>
              <div class="metric-grid compact">
                <article class="metric-card"><span class="label">Equity</span><span class="value">{_esc_fmt(row.get('equity'), 'money')}</span></article>
                <article class="metric-card {pnl_cls}"><span class="label">Net PnL</span><span class="value">{_esc_fmt(row.get('net_pnl'), 'money')}</span></article>
                <article class="metric-card"><span class="label">Trades</span><span class="value">{_esc_fmt(row.get('trade_count'), 'count')}</span></article>
                <article class="metric-card"><span class="label">Win rate</span><span class="value">{_esc_fmt(row.get('win_rate'), 'pct')}</span></article>
                <article class="metric-card"><span class="label">Passed gates</span><span class="value">{_esc_fmt(row.get('total_opportunities'), 'count')}</span></article>
                <article class="metric-card"><span class="label">Edges</span><span class="value">{_esc_fmt(row.get('depth_edges_found'), 'count')}</span></article>
              </div>
              <div class="feeds">{md_html}</div>
              <div class="card-links">
                <a href="{_esc(row.get('dashboard_url'))}">Dashboard</a>
                <a href="{_esc(row.get('dashboard_lite_url'))}">Mobile</a>
              </div>
            </article>
            """
        )

    cards_html = "\n".join(cards) or "<p class='empty'>No fleet instances configured.</p>"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
  <meta name="theme-color" content="#0b0f14"/>
  <meta http-equiv="refresh" content="5"/>
  <title>Moreney Fleet</title>
  <style>{_shared_css(fleet=True)}</style>
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      <div>
        <p class="eyebrow">Fleet overview</p>
        <h1 class="brand">Moreney Fleet</h1>
        <p class="sub">{online}/{configured} online · paper only · real orders 0</p>
      </div>
      <a class="link-lite" href="/logout">Logout</a>
    </div>
    <div class="container totals-bar">
      <article class="metric-card"><span class="label">Total equity</span><span class="value">{_esc_fmt(totals.get('equity'), 'money')}</span></article>
      <article class="metric-card {_pnl_class(totals.get('net_pnl'))}"><span class="label">Total net PnL</span><span class="value">{_esc_fmt(totals.get('net_pnl'), 'money')}</span></article>
      <article class="metric-card"><span class="label">Passed gates</span><span class="value">{_esc_fmt(totals.get('total_opportunities'), 'count')}</span></article>
      <article class="metric-card"><span class="label">Running</span><span class="value">{_esc_fmt(totals.get('running_count'), 'count')}/{configured}</span></article>
    </div>
  </header>
  <main class="container fleet-grid">{cards_html}</main>
  <footer class="footer">Paper mode only · no withdrawals · no leverage</footer>
</body>
</html>"""
    return HTMLResponse(content=html)


def _shared_css(*, lite: bool = False, fleet: bool = False) -> str:
    extra = ""
    if lite:
        extra = """
    body.lite { padding-bottom: env(safe-area-inset-bottom); }
    .lite-container { max-width: 480px; }
    .hero-lite .hero-inner { align-items: center; }
    .hero-metric { text-align: center; padding: 1.25rem 1rem; }
    .hero-metric .value-xl { font-size: 2rem; font-weight: 700; display: block; margin: .35rem 0; }
    .sub-metric { color: var(--muted); font-size: .9rem; }
    .lite-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .opp-list { display: grid; gap: .65rem; }
    .opp-card { border: 1px solid var(--line); border-radius: var(--radius-sm); padding: .75rem; background: var(--surface-2); }
    .opp-top { display: flex; justify-content: space-between; gap: .5rem; align-items: center; }
    .opp-route { color: var(--muted); font-size: .85rem; margin: .35rem 0; }
    .opp-pnl { display: flex; justify-content: space-between; gap: .5rem; font-family: var(--mono); font-size: .82rem; }
    .opp-ts { color: var(--muted); font-size: .75rem; margin-top: .35rem; }
    .foot-lite { margin: .75rem 0 0; font-size: .8rem; color: var(--muted); }
        """
    if fleet:
        extra += """
    .totals-bar { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: .65rem; padding: 0 1rem 1rem; max-width: 1100px; margin: 0 auto; }
    .fleet-grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr)); max-width: 1100px; }
    .fleet-card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 1rem; display: grid; gap: .75rem; }
    .fleet-card.offline { border-color: color-mix(in srgb, var(--bad) 40%, var(--line)); }
    .fleet-head { display: flex; justify-content: space-between; gap: .75rem; align-items: start; }
    .fleet-head h3 { margin: 0; font-size: 1.05rem; }
    .card-sub { margin: .25rem 0 0; color: var(--muted); font-size: .8rem; }
    .feeds { display: flex; flex-wrap: wrap; gap: .35rem; }
    .feed { border: 1px solid var(--line); border-radius: 999px; padding: .15rem .5rem; font-size: .68rem; text-transform: uppercase; color: var(--muted); }
    .feed.ok { color: var(--good); border-color: color-mix(in srgb, var(--good) 35%, var(--line)); }
    .feed.warn { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 35%, var(--line)); }
    .card-links { display: flex; gap: .75rem; font-size: .85rem; }
    .card-links a, .text-link { color: var(--accent); text-decoration: none; }
    .error { color: var(--bad); font-size: .85rem; margin: 0; }
    @media (min-width: 720px) { .totals-bar { grid-template-columns: repeat(4, minmax(0,1fr)); } }
        """
    return f"""
    :root {{
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
      --radius-sm: 10px;
      --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    html {{ -webkit-text-size-adjust: 100%; }}
    body {{
      margin: 0;
      font-family: var(--sans);
      background:
        radial-gradient(900px 420px at 0% -5%, rgba(77,163,255,.12), transparent 60%),
        radial-gradient(700px 380px at 100% 0%, rgba(52,211,153,.08), transparent 55%),
        var(--bg);
      color: var(--text);
      min-height: 100vh;
      line-height: 1.45;
    }}
    a {{ color: var(--accent); }}
    .hero {{
      border-bottom: 1px solid var(--line);
      padding: calc(1rem + env(safe-area-inset-top)) 1rem 1rem;
      background: color-mix(in srgb, var(--surface) 70%, transparent);
      backdrop-filter: blur(8px);
    }}
    .hero-inner {{
      max-width: 1100px;
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      align-items: flex-start;
      flex-wrap: wrap;
    }}
    .eyebrow {{
      margin: 0;
      font-size: .72rem;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .brand {{
      margin: .15rem 0 0;
      font-size: clamp(1.6rem, 5vw, 2.35rem);
      font-weight: 700;
      letter-spacing: -0.03em;
    }}
    .sub {{ margin: .35rem 0 0; color: var(--muted); font-size: .9rem; max-width: 42ch; }}
    .hero-badges {{ display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: .28rem .65rem;
      font-size: .72rem;
      letter-spacing: .06em;
      text-transform: uppercase;
      white-space: nowrap;
    }}
    .badge.ok {{ color: var(--good); border-color: color-mix(in srgb, var(--good) 35%, var(--line)); }}
    .badge.warn {{ color: var(--warn); border-color: color-mix(in srgb, var(--warn) 35%, var(--line)); }}
    .badge.muted {{ color: var(--muted); }}
    .container {{ max-width: 1100px; margin: 0 auto; padding: 1rem; display: grid; gap: .85rem; }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 1rem;
    }}
    .panel.highlight {{
      background: linear-gradient(180deg, color-mix(in srgb, var(--surface-2) 90%, #1a2940), var(--surface));
    }}
    .panel-head {{ display: flex; justify-content: space-between; align-items: center; gap: .5rem; margin-bottom: .5rem; }}
    .panel h2 {{
      margin: 0 0 .85rem;
      font-size: .72rem;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: var(--muted);
      font-weight: 600;
    }}
    .panel-head h2 {{ margin-bottom: 0; }}
    .link-lite {{ font-size: .82rem; color: var(--accent); text-decoration: none; white-space: nowrap; }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(100%, 140px), 1fr));
      gap: .65rem;
    }}
    .metric-grid.compact {{ grid-template-columns: repeat(auto-fit, minmax(min(100%, 120px), 1fr)); }}
    .metric-card {{
      background: var(--surface-2);
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      padding: .7rem .75rem;
      min-width: 0;
    }}
    .metric-card.positive .value {{ color: var(--good); }}
    .metric-card.negative .value {{ color: var(--bad); }}
    .metric-card .label {{
      display: block;
      color: var(--muted);
      font-size: .68rem;
      letter-spacing: .06em;
      text-transform: uppercase;
    }}
    .metric-card .value {{
      display: block;
      margin-top: .25rem;
      font-family: var(--mono);
      font-size: clamp(.95rem, 2.8vw, 1.15rem);
      font-weight: 600;
      font-variant-numeric: tabular-nums;
      word-break: break-word;
    }}
    .controls {{ display: flex; flex-wrap: wrap; gap: .5rem; }}
    .btn {{
      appearance: none;
      border: 1px solid var(--line);
      background: var(--surface-2);
      color: var(--text);
      border-radius: 999px;
      padding: .62rem 1rem;
      min-height: 44px;
      font: inherit;
      font-size: .9rem;
      cursor: pointer;
      touch-action: manipulation;
    }}
    .btn:active {{ transform: scale(.98); }}
    .btn-danger {{ border-color: color-mix(in srgb, var(--bad) 45%, var(--line)); color: #fecaca; }}
    .table-wrap {{
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      margin: 0 -.25rem;
      padding: 0 .25rem;
    }}
    table {{ width: 100%; min-width: 520px; border-collapse: collapse; font-size: .82rem; }}
    th, td {{ text-align: left; padding: .55rem .4rem; border-bottom: 1px solid var(--line); vertical-align: top; }}
    th {{
      color: var(--muted);
      font-size: .68rem;
      letter-spacing: .08em;
      text-transform: uppercase;
      font-weight: 600;
      white-space: nowrap;
    }}
    td.num, th {{ font-family: var(--mono); font-variant-numeric: tabular-nums; }}
    td.num {{ white-space: nowrap; }}
    td.ts {{ color: var(--muted); font-size: .75rem; white-space: nowrap; }}
    td.positive {{ color: var(--good); }}
    td.negative {{ color: var(--bad); }}
    .pill {{
      display: inline-block;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: .12rem .45rem;
      font-size: .68rem;
      text-transform: uppercase;
      letter-spacing: .04em;
      color: var(--muted);
      white-space: nowrap;
    }}
    .empty {{ color: var(--muted); text-align: center; padding: .75rem 0; }}
    .footer {{
      text-align: center;
      color: var(--muted);
      font-size: .75rem;
      padding: 1rem 1rem calc(1rem + env(safe-area-inset-bottom));
    }}
    .muted {{ color: var(--muted); }}
    @media (max-width: 640px) {{
      .container {{ padding: .75rem; gap: .7rem; }}
      .panel {{ padding: .85rem; }}
      .hero-inner {{ align-items: stretch; }}
      table {{ min-width: 640px; font-size: .78rem; }}
    }}
    {extra}
    """


def _shared_js(*, lite: bool = False) -> str:
    confirm_msg = (
        "Reset paper portfolio?"
        if lite
        else "Reset paper portfolio to starting capital? This never affects real exchange accounts."
    )
    return f"""
    async function post(url, body) {{
      await fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(body || {{}})
      }});
      location.reload();
    }}
    async function resetPaper() {{
      if (!confirm({confirm_msg!r})) return;
      await post('/paper/reset', {{confirm: true}});
    }}
    """


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _fmt_money(value: Any) -> str:
    d = _to_decimal(value)
    if d is None:
        return "—"
    q = d.quantize(_TWO, rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    q = abs(q)
    return f"{sign}{q:,.2f}"


def _fmt_count(value: Any) -> str:
    d = _to_decimal(value)
    if d is None:
        return "—"
    return f"{int(d):,}"


def _fmt_pct(value: Any) -> str:
    d = _to_decimal(value)
    if d is None:
        return "—"
    if abs(d) <= 1:
        d *= _HUNDRED
    q = d.quantize(_TWO, rounding=ROUND_HALF_UP)
    return f"{q:,.2f}%"


def _fmt_ratio(value: Any) -> str:
    d = _to_decimal(value)
    if d is None:
        return "—"
    q = d.quantize(_TWO, rounding=ROUND_HALF_UP)
    return f"{q:,.2f}"


def _fmt(value: Any) -> str:
    """Legacy helper — money-style 2 decimal places."""
    return _fmt_money(value)


def _esc_fmt(value: Any, kind: str = "money") -> str:
    if kind == "count":
        text = _fmt_count(value)
    elif kind == "pct":
        text = _fmt_pct(value)
    elif kind == "ratio":
        text = _fmt_ratio(value)
    else:
        text = _fmt_money(value)
    return _esc(text)


def _pnl_class(value: Any) -> str:
    d = _to_decimal(value)
    if d is None or d == 0:
        return ""
    return "positive" if d > 0 else "negative"


def _short_ts(value: Any) -> str:
    if value is None:
        return "—"
    text = str(value)
    if "T" in text:
        text = text.replace("T", " ")
    if "+" in text:
        text = text.split("+", 1)[0]
    if text.endswith("Z"):
        text = text[:-1]
    if "." in text:
        text = text.split(".", 1)[0]
    return text[-16:] if len(text) > 16 else text


def _esc(value: Any) -> str:
    text = "—" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

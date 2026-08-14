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
    hourly = payload.get("hourly") or []
    trades = payload.get("trades") or []

    def m(key: str, default: str = "0") -> str:
        return _fmt_money(perf.get(key, default))

    def c(key: str, default: str = "0") -> str:
        return _fmt_count(perf.get(key, default))

    def p(key: str, default: str = "0") -> str:
        return _fmt_pct(perf.get(key, default))

    strategy_rows = "".join(
        f"<tr>"
        f"<td>{_esc(_strategy_label(s.get('strategy')))}</td>"
        f"<td class='num {_pnl_class(s.get('net_pnl'))}'>{_esc_fmt(s.get('net_pnl'), 'money')}</td>"
        f"<td class='num'>{_esc_fmt(s.get('opportunities'), 'count')}</td>"
        f"<td class='num'>{_esc_fmt(s.get('trades'), 'count')}</td>"
        f"<td class='num'>{_esc_fmt(s.get('win_rate'), 'pct')}</td>"
        f"</tr>"
        for s in strategies
    ) or "<tr><td colspan='5' class='empty'>Nog geen resultaten</td></tr>"

    pair_rows = "".join(
        f"<tr>"
        f"<td>{_esc(_venue_label(p.get('buy_exchange')))} → {_esc(_venue_label(p.get('sell_exchange')))}</td>"
        f"<td class='num {_pnl_class(p.get('net_pnl'))}'>{_esc_fmt(p.get('net_pnl'), 'money')}</td>"
        f"<td class='num'>{_esc_fmt(p.get('trades'), 'count')}</td>"
        f"<td class='num'>{_esc_fmt(p.get('win_rate'), 'pct')}</td>"
        f"</tr>"
        for p in pairs
    ) or "<tr><td colspan='4' class='empty'>Nog geen beursresultaten</td></tr>"

    opp_rows = "".join(
        f"<tr>"
        f"<td class='ts'>{_esc(_short_ts(o.get('timestamp')))}</td>"
        f"<td><strong>{_esc(o.get('symbol'))}</strong></td>"
        f"<td>{_esc(_venue_label(o.get('buy_exchange')))}</td>"
        f"<td>{_esc(_venue_label(o.get('sell_exchange')))}</td>"
        f"<td class='num'>{_esc_fmt(o.get('expected_net_profit'), 'money')}</td>"
        f"<td class='num {_pnl_class(o.get('realized_net_profit'))}'>"
        f"{_esc_fmt(o.get('realized_net_profit'), 'money')}</td>"
        f"<td><span class='pill'>{_esc(_status_label(o.get('status')))}</span></td>"
        f"</tr>"
        for o in opportunities[:25]
    ) or "<tr><td colspan='7' class='empty'>Nog geen transacties</td></tr>"

    running = status.get("running")
    status_label = "Actief" if running else "Gestopt"
    status_cls = "ok" if running else "warn"
    net_pnl = perf.get("net_pnl", 0)
    pnl_cls = _pnl_class(net_pnl)
    pnl_word = _pnl_word(net_pnl)
    wins = perf.get("winning_trades", 0)
    losses = perf.get("losing_trades", 0)
    forecast = status.get("live_forecast") or {}
    forecast_hour = (
        _fmt_money(forecast.get("projected_per_hour_eur", 0))
        if forecast.get("projection_ready", True)
        else "n.v.t."
    )
    forecast_day = (
        _fmt_money(forecast.get("projected_per_day_eur", 0))
        if forecast.get("projection_ready", True)
        else "n.v.t."
    )
    forecast_note = _esc(forecast.get("note") or "Live-conservatief model.")
    confidence = str(forecast.get("confidence") or "very_low")
    confidence_label = {
        "very_low": "Nog onzeker",
        "low": "Voorzichtig",
        "medium": "Redelijk",
        "high": "Stabiel",
    }.get(confidence, confidence)
    assumptions = forecast.get("assumptions") or []
    assumption_lis = "".join(f"<li>{_esc(a)}</li>" for a in assumptions) or (
        "<li>Trade-through maker fills, fees, stale-edge caps</li>"
    )

    markout = status.get("markout") or {}
    desk_scan = status.get("desk_scan") or {}
    maker_scan = desk_scan.get("maker") or {}
    tri_scan = desk_scan.get("triangle") or {}
    inventory = status.get("inventory") or {}
    desk_strategy = _esc(str(status.get("strategy") or "—"))
    desk_tier = _esc(str(status.get("fee_tier") or "retail"))
    realism_note = (
        "Realistic-profiel: retail fees, 20% trade-through, fair-value aan. Dichtst bij echt geld."
        if str(status.get("fee_tier") or "retail").lower() == "retail"
        else "Ultra-profiel: rebate fees en hoge fill-%. Niet gelijk aan retail live-P&amp;L."
    )
    markout_5s = f"{_esc(str(markout.get('avg_adverse_bps_5s') or '0'))} bps"
    markout_suggested = f"{_esc(str(markout.get('suggested_adverse_bps') or '0'))} bps"
    tri_pairs = _fmt_count(tri_scan.get("pairs_evaluated", 0))
    tri_emits = _fmt_count(tri_scan.get("opportunities_emitted", 0))
    maker_emits = _fmt_count(maker_scan.get("opportunities_emitted", 0))
    fx_refills = _fmt_count(inventory.get("fx_refilled", 0))
    seeded_assets = _esc(", ".join(inventory.get("seeded_assets") or []) or "—")
    inventory_rows = _inventory_rows(inventory.get("venues") or {})

    global_engine = status.get("global_engine") or {}
    ge_enabled = global_engine.get("enabled")
    ge_regime = _esc(str(global_engine.get("regime") or "—"))
    ge_sessions = _esc(", ".join(global_engine.get("active_sessions") or []) or "—")
    ge_exposure = global_engine.get("portfolio_exposure") or {}
    ge_venue_exp = _esc(_exposure_summary(ge_exposure.get("venue")))
    ge_strategy_exp = _esc(_exposure_summary(ge_exposure.get("strategy")))
    ge_corr_exp = _esc(_exposure_summary(ge_exposure.get("correlation")))
    ge_decision_rows = _global_decision_rows(global_engine.get("recent_decisions") or [])
    ge_ranking_block = _global_ranking_block(global_engine.get("last_ranking"))

    profit_chart = _svg_cumulative_profit(trades)
    hourly_chart = _svg_hourly_bars(hourly)
    winloss_chart = _svg_win_loss(wins, losses)

    html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
  <meta name="theme-color" content="#0b0f14"/>
  <meta http-equiv="refresh" content="5"/>
  <title>Moreney — Winst en verlies</title>
  <style>{_shared_css()}</style>
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      <div>
        <p class="eyebrow">Oefenhandel · live-inschatting</p>
        <h1 class="brand">Moreney</h1>
        <p class="sub">{realism_note}</p>
      </div>
      <div class="hero-badges">
        <span class="badge {status_cls}">{status_label}</span>
        <span class="badge muted">Geen echte orders</span>
      </div>
    </div>
  </header>

  <main class="container">
    <section class="panel">
      <div class="panel-head">
        <h2>Bediening</h2>
        <a class="link-lite" href="/paper/dashboard-lite">Mobiel</a>
      </div>
      <div class="controls">
        <button type="button" class="btn" onclick="post('/paper/start')">Start</button>
        <button type="button" class="btn" onclick="post('/paper/stop')">Stop</button>
        <button type="button" class="btn btn-danger" onclick="resetPaper()">Opnieuw beginnen</button>
      </div>
    </section>

    <section class="panel highlight">
      <h2>Live-inschatting (echt geld)</h2>
      <div class="metric-grid">
        <article class="metric-card {pnl_cls}">
          <span class="label">{pnl_word} haalbaar met echt geld</span>
          <span class="value">{m('net_pnl')}</span>
        </article>
        <article class="metric-card">
          <span class="label">Tempo / uur</span>
          <span class="value">{forecast_hour}</span>
        </article>
        <article class="metric-card">
          <span class="label">Voorspelling / dag</span>
          <span class="value">{forecast_day}</span>
        </article>
        <article class="metric-card">
          <span class="label">Zekerheid</span>
          <span class="value">{confidence_label}</span>
        </article>
      </div>
      <p class="forecast-note">{forecast_note}</p>
      <ul class="forecast-assumptions">{assumption_lis}</ul>
    </section>

    <section class="panel">
      <h2>Desk-monitor</h2>
      <div class="metric-grid compact">
        <article class="metric-card"><span class="label">Strategie</span><span class="value">{desk_strategy}</span></article>
        <article class="metric-card"><span class="label">Fee-tier</span><span class="value">{desk_tier}</span></article>
        <article class="metric-card"><span class="label">Markout 5s</span><span class="value">{markout_5s}</span></article>
        <article class="metric-card"><span class="label">Adverse (gate)</span><span class="value">{markout_suggested}</span></article>
        <article class="metric-card"><span class="label">Triangle gescand</span><span class="value">{tri_pairs}</span></article>
        <article class="metric-card"><span class="label">Triangle emits</span><span class="value">{tri_emits}</span></article>
        <article class="metric-card"><span class="label">Maker emits</span><span class="value">{maker_emits}</span></article>
        <article class="metric-card"><span class="label">FX refills</span><span class="value">{fx_refills}</span></article>
      </div>
      <div class="table-wrap" style="margin-top:1rem">
        <table>
          <thead><tr><th>Venue</th><th>EUR</th><th>USDT</th><th>Overig</th></tr></thead>
          <tbody>{inventory_rows}</tbody>
        </table>
      </div>
      <p class="forecast-note">Seeded: {seeded_assets}</p>
    </section>

    <section class="panel">
      <h2>Global engine</h2>
      <div class="metric-grid compact">
        <article class="metric-card"><span class="label">Engine</span><span class="value">{"Aan" if ge_enabled else "Uit"}</span></article>
        <article class="metric-card"><span class="label">Regime</span><span class="value">{ge_regime}</span></article>
        <article class="metric-card"><span class="label">Actieve sessies</span><span class="value">{ge_sessions}</span></article>
        <article class="metric-card"><span class="label">Venue exposure</span><span class="value">{ge_venue_exp}</span></article>
        <article class="metric-card"><span class="label">Strategie exposure</span><span class="value">{ge_strategy_exp}</span></article>
        <article class="metric-card"><span class="label">Correlatie-groepen</span><span class="value">{ge_corr_exp}</span></article>
      </div>
      <div class="table-wrap" style="margin-top:1rem">
        <table>
          <thead><tr><th>Tijd</th><th>Actie</th><th>Strategie</th><th>Symbool</th><th>EV</th><th>Score</th><th>Stadium</th><th>Reden</th></tr></thead>
          <tbody>{ge_decision_rows}</tbody>
        </table>
      </div>
      <p class="forecast-note">Laatste beslissingen uit de EV-ranker. Volledige log: <a class="link-lite" href="/paper/opportunity-decisions">/paper/opportunity-decisions</a></p>
      {ge_ranking_block}
    </section>

    <section class="panel">
      <h2>Geld</h2>
      <div class="metric-grid">
        <article class="metric-card">
          <span class="label">Startkapitaal</span>
          <span class="value">{m('starting_equity')}</span>
        </article>
        <article class="metric-card {pnl_cls}">
          <span class="label">{pnl_word} (live-equivalent)</span>
          <span class="value">{m('net_pnl')}</span>
        </article>
        <article class="metric-card">
          <span class="label">Rendement</span>
          <span class="value">{p('return_pct')}</span>
        </article>
        <article class="metric-card">
          <span class="label">Oefenvermogen (incl. voorraad)</span>
          <span class="value">{m('current_equity')}</span>
        </article>
        <article class="metric-card">
          <span class="label">Paper-verschil (niet live)</span>
          <span class="value">{m('paper_equity_pnl')}</span>
        </article>
        <article class="metric-card">
          <span class="label">Grootste terugval</span>
          <span class="value">{p('maximum_drawdown')}</span>
        </article>
      </div>
    </section>

    <section class="panel">
      <h2>Grafieken</h2>
      <div class="charts">
        <figure class="chart-card">
          <figcaption>Winst in de tijd</figcaption>
          {profit_chart}
        </figure>
        <figure class="chart-card">
          <figcaption>Winst per uur</figcaption>
          {hourly_chart}
        </figure>
        <figure class="chart-card">
          <figcaption>Winst vs verlies</figcaption>
          {winloss_chart}
        </figure>
      </div>
    </section>

    <section class="panel">
      <h2>Activiteit</h2>
      <div class="metric-grid compact">
        <article class="metric-card"><span class="label">Markten bekeken</span><span class="value">{c('pairs_evaluated')}</span></article>
        <article class="metric-card"><span class="label">Koopkansen</span><span class="value">{c('depth_edges_found')}</span></article>
        <article class="metric-card"><span class="label">Afgewezen</span><span class="value">{c('scan_rejections')}</span></article>
        <article class="metric-card"><span class="label">Goedgekeurd</span><span class="value">{c('approved_opportunities')}</span></article>
        <article class="metric-card"><span class="label">Uitgevoerd</span><span class="value">{c('executed_opportunities')}</span></article>
        <article class="metric-card"><span class="label">Transacties</span><span class="value">{c('trade_count')}</span></article>
        <article class="metric-card"><span class="label">Winstkans</span><span class="value">{p('win_rate')}</span></article>
        <article class="metric-card"><span class="label">Winstende deals</span><span class="value">{c('winning_trades')}</span></article>
        <article class="metric-card"><span class="label">Verliezende deals</span><span class="value">{c('losing_trades')}</span></article>
      </div>
    </section>

    <section class="panel">
      <h2>Kosten</h2>
      <div class="metric-grid compact">
        <article class="metric-card"><span class="label">Beurskosten</span><span class="value">{m('fees')}</span></article>
        <article class="metric-card"><span class="label">Prijsverschil</span><span class="value">{m('slippage')}</span></article>
        <article class="metric-card"><span class="label">Omzet</span><span class="value">{m('trading_volume')}</span></article>
      </div>
    </section>

    <section class="panel">
      <h2>Per aanpak</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Aanpak</th><th>Winst</th><th>Kansen</th><th>Transacties</th><th>Winstkans</th></tr></thead>
          <tbody>{strategy_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Per beurs</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Route</th><th>Winst</th><th>Transacties</th><th>Winstkans</th></tr></thead>
          <tbody>{pair_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Recente transacties</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Tijd</th><th>Munt</th><th>Koop</th><th>Verkoop</th>
              <th>Verwacht</th><th>Echte winst</th><th>Uitslag</th>
            </tr>
          </thead>
          <tbody>{opp_rows}</tbody>
        </table>
      </div>
    </section>
  </main>

  <footer class="footer">
    Oefenhandel · geen opnames · geen hefboom · vernieuwt elke 5s ·
    <a href="/logout">Uitloggen</a>
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
    hourly = payload.get("hourly") or []
    trades = payload.get("trades") or []

    running = status.get("running")
    status_label = "Actief" if running else "Gestopt"
    status_cls = "ok" if running else "warn"
    pnl_cls = _pnl_class(perf.get("net_pnl", 0))
    pnl_word = _pnl_word(perf.get("net_pnl", 0))

    forecast = status.get("live_forecast") or {}
    forecast_day = (
        _fmt_money(forecast.get("projected_per_day_eur", 0))
        if forecast.get("projection_ready", True)
        else "n.v.t."
    )
    confidence = str(forecast.get("confidence") or "very_low")
    confidence_label = {
        "very_low": "Nog onzeker",
        "low": "Voorzichtig",
        "medium": "Redelijk",
        "high": "Stabiel",
    }.get(confidence, confidence)

    recent = "".join(
        f"<article class='opp-card'>"
        f"<div class='opp-top'>"
        f"<strong>{_esc(o.get('symbol'))}</strong>"
        f"<span class='pill'>{_esc(_status_label(o.get('status')))}</span>"
        f"</div>"
        f"<div class='opp-route'>{_esc(_venue_label(o.get('buy_exchange')))} → {_esc(_venue_label(o.get('sell_exchange')))}</div>"
        f"<div class='opp-pnl'>"
        f"<span>Verwacht {_esc_fmt(o.get('expected_net_profit'), 'money')}</span>"
        f"<span class='{_pnl_class(o.get('realized_net_profit'))}'>"
        f"{_status_label(o.get('status'))} {_esc_fmt(o.get('realized_net_profit'), 'money')}</span>"
        f"</div>"
        f"<div class='opp-ts'>{_esc(_short_ts(o.get('timestamp')))}</div>"
        f"</article>"
        for o in opportunities[:12]
    ) or "<p class='empty'>Nog geen transacties</p>"

    html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
  <meta name="theme-color" content="#0b0f14"/>
  <meta http-equiv="refresh" content="5"/>
  <title>Moreney — Winst</title>
  <style>{_shared_css(lite=True)}</style>
</head>
<body class="lite">
  <header class="hero hero-lite">
    <div class="hero-inner">
      <div>
        <p class="eyebrow">Mobiel · live-inschatting</p>
        <h1 class="brand">Moreney</h1>
      </div>
      <span class="badge {status_cls}">{status_label}</span>
    </div>
  </header>

  <main class="container lite-container">
    <section class="panel hero-metric {pnl_cls}">
      <span class="label">{pnl_word} haalbaar met echt geld</span>
      <span class="value-xl">{_fmt_money(perf.get('net_pnl', 0))}</span>
      <span class="sub-metric">Voorspelling / dag {forecast_day} · {confidence_label}</span>
      <span class="sub-metric">Oefenvermogen {_fmt_money(perf.get('current_equity', 0))}</span>
    </section>

    <section class="panel">
      <h2>Winst in de tijd</h2>
      {_svg_cumulative_profit(trades)}
    </section>

    <section class="panel">
      <div class="metric-grid lite-grid">
        <article class="metric-card"><span class="label">Rendement</span><span class="value">{_fmt_pct(perf.get('return_pct', 0))}</span></article>
        <article class="metric-card"><span class="label">Transacties</span><span class="value">{_fmt_count(perf.get('trade_count', 0))}</span></article>
        <article class="metric-card"><span class="label">Winstkans</span><span class="value">{_fmt_pct(perf.get('win_rate', 0))}</span></article>
        <article class="metric-card"><span class="label">Terugval</span><span class="value">{_fmt_pct(perf.get('maximum_drawdown', 0))}</span></article>
        <article class="metric-card"><span class="label">Winstende deals</span><span class="value">{_fmt_count(perf.get('winning_trades', 0))}</span></article>
        <article class="metric-card"><span class="label">Verliezende deals</span><span class="value">{_fmt_count(perf.get('losing_trades', 0))}</span></article>
      </div>
    </section>

    <section class="panel">
      <h2>Winst per uur</h2>
      {_svg_hourly_bars(hourly)}
    </section>

    <section class="panel">
      <div class="controls">
        <button type="button" class="btn" onclick="post('/paper/start')">Start</button>
        <button type="button" class="btn" onclick="post('/paper/stop')">Stop</button>
        <button type="button" class="btn btn-danger" onclick="resetPaper()">Opnieuw</button>
      </div>
      <p class="foot-lite"><a href="/paper/dashboard">Volledig overzicht</a> · Oefenhandel · 5s</p>
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

    groups = _group_fleet_instances(instances)
    groups_html = "".join(_render_capital_group(capital, pair) for capital, pair in groups)
    if not groups_html:
        groups_html = "<p class='empty'>Geen rekeningen ingesteld.</p>"

    fleet_chart = _svg_fleet_bars(instances)
    html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
  <meta name="theme-color" content="#0b0f14"/>
  <meta http-equiv="refresh" content="5"/>
  <title>Moreney — Alle rekeningen</title>
  <style>{_shared_css(fleet=True)}</style>
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      <div>
        <p class="eyebrow">Oefenhandel · per inleg</p>
        <h1 class="brand">Moreney</h1>
        <p class="sub">{online}/{configured} online · Ultra = optimistisch · Realistic = retail / echt-geld-achtig</p>
      </div>
      <a class="link-lite" href="/logout">Uitloggen</a>
    </div>
    <div class="container totals-bar">
      <article class="metric-card"><span class="label">Ultra transacties</span><span class="value">{_esc_fmt(totals.get('ultra_trades', totals.get('trade_count')), 'count')}</span></article>
      <article class="metric-card {_pnl_class(totals.get('ultra_pnl'))}"><span class="label">Ultra PnL</span><span class="value">{_esc_fmt(totals.get('ultra_pnl', totals.get('net_pnl')), 'money')}</span></article>
      <article class="metric-card"><span class="label">Realistic transacties</span><span class="value">{_esc_fmt(totals.get('realistic_trades', '0'), 'count')}</span></article>
      <article class="metric-card {_pnl_class(totals.get('realistic_pnl'))}"><span class="label">Realistic PnL</span><span class="value">{_esc_fmt(totals.get('realistic_pnl', '0'), 'money')}</span></article>
    </div>
  </header>
  <main class="container">
    {groups_html}
    <section class="panel">
      <h2>Winst per rekening</h2>
      {fleet_chart}
    </section>
  </main>
  <footer class="footer">Oefenhandel · geen echte orders · Realistic = retail fees</footer>
</body>
</html>"""
    return HTMLResponse(content=html)


def _fleet_profile(row: dict[str, Any]) -> str:
    label = str(row.get("label") or "").lower()
    if "realistic" in label or " live" in f" {label}":
        return "realistic"
    if "ultra" in label:
        return "ultra"
    if str(row.get("fee_tier") or "").lower() == "retail":
        return "realistic"
    return "ultra"


def _group_fleet_instances(instances: list[dict[str, Any]]) -> list[tuple[str, dict[str, dict[str, Any]]]]:
    """Group ultra/realistic cards by starting capital, preserving URL order."""
    ordered: list[str] = []
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in instances:
        capital = str(row.get("starting_capital") or row.get("label") or "?")
        key = capital
        if key not in grouped:
            ordered.append(key)
            grouped[key] = {}
        grouped[key][_fleet_profile(row)] = row
        if not row.get("ok") and "offline" not in grouped[key]:
            grouped[key]["offline"] = row
    return [(k, grouped[k]) for k in ordered]


def _render_capital_group(capital: str, pair: dict[str, dict[str, Any]]) -> str:
    cards = []
    for kind in ("ultra", "realistic"):
        row = pair.get(kind)
        if row is None:
            continue
        cards.append(_render_fleet_card(row, kind=kind))
    extra = [k for k in pair if k not in {"ultra", "realistic"}]
    for k in extra:
        cards.append(_render_fleet_card(pair[k], kind="other"))
    if not cards:
        return ""
    return f"""
    <section class="capital-group">
      <h2>Inleg {_esc_fmt(capital, 'money')}</h2>
      <div class="pair-grid">{''.join(cards)}</div>
    </section>
    """


def _render_fleet_card(row: dict[str, Any], *, kind: str) -> str:
    if not row.get("ok"):
        return f"""
        <article class="fleet-card offline">
          <header class="fleet-head">
            <h3>{_esc(row.get('label'))}</h3>
            <span class="badge warn">Offline</span>
          </header>
          <p class="error">{_esc(row.get('error'))}</p>
        </article>
        """
    running = row.get("running")
    status_label = "Actief" if running else "Gestopt"
    badge_cls = "ok" if running else "warn"
    profile = "Realistic" if kind == "realistic" else ("Ultra" if kind == "ultra" else _esc(row.get("label")))
    profile_cls = "realistic" if kind == "realistic" else "ultra"
    trades = _esc_fmt(row.get("trade_count") if row.get("trade_count") is not None else 0, "count")
    executed = _esc_fmt(
        row.get("executed_opportunities") if row.get("executed_opportunities") is not None else 0,
        "count",
    )
    scanned = _esc_fmt(row.get("pairs_evaluated") or 0, "count")
    return f"""
    <article class="fleet-card {profile_cls}">
      <header class="fleet-head">
        <div>
          <span class="profile-pill {profile_cls}">{profile}</span>
          <p class="card-sub">Fee {_esc(row.get('fee_tier') or '—')}</p>
        </div>
        <span class="badge {badge_cls}">{status_label}</span>
      </header>
      <div class="metric-grid compact">
        <article class="metric-card"><span class="label">Vermogen</span><span class="value">{_esc_fmt(row.get('equity'), 'money')}</span></article>
        <article class="metric-card {_pnl_class(row.get('net_pnl'))}"><span class="label">{_pnl_word(row.get('net_pnl'))}</span><span class="value">{_esc_fmt(row.get('net_pnl'), 'money')}</span></article>
        <article class="metric-card highlight-metric"><span class="label">Transacties</span><span class="value">{trades}</span></article>
        <article class="metric-card"><span class="label">Uitgevoerd</span><span class="value">{executed}</span></article>
        <article class="metric-card"><span class="label">Gescand</span><span class="value">{scanned}</span></article>
        <article class="metric-card"><span class="label">Winstkans</span><span class="value">{_esc_fmt(row.get('win_rate'), 'pct')}</span></article>
      </div>
      <div class="card-links">
        <a href="{_esc(row.get('dashboard_url'))}">Overzicht</a>
        <a href="{_esc(row.get('dashboard_lite_url'))}">Mobiel</a>
      </div>
    </article>
    """


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
    .capital-group { max-width: 1100px; margin: 0 auto 1.25rem; }
    .capital-group h2 { margin: 0 0 .65rem; font-size: 1.05rem; color: var(--muted); font-weight: 600; }
    .pair-grid { display: grid; gap: 1rem; grid-template-columns: 1fr; }
    .fleet-card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 1rem; display: grid; gap: .75rem; }
    .fleet-card.ultra { border-color: color-mix(in srgb, var(--accent) 35%, var(--line)); }
    .fleet-card.realistic { border-color: color-mix(in srgb, var(--good) 35%, var(--line)); }
    .fleet-card.offline { border-color: color-mix(in srgb, var(--bad) 40%, var(--line)); }
    .fleet-head { display: flex; justify-content: space-between; gap: .75rem; align-items: start; }
    .profile-pill { display: inline-block; font-size: .75rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; padding: .2rem .55rem; border-radius: 999px; }
    .profile-pill.ultra { background: color-mix(in srgb, var(--accent) 18%, transparent); color: var(--accent); }
    .profile-pill.realistic { background: color-mix(in srgb, var(--good) 18%, transparent); color: var(--good); }
    .card-sub { margin: .25rem 0 0; color: var(--muted); font-size: .8rem; }
    .highlight-metric .value { font-size: 1.15rem; }
    .card-links { display: flex; gap: .75rem; font-size: .85rem; }
    .card-links a, .text-link { color: var(--accent); text-decoration: none; }
    .error { color: var(--bad); font-size: .85rem; margin: 0; }
    @media (min-width: 720px) {
      .totals-bar { grid-template-columns: repeat(4, minmax(0,1fr)); }
      .pair-grid { grid-template-columns: 1fr 1fr; }
    }
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
    .forecast-note {{ margin: .85rem 0 .35rem; color: var(--muted); font-size: .9rem; }}
    .forecast-assumptions {{
      margin: 0; padding-left: 1.1rem; color: var(--muted); font-size: .82rem; line-height: 1.45;
    }}
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
    .charts {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: .85rem;
    }}
    .chart-card {{
      margin: 0;
      background: var(--surface-2);
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      padding: .85rem;
      min-width: 0;
    }}
    .chart-card figcaption {{
      margin: 0 0 .65rem;
      font-size: .72rem;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: var(--muted);
      font-weight: 600;
    }}
    .chart-card svg,
    .panel svg {{
      width: 100%;
      height: auto;
      display: block;
    }}
    .chart-empty {{
      color: var(--muted);
      text-align: center;
      padding: 1.4rem .5rem;
      font-size: .9rem;
      margin: 0;
    }}
    @media (max-width: 720px) {{
      .charts {{ grid-template-columns: 1fr; }}
    }}
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
        "Oefenrekening opnieuw beginnen?"
        if lite
        else "Oefenrekening terugzetten naar startkapitaal? Dit raakt geen echte beursrekeningen."
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


def _exposure_summary(exposure: Any) -> str:
    if not exposure or not isinstance(exposure, dict):
        return "—"
    parts: list[str] = []
    for key, value in sorted(exposure.items()):
        if value in (None, 0, "0", "0.0"):
            continue
        parts.append(f"{key}={value}")
    return ", ".join(parts[:4]) or "—"


def _global_ranking_block(ranking: Any) -> str:
    if not ranking or not isinstance(ranking, dict):
        return ""
    top = ranking.get("top") or []
    if not top:
        return (
            '<p class="forecast-note">Laatste rank-batch: '
            f'{_esc(ranking.get("input_candidates", 0))} kandidaten, '
            f'{_esc(ranking.get("scored", 0))} gescoord.</p>'
        )
    rows = "".join(
        "<tr>"
        f"<td class='num'>{_esc(item.get('rank'))}</td>"
        f"<td><strong>{_esc(item.get('symbol'))}</strong></td>"
        f"<td>{_esc(_strategy_label(item.get('strategy')))}</td>"
        f"<td class='num'>{_esc_fmt(item.get('expected_value'), 'money')}</td>"
        f"<td class='num'>{_esc_fmt(item.get('score'), 'money')}</td>"
        f"<td class='num'>{_esc_fmt(item.get('net_profit_usd'), 'money')}</td>"
        "</tr>"
        for item in top
    )
    return f"""
      <div class="table-wrap" style="margin-top:1rem">
        <h3 style="font-size:0.95rem;margin:0 0 0.5rem">Laatste EV-rank (top {len(top)})</h3>
        <table>
          <thead><tr><th>#</th><th>Symbool</th><th>Strategie</th><th>EV</th><th>Score</th><th>Net</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
        <p class="forecast-note">Batch: {_esc(ranking.get('input_candidates', 0))} in → {_esc(ranking.get('approved', 0))} goedgekeurd → {_esc(ranking.get('ranked_for_execution', 0))} uitvoerbaar</p>
      </div>
    """


def _global_decision_rows(decisions: list[Any]) -> str:
    if not decisions:
        return "<tr><td colspan='8' class='empty'>Nog geen engine-beslissingen</td></tr>"
    rows: list[str] = []
    for d in reversed(decisions[-10:]):
        if not isinstance(d, dict):
            continue
        action = str(d.get("action") or "—")
        action_cls = "ok" if action in {"approve", "execute", "approved"} else "warn"
        rows.append(
            "<tr>"
            f"<td class='ts'>{_esc(_short_ts(d.get('timestamp')))}</td>"
            f"<td><span class='pill {action_cls}'>{_esc(action)}</span></td>"
            f"<td>{_esc(_strategy_label(d.get('strategy')))}</td>"
            f"<td><strong>{_esc(d.get('symbol'))}</strong></td>"
            f"<td class='num'>{_esc_fmt(d.get('expected_value'), 'money')}</td>"
            f"<td class='num'>{_esc_fmt(d.get('score'), 'money')}</td>"
            f"<td>{_esc(d.get('stage'))}</td>"
            f"<td>{_esc((d.get('reason') or '')[:80])}</td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan='8' class='empty'>Nog geen engine-beslissingen</td></tr>"


def _inventory_rows(venues: dict[str, Any]) -> str:
    if not venues:
        return "<tr><td colspan='4' class='empty'>Nog geen venue-voorraad</td></tr>"
    rows: list[str] = []
    for venue, assets in sorted(venues.items()):
        if not isinstance(assets, dict):
            continue
        eur = assets.get("EUR", "0")
        usdt = assets.get("USDT", "0")
        other = ", ".join(
            f"{k}={v}"
            for k, v in sorted(assets.items())
            if k not in {"EUR", "USDT"} and _to_decimal(v) not in {None, Decimal("0")}
        ) or "—"
        rows.append(
            "<tr>"
            f"<td>{_esc(_venue_label(venue))}</td>"
            f"<td class='num'>{_esc(eur)}</td>"
            f"<td class='num'>{_esc(usdt)}</td>"
            f"<td>{_esc(other)}</td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan='4' class='empty'>Nog geen venue-voorraad</td></tr>"


def _esc(value: Any) -> str:
    text = "—" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _as_float(value: Any, default: float = 0.0) -> float:
    d = _to_decimal(value)
    if d is None:
        return default
    return float(d)


def _as_int(value: Any, default: int = 0) -> int:
    d = _to_decimal(value)
    if d is None:
        return default
    return int(d)


def _pnl_word(value: Any) -> str:
    d = _to_decimal(value)
    if d is None or d == 0:
        return "Winst / verlies"
    return "Winst" if d > 0 else "Verlies"


def _strategy_label(name: Any) -> str:
    key = str(name or "").strip().lower()
    return {
        "arbitrage": "Koop goedkoop, verkoop duurder",
        "crossexchangearbitragestrategy": "Koop goedkoop, verkoop duurder",
        "cross_exchange_arbitrage": "Koop goedkoop, verkoop duurder",
        "maker_inventory": "Maker: bied/laat vangen",
        "makerinventorystrategy": "Maker: bied/laat vangen",
        "triangle_bridge": "EUR↔USDT bridge",
        "desk_composite": "Desk (maker + triangle)",
        "global_composite": "Global composite (EV-ranked)",
        "funding_basis": "Funding / basis",
        "fx_relative_value": "FX relative value",
        "equity_mean_reversion": "Equity mean reversion",
        "momentum": "Meegaan met de beweging",
        "mean_reversion": "Terug naar het gemiddelde",
        "dca": "Periodiek bijkopen",
        "grid": "Grid-handel",
    }.get(key, str(name or "—").replace("_", " ").title())


def _venue_label(exchange: Any) -> str:
    key = str(exchange or "").strip().lower()
    return {
        "binance": "Binance",
        "kraken": "Kraken",
        "coinbase": "Coinbase",
        "bitvavo": "Bitvavo",
        "okx": "OKX",
        "bybit": "Bybit",
    }.get(key, str(exchange or "—").title())


def _status_label(status: Any) -> str:
    key = str(status or "").strip().lower()
    return {
        "detected": "Gezien",
        "rejected": "Afgewezen",
        "approved": "Goedgekeurd",
        "executed": "Uitgevoerd",
        "filled": "Gevuld",
        "profitable": "Winst",
        "unprofitable": "Verlies",
        "breakeven": "Gelijk",
        "running": "Actief",
        "stopped": "Gestopt",
        "idle": "Wacht",
        "error": "Fout",
    }.get(key, str(status or "—"))


def _svg_cumulative_profit(trades: list[dict], starting: Any = 0) -> str:
    if not trades:
        return (
            '<p class="chart-empty">Nog geen afgeronde transacties — '
            "de lijn verschijnt na de eerste verkoop.</p>"
        )
    chronological = list(reversed(trades))
    running = _as_float(starting, 0.0)
    values = [running]
    for trade in chronological:
        pnl = trade.get("realized_net_profit", trade.get("pnl"))
        running += _as_float(pnl, 0.0)
        values.append(running)
    return _svg_line(values)


def _svg_hourly_bars(hourly: list[dict]) -> str:
    if not hourly:
        return (
            '<p class="chart-empty">Nog geen uurdata. '
            "Na de eerste transacties zie je hier winst per uur.</p>"
        )
    values = [_as_float(row.get("net_pnl", row.get("pnl"))) for row in hourly]
    has_activity = any(
        value != 0 or _as_int(row.get("trades")) > 0
        for row, value in zip(hourly, values)
    )
    if not has_activity:
        return (
            '<p class="chart-empty">Nog geen uurdata. '
            "Na de eerste transacties zie je hier winst per uur.</p>"
        )
    labels = []
    for row in hourly:
        if row.get("hour") is not None:
            labels.append(f"{_as_int(row.get('hour')):02d}")
        else:
            labels.append(str(row.get("label") or "")[-5:] or "?")
    return _svg_bars(values, labels)


def _svg_win_loss(wins: Any, losses: Any) -> str:
    win_n = max(_as_int(wins), 0)
    loss_n = max(_as_int(losses), 0)
    total = win_n + loss_n
    if total == 0:
        return '<p class="chart-empty">Nog geen winst- of verliestransacties.</p>'
    win_w = 300.0 * win_n / total
    loss_w = 300.0 * loss_n / total
    return f"""
    <svg viewBox="0 0 320 64" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Winst versus verlies">
      <rect x="10" y="8" width="{win_w:.1f}" height="22" rx="6" fill="#34d399"/>
      <rect x="{10 + win_w:.1f}" y="8" width="{loss_w:.1f}" height="22" rx="6" fill="#f87171"/>
      <text x="10" y="50" fill="#34d399" font-size="13" font-weight="700">{win_n} winst</text>
      <text x="310" y="50" fill="#f87171" font-size="13" font-weight="700" text-anchor="end">{loss_n} verlies</text>
    </svg>
    """


def _svg_fleet_bars(instances: list[dict]) -> str:
    rows = [row for row in instances if row.get("ok")]
    if not rows:
        return '<p class="chart-empty">Nog geen online rekeningen om te vergelijken.</p>'
    values = [_as_float(row.get("net_pnl", row.get("pnl"))) for row in rows]
    labels = []
    for row in rows:
        capital = str(row.get("starting_capital") or "")
        kind = "R" if _fleet_profile(row) == "realistic" else "U"
        compact = capital.split(".")[0] if capital else str(row.get("label") or "?")[:6]
        labels.append(f"{compact}{kind}")
    return _svg_bars(values, labels)


def _svg_line(values: list[float]) -> str:
    width, height, pad = 640, 180, 22
    lo = min(values + [0.0])
    hi = max(values + [0.0])
    span = hi - lo or 1.0
    pts: list[str] = []
    for i, value in enumerate(values):
        x = pad + (width - 2 * pad) * (i / max(len(values) - 1, 1))
        y = height - pad - (height - 2 * pad) * ((value - lo) / span)
        pts.append(f"{x:.1f},{y:.1f}")
    last = values[-1]
    color = "#34d399" if last >= 0 else "#f87171"
    zero_y = height - pad - (height - 2 * pad) * ((0 - lo) / span)
    return f"""
    <svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Winst in de tijd">
      <line x1="{pad}" y1="{zero_y:.1f}" x2="{width - pad}" y2="{zero_y:.1f}" stroke="#243041" stroke-dasharray="4 4"/>
      <polyline fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round" points="{" ".join(pts)}"/>
      <text x="{width - pad}" y="16" fill="{color}" font-size="13" font-weight="700" text-anchor="end">{_fmt_money(last)}</text>
    </svg>
    """


def _svg_bars(values: list[float], labels: list[str]) -> str:
    if not values:
        return '<p class="chart-empty">Geen data.</p>'
    n = len(values)
    width = max(320, 28 * n + 40)
    height, pad = 180, 24
    lo = min(values + [0.0])
    hi = max(values + [0.0])
    span = hi - lo or 1.0
    zero_y = height - pad - (height - 2 * pad) * ((0 - lo) / span)
    gap = 6
    bar_w = max(8.0, (width - 2 * pad) / n - gap)
    bars: list[str] = []
    texts: list[str] = []
    for i, (value, label) in enumerate(zip(values, labels)):
        x = pad + i * ((width - 2 * pad) / n)
        y_val = height - pad - (height - 2 * pad) * ((value - lo) / span)
        top = min(y_val, zero_y)
        h = max(abs(y_val - zero_y), 1.5)
        color = "#34d399" if value >= 0 else "#f87171"
        bars.append(
            f'<rect x="{x:.1f}" y="{top:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="4" fill="{color}"/>'
        )
        texts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{height - 6}" fill="#8fa0b8" font-size="9" '
            f'text-anchor="middle">{_esc(label[:8])}</text>'
        )
    return f"""
    <svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Winst per periode">
      <line x1="{pad}" y1="{zero_y:.1f}" x2="{width - pad}" y2="{zero_y:.1f}" stroke="#243041" stroke-dasharray="4 4"/>
      {"".join(bars)}
      {"".join(texts)}
    </svg>
    """


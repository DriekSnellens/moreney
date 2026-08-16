"""Paper-trading dashboard (HTML). Extends the API — no fabricated values."""

from __future__ import annotations

import json
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
    forecast_day = (
        _fmt_money(forecast.get("projected_per_day_eur", 0))
        if forecast.get("projection_ready", True)
        else "n.v.t."
    )
    paper_day = _fmt_money(forecast.get("paper_run_rate_per_day_eur", 0))
    vol = forecast.get("vol_capture") or {}
    band_pct = _esc(str(vol.get("equity_move_pct") or forecast.get("projected_day_return_pct") or "—"))
    mtm = _fmt_money(forecast.get("paper_equity_pnl") or perf.get("paper_equity_pnl") or 0)
    forecast_note = _esc(forecast.get("note") or "Groei via NET euro per fill, niet via trade-aantal.")
    confidence = str(forecast.get("confidence") or "very_low")
    confidence_label = {
        "very_low": "Nog onzeker",
        "low": "Hoogste kans (geen garantie)",
        "medium": "Redelijk",
        "high": "Hoog (coupon)",
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
    realism_note = "Retail fees, fair-value aan, queue-fills uit. Dichtst bij echt geld."
    markout_1s = f"{_esc(str(markout.get('avg_adverse_bps_1s') or '0'))} bps"
    markout_5s = f"{_esc(str(markout.get('avg_adverse_bps_5s') or '0'))} bps"
    markout_30s = f"{_esc(str(markout.get('avg_adverse_bps_30s') or '0'))} bps"
    markout_60s = f"{_esc(str(markout.get('avg_adverse_bps_60s') or '0'))} bps"
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
    ge_equity_rows = _equity_quote_rows((global_engine.get("equity") or {}).get("quotes") or {})

    net_kpis = status.get("net_kpis") or {}
    why_not = status.get("why_not_trade") or {}
    ev_cal = status.get("ev_calibration") or {}
    why_rows = _why_not_rows(why_not)
    gate_rows = _gate_table_rows(why_not.get("gate_table") or [])
    cal_global = ev_cal.get("global") or {}
    ev_capture = _esc(
        str(net_kpis.get("ev_capture") or cal_global.get("raw_capture") or "—")
    )

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
        <p class="eyebrow">Oefenhandel · winst per fill, niet trade-aantal</p>
        <h1 class="brand">Moreney</h1>
        <p class="sub">{realism_note}</p>
      </div>
      <div class="hero-badges">
        <a class="link-lite" href="/fleet">Alle bots</a>
        <span class="badge {status_cls}">{status_label}</span>
        <span class="badge muted">Geen echte orders</span>
      </div>
    </div>
  </header>

  <main class="container">
    <section class="panel">
      <div class="panel-head">
        <h2>Bediening</h2>
        <span>
          <a class="link-lite" href="/fleet">Alle bots</a>
          ·
          <a class="link-lite" href="/paper/dashboard-lite">Mobiel</a>
        </span>
      </div>
      <div class="controls">
        <button type="button" class="btn" onclick="post('/paper/start')">Start</button>
        <button type="button" class="btn" onclick="post('/paper/stop')">Stop</button>
        <button type="button" class="btn btn-danger" onclick="resetPaper()">Opnieuw beginnen</button>
      </div>
    </section>

    <section class="panel highlight">
      <h2>Winst en tempo</h2>
      <div class="metric-grid">
        <article class="metric-card {pnl_cls}">
          <span class="label">Paper MTM</span>
          <span class="value">{mtm}</span>
        </article>
        <article class="metric-card">
          <span class="label">Live-equivalent PnL</span>
          <span class="value">{m('net_pnl')}</span>
        </article>
        <article class="metric-card">
          <span class="label">Maker-tempo / dag</span>
          <span class="value">{paper_day}</span>
        </article>
        <article class="metric-card">
          <span class="label">Zekerheid</span>
          <span class="value">{confidence_label}</span>
        </article>
      </div>
      <p class="forecast-note">{forecast_note}</p>
      <ul class="forecast-assumptions">{assumption_lis}</ul>
    </section>

    <section class="panel research-board">
      <div class="panel-head">
        <h2>Research findings</h2>
        <span class="badge muted">Geen productie-PnL</span>
      </div>
      <p class="research-sub">{_esc((status.get('research_findings') or {}).get('subtitle') or 'Onderzoeksconclusies')}</p>
      <div class="findings-grid">
        {_research_finding_cards((status.get('research_findings') or {}).get('cards') or [])}
      </div>
      <div class="finding-next">
        <span class="label">Volgende stap</span>
        <p>{_esc((status.get('research_findings') or {}).get('next_step') or (status.get('market_data_lab') or {}).get('next_step'))}</p>
      </div>
    </section>

    <section class="panel">
      <h2>Desk-monitor</h2>
      <div class="metric-grid compact">
        <article class="metric-card"><span class="label">Strategie</span><span class="value">{desk_strategy}</span></article>
        <article class="metric-card"><span class="label">Fee-tier</span><span class="value">{desk_tier}</span></article>
        <article class="metric-card"><span class="label">Markout 1s</span><span class="value">{markout_1s}</span></article>
        <article class="metric-card"><span class="label">Markout 5s</span><span class="value">{markout_5s}</span></article>
        <article class="metric-card"><span class="label">Markout 30s</span><span class="value">{markout_30s}</span></article>
        <article class="metric-card"><span class="label">Markout 60s</span><span class="value">{markout_60s}</span></article>
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
      <h2>NET-economie (per fill)</h2>
      <div class="metric-grid compact">
        <article class="metric-card"><span class="label">NET €/fill</span><span class="value">{_esc_fmt(net_kpis.get('net_eur_per_fill'), 'money')}</span></article>
        <article class="metric-card"><span class="label">NET bps/fill</span><span class="value">{_esc(str(net_kpis.get('net_bps_per_fill') or '0'))}</span></article>
        <article class="metric-card"><span class="label">EV capture</span><span class="value">{ev_capture}</span></article>
        <article class="metric-card"><span class="label">Fees/fill</span><span class="value">{_esc_fmt(net_kpis.get('fees_per_fill'), 'money')}</span></article>
        <article class="metric-card"><span class="label">Slippage/fill</span><span class="value">{_esc_fmt(net_kpis.get('slippage_per_fill'), 'money')}</span></article>
        <article class="metric-card"><span class="label">Kapitaal-velocity</span><span class="value">{_esc(str(net_kpis.get('capital_velocity') or '0'))}</span></article>
        <article class="metric-card"><span class="label">Reject opportunity cost</span><span class="value">{_esc_fmt(net_kpis.get('rejection_opportunity_cost'), 'money')}</span></article>
        <article class="metric-card"><span class="label">Calibratie n</span><span class="value">{_esc(str(cal_global.get('n') or 0))}</span></article>
      </div>
      <p class="forecast-note">EV capture = sum(realized NET) / sum(expected NET) op afgeronde round-trips. Shrinkage naar 1.0 voor ranking; early route-stop bij n≥8 + raw capture ≤ −0.25.</p>
    </section>

    <section class="panel">
      <h2>Edge-decompositie</h2>
      <div class="metric-grid compact">
        <article class="metric-card"><span class="label">Gross spread</span><span class="value">{_esc_fmt((status.get('edge_decomposition') or {}).get('overall', {}).get('gross_spread_contribution'), 'money')}</span></article>
        <article class="metric-card"><span class="label">Fees</span><span class="value">{_esc_fmt((status.get('edge_decomposition') or {}).get('overall', {}).get('fee_contribution'), 'money')}</span></article>
        <article class="metric-card"><span class="label">Slippage</span><span class="value">{_esc_fmt((status.get('edge_decomposition') or {}).get('overall', {}).get('slippage_contribution'), 'money')}</span></article>
        <article class="metric-card"><span class="label">Adverse</span><span class="value">{_esc_fmt((status.get('edge_decomposition') or {}).get('overall', {}).get('adverse_selection_contribution'), 'money')}</span></article>
        <article class="metric-card"><span class="label">NET alpha</span><span class="value">{_esc_fmt((status.get('edge_decomposition') or {}).get('overall', {}).get('net_alpha'), 'money')}</span></article>
        <article class="metric-card"><span class="label">E(NET|fill)</span><span class="value">{_esc_fmt((status.get('edge_decomposition') or {}).get('overall', {}).get('e_net_given_fill'), 'money')}</span></article>
      </div>
      <div class="table-wrap" style="margin-top:1rem">
        <table>
          <thead><tr><th>Route</th><th>n</th><th>Expected</th><th>Realized</th><th>EV capture</th></tr></thead>
          <tbody>{_edge_route_rows((status.get('edge_decomposition') or {}).get('by_route') or {})}</tbody>
        </table>
      </div>
      <p class="forecast-note">Waterfall: gross − fees − slippage − adverse − inventory = realized NET. execution_buffer is alleen expected haircut.</p>
    </section>

    <section class="panel">
      <h2>Route-status</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Route</th><th>State</th><th>n</th><th>Raw capture</th>
              <th>Shrunk</th><th>Realized</th><th>Reden</th>
            </tr>
          </thead>
          <tbody>{_route_state_rows((status.get('ev_calibration') or {}).get('route_states') or (status.get('ev_calibration') or {}).get('routes') or {})}</tbody>
        </table>
      </div>
      <p class="forecast-note">
        EARLY_STOPPED = raw verlies overrulet positieve shrinkage.
        Shrinkage blijft voor ranking; early-stop is aparte loss-containment.
      </p>
    </section>

    <section class="panel">
      <h2>Toxicity shadow (pre-trade)</h2>
      <div class="metric-grid compact">
        <article class="metric-card"><span class="label">Shadow mode</span><span class="value">{_esc((status.get('toxicity_shadow') or {}).get('enabled') and 'Aan' or 'Uit')}</span></article>
        <article class="metric-card"><span class="label">Wijzigt fills?</span><span class="value">Nee</span></article>
        <article class="metric-card"><span class="label">Model n</span><span class="value">{_esc(((status.get('toxicity_shadow') or {}).get('model') or {}).get('global_n'))}</span></article>
        <article class="metric-card"><span class="label">Global mean bps</span><span class="value">{_esc(((status.get('toxicity_shadow') or {}).get('model') or {}).get('global_mean_bps'))}</span></article>
        <article class="metric-card"><span class="label">Predicted adverse bps</span><span class="value">{_esc(((status.get('toxicity_shadow') or {}).get('last') or {}).get('predicted_adverse_bps'))}</span></article>
        <article class="metric-card"><span class="label">Uncertainty bps</span><span class="value">{_esc(((status.get('toxicity_shadow') or {}).get('last') or {}).get('uncertainty_bps'))}</span></article>
        <article class="metric-card"><span class="label">Sample size</span><span class="value">{_esc(((status.get('toxicity_shadow') or {}).get('last') or {}).get('sample_count'))}</span></article>
        <article class="metric-card"><span class="label">Shrinkage</span><span class="value">{_esc(((status.get('toxicity_shadow') or {}).get('last') or {}).get('shrinkage_source'))}</span></article>
        <article class="metric-card"><span class="label">Toxicity %ile</span><span class="value">{_esc(((status.get('toxicity_shadow') or {}).get('last') or {}).get('toxicity_percentile'))}</span></article>
        <article class="metric-card"><span class="label">Quote age bucket</span><span class="value">{_esc(((status.get('toxicity_shadow') or {}).get('last') or {}).get('quote_age_bucket'))}</span></article>
        <article class="metric-card"><span class="label">Spread bucket</span><span class="value">{_esc(((status.get('toxicity_shadow') or {}).get('last') or {}).get('spread_bucket'))}</span></article>
        <article class="metric-card"><span class="label">E[NET] before tox</span><span class="value">{_esc_fmt(((status.get('toxicity_shadow') or {}).get('last') or {}).get('expected_net_before_toxicity'), 'money')}</span></article>
        <article class="metric-card"><span class="label">E[adverse]</span><span class="value">{_esc_fmt(((status.get('toxicity_shadow') or {}).get('last') or {}).get('expected_adverse_eur'), 'money')}</span></article>
        <article class="metric-card"><span class="label">Uncertainty penalty</span><span class="value">{_esc_fmt(((status.get('toxicity_shadow') or {}).get('last') or {}).get('uncertainty_penalty_eur'), 'money')}</span></article>
        <article class="metric-card"><span class="label">Tox-adjusted NET</span><span class="value">{_esc_fmt(((status.get('toxicity_shadow') or {}).get('last') or {}).get('toxicity_adjusted_net'), 'money')}</span></article>
        <article class="metric-card"><span class="label">Shadow decision</span><span class="value">{_esc(((status.get('toxicity_shadow') or {}).get('last') or {}).get('reason'))}</span></article>
      </div>
      <p class="forecast-note">
        Pre-trade E(adverse|fill,state). Shadow only — wijzigt geen fills, fees of live toelating.
        Observed adverse verschijnt in markout/waterfall ná fill.
      </p>
    </section>

    <section class="panel">
      <h2>FILL MODEL LAB</h2>
      <div class="verdict-banner {_verdict_banner_class((status.get('fill_model_lab') or {}).get('recommendation'))}">
        <div>
          <span class="vb-kicker">Aanbeveling</span>
          <strong class="vb-verdict">{_esc((status.get('fill_model_lab') or {}).get('recommendation') or 'REQUIRE BETTER DATA')}</strong>
          <p class="vb-headline">{_esc((status.get('fill_model_lab') or {}).get('headline') or 'Trade-through baseline behouden')}</p>
        </div>
        <div class="vb-meta">
          <span>PnL source: {_esc((status.get('fill_model_lab') or {}).get('production_pnl_source') or 'TRADE_THROUGH_ONLY')}</span>
          <span>Letter: {_esc((status.get('fill_model_lab') or {}).get('success_letter'))}</span>
          <span>TT selector: {_esc(((status.get('fill_model_lab') or {}).get('toxicity_selector') or {}).get('answer'))}</span>
        </div>
      </div>
      <div class="table-wrap" style="margin-top:1rem">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>Label</th>
              <th>Support</th>
              <th>n</th>
              <th>Fill rate</th>
              <th>Median adverse</th>
              <th>Mean adverse</th>
              <th>NET</th>
              <th>Capital lock</th>
            </tr>
          </thead>
          <tbody>{_fill_model_lab_rows((status.get('fill_model_lab') or {}).get('panel') or [])}</tbody>
        </table>
      </div>
      <p class="forecast-note">
        CONSERVATIVE BASELINE = productie headline (TRADE_THROUGH_ONLY).
        EXPERIMENTAL COUNTERFACTUAL = observational only — nooit live-equivalent PnL.
        Zonder book/trade recordings blijven touch/depth-modellen UNSUPPORTED.
      </p>
    </section>

    <section class="panel">
      <h2>LEAD-LAG LAB <span class="pill">RESEARCH ONLY</span></h2>
      <div class="verdict-banner {_verdict_banner_class((status.get('lead_lag_lab') or {}).get('verdict'))}">
        <div>
          <span class="vb-kicker">Verdict</span>
          <strong class="vb-verdict">{_esc((status.get('lead_lag_lab') or {}).get('verdict') or 'INSUFFICIENT_DATA')}</strong>
          <p class="vb-headline">{_esc((status.get('lead_lag_lab') or {}).get('headline'))}</p>
        </div>
        <div class="vb-meta">
          <span>Quality: {_esc((status.get('lead_lag_lab') or {}).get('data_quality'))}</span>
          <span>Observer n: {_esc(((status.get('lead_lag_lab') or {}).get('observer') or {}).get('n_observations'))}</span>
          <span>Execution: {_esc((status.get('lead_lag_lab') or {}).get('execution_enabled') and 'Aan' or 'Uit')}</span>
        </div>
      </div>
      <p class="forecast-note">{_esc((status.get('lead_lag_lab') or {}).get('finding'))}</p>
      <div class="metric-grid compact">
        <article class="metric-card"><span class="label">Shadow only</span><span class="value">{_esc((status.get('lead_lag_lab') or {}).get('shadow_only') and 'Ja' or 'Nee')}</span></article>
        <article class="metric-card"><span class="label">Wijzigt PnL?</span><span class="value">Nee</span></article>
        <article class="metric-card"><span class="label">Enabled</span><span class="value">{_esc((status.get('lead_lag_lab') or {}).get('enabled') and 'Aan' or 'Uit')}</span></article>
      </div>
      <div class="table-wrap" style="margin-top:1rem">
        <table>
          <thead>
            <tr>
              <th>Pair</th>
              <th>Status</th>
              <th>Quality</th>
              <th>n</th>
              <th>Horizon</th>
              <th>Hit rate</th>
              <th>Median resp</th>
              <th>Pred err</th>
              <th>Shadow ops</th>
              <th>Admitted</th>
              <th>Shadow NET</th>
            </tr>
          </thead>
          <tbody>{_lead_lag_lab_rows((status.get('lead_lag_lab') or {}).get('panel') or [])}</tbody>
        </table>
      </div>
      <p class="forecast-note">
        RESEARCH ONLY — nooit mergen in Live-equivalent PnL.
        Non-participation is geen alpha. Default: LEAD_LAG_EXECUTION_ENABLED=false.
      </p>
    </section>

    <section class="panel">
      <h2>AUTONOMOUS RESEARCH <span class="pill">LOCAL LLM · RESEARCH ONLY</span></h2>
      <div class="verdict-banner {_verdict_banner_class('PARTIAL' if (status.get('autonomous_research') or {}).get('LLM_STATUS')=='AVAILABLE' else 'NOT_READY')}">
        <div>
          <span class="vb-kicker">Research scientist — not the judge</span>
          <strong class="vb-verdict">{_esc((status.get('autonomous_research') or {}).get('LLM_STATUS') or 'UNKNOWN')}</strong>
          <p class="vb-headline">Provider: {_esc((status.get('autonomous_research') or {}).get('Provider') or 'ollama')} · Model: {_esc((status.get('autonomous_research') or {}).get('Model'))}</p>
        </div>
        <div class="vb-meta">
          <span>Connection: {_esc((status.get('autonomous_research') or {}).get('Connection'))}</span>
          <span>Autonomous: {_esc((status.get('autonomous_research') or {}).get('Autonomous_mode'))}</span>
          <span>Research-only: YES</span>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Round field</th><th>Value</th></tr></thead>
          <tbody>{_autonomous_round_rows((status.get('autonomous_research') or {}).get('CURRENT_RESEARCH_ROUND') or {})}</tbody>
        </table>
      </div>
      <h3 class="subhead">Hypothesis pipeline (canonical = tournament)</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Strategy</th><th>Verdict</th><th>Gate</th></tr></thead>
          <tbody>{_autonomous_pipeline_rows((status.get('autonomous_research') or {}).get('HYPOTHESIS_PIPELINE') or [])}</tbody>
        </table>
      </div>
      <h3 class="subhead">Multiple testing exposure</h3>
      <p class="forecast-note">{_esc(json.dumps((status.get('autonomous_research') or {}).get('multiple_testing_exposure') or {}, sort_keys=True))}</p>
      <h3 class="subhead">WHAT THE LLM LEARNED</h3>
      <p class="forecast-note">
        <strong>NON-AUTHORITATIVE RESEARCH ANALYSIS</strong> —
        {_esc((status.get('autonomous_research') or {}).get('disclaimer'))}
      </p>
      <ul class="finding-list">
        {_autonomous_learning_items((status.get('autonomous_research') or {}).get('WHAT_THE_LLM_LEARNED') or {})}
      </ul>
      <p class="forecast-note">Run: python -m bot.research.autonomous.runner --dry-run</p>
    </section>

    <section class="panel">
      <h2>RESEARCH TOURNAMENT <span class="pill">RESEARCH ONLY</span></h2>
      <div class="verdict-banner {_verdict_banner_class('PARTIAL' if (status.get('research_tournament') or {}).get('PAPER_CANDIDATES') else 'NOT_READY')}">
        <div>
          <span class="vb-kicker">Strategy families under identical rules</span>
          <strong class="vb-verdict">{_esc('PAPER_CANDIDATE' if (status.get('research_tournament') or {}).get('PAPER_CANDIDATES') else 'ALL REJECTED' if (status.get('research_tournament') or {}).get('ALL_STRATEGIES_REJECTED') else 'PENDING')}</strong>
          <p class="vb-headline">{_esc((status.get('research_tournament') or {}).get('headline'))}</p>
        </div>
        <div class="vb-meta">
          <span>Dataset: {_esc((status.get('research_tournament') or {}).get('CURRENT_DATASET') or 'NONE')}</span>
          <span>Candidates: {_esc(len((status.get('research_tournament') or {}).get('PAPER_CANDIDATES') or []))}</span>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Strategy</th>
              <th>Verdict</th>
              <th>Failed gate</th>
              <th>Dev signals</th>
              <th>OOS signals</th>
              <th>Expected NET</th>
              <th>Exec NET</th>
              <th>Score</th>
            </tr>
          </thead>
          <tbody>{_research_tournament_rows((status.get('research_tournament') or {}).get('scoreboard') or [])}</tbody>
        </table>
      </div>
      <p class="forecast-note">
        Gescheiden van Live-equivalent PnL / Paper MTM.
        {_esc((status.get('research_tournament') or {}).get('disclaimer') or 'RESEARCH CANDIDATE — NOT PROVEN LIVE PROFITABLE')}
        Run: python -m bot.research.tournament.runner
      </p>
    </section>

    <section class="panel">
      <h2>RESEARCH DATA STATUS <span class="pill">OPERATIONAL</span></h2>
      <div class="verdict-banner {_verdict_banner_class(((status.get('market_data_lab') or {}).get('research_data_status') or {}).get('FINAL_ACCEPTANCE_VERDICT') or (status.get('market_data_lab') or {}).get('verdict'))}">
        <div>
          <span class="vb-kicker">CURRENT STATE</span>
          <strong class="vb-verdict">{_esc(((status.get('market_data_lab') or {}).get('research_data_status') or {}).get('CURRENT_STATE') or (status.get('market_data_lab') or {}).get('verdict') or 'NO_REAL_TAPE')}</strong>
          <p class="vb-headline">{_esc((status.get('market_data_lab') or {}).get('headline'))}</p>
        </div>
        <div class="vb-meta">
          <span>Enabled: {_esc(((status.get('market_data_lab') or {}).get('research_data_status') or {}).get('RECORDER_ENABLED'))}</span>
          <span>Running: {_esc(((status.get('market_data_lab') or {}).get('research_data_status') or {}).get('RECORDER_RUNNING'))}</span>
          <span>Written: {_esc(((status.get('market_data_lab') or {}).get('research_data_status') or {}).get('EVENTS_WRITTEN') or 0)}</span>
          <span>Dropped: {_esc(((status.get('market_data_lab') or {}).get('research_data_status') or {}).get('EVENTS_DROPPED') or 0)}</span>
          <span>Write err: {_esc(((status.get('market_data_lab') or {}).get('research_data_status') or {}).get('WRITE_ERRORS') or 0)}</span>
          <span>Queue: {_esc(((status.get('market_data_lab') or {}).get('research_data_status') or {}).get('QUEUE_DEPTH') or 0)}</span>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Field</th>
              <th>Value</th>
            </tr>
          </thead>
          <tbody>{_research_data_status_rows((status.get('market_data_lab') or {}).get('research_data_status') or {})}</tbody>
        </table>
      </div>
      <h3 class="subhead">Horizon readiness</h3>
      <div class="horizon-chips">
        {_horizon_chips((status.get('market_data_lab') or {}).get('horizon_rows') or [])}
      </div>
      <p class="forecast-note">
        Onderscheid: NO DATA ≠ BAD DATA ≠ USABLE FOR SLOW ≠ USABLE FOR FAST.
        Geen Live-equivalent PnL-wijziging.
      </p>
    </section>

    <section class="panel">
      <h2>MARKET DATA LAB <span class="pill">RESEARCH INFRASTRUCTURE</span></h2>
      <div class="verdict-banner {_verdict_banner_class((status.get('market_data_lab') or {}).get('verdict'))}">
        <div>
          <span class="vb-kicker">Acceptance</span>
          <strong class="vb-verdict">{_esc((status.get('market_data_lab') or {}).get('verdict') or 'DATA_NOT_READY')}</strong>
          <p class="vb-headline">{_esc((status.get('market_data_lab') or {}).get('headline'))}</p>
        </div>
        <div class="vb-meta">
          <span>Events: {_esc((status.get('market_data_lab') or {}).get('event_count') or 0)}</span>
          <span>Dataset: {_esc((status.get('market_data_lab') or {}).get('dataset_id') or 'NONE')}</span>
          <span>Dropped: {_esc(((status.get('market_data_lab') or {}).get('recorder') or {}).get('dropped') or 0)}</span>
        </div>
      </div>
      <ul class="finding-list">
        {_finding_list_items((status.get('market_data_lab') or {}).get('findings') or [])}
      </ul>
      <h3 class="subhead">Venues</h3>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Venue</th>
              <th>Exchange ts</th>
              <th>Quality</th>
              <th>Events</th>
              <th>Ex-ts %</th>
              <th>p50</th>
              <th>p95</th>
              <th>p99</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>{_market_data_lab_rows((status.get('market_data_lab') or {}).get('panel') or [])}</tbody>
        </table>
      </div>
      <div class="finding-next">
        <span class="label">Volgende stap</span>
        <p>{_esc((status.get('market_data_lab') or {}).get('next_step'))}</p>
      </div>
      <p class="forecast-note">
        RESEARCH INFRASTRUCTURE — timestamps, sync, replay. Geen alpha-optimalisatie.
        Redis blijft transport; research tape leeft op de publisher vóór Redis.
      </p>
    </section>

    <section class="panel">
      <h2>Why not trade?</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Eerste gate</th><th>Aantal</th></tr></thead>
          <tbody>{why_rows}</tbody>
        </table>
      </div>
      <div class="table-wrap" style="margin-top:1rem">
        <table>
          <thead><tr><th>Gate</th><th>Rejects</th><th>Geschat terecht</th><th>Geschat gemist</th><th>Advies</th></tr></thead>
          <tbody>{gate_rows}</tbody>
        </table>
      </div>
      <p class="forecast-note">Counterfactual gebruikt alleen mid-prijzen ná de beslissing en gaat niet terug de live ranker in.</p>
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
          <thead><tr><th>Aandeel</th><th>Bid</th><th>Ask</th><th>Last</th><th>Bron</th></tr></thead>
          <tbody>{ge_equity_rows}</tbody>
        </table>
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
        "low": "Hoogste kans (geen garantie)",
        "medium": "Redelijk",
        "high": "Hoog (coupon)",
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
      <span class="label">Paper MTM</span>
      <span class="value-xl">{_fmt_money(perf.get('paper_equity_pnl', perf.get('net_pnl', 0)))}</span>
      <span class="sub-metric">Up-day band {forecast_day} · {confidence_label}</span>
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
      <p class="foot-lite"><a href="/fleet">Alle bots</a> · <a href="/paper/dashboard">Volledig overzicht</a> · Oefenhandel · 5s</p>
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

    glance_html = "".join(_render_glance_card(row) for row in instances)
    if not glance_html:
        glance_html = "<p class='empty'>Geen rekeningen ingesteld.</p>"
    table_html = _render_fleet_table(instances)
    fleet_chart = _svg_fleet_bars(instances)
    html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
  <meta name="theme-color" content="#0b0f14"/>
  <meta http-equiv="refresh" content="5"/>
  <title>Moreney — Alle bots</title>
  <style>{_shared_css(fleet=True)}</style>
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      <div>
        <p class="eyebrow">Oefenhandel · alle rekeningen</p>
        <h1 class="brand">Alle bots</h1>
        <p class="sub">{online}/{configured} online · retail fees · ververst elke 5s</p>
      </div>
      <a class="link-lite" href="/logout">Uitloggen</a>
    </div>
    <div class="container totals-bar">
      <article class="metric-card"><span class="label">Transacties</span><span class="value">{_esc_fmt(totals.get('trade_count'), 'count')}</span></article>
      <article class="metric-card {_pnl_class(totals.get('net_pnl'))}"><span class="label">PnL</span><span class="value">{_esc_fmt(totals.get('net_pnl'), 'money')}</span></article>
      <article class="metric-card"><span class="label">Vermogen</span><span class="value">{_esc_fmt(totals.get('equity'), 'money')}</span></article>
      <article class="metric-card"><span class="label">Open quotes</span><span class="value">{_esc_fmt(totals.get('open_maker_quotes'), 'count')}</span></article>
    </div>
    <p class="sub totals-note">PnL is winst op afgeronde transacties. Vermogen is cash plus voorraad tegen de markt — geen extra inleg.</p>
  </header>
  <main class="container">
    <section class="panel">
      <div class="panel-head">
        <h2>Bediening</h2>
        <span class="muted">{online}/{configured} online</span>
      </div>
      <div class="controls">
        <button type="button" class="btn btn-danger" id="fleet-reset-btn" onclick="resetFleet()">
          Alle bots opnieuw beginnen
        </button>
      </div>
      <p class="forecast-note" id="fleet-reset-status">
        Zet elke oefenrekening terug naar startkapitaal (PnL, trades, markout, calibratie).
        Raakt geen echte beursrekeningen. Bots starten daarna opnieuw.
      </p>
    </section>
    <section class="glance-grid">{glance_html}</section>
    <section class="panel">
      <h2>Vergelijking</h2>
      <div class="table-wrap">{table_html}</div>
    </section>
    <section class="panel">
      <h2>Winst per rekening</h2>
      {fleet_chart}
    </section>
  </main>
  <footer class="footer">Oefenhandel · geen echte orders · retail fees</footer>
  <script>
    async function resetFleet() {{
      const msg =
        "Alle oefenbots opnieuw beginnen?\\n\\n" +
        "PnL, trades, markout en calibratie gaan terug naar nul. " +
        "Dit raakt geen echte beursrekeningen. Bots starten daarna opnieuw.";
      if (!confirm(msg)) return;
      const btn = document.getElementById("fleet-reset-btn");
      const status = document.getElementById("fleet-reset-status");
      if (btn) {{
        btn.disabled = true;
        btn.textContent = "Bezig…";
      }}
      if (status) status.textContent = "Alle bots worden gereset…";
      try {{
        const res = await fetch("/fleet/reset", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{confirm: true, restart: true}}),
        }});
        const data = await res.json().catch(() => ({{}}));
        if (!res.ok) {{
          throw new Error(
            (data.detail && data.detail.message) || data.message || res.statusText
          );
        }}
        const ok = data.ok_count ?? 0;
        const total = data.configured_count ?? 0;
        if (status) {{
          status.textContent =
            "Klaar: " + ok + "/" + total + " bots gereset. Pagina ververst…";
        }}
        setTimeout(() => location.reload(), 1200);
      }} catch (err) {{
        if (status) status.textContent = "Reset mislukt: " + err;
        if (btn) {{
          btn.disabled = false;
          btn.textContent = "Alle bots opnieuw beginnen";
        }}
      }}
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


def _return_fraction(row: dict[str, Any]) -> Any:
    start = _as_float(row.get("starting_capital"))
    pnl = _as_float(row.get("net_pnl"))
    if start == 0:
        return None
    return pnl / start


def _render_glance_card(row: dict[str, Any]) -> str:
    href = _esc(row.get("dashboard_url") or "#")
    if not row.get("ok"):
        return f"""
        <a class="glance-card offline" href="{href}">
          <span class="glance-label">{_esc(row.get('label'))}</span>
          <span class="badge warn">Offline</span>
          <span class="glance-error">{_esc(row.get('error'))}</span>
        </a>
        """
    running = row.get("running")
    status_label = "Actief" if running else "Gestopt"
    badge_cls = "ok" if running else "warn"
    return f"""
    <a class="glance-card" href="{href}">
      <span class="glance-label">{_esc(row.get('label'))}</span>
      <span class="glance-pnl {_pnl_class(row.get('net_pnl'))}">{_esc_fmt(row.get('net_pnl'), 'money')}</span>
      <span class="glance-sub">{_esc_fmt(_return_fraction(row), 'pct')} · {_esc_fmt(row.get('equity'), 'money')}</span>
      <span class="badge {badge_cls}">{status_label}</span>
    </a>
    """


def _render_fleet_table(instances: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for row in instances:
        if not row.get("ok"):
            rows.append(
                "<tr class='offline'>"
                f"<td>{_esc(row.get('label'))}</td>"
                "<td><span class='badge warn'>Offline</span></td>"
                "<td colspan='7' class='error'>"
                f"{_esc(row.get('error'))}</td>"
                f"<td><a href='{_esc(row.get('dashboard_url'))}'>Open</a></td>"
                "</tr>"
            )
            continue
        running = row.get("running")
        status_label = "Actief" if running else "Gestopt"
        badge_cls = "ok" if running else "warn"
        dash = _esc(row.get("dashboard_url"))
        lite = _esc(row.get("dashboard_lite_url"))
        rows.append(
            "<tr>"
            f"<td><strong>{_esc(row.get('label'))}</strong></td>"
            f"<td><span class='badge {badge_cls}'>{status_label}</span></td>"
            f"<td class='num'>{_esc_fmt(row.get('starting_capital'), 'money')}</td>"
            f"<td class='num'>{_esc_fmt(row.get('equity'), 'money')}</td>"
            f"<td class='num {_pnl_class(row.get('net_pnl'))}'>{_esc_fmt(row.get('net_pnl'), 'money')}</td>"
            f"<td class='num {_pnl_class(row.get('net_pnl'))}'>{_esc_fmt(_return_fraction(row), 'pct')}</td>"
            f"<td class='num'>{_esc_fmt(row.get('trade_count') if row.get('trade_count') is not None else 0, 'count')}</td>"
            f"<td class='num'>{_esc_fmt(row.get('open_maker_quotes') if row.get('open_maker_quotes') is not None else 0, 'count')}</td>"
            f"<td class='num'>{_esc_fmt(row.get('win_rate'), 'pct')}</td>"
            f"<td><a href='{dash}'>Overzicht</a> · <a href='{lite}'>Mobiel</a></td>"
            "</tr>"
        )
    body = "".join(rows) or "<tr><td colspan='10' class='empty'>Geen rekeningen ingesteld.</td></tr>"
    return f"""
    <table class="fleet-table">
      <thead>
        <tr>
          <th>Bot</th>
          <th>Status</th>
          <th>Inleg</th>
          <th>Vermogen</th>
          <th>PnL</th>
          <th>%</th>
          <th>Transacties</th>
          <th>Quotes</th>
          <th>Winstkans</th>
          <th></th>
        </tr>
      </thead>
      <tbody>{body}</tbody>
    </table>
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
    .totals-note { max-width: 1100px; margin: 0 auto; padding: 0 1rem 1.1rem; }
    .glance-grid { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); margin-bottom: 1.25rem; }
    .glance-card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: .85rem 1rem; display: grid; gap: .3rem; text-decoration: none; color: inherit; min-height: 7.5rem; }
    .glance-card:hover { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
    .glance-card.offline { border-color: color-mix(in srgb, var(--bad) 40%, var(--line)); }
    .glance-label { font-size: .82rem; color: var(--muted); font-weight: 600; }
    .glance-pnl { font-family: var(--mono); font-size: 1.35rem; font-weight: 700; }
    .glance-sub { color: var(--muted); font-size: .8rem; }
    .glance-error { color: var(--bad); font-size: .75rem; overflow: hidden; text-overflow: ellipsis; }
    .fleet-table { width: 100%; border-collapse: collapse; }
    .fleet-table th { text-align: left; color: var(--muted); font-size: .75rem; font-weight: 600; padding: .45rem .5rem; border-bottom: 1px solid var(--line); }
    .fleet-table td { padding: .55rem .5rem; border-bottom: 1px solid var(--line); vertical-align: middle; }
    .error { color: var(--bad); font-size: .85rem; margin: 0; }
    @media (min-width: 720px) {
      .totals-bar { grid-template-columns: repeat(4, minmax(0,1fr)); }
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
    .research-board {{
      border-color: color-mix(in srgb, var(--warn) 28%, var(--line));
    }}
    .research-sub {{
      margin: 0 0 1rem;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .findings-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 0.85rem;
    }}
    .finding-card {{
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 0.9rem 1rem;
      background: color-mix(in srgb, var(--surface) 88%, #000);
    }}
    .finding-top {{
      display: flex;
      justify-content: space-between;
      gap: 0.5rem;
      align-items: center;
      margin-bottom: 0.55rem;
    }}
    .finding-title {{
      font-size: 0.78rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
      font-weight: 700;
    }}
    .finding-headline {{
      margin: 0 0 0.35rem;
      font-size: 1.02rem;
      font-weight: 700;
      line-height: 1.25;
    }}
    .finding-detail {{
      margin: 0;
      color: var(--muted);
      font-size: 0.86rem;
      line-height: 1.35;
    }}
    .finding-next {{
      margin-top: 1rem;
      padding: 0.85rem 1rem;
      border-radius: 12px;
      border: 1px dashed color-mix(in srgb, var(--warn) 40%, var(--line));
      background: color-mix(in srgb, var(--warn) 8%, transparent);
    }}
    .finding-next .label {{
      display: block;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
      margin-bottom: 0.35rem;
      font-weight: 700;
    }}
    .finding-next p {{
      margin: 0;
      font-size: 0.92rem;
      line-height: 1.4;
    }}
    .verdict-banner {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      gap: 1rem;
      padding: 1rem 1.1rem;
      border-radius: 14px;
      margin-bottom: 1rem;
      border: 1px solid var(--line);
    }}
    .verdict-banner.bad {{
      border-color: color-mix(in srgb, var(--bad) 45%, var(--line));
      background: color-mix(in srgb, var(--bad) 12%, transparent);
    }}
    .verdict-banner.warn {{
      border-color: color-mix(in srgb, var(--warn) 45%, var(--line));
      background: color-mix(in srgb, var(--warn) 12%, transparent);
    }}
    .verdict-banner.ok {{
      border-color: color-mix(in srgb, var(--good) 45%, var(--line));
      background: color-mix(in srgb, var(--good) 12%, transparent);
    }}
    .vb-kicker {{
      display: block;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      margin-bottom: 0.2rem;
    }}
    .vb-verdict {{
      display: block;
      font-size: 1.25rem;
      letter-spacing: -0.02em;
    }}
    .vb-headline {{
      margin: 0.35rem 0 0;
      color: var(--text);
      opacity: 0.9;
    }}
    .vb-meta {{
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
      color: var(--muted);
      font-size: 0.86rem;
      justify-content: center;
    }}
    .finding-list {{
      margin: 0 0 1rem;
      padding-left: 1.1rem;
      color: var(--text);
      line-height: 1.45;
    }}
    .subhead {{
      margin: 0.4rem 0 0.55rem;
      font-size: 0.95rem;
    }}
    .horizon-chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.45rem;
      margin-bottom: 1rem;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      padding: 0.28rem 0.55rem;
      border-radius: 999px;
      border: 1px solid var(--line);
      font-size: 0.78rem;
      font-weight: 700;
      color: var(--muted);
    }}
    .chip.ok {{ color: var(--good); border-color: color-mix(in srgb, var(--good) 40%, var(--line)); }}
    .chip.warn {{ color: var(--warn); border-color: color-mix(in srgb, var(--warn) 40%, var(--line)); }}
    .chip.bad {{ color: var(--bad); border-color: color-mix(in srgb, var(--bad) 40%, var(--line)); }}
    td.note {{ max-width: 18rem; color: var(--muted); font-size: 0.82rem; }}
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


def _fill_model_lab_rows(panel: list[dict[str, Any]]) -> str:
    if not panel:
        return "<tr><td colspan='9' class='empty'>Geen fill-lab rapport — run study</td></tr>"
    rows: list[str] = []
    for row in panel:
        status = str(row.get("status") or "")
        if status == "CONSERVATIVE_BASELINE":
            label = "CONSERVATIVE BASELINE"
        elif status == "UNSUPPORTED":
            label = "EXPERIMENTAL (UNSUPPORTED)"
        else:
            label = "EXPERIMENTAL COUNTERFACTUAL"
        lock = row.get("capital_lock") or {}
        lock_med = None
        if isinstance(lock, dict):
            lock_med = (lock.get("capital_lock_ms") or {}).get("median")
        adv_med = row.get("median_adverse")
        if adv_med is None:
            adv_med = row.get("median_adverse_bps_5s_export")
        adv_mean = row.get("mean_adverse")
        if adv_mean is None:
            adv_mean = row.get("mean_adverse_bps_5s_export")
        rows.append(
            "<tr>"
            f"<td>{_esc(row.get('model'))}</td>"
            f"<td>{_esc(label)}</td>"
            f"<td>{_esc(row.get('support'))}</td>"
            f"<td class='num'>{_esc(row.get('sample_count'))}</td>"
            f"<td class='num'>{_esc(row.get('fill_rate'))}</td>"
            f"<td class='num'>{_esc(adv_med)}</td>"
            f"<td class='num'>{_esc(adv_mean)}</td>"
            f"<td class='num'>{_esc(row.get('net'))}</td>"
            f"<td class='num'>{_esc(lock_med)}</td>"
            "</tr>"
        )
    return "".join(rows)


def _lead_lag_lab_rows(panel: list[dict[str, Any]]) -> str:
    if not panel:
        return (
            "<tr><td colspan='11' class='empty'>"
            "Geen lead-lag rapport — run study (verwacht INSUFFICIENT_DATA zonder tape)"
            "</td></tr>"
        )
    rows: list[str] = []
    for row in panel:
        rows.append(
            "<tr>"
            f"<td>{_esc(row.get('pair'))}</td>"
            f"<td>{_esc(row.get('status'))}</td>"
            f"<td>{_esc(row.get('data_quality'))}</td>"
            f"<td class='num'>{_esc(row.get('sample_count'))}</td>"
            f"<td class='num'>{_esc(row.get('horizon_ms'))}</td>"
            f"<td class='num'>{_esc(row.get('directional_hit_rate'))}</td>"
            f"<td class='num'>{_esc(row.get('median_follower_response'))}</td>"
            f"<td class='num'>{_esc(row.get('estimated_prediction_error'))}</td>"
            f"<td class='num'>{_esc(row.get('shadow_opportunities'))}</td>"
            f"<td class='num'>{_esc(row.get('conservative_admissions'))}</td>"
            f"<td class='num'>{_esc(row.get('counterfactual_shadow_net'))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _autonomous_round_rows(round_info: dict[str, Any]) -> str:
    if not round_info:
        return "<tr><td colspan='2' class='empty'>Nog geen autonomous run</td></tr>"
    return "".join(
        f"<tr><td>{_esc(k)}</td><td class='note'>{_esc(v)}</td></tr>"
        for k, v in round_info.items()
    )


def _autonomous_pipeline_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<tr><td colspan='3' class='empty'>Geen pipeline</td></tr>"
    return "".join(
        "<tr>"
        f"<td>{_esc(r.get('strategy'))}</td>"
        f"<td><span class='chip {_status_chip_class(r.get('verdict'))}'>{_esc(r.get('verdict'))}</span></td>"
        f"<td>{_esc(r.get('gate') or '—')}</td>"
        "</tr>"
        for r in rows
    )


def _autonomous_learning_items(block: dict[str, Any]) -> str:
    items = block.get("items") if isinstance(block, dict) else None
    if not items:
        lessons = (block or {}).get("shared_lessons") if isinstance(block, dict) else None
        if lessons:
            return "".join(f"<li>{_esc(x)}</li>" for x in lessons)
        return "<li>Nog geen LLM-analyse.</li>"
    parts = []
    for it in items:
        if isinstance(it, dict):
            parts.append(
                f"<li>{_esc(it.get('learned') or it.get('notes') or it)}</li>"
            )
        else:
            parts.append(f"<li>{_esc(it)}</li>")
    return "".join(parts)


def _research_tournament_rows(board: list[dict[str, Any]]) -> str:
    if not board:
        return (
            "<tr><td colspan='8' class='empty'>"
            "Nog geen scoreboard — run python -m bot.research.tournament.runner"
            "</td></tr>"
        )
    rows: list[str] = []
    for row in board:
        verdict = str(row.get("VERDICT") or "")
        rows.append(
            "<tr>"
            f"<td>{_esc(row.get('STRATEGY'))}</td>"
            f"<td><span class='chip {_status_chip_class(verdict)}'>{_esc(verdict)}</span></td>"
            f"<td>{_esc(row.get('FAILED_GATE') or '—')}</td>"
            f"<td class='num'>{_esc(row.get('DEV_SIGNALS'))}</td>"
            f"<td class='num'>{_esc(row.get('OOS_SIGNALS'))}</td>"
            f"<td class='num'>{_esc(row.get('EXPECTED_NET'))}</td>"
            f"<td class='num'>{_esc(row.get('EXECUTION_NET'))}</td>"
            f"<td class='num'>{_esc(row.get('TOURNAMENT_SCORE'))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _research_data_status_rows(rds: dict[str, Any]) -> str:
    if not rds:
        return "<tr><td colspan='2' class='empty'>Geen operational status</td></tr>"
    order = (
        "CURRENT_STATE",
        "RECORDER_ENABLED",
        "RECORDER_RUNNING",
        "EVENTS_WRITTEN",
        "EVENTS_DROPPED",
        "WRITE_ERRORS",
        "QUEUE_DEPTH",
        "LAST_WRITE",
        "ACTIVE_DATASET",
        "DATASET_EVENT_COUNT",
        "DATASET_DURATION",
        "VENUES",
        "TIMESTAMP_COVERAGE",
        "FINAL_ACCEPTANCE_VERDICT",
    )
    rows: list[str] = []
    for key in order:
        val: Any = rds.get(key)
        if isinstance(val, float) and key == "DATASET_DURATION":
            val = f"{val:.1f}s"
        elif isinstance(val, (dict, list)):
            val = json.dumps(val, sort_keys=True)[:180]
        rows.append(
            "<tr>"
            f"<td>{_esc(key)}</td>"
            f"<td class='note'>{_esc(val)}</td>"
            "</tr>"
        )
    return "".join(rows)


def _market_data_lab_rows(panel: list[dict[str, Any]]) -> str:
    if not panel:
        return (
            "<tr><td colspan='9' class='empty'>"
            "Geen venue-data — recorder nog leeg"
            "</td></tr>"
        )
    rows: list[str] = []
    for row in panel:
        note = str(row.get("note") or row.get("quality_grade") or "")
        if len(note) > 72:
            note = note[:69] + "…"
        ex_cov = row.get("exchange_ts_coverage")
        if isinstance(ex_cov, (int, float)):
            ex_cov = f"{float(ex_cov) * 100:.0f}%"
        rows.append(
            "<tr>"
            f"<td>{_esc(row.get('venue'))}</td>"
            f"<td>{_esc(row.get('exchange_ts') or ('Ja' if row.get('exchange_ts_coverage') else '—'))}</td>"
            f"<td><span class='chip {_status_chip_class(row.get('quality') or row.get('quality_grade'))}'>"
            f"{_esc(row.get('quality') or row.get('quality_grade'))}</span></td>"
            f"<td class='num'>{_esc(row.get('events'))}</td>"
            f"<td class='num'>{_esc(ex_cov)}</td>"
            f"<td class='num'>{_esc(row.get('p50_ms'))}</td>"
            f"<td class='num'>{_esc(row.get('p95_ms'))}</td>"
            f"<td class='num'>{_esc(row.get('p99_ms'))}</td>"
            f"<td class='note'>{_esc(note)}</td>"
            "</tr>"
        )
    return "".join(rows)


def _research_finding_cards(cards: list[dict[str, Any]]) -> str:
    if not cards:
        return "<p class='forecast-note'>Nog geen research-conclusies geladen.</p>"
    parts: list[str] = []
    for card in cards:
        tone = str(card.get("tone") or "muted")
        parts.append(
            "<article class='finding-card'>"
            f"<div class='finding-top'><span class='finding-title'>{_esc(card.get('title'))}</span>"
            f"<span class='badge {tone}'>{_esc(card.get('verdict'))}</span></div>"
            f"<p class='finding-headline'>{_esc(card.get('headline'))}</p>"
            f"<p class='finding-detail'>{_esc(card.get('detail'))}</p>"
            "</article>"
        )
    return "".join(parts)


def _finding_list_items(items: list[Any]) -> str:
    if not items:
        return "<li>Geen findings in rapport.</li>"
    return "".join(f"<li>{_esc(x)}</li>" for x in items if x)


def _horizon_chips(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<span class='chip warn'>Geen horizon-scores</span>"
    parts: list[str] = []
    for row in rows:
        status = str(row.get("status") or "NOT_READY")
        parts.append(
            f"<span class='chip {_status_chip_class(status)}'>"
            f"{_esc(row.get('horizon'))}: {_esc(status)}</span>"
        )
    return "".join(parts)


def _status_chip_class(status: Any) -> str:
    text = str(status or "").upper()
    if text in {"READY", "HIGH", "SUPPORTED", "OK"}:
        return "ok"
    if "CAUTION" in text or "PARTIAL" in text or "MEDIUM" in text or "LOW" in text:
        return "warn"
    return "bad"


def _verdict_banner_class(verdict: Any) -> str:
    text = str(verdict or "").upper()
    if "READY_FOR_FAST" in text or "READY_FOR_SLOW" in text or (
        "READY_FOR" in text and "NOT" not in text and "PARTIAL" not in text
    ):
        return "ok"
    if "PARTIAL" in text or "RECORDING" in text or "CAUTION" in text:
        return "warn"
    return "bad"


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


def _route_state_rows(routes: dict[str, Any]) -> str:
    if not routes:
        return "<tr><td colspan='7' class='empty'>Nog geen route-samples</td></tr>"
    rows: list[str] = []
    for route, cell in sorted(
        routes.items(),
        key=lambda kv: -int((kv[1] or {}).get("n") or 0),
    ):
        if not isinstance(cell, dict):
            continue
        state = str(cell.get("state") or ("early_stopped" if cell.get("early_stop") else "—"))
        reason = cell.get("reason") or cell.get("detail") or ("—" if not cell.get("early_stop") else "early_raw_loss_overrides_shrinkage")
        rows.append(
            "<tr>"
            f"<td>{_esc(route)}</td>"
            f"<td><strong>{_esc(state)}</strong></td>"
            f"<td class='num'>{_esc(cell.get('n'))}</td>"
            f"<td class='num'>{_esc(cell.get('raw_capture') if cell.get('raw_capture') is not None else '—')}</td>"
            f"<td class='num'>{_esc(cell.get('shrunk_capture') if cell.get('shrunk_capture') is not None else '—')}</td>"
            f"<td class='num {_pnl_class(cell.get('sum_realized'))}'>"
            f"{_esc_fmt(cell.get('sum_realized'), 'money')}</td>"
            f"<td>{_esc(reason)}</td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan='7' class='empty'>Nog geen route-samples</td></tr>"


def _edge_route_rows(by_route: dict[str, Any]) -> str:
    if not by_route:
        return "<tr><td colspan='5' class='empty'>Nog geen afgeronde round-trips</td></tr>"
    rows: list[str] = []
    for route, cell in sorted(
        by_route.items(),
        key=lambda kv: -int((kv[1] or {}).get("n") or 0),
    ):
        if not isinstance(cell, dict):
            continue
        rows.append(
            "<tr>"
            f"<td>{_esc(route)}</td>"
            f"<td class='num'>{_esc(cell.get('n'))}</td>"
            f"<td class='num'>{_esc_fmt(cell.get('expected_net'), 'money')}</td>"
            f"<td class='num {_pnl_class(cell.get('realized_net'))}'>"
            f"{_esc_fmt(cell.get('realized_net'), 'money')}</td>"
            f"<td class='num'>{_esc(cell.get('ev_capture') if cell.get('ev_capture') is not None else '—')}</td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan='5' class='empty'>Nog geen afgeronde round-trips</td></tr>"


def _why_not_rows(why_not: dict[str, Any]) -> str:
    reasons = why_not.get("top_rejection_reasons") or []
    if not reasons:
        return "<tr><td colspan='2' class='empty'>Nog geen engine-rejects</td></tr>"
    rows: list[str] = []
    for item in reasons:
        if not isinstance(item, dict):
            continue
        rows.append(
            "<tr>"
            f"<td>{_esc(item.get('reason'))}</td>"
            f"<td class='num'>{_esc_fmt(item.get('count'), 'count')}</td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan='2' class='empty'>Nog geen engine-rejects</td></tr>"


def _gate_table_rows(table: list[Any]) -> str:
    if not table:
        return "<tr><td colspan='5' class='empty'>Nog geen gate-statistiek</td></tr>"
    rows: list[str] = []
    for item in table:
        if not isinstance(item, dict):
            continue
        rows.append(
            "<tr>"
            f"<td>{_esc(item.get('gate'))}</td>"
            f"<td class='num'>{_esc_fmt(item.get('rejections'), 'count')}</td>"
            f"<td class='num'>{_esc_fmt(item.get('estimated_good_rejections_eur'), 'money')}</td>"
            f"<td class='num'>{_esc_fmt(item.get('estimated_missed_profit_eur'), 'money')}</td>"
            f"<td>{_esc(item.get('recommendation'))}</td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan='5' class='empty'>Nog geen gate-statistiek</td></tr>"


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


def _equity_quote_rows(quotes: dict[str, Any]) -> str:
    if not quotes:
        return (
            "<tr><td colspan='5' class='empty'>"
            "Geen aandelenfeed (GLOBAL_EQUITY_ENABLED uit, of nog geen quote)"
            "</td></tr>"
        )
    rows: list[str] = []
    for symbol, payload in sorted(quotes.items()):
        if not isinstance(payload, dict):
            continue
        source = str(payload.get("source") or "—")
        rows.append(
            "<tr>"
            f"<td><strong>{_esc(symbol)}</strong></td>"
            f"<td class='num'>{_esc(payload.get('bid'))}</td>"
            f"<td class='num'>{_esc(payload.get('ask'))}</td>"
            f"<td class='num'>{_esc(payload.get('last'))}</td>"
            f"<td>{_esc(source)}</td>"
            "</tr>"
        )
    return "".join(rows) or (
        "<tr><td colspan='5' class='empty'>Geen aandelenfeed</td></tr>"
    )


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
        "nasdaq": "Nasdaq",
        "yahoo": "Yahoo",
        "equity_stub": "Equity stub",
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
        compact = capital.split(".")[0] if capital else str(row.get("label") or "?")[:8]
        labels.append(compact)
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


"""Live-only operator dashboard — portfolio, cash, net PnL, transactions."""

from __future__ import annotations

import html
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi.responses import HTMLResponse


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _eur(value: Any, *, signed: bool = False) -> str:
    amount = _dec(value)
    if amount is None:
        return "—"
    quantized = amount.quantize(Decimal("0.01"))
    text = f"{quantized:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if signed and quantized > 0:
        return f"+€{text}"
    if signed and quantized < 0:
        return f"-€{text[1:] if text.startswith('-') else text}"
    return f"€{text}"


def _free_eur_by_venue(observe: dict[str, Any]) -> dict[str, Decimal]:
    """Sum free EUR per venue from observe balances."""
    out: dict[str, Decimal] = {}
    for entry in observe.get("balances") or []:
        if not isinstance(entry, dict):
            continue
        nested = entry.get("balances") if isinstance(entry.get("balances"), list) else None
        rows = nested if nested is not None else ([entry] if entry.get("asset") else [])
        venue = str(entry.get("venue") or "").lower()
        for row in rows:
            if not isinstance(row, dict):
                continue
            asset = str(row.get("asset") or "").upper()
            if asset != "EUR":
                continue
            row_venue = str(row.get("venue") or venue or "unknown").lower() or "unknown"
            free = _dec(row.get("available") if row.get("available") is not None else row.get("free"))
            if free is None:
                free = _dec(row.get("total"))
            if free is None:
                continue
            prev = out.get(row_venue)
            out[row_venue] = free if prev is None else max(prev, free)
    return out


def _bitvavo_free_eur(observe: dict[str, Any]) -> Decimal | None:
    by_venue = _free_eur_by_venue(observe)
    if "bitvavo" in by_venue:
        return by_venue["bitvavo"]
    if len(by_venue) == 1:
        return next(iter(by_venue.values()))
    return None


def _nl_idle(hint: str) -> str:
    """Map machine idle codes to short Dutch operator lines."""
    raw = (hint or "").strip()
    if not raw:
        return "—"
    code = raw.split(" ", 1)[0]
    rest = raw[len(code) :].strip()
    labels = {
        "RISK_KILL_SWITCH_PAUSED": "Kill-switch gepauzeerd",
        "RISK_KILL_SWITCH_EMERGENCY_STOP": "Emergency stop actief",
        "DAILY_KILL": "Dagelijkse kill actief — geen nieuwe trades",
        "BUYS_BLOCKED_REGIME": "Buys geblokkeerd (regime)",
        "RESTING_ORDERS": "Openstaande orders op de beurs",
        "HOLDING_BELOW_COST": "Bags onder kostprijs — never-loss houdt vast",
        "SELLS_BLOCKED_NEVER_LOSS": "Sells geblokkeerd (never-loss / onder break-even)",
        "SELLS_BELOW_BREAK_EVEN": "Sells onder break-even (never-loss)",
        "WAITING_SOFT_ARM": "Wacht op soft-arm winstdrempel",
        "OVER_MAX_ALT_BASES": "Te veel alt-bases vast",
        "AT_MAX_ALT_BASES": "Max alt-bases bereikt (alleen bijvullen)",
        "FEES_EAT_EDGE": "Edge te klein na fees",
        "MOMENTUM_BLOCK": "Momentum-filter blokkeert",
        "CORR_GROUP_CAP": "Correlatie-groep vol",
        "POLICY_BLOCKED": "Policy blokkeert",
        "EXECUTION_ERROR": "Execution errors",
        "BUDGET_EXHAUSTED": "Budget op",
        "VENUE_CASH": "Vrije cash per venue",
        "SCANNING_NO_PASSING_EDGE": "Scant — geen edge die alle filters passeert",
        "SCANNING": "Scant / wacht op setup",
        "GESTOPTE SESSIE": "Sessie gestopt",
    }
    title = labels.get(code, code)
    return f"{title} — {rest}" if rest else title


def _nl_skip(reason: str) -> str:
    labels = {
        "sell_below_break_even": "sell onder break-even",
        "time_stop_below_be": "time-stop onder BE",
        "live_resting": "wacht op resting fill",
        "stale_quote_cancelled": "stale quote geannuleerd",
        "ladder_buy": "ladder buy skip",
        "orphan_open_cancelled": "orphan order cancelled",
        "policy_blocked": "policy",
        "execution_error": "execution error",
        "fees_eat_edge": "fees eten edge",
        "momentum_block": "momentum",
        "corr_group_cap": "corr-groep",
        "budget_exhausted": "budget",
        "venue_inventory": "venue inventory",
        "stale_edge": "stale edge",
    }
    return labels.get(reason, reason)


def _css() -> str:
    return """
    :root {
      --bg0: #0c1118;
      --bg1: #141c27;
      --text: #f3f6fa;
      --muted: #93a4bb;
      --good: #3ddc97;
      --bad: #ff6b6b;
      --line: #243247;
      --display: "Fraunces", "Iowan Old Style", Georgia, serif;
      --sans: "Sora", "Avenir Next", "Segoe UI", sans-serif;
      --mono: "IBM Plex Mono", ui-monospace, monospace;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      font-family: var(--sans);
      background:
        radial-gradient(900px 480px at 15% -10%, rgba(61,220,151,.12), transparent 55%),
        radial-gradient(700px 420px at 90% 0%, rgba(120,160,220,.10), transparent 50%),
        linear-gradient(165deg, #0a0e14 0%, var(--bg0) 45%, #101820 100%);
    }
    .wrap {
      max-width: 1100px;
      margin: 0 auto;
      padding: clamp(1.5rem, 4vw, 3rem) 1.25rem 2.5rem;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 1rem;
      margin-bottom: clamp(1.5rem, 4vw, 2.5rem);
    }
    .brand {
      margin: 0;
      font-family: var(--display);
      font-size: clamp(1.8rem, 4vw, 2.4rem);
      font-weight: 600;
      letter-spacing: -0.03em;
    }
    .status {
      font-size: .85rem;
      color: var(--muted);
      letter-spacing: .04em;
      text-transform: uppercase;
    }
    .status.on { color: var(--good); }
    .grid {
      display: grid;
      gap: 1rem;
      grid-template-columns: 1fr;
    }
    @media (min-width: 900px) {
      .grid { grid-template-columns: repeat(4, 1fr); }
    }
    @media (min-width: 600px) and (max-width: 899px) {
      .grid { grid-template-columns: repeat(2, 1fr); }
    }
    .card {
      background: color-mix(in srgb, var(--bg1) 88%, transparent);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 1.35rem 1.25rem 1.2rem;
      min-height: 9.5rem;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      backdrop-filter: blur(8px);
    }
    .label {
      margin: 0;
      color: var(--muted);
      font-size: .82rem;
      font-weight: 600;
      letter-spacing: .06em;
      text-transform: uppercase;
    }
    .value {
      margin: .85rem 0 0;
      font-family: var(--mono);
      font-size: clamp(1.55rem, 3.6vw, 2.15rem);
      font-weight: 600;
      letter-spacing: -0.03em;
      line-height: 1.1;
      word-break: break-word;
    }
    .value.good { color: var(--good); }
    .value.bad { color: var(--bad); }
    .hint {
      margin: .55rem 0 0;
      color: var(--muted);
      font-size: .8rem;
    }
    .positions {
      margin-top: 1.5rem;
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 1rem 1.1rem 1.15rem;
      background: color-mix(in srgb, var(--bg1) 88%, transparent);
      overflow-x: auto;
    }
    .positions h2 {
      margin: 0 0 .75rem;
      font-family: var(--sans);
      font-size: .82rem;
      font-weight: 600;
      letter-spacing: .06em;
      text-transform: uppercase;
      color: var(--muted);
    }
    table.pos {
      width: 100%;
      border-collapse: collapse;
      font-family: var(--mono);
      font-size: .82rem;
    }
    table.pos th, table.pos td {
      text-align: left;
      padding: .45rem .35rem;
      border-bottom: 1px solid var(--line);
      white-space: nowrap;
    }
    table.pos th { color: var(--muted); font-weight: 500; }
    table.pos td.good { color: var(--good); }
    table.pos td.bad { color: var(--bad); }
    ul.alerts {
      margin: .4rem 0 0;
      padding-left: 1.1rem;
      font-family: var(--mono);
      font-size: .8rem;
      color: var(--fg);
    }
    ul.alerts .kind {
      color: var(--muted);
      margin-right: .35rem;
    }
    footer {
      margin-top: 1.75rem;
      display: flex;
      flex-wrap: wrap;
      gap: .6rem;
    }
    .btn {
      appearance: none;
      border: 1px solid var(--line);
      background: transparent;
      color: var(--muted);
      border-radius: 999px;
      padding: .45rem .9rem;
      font: inherit;
      font-size: .82rem;
      cursor: pointer;
    }
    .btn:hover { color: var(--text); border-color: #3a4b63; }
    .idle-banner {
      margin: 0 0 1.25rem;
      padding: 1rem 1.15rem;
      border-radius: 14px;
      border: 1px solid #5a3a2a;
      background: linear-gradient(135deg, rgba(255,107,107,.14), rgba(20,28,39,.9));
    }
    .idle-banner.ok {
      border-color: #2a5a40;
      background: linear-gradient(135deg, rgba(61,220,151,.10), rgba(20,28,39,.9));
    }
    .idle-banner h2 {
      margin: 0 0 .45rem;
      font-size: .95rem;
      letter-spacing: .04em;
      text-transform: uppercase;
      color: #ffb4a8;
    }
    .idle-banner.ok h2 { color: var(--good); }
    .idle-banner .primary {
      margin: 0;
      font-family: var(--mono);
      font-size: .95rem;
      line-height: 1.35;
    }
    .idle-banner ul {
      margin: .55rem 0 0;
      padding-left: 1.1rem;
      color: var(--muted);
      font-family: var(--mono);
      font-size: .78rem;
    }
    .cash-grid {
      display: grid;
      gap: .6rem;
      grid-template-columns: 1fr 1fr;
      margin: 0 0 1.25rem;
    }
    .cash-grid .mini {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: .7rem .85rem;
      background: color-mix(in srgb, var(--bg1) 88%, transparent);
    }
    .cash-grid .mini .label { font-size: .72rem; }
    .cash-grid .mini .value { margin: .35rem 0 0; font-size: 1.15rem; }
    """


def render_live_dashboard(payload: dict[str, Any]) -> HTMLResponse:
    """Portfolio value, free cash, net session PnL, buy/sell transaction count."""
    session = payload.get("session") or {}
    observe = payload.get("observe") or {}
    bridge = session.get("bridge") or {}

    running = bool(session.get("running") or session.get("task_running"))
    free_by_observe = _free_eur_by_venue(observe)
    free = None
    if free_by_observe:
        free = sum(free_by_observe.values(), Decimal("0"))
    if free is None:
        free = _dec(bridge.get("free_quote_eur") or bridge.get("remaining_eur"))

    portfolio = _dec(session.get("portfolio_value_eur") or bridge.get("portfolio_value_eur"))
    if portfolio is None:
        # Fallback: observe total if it includes marks; else free EUR only.
        portfolio = _dec(observe.get("total_value_eur"))

    # Realized trade PnL after fees (FIFO vs session cost basis), not MTM.
    pnl = _dec(
        session.get("realized_trade_pnl_eur")
        or bridge.get("realized_trade_pnl_eur")
        or session.get("netto_winst_eur")
        or bridge.get("netto_winst_eur")
    )

    tx = session.get("live_transaction_count")
    if tx is None:
        tx = bridge.get("live_transaction_count")
    if tx is None:
        tx = session.get("live_fill_count") or bridge.get("live_fill_count")
    try:
        tx_n = int(tx or 0)
    except (TypeError, ValueError):
        tx_n = 0

    pnl_class = ""
    if pnl is not None and pnl > 0:
        pnl_class = "good"
    elif pnl is not None and pnl < 0:
        pnl_class = "bad"

    trail = bridge.get("trail_take_profit") or {}
    states = trail.get("states") or {}
    alerts = bridge.get("alerts") or trail.get("alerts") or []
    pos_rows = []
    for key, st in sorted(states.items()):
        if not isinstance(st, dict):
            continue
        venue = st.get("venue") or (str(key).split(":", 1)[0] if ":" in str(key) else "—")
        base = st.get("base") or (str(key).split(":", 1)[-1] if ":" in str(key) else key)
        gain = st.get("gain_pct") or "—"
        to_arm = st.get("pct_to_arm") or "—"
        soft = "ja" if st.get("soft_armed") else "nee"
        hard = "ja" if st.get("hard_armed") else "nee"
        partial = "ja" if st.get("partial_done") else "—"
        gain_cls = ""
        status = "—"
        status_cls = ""
        try:
            g = float(str(gain).replace(",", "."))
            if g > 0:
                gain_cls = "good"
            elif g < 0:
                gain_cls = "bad"
            if g < 0:
                status = "onder kost — houdt vast"
                status_cls = "bad"
            elif st.get("hard_armed"):
                status = "hard-armed — trail exit"
                status_cls = "good"
            elif st.get("soft_armed"):
                status = "soft-armed — partial/trail"
                status_cls = "good"
            else:
                status = f"wacht soft-arm (+{to_arm}%)"
        except ValueError:
            pass
        pos_rows.append(
            "<tr>"
            f"<td>{_esc(venue)}</td>"
            f"<td>{_esc(base)}</td>"
            f"<td class='{status_cls}'>{_esc(status)}</td>"
            f"<td>{_esc(st.get('cost') or '—')}</td>"
            f"<td>{_esc(st.get('mark') or '—')}</td>"
            f"<td class='{gain_cls}'>{_esc(gain)}%</td>"
            f"<td>{_esc(to_arm)}%</td>"
            f"<td>{_esc(soft)}/{_esc(hard)}</td>"
            f"<td>{_esc(st.get('soft_arm_pct') or '—')}/{_esc(st.get('hard_arm_pct') or '—')}</td>"
            f"<td>{_esc(st.get('peak') or '—')}</td>"
            f"<td>{_esc(partial)}</td>"
            f"<td>{_esc(st.get('session_qty') or '—')}</td>"
            f"<td>{_esc(st.get('age_sec') if st.get('age_sec') is not None else '—')}</td>"
            "</tr>"
        )
    if pos_rows:
        positions_html = (
            "<section class='positions'><h2>Posities / trail</h2>"
            "<p class='hint'>Winst% = mark vs kost (ongerealiseerd). Never-loss: geen sell onder break-even.</p>"
            "<table class='pos'><thead><tr>"
            "<th>Venue</th><th>Coin</th><th>Status</th><th>Cost</th><th>Mark</th><th>Winst%</th>"
            "<th>Tot arm</th><th>Soft/Hard</th><th>Arms%</th><th>Peak</th>"
            "<th>Partial</th><th>Sess qty</th><th>Age s</th>"
            "</tr></thead><tbody>"
            + "".join(pos_rows)
            + "</tbody></table></section>"
        )
    else:
        positions_html = (
            "<section class='positions'><h2>Posities / trail</h2>"
            "<p class='hint'>Nog geen session-buy inventory (pre-session bags niet getrailed)</p></section>"
        )

    alert_rows = []
    for a in list(alerts)[-8:]:
        if not isinstance(a, dict):
            continue
        alert_rows.append(
            f"<li><span class='kind'>{_esc(a.get('kind') or '')}</span> "
            f"{_esc(a.get('message') or '')}</li>"
        )
    kill = "aan" if trail.get("daily_kill_active") else "uit"
    funnel = session.get("pipeline_funnel") or {}
    cv = funnel.get("cross_venue") or {}
    cv_pairs = cv.get("pairs_evaluated", 0)
    cv_edges = cv.get("edges_found", 0)
    cv_emitted = cv.get("opportunities_emitted", 0)
    cv_prof_ok = cv.get("profitability_passed", 0)
    cv_prof_no = cv.get("profitability_rejected", 0)
    cv_risk_ok = cv.get("risk_passed", 0)
    cv_live = cv.get("live_orders", 0)
    cv_fills = cv.get("live_fills", 0)
    cv_rejects = cv.get("top_rejection_reasons") or []
    cv_reject_html = (
        "<ul class='alerts'>"
        + "".join(
            f"<li>{_esc(r.get('reason'))}: {_esc(r.get('count'))}</li>"
            for r in cv_rejects
            if isinstance(r, dict)
        )
        + "</ul>"
        if cv_rejects
        else "<p class='hint'>Nog geen cross-venue rejects geteld</p>"
    )
    cross_venue_html = (
        "<section class='positions'><h2>Cross-venue OKX ↔ Bitvavo</h2>"
        "<p class='hint'>Sessie-totalen: gezien vs door profitability / risk / live</p>"
        "<table class='pos'><thead><tr>"
        "<th>Pairs gescand</th><th>Edges</th><th>Kansen</th>"
        "<th>Profit ✓</th><th>Profit ✗</th><th>Risk ✓</th>"
        "<th>Live orders</th><th>Fills</th>"
        "</tr></thead><tbody><tr>"
        f"<td>{_esc(cv_pairs)}</td>"
        f"<td>{_esc(cv_edges)}</td>"
        f"<td>{_esc(cv_emitted)}</td>"
        f"<td class='good'>{_esc(cv_prof_ok)}</td>"
        f"<td class='bad'>{_esc(cv_prof_no)}</td>"
        f"<td>{_esc(cv_risk_ok)}</td>"
        f"<td>{_esc(cv_live)}</td>"
        f"<td>{_esc(cv_fills)}</td>"
        "</tr></tbody></table>"
        "<p class='hint'>Top strategy rejects (cross-venue)</p>"
        f"{cv_reject_html}"
        "</section>"
    )
    diag = bridge.get("diagnostics") or {}
    why = list(diag.get("why_idle") or [])
    skip_leaders = list(diag.get("skip_leaders") or [])
    # Per-venue free EUR: prefer last sync, fall back to observe.
    sync_by = bridge.get("last_sync_by_venue") or {}
    cash_cards = []
    for venue in ("bitvavo", "okx"):
        sync = sync_by.get(venue) or {}
        ledger = sync.get("ledger") or {}
        eur = (
            ledger.get("EUR")
            or sync.get("venue_budget_remaining")
            or sync.get("free_quote_eur")
            or free_by_observe.get(venue)
        )
        cash_cards.append(
            "<div class='mini'>"
            f"<p class='label'>{_esc(venue)} vrij EUR</p>"
            f"<p class='value'>{_esc(_eur(eur))}</p>"
            "</div>"
        )
    cash_html = (
        "<section class='cash-grid'>" + "".join(cash_cards) + "</section>"
        if cash_cards
        else ""
    )
    primary_raw = why[0] if why else ("SCANNING" if running else "GESTOPTE SESSIE")
    primary = _nl_idle(primary_raw)
    blocker_tokens = (
        "SELLS_BLOCKED",
        "SELLS_BELOW",
        "HOLDING_BELOW",
        "WAITING_SOFT",
        "RISK_KILL",
        "DAILY_KILL",
        "RESTING",
        "BUYS_BLOCKED",
        "EXECUTION_ERROR",
        "OVER_MAX",
        "AT_MAX",
    )
    idle_ok = running and not any(tok in primary_raw for tok in blocker_tokens)
    skip_li = ""
    for item in skip_leaders[:8]:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            skip_li += f"<li>{_esc(_nl_skip(str(item[0])))}: {_esc(item[1])}</li>"
        else:
            skip_li += f"<li>{_esc(item)}</li>"
    extra = "".join(f"<li>{_esc(_nl_idle(h))}</li>" for h in why[1:8])
    idle_banner = (
        f"<section class='idle-banner{' ok' if idle_ok else ''}'>"
        "<h2>Waarom nu stil / wat blokkeert</h2>"
        f"<p class='primary'>{_esc(primary)}</p>"
        + (
            "<ul>"
            + extra
            + (f"<li><em>Top skips deze sessie</em></li>{skip_li}" if skip_li else "")
            + "</ul>"
            if extra or skip_li
            else ""
        )
        + "</section>"
    )
    why_html = (
        "<section class='positions'><h2>Idle detail (codes)</h2>"
        + (
            "<ul class='alerts'>"
            + "".join(f"<li>{_esc(_nl_idle(h))} <span class='hint'>({_esc(h)})</span></li>" for h in why)
            + "</ul>"
            if why
            else "<p class='hint'>—</p>"
        )
        + (
            "<p class='hint'>Skip-counters (sessie)</p><ul class='alerts'>"
            + skip_li
            + "</ul>"
            if skip_li
            else ""
        )
        + "</section>"
    )

    trade_rows = []
    for t in list(diag.get("recent_live_trades") or [])[-8:]:
        if not isinstance(t, dict):
            continue
        trade_rows.append(
            "<tr>"
            f"<td>{_esc(t.get('symbol') or '')}</td>"
            f"<td>{_esc(t.get('side') or '')}</td>"
            f"<td>{_esc(t.get('requested_qty') or t.get('qty') or '')}</td>"
            f"<td>{_esc((t.get('result') or {}).get('order', {}).get('status') if isinstance(t.get('result'), dict) else t.get('status') or '')}</td>"
            "</tr>"
        )
    trades_html = (
        "<section class='positions'><h2>Recente live orders</h2>"
        + (
            "<table class='pos'><thead><tr><th>Symbol</th><th>Side</th>"
            "<th>Qty</th><th>Status</th></tr></thead><tbody>"
            + "".join(trade_rows)
            + "</tbody></table>"
            if trade_rows
            else "<p class='hint'>Nog geen live fills deze sessie</p>"
        )
        + "</section>"
    )
    alerts_html = (
        "<section class='positions'><h2>Alerts</h2>"
        f"<p class='hint'>Daily kill: {kill} · momentum="
        f"{'aan' if trail.get('momentum_enabled') else 'uit'} · "
        f"corr max {trail.get('max_per_corr_group') or '—'} · "
        f"soft/hard {trail.get('soft_arm_pct') or '—'}/{trail.get('hard_arm_pct') or '—'}</p>"
        + (
            "<ul class='alerts'>" + "".join(alert_rows) + "</ul>"
            if alert_rows
            else "<p class='hint'>Geen recente alerts</p>"
        )
        + "</section>"
    )

    html_doc = f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta http-equiv="refresh" content="5"/>
  <title>Moreney</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=IBM+Plex+Mono:wght@500;600&family=Sora:wght@500;600&display=swap" rel="stylesheet"/>
  <style>{_css()}</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1 class="brand">Moreney</h1>
      <p class="status {"on" if running else ""}">{("live" if running else "gestopt")}</p>
    </header>
    {idle_banner}
    {cash_html}
    <section class="grid">
      <article class="card">
        <p class="label">Portfolio</p>
        <p class="value">{_esc(_eur(portfolio))}</p>
        <p class="hint">EUR + crypto tegen marktprijs</p>
      </article>
      <article class="card">
        <p class="label">Vrij te besteden</p>
        <p class="value">{_esc(_eur(free))}</p>
        <p class="hint">Totaal vrij (zie per venue hierboven)</p>
      </article>
      <article class="card">
        <p class="label">Netto winst</p>
        <p class="value {pnl_class}">{_esc(_eur(pnl, signed=True))}</p>
        <p class="hint">Live FIFO gerealiseerd (na fees) — niet mark-to-market</p>
      </article>
      <article class="card">
        <p class="label">Transacties</p>
        <p class="value">{_esc(tx_n)}</p>
        <p class="hint">Elke buy of sell fill</p>
      </article>
    </section>
    {positions_html}
    {cross_venue_html}
    {why_html}
    {trades_html}
    {alerts_html}
    <footer>
      <button type="button" class="btn" onclick="post('/live/micro/session/start', {{minutes:null,budget_eur:2000,exclude_btc:true}})">Start</button>
      <button type="button" class="btn" onclick="post('/live/micro/session/stop')">Stop</button>
    </footer>
  </div>
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
    return HTMLResponse(content=html_doc)

"""Live-only operator dashboard — portfolio, cash, net PnL, transactions."""

from __future__ import annotations

import html
import json
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi.responses import HTMLResponse

from bot.live.dashboard_history import (
    chart_series_from_history,
    daily_realized_delta,
    load_history,
    recent_fills_for_display,
    today_portfolio_pnl,
    weekly_realized_delta,
    _calendar_pnl_for_payload,
)


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


def _format_fill_ts(raw: Any) -> str:
    if not raw:
        return "—"
    try:
        from datetime import datetime

        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return ts.astimezone().strftime("%H:%M:%S")
    except ValueError:
        return str(raw)[:19]


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
        "FOCUS_BASE_REQUIRED": "Nieuwe buy alleen op focus-coins",
        "ACTIVE_RING": "Active-book deploy (focus vs ring-target)",
        "VELOCITY_SLEEVE": "Velocity-sleeve (werkkapitaal + dag-verliescap)",
        "EXIT_ENGINE": "Exit-engine (soft-armed BE+ fill-seeking)",
        "EXIT_FILLS": "Exit fills (touch / taker / work)",
        "UNDERWATER_BASE_BLOCK": "Underwater base — geen nieuwe buy op die coin",
        "CORR_GROUP_CAP": "Correlatie-groep vol",
        "POLICY_BLOCKED": "Policy blokkeert",
        "EXECUTION_ERROR": "Execution errors",
        "BUDGET_EXHAUSTED": "Budget op",
        "VENUE_CASH": "Vrije cash per venue",
        "LONG_HOLD_OUTSIDE_MICRO": "Long-hold buiten micro-recycle",
        "MICRO_CAPITAL_LOCKED": "Vastgezet kapitaal (micro vs long-hold)",
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
        "focus_base_required": "alleen focus-coins",
        "underwater_base_block": "underwater base",
        "underwater_venue_block": "underwater venue (legacy)",
        "corr_group_cap": "corr-groep",
        "budget_exhausted": "budget",
        "venue_inventory": "venue inventory",
        "stale_edge": "stale edge",
        "sleeve_loss_cap": "velocity-sleeve verliescap",
        "exit_cooldown": "exit cooldown",
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
      padding:
        max(.85rem, env(safe-area-inset-top))
        max(.85rem, env(safe-area-inset-right))
        max(5.5rem, calc(4.5rem + env(safe-area-inset-bottom)))
        max(.85rem, env(safe-area-inset-left));
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: .75rem;
      margin-bottom: .85rem;
    }
    .brand {
      margin: 0;
      font-family: var(--display);
      font-size: clamp(1.35rem, 5vw, 2.4rem);
      font-weight: 600;
      letter-spacing: -0.03em;
    }
    .status {
      font-size: .72rem;
      color: var(--muted);
      letter-spacing: .06em;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .status.on { color: var(--good); }
    .dash-top {
      display: flex;
      flex-direction: column;
      gap: .85rem;
      margin-bottom: .5rem;
    }
    .grid-kpi {
      display: grid;
      gap: .65rem;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    @media (min-width: 720px) {
      .grid-kpi { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .85rem; }
    }
    @media (min-width: 1024px) {
      .grid-kpi { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    }
    .grid {
      display: grid;
      gap: 1rem;
      grid-template-columns: 1fr;
    }
    @media (min-width: 900px) {
      .grid { grid-template-columns: repeat(3, 1fr); }
    }
    .card {
      background: color-mix(in srgb, var(--bg1) 88%, transparent);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: .85rem .8rem .75rem;
      min-height: 0;
      display: flex;
      flex-direction: column;
      justify-content: flex-start;
      backdrop-filter: blur(8px);
    }
    .card.hero {
      border-color: color-mix(in srgb, #f0b429 35%, var(--line));
      background: color-mix(in srgb, #f0b429 6%, var(--bg1));
    }
    .card.hero-a {
      border-color: color-mix(in srgb, var(--good) 35%, var(--line));
      background: color-mix(in srgb, var(--good) 6%, var(--bg1));
    }
    .pnl-split {
      display: grid;
      gap: .65rem;
      grid-template-columns: 1fr 1fr;
      margin-bottom: .15rem;
    }
    @media (min-width: 720px) {
      .pnl-split { grid-template-columns: 1fr 1fr 1fr 1fr; gap: .85rem; }
    }
    .pnl-split .card.split-harvest {
      border-color: color-mix(in srgb, var(--good) 40%, var(--line));
      background: linear-gradient(135deg, color-mix(in srgb, var(--good) 10%, var(--bg1)), color-mix(in srgb, var(--bg1) 92%, transparent));
    }
    .pnl-split .card.split-open {
      border-color: color-mix(in srgb, #f0b429 35%, var(--line));
      background: linear-gradient(135deg, color-mix(in srgb, #f0b429 8%, var(--bg1)), color-mix(in srgb, var(--bg1) 92%, transparent));
    }
    .pnl-split .card.split-portfolio {
      border-color: color-mix(in srgb, #5b9fd4 40%, var(--line));
      background: linear-gradient(135deg, color-mix(in srgb, #5b9fd4 10%, var(--bg1)), color-mix(in srgb, var(--bg1) 92%, transparent));
    }
    .pnl-split .card.split-winnable {
      grid-column: 1 / -1;
    }
    @media (min-width: 720px) {
      .pnl-split .card.split-winnable { grid-column: auto; }
    }
    .pnl-split .value { font-size: clamp(1.35rem, 4.5vw, 1.85rem); }
    .pnl-split-intro {
      margin: 0 0 .35rem;
      color: var(--muted);
      font-size: .78rem;
      line-height: 1.4;
    }
    @media (min-width: 720px) {
      .card { padding: 1.1rem 1rem 1rem; border-radius: 18px; }
    }
    .label {
      margin: 0;
      color: var(--muted);
      font-size: .68rem;
      font-weight: 600;
      letter-spacing: .05em;
      text-transform: uppercase;
      line-height: 1.25;
    }
    @media (min-width: 720px) {
      .label { font-size: .78rem; letter-spacing: .06em; }
    }
    .value {
      margin: .45rem 0 0;
      font-family: var(--mono);
      font-size: clamp(1.15rem, 4.8vw, 2rem);
      font-weight: 600;
      letter-spacing: -0.03em;
      line-height: 1.1;
      word-break: break-word;
    }
    .value.good { color: var(--good); }
    .value.bad { color: var(--bad); }
    .hint {
      margin: .35rem 0 0;
      color: var(--muted);
      font-size: .68rem;
      line-height: 1.3;
    }
    @media (min-width: 720px) {
      .hint { font-size: .76rem; margin-top: .45rem; }
    }
    .target-band {
      margin: 0;
      padding: .75rem .85rem;
      border-radius: 14px;
      border: 1px solid color-mix(in srgb, #f0b429 35%, var(--line));
      background: color-mix(in srgb, #f0b429 8%, var(--bg1));
    }
    .target-band h2 {
      margin: 0 0 .35rem;
      font-size: .78rem;
      font-weight: 600;
      letter-spacing: .04em;
      text-transform: uppercase;
      color: #f0b429;
    }
    .target-band p {
      margin: 0;
      font-size: .78rem;
      line-height: 1.35;
      color: var(--muted);
    }
    @media (min-width: 720px) {
      .target-band { padding: 1rem 1.15rem; border-radius: 16px; }
      .target-band h2 { font-size: .88rem; }
      .target-band p { font-size: .85rem; line-height: 1.45; }
    }
    .target-band .band-row {
      display: flex;
      flex-wrap: wrap;
      gap: .75rem 1.5rem;
      margin-top: .65rem;
      font-family: var(--mono);
      font-size: .82rem;
    }
    .target-band .band-row span { color: var(--text); }
    .target-band .in-band { color: var(--good); }
    .target-band .out-band { color: var(--muted); }
    .portfolio-strip {
      margin: 0;
      padding: .75rem .85rem;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: color-mix(in srgb, var(--bg1) 92%, transparent);
    }
    .portfolio-strip h2 {
      margin: 0 0 .55rem;
      font-size: .78rem;
      font-weight: 600;
      letter-spacing: .04em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .hold-list {
      display: flex;
      flex-wrap: wrap;
      gap: .45rem .55rem;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .hold-item {
      display: inline-flex;
      align-items: center;
      gap: .35rem;
      padding: .35rem .55rem;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: color-mix(in srgb, var(--bg0) 55%, transparent);
      font-family: var(--mono);
      font-size: .78rem;
    }
    .hold-item .mom {
      font-size: .85rem;
      font-weight: 600;
      line-height: 1;
      min-width: .85rem;
      text-align: center;
    }
    .hold-item .mom-up { color: var(--good); }
    .hold-item .mom-down { color: var(--bad); }
    .hold-item .mom-flat { color: var(--muted); }
    .hold-item .coin { font-weight: 600; color: var(--text); }
    .hold-item .amt { color: var(--muted); font-size: .72rem; }
    .hold-item .venue {
      color: var(--muted);
      font-size: .65rem;
      text-transform: lowercase;
    }
    .portfolio-empty {
      margin: 0;
      color: var(--muted);
      font-size: .78rem;
    }
    @media (min-width: 720px) {
      .portfolio-strip { padding: 1rem 1.15rem; border-radius: 16px; }
      .hold-item { font-size: .82rem; padding: .4rem .65rem; }
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
    .tag {
      display: inline-block;
      border-radius: 999px;
      padding: .1rem .45rem;
      font-size: .68rem;
      letter-spacing: .03em;
      text-transform: uppercase;
      border: 1px solid var(--line);
      color: var(--muted);
    }
    .tag.long-hold {
      border-color: #4a5a78;
      color: #b8c7de;
    }
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
      position: fixed;
      left: 0;
      right: 0;
      bottom: 0;
      z-index: 20;
      margin: 0;
      padding: .55rem max(.85rem, env(safe-area-inset-right))
        max(.55rem, env(safe-area-inset-bottom))
        max(.85rem, env(safe-area-inset-left));
      display: flex;
      flex-wrap: wrap;
      gap: .5rem;
      justify-content: center;
      background: color-mix(in srgb, var(--bg0) 92%, transparent);
      border-top: 1px solid var(--line);
      backdrop-filter: blur(10px);
    }
    @media (min-width: 900px) {
      footer {
        position: static;
        margin-top: 1.5rem;
        padding: 0;
        background: transparent;
        border-top: 0;
        backdrop-filter: none;
        justify-content: flex-start;
      }
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
    .cash-grid {
      display: grid;
      gap: .5rem;
      grid-template-columns: 1fr 1fr;
      margin: 0;
    }
    .cash-grid .mini {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: .55rem .65rem;
      background: color-mix(in srgb, var(--bg1) 88%, transparent);
    }
    .cash-grid .mini .label { font-size: .65rem; }
    .cash-grid .mini .value { margin: .25rem 0 0; font-size: 1rem; }
    .charts {
      display: flex;
      flex-direction: column;
      gap: .65rem;
      margin: 0;
    }
    @media (min-width: 900px) {
      .charts {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 1rem;
      }
    }
    .chart-card {
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: .75rem .75rem .65rem;
      background: color-mix(in srgb, var(--bg1) 88%, transparent);
    }
    @media (min-width: 720px) {
      .chart-card { padding: 1rem; border-radius: 18px; }
    }
    .chart-card h2 {
      margin: 0 0 .5rem;
      font-size: .68rem;
      font-weight: 600;
      letter-spacing: .06em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .chart-wrap {
      position: relative;
      height: clamp(170px, 46vw, 240px);
      min-height: 170px;
    }
    @media (min-width: 900px) {
      .chart-wrap { height: min(42vw, 220px); min-height: 180px; }
    }
    .chart-wrap canvas { width: 100% !important; height: 100% !important; }
    .dash-secondary {
      display: flex;
      flex-direction: column;
      gap: .85rem;
      margin-top: .85rem;
    }
    .idle-banner {
      margin: 0;
      padding: .75rem .85rem;
      border-radius: 14px;
      border: 1px solid #5a3a2a;
      background: linear-gradient(135deg, rgba(255,107,107,.14), rgba(20,28,39,.9));
    }
    .idle-banner.ok {
      border-color: #2a5a40;
      background: linear-gradient(135deg, rgba(61,220,151,.10), rgba(20,28,39,.9));
    }
    .idle-banner.stale {
      border-color: #7a5a20;
      background: linear-gradient(135deg, rgba(240,180,41,.16), rgba(20,28,39,.9));
    }
    .idle-banner h2 {
      margin: 0 0 .35rem;
      font-size: .78rem;
      letter-spacing: .04em;
      text-transform: uppercase;
      color: #ffb4a8;
    }
    .idle-banner.ok h2 { color: var(--good); }
    .idle-banner.stale h2 { color: #f0b429; }
    .idle-banner .primary {
      margin: 0;
      font-family: var(--mono);
      font-size: .82rem;
      line-height: 1.35;
    }
    .idle-banner ul {
      margin: .45rem 0 0;
      padding-left: 1.1rem;
      color: var(--muted);
      font-family: var(--mono);
      font-size: .72rem;
    }
    .install-banner {
      display: none;
      margin: 0;
      padding: .75rem .85rem;
      border-radius: 14px;
      border: 1px dashed color-mix(in srgb, var(--good) 45%, var(--line));
      background: color-mix(in srgb, var(--good) 8%, transparent);
      font-size: .82rem;
    }
    .install-banner.show { display: flex; align-items: center; justify-content: space-between; gap: .75rem; flex-wrap: wrap; }
    .install-banner button {
      border: 0;
      border-radius: 999px;
      padding: .45rem .9rem;
      background: var(--good);
      color: #062015;
      font-weight: 600;
      cursor: pointer;
    }
    details.fold {
      margin: 0;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: color-mix(in srgb, var(--bg1) 88%, transparent);
    }
    details.fold > summary {
      cursor: pointer;
      padding: .75rem .85rem;
      font-weight: 600;
      font-size: .85rem;
      list-style: none;
    }
    details.fold > summary::-webkit-details-marker { display: none; }
    details.fold .fold-body { padding: 0 .85rem .85rem; }
    .updated-at {
      margin: 0;
      color: var(--muted);
      font-size: .68rem;
      font-family: var(--mono);
      text-align: center;
    }
    @media (min-width: 720px) {
      .updated-at { font-size: .72rem; text-align: left; }
    }
    """


def _chart_bootstrap(history: list[dict[str, Any]]) -> str:
    """JSON for initial Chart.js datasets (safe in script tag)."""
    return json.dumps(chart_series_from_history(history))


def _render_portfolio_strip(holdings: list[Any]) -> str:
    """Simple held-items row with momentum arrows."""
    if not holdings:
        return (
            "<section class='portfolio-strip' id='portfolio-strip'>"
            "<h2>Portfolio</h2>"
            "<p class='portfolio-empty'>Geen posities — alle cash</p>"
            "</section>"
        )
    chips: list[str] = []
    for row in holdings:
        if not isinstance(row, dict):
            continue
        base = str(row.get("base") or "—")
        venue = str(row.get("venue") or "")
        notional = _eur(row.get("notional_eur"))
        direction = str(row.get("momentum_direction") or "flat")
        arrow = str(row.get("momentum_arrow") or "→")
        ret = row.get("momentum_return_pct")
        title = f"momentum {ret}%" if ret is not None else "momentum —"
        role = str(row.get("role") or "")
        venue_tag = f"<span class='venue'>{_esc(venue)}</span>" if venue else ""
        chips.append(
            "<li class='hold-item'>"
            f"<span class='mom mom-{direction}' title='{_esc(title)}'>{_esc(arrow)}</span>"
            f"<span class='coin'>{_esc(base)}</span>"
            f"{venue_tag}"
            f"<span class='amt'>{_esc(notional)}</span>"
            + (
                "<span class='venue'>long-hold</span>"
                if role == "long_hold"
                else ""
            )
            + "</li>"
        )
    return (
        "<section class='portfolio-strip' id='portfolio-strip'>"
        "<h2>Portfolio</h2>"
        f"<ul class='hold-list' id='portfolio-holdings'>{''.join(chips)}</ul>"
        "</section>"
    )


def render_live_dashboard(payload: dict[str, Any]) -> HTMLResponse:
    """Portfolio value, free cash, net session PnL, buy/sell transaction count."""
    session = payload.get("session") or {}
    observe = payload.get("observe") or {}
    bridge = session.get("bridge") or {}

    task_running = session.get("task_running")
    if task_running is None:
        running = bool(session.get("running"))
    else:
        running = bool(task_running)
    stale = bool(session.get("stale"))
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
    session_start_realized = _dec(
        bridge.get("session_start_realized_eur")
        or session.get("session_start_realized_eur")
    )
    session_realized = None
    if pnl is not None and session_start_realized is not None:
        session_realized = pnl - session_start_realized
    elif pnl is not None and session_start_realized is None:
        session_realized = pnl

    tx = (
        session.get("session_live_transaction_count")
        or bridge.get("session_live_transaction_count")
        or session.get("live_transaction_count")
        or bridge.get("live_transaction_count")
    )
    if tx is None:
        tx = session.get("live_fill_count") or bridge.get("live_fill_count")
    try:
        tx_n = int(tx or 0)
    except (TypeError, ValueError):
        tx_n = 0
    backfill_n = int(bridge.get("backfill_mirrored_count") or 0)
    diag = bridge.get("diagnostics") or {}
    unrealized = _dec(
        bridge.get("unrealized_mtm_eur") or diag.get("unrealized_mtm_eur")
    )
    winnable = _dec(
        bridge.get("winnable_mtm_eur")
        or diag.get("winnable_mtm_eur")
    )
    blocked_sells = int(
        bridge.get("blocked_sells_session")
        or diag.get("blocked_sells_session")
        or 0
    )
    locked_notional = _dec(
        bridge.get("locked_notional_eur") or diag.get("locked_notional_eur")
    )
    micro_locked = _dec(
        bridge.get("micro_locked_notional_eur") or diag.get("micro_locked_notional_eur")
    )
    long_hold_locked = _dec(
        bridge.get("long_hold_notional_eur") or diag.get("long_hold_notional_eur")
    )
    holdings = bridge.get("portfolio_holdings") or []

    pnl_class = ""
    if pnl is not None and pnl > 0:
        pnl_class = "good"
    elif pnl is not None and pnl < 0:
        pnl_class = "bad"
    session_realized_class = ""
    if session_realized is not None and session_realized > 0:
        session_realized_class = "good"
    elif session_realized is not None and session_realized < 0:
        session_realized_class = "bad"
    hist_for_kpi = payload.get("history") or load_history(limit=720)
    if not isinstance(hist_for_kpi, list):
        hist_for_kpi = []
    daily_realized, weekly_realized, pnl_source = _calendar_pnl_for_payload(
        payload, current_realized=pnl
    )
    portfolio_pnl = today_portfolio_pnl(
        portfolio=portfolio,
        session_pnl=None,  # filled below once start_portfolio known
        daily_realized=daily_realized,
        history=hist_for_kpi,
    )
    start_portfolio = _dec(
        session.get("starting_portfolio_eur") or bridge.get("starting_portfolio_eur")
    )
    session_portfolio_pnl = (
        (portfolio - start_portfolio)
        if portfolio is not None and start_portfolio is not None
        else None
    )
    if portfolio_pnl is None and session_portfolio_pnl is not None:
        portfolio_pnl = session_portfolio_pnl
    daily_realized_class = ""
    if daily_realized is not None and daily_realized > 0:
        daily_realized_class = "good"
    elif daily_realized is not None and daily_realized < 0:
        daily_realized_class = "bad"
    portfolio_pnl_class = ""
    if portfolio_pnl is not None and portfolio_pnl > 0:
        portfolio_pnl_class = "good"
    elif portfolio_pnl is not None and portfolio_pnl < 0:
        portfolio_pnl_class = "bad"
    session_portfolio_class = ""
    if session_portfolio_pnl is not None and session_portfolio_pnl > 0:
        session_portfolio_class = "good"
    elif session_portfolio_pnl is not None and session_portfolio_pnl < 0:
        session_portfolio_class = "bad"
    weekly_realized_class = ""
    if weekly_realized is not None and weekly_realized > 0:
        weekly_realized_class = "good"
    elif weekly_realized is not None and weekly_realized < 0:
        weekly_realized_class = "bad"
    winn_class = ""
    if winnable is not None and winnable > 0:
        winn_class = "good"
    elif winnable is not None and winnable < 0:
        winn_class = "bad"
    unreal_class = ""
    if unrealized is not None and unrealized > 0:
        unreal_class = "good"
    elif unrealized is not None and unrealized < 0:
        unreal_class = "bad"

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
        role = str(st.get("role") or "micro_recycle")
        notional = st.get("notional_eur") or "—"
        winn = st.get("winnable_eur") or "—"
        soft = "ja" if st.get("soft_armed") else "nee"
        hard = "ja" if st.get("hard_armed") else "nee"
        partial = "ja" if st.get("partial_done") else "—"
        gain_cls = ""
        status = "—"
        status_cls = ""
        winn_cls = ""
        w = 0.0
        try:
            w = float(str(winn).replace(",", "."))
            if w > 0:
                winn_cls = "good"
            elif w < 0:
                winn_cls = "bad"
        except ValueError:
            pass
        try:
            g = float(str(gain).replace(",", "."))
            if g > 0:
                gain_cls = "good"
            elif g < 0:
                gain_cls = "bad"
            if role == "long_hold":
                status = "long-hold — buiten micro-recycle"
                status_cls = ""
            elif w > 0:
                status = "boven BE — harvestbaar"
                status_cls = "good"
            elif st.get("new_session_base") and g < 0:
                status = "nieuwe base — cut-loss −4% BE"
                status_cls = "bad"
            elif g < 0:
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
        role_tag = (
            "<span class='tag long-hold'>long-hold</span>"
            if role == "long_hold"
            else "<span class='tag'>micro</span>"
        )
        pos_rows.append(
            "<tr>"
            f"<td>{_esc(venue)}</td>"
            f"<td>{_esc(base)} {role_tag}</td>"
            f"<td class='{status_cls}'>{_esc(status)}</td>"
            f"<td>{_esc(_eur(notional))}</td>"
            f"<td class='{winn_cls}'>{_esc(_eur(winn, signed=True))}</td>"
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
            "<p class='hint'>Winnable = winst boven fee-aware break-even (nu verkoopbaar). Onder BE = €0.</p>"
            "<table class='pos'><thead><tr>"
            "<th>Venue</th><th>Coin</th><th>Status</th><th>Vast €</th><th>Winnable €</th>"
            "<th>Cost</th><th>Mark</th><th>Winst%</th>"
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
        "LONG_HOLD",
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
    updated_raw = session.get("updated_at")
    stale_banner = ""
    if stale:
        stale_banner = (
            "<section class='idle-banner stale' id='stale-banner'>"
            "<h2>Cijfers niet actueel</h2>"
            "<p class='primary'>Sessie-loop staat stil — portfolio/PnL zijn bevroren. "
            f"Laatst bijgewerkt: {_esc(updated_raw or 'onbekend')}. Druk op Start of herstart de bot.</p>"
            "</section>"
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
    fill_rows = []
    recent_fills = recent_fills_for_display(diag, limit=8)
    for f in recent_fills:
        side = str(f.get("side") or "").lower()
        side_cls = "good" if side == "sell" else ""
        fill_rows.append(
            "<tr>"
            f"<td>{_esc(_format_fill_ts(f.get('ts')))}</td>"
            f"<td>{_esc(f.get('venue') or '')}</td>"
            f"<td>{_esc(f.get('symbol') or '')}</td>"
            f"<td class='{side_cls}'>{_esc(f.get('side') or '')}</td>"
            f"<td>{_esc(f.get('qty') or '')}</td>"
            f"<td>{_esc(f.get('price') or '')}</td>"
            f"<td>{_esc(_eur(f.get('notional_eur')))}</td>"
            f"<td>{_esc(f.get('source') or '')}</td>"
            "</tr>"
        )
    last_fill = recent_fills[-1] if recent_fills else None
    last_fill_html = ""
    if last_fill:
        last_fill_html = (
            "<section class='idle-banner ok' id='last-fill-banner'>"
            "<h2>Laatste fill (exchange)</h2>"
            f"<p class='primary'>{_esc(_format_fill_ts(last_fill.get('ts')))} · "
            f"{_esc(last_fill.get('venue') or '')} · "
            f"{_esc(last_fill.get('symbol') or '')} · "
            f"{_esc(str(last_fill.get('side') or '').upper())} · "
            f"{_esc(last_fill.get('qty') or '')} @ €{_esc(last_fill.get('price') or '')} · "
            f"{_esc(_eur(last_fill.get('notional_eur')))}</p>"
            "<p class='hint'>Orders ≠ fills — Bitvavo kan vullen terwijl status 'cancelled' staat.</p>"
            "</section>"
        )
    fills_html = (
        "<section class='positions'><h2>Recente fills (exchange)</h2>"
        + (
            "<table class='pos'><thead><tr><th>Tijd</th><th>Venue</th><th>Symbol</th>"
            "<th>Side</th><th>Qty</th><th>Prijs</th><th>Notional</th><th>Bron</th>"
            "</tr></thead><tbody>"
            + "".join(fill_rows)
            + "</tbody></table>"
            if fill_rows
            else "<p class='hint'>Nog geen fills gespiegeld — reconcile draait elke ~30s</p>"
        )
        + "</section>"
    )
    trades_html = (
        "<section class='positions'><h2>Recente live orders (status)</h2>"
        + (
            "<table class='pos'><thead><tr><th>Symbol</th><th>Side</th>"
            "<th>Qty</th><th>Status</th></tr></thead><tbody>"
            + "".join(trade_rows)
            + "</tbody></table>"
            if trade_rows
            else "<p class='hint'>Nog geen live order-pogingen deze sessie</p>"
        )
        + "<p class='hint'>Submitted/cancelled hier ≠ geen fill — zie fills hierboven.</p>"
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

    history = payload.get("history") or hist_for_kpi or load_history(limit=720)
    chart_json = _chart_bootstrap(history if isinstance(history, list) else [])

    target_low = Decimal("20")
    target_high = Decimal("50")
    in_target_band = (
        daily_realized is not None and target_low <= daily_realized <= target_high
    )
    band_class = "in-band" if in_target_band else "out-band"
    target_band_html = (
        "<section class='target-band' aria-label='Doelband onderzoek'>"
        "<h2>Doel €20–50/dag netto</h2>"
        "<p><strong>Geïnd vandaag</strong> = verkochte coins (FIFO) · "
        "<strong>Open</strong> = totaal unrealized op bags · "
        "<strong>Portfolio-winst</strong> = equity Δ sinds 00:00 NL · "
        "<strong>Winnable</strong> = boven BE, nog niet verkocht.</p>"
        "<div class='band-row'>"
        f"<span>Geïnd: <strong class='{band_class}'>"
        f"{_esc(_eur(daily_realized, signed=True) if daily_realized is not None else '—')}</strong></span>"
        f"<span>Open: {_esc(_eur(unrealized, signed=True) if unrealized is not None else '—')}</span>"
        f"<span>Portfolio-winst: {_esc(_eur(portfolio_pnl, signed=True) if portfolio_pnl is not None else '—')}</span>"
        f"<span>Week geïnd: {_esc(_eur(weekly_realized, signed=True) if weekly_realized is not None else '—')}</span>"
        f"<span>Winnable: {_esc(_eur(winnable, signed=True) if winnable is not None else '—')}</span>"
    )
    win_gap = _dec(bridge.get("winnable_gap_eur") or diag.get("winnable_gap_eur"))
    if win_gap is not None and win_gap > 0:
        target_band_html += (
            f"<span class='warn'>Gap: {_esc(_eur(win_gap, signed=True))} "
            f"niet gecashd — exit fills checken</span>"
        )
    target_band_html += "</div></section>"

    exit_eng = bridge.get("exit_engine") or {}
    exit_quotes = exit_eng.get("quotes") or {}
    exit_fills = exit_eng.get("fills") or {}
    exit_pending = exit_eng.get("pending") or {}
    exit_rejects = exit_eng.get("rejects") or {}

    def _sum_quote_keys(d: dict) -> int:
        return sum(
            int(v or 0)
            for k, v in d.items()
            if not str(k).startswith("reason:")
        )

    touch_q = int(exit_quotes.get("rest_touch_maker", 0) or 0)
    hit_q = int(exit_quotes.get("hit_bid_taker", 0) or 0)
    lim_q = int(exit_quotes.get("limit_taker_be", 0) or 0)
    work_fills = int(exit_fills.get("reason:trail_exit_work", 0) or 0)
    harvest_fills = int(exit_fills.get("reason:trail_be_harvest", 0) or 0)
    sleeve_pnl = _dec(bridge.get("sleeve_realized_eur"))
    sleeve_cap = _dec(bridge.get("sleeve_daily_loss_cap_eur"))
    sleeve_paused = bool(bridge.get("sleeve_paused"))
    ring_by = bridge.get("active_book_notional_by_venue") or {}
    ring_target = _dec(bridge.get("active_ring_eur") or bridge.get("velocity_sleeve_eur"))
    ring_bits = []
    for vn in ("bitvavo", "okx"):
        active = _dec(ring_by.get(vn))
        ring_bits.append(
            f"{vn} {_esc(_eur(active))}/{_esc(_eur(ring_target))}"
        )
    sleeve_cls = ""
    if sleeve_pnl is not None and sleeve_pnl > 0:
        sleeve_cls = "good"
    elif sleeve_pnl is not None and sleeve_pnl < 0:
        sleeve_cls = "bad"
    obs_html = (
        "<section class='target-band' aria-label='Exit-engine & sleeve'>"
        "<h2>Exit-engine &amp; velocity sleeve</h2>"
        "<div class='band-row'>"
        f"<span>Quotes: touch={touch_q} · hit_bid={hit_q} · lim_taker={lim_q} "
        f"(totaal {_sum_quote_keys(exit_quotes)})</span>"
        f"<span>Fills: work={work_fills} · harvest={harvest_fills} · "
        f"all={_sum_quote_keys(exit_fills)} · pend={_sum_quote_keys(exit_pending)} · "
        f"rej={_sum_quote_keys(exit_rejects)}</span>"
        "</div>"
        "<div class='band-row'>"
        f"<span>Sleeve PnL: <strong class='{sleeve_cls}'>"
        f"{_esc(_eur(sleeve_pnl, signed=True) if sleeve_pnl is not None else '—')}</strong>"
        f" · cap −{_esc(_eur(sleeve_cap))}"
        f"{' · PAUSED' if sleeve_paused else ''}</span>"
        f"<span>Ring: {' · '.join(ring_bits) if ring_bits else '—'}</span>"
        "</div></section>"
    )
    eq_diag = bridge.get("diagnostics") or {}
    eq_candidates = eq_diag.get("entry_quality_candidates")
    eq_html = (
        "<section class='target-band' aria-label='Entry quality'>"
        "<h2>Entry quality</h2>"
        "<div class='band-row'>"
        f"<span>Candidates: <strong>{_esc(eq_candidates if eq_candidates is not None else '—')}</strong></span>"
        f"<span>Normal: {_esc(eq_diag.get('entry_quality_normal', '—'))}</span>"
        f"<span>Reduced: {_esc(eq_diag.get('entry_quality_reduced', '—'))}</span>"
        f"<span>Rejected: {_esc(eq_diag.get('entry_quality_rejected', '—'))}</span>"
        "</div>"
        "<div class='band-row'>"
        f"<span>Avg headroom: {_esc(eq_diag.get('average_headroom_pct', '—'))}</span>"
        f"<span>Avg extension: {_esc(eq_diag.get('average_extension_pct', '—'))}</span>"
        f"<span>Avg required move: {_esc(eq_diag.get('average_required_move_pct', '—'))}</span>"
        f"<span>Avg quality: {_esc(eq_diag.get('average_entry_quality', '—'))}</span>"
        "</div>"
        "<div class='band-row'>"
        f"<span>Headroom rejects: {_esc(eq_diag.get('headroom_reject', '—'))}</span>"
        f"<span>Extension rejects: {_esc(eq_diag.get('extension_reject', '—'))}</span>"
        f"<span>Continuity rejects: {_esc(eq_diag.get('continuity_reject', '—'))}</span>"
        f"<span>Headroom unknown: {_esc(eq_diag.get('headroom_unknown', '—'))}</span>"
        "</div></section>"
    )
    opp_html = (
        "<section class='target-band' aria-label='Opportunity engine'>"
        "<h2>Opportunity engine</h2>"
        "<div class='band-row'>"
        f"<span>Candidates: <strong>{_esc(eq_diag.get('opportunity_candidates', '—'))}</strong></span>"
        f"<span>High quality: {_esc(eq_diag.get('opportunity_high_quality', '—'))}</span>"
        f"<span>Reduced: {_esc(eq_diag.get('opportunity_reduced', '—'))}</span>"
        f"<span>Rejected: {_esc(eq_diag.get('opportunity_rejected', '—'))}</span>"
        "</div>"
        "<div class='band-row'>"
        f"<span>Best: {_esc(eq_diag.get('best_opportunity_symbol', '—'))} "
        f"@ {_esc(eq_diag.get('best_opportunity_venue', '—'))}</span>"
        f"<span>Score: {_esc(eq_diag.get('best_opportunity_score', '—'))}</span>"
        f"<span>NET: {_esc(eq_diag.get('best_opportunity_net_eur', '—'))}</span>"
        f"<span>NET/h: {_esc(eq_diag.get('best_opportunity_net_eur_per_hour', '—'))}</span>"
        f"<span>Headroom: {_esc(eq_diag.get('best_opportunity_headroom_pct', '—'))}</span>"
        f"<span>Extension: {_esc(eq_diag.get('best_opportunity_extension_pct', '—'))}</span>"
        f"<span>Hold: {_esc(eq_diag.get('best_opportunity_hold_minutes', '—'))} min</span>"
        "</div>"
        "<div class='band-row'>"
        f"<span>Allocator selected: {_esc(eq_diag.get('capital_allocator_selected', '—'))}</span>"
        f"<span>Allocator skipped: {_esc(eq_diag.get('capital_allocator_skipped', '—'))}</span>"
        f"<span>Volatility rejects: {_esc(eq_diag.get('volatility_reject', '—'))}</span>"
        f"<span>Spread rejects: {_esc(eq_diag.get('spread_reject', '—'))}</span>"
        f"<span>Timing rejects: {_esc(eq_diag.get('timing_reject', '—'))}</span>"
        "</div></section>"
    )
    cap_deployed = _dec(eq_diag.get("capital_deployed_eur"))
    cap_locked = _dec(eq_diag.get("capital_locked_eur"))
    cap_util = eq_diag.get("capital_utilization_pct")
    net_hr = eq_diag.get("net_eur_per_hour")
    mfe_cap = eq_diag.get("average_mfe_capture_ratio")
    hold_min = eq_diag.get("average_hold_minutes")
    eff_html = (
        "<section class='target-band' aria-label='Profit efficiency'>"
        "<h2>Profit efficiency</h2>"
        "<div class='band-row'>"
        f"<span>Realized NET today: <strong>{_esc(_eur(daily_realized, signed=True) if daily_realized is not None else '—')}</strong></span>"
        f"<span>NET EUR/hour: {_esc(net_hr if net_hr is not None else '—')}</span>"
        f"<span>Capital deployed: {_esc(_eur(cap_deployed) if cap_deployed is not None else '—')}</span>"
        f"<span>Capital locked: {_esc(_eur(cap_locked) if cap_locked is not None else '—')}</span>"
        f"<span>Utilization: {_esc(cap_util if cap_util is not None else '—')}%</span>"
        "</div>"
        "<div class='band-row'>"
        f"<span>Avg hold: {_esc(hold_min if hold_min is not None else '—')} min</span>"
        f"<span>MFE capture: {_esc(mfe_cap if mfe_cap is not None else '—')}</span>"
        f"<span>Cap-eff candidates: {_esc(eq_diag.get('capital_efficiency_candidates', '—'))}</span>"
        f"<span>Cap-eff rejected: {_esc(eq_diag.get('capital_efficiency_rejected', '—'))}</span>"
        f"<span>Venue: BV {_esc(eq_diag.get('venue_bitvavo_selected', '—'))} · OKX {_esc(eq_diag.get('venue_okx_selected', '—'))}</span>"
        "</div></section>"
    )

    charts_html = """
    <section class="charts" aria-label="Portfolio charts">
      <article class="chart-card chart-pnl-first">
        <h2>PnL — gerealiseerd, unrealized &amp; winnable</h2>
        <div class="chart-wrap"><canvas id="chart-pnl" role="img" aria-label="PnL grafiek"></canvas></div>
      </article>
      <article class="chart-card">
        <h2>Portfolio (EUR)</h2>
        <div class="chart-wrap"><canvas id="chart-portfolio" role="img" aria-label="Portfolio grafiek"></canvas></div>
      </article>
    </section>
    """

    kpi_grid_html = f"""
    <section aria-label="Geïnd vs open">
      <p class="pnl-split-intro">Geïnd = winst op <strong>verkopen</strong> vandaag. Open = totaal unrealized op alle bags (niet vandaag). <strong>Portfolio-winst</strong> = portfolio Δ sinds 00:00 NL (geïnd + MTM-beweging vandaag).</p>
      <div class="pnl-split">
        <article class="card split-harvest">
          <p class="label">Geïnd vandaag (verkopen)</p>
          <p class="value {daily_realized_class}" id="kpi-harvested-today">{_esc(_eur(daily_realized, signed=True) if daily_realized is not None else "—")}</p>
          <p class="hint">Coins verkocht · FIFO · sinds 00:00 NL</p>
        </article>
        <article class="card split-open">
          <p class="label">Open (unrealized)</p>
          <p class="value {unreal_class}" id="kpi-open-unrealized">{_esc(_eur(unrealized, signed=True) if unrealized is not None else "—")}</p>
          <p class="hint">Nog in portfolio · niet gecashd</p>
        </article>
        <article class="card split-portfolio">
          <p class="label">Portfolio-winst</p>
          <p class="value {portfolio_pnl_class}" id="kpi-portfolio-pnl">{_esc(_eur(portfolio_pnl, signed=True) if portfolio_pnl is not None else "—")}</p>
          <p class="hint">Equity Δ sinds 00:00 NL</p>
        </article>
        <article class="card split-winnable">
          <p class="label">Winnable (nog te harvesten)</p>
          <p class="value {winn_class}" id="kpi-winnable-harvest">{_esc(_eur(winnable, signed=True) if winnable is not None else "—")}</p>
          <p class="hint">Boven break-even · exit mogelijk</p>
        </article>
      </div>
    </section>
    <section class="grid-kpi" aria-label="Kern KPIs">
      <article class="card hero">
        <p class="label">Portfolio-winst</p>
        <p class="value {portfolio_pnl_class}" id="kpi-portfolio-pnl-hero">{_esc(_eur(portfolio_pnl, signed=True) if portfolio_pnl is not None else "—")}</p>
        <p class="hint">Portfolio Δ vandaag (equity)</p>
      </article>
      <article class="card hero-a">
        <p class="label">Geïnd vandaag</p>
        <p class="value {daily_realized_class}" id="kpi-daily-realized">{_esc(_eur(daily_realized, signed=True) if daily_realized is not None else "—")}</p>
        <p class="hint">Alleen verkopen · FIFO</p>
      </article>
      <article class="card">
        <p class="label">Week geïnd</p>
        <p class="value {weekly_realized_class}" id="kpi-weekly-realized">{_esc(_eur(weekly_realized, signed=True) if weekly_realized is not None else "—")}</p>
        <p class="hint">Ma–zo NL · FIFO exchange</p>
      </article>
      <article class="card">
        <p class="label">Winnable</p>
        <p class="value {winn_class}" id="kpi-winnable">{_esc(_eur(winnable, signed=True))}</p>
        <p class="hint">Open winst boven BE</p>
      </article>
      <article class="card">
        <p class="label">Open (unrealized)</p>
        <p class="value {unreal_class}" id="kpi-unrealized">{_esc(_eur(unrealized, signed=True))}</p>
        <p class="hint">Totaal open bags vs kost</p>
      </article>
      <article class="card">
        <p class="label">Portfolio</p>
        <p class="value" id="kpi-portfolio">{_esc(_eur(portfolio))}</p>
        <p class="hint">Marktwaarde totaal</p>
      </article>
      <article class="card">
        <p class="label">Gerealiseerd (sessie)</p>
        <p class="value {session_realized_class}" id="kpi-realized">{_esc(_eur(session_realized, signed=True) if session_realized is not None else "—")}</p>
        <p class="hint">Verkopen sinds restart · FIFO replay</p>
      </article>
      <article class="card">
        <p class="label">Portfolio Δ (sessie)</p>
        <p class="value {session_portfolio_class}" id="kpi-session-pnl">{_esc(_eur(session_portfolio_pnl, signed=True) if session_portfolio_pnl is not None else "—")}</p>
        <p class="hint">Sinds restart · equity</p>
      </article>
      <article class="card">
        <p class="label">Vrij EUR</p>
        <p class="value" id="kpi-free">{_esc(_eur(free))}</p>
        <p class="hint">Quote cash</p>
      </article>
      <article class="card">
        <p class="label">Transacties</p>
        <p class="value" id="kpi-tx">{_esc(tx_n)}</p>
        <p class="hint">Fills deze sessie</p>
      </article>
    </section>
    """

    portfolio_strip_html = _render_portfolio_strip(
        holdings if isinstance(holdings, list) else []
    )

    detail_html = (
        f"<details class='fold'><summary>Posities, cross-venue &amp; detail</summary>"
        f"<div class='fold-body'>{positions_html}{cross_venue_html}{why_html}{trades_html}{alerts_html}</div></details>"
    )

    html_doc = f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
  <meta name="theme-color" content="#0c1118"/>
  <meta name="apple-mobile-web-app-capable" content="yes"/>
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>
  <meta name="apple-mobile-web-app-title" content="Moreney"/>
  <meta name="mobile-web-app-capable" content="yes"/>
  <link rel="manifest" href="/live/manifest.webmanifest"/>
  <link rel="icon" href="/live/icon.svg" type="image/svg+xml"/>
  <link rel="apple-touch-icon" href="/live/icon.svg"/>
  <title>Moreney</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=IBM+Plex+Mono:wght@500;600&family=Sora:wght@500;600&display=swap" rel="stylesheet" media="print" onload="this.media='all'"/>
  <noscript><link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=IBM+Plex+Mono:wght@500;600&family=Sora:wght@500;600&display=swap" rel="stylesheet"/></noscript>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/chart.umd.min.js" defer></script>
  <style>{_css()}</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1 class="brand">Moreney</h1>
      <p class="status {"on" if running else ""}">{("live" if running else "gestopt")}</p>
    </header>
    <div id="install-banner" class="install-banner" hidden>
      <span>Voeg Moreney toe aan je startscherm voor snelle PnL-updates.</span>
      <button type="button" id="install-btn">Installeren</button>
    </div>
    {stale_banner}
    {last_fill_html}
    <div class="dash-top">
      {portfolio_strip_html}
      {kpi_grid_html}
      {charts_html}
      {target_band_html}
      {obs_html}
      {eq_html}
      {opp_html}
      {eff_html}
      <p class="updated-at" id="updated-at">—</p>
    </div>
    <div class="dash-secondary">
      {fills_html}
      {cash_html}
      {idle_banner}
      {detail_html}
    </div>
    <footer>
      <button type="button" class="btn" onclick="post('/live/micro/session/start', {{minutes:null,budget_eur:2000,exclude_btc:false}})">Start</button>
      <button type="button" class="btn" onclick="post('/live/micro/session/stop')">Stop</button>
    </footer>
  </div>
  <script>
    const CHART_BOOT = {chart_json};

    async function post(url, body) {{
      await fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(body || {{}})
      }});
      await refreshMetrics();
    }}

    const eurFmt = (v, signed=false) => {{
      if (v === null || v === undefined || Number.isNaN(v)) return '—';
      const n = Number(v);
      const text = Math.abs(n).toLocaleString('nl-NL', {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
      if (signed && n > 0) return '+€' + text;
      if (signed && n < 0) return '-€' + text;
      return '€' + text;
    }};

    const chartDefaults = {{
      responsive: true,
      maintainAspectRatio: false,
      animation: {{ duration: 350 }},
      plugins: {{ legend: {{ labels: {{ color: '#93a4bb', boxWidth: 12 }} }} }},
      scales: {{
        x: {{ ticks: {{ color: '#93a4bb', maxRotation: 0, autoSkip: true, maxTicksLimit: window.innerWidth < 720 ? 5 : 8 }}, grid: {{ color: 'rgba(36,50,71,.45)' }} }},
        y: {{ ticks: {{ color: '#93a4bb', maxTicksLimit: window.innerWidth < 720 ? 5 : 8 }}, grid: {{ color: 'rgba(36,50,71,.45)' }} }}
      }}
    }};

    let portfolioChart = null;
    let pnlChart = null;
    let chartVersion = null;

    function buildCharts(data) {{
      const labels = data.labels || [];
      const pfCtx = document.getElementById('chart-portfolio');
      const pnlCtx = document.getElementById('chart-pnl');
      if (!pfCtx || !pnlCtx || !window.Chart) return;

      if (portfolioChart) portfolioChart.destroy();
      if (pnlChart) pnlChart.destroy();

      portfolioChart = new Chart(pfCtx, {{
        type: 'line',
        data: {{
          labels,
          datasets: [{{
            label: 'Portfolio',
            data: data.portfolio || [],
            borderColor: '#78a0dc',
            backgroundColor: 'rgba(120,160,220,.12)',
            fill: true,
            tension: 0.25,
            pointRadius: 0,
            borderWidth: 2,
          }}]
        }},
        options: chartDefaults
      }});

      pnlChart = new Chart(pnlCtx, {{
        type: 'line',
        data: {{
          labels,
          datasets: [
            {{
              label: 'Gerealiseerd',
              data: data.realized || [],
              borderColor: '#3ddc97',
              tension: 0.25,
              pointRadius: 0,
              borderWidth: 2,
            }},
            {{
              label: 'Unrealized',
              data: data.unrealized || [],
              borderColor: '#f0b429',
              tension: 0.25,
              pointRadius: 0,
              borderWidth: 2,
            }},
            {{
              label: 'Winnable',
              data: data.winnable || [],
              borderColor: '#78a0dc',
              borderDash: [4, 4],
              tension: 0.25,
              pointRadius: 0,
              borderWidth: 1.5,
            }}
          ]
        }},
        options: chartDefaults
      }});
    }}

    function updateCharts(data) {{
      if (!portfolioChart || !pnlChart || !window.Chart) {{
        buildCharts(data);
        return;
      }}
      const labels = data.labels || [];
      portfolioChart.data.labels = labels;
      portfolioChart.data.datasets[0].data = data.portfolio || [];
      portfolioChart.update('none');
      pnlChart.data.labels = labels;
      pnlChart.data.datasets[0].data = data.realized || [];
      pnlChart.data.datasets[1].data = data.unrealized || [];
      pnlChart.data.datasets[2].data = data.winnable || [];
      pnlChart.update('none');
    }}

    function renderPortfolioHoldings(items) {{
      const strip = document.getElementById('portfolio-strip');
      if (!strip) return;
      if (!items || !items.length) {{
        strip.innerHTML = '<h2>Portfolio</h2><p class="portfolio-empty">Geen posities — alle cash</p>';
        return;
      }}
      const chips = items.map((row) => {{
        const dir = row.momentum_direction || 'flat';
        const arrow = row.momentum_arrow || '→';
        const ret = row.momentum_return_pct;
        const title = ret != null ? ('momentum ' + ret + '%') : 'momentum —';
        const venue = row.venue ? ('<span class="venue">' + row.venue + '</span>') : '';
        const lh = row.role === 'long_hold' ? '<span class="venue">long-hold</span>' : '';
        const amt = eurFmt(parseFloat(row.notional_eur));
        return '<li class="hold-item">'
          + '<span class="mom mom-' + dir + '" title="' + title + '">' + arrow + '</span>'
          + '<span class="coin">' + (row.base || '—') + '</span>'
          + venue + '<span class="amt">' + amt + '</span>' + lh + '</li>';
      }}).join('');
      strip.innerHTML = '<h2>Portfolio</h2><ul class="hold-list" id="portfolio-holdings">' + chips + '</ul>';
    }}

    function applyMetrics(m) {{
      const set = (id, text) => {{ const el = document.getElementById(id); if (el) el.textContent = text; }};
      set('kpi-portfolio', eurFmt(m.portfolio_eur));
      set('kpi-realized', eurFmt(m.session_realized_eur, true));
      set('kpi-daily-realized', eurFmt(m.daily_realized_eur, true));
      set('kpi-harvested-today', eurFmt(m.harvested_today_eur ?? m.daily_realized_eur, true));
      set('kpi-portfolio-pnl', eurFmt(m.portfolio_pnl_eur, true));
      set('kpi-portfolio-pnl-hero', eurFmt(m.portfolio_pnl_eur, true));
      set('kpi-session-pnl', eurFmt(m.session_pnl_eur, true));
      set('kpi-weekly-realized', eurFmt(m.weekly_realized_eur, true));
      set('kpi-winnable', eurFmt(m.winnable_eur, true));
      set('kpi-winnable-harvest', eurFmt(m.winnable_eur, true));
      set('kpi-unrealized', eurFmt(m.unrealized_eur, true));
      set('kpi-open-unrealized', eurFmt(m.open_unrealized_eur ?? m.unrealized_eur, true));
      set('kpi-free', eurFmt(m.free_eur));
      if (m.tx_count !== undefined) set('kpi-tx', String(m.tx_count));
      if (m.portfolio_holdings) renderPortfolioHoldings(m.portfolio_holdings);
      const paint = (id, val) => {{
        const el = document.getElementById(id);
        if (!el || val == null || Number.isNaN(val)) return;
        el.classList.remove('good', 'bad');
        if (val > 0) el.classList.add('good');
        else if (val < 0) el.classList.add('bad');
      }};
      paint('kpi-daily-realized', m.daily_realized_eur);
      paint('kpi-harvested-today', m.harvested_today_eur ?? m.daily_realized_eur);
      paint('kpi-portfolio-pnl', m.portfolio_pnl_eur);
      paint('kpi-portfolio-pnl-hero', m.portfolio_pnl_eur);
      paint('kpi-session-pnl', m.session_pnl_eur);
      paint('kpi-weekly-realized', m.weekly_realized_eur);
      paint('kpi-realized', m.session_realized_eur);
      paint('kpi-winnable', m.winnable_eur);
      paint('kpi-winnable-harvest', m.winnable_eur);
      paint('kpi-unrealized', m.unrealized_eur);
      paint('kpi-open-unrealized', m.open_unrealized_eur ?? m.unrealized_eur);
      const ts = document.getElementById('updated-at');
      if (ts && m.updated_at) {{
        let label = 'Bijgewerkt ' + new Date(m.updated_at).toLocaleString('nl-NL');
        if (m.stale) label += ' · NIET ACTUEEL (sessie stil)';
        ts.textContent = label;
      }}
      const staleEl = document.getElementById('stale-banner');
      if (staleEl) staleEl.hidden = !m.stale;
    }}

    async function refreshMetrics() {{
      try {{
        const res = await fetch('/live/dashboard/metrics', {{ credentials: 'same-origin' }});
        if (!res.ok) return;
        const body = await res.json();
        if (body.metrics) applyMetrics(body.metrics);
      }} catch (_) {{}}
    }}

    async function refreshCharts() {{
      try {{
        const res = await fetch('/live/dashboard/charts', {{ credentials: 'same-origin' }});
        if (!res.ok) return;
        const body = await res.json();
        if (body.version && body.version === chartVersion) return;
        chartVersion = body.version || null;
        if (body.history) updateCharts(body.history);
      }} catch (_) {{}}
    }}

    window.addEventListener('DOMContentLoaded', () => {{
      buildCharts(CHART_BOOT);
      refreshMetrics();
      refreshCharts();
      setInterval(refreshMetrics, 15000);
      setInterval(refreshCharts, 60000);
    }});

    if ('serviceWorker' in navigator) {{
      navigator.serviceWorker.register('/live/sw.js').catch(() => {{}});
    }}

    let deferredPrompt = null;
    const banner = document.getElementById('install-banner');
    const installBtn = document.getElementById('install-btn');
    window.addEventListener('beforeinstallprompt', (e) => {{
      e.preventDefault();
      deferredPrompt = e;
      if (banner) {{ banner.hidden = false; banner.classList.add('show'); }}
    }});
    if (installBtn) {{
      installBtn.addEventListener('click', async () => {{
        if (!deferredPrompt) return;
        deferredPrompt.prompt();
        await deferredPrompt.userChoice;
        deferredPrompt = null;
        if (banner) banner.hidden = true;
      }});
    }}
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html_doc)

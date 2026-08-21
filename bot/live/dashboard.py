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


def _bitvavo_free_eur(observe: dict[str, Any]) -> Decimal | None:
    best: Decimal | None = None
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
            row_venue = str(row.get("venue") or venue or "").lower()
            if asset != "EUR" or row_venue not in {"bitvavo", ""}:
                continue
            free = _dec(row.get("available") if row.get("available") is not None else row.get("free"))
            if free is None:
                free = _dec(row.get("total"))
            if free is None:
                continue
            best = free if best is None else max(best, free)
    return best


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
    """


def render_live_dashboard(payload: dict[str, Any]) -> HTMLResponse:
    """Portfolio value, free cash, net session PnL, buy/sell transaction count."""
    session = payload.get("session") or {}
    observe = payload.get("observe") or {}
    bridge = session.get("bridge") or {}

    running = bool(session.get("running") or session.get("task_running"))
    free = _bitvavo_free_eur(observe)
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
    for base, st in sorted(states.items()):
        if not isinstance(st, dict):
            continue
        gain = st.get("gain_pct") or "—"
        to_arm = st.get("pct_to_arm") or "—"
        soft = "ja" if st.get("soft_armed") else "nee"
        hard = "ja" if st.get("hard_armed") else "nee"
        partial = "ja" if st.get("partial_done") else "—"
        gain_cls = ""
        try:
            g = float(str(gain).replace(",", "."))
            if g > 0:
                gain_cls = "good"
            elif g < 0:
                gain_cls = "bad"
        except ValueError:
            pass
        pos_rows.append(
            "<tr>"
            f"<td>{_esc(base)}</td>"
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
            "<table class='pos'><thead><tr>"
            "<th>Coin</th><th>Cost</th><th>Mark</th><th>Winst%</th>"
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
    alerts_html = (
        "<section class='positions'><h2>Alerts</h2>"
        f"<p class='hint'>Daily kill: {kill} · momentum="
        f"{'aan' if trail.get('momentum_enabled') else 'uit'} · "
        f"corr max {trail.get('max_per_corr_group') or '—'}</p>"
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
    <section class="grid">
      <article class="card">
        <p class="label">Portfolio</p>
        <p class="value">{_esc(_eur(portfolio))}</p>
        <p class="hint">EUR + crypto tegen marktprijs</p>
      </article>
      <article class="card">
        <p class="label">Vrij te besteden</p>
        <p class="value">{_esc(_eur(free))}</p>
        <p class="hint">Beschikbare EUR op Bitvavo</p>
      </article>
      <article class="card">
        <p class="label">Netto winst</p>
        <p class="value {pnl_class}">{_esc(_eur(pnl, signed=True))}</p>
        <p class="hint">Gerealiseerd op trades (na fees) · trend/trail</p>
      </article>
      <article class="card">
        <p class="label">Transacties</p>
        <p class="value">{_esc(tx_n)}</p>
        <p class="hint">Elke buy of sell fill</p>
      </article>
    </section>
    {positions_html}
    {alerts_html}
    <footer>
      <button type="button" class="btn" onclick="post('/live/micro/session/start', {{minutes:null,budget_eur:2024,exclude_btc:true}})">Start</button>
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

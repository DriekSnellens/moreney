"""Simple Paper Lab dashboard — status + tunable parameters only."""

from __future__ import annotations

import html
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.responses import HTMLResponse

from bot.core.config import Settings

_ENV_PATH = Path("/opt/moreney/env/paper-lab.env")

# (env key, settings attr, label, group)
_TUNABLES: list[tuple[str, str, str, str]] = [
    ("PAPER_STARTING_EUR", "paper_starting_eur", "Startkapitaal (€)", "Kapitaal"),
    ("PAPER_CYCLE_INTERVAL_MS", "paper_cycle_interval_ms", "Cycle interval (ms)", "Kapitaal"),
    ("PAPER_SEED_SYMBOLS", "paper_seed_symbols", "Seed symbols", "Inventory"),
    ("PAPER_SEED_MAX_ASSETS", "paper_seed_max_assets", "Max seed assets", "Inventory"),
    ("PAPER_SEED_INVENTORY_PCT", "paper_seed_inventory_pct", "Seed inventory %", "Inventory"),
    ("PAPER_MAX_ALT_INVENTORY_PCT", "paper_max_alt_inventory_pct", "Max alt inventory %", "Inventory"),
    ("PAPER_MIN_ALT_INVENTORY_PCT", "paper_min_alt_inventory_pct", "Min alt inventory %", "Inventory"),
    ("PAPER_MAKER_ENABLED", "paper_maker_enabled", "Maker aan", "Maker"),
    ("PAPER_MAKER_VENUES", "paper_maker_venues", "Maker venues", "Maker"),
    ("PAPER_MAKER_MIN_NOTIONAL_EUR", "paper_maker_min_notional_eur", "Min notional (€)", "Maker"),
    ("PAPER_MAKER_MIN_PROFIT_EUR", "paper_maker_min_profit_eur", "Min profit (€)", "Maker"),
    ("PAPER_MAKER_MIN_NET_RETURN", "paper_maker_min_net_return", "Min net return", "Maker"),
    ("PAPER_MAKER_MIN_SPREAD_BPS", "paper_maker_min_spread_bps", "Min spread (bps)", "Maker"),
    ("PAPER_MAKER_ADVERSE_BPS", "paper_maker_adverse_bps", "Adverse (bps)", "Maker"),
    ("PAPER_MAKER_SELL_PROFIT_BUFFER_BPS", "paper_maker_sell_profit_buffer_bps", "Sell buffer (bps)", "Maker"),
    ("PAPER_TRAIL_TAKE_PROFIT_ENABLED", "paper_trail_take_profit_enabled", "Trail aan", "Trail"),
    ("PAPER_TRAIL_SOFT_ARM_PCT", "paper_trail_soft_arm_pct", "Soft arm", "Trail"),
    ("PAPER_TRAIL_SOFT_DRAWDOWN_PCT", "paper_trail_soft_drawdown_pct", "Soft drawdown", "Trail"),
    ("PAPER_TRAIL_SOFT_PARTIAL_PCT", "paper_trail_soft_partial_pct", "Soft partial", "Trail"),
    ("PAPER_TRAIL_HARD_ARM_PCT", "paper_trail_hard_arm_pct", "Hard arm", "Trail"),
    ("PAPER_TRAIL_HARD_DRAWDOWN_PCT", "paper_trail_hard_drawdown_pct", "Hard drawdown", "Trail"),
    ("PAPER_TRAIL_HARD_PARTIAL_PCT", "paper_trail_hard_partial_pct", "Hard partial", "Trail"),
    ("PAPER_TRAIL_PARTIAL_ENABLED", "paper_trail_partial_enabled", "Partials aan", "Trail"),
    ("PAPER_TIME_STOP_ENABLED", "paper_time_stop_enabled", "Time-stop aan", "Recovery"),
    ("PAPER_TIME_STOP_SEC", "paper_time_stop_sec", "Time-stop (sec)", "Recovery"),
    ("PAPER_LADDER_BUY_ENABLED", "paper_ladder_buy_enabled", "Ladder buy aan", "Buys"),
    ("PAPER_LADDER_BUY_PCTS", "paper_ladder_buy_pcts", "Ladder pcts", "Buys"),
    ("RISK_MAX_OPEN_POSITIONS", "risk_max_open_positions", "Max open positions", "Risk"),
    ("MAX_DAILY_LOSS_PERCENT", "max_daily_loss_percent", "Max daily loss %", "Risk"),
    ("MAX_POSITION_PERCENT", "max_position_percent", "Max position %", "Risk"),
]


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _fmt_money(value: Any) -> str:
    try:
        d = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return "—"
    sign = "+" if d > 0 else ""
    return f"{sign}€{d.quantize(Decimal('0.01'))}"


def _fmt_pct(value: Any) -> str:
    try:
        f = float(value)
    except Exception:  # noqa: BLE001
        return "—"
    if abs(f) <= 1.0 and abs(f) > 0:
        # likely fraction
        return f"{f * 100:.2f}%"
    return f"{f:.4g}"


def _setting_value(settings: Settings, attr: str) -> Any:
    return getattr(settings, attr, None)


def _display_value(attr: str, raw: Any) -> str:
    if raw is None:
        return "—"
    if isinstance(raw, bool):
        return "ja" if raw else "nee"
    if attr.endswith("_pct") and isinstance(raw, (int, float)):
        # inventory pcts are already 0-100 style; trail arms are fractions
        if attr.startswith("paper_trail_") or attr in {
            "paper_maker_min_net_return",
        }:
            return _fmt_pct(raw)
        if float(raw) <= 1.0 and "inventory" not in attr:
            return _fmt_pct(raw)
    return str(raw)


def lab_params_payload(settings: Settings) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for env_key, attr, label, group in _TUNABLES:
        groups.setdefault(group, []).append(
            {
                "env": env_key,
                "label": label,
                "value": _display_value(attr, _setting_value(settings, attr)),
                "raw": _setting_value(settings, attr),
            }
        )
    return {
        "env_file": str(_ENV_PATH),
        "restart": "sudo systemctl restart moreney-paper@lab",
        "groups": groups,
    }


def render_lab_dashboard(
    *,
    settings: Settings,
    status: dict[str, Any],
    performance: dict[str, Any] | None = None,
) -> HTMLResponse:
    perf = performance or {}
    running = bool(status.get("running"))
    equity = status.get("current_equity") or perf.get("current_equity")
    start = status.get("starting_equity") or settings.paper_starting_eur
    pnl = status.get("net_pnl")
    if pnl in (None, "", "0", 0) and equity is not None and start is not None:
        try:
            pnl = Decimal(str(equity)) - Decimal(str(start))
        except Exception:  # noqa: BLE001
            pass
    cycles = status.get("cycle_count") or 0
    approved = status.get("approved_opportunities") or 0
    executed = status.get("executed_opportunities") or 0
    strategy = status.get("strategy") or "—"

    positions: list[tuple[str, Any]] = []
    inv = status.get("inventory") or {}
    venues = inv.get("venues") if isinstance(inv, dict) else None
    if isinstance(venues, dict):
        for venue, assets in venues.items():
            if not isinstance(assets, dict):
                continue
            for asset, bal in assets.items():
                if str(asset).upper() in {"EUR", "USDT", "USD"}:
                    continue
                try:
                    qty = Decimal(str((bal or {}).get("total") or (bal or {}).get("available") or 0))
                except Exception:  # noqa: BLE001
                    qty = Decimal("0")
                if qty > 0:
                    positions.append((f"{venue}:{asset}", qty))

    params = lab_params_payload(settings)
    param_sections = []
    for group, rows in params["groups"].items():
        cells = "".join(
            f"<tr><th>{_esc(r['label'])}</th>"
            f"<td class='val'>{_esc(r['value'])}</td>"
            f"<td class='env'>{_esc(r['env'])}</td></tr>"
            for r in rows
        )
        param_sections.append(
            f"<section class='param-block'>"
            f"<h2>{_esc(group)}</h2>"
            f"<table><thead><tr><th>Parameter</th><th>Waarde</th><th>Env-key</th></tr></thead>"
            f"<tbody>{cells}</tbody></table></section>"
        )

    pos_html = (
        "<p class='muted'>Geen open alt-posities.</p>"
        if not positions
        else "<ul class='pos'>"
        + "".join(f"<li><span>{_esc(k)}</span><b>{_esc(v)}</b></li>" for k, v in positions[:12])
        + "</ul>"
    )

    state_class = "on" if running else "off"
    state_label = "DRAAIT" if running else "GESTOPT"
    pnl_class = "good" if (pnl is not None and Decimal(str(pnl)) > 0) else (
        "bad" if (pnl is not None and Decimal(str(pnl)) < 0) else ""
    )

    body = f"""<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta http-equiv="refresh" content="8"/>
  <title>Paper Lab · Moreney</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Sora:wght@400;600;700&display=swap" rel="stylesheet"/>
  <style>
    :root {{
      --ink: #12202b;
      --muted: #5b6b76;
      --line: rgba(18,32,43,.12);
      --ok: #0f7a4c;
      --bad: #b42318;
      --accent: #0c6e6b;
      --bg0: #e7f1ef;
      --bg1: #f7faf9;
      --panel: rgba(255,255,255,.72);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "Sora", sans-serif;
      background:
        radial-gradient(1200px 600px at 10% -10%, #cfe8e4 0%, transparent 55%),
        radial-gradient(900px 500px at 100% 0%, #d9e4f2 0%, transparent 50%),
        linear-gradient(180deg, var(--bg0), var(--bg1) 40%, #eef3f2);
      min-height: 100vh;
    }}
    main {{
      width: min(980px, calc(100% - 2rem));
      margin: 0 auto;
      padding: 2.2rem 0 3.5rem;
    }}
    .hero {{
      display: grid;
      gap: .55rem;
      padding: 1.4rem 0 1.8rem;
      border-bottom: 1px solid var(--line);
      animation: rise .55s ease both;
    }}
    .brand {{
      font-size: clamp(2rem, 5vw, 3rem);
      font-weight: 700;
      letter-spacing: -.03em;
      line-height: 1.05;
    }}
    .sub {{
      color: var(--muted);
      font-size: .98rem;
      max-width: 42rem;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: .45rem;
      font-family: "IBM Plex Mono", monospace;
      font-size: .78rem;
      letter-spacing: .04em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .dot {{
      width: .55rem; height: .55rem; border-radius: 50%;
      background: #9aa7b0;
    }}
    .pill.on .dot {{ background: var(--ok); box-shadow: 0 0 0 4px rgba(15,122,76,.12); }}
    .pill.off .dot {{ background: var(--bad); }}
    .kpis {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 1rem;
      padding: 1.4rem 0 1.6rem;
      border-bottom: 1px solid var(--line);
      animation: rise .65s .08s ease both;
    }}
    .kpi span {{
      display: block;
      color: var(--muted);
      font-size: .78rem;
      margin-bottom: .25rem;
    }}
    .kpi strong {{
      font-size: clamp(1.15rem, 2.4vw, 1.55rem);
      font-weight: 600;
      letter-spacing: -.02em;
    }}
    .kpi strong.good {{ color: var(--ok); }}
    .kpi strong.bad {{ color: var(--bad); }}
    section {{
      padding: 1.5rem 0 0;
      animation: rise .7s .12s ease both;
    }}
    h2 {{
      margin: 0 0 .7rem;
      font-size: 1.05rem;
      letter-spacing: -.01em;
    }}
    .howto {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 1rem 1.1rem;
      margin-bottom: 1.2rem;
    }}
    .howto code {{
      font-family: "IBM Plex Mono", monospace;
      font-size: .82rem;
      background: rgba(12,110,107,.08);
      color: var(--accent);
      padding: .12rem .35rem;
      border-radius: 6px;
    }}
    .howto ol {{ margin: .4rem 0 0 1.1rem; padding: 0; color: var(--muted); }}
    .howto li {{ margin: .25rem 0; }}
    .param-grid {{
      display: grid;
      gap: 1.1rem;
    }}
    .param-block {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: .85rem 1rem 1rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: .9rem;
    }}
    th, td {{
      text-align: left;
      padding: .42rem 0;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    tr:last-child th, tr:last-child td {{ border-bottom: 0; }}
    thead th {{
      color: var(--muted);
      font-weight: 500;
      font-size: .72rem;
      text-transform: uppercase;
      letter-spacing: .04em;
    }}
    tbody th {{ font-weight: 500; width: 42%; }}
    td.val {{
      font-family: "IBM Plex Mono", monospace;
      font-weight: 500;
      color: var(--accent);
    }}
    td.env {{
      font-family: "IBM Plex Mono", monospace;
      font-size: .75rem;
      color: var(--muted);
      word-break: break-all;
    }}
    .pos {{ list-style: none; margin: 0; padding: 0; display: grid; gap: .35rem; }}
    .pos li {{
      display: flex; justify-content: space-between; gap: 1rem;
      padding: .45rem 0; border-bottom: 1px solid var(--line);
      font-family: "IBM Plex Mono", monospace; font-size: .86rem;
    }}
    .muted {{ color: var(--muted); }}
    footer {{
      margin-top: 1.8rem;
      color: var(--muted);
      font-size: .8rem;
    }}
    @keyframes rise {{
      from {{ opacity: 0; transform: translateY(8px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @media (max-width: 760px) {{
      .kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      td.env {{ display: none; }}
      thead th:last-child {{ display: none; }}
    }}
  </style>
</head>
<body>
  <main>
    <header class="hero">
      <div class="pill {state_class}"><span class="dot"></span>{_esc(state_label)} · poort {_esc(settings.api_port)} · paper only</div>
      <div class="brand">Paper Lab</div>
      <p class="sub">Geïsoleerde strategie-sandbox. Geen live orders. Pas parameters aan in het env-bestand en herstart de service.</p>
    </header>

    <div class="kpis">
      <div class="kpi"><span>Equity</span><strong>{_esc(_fmt_money(equity))}</strong></div>
      <div class="kpi"><span>PnL</span><strong class="{pnl_class}">{_esc(_fmt_money(pnl))}</strong></div>
      <div class="kpi"><span>Cycles</span><strong>{_esc(cycles)}</strong></div>
      <div class="kpi"><span>Exec / approved</span><strong>{_esc(executed)} / {_esc(approved)}</strong></div>
    </div>

    <section>
      <div class="howto">
        <strong>Parameters wijzigen</strong>
        <ol>
          <li>Open <code>{_esc(params['env_file'])}</code></li>
          <li>Pas keys in het <code>TUNABLE</code>-blok aan</li>
          <li>Run <code>{_esc(params['restart'])}</code></li>
        </ol>
        <p class="muted" style="margin:.55rem 0 0">Strategie nu: <code>{_esc(strategy)}</code></p>
      </div>
      <h2>Instelbare parameters (actief)</h2>
      <div class="param-grid">
        {''.join(param_sections)}
      </div>
    </section>

    <section>
      <h2>Open posities</h2>
      {pos_html}
    </section>

    <footer>Auto-refresh 8s · persist {_esc(settings.paper_persist_path)} · live :8020 blijft onaangeroerd</footer>
  </main>
</body>
</html>"""
    return HTMLResponse(content=body)

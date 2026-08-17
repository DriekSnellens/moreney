"""Strategy Lab HTML dashboard — preserves paper dashboard separately."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.responses import HTMLResponse


def load_latest_lab_results(root: Path | None = None) -> dict[str, Any] | None:
    root = root or Path("data/strategy_lab")
    if not root.exists():
        return None
    dirs = sorted(
        [p for p in root.iterdir() if p.is_dir() and (p / "results.json").exists()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not dirs:
        return None
    return json.loads((dirs[0] / "results.json").read_text(encoding="utf-8"))


def render_strategy_lab_dashboard(payload: dict[str, Any] | None) -> HTMLResponse:
    if not payload:
        body = """
        <section class="card">
          <h1>Strategy Lab</h1>
          <p class="muted">No tournament results yet. Run:</p>
          <pre>PYTHONPATH=. python -m bot.strategy_lab.runner</pre>
          <p><a href="/paper/dashboard">← Paper dashboard</a></p>
        </section>
        """
        return HTMLResponse(_shell(body))

    leaderboard = payload.get("leaderboard") or []
    waterfalls = payload.get("waterfalls") or {}
    scorecards = payload.get("scorecards") or {}
    fingerprints = payload.get("fingerprints") or {}
    frozen = payload.get("frozen_config") or {}

    rows = "".join(_leaderboard_row(r) for r in leaderboard) or (
        "<tr><td colspan='12' class='empty'>No strategies</td></tr>"
    )
    detail_blocks = "".join(
        _strategy_detail(sid, scorecards.get(sid) or {}, waterfalls.get(sid) or {})
        for sid in [r["strategy"] for r in leaderboard]
    )
    compare_json = json.dumps(
        {
            "leaderboard": leaderboard,
            "waterfalls": waterfalls,
            "scorecards": {
                k: {
                    "dev_net": (v.get("development") or {}).get("realized_net_eur"),
                    "oos_net": ((v.get("oos") or {}) or {}).get("realized_net_eur"),
                    "velocity": (v.get("development") or {}).get("capital_velocity"),
                    "participation": (v.get("development") or {}).get("participation_rate"),
                    "waterfall": (v.get("development") or {}).get("waterfall"),
                }
                for k, v in scorecards.items()
            },
        },
        default=str,
    )

    body = f"""
    <header class="top">
      <div>
        <p class="eyebrow">Research · Shadow · Paper only</p>
        <h1>Strategy Lab</h1>
        <p class="sub">Which strategy has real NET edge after costs, capital lock, and untouched OOS?</p>
      </div>
      <div class="top-meta">
        <a class="btn ghost" href="/paper/dashboard">Paper dashboard</a>
        <a class="btn ghost" href="/fleet">Fleet</a>
      </div>
    </header>

    <section class="banner">
      <div><span class="k">Dataset</span><strong>{_esc(payload.get('dataset_id'))}</strong></div>
      <div><span class="k">Label</span><strong>{_esc(payload.get('data_label'))}</strong></div>
      <div><span class="k">Dev / OOS cycles</span>
        <strong>{_esc(frozen.get('n_development'))} / {_esc(frozen.get('n_oos'))}</strong></div>
      <div><span class="k">Fingerprint</span><code>{_esc((fingerprints.get('tournament') or '')[:16])}…</code></div>
    </section>

    <section class="card">
      <h2>Strategy Leaderboard</h2>
      <p class="muted">Green “winner” badges are never shown unless OOS verdict is PROMISING/ROBUST under frozen criteria.</p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Strategy</th><th>Status</th><th>Opps</th><th>Trades</th>
              <th>NET</th><th>NET €/fill</th><th>NET bps</th>
              <th>Capital</th><th>Velocity</th><th>Max DD</th>
              <th>OOS NET</th><th>OOS NET/cap·s</th><th>Evidence</th><th>Verdict</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <h2>Where did the money go?</h2>
      <p class="muted">Gross → fees → slippage → adverse/latency → funding/hedge → NET</p>
      <div class="waterfall-grid" id="waterfall-grid"></div>
    </section>

    <section class="card">
      <h2>Compare strategies</h2>
      <div class="compare-controls" id="compare-controls"></div>
      <div class="charts">
        <figure><figcaption>Cumulative NET (dev shadow)</figcaption><canvas id="chart-cum"></canvas></figure>
        <figure><figcaption>OOS NET</figcaption><canvas id="chart-oos"></canvas></figure>
        <figure><figcaption>Capital velocity</figcaption><canvas id="chart-vel"></canvas></figure>
        <figure><figcaption>Participation rate</figcaption><canvas id="chart-part"></canvas></figure>
      </div>
    </section>

    <section class="card">
      <h2>Strategy detail</h2>
      {detail_blocks}
    </section>

    <script id="lab-data" type="application/json">{compare_json}</script>
    <script>{_DASHBOARD_JS}</script>
    """
    return HTMLResponse(_shell(body))


def _leaderboard_row(r: dict[str, Any]) -> str:
    verdict = str(r.get("verdict") or "")
    return (
        "<tr>"
        f"<td><strong>{_esc(r.get('strategy'))}</strong></td>"
        f"<td><span class='pill {_status_cls(verdict)}'>{_esc(r.get('status'))}</span></td>"
        f"<td class='num'>{_esc(r.get('opportunities'))}</td>"
        f"<td class='num'>{_esc(r.get('trades'))}</td>"
        f"<td class='num {_pnl_cls(r.get('net'))}'>{_fmt(r.get('net'))}</td>"
        f"<td class='num' title='OBSERVED paper sleeve net per fill; not canonical replay'>{_fmt(r.get('net_per_fill'))}</td>"
        f"<td class='num'>{_fmt(r.get('net_bps'))}</td>"
        f"<td class='num'>{_fmt(r.get('capital'))}</td>"
        f"<td class='num'>{_fmt(r.get('capital_velocity'), digits=6)}</td>"
        f"<td class='num'>{_fmt(r.get('max_dd'))}</td>"
        f"<td class='num {_pnl_cls(r.get('oos_net'))}'>{_fmt(r.get('oos_net'))}</td>"
        f"<td class='num'>{_fmt(r.get('oos_net_per_capital_sec'), digits=6)}</td>"
        f"<td class='num'>{_esc(r.get('evidence'))}</td>"
        f"<td><span class='pill {_status_cls(verdict)}'>{_esc(verdict)}</span></td>"
        "</tr>"
    )


def _strategy_detail(sid: str, block: dict[str, Any], waterfall: dict[str, Any]) -> str:
    dev = block.get("development") or {}
    oos = block.get("oos") or {}
    verdict = block.get("verdict") or dev.get("verdict") or "RESEARCH"
    return f"""
    <article class="detail" id="detail-{_esc(sid)}">
      <h3>{_esc(sid)} <span class="pill {_status_cls(verdict)}">{_esc(verdict)}</span></h3>
      <div class="detail-grid">
        <div>
          <h4>Performance</h4>
          <ul>
            <li>Total NET: <strong class="{_pnl_cls(dev.get('realized_net_eur'))}">{_esc(dev.get('realized_net_eur'))}</strong></li>
            <li>OOS NET: <strong>{_esc((oos or {}).get('realized_net_eur'))}</strong></li>
            <li>NET/fill: {_esc(dev.get('net_eur_per_fill'))}</li>
            <li>Capital velocity: {_esc(dev.get('capital_velocity'))}</li>
            <li>Max DD: {_esc(dev.get('max_drawdown_eur'))}</li>
            <li>Win/Loss: {_esc(dev.get('winning'))}/{_esc(dev.get('losing'))}</li>
            <li>Participation: {_esc(dev.get('participation_rate'))}</li>
            <li>Independent events: {_esc(dev.get('independent_events'))}</li>
          </ul>
        </div>
        <div>
          <h4>Economics waterfall</h4>
          <ul class="wf">
            <li>Gross {_esc(waterfall.get('gross_opportunity') or dev.get('gross_pnl_eur'))}</li>
            <li>− Fees {_esc(waterfall.get('buy_fees'))} + {_esc(waterfall.get('sell_fees'))}</li>
            <li>− Slippage {_esc(waterfall.get('slippage'))}</li>
            <li>− Adverse {_esc(waterfall.get('adverse_selection'))}</li>
            <li>− Funding {_esc(waterfall.get('funding'))}</li>
            <li>− Other {_esc(waterfall.get('transfer_fx'))}</li>
            <li>= NET <strong>{_esc(waterfall.get('net') or dev.get('realized_net_eur'))}</strong></li>
          </ul>
        </div>
        <div>
          <h4>Evidence</h4>
          <ul>
            <li>Dev completed: {_esc(dev.get('completed'))}</li>
            <li>OOS completed: {_esc((oos or {}).get('completed'))}</li>
            <li>Version: {_esc(dev.get('strategy_version'))}</li>
            <li>Baseline opps: {_esc(dev.get('baseline_opportunities'))}</li>
          </ul>
        </div>
      </div>
    </article>
    """


def _esc(v: Any) -> str:
    s = "" if v is None else str(v)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt(v: Any, digits: int = 4) -> str:
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _pnl_cls(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    if x > 0:
        return "pos"
    if x < 0:
        return "neg"
    return ""


def _status_cls(v: str) -> str:
    v = str(v or "")
    if v in {"OOS_ROBUST", "OOS_PROMISING", "PROMISING"}:
        return "ok"
    if v in {"EDGE_NEGATIVE_AFTER_COSTS", "FAILED", "OOS_UNSTABLE"}:
        return "bad"
    if v in {"INSUFFICIENT_DATA", "IN_SAMPLE_ONLY", "NO_EDGE"}:
        return "warn"
    return ""


def _shell(body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Strategy Lab · Moreney</title>
  <style>{_CSS}</style>
</head>
<body>
  <main class="wrap">{body}</main>
</body>
</html>"""


_CSS = """
:root {
  --bg0:#0f1412; --bg1:#18201c; --ink:#e8f0ea; --muted:#8aa094;
  --line:#2a3a32; --accent:#6bcf8e; --warn:#d4a017; --bad:#e07070; --pos:#6bcf8e; --neg:#e07070;
  --serif:"Fraunces", "Iowan Old Style", Georgia, serif;
  --sans:"Sora", "Avenir Next", system-ui, sans-serif;
}
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=Sora:wght@400;600&display=swap');
* { box-sizing:border-box; }
body {
  margin:0; color:var(--ink); font-family:var(--sans);
  background:
    radial-gradient(1200px 600px at 10% -10%, #1d3a2c 0%, transparent 55%),
    radial-gradient(900px 500px at 100% 0%, #243028 0%, transparent 50%),
    linear-gradient(180deg, var(--bg0), #121816 40%, var(--bg0));
  min-height:100vh;
}
.wrap { max-width:1200px; margin:0 auto; padding:28px 20px 80px; }
.top { display:flex; justify-content:space-between; gap:20px; align-items:end; margin-bottom:22px; }
.eyebrow { letter-spacing:.12em; text-transform:uppercase; color:var(--muted); font-size:11px; margin:0 0 6px; }
h1 { font-family:var(--serif); font-size:clamp(2rem,4vw,3rem); margin:0; font-weight:600; }
.sub { color:var(--muted); max-width:42rem; }
.btn { display:inline-block; padding:8px 14px; border:1px solid var(--line); border-radius:8px; color:var(--ink); text-decoration:none; }
.btn.ghost { background:transparent; }
.banner, .card {
  background:linear-gradient(180deg, rgba(255,255,255,.03), transparent), var(--bg1);
  border:1px solid var(--line); border-radius:16px; padding:18px 20px; margin-bottom:18px;
}
.banner { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }
.banner .k { display:block; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.08em; }
.table-wrap { overflow:auto; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th, td { padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; }
th { color:var(--muted); font-weight:600; font-size:11px; text-transform:uppercase; letter-spacing:.06em; }
.num { text-align:right; font-variant-numeric:tabular-nums; }
.pos { color:var(--pos); } .neg { color:var(--neg); }
.pill { display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid var(--line); font-size:11px; }
.pill.ok { border-color:var(--accent); color:var(--accent); }
.pill.warn { border-color:var(--warn); color:var(--warn); }
.pill.bad { border-color:var(--bad); color:var(--bad); }
.muted { color:var(--muted); }
.detail { border-top:1px solid var(--line); padding-top:16px; margin-top:16px; }
.detail-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; }
.waterfall-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }
.wf-card { border:1px solid var(--line); border-radius:12px; padding:12px; background:rgba(0,0,0,.15); }
.wf-card h4 { margin:0 0 8px; font-size:13px; }
.wf-bar { display:flex; height:10px; border-radius:6px; overflow:hidden; background:#0c100e; margin:6px 0; }
.wf-bar span { display:block; height:100%; }
.charts { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
figure { margin:0; border:1px solid var(--line); border-radius:12px; padding:12px; background:rgba(0,0,0,.12); }
figcaption { color:var(--muted); font-size:12px; margin-bottom:8px; }
canvas { width:100%; height:160px; display:block; background:transparent; }
.compare-controls { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }
.compare-controls label { font-size:12px; color:var(--muted); border:1px solid var(--line); padding:4px 8px; border-radius:999px; }
@media (max-width:860px) {
  .banner, .detail-grid, .charts { grid-template-columns:1fr; }
}
"""

_DASHBOARD_JS = r"""
(function(){
  const data = JSON.parse(document.getElementById('lab-data').textContent);
  const lb = data.leaderboard || [];
  const sc = data.scorecards || {};
  const colors = ['#6bcf8e','#7eb6ff','#e0b05a','#c79bff','#e07070','#9aa7a0'];

  // Waterfall cards
  const grid = document.getElementById('waterfall-grid');
  lb.forEach((row, i) => {
    const wf = (sc[row.strategy]||{}).waterfall || row.waterfall || {};
    const gross = Math.abs(parseFloat(wf.gross_opportunity||0)) || 1;
    const fees = Math.abs(parseFloat(wf.buy_fees||0)+parseFloat(wf.sell_fees||0));
    const slip = Math.abs(parseFloat(wf.slippage||0));
    const adv = Math.abs(parseFloat(wf.adverse_selection||0));
    const fund = Math.abs(parseFloat(wf.funding||0))+Math.abs(parseFloat(wf.transfer_fx||0));
    const net = parseFloat(wf.net||0);
    const card = document.createElement('div');
    card.className = 'wf-card';
    card.innerHTML = `<h4>${row.strategy}</h4>
      <div class="wf-bar">
        <span style="width:${Math.min(100,gross/gross*100)}%;background:#3d6b52"></span>
      </div>
      <div class="muted" style="font-size:12px">
        Gross ${Number(wf.gross_opportunity||0).toFixed(4)} →
        fees ${fees.toFixed(4)} → slip ${slip.toFixed(4)} →
        adv ${adv.toFixed(4)} → other ${fund.toFixed(4)} →
        <strong style="color:${net>=0?'#6bcf8e':'#e07070'}">NET ${net.toFixed(4)}</strong>
      </div>`;
    grid.appendChild(card);
  });

  // Compare checkboxes
  const controls = document.getElementById('compare-controls');
  const selected = new Set(lb.slice(0,4).map(r => r.strategy));
  lb.forEach(row => {
    const lab = document.createElement('label');
    lab.innerHTML = `<input type="checkbox" ${selected.has(row.strategy)?'checked':''} data-s="${row.strategy}"/> ${row.strategy}`;
    controls.appendChild(lab);
  });
  controls.addEventListener('change', e => {
    const t = e.target;
    if (t.checked) selected.add(t.dataset.s); else selected.delete(t.dataset.s);
    drawAll();
  });

  function drawBar(canvasId, values, labels) {
    const c = document.getElementById(canvasId);
    const ctx = c.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    c.width = w*dpr; c.height = h*dpr; ctx.scale(dpr,dpr);
    ctx.clearRect(0,0,w,h);
    if (!values.length) return;
    const max = Math.max(...values.map(Math.abs), 1e-9);
    const barW = Math.max(8, (w-40)/values.length - 8);
    values.forEach((v,i) => {
      const x = 20 + i*(barW+8);
      const bh = (Math.abs(v)/max)*(h-40);
      const y = v>=0 ? (h/2 - bh) : h/2;
      ctx.fillStyle = colors[i%colors.length];
      ctx.fillRect(x, y, barW, bh || 1);
      ctx.fillStyle = '#8aa094';
      ctx.font = '10px sans-serif';
      ctx.fillText((labels[i]||'').slice(0,10), x, h-8);
    });
    ctx.strokeStyle = '#2a3a32';
    ctx.beginPath(); ctx.moveTo(10,h/2); ctx.lineTo(w-10,h/2); ctx.stroke();
  }

  function drawAll() {
    const rows = lb.filter(r => selected.has(r.strategy));
    const labels = rows.map(r => r.strategy);
    drawBar('chart-cum', rows.map(r => r.net||0), labels);
    drawBar('chart-oos', rows.map(r => r.oos_net||0), labels);
    drawBar('chart-vel', rows.map(r => r.capital_velocity||0), labels);
    drawBar('chart-part', rows.map(r => r.participation_rate||0), labels);
  }
  drawAll();
})();
"""

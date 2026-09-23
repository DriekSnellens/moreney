"""Operator page for the Daily Momentum Desk (server-rendered, auto-refresh)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from fastapi.responses import HTMLResponse

from bot.live.momentum_donchian import sleeve_live_caption
from bot.live.momentum_period_pnl import DeskEarnings, earnings_as_dict

_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;600;700;800&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500;600&display=swap');
:root {
  --canvas: #070B12;
  --card: #0F172A;
  --elevated: #1E293B;
  --border: rgba(255,255,255,.08);
  --border-soft: rgba(255,255,255,.05);
  --border-hover: rgba(255,255,255,.16);
  --ink: #F8FAFC;
  --muted: #94A3B8;
  --muted-2: #64748B;
  --primary: #34D399;
  --primary-2: #10B981;
  --good: #34D399;
  --bad: #F87171;
  --warn: #F59E0B;
  --accent: #34D399;
  --accent-2: #10B981;
  --line: var(--border);
  --line-soft: var(--border-soft);
  --panel: rgba(15,23,42,.92);
  --display: "Plus Jakarta Sans", Inter, system-ui, sans-serif;
  --sans: Inter, "Plus Jakarta Sans", system-ui, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, monospace;
  --radius: 0.75rem;
  --ease: cubic-bezier(.22,1,.36,1);
  --sidebar: 16rem;
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; }
html { overflow-x: clip; }
body {
  color: var(--ink);
  font-family: var(--sans);
  background:
    radial-gradient(900px 520px at 18% -8%, rgba(16,185,129,.07), transparent 55%),
    radial-gradient(700px 480px at 92% 108%, rgba(99,102,241,.06), transparent 50%),
    var(--canvas);
  padding-bottom: 5.5rem;
  -webkit-font-smoothing: antialiased;
  overflow-x: clip;
  -webkit-tap-highlight-color: transparent;
}
.mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); }
.good { color: var(--good); } .bad { color: var(--bad); } .warn { color: var(--warn); }

.mix-board { margin: 0 0 1.15rem; border-width: 1.5px; }
.mix-board.risk_on { border-color: rgba(52,211,153,.42); }
.mix-board.risk_off { border-color: rgba(245,158,11,.45); }
.mix-board.mid { border-color: rgba(148,163,184,.28); }
.mix-k {
  margin: 0 0 .2rem; color: var(--muted); font-size: .68rem; font-weight: 700;
  letter-spacing: .08em; text-transform: uppercase;
}
.mix-now {
  font-family: var(--display); font-size: clamp(1.35rem, 3vw, 1.85rem);
  font-weight: 800; letter-spacing: -.03em; line-height: 1.15; margin: 0 0 .35rem;
}
.mix-stance { margin: 0 0 .65rem; color: var(--muted); font-size: .88rem; }
.mix-why {
  margin: 0 0 .9rem; color: var(--ink); font-size: 1.02rem; line-height: 1.45;
  max-width: 62rem; font-weight: 500;
}
.mix-why em { font-style: normal; color: var(--primary); }
.mix-tape {
  position: relative; height: 3.6rem; margin: 0 0 .9rem;
  border-radius: .7rem; overflow: hidden; border: 1px solid var(--border);
  background: var(--elevated);
}
.mix-tape .zones { position: absolute; inset: 0; display: flex; }
.mix-tape .z { display: flex; align-items: flex-end; padding: .35rem .5rem; font-size: .62rem;
  font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: rgba(248,250,252,.55); }
.mix-tape .z.off { background: rgba(248,113,113,.16); }
.mix-tape .z.mid { background: rgba(245,158,11,.14); }
.mix-tape .z.on { background: rgba(52,211,153,.16); }
.mix-tape .z.cur { color: var(--ink); box-shadow: inset 0 0 0 1px rgba(255,255,255,.14); }
.mix-tape .mark {
  position: absolute; top: .2rem; transform: translateX(-50%);
  font-family: var(--mono); font-size: .68rem; font-weight: 700; white-space: nowrap;
  color: var(--ink);
}
.mix-tape .mark i {
  display: block; width: 2px; height: 1.55rem; margin: 0 auto .15rem;
  background: currentColor; border-radius: 2px;
}
.mix-tape .mark.btc { color: var(--primary); }
.mix-gates {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .55rem;
  margin: 0 0 .9rem;
}
.mix-gates div {
  background: var(--elevated); border: 1px solid var(--border); border-radius: .65rem;
  padding: .55rem .7rem;
}
.mix-gates span { display: block; color: var(--muted); font-size: .7rem; text-transform: uppercase; letter-spacing: .06em; }
.mix-gates strong { font-family: var(--mono); font-size: 1.05rem; }
.mix-sleeves {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: .65rem;
}
.mix-sleeve {
  border: 1px solid var(--border); border-radius: .75rem; padding: .75rem .8rem;
  background: rgba(15,23,42,.65); min-height: 8.2rem;
}
.mix-sleeve.on { border-color: rgba(52,211,153,.45); box-shadow: 0 0 0 1px rgba(52,211,153,.12) inset; }
.mix-sleeve.off { opacity: .72; }
.mix-sleeve h3 { margin: 0 0 .25rem; font-size: .92rem; }
.mix-sleeve .eur { font-family: var(--mono); font-weight: 700; font-size: 1.05rem; }
.mix-sleeve p { margin: .35rem 0 0; color: var(--muted); font-size: .78rem; line-height: 1.35; }
.mix-open { margin: .75rem 0 0; display: flex; flex-wrap: wrap; gap: .4rem; }
.mix-chip {
  display: inline-flex; align-items: baseline; gap: .4rem;
  padding: .35rem .65rem; border-radius: 999px; border: 1px solid var(--border);
  background: rgba(15,23,42,.8); font-size: .8rem;
}
.mix-chip strong { font-family: var(--mono); }
.mix-foot { margin: .75rem 0 0; font-size: .78rem; color: var(--muted); }
.paper-clip, .paper-sw, .side-15m { margin-top: .85rem; }
.paper-clip, .paper-sw { border-style: dashed; }
.paper-clip h2, .paper-sw h2, .side-15m h2 { font-size: .95rem; }
.paper-clip .clip-kpis, .paper-sw .clip-kpis, .side-15m .clip-kpis { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: .45rem; margin: .55rem 0 .4rem; }
.paper-clip .clip-kpis div, .paper-sw .clip-kpis div, .side-15m .clip-kpis div { padding: .45rem .55rem; border: 1px solid var(--border); border-radius: 10px; background: rgba(15,23,42,.55); }
.paper-clip .clip-kpis span, .paper-sw .clip-kpis span, .side-15m .clip-kpis span { display: block; font-size: .62rem; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }
.paper-clip .clip-kpis strong, .paper-sw .clip-kpis strong, .side-15m .clip-kpis strong { font-family: var(--mono); font-size: .95rem; }
@media (max-width: 720px) {
  .paper-clip .clip-kpis, .paper-sw .clip-kpis, .side-15m .clip-kpis { grid-template-columns: 1fr 1fr; }
}
.mix-dc-dec { display: grid; gap: .55rem; }
.mix-dc-dec > div { padding: .45rem 0; border-bottom: 1px solid var(--border-soft); }
@media (max-width: 720px) {
  .mix-gates { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 979px) {
  .mix-board { padding: .75rem .7rem; }
  .mix-now { font-size: clamp(1.05rem, 5.6vw, 1.45rem); }
  .mix-why { font-size: .9rem; max-width: none; margin-bottom: .7rem; }
  .mix-stance { font-size: .8rem; }
  .mix-sleeves { grid-template-columns: 1fr; }
  .mix-sleeve { min-height: 0; padding: .65rem .7rem; }
  .mix-sleeve p { font-size: .74rem; }
  .mix-tape { height: 3.05rem; margin-bottom: .7rem; }
  .mix-tape .z { font-size: .52rem; padding: .22rem .35rem; letter-spacing: .04em; }
  .mix-tape .mark { font-size: .56rem; }
  .mix-gates { grid-template-columns: 1fr 1fr; gap: .4rem; }
  .mix-gates div { padding: .45rem .55rem; }
  .mix-gates strong { font-size: .95rem; }
  .mix-chip { font-size: .74rem; padding: .28rem .5rem; max-width: 100%; }
  .mix-open { gap: .3rem; }
  .mix-foot { font-size: .7rem; line-height: 1.4; }
  .mix-k { font-size: .62rem; }
}

@keyframes rise-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: none; }
}
@keyframes live-pulse {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(52,211,153,.45); }
  50% { opacity: .55; box-shadow: 0 0 0 6px rgba(52,211,153,0); }
}

.shell { min-height: 100vh; }
.sidebar {
  display: none;
  position: fixed; left: 0; top: 0; bottom: 0; width: var(--sidebar);
  padding: 1.35rem 1rem; flex-direction: column; justify-content: space-between;
  background: rgba(10,15,29,.82); backdrop-filter: blur(22px);
  border-right: 1px solid var(--border);
  z-index: 40;
}
@media (min-width: 980px) {
  .sidebar { display: flex; }
  .shell-main { margin-left: var(--sidebar); }
  body { padding-bottom: 0; }
}
.side-brand { display: flex; align-items: center; gap: .75rem; padding: 0 .35rem; }
.side-logo {
  width: 2.25rem; height: 2.25rem; border-radius: .7rem;
  background: linear-gradient(135deg, #34D399, #059669);
  display: grid; place-items: center;
  box-shadow: 0 8px 24px -8px rgba(16,185,129,.45);
  font-weight: 800; color: #06281c; font-family: var(--display); font-size: .95rem;
}
.side-brand strong {
  display: block; font-family: var(--display); font-size: 1.05rem;
  letter-spacing: -.02em; font-weight: 700;
}
.side-brand span { display: block; font-size: .7rem; color: var(--muted); margin-top: .1rem; }
.side-status {
  margin: 1.25rem .15rem 0; padding: .85rem .9rem; border-radius: var(--radius);
  background: rgba(15,23,42,.7); border: 1px solid var(--border-soft);
}
.side-status .row {
  display: flex; justify-content: space-between; align-items: center;
  font-size: .72rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em;
}
.side-status .meta {
  margin-top: .45rem; display: flex; justify-content: space-between;
  font-family: var(--mono); font-size: .72rem; color: var(--muted);
}
.side-nav { margin-top: 1.35rem; display: grid; gap: .25rem; }
.side-nav .label {
  padding: .35rem .75rem; font-size: .68rem; font-weight: 600;
  letter-spacing: .08em; text-transform: uppercase; color: var(--muted-2);
}
.side-nav a {
  display: flex; align-items: center; justify-content: space-between;
  gap: .5rem; padding: .7rem .85rem; border-radius: .7rem;
  color: var(--muted); text-decoration: none; font-size: .88rem; font-weight: 500;
  border: 1px solid transparent; transition: .15s var(--ease);
}
.side-nav a:hover { color: var(--ink); background: rgba(255,255,255,.04); }
.side-nav a.active {
  color: #a7f3d0; background: rgba(16,185,129,.1);
  border-color: rgba(16,185,129,.22); font-weight: 600;
}
.side-nav .badge {
  font-family: var(--mono); font-size: .68rem; padding: .15rem .45rem;
  border-radius: 999px; background: rgba(15,23,42,.9);
  border: 1px solid rgba(148,163,184,.25); color: var(--primary);
}
.side-capital {
  margin: 1rem .15rem 0; padding: .95rem; border-radius: var(--radius);
  background: linear-gradient(160deg, rgba(15,23,42,.95), rgba(15,23,42,.45));
  border: 1px solid var(--border);
}
.side-capital .cap-label {
  display: flex; justify-content: space-between; font-size: .68rem;
  letter-spacing: .06em; text-transform: uppercase; color: var(--muted); font-weight: 600;
}
.side-capital .cap-val {
  margin-top: .35rem; font-family: var(--display); font-weight: 700;
  font-size: 1.15rem; letter-spacing: -.02em; font-variant-numeric: tabular-nums;
}
.side-capital .bar {
  margin-top: .65rem; height: 4px; border-radius: 999px; background: #1e293b; overflow: hidden;
}
.side-capital .bar > i {
  display: block; height: 100%; border-radius: inherit;
  background: linear-gradient(90deg, #10B981, #34D399);
}

.topbar {
  position: sticky; top: 0; z-index: 30;
  display: flex; align-items: center; justify-content: space-between; gap: 1rem;
  min-height: 3.75rem; padding: .65rem 1rem;
  background: rgba(7,11,18,.78); backdrop-filter: blur(18px);
  border-bottom: 1px solid var(--border);
}
.topbar-left {
  display: flex; align-items: center; gap: .75rem; flex-wrap: nowrap;
  min-width: 0; flex: 1 1 auto; overflow: hidden;
}
.topbar-right { display: flex; align-items: center; gap: .55rem; flex-wrap: wrap; }
.top-metric {
  display: none; flex-direction: column; gap: .1rem; padding: 0 .55rem;
  border-left: 1px solid var(--border-soft);
}
@media (min-width: 760px) { .top-metric { display: flex; } }
.top-metric .k {
  font-size: .62rem; font-weight: 600; letter-spacing: .08em;
  text-transform: uppercase; color: var(--muted-2);
}
.top-metric .v {
  font-family: var(--mono); font-size: .85rem; font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.wrap {
  max-width: 1440px; margin: 0 auto;
  padding: 1.1rem 1rem 2.4rem;
}
@media (min-width: 980px) {
  .wrap { padding: 1.35rem 1.6rem 2.75rem; }
}

.masthead {
  display: grid; gap: 1rem; margin-bottom: 1rem;
  padding: 1.15rem 1.2rem 1.2rem;
  border: 1px solid var(--border); border-radius: 1rem;
  background:
    linear-gradient(180deg, rgba(255,255,255,.04), transparent 42%),
    rgba(15,23,42,.88);
  box-shadow: 0 8px 32px -4px rgba(0,0,0,.5), 0 0 40px -12px rgba(16,185,129,.12);
  animation: rise-in .55s var(--ease) both;
}
.masthead-top {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: .85rem; flex-wrap: wrap;
}
.brand {
  margin: 0; font-family: var(--display); font-weight: 800;
  font-size: clamp(1.85rem, 4.5vw, 2.65rem); letter-spacing: -0.03em; line-height: .95;
  color: var(--ink);
}
.brand-sub {
  margin: .45rem 0 0; color: var(--muted); font-size: .9rem;
  max-width: 38rem; line-height: 1.45;
}
.earn-label {
  margin: 0 0 .45rem; font-size: .68rem; font-weight: 600;
  letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
}
.earn-grid {
  display: grid; gap: 0; grid-template-columns: 1fr;
  border-top: 1px solid var(--border-soft);
}
@media (min-width: 760px) {
  .earn-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .earn-tile + .earn-tile { border-left: 1px solid var(--border-soft); }
}
.earn-tile {
  padding: .85rem .25rem .2rem;
  animation: rise-in .65s var(--ease) both;
}
.earn-tile:nth-child(2) { animation-delay: .05s; }
.earn-tile:nth-child(3) { animation-delay: .1s; }
.earn-tile .period {
  margin: 0; font-size: .7rem; font-weight: 600;
  color: var(--muted); letter-spacing: .05em; text-transform: uppercase;
}
.earn-tile .amount {
  margin: .35rem 0 0; font-family: var(--display); font-weight: 700;
  font-size: clamp(1.55rem, 3.6vw, 2.05rem); letter-spacing: -0.03em; line-height: 1.05;
  font-variant-numeric: tabular-nums;
}
.earn-tile .meta { margin: .3rem 0 0; font-size: .72rem; color: var(--muted-2); }
.earn-foot {
  display: flex; flex-wrap: wrap; gap: .45rem 1.15rem; margin-top: .65rem;
  padding-top: .65rem; border-top: 1px solid var(--border-soft);
  font-size: .8rem; color: var(--muted);
}
.earn-foot strong { color: var(--ink); font-weight: 600; font-family: var(--mono); }

.pill {
  display: inline-flex; align-items: center; gap: .4rem;
  padding: .28rem .7rem; border-radius: 999px;
  border: 1px solid var(--border); background: rgba(255,255,255,.04);
  font-size: .66rem; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: var(--muted);
}
.pill .dot { width: .4rem; height: .4rem; border-radius: 999px; background: currentColor; }
.pill.on { color: var(--good); border-color: rgba(16,185,129,.35); background: rgba(16,185,129,.1); }
.pill.on .dot { animation: live-pulse 1.8s ease-in-out infinite; }
.pill.off { color: var(--bad); border-color: rgba(248,113,113,.35); background: rgba(239,68,68,.1); }
.pill.obs { color: var(--warn); border-color: rgba(245,158,11,.35); background: rgba(245,158,11,.1); }

.panel {
  margin-top: 1rem; padding: 1rem 1.05rem .85rem;
  border: 1px solid var(--border); border-radius: 1rem;
  background:
    linear-gradient(180deg, rgba(255,255,255,.035), transparent 40%),
    rgba(15,23,42,.86);
  box-shadow: 0 8px 28px -8px rgba(0,0,0,.45);
  animation: rise-in .5s var(--ease) both;
}
.panel-head {
  display: flex; justify-content: space-between; align-items: baseline;
  gap: .6rem; flex-wrap: wrap; margin-bottom: .7rem;
}
.panel-head h2, .fold-head {
  margin: 0; font-family: var(--display); font-weight: 650;
  font-size: .98rem; letter-spacing: -0.015em;
}
.panel-head .aside { font-size: .75rem; color: var(--muted); }
.card {
  background: rgba(15,23,42,.9); border: 1px solid var(--border);
  border-radius: var(--radius); padding: .9rem 1rem;
}
.card.section { margin-top: 1rem; }
.card-head {
  display: flex; justify-content: space-between; align-items: center;
  gap: .6rem; flex-wrap: wrap;
}
.card-head h2, .section h2 {
  margin: 0; font-family: var(--display); font-weight: 650;
  font-size: .98rem; letter-spacing: -0.015em;
}
.section { margin-top: 1rem; }
.section h2 { margin: 0 0 .55rem; }

.pulse {
  display: grid; gap: .75rem; margin-top: 1rem;
  animation: rise-in .55s var(--ease) .05s both;
}
@media (min-width: 760px) {
  .pulse { grid-template-columns: repeat(4, minmax(0,1fr)); }
}
.pulse-item, .hero-card {
  padding: .95rem 1rem;
  border: 1px solid var(--border); border-radius: var(--radius);
  background:
    linear-gradient(180deg, rgba(255,255,255,.04), transparent 55%),
    rgba(15,23,42,.88);
  box-shadow: 0 6px 22px -8px rgba(0,0,0,.4);
}
.pulse-item .label, .hero-card .label {
  margin: 0; color: var(--muted); font-size: .66rem; font-weight: 600;
  letter-spacing: .07em; text-transform: uppercase;
}
.pulse-item .value, .hero-card .value {
  margin: .4rem 0 0; font-family: var(--display);
  font-size: clamp(1.15rem, 2.5vw, 1.45rem); font-weight: 700;
  letter-spacing: -0.025em; font-variant-numeric: tabular-nums;
}
.pulse-item .hint, .hero-card .hint {
  margin: .35rem 0 0; color: var(--muted-2); font-size: .7rem;
  border: 0; padding: 0; background: none;
}
.hero-grid.pulse { display: grid; }
.pulse .hero-card { display: block; }

.stack { display: grid; gap: .85rem; }
@media (min-width: 980px) { .stack.two { grid-template-columns: 1.12fr .88fr; } }
@media (min-width: 1100px) { .stack.three { grid-template-columns: 1fr 1fr 1fr; } }
.hint {
  margin: .65rem 0; padding: .65rem .8rem; border-radius: .6rem;
  font-size: .8rem; border: 1px solid var(--border); background: rgba(255,255,255,.03);
}
.hint.bad { color: #fecaca; border-color: rgba(248,113,113,.35); background: rgba(239,68,68,.1); }
.hint.good { color: #a7f3d0; border-color: rgba(16,185,129,.3); background: rgba(16,185,129,.08); }
.hint.warn { color: #fde68a; border-color: rgba(245,158,11,.3); background: rgba(245,158,11,.08); }

.btn {
  cursor: pointer; font: inherit; font-size: .84rem; padding: .55rem .9rem; min-height: 42px;
  border-radius: .5rem; border: 1px solid var(--border); background: rgba(255,255,255,.04);
  color: var(--ink); touch-action: manipulation; font-weight: 600;
  transition: border-color .15s ease, background .15s ease, transform .15s var(--ease), box-shadow .15s ease;
}
.btn:hover { border-color: var(--border-hover); background: rgba(255,255,255,.07); transform: translateY(-1px); }
.btn:disabled { opacity: .45; cursor: not-allowed; transform: none; }
.btn.danger {
  color: #fecaca; border-color: rgba(248,113,113,.4); background: rgba(239,68,68,.1);
}
.btn.primary {
  background: linear-gradient(135deg, #10B981, #059669); color: #fff;
  border-color: rgba(255,255,255,.18);
  box-shadow: 0 4px 14px rgba(16,185,129,.25);
}
.btn.primary:hover { filter: brightness(1.06); border-color: rgba(255,255,255,.28); }
.btn.block { width: 100%; display: block; text-align: center; }

.toolbar { display: flex; flex-wrap: wrap; gap: .45rem; margin: .7rem 0 0; }
.ops-row { margin-top: .85rem; }

.table-scroll {
  overflow-x: auto; -webkit-overflow-scrolling: touch;
  border-radius: .65rem; border: 1px solid var(--border-soft);
  overscroll-behavior-x: contain;
}
table.desk, table.ledger {
  width: 100%; border-collapse: collapse; font-size: .82rem;
}
table.desk th, table.ledger th {
  text-align: left; padding: .7rem .75rem; font-size: .68rem; font-weight: 600;
  letter-spacing: .06em; text-transform: uppercase; color: var(--muted);
  border-bottom: 1px solid var(--border);
  background: rgba(7,11,18,.45);
}
table.desk td, table.ledger td {
  padding: .7rem .75rem; border-bottom: 1px solid rgba(255,255,255,.04);
  font-variant-numeric: tabular-nums; vertical-align: middle;
}
table.desk tr:hover td, table.ledger tr:hover td { background: rgba(255,255,255,.02); }
table.desk .num, table.ledger .num { font-family: var(--mono); }

.pos-empty { padding: .85rem .2rem; }
.pos-cards { display: none; gap: .75rem; }
.pos-card {
  border: 1px solid var(--border); border-radius: var(--radius);
  background: rgba(15,23,42,.9); padding: .9rem 1rem;
}
.pos-card .row1, .pos-card .title {
  display: flex; justify-content: space-between; gap: .5rem; align-items: baseline;
  font-family: var(--display); font-weight: 700; font-size: 1.05rem;
}
.pos-card .row1 strong { font-size: 1.1rem; letter-spacing: -.02em; }
.pos-card .meta, .pos-card .grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: .5rem .75rem; margin-top: .7rem;
  font-size: .8rem; font-variant-numeric: tabular-nums;
}
.pos-card .meta span, .pos-card .grid span {
  color: var(--muted-2); display: block; font-size: .62rem;
  letter-spacing: .05em; text-transform: uppercase; margin-bottom: .12rem; font-weight: 600;
}
.pos-card .actions { margin-top: .85rem; }
.pos-card .actions .btn { width: 100%; min-height: 44px; }

.fold {
  margin-top: 1rem; border: 1px solid var(--border); border-radius: var(--radius);
  background: rgba(15,23,42,.8); overflow: hidden;
}
.fold > summary {
  list-style: none; cursor: pointer; display: flex; justify-content: space-between;
  align-items: center; gap: .5rem; padding: .9rem 1rem;
  user-select: none;
}
.fold > summary::-webkit-details-marker { display: none; }
.fold .chev::before { content: "▸"; color: var(--muted); font-size: .85rem; }
.fold[open] .chev::before { content: "▾"; }
.fold-body { padding: 0 1rem 1rem; border-top: 1px solid var(--border-soft); }

.rules { display: grid; gap: .45rem; font-size: .8rem; }
.rules .row { display: grid; grid-template-columns: 9.5rem 1fr; gap: .6rem; }
.rules .k { color: var(--muted); font-size: .72rem; text-transform: uppercase;
  letter-spacing: .05em; font-weight: 600; }

.sticky-actions {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 50;
  padding: .65rem .75rem calc(.65rem + env(safe-area-inset-bottom, 0px));
  background: rgba(7,11,18,.92); backdrop-filter: blur(16px);
  border-top: 1px solid var(--border);
}
.sticky-actions .toolbar { margin: 0; }

.sleeve-split { display: grid; gap: .75rem; }
@media (min-width: 820px) { .sleeve-split { grid-template-columns: 1fr 1fr; } }
.sleeve-earn { font-size: .76rem; color: var(--muted); margin: .3rem 0 .1rem; }
.sleeve-earn b { font-family: var(--mono); font-weight: 600; }

.foot {
  margin-top: 1.25rem; font-size: .72rem; color: var(--muted-2);
  display: flex; flex-wrap: wrap; gap: .35rem .85rem; align-items: center;
}
.foot a { color: var(--primary); text-decoration: none; font-weight: 600; }
.foot a:hover { text-decoration: underline; }

/* Mobile bottom dock (nav) — desktop hidden */
.mobile-dock {
  display: none;
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 55;
  padding: .3rem .4rem calc(.3rem + env(safe-area-inset-bottom, 0px));
  background: rgba(10,15,29,.96); backdrop-filter: blur(18px);
  border-top: 1px solid var(--border);
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: .15rem;
}
.mobile-dock a {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: .12rem; min-height: 2.85rem; padding: .3rem .15rem; border-radius: .55rem;
  text-decoration: none; color: var(--muted); font-size: .6rem; font-weight: 600;
  letter-spacing: .02em; text-align: center; touch-action: manipulation;
}
.mobile-dock a .ico {
  font-size: .9rem; line-height: 1; color: var(--muted-2);
}
.mobile-dock a.active {
  color: #a7f3d0; background: rgba(16,185,129,.12);
}
.mobile-dock a.active .ico { color: var(--primary); }

@media (max-width: 979px) {
  .mobile-dock { display: grid; }
  .sticky-actions {
    bottom: calc(3.25rem + env(safe-area-inset-bottom, 0px));
    padding: .4rem .55rem;
  }
  body { padding-bottom: calc(7.1rem + env(safe-area-inset-bottom, 0px)); }
  .topbar {
    padding: .5rem .7rem;
    padding-top: calc(.5rem + env(safe-area-inset-top, 0px));
    min-height: 3.1rem;
    gap: .4rem;
  }
  .topbar-left { gap: .35rem; }
  .topbar-right, .ops-row { display: none !important; }
  .top-metric {
    display: flex; padding: 0 .3rem;
    border-left: 1px solid var(--border-soft);
    min-width: 0;
  }
  .top-metric.winrate { display: none; }
  .top-metric .k { font-size: .52rem; }
  .top-metric .v {
    font-size: .72rem;
    white-space: nowrap;
  }
  .wrap {
    padding: .7rem .65rem 1.1rem;
    max-width: 100%;
  }
  .masthead {
    padding: .85rem .75rem .9rem;
    border-radius: .85rem;
    gap: .65rem;
    margin-bottom: .75rem;
  }
  .masthead-top { gap: .55rem; }
  .brand {
    font-size: clamp(1.45rem, 7.5vw, 1.95rem);
  }
  .brand-sub { font-size: .8rem; max-width: none; line-height: 1.4; }
  .earn-label { margin-bottom: .3rem; }
  .earn-grid { gap: 0; }
  .earn-tile {
    padding: .65rem 0;
    border-top: 1px solid var(--border-soft);
  }
  .earn-tile:first-child { border-top: 0; padding-top: .5rem; }
  .earn-tile .amount {
    font-size: clamp(1.3rem, 6.5vw, 1.7rem);
  }
  .earn-foot {
    gap: .3rem .75rem;
    font-size: .72rem;
  }
  .panel {
    margin-top: .75rem;
    padding: .75rem .7rem .65rem;
    border-radius: .85rem;
  }
  .panel-head { margin-bottom: .55rem; }
  .panel-head h2, .fold-head, .card-head h2, .section h2 { font-size: .9rem; }
  .pulse {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: .5rem;
    margin-top: .75rem;
  }
  .pulse-item, .hero-card {
    padding: .7rem .65rem;
    border-radius: .7rem;
  }
  .pulse-item .value, .hero-card .value {
    font-size: clamp(1.02rem, 4.2vw, 1.22rem);
  }
  .pulse-item .hint, .hero-card .hint {
    font-size: .65rem;
    line-height: 1.35;
  }
  .desk-wide { display: none; }
  .pos-cards { display: grid; }
  .pos-card { padding: .75rem .8rem; }
  .pos-card .meta, .pos-card .grid {
    grid-template-columns: 1fr 1fr;
    gap: .45rem .55rem;
  }
  .btn {
    min-height: 42px;
    padding: .5rem .7rem;
    font-size: .82rem;
  }
  .sticky-actions .toolbar {
    display: flex;
    flex-wrap: nowrap;
    gap: .35rem;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  .sticky-actions .toolbar form { display: contents; }
  .sticky-actions .toolbar .btn {
    flex: 1 1 0;
    min-width: 0;
    width: auto;
    padding: .45rem .5rem;
    font-size: .78rem;
    white-space: nowrap;
  }
  .fold > summary { padding: .8rem .75rem; min-height: 44px; }
  .fold-body { padding: 0 .75rem .8rem; }
  .rules .row {
    grid-template-columns: 1fr;
    gap: .12rem;
    padding: .4rem 0;
    border-bottom: 1px solid var(--border-soft);
  }
  .rules .k { font-size: .64rem; }
  .hint { font-size: .76rem; padding: .55rem .65rem; }
  .table-scroll {
    margin: 0 -.1rem;
    border-radius: .55rem;
  }
  table.desk, table.ledger { font-size: .76rem; }
  table.desk th, table.ledger th,
  table.desk td, table.ledger td { padding: .55rem .5rem; }
  .foot { font-size: .66rem; gap: .35rem .55rem; }
  .stack.two { grid-template-columns: 1fr; }
  .stack.three { grid-template-columns: 1fr; }
  .card { padding: .8rem .85rem; }
  .card.section { margin-top: .85rem; }
  .sleeve-split { grid-template-columns: 1fr; }
}

@media (max-width: 420px) {
  .top-metric .k { display: none; }
  .top-metric .v { font-size: .7rem; }
  .earn-foot span { flex: 1 1 auto; }
  .earn-foot span:last-child { width: 100%; }
  .mobile-dock a { font-size: .55rem; min-height: 2.7rem; }
  .sticky-actions .toolbar .btn { font-size: .74rem; padding: .42rem .4rem; }
  .pos-card .meta, .pos-card .grid { grid-template-columns: 1fr 1fr; }
}

@media (max-width: 979px) {
  body.mix-live { padding-bottom: calc(3.65rem + env(safe-area-inset-bottom, 0px)); }
  .top-metric:has([data-k="mix-label-top"]) { display: none; }
}

@media (min-width: 721px) and (max-width: 979px) {
  .pulse { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .earn-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .earn-tile + .earn-tile { border-left: 1px solid var(--border-soft); border-top: 0; }
  .earn-tile { padding: .7rem .25rem .2rem; }
  .wrap { padding: .9rem 1rem 1.4rem; }
  .top-metric.winrate { display: flex; }
}

@media (min-width: 980px) {
  .mobile-dock { display: none !important; }
  .sticky-actions { display: none; }
  body { padding-bottom: 0; }
}
"""



def _fmt_eur(v: Any, *, signed: bool = True) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{x:+,.2f} €" if signed else f"{x:,.2f} €"


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{100 * float(v):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _cls(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    return "good" if x > 0 else "bad" if x < 0 else ""


def _ts(iso: Any) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso))
        return dt.astimezone(UTC).strftime("%d-%m %H:%M UTC")
    except ValueError:
        return escape(str(iso))


def _hero(
    label: str,
    value: str,
    *,
    cls: str = "",
    hint: str = "",
    value_attr: str = "",
) -> str:
    hint_html = f'<div class="hint muted">{escape(hint)}</div>' if hint else ""
    attrs = f" {value_attr}" if value_attr else ""
    return (
        f'<div class="pulse-item hero-card {cls}"><div class="label">{escape(label)}</div>'
        f'<div class="value {cls}"{attrs}>{value}</div>{hint_html}</div>'
    )




def _earnings_masthead(
    earnings: DeskEarnings | None,
    *,
    pill: str,
    venue: str,
    show_volatile: bool = False,
    show_mix: bool = False,
) -> str:
    """First-viewport composition: brand + week/month/all-time net."""
    use_combined = bool(show_volatile or show_mix)
    if earnings is None:
        c_week = c_month = c_all = 0.0
        open_mtm = 0.0
        tw = tm = ta = 0
        core_w = vol_w = 0.0
        as_of = "—"
    else:
        # Mix / dual-sleeve masthead uses combined (Donchian + shorts + idle core).
        sleeve = earnings.combined if use_combined else earnings.core
        c_week, c_month, c_all = sleeve.week_eur, sleeve.month_eur, sleeve.all_time_eur
        open_mtm = earnings.open_mtm_eur
        tw, tm, ta = sleeve.trades_week, sleeve.trades_month, sleeve.trades_all_time
        core_w, vol_w = earnings.core.week_eur, earnings.volatile.week_eur
        as_of = earnings.as_of
    if show_mix:
        week_meta = f"{tw} trades · mix deze week"
        brand_sub = f"Momentum desk · mix · {venue}. "
    elif show_volatile:
        week_meta = f"{tw} trades · core {_fmt_eur(core_w)} · vol {_fmt_eur(vol_w)}"
        brand_sub = f"Momentum desk · core + volatile · {venue}. "
    else:
        week_meta = f"{tw} trades deze week"
        brand_sub = f"Momentum desk · core · {venue}. "
    tiles = [
        ("Deze week", c_week, week_meta),
        ("Deze maand", c_month, f"{tm} trades deze maand"),
        ("Vanaf begin", c_all, f"{ta} trades all-time · netto gesloten"),
    ]
    tiles_html = "".join(
        '<div class="earn-tile">'
        f'<p class="period">{escape(label)}</p>'
        f'<p class="amount {_cls(val)}">{_fmt_eur(val)}</p>'
        f'<p class="meta">{escape(meta)}</p>'
        "</div>"
        for label, val, meta in tiles
    )
    day_eur = 0.0
    if earnings is not None:
        day_eur = (
            earnings.combined.day_eur if use_combined else earnings.core.day_eur
        )
    return (
        '<section class="masthead">'
        '<div class="masthead-top">'
        "<div>"
        '<h1 class="brand">Moreney</h1>'
        f'<p class="brand-sub">{brand_sub}'
        "Netto = gesloten trades na fees (Europe/Amsterdam).</p>"
        "</div>"
        f"<div>{pill}</div>"
        "</div>"
        '<p class="earn-label">Netto verdiend</p>'
        f'<div class="earn-grid">{tiles_html}</div>'
        '<div class="earn-foot">'
        f"<span>Vandaag <strong class='{_cls(day_eur)}'>"
        f"{_fmt_eur(day_eur)}</strong></span>"
        f"<span>Open MTM <strong class='{_cls(open_mtm)}'>{_fmt_eur(open_mtm)}</strong></span>"
        f'<span class="muted">peil {escape(str(as_of)[:19].replace("T", " "))} NL</span>'
        "</div></section>"
    )


def _sleeve_earnings_line(period: Any) -> str:
    if period is None:
        return ""
    return (
        f'<p class="sleeve-earn">week <b class="{_cls(period.week_eur)}">{_fmt_eur(period.week_eur)}</b>'
        f" · maand <b class='{_cls(period.month_eur)}'>{_fmt_eur(period.month_eur)}</b>"
        f" · begin <b class='{_cls(period.all_time_eur)}'>{_fmt_eur(period.all_time_eur)}</b></p>"
    )


def _positions_table(
    status: Mapping[str, Any],
    *,
    sell_all_path: str | None = "/live/momentum",
    post_sell_action: str | None = None,
    empty_text: str = "Geen open posities — 100% cash tot de volgende beslissing.",
) -> str:
    rows = [
        p
        for p in (status.get("positions") or [])
        if float(p.get("quantity") or 0.0) > 1e-12
    ]
    cfg = status.get("config") or {}
    if not rows:
        return f'<p class="pos-empty muted" data-live="positions-empty">{escape(empty_text)}</p>'
    # Preserve explicit 0.0 (paper bear-harvest disables trail/hard-stop).
    trail = float(cfg["trail_pct"]) if cfg.get("trail_pct") is not None else 0.05
    tight_after = (
        float(cfg["trail_tight_after"]) if cfg.get("trail_tight_after") is not None else 0.0
    )
    tight = float(cfg["trail_tight_pct"]) if cfg.get("trail_tight_pct") is not None else trail
    stop = float(cfg["hard_stop_pct"]) if cfg.get("hard_stop_pct") is not None else 0.03
    busy = status.get("manual_exit") or {}
    sell_busy = bool(busy) and not busy.get("done")
    out = [
        '<div class="table-scroll desk-wide" data-live="positions">'
        f'<table class="desk" data-trail="{trail}" data-tight-after="{tight_after}" '
        f'data-tight="{tight}" data-stop="{stop}"><thead><tr>',
        "<th>Base</th><th>Entry</th><th>Mark</th><th>Gross</th>"
        "<th>Peak</th><th>Trail-stop</th><th>Hard-stop</th><th>Net</th><th>Age</th>"
        "<th>Actie</th></tr></thead><tbody>",
    ]
    cards = ['<div class="pos-cards" data-live="position-cards">']
    for p in rows:
        entry = float(p.get("entry_price") or 0)
        peak_ret = float(p.get("peak_return") or 0)
        mark = p.get("mark")
        gross = p.get("gross_return")
        side = str(p.get("side") or "long").lower()
        live_peak_ret = peak_ret
        if mark and entry > 0:
            if side == "short":
                live_peak_ret = max(peak_ret, (entry - float(mark)) / entry)
            else:
                live_peak_ret = max(peak_ret, float(mark) / entry - 1.0)
        peak_px = entry * (1 + live_peak_ret) if side != "short" else entry * (1 - live_peak_ret)
        eff_trail = tight if (tight_after > 0 and live_peak_ret >= tight_after) else trail
        if side == "short":
            # Cover stop sits above mark as price rises against the short.
            if eff_trail > 0:
                trail_px = (
                    float(p["trail_stop_px"])
                    if p.get("trail_stop_px") is not None
                    else entry * (1.0 - live_peak_ret + eff_trail)
                )
                trail_cell = (
                    f"<td class='mono' data-k='trail'>{trail_px:,.4f} "
                    f"<span class='muted'>({100 * eff_trail:.1f}%)</span></td>"
                )
                trail_meta = (
                    f"<div><span>Trail</span><span data-k='trail'>{trail_px:,.4f} "
                    f"({100 * eff_trail:.1f}%)</span></div>"
                )
            else:
                trail_cell = "<td class='muted' data-k='trail'>uit</td>"
                trail_meta = "<div><span>Trail</span><span data-k='trail'>uit</span></div>"
            if stop > 0:
                stop_px = (
                    float(p["hard_stop_px"])
                    if p.get("hard_stop_px") is not None
                    else entry * (1 + stop)
                )
                stop_cell = f"<td class='mono'>{stop_px:,.4f}</td>"
                stop_meta = f"<div><span>Hard stop</span>{stop_px:,.4f}</div>"
            else:
                stop_cell = "<td class='muted'>uit</td>"
                stop_meta = "<div><span>Hard stop</span>uit</div>"
            side_badge = " <span class='pill obs' style='font-size:.65rem'>SHORT</span>"
        else:
            trail_px = peak_px * (1 - eff_trail)
            stop_px = entry * (1 - stop)
            trail_cell = (
                f"<td class='mono' data-k='trail'>{trail_px:,.4f} "
                f"<span class='muted'>({100 * eff_trail:.1f}%)</span></td>"
            )
            stop_cell = f"<td class='mono'>{stop_px:,.4f}</td>"
            trail_meta = (
                f"<div><span>Trail</span><span data-k='trail'>{trail_px:,.4f} "
                f"({100 * eff_trail:.1f}%)</span></div>"
            )
            stop_meta = f"<div><span>Hard stop</span>{stop_px:,.4f}</div>"
            side_badge = ""
        hid = escape(str(p.get("holding_id") or p.get("base") or ""))
        sell = _sell_cell(p, disabled=sell_busy, post_action=post_sell_action)
        out.append(
            f'<tr data-holding="{hid}" data-entry="{entry}" data-side="{side}">'
            f"<td><strong>{escape(str(p.get('base')))}</strong>{side_badge}"
            f" <span class='muted' style='font-size:.7rem'>{escape(str(p.get('venue') or ''))}"
            f"</span><div class='muted' style='font-size:.7rem'>"
            f"{escape(str(p.get('entry_reason') or ''))}</div></td>"
            f"<td class='mono'>{entry:,.4f}</td>"
            f"<td class='mono' data-k='mark'>{(f'{float(mark):,.4f}' if mark else '—')}"
            f"<div class='muted' style='font-size:.65rem' data-k='mark-meta'>"
            f"{escape(str(p.get('mark_source') or '—'))}"
            f"{(' · ' + str(int(p['mark_age_sec'])) + 's') if p.get('mark_age_sec') is not None else ''}"
            f"</div></td>"
            f"<td class='{_cls(gross)}' data-k='gross'>{_fmt_pct(gross)}</td>"
            f"<td data-k='peak'>{_fmt_pct(live_peak_ret)}</td>"
            f"{trail_cell}{stop_cell}"
            f"<td class='{_cls(p.get('unrealized_net_eur'))}' data-k='net'>"
            f"{_fmt_eur(p.get('unrealized_net_eur'))}</td>"
            f"<td data-k='age'>{float(p.get('age_h') or 0):.1f}h</td>"
            f"<td>{sell}</td>"
            "</tr>"
        )
        cards.append(
            f'<div class="pos-card" data-holding="{hid}" data-entry="{entry}" data-side="{side}">'
            f'<div class="row1"><div><strong>{escape(str(p.get("base")))}</strong> '
            f'<span class="muted">{escape(str(p.get("venue") or ""))}</span></div>'
            f'<div class="{_cls(p.get("unrealized_net_eur"))}" style="font-weight:600" data-k="net">'
            f"{_fmt_eur(p.get('unrealized_net_eur'))}</div></div>"
            f'<div class="muted" style="font-size:.7rem;margin-bottom:.35rem">'
            f"{escape(str(p.get('entry_reason') or ''))}</div>"
            '<div class="meta">'
            f"<div><span>Entry</span>{entry:,.4f}</div>"
            f"<div><span>Mark</span><span data-k='mark'>"
            f"{(f'{float(mark):,.4f}' if mark else '—')}</span></div>"
            f'<div><span>Gross</span><span class="{_cls(gross)}" data-k="gross">'
            f"{_fmt_pct(gross)}</span></div>"
            f"<div><span>Peak</span><span data-k='peak'>{_fmt_pct(live_peak_ret)}</span></div>"
            f"{trail_meta}{stop_meta}"
            f"<div><span>Age</span><span data-k='age'>{float(p.get('age_h') or 0):.1f}h</span></div>"
            "</div>"
            f'<div class="actions">{sell}</div></div>'
        )
    out.append("</tbody></table></div>")
    cards.append("</div>")
    out.append("".join(cards))
    sell_all = ""
    if rows and not sell_busy and (sell_all_path or post_sell_action):
        if post_sell_action:
            sell_all_action = post_sell_action.replace("/sell", "/sell-all")
            sell_all = (
                '<div class="toolbar" style="margin-top:.55rem">'
                f'<form method="post" action="{escape(sell_all_action)}">'
                '<input type="hidden" name="redirect" value="1">'
                '<button type="submit" class="btn danger">Verkoop alles…</button></form></div>'
            )
        elif sell_all_path:
            sell_all = (
                '<div class="toolbar" style="margin-top:.55rem">'
                f'<form method="get" action="{escape(sell_all_path)}">'
                '<input type="hidden" name="sell_all" value="1">'
                '<button type="submit" class="btn danger">Verkoop alles…</button></form></div>'
            )
    out.append(sell_all)
    out.append(
        "<p class='muted' style='font-size:.72rem;margin-top:.4rem'>Verkoop = maker-order op de "
        "bied, valt na 60 s terug op taker. Wordt in de ledger geboekt als <em>manual</em>. "
        "Marks vernieuwen elke 1s via ticker.</p>"
    )
    return "".join(out)


def _donchian_positions_table(status: Mapping[str, Any]) -> str:
    """Open Donchian longs: channel exits, no 15m trail/hard-stop, no 15m sell."""
    rows = [
        p
        for p in (status.get("positions") or [])
        if float(p.get("quantity") or 0.0) > 1e-12
    ]
    empty_text = "Geen open Donchian-longs — wacht op 10d-breakout na UTC-dagclose."
    if not rows:
        return f'<p class="pos-empty muted" data-live="positions-empty">{escape(empty_text)}</p>'
    out = [
        '<div class="table-scroll desk-wide" data-live="positions" data-mode="donchian">'
        '<table class="desk" data-trail="0" data-tight-after="0" data-tight="0" data-stop="0">'
        "<thead><tr>"
        "<th>Base</th><th>Sleeve</th><th>Qty</th><th>Entry</th><th>Mark</th>"
        "<th>Gross</th><th>Notional</th><th>Net</th><th>Age</th><th>Actie</th>"
        "</tr></thead><tbody>",
    ]
    cards = ['<div class="pos-cards" data-live="position-cards" data-mode="donchian">']
    for p in rows:
        entry = float(p.get("entry_price") or 0)
        mark = p.get("mark")
        gross = p.get("gross_return")
        qty = float(p.get("quantity") or 0)
        notional = p.get("notional_eur")
        hid = escape(str(p.get("holding_id") or p.get("base") or ""))
        sleeve = escape(str(p.get("sleeve") or "donch"))
        reason = escape(str(p.get("entry_reason") or ""))
        venue = escape(str(p.get("venue") or ""))
        age = p.get("age_h")
        if age is None and p.get("opened"):
            try:
                opened = datetime.fromisoformat(str(p["opened"]).replace("Z", "+00:00"))
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=UTC)
                age = (datetime.now(UTC) - opened.astimezone(UTC)).total_seconds() / 3600.0
            except ValueError:
                age = 0.0
        age = float(age or 0)
        mark_s = f"{float(mark):,.4f}" if mark else "—"
        mark_meta = escape(str(p.get("mark_source") or "—"))
        if p.get("mark_age_sec") is not None:
            mark_meta += f" · {int(p['mark_age_sec'])}s"
        qty_s = f"{qty:,.4f}".rstrip("0").rstrip(".")
        notional_s = _fmt_eur(notional, signed=False) if notional is not None else "—"
        out.append(
            f'<tr data-holding="{hid}" data-entry="{entry}" data-side="long">'
            f"<td><strong>{escape(str(p.get('base')))}</strong>"
            f" <span class='muted' style='font-size:.7rem'>{venue}</span>"
            f"<div class='muted' style='font-size:.7rem'>{reason}</div></td>"
            f"<td class='muted'>{sleeve}</td>"
            f"<td class='mono' data-k='qty'>{qty_s}</td>"
            f"<td class='mono'>{entry:,.4f}</td>"
            f"<td class='mono' data-k='mark'>{mark_s}"
            f"<div class='muted' style='font-size:.65rem' data-k='mark-meta'>{mark_meta}</div></td>"
            f"<td class='{_cls(gross)}' data-k='gross'>{_fmt_pct(gross)}</td>"
            f"<td class='mono'>{notional_s}</td>"
            f"<td class='{_cls(p.get('unrealized_net_eur'))}' data-k='net'>"
            f"{_fmt_eur(p.get('unrealized_net_eur'))}</td>"
            f"<td data-k='age'>{age:.1f}h</td>"
            f"<td>{_sell_cell(p, disabled=False, post_action='/live/momentum/donchian/sell')}</td>"
            "</tr>"
        )
        cards.append(
            f'<div class="pos-card" data-holding="{hid}" data-entry="{entry}" data-side="long">'
            f'<div class="row1"><div><strong>{escape(str(p.get("base")))}</strong> '
            f'<span class="muted">{venue}</span></div>'
            f'<div class="{_cls(p.get("unrealized_net_eur"))}" style="font-weight:600" data-k="net">'
            f"{_fmt_eur(p.get('unrealized_net_eur'))}</div></div>"
            f'<div class="muted" style="font-size:.7rem;margin-bottom:.35rem">'
            f"{sleeve} · {reason}</div>"
            '<div class="meta">'
            f"<div><span>Qty</span><span data-k='qty'>{qty_s}</span></div>"
            f"<div><span>Notional</span>{notional_s}</div>"
            f"<div><span>Entry</span>{entry:,.4f}</div>"
            f"<div><span>Mark</span><span data-k='mark'>{mark_s}</span></div>"
            f'<div><span>Gross</span><span class="{_cls(gross)}" data-k="gross">'
            f"{_fmt_pct(gross)}</span></div>"
            f"<div><span>Age</span><span data-k='age'>{age:.1f}h</span></div>"
            "</div>"
            f"{_sell_cell(p, disabled=False, post_action='/live/momentum/donchian/sell')}</div>"
        )
    out.append("</tbody></table></div>")
    cards.append("</div>")
    out.append("".join(cards))
    out.append(
        '<form method="post" action="/live/momentum/donchian/sell-all" '
        'style="margin:.55rem 0 0">'
        '<input type="hidden" name="redirect" value="1">'
        '<button type="submit" class="btn danger">Leeg Donchian</button></form>'
    )
    out.append(
        "<p class='muted dc-pos-note' style='font-size:.72rem;margin-top:.4rem'>"
        "Geen 15m trail of hard-stop. Exit = low van de exit-N dagkaars na UTC-close, "
        "of Friday-flat pas na vrijdag UTC-close. Marks elke 1s. "
        "Verkoop loopt via de Donchian-sleeve, niet via de 15m-desk.</p>"
    )
    return "".join(out)



def _sell_cell(
    p: Mapping[str, Any],
    *,
    disabled: bool,
    confirm_path: str = "/live/momentum",
    sell_param: str = "sell",
    post_action: str | None = None,
) -> str:
    hid = str(p.get("holding_id") or "")
    if not hid:
        return ""
    if p.get("exiting"):
        return "<span class='muted' style='font-size:.75rem'>verkoop bezig…</span>"
    dis = " disabled" if disabled else ""
    if post_action:
        return (
            f'<form method="post" action="{escape(post_action)}" style="display:inline">'
            f'<input type="hidden" name="holding_id" value="{escape(hid)}">'
            '<input type="hidden" name="redirect" value="1">'
            f'<button type="submit" class="btn danger" style="font-size:.78rem;padding:.4rem .7rem;'
            f'min-height:40px"{dis}>Verkoop</button></form>'
        )
    return (
        f'<form method="get" action="{escape(confirm_path)}" style="display:inline">'
        f'<input type="hidden" name="{escape(sell_param)}" value="{escape(hid)}">'
        f'<button type="submit" class="btn danger" style="font-size:.78rem;padding:.4rem .7rem;'
        f'min-height:40px"{dis}>Verkoop</button></form>'
    )


def _sell_confirm_panel(status: Mapping[str, Any], holding_id: str) -> str:
    p = next(
        (x for x in status.get("positions") or [] if str(x.get("holding_id")) == holding_id), None
    )
    if p is None:
        return (
            '<div class="hint bad">Positie niet (meer) gevonden; mogelijk al verkocht. '
            '<a href="/live/momentum">terug</a></div>'
        )
    base = escape(str(p.get("base")))
    mark = p.get("mark")
    qty = float(p.get("quantity") or 0)
    value = qty * float(mark) if mark else None
    return (
        '<div class="card"><h2>Verkoop bevestigen</h2>'
        f"<p><strong>{base}</strong> op {escape(str(p.get('venue') or ''))}: "
        f"{qty:,.6f} stuks, entry {float(p.get('entry_price') or 0):,.4f}, "
        f"mark {(f'{float(mark):,.4f}' if mark else '—')}, waarde "
        f"{(f'{value:,.2f} €' if value is not None else '—')}, "
        f"resultaat nu <span class='{_cls(p.get('unrealized_net_eur'))}'>"
        f"{_fmt_eur(p.get('unrealized_net_eur'))}</span> netto.</p>"
        "<div style='display:flex;gap:.6rem;align-items:center;flex-wrap:wrap'>"
        f'<form method="post" action="/live/momentum/sell?holding_id={escape(holding_id)}" '
        'style="display:inline"><button type="submit" class="btn danger">'
        f"Verkoop {base} als maker (echt geld)</button></form>"
        f'<form method="post" action="/live/momentum/sell?holding_id={escape(holding_id)}'
        '&amp;urgent=1" style="display:inline"><button type="submit" class="btn danger">'
        "Verkoop direct (taker)</button></form>"
        '<a href="/live/momentum" class="muted" style="font-size:.8rem">annuleren</a></div>'
        "<p class='muted' style='font-size:.75rem;margin-top:.5rem'>Maker: order op de bied, "
        "60 s rusten, daarna taker-fallback. Taker: meteen over de spread, hogere fee.</p></div>"
    )


def _sell_all_confirm_panel(status: Mapping[str, Any]) -> str:
    rows = [
        p
        for p in (status.get("positions") or [])
        if float(p.get("quantity") or 0.0) > 1e-12
    ]
    if not rows:
        return (
            '<div class="hint bad">Geen open posities om te verkopen. '
            '<a href="/live/momentum">terug</a></div>'
        )
    lines = []
    total = 0.0
    for p in rows:
        net = float(p.get("unrealized_net_eur") or 0)
        total += net
        lines.append(
            f"<li><strong>{escape(str(p.get('base')))}</strong> "
            f"({escape(str(p.get('venue') or ''))}) · "
            f"<span class='{_cls(net)}'>{_fmt_eur(net)}</span></li>"
        )
    return (
        '<div class="card"><h2>Alles verkopen?</h2>'
        f"<p>{len(rows)} posities, open resultaat nu "
        f"<span class='{_cls(total)}'>{_fmt_eur(total)}</span>.</p>"
        f"<ul style='margin:.4rem 0 .8rem;padding-left:1.1rem'>{''.join(lines)}</ul>"
        "<div class='toolbar'>"
        '<form method="post" action="/live/momentum/sell-all">'
        '<button type="submit" class="btn danger">Alles als maker verkopen</button></form>'
        '<form method="post" action="/live/momentum/sell-all?urgent=1">'
        '<button type="submit" class="btn danger">Alles direct (taker)</button></form>'
        '<form method="get" action="/live/momentum">'
        '<button type="submit" class="btn">Annuleren</button></form>'
        "</div>"
        "<p class='muted' style='font-size:.75rem;margin-top:.5rem'>Orders gaan één voor één. "
        "Tijdens de reeks zijn andere verkopen geblokkeerd.</p></div>"
    )


def _toolbar(
    *, running: bool, has_positions: bool, hold: bool, show_volatile: bool = False,
    compact: bool = False,
) -> str:
    if not running:
        return ""
    report_label = "Report" if compact else "Daily report"
    sell_label = "Verkoop" if compact else "Verkoop alles"
    bits = [
        '<div class="toolbar">',
        '<form method="get" action="/live/momentum">'
        '<input type="hidden" name="simulate" value="1">'
        '<button type="submit" class="btn primary">Simuleer</button></form>',
        '<form method="get" action="/live/momentum">'
        '<input type="hidden" name="report" value="1">'
        f'<button type="submit" class="btn">{report_label}</button></form>',
    ]
    if has_positions:
        bits.append(
            '<form method="get" action="/live/momentum">'
            '<input type="hidden" name="sell_all" value="1">'
            f'<button type="submit" class="btn danger">{sell_label}</button></form>'
        )
    if show_volatile:
        bits.append(
            '<form method="get" action="/live/momentum/volatile">'
            '<button type="submit" class="btn">Volatile</button></form>'
        )
    if hold:
        bits.append(
            '<form method="get" action="/live/momentum">'
            '<button type="submit" class="btn">Sluiten</button></form>'
        )
    bits.append("</div>")
    return "".join(bits)


_KIND_NL = {
    "early_manual": "Te vroeg handmatig verkocht",
    "late_manual": "Handmatig later dan de auto-regel",
    "would_have_exited": "Auto-regel had al verkocht (nog open)",
    "still_open": "Nog open — auto-regel nog niet geraakt",
    "matched": "Exit in lijn met de regel",
}


def _report_panel(report: Mapping[str, Any]) -> str:
    out = [
        '<div class="card section"><div class="card-head"><h2>Daily report</h2>',
        f'<span class="muted">{escape(str(report.get("day") or ""))} UTC</span></div>',
        f'<p style="margin:.3rem 0 .7rem">{escape(str(report.get("summary") or ""))}</p>',
        f'<p>Gerealiseerd vandaag: <strong class="{_cls(report.get("realized_net_eur"))}">'
        f"{_fmt_eur(report.get('realized_net_eur'))}</strong></p>",
    ]
    decs = report.get("decisions") or []
    out.append("<h3 style='font-size:.9rem;margin:1rem 0 .4rem'>Beslissingen</h3>")
    if not decs:
        out.append('<p class="muted">Geen beslissingen in de ledger vandaag.</p>')
    else:
        out.append(
            '<div class="table-scroll"><table class="desk"><thead><tr>'
            "<th>Tijd</th><th>Regime</th><th>BTC</th><th>Breadth</th>"
            "<th>Entries</th><th>Redenen</th></tr></thead><tbody>"
        )
        for d in decs:
            ok = bool(d.get("ok"))
            ents = d.get("entries") or []
            if isinstance(ents, list) and ents and isinstance(ents[0], dict):
                ent_s = ", ".join(str(e.get("base") or e) for e in ents)
            else:
                ent_s = ", ".join(str(e) for e in ents) if ents else "—"
            out.append(
                "<tr>"
                f"<td>{_ts(d.get('ts'))}</td>"
                f"<td class='{'good' if ok else 'bad'}'>{'AAN' if ok else 'UIT'}</td>"
                f"<td>{_fmt_pct(d.get('btc_ret'))}</td>"
                f"<td>{float(d.get('breadth') or 0):.2f}</td>"
                f"<td>{escape(ent_s)}</td>"
                f"<td class='muted' style='text-align:left'>"
                f"{escape(', '.join(d.get('reasons') or []) or '—')}</td></tr>"
            )
        out.append("</tbody></table></div>")

    missed = report.get("missed_entries") or []
    out.append("<h3 style='font-size:.9rem;margin:1rem 0 .4rem'>Gemiste instappen</h3>")
    if not missed:
        out.append('<p class="muted">Geen extra instapmomenten met geldige kandidaten vandaag.</p>')
    else:
        out.append(
            '<div class="table-scroll"><table class="desk"><thead><tr>'
            "<th>Uur</th><th>Coins</th><th>BTC</th><th>Breadth</th>"
            "<th>Note</th><th>Hypo netto</th><th>Hypo exit</th></tr></thead><tbody>"
        )
        for m in missed:
            hypos = m.get("hypothetical") or []
            hypo_net = sum(float(h.get("net_eur") or 0) for h in hypos)
            hypo_ex = (
                ", ".join(
                    f"{h.get('base')} {h.get('reason') or h.get('status')} "
                    f"{_fmt_eur(h.get('net_eur'))}"
                    for h in hypos
                )
                or "—"
            )
            out.append(
                "<tr>"
                f"<td class='mono'>{int(m.get('hour_utc') or 0):02d}:00"
                f"{' ★' if m.get('scheduled') else ''}</td>"
                f"<td>{escape(', '.join(m.get('bases') or []))}</td>"
                f"<td>{_fmt_pct(m.get('btc_ret'))}</td>"
                f"<td>{float(m.get('breadth') or 0):.2f}</td>"
                f"<td class='muted' style='text-align:left'>"
                f"{escape(str(m.get('note') or ''))}</td>"
                f"<td class='{_cls(hypo_net)}'>{_fmt_eur(hypo_net)}</td>"
                f"<td class='muted' style='text-align:left;white-space:normal'>"
                f"{escape(hypo_ex)}</td></tr>"
            )
        out.append("</tbody></table></div>")
        out.append(
            '<p class="muted" style="font-size:.72rem">★ = gepland beslismoment. '
            "Hypo = wat de exit-regel later met die entry gedaan zou hebben.</p>"
        )

    ops = report.get("exit_opportunities") or []
    out.append("<h3 style='font-size:.9rem;margin:1rem 0 .4rem'>Exit-kansen</h3>")
    if not ops:
        out.append('<p class="muted">Geen entries vandaag om exits tegen af te zetten.</p>')
    else:
        out.append(
            '<div class="table-scroll"><table class="desk"><thead><tr>'
            "<th>Coin</th><th>Soort</th><th>Handmatig</th><th>Auto</th>"
            "<th>Δ netto</th><th>Piek</th></tr></thead><tbody>"
        )
        for o in ops:
            kind = str(o.get("kind") or "")
            out.append(
                "<tr>"
                f"<td><strong>{escape(str(o.get('base') or ''))}</strong></td>"
                f"<td style='text-align:left;white-space:normal'>"
                f"{escape(_KIND_NL.get(kind, kind))}</td>"
                f"<td>{_fmt_eur(o.get('actual_net_eur'))}"
                f"<div class='muted' style='font-size:.7rem'>"
                f"{escape(str(o.get('actual_reason') or '—'))} · {_ts(o.get('actual_exit_ts'))}"
                "</div></td>"
                f"<td>{_fmt_eur(o.get('auto_net_eur'))}"
                f"<div class='muted' style='font-size:.7rem'>"
                f"{escape(str(o.get('auto_reason') or '—'))} · {_ts(o.get('auto_exit_ts'))}"
                "</div></td>"
                f"<td class='{_cls(o.get('delta_eur'))}'>{_fmt_eur(o.get('delta_eur'))}</td>"
                f"<td>{_fmt_pct(o.get('peak_return'))}</td></tr>"
            )
        out.append("</tbody></table></div>")
        out.append(
            '<p class="muted" style="font-size:.72rem">Δ netto = handmatig − auto. '
            "Negatief betekent dat de auto-regel meer zou hebben opgeleverd.</p>"
        )
    out.append(
        '<p style="margin-top:.8rem"><a href="/live/momentum" class="muted">terug</a></p></div>'
    )
    return "".join(out)


def _mix_tape(btc: Any, sma20: Any, sma50: Any, label: str) -> str:
    """Price tape: SMA20 | SMA50 | BTC so the regime is visible at a glance."""
    try:
        b = float(btc) if btc is not None else None
        s20 = float(sma20) if sma20 is not None else None
        s50 = float(sma50) if sma50 is not None else None
    except (TypeError, ValueError):
        return ""
    nums = [v for v in (b, s20, s50) if v is not None and v > 0]
    if len(nums) < 2:
        return ""
    lo = min(nums)
    hi = max(nums)
    pad = (hi - lo) * 0.14 or max(hi * 0.01, 1.0)
    lo -= pad
    hi += pad
    span = hi - lo or 1.0

    def pct(v: float) -> float:
        return max(2.0, min(98.0, 100.0 * (v - lo) / span))

    s20p = pct(s20) if s20 else None
    s50p = pct(s50) if s50 else None
    # Classifier: BTC>SMA50 → risk_on; BTC<SMA20 → risk_off; else mid.
    # When SMA20>SMA50 (typical uptrend) there is no mid band — split at SMA50.
    if s20p is not None and s50p is not None and s20p < s50p:
        left_w, mid_w, right_w = s20p, s50p - s20p, 100.0 - s50p
    elif s50p is not None:
        left_w, mid_w, right_w = s50p, 0.0, 100.0 - s50p
    elif s20p is not None:
        left_w, mid_w, right_w = s20p, 0.0, 100.0 - s20p
    else:
        left_w, mid_w, right_w = 33.0, 34.0, 33.0
    z_off = "cur" if label == "risk_off" else ""
    z_mid = "cur" if label == "mid" else ""
    z_on = "cur" if label == "risk_on" else ""
    marks = []
    if s20:
        marks.append(
            f'<span class="mark" style="left:{s20p:.1f}%"><i></i>SMA20</span>'
        )
    if s50:
        marks.append(
            f'<span class="mark" style="left:{s50p:.1f}%"><i></i>SMA50</span>'
        )
    if b:
        marks.append(
            f'<span class="mark btc" style="left:{pct(b):.1f}%"><i></i>BTC</span>'
        )
    mid_html = (
        f'<div class="z mid {z_mid}" style="width:{mid_w:.1f}%">chop</div>'
        if mid_w >= 4.0
        else (f'<div class="z mid {z_mid}" style="width:{mid_w:.1f}%"></div>' if mid_w > 0.05 else "")
    )
    return (
        f'<div class="mix-tape" data-live="mix-tape">'
        f'<div class="zones">'
        f'<div class="z off {z_off}" style="width:{left_w:.1f}%">risk off</div>'
        f"{mid_html}"
        f'<div class="z on {z_on}" style="width:{right_w:.1f}%">risk on</div>'
        f"</div>{''.join(marks)}</div>"
    )


def _donchian_decision_panel(donchian: Mapping[str, Any] | None) -> str:
    """Per-sleeve last Donchian decision (why this long is on/off)."""
    sleeves = (donchian or {}).get("sleeves") or []
    if not sleeves:
        return '<p class="muted">Donchian-sleeves starten met de mix.</p>'
    bits: list[str] = []
    nxt = (donchian or {}).get("next_decision")
    mix_label = str(((donchian or {}).get("allocator") or {}).get("label") or "")
    for sl in sleeves:
        on = bool(sl.get("active"))
        cap = str(sl.get("live_caption") or "")
        if not cap:
            cap = sleeve_live_caption(
                n_positions=int(sl.get("n_positions") or len(sl.get("positions") or [])),
                friday_flatten=bool(sl.get("friday_flatten")),
                risk_block=str(sl.get("risk_block") or ""),
                mix_label=mix_label,
                exit_n=int(sl.get("exit_n") or 5),
                channel=int(sl.get("channel") or 10),
                next_decision=nxt,
                allocator_active=on,
            )
        bits.append(
            f"<div><strong>{escape(str(sl.get('title') or sl.get('id')))}</strong> "
            f"<span class='pill {'on' if on else 'off'}'>{'AAN' if on else 'UIT'}</span>"
            f"<div class='muted'>{escape(cap)}</div></div>"
        )
    return (
        f'<div class="mix-dc-dec">{"".join(bits)}</div>'
        f'<p class="muted" style="margin:.55rem 0 0">volgende dagbesluit {_ts(nxt)}</p>'
    )


def _mix_panel(
    alloc: Mapping[str, Any] | None,
    donchian: Mapping[str, Any] | None = None,
    short_weakest: Mapping[str, Any] | None = None,
    core_15m: Mapping[str, Any] | None = None,
) -> str:
    """Which strategy is on, how much €, and why (loop mix)."""
    a = dict(alloc or {})
    if not a:
        return (
            '<section class="panel mix-board" data-live="mix-board">'
            '<div class="card-head"><h2>Actieve mix</h2>'
            '<span class="pill off"><span class="dot"></span>GEEN DATA</span></div>'
            '<p class="muted">Allocator nog niet geladen.</p></section>'
        )
    label = str(a.get("label") or (a.get("regime") or {}).get("label") or "mid")
    why = str(a.get("why") or (a.get("regime") or {}).get("why") or "")
    nt = a.get("now_trading") or {}
    headline = str(nt.get("headline") or "")
    if not headline:
        active_titles = [
            str(sl.get("title") or sl.get("id"))
            for sl in (a.get("sleeves") or [])
            if sl.get("active")
        ]
        headline = " + ".join(active_titles) if active_titles else "Cash — geen trades"
    stance = str(nt.get("stance") or "")
    idle = " · ".join(str(x) for x in (nt.get("idle_titles") or [])[:4])
    reg = a.get("regime") or {}
    pill_cls = {"risk_on": "on", "risk_off": "obs", "mid": "off"}.get(label, "off")
    pill_txt = {"risk_on": "RISK ON", "risk_off": "RISK OFF", "mid": "MID · CASH"}.get(
        label, label.upper()
    )
    btc = reg.get("btc")
    sma20 = reg.get("sma20")
    sma50 = reg.get("sma50")
    gap50 = reg.get("gap_vs_sma50_pct")
    gap20 = reg.get("gap_vs_sma20_pct")
    don_live = {str(s.get("id")): s for s in ((donchian or {}).get("sleeves") or [])}
    pos_by: dict[str, list[Mapping[str, Any]]] = {}
    for p in (donchian or {}).get("positions") or []:
        pos_by.setdefault(str(p.get("sleeve") or ""), []).append(p)
    for p in (short_weakest or {}).get("positions") or []:
        pos_by.setdefault("short_weakest", []).append(p)

    def px(v: Any) -> str:
        try:
            return f"{float(v):,.0f}"
        except (TypeError, ValueError):
            return "—"

    cards = []
    for sl in a.get("sleeves") or []:
        sid = str(sl.get("id") or "")
        on = bool(sl.get("active"))
        cls = "on" if on else "off"
        state = "AAN" if on else "UIT"
        live = don_live.get(sid) or {}
        n_held = len(pos_by.get(sid) or [])
        friday = bool(live.get("friday_flatten")) or sid in {"donch_fri10", "donch_fri"}
        if sid.startswith("donch"):
            why_s = str(live.get("live_caption") or "") or sleeve_live_caption(
                n_positions=n_held,
                friday_flatten=friday,
                risk_block=str(live.get("risk_block") or ""),
                mix_label=label,
                exit_n=int(live.get("exit_n") or (10 if sid == "donch_fri" else 5)),
                channel=int(live.get("channel") or (20 if sid == "donch_fri" else 10)),
                next_decision=(donchian or {}).get("next_decision"),
                allocator_active=on,
            )
        else:
            why_s = str(sl.get("why") or sl.get("blurb") or "")
        pos_bits = []
        for p in pos_by.get(sid) or []:
            net = p.get("unrealized_net_eur")
            pos_bits.append(
                f'<span class="mix-chip"><strong>{escape(str(p.get("base") or ""))}</strong>'
                f'<span class="{_cls(net)}">{_fmt_eur(net)}</span></span>'
            )
        pos_html = (
            f'<div class="mix-open" data-k="mix-pos">{"".join(pos_bits)}</div>'
            if pos_bits
            else '<div class="mix-open" data-k="mix-pos"></div>'
        )
        cards.append(
            f'<div class="mix-sleeve {cls}" data-mix-sleeve="{escape(sid)}">'
            f'<h3>{escape(str(sl.get("title") or sid))} '
            f'<span class="pill {"on" if on else "off"}" style="margin-left:.35rem">{state}</span></h3>'
            f'<div class="eur" data-k="mix-eur">{_fmt_eur(sl.get("target_eur"), signed=False)}</div>'
            f'<p data-k="mix-why-s">{escape(why_s)}</p>'
            f"{pos_html}</div>"
        )
    open_all = []
    for p in (donchian or {}).get("positions") or []:
        open_all.append(
            f'<span class="mix-chip" data-mix-pos="{escape(str(p.get("holding_id") or p.get("base") or ""))}">'
            f'<span class="muted">{escape(str(p.get("sleeve") or "donch"))}</span>'
            f'<strong>{escape(str(p.get("base") or ""))}</strong>'
            f'<span class="{_cls(p.get("unrealized_net_eur"))}">{_fmt_eur(p.get("unrealized_net_eur"))}</span>'
            f"</span>"
        )
    for p in (short_weakest or {}).get("positions") or []:
        open_all.append(
            f'<span class="mix-chip">'
            f'<span class="muted">short</span>'
            f'<strong>{escape(str(p.get("base") or ""))}</strong>'
            f'<span class="{_cls(p.get("unrealized_net_eur"))}">{_fmt_eur(p.get("unrealized_net_eur"))}</span>'
            f"</span>"
        )
    open_html = (
        f'<div class="mix-open" data-live="mix-open">{"".join(open_all)}</div>'
        if open_all
        else '<div class="mix-open" data-live="mix-open"><span class="muted">Geen open mix-posities.</span></div>'
    )
    don_run = bool((donchian or {}).get("running"))
    don_live = don_run and not bool((donchian or {}).get("dry_run", True))
    if don_live:
        run_pill = '<span class="pill on"><span class="dot"></span>DONCHIAN LIVE</span>'
    elif don_run:
        run_pill = '<span class="pill obs"><span class="dot"></span>MIX PAPER</span>'
    else:
        run_pill = '<span class="pill obs"><span class="dot"></span>MIX</span>'
    return (
        f'<section class="panel mix-board {escape(label)}" data-live="mix-board" '
        f'data-mix-label="{escape(label)}" id="mix">'
        f'<div class="card-head"><h2>Welke strategie nu</h2>'
        f"{run_pill}"
        f'<span class="pill {pill_cls}" data-live="mix-pill"><span class="dot"></span>'
        f'<span data-k="mix-label">{escape(pill_txt)}</span></span></div>'
        f'<p class="mix-k">Nu actief</p>'
        f'<div class="mix-now" data-k="mix-now">{escape(headline)}</div>'
        f'<p class="mix-stance" data-k="mix-stance">{escape(stance)}</p>'
        f'<p class="mix-why" data-k="mix-why">{escape(why)}</p>'
        f"{_mix_tape(btc, sma20, sma50, label)}"
        f'<div class="mix-gates">'
        f'<div><span>BTC</span><strong data-k="mix-btc">{px(btc)}</strong></div>'
        f'<div><span>SMA20</span><strong data-k="mix-sma20">{px(sma20)}</strong></div>'
        f'<div><span>SMA50</span><strong data-k="mix-sma50">{px(sma50)}</strong></div>'
        f'<div><span>vs SMA50</span><strong data-k="mix-gap50" class="{_cls(gap50)}">'
        f"{_fmt_pct(gap50)}</strong>"
        f'<span class="muted" style="margin-top:.15rem">vs SMA20 '
        f'<span data-k="mix-gap20">{_fmt_pct(gap20)}</span></span></div>'
        f"</div>"
        f'<div class="mix-sleeves" data-live="mix-sleeves">{"".join(cards)}</div>'
        f'<p class="mix-k" style="margin-top:.85rem">Open in deze mix</p>'
        f"{open_html}"
        f'<p class="mix-foot">Classifier sma20/50 · boek {_fmt_eur(a.get("book_eur"), signed=False)}. '
        f"{'Donchian-longs zijn LIVE Bitvavo-orders.' if don_live else 'Donchian-longs draaien paper.'} "
        f"Decide na UTC-dagclose (00:05) op de gesloten 1d-kaars · Friday-flat pas na vrijdagclose. "
        f"{_core_15m_mix_note(core_15m)} "
        f"Short-weakest is een apart paper-boek (BTC&lt;SMA20) en telt niet mee in deze live equity. "
        f"BTC+RS-clip is de live €20k-owner op Bitvavo (na het 15m-plafond)."
        f"{(' Uit: ' + escape(idle) + '.') if idle else ''}</p>"
        f"</section>"
    )


def _core_15m_mix_note(status: Mapping[str, Any] | None) -> str:
    st = status or {}
    running = bool(st.get("running"))
    dry = bool(st.get("dry_run"))
    if running and not dry:
        return (
            "15m WR-core is de €2k Bitvavo-satelliet (OKX-rest mag mee); "
            "niet de €20k-owner."
        )
    if running:
        return "15m WR-core draait ernaast in shadow (geen live orders)."
    return "15m WR-core staat idle."


def _core_15m_panel(status: Mapping[str, Any] | None) -> str:
    """Live 15m WR-core satellite — €2k Bitvavo cap + OKX leftover."""
    st = dict(status or {})
    running = bool(st.get("running"))
    dry = bool(st.get("dry_run"))
    if running and not dry:
        pill = '<span class="pill on" data-live="core15-pill"><span class="dot"></span>LIVE</span>'
    elif running:
        pill = '<span class="pill obs" data-live="core15-pill"><span class="dot"></span>SHADOW</span>'
    else:
        pill = '<span class="pill off" data-live="core15-pill"><span class="dot"></span>STOP</span>'
    cash_by = st.get("cash_by_venue") or {}
    if cash_by:
        cash_txt = " · ".join(
            f"{escape(str(k))} {float(v):,.0f} €" for k, v in cash_by.items()
        )
    else:
        cash_txt = f"cash {_fmt_eur(st.get('cash_eur'), signed=False)}"
    caps = st.get("venue_cash_caps") or {}
    cap_txt = (
        " · ".join(f"{escape(str(k))}≤{float(v):,.0f} €" for k, v in caps.items())
        if caps
        else "geen Bitvavo-plafond"
    )
    pos_bits = []
    for p in st.get("positions") or []:
        if float(p.get("quantity") or 0.0) <= 1e-12:
            continue
        net = p.get("unrealized_net_eur")
        hid = escape(str(p.get("holding_id") or p.get("base") or ""))
        pos_bits.append(
            f'<span class="mix-chip" data-holding="{hid}">'
            f'<span class="muted">{escape(str(p.get("venue") or ""))}</span>'
            f'<strong>{escape(str(p.get("base") or ""))}</strong>'
            f'<span class="{_cls(net)}" data-k="net">{_fmt_eur(net)}</span></span>'
        )
    pos_html = (
        f'<div class="mix-open" data-live="core15-open">{"".join(pos_bits)}</div>'
        if pos_bits
        else '<div class="mix-open" data-live="core15-open"><span class="muted">Geen open 15m-posities — wacht op slot 7/13/16 UTC.</span></div>'
    )
    n_pos = len(pos_bits)
    actions = ""
    if running:
        bits = [
            '<form method="get" action="/live/momentum" style="display:inline">'
            '<input type="hidden" name="simulate" value="1">'
            '<button type="submit" class="btn">Simuleer</button></form>'
        ]
        if n_pos:
            bits.append(
                '<form method="get" action="/live/momentum" style="display:inline">'
                '<input type="hidden" name="sell_all" value="1">'
                '<button type="submit" class="btn danger">Verkoop 15m</button></form>'
            )
        actions = f'<div class="toolbar" style="margin:.4rem 0 0">{"".join(bits)}</div>'
    risk = st.get("risk") or {}
    block = ""
    if not risk.get("entries_allowed", True):
        block = (
            f' · geblokkeerd: {escape(str(risk.get("block_reason") or "risk"))}'
        )
    eq = st.get("equity_eur") if running else None
    cash_show = cash_txt if running else "—"
    cap_show = cap_txt if running else "gestopt"
    return (
        f'<section class="panel mix-board side-15m" id="core-15m" data-live="core-15m">'
        f'<div class="card-head"><h2>15m WR-core</h2>{pill}'
        f'<span class="pill {"on" if running and not dry else "off"}">'
        f'<span class="dot"></span>€2k-satelliet</span></div>'
        f'<p class="mix-why" data-live="core15-caption">Satelliet naast de BTC+RS-clip: '
        f"Bitvavo-plafond €2k, OKX-rest mag mee. Hours 7/13/16 UTC{block}.</p>"
        f'<div class="clip-kpis">'
        f'<div><span>Equity</span><strong data-k="core15-eq">{_fmt_eur(eq, signed=False)}</strong></div>'
        f'<div><span>Open</span><strong data-k="core15-open-pnl" class="{_cls(st.get("unrealized_net_eur") if running else None)}">'
        f'{_fmt_eur(st.get("unrealized_net_eur") if running else None)}</strong></div>'
        f'<div><span>Gerealiseerd</span><strong data-k="core15-real" class="{_cls(st.get("realized_total_eur") if running else None)}">'
        f'{_fmt_eur(st.get("realized_total_eur") if running else None)}</strong></div>'
        f'<div><span>Cash</span><strong data-k="core15-cash">{escape(cash_show)}</strong></div>'
        f"</div>"
        f'<p class="muted" style="font-size:.78rem;margin:.15rem 0 .4rem">'
        f'<span data-k="core15-caps">{escape(cap_show)}</span> · next '
        f'<strong data-live="core15-next">{_ts(st.get("next_decision") if running else None)}</strong></p>'
        f"{pos_html}{actions}</section>"
    )


def _paper_clip_panel(status: Mapping[str, Any] | None) -> str:
    st = status or {}
    running = bool(st.get("running"))
    dry = bool(st.get("dry_run", True))
    allow_live = st.get("allow_live")
    live = running and not dry and allow_live is not False
    if not running:
        pill = '<span class="pill off" data-live="clip-pill"><span class="dot"></span>STOP</span>'
        title = "BTC + RS-clip"
    elif live:
        pill = '<span class="pill on" data-live="clip-pill"><span class="dot"></span>LIVE</span>'
        title = "Live · BTC + RS-clip"
    else:
        pill = '<span class="pill obs" data-live="clip-pill"><span class="dot"></span>PAPER</span>'
        title = "Paper · BTC + RS-clip"
    caption = str(st.get("live_caption") or st.get("last_decision", {}).get("caption") or "")
    if not caption:
        caption = (
            "75% BTC boven SMA50, max 25% wekelijkse RS-alt. Live €20k-owner, 15m blijft €2k-satelliet."
            if live
            else "75% BTC boven SMA50, max 25% wekelijkse RS-alt. Shadow €20k, geen Bitvavo-orders."
        )
    pos_bits = []
    for p in st.get("positions") or []:
        net = p.get("unrealized_net_eur")
        role = escape(str(p.get("role") or "long"))
        pos_bits.append(
            f'<span class="mix-chip" data-holding="{escape(str(p.get("holding_id") or p.get("base") or ""))}">'
            f'<span class="muted">{role}</span>'
            f'<strong>{escape(str(p.get("base") or ""))}</strong>'
            f'<span class="{_cls(net)}" data-k="net">{_fmt_eur(net)}</span></span>'
        )
    empty = (
        "Nog geen live-posities — eerste decide koopt 75% BTC + RS-alt."
        if live
        else "Nog geen paper-posities — eerste decide na start."
    )
    pos_html = (
        f'<div class="mix-open" data-live="clip-open">{"".join(pos_bits)}</div>'
        if pos_bits
        else f'<div class="mix-open" data-live="clip-open"><span class="muted">{empty}</span></div>'
    )
    risk_on = bool(st.get("risk_on"))
    gate = "BTC &gt; SMA50" if risk_on else "cash (BTC ≤ SMA50)"
    foot = (
        "Owner · Bitvavo live · 15m €2k-satelliet gereserveerd."
        if live
        else "geen live orders, geen mix-cash."
    )
    return (
        f'<section class="panel mix-board paper-clip" id="paper-clip" data-live="paper-clip">'
        f'<div class="card-head"><h2 data-live="clip-title">{escape(title)}</h2>{pill}'
        f'<span class="pill {"on" if risk_on else "off"}" data-live="clip-gate">'
        f'<span class="dot"></span>{gate}</span></div>'
        f'<p class="mix-why" data-live="clip-caption">{escape(caption)}</p>'
        f'<div class="clip-kpis">'
        f'<div><span>Equity</span><strong data-k="clip-eq">{_fmt_eur(st.get("equity_eur"), signed=False)}</strong></div>'
        f'<div><span>Open</span><strong data-k="clip-open" class="{_cls(st.get("unrealized_net_eur"))}">'
        f'{_fmt_eur(st.get("unrealized_net_eur"))}</strong></div>'
        f'<div><span>Gerealiseerd</span><strong data-k="clip-real" class="{_cls(st.get("realized_total_eur"))}">'
        f'{_fmt_eur(st.get("realized_total_eur"))}</strong></div>'
        f'<div><span>Boek</span><strong data-k="clip-book">{_fmt_eur(st.get("book_eur"), signed=False)}</strong></div>'
        f"</div>"
        f'<p class="muted" style="font-size:.78rem;margin:.15rem 0 .4rem">Alt: '
        f'<strong data-k="clip-alt">{escape(str(st.get("want_alt") or "—"))}</strong> · '
        f'next <strong data-live="clip-next">{_ts(st.get("next_decision"))}</strong> · '
        f'<span data-live="clip-foot">{foot}</span></p>'
        f"{pos_html}</section>"
    )


def _paper_sw_panel(status: Mapping[str, Any] | None) -> str:
    """Independent paper short-weakest book beside the live Donchian mix."""
    st = status or {}
    running = bool(st.get("running"))
    pill = (
        '<span class="pill obs"><span class="dot"></span>PAPER</span>'
        if running
        else '<span class="pill off"><span class="dot"></span>STOP</span>'
    )
    bear = st.get("bear") or {}
    pack = st.get("pack") or st.get("config") or {}
    bear_ok = bool(bear.get("bear_ok"))
    sma_n = int(bear.get("sma_days") or pack.get("sma_days") or 20)
    gate = "BEAR ON" if bear_ok else f"STANDBY (BTC&gt;SMA{sma_n})"
    caption = str(st.get("live_caption") or "")
    if not caption:
        caption = (
            "Paper short op de zwakste alt als BTC onder SMA20. "
            "Apart boek naast Donchian, geen Bitvavo-orders."
        )
    pos_bits = []
    for p in st.get("positions") or []:
        net = p.get("unrealized_net_eur")
        pos_bits.append(
            f'<span class="mix-chip" data-holding="{escape(str(p.get("holding_id") or p.get("base") or ""))}">'
            f'<span class="muted">short</span>'
            f'<strong>{escape(str(p.get("base") or ""))}</strong>'
            f'<span class="{_cls(net)}" data-k="net">{_fmt_eur(net)}</span></span>'
        )
    pos_html = (
        f'<div class="mix-open" data-live="sw-open">{"".join(pos_bits)}</div>'
        if pos_bits
        else (
            '<div class="mix-open" data-live="sw-open">'
            '<span class="muted">Geen open paper-shorts — standby tot BTC &lt; SMA20.</span></div>'
        )
    )
    btc = bear.get("btc")
    sma = bear.get("sma") if bear.get("sma") is not None else bear.get("sma200")
    gap = bear.get("gap_pct")
    btc_s = f"{float(btc):,.0f}" if btc is not None else "—"
    sma_s = f"{float(sma):,.0f}" if sma is not None else "—"
    return (
        f'<section class="panel mix-board paper-sw" id="paper-sw" data-live="paper-sw">'
        f'<div class="card-head"><h2>Paper · Short weakest</h2>{pill}'
        f'<span class="pill {"on" if bear_ok else "off"}" data-live="sw-gate">'
        f'<span class="dot"></span>{gate}</span></div>'
        f'<p class="mix-why" data-live="sw-caption">{escape(caption)}</p>'
        f'<div class="clip-kpis">'
        f'<div><span>Equity</span><strong data-k="sw-equity">{_fmt_eur(st.get("equity_eur"), signed=False)}</strong></div>'
        f'<div><span>Open</span><strong data-k="sw-open" class="{_cls(st.get("unrealized_net_eur"))}">'
        f'{_fmt_eur(st.get("unrealized_net_eur"))}</strong></div>'
        f'<div><span>Gerealiseerd</span><strong data-k="sw-realized" class="{_cls(st.get("realized_total_eur"))}">'
        f'{_fmt_eur(st.get("realized_total_eur"))}</strong></div>'
        f'<div><span>Boek</span><strong data-k="sw-book">{_fmt_eur(st.get("book_eur") or pack.get("book_eur"), signed=False)}</strong></div>'
        f"</div>"
        f'<p class="muted" style="font-size:.78rem;margin:.15rem 0 .4rem">'
        f'BTC <strong data-k="sw-btc">{escape(btc_s)}</strong> / '
        f'SMA{sma_n} <strong data-k="sw-sma">{escape(sma_s)}</strong> · '
        f'gap <span data-k="sw-gap" class="{_cls(gap)}">{_fmt_pct(gap) if gap is not None else "—"}</span> · '
        f'next <strong data-live="sw-next">{_ts(st.get("next_decision"))}</strong> · '
        f"geen live orders, geen mix-cash.</p>"
        f"{pos_html}</section>"
    )


def _short_weakest_decision_panel(status: Mapping[str, Any]) -> str:
    """Bear-harvest decision / regime view (not the long-desk panel)."""
    reg = status.get("last_regime") or {}
    bear = status.get("bear") or reg.get("bear") or {}
    pack = status.get("pack") or status.get("config") or {}
    risk = status.get("risk") or {}
    bear_ok = bool(bear.get("bear_ok"))
    sma_days = int(bear.get("sma_days") or pack.get("sma_days") or 20)
    if bear_ok:
        pill = '<span class="pill on"><span class="dot"></span>BEAR ON</span>'
    else:
        pill = '<span class="pill off"><span class="dot"></span>STANDBY</span>'
    btc = bear.get("btc")
    sma = bear.get("sma") if bear.get("sma") is not None else bear.get("sma200")
    gap = bear.get("gap_pct")
    gap_s = _fmt_pct(gap) if gap is not None else "—"
    btc_s = f"{float(btc):,.0f}" if btc is not None else "—"
    sma_s = f"{float(sma):,.0f}" if sma is not None else "—"
    block = risk.get("block_reason") or reg.get("risk_block") or ""
    block_html = (
        f'<div class="warn">Blok: {escape(str(block))}</div>' if block else ""
    )
    cands = reg.get("candidates") or []
    planned = reg.get("planned") or []
    cand_html = (
        "".join(
            f"<span>{escape(str(c.get('base')))} "
            f"mom {_fmt_pct(c.get('mom') if c.get('mom') is not None else c.get('score'))}"
            f"</span>"
            for c in cands[:6]
        )
        or '<span class="muted">geen short-kandidaten</span>'
    )
    planned_html = (
        ", ".join(
            f"{escape(str(p.get('base')))} {_fmt_eur(p.get('notional_eur'), signed=False)}"
            for p in planned
        )
        or "—"
    )
    rejected = reg.get("rejected") or []
    rej_counts: dict[str, int] = {}
    for r in rejected:
        key = str(r.get("reason") or "?")
        rej_counts[key] = rej_counts.get(key, 0) + 1
    rej_s = ", ".join(f"{k}×{v}" for k, v in sorted(rej_counts.items())) or "—"
    at = reg.get("at") or status.get("next_decision")
    pack_line = (
        f"top{pack.get('top_n', 1)} · lb{pack.get('lookback_days', 15)} "
        f"skip{pack.get('skip_days', 2)} · floor {_fmt_pct(pack.get('mom_floor'))} · "
        f"reb {pack.get('rebalance_days', 30)}d · mw {pack.get('max_weight', 0.5)} · "
        f"stops {'uit' if not float(pack.get('trail_pct') or 0) else 'aan'}"
    )
    return (
        f'<div data-live="sw-decision">'
        f"<div>{pill} <span class='muted' data-live='sw-role'>"
        f"{escape(str(status.get('role') or ''))}</span> · "
        f"<span class='muted'>om {_ts(at)}</span></div>"
        f"<div class='rules' style='margin-top:.7rem' data-live='sw-bear'>"
        f"<div><span>BTC</span><strong data-k='sw-btc'>{btc_s}</strong></div>"
        f"<div><span>SMA{sma_days}</span><strong data-k='sw-sma'>{sma_s}</strong></div>"
        f"<div><span>Gap vs SMA</span><span data-k='sw-gap' class='{_cls(gap)}'>{gap_s}</span></div>"
        f"<div><span>Pack</span>{escape(pack_line)}</div>"
        f"<div><span>Equity</span><strong data-k='sw-equity'>"
        f"{_fmt_eur(status.get('equity_eur'), signed=False)}</strong></div>"
        f"<div><span>Gerealiseerd</span><span data-k='sw-realized' class='{_cls(status.get('realized_total_eur'))}'>"
        f"{_fmt_eur(status.get('realized_total_eur'))}</span></div>"
        f"<div><span>Open PnL</span><span data-k='sw-open' class='{_cls(status.get('unrealized_net_eur'))}'>"
        f"{_fmt_eur(status.get('unrealized_net_eur'))}</span></div>"
        f"<div><span>Planned</span>{planned_html}</div>"
        f"<div><span>Rejected</span>{escape(rej_s)}</div>"
        f"</div>"
        f"<div class='chips' style='margin-top:.7rem' data-live='sw-cands'>{cand_html}</div>"
        f"{block_html}</div>"
    )


def _decision_panel(status: Mapping[str, Any]) -> str:
    reg = status.get("last_regime") or {}
    if not reg:
        return (
            '<p class="muted">Nog geen beslissing genomen. Eerste beslismoment: '
            f"<strong>{_ts(status.get('next_decision'))}</strong>.</p>"
        )
    ok = bool(reg.get("ok"))
    soft = bool(reg.get("soft"))
    label = str(reg.get("regime_label") or "")
    if soft:
        pill = '<span class="pill on"><span class="dot"></span>REGIME SOFT</span>'
    elif ok:
        pill = '<span class="pill on"><span class="dot"></span>REGIME ON</span>'
    else:
        pill = '<span class="pill off"><span class="dot"></span>REGIME IDLE</span>'
    reasons = ", ".join(reg.get("reasons") or []) or "—"
    cands = reg.get("candidates") or []
    cand_html = (
        "".join(
            f"<span>{escape(str(c.get('base')))} {_fmt_pct(c.get('excess'))} "
            f"<em class='muted'>{_fmt_pct(c.get('from_high'))} vs high</em></span>"
            for c in cands
        )
        or '<span class="muted">geen kandidaten</span>'
    )
    entries = ", ".join(reg.get("entries") or []) or "—"
    ai = reg.get("alphai") or {}
    ai_html = (
        f"macro caution: <strong>{'ja' if ai.get('macro_caution') else 'nee'}</strong> · "
        f"avoid: {escape(', '.join(ai.get('avoid') or []) or '—')} · "
        f"picks: {escape(', '.join(ai.get('picks') or []) or '—')}"
    )
    block = reg.get("risk_block") or ""
    block_html = f'<div class="warn">Risk-blok: {escape(str(block))}</div>' if block else ""
    label_html = f"<div><span>Label</span>{escape(label)}</div>" if label else ""
    pnl_bits: list[str] = []
    for key in ("strong", "firm", "soft", "weak"):
        bucket = (status.get("regime_pnl") or {}).get(key) or {}
        if not bucket.get("n"):
            continue
        wr = bucket.get("win_rate")
        wr_txt = f"{100 * float(wr):.0f}%" if wr is not None else "—"
        pnl_bits.append(
            f"{key}: {int(bucket['n'])}× {_fmt_eur(bucket.get('net_eur'))} (WR {wr_txt})"
        )
    pnl_html = (
        f"<div><span>Regime PnL</span>{escape(' · '.join(pnl_bits))}</div>" if pnl_bits else ""
    )
    return (
        f"<div>{pill} <span class='muted'>om {_ts(reg.get('at'))}</span></div>"
        f"<div class='rules' style='margin-top:.7rem'>"
        f"{label_html}"
        f"<div><span>BTC 24u</span>{_fmt_pct(reg.get('btc_ret'))}</div>"
        f"<div><span>Breadth</span>{float(reg.get('breadth') or 0):.2f}</div>"
        f"<div><span>Redenen</span>{escape(reasons)}</div>"
        f"<div><span>Entries</span>{escape(entries)}</div>"
        f"{pnl_html}"
        f"</div>"
        f"<div class='chips' style='margin-top:.7rem'>{cand_html}</div>"
        f"<div class='muted' style='margin-top:.6rem;font-size:.78rem'>AlphaI — {ai_html}</div>"
        f"{block_html}"
    )


_SCENARIOS: tuple[float, ...] = (-0.03, 0.0, 0.03, 0.05, 0.08)


def _simulate_button() -> str:
    return (
        '<form method="get" action="/live/momentum" style="display:inline">'
        '<input type="hidden" name="simulate" value="1">'
        '<button type="submit" class="btn">Simuleer beslissing nu</button></form>'
    )


def _expectancy_line(ledger_rows: Sequence[Mapping[str, Any]]) -> str:
    exits = [r for r in ledger_rows if r.get("event") == "exit"]
    if not exits:
        return (
            '<p class="muted" style="font-size:.78rem">Nog geen afgesloten trades van deze desk; '
            "de tabel toont daarom scenario's, geen voorspelling. Winst hangt af van de markt: "
            "de desk begrenst het verlies (hard stop), de winst loopt mee met de trail.</p>"
        )
    nets = [float(r.get("net_eur") or 0) for r in exits]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    avg = sum(nets) / len(nets)
    return (
        f'<p class="muted" style="font-size:.78rem">Historie van deze desk: {len(nets)} trades, '
        f"win {100 * len(wins) / len(nets):.0f}%, gemiddeld {_fmt_eur(avg)} per trade "
        f"(winst gem. {_fmt_eur(sum(wins) / len(wins)) if wins else '—'}, "
        f"verlies gem. {_fmt_eur(sum(losses) / len(losses)) if losses else '—'}).</p>"
    )


def _preview_panel(
    preview: Mapping[str, Any], ledger_rows: Sequence[Mapping[str, Any]], commit: Mapping[str, Any]
) -> str:
    planned = [p for p in preview.get("planned") or []]
    ok = bool(preview.get("ok"))
    soft = bool(preview.get("soft"))
    label = str(preview.get("regime_label") or ("soft" if soft else ("firm" if ok else "weak")))
    regime_state = "AAN" if ok else "IDLE"
    if soft:
        regime_state = "SOFT"
    regime = (
        f"Regime <strong class='{'good' if ok else 'bad'}'>{regime_state}</strong> "
        f"<span class='muted'>({escape(label)})</span> · "
        f"BTC 24h {_fmt_pct(preview.get('btc_ret'))} · breadth "
        f"{100 * float(preview.get('breadth') or 0):.0f}% · bar {_ts(preview.get('at'))}"
    )
    if preview.get("reasons"):
        regime += f" · <span class='bad'>{escape(', '.join(preview['reasons']))}</span>"
    if preview.get("risk_block"):
        regime += f" · <span class='bad'>risico-blok: {escape(str(preview['risk_block']))}</span>"
    ai = preview.get("alphai") or {}
    ai_bits = []
    if ai.get("macro_caution"):
        ai_bits.append("macro caution (clip ×0,7)")
    if ai.get("avoid"):
        ai_bits.append("avoid: " + ", ".join(ai["avoid"]))
    if ai.get("picks"):
        ai_bits.append("picks: " + ", ".join(ai["picks"]))
    ai_line = (
        f"<div class='muted' style='font-size:.78rem'>AlphaI: {escape(' · '.join(ai_bits))}</div>"
        if ai_bits
        else ""
    )
    out = [f"<p style='margin:.2rem 0 .6rem'>{regime}</p>{ai_line}"]
    if not planned:
        out.append(
            '<p class="muted">De desk zou nu <strong>niets kopen</strong>. Er is dus ook niets '
            "te committen.</p>"
        )
    else:
        head = "".join(
            f"<th>{'stop' if s < 0 else 'exit'} {s:+.0%}</th>".replace("+0%", "±0%")
            for s in _SCENARIOS
        )
        out.append(
            '<table class="desk"><thead><tr><th>Coin</th><th>Venue</th><th>Clip</th>'
            f"<th>Prijs</th><th>Break-even</th>{head}</tr></thead><tbody>"
        )
        totals = [0.0] * len(_SCENARIOS)
        for p in planned:
            clip = float(p.get("clip_eur") or 0)
            fee_rt = float(p.get("fee_in_eur") or 0) + float(p.get("fee_out_eur") or 0)
            cells = []
            for i, s in enumerate(_SCENARIOS):
                pnl = clip * s - fee_rt
                totals[i] += pnl
                cells.append(f"<td class='{_cls(pnl)}'>{_fmt_eur(pnl)}</td>")
            price = p.get("price")
            be = p.get("break_even")
            blocked = p.get("blocked")
            out.append(
                "<tr>"
                f"<td><strong>{escape(str(p.get('base')))}</strong>"
                f"<div class='muted' style='font-size:.7rem'>"
                f"{escape(', '.join(p.get('reasons') or []))}</div></td>"
                f"<td>{escape(str(p.get('venue') or '—'))}"
                f"{(' <span class=bad>' + escape(str(blocked)) + '</span>') if blocked else ''}"
                "</td>"
                f"<td class='mono'>{clip:,.0f} €</td>"
                f"<td class='mono'>{(f'{float(price):,.4f}' if price else '—')}</td>"
                f"<td class='mono'>{(f'{float(be):,.4f}' if be else '—')}</td>"
                f"{''.join(cells)}</tr>"
            )
        total_cells = "".join(
            f"<td class='{_cls(t)}'><strong>{_fmt_eur(t)}</strong></td>" for t in totals
        )
        out.append(
            f"<tr><td><strong>Totaal</strong></td><td></td>"
            f"<td class='mono'><strong>{sum(float(p.get('clip_eur') or 0) for p in planned):,.0f} €"
            f"</strong></td><td></td><td></td>{total_cells}</tr></tbody></table>"
        )
        trail_note = (
            f"trail vast {100 * float(planned[0].get('trail_pct') or 0.05):.0f}% onder de piek"
            if float(planned[0].get("trail_tight_after") or 0.0) <= 0.0
            else (
                f"trail {100 * float(planned[0].get('trail_pct') or 0.05):.0f}% onder de piek "
                f"({100 * float(planned[0].get('trail_tight_pct') or 0.02):.1f}% zodra "
                f"+{100 * float(planned[0].get('trail_tight_after') or 0.0):.0f}% piek)"
            )
        )
        out.append(
            "<p class='muted' style='font-size:.75rem;margin-top:.4rem'>Netto na fees. "
            "Prijs = laatste 15m-close; uitvoering gaat als maker op het live orderboek. "
            f"Hard stop bij {100 * float(planned[0].get('hard_stop_pct') or 0.03):.0f}%, "
            f"{trail_note}.</p>"
        )
    out.append(_expectancy_line(ledger_rows))
    if preview.get("rejected"):
        rej = " · ".join(
            f"{escape(str(r['base']))} {_fmt_pct(r.get('excess'))} ({escape(str(r.get('why')))})"
            for r in preview["rejected"]
        )
        out.append(f"<div class='muted' style='font-size:.75rem'>Afgewezen leaders: {rej}</div>")
    busy = bool(commit) and not commit.get("done")
    bases = ",".join(str(p.get("base")) for p in planned if not p.get("blocked"))
    if bases and not busy:
        out.append(
            "<div style='margin-top:.8rem;display:flex;gap:.6rem;align-items:center'>"
            f'<form method="post" action="/live/momentum/commit?bases={escape(bases)}'
            f'&amp;at={escape(str(preview.get("at") or ""))}" style="display:inline">'
            '<button type="submit" class="btn danger">Commit: koop nu '
            f"{escape(bases.replace(',', ' + '))} (echt geld)</button></form>"
            '<a href="/live/momentum" class="muted" style="font-size:.8rem">'
            "sluiten zonder kopen</a>"
            "</div>"
        )
    else:
        out.append(
            '<p style="margin-top:.8rem"><a href="/live/momentum" class="muted" '
            'style="font-size:.8rem">terug</a></p>'
        )
    return "".join(out)


def _manual_exit_notice(me: Mapping[str, Any]) -> str:
    if not me:
        return ""
    base = escape(str(me.get("base") or ""))
    is_all = bool(me.get("all") or (me.get("holding_id") == "*"))
    label = "Alles" if is_all else base
    if not me.get("done"):
        return (
            f'<div class="hint warn">Verkoop {label} bezig sinds {_ts(me.get("started_at"))}. '
            "Order rust als maker (tot 60 s per coin), daarna taker.</div>"
        )
    res = me.get("result") or {}
    if res.get("error"):
        return f'<div class="hint bad">Verkoop {label} mislukt: {escape(str(res["error"]))}</div>'
    if is_all:
        sold = int(res.get("sold") or 0)
        failed = int(res.get("failed") or 0)
        cls = "good" if sold and not failed else ("warn" if sold else "bad")
        return (
            f'<div class="hint {cls}">Sell-all klaar om {_ts(me.get("finished_at"))}: '
            f"{sold} verkocht, {failed} mislukt. Zie ledger.</div>"
        )
    if not res.get("ok"):
        why = str(res.get("reason") or "onbekend")
        hint = (
            " — OKX had minder coins vrij dan de desk dacht (fee in de coin zelf). "
            "Probeer opnieuw; de desk clamt nu op de vrije balance."
            if why == "exit_failed"
            else ""
        )
        return f'<div class="hint bad">Verkoop {base} niet uitgevoerd ({escape(why)}){hint}</div>'
    tail = " (gedeeltelijk gevuld, rest blijft open)" if res.get("partial") else ""
    return (
        f'<div class="hint good">{base} verkocht om {_ts(me.get("finished_at"))}{tail}. '
        "Zie ledger.</div>"
    )


def _commit_notice(commit: Mapping[str, Any]) -> str:
    if not commit:
        return ""
    if not commit.get("done"):
        return (
            f'<div class="hint warn">Uitvoering bezig voor '
            f"{escape(', '.join(commit.get('bases') or []))} sinds "
            f"{_ts(commit.get('started_at'))}. Orders rusten als maker (tot 90 s per coin).</div>"
        )
    res = commit.get("result") or {}
    if res.get("error"):
        return f'<div class="hint bad">Commit mislukt: {escape(str(res["error"]))}</div>'
    if res.get("mismatch"):
        return (
            '<div class="hint bad">Commit geweigerd: de desk zou inmiddels '
            f"{escape(', '.join(res.get('planned') or []) or 'niets')} kopen in plaats van "
            f"{escape(', '.join(commit.get('bases') or []))}. Simuleer opnieuw.</div>"
        )
    entered = res.get("entries") or []
    return (
        f'<div class="hint good">Commit uitgevoerd om {_ts(commit.get("finished_at"))}: '
        f"{escape(', '.join(entered)) if entered else 'geen entries'}. Zie ledger.</div>"
    )


def read_ledger_tail(path: str | Path, *, limit: int = 400) -> list[dict[str, Any]]:
    """Read the last ``limit`` JSONL ledger rows (best-effort)."""
    p = Path(path)
    rows: list[dict[str, Any]] = []
    if not p.exists():
        return rows
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines[-max(1, min(int(limit), 2000)) :]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _ledger_fill_price(row: Mapping[str, Any]) -> Any:
    """Core rows use ``price``; Donchian JSONL uses ``entry_price`` / ``exit_price``."""
    if row.get("price") not in (None, ""):
        return row.get("price")
    ev = str(row.get("event") or "")
    if ev.startswith("exit"):
        return row.get("exit_price") if row.get("exit_price") not in (None, "") else row.get("entry_price")
    return row.get("entry_price") if row.get("entry_price") not in (None, "") else row.get("exit_price")


def _ledger_reason(row: Mapping[str, Any]) -> str:
    reason = str(row.get("reason") or "")
    sleeve = str(row.get("sleeve") or "")
    if sleeve and reason:
        return f"{sleeve} · {reason}"
    return sleeve or reason


def _ledger_table(rows: Sequence[Mapping[str, Any]]) -> str:
    fills = [r for r in rows if r.get("event") in {"entry", "exit", "entry_failed", "exit_failed"}]
    if not fills:
        return '<p class="muted">Nog geen fills.</p>'
    out = [
        '<table class="desk"><thead><tr><th>Tijd</th><th>Event</th><th>Base</th><th>Prijs</th>'
        "<th>Notional</th><th>Fee</th><th>Gross</th><th>Peak</th><th>Net</th><th>Reden</th></tr></thead><tbody>"
    ]
    for r in reversed(fills[-40:]):
        ev = str(r.get("event"))
        ev_cls = "good" if ev == "entry" else ("bad" if ev.endswith("failed") else "")
        px = _ledger_fill_price(r)
        try:
            px_txt = f"{float(px):,.4f}" if px not in (None, "") else "—"
        except (TypeError, ValueError):
            px_txt = "—"
        out.append(
            "<tr>"
            f"<td>{_ts(r.get('ts'))}</td>"
            f"<td class='{ev_cls}'>{escape(ev)}{' (taker)' if r.get('taker') else ''}</td>"
            f"<td><strong>{escape(str(r.get('base') or ''))}</strong>"
            f" <span class='muted' style='font-size:.7rem'>{escape(str(r.get('venue') or ''))}"
            "</span></td>"
            f"<td class='mono'>{px_txt}</td>"
            f"<td>{_fmt_eur(r.get('notional_eur'), signed=False)}</td>"
            f"<td>{_fmt_eur(r.get('fee_eur'), signed=False)}</td>"
            f"<td class='{_cls(r.get('gross_return'))}'>{_fmt_pct(r.get('gross_return'))}</td>"
            f"<td>{_fmt_pct(r.get('peak_return'))}</td>"
            f"<td class='{_cls(r.get('net_eur'))}'>{_fmt_eur(r.get('net_eur'))}</td>"
            f"<td class='muted' style='text-align:left;max-width:220px;overflow:hidden;"
            f"text-overflow:ellipsis'>{escape(_ledger_reason(r))}</td>"
            "</tr>"
        )
    out.append("</tbody></table>")
    return "".join(out)


def _rules(cfg: Mapping[str, Any]) -> str:
    """Render live desk knobs. Field names must match ``status.config`` (DeskConfig)."""
    weekdays = " (ma–vr)" if cfg.get("skip_weekend_entries") else ""
    interval = float(cfg.get("decision_interval_sec") or 0.0)
    if interval > 0:
        if interval >= 60 and abs(interval / 60 - round(interval / 60)) < 1e-9:
            cadence = f"elke {interval / 60:.0f} min"
        else:
            cadence = f"elke {interval:.0f}s"
        decision_label = cadence + weekdays
    else:
        hours = ", ".join(f"{int(h):02d}:00" for h in (cfg.get("decision_hours_utc") or [0]))
        decision_label = f"uren {hours}{weekdays} (geen minutenscan)"
    refill = bool(cfg.get("refill_on_exit"))
    fade_eta = float(cfg.get("fade_eta_sec") or 0.0)
    green_h = float(cfg.get("green_deadline_hours") or 0.0)
    midflat_h = float(cfg.get("midflat_hours") or 0.0)
    items = [
        ("Beslismoment (UTC)", decision_label),
        (
            "Refill na exit",
            "aan — bij vrij slot meteen opnieuw beslissen" if refill else "uit",
        ),
        (
            "Clip",
            f"{float(cfg.get('clip_eur') or 0):,.0f} € "
            f"(AlphaI-pick ×{cfg.get('alphai_clip_mult')}, "
            f"brede tape ×{cfg.get('strong_clip_mult')}, "
            f"dunne tape ×{cfg.get('weak_clip_mult')})",
        ),
        ("Max posities", str(cfg.get("max_positions"))),
        (
            "Top-N",
            f"{cfg.get('top_n')} / {cfg.get('top_n_broad')} "
            f"bij breadth ≥ {cfg.get('broad_breadth')}",
        ),
        ("Min excess vs BTC", _fmt_pct(cfg.get("min_excess"))),
        ("Max onder 24u-high", _fmt_pct(cfg.get("max_from_high"))),
        (
            "Late-chase gate",
            (
                f"blokkeer als 24u ≥ {_fmt_pct(cfg.get('max_chase_ret_24h'))} "
                f"én < {_fmt_pct(cfg.get('chase_near_high'))} onder high"
                if float(cfg.get("max_chase_ret_24h") or 0.0) > 0.0
                else "uit"
            ),
        ),
        (
            "Regime",
            f"BTC 24u > {_fmt_pct(cfg.get('btc_min_ret'))}, breadth ≥ {cfg.get('min_breadth')}",
        ),
        (
            "Exit-ladder",
            "1) hard stop → 2) trail → 3) BE-arm → 4) partial → 5) time-exit",
        ),
        (
            "Trail",
            (
                f"vast {100 * float(cfg.get('trail_pct') or 0):.1f}% onder piek"
                if float(cfg.get("trail_tight_after") or 0.0) <= 0.0
                else (
                    f"{100 * float(cfg.get('trail_pct') or 0):.1f}% → "
                    f"{100 * float(cfg.get('trail_tight_pct') or 0):.1f}% na piek "
                    f"≥ {100 * float(cfg.get('trail_tight_after') or 0):.0f}%"
                )
            ),
        ),
        (
            "Hard stop",
            (
                f"−{float(cfg.get('hard_stop_eur') or 0):.0f} € bruto"
                if float(cfg.get("hard_stop_eur") or 0.0) > 0.0
                else f"−{100 * float(cfg.get('hard_stop_pct') or 0):.1f}%"
            ),
        ),
        (
            "BE-arm",
            (
                f"na piek ≥ {100 * float(cfg.get('be_arm_peak_pct') or 0):.1f}% "
                f"→ exit op fee-BE"
                if float(cfg.get("be_arm_peak_pct") or 0.0) > 0.0
                else "uit"
            ),
        ),
        (
            "Partial",
            (
                f"verkoop {100 * float(cfg.get('partial_frac') or 0):.0f}% "
                f"na piek ≥ {100 * float(cfg.get('partial_take_pct') or 0):.1f}% "
                f"(zolang boven fee-BE)"
                if float(cfg.get("partial_take_pct") or 0.0) > 0.0
                else "uit"
            ),
        ),
        (
            "Fade ETA→0",
            (
                f"≤{fade_eta:.0f}s voor "
                f"{float(cfg.get('fade_confirm_sec') or 0):.0f}s "
                f"(smooth {float(cfg.get('fade_smooth_sec') or 0):.0f}s; "
                f"arm ≥{float(cfg.get('fade_min_peak_eur') or 0):.0f}€/"
                f"{100 * float(cfg.get('fade_min_peak_pct') or 0):.1f}%; "
                f"giveback ≥{float(cfg.get('fade_min_giveback_eur') or 0):.0f}€)"
                if fade_eta > 0
                else "uit"
            ),
        ),
        (
            "Time-to-green",
            (
                f"{green_h:.0f}u zonder piek "
                f"≥ {100 * float(cfg.get('green_min_peak') or 0):.1f}%"
                if green_h > 0
                else "uit"
            ),
        ),
        (
            "Midflat",
            f"{midflat_h:.0f}u fee-flat" if midflat_h > 0 else "uit",
        ),
        ("Time-exit", f"{float(cfg.get('time_exit_hours') or 0):.0f}u onder break-even"),
        ("Daglimiet", f"−{float(cfg.get('day_loss_limit_eur') or 0):.0f} €"),
        (
            "Weeklimiet",
            f"−{float(cfg.get('week_loss_limit_eur') or 0):.0f} € → "
            f"{float(cfg.get('pause_hours_after_week_limit') or 0):.0f}u pauze",
        ),
        ("AlphaI macro", str(cfg.get("macro_caution_mode"))),
        (
            "AlphaI pick-gate",
            (
                "aan (alleen picks)"
                if cfg.get("requires_alphai_pick", False)
                else "uit (tape-namen ok)"
            ),
        ),
        (
            "Macro pick-gate",
            (
                "aan"
                if cfg.get("macro_caution_requires_alphai_pick", False)
                else "uit (tape-namen ok onder reduce)"
            ),
        ),
        (
            "Weak-tape survival",
            (
                "soft single-fail AlphaI×"
                f"{cfg.get('soft_regime_clip_mult', 0.5)}; "
                f"double-weak idle={'aan' if cfg.get('weak_tape_idle_on_double', True) else 'uit'}; "
                f"soft+macro idle={'aan' if cfg.get('soft_regime_idle_on_macro_caution', True) else 'uit'}"
                if cfg.get("soft_regime_on_weak_tape", True)
                else "hard block (soft uit)"
            ),
        ),
    ]
    return (
        '<div class="rules" data-live="rules">'
        + "".join(f"<div><span>{escape(k)}</span>{escape(v)}</div>" for k, v in items)
        + "</div>"
    )


def _sleeve_card(
    *,
    title: str,
    role: str,
    status: Mapping[str, Any] | None,
    href: str,
    book_label: str | None = None,
) -> str:
    """Compact dual-sleeve tile for the integrated desk view."""
    st = status or {}
    running = bool(st.get("running"))
    dry = bool(st.get("dry_run"))
    allow_live = st.get("allow_live")
    if not running:
        pill = '<span class="pill off"><span class="dot"></span>STOP</span>'
        mode = "gestopt"
    elif dry or allow_live is False:
        pill = '<span class="pill obs"><span class="dot"></span>PAPER</span>'
        mode = "paper / dry-run"
    else:
        pill = '<span class="pill on"><span class="dot"></span>LIVE</span>'
        mode = "live orders"
    risk = st.get("risk") or {}
    cfg = st.get("config") or {}
    n_pos = len(st.get("positions") or [])
    max_pos = cfg.get("max_positions") or "—"
    hours = cfg.get("decision_hours_utc") or []
    hours_s = ",".join(str(h) for h in hours) if hours else "—"
    interval = float(cfg.get("decision_interval_sec") or 0.0)
    if interval > 0:
        if interval >= 60 and abs(interval / 60 - round(interval / 60)) < 1e-9:
            schedule_s = f"elke {interval / 60:.0f} min"
        else:
            schedule_s = f"elke {interval:.0f}s"
    else:
        schedule_s = f"Uren {hours_s} UTC"
    book = st.get("book_eur")
    if book is None:
        book = cfg.get("book_eur")
    book_left = st.get("book_left_eur")
    book_html = ""
    if book_label and book is not None:
        left_s = (
            f" · vrij {_fmt_eur(book_left, signed=False)}"
            if book_left is not None
            else ""
        )
        book_html = (
            f"<p>{escape(book_label)} {_fmt_eur(book, signed=False)}{left_s}</p>"
        )
    pos_bits: list[str] = []
    for p in (st.get("positions") or [])[:4]:
        base = escape(str(p.get("base") or ""))
        net = p.get("unrealized_net_eur")
        pos_bits.append(f"<li><strong>{base}</strong> · <span class='{_cls(net)}'>{_fmt_eur(net)}</span></li>")
    if not pos_bits:
        pos_bits.append("<li class='muted'>Geen open posities</li>")
    return (
        f'<div class="card"><div class="card-head"><h2>{escape(title)}</h2>{pill}</div>'
        f'<p class="muted" style="margin:0 0 .5rem">{escape(role)} · {escape(mode)}</p>'
        f"<p>Gerealiseerd <strong class='{_cls(st.get('realized_total_eur'))}'>"
        f"{_fmt_eur(st.get('realized_total_eur'))}</strong> · "
        f"day <span class='{_cls(risk.get('day_realized_eur'))}'>"
        f"{_fmt_eur(risk.get('day_realized_eur'))}</span> · "
        f"pos {n_pos}/{max_pos}</p>"
        f"{book_html}"
        f"<p>{escape(schedule_s)} · next <strong>{_ts(st.get('next_decision'))}</strong></p>"
        f"<ul style='margin:.4rem 0 .6rem;padding-left:1.1rem'>{''.join(pos_bits)}</ul></div>"
    )


def _short_weakest_sleeve_card(status: Mapping[str, Any] | None) -> str:
    st = status or {}
    running = bool(st.get("running"))
    if not running:
        pill = '<span class="pill off"><span class="dot"></span>STOP</span>'
        mode = "gestopt"
    else:
        pill = '<span class="pill obs"><span class="dot"></span>PAPER</span>'
        mode = "bear-harvest paper"
    bear = st.get("bear") or {}
    pack = st.get("pack") or st.get("config") or {}
    bear_ok = bool(bear.get("bear_ok"))
    sma_n = int(bear.get("sma_days") or pack.get("sma_days") or 20)
    gate = "BEAR ON" if bear_ok else f"STANDBY (BTC&gt;SMA{sma_n})"
    gate_cls = "good" if bear_ok else "muted"
    btc = bear.get("btc")
    sma = bear.get("sma") if bear.get("sma") is not None else bear.get("sma200")
    gap = bear.get("gap_pct")
    n_pos = len(st.get("positions") or [])
    pos_bits: list[str] = []
    for p in (st.get("positions") or [])[:4]:
        base = escape(str(p.get("base") or ""))
        net = p.get("unrealized_net_eur")
        pos_bits.append(
            f"<li><strong>{base}</strong> · <span class='{_cls(net)}'>{_fmt_eur(net)}</span></li>"
        )
    if not pos_bits:
        pos_bits.append("<li class='muted'>Geen open paper-shorts</li>")
    return (
        f'<div class="card" data-live="sw-sleeve">'
        f'<div class="card-head"><h2>Short weakest</h2>{pill}</div>'
        f'<p class="muted" style="margin:0 0 .5rem">Paper short · SMA{sma_n} gate · {escape(mode)}</p>'
        f"<p><span class='{gate_cls}' data-live='sw-gate'><strong>{gate}</strong></span> · "
        f"BTC <span data-k='sw-btc'>{(f'{float(btc):,.0f}' if btc is not None else '—')}</span> / "
        f"SMA <span data-k='sw-sma'>{(f'{float(sma):,.0f}' if sma is not None else '—')}</span> · "
        f"gap <span data-k='sw-gap' class='{_cls(gap)}'>{_fmt_pct(gap) if gap is not None else '—'}</span></p>"
        f"<p>Equity <strong data-k='sw-equity'>{_fmt_eur(st.get('equity_eur'), signed=False)}</strong> · "
        f"real <span data-k='sw-realized' class='{_cls(st.get('realized_total_eur'))}'>"
        f"{_fmt_eur(st.get('realized_total_eur'))}</span> · "
        f"open <span data-k='sw-open' class='{_cls(st.get('unrealized_net_eur'))}'>"
        f"{_fmt_eur(st.get('unrealized_net_eur'))}</span> · "
        f"pos <span data-k='sw-npos'>{n_pos}</span>/{pack.get('top_n', 1)}</p>"
        f"<p class='muted' style='font-size:.78rem'>Pack top{pack.get('top_n', 1)} · "
        f"lb{pack.get('lookback_days', 15)} skip{pack.get('skip_days', 2)} · "
        f"reb {pack.get('rebalance_days', 30)}d · mw {pack.get('max_weight', 0.5)} · "
        f"idle-fill {'aan' if pack.get('idle_fill_enabled') else 'uit'}</p>"
        f"<p>Next <strong data-live='sw-next'>{_ts(st.get('next_decision'))}</strong></p>"
        f"<ul style='margin:.4rem 0 .6rem;padding-left:1.1rem' data-live='sw-poslist'>"
        f"{''.join(pos_bits)}</ul></div>"
    )


def _sleeves_panel(
    core: Mapping[str, Any],
    volatile: Mapping[str, Any] | None,
    short_weakest: Mapping[str, Any] | None = None,
) -> str:
    """Desk sleeves: core + optional volatile + optional paper short-weakest."""
    cash = float(core.get("cash_eur") or 0)
    core_exp = float(core.get("exposure_eur") or 0)
    v = volatile or {}
    book = float(v.get("book_eur") or (v.get("config") or {}).get("book_eur") or 0)
    deployed = float(v.get("deployed_eur") or v.get("exposure_eur") or 0)
    reserved = max(0.0, book - deployed) if book else 0.0
    sw = short_weakest or {}
    sw_book = float(sw.get("book_eur") or (sw.get("config") or {}).get("book_eur") or 0)
    sw_dep = float(sw.get("deployed_eur") or sw.get("exposure_eur") or 0)
    free_shared = max(0.0, cash - core_exp - reserved)
    capital = (
        '<div class="hint" style="margin-bottom:.7rem">'
        "<strong>Kapitaalbeeld</strong> — gedeelde venue-cash, gescheiden boeken. "
        f"Cash {_fmt_eur(cash, signed=False)} · core ingezet {_fmt_eur(core_exp, signed=False)}"
    )
    if book:
        capital += (
            f" · volatile book {_fmt_eur(book, signed=False)} "
            f"(vrij {_fmt_eur(reserved, signed=False)})"
        )
    if sw_book:
        capital += (
            f" · short-weakest paper {_fmt_eur(sw_book, signed=False)} "
            f"(ingezet {_fmt_eur(sw_dep, signed=False)})"
        )
    capital += f" · ongereserveerd ~{_fmt_eur(free_shared, signed=False)}.</div>"
    cards = _sleeve_card(
        title="Core",
        role="Stabiele RS-desk · core-16 · strengere filters",
        status=core,
        href="/live/momentum",
        book_label=None,
    )
    if volatile is not None:
        cards += _sleeve_card(
            title="Volatile",
            role="Agressievere AlphaI midcaps · soft book · eigen risk",
            status=volatile,
            href="/live/momentum/volatile",
            book_label="Soft book",
        )
    if short_weakest is not None:
        cards += _short_weakest_sleeve_card(short_weakest)
    roles = ["stabiel core"]
    if volatile is not None:
        roles.append("aggressief volatile")
    if short_weakest is not None:
        roles.append("paper short-weakest")
    return (
        '<div class="card section"><div class="card-head">'
        "<h2>Desk sleeves</h2>"
        f'<span class="muted">{" · ".join(roles)}</span></div>'
        f"{capital}"
        f'<div class="stack {"three" if short_weakest is not None and volatile is not None else "two"}">'
        + cards
        + "</div></div>"
    )


def _short_weakest_actions(status: Mapping[str, Any] | None) -> str:
    st = status or {}
    if not st.get("running"):
        return (
            '<form method="post" action="/live/momentum/short-weakest/start" '
            'style="display:inline">'
            '<input type="hidden" name="redirect" value="1">'
            '<button type="submit" class="btn primary">Start short-weakest PAPER</button></form>'
        )
    return (
        '<form method="post" action="/live/momentum/short-weakest/decide" '
        'style="display:inline;margin-right:.35rem">'
        '<input type="hidden" name="execute" value="0">'
        '<input type="hidden" name="redirect" value="1">'
        '<button type="submit" class="btn">Preview</button></form>'
        '<form method="post" action="/live/momentum/short-weakest/decide" style="display:inline">'
        '<input type="hidden" name="execute" value="1">'
        '<input type="hidden" name="redirect" value="1">'
        '<button type="submit" class="btn primary">Decide</button></form>'
    )


def _volatile_actions(volatile: Mapping[str, Any] | None) -> str:
    """Inline paper/live controls for the volatile sleeve on the main desk."""
    st = volatile or {}
    if not st.get("running"):
        return (
            '<form method="post" action="/live/momentum/volatile/start" style="display:inline">'
            '<input type="hidden" name="dry_run" value="false">'
            '<input type="hidden" name="redirect" value="1">'
            '<button type="submit" class="btn primary">Start volatile LIVE</button></form>'
        )
    return (
        '<form method="post" action="/live/momentum/volatile/decide" style="display:inline;margin-right:.35rem">'
        '<input type="hidden" name="execute" value="0">'
        '<input type="hidden" name="redirect" value="1">'
        '<button type="submit" class="btn">Preview</button></form>'
        '<form method="post" action="/live/momentum/volatile/decide" style="display:inline">'
        '<input type="hidden" name="execute" value="1">'
        '<input type="hidden" name="redirect" value="1">'
        '<button type="submit" class="btn primary">Decide</button></form>'
    )



_LIVE_MARKS_JS = r"""
<script>
(function () {
  if (window.__moreneyMarksPoll) return;
  window.__moreneyMarksPoll = true;
  const PULSE_URL = "/live/momentum/pulse";
  const STATUS_URL = "/live/momentum/status";
  const SHORT_STATUS_URL = "/live/momentum/short-weakest/status";
  const MIX_STATUS_URL = "/live/momentum/allocator/status";
    const DONCHIAN_STATUS_URL = "/live/momentum/donchian/status";
    const CLIP_STATUS_URL = "/live/momentum/btc-rs-clip/status";
  const INTERVAL_MS = 1000;

  function fmtPct(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v) * 100;
    return (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
  }
  function fmtEur(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    const sign = n > 0 ? "+" : "";
    return sign + n.toLocaleString("nl-NL", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " €";
  }
  function fmtPx(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    return Number(v).toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
  }
  function cls(v) {
    const n = Number(v);
    if (!Number.isFinite(n) || n === 0) return "";
    return n > 0 ? "good" : "bad";
  }
  function setText(root, key, text, className) {
    root.querySelectorAll(`[data-k="${key}"]`).forEach((el) => {
      el.textContent = text;
      if (className !== undefined) {
        el.classList.remove("good", "bad");
        if (className) el.classList.add(className);
      }
    });
  }
  function patchHolding(pos, trail, tightAfter, tight) {
    const id = String(pos.holding_id || pos.base || "");
    const nodes = document.querySelectorAll(`[data-holding="${CSS.escape(id)}"]`);
    if (!nodes.length) return;
    const entry = Number(pos.entry_price || 0);
    const mark = pos.mark == null ? null : Number(pos.mark);
    const peakBar = Number(pos.peak_return || 0);
    const gross = pos.gross_return;
    const side = String(pos.side || "long").toLowerCase();
    let livePeak = peakBar;
    if (mark && entry > 0) {
      livePeak = side === "short"
        ? Math.max(peakBar, (entry - mark) / entry)
        : Math.max(peakBar, mark / entry - 1);
    }
    const effTrail = (tightAfter > 0 && livePeak >= tightAfter) ? tight : trail;
    let trailTxt;
    if (!(effTrail > 0)) {
      trailTxt = "uit";
    } else if (side === "short") {
      const trailPx = pos.trail_stop_px != null
        ? Number(pos.trail_stop_px)
        : entry * (1 - livePeak + effTrail);
      trailTxt = `${fmtPx(trailPx)} (${(100 * effTrail).toFixed(1)}%)`;
    } else {
      const trailPx = entry * (1 + livePeak) * (1 - effTrail);
      trailTxt = `${fmtPx(trailPx)} (${(100 * effTrail).toFixed(1)}%)`;
    }
    nodes.forEach((root) => {
      setText(root, "mark", fmtPx(mark));
      if (pos.mark_source != null || pos.mark_age_sec != null) {
        const meta = [pos.mark_source || "—"]
          .concat(pos.mark_age_sec != null ? [`${Number(pos.mark_age_sec).toFixed(0)}s`] : [])
          .join(" · ");
        setText(root, "mark-meta", meta);
      }
      setText(root, "gross", fmtPct(gross), cls(gross));
      setText(root, "peak", fmtPct(livePeak));
      setText(root, "trail", trailTxt);
      setText(root, "net", fmtEur(pos.unrealized_net_eur), cls(pos.unrealized_net_eur));
      if (pos.age_h != null) setText(root, "age", `${Number(pos.age_h).toFixed(1)}h`);
    });
  }
  function patchHeroes(status) {
    const open = status.unrealized_net_eur;
    const el = document.querySelector('[data-live="open-pnl"]');
    if (el) {
      el.textContent = fmtEur(open);
      el.classList.remove("good", "bad");
      const c = cls(open);
      if (c) el.classList.add(c);
    }
    const eq = document.querySelector('[data-live="equity"]');
    if (eq && status.equity_eur != null) eq.textContent = fmtEur(status.equity_eur).replace(/^\+/, "");
    stampMarks(status);
    const next = document.querySelector('[data-live="next-decision"]');
    if (next && status.next_decision) {
      const d = new Date(status.next_decision);
      if (!Number.isNaN(d.getTime())) {
        const dd = String(d.getUTCDate()).padStart(2, "0");
        const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
        const hh = String(d.getUTCHours()).padStart(2, "0");
        const mi = String(d.getUTCMinutes()).padStart(2, "0");
        next.textContent = `${dd}-${mm} ${hh}:${mi} UTC`;
      }
    }
  }
  function stampMarks(status) {
    const stamp = document.querySelector('[data-live="marks-age"]');
    if (!stamp || !status) return;
    const age = (status.positions || []).map((p) => p.mark_age_sec).filter((v) => v != null);
    if (age.length) stamp.textContent = `marks ${Math.max(...age).toFixed(0)}s geleden`;
    else stamp.textContent = "marks live";
  }
  function livePositionIds() {
    const ids = new Set();
    document.querySelectorAll("[data-holding]").forEach((el) => {
      const id = el.getAttribute("data-holding");
      if (id) ids.add(id);
    });
    return ids;
  }
  function statusPositionIds(status) {
    const ids = new Set();
    (status.positions || []).forEach((p) => {
      if (Number(p.quantity || 0) <= 1e-12) return;
      const id = String(p.holding_id || p.base || "");
      if (id) ids.add(id);
    });
    return ids;
  }
  function positionsChanged(status) {
    const live = livePositionIds();
    const next = statusPositionIds(status);
    if (live.size !== next.size) return true;
    for (const id of next) {
      if (!live.has(id)) return true;
    }
    for (const id of live) {
      if (!next.has(id)) return true;
    }
    return false;
  }
  function trailKnobs(status) {
    const cfg = status.config || {};
    // Live research defaults: fixed 5% (tight_after=0 disables ratchet).
    const trail = Number(cfg.trail_pct != null ? cfg.trail_pct : 0.05);
    const tightAfter = Number(cfg.trail_tight_after != null ? cfg.trail_tight_after : 0.0);
    const tight = Number(cfg.trail_tight_pct != null ? cfg.trail_tight_pct : 0.02);
    document.querySelectorAll("table.desk[data-trail]").forEach((table) => {
      if (table.closest("#dc-open-pos") || table.getAttribute("data-mode") === "donchian") return;
      table.dataset.trail = String(trail);
      table.dataset.tightAfter = String(tightAfter);
      table.dataset.tight = String(tight);
    });
    return { trail, tightAfter, tight };
  }
  function patchRules(status) {
    const html = status.ui && status.ui.rules_html;
    const node = document.querySelector('[data-live="rules"]');
    if (html && node) node.outerHTML = html;
  }
  function patchShortSleeve(status) {
    const bear = status.bear || {};
    const fmtBtc = (v) => {
      if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
      return Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
    };
    const roots = [
      document.querySelector('[data-live="sw-sleeve"]'),
      document.querySelector('[data-live="sw-decision"]'),
      document.querySelector('[data-live="sw-bear"]'),
    ].filter(Boolean);
    const apply = (key, text, className) => {
      roots.forEach((root) => setText(root, key, text, className));
      // also global data-k under sw panels
      document.querySelectorAll(`[data-k="${key}"]`).forEach((el) => {
        if (!roots.some((r) => r.contains(el))) {
          el.textContent = text;
          if (className !== undefined) {
            el.classList.remove("good", "bad");
            if (className) el.classList.add(className);
          }
        }
      });
    };
    apply("sw-btc", fmtBtc(bear.btc));
    apply("sw-sma", fmtBtc(bear.sma != null ? bear.sma : bear.sma200));
    apply("sw-gap", fmtPct(bear.gap_pct), cls(bear.gap_pct));
    apply("sw-equity", fmtEur(status.equity_eur).replace(/^\+/, ""));
    apply("sw-realized", fmtEur(status.realized_total_eur), cls(status.realized_total_eur));
    apply("sw-open", fmtEur(status.unrealized_net_eur), cls(status.unrealized_net_eur));
    apply("sw-npos", String((status.positions || []).length));
    document.querySelectorAll('[data-live="sw-gate"]').forEach((gate) => {
      const on = !!bear.bear_ok;
      const smaN = bear.sma_days || (status.pack && status.pack.sma_days) || 20;
      const label = on ? "BEAR ON" : `STANDBY (BTC>SMA${smaN})`;
      if (gate.classList.contains("pill")) {
        gate.innerHTML = `<span class="dot"></span>${label}`;
        gate.classList.remove("on", "off");
        gate.classList.add(on ? "on" : "off");
      } else {
        gate.innerHTML = on
          ? "<strong>BEAR ON</strong>"
          : `<strong>STANDBY (BTC&gt;SMA${smaN})</strong>`;
        gate.classList.toggle("good", on);
        gate.classList.toggle("muted", !on);
      }
    });
    apply("sw-book", fmtEur(status.book_eur).replace(/^\+/, ""));
    const cap = document.querySelector('[data-live="sw-caption"]');
    if (cap && status.live_caption) cap.textContent = status.live_caption;
    const swOpen = document.querySelector('[data-live="paper-sw"] [data-live="sw-open"]');
    if (swOpen && Array.isArray(status.positions)) {
      const pos = status.positions.filter((p) => Number(p.quantity || p.notional_eur || 0) > 1e-12);
      if (!pos.length) {
        swOpen.innerHTML = '<span class="muted">Geen open paper-shorts — standby tot BTC &lt; SMA20.</span>';
      } else {
        swOpen.innerHTML = pos.map((p) => {
          const net = p.unrealized_net_eur;
          const hid = esc(p.holding_id || p.base || "");
          return `<span class="mix-chip" data-holding="${hid}"><span class="muted">short</span><strong>${esc(p.base || "")}</strong><span class="${cls(net)}" data-k="net">${fmtEur(net)}</span></span>`;
        }).join("");
      }
    }
    const decision = document.querySelector('[data-live="sw-decision"]');
    if (decision) {
      const pill = decision.querySelector(".pill");
      if (pill) {
        const on = !!bear.bear_ok;
        pill.className = on ? "pill on" : "pill off";
        pill.innerHTML = on
          ? '<span class="dot"></span>BEAR ON'
          : '<span class="dot"></span>STANDBY';
      }
    }
    const role = document.querySelector('[data-live="sw-role"]');
    if (role && status.role) role.textContent = status.role;
    const next = document.querySelector('[data-live="sw-next"]');
    if (next && status.next_decision) {
      const d = new Date(status.next_decision);
      if (!Number.isNaN(d.getTime())) {
        const dd = String(d.getUTCDate()).padStart(2, "0");
        const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
        const hh = String(d.getUTCHours()).padStart(2, "0");
        const mi = String(d.getUTCMinutes()).padStart(2, "0");
        next.textContent = `${dd}-${mm} ${hh}:${mi} UTC`;
      }
    }
    const posList = document.querySelector('[data-live="sw-poslist"]');
    if (posList && Array.isArray(status.positions)) {
      if (!status.positions.length) {
        posList.innerHTML = "<li class='muted'>Geen open paper-shorts</li>";
      } else {
        posList.innerHTML = status.positions.slice(0, 4).map((p) => {
          const net = p.unrealized_net_eur;
          const c = cls(net);
          return `<li><strong>${p.base || ""}</strong> · <span class="${c}">${fmtEur(net)}</span></li>`;
        }).join("");
      }
    }
  }
  function shortPositionsChanged(status) {
    const live = new Set();
    document.querySelectorAll("#sw-open-pos [data-holding]").forEach((el) => {
      const id = el.getAttribute("data-holding");
      if (id) live.add(id);
    });
    const empty = document.querySelector("#sw-open-pos [data-live='positions-empty']");
    const next = statusPositionIds(status);
    if (empty && next.size > 0) return true;
    if (!empty && next.size === 0 && document.querySelector("#sw-open-pos")) return true;
    if (live.size !== next.size) return true;
    for (const id of next) if (!live.has(id)) return true;
    for (const id of live) if (!next.has(id)) return true;
    return false;
  }
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => (
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
    ));
  }
  function patchMixOpen(mix) {
    const root = document.querySelector('[data-live="mix-open"]');
    if (!root || !Array.isArray(mix.positions)) return;
    const pos = mix.positions.filter((p) => Number(p.quantity || 1) > 1e-12);
    if (!pos.length) {
      root.innerHTML = '<span class="muted">Geen open mix-posities.</span>';
      return;
    }
    root.innerHTML = pos.map((p) => {
      const net = p.unrealized_net_eur;
      const hid = String(p.holding_id || p.base || "");
      return `<span class="mix-chip" data-mix-pos="${esc(hid)}">`
        + `<span class="muted">${esc(p.sleeve || "donch")}</span>`
        + `<strong>${esc(p.base || "")}</strong>`
        + `<span class="${cls(net)}">${fmtEur(net)}</span></span>`;
    }).join("");
  }
  function patchMix(mix) {
    const board = document.querySelector('[data-live="mix-board"]');
    if (!board || !mix) return;
    const reg = mix.regime || {};
    const label = mix.label || reg.label || "";
    if (label) {
      board.classList.remove("risk_on", "risk_off", "mid");
      board.classList.add(label);
      board.setAttribute("data-mix-label", label);
    }
    const pillTxt = label === "risk_on" ? "RISK ON" : label === "risk_off" ? "RISK OFF" : label === "mid" ? "MID · CASH" : label;
    const nt = mix.now_trading || {};
    const fmtBtc = (v) => {
      if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
      return Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
    };
    board.querySelectorAll('[data-k="mix-label"]').forEach((el) => { el.textContent = pillTxt; });
    board.querySelectorAll('[data-k="mix-now"]').forEach((el) => { el.textContent = nt.headline || el.textContent; });
    board.querySelectorAll('[data-k="mix-stance"]').forEach((el) => { el.textContent = nt.stance || ""; });
    board.querySelectorAll('[data-k="mix-why"]').forEach((el) => { el.textContent = mix.why || reg.why || ""; });
    board.querySelectorAll('[data-k="mix-btc"]').forEach((el) => { el.textContent = fmtBtc(reg.btc); });
    board.querySelectorAll('[data-k="mix-sma20"]').forEach((el) => { el.textContent = fmtBtc(reg.sma20); });
    board.querySelectorAll('[data-k="mix-sma50"]').forEach((el) => { el.textContent = fmtBtc(reg.sma50); });
    board.querySelectorAll('[data-k="mix-gap50"]').forEach((el) => {
      el.textContent = fmtPct(reg.gap_vs_sma50_pct);
      el.classList.remove("good", "bad");
      const c = cls(reg.gap_vs_sma50_pct);
      if (c) el.classList.add(c);
    });
    board.querySelectorAll('[data-k="mix-gap20"]').forEach((el) => { el.textContent = fmtPct(reg.gap_vs_sma20_pct); });
    const pill = board.querySelector('[data-live="mix-pill"]');
    if (pill) {
      pill.classList.remove("on", "off", "obs");
      pill.classList.add(label === "risk_on" ? "on" : label === "risk_off" ? "obs" : "off");
    }
    document.querySelectorAll('[data-k="mix-label-top"]').forEach((el) => { el.textContent = pillTxt; });
    (mix.sleeves || []).forEach((sl) => {
      const card = board.querySelector(`[data-mix-sleeve="${sl.id}"]`);
      if (!card) return;
      card.classList.toggle("on", !!sl.active);
      card.classList.toggle("off", !sl.active);
      const eur = card.querySelector('[data-k="mix-eur"]');
      if (eur) eur.textContent = fmtEur(sl.target_eur).replace(/^\+/, "");
      const live = (mix.sleeves_live || []).find((x) => x.id === sl.id) || {};
      const whyTxt = live.live_caption || "";
      const why = card.querySelector('[data-k="mix-why-s"]');
      if (why && whyTxt) why.textContent = whyTxt;
      const st = card.querySelector(".pill");
      if (st) {
        st.textContent = sl.active ? "AAN" : "UIT";
        st.classList.toggle("on", !!sl.active);
        st.classList.toggle("off", !sl.active);
      }
      const box = card.querySelector('[data-k="mix-pos"]');
      if (box) {
        const livePos = Array.isArray(live.positions)
          ? live.positions
          : (mix.positions || []).filter((p) => String(p.sleeve || "") === sl.id);
        box.innerHTML = livePos.map((p) => {
          const net = p.unrealized_net_eur;
          return `<span class="mix-chip"><strong>${esc(p.base || "")}</strong>`
            + `<span class="${cls(net)}">${fmtEur(net)}</span></span>`;
        }).join("");
      }
    });
    const engine = document.querySelector('[data-live="mix-engine-pill"]');
    if (engine && pillTxt) {
      engine.classList.remove("on", "off", "obs");
      engine.classList.add(label === "risk_on" ? "on" : label === "risk_off" ? "obs" : "off");
      engine.innerHTML = `<span class="dot"></span>MIX · ${esc(pillTxt)}`;
    }
    patchMixOpen(mix);
  }
  function mixHeroesLive() {
    return !!document.querySelector("#dc-open-pos");
  }
  function dcPositionIds(don) {
    const ids = new Set();
    (don.positions || []).forEach((p) => {
      if (Number(p.quantity || 0) <= 1e-12) return;
      const id = String(p.holding_id || p.base || "");
      if (id) ids.add(id);
    });
    return ids;
  }
  function donchianSetChanged(don) {
    const root = document.querySelector("#dc-open-pos");
    if (!root) return false;
    const live = new Set();
    root.querySelectorAll("[data-holding]").forEach((el) => {
      const id = el.getAttribute("data-holding");
      if (id) live.add(id);
    });
    const next = dcPositionIds(don);
    const empty = root.querySelector("[data-live='positions-empty']");
    if (empty && next.size > 0) return true;
    if (!empty && next.size === 0) return true;
    if (live.size !== next.size) return true;
    for (const id of next) if (!live.has(id)) return true;
    for (const id of live) if (!next.has(id)) return true;
    return false;
  }
  function renderDonchianOpen(don) {
    const root = document.querySelector("#dc-open-pos");
    if (!root) return;
    const pos = (don.positions || []).filter((p) => Number(p.quantity || 0) > 1e-12);
    const head = root.querySelector(".panel-head");
    const headHtml = head ? head.outerHTML : '<div class="panel-head"><h2>Donchian · live longs</h2></div>';
    if (!pos.length) {
      root.innerHTML = headHtml
        + '<p class="pos-empty muted" data-live="positions-empty">'
        + "Geen open Donchian-longs — wacht op 10d-breakout na UTC-dagclose.</p>";
      return;
    }
    const rows = pos.map((p) => {
      const hid = esc(p.holding_id || p.base || "");
      const entry = Number(p.entry_price || 0);
      const mark = p.mark == null ? null : Number(p.mark);
      const qty = Number(p.quantity || 0);
      const qtyS = qty.toLocaleString("en-US", { maximumFractionDigits: 4 });
      const notional = p.notional_eur == null ? "—" : fmtEur(p.notional_eur).replace(/^\+/, "");
      const markS = mark == null ? "—" : fmtPx(mark);
      const age = p.age_h == null ? "—" : `${Number(p.age_h).toFixed(1)}h`;
      const net = p.unrealized_net_eur;
      const reason = esc(p.entry_reason || "");
      const sleeve = esc(p.sleeve || "donch");
      const venue = esc(p.venue || "");
      return `<tr data-holding="${hid}" data-entry="${entry}" data-side="long">`
        + `<td><strong>${esc(p.base || "")}</strong> <span class="muted" style="font-size:.7rem">${venue}</span>`
        + `<div class="muted" style="font-size:.7rem">${reason}</div></td>`
        + `<td class="muted">${sleeve}</td>`
        + `<td class="mono" data-k="qty">${qtyS}</td>`
        + `<td class="mono">${fmtPx(entry)}</td>`
        + `<td class="mono" data-k="mark">${markS}</td>`
        + `<td class="${cls(p.gross_return)}" data-k="gross">${fmtPct(p.gross_return)}</td>`
        + `<td class="mono">${notional}</td>`
        + `<td class="${cls(net)}" data-k="net">${fmtEur(net)}</td>`
        + `<td data-k="age">${age}</td>`
        + `<td><form method="post" action="/live/momentum/donchian/sell" style="display:inline">`
        + `<input type="hidden" name="holding_id" value="${hid}">`
        + `<input type="hidden" name="redirect" value="1">`
        + `<button type="submit" class="btn danger" style="font-size:.78rem;padding:.4rem .7rem;min-height:40px">Verkoop</button></form></td></tr>`;
    }).join("");
    const cards = pos.map((p) => {
      const hid = esc(p.holding_id || p.base || "");
      const entry = Number(p.entry_price || 0);
      const mark = p.mark == null ? null : Number(p.mark);
      const qty = Number(p.quantity || 0);
      const qtyS = qty.toLocaleString("en-US", { maximumFractionDigits: 4 });
      const notional = p.notional_eur == null ? "—" : fmtEur(p.notional_eur).replace(/^\+/, "");
      const markS = mark == null ? "—" : fmtPx(mark);
      const age = p.age_h == null ? "—" : `${Number(p.age_h).toFixed(1)}h`;
      const net = p.unrealized_net_eur;
      const reason = esc(p.entry_reason || "");
      const sleeve = esc(p.sleeve || "donch");
      const venue = esc(p.venue || "");
      return `<div class="pos-card" data-holding="${hid}" data-entry="${entry}" data-side="long">`
        + `<div class="row1"><div><strong>${esc(p.base || "")}</strong> `
        + `<span class="muted">${venue}</span></div>`
        + `<div class="${cls(net)}" style="font-weight:600" data-k="net">${fmtEur(net)}</div></div>`
        + `<div class="muted" style="font-size:.7rem;margin-bottom:.35rem">${sleeve} · ${reason}</div>`
        + `<div class="meta">`
        + `<div><span>Qty</span><span data-k="qty">${qtyS}</span></div>`
        + `<div><span>Notional</span>${notional}</div>`
        + `<div><span>Entry</span>${fmtPx(entry)}</div>`
        + `<div><span>Mark</span><span data-k="mark">${markS}</span></div>`
        + `<div><span>Gross</span><span class="${cls(p.gross_return)}" data-k="gross">${fmtPct(p.gross_return)}</span></div>`
        + `<div><span>Age</span><span data-k="age">${age}</span></div>`
        + `</div></div>`;
    }).join("");
    root.innerHTML = headHtml
      + '<div class="table-scroll desk-wide" data-live="positions" data-mode="donchian">'
      + '<table class="desk" data-trail="0" data-tight-after="0" data-tight="0" data-stop="0">'
      + "<thead><tr><th>Base</th><th>Sleeve</th><th>Qty</th><th>Entry</th><th>Mark</th>"
      + "<th>Gross</th><th>Notional</th><th>Net</th><th>Age</th><th>Actie</th></tr></thead>"
      + `<tbody>${rows}</tbody></table></div>`
      + `<div class="pos-cards" data-live="position-cards" data-mode="donchian">${cards}</div>`
      + '<form method="post" action="/live/momentum/donchian/sell-all" style="margin:.55rem 0 0">'
      + '<input type="hidden" name="redirect" value="1">'
      + '<button type="submit" class="btn danger">Leeg Donchian</button></form>'
      + "<p class='muted dc-pos-note' style='font-size:.72rem;margin-top:.4rem'>"
      + "Geen 15m trail of hard-stop. Exit = low van de exit-N dagkaars na UTC-close, "
      + "of Friday-flat pas na vrijdag UTC-close. Marks elke 1s. "
      + "Verkoop loopt via de Donchian-sleeve, niet via de 15m-desk.</p>";
  }
  function patchPaperClip(st) {
    const root = document.querySelector('[data-live="paper-clip"]');
    if (!root || !st) return;
    setText(root, "clip-eq", fmtEur(st.equity_eur).replace(/^\+/, ""));
    setText(root, "clip-open", fmtEur(st.unrealized_net_eur), cls(st.unrealized_net_eur));
    setText(root, "clip-real", fmtEur(st.realized_total_eur), cls(st.realized_total_eur));
    setText(root, "clip-alt", st.want_alt || "—");
    const cap = document.querySelector('[data-live="clip-caption"]');
    if (cap && st.live_caption) cap.textContent = st.live_caption;
    const next = document.querySelector('[data-live="clip-next"]');
    if (next && st.next_decision) {
      const d = new Date(st.next_decision);
      next.textContent = Number.isNaN(d.getTime()) ? String(st.next_decision) : d.toLocaleString("nl-NL");
    }
    const live = !!st.running && !st.dry_run && st.allow_live !== false;
    const pill = root.querySelector('[data-live="clip-pill"]');
    if (pill) {
      pill.className = !st.running ? "pill off" : (live ? "pill on" : "pill obs");
      pill.innerHTML = !st.running
        ? '<span class="dot"></span>STOP'
        : (live ? '<span class="dot"></span>LIVE' : '<span class="dot"></span>PAPER');
    }
    const title = root.querySelector('[data-live="clip-title"]');
    if (title) {
      title.textContent = !st.running ? "BTC + RS-clip" : (live ? "Live · BTC + RS-clip" : "Paper · BTC + RS-clip");
    }
    const foot = root.querySelector('[data-live="clip-foot"]');
    if (foot) {
      foot.textContent = live
        ? "Owner · Bitvavo live · 15m €2k-satelliet gereserveerd."
        : "geen live orders, geen mix-cash.";
    }
    (st.positions || []).forEach((p) => patchHolding(p, 0, 0, 0));
    const open = root.querySelector('[data-live="clip-open"]');
    if (open) {
      const pos = (st.positions || []).filter((p) => Number(p.quantity || p.notional_eur || 0) > 1e-12);
      if (!pos.length) {
        const liveEmpty = !!st.running && !st.dry_run && st.allow_live !== false;
        open.innerHTML = liveEmpty
          ? '<span class="muted">Nog geen live-posities — eerste decide koopt 75% BTC + RS-alt.</span>'
          : '<span class="muted">Nog geen paper-posities — eerste decide na start.</span>';
      } else {
        open.innerHTML = pos.map((p) => {
          const net = p.unrealized_net_eur;
          const hid = esc(p.holding_id || p.base || "");
          return `<span class="mix-chip" data-holding="${hid}">`
            + `<span class="muted">${esc(p.role || p.venue || "")}</span>`
            + `<strong>${esc(p.base || "")}</strong>`
            + `<span class="${cls(net)}" data-k="net">${fmtEur(net)}</span></span>`;
        }).join("");
      }
    }
    const btc = st.btc;
    const sma50 = st.sma50;
    if (btc != null) {
      document.querySelectorAll('[data-k="mix-btc"]').forEach((el) => {
        el.textContent = Number(btc).toLocaleString("en-US", { maximumFractionDigits: 0 });
      });
    }
    if (btc != null && sma50) {
      const gap = Number(btc) / Number(sma50) - 1;
      document.querySelectorAll('[data-k="mix-gap50"]').forEach((el) => {
        el.textContent = fmtPct(gap);
        el.classList.remove("good", "bad");
        const c = cls(gap);
        if (c) el.classList.add(c);
      });
    }
    stampMarks(st);
  }
  function patchDonchian(don) {
    if (!don) return;
    if (mixHeroesLive()) patchHeroes(don);
    if (donchianSetChanged(don)) {
      renderDonchianOpen(don);
    } else {
      (don.positions || []).forEach((p) => patchHolding(p, 0, 0, 0));
    }
    const alloc = don.allocator;
    if (alloc && (alloc.label || alloc.regime)) {
      patchMix({
        ...alloc,
        sleeves_live: don.sleeves,
        positions: don.positions || alloc.positions || [],
      });
    }
    stampMarks(don);
  }
  function patchCore15m(status) {
    const root = document.querySelector('[data-live="core-15m"]');
    if (!root || !status) return;
    setText(root, "core15-eq", fmtEur(status.equity_eur).replace(/^\+/, ""));
    setText(root, "core15-open-pnl", fmtEur(status.unrealized_net_eur), cls(status.unrealized_net_eur));
    setText(root, "core15-real", fmtEur(status.realized_total_eur), cls(status.realized_total_eur));
    const cashBy = status.cash_by_venue || {};
    const cashKeys = Object.keys(cashBy);
    if (cashKeys.length) {
      setText(root, "core15-cash", cashKeys.map((k) => `${k} ${Number(cashBy[k]).toLocaleString("en-US", { maximumFractionDigits: 0 })} €`).join(" · "));
    }
    const nextEl = document.querySelector('[data-live="core15-next"]');
    if (nextEl && status.next_decision) {
      const d = new Date(status.next_decision);
      if (!Number.isNaN(d.getTime())) {
        const dd = String(d.getUTCDate()).padStart(2, "0");
        const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
        const hh = String(d.getUTCHours()).padStart(2, "0");
        const mi = String(d.getUTCMinutes()).padStart(2, "0");
        nextEl.textContent = `${dd}-${mm} ${hh}:${mi} UTC`;
      }
    }
    const pill = document.querySelector('[data-live="core15-pill"]');
    if (pill) {
      const on = !!status.running && !status.dry_run;
      pill.className = on ? "pill on" : (status.running ? "pill obs" : "pill off");
      pill.innerHTML = on
        ? '<span class="dot"></span>LIVE'
        : (status.running ? '<span class="dot"></span>SHADOW' : '<span class="dot"></span>STOP');
    }
    const open = root.querySelector('[data-live="core15-open"]');
    if (open) {
      const pos = (status.positions || []).filter((p) => Number(p.quantity || 0) > 1e-12);
      if (!pos.length) {
        open.innerHTML = '<span class="muted">Geen open 15m-posities — wacht op slot 7/13/16 UTC.</span>';
      } else {
        open.innerHTML = pos.map((p) => {
          const net = p.unrealized_net_eur;
          const hid = esc(p.holding_id || p.base || "");
          return `<span class="mix-chip" data-holding="${hid}">`
            + `<span class="muted">${esc(p.venue || "")}</span>`
            + `<strong>${esc(p.base || "")}</strong>`
            + `<span class="${cls(net)}" data-k="net">${fmtEur(net)}</span></span>`;
        }).join("");
      }
    }
    const knobs = trailKnobs(status);
    (status.positions || []).forEach((p) => patchHolding(p, knobs.trail, knobs.tightAfter, knobs.tight));
  }
  async function fetchJson(url) {
    const sep = url.includes("?") ? "&" : "?";
    const res = await fetch(url + sep + "_=" + Date.now(), {
      cache: "no-store",
      headers: { "Cache-Control": "no-cache" },
    });
    if (!res.ok) return null;
    return res.json();
  }
  function applyPulse(core, sw, don, clip) {
    if (core) {
      const knobs = trailKnobs(core);
      (core.positions || []).forEach((p) => patchHolding(p, knobs.trail, knobs.tightAfter, knobs.tight));
      if (mixHeroesLive()) patchCore15m(core);
      else patchHeroes(core);
      patchRules(core);
    }
    if (sw && sw.enabled_setting) {
      const knobs = trailKnobs(sw);
      (sw.positions || []).forEach((p) => patchHolding(p, knobs.trail, knobs.tightAfter, knobs.tight));
      patchShortSleeve(sw);
    }
    if (don) patchDonchian(don);
    else if (!clip) {
      /* mix fallback below */
    }
    if (clip) patchPaperClip(clip);
  }
  async function tick() {
    const pulse = await fetchJson(PULSE_URL).catch(() => null);
    if (pulse && (pulse.core || pulse.donchian || pulse.clip || pulse.short_weakest)) {
      applyPulse(pulse.core, pulse.short_weakest, pulse.donchian, pulse.clip);
      if (!pulse.donchian && !pulse.clip) {
        try {
          const mix = await fetchJson(MIX_STATUS_URL);
          if (mix) patchMix(mix);
        } catch (err) { /* mix optional */ }
      }
      return;
    }
    const [core, sw, don, clip] = await Promise.all([
      fetchJson(STATUS_URL).catch(() => null),
      fetchJson(SHORT_STATUS_URL).catch(() => null),
      fetchJson(DONCHIAN_STATUS_URL).catch(() => null),
      fetchJson(CLIP_STATUS_URL).catch(() => null),
    ]);
    applyPulse(core, sw, don, clip);
    if (!don) {
      try {
        const mix = await fetchJson(MIX_STATUS_URL);
        if (mix) patchMix(mix);
      } catch (err) {
        /* mix optional */
      }
    }
  }
  (async function pollLoop() {
    while (true) {
      const t0 = Date.now();
      try { await tick(); } catch (err) { /* ignore transient network blips */ }
      const wait = Math.max(0, INTERVAL_MS - (Date.now() - t0));
      await new Promise((r) => setTimeout(r, wait));
    }
  })();
})();
</script>
"""


def render_momentum_dashboard(
    status: Mapping[str, Any],
    ledger_rows: Sequence[Mapping[str, Any]],
    *,
    preview: Mapping[str, Any] | None = None,
    notice: str | None = None,
    sell: str | None = None,
    sell_all: bool = False,
    report: Mapping[str, Any] | None = None,
    volatile: Mapping[str, Any] | None = None,
    earnings: DeskEarnings | None = None,
    volatile_ledger_rows: Sequence[Mapping[str, Any]] | None = None,
    show_volatile: bool = False,
    short_weakest: Mapping[str, Any] | None = None,
    short_weakest_ledger_rows: Sequence[Mapping[str, Any]] | None = None,
    show_short_weakest: bool = False,
    allocator: Mapping[str, Any] | None = None,
    donchian: Mapping[str, Any] | None = None,
    donchian_ledger_rows: Sequence[Mapping[str, Any]] | None = None,
    btc_rs_clip: Mapping[str, Any] | None = None,
    btc_rs_clip_ledger_rows: Sequence[Mapping[str, Any]] | None = None,
    show_btc_rs_clip: bool = False,
) -> HTMLResponse:
    running = bool(status.get("running"))
    commit = status.get("commit") or {}
    dry = bool(status.get("dry_run"))
    mix_label = str((allocator or {}).get("label") or "")
    mix_on = bool(allocator) and mix_label
    mix_top_txt = {
        "risk_on": "RISK ON",
        "risk_off": "RISK OFF",
        "mid": "MID · CASH",
    }.get(mix_label, mix_label or "—")
    if mix_on:
        mix_pill_cls = {"risk_on": "on", "risk_off": "obs", "mid": "off"}.get(mix_label, "off")
        mix_pill_txt = {
            "risk_on": "MIX · RISK ON",
            "risk_off": "MIX · RISK OFF",
            "mid": "MIX · CASH",
        }.get(mix_label, "MIX")
        pill = (
            f'<span class="pill {mix_pill_cls}" data-live="mix-engine-pill">'
            f'<span class="dot"></span>{escape(mix_pill_txt)}</span>'
        )
    elif not running:
        pill = '<span class="pill off"><span class="dot"></span>GESTOPT</span>'
    elif dry:
        pill = '<span class="pill obs"><span class="dot"></span>SHADOW</span>'
    else:
        pill = '<span class="pill on"><span class="dot"></span>LIVE</span>'
    risk = status.get("risk") or {}
    cfg = status.get("config") or {}
    show_vol = bool(show_volatile)
    show_sw = bool(show_short_weakest)
    show_dc = donchian is not None or donchian_ledger_rows is not None
    show_clip = bool(show_btc_rs_clip) or btc_rs_clip is not None
    show_mix = bool(show_dc or show_sw)
    book = dict(donchian or {}) if show_dc else dict(status)
    if show_dc and book.get("cash_eur") is None:
        book["cash_eur"] = round(
            sum(float(s.get("cash_eur") or 0) for s in (book.get("sleeves") or [])),
            2,
        )
    n_pos = sum(
        1
        for p in (book.get("positions") or [])
        if float(p.get("quantity") or 0.0) > 1e-12
    )
    fill_src: list[Mapping[str, Any]] = list(ledger_rows)
    if show_dc:
        fill_src = list(donchian_ledger_rows or []) + list(short_weakest_ledger_rows or [])
    exits = [r for r in fill_src if r.get("event") == "exit"]
    wins = sum(1 for r in exits if float(r.get("net_eur") or 0) > 0)
    win_rate = f"{100 * wins / len(exits):.0f}%" if exits else "—"
    fees = sum(
        float(r.get("fee_eur") or 0) for r in fill_src if r.get("event") in {"entry", "exit"}
    )
    err = status.get("last_error")
    err_html = f'<div class="hint bad">Laatste fout: {escape(str(err))}</div>' if err else ""
    venue = escape(
        " + ".join(
            book.get("venues")
            or status.get("venues")
            or [str(status.get("venue") or "")]
        )
    )
    cash_by_venue = status.get("cash_by_venue") or {}
    if show_dc:
        cash_hint = f"cash {_fmt_eur(book.get('cash_eur'), signed=False)}"
    elif len(cash_by_venue) > 1:
        cash_hint = " · ".join(
            f"{escape(str(k))} {float(v):,.0f} €" for k, v in cash_by_venue.items()
        )
    else:
        cash_hint = f"cash {_fmt_eur(status.get('cash_eur'), signed=False)}"
    task_err = status.get("task_error")
    if task_err:
        err_html += f'<div class="hint bad">Loop gestopt: {escape(str(task_err))}</div>'
    if notice:
        err_html += f'<div class="hint warn">{escape(notice)}</div>'
    err_html += _commit_notice(commit)
    err_html += _manual_exit_notice(status.get("manual_exit") or {})
    hold_page = bool(preview) or bool(sell) or bool(sell_all) or bool(report)
    refresh_meta = ""  # marks poll via JS; full reload only on demand
    refresh_note = (
        "Geen live-update tijdens bevestiging"
        if hold_page
        else 'Marks live elke 1s · <span data-live="marks-age">—</span>'
    )
    live_js = "" if hold_page else _LIVE_MARKS_JS
    if show_mix:
        toolbar = ""
        toolbar_mobile = ""
    else:
        toolbar = _toolbar(
            running=running, has_positions=n_pos > 0, hold=hold_page, show_volatile=show_vol
        )
        toolbar_mobile = _toolbar(
            running=running,
            has_positions=n_pos > 0,
            hold=hold_page,
            show_volatile=show_vol,
            compact=True,
        )
    sell_html = _sell_confirm_panel(status, sell) if sell else ""
    sell_all_html = _sell_all_confirm_panel(status) if sell_all else ""
    report_html = _report_panel(report) if report else ""
    preview_html = (
        f'<div class="card section"><div class="card-head"><h2>Simulatie</h2>'
        f"{_simulate_button()}</div>{_preview_panel(preview, ledger_rows, commit)}</div>"
        if preview
        else ""
    )

    earnings_html = _earnings_masthead(
        earnings,
        pill=pill,
        venue=venue,
        show_volatile=show_vol,
        show_mix=show_mix,
    )

    dc_day = earnings.donchian.day_eur if (show_dc and earnings) else risk.get("day_realized_eur")
    open_pnl = (
        book.get("unrealized_net_eur")
        if show_dc
        else (earnings.open_mtm_eur if earnings else status.get("unrealized_net_eur"))
    )
    next_iso = book.get("next_decision") if show_dc else status.get("next_decision")
    max_pos = (book.get("config") or {}).get("max_positions") if show_dc else cfg.get("max_positions")
    bag_names = [
        str(p.get("base") or "")
        for p in (book.get("positions") or [])
        if float(p.get("quantity") or 0.0) > 1e-12
    ]
    bags_hint = (
        f"{n_pos} bags · {'+'.join(bag_names[:4])}" if bag_names else f"{n_pos} bags"
    )
    if show_dc:
        day_hint = f"Donchian gesloten · win {win_rate} · geen 15m trail"
        next_hint = "00:00 UTC na dagclose · geen kick-buy buiten window"
        eq_hint = (
            f"{cash_hint} · ingezet {_fmt_eur(book.get('deployed_eur') or book.get('exposure_eur'), signed=False)}"
        )
        day_label = "Donchian vandaag"
        next_label = "Volgende dagbesluit"
    else:
        day_hint = f"limiet −{float(cfg.get('day_loss_limit_eur') or 0):.0f} € · win {win_rate}"
        next_hint = (
            "entries toegestaan"
            if risk.get("entries_allowed", True)
            else f"geblokkeerd: {risk.get('block_reason')}"
        )
        eq_hint = f"{cash_hint} · ingezet {_fmt_eur(status.get('exposure_eur'), signed=False)}"
        day_label = "Core vandaag"
        next_label = "Volgende beslissing"
    heroes = "".join(
        [
            _hero(
                "Equity (cash + posities)",
                _fmt_eur(book.get("equity_eur"), signed=False),
                hint=eq_hint,
                value_attr='data-live="equity"',
            ),
            _hero(
                day_label,
                _fmt_eur(dc_day),
                cls=_cls(dc_day),
                hint=day_hint,
            ),
            _hero(
                "Open resultaat",
                _fmt_eur(open_pnl),
                cls=_cls(open_pnl),
                hint=f"{bags_hint} · fees {fees:,.2f} €",
                value_attr='data-live="open-pnl"',
            ),
            _hero(
                next_label,
                f"<span class='mono' style='font-size:1rem' data-live='next-decision'>"
                f"{_ts(next_iso)}</span>",
                hint=next_hint,
            ),
        ]
    )
    core_earn = _sleeve_earnings_line(earnings.core if earnings else None)
    vol_earn = (
        _sleeve_earnings_line(earnings.volatile if earnings else None) if show_vol else ""
    )
    sw_earn = (
        _sleeve_earnings_line(earnings.short_weakest if earnings else None) if show_sw else ""
    )
    dc_earn = (
        _sleeve_earnings_line(earnings.donchian if earnings else None) if show_dc else ""
    )
    sleeves_html = (
        _sleeves_panel(
            status,
            volatile if show_vol else None,
            short_weakest if show_sw else None,
        )
        if (show_vol or show_sw) and not show_dc
        else ""
    )
    if show_vol or show_sw or show_dc:
        pos_blocks = [
            '<div><div class="panel-head"><h2>Core · open posities</h2></div>'
            f"{_positions_table(status)}</div>"
        ]
        if show_vol:
            pos_blocks.append(
                '<div><div class="panel-head"><h2>Volatile · open posities</h2></div>'
                f"""{_positions_table(
                    volatile or {},
                    sell_all_path=None,
                    post_sell_action="/live/momentum/volatile/sell",
                    empty_text="Geen open volatile-posities — soft book staat klaar.",
                )}</div>"""
            )
        if show_sw:
            pos_blocks.append(
                '<div id="sw-open-pos"><div class="panel-head"><h2>Short weakest · paper</h2></div>'
                f"""{_positions_table(
                    short_weakest or {},
                    sell_all_path="/live/momentum/short-weakest/sell-all",
                    post_sell_action="/live/momentum/short-weakest/sell",
                    empty_text="Geen open paper-shorts — standby tot BTC &lt; SMA20.",
                )}</div>"""
            )
        if show_dc:
            don_live = bool((donchian or {}).get("running")) and not bool(
                (donchian or {}).get("dry_run", True)
            )
            dc_title = "Donchian · live longs" if don_live else "Donchian · paper longs"
            pos_blocks[0] = (
                f'<div id="dc-open-pos"><div class="panel-head"><h2>{escape(dc_title)}</h2></div>'
                f"{_donchian_positions_table(donchian or {})}</div>"
            )
        stack = "three" if show_vol and show_sw else "two"
        positions_html = (
            f'<section class="panel" id="open-pos"><div class="stack {stack}">'
            + "".join(pos_blocks)
            + "</div></section>"
        )
        dec_blocks = [
            '<div><div class="panel-head"><h2>Core · laatste beslissing</h2>'
            f"{_simulate_button() if running and not preview else ''}</div>"
            f"{_decision_panel(status)}</div>"
        ]
        if show_vol:
            dec_blocks.append(
                '<div><div class="panel-head"><h2>Volatile · laatste beslissing</h2>'
                f"{_volatile_actions(volatile)}</div>{_decision_panel(volatile or {})}</div>"
            )
        if show_sw:
            dec_blocks.append(
                '<div><div class="panel-head"><h2>Short weakest · paper live</h2>'
                f"{_short_weakest_actions(short_weakest)}</div>"
                f"{_short_weakest_decision_panel(short_weakest or {})}</div>"
            )
        if donchian is not None:
            dec_blocks[0] = (
                '<div><div class="panel-head"><h2>Donchian · laatste beslissing</h2></div>'
                f"{_donchian_decision_panel(donchian)}</div>"
            )
        decisions_html = (
            f'<section class="panel"><div class="stack {stack}">'
            + "".join(dec_blocks)
            + "</div></section>"
        )
        vol_ledger_html = ""
        if show_vol and volatile_ledger_rows is not None:
            vol_ledger_html += (
                f'<details class="fold"><summary><span class="fold-head">Volatile ledger</span>'
                f'<span class="chev"></span></summary><div class="fold-body">'
                f"{_ledger_table(volatile_ledger_rows or [])}</div></details>"
            )
        if show_sw and short_weakest_ledger_rows is not None:
            vol_ledger_html += (
                f'<details class="fold"><summary><span class="fold-head">Short-weakest ledger</span>'
                f'<span class="chev"></span></summary><div class="fold-body">'
                f"{_ledger_table(short_weakest_ledger_rows or [])}</div></details>"
            )
        if show_clip and btc_rs_clip_ledger_rows is not None:
            vol_ledger_html += (
                f'<details class="fold" id="clip-ledger"><summary><span class="fold-head">BTC+RS-clip ledger</span>'
                f'<span class="chev"></span></summary><div class="fold-body">'
                f"{_ledger_table(btc_rs_clip_ledger_rows or [])}</div></details>"
            )
        footer_links = (
            '<a href="/live/momentum/status">core JSON</a>'
            + (
                '<a href="/live/momentum/volatile/status">volatile JSON</a>'
                if show_vol
                else ""
            )
            + (
                '<a href="/live/momentum/short-weakest/status">short-weakest JSON</a>'
                if show_sw
                else ""
            )
            + '<a href="/live/momentum/allocator/status">mix JSON</a>'
            + (
                '<a href="/live/momentum/btc-rs-clip/status">paper-clip JSON</a>'
                if show_clip
                else ""
            )
            + '<a href="/live/momentum/donchian/status">donchian JSON</a>'
            + '<a href="/live/momentum/donchian/ledger">donchian ledger</a>'
            + '<a href="/live/momentum/ledger">core ledger</a>'
            + '<a href="/live/momentum/earnings">earnings</a>'
        )
    else:
        positions_html = (
            '<section class="panel" id="open-pos">'
            '<div class="panel-head">'
            '<h2>Open posities</h2>'
            f'<span class="aside">{n_pos}/{escape(str(cfg.get("max_positions") or "—"))} slots'
            f' · open {_fmt_eur(earnings.open_mtm_eur if earnings else status.get("unrealized_net_eur"))}'
            "</span></div>"
            f"{_positions_table(status)}</section>"
        )
        decisions_html = (
            '<section class="panel"><div class="panel-head"><h2>Laatste beslissing</h2>'
            f"{_simulate_button() if running and not preview else ''}</div>"
            f"{_decision_panel(status)}</section>"
        )
        vol_ledger_html = ""
        footer_links = (
            '<a href="/live/momentum/status">status JSON</a>'
            '<a href="/live/momentum/ledger">ledger</a>'
            '<a href="/live/momentum/earnings">earnings</a>'
        )
    if show_dc:
        exec_ledger_html = (
            '<details class="fold" open id="ledger">'
            '<summary><span class="fold-head">Execution stream · Donchian ledger</span>'
            '<span class="chev"></span></summary>'
            f'<div class="fold-body">{_ledger_table(donchian_ledger_rows or [])}</div></details>'
            '<details class="fold">'
            '<summary><span class="fold-head">15m WR-core ledger</span>'
            '<span class="chev"></span></summary>'
            f'<div class="fold-body">{_ledger_table(ledger_rows)}</div></details>'
        )
    else:
        exec_ledger_html = (
            '<details class="fold" open id="ledger">'
            '<summary><span class="fold-head">Execution stream · core ledger</span>'
            '<span class="chev"></span></summary>'
            f'<div class="fold-body">{_ledger_table(ledger_rows)}</div></details>'
        )
    equity = float(book.get("equity_eur") or 0)
    exposure = float(book.get("deployed_eur") or book.get("exposure_eur") or 0)
    util_pct = min(100.0, max(0.0, 100.0 * exposure / equity)) if equity > 0 else 0.0
    slots = escape(str(max_pos if max_pos is not None else "—"))
    active_cls = "active" if not show_vol else ""
    vol_cls = "active" if show_vol else ""
    sidebar = f"""
<aside class="sidebar" aria-label="Workspace">
  <div>
    <div class="side-brand">
      <div class="side-logo">M</div>
      <div>
        <strong>Moreney</strong>
        <span>Loop mix · SMA20/50</span>
      </div>
    </div>
    <div class="side-status">
      <div class="row"><span>Engine</span>{pill}</div>
      <div class="meta"><span>{venue}</span><span class="mono">{n_pos}/{slots}</span></div>
    </div>
    <nav class="side-nav">
      <div class="label">Workspace</div>
      <a class="active" href="/live/momentum#mix">Actieve mix</a>
      <a class="{active_cls}" href="/live/momentum">Command Center</a>
      <a class="{vol_cls}" href="/live/momentum/volatile">Volatile sleeve
        <span class="badge">{'on' if show_vol else 'off'}</span></a>
      <a href="/live/momentum#paper-sw">Short weakest paper
        <span class="badge">{'on' if show_sw else 'off'}</span></a>
      {('<a href="/live/momentum#paper-clip">BTC+RS clip <span class="badge">'
        + ('live' if (btc_rs_clip or {}).get('running') and not bool((btc_rs_clip or {}).get('dry_run', True))
           else 'paper')
        + '</span></a>' if show_clip else '')}
      <a href="/live/momentum#core-15m">15m WR-core
        <span class="badge">{'live' if running and not dry else ('on' if running else 'off')}</span></a>
      <a href="/live/momentum#ledger">Ledger</a>
      <a href="/live/momentum#dc-open-pos">Donchian longs</a>
      <a href="/live/momentum/allocator/status">Mix JSON</a>
    </nav>
  </div>
  <div class="side-capital">
    <div class="cap-label"><span>Allocated capital</span>
      <span class="{_cls(open_pnl)} mono">{_fmt_eur(open_pnl)}</span>
    </div>
    <div class="cap-val" data-live="equity">{_fmt_eur(equity, signed=False)}</div>
    <div class="bar" title="exposure / equity"><i style="width:{util_pct:.1f}%"></i></div>
  </div>
</aside>
"""
    topbar = f"""
<header class="topbar">
  <div class="topbar-left">
    {pill}
    <div class="top-metric"><span class="k">Mix</span>
      <span class="v" data-k="mix-label-top">{escape(mix_top_txt)}</span></div>
    <div class="top-metric"><span class="k">Equity</span>
      <span class="v" data-live="equity">{_fmt_eur(equity, signed=False)}</span></div>
    <div class="top-metric openpnl"><span class="k">Open</span>
      <span class="v {_cls(open_pnl)}" data-live="open-pnl">{_fmt_eur(open_pnl)}</span></div>
    <div class="top-metric winrate"><span class="k">Win rate</span><span class="v">{escape(win_rate)}</span></div>
  </div>
  <div class="topbar-right">{toolbar}</div>
</header>
"""
    if show_mix:
        clip_dock = (
            '<a href="/live/momentum#paper-clip"><span class="ico">◇</span>Clip</a>'
            if show_clip
            else '<a href="/live/momentum/earnings"><span class="ico">€</span>Earn</a>'
        )
        mobile_dock = f"""
<nav class="mobile-dock" aria-label="Mobile workspace">
  <a class="active" href="/live/momentum#mix"><span class="ico">◆</span>Mix</a>
  <a href="/live/momentum#dc-open-pos"><span class="ico">▣</span>Bags</a>
  {clip_dock}
  <a href="/live/momentum#ledger"><span class="ico">☰</span>Ledger</a>
</nav>
"""
    else:
        mobile_dock = f"""
<nav class="mobile-dock" aria-label="Mobile workspace">
  <a href="/live/momentum#mix"><span class="ico">◆</span>Mix</a>
  <a class="{active_cls}" href="/live/momentum"><span class="ico">◆</span>Desk</a>
  <a class="{vol_cls}" href="/live/momentum/volatile"><span class="ico">◇</span>Volatile</a>
  <a href="/live/momentum#ledger"><span class="ico">☰</span>Ledger</a>
</nav>
"""
    sticky = (
        f'<div class="sticky-actions">{toolbar_mobile}</div>'
        if toolbar_mobile and not hold_page
        else ""
    )
    html = f"""<!doctype html>
<html lang="nl" class="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#070B12">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
{refresh_meta}
<title>Moreney · Momentum Desk</title>
<style>{_CSS}</style></head>
<body class="{'mix-live' if show_mix else ''}"><div class="shell">
{sidebar}
<div class="shell-main">
{topbar}
<div class="wrap">
{earnings_html}
{_mix_panel(allocator, donchian, short_weakest if show_sw else None, status if show_mix else None)}
{_core_15m_panel(status) if show_mix else ""}
{_paper_sw_panel(short_weakest) if show_sw else ""}
{_paper_clip_panel(btc_rs_clip) if show_clip else ""}
{positions_html}
{err_html}
{'' if show_mix else f'<div class="ops-row">{toolbar}</div>'}
{sleeves_html}
{'' if show_mix else (core_earn and f'<div class="muted" style="font-size:.78rem;margin:.4rem 0 0">Core netto · </div>{core_earn}' or '')}
{vol_earn and f'<div class="muted" style="font-size:.78rem">Volatile netto · </div>{vol_earn}' or ''}
{dc_earn and f'<div class="muted" style="font-size:.78rem">Donchian netto · </div>{dc_earn}' or ''}
{sw_earn and f'<div class="muted" style="font-size:.78rem">Short-weakest netto · </div>{sw_earn}' or ''}
<div class="pulse hero-grid">{heroes}</div>
{preview_html}
{report_html}
{sell_html}
{sell_all_html}
{decisions_html}
{exec_ledger_html}
{vol_ledger_html}
<details class="fold">
<summary><span class="fold-head">{'15m satelliet rules (€2k Bitvavo)' if show_mix else 'Risk rules (core)'}</span><span class="chev"></span></summary>
<div class="fold-body">{_rules(cfg)}</div>
</details>
<p class="foot"><span>{refresh_note}</span>{footer_links}</p>
</div>
</div>
</div>
{sticky}
{mobile_dock}
{live_js}
</body></html>"""
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )

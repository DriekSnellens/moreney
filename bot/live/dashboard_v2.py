"""Live dashboard v2 — command-center layout with AlphaI intelligence front and center."""

from __future__ import annotations

from typing import Any, Callable


def dashboard_css() -> str:
    return """
    :root {
      --bg0: #070a0f;
      --bg1: #0f1520;
      --bg2: #161f2e;
      --surface: rgba(22, 31, 46, 0.92);
      --text: #eef3fb;
      --muted: #8fa3be;
      --line: #243247;
      --good: #34d399;
      --bad: #f87171;
      --warn: #fbbf24;
      --accent: #a78bfa;
      --accent-dim: rgba(167, 139, 250, 0.14);
      --blue: #60a5fa;
      --display: "Fraunces", "Iowan Old Style", Georgia, serif;
      --sans: "Sora", "Avenir Next", "Segoe UI", sans-serif;
      --mono: "IBM Plex Mono", ui-monospace, monospace;
      --radius: 18px;
      --shadow: 0 18px 50px rgba(0, 0, 0, 0.35);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      font-family: var(--sans);
      background:
        radial-gradient(1100px 520px at 8% -8%, rgba(167,139,250,.16), transparent 58%),
        radial-gradient(900px 480px at 92% 0%, rgba(96,165,250,.10), transparent 55%),
        radial-gradient(700px 400px at 50% 100%, rgba(52,211,153,.06), transparent 50%),
        linear-gradient(165deg, #05070b 0%, var(--bg0) 42%, #0b1219 100%);
    }
    .wrap {
      max-width: 1240px;
      margin: 0 auto;
      padding:
        max(.85rem, env(safe-area-inset-top))
        max(.85rem, env(safe-area-inset-right))
        max(5.5rem, calc(4.5rem + env(safe-area-inset-bottom)))
        max(.85rem, env(safe-area-inset-left));
    }
    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 1rem;
      margin-bottom: 1rem;
      flex-wrap: wrap;
    }
    .brand-block { flex: 1 1 220px; }
    .brand {
      margin: 0;
      font-family: var(--display);
      font-size: clamp(1.5rem, 5vw, 2.35rem);
      font-weight: 600;
      letter-spacing: -0.03em;
    }
    .tagline {
      margin: .25rem 0 0;
      color: var(--muted);
      font-size: .78rem;
      max-width: 36rem;
      line-height: 1.45;
    }
    .status-pills {
      display: flex;
      gap: .45rem;
      flex-wrap: wrap;
      align-items: center;
      justify-content: flex-end;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: .35rem;
      padding: .35rem .65rem;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,.03);
      font-size: .68rem;
      font-weight: 600;
      letter-spacing: .04em;
      text-transform: uppercase;
      color: var(--muted);
      white-space: nowrap;
    }
    .pill.on { color: var(--good); border-color: color-mix(in srgb, var(--good) 45%, var(--line)); }
    .pill.off { color: var(--bad); }
    .pill.obs { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 45%, var(--line)); }
    .pill .dot {
      width: .45rem; height: .45rem; border-radius: 50%; background: currentColor;
    }
    .dash-top {
      display: flex;
      flex-direction: column;
      gap: .85rem;
      margin-bottom: .5rem;
    }
    .hero-grid {
      display: grid;
      gap: .65rem;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    @media (min-width: 760px) {
      .hero-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .75rem; }
    }
    .hero-card {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: .85rem .9rem .75rem;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
      min-height: 0;
    }
    .hero-card.featured {
      grid-column: 1 / -1;
      border-color: color-mix(in srgb, var(--blue) 40%, var(--line));
      background: linear-gradient(135deg, rgba(96,165,250,.12), var(--surface));
    }
    @media (min-width: 760px) {
      .hero-card.featured { grid-column: span 2; }
    }
    .hero-card.positive { border-color: color-mix(in srgb, var(--good) 38%, var(--line)); }
    .hero-card.negative { border-color: color-mix(in srgb, var(--bad) 38%, var(--line)); }
    .label {
      margin: 0;
      color: var(--muted);
      font-size: .68rem;
      font-weight: 600;
      letter-spacing: .05em;
      text-transform: uppercase;
      line-height: 1.25;
    }
    .value {
      margin: .4rem 0 0;
      font-family: var(--mono);
      font-size: clamp(1.2rem, 4.5vw, 1.85rem);
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
      line-height: 1.35;
    }
    .hint.bad { color: var(--bad); }
    .command-grid {
      display: grid;
      gap: .85rem;
      grid-template-columns: 1fr;
    }
    @media (min-width: 980px) {
      .command-grid { grid-template-columns: 1.35fr .95fr; }
    }
    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: calc(var(--radius) + 2px);
      padding: 1rem;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }
    .panel.alphai {
      border-color: color-mix(in srgb, var(--accent) 42%, var(--line));
      background:
        linear-gradient(155deg, var(--accent-dim), transparent 42%),
        var(--surface);
    }
    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: .75rem;
      margin-bottom: .85rem;
      flex-wrap: wrap;
    }
    .panel-head h2 {
      margin: 0;
      font-family: var(--display);
      font-size: 1.05rem;
      font-weight: 600;
      letter-spacing: -0.02em;
    }
    .panel-head .sub {
      margin: .2rem 0 0;
      color: var(--muted);
      font-size: .72rem;
    }
    .kpi-strip {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: .55rem;
    }
    @media (min-width: 560px) {
      .kpi-strip { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    }
    .mini-kpi {
      padding: .55rem .65rem;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,.02);
    }
    .mini-kpi .label { font-size: .62rem; }
    .mini-kpi .value { font-size: 1rem; margin-top: .25rem; }
    .mini-kpi.quota .value { color: var(--accent); }
    .chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: .4rem;
      margin: .55rem 0 0;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      gap: .25rem;
      padding: .28rem .55rem;
      border-radius: 999px;
      font-size: .68rem;
      font-weight: 600;
      border: 1px solid var(--line);
      background: rgba(255,255,255,.03);
    }
    .chip.block { border-color: color-mix(in srgb, var(--bad) 45%, var(--line)); color: #fecaca; }
    .chip.watch { border-color: color-mix(in srgb, var(--warn) 45%, var(--line)); color: #fde68a; }
    .chip.macro { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); color: #ddd6fe; }
    .chip.none { color: var(--muted); font-weight: 500; }
    .headline-feed {
      display: flex;
      flex-direction: column;
      gap: .45rem;
      margin-top: .65rem;
      max-height: 280px;
      overflow: auto;
      padding-right: .15rem;
    }
    .headline-card {
      padding: .55rem .65rem;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,.02);
    }
    .headline-card .meta {
      display: flex;
      gap: .45rem;
      flex-wrap: wrap;
      margin-bottom: .25rem;
      font-size: .62rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .04em;
    }
    .headline-card .title {
      margin: 0;
      font-size: .78rem;
      line-height: 1.35;
      color: var(--text);
    }
    .sent-bear { color: var(--bad); }
    .sent-bull { color: var(--good); }
    .sent-neutral { color: var(--muted); }
    .operator-panel .primary {
      margin: .35rem 0 0;
      font-size: .92rem;
      line-height: 1.4;
      font-weight: 600;
    }
    .operator-panel ul {
      margin: .55rem 0 0;
      padding-left: 1.1rem;
      color: var(--muted);
      font-size: .74rem;
      line-height: 1.45;
    }
    .idle-banner {
      border-radius: var(--radius);
      border: 1px solid var(--line);
      background: rgba(248,113,113,.08);
      padding: .85rem 1rem;
      margin-bottom: .85rem;
    }
    .idle-banner.ok {
      background: rgba(52,211,153,.08);
      border-color: color-mix(in srgb, var(--good) 35%, var(--line));
    }
    .idle-banner.stale {
      background: rgba(251,191,36,.10);
      border-color: color-mix(in srgb, var(--warn) 40%, var(--line));
    }
    .idle-banner h2 {
      margin: 0 0 .35rem;
      font-size: .82rem;
      text-transform: uppercase;
      letter-spacing: .05em;
      color: var(--muted);
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
    .card {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: .85rem .8rem .75rem;
      backdrop-filter: blur(8px);
    }
    .card.hero {
      border-color: color-mix(in srgb, #f0b429 35%, var(--line));
      background: color-mix(in srgb, #f0b429 6%, var(--bg1));
    }
    .pnl-split {
      display: grid;
      gap: .65rem;
      grid-template-columns: 1fr 1fr;
    }
    @media (min-width: 720px) {
      .pnl-split { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    }
    .pnl-split-intro {
      margin: 0 0 .35rem;
      color: var(--muted);
      font-size: .78rem;
      line-height: 1.4;
    }
    .charts {
      display: grid;
      gap: .85rem;
      grid-template-columns: 1fr;
    }
    @media (min-width: 900px) {
      .charts { grid-template-columns: 1.2fr .8fr; }
    }
    .chart-card {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: .85rem;
    }
    .chart-card h2 {
      margin: 0 0 .55rem;
      font-size: .82rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .05em;
    }
    .chart-wrap { height: 220px; position: relative; }
    .chart-pnl-first .chart-wrap { height: 260px; }
    .target-band, .positions, .cash-grid, .portfolio-strip {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: .85rem 1rem;
    }
    .target-band h2, .positions h2, .portfolio-strip h2 {
      margin: 0 0 .55rem;
      font-size: .88rem;
    }
    .band-row {
      display: flex;
      flex-wrap: wrap;
      gap: .55rem 1rem;
      font-size: .74rem;
      color: var(--muted);
      margin-top: .35rem;
    }
    .band-row strong.in-band { color: var(--good); }
    .band-row strong.out-band { color: var(--text); }
    .band-row .warn { color: var(--warn); }
    .fold {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: rgba(255,255,255,.02);
      overflow: hidden;
    }
    .fold summary {
      cursor: pointer;
      padding: .85rem 1rem;
      font-weight: 600;
      font-size: .82rem;
      color: var(--muted);
      list-style: none;
    }
    .fold summary::-webkit-details-marker { display: none; }
    .fold-body { padding: 0 1rem 1rem; display: flex; flex-direction: column; gap: .85rem; }
    .dash-secondary { display: flex; flex-direction: column; gap: .85rem; margin-top: .85rem; }
    .cash-grid { display: grid; grid-template-columns: 1fr 1fr; gap: .65rem; }
    .mini .value { font-size: 1.15rem; }
    table.pos {
      width: 100%;
      border-collapse: collapse;
      font-size: .72rem;
    }
    table.pos th, table.pos td {
      border-bottom: 1px solid var(--line);
      padding: .35rem .25rem;
      text-align: left;
      vertical-align: top;
    }
    table.pos .good { color: var(--good); }
    table.pos .bad { color: var(--bad); }
    ul.alerts, ul.hold-list {
      margin: 0;
      padding: 0;
      list-style: none;
    }
    ul.hold-list { display: flex; flex-wrap: wrap; gap: .45rem; }
    li.hold-item {
      display: inline-flex;
      align-items: center;
      gap: .35rem;
      padding: .35rem .55rem;
      border-radius: 999px;
      border: 1px solid var(--line);
      font-size: .72rem;
    }
    .coin { font-weight: 600; }
    .amt { font-family: var(--mono); color: var(--muted); }
    .venue, .tag {
      font-size: .62rem;
      color: var(--muted);
      text-transform: uppercase;
    }
    .tag.long-hold { color: var(--warn); }
    .mom-down { color: var(--bad); }
    .mom-up { color: var(--good); }
    .updated-at {
      margin: .5rem 0 0;
      color: var(--muted);
      font-size: .68rem;
      text-align: right;
    }
    footer {
      position: fixed;
      left: 0; right: 0; bottom: 0;
      display: flex;
      gap: .55rem;
      justify-content: center;
      padding: .65rem max(.85rem, env(safe-area-inset-right))
        max(.65rem, env(safe-area-inset-bottom))
        max(.85rem, env(safe-area-inset-left));
      background: linear-gradient(180deg, transparent, rgba(7,10,15,.92) 35%);
      backdrop-filter: blur(8px);
    }
    .btn {
      border: 1px solid var(--line);
      background: var(--bg2);
      color: var(--text);
      border-radius: 999px;
      padding: .55rem 1.1rem;
      font-weight: 600;
      font-size: .78rem;
      cursor: pointer;
    }
    .install-banner {
      display: none;
      margin-bottom: .85rem;
      padding: .65rem .85rem;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,.03);
      font-size: .78rem;
    }
    .install-banner.show { display: flex; align-items: center; justify-content: space-between; gap: .75rem; }
    """


def _sentiment_class(sentiments: dict[str, str]) -> str:
    vals = [str(v).lower() for v in sentiments.values()]
    if any(v in {"bearish", "negative", "very_bearish"} for v in vals):
        return "sent-bear"
    if any(v in {"bullish", "positive", "very_bullish"} for v in vals):
        return "sent-bull"
    return "sent-neutral"


def render_alphai_command_center(
    alphai: dict[str, Any],
    *,
    esc: Callable[[Any], str],
) -> str:
    enabled = bool(alphai.get("enabled"))
    obs = bool(alphai.get("observation_mode"))
    macro = bool(alphai.get("macro_active") or alphai.get("macro_reduce_only"))
    blocked = list(alphai.get("blocked_bases") or [])
    detail = alphai.get("blocked_detail") if isinstance(alphai.get("blocked_detail"), dict) else {}
    headlines = alphai.get("headlines") if isinstance(alphai.get("headlines"), list) else []
    quota = alphai.get("rate_limit_remaining")
    polls = alphai.get("polls")
    skips = alphai.get("skips")
    last_poll = alphai.get("last_poll_at") or "—"
    err = alphai.get("last_error")

    mode_pill = "obs" if obs else ("on" if enabled else "off")
    mode_label = "Observatie" if obs else ("Actief" if enabled else "Uit")

    chips = []
    show_bases = blocked or [
        k for k in detail if k and str(k) != "_MACRO_" and not str(k).startswith("_")
    ]
    for base in show_bases[:12]:
        key = str(base)
        reason = detail.get(key) or detail.get(base) or ""
        cls = "watch" if obs and not blocked else "block"
        chips.append(
            f"<span class='chip {cls}' title='{esc(reason[:140])}'>{esc(key)}</span>"
        )
    if macro:
        macro_txt = detail.get("_MACRO_", "Macro headline")
        chips.append(
            f"<span class='chip macro' title='{esc(str(macro_txt)[:140])}'>MACRO RO</span>"
        )
    if not chips:
        chips.append("<span class='chip none'>Geen blocks</span>")

    hl_cards = []
    for h in headlines[:8]:
        if not isinstance(h, dict):
            continue
        title = str(h.get("title") or "")[:140]
        rel = h.get("relevance")
        cat = h.get("category") or "news"
        sentiments = h.get("sentiments") if isinstance(h.get("sentiments"), dict) else {}
        sents = ", ".join(f"{k.split('-')[0]}:{v}" for k, v in list(sentiments.items())[:3])
        hl_cards.append(
            "<article class='headline-card'>"
            f"<div class='meta'><span>{esc(cat)}</span><span>r{esc(rel)}</span>"
            f"<span class='{_sentiment_class(sentiments)}'>{esc(sents or 'neutral')}</span></div>"
            f"<p class='title'>{esc(title)}</p>"
            "</article>"
        )

    err_html = (
        f"<p class='hint bad'>Laatste fout: {esc(str(err)[:160])}</p>" if err else ""
    )

    return (
        "<section class='panel alphai' aria-label='AlphaI news intelligence' id='section-alphai'>"
        "<div class='panel-head'>"
        "<div><h2>AlphaI news intelligence</h2>"
        f"<p class='sub'>{'Headlines gelogd — buys nog niet geblokkeerd' if obs else 'Bearish headlines blokkeren nieuwe buys op focus-coins'}</p></div>"
        f"<span class='pill {mode_pill}' id='alphai-mode-pill'><span class='dot'></span>"
        f"<span id='alphai-mode-short'>{esc(mode_label)}</span></span>"
        "</div>"
        "<div class='kpi-strip'>"
        f"<div class='mini-kpi quota'><p class='label'>API quota</p>"
        f"<p class='value' id='alphai-quota'>{esc(quota if quota is not None else '—')}</p></div>"
        f"<div class='mini-kpi'><p class='label'>Polls</p>"
        f"<p class='value' id='alphai-polls'>{esc(polls if polls is not None else '—')}</p></div>"
        f"<div class='mini-kpi'><p class='label'>Live skips</p>"
        f"<p class='value' id='alphai-skips'>{esc(skips if skips is not None else '—')}</p></div>"
        f"<div class='mini-kpi'><p class='label'>Macro RO</p>"
        f"<p class='value' id='alphai-macro'>{'ja' if macro else 'nee'}</p></div>"
        f"<div class='mini-kpi'><p class='label'>Status</p>"
        f"<p class='value' id='alphai-enabled'>{'aan' if enabled else 'uit'}</p></div>"
        f"<div class='mini-kpi'><p class='label'>Modus</p>"
        f"<p class='value' id='alphai-mode'>{esc('observatie' if obs else 'enforce')}</p></div>"
        "</div>"
        f"<p class='hint'>Laatste poll: <span id='alphai-last-poll'>{esc(last_poll)}</span>"
        " · <span id='alphai-headline-count'>"
        f"{esc(len(headlines))}</span> headlines</p>"
        + err_html
        + "<p class='label' style='margin-top:.65rem'>"
        + ("Would-block (observatie)" if obs else "Geblokkeerde bases")
        + "</p>"
        + f"<div class='chip-row' id='alphai-chips'>{''.join(chips)}</div>"
        + "<div class='headline-feed' id='alphai-headline-feed'>"
        + ("".join(hl_cards) if hl_cards else "<p class='hint'>Nog geen headlines — poll loopt…</p>")
        + "</div>"
        + "</section>"
    )


def render_operator_panel(
    *,
    primary: str,
    running: bool,
    idle_ok: bool,
    why_extra: str,
    skip_li: str,
    net_hr: Any,
    cap_util: Any,
    esc: Callable[[Any], str],
) -> str:
    status_cls = "ok" if idle_ok else ""
    return (
        f"<section class='panel operator-panel {status_cls}'>"
        "<div class='panel-head'><div>"
        "<h2>Operator status</h2>"
        f"<p class='sub'>{'Sessie actief' if running else 'Sessie gestopt'}</p></div></div>"
        "<h3 class='label'>Waarom nu stil / wat blokkeert</h3>"
        f"<p class='primary' id='operator-primary'>{esc(primary)}</p>"
        + (f"<ul id='operator-why'>{why_extra}</ul>" if why_extra else "")
        + (f"<ul id='operator-skips'>{skip_li}</ul>" if skip_li else "")
        + "<div class='kpi-strip' style='margin-top:.75rem'>"
        f"<div class='mini-kpi'><p class='label'>NET EUR/uur</p>"
        f"<p class='value' id='eff-net-hr-inline'>{esc(net_hr if net_hr is not None else '—')}</p></div>"
        f"<div class='mini-kpi'><p class='label'>Cap util %</p>"
        f"<p class='value' id='eff-cap-util-inline'>{esc(cap_util if cap_util is not None else '—')}</p></div>"
        "</div></section>"
    )

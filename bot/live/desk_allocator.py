"""€20k multi-strategy desk allocator (loop winner).

Regime: sma20_50
  risk_on  BTC > SMA50     → Donchian 10/5 Friday-flat 50% + Donchian 10/5 50%
  mid      SMA20–SMA50     → 100% cash
  risk_off BTC < SMA20     → short-weakest 70% + Donchian 20/10 Friday 30%

15m WR core can run beside this mix on capped Bitvavo leftover + all OKX.
Shorts stay paper (spot cannot short). Donchian is the live €20k long sleeve.
No per-coin hardcodes.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any

BOOK_EUR = 20_000.0

# Loop-winner map (sma20_50 Donchian/short).
REGIME_MAP: dict[str, dict[str, float]] = {
    "risk_on": {"donch_fri10": 0.5, "donch10": 0.5},
    "mid": {"cash": 1.0},
    "risk_off": {"short_weakest": 0.7, "donch_fri": 0.3},
}

SLEEVE_META: dict[str, dict[str, str]] = {
    "donch_fri10": {
        "title": "Donchian 10/5 Friday-flat",
        "kind": "long",
        "blurb": "Live 10d-breakout, 5d-exit, vrijdag plat (weekend-gap).",
    },
    "donch10": {
        "title": "Donchian 10/5",
        "kind": "long",
        "blurb": "Live 10d-breakout, 5d-exit, BTC boven SMA50.",
    },
    "donch_fri": {
        "title": "Donchian 20/10 Friday-flat",
        "kind": "long",
        "blurb": "Langzamere Donchian; in deze mix alleen in risk_off (meestal idle).",
    },
    "short_weakest": {
        "title": "Short weakest",
        "kind": "short_paper",
        "blurb": "Apart paper-boek. Shorts als BTC onder SMA20; geen mix-cash, geen venue-orders.",
    },
    "cash": {
        "title": "Cash",
        "kind": "cash",
        "blurb": "Geen risico. Mid-regime is 100% cash.",
    },
    "core_15m": {
        "title": "15m WR-core",
        "kind": "idle",
        "blurb": "€2k Bitvavo-satelliet naast de clip-owner; OKX-rest blijft satelliet-cash.",
    },
}

REGIME_COPY = {
    "risk_on": "Uptrend: BTC noteert boven SMA50. Donchian-longs aan, shorts covered.",
    "mid": "Chop: BTC tussen SMA20 en SMA50. 100% cash — niet traden.",
    "risk_off": "Downtrend: BTC onder SMA20. Short-weakest aan, Donchian-longs uit (behalve 30% Friday-sleeve).",
}


def _sma(closes: Sequence[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(float(x) for x in closes[-period:]) / period


def classify_sma20_50(
    btc_closes: Sequence[float],
    *,
    btc_live: float | None = None,
) -> dict[str, Any]:
    """risk_off if BTC < SMA20; mid if SMA20 ≤ BTC < SMA50; else risk_on."""
    sma20 = _sma(btc_closes, 20)
    sma50 = _sma(btc_closes, 50)
    daily = float(btc_closes[-1]) if btc_closes else 0.0
    px = float(btc_live) if btc_live and btc_live > 0 else daily
    ready20 = sma20 is not None
    ready50 = sma50 is not None
    if ready20 and px < float(sma20):
        label = "risk_off"
    elif ready50 and px < float(sma50):
        label = "mid"
    else:
        label = "risk_on"
        if not ready50:
            label = "mid"  # SMA50 not ready → do not assume risk_on
    why = REGIME_COPY[label]
    if sma20 is not None and sma50 is not None:
        if label == "risk_off":
            why = (
                f"BTC {px:,.0f} < SMA20 {sma20:,.0f} "
                f"(SMA50 {sma50:,.0f}) — downtrend. Shorts aan, longs uit."
            )
        elif label == "mid":
            why = (
                f"SMA20 {sma20:,.0f} ≤ BTC {px:,.0f} < SMA50 {sma50:,.0f} "
                f"— chop. 100% cash."
            )
        else:
            why = (
                f"BTC {px:,.0f} > SMA50 {sma50:,.0f} "
                f"(SMA20 {sma20:,.0f}) — uptrend. Donchian-longs aan, shorts covered."
            )
    elif not ready50:
        why = "SMA50 nog niet klaar (te weinig daily history) — cash tot de gate live is."
    gap20 = (px / sma20 - 1.0) if sma20 else None
    gap50 = (px / sma50 - 1.0) if sma50 else None
    return {
        "label": label,
        "classifier": "sma20_50",
        "btc": round(px, 2) if px else None,
        "btc_daily": round(daily, 2) if daily else None,
        "sma20": round(float(sma20), 2) if sma20 is not None else None,
        "sma50": round(float(sma50), 2) if sma50 is not None else None,
        "gap_vs_sma20_pct": round(gap20, 4) if gap20 is not None else None,
        "gap_vs_sma50_pct": round(gap50, 4) if gap50 is not None else None,
        "why": why,
        "ready": bool(ready20 and ready50),
    }


def weights_for(label: str) -> dict[str, float]:
    return dict(REGIME_MAP.get(label) or {"cash": 1.0})


def euros_for(label: str, *, book: float = BOOK_EUR) -> dict[str, int]:
    return {k: int(round(book * v)) for k, v in weights_for(label).items() if v > 0}


def now_trading(label: str) -> dict[str, Any]:
    """Human-readable 'what is on right now' for the dashboard."""
    wmap = weights_for(label)
    active_ids = [sid for sid, w in wmap.items() if float(w) > 1e-9]
    titles = [SLEEVE_META[sid]["title"] for sid in active_ids if sid in SLEEVE_META]
    idle_ids = [
        sid
        for sid in SLEEVE_META
        if sid not in wmap or float(wmap.get(sid, 0.0)) <= 1e-9
    ]
    idle_titles = [SLEEVE_META[sid]["title"] for sid in idle_ids]
    if label == "mid" or (len(active_ids) == 1 and active_ids[0] == "cash"):
        headline = "Cash — geen trades"
    else:
        headline = " + ".join(titles) if titles else "Cash — geen trades"
    stance = {
        "risk_on": "Longs aan · shorts covered",
        "risk_off": "Shorts aan · longs grotendeels uit",
        "mid": "Alles plat · 100% cash",
    }.get(label, "")
    return {
        "ids": active_ids,
        "titles": titles,
        "headline": headline,
        "idle_ids": idle_ids,
        "idle_titles": idle_titles,
        "stance": stance,
    }


def sleeve_rows(label: str, *, book: float = BOOK_EUR) -> list[dict[str, Any]]:
    wmap = weights_for(label)
    rows = []
    for sid, meta in SLEEVE_META.items():
        w = float(wmap.get(sid, 0.0))
        active = w > 1e-9
        target = int(round(book * w)) if active else 0
        if sid == "core_15m":
            reason = meta["blurb"]
        elif sid == "cash" and not active:
            reason = "Geen cash-sleeve: het boek is volledig bij actieve strategieën."
        elif active:
            reason = f"Actief in {label} · {int(round(w * 100))}% van €{book:,.0f}."
        else:
            reason = f"Uit in {label}. {meta['blurb']}"
        rows.append(
            {
                "id": sid,
                "title": meta["title"],
                "kind": meta["kind"],
                "blurb": meta["blurb"],
                "weight": w,
                "target_eur": target,
                "active": active,
                "why": reason,
            }
        )
    return rows


def snapshot(
    btc_closes: Sequence[float],
    *,
    btc_live: float | None = None,
    book: float = BOOK_EUR,
) -> dict[str, Any]:
    regime = classify_sma20_50(btc_closes, btc_live=btc_live)
    label = str(regime["label"])
    return {
        "ok": True,
        "mix": "loop_sma20_50_donchian_short",
        "book_eur": book,
        "regime": regime,
        "label": label,
        "why": regime["why"],
        "weights": weights_for(label),
        "euros": euros_for(label, book=book),
        "sleeves": sleeve_rows(label, book=book),
        "now_trading": now_trading(label),
        "map": REGIME_MAP,
        "paper_short": True,
        "core_15m": "idle",
        "ts": time.time(),
    }


_cache: dict[str, Any] = {"snap": None, "ms": 0.0}


def cached_snapshot(
    btc_closes: Sequence[float],
    *,
    btc_live: float | None = None,
    book: float = BOOK_EUR,
    ttl_sec: float = 15.0,
) -> dict[str, Any]:
    now = time.time()
    prev = _cache.get("snap")
    if prev and (now - float(_cache.get("ms") or 0)) < ttl_sec:
        live = btc_live or (prev.get("regime") or {}).get("btc")
        # Recompute if live px would flip the label.
        old_px = (prev.get("regime") or {}).get("btc")
        if live and old_px and abs(float(live) - float(old_px)) / max(float(old_px), 1) < 0.002:
            return prev
    snap = snapshot(btc_closes, btc_live=btc_live, book=book)
    _cache["snap"] = snap
    _cache["ms"] = now
    return snap


def target_book(sleeve_id: str, snap: Mapping[str, Any] | None) -> float:
    if not snap:
        return 0.0
    return float((snap.get("euros") or {}).get(sleeve_id) or 0.0)


def live_snapshot(*, book: float = BOOK_EUR, btc_live: float | None = None) -> dict[str, Any]:
    """Fetch BTC dailies and classify. Safe for dashboard/status."""
    try:
        from bot.live.momentum_short_weakest import fetch_daily_closes

        rows = fetch_daily_closes("BTC", days=80)
        closes = [c for _, c in rows]
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(exc),
            "mix": "loop_sma20_50_donchian_short",
            "label": "mid",
            "why": f"BTC daily fetch failed: {exc} — mix stays cash tot de gate live is.",
            "sleeves": sleeve_rows("mid", book=book),
            "now_trading": now_trading("mid"),
            "weights": weights_for("mid"),
            "euros": euros_for("mid", book=book),
            "book_eur": book,
            "map": REGIME_MAP,
        }
    return cached_snapshot(closes, btc_live=btc_live, book=book, ttl_sec=20.0)

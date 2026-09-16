"""Momentum-desk trade outcome learning — size overlay only.

Records closed desk trades with **generic** entry-context attributes
(chase band, candidate count, soft regime, breadth — never per-coin keys).
Replays into a capped clip multiplier. Does **not** mutate WR membership
filters (hours, min_excess, trail, etc.).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

# Soft size only — never hard-block entries.
_DEFAULT_MULT_MIN = 0.75
_DEFAULT_MULT_MAX = 1.15
_DEFAULT_MIN_SAMPLES = 8
_DEFAULT_FULL_SAMPLES = 25
_MAX_TRADES = 800


def chase_band(ret_24h: float) -> str:
    """Coin-agnostic extension band from 24h return."""
    r = float(ret_24h)
    if r >= 0.08:
        return "chase_hi"
    if r >= 0.06:
        return "chase_mid"
    return "chase_lo"


def cands_band(n_cands: int) -> str:
    n = int(n_cands)
    if n <= 1:
        return "cands_1"
    if n <= 3:
        return "cands_2_3"
    return "cands_4p"


def breadth_band(breadth: float) -> str:
    b = float(breadth)
    if b >= 0.85:
        return "br_strong"
    if b >= 0.60:
        return "br_ok"
    return "br_thin"


def excess_band(excess: float) -> str:
    e = float(excess)
    if e >= 0.06:
        return "xs_hi"
    if e >= 0.04:
        return "xs_mid"
    return "xs_lo"


def build_entry_ctx(
    *,
    excess: float,
    ret_24h: float,
    from_high: float,
    n_cands: int,
    breadth: float,
    btc_ret: float | None,
    soft: bool,
    alphai_pick: bool,
) -> dict[str, Any]:
    """Snapshot at entry — attributes only, no base/coin identity."""
    return {
        "excess": round(float(excess), 5),
        "ret_24h": round(float(ret_24h), 5),
        "from_high": round(float(from_high), 5),
        "n_cands": int(n_cands),
        "breadth": round(float(breadth), 4),
        "btc_ret": None if btc_ret is None else round(float(btc_ret), 5),
        "soft": bool(soft),
        "alphai_pick": bool(alphai_pick),
        "chase": chase_band(ret_24h),
        "cands": cands_band(n_cands),
        "br": breadth_band(breadth),
        "xs": excess_band(excess),
    }


def bucket_key(ctx: Mapping[str, Any], *, level: str = "full") -> str:
    """Hierarchical generic bucket (no coin)."""
    chase = str(ctx.get("chase") or chase_band(float(ctx.get("ret_24h") or 0.0)))
    cands = str(ctx.get("cands") or cands_band(int(ctx.get("n_cands") or 0)))
    soft = "soft" if bool(ctx.get("soft")) else "firm"
    br = str(ctx.get("br") or breadth_band(float(ctx.get("breadth") or 0.0)))
    if level == "chase":
        return chase
    if level == "chase_cands":
        return f"{chase}|{cands}"
    if level == "chase_cands_soft":
        return f"{chase}|{cands}|{soft}"
    return f"{chase}|{cands}|{soft}|{br}"


@dataclass
class OutcomeBucket:
    samples: int = 0
    wins: int = 0
    sum_net_eur: float = 0.0
    sum_ret_pct: float = 0.0  # net / clip
    sum_peak_ret: float = 0.0
    sum_hold_h: float = 0.0

    def record(
        self,
        *,
        net_eur: float,
        clip_eur: float,
        peak_return: float,
        hold_h: float,
        won: bool,
    ) -> None:
        self.samples += 1
        if won:
            self.wins += 1
        self.sum_net_eur += float(net_eur)
        clip = max(1.0, float(clip_eur))
        self.sum_ret_pct += float(net_eur) / clip
        self.sum_peak_ret += float(peak_return)
        self.sum_hold_h += float(hold_h)

    @property
    def win_rate(self) -> float | None:
        if self.samples <= 0:
            return None
        return self.wins / self.samples

    @property
    def avg_net(self) -> float | None:
        if self.samples <= 0:
            return None
        return self.sum_net_eur / self.samples

    @property
    def avg_ret_pct(self) -> float | None:
        if self.samples <= 0:
            return None
        return self.sum_ret_pct / self.samples

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> OutcomeBucket:
        return cls(
            samples=int(raw.get("samples") or 0),
            wins=int(raw.get("wins") or 0),
            sum_net_eur=float(raw.get("sum_net_eur") or 0.0),
            sum_ret_pct=float(raw.get("sum_ret_pct") or 0.0),
            sum_peak_ret=float(raw.get("sum_peak_ret") or 0.0),
            sum_hold_h=float(raw.get("sum_hold_h") or 0.0),
        )


@dataclass
class MomentumTradeOutcomeStore:
    """Append-only trade log + generic context buckets → soft clip mult."""

    enabled: bool = True
    auto_size: bool = True
    min_samples: int = _DEFAULT_MIN_SAMPLES
    full_samples: int = _DEFAULT_FULL_SAMPLES
    mult_min: float = _DEFAULT_MULT_MIN
    mult_max: float = _DEFAULT_MULT_MAX
    trades: list[dict[str, Any]] = field(default_factory=list)
    buckets: dict[str, OutcomeBucket] = field(default_factory=dict)
    path: str | None = None

    def record_close(
        self,
        *,
        holding_id: str,
        base: str,
        net_eur: float,
        clip_eur: float,
        peak_return: float,
        hold_h: float,
        exit_reason: str,
        entry_ctx: Mapping[str, Any] | None,
        opened_ms: int | None = None,
        closed_ms: int | None = None,
    ) -> dict[str, Any]:
        """Persist one closed round-trip and update generic buckets."""
        ctx = dict(entry_ctx or {})
        won = float(net_eur) > 0.0
        row = {
            "holding_id": str(holding_id),
            "base": str(base or "").upper(),  # audit only — never used as bucket key
            "net_eur": round(float(net_eur), 4),
            "clip_eur": round(float(clip_eur), 2),
            "peak_return": round(float(peak_return), 5),
            "hold_h": round(float(hold_h), 3),
            "exit_reason": str(exit_reason or ""),
            "won": won,
            "entry_ctx": ctx,
            "opened_ms": opened_ms,
            "closed_ms": closed_ms,
            "buckets": {
                "full": bucket_key(ctx, level="full"),
                "chase_cands_soft": bucket_key(ctx, level="chase_cands_soft"),
                "chase_cands": bucket_key(ctx, level="chase_cands"),
                "chase": bucket_key(ctx, level="chase"),
            },
        }
        # Dedup by holding_id if re-recorded
        hid = row["holding_id"]
        self.trades = [t for t in self.trades if str(t.get("holding_id")) != hid]
        self.trades.append(row)
        if len(self.trades) > _MAX_TRADES:
            self.trades = self.trades[-_MAX_TRADES:]
        self._rebuild_buckets()
        return row

    def _rebuild_buckets(self) -> None:
        self.buckets = {}
        for t in self.trades:
            ctx = t.get("entry_ctx") or {}
            clip = float(t.get("clip_eur") or 0.0)
            net = float(t.get("net_eur") or 0.0)
            peak = float(t.get("peak_return") or 0.0)
            hold = float(t.get("hold_h") or 0.0)
            won = bool(t.get("won"))
            for level in ("full", "chase_cands_soft", "chase_cands", "chase"):
                key = bucket_key(ctx, level=level)
                b = self.buckets.setdefault(key, OutcomeBucket())
                b.record(
                    net_eur=net, clip_eur=clip, peak_return=peak, hold_h=hold, won=won
                )

    def size_mult(self, ctx: Mapping[str, Any] | None) -> tuple[float, tuple[str, ...]]:
        """Soft clip multiplier from matching generic buckets. Neutral if cold."""
        if not self.enabled or not self.auto_size or not ctx:
            return 1.0, ()
        # Prefer most specific bucket with enough samples.
        for level in ("full", "chase_cands_soft", "chase_cands", "chase"):
            key = bucket_key(ctx, level=level)
            bucket = self.buckets.get(key)
            if bucket is None or bucket.samples < int(self.min_samples):
                continue
            mult = self._mult_from_bucket(bucket)
            if abs(mult - 1.0) < 1e-6:
                return 1.0, (f"outcome_n={bucket.samples}",)
            return mult, (f"outcome_{level}={key}", f"outcome_x{mult:.2f}")
        return 1.0, ()

    def _mult_from_bucket(self, bucket: OutcomeBucket) -> float:
        avg_ret = bucket.avg_ret_pct or 0.0
        wr = bucket.win_rate if bucket.win_rate is not None else 0.5
        # Quality in [-1, +1]: blend return vs fees (~0.3%) and win rate vs 50%.
        ret_score = max(-1.0, min(1.0, avg_ret / 0.02))
        wr_score = max(-1.0, min(1.0, (wr - 0.5) / 0.25))
        quality = 0.55 * ret_score + 0.45 * wr_score
        # Ramp strength until full_samples.
        n = bucket.samples
        full = max(int(self.min_samples) + 1, int(self.full_samples))
        strength = 0.5 if n < full else 1.0
        if n < full:
            strength = 0.5 + 0.5 * (n - self.min_samples) / max(1, full - self.min_samples)
        # Map quality → mult around 1.0 within caps.
        span = min(1.0 - self.mult_min, self.mult_max - 1.0)
        raw = 1.0 + strength * quality * span
        return float(max(self.mult_min, min(self.mult_max, raw)))

    def summary(self, *, top_n: int = 8) -> dict[str, Any]:
        ranked = sorted(
            (
                (k, b)
                for k, b in self.buckets.items()
                if "|" in k and b.samples >= max(3, self.min_samples // 2)
            ),
            key=lambda kb: (kb[1].avg_ret_pct or 0.0, kb[1].samples),
        )
        worst = ranked[:top_n]
        best = list(reversed(ranked[-top_n:])) if ranked else []
        return {
            "enabled": self.enabled,
            "auto_size": self.auto_size,
            "trades": len(self.trades),
            "buckets": len(self.buckets),
            "min_samples": self.min_samples,
            "mult_range": [self.mult_min, self.mult_max],
            "worst_buckets": [
                {
                    "key": k,
                    "samples": b.samples,
                    "win_rate": round(b.win_rate or 0.0, 3),
                    "avg_ret_pct": round((b.avg_ret_pct or 0.0) * 100, 2),
                    "mult": round(self._mult_from_bucket(b), 3),
                }
                for k, b in worst
            ],
            "best_buckets": [
                {
                    "key": k,
                    "samples": b.samples,
                    "win_rate": round(b.win_rate or 0.3, 3),
                    "avg_ret_pct": round((b.avg_ret_pct or 0.0) * 100, 2),
                    "mult": round(self._mult_from_bucket(b), 3),
                }
                for k, b in best
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "auto_size": self.auto_size,
            "min_samples": self.min_samples,
            "full_samples": self.full_samples,
            "mult_min": self.mult_min,
            "mult_max": self.mult_max,
            "trades": list(self.trades),
            "buckets": {k: v.to_dict() for k, v in self.buckets.items()},
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None, *, path: str | None = None) -> MomentumTradeOutcomeStore:
        data = dict(raw or {})
        store = cls(
            enabled=bool(data.get("enabled", True)),
            auto_size=bool(data.get("auto_size", True)),
            min_samples=int(data.get("min_samples") or _DEFAULT_MIN_SAMPLES),
            full_samples=int(data.get("full_samples") or _DEFAULT_FULL_SAMPLES),
            mult_min=float(data.get("mult_min") or _DEFAULT_MULT_MIN),
            mult_max=float(data.get("mult_max") or _DEFAULT_MULT_MAX),
            trades=list(data.get("trades") or []),
            path=path,
        )
        if data.get("buckets") and not store.trades:
            store.buckets = {
                str(k): OutcomeBucket.from_dict(v)
                for k, v in dict(data.get("buckets") or {}).items()
            }
        else:
            store._rebuild_buckets()
        return store

    @classmethod
    def load(cls, path: str | Path, **kwargs: Any) -> MomentumTradeOutcomeStore:
        p = Path(path)
        if not p.exists():
            store = cls(path=str(p), **kwargs)
            return store
        try:
            raw = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            return cls(path=str(p), **kwargs)
        store = cls.from_dict(raw if isinstance(raw, Mapping) else {}, path=str(p))
        for k, v in kwargs.items():
            if hasattr(store, k) and v is not None:
                setattr(store, k, v)
        return store

    def save(self, path: str | Path | None = None) -> None:
        p = Path(path or self.path or "./data/momentum_trade_outcomes.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(p)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        tmp.replace(p)


def parse_entry_ctx_from_reason(reason: str) -> dict[str, Any]:
    """Best-effort parse of legacy ``entry_reason`` tags for backfill."""
    ctx: dict[str, Any] = {}
    soft = "soft_regime" in reason
    alphai = "alphai_pick" in reason
    excess = None
    from_high = None
    for part in str(reason or "").split(","):
        part = part.strip()
        if part.startswith("excess="):
            try:
                excess = float(part.split("=", 1)[1])
            except ValueError:
                pass
        if part.startswith("from_high="):
            try:
                from_high = float(part.split("=", 1)[1])
            except ValueError:
                pass
    if excess is None:
        return {}
    # Unknown ret24 / n_cands / breadth → neutral bands (won't overfit chase).
    ret_24h = float(excess)  # lower bound proxy only
    return build_entry_ctx(
        excess=float(excess),
        ret_24h=ret_24h,
        from_high=float(from_high or 0.0),
        n_cands=2,
        breadth=0.7,
        btc_ret=None,
        soft=soft,
        alphai_pick=alphai,
    )

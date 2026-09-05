"""Desk lesson ledger — close the AlphaI sleeve learn loop.

Records structural misses (idle cash vs unheld sleeve, early BE harvest on
sleeve winners, avoid bags blocking sleeve deploy), settles with outcomes,
and exposes capped feedback multipliers for deploy / harvest / avoid recycle.

``auto_apply`` defaults False (shadow). When True, bridge multiplies live
knobs by the feedback scales (still hard-capped).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_KIND_MISSED_DEPLOY = "missed_deploy"
_KIND_EARLY_HARVEST = "early_harvest"
_KIND_AVOID_VS_SLEEVE = "avoid_vs_sleeve"
_OPEN_KINDS = frozenset(
    {_KIND_MISSED_DEPLOY, _KIND_EARLY_HARVEST, _KIND_AVOID_VS_SLEEVE}
)

# Hard caps on live feedback (fractional multipliers around 1.0).
_DEPLOY_BIAS_MIN = 1.0
_DEPLOY_BIAS_MAX = 1.25
_HARVEST_SCALE_MIN = 1.0
_HARVEST_SCALE_MAX = 1.35
_AVOID_AGE_MIN = 0.55
_AVOID_AGE_MAX = 1.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _day_key(ts: datetime | None = None) -> str:
    instant = ts or datetime.now(UTC)
    return instant.astimezone(UTC).strftime("%Y-%m-%d")


def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


@dataclass
class DeskLessonFeedback:
    """Capped multipliers derived from settled lessons."""

    deploy_urgency_bias: float = 1.0
    harvest_floor_scale: float = 1.0
    avoid_recycle_age_scale: float = 1.0
    missed_deploy_hits: int = 0
    early_harvest_hits: int = 0
    avoid_vs_sleeve_hits: int = 0
    sum_missed_eur: float = 0.0
    sample_n: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "deploy_urgency_bias": round(self.deploy_urgency_bias, 4),
            "harvest_floor_scale": round(self.harvest_floor_scale, 4),
            "avoid_recycle_age_scale": round(self.avoid_recycle_age_scale, 4),
            "missed_deploy_hits": self.missed_deploy_hits,
            "early_harvest_hits": self.early_harvest_hits,
            "avoid_vs_sleeve_hits": self.avoid_vs_sleeve_hits,
            "sum_missed_eur": round(self.sum_missed_eur, 2),
            "sample_n": self.sample_n,
        }


@dataclass
class DeskLessonStore:
    """Persisted desk-lesson ledger (cap ~400 events)."""

    lessons: list[dict[str, Any]] = field(default_factory=list)
    auto_apply: bool = False
    feedback: DeskLessonFeedback = field(default_factory=DeskLessonFeedback)
    _path: str | None = field(default=None, repr=False)

    # --- record -----------------------------------------------------------

    def record_missed_deploy(
        self,
        *,
        sleeve_bases: list[str],
        free_cash_eur: float,
        held_non_picks: list[str],
        playbook: str = "",
        min_free_eur: float = 150.0,
        day: str | None = None,
    ) -> dict[str, Any] | None:
        """Idle deployable cash while rank-1/2 sleeve is unheld."""
        sleeve = [str(b).upper() for b in sleeve_bases if str(b or "").strip()]
        if not sleeve or free_cash_eur < min_free_eur:
            return None
        day_k = day or _day_key()
        # One open missed_deploy per day (aggregate sleeve).
        existing = self._open_lesson(_KIND_MISSED_DEPLOY, day=day_k)
        if existing is not None:
            existing["free_cash_eur"] = max(
                _f(existing.get("free_cash_eur")), free_cash_eur
            )
            existing["sleeve_bases"] = sorted(
                set(existing.get("sleeve_bases") or []) | set(sleeve)
            )
            existing["held_non_picks"] = sorted(
                set(existing.get("held_non_picks") or [])
                | {str(b).upper() for b in held_non_picks if b}
            )
            existing["playbook"] = playbook or existing.get("playbook")
            existing["updated_at"] = _now_iso()
            return existing
        entry = {
            "id": f"md-{day_k}-{sleeve[0]}",
            "kind": _KIND_MISSED_DEPLOY,
            "day": day_k,
            "recorded_at": _now_iso(),
            "updated_at": _now_iso(),
            "settled": False,
            "sleeve_bases": sleeve,
            "free_cash_eur": round(free_cash_eur, 2),
            "held_non_picks": sorted(
                {str(b).upper() for b in held_non_picks if b}
            ),
            "playbook": playbook,
            "outcome": None,
        }
        self._append(entry)
        return entry

    def record_early_harvest(
        self,
        *,
        base: str,
        venue: str,
        exit_gain_pct: float,
        peak_gain_pct: float,
        notional_eur: float,
        soft_arm_pct: float,
        reason: str = "trail_be_harvest",
        day: str | None = None,
    ) -> dict[str, Any] | None:
        """Sleeve BE harvest while gain is still far below soft-arm / peak."""
        b = str(base or "").strip().upper()
        if not b or notional_eur <= 0:
            return None
        # Premature when exit gain << soft arm, or left meaningful MFE on table.
        soft = max(soft_arm_pct, 0.0)
        premature = exit_gain_pct < max(0.003, soft * 0.35) if soft > 0 else (
            exit_gain_pct < 0.003
        )
        mfe_left = max(0.0, peak_gain_pct - exit_gain_pct)
        if not premature and mfe_left < 0.002:
            return None
        day_k = day or _day_key()
        # Debounce: one open early_harvest per base/day.
        existing = self._open_lesson(
            _KIND_EARLY_HARVEST, day=day_k, base=b
        )
        if existing is not None:
            existing["exit_gain_pct"] = min(
                _f(existing.get("exit_gain_pct"), exit_gain_pct), exit_gain_pct
            )
            existing["peak_gain_pct"] = max(
                _f(existing.get("peak_gain_pct")), peak_gain_pct
            )
            existing["notional_eur"] = max(
                _f(existing.get("notional_eur")), notional_eur
            )
            existing["updated_at"] = _now_iso()
            return existing
        missed_eur = round(mfe_left * notional_eur, 2)
        entry = {
            "id": f"eh-{day_k}-{b}-{venue}",
            "kind": _KIND_EARLY_HARVEST,
            "day": day_k,
            "recorded_at": _now_iso(),
            "updated_at": _now_iso(),
            "settled": False,
            "base": b,
            "venue": str(venue or "").lower(),
            "reason": reason,
            "exit_gain_pct": round(exit_gain_pct, 6),
            "peak_gain_pct": round(peak_gain_pct, 6),
            "soft_arm_pct": round(soft, 6),
            "notional_eur": round(notional_eur, 2),
            "mfe_left_pct": round(mfe_left, 6),
            "missed_eur_est": missed_eur,
            "outcome": None,
        }
        self._append(entry)
        return entry

    def record_avoid_vs_sleeve(
        self,
        *,
        avoid_bases: list[str],
        sleeve_bases: list[str],
        avoid_notional_eur: float,
        free_cash_eur: float,
        playbook: str = "",
        day: str | None = None,
    ) -> dict[str, Any] | None:
        """Held avoid/non-pick bags while sleeve targets are unheld."""
        avoids = [str(b).upper() for b in avoid_bases if str(b or "").strip()]
        sleeve = [str(b).upper() for b in sleeve_bases if str(b or "").strip()]
        if not avoids or not sleeve:
            return None
        if avoid_notional_eur < 25.0 and free_cash_eur < 150.0:
            return None
        day_k = day or _day_key()
        existing = self._open_lesson(_KIND_AVOID_VS_SLEEVE, day=day_k)
        if existing is not None:
            existing["avoid_bases"] = sorted(
                set(existing.get("avoid_bases") or []) | set(avoids)
            )
            existing["sleeve_bases"] = sorted(
                set(existing.get("sleeve_bases") or []) | set(sleeve)
            )
            existing["avoid_notional_eur"] = max(
                _f(existing.get("avoid_notional_eur")), avoid_notional_eur
            )
            existing["free_cash_eur"] = max(
                _f(existing.get("free_cash_eur")), free_cash_eur
            )
            existing["updated_at"] = _now_iso()
            return existing
        entry = {
            "id": f"avs-{day_k}-{avoids[0]}",
            "kind": _KIND_AVOID_VS_SLEEVE,
            "day": day_k,
            "recorded_at": _now_iso(),
            "updated_at": _now_iso(),
            "settled": False,
            "avoid_bases": avoids,
            "sleeve_bases": sleeve,
            "avoid_notional_eur": round(avoid_notional_eur, 2),
            "free_cash_eur": round(free_cash_eur, 2),
            "playbook": playbook,
            "outcome": None,
        }
        self._append(entry)
        return entry

    # --- settle -----------------------------------------------------------

    def settle_early_harvest_immediate(self, lesson_id: str) -> dict[str, Any] | None:
        """Close early_harvest using MFE-left estimated at record time."""
        lesson = next((x for x in self.lessons if x.get("id") == lesson_id), None)
        if lesson is None or lesson.get("kind") != _KIND_EARLY_HARVEST:
            return None
        if lesson.get("settled"):
            return lesson
        missed = _f(lesson.get("missed_eur_est"))
        lesson["settled"] = True
        lesson["settled_at"] = _now_iso()
        lesson["outcome"] = {
            "missed_eur": round(missed, 2),
            "positive_miss": missed > 0.5,
            "method": "mfe_at_exit",
            "exit_gain_pct": lesson.get("exit_gain_pct"),
            "peak_gain_pct": lesson.get("peak_gain_pct"),
        }
        self.recompute_feedback()
        return lesson

    def settle_open_with_day_returns(
        self,
        day_returns_pct: Mapping[str, float],
        *,
        day: str | None = None,
    ) -> int:
        """Settle open missed_deploy / avoid_vs_sleeve with day % returns.

        ``day_returns_pct`` maps BASE → day return in percent (e.g. 2.5 = +2.5%).
        """
        if not day_returns_pct:
            return 0
        day_k = day or _day_key()
        settled_n = 0
        for lesson in self.lessons:
            if lesson.get("settled"):
                continue
            if day and lesson.get("day") not in {day_k, day}:
                # Allow settling prior open days too when day is None;
                # when day is set, only that day.
                if lesson.get("day") != day:
                    continue
            kind = str(lesson.get("kind") or "")
            if kind == _KIND_MISSED_DEPLOY:
                sleeve = [str(b).upper() for b in (lesson.get("sleeve_bases") or [])]
                rets = [_f(day_returns_pct.get(b)) for b in sleeve if b in day_returns_pct]
                if not rets:
                    continue
                avg_sleeve = sum(rets) / len(rets)
                # Opportunity vs idle cash (~0): positive when sleeve rose.
                notional = min(_f(lesson.get("free_cash_eur")), 2000.0)
                missed = max(0.0, avg_sleeve / 100.0 * notional)
                lesson["settled"] = True
                lesson["settled_at"] = _now_iso()
                lesson["outcome"] = {
                    "missed_eur": round(missed, 2),
                    "positive_miss": missed > 5.0,
                    "method": "day_return_vs_cash",
                    "sleeve_avg_pct": round(avg_sleeve, 4),
                    "sleeve_returns_pct": {
                        b: round(_f(day_returns_pct.get(b)), 4)
                        for b in sleeve
                        if b in day_returns_pct
                    },
                }
                settled_n += 1
            elif kind == _KIND_AVOID_VS_SLEEVE:
                sleeve = [str(b).upper() for b in (lesson.get("sleeve_bases") or [])]
                avoids = [str(b).upper() for b in (lesson.get("avoid_bases") or [])]
                sleeve_rets = [
                    _f(day_returns_pct.get(b)) for b in sleeve if b in day_returns_pct
                ]
                avoid_rets = [
                    _f(day_returns_pct.get(b)) for b in avoids if b in day_returns_pct
                ]
                if not sleeve_rets or not avoid_rets:
                    continue
                avg_s = sum(sleeve_rets) / len(sleeve_rets)
                avg_a = sum(avoid_rets) / len(avoid_rets)
                spread_pp = avg_s - avg_a
                notional = min(_f(lesson.get("avoid_notional_eur")), 2000.0)
                missed = max(0.0, spread_pp / 100.0 * notional)
                lesson["settled"] = True
                lesson["settled_at"] = _now_iso()
                lesson["outcome"] = {
                    "missed_eur": round(missed, 2),
                    "positive_miss": missed > 5.0 and spread_pp > 0.5,
                    "method": "sleeve_minus_avoid_day",
                    "sleeve_avg_pct": round(avg_s, 4),
                    "avoid_avg_pct": round(avg_a, 4),
                    "spread_pp": round(spread_pp, 4),
                }
                settled_n += 1
            elif kind == _KIND_EARLY_HARVEST and not lesson.get("settled"):
                # Prefer immediate MFE settle; fall back to day return from exit.
                self.settle_early_harvest_immediate(str(lesson.get("id") or ""))
                if lesson.get("settled"):
                    settled_n += 1
        if settled_n:
            self.recompute_feedback()
        return settled_n

    # --- feedback ---------------------------------------------------------

    def recompute_feedback(self, *, lookback: int = 40) -> DeskLessonFeedback:
        """Derive capped multipliers from recent settled positive misses."""
        settled = [
            x
            for x in self.lessons
            if x.get("settled") and isinstance(x.get("outcome"), dict)
        ][-lookback:]
        fb = DeskLessonFeedback(sample_n=len(settled))
        for lesson in settled:
            outcome = lesson.get("outcome") or {}
            missed = _f(outcome.get("missed_eur"))
            fb.sum_missed_eur += missed
            if not outcome.get("positive_miss"):
                continue
            kind = str(lesson.get("kind") or "")
            if kind == _KIND_MISSED_DEPLOY:
                fb.missed_deploy_hits += 1
            elif kind == _KIND_EARLY_HARVEST:
                fb.early_harvest_hits += 1
            elif kind == _KIND_AVOID_VS_SLEEVE:
                fb.avoid_vs_sleeve_hits += 1

        # Each hit nudges bias; require ≥1 hit to move off 1.0.
        if fb.missed_deploy_hits:
            fb.deploy_urgency_bias = min(
                _DEPLOY_BIAS_MAX,
                _DEPLOY_BIAS_MIN + 0.05 * fb.missed_deploy_hits,
            )
        if fb.early_harvest_hits:
            fb.harvest_floor_scale = min(
                _HARVEST_SCALE_MAX,
                _HARVEST_SCALE_MIN + 0.07 * fb.early_harvest_hits,
            )
        if fb.avoid_vs_sleeve_hits:
            fb.avoid_recycle_age_scale = max(
                _AVOID_AGE_MIN,
                _AVOID_AGE_MAX - 0.10 * fb.avoid_vs_sleeve_hits,
            )
        self.feedback = fb
        return fb

    def applied_feedback(self) -> DeskLessonFeedback:
        """Return live multipliers; identity when auto_apply is off."""
        if not self.auto_apply:
            return DeskLessonFeedback()
        return self.feedback

    # --- summary / persist ------------------------------------------------

    def summary(self) -> dict[str, Any]:
        open_n = sum(1 for x in self.lessons if not x.get("settled"))
        settled_n = sum(1 for x in self.lessons if x.get("settled"))
        latest = self.lessons[-1] if self.lessons else None
        return {
            "lesson_count": len(self.lessons),
            "open_count": open_n,
            "settled_count": settled_n,
            "auto_apply": self.auto_apply,
            "feedback": self.feedback.as_dict(),
            "applied_feedback": self.applied_feedback().as_dict(),
            "latest_kind": (latest or {}).get("kind"),
            "latest_id": (latest or {}).get("id"),
            "latest_settled": bool((latest or {}).get("settled")),
            "latest_missed_eur": (
                ((latest or {}).get("outcome") or {}).get("missed_eur")
                if latest and latest.get("settled")
                else (latest or {}).get("missed_eur_est")
            ),
        }

    def snapshot(self) -> dict[str, Any]:
        s = self.summary()
        return {
            "desk_lessons_count": s["lesson_count"],
            "desk_lessons_open": s["open_count"],
            "desk_lessons_settled": s["settled_count"],
            "desk_lessons_auto_apply": self.auto_apply,
            "desk_lessons_deploy_bias": s["feedback"]["deploy_urgency_bias"],
            "desk_lessons_harvest_scale": s["feedback"]["harvest_floor_scale"],
            "desk_lessons_avoid_age_scale": s["feedback"]["avoid_recycle_age_scale"],
            "desk_lessons_applied_deploy_bias": s["applied_feedback"][
                "deploy_urgency_bias"
            ],
            "desk_lessons_applied_harvest_scale": s["applied_feedback"][
                "harvest_floor_scale"
            ],
            "desk_lessons_applied_avoid_age_scale": s["applied_feedback"][
                "avoid_recycle_age_scale"
            ],
            "desk_lessons_sum_missed_eur": s["feedback"]["sum_missed_eur"],
            "desk_lessons_latest_kind": s["latest_kind"],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "auto_apply": self.auto_apply,
            "feedback": self.feedback.as_dict(),
            "lessons": self.lessons[-400:],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> DeskLessonStore:
        store = cls()
        if not isinstance(raw, dict):
            return store
        store.auto_apply = bool(raw.get("auto_apply") or False)
        lessons = raw.get("lessons")
        if isinstance(lessons, list):
            store.lessons = [x for x in lessons if isinstance(x, dict)][-400:]
        fb_raw = raw.get("feedback")
        if isinstance(fb_raw, dict):
            store.feedback = DeskLessonFeedback(
                deploy_urgency_bias=_f(fb_raw.get("deploy_urgency_bias"), 1.0),
                harvest_floor_scale=_f(fb_raw.get("harvest_floor_scale"), 1.0),
                avoid_recycle_age_scale=_f(
                    fb_raw.get("avoid_recycle_age_scale"), 1.0
                ),
                missed_deploy_hits=int(fb_raw.get("missed_deploy_hits") or 0),
                early_harvest_hits=int(fb_raw.get("early_harvest_hits") or 0),
                avoid_vs_sleeve_hits=int(fb_raw.get("avoid_vs_sleeve_hits") or 0),
                sum_missed_eur=_f(fb_raw.get("sum_missed_eur")),
                sample_n=int(fb_raw.get("sample_n") or 0),
            )
        else:
            store.recompute_feedback()
        return store

    def save(self, path: Path | str | None = None) -> None:
        p = Path(path or self._path or "./data/alphai/desk_lessons.json")
        self._path = str(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(p)

    @classmethod
    def load(cls, path: Path | str) -> DeskLessonStore:
        p = Path(path)
        if not p.exists():
            store = cls()
            store._path = str(p)
            return store
        try:
            store = cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            logger.exception("DESK_LESSONS_LOAD_FAILED path=%s", p)
            store = cls()
        store._path = str(p)
        return store

    # --- internals --------------------------------------------------------

    def _append(self, entry: dict[str, Any]) -> None:
        self.lessons.append(entry)
        if len(self.lessons) > 400:
            self.lessons = self.lessons[-400:]
        logger.info(
            "DESK_LESSON_RECORD kind=%s id=%s",
            entry.get("kind"),
            entry.get("id"),
        )

    def _open_lesson(
        self,
        kind: str,
        *,
        day: str,
        base: str | None = None,
    ) -> dict[str, Any] | None:
        for lesson in reversed(self.lessons):
            if lesson.get("settled"):
                continue
            if lesson.get("kind") != kind:
                continue
            if lesson.get("day") != day:
                continue
            if base is not None and str(lesson.get("base") or "").upper() != base:
                continue
            return lesson
        return None


def sync_desk_lessons_day_returns(
    path: Path | str,
    day_returns_pct: Mapping[str, float],
    *,
    enabled: bool = True,
    auto_apply: bool | None = None,
) -> dict[str, Any] | None:
    """Settle open desk lessons with day returns; return summary."""
    if not enabled or not day_returns_pct:
        return None
    store = DeskLessonStore.load(path)
    if auto_apply is not None:
        store.auto_apply = bool(auto_apply)
    n = store.settle_open_with_day_returns(day_returns_pct)
    store.save(path)
    summary = store.summary()
    if n:
        logger.info(
            "DESK_LESSONS_SETTLED n=%s feedback=%s",
            n,
            summary.get("feedback"),
        )
    return summary

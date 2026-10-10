"""Selection veto and forward label for the precision push."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from bot.research.moonshot_preimage.day50 import Series
from bot.research.moonshot_preimage.precision_max import (
    _metrics,
    baseline_mask,
    build_panel,
    refine,
)


def test_refine_rejects_atom_that_collapses_on_select() -> None:
    rng = np.random.default_rng(0)
    n = 400
    y_fit = np.zeros(n, dtype=bool)
    y_fit[:80] = True
    y_sel = np.zeros(120, dtype=bool)
    y_sel[:12] = True
    # Atom keeps the fit hits and drops select hits.
    good_fit = np.zeros(n, dtype=bool)
    good_fit[:80] = True
    good_sel = np.zeros(120, dtype=bool)
    good_sel[:4] = True
    base_fit = np.ones(n, dtype=bool)
    base_sel = np.ones(120, dtype=bool)
    added = refine(
        y_fit,
        {"too_tight": good_fit},
        y_sel,
        {"too_tight": good_sel},
        base_fit,
        base_sel,
        min_n_fit=40,
        min_n_sel=20,
    )
    assert added == []


def test_forward_high_is_the_label_not_the_feature() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    n = 80

    def bars(close: float, vol: float) -> Series:
        dates, o, h, l, c, v = [], [], [], [], [], []
        for i in range(n):
            day = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            dates.append(day)
            o.append(close)
            h.append(close * 1.01)
            l.append(close * 0.99)
            c.append(close)
            v.append(vol)
        return Series(dates=dates, o=o, h=h, l=l, c=c, v=v)

    btc = bars(50_000.0, 10.0)
    alt = bars(100.0, 2_000.0)
    # Signal index 60: +20% close. Entry index 61 and the next week stay flat,
    # except index 64 which trades +60% off its open.
    alt.c[60] = 120.0
    alt.h[60] = 122.0
    alt.o[60] = 100.0
    alt.l[60] = 100.0
    alt.v[60] = 20_000.0
    alt.o[64] = 120.0
    alt.h[64] = 192.0
    alt.l[64] = 118.0
    alt.c[64] = 180.0
    panel, _top_r3, _top_xs = build_panel({"BTC": btc, "ALT": alt}, min_qvol=1_000.0)
    sig = [i for i, d in enumerate(panel.date) if d == "2024-03-01"]
    assert sig, "signal day missing"
    i = sig[0]
    assert panel.base[i] == "ALT"
    assert bool(panel.hit7[i])
    assert not bool(panel.hit1[i])
    # r3 on the signal day is the move into the close, not the later +60% bar.
    r3 = panel.x[i, panel.names.index("r3")]
    assert r3 < 0.30
    # Flat history does not pass the published momentum rule.
    assert not bool(baseline_mask(panel)[i])


def test_metrics_empty() -> None:
    y = np.zeros(4, dtype=bool)
    m = _metrics(y, np.zeros(4, dtype=bool))
    assert m["n"] == 0
    assert m["p"] == 0.0

"""Buy pressure is known before the entry, and a doji does not count."""

from __future__ import annotations

import numpy as np

from bot.research.rise_pattern.engine import (
    BAR_MS,
    buy_share,
    flow_names,
    measure_clock,
)
from bot.research.small_peak.engine import QUOTE_BARS


def test_buy_share_ignores_dojis_and_the_future() -> None:
    quote = np.ones(20)
    open_ = np.full(20, 100.0)
    close = np.full(20, 100.0)
    close[12:20] = 101.0
    assert buy_share(open_, close, quote, np.asarray([19]), width=8)[0] == 1.0
    close[19] = 100.0
    assert buy_share(open_, close, quote, np.asarray([19]), width=8)[0] == 1.0
    close[16:20] = 99.0
    assert buy_share(open_, close, quote, np.asarray([19]), width=8)[0] == 0.5
    before = buy_share(open_, close, quote, np.asarray([18]), width=8)[0]
    close[19] = 1.0
    assert buy_share(open_, close, quote, np.asarray([18]), width=8)[0] == before


def test_flow_names_use_the_locked_cuts() -> None:
    share = np.asarray([0.65, 0.649, 0.35, 0.349, np.nan])
    assert flow_names(share).tolist() == ["koop", "tussen", "verkoop", "verkoop", "onbekend"]
    assert flow_names(np.asarray([0.60]), buy=0.60).tolist() == ["koop"]
    assert flow_names(np.asarray([0.70]), buy=0.70).tolist() == ["koop"]
    assert flow_names(np.asarray([0.69]), buy=0.70).tolist() == ["tussen"]


def test_clock_buys_after_up_bars_and_does_not_read_the_entry() -> None:
    n = 220
    ts = 1_700_000_000_000 + np.arange(n) * BAR_MS
    open_ = np.full(n, 100.0)
    close = np.full(n, 100.0)
    high = np.full(n, 100.0)
    volume = np.full(n, 500.0)
    close[88:96] = 101.0
    high[96:192] = 110.0
    open_[192] = 105.0
    market = np.ones(n)
    clock = measure_clock(ts, open_, high, close, volume, market, start_ms=int(ts[0]))
    assert len(clock) == 1
    assert int(clock.ms[0]) == int(ts[QUOTE_BARS])
    assert clock.share_2h[0] == 1.0
    assert clock.rise_24h[0] >= 0.03
    assert clock.net_24h[0] > 0.0
    close[96] = 1.0
    high[192] = 1.0
    again = measure_clock(ts, open_, high, close, volume, market, start_ms=int(ts[0]))
    assert again.share_2h[0] == clock.share_2h[0]
    assert again.rise_24h[0] == clock.rise_24h[0]
    ts[192] += BAR_MS
    gapped = measure_clock(ts, open_, high, close, volume, market, start_ms=int(ts[0]))
    assert len(gapped) == 0

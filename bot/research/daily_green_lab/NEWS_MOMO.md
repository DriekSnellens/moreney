# News + momentum sleeve — what the tape allows

asof replay on `data/ignition_expand_candles`, book €2,000, next-open fills, trail 10%, time ≤ 3d.

Buying the hottest name every day does **not** survive both windows once the live chase cap (+12% closed day) and the stop are on. That path stays off the live sleeve.

| Pack | IS | OOS | Full | Full DD |
|---|---:|---:|---:|---:|
| `top_day` btc0 hs3 | −1,928 | −1,206 | −1,960 | 99% |
| `day_cap12` btc0 hs3 | −1,000 | +703 | −875 | 68% |
| `day_cap12` btc1 hs5 | −229 | +238 | −168 | 65% |

Live MoonShot (`news_momo`) therefore does two things only:

1. **AlphaI pick** whose live day return is still inside **+2% … +12%** — headline confirmed, move not already finished. No 20d-breakout required.
2. **Fresh 20d breakout**, still capped at +8% on the day and +3% through the high.

A name that is only hot, with no pick and no fresh breakout, is not bought. After a filled exit the sleeve waits until the next UTC day. An empty scan no longer burns that clock.

```bash
.venv/bin/python -m bot.research.daily_green_lab.news_momo_replay
```

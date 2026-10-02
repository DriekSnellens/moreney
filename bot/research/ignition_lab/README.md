# Ignition lab notes

Desk-universe **classic** early-signal (quiet + brk20 + day≥+6% + vol≥2×) stays the strict path.
Loosening those classic gates alone adds trades and **cuts** PnL on the wet desk tape.

## Exit ablation (€2k book, next-open entry, desk universe)

| Exit | full (24-03→26-10) | 2026 YTD | last 180d |
|---|---:|---:|---:|
| trail 15% (old live) | +€690 | +€1,561 | +€1,975 |
| trail 12% | +€1,493 | +€1,977 | +€2,206 |
| trail 12% + ratchet 30%→10% | **+€1,790** | **+€2,070** | **+€2,307** |

Shipped classic exits: `trail_pct=0.12`, `trail_ratchet_arm_pct=0.30`, `trail_ratchet_pct=0.10`.

## Miss #2 — explosive starts (coil hybrid)

Explosive legs rarely fire on the classic brk20 day; day-0 fails brk20 / day_ret / vol.
Soft/coil mid-leg entries catch the run. Wet desk ablation (`HYBRID_ENTRY.json`):

| Mode | full PnL | notes |
|---|---:|---|
| classic-only (trail12+ratchet) | ~+€2.7k | rare same-day hit on explosive starts |
| **desk coil_hybrid + coil-trail 25%** | **+€6.8k** | ~21% desk / ~80% leg catch vs ~2% classic |

Coil gates (generic, no coin hardcodes): liquid + quieter + compress (5d span &lt; 0.5× 20d) + brk5 + day≥2.5% + vol≥1.2× + close_loc≥0.65.
Classic wins rank when both fire. Coil lots use `coil_trail_pct=0.25` (then shared ratchet).

Artifacts: `EARLY_ENTRY_GRID.json`, `HYBRID_ENTRY.json`, `EXPLOSIVE_CAPTURE.json`.

## Daily OKX sleeve (activity pack)

Separate goal from sparse sniper: **trade often** on OKX / ex-desk with short holds.
Grid: `daily_sleeve.py` → `DAILY_SLEEVE.json` (€2k, wet next-open, 100 entry×exit combos).

**Winner (balanced):** `top_day_always` + **trail 8% or time-stop 2d**, BTC > SMA50

| | full | 2026 | 180d | entry-days | maxDD |
|---|---:|---:|---:|---:|---:|
| **top_day + t08/time2 + SMA50** | **+€10.1k** | **+€9.2k** | **+€9.0k** | **18.7%** | 54% |
| same, no BTC SMA | +€12.0k | +€11.4k | +€11.2k | 35% | **88%** |
| live sniper t12/r30 (same runner) | +€0.4k | −€0.1k | +€0.4k | 2.3% | 59% |
| EOD every risk-on day | −€1.5k | +€5.5k | +€5.5k | 48% | 98% |

Rules: each risk-on day pick the liquid ex-desk name with the strongest **day return**; enter next open; exit on 8% peak trail or after 2 sessions.  
**Not every calendar day** — BTC regime + open lot block coverage. True daily EOD is not +EV on the full window.

Live ignition stays the sparse sniper unless this pack is explicitly armed as a separate mode.

## Sniper waters (ex-desk, complementary to RS)

RS residual owns the desk-16 book. Ignition defaults to **`universe_mode=ex_desk`**:
top liquid EUR names **excluding** the RS desk, `quiet_max=0.15`, coil hybrid.

Wet €10k · 2 slots · compound (2024-03→2026-10):

| Universe | full | 2026 YTD | last 180d |
|---|---:|---:|---:|
| desk quiet12 | +€11.8k | **−€0.9k** | +€9.1k |
| expanded quiet15 | +€10.0k | +€1.0k | +€5.3k |
| **ex_desk quiet15** | **+€12.0k** | **+€9.7k** | **+€9.6k** |

Ex-desk PnL is almost all **coil**. Live re-ranks the pool hourly from Bitvavo 24h volume.

## Weekly ambition (€2–3k banked)

Mentality: ignition is a **spike engine** — quiet most weeks, then bank thousands when an explosive leg is caught.

Wet weekly scan (`WEEKLY_AMBITION.json`, desk coil hybrid, 2024-03→2026-10):

| Config | total PnL | realized max week | weeks ≥€2k | weeks ≥€3k |
|---|---:|---:|---:|---:|
| €2k · 1 slot · fixed (old) | +€5.7k | +€5.7k | 1 | 1 |
| **€10k · 1 slot · compound** | +€20k | +€28.5k | **4** | **4** |
| **€10k · 2 slots · compound** | +€11.8k | +€14.3k | **8** | **4** |
| €20k · 2 slots · compound | +€23.6k | +€28.5k | 10 | 10 |

Oracle ceiling on this tape: only ~27% of weeks even have a signal with forward ≥+50%. **€2–3k every calendar week is not in the data** — the shipped ambition stack maximizes spike-week banking instead:

- `book_eur=10000` seed, `max_positions=2`, `compound_sizing=true` (winners grow firepower)
- classic|coil hybrid + path trails

Re-run: `.venv/bin/python -m bot.research.ignition_lab.weekly_ambition`

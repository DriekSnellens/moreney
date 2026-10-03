# Moonshot preimage — can we hit 60–70% precision?

asof `2026-10-03T14:21:39.213557+00:00`  universe `80` bases  horizon 7d  `forward high from next open >= +30% / +50%`

Precision = P(moonshot | signal). Base rate of +50%/7d is ~3%. 60–70% precision is a different claim than 'patterns exist' — patterns can lift 5–6× and still land near 15–20% precision.

## Base rate (no filter)

| Split | n | P(+30%) | P(+50%) |
|---|---:|---:|---:|
| train ≤2025-12-31 | 32084 | 8.2% | 2.6% |
| test 2026+ | 16506 | 9.0% | 2.9% |

AlphaI session days in store: **13**. Test rows on AlphaI days: 436. AlphaI pick rows in test: 50 (P50=0.0).

## Verdict on 60–70%

**No rule in this grid reaches 60% precision on +50%/7d with n≥10 test signals.** Best viable (n≥20) below.

Best test precision (n≥20): **`r3_15+xs25+trend`** → P50=**15.8%** (n=278, lift×5.48, wilson≥12.0%).

## Rule table (sorted by test P50)

| Rule | train n | train P50 | test n | test P50 | test P30 | lift× |
|---|---:|---:|---:|---:|---:|---:|
| `r3_15+xs25+trend` | 420 | 13.6% | 278 | 15.8% | 27.7% | 5.48 |
| `r3_15+vol2+trend` | 350 | 13.7% | 275 | 13.8% | 25.8% | 4.78 |
| `xs25+sma50+trend` | 1153 | 10.2% | 667 | 11.4% | 24.6% | 3.94 |
| `xs25` | 1637 | 8.8% | 938 | 10.8% | 22.2% | 3.73 |
| `brk20+day6+vol2` | 466 | 10.3% | 394 | 9.9% | 21.3% | 3.43 |
| `xs15` | 3401 | 6.2% | 1863 | 7.8% | 18.1% | 2.69 |
| `coil+xs15` | 2132 | 5.8% | 863 | 6.6% | 18.3% | 2.28 |
| `quiet+brk20+day6+vol2` | 39 | 2.6% | 49 | 6.1% | 18.4% | 2.12 |
| `all_liquid` | 32084 | 2.6% | 16506 | 2.9% | 9.0% | 1.0 |
| `coil` | 19900 | 2.4% | 9652 | 2.8% | 8.7% | 0.97 |
| `quiet10` | 25932 | 2.1% | 13340 | 2.3% | 7.8% | 0.79 |
| `alphai_pick` | 0 | 0.0% | 50 | 0.0% | 2.0% | 0.0 |
| `alphai+xs15` | 0 | 0.0% | 20 | 0.0% | 5.0% | 0.0 |
| `alphai+coil` | 0 | 0.0% | 15 | 0.0% | 0.0% | 0.0 |
| `alphai+r3_15+trend` | 0 | 0.0% | 11 | 0.0% | 0.0% | 0.0 |

## How to read this

1. **Patterns are real** — several rules lift 4–6× over the ~3% base rate.
2. **Lift ≠ 60% accuracy** — 5× on a 3% event ≈ 15% precision.
3. **AlphaI alone** is not a moonshot oracle on this sample; combine with tape features.
4. Trading the signal still needs exits; prior explosive scans showed high P50 rules can still lose money with naive holds.

Reproduce:

```bash
.venv/bin/python -m bot.research.moonshot_preimage
```

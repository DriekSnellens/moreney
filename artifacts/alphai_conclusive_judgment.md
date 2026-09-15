# AlphaI entry judgment (conclusive)

**Verdict: NO_CLEAR_ENTRY_EDGE** (confidence medium-high)

AlphaI does **not** convincingly improve momentum-desk entries.

## All labeled data used
- 745 AlphaI sessions (2026-09-07 → 09-15), merged from live + orphaned tmp scorecards
- 734 sessions with settled vs-BTC score; 1170 individual pick outcomes
- Momentum desk ledger: 8 round-trips
- Research ablation: 1061 candidates (other bot surface, SHADOW)

## Results
1. **Pick quality:** mean picks vs BTC **-0.24 pp**; only **33%** of days beat BTC; individual beat-rate **35%**.
2. **Desk replay (WR hours 7/13/16):** with AlphaI **-€8.47** vs tape-only; rank/macro alone **-€15.26**; clip×1.3 recovers **+€6.79** of that.
3. **Live fills:** AlphaI-tagged n=3 → +€32 WR 33%; tape-only n=5 → +€82 WR 60%.
4. **Research ablation:** ALPHAI_FULL **-€0.64** vs baseline on 1061 candidates.

## Decision
Keep AlphaI as optional **size/rank overlay** only. Do **not** tighten entry gates on picks/avoid. Re-open after ≥30 AlphaI-tagged desk fills or ≥30 days settled history.

## Data ceiling
Months of candles exist, but AlphaI labels only start ~Sep 7. Paper-*live files are a different strategy without desk pick tags. This judgment uses every AlphaI-labeled source on disk.

# Edge Robustness Lab

Second-layer interpretation of frozen **H-0005** / **H-0007**. Mechanical `OOS_PASS` is **not** rewritten.

Does **not** create families, retune thresholds, change the production cost model, or enable execution.

```bash
python -m bot.research.robustness.runner --stride 4
```

- `INTERPRETATION_VERDICT` is separate from the tournament verdict.
- H-0007 is `GATE_INACTIVE` when admitted candidates equal parent candidates (or selectivity < 5%).
- Full fee/slip/adverse/fill/latency/partial cartesian stress is written to `data/edge_robustness_lab/stress_H-000*.jsonl` (no cherry-picking).
- `NET` is absolute EUR (sum at 100 EUR notional). First-lab `NET/fill` was mean-edge execution replay / estimated fills — not `NET/fills`.

## Frozen robustness run

Mechanical `OOS_PASS` was not rewritten.

| ID | Interpretation | Accounting | Independent windows | Final decision |
|---|---|---|---|---|
| H-0005 | PROMISING_BUT_UNCONFIRMED | FAIL (NET/fill units) | 14 | PROMISING_REPLICATION_REQUIRED |
| H-0007 | GATE_INACTIVE | FAIL (NET/fill units) | 14 | COLLECT_MORE_DATA |

H-0007 tape has both WIDE and NON_WIDE quotes; the SHMR parent still almost never produces NON_WIDE candidates (aggregate selectivity 0.07%). Not a validated regime-gated strategy. Execution remains **DISABLED**.


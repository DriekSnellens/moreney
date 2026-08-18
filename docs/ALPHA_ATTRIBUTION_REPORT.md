# Alpha attribution report

Research-only forensic attribution of the H-0005 freshness gate versus its parent
on the **same paired signal universe**. Canonical execution replay is the only
evaluation NET. Expected metrics are not substitutes.

**NO NEW ALPHA CLAIMED.**

This document does not enable trading, does not retune `quote_age_ms`, does not
modify H-0005, does not resurrect H-0007, and does not create a production
strategy. Bins are existing forensic/tournament constants. Contexts are
`DESCRIPTIVE_ONLY`.

Artifact: `data/research/alpha_attribution_results.json`

---

## 1. Paired delta plausibility audit

Sign convention (explicit):

```
paired_delta_eur = child_replay_net_eur - parent_replay_net_eur
```

Negative delta means the child underperformed the parent on canonical replay.

| Field | Value |
|---|---|
| Stored audit | PASS |
| Live audit | NOT_RUN |
| Combined `PAIRED_DELTA_ACCOUNTING_AUDIT` | PASS |
| Reported aggregate delta | -51461.2894766299632178779 |
| SUM(window paired deltas) | -51461.2894766299632178779 |
| SUM(parent replay NET) | 66096.9144332335648577683 |
| SUM(child replay NET) | 14635.6249566036016398904 |
| SUM(excluded signal NET) | 51461.2894766299632178779 |
| Issues | [] |

Identities checked per complete window:

- `parent_replay_net = shared_signal_net + excluded_signal_net`
- `child_replay_net = shared_signal_net` (pure filter: `child_only_signals = 0`)
- `paired_delta = child - parent`
- `SUM(window deltas) = reported aggregate` — **not rewritten on mismatch**

### Complete windows

| window_id | parent_signals | child_signals | parent_fills | child_fills | parent_replay_net | child_replay_net | paired_delta | retained_net | excluded_net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| W0_FIRST_OOS | 2318 | 667 | 1275 | 367 | 8192.5579220087085562459 | 2411.453753034821126561 | -5781.1041689738874296849 | 2411.453753034821126561 | 5781.1041689738874296849 |
| W1 | 1325 | 381 | 729 | 210 | 5244.8884258040992355888 | 1460.1769915538769026738 | -3784.7114342502223329150 | 1460.1769915538769026738 | 3784.711434250222332915 |
| W2 | 1489 | 415 | 819 | 228 | 5091.147847229059390506 | 1421.12894045778504056 | -3670.018906771274349946 | 1421.12894045778504056 | 3670.018906771274349946 |
| W3 | 1554 | 392 | 855 | 216 | 5367.3897462711725725866 | 1336.9056257577596844306 | -4030.4841205134128881560 | 1336.9056257577596844306 | 4030.484120513412888156 |
| W4 | 1324 | 270 | 728 | 148 | 4381.738602530012742172 | 893.44015063323621525 | -3488.298451896776526922 | 893.44015063323621525 | 3488.298451896776526922 |
| W5 | 1392 | 248 | 766 | 136 | 4307.876955847106850719 | 713.076349866004681239 | -3594.800605981102169480 | 713.076349866004681239 | 3594.800605981102169480 |
| W6 | 1317 | 357 | 724 | 196 | 4288.648816342037628444 | 1215.60177180841585211 | -3073.047044533621776334 | 1215.60177180841585211 | 3073.047044533621776334 |
| W7 | 1257 | 306 | 691 | 168 | 4173.849655636566422587 | 1038.879625174960352230 | -3134.970030461606070357 | 1038.879625174960352230 | 3134.970030461606070357 |
| W8 | 1077 | 250 | 592 | 138 | 4202.366205974921979928 | 946.23591064073392901 | -3256.130295334188050918 | 946.23591064073392901 | 3256.130295334188050918 |
| W9 | 1186 | 213 | 652 | 117 | 3877.263190960557537426 | 658.559721383952824856 | -3218.703469576604712570 | 658.559721383952824856 | 3218.703469576604712570 |
| W10 | 1145 | 210 | 630 | 116 | 3886.357788382441193796 | 587.70035776741844115 | -3298.657430615022752646 | 587.70035776741844115 | 3298.657430615022752646 |
| W11 | 1130 | 203 | 622 | 112 | 3337.53183952180713809 | 675.80019526584177933 | -2661.73164425596535876 | 675.80019526584177933 | 2661.73164425596535876 |
| W12 | 1231 | 165 | 677 | 91 | 3896.218223922187943565 | 499.41022847487183491 | -3396.807995447316108655 | 499.41022847487183491 | 3396.807995447316108655 |
| W13 | 1067 | 155 | 587 | 85 | 3587.88399789420679402 | 495.43579848845998043 | -3092.44819940574681359 | 495.43579848845998043 | 3092.44819940574681359 |
| W14 | 745 | 106 | 410 | 58 | 2261.195214908678872094 | 281.81953629546299515 | -1979.375678613215876944 | 281.81953629546299515 | 1979.375678613215876944 |

---

## 2. Parent vs child decomposition

| | EUR |
|---|---|
| PARENT_REPLAY_NET | 66096.9144332335648577683 |
| H-0005 / RETAINED_SIGNAL_NET | 14635.6249566036016398904 |
| EXCLUDED_SIGNAL_NET | 51461.2894766299632178779 |
| First-lab OOS H-0005 canonical NET (different split; headline only) | 2218.3727597787497 |

Walk-forward complete windows are the paired universe that produced the published
`-51461.29` delta. First-lab OOS `2218.37` is a **different sample** and is not
substituted for paired-window economics.

---

## 3. Retained vs excluded economics

Excluded economically positive on canonical replay: **None**

Do not infer this from EXPECTED_NET.

| GROUP | signals | fills | gross | fees | slippage | adverse | other_costs | replay_net | net/signal | net/fill | share_signals | share_net | +windows |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ALL_PARENT | None | None | None | None | None | None | None | None | None | None | None | None | None |
| RETAINED_BY_CHILD | None | None | None | None | None | None | None | None | None | None | None | None | None |
| EXCLUDED_BY_CHILD | None | None | None | None | None | None | None | None | None | None | None | None | None |

---

## 4. Feature attribution (pre-trade only)

Outcomes (`forward`, replay NET) are never admission features.
Unavailable pre-trade: inventory_state, predicted adverse as a state,
fill probability as a state (research uses frozen model constants).

Ranked by excluded canonical replay |NET| (forensic; not a threshold search):

| feature | bucket | retained | excluded | difference | economic contribution | stability | pre-trade | usefulness |
|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — |

---

## 5. Window stability

Existing floors: ≥30 signals, ≥3 windows, 70% symbol/route caps.
`STABLE` also requires no negative windows. Not an alpha claim.

See context table. Feature-bucket stability is in the ranked forensics rows.

---

## 6. Parent signal quality map (`DESCRIPTIVE_ONLY`)

Named contexts use existing quote-age / strength / depth buckets. Not grid-searched.

| context | signals | replay_net | net/signal | stability | contribution_share | +windows | −windows | label |
|---|---:|---:|---:|---|---:|---:|---:|---|
| — | — | — | — | — | — | — | — | — |

---

## 7. Leave-one-context-out / context dependency

`CONTEXT_DEPENDENCY` = None

Flagged: None

Existing LOO share floor = 0.50, or WITHOUT-context net ≤ 0. Not an automatic
reject or promote.

| context | ONLY net | WITHOUT net | contribution | +/− windows (ONLY) | CONTEXT_DEPENDENT_FLAG |
|---|---:|---:|---:|---|---|
| — | — | — | — | — | — |

---

## 8. Ranked research observations

- none

Future hypothesis requirements (none created here):

1. pre-trade feature
2. causal availability
3. sufficient economic contribution
4. stability evidence
5. DEV-only threshold definition
6. fresh unseen OOS data

---

## 9. Explicit non-claims

| | |
|---|---|
| NO_NEW_ALPHA_CLAIMED | True |
| NEW_STRATEGIES_CREATED | [] |
| PRODUCTION_EXECUTION | DISABLED |
| DESCRIPTIVE_ONLY | True |
| h0005_modified | False |
| h0007_optimized | False |
| oos_thresholds_created | False |

---

## 10. WHY H-0005 underperformed

Sign convention: paired_delta = child_replay_net - parent_replay_net. H-0005 is a pure freshness filter (child_only=0), so child net equals retained/shared net and parent net equals retained + excluded (plus unsupported, if any). On this paired complete-window universe parent=66096.9144332335648577683 EUR, retained/child=14635.6249566036016398904 EUR, excluded=51461.2894766299632178779 EUR. H-0005 underperformed because the gate dropped excluded parent signals whose canonical replay net is positive, not because retained trades are loss-making in aggregate EUR. Do not retune quote_age_ms on this OOS.

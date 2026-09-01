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
| Live audit | FAIL |
| Combined `PAIRED_DELTA_ACCOUNTING_AUDIT` | PASS |
| LIVE_VS_PUBLISHED (current tape vs frozen number) | FAIL |
| Reported aggregate delta | -51461.2894766299632178779 |
| SUM(window paired deltas) | -51461.2894766299632178779 |
| SUM(parent replay NET) | 66096.9144332335648577683 |
| SUM(child replay NET) | 14635.6249566036016398904 |
| SUM(excluded signal NET) | 51461.2894766299632178779 |
| Stored issues | [] |
| Live vs published issues | ['SUM(window paired deltas)=-51118.60634011018144683610 != reported_aggregate_delta=-51461.2894766299632178779', 'live aggregate_delta=-51118.60634011018144683610 != published=-51461.2894766299632178779'] |

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

Excluded economically positive on canonical replay: **True**

Do not infer this from EXPECTED_NET.

| GROUP | signals | fills | gross | fees | slippage | adverse | other_costs | replay_net | net/signal | net/fill | share_signals | share_net | +windows |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ALL_PARENT | 19557 | 10757 | 75288.704433233552048756232 | 6844.95000000000058671 | 391.14 | 1564.56 | 391.14 | 66096.9144332335648577683 | 3.379706214308614043962177226 | 6.144549078110399261668522822 | 1.0 | 1 | 15 |
| RETAINED_BY_CHILD | 4338 | 2386 | 16674.484956603598755958443 | 1518.30000000000013014 | 86.76 | 347.04 | 86.76 | 14635.6249566036016398904 | 3.373818569986998994903273398 | 6.133958489775189287464543168 | 0.2218131615278417 | 0.2214267501304841754940393824 | 15 |
| EXCLUDED_BY_CHILD | 15219 | 8370 | 58614.219476629953292797789 | 5326.65000000000045657 | 304.38 | 1217.52 | 304.38 | 51461.2894766299632178779 | 3.381384419254219279708121427 | 6.148302207482671830092939068 | 0.7781868384721583 | 0.7785732498695158245059606176 | 15 |

---

## 4. Feature attribution (pre-trade only)

Outcomes (`forward`, replay NET) are never admission features.
Unavailable pre-trade: inventory_state, predicted adverse as a state,
fill probability as a state (research uses frozen model constants).

Ranked by excluded canonical replay |NET| (forensic; not a threshold search):

| feature | bucket | retained | excluded | difference | economic contribution | stability | pre-trade | usefulness |
|---|---|---|---|---|---|---|---|---|
| strength_regime | STRONG | n=4363 net=14695.499142236437616559 | n=14979 net=51178.0660108569756150442 | 36482.5668686205379984852 | 51178.0660108569756150442 | STABLE | True | FORENSIC_ONLY |
| route | okx|bitvavo | n=4427 net=14691.65236333474673523703 | n=15353 net=51118.6063401101814468361 | 36426.95397677543471159907 | 51118.6063401101814468361 | STABLE | True | FORENSIC_ONLY |
| density_regime | SPARSE | n=4426 net=14692.17164697438919683703 | n=15338 net=51036.3649853141327993361 | 36344.19333833974360249907 | 51036.3649853141327993361 | STABLE | True | FORENSIC_ONLY |
| vol_regime | LOW | n=4391 net=14558.199159916951335691 | n=15209 net=50769.1694289653349850451 | 36210.9702690483836493541 | 50769.1694289653349850451 | STABLE | True | FORENSIC_ONLY |
| spread_regime | TIGHT | n=4396 net=14568.162226118124688361 | n=15015 net=50164.1559058410291557832 | 35595.9936797229044674222 | 50164.1559058410291557832 | STABLE | True | FORENSIC_ONLY |
| side | A_RICH | n=4397 net=14454.856450788703548474 | n=15074 net=49817.0332600328047755052 | 35362.1768092441012270312 | 49817.0332600328047755052 | STABLE | True | FORENSIC_ONLY |
| liquidity_regime | DEEP | n=3908 net=13001.155709226796510473 | n=13092 net=43590.9980136827345538342 | 30589.8423044559380433612 | 43590.9980136827345538342 | STABLE | True | FORENSIC_ONLY |
| quote_age_regime | STALE | n=0 net=0 | n=11139 net=37050.5869768670382841527 | 37050.5869768670382841527 | 37050.5869768670382841527 | STABLE | True | FORENSIC_ONLY |
| session_utc | UTC_16_24 | n=2604 net=8488.337324049589578762 | n=10624 net=34681.2123090350359642124 | 26192.8749849854463854504 | 34681.2123090350359642124 | STABLE | True | FORENSIC_ONLY |
| symbol | ETHEUR | n=2775 net=8441.863507310664386157 | n=9678 net=29582.2569131056951816742 | 21140.3934057950307955172 | 29582.2569131056951816742 | UNSTABLE | True | FORENSIC_ONLY |
| imbalance_regime | ASK_HEAVY | n=1835 net=5982.136638731640154888 | n=6981 net=22971.2943234444658689917 | 16989.1576847128257141037 | 22971.2943234444658689917 | STABLE | True | FORENSIC_ONLY |
| imbalance_regime | BID_HEAVY | n=1969 net=6637.444882803050907556 | n=6252 net=21183.4887411144268530152 | 14546.0438583113759454592 | 21183.4887411144268530152 | STABLE | True | FORENSIC_ONLY |
| session_utc | UTC_08_16 | n=1823 net=6203.31503928515715647503 | n=4729 net=16437.3940310751454826237 | 10234.07899178998832614867 | 16437.3940310751454826237 | STABLE | True | FORENSIC_ONLY |
| quote_age_regime | VERY_STALE | n=0 net=0 | n=4214 net=14068.0193632431431626834 | 14068.0193632431431626834 | 14068.0193632431431626834 | STABLE | True | FORENSIC_ONLY |
| symbol | BTCEUR | n=1097 net=4403.880635366899943614 | n=3424 net=13544.437661594464223087 | 9140.557026227564279473 | 13544.437661594464223087 | UNSTABLE | True | FORENSIC_ONLY |

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
| STALE_STRONG | 11029 | 37117.8316053963638332442 | 3.365475709982442998752760903 | STABLE | 0.5640128505292348906904485220 | 15 | 0 | DESCRIPTIVE_ONLY |
| VERY_STALE | 4214 | 14068.0193632431431626834 | 3.338400418425045838320692928 | STABLE | 0.2137663586255851233946747924 | 15 | 0 | DESCRIPTIVE_ONLY |
| FRESH_STRONG_DEEP | 3895 | 13013.05461819478322961 | 3.340963958458224192454428755 | STABLE | 0.1977359590217443872738755117 | 15 | 0 | DESCRIPTIVE_ONLY |
| FRESH_STRONG_NOT_DEEP | 489 | 1689.534953890116559889 | 3.455081705296761881163599182 | STABLE | 0.02567282042612112596496680586 | 15 | 0 | DESCRIPTIVE_ONLY |
| UNKNOWN_AGE | 0 | 0 | None | INSUFFICIENT_DATA | 0E+20 | 0 | 0 | DESCRIPTIVE_ONLY |
| FRESH_NOT_STRONG | 43 | -10.93720875015305426197 | -0.2543536918640245177202325581 | MIXED | -0.0001661930672456166941180919600 | 9 | 4 | DESCRIPTIVE_ONLY |
| STALE_NOT_STRONG | 110 | -67.2446285293255490915 | -0.6113148048120504462863636364 | MIXED | -0.001021795535439910629847539988 | 5 | 9 | DESCRIPTIVE_ONLY |

---

## 7. Leave-one-context-out / context dependency

`CONTEXT_DEPENDENCY` = CONTEXT_DEPENDENT

Flagged: ['STALE_STRONG']

Existing LOO share floor = 0.50, or WITHOUT-context net ≤ 0. Not an automatic
reject or promote.

| context | ONLY net | WITHOUT net | contribution | +/− windows (ONLY) | CONTEXT_DEPENDENT_FLAG |
|---|---:|---:|---:|---|---|
| FRESH_STRONG_DEEP | 13013.05461819478322961 | 52797.20408525014495246313 | 0.1977359590217443872738755117 | 15/0 | False |
| FRESH_STRONG_NOT_DEEP | 1689.534953890116559889 | 64120.72374955481162218413 | 0.02567282042612112596496680586 | 15/0 | False |
| FRESH_NOT_STRONG | -10.93720875015305426197 | 65821.1959121950812363351 | -0.0001661930672456166941180919600 | 9/4 | False |
| STALE_STRONG | 37117.8316053963638332442 | 28692.42709804856434882893 | 0.5640128505292348906904485220 | 15/0 | True |
| STALE_NOT_STRONG | -67.2446285293255490915 | 65877.50333197425373116463 | -0.001021795535439910629847539988 | 5/9 | False |
| VERY_STALE | 14068.0193632431431626834 | 51742.23934020178501938973 | 0.2137663586255851233946747924 | 15/0 | False |
| UNKNOWN_AGE | 0 | 65810.25870344492818207313 | 0E+20 | 0/0 | False |

---

## 8. Ranked research observations

- **Freshness gate drops positive parent replay mass** (RESEARCH_OBSERVATION): On the paired universe, excluded (stale) parent signals have canonical replay net 51461.2894766299632178779. Retained net is 14635.6249566036016398904. Parent net is 66096.9144332335648577683. H-0005 underperformed because it filtered economically positive trades, not because it uniquely captured a better subset in aggregate EUR. [usefulness=HIGH_FORENSIC — explains the paired delta. Not a new threshold. Do not retune quote_age_ms on this OOS.; auto_strategy=False]
- **Descriptive context STALE_STRONG** (RESEARCH_OBSERVATION): Context STALE_STRONG contribution 0.5640128505292348906904485220 replay_net=37117.8316053963638332442 stability=STABLE DESCRIPTIVE_ONLY. [usefulness=DESCRIPTIVE — not a strategy. DEV-only definition required before any hypothesis.; auto_strategy=False]
- **Descriptive context VERY_STALE** (RESEARCH_OBSERVATION): Context VERY_STALE contribution 0.2137663586255851233946747924 replay_net=14068.0193632431431626834 stability=STABLE DESCRIPTIVE_ONLY. [usefulness=DESCRIPTIVE — not a strategy. DEV-only definition required before any hypothesis.; auto_strategy=False]
- **Descriptive context FRESH_STRONG_DEEP** (RESEARCH_OBSERVATION): Context FRESH_STRONG_DEEP contribution 0.1977359590217443872738755117 replay_net=13013.05461819478322961 stability=STABLE DESCRIPTIVE_ONLY. [usefulness=DESCRIPTIVE — not a strategy. DEV-only definition required before any hypothesis.; auto_strategy=False]
- **Descriptive context FRESH_STRONG_NOT_DEEP** (RESEARCH_OBSERVATION): Context FRESH_STRONG_NOT_DEEP contribution 0.02567282042612112596496680586 replay_net=1689.534953890116559889 stability=STABLE DESCRIPTIVE_ONLY. [usefulness=DESCRIPTIVE — not a strategy. DEV-only definition required before any hypothesis.; auto_strategy=False]
- **Descriptive context UNKNOWN_AGE** (RESEARCH_OBSERVATION): Context UNKNOWN_AGE contribution 0E+20 replay_net=0 stability=INSUFFICIENT_DATA DESCRIPTIVE_ONLY. [usefulness=DESCRIPTIVE — not a strategy. DEV-only definition required before any hypothesis.; auto_strategy=False]
- **Feature contrast strength_regime=STRONG** (RESEARCH_OBSERVATION): strength_regime=STRONG: retained n=4363 net=14695.499142236437616559; excluded n=14979 net=51178.0660108569756150442 [usefulness=FORENSIC_ONLY; auto_strategy=False]
- **Feature contrast route=okx|bitvavo** (RESEARCH_OBSERVATION): route=okx|bitvavo: retained n=4427 net=14691.65236333474673523703; excluded n=15353 net=51118.6063401101814468361 [usefulness=FORENSIC_ONLY; auto_strategy=False]
- **Feature contrast density_regime=SPARSE** (RESEARCH_OBSERVATION): density_regime=SPARSE: retained n=4426 net=14692.17164697438919683703; excluded n=15338 net=51036.3649853141327993361 [usefulness=FORENSIC_ONLY; auto_strategy=False]
- **Feature contrast vol_regime=LOW** (RESEARCH_OBSERVATION): vol_regime=LOW: retained n=4391 net=14558.199159916951335691; excluded n=15209 net=50769.1694289653349850451 [usefulness=FORENSIC_ONLY; auto_strategy=False]
- **Feature contrast spread_regime=TIGHT** (RESEARCH_OBSERVATION): spread_regime=TIGHT: retained n=4396 net=14568.162226118124688361; excluded n=15015 net=50164.1559058410291557832 [usefulness=FORENSIC_ONLY; auto_strategy=False]
- **Feature contrast side=A_RICH** (RESEARCH_OBSERVATION): side=A_RICH: retained n=4397 net=14454.856450788703548474; excluded n=15074 net=49817.0332600328047755052 [usefulness=FORENSIC_ONLY; auto_strategy=False]
- **Feature contrast liquidity_regime=DEEP** (RESEARCH_OBSERVATION): liquidity_regime=DEEP: retained n=3908 net=13001.155709226796510473; excluded n=13092 net=43590.9980136827345538342 [usefulness=FORENSIC_ONLY; auto_strategy=False]
- **Feature contrast quote_age_regime=STALE** (RESEARCH_OBSERVATION): quote_age_regime=STALE: retained n=0 net=0; excluded n=11139 net=37050.5869768670382841527 [usefulness=FORENSIC_ONLY; auto_strategy=False]
- **Leave-one-context-out** (RESEARCH_OBSERVATION): CONTEXT_DEPENDENCY=CONTEXT_DEPENDENT. Flagged=['STALE_STRONG']. Not an automatic reject or promote. [usefulness=FORENSIC; auto_strategy=False]

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

Sign convention: paired_delta = child_replay_net - parent_replay_net. H-0005 is a pure freshness filter (child_only=0), so child net equals retained/shared net and parent net equals retained + excluded (plus unsupported, if any). On the published paired complete-window universe parent=66096.9144332335648577683 EUR, retained/child=14635.6249566036016398904 EUR, excluded=51461.2894766299632178779 EUR. Excluded share of parent signals=0.7781868384721583; share of parent net=0.7785732498695158245059606176. Replay NET/signal retained=3.373818569986998994903273398 vs excluded=3.381384419254219279708121427 (near-identical). The gate therefore dropped economically positive parent mass with similar per-signal replay quality rather than removing a loss-making tail. Do not retune quote_age_ms on this OOS. LIVE_VS_PUBLISHED=FAIL records current-tape drift; the published paired delta is not rewritten.

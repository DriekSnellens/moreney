# Nauwkeurigheid omhoog — boven op r3≥15% + xs≥25% + trend

asof `2026-10-05T13:51:52.990824+00:00`  rijen `48521`  bases **80**

Operating point is the candidate with the highest Wilson lower bound on 2025. Fit is 2024. Test (2026) is not used to choose.

Label: forward high / next open - 1 >= +50%. Baseline: `r3>=15% + xs10>=25% + trend`.
Fit ≤ `2024-12-31` · select = 2025 · test 2026.
Select is het hele jaar 2025. Een half jaar (2025-H2) had te weinig +50%-dagen om een strakkere regel van de baseline te onderscheiden.
De scoredrempel is een rangorde uit een gebalanceerd model, geen gekalibreerde kans.

## Kort

+50% binnen 7 dagen: baseline test **15.8% (n=278, wilson≥12.0%)**, operating **23.4% (n=154, wilson≥17.4%)** (2025: baseline 3.6% (n=137, wilson≥1.6%) → operating 6.2% (n=65, wilson≥2.4%)).
+50% binnen 1 dag: baseline test **4.3% (n=278, wilson≥2.5%)**, operating **11.7% (n=77, wilson≥6.3%)** (2025: baseline 0.7% (n=137, wilson≥0.1%) → operating 3.5% (n=29, wilson≥0.6%)).
Zelfde startregel (`r3≥15% + xs≥25% + trend`). De score houdt alleen de hoogste rangen daarvan over. Het zwaarste gewicht op 7 dagen is BTC boven zijn SMA50.

## +50% binnen 7 dagen

Gekozen op select: **`score`**.

| Punt | Fit | Select | Test 2026 |
|---|---|---|---|
| basiskans | 4.0% (n=10470, wilson≥3.6%) | 2.0% (n=21550, wilson≥1.8%) | 2.9% (n=16501, wilson≥2.6%) |
| baseline | 18.4% (n=283, wilson≥14.3%) | 3.6% (n=137, wilson≥1.6%) | 15.8% (n=278, wilson≥12.0%) |
| extra filters | 18.4% (n=283, wilson≥14.3%) | 3.6% (n=137, wilson≥1.6%) | 15.8% (n=278, wilson≥12.0%) |
| score binnen baseline | 21.1% (n=156, wilson≥15.5%) | 6.2% (n=65, wilson≥2.4%) | 23.4% (n=154, wilson≥17.4%) |
| **operating `score`** | 21.1% (n=156, wilson≥15.5%) | 6.2% (n=65, wilson≥2.4%) | 23.4% (n=154, wilson≥17.4%) |

Extra filters op de baseline: geen — niets hield de select-steekproef overeind.
Score-drempel ≥ `0.7769` alleen binnen de baseline.

Eén naam per dag (hoogste 3-daagse return binnen het operating masker):

- select 8.3% (n=48, wilson≥3.3%)
- test 32.5% (n=83, wilson≥23.4%)

Logistische gewichten (fit, gestandaardiseerd): `btc_on` +0.71, `trend` +0.38, `loc20` -0.30, `rngx` +0.26, `r3` +0.16, `xs10` +0.13, `dayloc` +0.08, `volx` +0.05.

## +50% binnen 1 dag

Gekozen op select: **`score`**.

| Punt | Fit | Select | Test 2026 |
|---|---|---|---|
| basiskans | 0.2% (n=10470, wilson≥0.1%) | 0.1% (n=21550, wilson≥0.1%) | 0.3% (n=16501, wilson≥0.2%) |
| baseline | 2.8% (n=283, wilson≥1.4%) | 0.7% (n=137, wilson≥0.1%) | 4.3% (n=278, wilson≥2.5%) |
| extra filters | 4.0% (n=174, wilson≥2.0%) | 1.4% (n=70, wilson≥0.2%) | 6.4% (n=156, wilson≥3.5%) |
| score binnen baseline | 4.1% (n=73, wilson≥1.4%) | 3.5% (n=29, wilson≥0.6%) | 11.7% (n=77, wilson≥6.3%) |
| **operating `score`** | 4.1% (n=73, wilson≥1.4%) | 3.5% (n=29, wilson≥0.6%) | 11.7% (n=77, wilson≥6.3%) |

Extra filters op de baseline: `r3_25`
Score-drempel ≥ `0.9377` alleen binnen de baseline.

Eén naam per dag (hoogste 3-daagse return binnen het operating masker):

- select 4.5% (n=22, wilson≥0.8%)
- test 14.9% (n=47, wilson≥7.4%)

Logistische gewichten (fit, gestandaardiseerd): `btc_on` +2.14, `r1` +0.76, `r3` +0.71, `rngx` +0.58, `brk20` -0.46, `volx` +0.33, `dayloc` +0.27, `xs10` -0.24.

## Hoe te lezen

1. De baseline is het eerdere onderzoek (`RESULTS.md`): ongeveer 16% op +50%/7d in 2026.
2. Een extra filter telt alleen als de fit-Wilson stijgt én de select-periode nog minstens 20 signalen houdt zonder dat de precisie daar instort.
3. Het operating point is de kandidaat met de hoogste select-Wilson. De testkolom is de meting, niet de keuze.
4. Eén naam per dag is de precisie van een sleeve die uit de gefilterde namen de sterkste 3-daagse return pakt.

Reproduce:

```bash
.venv/bin/python -m bot.research.moonshot_preimage.precision_max
```

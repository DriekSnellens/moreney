# Engine voor de stijging van volgende week

Bitvavo heeft hier 428 dagelijkse namen. De engine past elke
week een lineair model op de rangen van de weken die al dicht zijn,
en koopt het bovenste vijfde van de nieuwe week. De koop is de
volgende open, de verkoop de open zeven dagen later.

De score gebruikt zes dingen die op het slot bekend zijn: de afstand
tot de high van 20 dagen, het rendement over 1, 7 en 28 dagen, de
omzet tegen de eigen week, en de log van de omzet. Grote namen,
minstens €1.000.000 die dag, krijgen een eigen model. Obscure namen,
vanaf €10.000 en daaronder, ook. De eerste 26 weken zijn warmup.

Ter vergelijking, op dezelfde weken: het bovenste vijfde op afstand
tot de high, en elke geschikte naam. Een stijging is +10%, +20% of
+50% tot de verkoop. Het boek begint op €20.000. Kosten zijn 0,25%
taker per kant plus impact. Het haalt de lat alleen boven €20.000,
met een plus in 2024, 2025 en 2026, en boven het boek dat alles in
die mand aanhoudt. Niets wordt aangezet.

## Herkenning

Het getal naast een percentage is hoe vaak de keuze die stijging
haalt, gedeeld door hoe vaak de hele mand hem haalt.

| model | mand | +10% | × | +20% | × | +50% | × | bruto |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| walk-forward | alle | 13,3% | 0,86 | 4,1% | 0,71 | 0,5% | 0,56 | -0,5% |
| walk-forward | groot | 11,9% | 0,75 | 2,7% | 0,39 | 0,2% | 0,13 | -0,2% |
| walk-forward | obscure | 13,2% | 0,85 | 4,1% | 0,71 | 0,5% | 0,60 | -0,6% |
| slot bij de high | alle | 14,4% | 0,92 | 5,2% | 0,89 | 0,8% | 0,84 | -0,6% |
| slot bij de high | groot | 14,3% | 0,90 | 6,6% | 0,95 | 1,4% | 1,13 | 0,2% |
| slot bij de high | obscure | 14,5% | 0,94 | 5,1% | 0,89 | 0,7% | 0,84 | -0,7% |

## Boek

| model | mand | eind | PnL | terugval | weken | 2024 | 2025 | 2026 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| walk-forward | alle | €3.487 | −€16.513 | 93,2% | 115 | €9.051 | −€24.111 | −€1.454 |
| walk-forward | groot | €9.441 | −€10.559 | 77,9% | 115 | €5.768 | −€14.860 | −€1.467 |
| walk-forward | obscure | €2.649 | −€17.351 | 94,8% | 115 | €8.299 | −€24.550 | −€1.100 |
| slot bij de high | alle | €3.382 | −€16.618 | 93,5% | 115 | €8.929 | −€23.651 | −€1.896 |
| slot bij de high | groot | €6.875 | −€13.125 | 91,8% | 115 | €14.021 | −€26.325 | −€821 |
| slot bij de high | obscure | €2.889 | −€17.111 | 93,8% | 115 | €6.393 | −€21.837 | −€1.666 |
| elke geschikte naam | alle | €2.915 | −€17.085 | 94,1% | 115 | €10.770 | −€25.748 | −€2.107 |
| elke geschikte naam | groot | €2.077 | −€17.923 | 96,0% | 115 | €17.109 | −€31.551 | −€3.481 |
| elke geschikte naam | obscure | €2.974 | −€17.026 | 94,0% | 115 | €10.181 | −€25.285 | −€1.921 |

## Gemiddelde coëfficiënt op de rang

Positief wil zeggen: een hogere rang van die score ging in de
voorbije weken samen met een hogere rang van het rendement erna.

| mand | weken | near | ret1 | ret7 | ret28 | volx | log_size |
|---|---:|---:|---:|---:|---:|---:|---:|
| alle | 115 | 0,09 | -0,02 | -0,04 | -0,03 | 0,00 | -0,02 |
| groot | 115 | 0,14 | -0,06 | -0,08 | -0,02 | -0,01 | 0,05 |
| obscure | 115 | 0,07 | -0,01 | -0,03 | -0,03 | 0,01 | -0,03 |

Grote namen: de engine kiest namen waarvan 11,9% binnen een week +10% haalt, tegen 15,8% in de mand (0,75×). Het boek eindigt op €9.441. Obscure namen: de engine kiest namen waarvan 13,2% binnen een week +10% haalt, tegen 15,5% in de mand (0,85×). Het boek eindigt op €2.649. Geen boek haalt de lat. Er is niets aangezet.

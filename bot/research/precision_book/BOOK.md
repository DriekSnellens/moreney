# Wat de aanwijzing doet voor de PnL

Bitvavo heeft hier 428 dagelijkse namen. Het venster loopt van
2024-01-18 tot 2026-10-30. De namen zijn dezelfde als in de
precisieladder: het slot bij de high, die high alleen als de naam
over 7 dagen nog niet steeg, de grootste daling over 7 dagen, de
omzet die dag, en twee logistische modellen die alleen afgesloten
weken zien. De selectie is 1 naam en de bovenste 1%, 5%, 10% en 20%.
Mand: alle namen vanaf €10.000, groot vanaf €1.000.000, en obscure
daartussen. De eerste 26 weken trainen alleen.

Twee inzetten en vier uitstappen stonden vast voor deze draai. Het
hele boek wordt gelijk verdeeld. De andere inzet geeft een naam
maximaal 2% van zijn omzet die dag, de rest blijft cash. De uitstap
is de open na 7 dagen, een stop op −15%, of een verkoop zodra de
koers +10% of +50% heeft aangetikt. Een gat door die grens vult op
de open. Kosten zijn 0,25% taker per kant plus impact. Het boek
begint op €20.000.

De lat: minstens 30 weken, gemiddeld minstens een kwart van het
kapitaal ingezet, eind boven €20.000, eind boven het boek dat elke
geschikte naam in die mand aanhoudt met dezelfde inzet en uitstap,
en in 2024, 2025 en 2026 elk minstens acht weken en een plus.
Niets wordt aangezet.

## Live

Het boek dat aan staat is het residual-pakket uit tactic lab,
venster 2024-03-16 t/m 2026-10-03, start €20.000, één munt. De regel
is 10-daags excess met één dag overslaan, klok van 7 dagen, trail
10% en lock +12%/+6%. Dit rapport meet een andere regel op een
andere mand. De eindstand hiernaast is de gepubliceerde live-stand.

| boek | eind | PnL | terugval | rondes | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|
| live residual | €488.677 | €468.677 | 31,2% | 96 | €132.986 | €18.812 | €316.879 |

## Uitkomst

Geen regel haalt de lat. Het hoogste boek dat minstens een kwart inzet: model op +20%, alle, 1 naam, max 2% van de omzet, verkoop op +10% eindigt op €33.553 (PnL €13.553, terugval 41,2%, inzet 61,1%, 115 weken, 2024 €11.279, 2025 €12.091, 2026 −€9.816). Zonder taker en zonder impact eindigt dezelfde regel op €74.838. Geen boek is in 2024, 2025 en 2026 alle drie positief. Het live residual-boek eindigt op €488.677, PnL €468.677, terugval 31,2%, 96 rondes, 2024 €132.986, 2025 €18.812, 2026 €316.879. Dat boek blijft voor. Er is niets aangezet.

## De drie koppen uit de precisie

Dit zijn de cellen met de hoogste precisie bij minstens 100 keuzes.
Hun PnL staat erbij voor elke inzet en elke uitstap.

| score | mand | selectie | inzet | uitstap | eind | PnL | terugval | deel | weken | 2024 | 2025 | 2026 |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| omzet die dag | alle | 1 naam | hele boek | open na 7 dagen | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | alle | 1 naam | hele boek | stop op −15% | €11.839 | −€8.161 | 72,0% | 100,0% | 115 | €10.751 | −€15.953 | −€2.959 |
| omzet die dag | alle | 1 naam | hele boek | verkoop op +10% | €12.041 | −€7.959 | 66,2% | 100,0% | 115 | €11.298 | −€13.316 | −€5.941 |
| omzet die dag | alle | 1 naam | hele boek | verkoop op +50% | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | alle | 1 naam | max 2% van de omzet | open na 7 dagen | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | alle | 1 naam | max 2% van de omzet | stop op −15% | €11.839 | −€8.161 | 72,0% | 100,0% | 115 | €10.751 | −€15.953 | −€2.959 |
| omzet die dag | alle | 1 naam | max 2% van de omzet | verkoop op +10% | €12.041 | −€7.959 | 66,2% | 100,0% | 115 | €11.298 | −€13.316 | −€5.941 |
| omzet die dag | alle | 1 naam | max 2% van de omzet | verkoop op +50% | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| grootste daling over 7 dagen | obscure | 1% | hele boek | open na 7 dagen | €391 | −€19.609 | 98,9% | 100,0% | 115 | €1.236 | −€19.561 | −€1.284 |
| grootste daling over 7 dagen | obscure | 1% | hele boek | stop op −15% | €1.074 | −€18.926 | 97,0% | 100,0% | 115 | −€6.275 | −€11.862 | −€790 |
| grootste daling over 7 dagen | obscure | 1% | hele boek | verkoop op +10% | €9.648 | −€10.352 | 71,3% | 100,0% | 115 | €3.378 | −€5.889 | −€7.842 |
| grootste daling over 7 dagen | obscure | 1% | hele boek | verkoop op +50% | €1.608 | −€18.392 | 95,8% | 100,0% | 115 | €3.409 | −€17.769 | −€4.033 |
| grootste daling over 7 dagen | obscure | 1% | max 2% van de omzet | open na 7 dagen | €360 | −€19.640 | 98,3% | 82,0% | 115 | −€2.612 | −€15.869 | −€1.158 |
| grootste daling over 7 dagen | obscure | 1% | max 2% van de omzet | stop op −15% | €1.301 | −€18.699 | 95,2% | 82,4% | 115 | −€7.616 | −€10.272 | −€811 |
| grootste daling over 7 dagen | obscure | 1% | max 2% van de omzet | verkoop op +10% | €21.230 | €1.230 | 34,3% | 41,5% | 115 | €3.555 | €3.867 | −€6.193 |
| grootste daling over 7 dagen | obscure | 1% | max 2% van de omzet | verkoop op +50% | €1.526 | −€18.474 | 92,8% | 76,4% | 115 | −€2.118 | −€13.413 | −€2.942 |
| model op +20% | groot | 10% | hele boek | open na 7 dagen | €41 | −€19.959 | 99,9% | 100,0% | 115 | €25.343 | −€43.432 | −€1.870 |
| model op +20% | groot | 10% | hele boek | stop op −15% | €210 | −€19.790 | 99,5% | 100,0% | 115 | €15.098 | −€33.264 | −€1.625 |
| model op +20% | groot | 10% | hele boek | verkoop op +10% | €3.316 | −€16.684 | 93,0% | 100,0% | 115 | €16.280 | −€20.575 | −€12.389 |
| model op +20% | groot | 10% | hele boek | verkoop op +50% | €344 | −€19.656 | 99,6% | 100,0% | 115 | €41.430 | −€52.990 | −€8.096 |
| model op +20% | groot | 10% | max 2% van de omzet | open na 7 dagen | €41 | −€19.959 | 99,9% | 100,0% | 115 | €25.343 | −€43.432 | −€1.870 |
| model op +20% | groot | 10% | max 2% van de omzet | stop op −15% | €210 | −€19.790 | 99,5% | 100,0% | 115 | €15.098 | −€33.264 | −€1.625 |
| model op +20% | groot | 10% | max 2% van de omzet | verkoop op +10% | €3.316 | −€16.684 | 93,0% | 100,0% | 115 | €16.280 | −€20.575 | −€12.389 |
| model op +20% | groot | 10% | max 2% van de omzet | verkoop op +50% | €339 | −€19.661 | 99,6% | 99,9% | 115 | €40.864 | −€52.550 | −€7.975 |

## Boeken die de lat halen

Geen enkel boek haalt de lat.

## Hoogste tien eindstanden

| score | mand | selectie | inzet | uitstap | eind | PnL | terugval | deel | weken | 2024 | 2025 | 2026 |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| model op +20% | alle | 1 naam | max 2% van de omzet | verkoop op +10% | €33.553 | €13.553 | 41,2% | 61,1% | 115 | €11.279 | €12.091 | −€9.816 |
| slot bij de high | obscure | 1 naam | max 2% van de omzet | verkoop op +10% | €22.048 | €2.048 | 32,3% | 28,8% | 115 | €1.527 | −€4.384 | €4.905 |
| grootste daling over 7 dagen | obscure | 1% | max 2% van de omzet | verkoop op +10% | €21.230 | €1.230 | 34,3% | 41,5% | 115 | €3.555 | €3.867 | −€6.193 |
| slot bij de high | groot | 10% | hele boek | verkoop op +10% | €20.025 | €25 | 60,0% | 100,0% | 115 | €4.266 | −€10.819 | €6.579 |
| slot bij de high | groot | 10% | max 2% van de omzet | verkoop op +10% | €20.025 | €25 | 60,0% | 100,0% | 115 | €4.266 | −€10.819 | €6.579 |
| omzet die dag | alle | 1% | hele boek | stop op −15% | €19.461 | −€539 | 67,1% | 100,0% | 115 | €14.494 | −€12.796 | −€2.237 |
| omzet die dag | alle | 1% | max 2% van de omzet | stop op −15% | €19.461 | −€539 | 67,1% | 100,0% | 115 | €14.494 | −€12.796 | −€2.237 |
| omzet die dag | alle | 1% | hele boek | open na 7 dagen | €19.270 | −€730 | 70,5% | 100,0% | 115 | €18.164 | −€12.202 | −€6.692 |
| omzet die dag | alle | 1% | max 2% van de omzet | open na 7 dagen | €19.270 | −€730 | 70,5% | 100,0% | 115 | €18.164 | −€12.202 | −€6.692 |
| model op +10% | obscure | 5% | max 2% van de omzet | verkoop op +10% | €18.608 | −€1.392 | 63,3% | 85,1% | 115 | €9.431 | −€15.514 | €4.691 |

## open na 7 dagen, hele boek

| score | mand | selectie | inzet | uitstap | eind | PnL | terugval | deel | weken | 2024 | 2025 | 2026 |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| slot bij de high | alle | 1 naam | hele boek | open na 7 dagen | €1.407 | −€18.593 | 94,6% | 100,0% | 115 | €3.515 | −€17.555 | −€4.553 |
| slot bij de high | alle | 1% | hele boek | open na 7 dagen | €2.925 | −€17.075 | 90,7% | 100,0% | 115 | −€174 | −€16.492 | −€409 |
| slot bij de high | alle | 5% | hele boek | open na 7 dagen | €3.400 | −€16.600 | 93,3% | 100,0% | 115 | €3.955 | −€19.195 | −€1.360 |
| slot bij de high | alle | 10% | hele boek | open na 7 dagen | €3.322 | −€16.678 | 93,4% | 100,0% | 115 | €4.682 | −€20.372 | −€988 |
| slot bij de high | alle | 20% | hele boek | open na 7 dagen | €3.396 | −€16.604 | 93,5% | 100,0% | 115 | €9.139 | −€23.800 | −€1.943 |
| high, nog niet gestegen | alle | 1 naam | hele boek | open na 7 dagen | €3.196 | −€16.804 | 84,6% | 100,0% | 115 | −€8.028 | −€7.593 | −€1.183 |
| high, nog niet gestegen | alle | 1% | hele boek | open na 7 dagen | €2.257 | −€17.743 | 89,3% | 100,0% | 115 | −€7.585 | −€8.694 | −€1.464 |
| high, nog niet gestegen | alle | 5% | hele boek | open na 7 dagen | €2.082 | −€17.918 | 92,8% | 100,0% | 115 | −€4.468 | −€12.557 | −€893 |
| high, nog niet gestegen | alle | 10% | hele boek | open na 7 dagen | €2.399 | −€17.601 | 93,2% | 100,0% | 115 | −€1.705 | −€14.742 | −€1.155 |
| high, nog niet gestegen | alle | 20% | hele boek | open na 7 dagen | €2.888 | −€17.112 | 93,0% | 100,0% | 115 | €2.186 | −€18.002 | −€1.295 |
| grootste daling over 7 dagen | alle | 1 naam | hele boek | open na 7 dagen | €1 | −€19.999 | 100,0% | 100,0% | 115 | €6.187 | −€26.116 | −€70 |
| grootste daling over 7 dagen | alle | 1% | hele boek | open na 7 dagen | €158 | −€19.842 | 99,6% | 100,0% | 115 | €2.968 | −€22.011 | −€799 |
| grootste daling over 7 dagen | alle | 5% | hele boek | open na 7 dagen | €567 | −€19.433 | 98,0% | 100,0% | 115 | €639 | −€19.382 | −€690 |
| grootste daling over 7 dagen | alle | 10% | hele boek | open na 7 dagen | €925 | −€19.075 | 97,0% | 100,0% | 115 | €1.090 | −€19.254 | −€912 |
| grootste daling over 7 dagen | alle | 20% | hele boek | open na 7 dagen | €1.415 | −€18.585 | 96,5% | 100,0% | 115 | €5.637 | −€22.896 | −€1.326 |
| omzet die dag | alle | 1 naam | hele boek | open na 7 dagen | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | alle | 1% | hele boek | open na 7 dagen | €19.270 | −€730 | 70,5% | 100,0% | 115 | €18.164 | −€12.202 | −€6.692 |
| omzet die dag | alle | 5% | hele boek | open na 7 dagen | €2.728 | −€17.272 | 94,4% | 100,0% | 115 | €20.326 | −€31.890 | −€5.708 |
| omzet die dag | alle | 10% | hele boek | open na 7 dagen | €1.927 | −€18.073 | 96,3% | 100,0% | 115 | €19.290 | −€33.466 | −€3.897 |
| omzet die dag | alle | 20% | hele boek | open na 7 dagen | €1.862 | −€18.138 | 96,1% | 100,0% | 115 | €11.883 | −€27.123 | −€2.899 |
| model op +10% | alle | 1 naam | hele boek | open na 7 dagen | €0 | −€20.000 | 100,0% | 100,0% | 115 | −€8.836 | −€11.083 | −€80 |
| model op +10% | alle | 1% | hele boek | open na 7 dagen | €9 | −€19.991 | 100,0% | 100,0% | 115 | −€7.582 | −€12.158 | −€251 |
| model op +10% | alle | 5% | hele boek | open na 7 dagen | €316 | −€19.684 | 98,9% | 100,0% | 115 | €3.815 | −€22.087 | −€1.412 |
| model op +10% | alle | 10% | hele boek | open na 7 dagen | €708 | −€19.292 | 97,9% | 100,0% | 115 | €7.410 | −€24.631 | −€2.071 |
| model op +10% | alle | 20% | hele boek | open na 7 dagen | €1.334 | −€18.666 | 96,7% | 100,0% | 115 | €9.781 | −€26.342 | −€2.105 |
| model op +20% | alle | 1 naam | hele boek | open na 7 dagen | €15 | −€19.985 | 100,0% | 100,0% | 115 | −€5.051 | −€14.674 | −€260 |
| model op +20% | alle | 1% | hele boek | open na 7 dagen | €76 | −€19.924 | 99,8% | 100,0% | 115 | €1.928 | −€21.057 | −€794 |
| model op +20% | alle | 5% | hele boek | open na 7 dagen | €219 | −€19.781 | 99,4% | 100,0% | 115 | €6.009 | −€23.932 | −€1.857 |
| model op +20% | alle | 10% | hele boek | open na 7 dagen | €864 | −€19.136 | 97,7% | 100,0% | 115 | €8.493 | −€25.242 | −€2.387 |
| model op +20% | alle | 20% | hele boek | open na 7 dagen | €1.435 | −€18.565 | 96,8% | 100,0% | 115 | €10.609 | −€26.818 | −€2.356 |
| elke geschikte naam | alle | alles | hele boek | open na 7 dagen | €2.915 | −€17.085 | 94,1% | 100,0% | 115 | €10.770 | −€25.748 | −€2.107 |
| slot bij de high | groot | 1 naam | hele boek | open na 7 dagen | €6.680 | −€13.320 | 85,2% | 100,0% | 115 | €16.329 | −€20.159 | −€9.489 |
| slot bij de high | groot | 1% | hele boek | open na 7 dagen | €6.680 | −€13.320 | 85,2% | 100,0% | 115 | €16.329 | −€20.159 | −€9.489 |
| slot bij de high | groot | 5% | hele boek | open na 7 dagen | €7.617 | −€12.383 | 85,9% | 100,0% | 115 | €17.183 | −€20.732 | −€8.834 |
| slot bij de high | groot | 10% | hele boek | open na 7 dagen | €9.443 | −€10.557 | 88,0% | 100,0% | 115 | €12.804 | −€22.484 | −€877 |
| slot bij de high | groot | 20% | hele boek | open na 7 dagen | €5.823 | −€14.177 | 93,0% | 100,0% | 115 | €14.757 | −€27.043 | −€1.891 |
| high, nog niet gestegen | groot | 1 naam | hele boek | open na 7 dagen | €2.927 | −€17.073 | 91,0% | 100,0% | 115 | €3.579 | −€18.093 | −€2.559 |
| high, nog niet gestegen | groot | 1% | hele boek | open na 7 dagen | €2.927 | −€17.073 | 91,0% | 100,0% | 115 | €3.579 | −€18.093 | −€2.559 |
| high, nog niet gestegen | groot | 5% | hele boek | open na 7 dagen | €3.303 | −€16.697 | 90,7% | 100,0% | 115 | €5.227 | −€19.414 | −€2.510 |
| high, nog niet gestegen | groot | 10% | hele boek | open na 7 dagen | €2.977 | −€17.023 | 94,3% | 100,0% | 115 | €5.962 | −€20.466 | −€2.519 |
| high, nog niet gestegen | groot | 20% | hele boek | open na 7 dagen | €4.093 | −€15.907 | 92,9% | 100,0% | 115 | €6.694 | −€19.343 | −€3.258 |
| grootste daling over 7 dagen | groot | 1 naam | hele boek | open na 7 dagen | €2 | −€19.998 | 100,0% | 100,0% | 115 | €5.835 | −€25.671 | −€162 |
| grootste daling over 7 dagen | groot | 1% | hele boek | open na 7 dagen | €2 | −€19.998 | 100,0% | 100,0% | 115 | €5.835 | −€25.671 | −€162 |
| grootste daling over 7 dagen | groot | 5% | hele boek | open na 7 dagen | €4 | −€19.996 | 100,0% | 100,0% | 115 | €4.987 | −€24.656 | −€327 |
| grootste daling over 7 dagen | groot | 10% | hele boek | open na 7 dagen | €57 | −€19.943 | 99,8% | 100,0% | 115 | €3.694 | −€23.047 | −€590 |
| grootste daling over 7 dagen | groot | 20% | hele boek | open na 7 dagen | €394 | −€19.606 | 98,9% | 100,0% | 115 | €5.332 | −€23.706 | −€1.233 |
| omzet die dag | groot | 1 naam | hele boek | open na 7 dagen | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | groot | 1% | hele boek | open na 7 dagen | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | groot | 5% | hele boek | open na 7 dagen | €15.783 | −€4.217 | 71,9% | 100,0% | 115 | €14.946 | −€12.570 | −€6.594 |
| omzet die dag | groot | 10% | hele boek | open na 7 dagen | €16.343 | −€3.657 | 68,4% | 100,0% | 115 | €15.580 | −€9.332 | −€9.906 |
| omzet die dag | groot | 20% | hele boek | open na 7 dagen | €8.427 | −€11.573 | 86,9% | 100,0% | 115 | €19.561 | −€25.511 | −€5.624 |
| model op +10% | groot | 1 naam | hele boek | open na 7 dagen | €0 | −€20.000 | 100,0% | 100,0% | 115 | −€10.456 | −€9.487 | −€56 |
| model op +10% | groot | 1% | hele boek | open na 7 dagen | €0 | −€20.000 | 100,0% | 100,0% | 115 | −€10.456 | −€9.487 | −€56 |
| model op +10% | groot | 5% | hele boek | open na 7 dagen | €1 | −€19.999 | 100,0% | 100,0% | 115 | −€2.957 | −€16.946 | −€97 |
| model op +10% | groot | 10% | hele boek | open na 7 dagen | €26 | −€19.974 | 99,9% | 100,0% | 115 | €903 | −€19.974 | −€903 |
| model op +10% | groot | 20% | hele boek | open na 7 dagen | €83 | −€19.917 | 99,8% | 100,0% | 115 | €8.796 | −€27.150 | −€1.562 |
| model op +20% | groot | 1 naam | hele boek | open na 7 dagen | €2 | −€19.998 | 100,0% | 100,0% | 115 | €30.272 | −€50.113 | −€157 |
| model op +20% | groot | 1% | hele boek | open na 7 dagen | €2 | −€19.998 | 100,0% | 100,0% | 115 | €30.272 | −€50.113 | −€157 |
| model op +20% | groot | 5% | hele boek | open na 7 dagen | €5 | −€19.995 | 100,0% | 100,0% | 115 | €6.509 | −€26.212 | −€292 |
| model op +20% | groot | 10% | hele boek | open na 7 dagen | €41 | −€19.959 | 99,9% | 100,0% | 115 | €25.343 | −€43.432 | −€1.870 |
| model op +20% | groot | 20% | hele boek | open na 7 dagen | €132 | −€19.868 | 99,8% | 100,0% | 115 | €25.980 | −€44.121 | −€1.727 |
| elke geschikte naam | groot | alles | hele boek | open na 7 dagen | €2.077 | −€17.923 | 96,0% | 100,0% | 115 | €17.109 | −€31.551 | −€3.481 |
| slot bij de high | obscure | 1 naam | hele boek | open na 7 dagen | €434 | −€19.566 | 98,1% | 100,0% | 115 | −€5.049 | −€13.950 | −€567 |
| slot bij de high | obscure | 1% | hele boek | open na 7 dagen | €1.077 | −€18.923 | 96,7% | 100,0% | 115 | −€6.029 | −€12.603 | −€291 |
| slot bij de high | obscure | 5% | hele boek | open na 7 dagen | €2.090 | −€17.910 | 94,8% | 100,0% | 115 | −€1.343 | −€15.480 | −€1.087 |
| slot bij de high | obscure | 10% | hele boek | open na 7 dagen | €2.289 | −€17.711 | 94,9% | 100,0% | 115 | €1.256 | −€18.204 | −€763 |
| slot bij de high | obscure | 20% | hele boek | open na 7 dagen | €2.907 | −€17.093 | 93,8% | 100,0% | 115 | €6.336 | −€21.758 | −€1.672 |
| high, nog niet gestegen | obscure | 1 naam | hele boek | open na 7 dagen | €787 | −€19.213 | 96,1% | 100,0% | 115 | −€12.617 | −€5.941 | −€656 |
| high, nog niet gestegen | obscure | 1% | hele boek | open na 7 dagen | €1.536 | −€18.464 | 92,4% | 100,0% | 115 | −€10.976 | −€6.561 | −€927 |
| high, nog niet gestegen | obscure | 5% | hele boek | open na 7 dagen | €1.820 | −€18.180 | 93,5% | 100,0% | 115 | −€5.190 | −€12.352 | −€639 |
| high, nog niet gestegen | obscure | 10% | hele boek | open na 7 dagen | €2.124 | −€17.876 | 93,7% | 100,0% | 115 | −€1.705 | −€14.920 | −€1.251 |
| high, nog niet gestegen | obscure | 20% | hele boek | open na 7 dagen | €2.628 | −€17.372 | 93,2% | 100,0% | 115 | €1.031 | −€17.283 | −€1.120 |
| grootste daling over 7 dagen | obscure | 1 naam | hele boek | open na 7 dagen | €11 | −€19.989 | 100,0% | 100,0% | 115 | €671 | −€20.218 | −€442 |
| grootste daling over 7 dagen | obscure | 1% | hele boek | open na 7 dagen | €391 | −€19.609 | 98,9% | 100,0% | 115 | €1.236 | −€19.561 | −€1.284 |
| grootste daling over 7 dagen | obscure | 5% | hele boek | open na 7 dagen | €700 | −€19.300 | 97,4% | 100,0% | 115 | −€1.108 | −€17.512 | −€680 |
| grootste daling over 7 dagen | obscure | 10% | hele boek | open na 7 dagen | €1.098 | −€18.902 | 96,5% | 100,0% | 115 | €2.004 | −€19.894 | −€1.012 |
| grootste daling over 7 dagen | obscure | 20% | hele boek | open na 7 dagen | €1.557 | −€18.443 | 96,2% | 100,0% | 115 | €5.064 | −€22.262 | −€1.244 |
| omzet die dag | obscure | 1 naam | hele boek | open na 7 dagen | €96 | −€19.904 | 99,7% | 100,0% | 115 | −€8.848 | −€10.787 | −€269 |
| omzet die dag | obscure | 1% | hele boek | open na 7 dagen | €409 | −€19.591 | 99,0% | 100,0% | 115 | −€917 | −€17.217 | −€1.456 |
| omzet die dag | obscure | 5% | hele boek | open na 7 dagen | €1.658 | −€18.342 | 96,7% | 100,0% | 115 | €6.415 | −€22.779 | −€1.978 |
| omzet die dag | obscure | 10% | hele boek | open na 7 dagen | €2.103 | −€17.897 | 95,1% | 100,0% | 115 | €7.767 | −€22.795 | −€2.869 |
| omzet die dag | obscure | 20% | hele boek | open na 7 dagen | €2.132 | −€17.868 | 95,4% | 100,0% | 115 | €8.758 | −€24.207 | −€2.419 |
| model op +10% | obscure | 1 naam | hele boek | open na 7 dagen | €7 | −€19.993 | 100,0% | 100,0% | 115 | €15.602 | −€35.297 | −€297 |
| model op +10% | obscure | 1% | hele boek | open na 7 dagen | €50 | −€19.950 | 99,9% | 100,0% | 115 | €14.664 | −€34.071 | −€543 |
| model op +10% | obscure | 5% | hele boek | open na 7 dagen | €868 | −€19.132 | 97,7% | 100,0% | 115 | €10.182 | −€26.989 | −€2.324 |
| model op +10% | obscure | 10% | hele boek | open na 7 dagen | €1.091 | −€18.909 | 97,3% | 100,0% | 115 | €12.207 | −€28.741 | −€2.375 |
| model op +10% | obscure | 20% | hele boek | open na 7 dagen | €1.514 | −€18.486 | 96,5% | 100,0% | 115 | €11.908 | −€28.150 | −€2.245 |
| model op +20% | obscure | 1 naam | hele boek | open na 7 dagen | €2 | −€19.998 | 100,0% | 100,0% | 115 | −€7.348 | −€12.588 | −€62 |
| model op +20% | obscure | 1% | hele boek | open na 7 dagen | €27 | −€19.973 | 99,9% | 100,0% | 115 | −€395 | −€19.367 | −€211 |
| model op +20% | obscure | 5% | hele boek | open na 7 dagen | €543 | −€19.457 | 98,6% | 100,0% | 115 | €8.394 | −€25.691 | −€2.161 |
| model op +20% | obscure | 10% | hele boek | open na 7 dagen | €1.172 | −€18.828 | 97,1% | 100,0% | 115 | €7.490 | −€24.517 | −€1.802 |
| model op +20% | obscure | 20% | hele boek | open na 7 dagen | €1.959 | −€18.041 | 95,7% | 100,0% | 115 | €10.489 | −€26.278 | −€2.252 |
| elke geschikte naam | obscure | alles | hele boek | open na 7 dagen | €2.974 | −€17.026 | 94,0% | 100,0% | 115 | €10.181 | −€25.285 | −€1.921 |

## open na 7 dagen, max 2% van de omzet

| score | mand | selectie | inzet | uitstap | eind | PnL | terugval | deel | weken | 2024 | 2025 | 2026 |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| slot bij de high | alle | 1 naam | max 2% van de omzet | open na 7 dagen | €6.602 | −€13.398 | 76,9% | 65,4% | 115 | €7.682 | −€12.667 | −€8.413 |
| slot bij de high | alle | 1% | max 2% van de omzet | open na 7 dagen | €4.660 | −€15.340 | 84,8% | 81,3% | 115 | −€96 | −€15.351 | €107 |
| slot bij de high | alle | 5% | max 2% van de omzet | open na 7 dagen | €3.767 | −€16.233 | 92,1% | 94,7% | 115 | €3.125 | −€17.852 | −€1.506 |
| slot bij de high | alle | 10% | max 2% van de omzet | open na 7 dagen | €3.419 | −€16.581 | 92,9% | 97,4% | 115 | €4.058 | −€19.619 | −€1.021 |
| slot bij de high | alle | 20% | max 2% van de omzet | open na 7 dagen | €3.462 | −€16.538 | 93,2% | 98,9% | 115 | €8.832 | −€23.387 | −€1.983 |
| high, nog niet gestegen | alle | 1 naam | max 2% van de omzet | open na 7 dagen | €14.223 | −€5.777 | 41,4% | 55,2% | 115 | €1.887 | −€5.701 | −€1.964 |
| high, nog niet gestegen | alle | 1% | max 2% van de omzet | open na 7 dagen | €9.598 | −€10.402 | 59,6% | 62,4% | 115 | −€1.732 | −€6.928 | −€1.742 |
| high, nog niet gestegen | alle | 5% | max 2% van de omzet | open na 7 dagen | €2.830 | −€17.170 | 90,3% | 92,8% | 115 | −€2.395 | −€13.471 | −€1.305 |
| high, nog niet gestegen | alle | 10% | max 2% van de omzet | open na 7 dagen | €2.751 | −€17.249 | 92,2% | 97,0% | 115 | −€710 | −€15.194 | −€1.345 |
| high, nog niet gestegen | alle | 20% | max 2% van de omzet | open na 7 dagen | €2.956 | −€17.044 | 92,8% | 99,0% | 115 | €2.301 | −€18.017 | −€1.328 |
| grootste daling over 7 dagen | alle | 1 naam | max 2% van de omzet | open na 7 dagen | €1 | −€19.999 | 100,0% | 82,5% | 115 | −€1.509 | −€18.424 | −€67 |
| grootste daling over 7 dagen | alle | 1% | max 2% van de omzet | open na 7 dagen | €123 | −€19.877 | 99,5% | 86,3% | 115 | −€3.770 | −€15.492 | −€615 |
| grootste daling over 7 dagen | alle | 5% | max 2% van de omzet | open na 7 dagen | €652 | −€19.348 | 97,6% | 94,2% | 115 | €864 | −€19.415 | −€798 |
| grootste daling over 7 dagen | alle | 10% | max 2% van de omzet | open na 7 dagen | €975 | −€19.025 | 96,7% | 97,1% | 115 | €728 | −€18.789 | −€964 |
| grootste daling over 7 dagen | alle | 20% | max 2% van de omzet | open na 7 dagen | €1.413 | −€18.587 | 96,4% | 98,9% | 115 | €5.435 | −€22.699 | −€1.323 |
| omzet die dag | alle | 1 naam | max 2% van de omzet | open na 7 dagen | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | alle | 1% | max 2% van de omzet | open na 7 dagen | €19.270 | −€730 | 70,5% | 100,0% | 115 | €18.164 | −€12.202 | −€6.692 |
| omzet die dag | alle | 5% | max 2% van de omzet | open na 7 dagen | €2.728 | −€17.272 | 94,4% | 100,0% | 115 | €20.326 | −€31.890 | −€5.708 |
| omzet die dag | alle | 10% | max 2% van de omzet | open na 7 dagen | €1.927 | −€18.073 | 96,3% | 100,0% | 115 | €19.290 | −€33.466 | −€3.897 |
| omzet die dag | alle | 20% | max 2% van de omzet | open na 7 dagen | €1.862 | −€18.138 | 96,1% | 100,0% | 115 | €11.883 | −€27.123 | −€2.899 |
| model op +10% | alle | 1 naam | max 2% van de omzet | open na 7 dagen | €1 | −€19.999 | 100,0% | 82,6% | 115 | −€7.251 | −€12.553 | −€194 |
| model op +10% | alle | 1% | max 2% van de omzet | open na 7 dagen | €16 | −€19.984 | 99,9% | 87,1% | 115 | −€5.291 | −€14.259 | −€434 |
| model op +10% | alle | 5% | max 2% van de omzet | open na 7 dagen | €396 | −€19.604 | 98,7% | 94,4% | 115 | €4.921 | −€22.751 | −€1.774 |
| model op +10% | alle | 10% | max 2% van de omzet | open na 7 dagen | €740 | −€19.260 | 97,8% | 97,3% | 115 | €7.153 | −€24.246 | −€2.167 |
| model op +10% | alle | 20% | max 2% van de omzet | open na 7 dagen | €1.344 | −€18.656 | 96,7% | 99,1% | 115 | €9.570 | −€26.106 | −€2.120 |
| model op +20% | alle | 1 naam | max 2% van de omzet | open na 7 dagen | €27 | −€19.973 | 99,9% | 88,8% | 115 | −€3.339 | −€16.166 | −€469 |
| model op +20% | alle | 1% | max 2% van de omzet | open na 7 dagen | €99 | −€19.901 | 99,7% | 95,1% | 115 | €1.553 | −€20.419 | −€1.035 |
| model op +20% | alle | 5% | max 2% van de omzet | open na 7 dagen | €220 | −€19.780 | 99,4% | 99,1% | 115 | €6.019 | −€23.940 | −€1.860 |
| model op +20% | alle | 10% | max 2% van de omzet | open na 7 dagen | €874 | −€19.126 | 97,7% | 99,5% | 115 | €8.579 | −€25.290 | −€2.416 |
| model op +20% | alle | 20% | max 2% van de omzet | open na 7 dagen | €1.441 | −€18.559 | 96,8% | 99,8% | 115 | €10.714 | −€26.907 | −€2.367 |
| elke geschikte naam | alle | alles | max 2% van de omzet | open na 7 dagen | €2.915 | −€17.085 | 94,1% | 100,0% | 115 | €10.770 | −€25.748 | −€2.107 |
| slot bij de high | groot | 1 naam | max 2% van de omzet | open na 7 dagen | €6.744 | −€13.256 | 85,1% | 99,4% | 115 | €16.340 | −€20.004 | −€9.591 |
| slot bij de high | groot | 1% | max 2% van de omzet | open na 7 dagen | €6.744 | −€13.256 | 85,1% | 99,4% | 115 | €16.340 | −€20.004 | −€9.591 |
| slot bij de high | groot | 5% | max 2% van de omzet | open na 7 dagen | €7.674 | −€12.326 | 85,8% | 99,6% | 115 | €17.183 | −€20.603 | −€8.907 |
| slot bij de high | groot | 10% | max 2% van de omzet | open na 7 dagen | €9.443 | −€10.557 | 88,0% | 100,0% | 115 | €12.804 | −€22.484 | −€877 |
| slot bij de high | groot | 20% | max 2% van de omzet | open na 7 dagen | €5.823 | −€14.177 | 93,0% | 100,0% | 115 | €14.757 | −€27.043 | −€1.891 |
| high, nog niet gestegen | groot | 1 naam | max 2% van de omzet | open na 7 dagen | €2.989 | −€17.011 | 91,0% | 99,6% | 115 | €4.127 | −€18.521 | −€2.618 |
| high, nog niet gestegen | groot | 1% | max 2% van de omzet | open na 7 dagen | €2.989 | −€17.011 | 91,0% | 99,6% | 115 | €4.127 | −€18.521 | −€2.618 |
| high, nog niet gestegen | groot | 5% | max 2% van de omzet | open na 7 dagen | €3.303 | −€16.697 | 90,7% | 100,0% | 115 | €5.227 | −€19.414 | −€2.510 |
| high, nog niet gestegen | groot | 10% | max 2% van de omzet | open na 7 dagen | €2.977 | −€17.023 | 94,3% | 100,0% | 115 | €5.962 | −€20.466 | −€2.519 |
| high, nog niet gestegen | groot | 20% | max 2% van de omzet | open na 7 dagen | €4.093 | −€15.907 | 92,9% | 100,0% | 115 | €6.694 | −€19.343 | −€3.258 |
| grootste daling over 7 dagen | groot | 1 naam | max 2% van de omzet | open na 7 dagen | €2 | −€19.998 | 100,0% | 99,7% | 115 | €7.773 | −€27.597 | −€174 |
| grootste daling over 7 dagen | groot | 1% | max 2% van de omzet | open na 7 dagen | €2 | −€19.998 | 100,0% | 99,7% | 115 | €7.773 | −€27.597 | −€174 |
| grootste daling over 7 dagen | groot | 5% | max 2% van de omzet | open na 7 dagen | €4 | −€19.996 | 100,0% | 99,9% | 115 | €5.336 | −€25.001 | −€331 |
| grootste daling over 7 dagen | groot | 10% | max 2% van de omzet | open na 7 dagen | €57 | −€19.943 | 99,8% | 100,0% | 115 | €3.694 | −€23.047 | −€590 |
| grootste daling over 7 dagen | groot | 20% | max 2% van de omzet | open na 7 dagen | €394 | −€19.606 | 98,9% | 100,0% | 115 | €5.332 | −€23.706 | −€1.233 |
| omzet die dag | groot | 1 naam | max 2% van de omzet | open na 7 dagen | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | groot | 1% | max 2% van de omzet | open na 7 dagen | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | groot | 5% | max 2% van de omzet | open na 7 dagen | €15.783 | −€4.217 | 71,9% | 100,0% | 115 | €14.946 | −€12.570 | −€6.594 |
| omzet die dag | groot | 10% | max 2% van de omzet | open na 7 dagen | €16.343 | −€3.657 | 68,4% | 100,0% | 115 | €15.580 | −€9.332 | −€9.906 |
| omzet die dag | groot | 20% | max 2% van de omzet | open na 7 dagen | €8.427 | −€11.573 | 86,9% | 100,0% | 115 | €19.561 | −€25.511 | −€5.624 |
| model op +10% | groot | 1 naam | max 2% van de omzet | open na 7 dagen | €0 | −€20.000 | 100,0% | 100,0% | 115 | −€10.456 | −€9.487 | −€56 |
| model op +10% | groot | 1% | max 2% van de omzet | open na 7 dagen | €0 | −€20.000 | 100,0% | 100,0% | 115 | −€10.456 | −€9.487 | −€56 |
| model op +10% | groot | 5% | max 2% van de omzet | open na 7 dagen | €1 | −€19.999 | 100,0% | 100,0% | 115 | −€2.957 | −€16.946 | −€97 |
| model op +10% | groot | 10% | max 2% van de omzet | open na 7 dagen | €26 | −€19.974 | 99,9% | 100,0% | 115 | €903 | −€19.974 | −€903 |
| model op +10% | groot | 20% | max 2% van de omzet | open na 7 dagen | €83 | −€19.917 | 99,8% | 100,0% | 115 | €8.796 | −€27.150 | −€1.562 |
| model op +20% | groot | 1 naam | max 2% van de omzet | open na 7 dagen | €2 | −€19.998 | 100,0% | 97,8% | 115 | €27.093 | −€46.859 | −€231 |
| model op +20% | groot | 1% | max 2% van de omzet | open na 7 dagen | €2 | −€19.998 | 100,0% | 97,8% | 115 | €27.093 | −€46.859 | −€231 |
| model op +20% | groot | 5% | max 2% van de omzet | open na 7 dagen | €5 | −€19.995 | 100,0% | 99,8% | 115 | €6.337 | −€26.041 | −€291 |
| model op +20% | groot | 10% | max 2% van de omzet | open na 7 dagen | €41 | −€19.959 | 99,9% | 100,0% | 115 | €25.343 | −€43.432 | −€1.870 |
| model op +20% | groot | 20% | max 2% van de omzet | open na 7 dagen | €132 | −€19.868 | 99,8% | 100,0% | 115 | €25.980 | −€44.121 | −€1.727 |
| elke geschikte naam | groot | alles | max 2% van de omzet | open na 7 dagen | €2.077 | −€17.923 | 96,0% | 100,0% | 115 | €17.109 | −€31.551 | −€3.481 |
| slot bij de high | obscure | 1 naam | max 2% van de omzet | open na 7 dagen | €5.939 | −€14.061 | 85,0% | 48,9% | 115 | −€1.837 | −€13.346 | €1.122 |
| slot bij de high | obscure | 1% | max 2% van de omzet | open na 7 dagen | €1.737 | −€18.263 | 94,0% | 80,9% | 115 | −€6.908 | −€11.143 | −€213 |
| slot bij de high | obscure | 5% | max 2% van de omzet | open na 7 dagen | €2.284 | −€17.716 | 93,5% | 92,5% | 115 | −€2.744 | −€13.769 | −€1.203 |
| slot bij de high | obscure | 10% | max 2% van de omzet | open na 7 dagen | €2.410 | −€17.590 | 94,3% | 96,5% | 115 | €562 | −€17.345 | −€808 |
| slot bij de high | obscure | 20% | max 2% van de omzet | open na 7 dagen | €3.004 | −€16.996 | 93,2% | 98,2% | 115 | €5.845 | −€21.109 | −€1.732 |
| high, nog niet gestegen | obscure | 1 naam | max 2% van de omzet | open na 7 dagen | €13.575 | −€6.425 | 37,0% | 27,5% | 115 | −€1.680 | −€3.774 | −€971 |
| high, nog niet gestegen | obscure | 1% | max 2% van de omzet | open na 7 dagen | €7.612 | −€12.388 | 65,3% | 49,7% | 115 | −€4.411 | −€6.719 | −€1.259 |
| high, nog niet gestegen | obscure | 5% | max 2% van de omzet | open na 7 dagen | €2.714 | −€17.286 | 90,3% | 89,6% | 115 | −€3.008 | −€13.216 | −€1.062 |
| high, nog niet gestegen | obscure | 10% | max 2% van de omzet | open na 7 dagen | €2.596 | −€17.404 | 92,2% | 95,5% | 115 | −€486 | −€15.354 | −€1.564 |
| high, nog niet gestegen | obscure | 20% | max 2% van de omzet | open na 7 dagen | €2.734 | −€17.266 | 92,8% | 98,5% | 115 | €1.304 | −€17.401 | −€1.170 |
| grootste daling over 7 dagen | obscure | 1 naam | max 2% van de omzet | open na 7 dagen | €13 | −€19.987 | 99,9% | 74,8% | 115 | −€1.539 | −€17.932 | −€516 |
| grootste daling over 7 dagen | obscure | 1% | max 2% van de omzet | open na 7 dagen | €360 | −€19.640 | 98,3% | 82,0% | 115 | −€2.612 | −€15.869 | −€1.158 |
| grootste daling over 7 dagen | obscure | 5% | max 2% van de omzet | open na 7 dagen | €823 | −€19.177 | 96,6% | 92,4% | 115 | −€1.094 | −€17.276 | −€807 |
| grootste daling over 7 dagen | obscure | 10% | max 2% van de omzet | open na 7 dagen | €1.177 | −€18.823 | 96,0% | 95,8% | 115 | €1.270 | −€19.005 | −€1.089 |
| grootste daling over 7 dagen | obscure | 20% | max 2% van de omzet | open na 7 dagen | €1.585 | −€18.415 | 96,1% | 98,4% | 115 | €5.005 | −€22.152 | −€1.268 |
| omzet die dag | obscure | 1 naam | max 2% van de omzet | open na 7 dagen | €97 | −€19.903 | 99,7% | 99,9% | 115 | −€8.667 | −€10.963 | −€274 |
| omzet die dag | obscure | 1% | max 2% van de omzet | open na 7 dagen | €413 | −€19.587 | 99,0% | 99,9% | 115 | −€710 | −€17.405 | −€1.471 |
| omzet die dag | obscure | 5% | max 2% van de omzet | open na 7 dagen | €1.658 | −€18.342 | 96,7% | 100,0% | 115 | €6.415 | −€22.779 | −€1.978 |
| omzet die dag | obscure | 10% | max 2% van de omzet | open na 7 dagen | €2.103 | −€17.897 | 95,1% | 100,0% | 115 | €7.767 | −€22.795 | −€2.869 |
| omzet die dag | obscure | 20% | max 2% van de omzet | open na 7 dagen | €2.132 | −€17.868 | 95,4% | 100,0% | 115 | €8.758 | −€24.207 | −€2.419 |
| model op +10% | obscure | 1 naam | max 2% van de omzet | open na 7 dagen | €35 | −€19.965 | 99,9% | 66,6% | 115 | €7.902 | −€26.485 | −€1.382 |
| model op +10% | obscure | 1% | max 2% van de omzet | open na 7 dagen | €133 | −€19.867 | 99,6% | 76,4% | 115 | €11.539 | −€29.921 | −€1.485 |
| model op +10% | obscure | 5% | max 2% van de omzet | open na 7 dagen | €1.272 | −€18.728 | 96,3% | 89,9% | 115 | €10.710 | −€25.993 | −€3.445 |
| model op +10% | obscure | 10% | max 2% van de omzet | open na 7 dagen | €1.292 | −€18.708 | 96,6% | 94,7% | 115 | €10.731 | −€26.609 | −€2.830 |
| model op +10% | obscure | 20% | max 2% van de omzet | open na 7 dagen | €1.589 | −€18.411 | 96,2% | 98,1% | 115 | €11.635 | −€27.685 | −€2.360 |
| model op +20% | obscure | 1 naam | max 2% van de omzet | open na 7 dagen | €3 | −€19.997 | 100,0% | 91,0% | 115 | −€3.796 | −€16.111 | −€90 |
| model op +20% | obscure | 1% | max 2% van de omzet | open na 7 dagen | €27 | −€19.973 | 99,9% | 93,4% | 115 | −€1.021 | −€18.743 | −€209 |
| model op +20% | obscure | 5% | max 2% van de omzet | open na 7 dagen | €532 | −€19.468 | 98,6% | 97,2% | 115 | €7.287 | −€24.635 | −€2.119 |
| model op +20% | obscure | 10% | max 2% van de omzet | open na 7 dagen | €1.166 | −€18.834 | 97,1% | 98,2% | 115 | €7.260 | −€24.301 | −€1.793 |
| model op +20% | obscure | 20% | max 2% van de omzet | open na 7 dagen | €1.966 | −€18.034 | 95,7% | 99,3% | 115 | €10.305 | −€26.080 | −€2.259 |
| elke geschikte naam | obscure | alles | max 2% van de omzet | open na 7 dagen | €2.975 | −€17.025 | 94,0% | 100,0% | 115 | €10.186 | −€25.290 | −€1.922 |

## stop op −15%, hele boek

| score | mand | selectie | inzet | uitstap | eind | PnL | terugval | deel | weken | 2024 | 2025 | 2026 |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| slot bij de high | alle | 1 naam | hele boek | stop op −15% | €1.604 | −€18.396 | 93,2% | 100,0% | 115 | €3.331 | −€15.459 | −€6.269 |
| slot bij de high | alle | 1% | hele boek | stop op −15% | €1.766 | −€18.234 | 93,2% | 100,0% | 115 | €410 | −€16.846 | −€1.798 |
| slot bij de high | alle | 5% | hele boek | stop op −15% | €3.824 | −€16.176 | 92,3% | 100,0% | 115 | €4.856 | −€19.920 | −€1.112 |
| slot bij de high | alle | 10% | hele boek | stop op −15% | €4.119 | −€15.881 | 92,0% | 100,0% | 115 | €5.988 | −€21.225 | −€644 |
| slot bij de high | alle | 20% | hele boek | stop op −15% | €3.864 | −€16.136 | 92,3% | 100,0% | 115 | €7.911 | −€22.537 | −€1.510 |
| high, nog niet gestegen | alle | 1 naam | hele boek | stop op −15% | €3.343 | −€16.657 | 83,8% | 100,0% | 115 | −€4.663 | −€10.722 | −€1.273 |
| high, nog niet gestegen | alle | 1% | hele boek | stop op −15% | €1.770 | −€18.230 | 91,3% | 100,0% | 115 | −€5.552 | −€11.094 | −€1.584 |
| high, nog niet gestegen | alle | 5% | hele boek | stop op −15% | €2.330 | −€17.670 | 91,9% | 100,0% | 115 | −€3.723 | −€12.945 | −€1.002 |
| high, nog niet gestegen | alle | 10% | hele boek | stop op −15% | €2.711 | −€17.289 | 91,9% | 100,0% | 115 | −€1.354 | −€14.764 | −€1.170 |
| high, nog niet gestegen | alle | 20% | hele boek | stop op −15% | €3.073 | −€16.927 | 92,0% | 100,0% | 115 | €1.487 | −€17.256 | −€1.159 |
| grootste daling over 7 dagen | alle | 1 naam | hele boek | stop op −15% | €14 | −€19.986 | 100,0% | 100,0% | 115 | €4.554 | −€24.217 | −€322 |
| grootste daling over 7 dagen | alle | 1% | hele boek | stop op −15% | €698 | −€19.302 | 98,0% | 100,0% | 115 | −€669 | −€17.730 | −€903 |
| grootste daling over 7 dagen | alle | 5% | hele boek | stop op −15% | €1.112 | −€18.888 | 95,6% | 100,0% | 115 | −€1.954 | −€16.155 | −€778 |
| grootste daling over 7 dagen | alle | 10% | hele boek | stop op −15% | €1.672 | −€18.328 | 93,8% | 100,0% | 115 | −€646 | −€16.644 | −€1.038 |
| grootste daling over 7 dagen | alle | 20% | hele boek | stop op −15% | €2.209 | −€17.791 | 93,7% | 100,0% | 115 | €3.436 | −€19.806 | −€1.420 |
| omzet die dag | alle | 1 naam | hele boek | stop op −15% | €11.839 | −€8.161 | 72,0% | 100,0% | 115 | €10.751 | −€15.953 | −€2.959 |
| omzet die dag | alle | 1% | hele boek | stop op −15% | €19.461 | −€539 | 67,1% | 100,0% | 115 | €14.494 | −€12.796 | −€2.237 |
| omzet die dag | alle | 5% | hele boek | stop op −15% | €3.743 | −€16.257 | 91,0% | 100,0% | 115 | €11.798 | −€24.006 | −€4.049 |
| omzet die dag | alle | 10% | hele boek | stop op −15% | €2.581 | −€17.419 | 94,3% | 100,0% | 115 | €11.908 | −€26.041 | −€3.286 |
| omzet die dag | alle | 20% | hele boek | stop op −15% | €2.568 | −€17.432 | 94,0% | 100,0% | 115 | €7.994 | −€22.921 | −€2.505 |
| model op +10% | alle | 1 naam | hele boek | stop op −15% | €3 | −€19.997 | 100,0% | 100,0% | 115 | −€6.003 | −€13.841 | −€154 |
| model op +10% | alle | 1% | hele boek | stop op −15% | €29 | −€19.971 | 99,9% | 100,0% | 115 | −€5.897 | −€13.478 | −€597 |
| model op +10% | alle | 5% | hele boek | stop op −15% | €625 | −€19.375 | 97,6% | 100,0% | 115 | €1.928 | −€19.227 | −€2.076 |
| model op +10% | alle | 10% | hele boek | stop op −15% | €1.084 | −€18.916 | 96,6% | 100,0% | 115 | €6.318 | −€22.782 | −€2.452 |
| model op +10% | alle | 20% | hele boek | stop op −15% | €1.960 | −€18.040 | 94,6% | 100,0% | 115 | €8.327 | −€23.986 | −€2.382 |
| model op +20% | alle | 1 naam | hele boek | stop op −15% | €11 | −€19.989 | 99,9% | 100,0% | 115 | −€13.624 | −€6.109 | −€256 |
| model op +20% | alle | 1% | hele boek | stop op −15% | €60 | −€19.940 | 99,8% | 100,0% | 115 | −€8.971 | −€10.506 | −€462 |
| model op +20% | alle | 5% | hele boek | stop op −15% | €363 | −€19.637 | 98,8% | 100,0% | 115 | €2.378 | −€19.979 | −€2.037 |
| model op +20% | alle | 10% | hele boek | stop op −15% | €932 | −€19.068 | 97,3% | 100,0% | 115 | €4.628 | −€21.401 | −€2.295 |
| model op +20% | alle | 20% | hele boek | stop op −15% | €1.808 | −€18.192 | 95,3% | 100,0% | 115 | €7.191 | −€22.966 | −€2.416 |
| elke geschikte naam | alle | alles | hele boek | stop op −15% | €3.608 | −€16.392 | 92,1% | 100,0% | 115 | €9.131 | −€23.527 | −€1.996 |
| slot bij de high | groot | 1 naam | hele boek | stop op −15% | €9.682 | −€10.318 | 74,2% | 100,0% | 115 | €15.453 | −€15.059 | −€10.713 |
| slot bij de high | groot | 1% | hele boek | stop op −15% | €9.682 | −€10.318 | 74,2% | 100,0% | 115 | €15.453 | −€15.059 | −€10.713 |
| slot bij de high | groot | 5% | hele boek | stop op −15% | €6.915 | −€13.085 | 83,5% | 100,0% | 115 | €17.806 | −€20.402 | −€10.489 |
| slot bij de high | groot | 10% | hele boek | stop op −15% | €11.013 | −€8.987 | 83,4% | 100,0% | 115 | €11.856 | −€21.279 | €435 |
| slot bij de high | groot | 20% | hele boek | stop op −15% | €8.607 | −€11.393 | 89,6% | 100,0% | 115 | €15.307 | −€26.598 | −€102 |
| high, nog niet gestegen | groot | 1 naam | hele boek | stop op −15% | €4.273 | −€15.727 | 88,2% | 100,0% | 115 | €9.022 | −€21.862 | −€2.887 |
| high, nog niet gestegen | groot | 1% | hele boek | stop op −15% | €4.273 | −€15.727 | 88,2% | 100,0% | 115 | €9.022 | −€21.862 | −€2.887 |
| high, nog niet gestegen | groot | 5% | hele boek | stop op −15% | €3.886 | −€16.114 | 89,7% | 100,0% | 115 | €5.797 | −€19.161 | −€2.750 |
| high, nog niet gestegen | groot | 10% | hele boek | stop op −15% | €5.645 | −€14.355 | 89,4% | 100,0% | 115 | €7.576 | −€19.647 | −€2.285 |
| high, nog niet gestegen | groot | 20% | hele boek | stop op −15% | €5.594 | −€14.406 | 89,4% | 100,0% | 115 | €4.734 | −€16.199 | −€2.941 |
| grootste daling over 7 dagen | groot | 1 naam | hele boek | stop op −15% | €44 | −€19.956 | 99,9% | 100,0% | 115 | €7.943 | −€27.287 | −€613 |
| grootste daling over 7 dagen | groot | 1% | hele boek | stop op −15% | €44 | −€19.956 | 99,9% | 100,0% | 115 | €7.943 | −€27.287 | −€613 |
| grootste daling over 7 dagen | groot | 5% | hele boek | stop op −15% | €80 | −€19.920 | 99,7% | 100,0% | 115 | €492 | −€19.483 | −€929 |
| grootste daling over 7 dagen | groot | 10% | hele boek | stop op −15% | €382 | −€19.618 | 98,8% | 100,0% | 115 | €2.666 | −€21.041 | −€1.243 |
| grootste daling over 7 dagen | groot | 20% | hele boek | stop op −15% | €1.248 | −€18.752 | 95,6% | 100,0% | 115 | €688 | −€17.686 | −€1.754 |
| omzet die dag | groot | 1 naam | hele boek | stop op −15% | €11.839 | −€8.161 | 72,0% | 100,0% | 115 | €10.751 | −€15.953 | −€2.959 |
| omzet die dag | groot | 1% | hele boek | stop op −15% | €11.839 | −€8.161 | 72,0% | 100,0% | 115 | €10.751 | −€15.953 | −€2.959 |
| omzet die dag | groot | 5% | hele boek | stop op −15% | €16.884 | −€3.116 | 68,7% | 100,0% | 115 | €13.556 | −€11.766 | −€4.906 |
| omzet die dag | groot | 10% | hele boek | stop op −15% | €15.023 | −€4.977 | 68,4% | 100,0% | 115 | €12.845 | −€12.836 | −€4.986 |
| omzet die dag | groot | 20% | hele boek | stop op −15% | €9.178 | −€10.822 | 82,7% | 100,0% | 115 | €10.289 | −€18.022 | −€3.090 |
| model op +10% | groot | 1 naam | hele boek | stop op −15% | €4 | −€19.996 | 100,0% | 100,0% | 115 | −€9.937 | −€9.971 | −€88 |
| model op +10% | groot | 1% | hele boek | stop op −15% | €4 | −€19.996 | 100,0% | 100,0% | 115 | −€9.937 | −€9.971 | −€88 |
| model op +10% | groot | 5% | hele boek | stop op −15% | €7 | −€19.993 | 100,0% | 100,0% | 115 | −€6.866 | −€12.990 | −€137 |
| model op +10% | groot | 10% | hele boek | stop op −15% | €81 | −€19.919 | 99,7% | 100,0% | 115 | −€3.785 | −€15.486 | −€648 |
| model op +10% | groot | 20% | hele boek | stop op −15% | €179 | −€19.821 | 99,4% | 100,0% | 115 | €3.457 | −€21.611 | −€1.668 |
| model op +20% | groot | 1 naam | hele boek | stop op −15% | €14 | −€19.986 | 100,0% | 100,0% | 115 | −€6.469 | −€13.276 | −€241 |
| model op +20% | groot | 1% | hele boek | stop op −15% | €14 | −€19.986 | 100,0% | 100,0% | 115 | −€6.469 | −€13.276 | −€241 |
| model op +20% | groot | 5% | hele boek | stop op −15% | €13 | −€19.987 | 100,0% | 100,0% | 115 | −€6.007 | −€13.826 | −€155 |
| model op +20% | groot | 10% | hele boek | stop op −15% | €210 | −€19.790 | 99,5% | 100,0% | 115 | €15.098 | −€33.264 | −€1.625 |
| model op +20% | groot | 20% | hele boek | stop op −15% | €260 | −€19.740 | 99,5% | 100,0% | 115 | €16.128 | −€34.027 | −€1.842 |
| elke geschikte naam | groot | alles | hele boek | stop op −15% | €2.985 | −€17.015 | 93,6% | 100,0% | 115 | €12.140 | −€26.251 | −€2.904 |
| slot bij de high | obscure | 1 naam | hele boek | stop op −15% | €520 | −€19.480 | 97,7% | 100,0% | 115 | −€2.289 | −€16.274 | −€917 |
| slot bij de high | obscure | 1% | hele boek | stop op −15% | €1.046 | −€18.954 | 96,4% | 100,0% | 115 | −€3.524 | −€14.524 | −€906 |
| slot bij de high | obscure | 5% | hele boek | stop op −15% | €2.574 | −€17.426 | 93,7% | 100,0% | 115 | €403 | −€16.678 | −€1.151 |
| slot bij de high | obscure | 10% | hele boek | stop op −15% | €2.756 | −€17.244 | 93,7% | 100,0% | 115 | €1.878 | −€18.493 | −€629 |
| slot bij de high | obscure | 20% | hele boek | stop op −15% | €3.183 | −€16.817 | 92,9% | 100,0% | 115 | €5.744 | −€21.218 | −€1.343 |
| high, nog niet gestegen | obscure | 1 naam | hele boek | stop op −15% | €726 | −€19.274 | 96,4% | 100,0% | 115 | −€10.909 | −€7.771 | −€593 |
| high, nog niet gestegen | obscure | 1% | hele boek | stop op −15% | €1.013 | −€18.987 | 95,0% | 100,0% | 115 | −€10.300 | −€7.836 | −€851 |
| high, nog niet gestegen | obscure | 5% | hele boek | stop op −15% | €1.892 | −€18.108 | 92,9% | 100,0% | 115 | −€5.340 | −€12.075 | −€693 |
| high, nog niet gestegen | obscure | 10% | hele boek | stop op −15% | €2.094 | −€17.906 | 93,1% | 100,0% | 115 | −€2.639 | −€14.092 | −€1.176 |
| high, nog niet gestegen | obscure | 20% | hele boek | stop op −15% | €2.741 | −€17.259 | 92,4% | 100,0% | 115 | €278 | −€16.495 | −€1.042 |
| grootste daling over 7 dagen | obscure | 1 naam | hele boek | stop op −15% | €59 | −€19.941 | 99,8% | 100,0% | 115 | −€6.642 | −€12.712 | −€587 |
| grootste daling over 7 dagen | obscure | 1% | hele boek | stop op −15% | €1.074 | −€18.926 | 97,0% | 100,0% | 115 | −€6.275 | −€11.862 | −€790 |
| grootste daling over 7 dagen | obscure | 5% | hele boek | stop op −15% | €1.207 | −€18.793 | 95,1% | 100,0% | 115 | −€3.290 | −€14.783 | −€720 |
| grootste daling over 7 dagen | obscure | 10% | hele boek | stop op −15% | €1.701 | −€18.299 | 93,7% | 100,0% | 115 | −€701 | −€16.554 | −€1.044 |
| grootste daling over 7 dagen | obscure | 20% | hele boek | stop op −15% | €2.306 | −€17.694 | 93,5% | 100,0% | 115 | €3.085 | −€19.469 | −€1.311 |
| omzet die dag | obscure | 1 naam | hele boek | stop op −15% | €123 | −€19.877 | 99,4% | 100,0% | 115 | −€8.495 | −€10.903 | −€479 |
| omzet die dag | obscure | 1% | hele boek | stop op −15% | €602 | −€19.398 | 98,5% | 100,0% | 115 | €117 | −€17.882 | −€1.633 |
| omzet die dag | obscure | 5% | hele boek | stop op −15% | €2.661 | −€17.339 | 94,3% | 100,0% | 115 | €6.162 | −€21.728 | −€1.774 |
| omzet die dag | obscure | 10% | hele boek | stop op −15% | €3.141 | −€16.859 | 92,4% | 100,0% | 115 | €6.903 | −€21.049 | −€2.713 |
| omzet die dag | obscure | 20% | hele boek | stop op −15% | €2.770 | −€17.230 | 93,4% | 100,0% | 115 | €6.113 | −€20.939 | −€2.404 |
| model op +10% | obscure | 1 naam | hele boek | stop op −15% | €59 | −€19.941 | 99,9% | 100,0% | 115 | €19.873 | −€39.226 | −€588 |
| model op +10% | obscure | 1% | hele boek | stop op −15% | €138 | −€19.862 | 99,7% | 100,0% | 115 | €15.336 | −€34.253 | −€945 |
| model op +10% | obscure | 5% | hele boek | stop op −15% | €1.496 | −€18.504 | 95,9% | 100,0% | 115 | €9.258 | −€24.184 | −€3.578 |
| model op +10% | obscure | 10% | hele boek | stop op −15% | €1.628 | −€18.372 | 95,9% | 100,0% | 115 | €12.292 | −€27.482 | −€3.182 |
| model op +10% | obscure | 20% | hele boek | stop op −15% | €2.200 | −€17.800 | 94,3% | 100,0% | 115 | €11.095 | −€26.259 | −€2.636 |
| model op +20% | obscure | 1 naam | hele boek | stop op −15% | €28 | −€19.972 | 99,9% | 100,0% | 115 | −€4.978 | −€14.726 | −€268 |
| model op +20% | obscure | 1% | hele boek | stop op −15% | €103 | −€19.897 | 99,6% | 100,0% | 115 | −€605 | −€18.737 | −€555 |
| model op +20% | obscure | 5% | hele boek | stop op −15% | €820 | −€19.180 | 97,9% | 100,0% | 115 | €7.433 | −€23.825 | −€2.788 |
| model op +20% | obscure | 10% | hele boek | stop op −15% | €1.490 | −€18.510 | 95,7% | 100,0% | 115 | €6.662 | −€22.958 | −€2.215 |
| model op +20% | obscure | 20% | hele boek | stop op −15% | €2.754 | −€17.246 | 93,1% | 100,0% | 115 | €9.406 | −€23.990 | −€2.661 |
| elke geschikte naam | obscure | alles | hele boek | stop op −15% | €3.671 | −€16.329 | 92,0% | 100,0% | 115 | €9.151 | −€23.598 | −€1.882 |

## stop op −15%, max 2% van de omzet

| score | mand | selectie | inzet | uitstap | eind | PnL | terugval | deel | weken | 2024 | 2025 | 2026 |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| slot bij de high | alle | 1 naam | max 2% van de omzet | stop op −15% | €7.329 | −€12.671 | 74,6% | 65,1% | 115 | €7.675 | −€11.663 | −€8.683 |
| slot bij de high | alle | 1% | max 2% van de omzet | stop op −15% | €2.882 | −€17.118 | 88,4% | 82,7% | 115 | €488 | −€15.871 | −€1.735 |
| slot bij de high | alle | 5% | max 2% van de omzet | stop op −15% | €4.285 | −€15.715 | 90,9% | 94,5% | 115 | €4.223 | −€18.717 | −€1.221 |
| slot bij de high | alle | 10% | max 2% van de omzet | stop op −15% | €4.269 | −€15.731 | 91,3% | 97,2% | 115 | €5.386 | −€20.444 | −€673 |
| slot bij de high | alle | 20% | max 2% van de omzet | stop op −15% | €3.914 | −€16.086 | 92,0% | 98,9% | 115 | €7.616 | −€22.171 | −€1.532 |
| high, nog niet gestegen | alle | 1 naam | max 2% van de omzet | stop op −15% | €13.767 | −€6.233 | 43,4% | 55,5% | 115 | €1.980 | −€6.307 | −€1.906 |
| high, nog niet gestegen | alle | 1% | max 2% van de omzet | stop op −15% | €7.609 | −€12.391 | 65,6% | 63,5% | 115 | −€484 | −€9.477 | −€2.430 |
| high, nog niet gestegen | alle | 5% | max 2% van de omzet | stop op −15% | €3.092 | −€16.908 | 89,3% | 92,3% | 115 | −€2.285 | −€13.197 | −€1.425 |
| high, nog niet gestegen | alle | 10% | max 2% van de omzet | stop op −15% | €3.045 | −€16.955 | 90,9% | 96,9% | 115 | −€849 | −€14.773 | −€1.334 |
| high, nog niet gestegen | alle | 20% | max 2% van de omzet | stop op −15% | €3.117 | −€16.883 | 91,8% | 99,1% | 115 | €1.523 | −€17.229 | −€1.177 |
| grootste daling over 7 dagen | alle | 1 naam | max 2% van de omzet | stop op −15% | €15 | −€19.985 | 99,9% | 80,7% | 115 | −€2.637 | −€16.999 | −€348 |
| grootste daling over 7 dagen | alle | 1% | max 2% van de omzet | stop op −15% | €677 | −€19.323 | 97,3% | 85,3% | 115 | −€4.939 | −€13.549 | −€834 |
| grootste daling over 7 dagen | alle | 5% | max 2% van de omzet | stop op −15% | €1.231 | −€18.769 | 95,0% | 94,2% | 115 | −€1.668 | −€16.232 | −€869 |
| grootste daling over 7 dagen | alle | 10% | max 2% van de omzet | stop op −15% | €1.755 | −€18.245 | 93,4% | 97,1% | 115 | −€653 | −€16.498 | −€1.094 |
| grootste daling over 7 dagen | alle | 20% | max 2% van de omzet | stop op −15% | €2.216 | −€17.784 | 93,7% | 98,9% | 115 | €3.473 | −€19.832 | −€1.425 |
| omzet die dag | alle | 1 naam | max 2% van de omzet | stop op −15% | €11.839 | −€8.161 | 72,0% | 100,0% | 115 | €10.751 | −€15.953 | −€2.959 |
| omzet die dag | alle | 1% | max 2% van de omzet | stop op −15% | €19.461 | −€539 | 67,1% | 100,0% | 115 | €14.494 | −€12.796 | −€2.237 |
| omzet die dag | alle | 5% | max 2% van de omzet | stop op −15% | €3.743 | −€16.257 | 91,0% | 100,0% | 115 | €11.798 | −€24.006 | −€4.049 |
| omzet die dag | alle | 10% | max 2% van de omzet | stop op −15% | €2.581 | −€17.419 | 94,3% | 100,0% | 115 | €11.908 | −€26.041 | −€3.286 |
| omzet die dag | alle | 20% | max 2% van de omzet | stop op −15% | €2.568 | −€17.432 | 94,0% | 100,0% | 115 | €7.994 | −€22.921 | −€2.505 |
| model op +10% | alle | 1 naam | max 2% van de omzet | stop op −15% | €7 | −€19.993 | 100,0% | 81,4% | 115 | −€5.216 | −€14.380 | −€398 |
| model op +10% | alle | 1% | max 2% van de omzet | stop op −15% | €47 | −€19.953 | 99,8% | 85,6% | 115 | −€4.136 | −€14.823 | −€994 |
| model op +10% | alle | 5% | max 2% van de omzet | stop op −15% | €788 | −€19.212 | 97,2% | 94,2% | 115 | €3.313 | −€19.893 | −€2.633 |
| model op +10% | alle | 10% | max 2% van de omzet | stop op −15% | €1.135 | −€18.865 | 96,4% | 97,1% | 115 | €6.189 | −€22.484 | −€2.570 |
| model op +10% | alle | 20% | max 2% van de omzet | stop op −15% | €1.980 | −€18.020 | 94,5% | 99,0% | 115 | €8.156 | −€23.769 | −€2.408 |
| model op +20% | alle | 1 naam | max 2% van de omzet | stop op −15% | €13 | −€19.987 | 99,9% | 95,8% | 115 | −€12.964 | −€6.733 | −€290 |
| model op +20% | alle | 1% | max 2% van de omzet | stop op −15% | €63 | −€19.937 | 99,8% | 97,0% | 115 | −€9.076 | −€10.379 | −€482 |
| model op +20% | alle | 5% | max 2% van de omzet | stop op −15% | €363 | −€19.637 | 98,8% | 99,1% | 115 | €2.375 | −€19.973 | −€2.038 |
| model op +20% | alle | 10% | max 2% van de omzet | stop op −15% | €951 | −€19.049 | 97,2% | 99,5% | 115 | €4.923 | −€21.630 | −€2.342 |
| model op +20% | alle | 20% | max 2% van de omzet | stop op −15% | €1.821 | −€18.179 | 95,3% | 99,8% | 115 | €7.371 | −€23.117 | −€2.434 |
| elke geschikte naam | alle | alles | max 2% van de omzet | stop op −15% | €3.608 | −€16.392 | 92,1% | 100,0% | 115 | €9.131 | −€23.527 | −€1.996 |
| slot bij de high | groot | 1 naam | max 2% van de omzet | stop op −15% | €9.884 | −€10.116 | 74,0% | 99,0% | 115 | €15.462 | −€14.611 | −€10.967 |
| slot bij de high | groot | 1% | max 2% van de omzet | stop op −15% | €9.884 | −€10.116 | 74,0% | 99,0% | 115 | €15.462 | −€14.611 | −€10.967 |
| slot bij de high | groot | 5% | max 2% van de omzet | stop op −15% | €6.982 | −€13.018 | 83,4% | 99,4% | 115 | €17.806 | −€20.223 | −€10.601 |
| slot bij de high | groot | 10% | max 2% van de omzet | stop op −15% | €11.013 | −€8.987 | 83,4% | 100,0% | 115 | €11.856 | −€21.279 | €435 |
| slot bij de high | groot | 20% | max 2% van de omzet | stop op −15% | €8.607 | −€11.393 | 89,6% | 100,0% | 115 | €15.307 | −€26.598 | −€102 |
| high, nog niet gestegen | groot | 1 naam | max 2% van de omzet | stop op −15% | €4.308 | −€15.692 | 88,0% | 99,3% | 115 | €8.853 | −€21.632 | −€2.913 |
| high, nog niet gestegen | groot | 1% | max 2% van de omzet | stop op −15% | €4.308 | −€15.692 | 88,0% | 99,3% | 115 | €8.853 | −€21.632 | −€2.913 |
| high, nog niet gestegen | groot | 5% | max 2% van de omzet | stop op −15% | €3.886 | −€16.114 | 89,7% | 100,0% | 115 | €5.797 | −€19.161 | −€2.750 |
| high, nog niet gestegen | groot | 10% | max 2% van de omzet | stop op −15% | €5.645 | −€14.355 | 89,4% | 100,0% | 115 | €7.576 | −€19.647 | −€2.285 |
| high, nog niet gestegen | groot | 20% | max 2% van de omzet | stop op −15% | €5.594 | −€14.406 | 89,4% | 100,0% | 115 | €4.734 | −€16.199 | −€2.941 |
| grootste daling over 7 dagen | groot | 1 naam | max 2% van de omzet | stop op −15% | €46 | −€19.954 | 99,9% | 99,5% | 115 | €9.933 | −€29.233 | −€654 |
| grootste daling over 7 dagen | groot | 1% | max 2% van de omzet | stop op −15% | €46 | −€19.954 | 99,9% | 99,5% | 115 | €9.933 | −€29.233 | −€654 |
| grootste daling over 7 dagen | groot | 5% | max 2% van de omzet | stop op −15% | €82 | −€19.918 | 99,7% | 99,9% | 115 | €1.044 | −€20.010 | −€952 |
| grootste daling over 7 dagen | groot | 10% | max 2% van de omzet | stop op −15% | €382 | −€19.618 | 98,8% | 100,0% | 115 | €2.666 | −€21.041 | −€1.243 |
| grootste daling over 7 dagen | groot | 20% | max 2% van de omzet | stop op −15% | €1.248 | −€18.752 | 95,6% | 100,0% | 115 | €688 | −€17.686 | −€1.754 |
| omzet die dag | groot | 1 naam | max 2% van de omzet | stop op −15% | €11.839 | −€8.161 | 72,0% | 100,0% | 115 | €10.751 | −€15.953 | −€2.959 |
| omzet die dag | groot | 1% | max 2% van de omzet | stop op −15% | €11.839 | −€8.161 | 72,0% | 100,0% | 115 | €10.751 | −€15.953 | −€2.959 |
| omzet die dag | groot | 5% | max 2% van de omzet | stop op −15% | €16.884 | −€3.116 | 68,7% | 100,0% | 115 | €13.556 | −€11.766 | −€4.906 |
| omzet die dag | groot | 10% | max 2% van de omzet | stop op −15% | €15.023 | −€4.977 | 68,4% | 100,0% | 115 | €12.845 | −€12.836 | −€4.986 |
| omzet die dag | groot | 20% | max 2% van de omzet | stop op −15% | €9.178 | −€10.822 | 82,7% | 100,0% | 115 | €10.289 | −€18.022 | −€3.090 |
| model op +10% | groot | 1 naam | max 2% van de omzet | stop op −15% | €4 | −€19.996 | 100,0% | 100,0% | 115 | −€9.937 | −€9.971 | −€88 |
| model op +10% | groot | 1% | max 2% van de omzet | stop op −15% | €4 | −€19.996 | 100,0% | 100,0% | 115 | −€9.937 | −€9.971 | −€88 |
| model op +10% | groot | 5% | max 2% van de omzet | stop op −15% | €7 | −€19.993 | 100,0% | 100,0% | 115 | −€6.866 | −€12.990 | −€137 |
| model op +10% | groot | 10% | max 2% van de omzet | stop op −15% | €81 | −€19.919 | 99,7% | 100,0% | 115 | −€3.785 | −€15.486 | −€648 |
| model op +10% | groot | 20% | max 2% van de omzet | stop op −15% | €179 | −€19.821 | 99,4% | 100,0% | 115 | €3.457 | −€21.611 | −€1.668 |
| model op +20% | groot | 1 naam | max 2% van de omzet | stop op −15% | €14 | −€19.986 | 100,0% | 99,8% | 115 | −€6.575 | −€13.171 | −€240 |
| model op +20% | groot | 1% | max 2% van de omzet | stop op −15% | €14 | −€19.986 | 100,0% | 99,8% | 115 | −€6.575 | −€13.171 | −€240 |
| model op +20% | groot | 5% | max 2% van de omzet | stop op −15% | €13 | −€19.987 | 100,0% | 99,8% | 115 | −€6.118 | −€13.716 | −€154 |
| model op +20% | groot | 10% | max 2% van de omzet | stop op −15% | €210 | −€19.790 | 99,5% | 100,0% | 115 | €15.098 | −€33.264 | −€1.625 |
| model op +20% | groot | 20% | max 2% van de omzet | stop op −15% | €260 | −€19.740 | 99,5% | 100,0% | 115 | €16.128 | −€34.027 | −€1.842 |
| elke geschikte naam | groot | alles | max 2% van de omzet | stop op −15% | €2.985 | −€17.015 | 93,6% | 100,0% | 115 | €12.140 | −€26.251 | −€2.904 |
| slot bij de high | obscure | 1 naam | max 2% van de omzet | stop op −15% | €4.604 | −€15.396 | 88,0% | 52,2% | 115 | −€632 | −€15.053 | €290 |
| slot bij de high | obscure | 1% | max 2% van de omzet | stop op −15% | €1.763 | −€18.237 | 93,2% | 79,1% | 115 | −€5.570 | −€11.710 | −€957 |
| slot bij de high | obscure | 5% | max 2% van de omzet | stop op −15% | €2.802 | −€17.198 | 92,1% | 91,9% | 115 | −€1.163 | −€14.765 | −€1.269 |
| slot bij de high | obscure | 10% | max 2% van de omzet | stop op −15% | €2.933 | −€17.067 | 92,8% | 96,2% | 115 | €1.355 | −€17.746 | −€677 |
| slot bij de high | obscure | 20% | max 2% van de omzet | stop op −15% | €3.262 | −€16.738 | 92,3% | 98,2% | 115 | €5.346 | −€20.704 | −€1.380 |
| high, nog niet gestegen | obscure | 1 naam | max 2% van de omzet | stop op −15% | €12.986 | −€7.014 | 39,7% | 28,2% | 115 | −€1.228 | −€4.797 | −€990 |
| high, nog niet gestegen | obscure | 1% | max 2% van de omzet | stop op −15% | €6.055 | −€13.945 | 71,5% | 50,7% | 115 | −€3.484 | −€8.522 | −€1.939 |
| high, nog niet gestegen | obscure | 5% | max 2% van de omzet | stop op −15% | €2.753 | −€17.247 | 89,7% | 89,4% | 115 | −€3.746 | −€12.387 | −€1.114 |
| high, nog niet gestegen | obscure | 10% | max 2% van de omzet | stop op −15% | €2.416 | −€17.584 | 91,9% | 95,9% | 115 | −€2.172 | −€14.032 | −€1.380 |
| high, nog niet gestegen | obscure | 20% | max 2% van de omzet | stop op −15% | €2.818 | −€17.182 | 92,0% | 98,6% | 115 | €438 | −€16.545 | −€1.075 |
| grootste daling over 7 dagen | obscure | 1 naam | max 2% van de omzet | stop op −15% | €130 | −€19.870 | 99,4% | 71,8% | 115 | −€3.935 | −€14.942 | −€993 |
| grootste daling over 7 dagen | obscure | 1% | max 2% van de omzet | stop op −15% | €1.301 | −€18.699 | 95,2% | 82,4% | 115 | −€7.616 | −€10.272 | −€811 |
| grootste daling over 7 dagen | obscure | 5% | max 2% van de omzet | stop op −15% | €1.346 | −€18.654 | 94,2% | 92,6% | 115 | −€3.120 | −€14.721 | −€813 |
| grootste daling over 7 dagen | obscure | 10% | max 2% van de omzet | stop op −15% | €1.805 | −€18.195 | 93,0% | 95,9% | 115 | −€870 | −€16.212 | −€1.113 |
| grootste daling over 7 dagen | obscure | 20% | max 2% van de omzet | stop op −15% | €2.347 | −€17.653 | 93,4% | 98,5% | 115 | €3.346 | −€19.663 | −€1.336 |
| omzet die dag | obscure | 1 naam | max 2% van de omzet | stop op −15% | €125 | −€19.875 | 99,4% | 99,9% | 115 | −€8.331 | −€11.059 | −€486 |
| omzet die dag | obscure | 1% | max 2% van de omzet | stop op −15% | €608 | −€19.392 | 98,5% | 99,9% | 115 | €335 | −€18.078 | −€1.650 |
| omzet die dag | obscure | 5% | max 2% van de omzet | stop op −15% | €2.661 | −€17.339 | 94,3% | 100,0% | 115 | €6.162 | −€21.728 | −€1.774 |
| omzet die dag | obscure | 10% | max 2% van de omzet | stop op −15% | €3.141 | −€16.859 | 92,4% | 100,0% | 115 | €6.903 | −€21.049 | −€2.713 |
| omzet die dag | obscure | 20% | max 2% van de omzet | stop op −15% | €2.770 | −€17.230 | 93,4% | 100,0% | 115 | €6.113 | −€20.939 | −€2.404 |
| model op +10% | obscure | 1 naam | max 2% van de omzet | stop op −15% | €390 | −€19.610 | 98,6% | 60,8% | 115 | €7.967 | −€24.076 | −€3.500 |
| model op +10% | obscure | 1% | max 2% van de omzet | stop op −15% | €364 | −€19.636 | 98,9% | 72,3% | 115 | €11.515 | −€28.612 | −€2.539 |
| model op +10% | obscure | 5% | max 2% van de omzet | stop op −15% | €2.250 | −€17.750 | 93,5% | 88,7% | 115 | €10.277 | −€22.606 | −€5.420 |
| model op +10% | obscure | 10% | max 2% van de omzet | stop op −15% | €1.926 | −€18.074 | 94,9% | 94,2% | 115 | €11.015 | −€25.301 | −€3.787 |
| model op +10% | obscure | 20% | max 2% van de omzet | stop op −15% | €2.312 | −€17.688 | 93,9% | 97,9% | 115 | €10.805 | −€25.717 | −€2.776 |
| model op +20% | obscure | 1 naam | max 2% van de omzet | stop op −15% | €52 | −€19.948 | 99,8% | 87,7% | 115 | −€1.110 | −€18.332 | −€505 |
| model op +20% | obscure | 1% | max 2% van de omzet | stop op −15% | €115 | −€19.885 | 99,5% | 92,4% | 115 | −€1.182 | −€18.085 | −€618 |
| model op +20% | obscure | 5% | max 2% van de omzet | stop op −15% | €809 | −€19.191 | 97,8% | 97,1% | 115 | €6.454 | −€22.899 | −€2.746 |
| model op +20% | obscure | 10% | max 2% van de omzet | stop op −15% | €1.498 | −€18.502 | 95,7% | 98,2% | 115 | €6.714 | −€22.988 | −€2.228 |
| model op +20% | obscure | 20% | max 2% van de omzet | stop op −15% | €2.778 | −€17.222 | 93,0% | 99,2% | 115 | €9.354 | −€23.891 | −€2.685 |
| elke geschikte naam | obscure | alles | max 2% van de omzet | stop op −15% | €3.671 | −€16.329 | 92,0% | 100,0% | 115 | €9.155 | −€23.601 | −€1.882 |

## verkoop op +10%, hele boek

| score | mand | selectie | inzet | uitstap | eind | PnL | terugval | deel | weken | 2024 | 2025 | 2026 |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| slot bij de high | alle | 1 naam | hele boek | verkoop op +10% | €5.414 | −€14.586 | 81,3% | 100,0% | 115 | −€1.088 | −€11.653 | −€1.845 |
| slot bij de high | alle | 1% | hele boek | verkoop op +10% | €10.880 | −€9.120 | 55,6% | 100,0% | 115 | −€5.121 | −€2.180 | −€1.820 |
| slot bij de high | alle | 5% | hele boek | verkoop op +10% | €8.574 | −€11.426 | 71,7% | 100,0% | 115 | €55 | −€9.169 | −€2.312 |
| slot bij de high | alle | 10% | hele boek | verkoop op +10% | €8.620 | −€11.380 | 72,5% | 100,0% | 115 | €1.312 | −€11.584 | −€1.108 |
| slot bij de high | alle | 20% | hele boek | verkoop op +10% | €8.164 | −€11.836 | 75,8% | 100,0% | 115 | €3.156 | −€13.572 | −€1.420 |
| high, nog niet gestegen | alle | 1 naam | hele boek | verkoop op +10% | €4.674 | −€15.326 | 80,7% | 100,0% | 115 | −€6.171 | −€7.629 | −€1.526 |
| high, nog niet gestegen | alle | 1% | hele boek | verkoop op +10% | €4.436 | −€15.564 | 79,9% | 100,0% | 115 | −€6.529 | −€7.414 | −€1.621 |
| high, nog niet gestegen | alle | 5% | hele boek | verkoop op +10% | €4.769 | −€15.231 | 84,5% | 100,0% | 115 | €711 | −€14.462 | −€1.480 |
| high, nog niet gestegen | alle | 10% | hele boek | verkoop op +10% | €5.628 | −€14.372 | 83,0% | 100,0% | 115 | €2.718 | −€15.157 | −€1.932 |
| high, nog niet gestegen | alle | 20% | hele boek | verkoop op +10% | €6.943 | −€13.057 | 79,5% | 100,0% | 115 | €3.503 | −€14.933 | −€1.627 |
| grootste daling over 7 dagen | alle | 1 naam | hele boek | verkoop op +10% | €2.086 | −€17.914 | 97,3% | 100,0% | 115 | €19.660 | −€35.802 | −€1.771 |
| grootste daling over 7 dagen | alle | 1% | hele boek | verkoop op +10% | €6.301 | −€13.699 | 89,3% | 100,0% | 115 | €23.541 | −€23.770 | −€13.470 |
| grootste daling over 7 dagen | alle | 5% | hele boek | verkoop op +10% | €7.034 | −€12.966 | 78,5% | 100,0% | 115 | €9.378 | −€17.207 | −€5.138 |
| grootste daling over 7 dagen | alle | 10% | hele boek | verkoop op +10% | €9.973 | −€10.027 | 74,7% | 100,0% | 115 | €9.605 | −€17.136 | −€2.496 |
| grootste daling over 7 dagen | alle | 20% | hele boek | verkoop op +10% | €9.161 | −€10.839 | 77,7% | 100,0% | 115 | €9.599 | −€18.523 | −€1.916 |
| omzet die dag | alle | 1 naam | hele boek | verkoop op +10% | €12.041 | −€7.959 | 66,2% | 100,0% | 115 | €11.298 | −€13.316 | −€5.941 |
| omzet die dag | alle | 1% | hele boek | verkoop op +10% | €16.319 | −€3.681 | 65,1% | 100,0% | 115 | €11.730 | −€12.669 | −€2.742 |
| omzet die dag | alle | 5% | hele boek | verkoop op +10% | €13.664 | −€6.336 | 66,3% | 100,0% | 115 | €15.135 | −€14.162 | −€7.310 |
| omzet die dag | alle | 10% | hele boek | verkoop op +10% | €9.634 | −€10.366 | 75,4% | 100,0% | 115 | €12.387 | −€17.501 | −€5.252 |
| omzet die dag | alle | 20% | hele boek | verkoop op +10% | €8.719 | −€11.281 | 77,5% | 100,0% | 115 | €9.426 | −€17.076 | −€3.631 |
| model op +10% | alle | 1 naam | hele boek | verkoop op +10% | €791 | −€19.209 | 98,0% | 100,0% | 115 | €1.680 | −€16.019 | −€4.871 |
| model op +10% | alle | 1% | hele boek | verkoop op +10% | €6.811 | −€13.189 | 80,8% | 100,0% | 115 | €727 | −€10.190 | −€3.726 |
| model op +10% | alle | 5% | hele boek | verkoop op +10% | €9.647 | −€10.353 | 79,3% | 100,0% | 115 | €12.622 | −€22.764 | −€211 |
| model op +10% | alle | 10% | hele boek | verkoop op +10% | €10.184 | −€9.816 | 79,1% | 100,0% | 115 | €15.403 | −€24.884 | −€336 |
| model op +10% | alle | 20% | hele boek | verkoop op +10% | €10.596 | −€9.404 | 77,1% | 100,0% | 115 | €13.920 | −€22.445 | −€880 |
| model op +20% | alle | 1 naam | hele boek | verkoop op +10% | €15.551 | −€4.449 | 68,4% | 100,0% | 115 | €11.040 | −€4.459 | −€11.030 |
| model op +20% | alle | 1% | hele boek | verkoop op +10% | €7.578 | −€12.422 | 83,6% | 100,0% | 115 | €15.852 | −€22.605 | −€5.670 |
| model op +20% | alle | 5% | hele boek | verkoop op +10% | €9.226 | −€10.774 | 79,7% | 100,0% | 115 | €14.582 | −€23.082 | −€2.274 |
| model op +20% | alle | 10% | hele boek | verkoop op +10% | €13.207 | −€6.793 | 71,1% | 100,0% | 115 | €15.331 | −€20.525 | −€1.598 |
| model op +20% | alle | 20% | hele boek | verkoop op +10% | €11.426 | −€8.574 | 76,2% | 100,0% | 115 | €12.766 | −€20.773 | −€567 |
| elke geschikte naam | alle | alles | hele boek | verkoop op +10% | €10.311 | −€9.689 | 76,2% | 100,0% | 115 | €9.655 | −€18.673 | −€670 |
| slot bij de high | groot | 1 naam | hele boek | verkoop op +10% | €8.321 | −€11.679 | 74,2% | 100,0% | 115 | −€1.418 | −€10.452 | €190 |
| slot bij de high | groot | 1% | hele boek | verkoop op +10% | €8.321 | −€11.679 | 74,2% | 100,0% | 115 | −€1.418 | −€10.452 | €190 |
| slot bij de high | groot | 5% | hele boek | verkoop op +10% | €12.231 | −€7.769 | 68,2% | 100,0% | 115 | €920 | −€10.427 | €1.738 |
| slot bij de high | groot | 10% | hele boek | verkoop op +10% | €20.025 | €25 | 60,0% | 100,0% | 115 | €4.266 | −€10.819 | €6.579 |
| slot bij de high | groot | 20% | hele boek | verkoop op +10% | €16.611 | −€3.389 | 64,9% | 100,0% | 115 | €4.337 | −€8.754 | €1.028 |
| high, nog niet gestegen | groot | 1 naam | hele boek | verkoop op +10% | €7.516 | −€12.484 | 80,3% | 100,0% | 115 | €7.652 | −€12.494 | −€7.642 |
| high, nog niet gestegen | groot | 1% | hele boek | verkoop op +10% | €7.516 | −€12.484 | 80,3% | 100,0% | 115 | €7.652 | −€12.494 | −€7.642 |
| high, nog niet gestegen | groot | 5% | hele boek | verkoop op +10% | €5.624 | −€14.376 | 83,6% | 100,0% | 115 | €6.380 | −€15.955 | −€4.801 |
| high, nog niet gestegen | groot | 10% | hele boek | verkoop op +10% | €7.300 | −€12.700 | 81,0% | 100,0% | 115 | €8.186 | −€17.214 | −€3.672 |
| high, nog niet gestegen | groot | 20% | hele boek | verkoop op +10% | €7.220 | −€12.780 | 81,7% | 100,0% | 115 | €4.908 | −€13.599 | −€4.090 |
| grootste daling over 7 dagen | groot | 1 naam | hele boek | verkoop op +10% | €698 | −€19.302 | 99,2% | 100,0% | 115 | €63.615 | −€67.251 | −€15.666 |
| grootste daling over 7 dagen | groot | 1% | hele boek | verkoop op +10% | €698 | −€19.302 | 99,2% | 100,0% | 115 | €63.615 | −€67.251 | −€15.666 |
| grootste daling over 7 dagen | groot | 5% | hele boek | verkoop op +10% | €825 | −€19.175 | 98,7% | 100,0% | 115 | €31.496 | −€36.159 | −€14.512 |
| grootste daling over 7 dagen | groot | 10% | hele boek | verkoop op +10% | €2.900 | −€17.100 | 94,1% | 100,0% | 115 | €20.235 | −€26.368 | −€10.966 |
| grootste daling over 7 dagen | groot | 20% | hele boek | verkoop op +10% | €4.908 | −€15.092 | 88,1% | 100,0% | 115 | €13.963 | −€22.434 | −€6.620 |
| omzet die dag | groot | 1 naam | hele boek | verkoop op +10% | €12.041 | −€7.959 | 66,2% | 100,0% | 115 | €11.298 | −€13.316 | −€5.941 |
| omzet die dag | groot | 1% | hele boek | verkoop op +10% | €12.041 | −€7.959 | 66,2% | 100,0% | 115 | €11.298 | −€13.316 | −€5.941 |
| omzet die dag | groot | 5% | hele boek | verkoop op +10% | €11.427 | −€8.573 | 70,1% | 100,0% | 115 | €10.837 | −€13.915 | −€5.495 |
| omzet die dag | groot | 10% | hele boek | verkoop op +10% | €12.772 | −€7.228 | 69,0% | 100,0% | 115 | €9.139 | −€13.296 | −€3.070 |
| omzet die dag | groot | 20% | hele boek | verkoop op +10% | €14.150 | −€5.850 | 68,3% | 100,0% | 115 | €11.702 | −€15.541 | −€2.011 |
| model op +10% | groot | 1 naam | hele boek | verkoop op +10% | €1.231 | −€18.769 | 97,6% | 100,0% | 115 | €8.019 | −€23.468 | −€3.320 |
| model op +10% | groot | 1% | hele boek | verkoop op +10% | €1.231 | −€18.769 | 97,6% | 100,0% | 115 | €8.019 | −€23.468 | −€3.320 |
| model op +10% | groot | 5% | hele boek | verkoop op +10% | €1.197 | −€18.803 | 97,5% | 100,0% | 115 | €16.146 | −€30.787 | −€4.162 |
| model op +10% | groot | 10% | hele boek | verkoop op +10% | €1.591 | −€18.409 | 95,6% | 100,0% | 115 | €10.983 | −€21.590 | −€7.802 |
| model op +10% | groot | 20% | hele boek | verkoop op +10% | €2.309 | −€17.691 | 93,5% | 100,0% | 115 | €10.034 | −€20.827 | −€6.897 |
| model op +20% | groot | 1 naam | hele boek | verkoop op +10% | €4.436 | −€15.564 | 94,6% | 100,0% | 115 | €44.241 | −€45.768 | −€14.036 |
| model op +20% | groot | 1% | hele boek | verkoop op +10% | €4.436 | −€15.564 | 94,6% | 100,0% | 115 | €44.241 | −€45.768 | −€14.036 |
| model op +20% | groot | 5% | hele boek | verkoop op +10% | €4.323 | −€15.677 | 91,7% | 100,0% | 115 | €19.234 | −€20.615 | −€14.295 |
| model op +20% | groot | 10% | hele boek | verkoop op +10% | €3.316 | −€16.684 | 93,0% | 100,0% | 115 | €16.280 | −€20.575 | −€12.389 |
| model op +20% | groot | 20% | hele boek | verkoop op +10% | €5.817 | −€14.183 | 86,6% | 100,0% | 115 | €17.920 | −€25.792 | −€6.311 |
| elke geschikte naam | groot | alles | hele boek | verkoop op +10% | €9.529 | −€10.471 | 75,7% | 100,0% | 115 | €11.432 | −€17.585 | −€4.319 |
| slot bij de high | obscure | 1 naam | hele boek | verkoop op +10% | €5.829 | −€14.171 | 80,7% | 100,0% | 115 | €1.743 | −€14.739 | −€1.175 |
| slot bij de high | obscure | 1% | hele boek | verkoop op +10% | €6.728 | −€13.272 | 69,6% | 100,0% | 115 | −€5.444 | −€4.314 | −€3.514 |
| slot bij de high | obscure | 5% | hele boek | verkoop op +10% | €6.601 | −€13.399 | 78,6% | 100,0% | 115 | −€1.118 | −€10.208 | −€2.072 |
| slot bij de high | obscure | 10% | hele boek | verkoop op +10% | €6.379 | −€13.621 | 79,8% | 100,0% | 115 | €345 | −€12.649 | −€1.317 |
| slot bij de high | obscure | 20% | hele boek | verkoop op +10% | €7.366 | −€12.634 | 78,1% | 100,0% | 115 | €2.907 | −€14.284 | −€1.256 |
| high, nog niet gestegen | obscure | 1 naam | hele boek | verkoop op +10% | €1.324 | −€18.676 | 93,6% | 100,0% | 115 | −€11.236 | −€6.419 | −€1.020 |
| high, nog niet gestegen | obscure | 1% | hele boek | verkoop op +10% | €4.088 | −€15.912 | 82,6% | 100,0% | 115 | −€7.541 | −€7.422 | −€950 |
| high, nog niet gestegen | obscure | 5% | hele boek | verkoop op +10% | €4.541 | −€15.459 | 85,3% | 100,0% | 115 | €102 | −€14.584 | −€976 |
| high, nog niet gestegen | obscure | 10% | hele boek | verkoop op +10% | €5.754 | −€14.246 | 82,2% | 100,0% | 115 | €2.559 | −€14.985 | −€1.819 |
| high, nog niet gestegen | obscure | 20% | hele boek | verkoop op +10% | €7.156 | −€12.844 | 78,6% | 100,0% | 115 | €3.496 | −€15.198 | −€1.141 |
| grootste daling over 7 dagen | obscure | 1 naam | hele boek | verkoop op +10% | €2.660 | −€17.340 | 95,2% | 100,0% | 115 | €5.914 | −€19.009 | −€4.245 |
| grootste daling over 7 dagen | obscure | 1% | hele boek | verkoop op +10% | €9.648 | −€10.352 | 71,3% | 100,0% | 115 | €3.378 | −€5.889 | −€7.842 |
| grootste daling over 7 dagen | obscure | 5% | hele boek | verkoop op +10% | €6.652 | −€13.348 | 77,3% | 100,0% | 115 | €4.674 | −€13.699 | −€4.323 |
| grootste daling over 7 dagen | obscure | 10% | hele boek | verkoop op +10% | €9.518 | −€10.482 | 74,5% | 100,0% | 115 | €8.479 | −€16.575 | −€2.387 |
| grootste daling over 7 dagen | obscure | 20% | hele boek | verkoop op +10% | €8.830 | −€11.170 | 78,1% | 100,0% | 115 | €8.339 | −€18.091 | −€1.418 |
| omzet die dag | obscure | 1 naam | hele boek | verkoop op +10% | €1.088 | −€18.912 | 96,0% | 100,0% | 115 | −€4.969 | −€12.818 | −€1.125 |
| omzet die dag | obscure | 1% | hele boek | verkoop op +10% | €2.489 | −€17.511 | 93,5% | 100,0% | 115 | −€613 | −€13.154 | −€3.744 |
| omzet die dag | obscure | 5% | hele boek | verkoop op +10% | €9.181 | −€10.819 | 78,3% | 100,0% | 115 | €7.380 | −€14.270 | −€3.930 |
| omzet die dag | obscure | 10% | hele boek | verkoop op +10% | €9.731 | −€10.269 | 76,0% | 100,0% | 115 | €8.463 | −€15.668 | −€3.064 |
| omzet die dag | obscure | 20% | hele boek | verkoop op +10% | €9.589 | −€10.411 | 76,6% | 100,0% | 115 | €9.509 | −€16.744 | −€3.176 |
| model op +10% | obscure | 1 naam | hele boek | verkoop op +10% | €381 | −€19.619 | 99,0% | 100,0% | 115 | €4.836 | −€22.491 | −€1.965 |
| model op +10% | obscure | 1% | hele boek | verkoop op +10% | €8.728 | −€11.272 | 78,2% | 100,0% | 115 | €7.476 | −€15.857 | −€2.891 |
| model op +10% | obscure | 5% | hele boek | verkoop op +10% | €15.791 | −€4.209 | 74,5% | 100,0% | 115 | €13.347 | −€21.658 | €4.102 |
| model op +10% | obscure | 10% | hele boek | verkoop op +10% | €12.620 | −€7.380 | 75,4% | 100,0% | 115 | €12.493 | −€21.185 | €1.312 |
| model op +10% | obscure | 20% | hele boek | verkoop op +10% | €11.169 | −€8.831 | 76,9% | 100,0% | 115 | €13.882 | −€22.495 | −€218 |
| model op +20% | obscure | 1 naam | hele boek | verkoop op +10% | €1.711 | −€18.289 | 96,5% | 100,0% | 115 | €2.735 | −€20.553 | −€471 |
| model op +20% | obscure | 1% | hele boek | verkoop op +10% | €5.556 | −€14.444 | 93,1% | 100,0% | 115 | €18.900 | −€34.535 | €1.190 |
| model op +20% | obscure | 5% | hele boek | verkoop op +10% | €16.702 | −€3.298 | 72,3% | 100,0% | 115 | €17.461 | −€22.103 | €1.345 |
| model op +20% | obscure | 10% | hele boek | verkoop op +10% | €13.105 | −€6.895 | 74,1% | 100,0% | 115 | €13.007 | −€20.349 | €447 |
| model op +20% | obscure | 20% | hele boek | verkoop op +10% | €11.995 | −€8.005 | 76,3% | 100,0% | 115 | €12.189 | −€20.613 | €419 |
| elke geschikte naam | obscure | alles | hele boek | verkoop op +10% | €10.135 | −€9.865 | 76,9% | 100,0% | 115 | €9.311 | −€18.806 | −€370 |

## verkoop op +10%, max 2% van de omzet

| score | mand | selectie | inzet | uitstap | eind | PnL | terugval | deel | weken | 2024 | 2025 | 2026 |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| slot bij de high | alle | 1 naam | max 2% van de omzet | verkoop op +10% | €11.924 | −€8.076 | 58,2% | 64,7% | 115 | €1.645 | −€10.112 | €391 |
| slot bij de high | alle | 1% | max 2% van de omzet | verkoop op +10% | €13.528 | −€6.472 | 44,8% | 73,0% | 115 | −€3.059 | −€3.704 | €291 |
| slot bij de high | alle | 5% | max 2% van de omzet | verkoop op +10% | €9.847 | −€10.153 | 66,3% | 90,6% | 115 | €49 | −€7.724 | −€2.479 |
| slot bij de high | alle | 10% | max 2% van de omzet | verkoop op +10% | €8.833 | −€11.167 | 71,1% | 96,1% | 115 | €1.083 | −€11.091 | −€1.159 |
| slot bij de high | alle | 20% | max 2% van de omzet | verkoop op +10% | €8.281 | −€11.719 | 75,2% | 98,8% | 115 | €3.076 | −€13.349 | −€1.446 |
| high, nog niet gestegen | alle | 1 naam | max 2% van de omzet | verkoop op +10% | €13.689 | −€6.311 | 40,4% | 55,8% | 115 | €209 | −€4.997 | −€1.523 |
| high, nog niet gestegen | alle | 1% | max 2% van de omzet | verkoop op +10% | €12.550 | −€7.450 | 47,4% | 61,3% | 115 | −€2.420 | −€4.981 | −€48 |
| high, nog niet gestegen | alle | 5% | max 2% van de omzet | verkoop op +10% | €6.851 | −€13.149 | 76,6% | 86,0% | 115 | €1.862 | −€13.014 | −€1.996 |
| high, nog niet gestegen | alle | 10% | max 2% van de omzet | verkoop op +10% | €6.789 | −€13.211 | 79,2% | 94,1% | 115 | €3.583 | −€14.302 | −€2.493 |
| high, nog niet gestegen | alle | 20% | max 2% van de omzet | verkoop op +10% | €7.218 | −€12.782 | 78,6% | 98,6% | 115 | €3.608 | −€14.683 | −€1.707 |
| grootste daling over 7 dagen | alle | 1 naam | max 2% van de omzet | verkoop op +10% | €5.430 | −€14.570 | 88,1% | 53,0% | 115 | €12.352 | −€23.197 | −€3.724 |
| grootste daling over 7 dagen | alle | 1% | max 2% van de omzet | verkoop op +10% | €10.309 | −€9.691 | 75,5% | 56,7% | 115 | €14.102 | −€12.884 | −€10.909 |
| grootste daling over 7 dagen | alle | 5% | max 2% van de omzet | verkoop op +10% | €9.227 | −€10.773 | 70,2% | 80,6% | 115 | €9.027 | −€12.631 | −€7.169 |
| grootste daling over 7 dagen | alle | 10% | max 2% van de omzet | verkoop op +10% | €11.421 | −€8.579 | 69,3% | 90,2% | 115 | €8.847 | −€14.439 | −€2.987 |
| grootste daling over 7 dagen | alle | 20% | max 2% van de omzet | verkoop op +10% | €9.595 | −€10.405 | 76,3% | 97,7% | 115 | €9.269 | −€17.631 | −€2.043 |
| omzet die dag | alle | 1 naam | max 2% van de omzet | verkoop op +10% | €12.041 | −€7.959 | 66,2% | 100,0% | 115 | €11.298 | −€13.316 | −€5.941 |
| omzet die dag | alle | 1% | max 2% van de omzet | verkoop op +10% | €16.319 | −€3.681 | 65,1% | 100,0% | 115 | €11.730 | −€12.669 | −€2.742 |
| omzet die dag | alle | 5% | max 2% van de omzet | verkoop op +10% | €13.664 | −€6.336 | 66,3% | 100,0% | 115 | €15.135 | −€14.162 | −€7.310 |
| omzet die dag | alle | 10% | max 2% van de omzet | verkoop op +10% | €9.634 | −€10.366 | 75,4% | 100,0% | 115 | €12.387 | −€17.501 | −€5.252 |
| omzet die dag | alle | 20% | max 2% van de omzet | verkoop op +10% | €8.719 | −€11.281 | 77,5% | 100,0% | 115 | €9.426 | −€17.076 | −€3.631 |
| model op +10% | alle | 1 naam | max 2% van de omzet | verkoop op +10% | €3.908 | −€16.092 | 88,5% | 60,9% | 115 | €3.707 | −€3.315 | −€16.484 |
| model op +10% | alle | 1% | max 2% van de omzet | verkoop op +10% | €10.987 | −€9.013 | 68,8% | 71,2% | 115 | €5.493 | −€5.935 | −€8.571 |
| model op +10% | alle | 5% | max 2% van de omzet | verkoop op +10% | €11.308 | −€8.692 | 73,4% | 90,2% | 115 | €11.195 | −€19.718 | −€170 |
| model op +10% | alle | 10% | max 2% van de omzet | verkoop op +10% | €11.189 | −€8.811 | 75,9% | 95,1% | 115 | €13.918 | −€22.563 | −€166 |
| model op +10% | alle | 20% | max 2% van de omzet | verkoop op +10% | €10.760 | −€9.240 | 76,3% | 98,3% | 115 | €13.359 | −€21.703 | −€896 |
| model op +20% | alle | 1 naam | max 2% van de omzet | verkoop op +10% | €33.553 | €13.553 | 41,2% | 61,1% | 115 | €11.279 | €12.091 | −€9.816 |
| model op +20% | alle | 1% | max 2% van de omzet | verkoop op +10% | €8.827 | −€11.173 | 77,6% | 90,0% | 115 | €12.076 | −€16.506 | −€6.743 |
| model op +20% | alle | 5% | max 2% van de omzet | verkoop op +10% | €9.351 | −€10.649 | 78,5% | 97,6% | 115 | €13.097 | −€21.987 | −€1.760 |
| model op +20% | alle | 10% | max 2% van de omzet | verkoop op +10% | €13.645 | −€6.355 | 69,8% | 98,3% | 115 | €14.862 | −€19.964 | −€1.254 |
| model op +20% | alle | 20% | max 2% van de omzet | verkoop op +10% | €11.362 | −€8.638 | 76,2% | 99,5% | 115 | €12.580 | −€20.664 | −€554 |
| elke geschikte naam | alle | alles | max 2% van de omzet | verkoop op +10% | €10.311 | −€9.689 | 76,2% | 100,0% | 115 | €9.655 | −€18.673 | −€670 |
| slot bij de high | groot | 1 naam | max 2% van de omzet | verkoop op +10% | €8.321 | −€11.679 | 74,2% | 100,0% | 115 | −€1.418 | −€10.452 | €190 |
| slot bij de high | groot | 1% | max 2% van de omzet | verkoop op +10% | €8.321 | −€11.679 | 74,2% | 100,0% | 115 | −€1.418 | −€10.452 | €190 |
| slot bij de high | groot | 5% | max 2% van de omzet | verkoop op +10% | €12.231 | −€7.769 | 68,2% | 100,0% | 115 | €920 | −€10.427 | €1.738 |
| slot bij de high | groot | 10% | max 2% van de omzet | verkoop op +10% | €20.025 | €25 | 60,0% | 100,0% | 115 | €4.266 | −€10.819 | €6.579 |
| slot bij de high | groot | 20% | max 2% van de omzet | verkoop op +10% | €16.611 | −€3.389 | 64,9% | 100,0% | 115 | €4.337 | −€8.754 | €1.028 |
| high, nog niet gestegen | groot | 1 naam | max 2% van de omzet | verkoop op +10% | €7.248 | −€12.752 | 80,2% | 99,3% | 115 | €6.483 | −€11.894 | −€7.341 |
| high, nog niet gestegen | groot | 1% | max 2% van de omzet | verkoop op +10% | €7.248 | −€12.752 | 80,2% | 99,3% | 115 | €6.483 | −€11.894 | −€7.341 |
| high, nog niet gestegen | groot | 5% | max 2% van de omzet | verkoop op +10% | €5.624 | −€14.376 | 83,6% | 100,0% | 115 | €6.380 | −€15.955 | −€4.801 |
| high, nog niet gestegen | groot | 10% | max 2% van de omzet | verkoop op +10% | €7.300 | −€12.700 | 81,0% | 100,0% | 115 | €8.186 | −€17.214 | −€3.672 |
| high, nog niet gestegen | groot | 20% | max 2% van de omzet | verkoop op +10% | €7.220 | −€12.780 | 81,7% | 100,0% | 115 | €4.908 | −€13.599 | −€4.090 |
| grootste daling over 7 dagen | groot | 1 naam | max 2% van de omzet | verkoop op +10% | €1.330 | −€18.670 | 98,3% | 85,0% | 115 | €50.310 | −€40.211 | −€28.768 |
| grootste daling over 7 dagen | groot | 1% | max 2% van de omzet | verkoop op +10% | €1.330 | −€18.670 | 98,3% | 85,0% | 115 | €50.310 | −€40.211 | −€28.768 |
| grootste daling over 7 dagen | groot | 5% | max 2% van de omzet | verkoop op +10% | €704 | −€19.296 | 98,8% | 96,9% | 115 | €26.836 | −€33.836 | −€12.296 |
| grootste daling over 7 dagen | groot | 10% | max 2% van de omzet | verkoop op +10% | €2.900 | −€17.100 | 94,1% | 100,0% | 115 | €20.235 | −€26.368 | −€10.966 |
| grootste daling over 7 dagen | groot | 20% | max 2% van de omzet | verkoop op +10% | €4.908 | −€15.092 | 88,1% | 100,0% | 115 | €13.963 | −€22.434 | −€6.620 |
| omzet die dag | groot | 1 naam | max 2% van de omzet | verkoop op +10% | €12.041 | −€7.959 | 66,2% | 100,0% | 115 | €11.298 | −€13.316 | −€5.941 |
| omzet die dag | groot | 1% | max 2% van de omzet | verkoop op +10% | €12.041 | −€7.959 | 66,2% | 100,0% | 115 | €11.298 | −€13.316 | −€5.941 |
| omzet die dag | groot | 5% | max 2% van de omzet | verkoop op +10% | €11.427 | −€8.573 | 70,1% | 100,0% | 115 | €10.837 | −€13.915 | −€5.495 |
| omzet die dag | groot | 10% | max 2% van de omzet | verkoop op +10% | €12.772 | −€7.228 | 69,0% | 100,0% | 115 | €9.139 | −€13.296 | −€3.070 |
| omzet die dag | groot | 20% | max 2% van de omzet | verkoop op +10% | €14.150 | −€5.850 | 68,3% | 100,0% | 115 | €11.702 | −€15.541 | −€2.011 |
| model op +10% | groot | 1 naam | max 2% van de omzet | verkoop op +10% | €2.037 | −€17.963 | 95,8% | 95,9% | 115 | €14.453 | −€26.754 | −€5.661 |
| model op +10% | groot | 1% | max 2% van de omzet | verkoop op +10% | €2.037 | −€17.963 | 95,8% | 95,9% | 115 | €14.453 | −€26.754 | −€5.661 |
| model op +10% | groot | 5% | max 2% van de omzet | verkoop op +10% | €1.168 | −€18.832 | 97,5% | 99,6% | 115 | €15.053 | −€29.832 | −€4.053 |
| model op +10% | groot | 10% | max 2% van de omzet | verkoop op +10% | €1.591 | −€18.409 | 95,6% | 100,0% | 115 | €10.983 | −€21.590 | −€7.802 |
| model op +10% | groot | 20% | max 2% van de omzet | verkoop op +10% | €2.309 | −€17.691 | 93,5% | 100,0% | 115 | €10.034 | −€20.827 | −€6.897 |
| model op +20% | groot | 1 naam | max 2% van de omzet | verkoop op +10% | €10.540 | −€9.460 | 84,7% | 84,9% | 115 | €36.426 | −€10.524 | −€35.362 |
| model op +20% | groot | 1% | max 2% van de omzet | verkoop op +10% | €10.540 | −€9.460 | 84,7% | 84,9% | 115 | €36.426 | −€10.524 | −€35.362 |
| model op +20% | groot | 5% | max 2% van de omzet | verkoop op +10% | €4.444 | −€15.556 | 91,3% | 99,0% | 115 | €18.298 | −€18.724 | −€15.129 |
| model op +20% | groot | 10% | max 2% van de omzet | verkoop op +10% | €3.316 | −€16.684 | 93,0% | 100,0% | 115 | €16.280 | −€20.575 | −€12.389 |
| model op +20% | groot | 20% | max 2% van de omzet | verkoop op +10% | €5.817 | −€14.183 | 86,6% | 100,0% | 115 | €17.920 | −€25.792 | −€6.311 |
| elke geschikte naam | groot | alles | max 2% van de omzet | verkoop op +10% | €9.529 | −€10.471 | 75,7% | 100,0% | 115 | €11.432 | −€17.585 | −€4.319 |
| slot bij de high | obscure | 1 naam | max 2% van de omzet | verkoop op +10% | €22.048 | €2.048 | 32,3% | 28,8% | 115 | €1.527 | −€4.384 | €4.905 |
| slot bij de high | obscure | 1% | max 2% van de omzet | verkoop op +10% | €15.485 | −€4.515 | 33,5% | 53,4% | 115 | −€2.836 | −€264 | −€1.415 |
| slot bij de high | obscure | 5% | max 2% van de omzet | verkoop op +10% | €7.680 | −€12.320 | 72,6% | 86,8% | 115 | −€1.551 | −€8.582 | −€2.186 |
| slot bij de high | obscure | 10% | max 2% van de omzet | verkoop op +10% | €6.715 | −€13.285 | 77,8% | 94,5% | 115 | €79 | −€11.925 | −€1.439 |
| slot bij de high | obscure | 20% | max 2% van de omzet | verkoop op +10% | €7.582 | −€12.418 | 76,9% | 98,0% | 115 | €2.743 | −€13.857 | −€1.305 |
| high, nog niet gestegen | obscure | 1 naam | max 2% van de omzet | verkoop op +10% | €14.269 | −€5.731 | 34,6% | 27,1% | 115 | −€1.828 | −€3.283 | −€619 |
| high, nog niet gestegen | obscure | 1% | max 2% van de omzet | verkoop op +10% | €11.688 | −€8.312 | 52,8% | 46,6% | 115 | −€3.478 | −€5.907 | €1.074 |
| high, nog niet gestegen | obscure | 5% | max 2% van de omzet | verkoop op +10% | €7.326 | −€12.674 | 75,0% | 80,3% | 115 | €1.288 | −€12.642 | −€1.319 |
| high, nog niet gestegen | obscure | 10% | max 2% van de omzet | verkoop op +10% | €7.544 | −€12.456 | 76,1% | 90,8% | 115 | €3.940 | −€13.801 | −€2.594 |
| high, nog niet gestegen | obscure | 20% | max 2% van de omzet | verkoop op +10% | €7.655 | −€12.345 | 76,7% | 97,7% | 115 | €3.796 | −€14.890 | −€1.252 |
| grootste daling over 7 dagen | obscure | 1 naam | max 2% van de omzet | verkoop op +10% | €17.898 | −€2.102 | 44,2% | 28,8% | 115 | €4.083 | −€4.526 | −€1.658 |
| grootste daling over 7 dagen | obscure | 1% | max 2% van de omzet | verkoop op +10% | €21.230 | €1.230 | 34,3% | 41,5% | 115 | €3.555 | €3.867 | −€6.193 |
| grootste daling over 7 dagen | obscure | 5% | max 2% van de omzet | verkoop op +10% | €8.798 | −€11.202 | 68,1% | 78,1% | 115 | €4.454 | −€9.434 | −€6.222 |
| grootste daling over 7 dagen | obscure | 10% | max 2% van de omzet | verkoop op +10% | €11.224 | −€8.776 | 67,5% | 88,1% | 115 | €7.576 | −€13.582 | −€2.771 |
| grootste daling over 7 dagen | obscure | 20% | max 2% van de omzet | verkoop op +10% | €9.450 | −€10.550 | 76,1% | 97,0% | 115 | €7.831 | −€16.814 | −€1.567 |
| omzet die dag | obscure | 1 naam | max 2% van de omzet | verkoop op +10% | €1.178 | −€18.822 | 95,7% | 99,0% | 115 | −€3.612 | −€13.986 | −€1.225 |
| omzet die dag | obscure | 1% | max 2% van de omzet | verkoop op +10% | €2.485 | −€17.515 | 93,5% | 99,8% | 115 | −€645 | −€13.131 | −€3.738 |
| omzet die dag | obscure | 5% | max 2% van de omzet | verkoop op +10% | €9.181 | −€10.819 | 78,3% | 100,0% | 115 | €7.380 | −€14.270 | −€3.930 |
| omzet die dag | obscure | 10% | max 2% van de omzet | verkoop op +10% | €9.731 | −€10.269 | 76,0% | 100,0% | 115 | €8.463 | −€15.668 | −€3.064 |
| omzet die dag | obscure | 20% | max 2% van de omzet | verkoop op +10% | €9.589 | −€10.411 | 76,6% | 100,0% | 115 | €9.509 | −€16.744 | −€3.176 |
| model op +10% | obscure | 1 naam | max 2% van de omzet | verkoop op +10% | €816 | −€19.184 | 97,0% | 62,8% | 115 | €3.779 | −€18.222 | −€4.740 |
| model op +10% | obscure | 1% | max 2% van de omzet | verkoop op +10% | €14.487 | −€5.513 | 51,2% | 55,8% | 115 | €4.591 | −€3.372 | −€6.732 |
| model op +10% | obscure | 5% | max 2% van de omzet | verkoop op +10% | €18.608 | −€1.392 | 63,3% | 85,1% | 115 | €9.431 | −€15.514 | €4.691 |
| model op +10% | obscure | 10% | max 2% van de omzet | verkoop op +10% | €14.749 | −€5.251 | 68,1% | 92,4% | 115 | €9.702 | −€16.629 | €1.677 |
| model op +10% | obscure | 20% | max 2% van de omzet | verkoop op +10% | €11.724 | −€8.276 | 74,8% | 96,8% | 115 | €12.932 | −€20.971 | −€237 |
| model op +20% | obscure | 1 naam | max 2% van de omzet | verkoop op +10% | €3.122 | −€16.878 | 91,7% | 78,4% | 115 | −€163 | −€15.641 | −€1.074 |
| model op +20% | obscure | 1% | max 2% van de omzet | verkoop op +10% | €4.885 | −€15.115 | 90,9% | 85,9% | 115 | €6.925 | −€23.027 | €986 |
| model op +20% | obscure | 5% | max 2% van de omzet | verkoop op +10% | €16.807 | −€3.193 | 69,6% | 93,8% | 115 | €14.291 | −€19.514 | €2.030 |
| model op +20% | obscure | 10% | max 2% van de omzet | verkoop op +10% | €13.421 | −€6.579 | 72,9% | 97,1% | 115 | €12.022 | −€19.314 | €713 |
| model op +20% | obscure | 20% | max 2% van de omzet | verkoop op +10% | €11.918 | −€8.082 | 75,9% | 98,9% | 115 | €11.430 | −€19.941 | €429 |
| elke geschikte naam | obscure | alles | max 2% van de omzet | verkoop op +10% | €10.135 | −€9.865 | 76,9% | 100,0% | 115 | €9.312 | −€18.807 | −€370 |

## verkoop op +50%, hele boek

| score | mand | selectie | inzet | uitstap | eind | PnL | terugval | deel | weken | 2024 | 2025 | 2026 |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| slot bij de high | alle | 1 naam | hele boek | verkoop op +50% | €2.498 | −€17.502 | 90,7% | 100,0% | 115 | €4.148 | −€15.833 | −€5.817 |
| slot bij de high | alle | 1% | hele boek | verkoop op +50% | €3.222 | −€16.778 | 89,8% | 100,0% | 115 | €105 | −€16.328 | −€554 |
| slot bij de high | alle | 5% | hele boek | verkoop op +50% | €5.881 | −€14.119 | 87,8% | 100,0% | 115 | €3.074 | −€15.898 | −€1.296 |
| slot bij de high | alle | 10% | hele boek | verkoop op +50% | €5.181 | −€14.819 | 89,1% | 100,0% | 115 | €4.788 | −€18.774 | −€833 |
| slot bij de high | alle | 20% | hele boek | verkoop op +50% | €5.240 | −€14.760 | 90,5% | 100,0% | 115 | €10.905 | −€23.691 | −€1.974 |
| high, nog niet gestegen | alle | 1 naam | hele boek | verkoop op +50% | €3.196 | −€16.804 | 84,6% | 100,0% | 115 | −€8.028 | −€7.593 | −€1.183 |
| high, nog niet gestegen | alle | 1% | hele boek | verkoop op +50% | €2.257 | −€17.743 | 89,3% | 100,0% | 115 | −€7.585 | −€8.694 | −€1.464 |
| high, nog niet gestegen | alle | 5% | hele boek | verkoop op +50% | €2.729 | −€17.271 | 90,7% | 100,0% | 115 | −€4.057 | −€12.326 | −€889 |
| high, nog niet gestegen | alle | 10% | hele boek | verkoop op +50% | €3.094 | −€16.906 | 91,3% | 100,0% | 115 | −€1.462 | −€14.248 | −€1.196 |
| high, nog niet gestegen | alle | 20% | hele boek | verkoop op +50% | €4.018 | −€15.982 | 90,3% | 100,0% | 115 | €3.542 | −€18.031 | −€1.493 |
| grootste daling over 7 dagen | alle | 1 naam | hele boek | verkoop op +50% | €28 | −€19.972 | 99,9% | 100,0% | 115 | €11.036 | −€30.746 | −€262 |
| grootste daling over 7 dagen | alle | 1% | hele boek | verkoop op +50% | €499 | −€19.501 | 98,8% | 100,0% | 115 | €5.369 | −€22.143 | −€2.727 |
| grootste daling over 7 dagen | alle | 5% | hele boek | verkoop op +50% | €1.329 | −€18.671 | 95,6% | 100,0% | 115 | €3.509 | −€20.892 | −€1.288 |
| grootste daling over 7 dagen | alle | 10% | hele boek | verkoop op +50% | €2.201 | −€17.799 | 93,6% | 100,0% | 115 | €3.893 | −€20.487 | −€1.206 |
| grootste daling over 7 dagen | alle | 20% | hele boek | verkoop op +50% | €2.590 | −€17.410 | 93,7% | 100,0% | 115 | €6.696 | −€22.748 | −€1.359 |
| omzet die dag | alle | 1 naam | hele boek | verkoop op +50% | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | alle | 1% | hele boek | verkoop op +50% | €18.494 | −€1.506 | 70,5% | 100,0% | 115 | €16.577 | −€11.675 | −€6.408 |
| omzet die dag | alle | 5% | hele boek | verkoop op +50% | €4.273 | −€15.727 | 91,3% | 100,0% | 115 | €20.325 | −€29.556 | −€6.497 |
| omzet die dag | alle | 10% | hele boek | verkoop op +50% | €3.330 | −€16.670 | 93,9% | 100,0% | 115 | €18.902 | −€30.900 | −€4.671 |
| omzet die dag | alle | 20% | hele boek | verkoop op +50% | €3.119 | −€16.881 | 93,8% | 100,0% | 115 | €13.211 | −€26.779 | −€3.314 |
| model op +10% | alle | 1 naam | hele boek | verkoop op +50% | €29 | −€19.971 | 99,9% | 100,0% | 115 | −€8.168 | −€11.661 | −€142 |
| model op +10% | alle | 1% | hele boek | verkoop op +50% | €188 | −€19.812 | 99,4% | 100,0% | 115 | −€4.752 | −€14.369 | −€691 |
| model op +10% | alle | 5% | hele boek | verkoop op +50% | €1.092 | −€18.908 | 96,5% | 100,0% | 115 | €5.842 | −€22.739 | −€2.010 |
| model op +10% | alle | 10% | hele boek | verkoop op +50% | €2.393 | −€17.607 | 94,3% | 100,0% | 115 | €11.493 | −€26.200 | −€2.900 |
| model op +10% | alle | 20% | hele boek | verkoop op +50% | €3.128 | −€16.872 | 93,8% | 100,0% | 115 | €12.624 | −€27.166 | −€2.330 |
| model op +20% | alle | 1 naam | hele boek | verkoop op +50% | €424 | −€19.576 | 99,5% | 100,0% | 115 | −€1.484 | −€18.114 | €22 |
| model op +20% | alle | 1% | hele boek | verkoop op +50% | €474 | −€19.526 | 99,2% | 100,0% | 115 | €7.367 | −€25.728 | −€1.165 |
| model op +20% | alle | 5% | hele boek | verkoop op +50% | €1.285 | −€18.715 | 97,3% | 100,0% | 115 | €14.796 | −€29.273 | −€4.238 |
| model op +20% | alle | 10% | hele boek | verkoop op +50% | €2.758 | −€17.242 | 94,5% | 100,0% | 115 | €14.499 | −€27.979 | −€3.762 |
| model op +20% | alle | 20% | hele boek | verkoop op +50% | €3.413 | −€16.587 | 93,8% | 100,0% | 115 | €15.076 | −€28.613 | −€3.050 |
| elke geschikte naam | alle | alles | hele boek | verkoop op +50% | €4.691 | −€15.309 | 91,3% | 100,0% | 115 | €12.548 | −€26.051 | −€1.806 |
| slot bij de high | groot | 1 naam | hele boek | verkoop op +50% | €11.802 | −€8.198 | 76,2% | 100,0% | 115 | €20.216 | −€22.114 | −€6.300 |
| slot bij de high | groot | 1% | hele boek | verkoop op +50% | €11.802 | −€8.198 | 76,2% | 100,0% | 115 | €20.216 | −€22.114 | −€6.300 |
| slot bij de high | groot | 5% | hele boek | verkoop op +50% | €11.835 | −€8.165 | 78,9% | 100,0% | 115 | €14.440 | −€20.349 | −€2.256 |
| slot bij de high | groot | 10% | hele boek | verkoop op +50% | €11.946 | −€8.054 | 83,2% | 100,0% | 115 | €11.239 | −€20.548 | €1.255 |
| slot bij de high | groot | 20% | hele boek | verkoop op +50% | €10.475 | −€9.525 | 86,1% | 100,0% | 115 | €12.734 | −€19.965 | −€2.293 |
| high, nog niet gestegen | groot | 1 naam | hele boek | verkoop op +50% | €4.130 | −€15.870 | 89,6% | 100,0% | 115 | €8.888 | −€22.244 | −€2.514 |
| high, nog niet gestegen | groot | 1% | hele boek | verkoop op +50% | €4.130 | −€15.870 | 89,6% | 100,0% | 115 | €8.888 | −€22.244 | −€2.514 |
| high, nog niet gestegen | groot | 5% | hele boek | verkoop op +50% | €5.012 | −€14.988 | 87,3% | 100,0% | 115 | €8.201 | −€20.614 | −€2.575 |
| high, nog niet gestegen | groot | 10% | hele boek | verkoop op +50% | €2.949 | −€17.051 | 92,5% | 100,0% | 115 | €5.476 | −€19.463 | −€3.064 |
| high, nog niet gestegen | groot | 20% | hele boek | verkoop op +50% | €5.102 | −€14.898 | 89,5% | 100,0% | 115 | €6.364 | −€16.551 | −€4.711 |
| grootste daling over 7 dagen | groot | 1 naam | hele boek | verkoop op +50% | €4 | −€19.996 | 100,0% | 100,0% | 115 | €11.647 | −€31.358 | −€285 |
| grootste daling over 7 dagen | groot | 1% | hele boek | verkoop op +50% | €4 | −€19.996 | 100,0% | 100,0% | 115 | €11.647 | −€31.358 | −€285 |
| grootste daling over 7 dagen | groot | 5% | hele boek | verkoop op +50% | €8 | −€19.992 | 100,0% | 100,0% | 115 | €10.462 | −€29.942 | −€513 |
| grootste daling over 7 dagen | groot | 10% | hele boek | verkoop op +50% | €99 | −€19.901 | 99,7% | 100,0% | 115 | €6.010 | −€25.051 | −€860 |
| grootste daling over 7 dagen | groot | 20% | hele boek | verkoop op +50% | €623 | −€19.377 | 98,3% | 100,0% | 115 | €6.558 | −€24.498 | −€1.437 |
| omzet die dag | groot | 1 naam | hele boek | verkoop op +50% | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | groot | 1% | hele boek | verkoop op +50% | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | groot | 5% | hele boek | verkoop op +50% | €15.328 | −€4.672 | 71,9% | 100,0% | 115 | €13.900 | −€12.180 | −€6.393 |
| omzet die dag | groot | 10% | hele boek | verkoop op +50% | €13.055 | −€6.945 | 71,7% | 100,0% | 115 | €14.510 | −€13.611 | −€7.844 |
| omzet die dag | groot | 20% | hele boek | verkoop op +50% | €7.375 | −€12.625 | 88,0% | 100,0% | 115 | €17.785 | −€25.504 | −€4.906 |
| model op +10% | groot | 1 naam | hele boek | verkoop op +50% | €1 | −€19.999 | 100,0% | 100,0% | 115 | −€9.407 | −€10.403 | −€189 |
| model op +10% | groot | 1% | hele boek | verkoop op +50% | €1 | −€19.999 | 100,0% | 100,0% | 115 | −€9.407 | −€10.403 | −€189 |
| model op +10% | groot | 5% | hele boek | verkoop op +50% | €1 | −€19.999 | 100,0% | 100,0% | 115 | −€1.903 | −€17.847 | −€249 |
| model op +10% | groot | 10% | hele boek | verkoop op +50% | €150 | −€19.850 | 99,6% | 100,0% | 115 | €8.061 | −€25.670 | −€2.241 |
| model op +10% | groot | 20% | hele boek | verkoop op +50% | €507 | −€19.493 | 99,1% | 100,0% | 115 | €18.657 | −€32.270 | −€5.880 |
| model op +20% | groot | 1 naam | hele boek | verkoop op +50% | €8 | −€19.992 | 100,0% | 100,0% | 115 | €46.230 | −€65.364 | −€858 |
| model op +20% | groot | 1% | hele boek | verkoop op +50% | €8 | −€19.992 | 100,0% | 100,0% | 115 | €46.230 | −€65.364 | −€858 |
| model op +20% | groot | 5% | hele boek | verkoop op +50% | €16 | −€19.984 | 100,0% | 100,0% | 115 | €12.405 | −€31.329 | −€1.061 |
| model op +20% | groot | 10% | hele boek | verkoop op +50% | €344 | −€19.656 | 99,6% | 100,0% | 115 | €41.430 | −€52.990 | −€8.096 |
| model op +20% | groot | 20% | hele boek | verkoop op +50% | €1.114 | −€18.886 | 98,7% | 100,0% | 115 | €42.713 | −€54.568 | −€7.030 |
| elke geschikte naam | groot | alles | hele boek | verkoop op +50% | €3.598 | −€16.402 | 93,5% | 100,0% | 115 | €17.998 | −€30.312 | −€4.088 |
| slot bij de high | obscure | 1 naam | hele boek | verkoop op +50% | €511 | −€19.489 | 97,8% | 100,0% | 115 | −€182 | −€18.090 | −€1.217 |
| slot bij de high | obscure | 1% | hele boek | verkoop op +50% | €1.166 | −€18.834 | 96,4% | 100,0% | 115 | −€3.364 | −€14.795 | −€676 |
| slot bij de high | obscure | 5% | hele boek | verkoop op +50% | €3.735 | −€16.265 | 91,6% | 100,0% | 115 | −€73 | −€15.304 | −€888 |
| slot bij de high | obscure | 10% | hele boek | verkoop op +50% | €3.446 | −€16.554 | 92,8% | 100,0% | 115 | €3.454 | −€19.328 | −€679 |
| slot bij de high | obscure | 20% | hele boek | verkoop op +50% | €4.448 | −€15.552 | 91,3% | 100,0% | 115 | €8.803 | −€22.857 | −€1.497 |
| high, nog niet gestegen | obscure | 1 naam | hele boek | verkoop op +50% | €787 | −€19.213 | 96,1% | 100,0% | 115 | −€12.617 | −€5.941 | −€656 |
| high, nog niet gestegen | obscure | 1% | hele boek | verkoop op +50% | €1.536 | −€18.464 | 92,4% | 100,0% | 115 | −€10.976 | −€6.561 | −€927 |
| high, nog niet gestegen | obscure | 5% | hele boek | verkoop op +50% | €2.302 | −€17.698 | 91,8% | 100,0% | 115 | −€5.142 | −€11.950 | −€606 |
| high, nog niet gestegen | obscure | 10% | hele boek | verkoop op +50% | €3.072 | −€16.928 | 91,1% | 100,0% | 115 | −€1.292 | −€14.468 | −€1.168 |
| high, nog niet gestegen | obscure | 20% | hele boek | verkoop op +50% | €3.788 | −€16.212 | 90,7% | 100,0% | 115 | €2.812 | −€17.888 | −€1.136 |
| grootste daling over 7 dagen | obscure | 1 naam | hele boek | verkoop op +50% | €320 | −€19.680 | 99,2% | 100,0% | 115 | €4.409 | −€22.203 | −€1.886 |
| grootste daling over 7 dagen | obscure | 1% | hele boek | verkoop op +50% | €1.608 | −€18.392 | 95,8% | 100,0% | 115 | €3.409 | −€17.769 | −€4.033 |
| grootste daling over 7 dagen | obscure | 5% | hele boek | verkoop op +50% | €1.433 | −€18.567 | 94,7% | 100,0% | 115 | −€711 | −€16.743 | −€1.113 |
| grootste daling over 7 dagen | obscure | 10% | hele boek | verkoop op +50% | €2.646 | −€17.354 | 92,7% | 100,0% | 115 | €5.081 | −€21.206 | −€1.229 |
| grootste daling over 7 dagen | obscure | 20% | hele boek | verkoop op +50% | €2.772 | −€17.228 | 93,3% | 100,0% | 115 | €6.064 | −€22.024 | −€1.268 |
| omzet die dag | obscure | 1 naam | hele boek | verkoop op +50% | €178 | −€19.822 | 99,5% | 100,0% | 115 | −€8.848 | −€10.787 | −€187 |
| omzet die dag | obscure | 1% | hele boek | verkoop op +50% | €764 | −€19.236 | 98,2% | 100,0% | 115 | −€575 | −€17.177 | −€1.484 |
| omzet die dag | obscure | 5% | hele boek | verkoop op +50% | €3.050 | −€16.950 | 94,5% | 100,0% | 115 | €10.389 | −€24.916 | −€2.424 |
| omzet die dag | obscure | 10% | hele boek | verkoop op +50% | €3.585 | −€16.415 | 92,5% | 100,0% | 115 | €9.466 | −€23.154 | −€2.727 |
| omzet die dag | obscure | 20% | hele boek | verkoop op +50% | €3.526 | −€16.474 | 92,7% | 100,0% | 115 | €10.031 | −€24.089 | −€2.416 |
| model op +10% | obscure | 1 naam | hele boek | verkoop op +50% | €65 | −€19.935 | 99,9% | 100,0% | 115 | €22.535 | −€41.694 | −€775 |
| model op +10% | obscure | 1% | hele boek | verkoop op +50% | €741 | −€19.259 | 99,0% | 100,0% | 115 | €19.046 | −€36.545 | −€1.760 |
| model op +10% | obscure | 5% | hele boek | verkoop op +50% | €3.305 | −€16.695 | 92,6% | 100,0% | 115 | €11.390 | −€25.317 | −€2.768 |
| model op +10% | obscure | 10% | hele boek | verkoop op +50% | €3.540 | −€16.460 | 93,0% | 100,0% | 115 | €13.345 | −€27.439 | −€2.365 |
| model op +10% | obscure | 20% | hele boek | verkoop op +50% | €3.560 | −€16.440 | 93,4% | 100,0% | 115 | €15.767 | −€30.035 | −€2.172 |
| model op +20% | obscure | 1 naam | hele boek | verkoop op +50% | €173 | −€19.827 | 99,8% | 100,0% | 115 | −€766 | −€18.914 | −€148 |
| model op +20% | obscure | 1% | hele boek | verkoop op +50% | €320 | −€19.680 | 99,4% | 100,0% | 115 | €13.776 | −€32.869 | −€586 |
| model op +20% | obscure | 5% | hele boek | verkoop op +50% | €3.268 | −€16.732 | 93,9% | 100,0% | 115 | €15.397 | −€29.191 | −€2.938 |
| model op +20% | obscure | 10% | hele boek | verkoop op +50% | €3.548 | −€16.452 | 93,5% | 100,0% | 115 | €12.848 | −€27.198 | −€2.102 |
| model op +20% | obscure | 20% | hele boek | verkoop op +50% | €4.256 | −€15.744 | 92,3% | 100,0% | 115 | €14.601 | −€27.987 | −€2.358 |
| elke geschikte naam | obscure | alles | hele boek | verkoop op +50% | €4.817 | −€15.183 | 91,2% | 100,0% | 115 | €12.291 | −€25.920 | −€1.554 |

## verkoop op +50%, max 2% van de omzet

| score | mand | selectie | inzet | uitstap | eind | PnL | terugval | deel | weken | 2024 | 2025 | 2026 |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| slot bij de high | alle | 1 naam | max 2% van de omzet | verkoop op +50% | €9.281 | −€10.719 | 67,8% | 63,8% | 115 | €8.417 | −€12.698 | −€6.437 |
| slot bij de high | alle | 1% | max 2% van de omzet | verkoop op +50% | €4.946 | −€15.054 | 84,2% | 80,9% | 115 | €206 | −€15.343 | €83 |
| slot bij de high | alle | 5% | max 2% van de omzet | verkoop op +50% | €6.818 | −€13.182 | 84,8% | 93,1% | 115 | €2.268 | −€14.051 | −€1.399 |
| slot bij de high | alle | 10% | max 2% van de omzet | verkoop op +50% | €5.377 | −€14.623 | 88,1% | 97,0% | 115 | €4.169 | −€17.919 | −€874 |
| slot bij de high | alle | 20% | max 2% van de omzet | verkoop op +50% | €5.373 | −€14.627 | 89,9% | 98,7% | 115 | €10.501 | −€23.099 | −€2.030 |
| high, nog niet gestegen | alle | 1 naam | max 2% van de omzet | verkoop op +50% | €14.223 | −€5.777 | 41,4% | 55,2% | 115 | €1.887 | −€5.701 | −€1.964 |
| high, nog niet gestegen | alle | 1% | max 2% van de omzet | verkoop op +50% | €9.598 | −€10.402 | 59,6% | 62,4% | 115 | −€1.732 | −€6.928 | −€1.742 |
| high, nog niet gestegen | alle | 5% | max 2% van de omzet | verkoop op +50% | €3.728 | −€16.272 | 87,4% | 92,0% | 115 | −€1.941 | −€13.006 | −€1.325 |
| high, nog niet gestegen | alle | 10% | max 2% van de omzet | verkoop op +50% | €3.576 | −€16.424 | 89,9% | 96,7% | 115 | −€452 | −€14.558 | −€1.413 |
| high, nog niet gestegen | alle | 20% | max 2% van de omzet | verkoop op +50% | €4.147 | −€15.853 | 89,9% | 98,9% | 115 | €3.708 | −€18.014 | −€1.547 |
| grootste daling over 7 dagen | alle | 1 naam | max 2% van de omzet | verkoop op +50% | €15 | −€19.985 | 99,9% | 81,6% | 115 | −€940 | −€18.901 | −€144 |
| grootste daling over 7 dagen | alle | 1% | max 2% van de omzet | verkoop op +50% | €335 | −€19.665 | 98,6% | 84,5% | 115 | −€3.326 | −€14.708 | −€1.630 |
| grootste daling over 7 dagen | alle | 5% | max 2% van de omzet | verkoop op +50% | €1.578 | −€18.422 | 94,5% | 92,9% | 115 | €3.648 | −€20.519 | −€1.551 |
| grootste daling over 7 dagen | alle | 10% | max 2% van de omzet | verkoop op +50% | €2.345 | −€17.655 | 92,8% | 96,5% | 115 | €3.261 | −€19.623 | −€1.293 |
| grootste daling over 7 dagen | alle | 20% | max 2% van de omzet | verkoop op +50% | €2.596 | −€17.404 | 93,6% | 98,8% | 115 | €6.508 | −€22.550 | −€1.362 |
| omzet die dag | alle | 1 naam | max 2% van de omzet | verkoop op +50% | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | alle | 1% | max 2% van de omzet | verkoop op +50% | €18.494 | −€1.506 | 70,5% | 100,0% | 115 | €16.577 | −€11.675 | −€6.408 |
| omzet die dag | alle | 5% | max 2% van de omzet | verkoop op +50% | €4.273 | −€15.727 | 91,3% | 100,0% | 115 | €20.325 | −€29.556 | −€6.497 |
| omzet die dag | alle | 10% | max 2% van de omzet | verkoop op +50% | €3.330 | −€16.670 | 93,9% | 100,0% | 115 | €18.902 | −€30.900 | −€4.671 |
| omzet die dag | alle | 20% | max 2% van de omzet | verkoop op +50% | €3.119 | −€16.881 | 93,8% | 100,0% | 115 | €13.211 | −€26.779 | −€3.314 |
| model op +10% | alle | 1 naam | max 2% van de omzet | verkoop op +50% | €55 | −€19.945 | 99,8% | 82,2% | 115 | −€7.094 | −€12.578 | −€274 |
| model op +10% | alle | 1% | max 2% van de omzet | verkoop op +50% | €321 | −€19.679 | 98,8% | 84,5% | 115 | −€2.144 | −€16.338 | −€1.196 |
| model op +10% | alle | 5% | max 2% van de omzet | verkoop op +50% | €1.368 | −€18.632 | 95,8% | 93,6% | 115 | €7.637 | −€23.731 | −€2.538 |
| model op +10% | alle | 10% | max 2% van de omzet | verkoop op +50% | €2.458 | −€17.542 | 93,8% | 96,8% | 115 | €10.368 | −€24.926 | −€2.984 |
| model op +10% | alle | 20% | max 2% van de omzet | verkoop op +50% | €3.170 | −€16.830 | 93,6% | 98,9% | 115 | €12.366 | −€26.834 | −€2.362 |
| model op +20% | alle | 1 naam | max 2% van de omzet | verkoop op +50% | €622 | −€19.378 | 99,2% | 89,6% | 115 | €1.191 | −€20.597 | €28 |
| model op +20% | alle | 1% | max 2% van de omzet | verkoop op +50% | €623 | −€19.377 | 98,9% | 94,5% | 115 | €6.837 | −€24.670 | −€1.545 |
| model op +20% | alle | 5% | max 2% van de omzet | verkoop op +50% | €1.264 | −€18.736 | 97,2% | 98,7% | 115 | €13.182 | −€27.800 | −€4.119 |
| model op +20% | alle | 10% | max 2% van de omzet | verkoop op +50% | €2.717 | −€17.283 | 94,5% | 99,3% | 115 | €14.060 | −€27.641 | −€3.702 |
| model op +20% | alle | 20% | max 2% van de omzet | verkoop op +50% | €3.429 | −€16.571 | 93,8% | 99,7% | 115 | €15.132 | −€28.638 | −€3.065 |
| elke geschikte naam | alle | alles | max 2% van de omzet | verkoop op +50% | €4.691 | −€15.309 | 91,3% | 100,0% | 115 | €12.548 | −€26.051 | −€1.806 |
| slot bij de high | groot | 1 naam | max 2% van de omzet | verkoop op +50% | €12.807 | −€7.193 | 74,2% | 98,6% | 115 | €20.237 | −€21.766 | −€5.664 |
| slot bij de high | groot | 1% | max 2% van de omzet | verkoop op +50% | €12.807 | −€7.193 | 74,2% | 98,6% | 115 | €20.237 | −€21.766 | −€5.664 |
| slot bij de high | groot | 5% | max 2% van de omzet | verkoop op +50% | €11.854 | −€8.146 | 78,9% | 99,9% | 115 | €14.440 | −€20.325 | −€2.261 |
| slot bij de high | groot | 10% | max 2% van de omzet | verkoop op +50% | €11.946 | −€8.054 | 83,2% | 100,0% | 115 | €11.239 | −€20.548 | €1.255 |
| slot bij de high | groot | 20% | max 2% van de omzet | verkoop op +50% | €10.475 | −€9.525 | 86,1% | 100,0% | 115 | €12.734 | −€19.965 | −€2.293 |
| high, nog niet gestegen | groot | 1 naam | max 2% van de omzet | verkoop op +50% | €4.201 | −€15.799 | 89,5% | 99,3% | 115 | €8.980 | −€22.218 | −€2.561 |
| high, nog niet gestegen | groot | 1% | max 2% van de omzet | verkoop op +50% | €4.201 | −€15.799 | 89,5% | 99,3% | 115 | €8.980 | −€22.218 | −€2.561 |
| high, nog niet gestegen | groot | 5% | max 2% van de omzet | verkoop op +50% | €5.012 | −€14.988 | 87,3% | 100,0% | 115 | €8.201 | −€20.614 | −€2.575 |
| high, nog niet gestegen | groot | 10% | max 2% van de omzet | verkoop op +50% | €2.949 | −€17.051 | 92,5% | 100,0% | 115 | €5.476 | −€19.463 | −€3.064 |
| high, nog niet gestegen | groot | 20% | max 2% van de omzet | verkoop op +50% | €5.102 | −€14.898 | 89,5% | 100,0% | 115 | €6.364 | −€16.551 | −€4.711 |
| grootste daling over 7 dagen | groot | 1 naam | max 2% van de omzet | verkoop op +50% | €4 | −€19.996 | 100,0% | 99,4% | 115 | €14.313 | −€34.001 | −€308 |
| grootste daling over 7 dagen | groot | 1% | max 2% van de omzet | verkoop op +50% | €4 | −€19.996 | 100,0% | 99,4% | 115 | €14.313 | −€34.001 | −€308 |
| grootste daling over 7 dagen | groot | 5% | max 2% van de omzet | verkoop op +50% | €8 | −€19.992 | 100,0% | 99,8% | 115 | €10.888 | −€30.360 | −€520 |
| grootste daling over 7 dagen | groot | 10% | max 2% van de omzet | verkoop op +50% | €99 | −€19.901 | 99,7% | 100,0% | 115 | €6.010 | −€25.051 | −€860 |
| grootste daling over 7 dagen | groot | 20% | max 2% van de omzet | verkoop op +50% | €623 | −€19.377 | 98,3% | 100,0% | 115 | €6.558 | −€24.498 | −€1.437 |
| omzet die dag | groot | 1 naam | max 2% van de omzet | verkoop op +50% | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | groot | 1% | max 2% van de omzet | verkoop op +50% | €11.561 | −€8.439 | 72,7% | 100,0% | 115 | €10.751 | −€14.864 | −€4.327 |
| omzet die dag | groot | 5% | max 2% van de omzet | verkoop op +50% | €15.328 | −€4.672 | 71,9% | 100,0% | 115 | €13.900 | −€12.180 | −€6.393 |
| omzet die dag | groot | 10% | max 2% van de omzet | verkoop op +50% | €13.055 | −€6.945 | 71,7% | 100,0% | 115 | €14.510 | −€13.611 | −€7.844 |
| omzet die dag | groot | 20% | max 2% van de omzet | verkoop op +50% | €7.375 | −€12.625 | 88,0% | 100,0% | 115 | €17.785 | −€25.504 | −€4.906 |
| model op +10% | groot | 1 naam | max 2% van de omzet | verkoop op +50% | €1 | −€19.999 | 100,0% | 99,9% | 115 | −€9.268 | −€10.540 | −€192 |
| model op +10% | groot | 1% | max 2% van de omzet | verkoop op +50% | €1 | −€19.999 | 100,0% | 99,9% | 115 | −€9.268 | −€10.540 | −€192 |
| model op +10% | groot | 5% | max 2% van de omzet | verkoop op +50% | €1 | −€19.999 | 100,0% | 100,0% | 115 | −€1.887 | −€17.862 | −€249 |
| model op +10% | groot | 10% | max 2% van de omzet | verkoop op +50% | €150 | −€19.850 | 99,6% | 100,0% | 115 | €8.061 | −€25.670 | −€2.241 |
| model op +10% | groot | 20% | max 2% van de omzet | verkoop op +50% | €507 | −€19.493 | 99,1% | 100,0% | 115 | €18.657 | −€32.270 | −€5.880 |
| model op +20% | groot | 1 naam | max 2% van de omzet | verkoop op +50% | €10 | −€19.990 | 100,0% | 96,1% | 115 | €39.216 | −€58.036 | −€1.169 |
| model op +20% | groot | 1% | max 2% van de omzet | verkoop op +50% | €10 | −€19.990 | 100,0% | 96,1% | 115 | €39.216 | −€58.036 | −€1.169 |
| model op +20% | groot | 5% | max 2% van de omzet | verkoop op +50% | €15 | −€19.985 | 100,0% | 99,5% | 115 | €12.022 | −€30.975 | −€1.031 |
| model op +20% | groot | 10% | max 2% van de omzet | verkoop op +50% | €339 | −€19.661 | 99,6% | 99,9% | 115 | €40.864 | −€52.550 | −€7.975 |
| model op +20% | groot | 20% | max 2% van de omzet | verkoop op +50% | €1.114 | −€18.886 | 98,7% | 100,0% | 115 | €42.713 | −€54.568 | −€7.030 |
| elke geschikte naam | groot | alles | max 2% van de omzet | verkoop op +50% | €3.598 | −€16.402 | 93,5% | 100,0% | 115 | €17.998 | −€30.312 | −€4.088 |
| slot bij de high | obscure | 1 naam | max 2% van de omzet | verkoop op +50% | €5.105 | −€14.895 | 81,6% | 47,9% | 115 | −€1.196 | −€12.989 | −€710 |
| slot bij de high | obscure | 1% | max 2% van de omzet | verkoop op +50% | €1.691 | −€18.309 | 94,1% | 80,2% | 115 | −€6.265 | −€11.452 | −€592 |
| slot bij de high | obscure | 5% | max 2% van de omzet | verkoop op +50% | €4.242 | −€15.758 | 88,9% | 91,1% | 115 | −€1.700 | −€13.054 | −€1.005 |
| slot bij de high | obscure | 10% | max 2% van de omzet | verkoop op +50% | €3.675 | −€16.325 | 91,8% | 95,9% | 115 | €2.587 | −€18.178 | −€735 |
| slot bij de high | obscure | 20% | max 2% van de omzet | verkoop op +50% | €4.626 | −€15.374 | 90,5% | 97,9% | 115 | €8.146 | −€21.955 | −€1.566 |
| high, nog niet gestegen | obscure | 1 naam | max 2% van de omzet | verkoop op +50% | €13.575 | −€6.425 | 37,0% | 27,5% | 115 | −€1.680 | −€3.774 | −€971 |
| high, nog niet gestegen | obscure | 1% | max 2% van de omzet | verkoop op +50% | €7.612 | −€12.388 | 65,3% | 49,7% | 115 | −€4.411 | −€6.719 | −€1.259 |
| high, nog niet gestegen | obscure | 5% | max 2% van de omzet | verkoop op +50% | €3.426 | −€16.574 | 87,7% | 88,8% | 115 | −€2.984 | −€12.579 | −€1.012 |
| high, nog niet gestegen | obscure | 10% | max 2% van de omzet | verkoop op +50% | €3.837 | −€16.163 | 88,7% | 94,8% | 115 | €66 | −€14.707 | −€1.521 |
| high, nog niet gestegen | obscure | 20% | max 2% van de omzet | verkoop op +50% | €3.992 | −€16.008 | 90,0% | 98,4% | 115 | €3.168 | −€17.969 | −€1.207 |
| grootste daling over 7 dagen | obscure | 1 naam | max 2% van de omzet | verkoop op +50% | €312 | −€19.688 | 98,6% | 68,8% | 115 | −€1.043 | −€17.377 | −€1.268 |
| grootste daling over 7 dagen | obscure | 1% | max 2% van de omzet | verkoop op +50% | €1.526 | −€18.474 | 92,8% | 76,4% | 115 | −€2.118 | −€13.413 | −€2.942 |
| grootste daling over 7 dagen | obscure | 5% | max 2% van de omzet | verkoop op +50% | €1.710 | −€18.290 | 93,0% | 91,4% | 115 | −€759 | −€16.175 | −€1.357 |
| grootste daling over 7 dagen | obscure | 10% | max 2% van de omzet | verkoop op +50% | €2.880 | −€17.120 | 91,3% | 94,8% | 115 | €3.802 | −€19.571 | −€1.352 |
| grootste daling over 7 dagen | obscure | 20% | max 2% van de omzet | verkoop op +50% | €2.837 | −€17.163 | 93,1% | 98,3% | 115 | €5.979 | −€21.841 | −€1.301 |
| omzet die dag | obscure | 1 naam | max 2% van de omzet | verkoop op +50% | €181 | −€19.819 | 99,5% | 99,9% | 115 | −€8.667 | −€10.963 | −€190 |
| omzet die dag | obscure | 1% | max 2% van de omzet | verkoop op +50% | €772 | −€19.228 | 98,2% | 99,9% | 115 | −€364 | −€17.365 | −€1.499 |
| omzet die dag | obscure | 5% | max 2% van de omzet | verkoop op +50% | €3.050 | −€16.950 | 94,5% | 100,0% | 115 | €10.389 | −€24.916 | −€2.424 |
| omzet die dag | obscure | 10% | max 2% van de omzet | verkoop op +50% | €3.585 | −€16.415 | 92,5% | 100,0% | 115 | €9.466 | −€23.154 | −€2.727 |
| omzet die dag | obscure | 20% | max 2% van de omzet | verkoop op +50% | €3.526 | −€16.474 | 92,7% | 100,0% | 115 | €10.031 | −€24.089 | −€2.416 |
| model op +10% | obscure | 1 naam | max 2% van de omzet | verkoop op +50% | €198 | −€19.802 | 99,5% | 65,7% | 115 | €6.342 | −€23.935 | −€2.208 |
| model op +10% | obscure | 1% | max 2% van de omzet | verkoop op +50% | €2.039 | −€17.961 | 95,6% | 70,7% | 115 | €9.692 | −€22.601 | −€5.052 |
| model op +10% | obscure | 5% | max 2% van de omzet | verkoop op +50% | €4.665 | −€15.335 | 88,0% | 88,9% | 115 | €10.429 | −€21.973 | −€3.792 |
| model op +10% | obscure | 10% | max 2% van de omzet | verkoop op +50% | €4.162 | −€15.838 | 91,0% | 94,3% | 115 | €11.383 | −€24.415 | −€2.805 |
| model op +10% | obscure | 20% | max 2% van de omzet | verkoop op +50% | €3.747 | −€16.253 | 92,8% | 97,7% | 115 | €14.879 | −€28.838 | −€2.294 |
| model op +20% | obscure | 1 naam | max 2% van de omzet | verkoop op +50% | €176 | −€19.824 | 99,8% | 90,8% | 115 | −€3.370 | −€16.303 | −€151 |
| model op +20% | obscure | 1% | max 2% van de omzet | verkoop op +50% | €268 | −€19.732 | 99,4% | 92,5% | 115 | €6.353 | −€25.597 | −€487 |
| model op +20% | obscure | 5% | max 2% van de omzet | verkoop op +50% | €3.099 | −€16.901 | 93,5% | 96,7% | 115 | €11.656 | −€25.860 | −€2.697 |
| model op +20% | obscure | 10% | max 2% van de omzet | verkoop op +50% | €3.448 | −€16.552 | 93,4% | 98,0% | 115 | €11.826 | −€26.341 | −€2.038 |
| model op +20% | obscure | 20% | max 2% van de omzet | verkoop op +50% | €4.241 | −€15.759 | 92,2% | 99,2% | 115 | €14.013 | −€27.423 | −€2.349 |
| elke geschikte naam | obscure | alles | max 2% van de omzet | verkoop op +50% | €4.818 | −€15.182 | 91,2% | 100,0% | 115 | €12.301 | −€25.929 | −€1.554 |

Er is niets aangezet.

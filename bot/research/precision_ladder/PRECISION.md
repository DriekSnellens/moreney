# Hoe vaak de aangewezen naam stijgt

Bitvavo heeft hier 428 dagelijkse namen. Een stijger wordt
gemeten van de volgende open tot de open zeven dagen later.
Precisie is het aandeel van de aangewezen namen dat de stap haalt.
Recall is het aandeel van alle stijgers dat in die keuze zat.
De basis is het aandeel in de hele mand.

De selectie staat vast: de hoogste naam, en de bovenste 1%, 5%,
10% en 20% van de mand die week. Groot is minstens €1.000.000 omzet,
obscure is €10.000 tot daaronder. Een week heeft minstens 10 namen.
De eerste 26 weken trainen alleen.

Zes scores: het slot tegen de high van 20 dagen, die high alleen bij
namen die over 7 dagen nog niet stegen, de grootste 7-daagse daling,
de omzet, en twee logistische modellen (op +10% en op +20%) die alleen
afgesloten weken zien. De straf staat op 1, de klassen wegen gelijk.
Het hoogste percentage met minstens 100 keuzes is de kop. Het hoogste
percentage op een kleinere cel staat erbij, met het aantal. Niets
wordt aangezet.

## Precisie op +10%

| score | mand | selectie | n | precisie | basis | × | recall | 2024 | 2025 | 2026 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| slot bij de high | alle | 1 naam | 115 | 10,4% | 15,5% | 0,67 | 0,3% | 10,5% | 7,7% | 13,6% |
| slot bij de high | alle | 1% | 307 | 11,1% | 15,5% | 0,71 | 0,7% | 15,8% | 7,4% | 13,4% |
| slot bij de high | alle | 5% | 1513 | 14,0% | 15,5% | 0,90 | 4,5% | 22,9% | 11,2% | 14,2% |
| slot bij de high | alle | 10% | 2999 | 14,1% | 15,5% | 0,91 | 9,0% | 22,8% | 11,7% | 13,9% |
| slot bij de high | alle | 20% | 6018 | 14,3% | 15,5% | 0,92 | 18,5% | 26,5% | 11,8% | 13,4% |
| high, nog niet gestegen | alle | 1 naam | 115 | 4,3% | 15,5% | 0,28 | 0,1% | 10,5% | 3,8% | 2,3% |
| high, nog niet gestegen | alle | 1% | 307 | 6,5% | 15,5% | 0,42 | 0,4% | 10,5% | 4,4% | 7,5% |
| high, nog niet gestegen | alle | 5% | 1513 | 11,6% | 15,5% | 0,74 | 3,7% | 18,8% | 9,9% | 11,2% |
| high, nog niet gestegen | alle | 10% | 2999 | 12,3% | 15,5% | 0,79 | 7,9% | 19,1% | 10,7% | 11,8% |
| high, nog niet gestegen | alle | 20% | 6018 | 13,1% | 15,5% | 0,85 | 16,9% | 22,4% | 11,1% | 12,5% |
| grootste daling over 7 dagen | alle | 1 naam | 115 | 13,9% | 15,5% | 0,90 | 0,3% | 31,6% | 11,5% | 9,1% |
| grootste daling over 7 dagen | alle | 1% | 307 | 17,6% | 15,5% | 1,13 | 1,2% | 28,9% | 17,0% | 14,9% |
| grootste daling over 7 dagen | alle | 5% | 1513 | 16,7% | 15,5% | 1,08 | 5,4% | 22,4% | 15,6% | 16,2% |
| grootste daling over 7 dagen | alle | 10% | 2999 | 17,3% | 15,5% | 1,11 | 11,1% | 22,5% | 16,2% | 16,8% |
| grootste daling over 7 dagen | alle | 20% | 6018 | 16,1% | 15,5% | 1,04 | 20,7% | 23,1% | 14,6% | 15,6% |
| omzet die dag | alle | 1 naam | 115 | 6,1% | 15,5% | 0,39 | 0,1% | 15,8% | 1,9% | 6,8% |
| omzet die dag | alle | 1% | 307 | 10,7% | 15,5% | 0,69 | 0,7% | 18,4% | 10,4% | 9,0% |
| omzet die dag | alle | 5% | 1513 | 15,0% | 15,5% | 0,97 | 4,9% | 23,4% | 14,4% | 13,1% |
| omzet die dag | alle | 10% | 2999 | 15,6% | 15,5% | 1,01 | 10,0% | 27,2% | 15,0% | 12,9% |
| omzet die dag | alle | 20% | 6018 | 16,2% | 15,5% | 1,04 | 20,9% | 26,7% | 15,4% | 14,0% |
| model op +10% | alle | 1 naam | 115 | 8,7% | 15,5% | 0,56 | 0,2% | 15,8% | 5,8% | 9,1% |
| model op +10% | alle | 1% | 307 | 12,1% | 15,5% | 0,78 | 0,8% | 15,8% | 12,6% | 10,4% |
| model op +10% | alle | 5% | 1513 | 15,4% | 15,5% | 0,99 | 5,0% | 20,3% | 15,6% | 13,7% |
| model op +10% | alle | 10% | 2999 | 16,1% | 15,5% | 1,04 | 10,3% | 23,6% | 16,0% | 14,1% |
| model op +10% | alle | 20% | 6018 | 16,7% | 15,5% | 1,07 | 21,5% | 25,7% | 16,2% | 14,6% |
| model op +20% | alle | 1 naam | 115 | 13,0% | 15,5% | 0,84 | 0,3% | 15,8% | 13,5% | 11,4% |
| model op +20% | alle | 1% | 307 | 15,3% | 15,5% | 0,99 | 1,0% | 23,7% | 17,8% | 10,4% |
| model op +20% | alle | 5% | 1513 | 15,9% | 15,5% | 1,02 | 5,1% | 25,5% | 17,0% | 11,9% |
| model op +20% | alle | 10% | 2999 | 16,9% | 15,5% | 1,09 | 10,8% | 25,1% | 16,9% | 14,5% |
| model op +20% | alle | 20% | 6018 | 16,7% | 15,5% | 1,07 | 21,5% | 26,0% | 16,1% | 14,6% |
| slot bij de high | groot | 1 naam | 115 | 9,6% | 15,8% | 0,60 | 2,1% | 15,8% | 5,8% | 11,4% |
| slot bij de high | groot | 1% | 115 | 9,6% | 15,8% | 0,60 | 2,1% | 15,8% | 5,8% | 11,4% |
| slot bij de high | groot | 5% | 168 | 9,5% | 15,8% | 0,60 | 3,0% | 11,8% | 7,2% | 11,8% |
| slot bij de high | groot | 10% | 338 | 13,0% | 15,8% | 0,82 | 8,3% | 18,5% | 10,2% | 14,0% |
| slot bij de high | groot | 20% | 667 | 13,9% | 15,8% | 0,88 | 17,5% | 25,4% | 10,7% | 12,1% |
| high, nog niet gestegen | groot | 1 naam | 115 | 7,8% | 15,8% | 0,49 | 1,7% | 26,3% | 0,0% | 9,1% |
| high, nog niet gestegen | groot | 1% | 115 | 7,8% | 15,8% | 0,49 | 1,7% | 26,3% | 0,0% | 9,1% |
| high, nog niet gestegen | groot | 5% | 168 | 8,3% | 15,8% | 0,53 | 2,6% | 20,6% | 3,6% | 7,8% |
| high, nog niet gestegen | groot | 10% | 338 | 9,5% | 15,8% | 0,60 | 6,0% | 16,9% | 7,8% | 7,5% |
| high, nog niet gestegen | groot | 20% | 667 | 11,5% | 15,8% | 0,73 | 14,5% | 18,3% | 11,0% | 8,4% |
| grootste daling over 7 dagen | groot | 1 naam | 115 | 16,5% | 15,8% | 1,04 | 3,6% | 26,3% | 11,5% | 18,2% |
| grootste daling over 7 dagen | groot | 1% | 115 | 16,5% | 15,8% | 1,04 | 3,6% | 26,3% | 11,5% | 18,2% |
| grootste daling over 7 dagen | groot | 5% | 168 | 14,9% | 15,8% | 0,94 | 4,7% | 23,5% | 10,8% | 15,7% |
| grootste daling over 7 dagen | groot | 10% | 338 | 12,7% | 15,8% | 0,80 | 8,1% | 16,9% | 10,8% | 13,1% |
| grootste daling over 7 dagen | groot | 20% | 667 | 14,5% | 15,8% | 0,92 | 18,3% | 21,4% | 13,1% | 12,6% |
| omzet die dag | groot | 1 naam | 115 | 6,1% | 15,8% | 0,38 | 1,3% | 15,8% | 1,9% | 6,8% |
| omzet die dag | groot | 1% | 115 | 6,1% | 15,8% | 0,38 | 1,3% | 15,8% | 1,9% | 6,8% |
| omzet die dag | groot | 5% | 168 | 9,5% | 15,8% | 0,60 | 3,0% | 14,7% | 8,4% | 7,8% |
| omzet die dag | groot | 10% | 338 | 10,7% | 15,8% | 0,67 | 6,8% | 20,0% | 9,6% | 6,5% |
| omzet die dag | groot | 20% | 667 | 13,6% | 15,8% | 0,86 | 17,1% | 21,4% | 13,8% | 8,9% |
| model op +10% | groot | 1 naam | 115 | 14,8% | 15,8% | 0,93 | 3,2% | 21,1% | 13,5% | 13,6% |
| model op +10% | groot | 1% | 115 | 14,8% | 15,8% | 0,93 | 3,2% | 21,1% | 13,5% | 13,6% |
| model op +10% | groot | 5% | 168 | 15,5% | 15,8% | 0,98 | 4,9% | 20,6% | 15,7% | 11,8% |
| model op +10% | groot | 10% | 338 | 17,8% | 15,8% | 1,12 | 11,3% | 21,5% | 17,5% | 15,9% |
| model op +10% | groot | 20% | 667 | 16,8% | 15,8% | 1,06 | 21,1% | 21,4% | 17,1% | 13,6% |
| model op +20% | groot | 1 naam | 115 | 17,4% | 15,8% | 1,10 | 3,8% | 31,6% | 13,5% | 15,9% |
| model op +20% | groot | 1% | 115 | 17,4% | 15,8% | 1,10 | 3,8% | 31,6% | 13,5% | 15,9% |
| model op +20% | groot | 5% | 168 | 17,9% | 15,8% | 1,13 | 5,6% | 23,5% | 16,9% | 15,7% |
| model op +20% | groot | 10% | 338 | 18,9% | 15,8% | 1,20 | 12,1% | 24,6% | 19,3% | 15,0% |
| model op +20% | groot | 20% | 667 | 17,7% | 15,8% | 1,12 | 22,2% | 26,2% | 17,4% | 13,1% |
| slot bij de high | obscure | 1 naam | 115 | 14,8% | 15,5% | 0,95 | 0,4% | 21,1% | 11,5% | 15,9% |
| slot bij de high | obscure | 1% | 263 | 13,3% | 15,5% | 0,86 | 0,8% | 20,0% | 9,5% | 14,6% |
| slot bij de high | obscure | 5% | 1342 | 14,7% | 15,5% | 0,95 | 4,8% | 25,2% | 12,2% | 14,3% |
| slot bij de high | obscure | 10% | 2675 | 14,0% | 15,5% | 0,90 | 9,0% | 23,0% | 11,2% | 14,2% |
| slot bij de high | obscure | 20% | 5348 | 14,5% | 15,5% | 0,94 | 18,8% | 26,4% | 12,0% | 13,8% |
| high, nog niet gestegen | obscure | 1 naam | 115 | 5,2% | 15,5% | 0,34 | 0,1% | 10,5% | 5,8% | 2,3% |
| high, nog niet gestegen | obscure | 1% | 263 | 7,6% | 15,5% | 0,49 | 0,5% | 11,4% | 6,7% | 7,3% |
| high, nog niet gestegen | obscure | 5% | 1342 | 11,7% | 15,5% | 0,75 | 3,8% | 18,2% | 9,9% | 11,7% |
| high, nog niet gestegen | obscure | 10% | 2675 | 12,4% | 15,5% | 0,80 | 8,0% | 19,2% | 11,0% | 12,0% |
| high, nog niet gestegen | obscure | 20% | 5348 | 13,3% | 15,5% | 0,86 | 17,1% | 22,4% | 11,3% | 12,8% |
| grootste daling over 7 dagen | obscure | 1 naam | 115 | 16,5% | 15,5% | 1,07 | 0,5% | 31,6% | 15,4% | 11,4% |
| grootste daling over 7 dagen | obscure | 1% | 263 | 19,4% | 15,5% | 1,25 | 1,2% | 28,6% | 18,1% | 17,9% |
| grootste daling over 7 dagen | obscure | 5% | 1342 | 16,8% | 15,5% | 1,09 | 5,5% | 23,3% | 16,3% | 15,6% |
| grootste daling over 7 dagen | obscure | 10% | 2675 | 17,1% | 15,5% | 1,10 | 11,0% | 23,0% | 16,0% | 16,6% |
| grootste daling over 7 dagen | obscure | 20% | 5348 | 16,4% | 15,5% | 1,06 | 21,1% | 23,3% | 14,8% | 16,0% |
| omzet die dag | obscure | 1 naam | 115 | 14,8% | 15,5% | 0,95 | 0,4% | 26,3% | 9,6% | 15,9% |
| omzet die dag | obscure | 1% | 263 | 18,6% | 15,5% | 1,20 | 1,2% | 31,4% | 18,1% | 15,4% |
| omzet die dag | obscure | 5% | 1342 | 18,1% | 15,5% | 1,17 | 5,9% | 30,8% | 17,6% | 15,3% |
| omzet die dag | obscure | 10% | 2675 | 17,2% | 15,5% | 1,11 | 11,1% | 28,9% | 16,6% | 14,8% |
| omzet die dag | obscure | 20% | 5348 | 16,5% | 15,5% | 1,07 | 21,3% | 26,9% | 15,6% | 14,6% |
| model op +10% | obscure | 1 naam | 115 | 10,4% | 15,5% | 0,67 | 0,3% | 21,1% | 7,7% | 9,1% |
| model op +10% | obscure | 1% | 263 | 12,9% | 15,5% | 0,83 | 0,8% | 20,0% | 10,5% | 13,0% |
| model op +10% | obscure | 5% | 1342 | 16,0% | 15,5% | 1,03 | 5,2% | 25,8% | 15,5% | 14,0% |
| model op +10% | obscure | 10% | 2675 | 16,5% | 15,5% | 1,07 | 10,7% | 26,7% | 15,9% | 14,4% |
| model op +10% | obscure | 20% | 5348 | 16,4% | 15,5% | 1,06 | 21,2% | 27,7% | 15,1% | 14,7% |
| model op +20% | obscure | 1 naam | 115 | 9,6% | 15,5% | 0,62 | 0,3% | 21,1% | 7,7% | 6,8% |
| model op +20% | obscure | 1% | 263 | 11,0% | 15,5% | 0,71 | 0,7% | 25,7% | 6,7% | 10,6% |
| model op +20% | obscure | 5% | 1342 | 16,1% | 15,5% | 1,04 | 5,2% | 27,0% | 15,7% | 13,7% |
| model op +20% | obscure | 10% | 2675 | 17,0% | 15,5% | 1,10 | 11,0% | 27,7% | 15,6% | 15,6% |
| model op +20% | obscure | 20% | 5348 | 17,1% | 15,5% | 1,10 | 22,0% | 27,7% | 15,9% | 15,3% |

## Eén naam, alle stappen

| score | mand | groen | +10% | +20% | +50% | bruto |
|---|---|---:|---:|---:|---:|---:|
| slot bij de high | alle | 40,9% | 10,4% | 4,3% | 0,9% | -0,5% |
| high, nog niet gestegen | alle | 47,8% | 4,3% | 0,0% | 0,0% | -0,2% |
| grootste daling over 7 dagen | alle | 32,2% | 13,9% | 7,0% | 0,9% | -5,8% |
| omzet die dag | alle | 56,5% | 6,1% | 0,0% | 0,0% | 0,3% |
| model op +10% | alle | 29,6% | 8,7% | 4,3% | 0,9% | -6,6% |
| model op +20% | alle | 28,7% | 13,0% | 8,7% | 3,5% | -2,4% |
| slot bij de high | groot | 46,1% | 9,6% | 6,1% | 0,0% | 0,6% |
| high, nog niet gestegen | groot | 45,2% | 7,8% | 3,5% | 0,0% | -0,6% |
| grootste daling over 7 dagen | groot | 36,5% | 16,5% | 6,1% | 0,0% | -5,4% |
| omzet die dag | groot | 56,5% | 6,1% | 0,0% | 0,0% | 0,3% |
| model op +10% | groot | 32,2% | 14,8% | 7,8% | 2,6% | -6,3% |
| model op +20% | groot | 32,2% | 17,4% | 11,3% | 3,5% | -3,4% |
| slot bij de high | obscure | 38,3% | 14,8% | 7,0% | 1,7% | -1,0% |
| high, nog niet gestegen | obscure | 43,5% | 5,2% | 0,0% | 0,0% | -1,2% |
| grootste daling over 7 dagen | obscure | 35,7% | 16,5% | 7,8% | 1,7% | -3,5% |
| omzet die dag | obscure | 36,5% | 14,8% | 7,8% | 0,0% | -2,9% |
| model op +10% | obscure | 34,8% | 10,4% | 7,0% | 1,7% | -3,4% |
| model op +20% | obscure | 35,7% | 9,6% | 6,1% | 0,9% | -5,3% |

Op een groene week is het hoogste percentage met minstens 100 keuzes 56,5% (omzet die dag, alle, 1 naam, n=115), tegen een basis van 43,8%. Recall daarbij is 0,5%. Op +10% is het hoogste percentage met minstens 100 keuzes 19,4% (grootste daling over 7 dagen, obscure, 1%, n=263), tegen een basis van 15,5%. Recall daarbij is 1,2%. Op +20% is het hoogste percentage met minstens 100 keuzes 12,4% (model op +20%, groot, 10%, n=338), tegen een basis van 6,9%. Recall daarbij is 18,0%. Op +50% is het hoogste percentage met minstens 100 keuzes 4,4% (model op +20%, groot, 10%, n=338), tegen een basis van 1,3%. Recall daarbij is 34,9%. Geen stap haalt 60% in een cel met minstens 100 keuzes. Er is niets aangezet.

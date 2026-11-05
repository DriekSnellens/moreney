# CoinMarketCap als filter op het wekelijkse boek

Bitvavo heeft hier 428 dagelijkse namen. CoinMarketCap telt op
2026-11-05 8043 actieve munten, een
marktkapitaal van €2.391.945.222.999 en een dominantie van
de grootste munt van 60,2%.
De dominantie van de tweede munt is 11,0%.
Over 30 dagen veranderde het marktkapitaal −8,2%,
over 90 dagen 25,8%.

De dagelijkse stempel van CoinMarketCap is de Bitvavo-slot van de
vorige sessie. Dat is gecontroleerd op de prijs van de grootste munt:
de stempel van middernacht valt samen met die slotkoers. De koop is
de volgende open, de verkoop de open zeven dagen later. Een latere
stempel blijft onzichtbaar.

Het filter stond vast: marktkapitaal omhoog of omlaag over 7 dagen,
dominantie omhoog of omlaag in procentpunten, beide combinaties, en
wereldvolume minstens 1,5 keer of hoogstens 0,7 keer het gemiddelde
van de zeven dagen ervoor. Daaronder ligt een vast Bitvavo-boek:
elke liquide naam, de hoogste stijging over 7 dagen, de hoogste
omzet, of de vijf hoogste stijgingen. Liquide is minstens €1.000.000
omzet en geen peg. Start €20.000. Kosten zijn 0,25% taker per kant
plus impact.

De lat: minstens 30 weken, eind boven €20.000, eind boven hetzelfde
boek zonder filter, en in 2024, 2025 en 2026 elk minstens acht weken
en een plus. Niets wordt aangezet.

## Live

Het boek dat aan staat is het residual-pakket uit tactic lab,
venster 2024-03-16 t/m 2026-10-03, één munt, trail 10% en lock
+12%/+6%. De eindstand is de gepubliceerde live-stand.

| boek | eind | PnL | terugval | rondes | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|
| live residual | €488.677 | €468.677 | 31,2% | 96 | €132.986 | €18.812 | €316.879 |

## Uitkomst

Geen filter haalt de lat. Het hoogste filter: vijf hoogste stijgingen, wereldvolume minstens 1,5×, eind €42.371 (PnL €22.371, terugval 40,3%, 14 weken, 2024 €13.790, 2025 €30.569, 2026 −€21.988). Zonder taker en zonder impact eindigt dezelfde regel op €46.509. Geen filter is in 2024, 2025 en 2026 alle drie positief. Het live residual-boek eindigt op €488.677, PnL €468.677, terugval 31,2%, 96 rondes, 2024 €132.986, 2025 €18.812, 2026 €316.879. Dat boek blijft voor. Er is niets aangezet.

## Elke filter

| boek | filter | eind | PnL | terugval | weken | 2024 | 2025 | 2026 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| elke liquide naam | elke week | €1.201 | −€18.799 | 96,3% | 141 | €4.280 | −€20.761 | −€2.317 |
| elke liquide naam | marktkapitaal stijgt | €6.685 | −€13.315 | 81,1% | 82 | €5.384 | −€16.518 | −€2.181 |
| elke liquide naam | marktkapitaal daalt | €3.546 | −€16.454 | 87,1% | 59 | −€883 | −€11.220 | −€4.352 |
| elke liquide naam | dominantie stijgt | €1.781 | −€18.219 | 92,7% | 74 | −€7.078 | −€8.741 | −€2.400 |
| elke liquide naam | dominantie daalt | €13.292 | −€6.708 | 75,7% | 67 | €17.563 | −€20.828 | −€3.444 |
| elke liquide naam | kapitaal stijgt, dominantie daalt | €27.355 | €7.355 | 37,3% | 39 | €13.527 | −€6.881 | €709 |
| elke liquide naam | kapitaal en dominantie stijgen | €4.859 | −€15.141 | 80,0% | 43 | −€4.861 | −€8.503 | −€1.777 |
| elke liquide naam | wereldvolume minstens 1,5× | €24.848 | €4.848 | 29,0% | 14 | €3.675 | €7.790 | −€6.616 |
| elke liquide naam | wereldvolume hoogstens 0,7× | €21.934 | €1.934 | 0,0% | 3 | €0 | €1.359 | €575 |
| hoogste stijging 7 dagen | elke week | €0 | −€20.000 | 100,0% | 140 | €151.652 | −€170.850 | −€801 |
| hoogste stijging 7 dagen | marktkapitaal stijgt | €144 | −€19.856 | 99,8% | 82 | €13.904 | −€25.417 | −€8.344 |
| hoogste stijging 7 dagen | marktkapitaal daalt | €35 | −€19.965 | 100,0% | 58 | €88.064 | −€106.030 | −€2.000 |
| hoogste stijging 7 dagen | dominantie stijgt | €5 | −€19.995 | 100,0% | 73 | €9.901 | −€28.773 | −€1.123 |
| hoogste stijging 7 dagen | dominantie daalt | €1.005 | −€18.995 | 99,9% | 67 | €101.679 | −€106.653 | −€14.021 |
| hoogste stijging 7 dagen | kapitaal stijgt, dominantie daalt | €27.117 | €7.117 | 90,8% | 39 | €33.533 | €50.323 | −€76.739 |
| hoogste stijging 7 dagen | kapitaal en dominantie stijgen | €99 | −€19.901 | 99,8% | 43 | −€7.199 | −€11.221 | −€1.481 |
| hoogste stijging 7 dagen | wereldvolume minstens 1,5× | €41.717 | €21.717 | 80,6% | 14 | €52.507 | €74.164 | −€104.954 |
| hoogste stijging 7 dagen | wereldvolume hoogstens 0,7× | €12.244 | −€7.756 | 38,8% | 3 | €0 | −€3.716 | −€4.040 |
| hoogste omzet | elke week | €10.359 | −€9.641 | 72,6% | 141 | €7.451 | −€13.238 | −€3.854 |
| hoogste omzet | marktkapitaal stijgt | €16.731 | −€3.269 | 50,1% | 82 | €5.845 | −€12.451 | €3.336 |
| hoogste omzet | marktkapitaal daalt | €12.341 | −€7.659 | 62,8% | 59 | €1.250 | −€5 | −€8.905 |
| hoogste omzet | dominantie stijgt | €8.961 | −€11.039 | 55,2% | 74 | −€3.285 | −€4.110 | −€3.644 |
| hoogste omzet | dominantie daalt | €23.030 | €3.030 | 53,2% | 67 | €12.878 | −€10.297 | €449 |
| hoogste omzet | kapitaal stijgt, dominantie daalt | €27.336 | €7.336 | 27,7% | 39 | €6.206 | −€5.451 | €6.580 |
| hoogste omzet | kapitaal en dominantie stijgen | €12.211 | −€7.789 | 41,4% | 43 | −€283 | −€6.826 | −€681 |
| hoogste omzet | wereldvolume minstens 1,5× | €19.640 | −€360 | 24,9% | 14 | €1.070 | €1.057 | −€2.486 |
| hoogste omzet | wereldvolume hoogstens 0,7× | €21.009 | €1.009 | 0,0% | 3 | €0 | €430 | €580 |
| vijf hoogste stijgingen | elke week | €110 | −€19.890 | 99,7% | 140 | €615 | −€19.031 | −€1.475 |
| vijf hoogste stijgingen | marktkapitaal stijgt | €2.145 | −€17.855 | 94,4% | 82 | €2.836 | −€15.371 | −€5.320 |
| vijf hoogste stijgingen | marktkapitaal daalt | €990 | −€19.010 | 96,1% | 58 | −€1.988 | −€13.838 | −€3.184 |
| vijf hoogste stijgingen | dominantie stijgt | €570 | −€19.430 | 98,1% | 73 | −€7.430 | −€10.029 | −€1.971 |
| vijf hoogste stijgingen | dominantie daalt | €3.712 | −€16.288 | 91,3% | 67 | €12.744 | −€20.521 | −€8.511 |
| vijf hoogste stijgingen | kapitaal stijgt, dominantie daalt | €18.234 | −€1.766 | 63,9% | 39 | €16.295 | −€132 | −€17.929 |
| vijf hoogste stijgingen | kapitaal en dominantie stijgen | €2.297 | −€17.703 | 92,5% | 43 | −€7.423 | −€8.501 | −€1.779 |
| vijf hoogste stijgingen | wereldvolume minstens 1,5× | €42.371 | €22.371 | 40,3% | 14 | €13.790 | €30.569 | −€21.988 |
| vijf hoogste stijgingen | wereldvolume hoogstens 0,7× | €19.331 | −€669 | 7,4% | 3 | €0 | €886 | −€1.555 |

## Twaalf maanden muntquotes

Deze sleutel geeft muntkoersen vanaf 2025-11-05. 416 Bitvavo-namen
vielen samen met een CoinMarketCap-id: de euro-prijs week in de mediaan
hoogstens 25% af van de Bitvavo-slot. Botsende symbolen houden het id
waarvan de prijs het dichtst bij die slot ligt. De scores zijn de
7-daagse CMC-verandering, het CMC-marktkapitaal, en de eigen
Bitvavo-stijging over 7 dagen, op dezelfde weken en met dezelfde
kosten. 2024 heeft hier geen trades. Dit venster vervangt het live
boek niet.

| boek | filter | eind | PnL | terugval | weken | 2024 | 2025 | 2026 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| CMC-stijging 7 dagen | 1 naam | €1 | −€19.999 | 100,0% | 51 | €0 | −€16.329 | −€3.670 |
| CMC-stijging 7 dagen | 5 namen | €511 | −€19.489 | 97,4% | 51 | €0 | −€12.041 | −€7.448 |
| CMC-marktkapitaal | 1 naam | €11.860 | −€8.140 | 50,3% | 51 | €0 | −€3.782 | −€4.357 |
| CMC-marktkapitaal | 5 namen | €11.450 | −€8.550 | 53,0% | 51 | €0 | −€4.071 | −€4.478 |
| Bitvavo-stijging 7 dagen | 1 naam | €1 | −€19.999 | 100,0% | 51 | €0 | −€16.329 | −€3.670 |
| Bitvavo-stijging 7 dagen | 5 namen | €540 | −€19.460 | 97,3% | 51 | €0 | −€12.118 | −€7.342 |

Er is niets aangezet.

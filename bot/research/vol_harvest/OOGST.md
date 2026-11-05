# Ladder en volgdag op volatiele alts

Tweede verse zoektocht. De ladder koopt een trede op een volatiele
naam, koopt een trede bij als het slot een stap onder de laagste
trede staat, en verkoopt een trede als het slot een stap boven haar
kosten staat. Vier treden, drie namen, de meest liquide naam eerst.
Een poort sluit de hele ladder: geen, het eigen 50-daags gemiddelde,
het 50-daags gemiddelde van de benchmark, of een stijgende 7-daagse
marktwaarde van CoinMarketCap. Elke ladder gaat na 90 dagen dicht.

De volgdag koopt na een grote dag van de benchmark (3% of 5%) de drie
namen met de hoogste 60-daagse bèta, en verkoopt na 1, 3 of 7 sessies.

Signaal op het slot, vulling op de volgende open. Taker 0,25% per kant
plus impact. Pegs en de benchmark doen niet mee. Volatiel is een band
van 20 dagen van minstens 20%. Obscure is €10.000 tot €1.000.000 omzet,
liquide is daarboven. AlphaI staat pas aan vanaf augustus 2026 en kan
de drie-jaren-lat niet halen; het telt hier niet mee.
Venster 16 maart 2024 tot 2026-10-30. Start €20.000.

Een cel is winstgevend als ze minstens 30 rondes heeft, boven €20.000
eindigt, en in 2024, 2025 en 2026 elk een plus heeft met minstens acht
rondes. Niets wordt aangezet.

## Overzicht

Het live residual-pakket is het boek dat op deze tape al winstgevend is: eind €488.677, terugval 31,2%, 96 rondes, 2024 €132.986, 2025 €18.812, 2026 €316.879. In dit rooster haalt de lat: liquide en volatiel, ladder 15%, zonder poort eindigt op €28.006, terugval 31,4%, 147 rondes, €3.250 (51), €1.941 (56), €2.815 (40). Van haar 5 buren eindigen 0 boven de inleg. Dat is een smalle cel: één stap of één poort verder verliest het boek. Een cel op dezelfde tape is een kandidaat, geen nieuwe live-stand. De hoogste ladder is de cel hierboven. De hoogste volgdag is liquide, volgdag na 5%, 7 sessies eindigt op €29.192, terugval 49,0%, 51 rondes, €1.819 (21), −€139 (18), €7.512 (12). Die haalt de lat niet. Er is niets aangezet.

## Ladder

| cel | eind | PnL | terugval | rondes | 2024 | 2025 | 2026 | lat |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| liquide en volatiel, ladder 5%, zonder poort | €11.264 | −€8.736 | 66,7% | 318 | €1.799 (115) | €2.286 (119) | −€12.821 (84) | nee |
| liquide en volatiel, ladder 5%, eigen 50-daags | €1.416 | −€18.584 | 93,3% | 752 | −€6.344 (213) | −€8.804 (312) | −€3.437 (227) | nee |
| liquide en volatiel, ladder 5%, benchmark 50-daags | €15.147 | −€4.853 | 40,0% | 363 | −€317 (140) | €1.974 (116) | −€6.510 (107) | nee |
| liquide en volatiel, ladder 5%, marktwaarde omhoog | €16.877 | −€3.123 | 46,1% | 489 | €4.079 (150) | −€3.433 (178) | −€3.769 (161) | nee |
| liquide en volatiel, ladder 10%, zonder poort | €17.265 | −€2.735 | 41,5% | 209 | €819 (76) | −€1.554 (71) | −€2.001 (62) | nee |
| liquide en volatiel, ladder 10%, eigen 50-daags | €1.517 | −€18.483 | 92,8% | 478 | −€5.476 (130) | −€8.855 (199) | −€4.152 (149) | nee |
| liquide en volatiel, ladder 10%, benchmark 50-daags | €11.579 | −€8.421 | 51,0% | 223 | −€5.361 (83) | −€399 (75) | −€2.662 (65) | nee |
| liquide en volatiel, ladder 10%, marktwaarde omhoog | €17.584 | −€2.416 | 37,5% | 352 | €4.339 (104) | −€3.081 (133) | −€3.674 (115) | nee |
| liquide en volatiel, ladder 15%, zonder poort | €28.006 | €8.006 | 31,4% | 147 | €3.250 (51) | €1.941 (56) | €2.815 (40) | ja |
| liquide en volatiel, ladder 15%, eigen 50-daags | €2.443 | −€17.557 | 88,1% | 338 | −€5.705 (84) | −€8.274 (149) | −€3.579 (105) | nee |
| liquide en volatiel, ladder 15%, benchmark 50-daags | €14.697 | −€5.303 | 34,6% | 168 | −€3.254 (61) | €964 (57) | −€3.013 (50) | nee |
| liquide en volatiel, ladder 15%, marktwaarde omhoog | €18.603 | −€1.397 | 37,3% | 304 | €5.649 (87) | −€4.150 (118) | −€2.895 (99) | nee |
| obscure en volatiel, ladder 5%, zonder poort | €4.448 | −€15.552 | 83,1% | 332 | −€3.103 (100) | −€11.167 (114) | −€1.282 (118) | nee |
| obscure en volatiel, ladder 5%, eigen 50-daags | €5.458 | −€14.542 | 76,8% | 837 | −€1.532 (233) | −€5.701 (342) | −€7.309 (262) | nee |
| obscure en volatiel, ladder 5%, benchmark 50-daags | €4.083 | −€15.917 | 81,3% | 402 | −€5.301 (138) | −€8.673 (129) | −€1.942 (135) | nee |
| obscure en volatiel, ladder 5%, marktwaarde omhoog | €5.725 | −€14.275 | 76,3% | 542 | −€1.651 (162) | −€10.794 (219) | −€1.830 (161) | nee |
| obscure en volatiel, ladder 10%, zonder poort | €9.396 | −€10.604 | 64,9% | 225 | −€416 (78) | −€10.220 (75) | €32 (72) | nee |
| obscure en volatiel, ladder 10%, eigen 50-daags | €4.665 | −€15.335 | 77,9% | 478 | −€6.292 (139) | −€4.404 (196) | −€4.639 (143) | nee |
| obscure en volatiel, ladder 10%, benchmark 50-daags | €10.488 | −€9.512 | 57,7% | 253 | −€3.269 (87) | −€5.648 (85) | −€595 (81) | nee |
| obscure en volatiel, ladder 10%, marktwaarde omhoog | €7.277 | −€12.723 | 66,9% | 367 | −€3.068 (106) | −€7.172 (147) | −€2.482 (114) | nee |
| obscure en volatiel, ladder 15%, zonder poort | €15.408 | −€4.592 | 54,6% | 166 | −€255 (42) | −€5.684 (61) | €1.347 (63) | nee |
| obscure en volatiel, ladder 15%, eigen 50-daags | €5.408 | −€14.592 | 75,0% | 358 | −€4.788 (103) | −€5.953 (145) | −€3.850 (110) | nee |
| obscure en volatiel, ladder 15%, benchmark 50-daags | €12.144 | −€7.856 | 50,7% | 182 | −€2.517 (63) | −€4.509 (59) | −€830 (60) | nee |
| obscure en volatiel, ladder 15%, marktwaarde omhoog | €8.433 | −€11.567 | 62,9% | 304 | −€1.254 (84) | −€7.714 (121) | −€2.600 (99) | nee |

## Volgdag

| cel | eind | PnL | terugval | rondes | 2024 | 2025 | 2026 | lat |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| liquide, volgdag na 3%, 1 sessies | €10.092 | −€9.908 | 60,6% | 210 | −€1.881 (87) | −€6.556 (72) | −€1.470 (51) | nee |
| liquide, volgdag na 3%, 3 sessies | €14.017 | −€5.983 | 69,8% | 177 | €1.882 (75) | −€9.677 (60) | €1.812 (42) | nee |
| liquide, volgdag na 3%, 7 sessies | €8.663 | −€11.337 | 83,8% | 135 | €4.672 (51) | −€15.604 (54) | −€405 (30) | nee |
| liquide, volgdag na 5%, 1 sessies | €17.343 | −€2.657 | 39,4% | 69 | €515 (27) | −€2.436 (18) | −€737 (24) | nee |
| liquide, volgdag na 5%, 3 sessies | €28.440 | €8.440 | 42,4% | 60 | −€3.372 (27) | €7.787 (18) | €4.025 (15) | nee |
| liquide, volgdag na 5%, 7 sessies | €29.192 | €9.192 | 49,0% | 51 | €1.819 (21) | −€139 (18) | €7.512 (12) | nee |
| obscure, volgdag na 3%, 1 sessies | €12.174 | −€7.826 | 54,5% | 210 | −€4.659 (87) | −€2.545 (72) | −€622 (51) | nee |
| obscure, volgdag na 3%, 3 sessies | €11.350 | −€8.650 | 63,2% | 177 | −€1.873 (75) | −€8.212 (60) | €1.434 (42) | nee |
| obscure, volgdag na 3%, 7 sessies | €11.137 | −€8.863 | 75,1% | 135 | €4.389 (51) | −€11.647 (54) | −€1.605 (30) | nee |
| obscure, volgdag na 5%, 1 sessies | €12.818 | −€7.182 | 35,9% | 69 | −€1.545 (27) | −€3.938 (18) | −€1.700 (24) | nee |
| obscure, volgdag na 5%, 3 sessies | €15.306 | −€4.694 | 45,5% | 60 | −€3.012 (27) | −€1.696 (18) | €14 (15) | nee |
| obscure, volgdag na 5%, 7 sessies | €13.802 | −€6.198 | 60,5% | 51 | −€3.710 (21) | −€5.878 (18) | €3.390 (12) | nee |

Er is niets aangezet.

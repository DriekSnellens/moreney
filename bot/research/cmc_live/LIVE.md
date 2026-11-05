# CoinMarketCap op het live residual-boek

Het boek is het pakket dat aan staat: één munt, 10-daags excess
met één dag overslaan, klok van 7 dagen, trail 10% en lock +12%/+6%.
Start €20.000, venster 2024-03-16 t/m 2026-10-03, natte Bitvavo-vulling.
CoinMarketCap is de enige toevoeging. Elke poort stond vast.

Een wereldpoort leest de stempel die bij de slot van die sessie
hoort. Een muntpoort doet niets tot er die dag twintig namen een
stempel hebben. Daarna eist hij een positieve CMC-stijging over
7 dagen, een stijging boven de naam met het grootste CMC-kapitaal,
of een plek in de 10, 20 of 50 grootste.

Hij verslaat het live boek alleen met een hogere eindstand, een plus
in 2024, 2025 en 2026, minstens 30 rondes, en een terugval die
hoogstens 15 procentpunten slechter is. Niets wordt aangezet.

## Gepubliceerde live-stand

Tactic lab, venster tot 2026-10-03. De rij daaronder is dit cache
opnieuw gedraaid, op hetzelfde venster als de poorten.

| boek | eind | PnL | terugval | rondes | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|
| gepubliceerd | €488.677 | €468.677 | 31,2% | 96 | €132.986 | €18.812 | €316.879 |

## Deze tape

Geen poort verslaat het live boek op deze tape. Deze poorten laten elke ronde van het live boek staan: CMC-stijging positief, CMC-stijging boven de grootste. De hoogste poort die het pad wel verandert: binnen de 50 grootste eindigt op €356.805 (PnL €336.805, terugval 28,5%, 86 rondes, 2024 €132.986, 2025 €18.812, 2026 €185.007). Hetzelfde residual-boek zonder CoinMarketCap eindigt hier op €488.677, PnL €468.677, terugval 31,2%, 96 rondes. Er is niets aangezet.

| poort | eind | PnL | terugval | rondes | 2024 | 2025 | 2026 | verslaat |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| live residual | €488.677 | €468.677 | 31,2% | 96 | €132.986 | €18.812 | €316.879 | nee |
| marktkapitaal stijgt | €166.112 | €146.112 | 33,0% | 72 | €85.748 | €31.322 | €29.042 | nee |
| marktkapitaal daalt | €41.498 | €21.498 | 30,8% | 39 | €794 | €743 | €19.961 | nee |
| dominantie stijgt | €61.366 | €41.366 | 35,6% | 66 | €3.253 | €865 | €37.248 | nee |
| dominantie daalt | €39.589 | €19.589 | 36,1% | 52 | €18.847 | −€2.699 | €3.441 | nee |
| kapitaal stijgt, dominantie daalt | €24.806 | €4.806 | 30,3% | 36 | €13.050 | −€407 | −€7.836 | nee |
| kapitaal en dominantie stijgen | €47.675 | €27.675 | 24,0% | 50 | €8.634 | €2.100 | €16.942 | nee |
| wereldvolume minstens 1,5× | €21.876 | €1.876 | 18,4% | 14 | −€2.975 | €2.772 | €2.079 | nee |
| wereldvolume hoogstens 0,7× | €19.305 | −€695 | 14,6% | 4 | −€695 | €0 | €0 | nee |
| CMC-stijging positief | €488.677 | €468.677 | 31,2% | 96 | €132.986 | €18.812 | €316.879 | nee |
| CMC-stijging boven de grootste | €488.677 | €468.677 | 31,2% | 96 | €132.986 | €18.812 | €316.879 | nee |
| binnen de 10 grootste | €175.573 | €155.573 | 24,8% | 72 | €132.986 | €18.812 | €3.775 | nee |
| binnen de 20 grootste | €323.412 | €303.412 | 35,3% | 82 | €132.986 | €18.812 | €151.614 | nee |
| binnen de 50 grootste | €356.805 | €336.805 | 28,5% | 86 | €132.986 | €18.812 | €185.007 | nee |

Er is niets aangezet.

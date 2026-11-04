# Strategie zoeken bij coin-nieuws en marktnieuws

De regel stond vast voordat de tape draaide. Er is daarna geen drempel
of horizon bijgesteld, en er is niets live gezet.

Per UTC-dag zijn twee feiten bekend op de eerstvolgende middernacht.
Het coin-nieuws is de meerderheid van de directionele labels van die coin
die dag: bull, bear, of stil. Het marktnieuws is de meerderheid van
`market_sentiment_alignment` op de AlphaI-macrofeed (macro, grondstoffen,
geopolitiek) die dag: bull, bear, gemengd bij gelijkspel, of stil.
Een stille coin op een stille markt blijft cash.

Voor elk ander paar zoekt de bot in de al afgesloten uitkomsten van
datzelfde paar. Het menu is een spot-koop: 24 uur vasthouden, een 10%
trail tot 7 dagen, of 7 dagen vasthouden. Een strategie telt pas bij
minstens 8 afgesloten uitkomsten en een positief gemiddeld nettorendement.
Het hoogste gemiddelde wint. Bij gelijkspel wint de eerdere naam in het
menu. Anders cash. Bitvavo spot kan niet shorten, dus shorten zit niet
in het menu. Kosten zijn 0,25% taker per kant. De vulling is de eerste
15-minuten-opening op of na de middernacht. Het boek heeft vier stukken
van €5.000, en een tweede boek dat elk stuk terugzet op een kwart van
de equity. Een coin die al open staat wordt overgeslagen.

Macro-archief 2026-08-07 t/m 2026-11-02, 9454 headlines,
88 dagen met een directionele marktstem. Coin-labels uit de
crypto-store, 1565 coin-dagen met een directionele stem.
Kandidaten zijn de live desk plus elke coin met een label die dag,
mits er een 15-minuten-tape is. Episodes met een pad: 913.

| regel | signalen | gevuld vast | PnL vast | eind herbelegd | max DD | gevuld herbelegd | gem. net signaal |
|---|---:|---:|---:|---:|---:|---:|---:|
| matcher | 684 | 110 | −€4,259 | €15,847 | 33.2% | 110 | -1.3% |
| bullish coin, houd 24u | 371 | 263 | −€2,331 | €17,078 | 34.5% | 263 | -0.0% |
| bullish coin, houd 7d | 161 | 37 | €5,088 | €24,996 | 6.8% | 37 | -0.3% |
| elk coin-nieuws, houd 24u | 505 | 317 | −€4,870 | €15,043 | 38.4% | 317 | -0.1% |

De matcher nam 684 signalen: hold_24h 526, trail_7d 22, hold_7d 136.
De vaste regels kopen zonder te kijken welk paar van coin en markt
in het verleden betaalde. Het herbelegde eindbedrag begint op €20.000.
Een positief eurobedrag naast een negatief gemiddeld signaal komt doordat
het boek maar vier stukken vult. Het gemiddelde over alle signalen is
de verwachting van de regel.

Dit venster is de overlap van de crypto-store en het macro-archief.
Een uitkomst op dit stuk tape zet de regel niet live.

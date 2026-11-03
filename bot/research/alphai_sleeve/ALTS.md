# Alt-market sleeve

Rules are the module docstring in `__init__.py`. This grid was
locked before the replay. The universe is every trading Bitvavo
EUR alt. Listed bases 419, with a daily cache for 419.
BTC and the cash pegs are outside it. Daily candles, taker both
sides, sleeve €10,000.
Archive 2026-08-05 → 2026-11-02. Overlap 2026-08-12 → 2026-11-02.
There is no news path before 2026.

## News overlap

| cell | pnl | max DD | name-days | buys | largest day | names | largest | one-window |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| desk four | -€183 | 30.4% | 313 | 174 | -€1,217 | 12 | ETH +€1,255, SOL +€984, UNI +€724, AVAX -€3 | — |
| desk all | +€1,586 | 28.5% | 600 | 325 | -€1,437 | 12 | ETH +€1,105, SOL +€893, UNI +€543, AVAX +€437 | — |
| liquid four | -€1,017 | 37.1% | 408 | 194 | +€1,200 | 24 | ETH +€895, SOL +€639, HYPE +€550, PUMP +€287 | — |
| liquid all | +€2,153 | 27.1% | 1267 | 698 | +€1,163 | 41 | ENA +€570, ETH +€415, SOL +€389, UNI +€373 | — |
| listed four | -€1,017 | 37.1% | 408 | 194 | +€1,200 | 24 | ETH +€895, SOL +€639, HYPE +€550, PUMP +€287 | — |
| listed all | +€1,959 | 27.4% | 1321 | 726 | +€1,158 | 44 | ENA +€553, ETH +€390, SOL +€385, VVV +€359 | — |
| listed chop | -€7,234 | 73.2% | 533 | 219 | +€1,997 | 67 | ZEUS +€1,939, AGI +€1,381, FLOCK +€1,145, GTC +€721 | — |
| listed book | +€1,711 | 24.3% | 27952 | 8641 | -€1,067 | 418 | ZEUS +€87, QNT +€67, LSK +€64, USELESS +€56 | — |

An overlap pass is not a two-period result. `desk` is the sixteen
large names on this same daily tape. `liquid` keeps the €100,000
quote floor. `listed` lowers that floor to €10,000.

## Full tape, no news

| cell | pnl | max DD | name-days | buys | largest day | names | largest | one-window |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| listed book full | -€8,173 | 91.8% | 254688 | 45465 | -€2,794 | 419 | PEPE +€235, FLOKI +€179, RSR +€151, JASMY +€140 | — |
| listed book before 2026 | -€7,322 | 85.7% | 162268 | 39629 | -€2,794 | 362 | PEPE +€232, FLOKI +€181, RSR +€153, JASMY +€140 | — |
| listed book 2026 | -€3,297 | 54.0% | 93228 | 20680 | -€1,129 | 419 | SYN +€54, DUSK +€54, USELESS +€40, CAP +€38 | — |
| listed chop full | -€9,997 | 100.0% | 5467 | 1576 | +€2,863 | 266 | FLOKI +€2,562, PEPE +€2,277, ZRX +€1,632, ONDO +€829 | — |
| listed chop before 2026 | -€9,996 | 100.0% | 3830 | 1575 | +€2,863 | 266 | FLOKI +€2,562, PEPE +€2,277, ZRX +€1,632, ONDO +€829 | — |
| listed chop 2026 | -€9,970 | 99.7% | 2082 | 755 | -€1,060 | 178 | DUSK +€831, AXS +€237, WMTX +€174, BILL +€118 | — |
| liquid book full | -€9,180 | 96.0% | 120903 | 41716 | -€2,336 | 418 | PEPE +€275, RSR +€182, JASMY +€146, FLOKI +€144 | — |
| liquid book before 2026 | -€8,458 | 91.2% | 86349 | 36420 | -€2,336 | 356 | PEPE +€267, RSR +€185, JASMY +€150, FLOKI +€147 | — |
| liquid book 2026 | -€4,926 | 64.4% | 34682 | 15248 | -€1,097 | 387 | DUSK +€120, CAP +€91, HYPE +€89, USELESS +€83 | — |

No full-tape cell clears both periods.
Nothing is armed.

Reproduce: `python -m bot.research.alphai_sleeve.alts`

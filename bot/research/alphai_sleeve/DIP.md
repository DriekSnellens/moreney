# Seven percent dip

Rules are the module docstring in `__init__.py`. The sell is 7%
above the fill. The bid stays 1% above the low already printed.
Sleeve €10,000, split across every eligible name that day.
Listed alts 419. Fifteen-minute books 419.
Tape 2024-01-01 → 2026-11-02. News overlap 2026-08-12 → 2026-11-02.
`tone` has no path before 2026.

| cell | pnl | max DD | fills | maker exits | flattens | maker pnl | flatten pnl | largest day | largest names | one-window |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| listed / full | -€9,406 | 94.9% | 75139 | 12057 | 63082 | +€23,291 | -€32,697 | -€1,403 | DIA +€49, AMP +€34, COMP +€28, ONDO +€27 | — |
| liquid / full | -€9,577 | 96.4% | 63350 | 10762 | 52588 | +€24,336 | -€33,913 | -€1,404 | DIA +€58, ACH +€51, CTSI +€43, LMWR +€36 | — |
| desk / full | -€9,924 | 99.4% | 14010 | 1372 | 12638 | +€19,039 | -€28,963 | -€1,249 | FET +€14, XRP -€286, SOL -€297, DOGE -€447 | — |
| listed / before 2026 | -€9,392 | 94.8% | 74718 | 11991 | 62727 | +€23,269 | -€32,660 | -€1,403 | DIA +€49, AMP +€34, COMP +€28, ONDO +€27 | — |
| liquid / before 2026 | -€9,579 | 96.4% | 62617 | 10655 | 51962 | +€24,299 | -€33,879 | -€1,404 | DIA +€58, ACH +€51, CTSI +€43, LMWR +€36 | — |
| desk / before 2026 | -€9,684 | 97.4% | 9924 | 1084 | 8840 | +€18,868 | -€28,551 | -€1,249 | FET +€24, XRP -€269, SOL -€284, DOGE -€430 | — |
| listed / 2026 | -€6,768 | 70.6% | 41839 | 6922 | 34917 | +€13,937 | -€20,705 | -€986 | ZBCN +€29, ZIG +€29, PONKE +€25, DGB +€23 | — |
| liquid / 2026 | -€7,524 | 77.3% | 28137 | 4601 | 23536 | +€12,702 | -€20,226 | -€1,011 | ZIG +€35, ZRC +€30, LMWR +€25, NPC +€24 | — |
| desk / 2026 | -€7,742 | 79.4% | 4086 | 288 | 3798 | +€5,245 | -€12,987 | -€1,144 | ATOM -€194, FET -€324, NEAR -€358, LINK -€414 | — |
| tone / 2026-08-12 → 2026-11-02 | -€1,751 | 25.8% | 1115 | 120 | 995 | +€5,334 | -€7,085 | -€584 | SOL +€133, ETH +€127, VVV +€120, PUMP +€113 | — |

Names with at least one fill: listed / full 299; liquid / full 315; desk / full 16; listed / before 2026 279; liquid / before 2026 278; desk / before 2026 16; listed / 2026 407; liquid / 2026 385; desk / 2026 16; tone / 2026-08-12 → 2026-11-02 44.

No cell clears both periods.
Nothing is armed.

Reproduce: `python -m bot.research.alphai_sleeve.dip`

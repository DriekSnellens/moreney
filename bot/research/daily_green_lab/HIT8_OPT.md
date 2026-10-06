# Entry/exit rond de trefzekere sleeve

asof `2026-10-06T08:00:20.599995+00:00`  specs `15000`

Trefzekerheid = aandeel trades waarvan de high binnen 3 dagen +8% boven de opening komt.
Een kandidaat telt alleen mee als die lat op 2024 én op 2025 minstens zo hoog blijft als de basis, en beide vensters groen zijn.
Gekozen op de som van de 2024- en 2025-PnL. 2026 is één meting.

Basis: `r3<=0.05 up>=0.08 xs>=0.25 tp=8% stop=5% 3d`

| Venster | Basis |
|---|---|
| 2024 | 68.6% · n=35 · €+120 |
| 2025 | 74.0% · n=77 · €+1,182 |
| 2026 | 62.5% · n=88 · €+375 |

Hoogste PnL met minstens die trefzekerheid: `r3<=0.08 up>=0.10 xs>=0.15 tp=6% stop=0% 8d`

| Venster | Gekozen |
|---|---|
| 2024 | 70.0% · n=20 · €+1,734 |
| 2025 | 77.0% · n=61 · €+4,586 |
| 2026 | 60.9% · n=46 · €+196 |

Reproduce:

```bash
.venv/bin/python -m bot.research.daily_green_lab.hit8_opt
```

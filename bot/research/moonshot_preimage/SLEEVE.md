# Moonshot sleeve — vast €1.700 boek

Apart van de owner residual/clip. Alleen de lab-gate:

`r3 ≥ 15%` + `RS excess ≥ 25%` + `close > SMA20 > SMA50`

Cash zonder setup · trail 10% · sizing capped op `book_eur` · rebalance elke 3d.

## Arm (paper eerst)

```bash
# .env / process env
MOMENTUM_MOONSHOT_CLIP_ENABLED=true
MOMENTUM_MOONSHOT_CLIP_ALLOW_LIVE=false   # paper
MOMENTUM_MOONSHOT_CLIP_BOOK_EUR=1700
```

Start / status:

```bash
curl -X POST https://<host>/live/momentum/moonshot-clip/start
curl https://<host>/live/momentum/moonshot-clip/status
```

Live (pas als paper ok is en ~€1.7k Bitvavo-vrij naast de owner-clip):

```bash
MOMENTUM_MOONSHOT_CLIP_ALLOW_LIVE=true
```

State: `data/momentum_moonshot_clip_state.json`  
Ledger: `data/momentum_moonshot_clip_ledger.jsonl`

## Wat dit níet is

- Geen 60–70% moonshot-voorspeller (~16% P50 in de lab).
- Vervangt **niet** residual_full / btc50 owner.
- AlphaI hard gate zit er niet op (bewust — mist runners).

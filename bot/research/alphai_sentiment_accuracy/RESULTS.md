# AlphaI news sentiment, hit rate

Each ticker label on a headline is one call. Positive is a call that
the next open is higher; negative is a call that it is lower. Neutral
is not a direction and is left out of the hit rate. The entry is the
first 15-minute Bitvavo open at or after the headline (or, for the
daily vote, the next UTC midnight). The exit is the open one horizon
later. A gap, or a bar more than 15 minutes after the label, drops
the call. A return of zero is a miss.

Null is the hit rate you get by keeping this mix of bullish and
bearish labels and ignoring which headline they came from: the share
of bullish labels times the share of up moves, plus the share of
bearish labels times the share of down moves. Lift is hit minus that
null, in percentage points. Mean bull and mean bear are the average
forward returns of those labels, in percent, with no fee.

Tape `data/research/alphai_sleeve/15m`. BTC-EUR 15m comes from Bitvavo, cached at `data/research/btc_eur_15m_sentiment.json`,
because that sleeve store has no BTC file.
News `data/research/alphai_crypto_news.json`. Horizons and the relevance cut at
8 were fixed before the run. Nothing is armed.

Directional labels 4786. Neutral labels 1267 (20.9% of labels). Labels with no Bitvavo 15m file: 2470.

### Price sign, every directional label

| horizon | hit | n | bull (n) | bear (n) | up | null | lift | mean bull | mean bear |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1h | 50.7% | 2253 | 52.5% (1532) | 46.9% (721) | 52.7% | 51.0% | -0.2 pp | +0.08% | +0.07% |
| 4h | 49.1% | 2180 | 51.0% (1482) | 45.1% (698) | 52.2% | 50.8% | -1.7 pp | +0.06% | +0.12% |
| 24h | 47.6% | 1871 | 46.9% (1255) | 48.9% (616) | 48.3% | 49.4% | -1.9 pp | +0.12% | +0.11% |
| 7d | 52.5% | 882 | 44.4% (597) | 69.5% (285) | 39.9% | 46.4% | +6.1 pp | +0.23% | -1.87% |

### Sign versus BTC, every directional label

| horizon | hit | n | bull (n) | bear (n) | up | null | lift | mean bull | mean bear |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1h | 50.1% | 2253 | 50.3% (1532) | 49.8% (721) | 50.2% | 50.1% | +0.0 pp | +0.05% | +0.04% |
| 4h | 48.0% | 2180 | 47.4% (1482) | 49.1% (698) | 48.5% | 49.5% | -1.5 pp | +0.00% | +0.06% |
| 24h | 46.2% | 1871 | 42.8% (1255) | 53.2% (616) | 44.1% | 48.0% | -1.8 pp | -0.13% | +0.11% |
| 7d | 51.0% | 882 | 43.4% (597) | 67.0% (285) | 40.0% | 46.5% | +4.5 pp | -0.15% | -1.26% |

### Price sign, relevance 8 or higher

| horizon | hit | n | bull (n) | bear (n) | up | null | lift | mean bull | mean bear |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1h | 51.4% | 259 | 54.5% (156) | 46.6% (103) | 54.1% | 50.8% | +0.5 pp | +0.15% | -0.01% |
| 4h | 50.2% | 253 | 57.1% (154) | 39.4% (99) | 58.5% | 51.8% | -1.6 pp | -0.19% | +0.15% |
| 24h | 47.9% | 211 | 49.6% (129) | 45.1% (82) | 51.7% | 50.4% | -2.5 pp | +0.11% | +0.62% |
| 7d | 48.9% | 90 | 47.5% (59) | 51.6% (31) | 47.8% | 49.3% | -0.4 pp | +1.09% | -0.30% |

### Price sign, one majority vote per coin per UTC day

| horizon | hit | n | bull (n) | bear (n) | up | null | lift | mean bull | mean bear |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 24h | 53.3% | 505 | 54.2% (371) | 50.7% (134) | 52.9% | 51.3% | +1.9 pp | +0.50% | +0.00% |
| 7d | 48.4% | 225 | 38.5% (161) | 73.4% (64) | 35.1% | 43.6% | +4.9 pp | +0.22% | -1.58% |

### Price sign, BTC and ETH only

| horizon | hit | n | bull (n) | bear (n) | up | null | lift | mean bull | mean bear |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1h | 46.9% | 469 | 49.3% (270) | 43.7% (199) | 52.2% | 50.2% | -3.3 pp | +0.02% | +0.08% |
| 4h | 46.9% | 465 | 49.8% (267) | 42.9% (198) | 52.9% | 50.4% | -3.5 pp | +0.07% | +0.11% |
| 24h | 46.9% | 441 | 49.4% (251) | 43.7% (190) | 52.4% | 50.3% | -3.4 pp | +0.26% | +0.10% |
| 7d | 58.9% | 241 | 53.1% (147) | 68.1% (94) | 44.8% | 48.9% | +10.1 pp | +0.56% | -1.69% |

### Price sign, every other coin

| horizon | hit | n | bull (n) | bear (n) | up | null | lift | mean bull | mean bear |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1h | 51.7% | 1784 | 53.2% (1262) | 48.1% (522) | 52.9% | 51.2% | +0.6 pp | +0.09% | +0.06% |
| 4h | 49.7% | 1715 | 51.3% (1215) | 46.0% (500) | 52.1% | 50.9% | -1.1 pp | +0.06% | +0.13% |
| 24h | 47.8% | 1430 | 46.3% (1004) | 51.2% (426) | 47.1% | 48.8% | -1.1 pp | +0.08% | +0.11% |
| 7d | 50.1% | 641 | 41.6% (450) | 70.2% (191) | 38.1% | 45.2% | +4.9 pp | +0.11% | -1.95% |


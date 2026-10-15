"""Bear-market sleeve lab: what earns when the RS sleeve sits in cash.

Causal daily replay on Bitvavo closes. Spot legs fill at the next open with the
Bitvavo taker fee; short legs are perp legs (OKX-style fee) with Binance funding
as the carry proxy, because Bitvavo spot cannot short. No per-coin branches.
"""

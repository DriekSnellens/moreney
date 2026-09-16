"""Non-executing rebalance recommendations from venue balances."""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping

from bot.funding.models import RebalanceRecommendation

_ZERO = Decimal("0")


def recommend_quote_rebalance(
    balances: Mapping[str, Decimal],
    *,
    asset: str = "EUR",
    target_weights: Mapping[str, Decimal] | None = None,
    fee_bps: Decimal = Decimal("10"),
    min_move: Decimal = Decimal("1"),
) -> list[RebalanceRecommendation]:
    """Suggest quote-asset moves toward equal (or weighted) targets.

    Does **not** mutate balances or call exchange APIs.
    """
    venues = [v for v, bal in balances.items() if bal is not None]
    if len(venues) < 2:
        return []

    bals = {v: Decimal(str(balances[v])) for v in venues}
    total = sum(bals.values(), _ZERO)
    if total <= 0:
        return []

    if target_weights:
        weight_sum = sum((Decimal(str(w)) for w in target_weights.values()), _ZERO)
        if weight_sum <= 0:
            return []
        targets = {
            v: total * (Decimal(str(target_weights.get(v, 0))) / weight_sum) for v in venues
        }
    else:
        each = total / Decimal(len(venues))
        targets = {v: each for v in venues}

    rich = sorted(
        ((v, bals[v] - targets[v]) for v in venues if bals[v] > targets[v]),
        key=lambda x: x[1],
        reverse=True,
    )
    poor = sorted(
        ((v, targets[v] - bals[v]) for v in venues if bals[v] < targets[v]),
        key=lambda x: x[1],
        reverse=True,
    )

    recs: list[RebalanceRecommendation] = []
    i = j = 0
    while i < len(rich) and j < len(poor):
        src, surplus = rich[i]
        dst, need = poor[j]
        qty = min(surplus, need)
        if qty < min_move:
            if surplus < need:
                i += 1
            else:
                j += 1
            continue
        fee = qty * (fee_bps / Decimal("10000"))
        recs.append(
            RebalanceRecommendation(
                from_venue=src,
                to_venue=dst,
                asset=asset.upper(),
                amount=qty,
                reason=f"{dst} {asset.upper()} inventory low vs target",
                current_from=bals[src],
                current_to=bals[dst],
                target_to=targets[dst],
                estimated_fee=fee,
                status="pending_manual",
            )
        )
        surplus -= qty
        need -= qty
        rich[i] = (src, surplus)
        poor[j] = (dst, need)
        if surplus <= 0:
            i += 1
        if need <= 0:
            j += 1
    return recs


def recommend_asset_topups(
    *,
    asset: str,
    balances: Mapping[str, Decimal],
    min_amount: Decimal,
    donor_preference: list[str] | None = None,
    fee_bps: Decimal = Decimal("10"),
) -> list[RebalanceRecommendation]:
    """Recommend moving ``asset`` from rich venues to venues below ``min_amount``."""
    bals = {v: Decimal(str(a)) for v, a in balances.items()}
    poor = [v for v, a in bals.items() if a < min_amount]
    if not poor:
        return []
    donors = sorted(
        ((v, a) for v, a in bals.items() if a > min_amount),
        key=lambda x: x[1],
        reverse=True,
    )
    if donor_preference:
        pref = {n.lower(): i for i, n in enumerate(donor_preference)}
        donors.sort(key=lambda x: (pref.get(x[0].lower(), 999), -x[1]))
    recs: list[RebalanceRecommendation] = []
    for dst in poor:
        need = min_amount - bals[dst]
        for idx, (src, have) in enumerate(donors):
            if src == dst or have <= min_amount:
                continue
            send = min(need, have - min_amount)
            if send <= 0:
                continue
            fee = send * (fee_bps / Decimal("10000"))
            recs.append(
                RebalanceRecommendation(
                    from_venue=src,
                    to_venue=dst,
                    asset=asset.upper(),
                    amount=send,
                    reason=f"{dst} {asset.upper()} inventory low",
                    current_from=have,
                    current_to=bals[dst],
                    target_to=min_amount,
                    estimated_fee=fee,
                    status="pending_manual",
                )
            )
            donors[idx] = (src, have - send)
            need -= send
            if need <= 0:
                break
    return recs

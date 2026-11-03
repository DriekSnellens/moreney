"""Idle cash on the long tail. Nothing here is armed.

The live book is one desk name on a weekly clock, and it sits in cash
about two weeks in three. This lab does not change that book. On a day
the live book is flat at both closes, the idle euros can buy up to four
alts that are not in the desk.

A name qualifies when the prior session fell at least 8%, BTC on that
same session fell less than 3%, and the five prior sessions have a
median quote of at least €100,000. The most negative names are taken.
Each one gets one quarter of the idle equity. The buy is the next open.
Two exits, locked before either replay. ``target`` rests a sell 6% above
the open and otherwise sells on the fourth session. ``stop`` rests a
sell 6% under the open and does not cap the winner: the lot stays until
that stop, or until the live book wants the cash back. A session that
opens through the stop fills at the open. If the live book wants the
cash, the lot is sold on that open first.

Both sides pay the Bitvavo taker and the square-root impact. The desk
names, BTC, and the cash pegs are not eligible. Rules above were locked
before the replay. A cell is better than live only when the combined
path earns more in 2024, in 2025, and in 2026, and its drawdown is not
deeper. Nothing is armed.
"""

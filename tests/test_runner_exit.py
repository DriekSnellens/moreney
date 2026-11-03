"""Runner trail stays off until the peak clears the arm."""

from bot.research.btc_residual_mix.engine import equity_below_ma
from bot.research.clip_exit_lab.policies import ExitPolicy, runner_exit


def _policy(**kwargs: float | bool) -> ExitPolicy:
    return ExitPolicy(name="t", alt_trail_pct=0.10, alt_lock_arm_pct=0.12, **kwargs)


def test_below_the_arm_the_live_trail_and_lock_stay() -> None:
    width, lock_on = runner_exit(_policy(alt_runner_arm_pct=0.40, alt_runner_trail_pct=0.20), 0.39)
    assert (width, lock_on) == (0.10, True)


def test_past_the_arm_the_trail_widens_and_the_lock_goes_off() -> None:
    width, lock_on = runner_exit(_policy(alt_runner_arm_pct=0.40, alt_runner_trail_pct=0.20), 0.40)
    assert (width, lock_on) == (0.20, False)


def test_a_hold_past_the_arm_drops_the_trail_and_the_lock() -> None:
    width, lock_on = runner_exit(
        _policy(alt_runner_arm_pct=0.40, alt_runner_hold=True), 0.50
    )
    assert (width, lock_on) == (0.0, False)


def test_equity_block_waits_until_the_window_is_full() -> None:
    assert equity_below_ma([10.0, 9.0], 20) is False


def test_sub_cent_noise_does_not_block_a_flat_book() -> None:
    window = [110_821.59] * 19 + [110_821.586]
    assert equity_below_ma(window, 20) is False


def test_equity_block_fires_when_the_last_mark_is_under_the_mean() -> None:
    rising = [float(i) for i in range(1, 21)]
    assert equity_below_ma(rising, 20) is False
    dipped = rising[:-1] + [5.0]
    assert equity_below_ma(dipped, 20) is True


def test_an_unarmed_policy_ignores_a_large_peak() -> None:
    width, lock_on = runner_exit(_policy(), 1.5)
    assert (width, lock_on) == (0.10, True)

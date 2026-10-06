"""The PnL search may not buy accuracy with a looser hit rate."""

from bot.research.daily_green_lab.hit8_opt import Spec, Row, choose


def _row(name_pnl: tuple[float, float], hit: tuple[float, float], n: tuple[int, int] = (30, 50)) -> Row:
    fit_pnl, sel_pnl = name_pnl
    fit_p, sel_p = hit
    return Row(
        spec=Spec(0.05, 0.08, 0.25, 0.08, 0.05, 3),
        fit_n=n[0],
        fit_p=fit_p,
        fit_pnl=fit_pnl,
        sel_n=n[1],
        sel_p=sel_p,
        sel_pnl=sel_pnl,
    )


def test_choose_rejects_a_richer_sleeve_that_is_less_accurate() -> None:
    rich = _row((5_000, 8_000), (0.60, 0.70))
    accurate = _row((400, 1_200), (0.70, 0.75))
    picked = choose([rich, accurate], fit_floor=0.68, sel_floor=0.74)
    assert picked is accurate


def test_choose_takes_the_highest_pnl_among_accurate_sleeves() -> None:
    low = _row((300, 900), (0.70, 0.80))
    high = _row((500, 2_000), (0.69, 0.74))
    picked = choose([low, high], fit_floor=0.68, sel_floor=0.74)
    assert picked is high

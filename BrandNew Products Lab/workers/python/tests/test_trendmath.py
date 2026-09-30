import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trendmath import linear_slope, read_trend


class TrendMathTests(unittest.TestCase):
    def test_straight_series_matches_typescript_contract(self) -> None:
        self.assertEqual(linear_slope([1, 2, 3]), 1)
        reading = read_trend([1, 2, 3, 4])
        self.assertEqual(reading["direction"], "rising")
        self.assertGreater(reading["momentum_per_step"], 0)

    def test_short_series_is_insufficient(self) -> None:
        self.assertEqual(read_trend([1, 2, 3])["direction"], "insufficient")


if __name__ == "__main__":
    unittest.main()

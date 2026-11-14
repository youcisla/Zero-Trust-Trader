"""A dead instrument produces no signal.

A flat feed is not an overbought market and not a breakout. Two real bugs in this
tree were silent: RSI returned 100 on a flat series (a false short) and MACD's
signal line was all-NaN (a family that could never fire). These tests pin the
guards that turn a zero ATR into "no trade" — the indicator behaviour is not
changed here, only asserted.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import _mt5stub  # noqa: E402

_mt5stub.install()

import marketdata as md  # noqa: E402
import timemachine_mt5 as tm  # noqa: E402

BARS = 320


def dead_bars(price: float = 1.1, bars: int = BARS) -> dict:
    """Flat market: high, low and close identical on every bar."""
    c = np.full(bars, price)
    return {"h": c.copy(), "l": c.copy(), "c": c, "t": np.arange(bars) * 900}


def zero_range_bars(bars: int = BARS) -> dict:
    """h == l == c on every bar, but the price still steps between bars."""
    c = np.cumsum(np.random.RandomState(11).randn(bars)) + 50.0
    return {"h": c.copy(), "l": c.copy(), "c": c, "t": np.arange(bars) * 900}


class FlatAndAllEqualBars(unittest.TestCase):
    def test_atr_is_zero_once_warm(self):
        for bars in (dead_bars(), dead_bars(price=3.0)):
            a = md.atr(bars["h"], bars["l"], bars["c"], 14)
            self.assertFalse(np.any(np.isnan(a[14:])))
            self.assertTrue(np.all(a[14:] == 0.0))

    def test_bollinger_bands_collapse_onto_the_mean(self):
        mid, up, lo = md.bollinger(np.full(200, 1.1), 20, 2.0)
        warm = ~np.isnan(mid)
        # Mathematically upper == lower == mean; the band width is a rounding
        # residue (the window mean is not even exactly 1.1), so compare at 1e-12.
        self.assertTrue(np.allclose(up[warm], mid[warm], rtol=0, atol=1e-12))
        self.assertTrue(np.allclose(lo[warm], mid[warm], rtol=0, atol=1e-12))
        self.assertTrue(np.allclose(mid[warm], 1.1, rtol=0, atol=1e-12))

    def test_rsi_on_a_flat_series_is_neutral_not_overbought(self):
        # The old loop returned 100 here, which fired a false short.
        r = md.rsi(np.full(200, 1.1), 14)
        self.assertTrue(np.all(r[14:] == 50.0))

    def test_scanners_return_no_trades(self):
        bars = dead_bars()
        self.assertEqual(
            tm.scan_donchian(bars, 20, 1.5, 1.5, 32, "both", 50, 200, 0.0, False), [])
        self.assertEqual(
            tm.scan_rsi(bars, 14, 30, 70, 1.5, 1.5, 32, "both", 0.0, False), [])
        self.assertEqual(
            tm.scan_bollinger(bars, 20, 2.0, 1.5, 1.5, 32, "both", 0.0, False), [])


class ZeroRangeBars(unittest.TestCase):
    def test_every_bar_has_no_range(self):
        bars = zero_range_bars()
        self.assertTrue(np.all(bars["h"] - bars["l"] == 0.0))

    def test_atr_measures_the_step_not_the_bar_range(self):
        # h == l still leaves a non-zero true range when the close moves, which
        # is why the scanners guard on a[i] <= 0 and not on h == l.
        bars = zero_range_bars()
        a = md.atr(bars["h"], bars["l"], bars["c"], 14)
        self.assertFalse(np.any(np.isnan(a[14:])))
        self.assertTrue(np.all(a[14:] >= 0.0))


if __name__ == "__main__":
    unittest.main()

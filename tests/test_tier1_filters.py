"""Gate 1: what the whole funnel is allowed to scan.

The universe filter decides which symbols every later stage sees, and it had no
test at all: a regression in any of its four checks passed the whole suite and a
green CI, because nothing could reach the loop without a trading terminal.
"""
import argparse
import unittest
from types import SimpleNamespace

from tests._mt5stub import install

install()

import tier1_universe as t1  # noqa: E402


def params(**kw):
    base = dict(min_days=160, max_spread_pct=15.0, max_idle=150, skip_shares=False)
    base.update(kw)
    return argparse.Namespace(**base)


def symbol(mode=0, **kw):
    info = dict(trade_mode=mode, volume_min=0.01, volume_step=0.01,
                trade_contract_size=100_000.0, point=1e-5, digits=5)
    info.update(kw)
    return SimpleNamespace(**info)


class TheFourFilters(unittest.TestCase):
    def test_a_healthy_symbol_is_kept(self):
        self.assertIsNone(t1.rejection(symbol(t1.TRADE_MODE_FULL), 20.0, 300.0, 2.0, params()))

    def test_a_non_tradable_mode_is_refused(self):
        self.assertEqual(t1.rejection(symbol(t1.TRADE_MODE_FULL + 1), 20.0, 300.0, 2.0, params()),
                         "not_tradable")

    def test_a_stale_market_is_refused(self):
        # 151 minutes without a bar is a closed market, not a quiet one.
        self.assertEqual(t1.rejection(symbol(t1.TRADE_MODE_FULL), 151.0, 300.0, 2.0, params()),
                         "market_closed")
        self.assertIsNone(t1.rejection(symbol(t1.TRADE_MODE_FULL), 150.0, 300.0, 2.0, params()))

    def test_a_thin_history_is_refused(self):
        self.assertEqual(t1.rejection(symbol(t1.TRADE_MODE_FULL), 20.0, 159.0, 2.0, params()),
                         "short_history")

    def test_a_wide_spread_is_refused(self):
        self.assertEqual(t1.rejection(symbol(t1.TRADE_MODE_FULL), 20.0, 300.0, 15.1, params()),
                         "too_wide")
        self.assertIsNone(t1.rejection(symbol(t1.TRADE_MODE_FULL), 20.0, 300.0, 15.0, params()))

    def test_the_first_refusal_wins_and_says_which(self):
        # A closed market with a thin history reports the reason a human can act on.
        self.assertEqual(t1.rejection(symbol(0), 999.0, 1.0, 99.0, params()), "not_tradable")

    def test_the_thresholds_are_the_arguments_not_constants(self):
        strict = params(max_spread_pct=0.5, min_days=1000)
        self.assertEqual(t1.rejection(symbol(t1.TRADE_MODE_FULL), 20.0, 300.0, 1.0, strict),
                         "short_history")
        self.assertEqual(t1.rejection(symbol(t1.TRADE_MODE_FULL), 20.0, 2000.0, 1.0, strict),
                         "too_wide")


if __name__ == "__main__":
    unittest.main()

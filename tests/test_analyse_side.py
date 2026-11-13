"""The proposal side must be the side that was tested (#2).

`analyse()` used to derive `current.side` from indicator state regardless of the
side the backtest was told to test: a `sides="long"` run on a bearish last bar
proposed a short. And when `sides="both"` and no rule fired on the present bar,
it fell back to the Donchian channel midpoint, so a family with no current setup
still produced a directional proposal.
"""
from __future__ import annotations

import pathlib
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from _mt5stub import install  # noqa: E402

install()  # must precede marketdata / timemachine_mt5

import marketdata as md          # noqa: E402
import timemachine_mt5 as tm     # noqa: E402


def synth(n: int = 900, seed: int = 7, first: float = 0.00040) -> dict:
    """Drift by `first`, then by `-first`; both sides trigger, the last half reverses."""
    rng = np.random.default_rng(seed)
    drift = np.concatenate([np.full(n // 2, first), np.full(n - n // 2, -first)])
    ret = drift + rng.normal(0, 0.0005, n)
    c = 1.10 * np.exp(np.cumsum(ret))
    o = np.concatenate([[c[0]], c[:-1]])
    pad = np.abs(c) * 0.0004
    return {"o": o, "h": np.maximum(o, c) + pad, "l": np.minimum(o, c) - pad,
            "c": c, "t": np.arange(n) * 900 + 1_700_000_000}


def inside_channel(n: int = 2500, seed: int = 11) -> dict:
    """Oscillating series; the last close sits inside the Donchian channel."""
    rng = np.random.default_rng(seed)
    i = np.arange(n)
    ret = 0.0006 * np.sin(2 * np.pi * i / 14) + rng.normal(0, 0.0012, n)
    c = 1.10 * np.exp(np.cumsum(ret))
    o = np.concatenate([[c[0]], c[:-1]])
    pad = np.abs(c) * 0.0006
    return {"o": o, "h": np.maximum(o, c) + pad, "l": np.minimum(o, c) - pad,
            "c": c, "t": np.arange(n) * 900 + 1_700_000_000}


def tick():
    return SimpleNamespace(bid=1.10, ask=1.1001, time=time.time())


def analyse(bars, sides):
    with mock.patch.object(md, "connect"), \
         mock.patch.object(md, "broker_offset", return_value=0), \
         mock.patch.object(md, "fetch", return_value=bars), \
         mock.patch.object(md.mt5, "symbol_info_tick", return_value=tick()), \
         mock.patch.object(md, "symbol_info", return_value=None), \
         mock.patch.object(md, "account_currency", return_value=""):
        return tm.analyse("FIXTURE", "M15", "donchian", don_n=20, ema_fast=50,
                          ema_slow=200, horizon=32, sides=sides,
                          bars_n=len(bars["c"]), replays=5, permutations=0)


class TheProposalSideIsTheTestedSide(unittest.TestCase):
    def test_long_run_on_a_bearish_bar_proposes_long(self):
        bars = synth()
        ef, es = md.ema(bars["c"], 50), md.ema(bars["c"], 200)
        self.assertLess(ef[-1], es[-1], "fixture must end bearish")

        doc = analyse(bars, sides="long")
        self.assertIsNotNone(doc, "the uptrend half must produce long trades")
        self.assertEqual(doc["current"]["side"], "long")

    def test_short_run_on_a_bullish_bar_proposes_short(self):
        bars = synth(first=-0.00040)  # fall, then rise: the last bars are bullish
        ef, es = md.ema(bars["c"], 50), md.ema(bars["c"], 200)
        self.assertGreater(ef[-1], es[-1], "fixture must end bullish")

        doc = analyse(bars, sides="short")
        self.assertIsNotNone(doc, "the downtrend half must produce short trades")
        self.assertEqual(doc["current"]["side"], "short")

    def test_both_sides_with_no_setup_fires_is_no_proposal(self):
        bars = inside_channel()
        upper, lower = md.donchian(bars["h"], bars["l"], 20)
        self.assertTrue(lower[-1] <= bars["c"][-1] <= upper[-1],
                        "fixture's last close must sit inside the channel")

        self.assertIsNone(analyse(bars, sides="both"),
                          "no current setup must mean no proposal, not a midpoint guess")


if __name__ == "__main__":
    unittest.main()

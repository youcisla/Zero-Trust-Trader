"""The backtest must grade the strategy that is actually proposed.

Tier 2 decides what gets proposed; the Time Machine decides whether it has an
edge. If they disagree about the rule, the significance number describes a
strategy nobody trades — which is what happened before: Tier 2 gated Donchian on
EMA50/200, the backtest had no trend gate at all, charged no spread, and booked
a fixed +rr that the live supervisor never sets.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import tier2_patterns as t2  # noqa: E402
import timemachine_mt5 as tm  # noqa: E402

MIN_BARS = 260  # tier2.scan_symbol needs max(don_n, ema_slow) + 40


def synth(n: int = 900, seed: int = 7) -> dict:
    """Trend up, then down, so both sides can trigger."""
    rng = np.random.default_rng(seed)
    drift = np.concatenate([np.full(n // 2, 0.00040), np.full(n - n // 2, -0.00040)])
    ret = drift + rng.normal(0, 0.0005, n)
    c = 1.10 * np.exp(np.cumsum(ret))
    o = np.concatenate([[c[0]], c[:-1]])
    pad = np.abs(c) * 0.0004
    return {
        "o": o,
        "h": np.maximum(o, c) + pad,
        "l": np.minimum(o, c) - pad,
        "c": c,
        "t": np.arange(n) * 900 + 1_700_000_000,
    }


def osc(n: int = 2500, seed: int = 11, amp: float = 0.0006,
        sigma: float = 0.0012, period: int = 14) -> dict:
    """Oscillating series: every family triggers on it, which the trending
    fixture above does not manage (RSI crossings and pullbacks are rare in a
    one-way drift)."""
    rng = np.random.default_rng(seed)
    i = np.arange(n)
    ret = amp * np.sin(2 * np.pi * i / period) + rng.normal(0, sigma, n)
    c = 1.10 * np.exp(np.cumsum(ret))
    o = np.concatenate([[c[0]], c[:-1]])
    pad = np.abs(c) * 0.0006
    return {"o": o, "h": np.maximum(o, c) + pad, "l": np.minimum(o, c) - pad,
            "c": c, "t": np.arange(n) * 900 + 1_700_000_000}


class TestBacktestMatchesTier2(unittest.TestCase):
    def setUp(self):
        self.bars = synth()

    def test_every_backtest_entry_is_a_tier2_signal(self):
        """The backtest may only open where Tier 2 would signal, on the same side."""
        trades = tm.scan_donchian(self.bars, 20, 1.5, 1.5, 32, "both", 50, 200, 0.0, False)
        checked = [t for t in trades if t["bar"] >= MIN_BARS]
        self.assertGreater(len(checked), 0, "fixture must produce comparable trades")

        for tr in checked:
            i = tr["bar"]
            view = {k: v[: i + 1] for k, v in self.bars.items()}
            sigs = [s for s in t2.scan_symbol(view, 20, 50, 200)
                    if s["strategy"] == "donchian"]
            self.assertTrue(sigs, f"bar {i}: backtest traded where tier2 signals nothing")
            self.assertEqual(sigs[0]["side"], tr["side"], f"bar {i}: side disagreement")

    def test_ema_gate_is_wired(self):
        """Swapping the EMA pair must change which breakouts qualify. If the two
        runs are identical, the trend gate is not applied at all."""
        slow_pair = tm.scan_donchian(self.bars, 20, 1.5, 1.5, 32, "both", 50, 200, 0.0, False)
        fast_pair = tm.scan_donchian(self.bars, 20, 1.5, 1.5, 32, "both", 3, 6, 0.0, False)
        self.assertNotEqual(
            [(t["bar"], t["side"]) for t in slow_pair],
            [(t["bar"], t["side"]) for t in fast_pair],
            "EMA parameters have no effect: the trend gate is missing",
        )

    def test_spread_reduces_return(self):
        free = tm.scan_donchian(self.bars, 20, 1.5, 1.5, 32, "both", 50, 200, 0.0, True)
        costly = tm.scan_donchian(self.bars, 20, 1.5, 1.5, 32, "both", 50, 200, 0.0005, True)
        self.assertGreater(len(free), 0, "fixture must produce trades")
        self.assertLess(sum(t["R"] for t in costly), sum(t["R"] for t in free),
                        "charging a spread must reduce total R")

    def test_exits_go_through_the_live_supervisor(self):
        """Parity by construction: the backtest calls position_manager.next_stop."""
        calls: list[tuple] = []
        real = tm.next_stop

        def spy(*a, **k):
            calls.append(a)
            return real(*a, **k)

        tm.next_stop = spy
        try:
            tm.scan_donchian(self.bars, 20, 1.5, 1.5, 32, "both", 50, 200, 0.0, True)
        finally:
            tm.next_stop = real
        self.assertGreater(len(calls), 0, "backtest must use the live exit function")


class TestLiveExitRules(unittest.TestCase):
    def _bars_around_close(self):
        """Flat bars every 15 min from 16:00 UTC, so only time can close a trade."""
        n = 40
        midnight = 1_700_000_000 - (1_700_000_000 % 86400)
        t = np.array([midnight + 16 * 3600 + i * 900 for i in range(n)])
        c = np.full(n, 1.10)
        return {"o": c.copy(), "h": c + 1e-5, "l": c - 1e-5, "c": c, "t": t}

    def test_trade_closes_at_the_session_end(self):
        """A horizon alone lets a 16:45 entry run to 00:45. The session close is
        a hard exit, so 'no overnight holds' is not just a bar count."""
        bars = self._bars_around_close()
        atr = np.full(len(bars["c"]), 0.0010)
        outcome, _mae, exit_i, _px = tm._resolve_live(
            bars, 0, "long", 1.10, 1.09, 0.0010, atr, 0.0, 32, close_utc="17:00")
        self.assertEqual(outcome, "session")
        self.assertEqual(exit_i, 4, "16:00 + 4 bars of 15 min is exactly 17:00")

    def test_profitable_trailing_exit_counts_as_a_win(self):
        """Regression: live exits never produce a 'target' outcome, so scoring
        by outcome label made every trade a loss and no candidate could pass."""
        self.assertGreater(tm._z_from([{"R": 0.3}, {"R": 0.2}, {"R": -1.0}], 1.5, 0.4), 0.0)
        # a trade that ended green but exited on a trail is still a win
        self.assertGreater(tm._z_from([{"R": 0.1, "outcome": "stop"}] * 20, 1.5, 0.4), 0.0)
        self.assertLess(tm._z_from([{"R": -1.0, "outcome": "stop"}] * 20, 1.5, 0.4), 0.0)



class TestOutcomeBucketsAddUp(unittest.TestCase):
    """Every trade must land in exactly one bucket.

    The history block published target / stop / neither only, so a real scan
    showed occurrences 271 against 248 accounted for and the desk's chief
    reported, correctly, that the components did not sum. session and horizon
    exits were silently missing.
    """

    def test_every_outcome_is_counted_once(self):
        from timemachine_mt5 import _outcome_counts
        trades = ([{"outcome": "target"}] * 2 + [{"outcome": "stop"}] * 3
                  + [{"outcome": "session"}] * 4 + [{"outcome": "horizon"}] * 5
                  + [{"outcome": "neither"}] * 1)
        c = _outcome_counts(trades)
        self.assertEqual(sum(c.values()), len(trades))
        self.assertEqual(c, {"target": 2, "stop": 3, "session": 4, "horizon": 5, "neither": 1})

    def test_an_unknown_or_absent_outcome_is_not_silently_dropped(self):
        from timemachine_mt5 import _outcome_counts
        c = _outcome_counts([{"outcome": "weird"}, {}])
        self.assertEqual(sum(c.values()), 2)
        self.assertEqual(c["neither"], 2)

if __name__ == "__main__":
    unittest.main()


class TestEveryScannerTradesTheExitPolicyItIsGradedOn(unittest.TestCase):
    """The null must trade the same rule as the observation.

    Four of the five families used to book a fixed +rr with no spread while
    `permutation_test` charged the spread and trailed. That made the null
    harder than the observation, so those families beat their own null for
    free: EURNZD/rsi scored p=0.001 with a null mean z of -4.555 while the one
    family that was already matched (donchian) scored p=0.93.
    """

    def setUp(self):
        self.bars = osc()

    def _calls(self, live_exits):
        """Count which resolver each scanner actually reaches."""
        counts = {"live": 0, "free": 0}
        real_live, real_free = tm._resolve_live, tm._resolve

        def live(*a, **k):
            counts["live"] += 1
            return real_live(*a, **k)

        def free(*a, **k):
            counts["free"] += 1
            return real_free(*a, **k)

        tm._resolve_live, tm._resolve = live, free
        try:
            for fn, args in (
                (tm.scan_donchian, (20, 1.5, 1.5, 32, "both")),
                (tm.scan_pullback, (50, 200, 1.5, 1.5, 32, "both")),
                (tm.scan_rsi, (14, 35.0, 65.0, 1.5, 1.5, 32, "both")),
                (tm.scan_macd, (12, 26, 9, 1.5, 1.5, 32, "both")),
                (tm.scan_bollinger, (20, 2.0, 1.5, 1.5, 32, "both")),
            ):
                trades = fn(self.bars, *args, spread=0.0002,
                            live_exits=live_exits, close_utc="17:00")
                self.assertGreater(len(trades), 0,
                                   f"{fn.__name__} produced no trades")
        finally:
            tm._resolve_live, tm._resolve = real_live, real_free
        return counts

    def test_all_five_scanners_use_the_live_resolver(self):
        counts = self._calls(live_exits=True)
        self.assertGreater(counts["live"], 0, "no scanner reached _resolve_live")
        self.assertEqual(counts["free"], 0,
                         "a scanner still resolves frictionless while the null trails")

    def test_frictionless_mode_uses_the_frictionless_resolver(self):
        counts = self._calls(live_exits=False)
        self.assertGreater(counts["free"], 0)
        self.assertEqual(counts["live"], 0)

    def test_spread_is_charged_on_entry(self):
        free = tm.scan_rsi(self.bars, 14, 35.0, 65.0, 1.5, 1.5, 32, "both",
                           spread=0.0, live_exits=False)
        costly = tm.scan_rsi(self.bars, 14, 35.0, 65.0, 1.5, 1.5, 32, "both",
                             spread=0.0020, live_exits=False)
        longs = [t for t in free if t["side"] == "long"]
        self.assertGreater(len(longs), 0, "fixture must produce a long")
        by_bar = {t["bar"]: t for t in costly}
        shifted = [round(by_bar[t["bar"]]["entry"] - t["entry"], 6)
                   for t in longs if t["bar"] in by_bar]
        self.assertTrue(shifted)
        for d in shifted:
            self.assertAlmostEqual(d, 0.0020, places=6,
                                   msg="long entry did not pay the spread")

    def test_null_uses_the_same_fills_and_rounding_as_the_observation(self):
        from unittest import mock

        atr = tm.md.atr(self.bars["h"], self.bars["l"], self.bars["c"], 14)
        i = 40
        row, _ = tm._trade_row(self.bars, i, "long", self.bars["c"][i],
                               1.5 * atr[i], 1.5, 32, atr, 0.0005, False, None)
        with mock.patch.object(tm.random.Random, "randint", return_value=i), \
             mock.patch.object(tm, "_trade_row", wraps=tm._trade_row) as resolve:
            result = tm.permutation_test(self.bars, [row] * 20, 1.5, 1.5, 32,
                                          permutations=1000, spread=0.0005,
                                          live_exits=False)
        self.assertEqual(resolve.call_count, 20000)
        self.assertEqual(result["null_z_mean"], result["actual_z"])

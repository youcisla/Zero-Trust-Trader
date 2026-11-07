"""A frozen weekend tick must not define the broker clock.

Measured 2026-11-07 00:27Z on the demo terminal: EURUSD/GBPUSD/US30/XAUUSD ticks
were all frozen at Friday 23:54:58 server time, so the old single-symbol probe
returned -1800 s (-0.5 h) instead of +7200 s (+2.0 h). With server_now frozen at
Friday's close, bar_age_min() reported ~12 min for every shut instrument (tier 2
scanned frozen Friday bars) and fetch() kept only bars up to Friday 23:30 even
for BTCUSD, which was live and ticking.
"""

from __future__ import annotations

import pathlib
import sys
import time
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from _mt5stub import install  # noqa: E402

install()  # before marketdata: MetaTrader5 is Windows-only, CI has no wheel

import marketdata as md  # noqa: E402


def _mt5(clocks: dict[str, float | None]):
    def tick(symbol):
        t = clocks.get(symbol)
        return types.SimpleNamespace(time=t) if t else None

    return types.SimpleNamespace(symbol_info_tick=tick)


class TestBrokerOffsetIgnoresAFrozenTick(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000.0
        md._LAST_OFFSET = None
        patcher = mock.patch.object(md.time, "time", lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, md, "_LAST_OFFSET", None)

    def test_live_probe_wins_over_a_symbol_frozen_at_the_weekend_close(self):
        """EURUSD stopped 5 h ago; BTCUSD ticks +2 h ahead of UTC right now."""
        frozen = self.now - 5 * 3600
        with mock.patch.object(md, "mt5", _mt5({"EURUSD": frozen, "BTCUSD": self.now + 7200})):
            self.assertEqual(md.broker_offset(), 7200)

    def test_single_frozen_symbol_would_have_returned_a_shrunk_offset(self):
        """The old behaviour, for the record: one frozen tick -> -1800 s."""
        frozen = self.now - 5 * 3600
        with mock.patch.object(md, "mt5", _mt5({"EURUSD": frozen})):
            self.assertNotEqual(md.broker_offset(), -1800,
                                "a stale probe must not define the broker clock")

    def test_all_probes_stale_keeps_the_last_known_offset(self):
        live = self.now + 7200
        with mock.patch.object(md, "mt5", _mt5({"BTCUSD": live})):
            self.assertEqual(md.broker_offset(), 7200)
        stale = self.now - 6 * 3600
        with mock.patch.object(md, "mt5", _mt5({"EURUSD": stale, "BTCUSD": stale})):
            self.assertEqual(md.broker_offset(), 7200,
                             "with no fresh tick the last plausible offset should hold")

    def test_no_history_and_no_fresh_tick_fails_closed_to_utc(self):
        stale = self.now - 6 * 3600
        with mock.patch.object(md, "mt5", _mt5({"EURUSD": stale, "BTCUSD": stale})):
            self.assertEqual(md.broker_offset(), 0,
                             "0 means 'assume UTC', which makes bars look old, not fresh")

    def test_bar_age_skips_a_symbol_that_closed_on_friday(self):
        """The end-to-end consequence: 162 min, not 12 min."""
        last_bar = self.now - 5 * 3600 - 900
        bars = {"tf": "M15", "t": [last_bar]}
        with mock.patch.object(md, "mt5", _mt5({"BTCUSD": self.now + 7200,
                                                "EURUSD": self.now - 5 * 3600})):
            age = md.bar_age_min(bars, md.broker_offset())
        self.assertGreater(age, 45.0, "a Friday close must fail the 45-minute guard")


if __name__ == "__main__":
    unittest.main()

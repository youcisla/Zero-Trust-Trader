"""Ticket 0015: the owner's daily halt (D1), read from trades.csv.

trades.csv has no R column, so these tests pin the R arithmetic as well as the
two caps. The gate must refuse (trip) whenever it cannot read today's rows:
understating the day's loss is the failure the cap exists to prevent.
"""

import csv
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

import run_funnel as rf  # noqa: E402

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
HEADER = "closed,symbol,side,entry,exit,stop,hold_hours\n"


def _csv(path: Path, *rows: str) -> Path:
    path.write_text(HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return path


class TheDailyCap(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _path(self, name="trades.csv") -> Path:
        return self.dir / name

    def test_today_net_loss_at_the_cap_trips(self):
        # long: risk 10, -3R exactly; short: risk 10, -3R exactly.
        path = _csv(self._path(), f"{TODAY} 10:00:00,EURUSD,long,100,70,90,1.0")
        self.assertIs(rf.daily_stop(path)[0], True, rf.daily_stop(path)[1])
        path = _csv(self._path(), f"{TODAY} 10:00:00,EURUSD,short,100,130,110,1.0")
        self.assertIs(rf.daily_stop(path)[0], True, rf.daily_stop(path)[1])

    def test_five_closed_today_trips_even_when_flat(self):
        rows = [f"{TODAY} 1{i}:00:00,EURUSD,long,100,100.5,90,0.1" for i in range(5)]
        tripped, why = rf.daily_stop(_csv(self._path(), *rows))
        self.assertIs(tripped, True, why)
        self.assertIn("5 closed", why)

    def test_below_both_caps_allows(self):
        # four trades, -0.5R each: neither the count nor the loss cap is reached.
        rows = [f"{TODAY} 0{i}:00:00,EURUSD,long,100,95,90,0.1" for i in range(1, 5)]
        tripped, why = rf.daily_stop(_csv(self._path(), *rows))
        self.assertIs(tripped, False, why)
        self.assertIn("-2.00R", why)
        # Wins and losses sum: two -2R and two +1R are -2R, not five or zero.
        rows = [f"{TODAY} 0{i}:00:00,EURUSD,long,100,80,90,0.1" for i in range(2)]
        rows += [f"{TODAY} 0{i}:00:00,EURUSD,long,100,110,90,0.1" for i in range(2)]
        tripped, why = rf.daily_stop(_csv(self._path(), *rows))
        self.assertIs(tripped, False, why)
        self.assertIn("-2.00R", why)

    def test_short_r_has_the_right_sign(self):
        # short from 100 with risk 10, exit 90 -> +1R, not -1R.
        row = f"{TODAY} 10:00:00,EURUSD,short,100,90,110,1.0"
        tripped, why = rf.daily_stop(_csv(self._path(), row))
        self.assertIs(tripped, False, why)
        self.assertIn("+1.00R", why)

    def test_absent_or_empty_allows(self):
        self.assertIs(rf.daily_stop(self._path("missing.csv"))[0], False)
        self.assertIs(rf.daily_stop(_csv(self._path()))[0], False)

    def test_yesterdays_rows_do_not_count_toward_today(self):
        old = "2020-01-01 10:00:00,EURUSD,long,100,10,90,1.0"  # -9R, years ago
        tripped, why = rf.daily_stop(_csv(self._path(), old))
        self.assertIs(tripped, False, why)

    def test_an_unreadable_today_row_refuses(self):
        # Empty stop: risk cannot be computed, so the row could hide any loss.
        row = f"{TODAY} 10:00:00,EURUSD,long,100,70,,1.0"
        tripped, why = rf.daily_stop(_csv(self._path(), row))
        self.assertIs(tripped, True, why)
        self.assertIn("unreadable", why)

    def test_a_zero_risk_row_refuses(self):
        row = f"{TODAY} 10:00:00,EURUSD,long,100,70,100,1.0"
        self.assertIs(rf.daily_stop(_csv(self._path(), row))[0], True)

    def test_an_unreadable_date_refuses(self):
        # A date that cannot be read cannot be proven not to be today's.
        row = "not-a-date,EURUSD,long,100,70,90,1.0"
        tripped, why = rf.daily_stop(_csv(self._path(), row))
        self.assertIs(tripped, True, why)

    def test_non_utf8_bytes_refuse_without_raising(self):
        # A file the decoder cannot read must stand the scan down, not traceback.
        path = self._path()
        path.write_bytes(b"closed,symbol,side,entry,exit,stop,hold_hours\n\xff\xfe\n")
        tripped, why = rf.daily_stop(path)
        self.assertIs(tripped, True, why)
        self.assertIn("unreadable", why)

    def test_an_over_limit_csv_row_refuses(self):
        # csv.Error on a giant field is still "cannot read today" -> refuse.
        limit = csv.field_size_limit()
        row = f"{TODAY} 10:00:00,EURUSD,long,100,70,90,{'9' * (limit + 1)}"
        tripped, why = rf.daily_stop(_csv(self._path(), row))
        self.assertIs(tripped, True, why)
        self.assertIn("unreadable", why)


class TheGateIsWired(unittest.TestCase):
    def test_a_spent_day_refuses_before_the_lock(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _csv(tmp / "trades.csv", f"{TODAY} 10:00:00,EURUSD,long,100,70,90,1.0")
            locked = []
            with mock.patch.object(rf, "HERE", tmp), \
                 mock.patch.object(rf.kill_switch, "MARKER", tmp / "KILL_SWITCH"), \
                 mock.patch.object(rf, "acquire_lock", lambda *a: locked.append(a) or True), \
                 mock.patch.object(rf, "release_lock", lambda *a: None), \
                 mock.patch.object(sys, "argv", ["run_funnel.py"]):
                rc = rf.main()
        self.assertEqual(rc, 0, "a stand-down, not a failure")
        self.assertEqual(locked, [], "the daily cap must refuse before the lock")


if __name__ == "__main__":
    unittest.main()

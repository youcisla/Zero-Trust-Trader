"""SECURITY-02: the opt-in `--only-comment` supervisor filter.

The bridge stamps `magic: 0` on every order, so a magic filter would match
nothing. The supervisor instead filters by the position's `comment` prefix, and
only when asked: with the flag absent every position is managed exactly as
before. These tests use synthetic broker objects; no terminal is imported.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import position_manager as pm
import run_funnel as rf


def _pos(ticket, comment, symbol="FIXTURE"):
    return types.SimpleNamespace(
        ticket=ticket, type=0, symbol=symbol, volume=0.02,
        price_open=100.0, sl=90.0, price_current=105.0, profit=1.0, tp=0.0,
        comment=comment,
    )


class TestCommentFilter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for patcher in (
            mock.patch.object(pm, "HERE", pathlib.Path(self.tmp.name)),
            mock.patch.object(pm.kill_switch, "MARKER",
                              pathlib.Path(self.tmp.name) / "KILL_SWITCH"),
            mock.patch.object(rf, "EXPECTED_LOGIN", 12345),
            mock.patch.dict(os.environ, {"MT5_EXPECTED_LOGIN": "12345"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.account = types.SimpleNamespace(
            login=12345, server="Synthetic-Demo", trade_mode=0,
            balance=100, equity=100, currency="TEST", leverage=30)
        self.sent = []
        self.positions = ()

        def positions_get(**kw):
            ticket = kw.get("ticket")
            if ticket is None:
                return self.positions
            return tuple(p for p in self.positions if p.ticket == ticket)

        def order_send(request):
            self.sent.append(request)
            return types.SimpleNamespace(retcode=10009)

        self.mt5 = types.SimpleNamespace(
            terminal_info=lambda: types.SimpleNamespace(connected=True),
            account_info=lambda: self.account,
            positions_get=positions_get,
            order_send=order_send,
            symbol_info_tick=lambda symbol: types.SimpleNamespace(
                bid=105.0, ask=105.1, time=time.time()),
            symbol_info=lambda symbol: types.SimpleNamespace(
                trade_tick_size=0.25, point=0.01,
                trade_stops_level=0, trade_freeze_level=0),
            last_error=lambda: (0, "stub"),
            ORDER_FILLING_IOC=1, ORDER_FILLING_FOK=0, TRADE_ACTION_DEAL=1,
            TRADE_ACTION_SLTP=6, ORDER_TYPE_SELL=1, ORDER_TYPE_BUY=0,
            TRADE_RETCODE_DONE=10009, TRADE_RETCODE_INVALID_FILL=10030,
        )
        import marketdata as md
        for patcher in (
            mock.patch.object(md, "broker_offset", return_value=0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, only_comment, apply=False, due=False):
        patches = [
            mock.patch.dict(sys.modules, {"MetaTrader5": self.mt5}),
            mock.patch.object(pm, "ensure_mt5"),
            mock.patch.object(pm, "_atr", return_value=(1.0, 0.1)),
        ]
        if due:
            patches.append(mock.patch.object(pm, "close_due", return_value=True))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return pm.run_once(pm.Cfg(), apply,
                           "00:00" if due else None, only_comment)

    # --- opt-in: tagged managed, untagged/empty/None skipped ---------------

    def test_tagged_is_managed_and_others_are_skipped(self):
        self.positions = (_pos(1, "ztt-eurusd"), _pos(2, "manual"),
                          _pos(3, ""), _pos(4, None))
        doc = self._run(only_comment="ztt")

        managed = {d["ticket"]: d["managed"] for d in doc["positions"]}
        self.assertEqual(managed, {1: True, 2: False, 3: False, 4: False})
        self.assertEqual(doc["positions"][1]["reason"], "not managed: comment manual")
        # Exactly one trailing action, for the tagged position.
        self.assertEqual(len(doc["actions"]), 1)
        self.assertIn("would set FIXTURE", doc["actions"][0])

    def test_untagged_is_never_sent_to_the_broker(self):
        self.positions = (_pos(1, "ztt-eurusd"), _pos(2, "manual"))
        doc = self._run(only_comment="ztt", apply=True)

        self.assertTrue(doc["ok"], doc.get("error"))
        self.assertEqual([r["position"] for r in self.sent], [1])

    def test_session_close_skips_untagged(self):
        self.positions = (_pos(1, "ztt-eurusd"), _pos(2, "manual"))
        doc = self._run(only_comment="ztt", due=True)

        self.assertEqual(len(doc["actions"]), 1)
        self.assertIn("would close 1", doc["actions"][0])
        self.assertNotIn("manual", " ".join(doc["actions"]))

    # --- default: the flag absent changes nothing --------------------------

    def test_absent_flag_manages_all(self):
        self.positions = (_pos(1, "ztt-eurusd"), _pos(2, "manual"),
                          _pos(3, ""), _pos(4, None))
        doc = self._run(only_comment=None)

        self.assertTrue(all(d["managed"] for d in doc["positions"]))
        self.assertEqual(len(doc["actions"]), 4)

    def test_absent_flag_closes_all(self):
        self.positions = (_pos(1, "ztt-eurusd"), _pos(2, "manual"))
        doc = self._run(only_comment=None, due=True)

        self.assertEqual(len(doc["actions"]), 2)
        self.assertTrue(all("would close" in a for a in doc["actions"]))


if __name__ == "__main__":
    unittest.main()

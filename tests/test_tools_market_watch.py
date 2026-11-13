"""The read-only tools must leave the user's Market Watch exactly as they found it.

`marketdata.fetch()` records what it selects and releases it; a tool that calls
`mt5.symbol_select()` itself used to leak every symbol it touched.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

TIMEFRAMES = {"M15": 15, "M30": 30, "H1": 16385, "H4": 16388, "D1": 16408}


def _install_stateful_stub(visible):
    """A terminal stand-in whose Market Watch is a set the test can inspect."""
    state = {"visible": set(visible)}
    mod = types.ModuleType("MetaTrader5")
    for name, tf in TIMEFRAMES.items():
        setattr(mod, f"TIMEFRAME_{name}", tf)
    mod.initialize = lambda *a, **k: True
    mod.shutdown = lambda *a, **k: None
    mod.last_error = lambda: (0, "stub")
    mod.ORDER_TYPE_BUY = 0

    def symbol_select(symbol, enable=True):
        if enable:
            state["visible"].add(symbol)
        else:
            state["visible"].discard(symbol)
        return True

    def symbol_info(symbol):
        return types.SimpleNamespace(
            visible=symbol in state["visible"],
            point=0.0001,
            digits=5,
            trade_tick_value=1.0,
            trade_tick_size=0.0001,
            volume_min=0.01,
            trade_contract_size=100000.0,
        )

    now = time.time()
    bars = [
        {
            "time": int(now - 900 * (30 - i)),
            "open": 1.0,
            "high": 1.001,
            "low": 0.999,
            "close": 1.0,
        }
        for i in range(30)
    ]
    mod.symbol_select = symbol_select
    mod.symbol_info = symbol_info
    mod.symbol_info_tick = lambda s: types.SimpleNamespace(
        bid=1.0, ask=1.0002, time=time.time()
    )
    mod.copy_rates_from_pos = lambda *a, **k: bars
    mod.account_info = lambda: types.SimpleNamespace(leverage=30, currency="EUR")
    mod.order_calc_margin = lambda *a, **k: 33.4
    sys.modules["MetaTrader5"] = mod
    return mod, state


class TestMarketWatch(unittest.TestCase):
    def setUp(self):
        self._saved = sys.modules.get("MetaTrader5")
        self.mod, self.state = _install_stateful_stub({"EURUSD"})
        import marketdata

        self.md = marketdata
        self.md.mt5 = self.mod
        self.md._AUTO_SELECTED.clear()

    def tearDown(self):
        if self._saved is not None:
            sys.modules["MetaTrader5"] = self._saved

    def test_vol_dispersion_releases_only_what_it_selected(self):
        import tools.vol_dispersion as vd

        vd.mt5 = self.mod
        with tempfile.TemporaryDirectory() as tmp:
            universe = Path(tmp) / "liquid_universe.json"
            universe.write_text(
                json.dumps({"symbols": [{"symbol": "EURUSD"}, {"symbol": "AAA"}]}),
                encoding="utf-8",
            )
            vd.UNIVERSE = universe
            with contextlib.redirect_stdout(io.StringIO()):
                vd.main([])
        self.assertEqual(self.state["visible"], {"EURUSD"})

    def test_broker_math_releases_only_what_it_selected(self):
        import tools.broker_math as bm

        bm.mt5 = self.mod
        with contextlib.redirect_stdout(io.StringIO()):
            bm.main(["EURUSD", "AAA"])
        self.assertEqual(self.state["visible"], {"EURUSD"})


if __name__ == "__main__":
    unittest.main()

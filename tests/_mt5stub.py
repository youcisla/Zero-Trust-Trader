"""Minimal MetaTrader5 stub so the pure-math modules import off-Windows.

MetaTrader5 is a Windows-only binary package, so CI cannot install it. But the
parts most likely to be wrong are pure numpy: rsi(), macd(), atr(), bollinger(),
donchian(), bonferroni_z(). Two real bugs lived in exactly that code (RSI
returning 100 on a flat series, MACD's signal line silently all-NaN), so those
functions must be testable in CI rather than skipped.

Import this BEFORE marketdata.
"""

from __future__ import annotations

import sys
import types

TIMEFRAMES = {"M15": 15, "M30": 30, "H1": 1, "H4": 4, "D1": 24}


def install() -> types.ModuleType:
    mod = types.ModuleType("MetaTrader5")
    for name, tf in TIMEFRAMES.items():
        setattr(mod, f"TIMEFRAME_{name}", tf)
    for attr in ("initialize", "shutdown", "symbol_select", "copy_rates_from_pos",
                 "symbol_info", "symbol_info_tick", "positions_get", "orders_get",
                 "history_deals_get", "account_info", "terminal_info"):
        setattr(mod, attr, lambda *a, **k: None)
    mod.last_error = lambda: (0, "stub")
    sys.modules["MetaTrader5"] = mod
    return mod

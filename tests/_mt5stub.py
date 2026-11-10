"""Minimal MetaTrader5 stub so the pure-math modules import off-Windows.

MetaTrader5 is a Windows-only binary package, so CI cannot install it. But the
parts most likely to be wrong are pure numpy: rsi(), macd(), atr(), bollinger(),
donchian(), bonferroni_z(). Two real bugs lived in exactly that code (RSI
returning 100 on a flat series, MACD's signal line silently all-NaN), so those
functions must be testable in CI rather than skipped.

The constants are the **real** ones (H1 is 16385, not 1). The first version of
this file used the timeframe's hour count, which made the stub's H1 collide with
the real M1 - harmless only while every data call returned None, and a trap the
moment anything branches on a timeframe. `initialize()` returns True because the
real one does; a stub that fails to initialise makes the happy path of
`marketdata.connect()` unreachable.

Data calls return None by default: a test that needs bars supplies them. Import
this BEFORE marketdata.
"""

from __future__ import annotations

import sys
import types

# The published constants, as the terminal reports them.
TIMEFRAMES = {"M1": 1, "M15": 15, "M30": 30, "H1": 16385, "H4": 16388, "D1": 16408}


def install() -> types.ModuleType:
    mod = types.ModuleType("MetaTrader5")
    for name, tf in TIMEFRAMES.items():
        setattr(mod, f"TIMEFRAME_{name}", tf)
    for attr in ("symbol_select", "copy_rates_from_pos", "symbol_info",
                 "symbol_info_tick", "positions_get", "orders_get",
                 "history_deals_get", "account_info", "terminal_info",
                 "order_send", "order_calc_margin", "symbols_get"):
        setattr(mod, attr, lambda *a, **k: None)
    mod.initialize = lambda *a, **k: True
    mod.shutdown = lambda *a, **k: None
    mod.last_error = lambda: (0, "stub")
    # Order vocabulary, so a broker-path test can name the constants instead of
    # inventing its own numbers.
    mod.ORDER_FILLING_IOC, mod.ORDER_FILLING_FOK = 1, 0
    mod.TRADE_ACTION_DEAL, mod.TRADE_ACTION_SLTP = 1, 6
    mod.ORDER_TYPE_BUY, mod.ORDER_TYPE_SELL = 0, 1
    mod.TRADE_RETCODE_DONE, mod.TRADE_RETCODE_INVALID_FILL = 10009, 10030
    sys.modules["MetaTrader5"] = mod
    return mod

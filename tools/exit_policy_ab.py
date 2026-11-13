#!/usr/bin/env python
"""A/B the two exit policies on identical bars.

`timemachine_mt5.analyse()` hardwires `live_exits=True`, which means the
supervisor's rules: a 1.5xATR stop, trailing and break-even moves, a session
close, and a horizon. It has **no take-profit** - deliberately, because
`position_manager` never sets one (it only preserves whatever `tp` the opening
order carried).

So every published result is "trail only". That is a policy, not a fact about
the market, and nobody chose it: an order placed through the bridge may carry a
tp at rr x risk, which the bridge supports even though the order path was
removed from this tree. This script
measures the difference on the same bars, so the choice is made with numbers.

    python tools/exit_policy_ab.py BTCUSD BCHUSD

Read-only: fetches bars, computes, prints. Places nothing.
"""
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(os.environ.get("ZTT_ROOT", ".")).resolve()
sys.path.insert(0, str(ROOT))

import MetaTrader5 as mt5  # noqa: E402
import marketdata as md  # noqa: E402
import timemachine_mt5 as tm  # noqa: E402

RR = 1.5
ATR_K = 1.5
# 32, not HORIZON_DEFAULT["M15"] (96): tier3_deep's --horizon defaults to 32 and
# that is what every published result used. analyse() only reaches for
# HORIZON_DEFAULT when horizon is None, so a caller that omits it measures a
# 24-hour hold while the pipeline measures an 8-hour one.
HORIZON = 32
BARS = 15000


def summarise(trades, rr):
    if not trades:
        return None
    rs = [t["R"] for t in trades]
    cnt = {}
    for t in trades:
        cnt[t["outcome"]] = cnt.get(t["outcome"], 0) + 1
    return {
        "n": len(trades),
        "win": sum(1 for r in rs if r > 0) / len(rs),
        "avgR": statistics.mean(rs),
        "target": cnt.get("target", 0),
        "stop": cnt.get("stop", 0),
        "session": cnt.get("session", 0),
        "horizon": cnt.get("horizon", 0),
        "maxDD": tm.max_drawdown(rs),
    }


def main(symbols):
    md.connect()
    print(f"{'symbol':<9}{'policy':<12}{'n':>5}{'win':>7}{'avgR':>8}"
          f"{'target':>7}{'stop':>6}{'sess':>6}{'horiz':>6}{'maxDD':>9}")
    out = []
    offset = md.broker_offset()
    for sym in symbols:
        # Select FIRST, then read the quote: a symbol outside Market Watch has
        # no tick, and tick_spread refuses rather than measuring a free market.
        if not mt5.symbol_select(sym, True):
            print(f"{sym}: cannot select")
            continue
        bars = md.fetch(sym, "M15", BARS, offset)
        if not bars or len(bars["c"]) < 500:
            print(f"{sym}: no usable bars")
            continue
        # The same validated ask-bid the live path charges, never a raw field.
        spread_px = md.tick_spread(mt5.symbol_info_tick(sym), offset)
        if spread_px is None:
            print(f"{sym}: no validated spread")
            continue
        for label, live in (("trail only", True), ("trail + rr TP", False)):
            trades = tm.scan_donchian(bars, 20, ATR_K, RR, HORIZON, "both",
                                      spread=spread_px, live_exits=live,
                                      close_utc="22:00")
            s = summarise(trades, RR)
            if s is None:
                print(f"{sym:<9}{label:<12} no setups fired")
                continue
            print(f"{sym:<9}{label:<12}{s['n']:>5}{s['win']*100:>6.1f}%"
                  f"{s['avgR']:>8.3f}{s['target']:>7}{s['stop']:>6}"
                  f"{s['session']:>6}{s['horizon']:>6}{s['maxDD']:>9.1f}")
            out.append({"symbol": sym, "policy": label, **s})
        mt5.symbol_select(sym, False)
    if len(out) >= 2:
        print("\ndelta (trail+TP minus trail only), same bars, same stops:")
        for i in range(0, len(out) - 1, 2):
            a, b = out[i], out[i + 1]
            print(f"  {a['symbol']:<9} avgR {a['avgR']:+.3f} -> {b['avgR']:+.3f} "
                  f"({b['avgR']-a['avgR']:+.3f})   win {a['win']*100:.1f}% -> "
                  f"{b['win']*100:.1f}%   target hits {a['target']} -> {b['target']}")


if __name__ == "__main__":
    args = sys.argv[1:] or ["BTCUSD", "BCHUSD"]
    main(args)

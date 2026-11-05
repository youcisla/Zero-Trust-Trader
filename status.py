"""One-command system status.

    python status.py

Safe to run any time: reads state, places nothing, toggles nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path(__file__).resolve().parent
if WORK.exists():
    HERE = WORK


def main() -> int:
    print("=== MT5 terminal ===")
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            print(f"  terminal not reachable: {mt5.last_error()}")
        else:
            t, a = mt5.terminal_info(), mt5.account_info()
            pos = mt5.positions_get() or []
            print(f"  AutoTrading : {'ON  <-- UNSAFE' if t.trade_allowed else 'off'}")
            print(f"  account     : {a.login} {a.server} ({'DEMO' if a.trade_mode != 0 else '?'})")
            print(f"  balance     : {a.balance:.2f} {a.currency}   equity {a.equity:.2f}")
            print(f"  positions   : {len(pos)}   orders {len(mt5.orders_get() or [])}")
            print(f"  market watch: {sum(1 for s in mt5.symbols_get() if s.visible)} symbols")
            mt5.shutdown()
    except Exception as e:
        print(f"  unavailable: {e}")

    print("\n=== last 3 funnel runs ===")
    log = HERE / "funnel_history.log"
    lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()][-3:] if log.exists() else []
    for ln in lines:
        print(f"  {ln[:150]}")

    print("\n=== trials accounting ===")
    try:
        import trials_total
        legacy, logged = trials_total.total()
        print(f"  total {legacy + logged}  (legacy {legacy} + logged {logged})")
        print(f"  -> botlab --trials {legacy + logged}")
    except Exception as e:
        print(f"  unavailable: {e}")

    print("\n=== newest verdict ===")
    t3 = HERE / "tier3_results.json"
    if t3.exists():
        doc = json.loads(t3.read_text())
        state = "WINNER " + ",".join(doc.get("survivors", [])) if not doc.get("stood_down") else "STAND_DOWN"
        print(f"  {doc.get('generated_utc')}  {state}  "
              f"({len(doc.get('results', []))} tested, {doc.get('trials_declared')} trials, "
              f"z bar {doc.get('z_crit_corrected')})")
    else:
        print("  no results yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Prove the order path works end to end — once, by hand, on demo.

Run:  python manual_e2e_test.py

What this does
--------------
 1. refuses unless the account is DEMO and AutoTrading is ON
 2. shows the exact 0.01-lot order and waits for you to type EXECUTE
 3. sends it, confirms the position exists, closes it, confirms flat
 4. asks you to switch AutoTrading back off and verifies that too

What it cannot do
-----------------
AutoTrading is a terminal GUI toggle. The MT5 Python API can read it
(`terminal_info().trade_allowed`) but cannot flip it, so this script begins and
ends by asking you to click the toolbar button. It also does not speak the MCP
transport — the bridge's *policy* code is exercised in-process through
`check_preflight_limits`, exactly as `policy_check.py` replays it. If you want
the transport itself exercised, start the bridge server and call its tool as a
client; this script covers the terminal side.

Nothing here runs in CI: it places a real order on a demo account.
"""

from __future__ import annotations

import sys
from decimal import Decimal

import MetaTrader5 as mt5
from mt5_mcp.config import load_config
from mt5_mcp.policy.preflight import PreflightInputs, check_preflight_limits
from mt5_mcp.types import OrderRequest

SYMBOL = "EURUSD"
VOLUME = 0.01
MAGIC = 20261105
DEVIATION = 20


def connect_or_die() -> tuple[object, object]:
    if not mt5.initialize():
        sys.exit(f"initialize() failed: {mt5.last_error()}")
    acct, term = mt5.account_info(), mt5.terminal_info()
    if acct is None or term is None:
        sys.exit("no account/terminal info — is the terminal logged in?")

    print(f"  account : {acct.login} on {acct.server}")
    print(f"  equity  : {acct.equity} {acct.currency}")
    print(f"  mode    : {acct.trade_mode} (0 = demo)")

    if acct.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        sys.exit("REFUSING: this is not a demo account.")
    if "demo" not in (acct.server or "").lower():
        sys.exit(f"REFUSING: server '{acct.server}' does not look like a demo server.")
    return acct, term


def require_autotrading(term) -> None:
    if term.trade_allowed:
        return
    print("\nAutoTrading is OFF.")
    print("Click the AutoTrading button in the MT5 toolbar, then run this again.")
    print("This script cannot flip it for you: it is a GUI toggle, not an API flag.")
    sys.exit(3)


def open_position() -> int:
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        sys.exit(f"no tick for {SYMBOL}")
    ask = Decimal(str(tick.ask))

    try:
        cfg = load_config()
    except Exception as e:
        sys.exit(f"cannot load the bridge config: {e}")

    refusal = check_preflight_limits(
        "place_order",
        OrderRequest(symbol=SYMBOL, side="buy", type="market", volume=str(VOLUME)),
        PreflightInputs(notional=Decimal(str(VOLUME)) * ask),
        cfg,
    )
    if refusal is not None:
        sys.exit(f"REFUSED by bridge policy: {refusal.code} {getattr(refusal, 'detail', '')}")

    print(f"\n  about to send: BUY {VOLUME} {SYMBOL} @ market (ask {ask})")
    if input("  type EXECUTE to send it: ").strip() != "EXECUTE":
        sys.exit("cancelled — nothing sent")

    res = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": VOLUME,
        "type": mt5.ORDER_TYPE_BUY,
        "price": float(tick.ask),
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": "manual-e2e",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    })
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        sys.exit(f"order failed: {getattr(res, 'retcode', None)} {mt5.last_error()}")
    print(f"  filled: ticket {res.order} at {res.price}")
    return int(res.order)


def close_position(ticket: int) -> None:
    positions = [p for p in (mt5.positions_get() or []) if p.ticket == ticket]
    if not positions:
        sys.exit(f"position {ticket} not found — did something else close it?")
    pos = positions[0]
    tick = mt5.symbol_info_tick(pos.symbol)
    res = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "type": mt5.ORDER_TYPE_SELL,
        "position": pos.ticket,
        "price": float(tick.bid),
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": "manual-e2e close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    })
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        sys.exit(f"close failed: {getattr(res, 'retcode', None)} — close it by hand now")
    print(f"  closed: ticket {pos.ticket}")


def main() -> int:
    print("Manual end-to-end check — nothing is sent until you type EXECUTE.\n")
    acct, term = connect_or_die()

    open_now = mt5.positions_get() or []
    if open_now:
        sys.exit(f"REFUSING: {len(open_now)} position(s) already open — flatten them first")

    require_autotrading(term)

    ticket = open_position()
    close_position(ticket)

    left = mt5.positions_get() or []
    if left:
        print(f"! {len(left)} position(s) still open — close them by hand")
    else:
        print("  flat: confirmed")

    print("\nNow click AutoTrading OFF in the toolbar.")
    input("  press Enter once it is off: ")
    if mt5.terminal_info().trade_allowed:
        print("! AutoTrading is still ON — turn it off before you walk away")
        return 1

    print("\nOK — order path verified, account flat, AutoTrading off.")
    print(f"  equity {mt5.account_info().equity}")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        mt5.shutdown()
    sys.exit(code)

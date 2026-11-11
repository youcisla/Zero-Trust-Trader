"""Everything the broker knows about a 50 EUR account, read from the terminal.

No web research, no broker PDF: MetaTrader5 exposes the contract spec
(volume_min, contract size, tick value, margin rate, spread) and
order_calc_margin() returns the exact margin the server would take. The ATR
comes from the same M15 bars the funnel trades on, so "spread as a fraction of
the move you are trying to capture" is measured, not estimated.

Read-only. order_calc_margin is a calculation, not an order.

Run from the repo root:  python tools/broker_math.py [symbol ...]
Numbers and what they mean: docs/SIZE-AND-RUIN.md
"""

from __future__ import annotations

import sys

import MetaTrader5 as mt5  # noqa: E402  (needs marketdata's stub-free import order)

import marketdata as md

# ponytail: account size is the question being asked, not a constant of the system
ACCOUNT_EUR = 50.0
TARGET_MULT = 20.0
DAYS = 21
# Leverage is 1:30 on this server (AGENTS.md section 10); no constant here.
DEFAULT = ("EURUSD", "XAUUSD", "US30", "BTCUSD")


def fmt(x, n=2, dash="—"):
    return dash if x is None else f"{x:,.{n}f}"


def main(argv: list[str]) -> int:
    md.connect()                # marketdata owns the terminal connection
    acc = mt5.account_info()
    leverage = acc.leverage if acc else None
    print(f"account {ACCOUNT_EUR:.0f} {acc.currency if acc else 'EUR'} · "
          f"server leverage 1:{leverage} · window {DAYS} days · target {TARGET_MULT:.0f}x\n")

    g = TARGET_MULT ** (1 / DAYS) - 1
    rows = []
    for sym in (argv or list(DEFAULT)):
        if not mt5.symbol_select(sym, True):
            print(f"{sym:<9} not on this server")
            continue
        si, tk = mt5.symbol_info(sym), mt5.symbol_info_tick(sym)
        bars = md.fetch(sym, "M15", 400)
        if not (si and tk and bars):
            print(f"{sym:<9} no spec/tick/bars — skipped")
            continue
        atr = float(md.atr(bars["h"], bars["l"], bars["c"], 14)[-1])
        vm = si.volume_min
        margin = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, sym, vm, tk.ask) or 0.0
        point_value = si.trade_tick_value * (si.point / si.trade_tick_size) * vm
        pips_per_point = 1.0
        if sym.endswith("USD") and si.digits >= 5:
            pips_per_point = 0.1                       # 5-digit FX: 10 points = 1 pip
        rows.append(dict(sym=sym, vm=vm, margin=margin, pv=point_value, atr=atr,
                         spread=tk.ask - tk.bid, point=si.point, ppp=pips_per_point,
                         contract=si.trade_contract_size, digits=si.digits))

    print(f"{'symbol':<9} {'min lot':>7} {'contract':>9} {'margin@min':>10} {'afford?':>8} "
          f"{'spread':>8} {'M15 ATR':>9} {'spread/ATR':>10}")
    for r in rows:
        afford = "yes" if r["margin"] <= ACCOUNT_EUR else "NO"
        print(f"{r['sym']:<9} {r['vm']:>7.2f} {r['contract']:>9.0f} {r['margin']:>9.2f}€ "
              f"{afford:>8} {r['spread']/r['point']:>6.0f}pt {r['atr']:>9.5f} "
              f"{r['spread']/r['atr']*100 if r['atr'] else 0:>9.1f}%")

    print(f"\n{'symbol':<9} {'€/point':>8} {'free margin':>12} {'stop-out':>16} "
          f"{'pts/day (flat)':>15} {'pts day-1':>10}")
    for r in rows:
        free = ACCOUNT_EUR - r["margin"]
        if r["pv"] <= 0:
            continue
        if free <= 0:
            print(f"{r['sym']:<9} {r['pv']:>8.4f} {'—':>12} "
                  f"{'CANNOT OPEN on 50 EUR':>16} {'—':>15} {'—':>10}")
            continue
        so_pts = free / r["pv"]
        need = (ACCOUNT_EUR * (TARGET_MULT - 1) / DAYS) / r["pv"]
        d1 = (ACCOUNT_EUR * g) / r["pv"]
        print(f"{r['sym']:<9} {r['pv']:>8.4f} {free:>11.2f}€ "
              f"{so_pts:>9,.0f}pt = {so_pts*r['ppp']:>5,.0f}pip {need:>14,.0f} {d1:>10,.1f}")

    print(f"\nthe invariant: profit_needed / ruin_distance = {TARGET_MULT-1:.0f}x at every size, "
          f"because both scale with 1/lots")
    print(f"so each day must bank {((TARGET_MULT-1)/DAYS):.2f}x the distance between the "
          f"account and zero")
    md.release_auto_selected()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

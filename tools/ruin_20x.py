"""Can the funnel's own best measured edge turn 50 EUR into 1000 EUR in a month?

20x in 21 trading days is +15.2% per day compounded. This asks the only
question that matters with numbers we already have: given the best candidate
this system has actually deep-tested (XAUUSD/bollinger: n=458, win 36.0%,
avg R +0.102, max drawdown -21.3R), what is the probability of reaching 20x
before ruin, as a function of how much of the account is risked per trade?

Model, stated so it can be attacked:
  - one trade = +W or -L in R multiples, p = win rate (bimodal approximation
    of the real exit distribution: no fixed target, trailing stop)
  - the account compounds; each trade risks `f` of current equity per 1R
  - ruin = equity <= 1% of start (a 99% drawdown is over; MT5 stops you first)
  - 3 trades/day x 21 days = 63 trades
  - zero cost is assumed, which flatters the result: spread is already inside
    the measured avg R, but slippage on the exit is not.

Run:  python tools/ruin_20x.py      (pure stdlib, no terminal)
The table this prints is in docs/SIZE-AND-RUIN.md
"""

from __future__ import annotations

import random

TARGET_MULT = 20.0     # 50 -> 1000 EUR
DAYS = 21
TRADES_PER_DAY = 3
P_WIN = 0.3603         # measured, tier3 XAUUSD/bollinger
AVG_R = 0.102          # measured
WIN_R, LOSS_R = 1.5, 0.70   # chosen so p*W + (1-p)*L == AVG_R
RUIN_AT = 0.01


def simulate(risk: float, paths: int = 20000, seed: int = 7,
             p_win: float = P_WIN, win_r: float = WIN_R, loss_r: float = LOSS_R) -> dict:
    rng = random.Random(seed)
    n = DAYS * TRADES_PER_DAY
    hit = ruined = 0
    finals: list[float] = []
    for _ in range(paths):
        eq = 1.0
        for _ in range(n):
            r = win_r if rng.random() < p_win else -loss_r
            eq *= 1.0 + r * risk
            if eq <= RUIN_AT:
                ruined += 1
                eq = RUIN_AT
                break
            if eq >= TARGET_MULT:
                hit += 1
                break
        finals.append(eq)
    finals.sort()
    return {
        "risk": risk,
        "p_20x": hit / paths,
        "p_ruin": ruined / paths,
        "median_final": finals[len(finals) // 2],
        "p05": finals[int(paths * 0.05)],
        "p95": finals[int(paths * 0.95)],
    }


if __name__ == "__main__":
    assert abs(P_WIN * WIN_R - (1 - P_WIN) * LOSS_R - AVG_R) < 0.01, "R model does not reproduce avg R"
    print(f"edge: {P_WIN:.3f} win @ +{WIN_R}R / {(1-P_WIN):.3f} loss @ -{LOSS_R}R "
          f"-> {P_WIN*WIN_R-(1-P_WIN)*LOSS_R:+.3f}R per trade")
    print(f"need: x{TARGET_MULT:.0f} in {DAYS} days = {(TARGET_MULT**(1/DAYS)-1)*100:+.1f}%/day compounded\n")
    print(f"{'risk/trade':>10} {'P(20x)':>8} {'P(ruin)':>8} {'median end':>11} {'p05':>7} {'p95':>7}")
    for f in (0.01, 0.02, 0.05, 0.10, 0.20, 0.35, 0.50):
        s = simulate(f)
        print(f"{s['risk']*100:>9.0f}% {s['p_20x']*100:>7.2f}% {s['p_ruin']*100:>7.2f}% "
              f"{s['median_final']:>10.2f}x {s['p05']:>6.2f}x {s['p95']:>6.2f}x")
    print("\nzero-edge control (p=0.5, +1R/-1R), same 63 trades:")
    for f in (0.05, 0.20):
        s = simulate(f, p_win=0.5, win_r=1.0, loss_r=1.0)
        print(f"{s['risk']*100:>9.0f}% {s['p_20x']*100:>7.2f}% {s['p_ruin']*100:>7.2f}% "
              f"{s['median_final']:>10.2f}x")

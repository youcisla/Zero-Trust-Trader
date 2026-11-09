#!/usr/bin/env python3
"""Time Machine — MT5 adaptation, multi-instrument / multi-timeframe.

Port of trader-timemachine's methodology (day-trader-skill, MIT) from Yahoo
5-minute US-equity bars to MT5 bars of any timeframe.

Kept from the original:
  * the slot pool: each bar becomes (o,h,l,c) ratios to the previous close,
    bucketed by position within the natural cycle (hour of day for H1, hour//4
    for H4, weekday for D1, half-hour slot for M30/M15).
  * stop is checked before target inside every bar; a bar touching both counts
    as the stop (conservative).
  * outcomes target / stop / session / horizon / neither, R = +rr / -1 / mark-to-market.
  * the history leg: the same rule on every past occurrence, real bars.
  * the verdict: target share of decided trades vs break-even 1/(1+R).

Added:
  * z-score of the win rate against break-even, and a one-sample t on mean R.
    Exactly one of them gates. The z measures a proportion against a break-even
    that only exists when a fixed +rr target is booked, so under the live exit
    policy (trail, break-even, session close, no target) it degenerates into
    "lost slower than random". `use_t` switches the gate to the t, which needs
    no target — see significance().
  * Bonferroni-corrected critical value, because scanning N symbols and keeping
    the best is exactly what inflates a nominal 1.96 into noise.

Honesty rules: no order placement, no sizing, no prediction. Never round a coin
flip up into an edge.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from datetime import datetime, timezone

import marketdata as md

# The live supervisor owns the exit policy. Import it rather than restating it:
# a backtest that re-implements trailing/break-even measures a strategy nobody
# trades, which is exactly the mismatch this module used to have.
from position_manager import Cfg, next_stop

CYCLE = {"M15": 96, "M30": 48, "H1": 24, "H4": 6, "D1": 5}
HORIZON_DEFAULT = {"M15": 96, "M30": 48, "H1": 24, "H4": 12, "D1": 10}


# ---------------------------------------------------------------- helpers

def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF by bisection on math.erf. Stdlib only."""
    lo, hi = -10.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if 0.5 * (1.0 + math.erf(mid / math.sqrt(2.0))) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def bonferroni_z(n_tests: int, alpha: float = 0.05) -> float:
    """Two-sided critical z for n_tests independent comparisons."""
    n = max(1, n_tests)
    return round(norm_ppf(1.0 - alpha / (2.0 * n)), 3)


def slot_of(t_server: float, tf: str) -> int:
    """Position within the natural cycle, from the bar's own server timestamp."""
    d = datetime.fromtimestamp(t_server, timezone.utc)
    if tf == "D1":
        return d.weekday()
    if tf == "H4":
        return d.hour // 4
    if tf == "H1":
        return d.hour
    if tf == "M30":
        return d.hour * 2 + d.minute // 30
    return d.hour * 4 + d.minute // 15


# ---------------------------------------------------------------- resolve + scans

def _resolve(bars, i, side, entry, stop, target, risk, horizon):
    """Walk forward bar by bar. Stop before target. Returns (outcome, mae_R, exit_i)."""
    hi_a, lo_a = bars["h"], bars["l"]
    n = len(hi_a)
    j, mae, outcome = i + 1, 0.0, None
    while j < n and j <= i + horizon:
        hi, lo = hi_a[j], lo_a[j]
        if side == "long":
            mae = max(mae, (entry - min(lo, entry)) / risk)
            if lo <= stop:
                outcome = "stop"
                break
            if hi >= target:
                outcome = "target"
                break
        else:
            mae = max(mae, (max(hi, entry) - entry) / risk)
            if hi >= stop:
                outcome = "stop"
                break
            if lo <= target:
                outcome = "target"
                break
        j += 1
    return outcome, mae, min(j, n - 1)


def _resolve_live(bars, i, side, entry, stop, risk, atr, spread, horizon,
                  cfg: "Cfg" = Cfg(), close_utc: str | None = None):
    """Walk forward using the supervisor's own rules.

    There is deliberately no fixed target here: `position_manager` never sets
    one, so booking +rr in the backtest would be measuring a strategy that is
    not traded. The stop only ever moves in the protective direction because
    `next_stop()` is the same pure function the live loop calls.

    Prices are bid. A long pays the spread on entry, a short on exit, so every
    trade is charged exactly one spread round trip.

    Returns (reason, mae_R, exit_i, exit_price_effective).
    """
    sgn = 1 if side == "long" else -1
    hi_a, lo_a, cl_a = bars["h"], bars["l"], bars["c"]
    times = bars.get("t")
    close_min = None
    if close_utc:
        _h, _m = (int(x) for x in close_utc.split(":"))
        close_min = _h * 60 + _m
    n = len(cl_a)
    cur = stop
    mae = 0.0
    j = i + 1
    while j < n and j <= i + horizon:
        a_j = float(atr[j]) if not math.isnan(atr[j]) else 0.0
        # stop is checked on the bar before any trail update (conservative)
        if side == "long":
            mae = max(mae, (entry - min(lo_a[j], entry)) / risk)
            if lo_a[j] <= cur:
                return "stop", mae, j, cur
        else:
            mae = max(mae, (max(hi_a[j], entry) - entry) / risk)
            if hi_a[j] + spread >= cur:          # buy-back happens at the ask
                return "stop", mae, j, cur + spread
        if a_j > 0:
            nxt, _why = next_stop(sgn, entry, cur, float(cl_a[j]), a_j, 0.0, cfg)
            if nxt is not None:
                cur = nxt
        # No overnight holds: the horizon alone allows a 16:45 entry to run to
        # 00:45, so the session close is a hard exit in the test as well.
        if close_min is not None and times is not None:
            bt = datetime.fromtimestamp(int(times[j]), tz=timezone.utc)
            if bt.hour * 60 + bt.minute >= close_min:
                px = cl_a[j] if side == "long" else cl_a[j] + spread
                return "session", mae, j, px
        j += 1
    j = min(j, n - 1)
    return "horizon", mae, j, (cl_a[j] if side == "long" else cl_a[j] + spread)


def _row(bars, i, side, entry, stop, target, risk, outcome, mae, exit_i, rr,
         exit_price=None):
    mark = bars["c"][exit_i] if exit_price is None else exit_price
    gross = ((mark - entry) if side == "long" else (entry - mark)) / risk
    r = gross if exit_price is not None else (
        rr if outcome == "target" else -1.0 if outcome == "stop" else gross)
    return {"bar": i, "time": int(bars["t"][i]), "side": side,
            "entry": round(entry, 5), "stop": round(stop, 5), "target": round(target, 5),
            "risk": round(risk, 6), "outcome": outcome or "neither",
            "R": round(r, 3), "mae_R": round(mae, 3), "bars_held": exit_i - i}


def _trade_row(bars, i, side, close_px, risk, rr, horizon, atr, spread,
               live_exits, close_utc):
    """Enter at ``close_px`` and resolve with one exit policy. Returns (row, exit_i).

    Every scanner and the permutation null go through here, so a scanner
    physically cannot grade a different strategy from the one
    ``permutation_test`` grades. That mismatch is what let four of the five
    families beat their own null for free: they booked a fixed +rr with no
    spread while the null paid the spread and trailed.

    Long pays the spread on entry, short on exit: exactly one round trip.
    """
    entry = close_px + spread if side == "long" else close_px
    sgn = 1 if side == "long" else -1
    stop = entry - sgn * risk
    target = entry + sgn * rr * risk
    if live_exits:
        outcome, mae, exit_i, exit_px = _resolve_live(
            bars, i, side, entry, stop, risk, atr, spread, horizon,
            close_utc=close_utc)
        row = _row(bars, i, side, entry, stop, target, risk, outcome, mae,
                   exit_i, rr, exit_price=exit_px)
    else:
        outcome, mae, exit_i = _resolve(
            bars, i, side, entry, stop, target, risk, horizon)
        row = _row(bars, i, side, entry, stop, target, risk, outcome, mae,
                   exit_i, rr)
    return row, exit_i


def scan_donchian(bars, n, k_atr, rr, horizon, sides,
                  ema_fast: int = 50, ema_slow: int = 200, spread: float = 0.0,
                  live_exits: bool = True, close_utc: str | None = None):
    """Donchian breakout on the prior n bars — the exact rule Tier 2 proposes.

    Tier 2 requires the EMA(fast) vs EMA(slow) trend gate, so the backtest must
    apply the identical gate; without it this function graded a different
    strategy from the one proposed for execution.

    Costs and exits match live: the entry pays the spread and the stop trails
    and moves to break-even through position_manager.next_stop().
    """
    upper, lower = md.donchian(bars["h"], bars["l"], n)
    a = md.atr(bars["h"], bars["l"], bars["c"], 14)
    c = bars["c"]
    ef, es = md.ema(c, ema_fast), md.ema(c, ema_slow)
    trades = []
    i = max(n + 1, ema_slow + 1)
    while i < len(c) - 1:
        if (math.isnan(upper[i]) or math.isnan(lower[i]) or math.isnan(a[i])
                or a[i] <= 0 or math.isnan(ef[i]) or math.isnan(es[i])):
            i += 1
            continue
        up_trend = bool(ef[i] > es[i])
        side = None
        # identical to tier2: long needs the up-trend, short needs it absent
        if sides in ("both", "long") and c[i] > upper[i] and up_trend:
            side = "long"
        elif sides in ("both", "short") and c[i] < lower[i] and not up_trend:
            side = "short"
        if side is None:
            i += 1
            continue
        tr, exit_i = _trade_row(bars, i, side, c[i], k_atr * a[i], rr, horizon,
                                a, spread, live_exits, close_utc)
        trades.append(tr)
        i = max(exit_i, i + 1)
    return trades


def scan_pullback(bars, fast, slow, k_atr, rr, horizon, sides,
                  spread: float = 0.0, live_exits: bool = True,
                  close_utc: str | None = None):
    """Pullback to EMA(fast) with a close back across it, in EMA(fast/slow) trend."""
    c = bars["c"]
    ef, es = md.ema(c, fast), md.ema(c, slow)
    a = md.atr(bars["h"], bars["l"], c, 14)
    trades, i = [], max(fast, slow, 15)
    while i < len(c) - 1:
        if math.isnan(ef[i]) or math.isnan(es[i]) or math.isnan(a[i]) or a[i] <= 0:
            i += 1
            continue
        side = None
        if sides in ("both", "long") and ef[i] > es[i] and bars["l"][i] <= ef[i] and c[i] > ef[i]:
            side = "long"
        elif sides in ("both", "short") and ef[i] < es[i] and bars["h"][i] >= ef[i] and c[i] < ef[i]:
            side = "short"
        if side is None:
            i += 1
            continue
        tr, exit_i = _trade_row(bars, i, side, c[i], k_atr * a[i], rr, horizon,
                                a, spread, live_exits, close_utc)
        trades.append(tr)
        i = max(exit_i, i + 1)
    return trades


def scan_rsi(bars, n, lo, hi, k_atr, rr, horizon, sides,
             spread: float = 0.0, live_exits: bool = True,
             close_utc: str | None = None):
    """RSI-n mean reversion: enter as RSI crosses INTO oversold/overbought.

    Crossing (not the resting state) so one oversold episode cannot emit a run
    of entries, and so Tier 2's "currently triggering" means the same thing as
    the historical scan.
    """
    c = bars["c"]
    r = md.rsi(c, n)
    a = md.atr(bars["h"], bars["l"], c, 14)
    trades, i = [], n + 2
    while i < len(c) - 1:
        if math.isnan(r[i]) or math.isnan(r[i - 1]) or math.isnan(a[i]) or a[i] <= 0:
            i += 1
            continue
        side = None
        if sides in ("both", "long") and r[i - 1] >= lo and r[i] < lo:
            side = "long"
        elif sides in ("both", "short") and r[i - 1] <= hi and r[i] > hi:
            side = "short"
        if side is None:
            i += 1
            continue
        tr, exit_i = _trade_row(bars, i, side, c[i], k_atr * a[i], rr, horizon,
                                a, spread, live_exits, close_utc)
        trades.append(tr)
        i = max(exit_i, i + 1)
    return trades


def scan_macd(bars, fast, slow, signal_n, k_atr, rr, horizon, sides,
              spread: float = 0.0, live_exits: bool = True,
              close_utc: str | None = None):
    """MACD line crossing its signal line. No extra trend filter."""
    c = bars["c"]
    line, sig = md.macd(c, fast, slow, signal_n)
    a = md.atr(bars["h"], bars["l"], c, 14)
    trades, i = [], slow + signal_n + 2
    while i < len(c) - 1:
        if any(math.isnan(v) for v in (line[i], sig[i], line[i - 1], sig[i - 1], a[i])) or a[i] <= 0:
            i += 1
            continue
        side = None
        if sides in ("both", "long") and line[i - 1] <= sig[i - 1] and line[i] > sig[i]:
            side = "long"
        elif sides in ("both", "short") and line[i - 1] >= sig[i - 1] and line[i] < sig[i]:
            side = "short"
        if side is None:
            i += 1
            continue
        tr, exit_i = _trade_row(bars, i, side, c[i], k_atr * a[i], rr, horizon,
                                a, spread, live_exits, close_utc)
        trades.append(tr)
        i = max(exit_i, i + 1)
    return trades


def scan_bollinger(bars, n, k, k_atr, rr, horizon, sides,
                   spread: float = 0.0, live_exits: bool = True,
                   close_utc: str | None = None):
    """Bollinger fade: close outside the band is faded, not followed.

    Entering on the CROSS out of the band (not the resting state) for the same
    reason as scan_rsi: one excursion must not emit a run of entries.
    """
    c = bars["c"]
    _mid, up, lo = md.bollinger(c, n, k)
    a = md.atr(bars["h"], bars["l"], c, 14)
    trades, i = [], n + 2
    while i < len(c) - 1:
        if any(math.isnan(v) for v in (up[i], lo[i], up[i - 1], lo[i - 1], a[i])) or a[i] <= 0:
            i += 1
            continue
        side = None
        if sides in ("both", "long") and c[i] < lo[i] and c[i - 1] >= lo[i - 1]:
            side = "long"
        elif sides in ("both", "short") and c[i] > up[i] and c[i - 1] <= up[i - 1]:
            side = "short"
        if side is None:
            i += 1
            continue
        tr, exit_i = _trade_row(bars, i, side, c[i], k_atr * a[i], rr, horizon,
                                a, spread, live_exits, close_utc)
        trades.append(tr)
        i = max(exit_i, i + 1)
    return trades


# ---------------------------------------------------------------- stats

def max_drawdown(rs) -> float:
    peak = cum = dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return round(dd, 3)


def _t_from(rs) -> float:
    """One-sample t on mean R (0.0 when there is nothing to test).

    The only edge statistic that survives an exit policy with no fixed target.
    """
    if len(rs) < 3:
        return 0.0
    # Coerce: R comes from numpy scalars, and statistics.mean/stdev preserve
    # np.float64. That leaks numpy bools into passes_* and breaks json.dumps
    # of the document (the --json path).
    rs = [float(x) for x in rs]
    sd = statistics.stdev(rs)
    return statistics.mean(rs) / (sd / math.sqrt(len(rs))) if sd else 0.0


def significance(win_rate: float, n_decided: int, rr: float, rs, n_tests: int,
                 use_t: bool = False) -> dict:
    """Two statistics on one trade series. Exactly one of them gates.

    z benchmarks the win rate against p0 = 1/(1+rr). That break-even only
    exists when a fixed +rr target is booked, so under the live exit policy it
    is a phantom: trailing exits push every trade below p0, winners included,
    and a strategy can score a *positive* z merely by losing more slowly than
    the null.

    `use_t` switches the gate to the t on mean R and REPLACES the binomial
    check rather than AND-ing with it. AND would reject exactly the
    low-win/high-R profile the t exists to admit, while the t alone already
    rejects the 45%-tiny-scratch / 55%-full-stop shape (negative mean R).
    """
    p0 = 1.0 / (1.0 + rr)
    se = math.sqrt(p0 * (1.0 - p0) / n_decided) if n_decided else 0.0
    z = (win_rate - p0) / se if se else 0.0
    t = _t_from(rs)
    zc = bonferroni_z(n_tests)
    gate = t if use_t else z
    return {"break_even": round(p0, 4), "z": round(z, 3), "z_crit_naive": 1.96,
            "z_crit_bonferroni": zc, "n_tests": n_tests,
            "gate": "t" if use_t else "z",
            "passes_naive": gate > 1.96, "passes_corrected": gate > zc,
            "t_stat_R": round(t, 3)}


def build_pool(bars, tf, pool_bars):
    cyc = CYCLE[tf]
    recent_t = bars["t"][-pool_bars:]
    recent = {k: bars[k][-pool_bars:] for k in ("o", "h", "l", "c")}
    pool = {}
    for i in range(1, len(recent_t)):
        pc = recent["c"][i - 1]
        if pc <= 0:
            continue
        s = slot_of(recent_t[i], tf) % cyc
        pool.setdefault(s, []).append((recent["o"][i] / pc, recent["h"][i] / pc,
                                       recent["l"][i] / pc, recent["c"][i] / pc))
    return pool


def simulate(pool, entry, stop, target, start_slot, horizon, rr, replays, seed, window, side, tf):
    cyc = CYCLE[tf]
    long = side == "long"
    risk = abs(entry - stop)
    rng = random.Random(seed)
    outcomes, closes, maes = [], [], []
    for _ in range(replays):
        p, outcome, mae = entry, None, 0.0
        for s in range(1, horizon + 1):
            slot = (start_slot + s) % cyc if window == 0 else (start_slot + s + rng.randint(-window, window)) % cyc
            bucket = pool.get(slot)
            if not bucket:
                continue
            _, h, l, c = rng.choice(bucket)
            hi, lo, cl = p * h, p * l, p * c
            if outcome is None:
                if long:
                    if risk:
                        mae = max(mae, (p - min(lo, p)) / risk)
                    if lo <= stop:
                        outcome = "stop"
                    elif hi >= target:
                        outcome = "target"
                else:
                    if risk:
                        mae = max(mae, (max(hi, p) - p) / risk)
                    if hi >= stop:
                        outcome = "stop"
                    elif lo <= target:
                        outcome = "target"
            p = cl
        outcomes.append(outcome or "neither")
        closes.append(p)
        maes.append(mae)
    n = replays
    cnt = {k: outcomes.count(k) for k in ("target", "stop", "neither")}
    green = sum(1 for o, p in zip(outcomes, closes)
                if o == "target" or (o == "neither" and ((p > entry) if long else (p < entry))))
    rs = [rr if o == "target" else -1.0 if o == "stop"
          else (((p - entry) if long else (entry - p)) / risk if risk else 0.0)
          for o, p in zip(outcomes, closes)]
    srt = sorted(closes)
    pct = lambda q: srt[min(len(srt) - 1, int(q * len(srt)))]
    return {"replays": n, "seed": seed, "window": window, "horizon_bars": horizon,
            "p_target": round(cnt["target"] / n, 4), "p_stop": round(cnt["stop"] / n, 4),
            "p_neither": round(cnt["neither"] / n, 4), "p_green": round(green / n, 4),
            "avg_R": round(statistics.mean(rs), 3), "avg_mae_R": round(statistics.mean(maes), 3),
            "median_close": round(pct(0.5), 5), "p5_close": round(pct(0.05), 5),
            "p95_close": round(pct(0.95), 5)}


# ---------------------------------------------------------------- analysis

def _z_from(trades, rr: float, p0: float) -> float:
    """Win-rate z against break-even, from a list of trades carrying 'R'.

    A win is a trade that ended with R > 0. The live exit policy has no fixed
    target, so the old target/stop labels stopped describing the outcome —
    counting 'target' entries would have scored every trade as a loss.
    """
    if not trades:
        return 0.0
    win = sum(1 for t in trades if t["R"] > 0) / len(trades)
    se = math.sqrt(p0 * (1.0 - p0) / len(trades))
    return (win - p0) / se if se else 0.0


def permutation_test(bars, trades, k_atr, rr, horizon, permutations=1000, seed=19,
                     spread: float = 0.0, close_utc: str | None = None,
                     live_exits: bool = True, use_t: bool = False) -> dict | None:
    """Random-entry permutation null for the strategy's z-score.

    Why not "shuffle the returns": the statistic is a function of the mean and
    win rate of the R series, and shuffling that series leaves both unchanged —
    it is a no-op. The permutation that actually tests the claim is to destroy
    the *signal->return* pairing: keep the real price path, the real volatility
    clustering and the real barrier geometry, but enter at uniformly random bars
    with the same side mix. Then ask how often chance timing produces a z at
    least as good.

    Because the null reuses the same instrument and period, it absorbs the
    cross-sectional correlation between candidates (AMD and QCOM moving
    together) that the analytic z assumes away.

    p = (ge + 1) / (permutations + 1) — the add-one form, so p is never 0.

    `passes` also requires the observation to be profitable (statistic > 0).
    Without that guard the test only asks "did you lose more slowly than random
    entries?", which a -0.67R strategy answers yes: measured on EURNZD M15 it
    returned p = 0.0233 with an average of -0.670R per trade.

    `use_t` scores the null on the mean-R t instead of the win-rate z, for the
    same reason significance() offers it: under the live exit policy there is
    no +rr target, so p0 is unreachable and the binomial degenerates.
    """
    c = bars["c"].tolist()
    n_bars = len(c)
    a = md.atr(bars["h"], bars["l"], bars["c"], 14).tolist()
    sides = [t["side"] for t in trades]
    k = len(sides)
    if k < 20:
        return None
    p0 = 1.0 / (1.0 + rr)
    actual = (_t_from([t["R"] for t in trades]) if use_t
              else _z_from(trades, rr, p0))

    lo_i, hi_i = 20, n_bars - horizon - 2
    if hi_i - lo_i < 50:
        return None

    rng = random.Random(seed)
    zs, ge = [], 0
    for _ in range(permutations):
        perm = []
        for side in sides:
            i = rng.randint(lo_i, hi_i)
            risk = k_atr * a[i]
            if not risk or risk <= 0:
                continue
            # The null must trade the same rule as the observation: same fills,
            # same costs, same exit policy. Otherwise the p-value compares two
            # different strategies and means nothing.
            entry = c[i] + spread if side == "long" else c[i]
            stop = entry - risk if side == "long" else entry + risk
            if live_exits:
                outcome, _mae, exit_i, exit_px = _resolve_live(
                    bars, i, side, entry, stop, risk, a, spread, horizon,
                    close_utc=close_utc)
                r = ((exit_px - entry) if side == "long"
                     else (entry - exit_px)) / risk
            else:
                target = (entry + rr * risk if side == "long"
                          else entry - rr * risk)
                outcome, _mae, exit_i = _resolve(
                    bars, i, side, entry, stop, target, risk, horizon)
                mark = c[exit_i]
                gross = ((mark - entry) if side == "long"
                         else (entry - mark)) / risk
                r = (rr if outcome == "target"
                     else -1.0 if outcome == "stop" else gross)
            perm.append({"outcome": outcome or "neither", "R": r})
        stat = (_t_from([p["R"] for p in perm]) if use_t
                else _z_from(perm, rr, p0))
        zs.append(stat)
        if stat >= actual:
            ge += 1

    zs.sort()
    return {
        "permutations": permutations,
        "statistic": "t" if use_t else "z",
        "actual_z": round(actual, 3),
        "null_z_mean": round(statistics.mean(zs), 3),
        "null_z_p95": round(zs[min(len(zs) - 1, int(0.95 * len(zs)))], 3),
        "p_value": round((ge + 1) / (permutations + 1), 4),
        "passes": actual > 0 and (ge + 1) / (permutations + 1) < 0.05,
    }


def _outcome_counts(trades: list[dict]) -> dict:
    """Every trade lands in exactly one bucket, so the buckets sum to occurrences.

    _resolve_live can end a trade four ways - stop, target, session close, or
    the horizon running out - and the history block used to publish only three.
    A reader (or the desk's chief) then found 271 occurrences and 248 accounted
    for, and correctly called it a contradiction. Naming the buckets fixes the
    arithmetic instead of explaining it.
    """
    keys = ("target", "stop", "session", "horizon", "neither")
    c = {k: 0 for k in keys}
    for t in trades:
        o = t.get("outcome")
        c[o if o in c else "neither"] += 1
    return c


def analyse(symbol, tf="H1", strategy="donchian", don_n=20, ema_fast=50, ema_slow=200,
            atr_k=1.5, rr=1.5, horizon=None, sides="both", bars_n=5000,
            replays=10000, seed=19, window=2, pool_bars=1500, n_tests=1,
            permutations=0, close_utc=None, use_t: bool = False) -> dict | None:
    """Full Time Machine for one symbol. Returns None when there is nothing to say."""
    if horizon is None:
        horizon = HORIZON_DEFAULT[tf]
    md.connect()
    offset = md.broker_offset()
    bars = md.fetch(symbol, tf, bars_n, offset)
    if bars is None:
        return None
    info = md.mt5.symbol_info(symbol)
    tick = md.mt5.symbol_info_tick(symbol)
    # One spread per round trip, in price units. Tier 1 already refuses symbols
    # whose spread exceeds 15% of ATR; the backtest now charges what it admits.
    spread_px = (float(info.spread) * float(info.point)
                 if info and getattr(info, "spread", 0) else 0.0)

    if strategy == "donchian":
        trades = scan_donchian(bars, don_n, atr_k, rr, horizon, sides,
                               ema_fast, ema_slow, spread_px, True, close_utc)
        rule = (f"Donchian-{don_n} breakout gated by EMA{ema_fast}/EMA{ema_slow}, "
                f"stop {atr_k}xATR14, trail+break-even exits, spread {spread_px:g}, "
                f"{horizon} bars")
    elif strategy == "pullback":
        trades = scan_pullback(bars, ema_fast, ema_slow, atr_k, rr, horizon, sides,
                               spread_px, True, close_utc)
        rule = (f"EMA{ema_fast}/{ema_slow} pullback, stop {atr_k}xATR14, "
                f"trail+break-even exits, spread {spread_px:g}, {horizon} bars")
    elif strategy == "rsi":
        trades = scan_rsi(bars, 14, 35.0, 65.0, atr_k, rr, horizon, sides,
                          spread_px, True, close_utc)
        rule = (f"RSI-14 mean reversion (<35 long / >65 short), stop "
                f"{atr_k}xATR14, trail+break-even exits, spread {spread_px:g}, "
                f"{horizon} bars")
    elif strategy == "bollinger":
        trades = scan_bollinger(bars, 20, 2.0, atr_k, rr, horizon, sides,
                                spread_px, True, close_utc)
        rule = (f"Bollinger-20 2.0sd fade, stop {atr_k}xATR14, "
                f"trail+break-even exits, spread {spread_px:g}, {horizon} bars")
    elif strategy == "macd":
        trades = scan_macd(bars, 12, 26, 9, atr_k, rr, horizon, sides,
                           spread_px, True, close_utc)
        rule = (f"MACD 12/26/9 line cross, stop {atr_k}xATR14, "
                f"trail+break-even exits, spread {spread_px:g}, {horizon} bars")
    else:
        raise ValueError(strategy)

    if not trades:
        return None

    rs = [t["R"] for t in trades]
    win = sum(1 for r in rs if r > 0) / len(rs) if rs else 0.0
    sig = significance(win, len(rs), rr, rs, n_tests, use_t=use_t)

    a = md.atr(bars["h"], bars["l"], bars["c"], 14)
    atr_now = float(a[-1]) if not math.isnan(a[-1]) else float("nan")
    last = len(bars["c"]) - 1
    c_now = float(bars["c"][last])
    upper, lower = md.donchian(bars["h"], bars["l"], don_n)
    ref = float(tick.ask) if tick and tick.ask else c_now
    # The proposal side must come from the strategy that was tested, not from a
    # Donchian midpoint: an RSI short on an up-trending channel is a real setup.
    if strategy == "rsi":
        r_now = float(md.rsi(bars["c"], 14)[-1])
        side = "long" if r_now < 35 else "short" if r_now > 65 else trades[-1]["side"]
    elif strategy == "bollinger":
        _m, bu, bl = md.bollinger(bars["c"], 20, 2.0)
        side = ("long" if c_now < float(bl[-1]) else
                "short" if c_now > float(bu[-1]) else trades[-1]["side"])
    elif strategy == "macd":
        ln, sg = md.macd(bars["c"], 12, 26, 9)
        side = "long" if float(ln[-1]) >= float(sg[-1]) else "short"
    else:
        side = "long" if (not math.isnan(upper[last]) and c_now > (upper[last] + lower[last]) / 2) else "short"
    risk_p = atr_k * atr_now
    stop = ref - risk_p if side == "long" else ref + risk_p
    target = ref + rr * risk_p if side == "long" else ref - rr * risk_p

    pool = build_pool(bars, tf, pool_bars)
    start_slot = slot_of(bars["t"][last], tf) % CYCLE[tf]
    mc = simulate(pool, ref, stop, target, start_slot, horizon, rr, replays, seed, window, side, tf)

    perm = (permutation_test(bars, trades, atr_k, rr, horizon, permutations, seed,
                             spread_px, close_utc, live_exits=True, use_t=use_t)
            if permutations else None)

    return {
        "symbol": symbol, "timeframe": tf, "strategy": strategy, "rule": rule,
        "bars": int(len(bars["c"])), "span_days": round(md.span_days(bars), 1),
        "history": {
            "occurrences": len(trades),
            **_outcome_counts(trades),
            "n_decided": len(rs),
            "win_rate": round(win, 4),
            "avg_R": round(statistics.mean(rs), 3),
            "avg_mae_R": round(statistics.mean(t["mae_R"] for t in trades), 3),
            "max_drawdown_R": max_drawdown(rs),
            "avg_bars_held": round(statistics.mean(t["bars_held"] for t in trades), 1),
        },
        "significance": sig,
        "permutation": perm,
        "mc": mc,
        "current": {
            "side": side, "reference_ask": round(ref, 6), "atr14": round(atr_now, 6),
            "proposed_entry": round(ref, 5), "proposed_stop": round(stop, 5),
            "proposed_target": round(target, 5), "risk": round(risk_p, 6),
            "donchian_upper": round(float(upper[last]), 6) if not math.isnan(upper[last]) else None,
            "donchian_lower": round(float(lower[last]), 6) if not math.isnan(lower[last]) else None,
        },
    }


def verdict_line(stat: float, zc: float, name: str = "z") -> str:
    if stat > zc:
        return f"PASS: {name}={stat:+.2f} > corrected {zc:.2f} — survives the multiple-comparisons bar."
    if stat > 1.96:
        return f"FAIL: {name}={stat:+.2f} clears a naive 1.96 but not the corrected {zc:.2f} — this is what scanning produces by luck."
    return f"FAIL: {name}={stat:+.2f} does not clear 1.96 — indistinguishable from noise."


def main() -> int:
    ap = argparse.ArgumentParser(description="Time Machine (MT5), any symbol/timeframe.")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--timeframe", default="H1", choices=list(CYCLE))
    ap.add_argument("--strategy", default="donchian",
                    choices=["donchian", "pullback", "rsi", "bollinger", "macd"])
    ap.add_argument("--donchian", type=int, default=20)
    ap.add_argument("--ema-fast", type=int, default=20)
    ap.add_argument("--ema-slow", type=int, default=50)
    ap.add_argument("--atr-k", type=float, default=1.5)
    ap.add_argument("--rr", type=float, default=1.5)
    ap.add_argument("--horizon", type=int, default=None)
    ap.add_argument("--sides", default="both", choices=["both", "long", "short"])
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--replays", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--window", type=int, default=2)
    ap.add_argument("--pool-bars", type=int, default=1500)
    ap.add_argument("--trials", type=int, default=1, help="independent tests tried (botlab DSR / Bonferroni)")
    ap.add_argument("--permutations", type=int, default=0,
                    help="random-entry permutations (0 = skip the null)")
    ap.add_argument("--t-gate", action="store_true",
                    help="gate on the mean-R t instead of the win-rate z "
                         "(required under trailing exits: p0 has no target to hit)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    doc = analyse(a.symbol, a.timeframe, a.strategy, a.donchian, a.ema_fast, a.ema_slow,
                  a.atr_k, a.rr, a.horizon, a.sides, a.bars, a.replays, a.seed,
                  a.window, a.pool_bars, a.trials, a.permutations, None, a.t_gate)
    if doc is None:
        print(f"{a.symbol}: no setups or no data", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(doc, indent=2))
        return 0

    h, s, m, c = doc["history"], doc["significance"], doc["mc"], doc["current"]
    print(f"Time Machine — {doc['symbol']} {doc['timeframe']} — {doc['bars']} bars ({doc['span_days']} days)")
    print(f"Rule: {doc['rule']}")
    print(f"\nHISTORY (real bars)")
    print(f"  fired {h['occurrences']}: {h['target']} target, {h['stop']} stop, {h['neither']} neither")
    print(f"  win rate {h['win_rate']:.1%} vs break-even {s['break_even']:.1%}  ->  z = {s['z']:+.2f}")
    print(f"  avg R {h['avg_R']:+.3f} (t={s['t_stat_R']:+.2f})   avg drawdown {h['avg_mae_R']:.3f}R   "
          f"max drawdown {h['max_drawdown_R']:+.3f}R")
    print(f"\nMONTE CARLO ({m['replays']:,} replays, seed {m['seed']})")
    print(f"  target {m['p_target']:.1%}  stop {m['p_stop']:.1%}  neither {m['p_neither']:.1%}  "
          f"avg R {m['avg_R']:+.3f}")
    print(f"\nCURRENT: {c['side']} near {c['proposed_entry']} stop {c['proposed_stop']} target {c['proposed_target']}")
    gate = s.get("gate") or "z"
    print(f"\n{verdict_line(s['t_stat_R'] if gate == 't' else s['z'], s['z_crit_bonferroni'], gate)}")
    print("Simulation from the instrument's own recent bars. Not a prediction.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

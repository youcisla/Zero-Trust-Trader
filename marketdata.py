"""Shared MT5 data + indicator helpers (numpy).

One fetch path for every tier, so the funnel and the Time Machine cannot drift
apart on the two things that silently corrupt results:

  * broker-server time. MT5 stamps bars in server time (+2h on this broker) and
    the reported broker_tz_offset_minutes is wrong. The offset is self-calibrated
    from a live tick against the system clock.
  * the forming bar. copy_rates_from_pos returns the in-progress bar last; a
    scanner that includes it reads a bar that has not closed yet.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

TIMEFRAMES = {
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}
TF_SECONDS = {"M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


def symbol_info(symbol: str):
    """Contract facts for sizing (tick size/value, contract size).

    The connection, the broker offset and the bar cache live here; a module that
    needs one read-only fact asks for it rather than reaching for `mt5` itself.
    """
    return mt5.symbol_info(symbol)


def account_currency() -> str:
    """The account's currency, or "" when the terminal will not say."""
    info = mt5.account_info()
    return (info.currency if info else "") or ""


class TerminalError(RuntimeError, SystemExit):
    """The terminal refused to initialise.

    A library must not end the process, so this is catchable as a RuntimeError;
    it is also a SystemExit because every CLI in this tree has always let an
    initialisation failure terminate with a one-line message, and that behaviour
    is the one thing no caller has to change.
    """


def connect() -> None:
    if not mt5.initialize():
        raise TerminalError(f"mt5.initialize failed: {mt5.last_error()}")


_OFFSET_PROBES = ("BTCUSD", "ETHUSD", "US30", "EURUSD")
_LAST_OFFSET: int | None = None


def broker_offset() -> int:
    """Seconds the broker clock runs ahead of UTC, rounded to 30 min.

    Taken from the freshest tick of a probe set, never from a single symbol.
    After a weekend close EURUSD's last tick is frozen at Friday 23:59 while the
    real clock keeps advancing, so a one-symbol probe reports a shrinking offset
    (-0.5 h by Saturday morning instead of +2.0 h). That drags server_now back to
    Friday's close, which makes every shut instrument report bar_age_min ~ 12 min
    — so tier 2 scans frozen Friday bars all weekend — and makes fetch() discard
    the live bars of the instruments that ARE open.

    If every probe is stale (maintenance, or a weekend with no 24/7 symbol) the
    last plausible offset is reused. With none on record it returns 0, i.e. UTC:
    everything then looks older than it is and callers skip it. Fail closed.
    """
    global _LAST_OFFSET
    now = time.time()
    fresh = max((t.time for t in (mt5.symbol_info_tick(s) for s in _OFFSET_PROBES)
                 if t and t.time), default=0)
    if fresh and now - fresh < 3600:
        _LAST_OFFSET = round((fresh - now) / 1800) * 1800
    return _LAST_OFFSET if _LAST_OFFSET is not None else 0


_AUTO_SELECTED: set[str] = set()


def fetch(symbol: str, tf: str = "H1", bars: int = 3000, offset: int | None = None) -> dict | None:
    """Closed bars only, oldest first. None when the symbol has no usable data.

    Fetching requires the symbol to be in Market Watch, so this selects it. That
    is a real side effect on the terminal: anything selected here is recorded and
    can be released with release_auto_selected(). Callers that scan hundreds of
    symbols must release, or Market Watch grows by that many every run.
    """
    if tf not in TIMEFRAMES:
        raise ValueError(f"timeframe {tf!r} not in {sorted(TIMEFRAMES)}")
    inf = mt5.symbol_info(symbol)
    was_visible = bool(inf and getattr(inf, "visible", False))
    if not mt5.symbol_select(symbol, True):
        _AUTO_SELECTED.discard(symbol)
        return None
    if not was_visible:
        # Track on the successful select. Tracking the pre-read missed the case
        # where symbol_info() returns None (transient IPC) but the select worked:
        # the symbol then leaked into Market Watch with no record of it.
        _AUTO_SELECTED.add(symbol)
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAMES[tf], 0, bars)
    if rates is None or len(rates) == 0:
        return None
    if offset is None:
        offset = broker_offset()
    server_now = time.time() + offset
    secs = TF_SECONDS[tf]
    try:
        keep = [r for r in rates if int(r["time"]) + secs <= server_now]
    except (TypeError, ValueError, OverflowError):
        return None
    # Validity is "at least one closed bar", NOT "as many as were asked for".
    # Re-applying the request size here made a 30-bar probe always fail (29 bars
    # survive the forming-bar drop) and silently starved every caller.
    if not keep:
        return None
    def _tick_volume(r) -> float:
        # A rate feed without tick_volume (old stub) measures 0, so a turnover
        # ranking has nothing to rank rather than an invented number.
        try:
            return float(r["tick_volume"])
        except (KeyError, TypeError, ValueError):
            return 0.0

    try:
        arr = np.array([(int(r["time"]), float(r["open"]), float(r["high"]),
                         float(r["low"]), float(r["close"]), _tick_volume(r))
                        for r in keep], dtype=np.float64)
    except (TypeError, ValueError, OverflowError, KeyError):
        return None
    # tick_volume rides on the same copy_rates call, so a turnover measurement
    # never needs a second fetch. Validation stays on the OHLC columns: a
    # zero-volume bar is real, a zero-price bar is not.
    if (not np.isfinite(arr[:, :5]).all() or (arr[:, 1:5] <= 0).any()
            or (np.diff(arr[:, 0]) <= 0).any()
            or (arr[:, 2] < np.maximum(arr[:, 1], arr[:, 4])).any()
            or (arr[:, 3] > np.minimum(arr[:, 1], arr[:, 4])).any()):
        return None
    return {"symbol": symbol, "tf": tf, "offset": offset,
            "timestamp_basis": "broker bar open; subtract offset for UTC",
            "t": arr[:, 0], "o": arr[:, 1], "h": arr[:, 2],
            "l": arr[:, 3], "c": arr[:, 4], "v": arr[:, 5]}


def fetch_cached(cache: dict, symbol: str, tf: str = "H1", bars: int = 3000,
                 offset: int | None = None) -> dict | None:
    """fetch() at most once per (symbol, timeframe, bar count) within one run.

    A sweep that runs three families over one symbol used to read the same
    series three times. The bar count is part of the key: a 500-bar turnover
    probe must never answer a 3000-bar correlation question.
    """
    key = (symbol, tf, bars, offset)
    if key not in cache:
        cache[key] = fetch(symbol, tf, bars, offset)
    return cache[key]


def tick_spread(tick, offset: int = 0, max_age: float = 120.0) -> float | None:
    """Validated bid/ask cost in price units; never substitutes free execution."""
    if tick is None:
        return None
    try:
        bid, ask, stamp = float(tick.bid), float(tick.ask), float(tick.time)
    except (AttributeError, TypeError, ValueError):
        return None
    age = time.time() + offset - stamp
    if (not all(math.isfinite(x) for x in (bid, ask, stamp, age))
            or bid <= 0 or ask <= bid or stamp <= 0 or not -60 <= age <= max_age):
        return None
    return ask - bid


def bar_close_utc(bars: dict, index: int) -> datetime:
    """Convert this module's broker bar-open timestamp to its completed UTC time."""
    stamp = float(bars["t"][index]) - bars.get("offset", 0)
    stamp += TF_SECONDS.get(bars.get("tf"), 0)
    return datetime.fromtimestamp(stamp, timezone.utc)


def span_days(bars: dict) -> float:
    if bars is None or len(bars["t"]) < 2:
        return 0.0
    return (bars["t"][-1] - bars["t"][0]) / 86400.0


def ema(x, n: int) -> np.ndarray:
    """Seed with the mean of the first n, then the usual 2/(n+1) recurrence."""
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape, np.nan)
    if len(x) < n:
        return out
    # pandas seeds ewm from the first value, so prepend the loop's own seed.
    seeded = np.concatenate(([x[:n].mean()], x[n:]))
    out[n - 1:] = pd.Series(seeded).ewm(span=n, adjust=False).mean().to_numpy()
    # ewm resumes after a NaN; the loop's recurrence poisons every later value.
    bad = np.isnan(seeded)
    if bad.any():
        out[n - 1 + int(np.argmax(bad)):] = np.nan
    return out


def atr(h, l, c, n: int = 14) -> np.ndarray:
    """Simple mean of true range over n bars. NaN until n TRs exist."""
    h, l, c = np.asarray(h, float), np.asarray(l, float), np.asarray(c, float)
    out = np.full(h.shape, np.nan)
    if len(h) < n + 1:
        return out
    pc = c[:-1]
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - pc), abs(l[1:] - pc)))
    # tr[j] corresponds to bar j+1
    pad = np.concatenate(([np.nan], tr))
    # Stride windows, not rolling().mean(): pandas skips NaN, the loop's np.mean
    # propagates it. Window j ends at bar j+n-1, so the first is bar n-1 (NaN).
    windows = np.lib.stride_tricks.sliding_window_view(pad, n)
    out[n - 1:] = windows.mean(axis=-1)
    return out


def donchian(h, l, n: int = 20) -> tuple[np.ndarray, np.ndarray]:
    """Channel of the PRIOR n bars, excluding bar i (no look-ahead)."""
    h, l = np.asarray(h, float), np.asarray(l, float)
    upper = np.full(h.shape, np.nan)
    lower = np.full(l.shape, np.nan)
    if len(h) < n:
        return upper, lower
    # Window j is bars j..j+n-1 and drives bar j+n; the last window (bar len-1)
    # would include bar i itself, so it is dropped.
    upper[n:] = np.lib.stride_tricks.sliding_window_view(h, n).max(axis=-1)[:-1]
    lower[n:] = np.lib.stride_tricks.sliding_window_view(l, n).min(axis=-1)[:-1]
    return upper, lower


def bollinger(c, n: int = 20, k: float = 2.0):
    """SMA(n) +/- k population standard deviations (ddof=0).

    ddof=0 deliberately: pandas rolling().std() defaults to ddof=1, so the two
    tiers would compute different bands from the same bars and disagree about
    whether price closed outside the band.
    """
    c = np.asarray(c, dtype=float)
    mid = np.full(c.shape, np.nan)
    up = np.full(c.shape, np.nan)
    lo = np.full(c.shape, np.nan)
    if len(c) < n:
        return mid, up, lo
    windows = np.lib.stride_tricks.sliding_window_view(c, n)
    m, sd = windows.mean(axis=-1), windows.std(axis=-1)  # std default ddof=0
    mid[n - 1:], up[n - 1:], lo[n - 1:] = m, m + k * sd, m - k * sd
    return mid, up, lo


def rsi(c, n: int = 14) -> np.ndarray:
    """Wilder's RSI. NaN until n changes exist, then smoothed recursively."""
    c = np.asarray(c, dtype=float)
    out = np.full(c.shape, np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c)
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    # Seed as in the loop, then Wilder = ewm(alpha=1/n, adjust=False).
    au = np.concatenate(([up[:n].mean()], up[n:]))
    ad = np.concatenate(([dn[:n].mean()], dn[n:]))
    bad = np.isnan(au) | np.isnan(ad)  # ewm resumes after NaN; the loop does not
    au = pd.Series(au).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()
    ad = pd.Series(ad).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()
    # Zero denominator: flat averages are 50, all-gain is 100 (the old helper).
    ratio = np.divide(au, ad, out=np.zeros_like(au), where=ad != 0)
    out[n:] = np.where(ad == 0, np.where(au == 0, 50.0, 100.0),
                       100.0 - 100.0 / (1.0 + ratio))
    if bad.any():
        out[n + int(np.argmax(bad)):] = np.nan
    return out


def macd(c, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD line (EMA fast - EMA slow) and its EMA(signal).

    The signal EMA is seeded from the first non-NaN MACD value. Running
    ema() straight over the line would average a NaN seed and return NaN for
    the entire series — the signal line would silently never exist.
    """
    c = np.asarray(c, dtype=float)
    line = ema(c, fast) - ema(c, slow)
    sig = np.full(line.shape, np.nan)
    ok = ~np.isnan(line)
    if ok.any():
        start = int(np.argmax(ok))
        sig[start:] = ema(line[start:], signal)
    return line, sig


def corr_returns(a: dict, b: dict) -> float | None:
    """Pearson correlation of close-to-close returns for two already-fetched series."""
    if a is None or b is None:
        return None
    common = np.intersect1d(a["t"], b["t"])
    if len(common) < 100:
        return None
    ca = a["c"][np.searchsorted(a["t"], common)]
    cb = b["c"][np.searchsorted(b["t"], common)]
    if (not np.isfinite(ca).all() or not np.isfinite(cb).all()
            or (ca <= 0).any() or (cb <= 0).any()):
        return None
    ra, rb = np.diff(ca) / ca[:-1], np.diff(cb) / cb[:-1]
    if ra.std() == 0 or rb.std() == 0:
        return None
    rho = float(np.corrcoef(ra, rb)[0, 1])
    return rho if math.isfinite(rho) else None


def return_vector(bars: dict) -> tuple[np.ndarray, np.ndarray] | None:
    """(timestamps, close-to-close returns) for one fetched series.

    Precomputed once per symbol, so a dedupe pass correlates vectors instead of
    re-diffing the same closes once per pair. None when corr_returns() would
    refuse the series anyway.
    """
    if bars is None:
        return None
    t, c = bars["t"], bars["c"]
    if len(t) < 101 or not np.isfinite(c).all() or (c <= 0).any():
        return None
    return t, np.diff(c) / c[:-1]


def corr_vector(a, b) -> float | None:
    """Pearson rho from two return_vector() values on the identical bar grid.

    None when the grids differ or a vector is unusable; callers then fall back
    to corr_returns(), which aligns the two series first — a symbol with missing
    bars is never measured on a different sample than before.
    """
    if a is None or b is None:
        return None
    ta, ra = a
    tb, rb = b
    if ta.shape != tb.shape or not np.array_equal(ta, tb):
        return None
    if ra.std() == 0 or rb.std() == 0:
        return None
    rho = float(np.corrcoef(ra, rb)[0, 1])
    return rho if math.isfinite(rho) else None


def correlation(sym_a: str, sym_b: str, bars_n: int = 3000, tf: str = "H1",
                cache: dict | None = None) -> float | None:
    """Pearson correlation of close-to-close returns over the common window.

    Same-symbol compares as +1.0 without a fetch: two strategies triggering on
    one instrument is one position, not two. A per-run `cache` serves every pair
    from one fetch per symbol instead of re-reading both sides per pair.
    """
    if sym_a == sym_b:
        return 1.0
    if cache is not None:
        return corr_returns(fetch_cached(cache, sym_a, tf, bars_n),
                            fetch_cached(cache, sym_b, tf, bars_n))
    return corr_returns(fetch(sym_a, tf, bars_n), fetch(sym_b, tf, bars_n))


def asset_class(path: str) -> str:
    p = (path or "").lower()
    if "forex" in p:
        return "FX"
    if "crypto" in p:
        return "Crypto"
    if "indices" in p or "index" in p:
        return "Indices"
    if "stocks" in p or "shares" in p or "etf" in p:
        return "Shares"
    if "commodit" in p:
        return "Commodities"
    return "Other"


def bar_age_min(bars: dict, offset: int) -> float:
    """Minutes since the last CLOSED bar ended (server clock).

    The reliable open/closed test on this build. `symbol_info_tick()` is None and
    `symbol_info().time` is 0 until a symbol is selected into Market Watch, and
    this MT5 python build exposes no session-schedule API — so bar recency is the
    only signal that is both data-driven and always available. ~20 min when the
    market trades, hours/1000+ when it is shut.
    """
    if bars is None or len(bars["t"]) == 0:
        return float("inf")
    tf_secs = TF_SECONDS[bars["tf"]]
    server_now = time.time() + offset
    return (server_now - (bars["t"][-1] + tf_secs)) / 60.0


def release_auto_selected() -> int:
    """Deselect every symbol fetch() had to add. Returns how many were released.

    Only symbols this module selected are touched — anything already visible in
    Market Watch before we ran is left exactly as the user had it.
    """
    released = 0
    for s in list(_AUTO_SELECTED):
        if mt5.symbol_select(s, False):
            released += 1
            _AUTO_SELECTED.discard(s)
    return released


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

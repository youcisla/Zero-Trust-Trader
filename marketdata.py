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

TIMEFRAMES = {
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}
TF_SECONDS = {"M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


def connect() -> None:
    if not mt5.initialize():
        raise SystemExit(f"mt5.initialize failed: {mt5.last_error()}")


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
    if inf is not None and not inf.visible:
        _AUTO_SELECTED.add(symbol)
    if not mt5.symbol_select(symbol, True):
        _AUTO_SELECTED.discard(symbol)
        return None
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
    try:
        arr = np.array([(int(r["time"]), float(r["open"]), float(r["high"]),
                         float(r["low"]), float(r["close"])) for r in keep],
                       dtype=np.float64)
    except (TypeError, ValueError, OverflowError):
        return None
    if (not np.isfinite(arr).all() or (arr[:, 1:] <= 0).any()
            or (np.diff(arr[:, 0]) <= 0).any()
            or (arr[:, 2] < np.maximum(arr[:, 1], arr[:, 4])).any()
            or (arr[:, 3] > np.minimum(arr[:, 1], arr[:, 4])).any()):
        return None
    return {"symbol": symbol, "tf": tf, "offset": offset,
            "timestamp_basis": "broker bar open; subtract offset for UTC",
            "t": arr[:, 0], "o": arr[:, 1], "h": arr[:, 2],
            "l": arr[:, 3], "c": arr[:, 4]}


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
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape, np.nan)
    if len(x) < n:
        return out
    k = 2.0 / (n + 1)
    prev = x[:n].mean()
    out[n - 1] = prev
    for i in range(n, len(x)):
        prev = x[i] * k + prev * (1 - k)
        out[i] = prev
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
    for i in range(n, len(h)):
        window = pad[i - n + 1:i + 1]
        out[i] = np.mean(window)
    return out


def donchian(h, l, n: int = 20) -> tuple[np.ndarray, np.ndarray]:
    """Channel of the PRIOR n bars, excluding bar i (no look-ahead)."""
    h, l = np.asarray(h, float), np.asarray(l, float)
    upper = np.full(h.shape, np.nan)
    lower = np.full(l.shape, np.nan)
    for i in range(n, len(h)):
        upper[i] = h[i - n:i].max()
        lower[i] = l[i - n:i].min()
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
    for i in range(n - 1, len(c)):
        w = c[i - n + 1:i + 1]
        m, sd = w.mean(), w.std()
        mid[i], up[i], lo[i] = m, m + k * sd, m - k * sd
    return mid, up, lo


def _rsi_at(au: float, ad: float) -> float:
    """Flat average => 50 (neutral). ad == 0 with movement => 100."""
    if ad == 0:
        return 50.0 if au == 0 else 100.0
    return 100.0 - 100.0 / (1.0 + au / ad)


def rsi(c, n: int = 14) -> np.ndarray:
    """Wilder's RSI. NaN until n changes exist, then smoothed recursively."""
    c = np.asarray(c, dtype=float)
    out = np.full(c.shape, np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c)
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    au, ad = up[:n].mean(), dn[:n].mean()
    out[n] = _rsi_at(au, ad)
    for i in range(n + 1, len(c)):
        au = (au * (n - 1) + up[i - 1]) / n
        ad = (ad * (n - 1) + dn[i - 1]) / n
        out[i] = _rsi_at(au, ad)
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


def correlation(sym_a: str, sym_b: str, bars_n: int = 3000, tf: str = "H1") -> float | None:
    """Pearson correlation of close-to-close returns over the common window.

    Same-symbol compares as +1.0 without a fetch: two strategies triggering on
    one instrument is one position, not two.
    """
    if sym_a == sym_b:
        return 1.0
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

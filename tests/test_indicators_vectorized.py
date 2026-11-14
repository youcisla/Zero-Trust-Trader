"""The vectorised indicators must equal the loop they replaced, to 1e-9.

The owner accepted floating-point drift, not a semantics change. Each reference
below is the original per-bar loop, kept here verbatim so the comparison is
against the definition rather than against another vectorisation. Tolerance is
fixed at 1e-9 with no relative slack: a drifting formula fails, not widens.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from _mt5stub import install  # noqa: E402

install()  # must precede marketdata

import marketdata as md  # noqa: E402

TOL = 1e-9


# --- the definitions, as they were before vectorisation --------------------

def ref_ema(x, n):
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


def ref_atr(h, l, c, n=14):
    h, l, c = np.asarray(h, float), np.asarray(l, float), np.asarray(c, float)
    out = np.full(h.shape, np.nan)
    if len(h) < n + 1:
        return out
    pc = c[:-1]
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - pc), abs(l[1:] - pc)))
    pad = np.concatenate(([np.nan], tr))
    for i in range(n, len(h)):
        out[i] = np.mean(pad[i - n + 1:i + 1])
    return out


def ref_donchian(h, l, n=20):
    h, l = np.asarray(h, float), np.asarray(l, float)
    upper, lower = np.full(h.shape, np.nan), np.full(l.shape, np.nan)
    for i in range(n, len(h)):
        upper[i] = h[i - n:i].max()
        lower[i] = l[i - n:i].min()
    return upper, lower


def ref_bollinger(c, n=20, k=2.0):
    c = np.asarray(c, dtype=float)
    mid, up, lo = np.full(c.shape, np.nan), np.full(c.shape, np.nan), np.full(c.shape, np.nan)
    for i in range(n - 1, len(c)):
        w = c[i - n + 1:i + 1]
        m, sd = w.mean(), w.std()
        mid[i], up[i], lo[i] = m, m + k * sd, m - k * sd
    return mid, up, lo


def _rsi_at(au, ad):
    if ad == 0:
        return 50.0 if au == 0 else 100.0
    return 100.0 - 100.0 / (1.0 + au / ad)


def ref_rsi(c, n=14):
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


# --- series ---------------------------------------------------------------

def random_bars(seed=7, bars=500):
    rs = np.random.RandomState(seed)
    c = np.cumsum(rs.randn(bars)) + 100.0
    h = c + rs.rand(bars) * 0.5
    l = c - rs.rand(bars) * 0.5
    return h, l, c


def flat(n=200):
    c = np.full(n, 1.1)
    return c.copy(), c.copy(), c.copy()


def zero_range(n=200):
    """h == l == c: ATR's true range is all zero."""
    c = np.cumsum(np.random.RandomState(11).randn(n)) + 50.0
    return c.copy(), c.copy(), c.copy()


def all_equal(n=40):
    return np.full(n, 3.0), np.full(n, 3.0), np.full(n, 3.0)


def with_nan(n=200):
    """A missing close: the loop poisons EMA/RSI after it, and ATR/Donchian/
    Bollinger recover only once the NaN leaves the window."""
    c = np.cumsum(np.random.RandomState(13).randn(n)) + 100.0
    c[n // 2] = np.nan
    return c.copy(), c.copy(), c.copy()


SERIES = {
    "random": random_bars(),
    "flat": flat(),
    "zero_range": zero_range(),
    "all_equal": all_equal(),
    "nan": with_nan(),
    "short": random_bars(bars=10),
    "empty": (np.array([]), np.array([]), np.array([])),
}


class TestVectorizedMatchesLoop(unittest.TestCase):
    def assert_close(self, got, want, label):
        self.assertEqual(np.shape(got), np.shape(want), f"{label}: shape")
        if not np.allclose(got, want, rtol=0, atol=TOL, equal_nan=True):
            drift = np.abs(np.asarray(got) - np.asarray(want))
            self.fail(f"{label}: max drift {np.nanmax(drift) if drift.size else 0.0}")

    def test_ema(self):
        for name, (_h, _l, c) in SERIES.items():
            for n in (1, 3, 14, 50):
                self.assert_close(md.ema(c, n), ref_ema(c, n), f"ema/{name}/n={n}")

    def test_atr(self):
        for name, (h, l, c) in SERIES.items():
            for n in (1, 3, 14):
                self.assert_close(md.atr(h, l, c, n), ref_atr(h, l, c, n),
                                  f"atr/{name}/n={n}")

    def test_donchian(self):
        for name, (h, l, _c) in SERIES.items():
            for n in (2, 20):
                gu, gl = md.donchian(h, l, n)
                ru, rl = ref_donchian(h, l, n)
                self.assert_close(gu, ru, f"donchian upper/{name}/n={n}")
                self.assert_close(gl, rl, f"donchian lower/{name}/n={n}")

    def test_bollinger(self):
        for name, (_h, _l, c) in SERIES.items():
            for n in (2, 20):
                gm, gu, gl = md.bollinger(c, n, 2.0)
                rm, ru, rl = ref_bollinger(c, n, 2.0)
                self.assert_close(gm, rm, f"bollinger mid/{name}/n={n}")
                self.assert_close(gu, ru, f"bollinger up/{name}/n={n}")
                self.assert_close(gl, rl, f"bollinger lo/{name}/n={n}")

    def test_rsi(self):
        for name, (_h, _l, c) in SERIES.items():
            for n in (1, 3, 14):
                self.assert_close(md.rsi(c, n), ref_rsi(c, n), f"rsi/{name}/n={n}")


class TestEdgesPreserved(unittest.TestCase):
    """NaN/edge behaviour, asserted exactly as the loop defined it."""

    def test_shorter_than_lookback_is_all_nan(self):
        c = np.arange(10.0)
        self.assertTrue(np.all(np.isnan(md.ema(c, 50))))
        self.assertTrue(np.all(np.isnan(md.rsi(c, 14))))
        self.assertTrue(np.all(np.isnan(md.atr(c, c, c, 14))))
        for band in md.bollinger(c, 14):
            self.assertTrue(np.all(np.isnan(band)))
        for band in md.donchian(c, c, 14):
            self.assertTrue(np.all(np.isnan(band)))

    def test_warmup_lengths(self):
        _h, _l, c = random_bars()
        self.assertFalse(np.isnan(md.ema(c, 50)[49]))
        self.assertTrue(np.all(np.isnan(md.ema(c, 50)[:49])))
        self.assertFalse(np.isnan(md.atr(_h, _l, c, 14)[14]))
        self.assertTrue(np.all(np.isnan(md.atr(_h, _l, c, 14)[:14])))
        self.assertFalse(np.isnan(md.donchian(_h, _l, 20)[0][20]))
        self.assertTrue(np.all(np.isnan(md.donchian(_h, _l, 20)[0][:20])))
        self.assertFalse(np.isnan(md.bollinger(c, 20)[0][19]))
        self.assertTrue(np.all(np.isnan(md.bollinger(c, 20)[0][:19])))
        self.assertTrue(np.all(np.isnan(md.rsi(c, 14)[:14])))
        self.assertFalse(np.isnan(md.rsi(c, 14)[14]))

    def test_empty_input_returns_empty(self):
        empty = np.array([])
        for arr in (md.ema(empty, 5), md.atr(empty, empty, empty, 5),
                    md.rsi(empty, 5), *md.bollinger(empty, 5),
                    *md.donchian(empty, empty, 5)):
            self.assertEqual(len(arr), 0)

    def test_rsi_zero_denominator_branches(self):
        self.assertAlmostEqual(md.rsi(np.full(100, 1.1), 14)[-1], 50.0)
        self.assertAlmostEqual(md.rsi(np.arange(100) * 0.01, 14)[-1], 100.0)
        self.assertAlmostEqual(md.rsi(np.arange(100, 0, -1) * 0.01, 14)[-1], 0.0)

    def test_flat_bollinger_is_zero_width(self):
        _mid, up, lo = md.bollinger(np.full(100, 2.0), 20)
        live = ~np.isnan(up)
        self.assertTrue(np.all(up[live] == lo[live]))

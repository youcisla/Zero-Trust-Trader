"""A universe that halves must not be written by accident.

A `--top 1` probe once replaced 188 symbols with EURUSD and nothing complained:
the next scan tested one symbol and reported STAND_DOWN as though the market had
narrowed to a currency pair. The guard is one inequality, and this test calls the
function that applies it — re-implementing the arithmetic in the test is how a
`<` becomes `<=` in production and the suite stays green.
"""
import sys
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import _mt5stub  # noqa: E402

# make_liquid imports marketdata, which imports MetaTrader5 - Windows-only. Install
# the stub first, or this module only collects when another test happened to run
# before it in alphabetical order.
_mt5stub.install()
import make_liquid  # noqa: E402


class ShrinkGuard(unittest.TestCase):
    def test_halving_is_refused(self):
        self.assertTrue(make_liquid.refuses_shrink(93, 188, False))
        self.assertTrue(make_liquid.refuses_shrink(1, 188, False))

    def test_a_normal_rebuild_is_written(self):
        self.assertFalse(make_liquid.refuses_shrink(187, 188, False))
        self.assertFalse(make_liquid.refuses_shrink(93, 120, False))  # 77%
        self.assertFalse(make_liquid.refuses_shrink(3, 0, False))     # nothing on disk

    def test_force_is_the_escape(self):
        self.assertFalse(make_liquid.refuses_shrink(93, 188, True))

    def test_exactly_half_is_allowed(self):
        # The rule is "less than half", not "at most half".
        self.assertFalse(make_liquid.refuses_shrink(94, 188, False))
        self.assertTrue(make_liquid.refuses_shrink(93, 188, False))

    def test_the_writer_calls_it_before_writing(self):
        src = (Path(__file__).resolve().parents[1] / "make_liquid.py").read_text(encoding="utf-8")
        self.assertIn("refuses_shrink(len(picked), before, a.force)", src)
        self.assertLess(src.index("refuses_shrink(len(picked)"), src.index("OUT.write_text"))


if __name__ == "__main__":
    unittest.main()

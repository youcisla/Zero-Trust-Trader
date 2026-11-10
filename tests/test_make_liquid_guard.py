"""A universe that halves must not be written by accident.

A `--top 1` probe once replaced 188 symbols with EURUSD and nothing complained:
the next scan tested one symbol and reported STAND_DOWN as though the market had
narrowed to a currency pair. The guard is one inequality, and it is asserted here
against the source as well, because the way this test fails to help is the guard
quietly disappearing from `make_liquid.py`.
"""
import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "make_liquid.py").read_text(encoding="utf-8")


def refuses(picked: int, before: int, force: bool = False) -> bool:
    """The same inequality `make_liquid._main` applies before writing."""
    if not picked:
        return True
    return bool(before and picked * 2 < before and not force)


class ShrinkGuard(unittest.TestCase):
    def test_halving_is_refused(self):
        self.assertTrue(refuses(93, 188))
        self.assertTrue(refuses(1, 188))

    def test_a_normal_rebuild_is_written(self):
        self.assertFalse(refuses(187, 188))
        self.assertFalse(refuses(93, 120))  # 77% - a real rebuild, not a probe
        self.assertFalse(refuses(3, 0))     # nothing on disk yet

    def test_force_is_the_escape(self):
        self.assertFalse(refuses(93, 188, force=True))

    def test_the_guard_is_still_in_the_writer(self):
        self.assertIn("len(picked) * 2 < before", SRC)
        self.assertIn("return 4", SRC)
        self.assertIn('"--force", action="store_true"', SRC)
        # and it runs before the write, not after it
        self.assertLess(SRC.index("len(picked) * 2 < before"), SRC.index("OUT.write_text"))


if __name__ == "__main__":
    unittest.main()

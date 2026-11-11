"""Every z in the docs is computed, never typed.

A repository whose differentiator is numerical honesty cannot afford one invented
row: a caught row poisons every uncaught one. This reads the published tables and
re-derives each figure from the function the gate uses, so drift fails the build
instead of surviving until someone recomputes it by hand.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import _mt5stub  # noqa: E402

_mt5stub.install()

import timemachine_mt5 as tm  # noqa: E402


class TheReadmeBarTable(unittest.TestCase):
    def test_every_row_is_bonferroni_z_of_its_count(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        rows = re.findall(r"^\| ([\d,]+)[^|]* \| ([\d.]+) \|$", text, re.M)
        self.assertGreaterEqual(len(rows), 6, rows)   # the table is there at all
        for count, published in rows:
            n = int(count.replace(",", ""))
            want = tm.bonferroni_z(n)
            self.assertAlmostEqual(float(published), want, places=2,
                                   msg=f"README says z={published} at {n} trials, gate says {want}")

    def test_the_arithmetic_behind_the_rows_is_the_arithmetic(self):
        # 291 tests a scan x 48 scans, and the week/month rows compound it.
        for count, mult in ((13_968, 1), (97_776, 7), (419_040, 30)):
            self.assertEqual(count, 291 * 48 * mult)


class TheTrialsLedger(unittest.TestCase):
    def test_the_stated_total_is_the_sum_of_the_rows(self):
        text = (ROOT / "docs/TRIALS.md").read_text(encoding="utf-8")
        log = text.split("## Log")[1].split("**Cumulative:")[0]
        rows = re.findall(r"^\| [^|]+ \| ([\d,]+) \|", log, re.M)
        self.assertGreaterEqual(len(rows), 8, rows)
        total = sum(int(r.replace(",", "")) for r in rows)
        stated = int(re.search(r"\*\*Cumulative: ([\d,]+)", text).group(1).replace(",", ""))
        self.assertEqual(stated, total, "the cumulative line is not the sum of the log")


class TheUniverseSentence(unittest.TestCase):
    """The dated canonical line and the count on disk agree, when the file is here."""

    def test_one_number_for_the_universe(self):
        import json
        universe = ROOT / "liquid_universe.json"
        if not universe.exists():
            self.skipTest("runtime artifact absent (fresh clone or CI)")
        count = json.loads(universe.read_text(encoding="utf-8"))["count"]
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        canon = re.findall(r"1,711 quoted → \d+ tradable[^.]*?(\d+) liquid", readme)
        if not canon:
            self.skipTest("canonical sentence not found")
        self.assertEqual(int(canon[0]), count,
                         f"README says {canon[0]} liquid, liquid_universe.json says {count}")


if __name__ == "__main__":
    unittest.main()

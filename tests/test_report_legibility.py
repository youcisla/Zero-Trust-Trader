"""Generated reports must not render functional labels below 11px."""
import re
import unittest
from pathlib import Path

import report


def _doc():
    return {"scan_id": "fixture", "generated_utc": "2026-11-14T00:00:00+00:00",
            "stood_down": True, "survivors": [], "trials_declared": 10000,
            "z_crit_corrected": 4.5, "results": []}


class TestReportLegibility(unittest.TestCase):
    def setUp(self):
        self.html = report.render(_doc(), Path("fixture.json"))

    def test_no_font_size_below_eleven_px(self):
        self.assertEqual(re.findall(r"font-size:\s*(?:8|9|10)(?:\.\d+)?px", self.html), [])

    def test_three_labels_are_eleven_px(self):
        for selector in (".prov span", ".stats span", ".why span"):
            rule = re.search(re.escape(selector) + r"\{[^}]*\}", self.html)
            self.assertIsNotNone(rule, selector)
            self.assertIn("font-size:11px", rule.group(0), selector)


if __name__ == "__main__":
    unittest.main()

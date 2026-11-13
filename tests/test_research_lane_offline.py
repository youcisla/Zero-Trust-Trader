"""The external research readers must never reach the money path.

`agent-reach` and `last30days` are documented and fenced in
`docs/RESEARCH-LANE.md`. This is the fence: it reads the seven modules that can
place, size or gate an order and fails if either tool's name appears in any of
them. A shell-out must name the tool in its source, so the token scan also
covers `subprocess`, `os.system` and `os.popen` invocations.

`DetectorFlagsSampleTest` proves the scanner bites: it feeds samples that contain
each tool's name and asserts the detector reports them, so a broken pattern or an
empty file list cannot make the module pass vacuously.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROTECTED = (
    "run_funnel.py",
    "tier1_universe.py",
    "tier2_patterns.py",
    "tier3_deep.py",
    "position_manager.py",
    "desk.py",
    "agent_brief.py",
)

TOOLS = re.compile(r"agent-reach|last30days")


def mentions(text: str) -> list[tuple[int, str]]:
    """Every line that names one of the research readers, with its line number."""
    return [
        (number, line)
        for number, line in enumerate(text.splitlines(), 1)
        if TOOLS.search(line)
    ]


class DetectorFlagsSampleTest(unittest.TestCase):
    def test_detector_flags_sample(self):
        sample = "import subprocess\nsubprocess.run(['agent-reach', 'reddit', 'search'])\n"
        self.assertEqual(len(mentions(sample)), 1)

    def test_detector_flags_last30days(self):
        self.assertTrue(mentions("from last30days import sweep\n"))

    def test_detector_ignores_unrelated_shell_outs(self):
        self.assertEqual(mentions("subprocess.run([PY, 'run_funnel.py'])\n"), [])


class ProtectedModulesTest(unittest.TestCase):
    def test_every_protected_module_exists(self):
        # Seven, not "whatever is there": an emptied tuple would make the scan below vacuous.
        self.assertEqual(len(PROTECTED), 7)
        missing = [name for name in PROTECTED if not (ROOT / name).is_file()]
        self.assertEqual(missing, [], "protected module list is stale")

    def test_no_protected_module_references_a_tool(self):
        hits = {}
        for name in PROTECTED:
            found = mentions((ROOT / name).read_text(encoding="utf-8"))
            if found:
                hits[name] = found
        self.assertEqual(hits, {}, "external research reader referenced on the money path")

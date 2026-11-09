"""Reproduce audit findings using synthetic inputs and temporary files only.

This records the audited baseline, so `defect_reproduced: true` means a defect
was observed. It is not a passing safety test. After a fix, turn the relevant
case into a regression test that asserts the safe behavior.

No terminal or provider is contacted. MetaTrader5 is replaced before imports.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import socket
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from _mt5stub import install

install()

import agent_brief
import desk
import export_ledger


def main() -> None:
    records = []

    def record(case, safe, observed, defect):
        records.append({"case": case, "expected_safe_behavior": safe,
                        "observed": observed, "defect_reproduced": bool(defect)})

    # Nothing below requires sockets, including provider calls.
    def no_network(*args, **kwargs):
        raise RuntimeError("Audit probes forbid network access")

    with mock.patch.object(socket.socket, "connect", no_network):
        examples = [
            ("citation_substring", {"agent": "chief", "summary": "z = 4.531"}, "z = 14.531"),
            ("citation_sign", {"agent": "chief", "summary": "t = 5.523"}, "t = -5.523"),
            ("citation_small_metric", {"agent": "chief", "summary": "win rate = 3%"}, "no measured figures"),
        ]
        for name, note, source in examples:
            result = desk._guard(note.copy(), source)
            record(name, "reject the uncited metric", result,
                   result.get("status") == "ok")

        empty = desk._guard({}, "no measured figures")
        record("empty_note", "reject a note missing its role and content", empty,
               empty.get("status") == "ok")

        with tempfile.TemporaryDirectory(prefix="ztt-audit-probes-") as directory:
            root = Path(directory)
            for status in ("rejected", "error", "partial"):
                notes = {"status": status, "model": "fixture-model", "facts_sha": "fixture",
                         "roles": {"chief": {"status": status}}}
                with mock.patch.object(desk, "run", return_value=notes), \
                     mock.patch.object(desk, "write_notes", return_value=root / "agent_notes.json"), \
                     mock.patch.object(desk, "append_journal", return_value=root / "desk_journal.jsonl"), \
                     mock.patch.object(agent_brief.audit, "write"), \
                     contextlib.redirect_stdout(io.StringIO()):
                    code = agent_brief.main(["--root", str(root)])
                record("desk_exit_" + status, "nonzero exit for an unsuccessful brief",
                       {"exit_code": code, "status": status}, code == 0)

            ledger = root / "trades.csv"
            sentinel = "closed,symbol,side,entry,exit,stop,hold_hours\nexisting-history\n"
            ledger.write_text(sentinel, encoding="utf-8")
            fake_mt5 = SimpleNamespace(
                initialize=lambda: True,
                account_info=lambda: SimpleNamespace(login=12345, server="fixture-demo"),
                terminal_info=lambda: SimpleNamespace(connected=True),
                symbol_info=lambda *args: SimpleNamespace(visible=True),
                symbol_select=lambda *args: True,
                symbol_info_tick=lambda *args: SimpleNamespace(time=int(time.time())),
                history_deals_get=lambda *args: None,
                shutdown=lambda: None,
            )
            with mock.patch.object(export_ledger, "mt5", fake_mt5), \
                 mock.patch.object(export_ledger.stops, "load", return_value={}), \
                 mock.patch.dict("os.environ", {"MT5_EXPECTED_LOGIN": "12345"}), \
                 mock.patch.object(sys, "argv", ["export_ledger.py", "--symbol", "FIXTURE",
                                                 "--out", str(ledger)]), \
                 contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                code = export_ledger.main()
            unchanged = ledger.read_text(encoding="utf-8") == sentinel
            record("export_api_error_preserves_ledger",
                   "refuse the API error and preserve the existing ledger",
                   {"exit_code": code, "existing_history_preserved": unchanged}, not unchanged)

            # A partial exit must not be represented as a fully closed position.
            base = int(time.time())
            deals = [
                SimpleNamespace(symbol="FIXTURE", position_id=42, entry=0, type=0,
                                price=100.0, time=base - 3600, volume=0.02),
                SimpleNamespace(symbol="FIXTURE", position_id=42, entry=1, type=1,
                                price=101.0, time=base, volume=0.01),
            ]
            fake_mt5.history_deals_get = lambda *args: deals
            partial = root / "partial.csv"
            with mock.patch.object(export_ledger, "mt5", fake_mt5), \
                 mock.patch.object(export_ledger.stops, "load", return_value={}), \
                 mock.patch.dict("os.environ", {"MT5_EXPECTED_LOGIN": "12345"}), \
                 mock.patch.object(sys, "argv", ["export_ledger.py", "--symbol", "FIXTURE",
                                                 "--out", str(partial)]), \
                 contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                code = export_ledger.main()
            rows = list(csv.DictReader(io.StringIO(partial.read_text(encoding="utf-8")))) if partial.exists() else []
            record("export_partial_position",
                   "do not claim the remaining open volume is a closed position",
                   {"exit_code": code, "closed_rows": len(rows),
                    "opened_volume": 0.02, "closed_volume": 0.01}, len(rows) == 1)

    print(json.dumps({"kind": "synthetic audit reproductions", "checks": records}, indent=2))


if __name__ == "__main__":
    main()

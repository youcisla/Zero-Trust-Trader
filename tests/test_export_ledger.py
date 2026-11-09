"""Immutable export and deal-reconstruction contracts; no terminal is used."""
import csv
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from _mt5stub import install

install()
import export_ledger as exporter


def deal(entry, volume=0.01, **kwargs):
    return SimpleNamespace(symbol="FIXTURE", position_id=42, entry=entry,
        type=entry, price=100 + entry, time=1000 + entry * 3600, volume=volume,
        commission=0, swap=0, fee=0, **kwargs)


class TestReconstruction(unittest.TestCase):
    def test_simple_closed_position(self):
        rows = exporter.closed_rows([deal(0), deal(1)], "FIXTURE", {"42": "99"}, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry"], "100")
        self.assertEqual(rows[0]["stop"], "99")
        self.assertEqual(rows[0]["hold_hours"], "1.0000")

    def test_partial_multifill_reversal_and_missing_entry_are_refused(self):
        cases = [[deal(0, .02), deal(1, .01)], [deal(0), deal(0), deal(1, .02)],
                 [deal(1)], [deal(0), deal(2)]]
        for deals in cases:
            with self.subTest(deals=deals), self.assertRaises(exporter.ExportError):
                exporter.closed_rows(deals, "FIXTURE", {}, 0)

    def test_open_position_is_not_a_closed_trade(self):
        self.assertEqual(exporter.closed_rows([deal(0)], "FIXTURE", {}, 0), [])

    def test_nonfinite_prices_and_unrepresented_costs_are_refused(self):
        for attribute, value in [("price", float("nan")), ("volume", -1), ("commission", -.5)]:
            entry = deal(0)
            setattr(entry, attribute, value)
            with self.subTest(attribute=attribute), self.assertRaises(exporter.ExportError):
                exporter.closed_rows([entry, deal(1)], "FIXTURE", {}, 0)


class TestImmutableOutput(unittest.TestCase):
    def test_existing_output_is_never_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trades.csv"
            output.write_text("existing history\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                exporter.write_export(output, [{k: "fixture" for k in exporter.FIELDS}])
            self.assertEqual(output.read_text(encoding="utf-8"), "existing history\n")

    def test_new_snapshot_has_botlab_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot.csv"
            exporter.write_export(output, exporter.closed_rows([deal(0), deal(1)], "FIXTURE", {}, 0))
            with output.open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, exporter.FIELDS)
                self.assertEqual(len(list(reader)), 1)

    def test_api_error_never_creates_or_changes_output(self):
        import contextlib
        import os
        terminal = SimpleNamespace(initialize=lambda: True, shutdown=lambda: None,
            symbol_select=lambda *args: True,
            symbol_info=lambda *args: SimpleNamespace(visible=True),
            symbol_info_tick=lambda *args: SimpleNamespace(time=1000),
            account_info=lambda: SimpleNamespace(login=12345, server="fixture-demo"),
            terminal_info=lambda: SimpleNamespace(connected=True),
            history_deals_get=lambda *args: None, last_error=lambda: (1, "fixture error"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot.csv"
            with mock.patch.object(exporter, "mt5", terminal), \
                 mock.patch.object(exporter.stops, "load", return_value={}), \
                 mock.patch.dict(os.environ, {"MT5_EXPECTED_LOGIN": "12345"}), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = exporter.main(["--out", str(output), "--symbol", "FIXTURE"])
            self.assertNotEqual(code, 0)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

"""The sweep's ranking is the only thing standing between a number and a claim."""

import unittest

from tools.batch_backtest import rank, summarise


class TestSummarise(unittest.TestCase):
    def test_a_document_becomes_one_row(self):
        doc = {
            "history": {"n_decided": 210, "win_rate": 0.49, "avg_R": 0.185, "max_drawdown_R": -12.0},
            "significance": {"gate": "t", "t_stat_R": 1.5, "mean_net_R": 0.02},
        }
        row = summarise(doc, "donchian", "SAN.ES", "SHARES")
        self.assertEqual(row["symbol"], "SAN.ES")
        self.assertEqual(row["stat"], 1.5, "the gate says t, so the row carries t")
        self.assertEqual(row["n_decided"], 210)

    def test_a_z_gate_row_carries_z_not_t(self):
        doc = {"history": {"n_decided": 5}, "significance": {"gate": "z", "z": 2.2, "t_stat_R": 9.9}}
        self.assertEqual(summarise(doc, "rsi", "X", None)["stat"], 2.2)

    def test_an_empty_result_is_no_row_at_all(self):
        self.assertIsNone(summarise(None, "donchian", "X", None))
        self.assertIsNone(summarise({}, "donchian", "X", None))

    def test_a_missing_statistic_stays_missing(self):
        doc = {"history": {"n_decided": 3}, "significance": {"gate": "t"}}
        self.assertIsNone(summarise(doc, "donchian", "X", None)["stat"])


class TestRank(unittest.TestCase):
    def rows(self):
        return [
            {"symbol": "A", "stat": -3.0, "family": "donchian"},
            {"symbol": "B", "stat": 2.68, "family": "donchian"},
            {"symbol": "C", "stat": None, "family": "donchian"},
            {"symbol": "D", "stat": 4.9, "family": "donchian"},
        ]

    def test_highest_first_and_unmeasured_last(self):
        out = rank(self.rows(), bar=4.56)
        self.assertEqual([r["symbol"] for r in out], ["D", "B", "A", "C"])

    def test_margin_is_measured_against_the_engine_bar(self):
        out = rank(self.rows(), bar=4.56)
        self.assertAlmostEqual(out[0]["margin"], 0.34)
        self.assertTrue(out[0]["clears_bar"])
        self.assertFalse(out[1]["clears_bar"])
        self.assertIsNone(out[-1]["margin"], "no statistic means no margin, not zero")

    def test_without_a_bar_nothing_clears(self):
        out = rank(self.rows(), bar=None)
        self.assertFalse(any(r["clears_bar"] for r in out))
        self.assertTrue(all(r["margin"] is None for r in out))


if __name__ == "__main__":
    unittest.main()

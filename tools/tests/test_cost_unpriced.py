# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for adt_cost pricing of model ids: dated snapshot ids price at the base
model's rate, and unknown ids are left unpriced and reported by name.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_cost  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "unpriced-ledger" / "ledger.tsv"
LINES = FIX.read_text().splitlines()
PRICES = adt_cost.load_prices()


class DatedSnapshotTest(unittest.TestCase):
    def test_dated_id_prices_at_the_base_models_rate(self):
        dated = adt_cost.rates_for(PRICES, "claude-haiku-4-5-20251001", "standard")
        plain = adt_cost.rates_for(PRICES, "claude-haiku-4-5", "standard")
        self.assertIsNotNone(dated, "a dated snapshot id must price")
        self.assertEqual(dated, plain,
                         "a dated snapshot is the same model at the same rate")

    def test_an_unknown_model_is_still_none(self):
        self.assertIsNone(
            adt_cost.rates_for(PRICES, "claude-imaginary-9", "standard"))

    def test_a_version_segment_is_not_stripped_as_a_date_suffix(self):
        """Only an eight-digit trailing date is stripped, never a version number."""
        self.assertIsNotNone(adt_cost.rates_for(PRICES, "claude-opus-4-5", "standard"))
        self.assertIsNone(adt_cost.rates_for(PRICES, "claude-opus-4-9", "standard"))


class NamesTheModelTest(unittest.TestCase):
    def test_price_ledger_reports_the_unpriced_id_per_ticket(self):
        totals = adt_cost.price_ledger(LINES, PRICES, None)
        self.assertEqual(list(totals["ADT-801"]["unpriced"]), ["claude-imaginary-9"])
        self.assertEqual(totals["ADT-801"]["unpriced"]["claude-imaginary-9"],
                         50 + 60 + 70000)

    def test_a_ticket_whose_rows_all_price_reports_nothing(self):
        """ADT-800 in the fixture has a dated id and a zero-token synthetic row."""
        totals = adt_cost.price_ledger(LINES, PRICES, None)
        self.assertEqual(totals["ADT-800"]["unpriced"], {})

    def test_unpriced_excludes_rows_worth_zero_tokens(self):
        found = adt_cost.unpriced(LINES, PRICES)
        self.assertNotIn("<synthetic>", found)
        self.assertEqual(set(found), {"claude-imaginary-9"})
        self.assertEqual(found["claude-imaginary-9"]["rows"], 1)


class CliTest(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(TOOLS / "adt_cost.py"), "unpriced", *args],
            capture_output=True, text=True)

    def test_exit_1_and_the_model_is_named(self):
        p = self._run(str(FIX))
        self.assertEqual(p.returncode, 1)
        self.assertIn("claude-imaginary-9", p.stdout)

    def test_exit_0_when_every_row_prices(self):
        clean = [l for l in LINES if "claude-imaginary-9" not in l]
        tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "unpriced-clean-ledger.tsv"
        tmp.write_text("\n".join(clean) + "\n")
        try:
            p = self._run(str(tmp))
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        finally:
            tmp.unlink(missing_ok=True)

    def test_no_argument_is_a_usage_error_not_a_pass(self):
        p = self._run()
        self.assertEqual(p.returncode, 2)

    def test_a_ledger_that_cannot_be_read_exits_2(self):
        p = self._run("/definitely/not/a/file.log")
        self.assertEqual(p.returncode, 2)
        self.assertNotIn("all rows priced", p.stdout)
        self.assertIn("/definitely/not/a/file.log", p.stderr)

    def test_a_partial_read_still_grades_what_it_read(self):
        """With one readable and one missing ledger, the readable one is graded and the missing one named on stderr."""
        p = self._run(str(FIX), "/definitely/not/a/file.log")
        self.assertEqual(p.returncode, 1)
        self.assertIn("claude-imaginary-9", p.stdout)
        self.assertIn("could not read", p.stderr)


if __name__ == "__main__":
    unittest.main()

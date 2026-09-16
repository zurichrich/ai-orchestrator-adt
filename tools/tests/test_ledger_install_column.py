# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that adt_cost.parse_row reads every ledger width (5, 11, 12 and 13
columns): column 12 is the command, column 13 the install id, and neither
changes the cost or the measured fields."""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import adt_cost  # noqa: E402

UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

FIVE = "2026-01-01T00:00:00Z\tADT-1\t100\t200\toldsess"
ELEVEN = ("2026-01-02T00:00:00Z\tADT-2\t10\t20\tsess\t"
          "claude-opus-5\tstandard\t300\t0\t7\tmeasured")
TWELVE = ELEVEN.replace("\tADT-2\t", "\tADT-3\t") + "\tadt-build"
THIRTEEN = ELEVEN.replace("\tADT-2\t", "\tADT-4\t") + "\tadt-build\t" + UUID


class WidthTest(unittest.TestCase):
    def test_all_four_widths_parse(self):
        for label, line, tier in (("5", FIVE, "legacy"), ("11", ELEVEN, "measured"),
                                  ("12", TWELVE, "measured"), ("13", THIRTEEN, "measured")):
            row = adt_cost.parse_row(line)
            self.assertIsNotNone(row, label)
            self.assertEqual(row["tier"], tier, label)

    def test_a_thirteen_column_row_carries_the_install(self):
        self.assertEqual(adt_cost.parse_row(THIRTEEN)["install"], UUID)

    def test_narrower_rows_have_no_install(self):
        for line in (FIVE, ELEVEN, TWELVE):
            self.assertEqual(adt_cost.parse_row(line)["install"], "")

    def test_a_thirteen_column_row_keeps_its_cache_classes(self):
        row = adt_cost.parse_row(THIRTEEN)
        self.assertEqual(row["tier"], "measured")
        self.assertEqual(row["model"], "claude-opus-5")
        self.assertEqual(row["cache_read"], 300)
        self.assertEqual(row["cache_write_1h"], 7)

    def test_the_install_column_is_inert_to_cost(self):
        prices = adt_cost.load_prices()
        est = {"usd_per_mtok": 174.58}
        a = adt_cost.price_ledger([TWELVE], prices, est)["ADT-3"]["micros"]
        b = adt_cost.price_ledger([THIRTEEN], prices, est)["ADT-4"]["micros"]
        self.assertEqual(a, b)

    # ── the command column (column 12) ──

    def test_twelve_column_row_carries_the_command(self):
        self.assertEqual(adt_cost.parse_row(TWELVE)["command"], "adt-build")

    def test_a_twelve_column_row_keeps_its_measured_fields(self):
        row = adt_cost.parse_row(TWELVE)
        self.assertEqual(row["tier"], "measured")
        self.assertEqual(row["model"], "claude-opus-5")
        self.assertEqual(row["speed"], "standard")
        self.assertEqual(row["cache_read"], 300)
        self.assertEqual(row["cache_write_1h"], 7)

    def test_eleven_and_twelve_column_rows_parse_to_the_same_cost_fields(self):
        eleven = adt_cost.parse_row(ELEVEN)
        twelve = adt_cost.parse_row(TWELVE)
        for field in ("input", "output", "model", "speed", "cache_read",
                      "cache_write_5m", "cache_write_1h", "tier"):
            self.assertEqual(eleven[field], twelve[field], field)

    def test_a_trailing_empty_command_is_not_a_missing_column(self):
        row = adt_cost.parse_row(ELEVEN + "\t")
        self.assertEqual(row["command"], "")
        self.assertEqual(row["tier"], "measured")

    def test_narrower_rows_have_no_command(self):
        for line in (FIVE, ELEVEN):
            self.assertEqual(adt_cost.parse_row(line)["command"], "")

    def test_a_trailing_empty_install_is_not_a_missing_column(self):
        row = adt_cost.parse_row(TWELVE + "\t")
        self.assertEqual(row["install"], "")
        self.assertEqual(row["tier"], "measured")


if __name__ == "__main__":
    unittest.main()

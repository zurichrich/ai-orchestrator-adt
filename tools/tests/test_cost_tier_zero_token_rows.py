# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that zero-token ledger rows do not lower a ticket's cost tier in adt_cost.price_ledger.

A row with tokens and no price still lowers it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_cost  # noqa: E402

PRICES = adt_cost.load_prices()


def row(tix, model, inp=0, out=0, cread=0, cw5=0, cw1h=0, tier="measured"):
    return "\t".join(["2026-09-07T10:00:00Z", tix, str(inp), str(out), "sess",
                      model, "standard", str(cread), str(cw5), str(cw1h),
                      tier, "adt-build", "inst1"])


PRICED = row("ADT-800", "claude-opus-5", 10, 20, 1000, 0, 500)


class ZeroTokenRowsTest(unittest.TestCase):
    def test_a_zero_token_synthetic_row_leaves_the_ticket_measured(self):
        totals = adt_cost.price_ledger([PRICED, row("ADT-800", "<synthetic>")],
                                       PRICES, None)
        self.assertEqual(totals["ADT-800"]["tier"], "measured")

    def test_the_money_is_unchanged_by_the_zero_token_row(self):
        with_row = adt_cost.price_ledger([PRICED, row("ADT-800", "<synthetic>")],
                                         PRICES, None)["ADT-800"]["micros"]
        without = adt_cost.price_ledger([PRICED], PRICES, None)["ADT-800"]["micros"]
        self.assertEqual(with_row, without)

    def test_a_ticket_of_only_zero_token_rows_has_unknown_cost_not_zero(self):
        totals = adt_cost.price_ledger([row("ADT-802", "<synthetic>")], PRICES, None)
        self.assertIsNone(totals["ADT-802"]["micros"])


class RealGapsStillShowTest(unittest.TestCase):
    def test_a_row_with_tokens_and_no_price_still_makes_it_estimated(self):
        totals = adt_cost.price_ledger(
            [PRICED, row("ADT-800", "claude-imaginary-9", 50, 60, 70000)],
            PRICES, None)
        self.assertEqual(totals["ADT-800"]["tier"], "estimated")

    def test_a_legacy_row_with_tokens_still_makes_it_legacy(self):
        legacy = "\t".join(["2026-09-07T10:00:00Z", "ADT-800", "500", "1000", "sess"])
        totals = adt_cost.price_ledger([PRICED, legacy], PRICES, None)
        self.assertEqual(totals["ADT-800"]["tier"], "legacy")

    def test_a_zero_token_legacy_row_does_not_lower_the_tier(self):
        """A 5-column legacy row with no tokens is ignored like any other zero-token row."""
        legacy_zero = "\t".join(["2026-09-07T10:00:00Z", "ADT-800", "0", "0", "sess"])
        totals = adt_cost.price_ledger([PRICED, legacy_zero], PRICES, None)
        self.assertEqual(totals["ADT-800"]["tier"], "measured")


if __name__ == "__main__":
    unittest.main()

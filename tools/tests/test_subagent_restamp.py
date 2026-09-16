# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that adding subagent ledger rows raises a ticket's priced cost, keeps
it `measured`, and does not change how an individual row is priced."""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import adt_cost  # noqa: E402

PRICES = adt_cost.load_prices()
ESTIMATOR = {"usd_per_mtok": 174.58}


def row(tix, inp, out, cread=0, cw5=0, command="", model="claude-opus-5"):
    return "\t".join(["2026-09-05T10:00:00Z", tix, str(inp), str(out), "sess",
                      model, "standard", str(cread), str(cw5), "0",
                      "measured", command])


# What the main-session hook recorded for a reviewer-heavy ticket.
MAIN_ROWS = [row("ADT-205", 5000, 9000, 120000, 4000) for _ in range(3)]
# What the subagents cost, mostly cache reads and writes.
SUBAGENT_ROWS = [
    row("ADT-205", 210, 34881, 5732956, 333781, "subagent:adt-plan-quality-reviewer"),
    row("ADT-205", 180, 21000, 3100000, 210000, "subagent:adt-dod-coverage-reviewer"),
]


class RestampTest(unittest.TestCase):
    def test_adding_subagent_rows_raises_the_ticket_cost(self):
        before = adt_cost.price_ledger(MAIN_ROWS, PRICES, ESTIMATOR)["ADT-205"]
        after = adt_cost.price_ledger(MAIN_ROWS + SUBAGENT_ROWS, PRICES, ESTIMATOR)["ADT-205"]
        self.assertGreater(after["micros"], before["micros"],
                           "adding subagent rows did not move the ticket's cost")
        # These subagent rows are worth more than three times the main rows.
        self.assertGreater(after["micros"], before["micros"] * 4,
                           "expected the cost to rise more than 4x")

    def test_the_ticket_stays_measured(self):
        after = adt_cost.price_ledger(MAIN_ROWS + SUBAGENT_ROWS, PRICES, ESTIMATOR)["ADT-205"]
        self.assertEqual(after["tier"], "measured")

    def test_only_a_measured_row_prices_the_cache_classes(self):
        """Removing the cache tokens lowers a measured row's price and leaves an
        estimated row's price unchanged."""
        def price(tier, cache):
            row = "\t".join(["2026-09-05T10:00:00Z", "ADT-205", "210", "34881",
                             "sess", "claude-opus-5", "standard",
                             str(5732956 if cache else 0),
                             str(333781 if cache else 0), "0", tier,
                             "subagent:adt-plan-quality-reviewer"])
            return adt_cost.price_ledger([row], PRICES, ESTIMATOR)["ADT-205"]["micros"]

        self.assertGreater(price("measured", True), price("measured", False),
                           "a measured row ignored its own cache classes")
        self.assertEqual(price("estimated", True), price("estimated", False),
                         "an estimated row priced its cache classes")

    def test_a_ticket_with_no_subagents_is_unchanged(self):
        only_main = adt_cost.price_ledger(MAIN_ROWS, PRICES, ESTIMATOR)["ADT-205"]
        again = adt_cost.price_ledger(MAIN_ROWS, PRICES, ESTIMATOR)["ADT-205"]
        self.assertEqual(only_main["micros"], again["micros"])

    def test_subagent_rows_do_not_change_how_a_row_is_priced(self):
        """The same row prices the same with and without `subagent:<type>`."""
        plain = adt_cost.price_ledger([row("ADT-9", 10, 20, 30, 40)], PRICES, ESTIMATOR)
        tagged = adt_cost.price_ledger(
            [row("ADT-9", 10, 20, 30, 40, "subagent:adt-design-reviewer")], PRICES, ESTIMATOR)
        self.assertEqual(plain["ADT-9"]["micros"], tagged["ADT-9"]["micros"])


if __name__ == "__main__":
    unittest.main()

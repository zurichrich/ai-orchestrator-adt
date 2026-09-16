# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests how subagent cost rows set a ticket's cost tier.

A row from the SubagentStop hook has the full token split and is `measured`. A
fallback row appended by a playbook has only a total and is `estimated`. One
estimated row makes the whole ticket estimated, shown with a `~`.
"""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import adt_cost  # noqa: E402

PRICES = adt_cost.load_prices()
ESTIMATOR = {"usd_per_mtok": 174.58}


def hook_row(tix="ADT-224", agent="adt-plan-quality-reviewer"):
    """What adt-subagent-cost.sh writes: full split, measured."""
    return "\t".join(["2026-09-05T10:00:00Z", tix, "210", "34881", "sess",
                      "claude-opus-5", "standard", "5732956", "333781", "0",
                      "measured", "subagent:" + agent])


def fallback_row(tix="ADT-224", agent="adt-plan-quality-reviewer"):
    """What a playbook append can manage: a bare total, no cache classes."""
    return "\t".join(["2026-09-05T10:05:00Z", tix, "600000", "40000", "sess",
                      "claude-opus-5", "standard", "0", "0", "0",
                      "estimated", "subagent:" + agent])


class CostTierTest(unittest.TestCase):
    def test_a_hook_row_is_measured(self):
        priced = adt_cost.price_ledger([hook_row()], PRICES, ESTIMATOR)["ADT-224"]
        self.assertEqual(priced["tier"], "measured")

    def test_one_fallback_row_makes_the_ticket_estimated(self):
        priced = adt_cost.price_ledger([hook_row(), fallback_row()],
                                       PRICES, ESTIMATOR)["ADT-224"]
        self.assertEqual(priced["tier"], "estimated",
                         "expected one estimated row to make the ticket estimated")

    def test_ticket_tier_does_not_depend_on_row_order(self):
        a = adt_cost.price_ledger([hook_row(), fallback_row()], PRICES, ESTIMATOR)
        b = adt_cost.price_ledger([fallback_row(), hook_row()], PRICES, ESTIMATOR)
        self.assertEqual(a["ADT-224"]["tier"], b["ADT-224"]["tier"])

    def test_a_degraded_ticket_renders_approximate(self):
        """Any tier other than `measured` renders with a `~` prefix."""
        priced = adt_cost.price_ledger([hook_row(), fallback_row()],
                                       PRICES, ESTIMATOR)["ADT-224"]
        rendered = adt_cost.fmt_cost(priced["micros"], priced["tier"])
        self.assertTrue(rendered.startswith("~"), rendered)

        clean = adt_cost.price_ledger([hook_row()], PRICES, ESTIMATOR)["ADT-224"]
        self.assertFalse(
            adt_cost.fmt_cost(clean["micros"], clean["tier"]).startswith("~"))

    def test_the_fallback_row_still_contributes_money(self):
        with_fallback = adt_cost.price_ledger([hook_row(), fallback_row()],
                                              PRICES, ESTIMATOR)["ADT-224"]
        without = adt_cost.price_ledger([hook_row()], PRICES, ESTIMATOR)["ADT-224"]
        self.assertGreater(with_fallback["micros"], without["micros"])


if __name__ == "__main__":
    unittest.main()

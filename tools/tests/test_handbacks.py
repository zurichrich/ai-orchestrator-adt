# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests adt_metrics.handbacks, which counts per ticket how many turns ended
and handed back to the human, from the cost ledger. Subagent rows are not
handbacks."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import adt_metrics  # noqa: E402


def row(ts, tix, command=""):
    return "\t".join([ts, tix, "10", "20", "sess", "claude-opus-5", "standard",
                      "0", "0", "0", "measured", command])


def ledger(tmp, *rows):
    p = os.path.join(tmp, "cost-ledger.log")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
    return p


class HandbackTest(unittest.TestCase):
    def test_counts_more_than_one_handback(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = ledger(tmp,
                       row("2026-09-01T10:00:00Z", "ADT-9"),
                       row("2026-09-01T10:05:00Z", "ADT-9"),
                       row("2026-09-01T10:09:00Z", "ADT-9"))
            self.assertEqual(adt_metrics.handbacks(p)["ADT-9"], 3)

    def test_excludes_subagent_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = ledger(tmp,
                       row("2026-09-01T10:00:00Z", "ADT-9"),
                       row("2026-09-01T10:01:00Z", "ADT-9", "subagent:adt-design-reviewer"),
                       row("2026-09-01T10:02:00Z", "ADT-9", "subagent:adt-security-reviewer"),
                       row("2026-09-01T10:05:00Z", "ADT-9", "adt-build"))
            self.assertEqual(adt_metrics.handbacks(p)["ADT-9"], 2)

    def test_rows_in_one_turn_count_once(self):
        """A turn writes one row per (model, speed); they share a timestamp."""
        with tempfile.TemporaryDirectory() as tmp:
            p = ledger(tmp,
                       row("2026-09-01T10:00:00Z", "ADT-9"),
                       row("2026-09-01T10:00:00Z", "ADT-9"))
            self.assertEqual(adt_metrics.handbacks(p)["ADT-9"], 1)

    def test_a_ticket_with_only_subagent_rows_has_no_handbacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = ledger(tmp, row("2026-09-01T10:00:00Z", "ADT-9", "subagent:x"))
            self.assertEqual(adt_metrics.handbacks(p).get("ADT-9", 0), 0)

    def test_missing_ledger_is_not_an_error(self):
        self.assertEqual(adt_metrics.handbacks("/nonexistent/ledger.log"), {})

    def test_an_eleven_column_row_still_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = "\t".join(["2026-09-01T10:00:00Z", "ADT-9", "10", "20", "sess",
                             "claude-opus-5", "standard", "0", "0", "0", "measured"])
            p = ledger(tmp, old)
            self.assertEqual(adt_metrics.handbacks(p)["ADT-9"], 1)


if __name__ == "__main__":
    unittest.main()

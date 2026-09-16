# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for adt_rollup: each install writes one file per month and never
another install's, and a rollup holds only counts and the anonymous install id."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import adt_rollup as r  # noqa: E402

A = "aaaaaaaa-1111-4aaa-8aaa-aaaaaaaaaaaa"
B = "bbbbbbbb-2222-4bbb-8bbb-bbbbbbbbbbbb"


def row(ts, tix, install, command="adt-build", inp=10, out=20):
    return "\t".join([ts, tix, str(inp), str(out), "sess-secret",
                      "claude-opus-5", "standard", "300", "0", "0",
                      "measured", command, install])


class ScopeTest(unittest.TestCase):
    def test_each_install_writes_only_its_own_file(self):
        lines = [row("2026-09-01T10:00:00Z", "ADT-1", A),
                 row("2026-09-01T11:00:00Z", "ADT-2", B)]
        mine = r.aggregate(lines, A)
        self.assertEqual(sum(v[0] for v in mine.values()), 1,
                         "another install's rows leaked into our rollup")

    def test_two_installs_produce_two_files_and_no_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            lines = [row("2026-09-01T10:00:00Z", "ADT-1", A),
                     row("2026-09-01T11:00:00Z", "ADT-2", B)]
            r.write(d, A, r.aggregate(lines, A))
            r.write(d, B, r.aggregate(lines, B))
            names = sorted(os.listdir(d))
            self.assertEqual(names, ["%s-2026-09.tsv" % A, "%s-2026-09.tsv" % B])
            union = r.read_all(d)
            self.assertEqual(sorted({x["install"] for x in union}), sorted([A, B]))

    def test_a_second_install_needs_no_code_or_schema_change(self):
        """The same writer runs for three installs in one directory and the
        reader sees all three."""
        with tempfile.TemporaryDirectory() as d:
            for who in (A, B, "cccccccc-3333-4ccc-8ccc-cccccccccccc"):
                r.write(d, who, r.aggregate([row("2026-09-02T09:00:00Z", "ADT-9", who)], who))
            self.assertEqual(len(os.listdir(d)), 3)
            self.assertEqual(len({x["install"] for x in r.read_all(d)}), 3)

    def test_one_file_per_month(self):
        with tempfile.TemporaryDirectory() as d:
            lines = [row("2026-08-30T10:00:00Z", "ADT-1", A),
                     row("2026-09-01T10:00:00Z", "ADT-1", A)]
            r.write(d, A, r.aggregate(lines, A))
            self.assertEqual(sorted(os.listdir(d)),
                             ["%s-2026-08.tsv" % A, "%s-2026-09.tsv" % A])


class PrivacyTest(unittest.TestCase):
    def test_rollup_carries_no_ticket_or_session_id(self):
        """No ticket id, session id, model, path or tier reaches the rendered file."""
        lines = [row("2026-09-01T10:00:00Z", "ADT-224", A),
                 row("2026-09-01T11:00:00Z", "ADT-254", A, command="adt-plan")]
        body = r.render(r.aggregate(lines, A), A)
        for forbidden in ("ADT-224", "ADT-254", "sess-secret", "claude-opus-5",
                          "/Users", "measured"):
            self.assertNotIn(forbidden, body, "%r reached the rollup:\n%s"
                             % (forbidden, body))

    def test_the_columns_are_exactly_the_declared_set(self):
        self.assertEqual(r.HEADER,
                         ["date", "install", "command", "turns", "tokens", "micros"])


class AggregationTest(unittest.TestCase):
    def test_a_turn_is_a_timestamp_not_a_row(self):
        """Two rows with the same timestamp count as one turn."""
        same = "2026-09-01T10:00:00Z"
        rows = r.aggregate([row(same, "ADT-1", A), row(same, "ADT-1", A)], A)
        self.assertEqual(list(rows.values())[0][0], 1)

    def test_rows_without_an_install_column_are_included(self):
        """Rows with no install id come from this machine's ledger, so they
        count as this install's."""
        old = "\t".join(["2026-07-01T10:00:00Z", "ADT-1", "10", "20", "s",
                         "claude-opus-5", "standard", "0", "0", "0", "measured"])
        self.assertEqual(sum(v[0] for v in r.aggregate([old], A).values()), 1)

    def test_rerunning_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            rows = r.aggregate([row("2026-09-01T10:00:00Z", "ADT-1", A)], A)
            self.assertEqual(len(r.write(d, A, rows)), 1)
            self.assertEqual(r.write(d, A, rows), [],
                             "an unchanged month was rewritten")


class ReadTest(unittest.TestCase):
    def test_a_malformed_row_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            good = os.path.join(d, "%s-2026-09.tsv" % A)
            with open(good, "w", encoding="utf-8") as fh:
                fh.write("\t".join(r.HEADER) + "\n")
                fh.write("2026-09-01\t%s\tadt-build\t1\t30\t100\n" % A)
                fh.write("this row is nonsense\n")
                fh.write("2026-09-02\t%s\tadt-plan\tNOTANUMBER\t1\t1\n" % A)
            rows = r.read_all(d)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["command"], "adt-build")

    def test_a_missing_directory_reads_empty(self):
        self.assertEqual(r.read_all("/nonexistent/rollups"), [])


if __name__ == "__main__":
    unittest.main()

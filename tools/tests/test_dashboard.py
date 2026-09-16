# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for build_dashboard: coverage labels on every panel, the unattributed
and unmapped buckets, histograms, and no network access.
"""
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_dashboard as d  # noqa: E402

A = "aaaaaaaa-1111-4aaa-8aaa-aaaaaaaaaaaa"
B = "bbbbbbbb-2222-4bbb-8bbb-bbbbbbbbbbbb"

COVERAGE_RE = re.compile(r'data-coverage="(\d+) of (\d+)"')


def row(install, command="adt-build", tix="ADT-1", micros=1000, turns=1, tokens=30):
    return {"date": "2026-09-01", "install": install, "command": command,
            "tix": tix, "turns": turns, "tokens": tokens, "micros": micros}


class CoverageTest(unittest.TestCase):
    def test_every_panel_carries_coverage(self):
        page = d.render([row(A)], [A], 1, 1)
        panels = page.count('<section class="panel"')
        self.assertGreater(panels, 0)
        self.assertEqual(len(COVERAGE_RE.findall(page)), panels,
                         "a panel rendered without a data-coverage value")

    def test_single_install_reads_one_of_one(self):
        page = d.render([row(A)], [A], 1, 1)
        self.assertIn("1 install<", page)
        self.assertIn('data-coverage="1 of 1"', page)

    def test_two_installs_read_two_of_two(self):
        page = d.render([row(A), row(B, tix="ADT-2")], [A, B], 2, 2)
        self.assertIn("2 installs<", page)
        self.assertIn('data-coverage="2 of 2"', page)

    def test_partial_coverage_is_visible(self):
        page = d.render([row(A)], [A], 3, 42)
        self.assertIn('data-coverage="3 of 42"', page)


class BucketTest(unittest.TestCase):
    def test_unattributed_rows_are_a_named_bucket(self):
        page = d.render([row(A), row(d.UNATTRIBUTED)], [A, d.UNATTRIBUTED], 2, 2)
        self.assertIn("unattributed", page)

    def test_unattributed_rows_are_excluded_from_the_per_install_mean(self):
        rows = [row(A, micros=1000), row(d.UNATTRIBUTED, micros=9000, tix="ADT-2")]
        per_install = d.by_key(rows, lambda r: r["install"])
        self.assertEqual(per_install[A][2], 1000,
                         "unattributed spend leaked into a real install's total")
        self.assertEqual(per_install[d.UNATTRIBUTED][2], 9000)
        self.assertNotIn(d.UNATTRIBUTED, [A],
                         "the bucket must be its own key, not merged")

    def test_unmapped_commands_are_a_named_lane_bucket(self):
        """Commands that belong to no single lane go in the unmapped bucket."""
        for command in ("adt-decide", "adt-unblock", "adt-review-security",
                        "adt-build-todone", "subagent:adt-design-reviewer", ""):
            self.assertEqual(d.lane_of(command), d.UNMAPPED, command)
        page = d.render([row(A, command="adt-decide")], [A], 1, 1)
        self.assertIn("unmapped", page)

    def test_per_lane_and_per_command_totals_are_correct(self):
        rows = [row(A, command="adt-build", micros=100),
                row(A, command="adt-build", micros=200, tix="ADT-2"),
                row(A, command="adt-qa-run", micros=50, tix="ADT-3")]
        lanes = d.by_key(rows, lambda r: d.lane_of(r["command"]))
        self.assertEqual(lanes["building"][2], 300)
        self.assertEqual(lanes["qa"][2], 50)
        cmds = d.by_key(rows, lambda r: r["command"])
        self.assertEqual(cmds["adt-build"][2], 300)
        self.assertEqual(cmds["adt-qa-run"][2], 50)


class DistributionTest(unittest.TestCase):
    def test_distribution_is_rendered_as_a_histogram(self):
        page = d.render([row(A, micros=100), row(A, micros=5000, tix="ADT-2")],
                        [A], 2, 2)
        self.assertIn("<svg", page)
        self.assertIn("<rect", page)

    def test_a_single_value_is_one_bar_not_a_crash(self):
        self.assertEqual(d.histogram([5]), [(5, 5, 1)])

    def test_every_value_lands_in_exactly_one_bin(self):
        vals = [1, 2, 3, 10, 10, 99]
        self.assertEqual(sum(b[2] for b in d.histogram(vals)), len(vals),
                         "a value fell outside every bin, or was counted twice")

    def test_no_data_is_not_a_crash(self):
        self.assertEqual(d.histogram([]), [])
        self.assertIn("no data yet", d.render([], [A], 0, 0))


class NoNetworkTest(unittest.TestCase):
    def test_dashboard_makes_no_network_calls(self):
        """Patches socket.socket to raise, so any connection attempt fails the test."""
        import socket

        real = socket.socket

        class Blocked(Exception):
            pass

        def explode(*a, **k):
            raise Blocked("the dashboard opened a socket")

        socket.socket = explode
        try:
            with tempfile.TemporaryDirectory() as tmp:
                state = os.path.join(tmp, ".adt", "state")
                os.makedirs(state)
                with open(os.path.join(state, "install-id"), "w") as fh:
                    fh.write(A + "\n")
                with open(os.path.join(state, "cost-ledger.log"), "w") as fh:
                    fh.write("\t".join(["2026-09-01T10:00:00Z", "ADT-1", "10", "20",
                                        "s", "claude-opus-5", "standard", "0", "0",
                                        "0", "measured", "adt-build", A]) + "\n")
                rows, installs = d.load(tmp, os.path.join(state, "rollup"))
                page = d.render(rows, installs, 1, 1)
                self.assertIn("data-coverage=", page)
        finally:
            socket.socket = real

    def test_patching_socket_blocks_a_socket_call(self):
        import socket

        real = socket.socket
        socket.socket = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked"))
        try:
            with self.assertRaises(RuntimeError):
                socket.socket()
        finally:
            socket.socket = real


if __name__ == "__main__":
    unittest.main()

# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests which lane adt_lane_cost puts a ledger turn in when the lane stamp
(column 14), the command (column 12) and the board labels disagree.
"""
import datetime as dt
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import adt_lane_cost as lc  # noqa: E402


def _t(hour):
    return dt.datetime(2026, 9, 1, hour, 0, 0, tzinfo=dt.timezone.utc)


def _row(hour, tix, command="", lane=None):
    base = ("2026-09-01T%02d:00:00Z\t%s\t10\t20\tsess\t"
            "claude-opus-5\tstandard\t30\t0\t0\tmeasured" % (hour, tix))
    if lane is None:
        return base + ("\t" + command if command else "")
    # A lane-stamped row is full width: command (12), install (13), lane (14).
    return base + "\t" + command + "\tinstall-x\t" + lane


# The board says this ticket sat in `ideas` the whole time.
IDEAS_ONLY = [(_t(0), None, "ideas")]


class LaneStampTest(unittest.TestCase):
    """Column 14 is the ticket's lane at the time of the turn. It wins over
    both the command column and the board labels."""

    def test_the_stamp_wins_over_a_disagreeing_command(self):
        # The command says `adt-brief` but the ticket was in `qa`.
        rows = [_row(1, "ADT-9", "adt-brief", lane="qa")]
        buckets = lc.bucket_rows(rows, "ADT-9", IDEAS_ONLY)
        self.assertEqual(sorted(buckets), ["qa"])

    def test_the_stamp_wins_over_a_disagreeing_label(self):
        rows = [_row(1, "ADT-9", "", lane="building")]
        buckets = lc.bucket_rows(rows, "ADT-9", IDEAS_ONLY)
        self.assertEqual(sorted(buckets), ["building"])
        self.assertNotIn("ideas", buckets)

    def test_an_empty_stamp_falls_back_to_the_command(self):
        rows = [_row(1, "ADT-9", "adt-build", lane="")]
        buckets = lc.bucket_rows(rows, "ADT-9", IDEAS_ONLY)
        self.assertEqual(sorted(buckets), ["building"])

    def test_an_unrecognised_stamp_falls_back_rather_than_inventing_a_bucket(self):
        rows = [_row(1, "ADT-9", "adt-build", lane="not-a-lane")]
        buckets = lc.bucket_rows(rows, "ADT-9", IDEAS_ONLY)
        self.assertEqual(sorted(buckets), ["building"])
        self.assertNotIn("not-a-lane", buckets)

    def test_no_row_is_lost_whichever_path_it_takes(self):
        rows = [_row(1, "ADT-9", "adt-brief", lane="qa"),
                _row(2, "ADT-9", "adt-build", lane=""),
                _row(3, "ADT-9", "", lane="not-a-lane"),
                _row(4, "ADT-9", "")]
        buckets = lc.bucket_rows(rows, "ADT-9", IDEAS_ONLY)
        self.assertEqual(sum(len(v) for v in buckets.values()), 4)


class ColumnPreferenceTest(unittest.TestCase):
    def test_column_12_wins_over_a_disagreeing_label(self):
        rows = [_row(1, "ADT-9", "adt-build"), _row(2, "ADT-9", "adt-qa-run")]
        buckets = lc.bucket_rows(rows, "ADT-9", IDEAS_ONLY)
        self.assertEqual(sorted(buckets), ["building", "qa"])
        self.assertNotIn("ideas", buckets)

    def test_built_ticket_shows_more_building_than_ideas(self):
        rows = [_row(1, "ADT-9", "adt-brief")] + \
               [_row(h, "ADT-9", "adt-build") for h in range(2, 8)]
        buckets = lc.bucket_rows(rows, "ADT-9", IDEAS_ONLY)
        self.assertGreater(len(buckets.get("building", [])),
                           len(buckets.get("ideas", [])),
                           "expected more turns in building than in ideas")

    def test_unmapped_command_falls_back_to_labels(self):
        """`decide`, `unblock` and the three `review-*` commands name no lane."""
        for command in ("adt-decide", "adt-unblock", "adt-review-security",
                        "adt-review-designer", "adt-review-critical-path"):
            buckets = lc.bucket_rows([_row(3, "ADT-9", command)], "ADT-9", IDEAS_ONLY)
            self.assertEqual(list(buckets), ["ideas"], command)

    def test_build_todone_turns_are_not_counted_as_ideas(self):
        """`build-todone` spans several lanes, so it maps to none and falls back to labels."""
        self.assertIsNone(lc.command_lane("adt-build-todone"))
        buckets = lc.bucket_rows([_row(3, "ADT-9", "adt-build-todone")],
                                 "ADT-9", IDEAS_ONLY)
        self.assertEqual(list(buckets), ["ideas"],
                         "an unmapped command should fall back to the labels")

    def test_a_row_with_no_command_falls_back(self):
        buckets = lc.bucket_rows([_row(3, "ADT-9")], "ADT-9", IDEAS_ONLY)
        self.assertEqual(list(buckets), ["ideas"])

    def test_subagent_rows_fall_back_rather_than_forming_a_lane(self):
        """`subagent:<type>` rows take the lane from the labels."""
        self.assertIsNone(lc.command_lane("subagent:adt-plan-quality-reviewer"))
        buckets = lc.bucket_rows([_row(3, "ADT-9", "subagent:adt-design-reviewer")],
                                 "ADT-9", IDEAS_ONLY)
        self.assertEqual(list(buckets), ["ideas"])

    def test_no_row_is_lost_whichever_path_it_takes(self):
        rows = [_row(1, "ADT-9", "adt-build"), _row(2, "ADT-9", "adt-decide"),
                _row(3, "ADT-9"), _row(4, "ADT-9", "subagent:x"),
                _row(5, "ADT-9", "adt-close")]
        buckets = lc.bucket_rows(rows, "ADT-9", IDEAS_ONLY)
        self.assertEqual(sum(len(v) for v in buckets.values()), len(rows))

    def test_the_prefix_is_optional(self):
        self.assertEqual(lc.command_lane("build"), "building")
        self.assertEqual(lc.command_lane("adt-build"), "building")

    def test_every_mapped_lane_is_a_real_stage(self):
        stages = {"ideas", "planned", "building", "qa", "blocked",
                  "ready-to-release", "done"}
        self.assertTrue(set(lc.COMMAND_LANE.values()) <= stages,
                        set(lc.COMMAND_LANE.values()) - stages)

    def test_every_mapped_command_is_a_shipped_command(self):
        shipped = {p[:-3] for p in os.listdir(os.path.join(ROOT, "commands"))
                   if p.endswith(".md")}
        self.assertTrue(set(lc.COMMAND_LANE) <= shipped,
                        set(lc.COMMAND_LANE) - shipped)


if __name__ == "__main__":
    unittest.main()

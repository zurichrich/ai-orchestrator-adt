# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests to_issue on tickets with no title, and the close-reason map from the
API form (`not_planned`) to the gh CLI form (`not planned`)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from ticket_serializer import parse_md, to_issue  # noqa: E402


class TestTitleFallback(unittest.TestCase):
    def test_no_title_or_h1_gives_none_and_keeps_the_slug(self):
        # to_issue returns no title here; the reconciler falls back to the slug.
        md = "---\nslug: orphan-ticket\nstage: done\nissue_number: 9\n---\n\nNo heading here, just prose.\n"
        d = parse_md(md)
        issue = to_issue(d)
        self.assertIsNone(issue["title"])
        self.assertEqual(d["slug"], "orphan-ticket")


class TestCloseReasonMapping(unittest.TestCase):
    """The API-to-CLI reason map used in reconcile_one's close path."""

    REASON_MAP = {"not_planned": "not planned",
                  "completed": "completed",
                  "duplicate": "duplicate"}

    def test_cancelled_maps_to_not_planned_human_form(self):
        d = parse_md("---\nslug: c\nstage: building\nstatus: cancelled\n"
                     "issue_number: 5\nstate_reason: not_planned\n---\n\n# C\n")
        issue = to_issue(d)
        self.assertEqual(issue["stateReason"], "not_planned")
        self.assertEqual(self.REASON_MAP[issue["stateReason"]], "not planned")

    def test_done_maps_to_completed(self):
        d = parse_md("---\nslug: d\nstage: done\nissue_number: 6\n---\n\n# D\n")
        issue = to_issue(d)
        self.assertEqual(issue["stateReason"], "completed")
        self.assertEqual(self.REASON_MAP[issue["stateReason"]], "completed")


if __name__ == "__main__":
    unittest.main(verbosity=2)

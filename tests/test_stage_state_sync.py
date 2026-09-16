# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that build_kanban.sync_stage_frontmatter keeps `state:` (open or
closed) in step with the ticket's folder."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import build_kanban  # noqa: E402


def _item(tmp, status, frontmatter_extra=""):
    """Write a brief into <tmp>/<type>/<status>/ and return a built Item."""
    d = os.path.join(tmp, "bugs", status)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "t.md")
    with open(path, "w") as f:
        f.write(f"---\nslug: t\nstage: {status}\n{frontmatter_extra}---\n\n# T\n")
    it = build_kanban.Item.__new__(build_kanban.Item)
    it.path = __import__("pathlib").Path(path)
    it.text = open(path).read()
    it.status = status
    it.fm = build_kanban.parse_frontmatter(it.text)
    return it


class TestStateLockstep(unittest.TestCase):
    def test_synced_ticket_keeps_state_open_in_an_active_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            it = _item(tmp, "building",
                       "state: open\nissue_number: 5\nstate_reason: null\n")
            build_kanban.sync_stage_frontmatter([it])
            self.assertIn("state: open", it.path.read_text())

    def test_done_lane_forces_state_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            it = _item(tmp, "done",
                       "state: open\nissue_number: 5\nstate_reason: null\n")
            build_kanban.sync_stage_frontmatter([it])
            self.assertIn("state: closed", it.path.read_text())

    def test_cancelled_status_forces_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            it = _item(tmp, "building",
                       "status: cancelled\nstate: open\nissue_number: 5\n")
            build_kanban.sync_stage_frontmatter([it])
            self.assertIn("state: closed", it.path.read_text())

    def test_ticket_without_issue_fields_gets_no_state_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            it = _item(tmp, "ideas", "priority: P1\n")
            build_kanban.sync_stage_frontmatter([it])
            self.assertNotIn("state:", it.path.read_text())

    def test_second_sync_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            it = _item(tmp, "building",
                       "state: open\nissue_number: 5\nstate_reason: null\n")
            build_kanban.sync_stage_frontmatter([it])
            after_first = it.path.read_text()
            it.text = after_first
            it.fm = build_kanban.parse_frontmatter(after_first)
            changed = build_kanban.sync_stage_frontmatter([it])
            self.assertEqual(changed, 0, "second sync must be a no-op")


if __name__ == "__main__":
    unittest.main(verbosity=2)

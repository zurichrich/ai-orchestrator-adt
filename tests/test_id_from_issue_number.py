# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that `build_kanban.assign_missing_ids` sets a ticket's id to
<PREFIX>-<issue_number>."""

import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import build_kanban  # noqa: E402


def _write(d: pathlib.Path, slug: str, frontmatter: str) -> None:
    (d / f"{slug}.md").write_text(f"---\nslug: {slug}\n{frontmatter}---\n\n# {slug}\n")


class IdFromIssueNumber(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        build_kanban.configure(self.tmp, id_prefix="ADT")
        self.ideas = pathlib.Path(build_kanban.BL) / "enhancements" / "ideas"
        self.ideas.mkdir(parents=True, exist_ok=True)

    def _ids(self):
        return {it.slug: it.id for it in build_kanban.load_items()}

    def test_high_issue_number_yields_matching_id(self):
        _write(self.ideas, "x", "title: X\nstage: ideas\nissue_number: 31\n")
        n = build_kanban.assign_missing_ids(build_kanban.load_items())
        self.assertEqual(n, 1)
        self.assertEqual(self._ids()["x"], "ADT-031")

    def test_unsynced_ticket_left_unlabelled(self):
        _write(self.ideas, "y", "title: Y\nstage: ideas\n")  # no issue_number
        build_kanban.assign_missing_ids(build_kanban.load_items())
        self.assertEqual(self._ids()["y"], "")

    def test_existing_id_not_overwritten(self):
        _write(self.ideas, "z", "id: ADT-005\ntitle: Z\nstage: ideas\nissue_number: 99\n")
        n = build_kanban.assign_missing_ids(build_kanban.load_items())
        self.assertEqual(n, 0)            # already has an id → untouched
        self.assertEqual(self._ids()["z"], "ADT-005")


if __name__ == "__main__":
    unittest.main()

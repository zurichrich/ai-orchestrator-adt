# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests adt_dod.dod_amendments(), which counts how many times a ticket's DoD
changed, using its `dod_snapshot` digests."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import adt_dod  # noqa: E402

FRONT = """---
slug: t
id: ADT-998
title: T
type: enhancement
priority: P1
size: S
stage: building
state: open
created: 2026-09-01
created_by: po
done_evidence:
{conds}---

# T
"""


def ticket(tmp, conds):
    path = os.path.join(tmp, "t.md")
    block = "".join("  - must_run: '%s'\n    lane: build\n" % c for c in conds)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(FRONT.format(conds=block))
    return path


def amend(path, conds):
    """Rewrite the DoD with ticket_serializer, which keeps the `dod_snapshot` history."""
    import ticket_serializer
    data = ticket_serializer.parse_md(open(path, encoding="utf-8").read())
    data["done_evidence"] = [{"must_run": c, "lane": "build"} for c in conds]
    open(path, "w", encoding="utf-8").write(ticket_serializer.emit_md(data))


class AmendmentTest(unittest.TestCase):
    def test_each_change_to_the_dod_adds_one_amendment(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, ["true"])
            adt_dod.snapshot_dod(t)
            self.assertEqual(adt_dod.dod_amendments(t), 0)

            amend(t, ["true", "test -f README.md"])
            adt_dod.snapshot_dod(t)
            self.assertEqual(adt_dod.dod_amendments(t), 1)

            amend(t, ["true", "test -f README.md", "test -d tools"])
            adt_dod.snapshot_dod(t)
            self.assertEqual(adt_dod.dod_amendments(t), 2)

    def test_an_unchanged_dod_is_zero_amendments(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = ticket(tmp, ["true"])
            for _ in range(4):
                adt_dod.snapshot_dod(t)
            self.assertEqual(adt_dod.dod_amendments(t), 0)

    def test_a_ticket_never_snapshotted_reads_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(adt_dod.dod_amendments(ticket(tmp, ["true"])), 0)


if __name__ == "__main__":
    unittest.main()

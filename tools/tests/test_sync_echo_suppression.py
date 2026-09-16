# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests adt_sync._push_hash, which leaves out the `_PULL_OWNED` fields so a
pull that only writes those fields does not trigger a push, while a real
content change still does."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_sync  # noqa: E402
from ticket_serializer import parse_md, emit_md  # noqa: E402

TICKET = """---
slug: sample
title: Sample
type: bug
priority: P1
stage: building
state: open
created: 2026-08-01
updated: 2026-08-01T09:00:00Z
assignees: []
issue_number: 58
issue_node_id: NODE58
comments: []
---

# Sample

Body text.
"""


def _data():
    return parse_md(TICKET)


# --- pull-owned fields do not change the push hash -------------------------

def test_pull_owned_write_does_not_rearm_the_push():
    base = adt_sync._push_hash(_data())
    for field, value in (
        ("updated", "2099-01-01T00:00:00Z"),
        ("assignees", [{"login": "someone"}]),
        ("issue_number", 999),
        ("issue_node_id", "NODE_OTHER"),
    ):
        d = _data()
        d[field] = value
        assert adt_sync._push_hash(d) == base, (
            f"{field} is pull-owned; changing it should not change the push hash")


def test_every_pull_owned_field_is_excluded():
    # Loops over _PULL_OWNED, so a newly added field is covered automatically.
    base = adt_sync._push_hash(_data())
    for field in adt_sync._PULL_OWNED:
        d = _data()
        d[field] = "MUTATED-%s" % field
        assert adt_sync._push_hash(d) == base, field


# --- content fields do change the push hash --------------------------------

def test_real_content_changes_still_rearm_the_push():
    base = adt_sync._push_hash(_data())
    for field, value in (
        ("stage", "qa"),
        ("title", "Something else"),
        ("state", "closed"),
        ("priority", "P0"),
        ("body", "# Sample\n\nDifferent body.\n"),
    ):
        d = _data()
        d[field] = value
        assert adt_sync._push_hash(d) != base, (
            f"{field} is push-owned; changing it should change the push hash")


def test_hash_is_stable_across_an_emit_roundtrip():
    # The next tick hashes the re-parsed file, so emit then parse must not change it.
    d = _data()
    assert adt_sync._push_hash(d) == adt_sync._push_hash(parse_md(emit_md(d)))


# --- two machines pulling the same Issue -----------------------------------

def test_two_machines_pulling_a_new_updated_date_never_push(tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    for p in (a, b):
        p.write_text(TICKET)

    state = {str(a): adt_sync._push_hash(parse_md(a.read_text())),
             str(b): adt_sync._push_hash(parse_md(b.read_text()))}

    pushes = 0
    for tick in range(10):
        # A register upsert moved the Issue; both machines pull it.
        for p in (a, b):
            d = parse_md(p.read_text())
            d["updated"] = "2026-08-24T18:%02d:00Z" % tick
            p.write_text(emit_md(d))
        # Each machine's push loop decides from its own hash.
        for p in (a, b):
            h = adt_sync._push_hash(parse_md(p.read_text()))
            if state[str(p)] != h:
                pushes += 1
                state[str(p)] = h

    assert pushes == 0, f"{pushes} push(es) triggered by pull-only writes"


def test_a_local_stage_change_still_changes_the_push_hash(tmp_path):
    p = tmp_path / "t.md"
    p.write_text(TICKET)
    state = adt_sync._push_hash(parse_md(p.read_text()))

    d = parse_md(p.read_text())
    d["stage"] = "qa"
    p.write_text(emit_md(d))

    h = adt_sync._push_hash(parse_md(p.read_text()))
    assert h != state, "a real stage move must reach the Issue"

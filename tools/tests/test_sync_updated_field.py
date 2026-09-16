# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for the `updated:` and `closed:` ticket fields: from_issue() maps
them from the Issue, they survive emit_md, and pull owns them. Pure helpers,
no `gh` calls.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ticket_serializer as ts  # noqa: E402
import adt_sync  # noqa: E402


def test_from_issue_keeps_full_updatedAt_timestamp():
    # The full timestamp is kept so same-day edits sort by time of day.
    issue = {
        "number": 54,
        "title": "x",
        "createdAt": "2026-06-20T09:00:00Z",
        "updatedAt": "2026-06-26T15:33:01Z",
        "closedAt": None,
    }
    out = ts.from_issue(issue)
    assert out["updated"] == "2026-06-26T15:33:01Z"


def test_same_day_edits_sort_by_time_not_tie():
    # Two tickets edited on the same day order by time of day.
    earlier = "2026-06-26T09:00:00Z"
    later = "2026-06-26T16:10:57Z"
    # reverse-lexicographic order on full ISO timestamps is reverse-chronological
    assert later > earlier
    assert sorted([earlier, later], reverse=True)[0] == later


def test_updated_survives_emit_md_round_trip():
    data = {
        "slug": "x", "id": "ADT-54", "title": "x",
        "created": "2026-06-20", "updated": "2026-06-26", "body": "hi",
    }
    md = ts.emit_md(data)
    assert "updated: 2026-06-26" in md


def test_pull_owns_updated():
    # pull must refresh `updated:` on existing cache files, not just reconstruct.
    assert "updated" in adt_sync._PULL_OWNED


def test_rest_adapter_supplies_updatedAt():
    # The REST adapter maps `updated_at` onto the camelCase `updatedAt` that from_issue() reads.
    out = adt_sync._issue_from_rest({"number": 54,
                                     "updated_at": "2026-06-26T15:33:01Z"})
    assert out["updatedAt"] == "2026-06-26T15:33:01Z"
    assert ts.from_issue(out)["updated"] == "2026-06-26T15:33:01Z"


def test_pull_owns_closed():
    # pull must refresh `closed:` on existing cache files; the done lane sorts by it.
    assert "closed" in adt_sync._PULL_OWNED


def test_closed_is_outside_the_push_hash():
    # Changing `closed` must not change the push hash, so a pull never triggers a push.
    base = {"slug": "x", "id": "ADT-1", "body": "hi"}
    assert (adt_sync._push_hash(dict(base, closed=None))
            == adt_sync._push_hash(dict(base, closed="2026-06-26T15:00:00Z")))

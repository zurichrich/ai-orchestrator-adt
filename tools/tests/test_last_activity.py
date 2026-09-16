# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests last_activity, which rebuilds each Issue's true last-activity date
into a per-repo table, and the board's use of that table in place of the
`updatedAt` date GitHub stamped when a backlog was adopted."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import build_kanban as bk  # noqa: E402
import last_activity as la  # noqa: E402
import adt_sync  # noqa: E402

HDR = "issue\tupdated_at_now\ttrue_last_activity\tbasis\n"


def _table(tmp_path, repo="zurichrich/proj", rows=("7\tX\t2026-06-01T12:00:00Z\tclosed",)):
    p = la.table_path(tmp_path, repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(HDR + "\n".join(rows) + "\n")
    return p


# -- the table ------------------------------------------------------------

def test_load_reads_the_table_and_skips_the_header(tmp_path):
    _table(tmp_path, rows=("7\tX\t2026-06-01T12:00:00Z\tclosed",
                           "9\tX\t2026-07-02T08:00:00Z\tlast comment"))
    assert la.load(tmp_path, "zurichrich/proj") == {
        7: "2026-06-01T12:00:00Z", 9: "2026-07-02T08:00:00Z"}


def test_load_is_empty_and_silent_when_there_is_no_table(tmp_path):
    assert la.load(tmp_path, "zurichrich/nothing-here") == {}


def test_load_survives_a_malformed_table(tmp_path):
    _table(tmp_path, rows=("not-a-number\tX\t2026-06-01T12:00:00Z\tx",
                           "short-row",
                           "8\tX\t2026-06-02T12:00:00Z\tok"))
    assert la.load(tmp_path, "zurichrich/proj") == {8: "2026-06-02T12:00:00Z"}


def test_load_stamps_reads_the_stamp_column(tmp_path):
    _table(tmp_path, rows=("7\t2026-09-09T08:00:00Z\t2026-06-01T12:00:00Z\tclosed",
                           "9\t\t2026-07-02T08:00:00Z\tlast comment",
                           "short-row"))
    # Only the row that HAS a stamp; an empty or truncated one is dropped, the
    # same way load() drops a missing date rather than raising.
    assert la.load_stamps(tmp_path, "zurichrich/proj") == {
        7: "2026-09-09T08:00:00Z"}
    assert la.load_stamps(tmp_path, "zurichrich/nothing-here") == {}


def test_the_table_is_keyed_by_repo_not_project(tmp_path):
    # Two project configs pointing at one repo share one table.
    assert la.table_path(tmp_path, "o/r").name == "o-r.tsv"


# -- the board ------------------------------------------------------------

def _item(tmp_path, fm_extra="", status="ideas"):
    md = tmp_path / "t.md"
    md.write_text("---\nid: T-1\ntitle: t\nissue_number: 7\n"
                  "updated: 2026-09-09T10:00:00Z\ncreated: 2026-01-01T00:00:00Z\n"
                  + fm_extra + "---\n\nbody\n")
    return bk.Item(md, "bugs", status)


def test_board_prefers_a_repaired_date_over_the_stamped_one(tmp_path, monkeypatch):
    monkeypatch.setattr(bk, "_LAST_ACTIVITY", {7: "2026-06-01T12:00:00Z"})
    assert _item(tmp_path).updated.startswith("2026-06-01")


def test_an_untouched_ticket_keeps_its_repaired_date(tmp_path, monkeypatch):
    # `updated:` still EQUALS the stamp the adoption wrote, so nobody has
    # touched the ticket since the table was built and the repair still holds.
    monkeypatch.setattr(bk, "_LAST_ACTIVITY", {7: "2026-06-01T12:00:00Z"})
    monkeypatch.setattr(bk, "_ADOPTION_STAMPS", {7: "2026-09-09T10:00:00Z"})
    assert _item(tmp_path).updated.startswith("2026-06-01")


def test_a_ticket_edited_after_the_stamp_uses_its_updated_date(tmp_path, monkeypatch):
    # The edit is later than the stamp, so it is real activity the
    # reconstruction never saw and the repaired date is the stale one.
    monkeypatch.setattr(bk, "_LAST_ACTIVITY", {7: "2026-06-01T12:00:00Z"})
    monkeypatch.setattr(bk, "_ADOPTION_STAMPS", {7: "2026-09-08T10:00:00Z"})
    assert _item(tmp_path).updated.startswith("2026-09-09")


def test_a_repaired_date_stands_when_the_row_has_no_usable_stamp(tmp_path, monkeypatch):
    # A row whose stamp column is empty or unparseable behaves as it did before
    # ADT-381: without the stamp there is nothing to compare `updated:` against.
    monkeypatch.setattr(bk, "_LAST_ACTIVITY", {7: "2026-06-01T12:00:00Z"})
    monkeypatch.setattr(bk, "_ADOPTION_STAMPS", {})
    assert _item(tmp_path).updated.startswith("2026-06-01")
    monkeypatch.setattr(bk, "_ADOPTION_STAMPS", {7: "garbage"})
    assert _item(tmp_path).updated.startswith("2026-06-01")


def test_board_falls_back_when_the_ticket_is_not_in_the_table(tmp_path, monkeypatch):
    monkeypatch.setattr(bk, "_LAST_ACTIVITY", {999: "2026-06-01T12:00:00Z"})
    assert _item(tmp_path).updated.startswith("2026-09-09")


def test_board_is_unchanged_when_no_table_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(bk, "_LAST_ACTIVITY", {})
    assert _item(tmp_path).updated.startswith("2026-09-09")


def test_done_lane_still_sorts_by_closed(tmp_path, monkeypatch):
    # The done lane sorts by `closed`, which the repaired dates do not change.
    monkeypatch.setattr(bk, "_LAST_ACTIVITY", {7: "2026-06-01T12:00:00Z"})
    it = _item(tmp_path, fm_extra="closed: 2026-03-03T00:00:00Z\n", status="done")
    assert it.recency_ts.startswith("2026-03-03")


# -- the reconstruction ---------------------------------------------------

def test_a_trailer_only_edit_is_not_activity():
    body = "Description of Epic 1"
    assert la._strip_trailer(body + "\n\n<!-- adt: slug=issue-1 -->\n") == body


def test_edits_are_walked_oldest_first(tmp_path):
    # The API returns edits newest-first. Walking them oldest-first means the
    # real edit before the trailer-only edit is still found.
    seq = [("2026-09-09T16:19:48Z", "BIG\n\n<!-- adt: slug=x -->\n"),
           ("2026-09-09T09:15:36Z", "BIG"),
           ("2026-06-26T18:14:47Z", "small")]
    walked = sorted(seq, key=lambda e: e[0])
    assert [e[0] for e in walked][0].startswith("2026-06-26")
    prev, hits = None, []
    for at, body in walked:
        cur = la._strip_trailer(body)
        if prev is not None and cur != prev:
            hits.append(at)
        prev = cur
    assert hits == ["2026-09-09T09:15:36Z"]


def test_label_churn_inside_an_adoption_window_is_discounted():
    w = [la._parse_window("2026-09-09T07:55:00Z..2026-09-09T08:35:00Z")]
    assert la._in_any(la._iso("2026-09-09T07:59:34Z"), w)
    assert not la._in_any(la._iso("2026-09-09T09:15:36Z"), w)


def test_a_window_is_required_and_never_defaulted():
    # An adoption window is specific to one install, so --window has no default.
    import argparse
    try:
        la.main(["reconstruct", "--repo", "o/r"])
    except SystemExit as e:
        assert e.code != 0
    except argparse.ArgumentError:
        pass
    else:
        raise AssertionError("reconstruct ran with no --window")


def test_board_plumbing_is_not_activity():
    assert "added_to_project_v2" in la._NOT_ACTIVITY
    assert "cross-referenced" in la._NOT_ACTIVITY


# -- the sync is unchanged -----------------------------------------------------

def test_the_repair_adds_no_frontmatter_field_to_the_push_hash():
    # A last_activity frontmatter key would change the push hash and mark every
    # ticket dirty, which is why the dates live in a table.
    base = {"slug": "s", "title": "t", "body": "b"}
    with_field = dict(base, last_activity="2026-06-01T12:00:00Z")
    assert adt_sync._push_hash(base) != adt_sync._push_hash(with_field)
    # In _PULL_OWNED it would be cleared on every pull, since GitHub has no value.
    assert "last_activity" not in adt_sync._PULL_OWNED


# -- a project's table is never stored in the ADT checkout ------------------

def test_no_recovered_backlog_is_committed_in_adt():
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    stray = []
    for dirpath, _dirs, files in os.walk(root):
        if os.sep + ".git" in dirpath:
            continue
        for f in files:
            if f.endswith(".tsv") and "last-activity" in (dirpath + "/" + f):
                stray.append(os.path.relpath(os.path.join(dirpath, f), root))
    assert stray == [], (
        "recovered-backlog tables must live in ~/.adt/<project>/, not in the "
        f"ADT checkout: {stray}")


def test_the_table_is_looked_up_beside_the_cache_not_in_adt(monkeypatch, tmp_path):
    # adt_watch renders with project_root=<cache>, so the project dir is its parent.
    cache = tmp_path / "myproject" / "cache"
    cache.mkdir(parents=True)
    monkeypatch.setattr(bk, "REPO", cache)
    assert bk._project_state_dir() == tmp_path / "myproject"
    monkeypatch.setattr(bk, "REPO", tmp_path / "plain")
    assert bk._project_state_dir() == tmp_path / "plain"

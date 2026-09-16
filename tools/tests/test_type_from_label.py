# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests the `type:` label round trip: from_issue() reads it, to_issue()
writes it, _cache_path_for() buckets by it, and Issues with no type are
summarised in one line. Pure helpers, no `gh` calls.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ticket_serializer as ts  # noqa: E402
import adt_sync  # noqa: E402


def _issue(labels):
    return {
        "number": 73,
        "title": "x",
        "createdAt": "2026-06-27T09:00:00Z",
        "updatedAt": "2026-06-27T09:00:00Z",
        "labels": [{"name": n} for n in labels],
    }


# ── reading the type from the label ─────────────────────────────────────────

def test_type_bug_label_sets_type_not_tags():
    out = ts.from_issue(_issue(["type:bug"]))
    assert out.get("type") == "bug"            # singular, as _cache_path_for expects
    assert "type:bug" not in out.get("tags", [])  # not copied into tags


def test_type_enhancement_and_task_labels():
    assert ts.from_issue(_issue(["type:enhancement"]))["type"] == "enhancement"
    assert ts.from_issue(_issue(["type:task"]))["type"] == "task"


def test_no_type_label_leaves_type_unset():
    # No type label leaves type unset.
    out = ts.from_issue(_issue(["P0", "some-tag"]))
    assert "type" not in out
    assert "some-tag" in out.get("tags", [])   # non-reserved labels still tag


def test_unrelated_typeish_label_still_tags():
    # Only the three reserved type:* labels are special; anything else tags.
    out = ts.from_issue(_issue(["type:weird"]))
    assert out.get("type") != "weird"
    assert "type:weird" in out.get("tags", [])


# ── _cache_path_for buckets by type ─────────────────────────────────────────

def _cfg(tmp_path):
    # _cache_path_for calls cache_dir(cfg); give it a cache root it can join.
    return {"cache_dir": str(tmp_path)}


def test_type_bug_lands_in_bugs(tmp_path):
    p = adt_sync._cache_path_for(_cfg(tmp_path), {"type": "bug", "stage": "ideas",
                                                  "slug": "x", "issue_number": 73})
    assert os.path.join("bugs", "ideas", "x.md") in p


def test_missing_type_still_buckets_tasks(tmp_path):
    p = adt_sync._cache_path_for(_cfg(tmp_path), {"stage": "ideas", "slug": "x",
                                                  "issue_number": 73})
    assert os.path.join("tasks", "ideas", "x.md") in p


def test_cache_path_for_is_silent(tmp_path, capsys):
    """The path helper prints nothing; the pull summarises missing types instead."""
    for n in (73, 74, 75):
        adt_sync._cache_path_for(_cfg(tmp_path), {"stage": "ideas",
                                                  "slug": f"x{n}",
                                                  "issue_number": n})
    assert capsys.readouterr().err == ""


# ── writing the type label ──────────────────────────────────────────────────

def test_to_issue_emits_a_type_label():
    labels = ts.to_issue({"type": "bug", "title": "t", "body": "b"})["labels"]
    assert "type:bug" in [l["name"] if isinstance(l, dict) else l for l in labels]


def test_to_issue_type_label_round_trips_through_from_issue():
    for typ in ("bug", "enhancement", "task"):
        issue = ts.to_issue({"type": typ, "title": "t", "body": "b"})
        names = [l["name"] if isinstance(l, dict) else l for l in issue["labels"]]
        assert f"type:{typ}" in names
        assert ts.from_issue(_issue([f"type:{typ}"])).get("type") == typ


def test_to_issue_omits_type_label_when_absent():
    labels = ts.to_issue({"title": "t", "body": "b"})["labels"]
    names = [l["name"] if isinstance(l, dict) else l for l in labels]
    assert not [n for n in names if n.startswith("type:")]


# ── the one-line summary of Issues with no type ────────────────────────────

def test_summary_counts_and_states_the_remedy():
    out = adt_sync._summarise_no_type([31, 32, 33])
    assert "3 issue(s)" in out
    assert "#31" in out and "#33" in out
    assert "Remedy:" in out
    assert "type:<value>" in out       # names the label to add


def test_summary_truncates_a_long_list():
    out = adt_sync._summarise_no_type(list(range(1, 41)))
    assert "40 issue(s)" in out
    assert "#10" in out and "#40" not in out   # first 10 shown, rest elided
    assert "…" in out


def test_summary_is_one_line():
    assert "\n" not in adt_sync._summarise_no_type(list(range(1, 41)))

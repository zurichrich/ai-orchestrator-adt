# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for checkpoint_tokens(), which writes each machine's cumulative token
total to one Issue comment (`<!-- adt:tokens machine=<id> total=<N> -->`) and
updates it in place, and for pulling those comments into a fresh cache.

`gh` is faked by monkeypatching adt_sync._gh; no network.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_sync  # noqa: E402


# ── fixture helpers ─────────────────────────────────────────────────────────

TICKET = """---
slug: sample
id: ADT-58
title: Sample ticket
type: bug
priority: P1
stage: building
issue_number: 58
---

# Sample ticket
"""


def _mk_project(tmp_path, ledger_rows, stage="building"):
    """Build a minimal project root + cache with one ticket, return paths."""
    root = tmp_path / "proj"
    (root / ".adt" / "state").mkdir(parents=True, exist_ok=True)
    cache = tmp_path / "cache"
    (cache / "bugs" / stage).mkdir(parents=True, exist_ok=True)
    (root / ".adt" / "config.yaml").write_text(
        f"project: proj\nrepo: o/r\nid_prefix: ADT\ncache_dir: {cache}\n"
        "stages:\n  - name: building\n    label: stage:building\n"
    )
    ledger = root / ".adt" / "state" / "token-usage.log"
    ledger.write_text("".join(ledger_rows))
    ticket = cache / "bugs" / stage / "sample.md"
    ticket.write_text(TICKET.replace("stage: building", f"stage: {stage}"))
    return root, cache, ledger, ticket


def _row(tix, inp, out):
    return f"2026-07-03T10:00:00Z\t{tix}\t{inp}\t{out}\tsess1\n"


class FakeGh:
    """Records calls; returns canned bodies. Raises what you tell it to."""

    def __init__(self, fail_with=None):
        self.calls = []
        self.fail_with = fail_with  # exception instance, or None

    def __call__(self, args, input_text=None):
        self.calls.append(args)
        if self.fail_with is not None:
            exc, self.fail_with = self.fail_with, None  # fail once
            raise exc
        if args[:2] == ["api", "-X"] and args[2] == "POST":
            return json.dumps({"id": 991})
        return ""


def _cfg(root):
    return adt_sync.load_config(str(root))


def _run(root, monkeypatch, fake, stage_changed=None):
    monkeypatch.setattr(adt_sync, "_gh", fake)
    return adt_sync.checkpoint_tokens(str(root), _cfg(root),
                                      stage_changed or set())


def _cursor(root, tix="ADT-58"):
    p = (root / ".adt" / "state" / "token-checkpoint" / tix)
    return p.read_text().strip() if p.exists() else None


# ── trigger conditions ──────────────────────────────────────────────────────

def test_no_trigger_means_zero_api_calls(tmp_path, monkeypatch):
    # Drift below threshold, no stage change, not done -> purely local pass.
    root, *_ = _mk_project(tmp_path, [_row("ADT-58", 100, 200)])
    fake = FakeGh()
    res = _run(root, monkeypatch, fake)
    assert fake.calls == []
    assert res == []
    assert _cursor(root) is None  # cursor untouched; the delta is still owed


def test_stage_change_triggers_create_and_cursor(tmp_path, monkeypatch):
    root, cache, *_ = _mk_project(tmp_path, [_row("ADT-58", 1000, 2000)])
    fake = FakeGh()
    ticket_path = str(cache / "bugs" / "building" / "sample.md")
    res = _run(root, monkeypatch, fake, {ticket_path})
    assert len(fake.calls) == 1
    assert fake.calls[0][2] == "POST"          # created, not patched
    assert "repos/o/r/issues/58/comments" in fake.calls[0][3]
    assert res[0]["action"] == "token-checkpoint"
    assert res[0]["total"] == 3000
    assert _cursor(root) == "3000\t991"        # sum + stored comment id


def test_drift_threshold_triggers_without_stage_change(tmp_path, monkeypatch):
    root, *_ = _mk_project(
        tmp_path, [_row("ADT-58", 20_000, 10_000)])  # 30k >= 25k
    fake = FakeGh()
    res = _run(root, monkeypatch, fake)
    assert len(fake.calls) == 1
    assert res[0]["total"] == 30_000


def test_done_stage_triggers(tmp_path, monkeypatch):
    root, *_ = _mk_project(tmp_path, [_row("ADT-58", 100, 100)], stage="done")
    fake = FakeGh()
    res = _run(root, monkeypatch, fake)
    assert len(fake.calls) == 1
    assert res[0]["total"] == 200


# ── cursor arithmetic ───────────────────────────────────────────────────────

def test_second_pass_with_no_new_rows_is_silent(tmp_path, monkeypatch):
    root, cache, *_ = _mk_project(tmp_path, [_row("ADT-58", 1000, 2000)])
    ticket_path = str(cache / "bugs" / "building" / "sample.md")
    _run(root, monkeypatch, FakeGh(), {ticket_path})
    fake2 = FakeGh()
    res = _run(root, monkeypatch, fake2, {ticket_path})
    assert fake2.calls == []                   # tail == 0 -> nothing owed
    assert res == []


def test_patch_updates_existing_comment_cumulatively(tmp_path, monkeypatch):
    root, cache, ledger, _ = _mk_project(tmp_path, [_row("ADT-58", 1000, 2000)])
    ticket_path = str(cache / "bugs" / "building" / "sample.md")
    _run(root, monkeypatch, FakeGh(), {ticket_path})   # total 3000, id 991
    ledger.write_text(ledger.read_text() + _row("ADT-58", 500, 500))
    fake2 = FakeGh()
    res = _run(root, monkeypatch, fake2, {ticket_path})
    assert fake2.calls[0][2] == "PATCH"
    assert "issues/comments/991" in fake2.calls[0][3]
    assert res[0]["total"] == 4000             # cumulative, not the delta
    assert _cursor(root) == "4000\t991"


def test_failed_write_leaves_cursor_for_exact_retry(tmp_path, monkeypatch):
    root, cache, *_ = _mk_project(tmp_path, [_row("ADT-58", 1000, 2000)])
    ticket_path = str(cache / "bugs" / "building" / "sample.md")
    fake = FakeGh(fail_with=adt_sync.GhError("boom 500"))
    res = _run(root, monkeypatch, fake, {ticket_path})
    assert res[0]["action"] == "error"
    assert _cursor(root) is None               # not advanced; retried next pass
    # retry succeeds and bills the same delta exactly once
    fake2 = FakeGh()
    res2 = _run(root, monkeypatch, fake2, {ticket_path})
    assert res2[0]["total"] == 3000


def test_pruned_ledger_realigns_cursor_never_negative(tmp_path, monkeypatch):
    root, cache, ledger, _ = _mk_project(tmp_path, [_row("ADT-58", 20_000, 20_000)])
    ticket_path = str(cache / "bugs" / "building" / "sample.md")
    _run(root, monkeypatch, FakeGh(), {ticket_path})   # cursor 40000
    ledger.write_text(_row("ADT-58", 100, 100))              # pruned to 200
    fake2 = FakeGh()
    res = _run(root, monkeypatch, fake2, {ticket_path})
    assert fake2.calls == []                   # no API call on realign
    assert res == []
    assert _cursor(root).startswith("200\t")   # realigned to the new sum
    # new work after the prune bills from the realigned cursor
    ledger.write_text(ledger.read_text() + _row("ADT-58", 300, 0))
    fake3 = FakeGh()
    res3 = _run(root, monkeypatch, fake3, {ticket_path})
    assert res3[0]["total"] == 500             # 200 realigned + 300 tail


def test_patch_404_recreates_comment(tmp_path, monkeypatch):
    root, cache, ledger, _ = _mk_project(tmp_path, [_row("ADT-58", 1000, 2000)])
    ticket_path = str(cache / "bugs" / "building" / "sample.md")
    _run(root, monkeypatch, FakeGh(), {ticket_path})   # id 991 stored
    ledger.write_text(ledger.read_text() + _row("ADT-58", 100, 100))
    fake = FakeGh(fail_with=adt_sync.GhError("HTTP 404 Not Found"))
    res = _run(root, monkeypatch, fake, {ticket_path})
    # first call PATCH failed 404, second call recreated via POST
    assert [c[2] for c in fake.calls] == ["PATCH", "POST"]
    assert res[0]["action"] == "token-checkpoint"
    assert _cursor(root) == "3200\t991"        # new id from the POST


# ── id canonicalisation + body contract ─────────────────────────────────────

def test_canon_tix_pads_and_case_sum_to_one_register(tmp_path, monkeypatch):
    # Ledger wrote ADT-058 and adt-58; ticket id is ADT-58 -> ONE register.
    root, cache, *_ = _mk_project(
        tmp_path, [_row("ADT-058", 100, 0), _row("adt-58", 0, 50)])
    ticket_path = str(cache / "bugs" / "building" / "sample.md")
    fake = FakeGh()
    res = _run(root, monkeypatch, fake, {ticket_path})
    assert res[0]["total"] == 150
    assert _cursor(root, "ADT-58") is not None  # cursor keyed canonically


def test_register_body_is_single_line_with_marker(tmp_path, monkeypatch):
    body = adt_sync._register_body("mach1", 62_167)
    assert "\n" not in body                    # frontmatter/kanban contract
    assert "<!-- adt:tokens machine=mach1 total=62167 -->" in body


# ── pull_all fetches comments only for Issues with no cache file ────────────


class FakePullGh:
    """Serves the REST issues list and the REST comments endpoint; records calls."""

    def __init__(self, issues, comments):
        self.issues, self.comments, self.calls = issues, comments, []

    def __call__(self, args, input_text=None):
        self.calls.append(args)
        if args[0] == "api" and "/issues?" in args[-1]:
            return json.dumps(self.issues)
        # REST: GET repos/<repo>/issues/<n>/comments -> a BARE ARRAY.
        if args[0] == "api" and args[-1].endswith("/comments"):
            return json.dumps(self.comments)
        return ""


# REST /issues item shape (snake_case — _issue_from_rest maps it).
ISSUE_58 = {
    "number": 58, "node_id": "NODE58", "title": "Sample", "body": "# Sample",
    "state": "open", "state_reason": None, "assignees": [], "milestone": None,
    "labels": [{"name": "type:bug"}, {"name": "stage:building"}, {"name": "P1"}],
    "created_at": "2026-07-01T09:00:00Z", "updated_at": "2026-07-03T09:00:00Z",
    "closed_at": None, "user": {"login": "someone"},
}


def test_pull_reconstruct_fetches_comments_for_fresh_clone(tmp_path, monkeypatch):
    # No cache file for the Issue, so reconstruct fetches its comments and the
    # register markers land in the new cache file, flattened to one line.
    root, cache, _, ticket = _mk_project(tmp_path, [])
    ticket.unlink()                            # cache knows nothing of #58
    fake = FakePullGh([ISSUE_58], [
        {"user": {"login": "adt"}, "created_at": "2026-07-03T10:00:00Z",
         "body": "<!-- adt:tokens machine=m1 total=5000 -->\n🪙 5,000 tokens"}])
    monkeypatch.setattr(adt_sync, "_gh", fake)
    res = adt_sync.pull_all(_cfg(root))
    assert [r["action"] for r in res] == ["reconstructed"]
    written = (cache / "bugs" / "building" / "issue-58.md").read_text()
    assert "<!-- adt:tokens machine=m1 total=5000 -->" in written
    assert "🪙 5,000 tokens" in written        # flattened onto the marker line
    # Exactly one comments fetch, for the one missing file.
    views = [c for c in fake.calls
             if c[0] == "api" and c[-1].endswith("/comments")]
    assert len(views) == 1
    assert not [c for c in fake.calls if c[:2] == ["issue", "view"]]


def test_pull_existing_file_never_fetches_comments(tmp_path, monkeypatch):
    # An existing cache file pulls only _PULL_OWNED fields, with no comments fetch.
    root, cache, _, _ = _mk_project(tmp_path, [])   # sample.md exists (#58)
    fake = FakePullGh([ISSUE_58], [])
    monkeypatch.setattr(adt_sync, "_gh", fake)
    adt_sync.pull_all(_cfg(root))
    assert not any(c[:2] == ["issue", "view"] for c in fake.calls)


def test_serializer_flattens_multiline_comment_body_on_emit():
    # A multi-line comment body is emitted as one line, so the frontmatter
    # stays valid and round-trips.
    import ticket_serializer as ts
    issue = {"number": 1, "title": "x",
             "comments": [{"author": {"login": "a"},
                           "createdAt": "2026-07-03T10:00:00Z",
                           "body": "line one\nline two\r\nline three"}]}
    md = ts.emit_md(ts.from_issue(issue))
    reparsed = ts.parse_md(md)
    assert reparsed["comments"][0]["body"] == "line one line two line three"


def test_ratelimit_propagates_to_abort_the_tick(tmp_path, monkeypatch):
    root, cache, *_ = _mk_project(tmp_path, [_row("ADT-58", 1000, 2000)])
    ticket_path = str(cache / "bugs" / "building" / "sample.md")
    fake = FakeGh(fail_with=adt_sync.RateLimitError("rate limit exceeded"))
    try:
        _run(root, monkeypatch, fake, {ticket_path})
        raised = False
    except adt_sync.RateLimitError:
        raised = True
    assert raised                              # caller aborts, like the push loop
    assert _cursor(root) is None

# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for adt_sync's incremental REST pull.

Covers the REST-to-from_issue field mapping, dropping PRs, pagination, the
`since` watermark, full sweeps, the reconstruct grace window for newly created
Issues, and the `adt:filing` claim label. No `gh` call is made; _gh_json and
_gh are monkeypatched.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_sync  # noqa: E402


def _rest_issue(number, updated_at="2026-07-03T10:00:00Z", **over):
    item = {
        "number": number,
        "node_id": f"I_node{number}",
        "title": f"Issue {number}",
        "body": f"body {number}",
        "state": "open",
        "state_reason": None,
        "labels": [{"name": "P1"}, {"name": "stage:ideas"},
                   {"name": "type:bug"}],
        "assignees": [{"login": "someone"}],
        "milestone": None,
        "created_at": "2026-07-01T09:00:00Z",
        "updated_at": updated_at,
        "closed_at": None,
        "user": {"login": "author-x"},
    }
    item.update(over)
    return item


def _cfg(tmp_path):
    return {"repo": "o/r", "cache_dir": str(tmp_path / "cache")}


def _write_ticket(tmp_path, slug, number, extra=""):
    d = tmp_path / "cache" / "bugs" / "ideas"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug}.md"
    p.write_text(
        f"---\nslug: {slug}\nid: ADT-{number}\ntitle: {slug}\ntype: bug\n"
        f"stage: ideas\nissue_number: {number}\n"
        f"issue_node_id: I_node{number}\n{extra}---\n\n# {slug}\n")
    return p


# ---------------------------------------------------------------- adapter --
def test_issue_from_rest_maps_to_from_issue_shape():
    out = adt_sync._issue_from_rest(_rest_issue(7))
    assert out["number"] == 7
    assert out["id"] == "I_node7"            # node_id -> id
    assert out["updatedAt"] == "2026-07-03T10:00:00Z"  # snake -> camel
    assert out["createdAt"] == "2026-07-01T09:00:00Z"
    assert out["author"] == {"login": "author-x"}      # user -> author
    assert out["stateReason"] is None
    assert out["labels"][0]["name"] == "P1"
    assert out["assignees"][0]["login"] == "someone"


def test_issue_from_rest_keeps_lowercase_state_convention():
    out = adt_sync._issue_from_rest(_rest_issue(
        8, state="closed", state_reason="not_planned"))
    assert out["state"] == "closed"          # cache convention, verbatim
    assert out["stateReason"] == "not_planned"


def test_issue_from_rest_null_body_becomes_empty():
    assert adt_sync._issue_from_rest(_rest_issue(9, body=None))["body"] == ""


# ----------------------------------------------------- list: filter+pages --
def test_list_issues_rest_drops_prs_and_passes_since(monkeypatch):
    urls = []

    def fake(args):
        urls.append(args[1])
        return [_rest_issue(1), _rest_issue(2, pull_request={"url": "x"})]

    monkeypatch.setattr(adt_sync, "_gh_json", fake)
    got = adt_sync._list_issues_rest("o/r", since="2026-07-02T00:00:00Z")
    assert [i["number"] for i in got] == [1]          # the PR is dropped
    assert "since=2026-07-02T00:00:00Z" in urls[0]
    assert "state=all" in urls[0]


def test_list_issues_rest_omits_since_when_none(monkeypatch):
    urls = []

    def fake(args):
        urls.append(args[1])
        return []

    monkeypatch.setattr(adt_sync, "_gh_json", fake)
    adt_sync._list_issues_rest("o/r", since=None)
    assert "since" not in urls[0]


def test_list_issues_rest_paginates_until_short_page(monkeypatch):
    # The loop requests pages sequentially, so serving responses in call order
    # exercises pagination without parsing the URL: one full page (=> fetch
    # another), then a short page (=> stop).
    responses = [[_rest_issue(n) for n in range(1, 101)],  # full page
                 [_rest_issue(101)]]                       # short -> stop
    monkeypatch.setattr(adt_sync, "_gh_json", lambda args: responses.pop(0))
    got = adt_sync._list_issues_rest("o/r")
    assert len(got) == 101
    assert got[-1]["number"] == 101
    assert not responses                                   # both pages consumed


# ------------------------------------------------------ pull_all + state --
def test_pull_all_advances_watermark_to_max_updated(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _write_ticket(tmp_path, "a", 1)
    monkeypatch.setattr(adt_sync, "_list_issues_rest", lambda repo, since=None: [
        adt_sync._issue_from_rest(_rest_issue(1, "2026-07-03T11:00:00Z")),
        adt_sync._issue_from_rest(_rest_issue(1, "2026-07-03T12:00:00Z")),
    ])
    adt_sync.pull_all(cfg)
    pull = adt_sync._load_pull_state(cfg)
    assert pull["watermark"] == "2026-07-03T12:00:00Z"
    assert isinstance(pull["last_full_pull"], float)


def test_pull_all_uses_since_when_state_fresh(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    adt_sync._save_pull_state(cfg, {"watermark": "2026-07-03T08:00:00Z",
                                    "last_full_pull": adt_sync.time.time()})
    seen = {}

    def fake(repo, since=None):
        seen["since"] = since
        return []

    monkeypatch.setattr(adt_sync, "_list_issues_rest", fake)
    adt_sync.pull_all(cfg)
    assert seen["since"] == "2026-07-03T08:00:00Z"
    # empty result keeps the watermark, doesn't erase it
    assert adt_sync._load_pull_state(cfg)["watermark"] == "2026-07-03T08:00:00Z"


def test_pull_all_full_sweeps_when_last_full_stale(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    adt_sync._save_pull_state(cfg, {
        "watermark": "2026-07-03T08:00:00Z",
        "last_full_pull": adt_sync.time.time() - 2 * adt_sync._FULL_SWEEP_INTERVAL})
    seen = {}

    def fake(repo, since=None):
        seen["since"] = since
        return []

    monkeypatch.setattr(adt_sync, "_list_issues_rest", fake)
    adt_sync.pull_all(cfg)
    assert seen["since"] is None                     # watermark bypassed


def test_full_sweep_reconstructs_deleted_cache_file(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(adt_sync, "_list_issues_rest", lambda repo, since=None: [
        adt_sync._issue_from_rest(_rest_issue(31))])
    # Reconstructing also fetches the Issue's comments, which REST returns as
    # a bare array. Stub _gh so the test never calls the real gh.
    monkeypatch.setattr(adt_sync, "_gh",
                        lambda args, input_text=None: '[]')
    res = adt_sync.pull_all(cfg)
    assert [r["action"] for r in res] == ["reconstructed"]
    rebuilt = tmp_path / "cache" / "bugs" / "ideas" / "issue-31.md"
    assert rebuilt.exists()
    assert "issue_number: 31" in rebuilt.read_text()


def test_watermark_not_advanced_on_rate_limit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    adt_sync._save_pull_state(cfg, {"watermark": "2026-07-03T08:00:00Z",
                                    "last_full_pull": adt_sync.time.time()})

    def boom(repo, since=None):
        raise adt_sync.RateLimitError("quota gone")

    monkeypatch.setattr(adt_sync, "_list_issues_rest", boom)
    try:
        adt_sync.pull_all(cfg)
        assert False, "expected RateLimitError to propagate"
    except adt_sync.RateLimitError:
        pass
    assert adt_sync._load_pull_state(cfg)["watermark"] == "2026-07-03T08:00:00Z"


def test_persist_state_preserves_pull_key(tmp_path):
    cfg = _cfg(tmp_path)
    adt_sync._save_pull_state(cfg, {"watermark": "2026-07-03T08:00:00Z",
                                    "last_full_pull": 123.0})
    adt_sync._persist_state(cfg, {"/some/file.md": "abc"})   # push-side write
    doc = json.load(open(adt_sync._state_path(cfg)))
    assert doc["pull"]["watermark"] == "2026-07-03T08:00:00Z"  # survived
    assert doc["file_hashes"] == {"/some/file.md": "abc"}


# ------------------------------------------------- reconstruct grace window --


def _iso(ts: float) -> str:
    """GitHub's ISO8601-Z shape for an epoch timestamp."""
    return adt_sync.datetime.datetime.fromtimestamp(
        ts, adt_sync.datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_created_within_grace_boundaries():
    now = 1_000_000.0
    inside = _iso(now - 10)
    outside = _iso(now - adt_sync.RECONSTRUCT_GRACE_SECONDS - 1)
    assert adt_sync._created_within_grace(inside, now=now) is True
    assert adt_sync._created_within_grace(outside, now=now) is False


def test_created_within_grace_fails_open():
    """Missing, malformed or future timestamps do not defer."""
    now = 1_000_000.0
    assert adt_sync._created_within_grace(None, now=now) is False
    assert adt_sync._created_within_grace("", now=now) is False
    assert adt_sync._created_within_grace("not-a-date", now=now) is False
    assert adt_sync._created_within_grace(_iso(now + 600), now=now) is False


def test_freshly_created_issue_is_not_reconstructed(tmp_path, monkeypatch):
    """An Issue created seconds ago with no cache file yet is deferred."""
    cfg = _cfg(tmp_path)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    fresh = _rest_issue(115, created_at=_iso(adt_sync.time.time()))
    monkeypatch.setattr(adt_sync, "_list_issues_rest",
                        lambda repo, since=None: [adt_sync._issue_from_rest(fresh)])
    # Any _gh call here would mean the reconstruct branch ran anyway.
    monkeypatch.setattr(adt_sync, "_gh", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("reconstruct branch must not run for a fresh Issue")))

    res = adt_sync.pull_all(cfg)

    assert [r["action"] for r in res] == ["deferred"]
    assert not (tmp_path / "cache" / "bugs" / "ideas" / "issue-115.md").exists()


def test_issue_older_than_grace_is_still_reconstructed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    old = _rest_issue(
        116,
        created_at=_iso(adt_sync.time.time() - adt_sync.RECONSTRUCT_GRACE_SECONDS - 60))
    monkeypatch.setattr(adt_sync, "_list_issues_rest",
                        lambda repo, since=None: [adt_sync._issue_from_rest(old)])
    monkeypatch.setattr(adt_sync, "_gh",
                        lambda args, input_text=None: '[]')

    res = adt_sync.pull_all(cfg)

    assert [r["action"] for r in res] == ["reconstructed"]
    assert (tmp_path / "cache" / "bugs" / "ideas" / "issue-116.md").exists()


def test_deferred_issue_reconstructs_on_a_later_tick(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    created = adt_sync.time.time()
    issue = _rest_issue(117, created_at=_iso(created))
    # The stub filters on `since` as REST does, so the watermark has an effect.
    monkeypatch.setattr(adt_sync, "_list_issues_rest",
                        _since_honouring_stub([issue]))
    monkeypatch.setattr(adt_sync, "_gh",
                        lambda args, input_text=None: '[]')

    assert [r["action"] for r in adt_sync.pull_all(cfg)] == ["deferred"]

    # ...the window passes.
    monkeypatch.setattr(adt_sync.time, "time",
                        lambda: created + adt_sync.RECONSTRUCT_GRACE_SECONDS + 1)

    assert [r["action"] for r in adt_sync.pull_all(cfg)] == ["reconstructed"]
    assert (tmp_path / "cache" / "bugs" / "ideas" / "issue-117.md").exists()


def _since_honouring_stub(rest_issues):
    """A _list_issues_rest stub that applies `since` inclusively, as the REST endpoint does."""
    def _stub(repo, since=None):
        out = [adt_sync._issue_from_rest(i) for i in rest_issues]
        if since:
            out = [i for i in out if i["updatedAt"] >= since]
        return out
    return _stub


def test_deferred_issue_survives_a_newer_sibling(tmp_path, monkeypatch):
    """A newer Issue in the same batch does not move the watermark past a deferred one."""
    cfg = _cfg(tmp_path)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    created = adt_sync.time.time()
    fresh = _rest_issue(342, created_at=_iso(created), updated_at=_iso(created))
    newer = _rest_issue(339, created_at="2026-01-01T00:00:00Z",
                        updated_at=_iso(created + 60))
    monkeypatch.setattr(adt_sync, "_list_issues_rest",
                        _since_honouring_stub([fresh, newer]))
    monkeypatch.setattr(adt_sync, "_gh", lambda args, input_text=None: '[]')

    assert "deferred" in [r["action"] for r in adt_sync.pull_all(cfg)]

    monkeypatch.setattr(adt_sync.time, "time",
                        lambda: created + adt_sync.RECONSTRUCT_GRACE_SECONDS + 1)

    assert "reconstructed" in [r["action"] for r in adt_sync.pull_all(cfg)]
    assert (tmp_path / "cache" / "bugs" / "ideas" / "issue-342.md").exists()


def test_watermark_clamps_to_the_oldest_deferred(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    created = adt_sync.time.time()
    older = _rest_issue(400, created_at=_iso(created - 100),
                        updated_at=_iso(created - 100))
    younger = _rest_issue(401, created_at=_iso(created),
                          updated_at=_iso(created))
    newer = _rest_issue(402, created_at="2026-01-01T00:00:00Z",
                        updated_at=_iso(created + 60))
    monkeypatch.setattr(adt_sync, "_list_issues_rest",
                        _since_honouring_stub([older, younger, newer]))
    monkeypatch.setattr(adt_sync, "_gh", lambda args, input_text=None: '[]')

    assert [r["action"] for r in adt_sync.pull_all(cfg)].count("deferred") == 2

    state = json.loads((tmp_path / "cache" / ".adt-sync-state.json").read_text())
    assert state["pull"]["watermark"] == _iso(created - 100), (
        "expected the watermark at the oldest deferred Issue")

    # Both are reconstructed once the window passes.
    monkeypatch.setattr(adt_sync.time, "time",
                        lambda: created + adt_sync.RECONSTRUCT_GRACE_SECONDS + 1)
    actions = [r["action"] for r in adt_sync.pull_all(cfg)]
    assert actions.count("reconstructed") == 2, actions


# ------------------------------------------------------------ filing claim --
#
# The claim is an `adt:filing` label on the Issue, so a watch on any machine sees it.


def _labelled(number, created_at, extra_label=adt_sync.FILING_LABEL):
    issue = _rest_issue(number, created_at=created_at)
    issue["labels"] = issue["labels"] + [{"name": extra_label}]
    return adt_sync._issue_from_rest(issue)


def _minutes_ago(mins):
    return _iso(adt_sync.time.time() - mins * 60)


def test_claimed_issue_is_not_reconstructed_past_the_age_window(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(adt_sync, "_list_issues_rest",
                        lambda repo, since=None: [_labelled(200, _minutes_ago(10))])
    monkeypatch.setattr(adt_sync, "_gh", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("a claimed Issue must not be reconstructed")))

    assert [r["action"] for r in adt_sync.pull_all(cfg)] == ["deferred"]
    assert not (tmp_path / "cache" / "bugs" / "ideas" / "issue-200.md").exists()


def test_an_expired_claim_is_reconstructed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    stale = _minutes_ago(adt_sync.FILING_CLAIM_MAX_AGE_SECONDS / 60 + 5)
    monkeypatch.setattr(adt_sync, "_list_issues_rest",
                        lambda repo, since=None: [_labelled(201, stale)])
    monkeypatch.setattr(adt_sync, "_gh",
                        lambda args, input_text=None: '[]')

    assert [r["action"] for r in adt_sync.pull_all(cfg)] == ["reconstructed"]
    assert (tmp_path / "cache" / "bugs" / "ideas" / "issue-201.md").exists()


def test_has_filing_claim_fails_open():
    """No label, no labels key, or no timestamp means no claim."""
    now = 1_000_000.0
    fresh = _iso(now - 10)
    assert adt_sync._has_filing_claim({"labels": [], "createdAt": fresh}, now=now) is False
    assert adt_sync._has_filing_claim({"createdAt": fresh}, now=now) is False
    assert adt_sync._has_filing_claim(
        {"labels": [{"name": adt_sync.FILING_LABEL}]}, now=now) is False
    assert adt_sync._has_filing_claim(
        {"labels": [{"name": adt_sync.FILING_LABEL}], "createdAt": fresh},
        now=now) is True


def test_filing_label_never_becomes_a_tag():
    from ticket_serializer import from_issue
    out = from_issue(_labelled(202, _minutes_ago(1)), base={})
    assert adt_sync.FILING_LABEL not in (out.get("tags") or [])
    # a genuine tag alongside it still survives
    out2 = from_issue(_labelled(203, _minutes_ago(1), extra_label="kanban"), base={})
    assert "kanban" in (out2.get("tags") or [])

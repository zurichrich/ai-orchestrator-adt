# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""The telemetry archive Issue is never treated as a ticket (ADT-354).

lib/uninstall.sh files an "ADT telemetry archive" Issue. A reinstall's pull
rebuilt a cache file for it, like any Issue with no cache file, and the push
then labelled it `stage:ideas` and put it on the board (one consumer Issue, #881). The
archive now carries `adt:archive`, and the sync skips it on both sides."""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_sync  # noqa: E402


def _rest_issue(number, labels):
    return {"number": number, "node_id": f"I_{number}", "title": f"Issue {number}",
            "body": "b", "state": "open", "state_reason": None,
            "labels": [{"name": n} for n in labels], "assignees": [],
            "milestone": None, "created_at": "2026-07-01T09:00:00Z",
            "updated_at": "2026-07-03T10:00:00Z", "closed_at": None,
            "user": {"login": "someone"}}


def _cfg(tmp_path):
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    return {"repo": "o/r", "cache_dir": str(tmp_path / "cache")}


def _pull(tmp_path, monkeypatch, issues):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(adt_sync, "_list_issues_rest", lambda repo, since=None: [
        adt_sync._issue_from_rest(i) for i in issues])
    monkeypatch.setattr(adt_sync, "_gh", lambda args, input_text=None: "[]")
    return adt_sync.pull_all(cfg)


def test_pull_does_not_rebuild_the_archive(tmp_path, monkeypatch):
    res = _pull(tmp_path, monkeypatch, [_rest_issue(881, ["adt:archive"])])
    assert not list((tmp_path / "cache").rglob("*.md")), "a cache file was rebuilt"
    assert not [r for r in res if r.get("action") == "reconstructed"]


def test_pull_still_rebuilds_an_ordinary_issue(tmp_path, monkeypatch):
    res = _pull(tmp_path, monkeypatch, [_rest_issue(881, ["adt:archive"]),
                                        _rest_issue(31, ["type:bug"])])
    rebuilt = [p.name for p in (tmp_path / "cache").rglob("*.md")]
    assert rebuilt == ["issue-31.md"], rebuilt
    assert [r["number"] for r in res if r.get("action") == "reconstructed"] == [31]


ARCHIVE_FILE = {"slug": "issue-881", "issue_number": 881, "type": "task",
                "stage": "done", "state": "closed", "status": "cancelled",
                "title": "ADT telemetry archive", "body": "archive"}


def _push(monkeypatch, labels):
    calls = []
    monkeypatch.setattr(adt_sync, "_current_issue", lambda repo, n: adt_sync._issue_from_rest(
        dict(_rest_issue(n, labels), state="closed", title="ADT telemetry archive")))
    monkeypatch.setattr(adt_sync, "_gh", lambda args, input_text=None: calls.append(args) or "")
    monkeypatch.setattr(adt_sync, "_ensure_labels_exist", lambda repo, names: None)
    monkeypatch.setattr(adt_sync, "ensure_on_board",
                        lambda cfg, n, stage: calls.append(["board", n, stage]) or None)
    r = adt_sync.reconcile_one("tasks/done/issue-881.md", dict(ARCHIVE_FILE), {"repo": "o/r"})
    return r, calls


def test_push_leaves_a_labelled_archive_alone(monkeypatch):
    r, calls = _push(monkeypatch, ["adt:archive", "stage:ideas"])
    assert r["action"] == "noop"
    assert calls == [], f"the push touched the archive: {calls}"


def test_push_still_edits_an_unlabelled_issue(monkeypatch):
    # The same file against an Issue without the label: the push edits its
    # labels and puts it on the board. Shows the test above can see a push.
    r, calls = _push(monkeypatch, ["stage:ideas"])
    assert any(c[:2] == ["issue", "edit"] for c in calls), calls
    assert any(c[0] == "board" for c in calls), calls


def test_install_issue_gets_no_cache_file_and_no_push(tmp_path, monkeypatch):
    # ADT-384: the machine-report Issue is not a ticket either. Both halves: the
    # pull builds no cache file for it, and a file that somehow exists for it
    # does not make the push edit it or put it on the board.
    res = _pull(tmp_path, monkeypatch, [_rest_issue(900, ["adt:install"]),
                                        _rest_issue(31, ["type:bug"])])
    rebuilt = [p.name for p in (tmp_path / "cache").rglob("*.md")]
    assert rebuilt == ["issue-31.md"], rebuilt
    assert [r["number"] for r in res if r.get("action") == "reconstructed"] == [31]

    r, calls = _push(monkeypatch, ["adt:install", "stage:ideas"])
    assert r["action"] == "noop"
    assert calls == [], f"the push touched the machine-report Issue: {calls}"

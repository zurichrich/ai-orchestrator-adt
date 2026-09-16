# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Machine reports on the adt:install Issue (ADT-384, tools/adt_machines.py).

A fake `gh` serves the REST endpoints the module calls and records every call,
so each test asserts on what went to GitHub, not only on the return value."""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_machines  # noqa: E402

REPO = "o/r"
UTC = datetime.timezone.utc


class FakeGh:
    def __init__(self):
        self.calls = []
        self.issues = {}     # number -> {"number", "labels", "state"}
        self.comments = {}   # id -> {"id", "issue", "body", "author_association", "updated_at", "user"}
        self.next_issue = 5
        self.next_comment = 1000
        self.now = "2026-09-16T08:00:00Z"

    def _fields(self, args):
        out = {}
        for i, a in enumerate(args):
            if a == "-f":
                k, _, v = args[i + 1].partition("=")
                out.setdefault(k, []).append(v)
        return out

    def add_comment(self, issue, body, assoc="OWNER", login="zurichrich", updated_at=None):
        cid = self.next_comment
        self.next_comment += 1
        self.comments[cid] = {"id": cid, "issue": issue, "body": body,
                              "author_association": assoc, "user": {"login": login},
                              "updated_at": updated_at or self.now}
        return cid

    def __call__(self, args):
        self.calls.append(list(args))
        method = "GET"
        if "-X" in args:
            method = args[args.index("-X") + 1]
        path = [a for a in args[1:] if a.startswith("repos/")][0]
        f = self._fields(args)
        if path == f"repos/{REPO}/labels" and method == "POST":
            return "{}"
        if path.startswith(f"repos/{REPO}/issues?labels="):
            return json.dumps([{"number": n, "labels": [{"name": x} for x in i["labels"]]}
                               for n, i in sorted(self.issues.items())])
        if path == f"repos/{REPO}/issues" and method == "POST":
            n = self.next_issue
            self.next_issue += 1
            self.issues[n] = {"number": n, "labels": f.get("labels[]", []), "state": "open"}
            return json.dumps({"number": n})
        if path.startswith(f"repos/{REPO}/issues/comments/") and method == "PATCH":
            cid = int(path.rsplit("/", 1)[1])
            if cid not in self.comments:
                raise RuntimeError("gh api failed: 404 Not Found")
            self.comments[cid].update(body=f["body"][0], updated_at=self.now)
            return json.dumps(self.comments[cid])
        if path.endswith("/comments") and method == "POST":
            n = int(path.split("/")[-2])
            cid = self.add_comment(n, f["body"][0])
            return json.dumps(self.comments[cid])
        if "/comments?" in path:
            n = int(path.split("/issues/")[1].split("/")[0])
            return json.dumps([c for c in self.comments.values() if c["issue"] == n])
        if method == "PATCH" and path.startswith(f"repos/{REPO}/issues/"):
            n = int(path.rsplit("/", 1)[1])
            self.issues[n]["state"] = f["state"][0]
            return "{}"
        raise AssertionError(f"unexpected gh call: {args}")


def _marker(commit, version="0.1.0", os_name="Darwin"):
    return ("ADT line\n\n<!-- adt-machine "
            + json.dumps({"version": version, "commit": commit, "os": os_name}) + " -->")


@pytest.fixture
def adt_root(tmp_path):
    root = tmp_path / "adt"
    root.mkdir()
    env = {"GIT_CONFIG_GLOBAL": str(tmp_path / "gc"), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
    (root / "VERSION").write_text("0.1.0\n")
    for msg in ("one", "two"):
        subprocess.run(["git", "-C", str(root), "commit", "-q", "--allow-empty", "-m", msg],
                       check=True, env=env)
    return root


def _head(root, ref="HEAD"):
    return subprocess.run(["git", "-C", str(root), "rev-parse", ref],
                          capture_output=True, text=True).stdout.strip()


def _report(project, adt_root, gh, today="2026-09-16"):
    return adt_machines.report(str(project), str(adt_root), REPO, gh=gh, today=today,
                               now=datetime.datetime(2026, 9, 16, 9, 0, tzinfo=UTC))


def test_first_report_creates_closed_install_issue(tmp_path, adt_root):
    gh = FakeGh()
    machines = _report(tmp_path / "p", adt_root, gh)
    assert list(gh.issues) == [5]
    assert gh.issues[5]["labels"] == ["adt:install"]
    assert gh.issues[5]["state"] == "closed"
    assert [c["issue"] for c in gh.comments.values()] == [5]
    assert len(machines) == 1 and machines[0]["commit"] == _head(adt_root)
    assert machines[0]["this_machine"] is True
    written = json.loads((tmp_path / "p/.adt/state/machines.json").read_text())
    assert written == machines


def test_install_issue_create_call_carries_the_label(tmp_path, adt_root):
    gh = FakeGh()
    _report(tmp_path / "p", adt_root, gh)
    creates = [c for c in gh.calls if f"repos/{REPO}/issues" in c and "POST" in c]
    assert len(creates) == 1
    assert "labels[]=adt:install" in creates[0], creates[0]
    # and no separate label call on the Issue afterwards
    assert not [c for c in gh.calls if any(a.endswith("/labels") and "/issues/" in a for a in c)]


def test_second_report_updates_own_comment_in_place(tmp_path, adt_root):
    gh = FakeGh()
    _report(tmp_path / "p", adt_root, gh, today="2026-09-15")
    first_ids = list(gh.comments)
    gh.calls.clear()
    gh.now = "2026-09-16T08:30:00Z"
    _report(tmp_path / "p", adt_root, gh, today="2026-09-16")
    assert list(gh.comments) == first_ids, "a second comment was posted"
    patches = [c for c in gh.calls if f"repos/{REPO}/issues/comments/{first_ids[0]}" in c]
    assert patches and "PATCH" in patches[0]
    assert not [c for c in gh.calls if "POST" in c and any(a.endswith("/comments") for a in c)]


def test_report_runs_at_most_once_per_utc_day(tmp_path, adt_root):
    gh = FakeGh()
    assert _report(tmp_path / "p", adt_root, gh) is not None
    n = len(gh.calls)
    assert _report(tmp_path / "p", adt_root, gh) is None
    assert len(gh.calls) == n, "the second report of the day called GitHub"


def test_report_moves_to_the_lowest_install_issue(tmp_path, adt_root):
    gh = FakeGh()
    gh.issues[9] = {"number": 9, "labels": ["adt:install"], "state": "closed"}
    gh.issues[4] = {"number": 4, "labels": ["adt:install"], "state": "closed"}
    old = gh.add_comment(9, _marker(_head(adt_root)))
    state = tmp_path / "p/.adt/state/machine-report.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"issue": 9, "comment_id": old}))
    _report(tmp_path / "p", adt_root, gh)
    mine = json.loads(state.read_text())
    assert mine["issue"] == 4
    assert gh.comments[mine["comment_id"]]["issue"] == 4


def test_machines_file_keeps_machines_reported_in_the_last_7_days(tmp_path, adt_root):
    gh = FakeGh()
    gh.issues[5] = {"number": 5, "labels": ["adt:install"], "state": "closed"}
    gh.next_issue = 6
    # _report's clock is 2026-09-16T09:00Z: one report an hour inside the 7 days,
    # one an hour outside, so a window of 6 or 8 days fails too.
    recent, stale = "a" * 40, "b" * 40
    gh.add_comment(5, _marker(recent), updated_at="2026-09-09T10:00:00Z")
    gh.add_comment(5, _marker(stale), updated_at="2026-09-09T08:00:00Z")
    _report(tmp_path / "p", adt_root, gh)
    listed = {m["commit"] for m in json.loads((tmp_path / "p/.adt/state/machines.json").read_text())}
    assert recent in listed and stale not in listed


def test_report_does_not_carry_the_telemetry_install_id(tmp_path, adt_root):
    install_id = "3f2a9c1e-7b44-4d0e-9a51-2c8e6f1d0b77"
    p = tmp_path / "p"
    (p / ".adt/state").mkdir(parents=True)
    (p / ".adt/state/install-id").write_text(install_id + "\n")
    gh = FakeGh()
    _report(p, adt_root, gh)
    assert gh.comments, "no report was written"
    assert not [c for c in gh.calls if any(install_id in a for a in c)]
    assert install_id not in (p / ".adt/state/machines.json").read_text()


def test_comment_from_non_collaborator_is_ignored(tmp_path, adt_root):
    gh = FakeGh()
    gh.issues[5] = {"number": 5, "labels": ["adt:install"], "state": "closed"}
    gh.next_issue = 6
    gh.add_comment(5, _marker("c" * 40), assoc="NONE", login="stranger")
    gh.add_comment(5, _marker("d" * 40), assoc="CONTRIBUTOR", login="drive-by")
    machines = _report(tmp_path / "p", adt_root, gh)
    assert {m["login"] for m in machines} == {"zurichrich"}
    assert len(machines) == 1


def test_malformed_commit_is_ignored(tmp_path, adt_root):
    gh = FakeGh()
    gh.issues[5] = {"number": 5, "labels": ["adt:install"], "state": "closed"}
    gh.next_issue = 6
    for bad in ("--upload-pack=touch /tmp/pwned", "e" * 39, "F" * 40, "HEAD"):
        gh.add_comment(5, _marker(bad))
    gh.add_comment(5, "no marker at all")
    machines = _report(tmp_path / "p", adt_root, gh)
    assert [m["commit"] for m in machines] == [_head(adt_root)]


def test_tests_cannot_reach_github_through_the_report(tmp_path, adt_root, monkeypatch):
    # conftest.py replaces adt_machines._gh for every test, so a watch tick in any
    # test cannot create the adt:install label, Issue or comment on a real repo.
    # A logging `gh` on PATH shows whether a call escaped anyway.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    stub = bin_dir / "gh"
    stub.write_text('#!/bin/sh\necho "$*" >> %s\necho "[]"\n' % log)
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", "%s%s%s" % (bin_dir, os.pathsep, os.environ["PATH"]))
    assert adt_machines.report(str(tmp_path / "p"), str(adt_root), REPO,
                               today="2026-09-16") is None
    assert not log.exists(), log.read_text()


def test_older_than_lists_older_and_unknown_machines(adt_root):
    old, new = _head(adt_root, "HEAD~1"), _head(adt_root)
    machines = [{"commit": old}, {"commit": new}, {"commit": "f" * 40}]
    got = [(m["commit"], why) for m, why in adt_machines.older_than(machines, new, str(adt_root))]
    assert got == [(old, "older"), ("f" * 40, "unknown")]


def test_watch_daily_branch_reports_machine(tmp_path, monkeypatch):
    import adt_sync
    import adt_watch

    calls = []
    monkeypatch.setattr(adt_sync, "load_config", lambda root: {"repo": REPO})
    monkeypatch.setattr(adt_sync, "cache_dir", lambda cfg: str(tmp_path / "cache"))

    @contextlib.contextmanager
    def lock(cfg):
        yield True
    monkeypatch.setattr(adt_sync, "pass_lock", lock)
    monkeypatch.setattr(adt_watch, "_rotate_log", lambda root: None)
    monkeypatch.setattr(adt_watch, "_watch_state", lambda cfg: {})
    monkeypatch.setattr(adt_watch, "_save_watch_state", lambda *a: None)
    monkeypatch.setattr(adt_watch, "_cache_fingerprint", lambda cache: 1.0)
    monkeypatch.setattr(adt_watch, "_rate_limit_snapshot", lambda: None)
    monkeypatch.setattr(adt_watch, "_branch_protection_snapshot", lambda repo, branch: None)
    monkeypatch.setattr(adt_watch, "_pool_floor_breached", lambda pools: "")
    monkeypatch.setattr(adt_watch, "_sync", lambda root: False)
    monkeypatch.setattr(adt_watch, "_render", lambda *a, **k: calls.append("render"))
    monkeypatch.setattr(adt_watch.adt_phone_home, "run_once", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch.adt_machines, "report",
                        lambda root, adt_root, repo, quiet=True: calls.append(("report", root, adt_root, repo)))

    adt_watch.watch(str(tmp_path), once=True)
    assert ("report", str(tmp_path), adt_watch._ADT_ROOT, REPO) in calls
    assert calls.index("render") > calls.index(("report", str(tmp_path), adt_watch._ADT_ROOT, REPO))

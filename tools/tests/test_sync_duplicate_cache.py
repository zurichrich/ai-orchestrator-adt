# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Two cache files for one Issue are never pushed (ADT-354).

A stale stub for a closed Issue survived an uninstall and reinstall next to the
real ticket file. The push loop pushed every file that had changed, so the
stub's `state: open` would reopen the closed Issue and move its card back to
Ideas. The push now refuses any Issue number that more than one cache file
claims, and reports each file so a human can delete the wrong one."""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_sync  # noqa: E402
import adt_watch  # noqa: E402


REAL = ("tasks/done/issue-877.md",
        {"issue_number": 877, "slug": "issue-877", "stage": "done",
         "state": "closed", "title": "real"})
STUB = ("bugs/ideas/issue-877.md",
        {"issue_number": 877, "slug": "issue-877", "stage": "ideas",
         "state": "open", "title": "stub"})
OTHER = ("tasks/qa/issue-900.md",
         {"issue_number": 900, "slug": "issue-900", "stage": "qa",
          "state": "open", "title": "other"})


def _run(monkeypatch, files):
    pushed = []
    monkeypatch.setattr(adt_sync, "load_config", lambda root: {"repo": "o/r"})
    monkeypatch.setattr(adt_sync, "iter_cache_files", lambda cfg: list(files))
    monkeypatch.setattr(adt_sync, "_load_sync_state", lambda cfg: {})  # a new machine
    monkeypatch.setattr(adt_sync, "_persist_state", lambda cfg, s: None)
    monkeypatch.setattr(adt_sync, "checkpoint_tokens", lambda *a, **k: [])
    monkeypatch.setattr(adt_sync, "restamp_closed", lambda *a, **k: [])
    monkeypatch.setattr(adt_sync, "checkpoint_stamp", lambda *a, **k: [])

    def reconcile_one(path, data, cfg, dry_run=False):
        pushed.append(path)
        return {"path": path, "action": "updated"}

    monkeypatch.setattr(adt_sync, "reconcile_one", reconcile_one)
    return adt_sync.reconcile_all("/nowhere", pull=False), pushed


def test_neither_file_of_a_duplicate_pair_is_pushed(monkeypatch):
    results, pushed = _run(monkeypatch, [REAL, STUB, OTHER])
    assert REAL[0] not in pushed and STUB[0] not in pushed, pushed


def test_both_paths_are_reported(monkeypatch):
    results, _ = _run(monkeypatch, [REAL, STUB, OTHER])
    dups = [r for r in results if r["action"] == "duplicate"]
    assert sorted(r["path"] for r in dups) == sorted([REAL[0], STUB[0]])
    assert all(r["number"] == 877 for r in dups)


def test_other_tickets_are_still_pushed(monkeypatch):
    _, pushed = _run(monkeypatch, [REAL, STUB, OTHER])
    assert pushed == [OTHER[0]]


def test_a_single_file_per_issue_is_pushed(monkeypatch):
    _, pushed = _run(monkeypatch, [REAL, OTHER])
    assert pushed == [REAL[0], OTHER[0]]


def test_a_waiting_duplicate_does_not_keep_the_watcher_eager(monkeypatch):
    monkeypatch.setattr(adt_sync, "reconcile_all", lambda root, pull=True: [
        {"path": "a.md", "action": "noop"},
        {"path": REAL[0], "action": "duplicate", "number": 877},
        {"path": STUB[0], "action": "duplicate", "number": 877}])
    assert adt_watch._sync("/nowhere") is False


def test_a_real_push_still_counts_as_movement(monkeypatch):
    monkeypatch.setattr(adt_sync, "reconcile_all", lambda root, pull=True: [
        {"path": "a.md", "action": "updated"},
        {"path": REAL[0], "action": "duplicate", "number": 877}])
    assert adt_watch._sync("/nowhere") is True

# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that adt_dod._check_run runs each must_run command once per grading pass when given a cache."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_dod  # noqa: E402


def _count_runs(monkeypatch):
    """Replace the shell with a counter, keyed by command."""
    calls: dict[str, int] = {}

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kw):
        calls[command] = calls.get(command, 0) + 1
        return _R()

    monkeypatch.setattr(adt_dod.subprocess, "run", fake_run)
    return calls


def test_one_command_named_by_three_conditions_runs_once(monkeypatch):
    calls = _count_runs(monkeypatch)
    now_cache: dict = {}
    evs = [{"must_run": "bash slow-suite.sh", "lane": "build"} for _ in range(3)]

    for ev in evs:
        adt_dod._check_run(ev, ".", None, now_cache)

    assert calls["bash slow-suite.sh"] == 1, (
        "expected one execution for three conditions naming the same command, "
        "got %d" % calls["bash slow-suite.sh"])


def test_distinct_commands_are_not_collapsed(monkeypatch):
    calls = _count_runs(monkeypatch)
    now_cache: dict = {}

    adt_dod._check_run({"must_run": "echo a", "lane": "build"}, ".", None, now_cache)
    adt_dod._check_run({"must_run": "echo b", "lane": "build"}, ".", None, now_cache)
    adt_dod._check_run({"must_run": "echo a", "lane": "build"}, ".", None, now_cache)

    assert calls == {"echo a": 1, "echo b": 1}, calls


def test_no_cache_means_no_memo(monkeypatch):
    """Without a cache, the command runs once per condition."""
    calls = _count_runs(monkeypatch)

    for _ in range(3):
        adt_dod._check_run({"must_run": "bash slow-suite.sh", "lane": "build"}, ".")

    assert calls["bash slow-suite.sh"] == 3, calls

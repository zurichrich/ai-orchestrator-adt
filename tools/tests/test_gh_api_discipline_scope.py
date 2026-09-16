# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests the ban on gh GraphQL porcelain (`gh issue view`, `gh pr merge`, ...):
adt_sync._gh refuses a banned argv, and tests/test_gh_api_discipline.sh flags
it in playbooks."""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_sync  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "..", "tests", "test_gh_api_discipline.sh")


# --- the runner ban in adt_sync._gh ----------------------------------------

def test_banned_porcelain_is_refused_before_any_subprocess():
    for argv in (["issue", "view", "58", "--repo", "o/r"],
                 ["pr", "view", "12", "--json", "mergeable"],
                 ["pr", "create", "--title", "x"],
                 ["pr", "merge", "12"]):
        try:
            adt_sync._gh(argv)
        except adt_sync.GhError as e:
            assert "GraphQL porcelain" in str(e)
        else:                                        # pragma: no cover
            raise AssertionError(f"{argv[:2]} must be refused")


def test_an_argv_written_across_several_lines_is_refused():
    argv = [
        "issue", "view", "58", "--repo", "o/r",
        "--json", "number,title,body",
    ]
    try:
        adt_sync._gh(argv)
    except adt_sync.GhError:
        pass
    else:                                            # pragma: no cover
        raise AssertionError("the multi-line shape must be refused too")


def test_indirect_argv_is_caught():
    # Built in pieces, then handed to the runner.
    built = ["issue", "view"]
    built += ["58", "--repo", "o/r"]
    try:
        adt_sync._gh(built)
    except adt_sync.GhError:
        pass
    else:                                            # pragma: no cover
        raise AssertionError("indirect argv must be refused")


def test_permitted_verbs_are_not_banned():
    # Allowed: one-shot issue commands, and Projects v2, which has no REST API.
    for pair in (("issue", "create"), ("issue", "list"), ("issue", "edit"),
                 ("issue", "close"), ("issue", "reopen"), ("project", "view"),
                 ("project", "item-edit"), ("api", "repos/o/r/issues/1")):
        assert pair not in adt_sync._BANNED_PORCELAIN, pair


def test_the_real_sync_module_makes_no_banned_call(monkeypatch):
    # _current_issue calls gh through the REST api, not issue view.
    seen = []
    monkeypatch.setattr(adt_sync, "_gh",
                        lambda args, input_text=None: seen.append(args) or "{}")
    adt_sync._current_issue("o/r", 58)
    assert seen and seen[0][0] == "api"
    assert tuple(seen[0][:2]) not in adt_sync._BANNED_PORCELAIN


# --- the playbook grep in tests/test_gh_api_discipline.sh ------------------

def _run(root):
    return subprocess.run(["bash", SCRIPT, str(root)],
                          capture_output=True, text=True)


def test_playbook_layer_still_greps_shell_text(tmp_path):
    d = tmp_path / "commands"
    d.mkdir(parents=True, exist_ok=True)
    (d / "x.md").write_text("Run `gh pr view 12 --json mergeable` here.\n")
    r = _run(tmp_path)
    assert r.returncode == 1, r.stdout
    assert "x.md" in r.stdout


def test_clean_playbook_layer_passes(tmp_path):
    d = tmp_path / "commands"
    d.mkdir(parents=True, exist_ok=True)
    (d / "x.md").write_text("Use `gh api repos/o/r/pulls/12` instead.\n")
    assert _run(tmp_path).returncode == 0


def test_issue_creation_is_not_flagged_in_playbooks(tmp_path):
    d = tmp_path / "commands"
    d.mkdir(parents=True, exist_ok=True)
    (d / "brief.md").write_text('url=$(gh issue create --repo r --title t)\n')
    assert _run(tmp_path).returncode == 0

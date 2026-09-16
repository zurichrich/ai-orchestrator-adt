# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests the kanban header badge that shows whether the main branch is
protected, and the snapshot the watcher takes to feed it."""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402
import adt_watch  # noqa: E402


def _badge(tmp_path, protection):
    build_kanban.configure(str(tmp_path), branch_protection=protection)
    return build_kanban._protection_badge()


def test_protected_and_unprotected_render_differently(tmp_path):
    prot = _badge(tmp_path, ("main", "protected"))
    unprot = _badge(tmp_path, ("main", "unprotected"))
    assert "prot-ok" in prot and "protected" in prot
    assert "prot-warn" in unprot and "unprotected" in unprot
    assert prot != unprot
    # Both carry the badge's element id.
    assert 'id="board-protection"' in prot
    assert 'id="board-protection"' in unprot


def test_unreadable_state_renders_nothing(tmp_path):
    # An unknown state shows no badge rather than a guessed one.
    assert _badge(tmp_path, None) == ""
    assert _badge(tmp_path, ("main", "unknown")) == ""
    assert _badge(tmp_path, ()) == ""


def test_badge_label_uses_the_configured_branch_name(tmp_path):
    # Check the visible label only: the tooltip says "main branch" regardless.
    out = _badge(tmp_path, ("master", "unprotected"))
    label = out.split(">")[-2].replace("</span", "")
    assert label == "\U0001F513 master unprotected", label


# The branch name comes from config, and git ref names may contain <, >, & and ".
def test_branch_label_is_html_escaped(tmp_path):
    out = _badge(tmp_path, ('feat/<img src=x>&"q"', "unprotected"))
    assert "&lt;img" in out
    assert "&amp;" in out
    assert "<img" not in out
    assert 'x>&"q"' not in out


# The raw protection response lists bypass actors and teams; none of it may
# reach the snapshot or the page.
_RAW_KEYS = ("required_pull_request_reviews", "enforce_admins", "url",
             "restrictions", "allow_force_pushes")

PROTECTED_JSON = (
    '{"url":"https://api.github.com/repos/o/r/branches/main/protection",'
    '"required_pull_request_reviews":{"required_approving_review_count":0},'
    '"enforce_admins":{"enabled":false},"restrictions":null,'
    '"allow_force_pushes":{"enabled":false}}'
)
UNPROTECTED_JSON = '{"message":"Branch not protected","status":"404"}'
FORBIDDEN_JSON = '{"message":"Resource not accessible by personal access token"}'


class _Result:
    def __init__(self, returncode, stdout):
        self.returncode, self.stdout, self.stderr = returncode, stdout, ""


def _snapshot(monkeypatch, returncode, stdout, repo="o/r", branch="main"):
    """Run the real snapshot with subprocess.run faked."""
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: _Result(returncode, stdout),
    )
    return adt_watch._branch_protection_snapshot(repo, branch)


def test_snapshot_classifies_the_measured_response_shapes(monkeypatch):
    # Response bodies as returned by the GitHub API.
    assert _snapshot(monkeypatch, 0, PROTECTED_JSON) == ("main", "protected")
    assert _snapshot(monkeypatch, 1, UNPROTECTED_JSON) == ("main", "unprotected")
    # A 403 means unknown, not unprotected.
    assert _snapshot(monkeypatch, 1, FORBIDDEN_JSON) is None


def test_snapshot_returns_state_and_branch_only_never_the_raw_body(monkeypatch):
    snap = _snapshot(monkeypatch, 0, PROTECTED_JSON)
    assert snap == ("main", "protected")
    flat = repr(snap)
    for key in _RAW_KEYS:
        assert key not in flat, f"raw response key {key!r} leaked into the snapshot"


def test_badge_rendered_from_a_snapshot_carries_no_raw_body(tmp_path, monkeypatch):
    snap = _snapshot(monkeypatch, 0, PROTECTED_JSON)
    out = _badge(tmp_path, snap)
    for key in _RAW_KEYS:
        assert key not in out, f"raw response key {key!r} reached the rendered page"


def test_snapshot_never_raises_and_needs_both_arguments(monkeypatch):
    def _boom(*a, **k):
        raise OSError("gh missing")
    monkeypatch.setattr("subprocess.run", _boom)
    # Any failure returns None, which drops the badge.
    assert adt_watch._branch_protection_snapshot("o/r", "main") is None
    assert adt_watch._branch_protection_snapshot("", "main") is None
    assert adt_watch._branch_protection_snapshot("o/r", "") is None


# The full render path. `_render` swallows exceptions, so these tests check the
# file it writes rather than that the call returned.

import json  # noqa: E402


def _cache_with_one_ticket(tmp_path):
    cache = tmp_path / "cache"
    (cache / "enhancements" / "ideas").mkdir(parents=True, exist_ok=True)
    (cache / "enhancements" / "ideas" / "x.md").write_text(
        "---\nslug: x\nid: ADT-1\ntitle: A ticket\ntype: enhancement\n"
        "stage: ideas\nstate: open\n---\n\n# A ticket\n")
    return cache


def _render_live(tmp_path, protection):
    """Render the board through adt_watch._render and return the HTML."""
    cache = _cache_with_one_ticket(tmp_path)
    cfg = {"repo": "o/r", "main_branch": "main", "id_prefix": "ADT",
           "cache_dir": str(cache), "board_out": str(cache / "kanban.html")}
    adt_watch._render(str(cache), cfg, "", quiet=True, pools=[],
                      protection=protection)
    out = cache / "kanban.html"
    return out.read_text() if out.exists() else ""


def test_render_writes_the_board_with_the_protected_badge(tmp_path):
    html = _render_live(tmp_path, ("main", "protected"))
    assert html, "no kanban.html was written"
    assert 'id="board-protection"' in html
    assert "prot-ok" in html


def test_render_writes_the_board_with_the_unprotected_badge(tmp_path):
    html = _render_live(tmp_path, ("main", "unprotected"))
    assert html, "no kanban.html was written"
    assert 'id="board-protection"' in html
    assert "prot-warn" in html


def test_render_without_a_snapshot_writes_the_board_with_no_badge(tmp_path):
    html = _render_live(tmp_path, None)
    assert html, "no kanban.html was written with protection=None"
    assert 'id="board-protection"' not in html


def test_run_accepts_and_forwards_branch_protection(tmp_path):
    import inspect
    assert "branch_protection" in inspect.signature(build_kanban.run).parameters
    cache = _cache_with_one_ticket(tmp_path)
    build_kanban.run(str(cache), backlog_root="", id_prefix="ADT",
                     branch_protection=("release", "unprotected"), quiet=True)
    assert build_kanban.BRANCH_PROTECTION == ("release", "unprotected")
    assert "release" in build_kanban._protection_badge()

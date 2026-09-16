# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that adt_watch._render copies kanban.html into the project's .adt/
directory, does not copy references.html, and removes a stale .adt/tickets/."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_watch  # noqa: E402


def _seed_cache(cache: str) -> None:
    """Write a board + the artifacts it links to into a fake cache dir."""
    with open(os.path.join(cache, "kanban.html"), "w") as f:
        f.write('<a href="references.html">refs</a>'
                '<a href="tickets/foo.html">foo</a>')
    with open(os.path.join(cache, "references.html"), "w") as f:
        f.write("<h1>References</h1>")
    os.makedirs(os.path.join(cache, "tickets"), exist_ok=True)
    with open(os.path.join(cache, "tickets", "foo.html"), "w") as f:
        f.write("<h1>foo</h1>")


def test_render_copies_the_board_and_removes_stale_tickets(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    code_root = tmp_path / "repo"
    (code_root / ".adt").mkdir(parents=True, exist_ok=True)
    _seed_cache(str(cache))
    # A stale .adt/tickets/ left by an older render, so the removal path runs.
    stale = code_root / ".adt" / "tickets"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "orphan.html").write_text("<h1>orphan</h1>")

    # Stub the render so only the copy step runs.
    monkeypatch.setattr(adt_watch, "_HAVE_RENDERER", True)
    monkeypatch.setattr(adt_watch.build_kanban, "run", lambda **kw: None)

    adt_watch._render(str(cache), cfg={}, code_root=str(code_root))

    dst = code_root / ".adt"
    assert (dst / "kanban.html").is_file(), "board not copied"
    assert not (dst / "references.html").exists(), \
        "references.html was copied; only kanban.html should be"
    assert not (dst / "tickets").exists(), \
        "stale tickets/ tree left in .adt/"


def test_render_survives_missing_optional_artifacts(tmp_path, monkeypatch):
    """With only kanban.html in the cache, the copy succeeds without raising."""
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    code_root = tmp_path / "repo"
    (code_root / ".adt").mkdir(parents=True, exist_ok=True)
    with open(cache / "kanban.html", "w") as f:
        f.write("<html></html>")

    monkeypatch.setattr(adt_watch, "_HAVE_RENDERER", True)
    monkeypatch.setattr(adt_watch.build_kanban, "run", lambda **kw: None)

    adt_watch._render(str(cache), cfg={}, code_root=str(code_root))

    assert (code_root / ".adt" / "kanban.html").is_file()
    assert not (code_root / ".adt" / "references.html").exists()

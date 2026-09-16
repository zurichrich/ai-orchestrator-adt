# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests the `track:` field in build_kanban: how Item.track is parsed, and that
the card shows no tier chip for any tier."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402


def _brief(tmp_path: Path, track_line: str) -> build_kanban.Item:
    """Write a minimal brief with the given frontmatter line, return its Item."""
    p = tmp_path / "x.md"
    fm = "---\nslug: x\npriority: P1\nsize: M\n" + track_line + "id: TIX-001\n---\n\n# Title\n\nhook.\n"
    p.write_text(fm)
    return build_kanban.Item(p, "bugs", "planned")


@pytest.mark.parametrize("line,expected", [
    ("track: fast\n", "fast"),
    ("track: standard\n", "standard"),
    ("track: full\n", "full"),
    ("track: FAST\n", "fast"),          # case-insensitive
    ("track:   full  \n", "full"),       # whitespace tolerated
    ("", "standard"),                    # absent → default
    ("track: \n", "standard"),           # blank → default
    ("track: nonsense\n", "standard"),   # unknown → default, no crash
])
def test_track_parsing(tmp_path, line, expected):
    assert _brief(tmp_path, line).track == expected


def test_default_is_standard(tmp_path):
    assert _brief(tmp_path, "").track == "standard"


def test_track_chip_not_rendered_on_card(tmp_path):
    # Render a board with all three tiers and check there is no chip markup.
    build_kanban.configure(str(tmp_path))
    bl = tmp_path / ".adt" / "backlog" / "bugs" / "planned"
    bl.mkdir(parents=True, exist_ok=True)
    for slug, track in [("a", "fast"), ("b", "full"), ("c", "standard")]:
        (bl / f"{slug}.md").write_text(
            f"---\nslug: {slug}\npriority: P1\nsize: M\ntrack: {track}\nid: TIX-00{slug}\n---\n\n# {slug}\n\nhook.\n"
        )
    items = build_kanban.load_items()
    assert {it.track for it in items} == {"fast", "full", "standard"}
    html = build_kanban.render_html(items)
    assert "badge track" not in html, "expected no tier chip on the card"

# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that a ticket's `type:` frontmatter sets its type on the board, with the
folder as the fallback, and that adt_sync's no-type message names the right fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402
import adt_sync  # noqa: E402


def _brief(tmp_path: Path, type_line: str, folder: str = "tasks") -> build_kanban.Item:
    """A minimal brief carrying `type_line`, loaded as if found in `folder`."""
    p = tmp_path / "x.md"
    p.write_text(
        "---\nslug: x\npriority: P1\nsize: M\n" + type_line + "id: ADT-155\n---\n"
        "\n# Title\n\nhook.\n"
    )
    return build_kanban.Item(p, folder, "planned")


# ── the preference: frontmatter wins over the folder ────────────────────────

@pytest.mark.parametrize("line,expected", [
    ("type: bug\n", "bugs"),
    ("type: enhancement\n", "enhancements"),
    ("type: task\n", "tasks"),
    ("type: bugs\n", "bugs"),                 # plural form accepted too
    ("type: enhancements\n", "enhancements"),
    ("type:   Bug \n", "bugs"),               # value case + whitespace tolerated
    ("type: ENHANCEMENT\n", "enhancements"),
])
def test_frontmatter_type_beats_the_folder(tmp_path, line, expected):
    # Every case is loaded from `tasks/`, so a pass means the folder did not decide.
    assert _brief(tmp_path, line).type == expected


# ── the fallback: anything unusable keeps the folder's value ────────────────

@pytest.mark.parametrize("line", [
    "",                    # absent
    "type: \n",            # blank
    "type: weird\n",       # not one of the three
    "type: bugz\n",        # near-miss typo
])
def test_unusable_frontmatter_falls_back_to_the_folder(tmp_path, line):
    assert _brief(tmp_path, line, folder="tasks").type == "tasks"
    assert _brief(tmp_path, line, folder="bugs").type == "bugs"


def test_type_helper_always_returns_a_known_type():
    for raw in (None, "", "  ", "bug", "BUGS", "weird", "task", 0):
        got = build_kanban._type_from_fm({"type": raw}, "tasks")
        assert got in build_kanban.TYPES, (raw, got)


# ── the detail panel: an unrecognised type must not crash the render ────────

def test_unrecognised_type_does_not_break_the_detail_panel(tmp_path):
    build_kanban.configure(str(tmp_path))
    bl = tmp_path / ".adt" / "backlog" / "bugs" / "planned"
    bl.mkdir(parents=True, exist_ok=True)
    (bl / "z.md").write_text(
        "---\nslug: z\npriority: P1\nsize: M\ntype: weird\nid: ADT-900\n---\n"
        "\n# Z\n\nhook.\n"
    )
    items = build_kanban.load_items()
    assert [it.type for it in items] == ["bugs"]      # fell back to the folder
    html = build_kanban.render_html(items)            # must not raise
    assert 'data-type="bugs"' in html
    assert "pill type-bug" in html


# ── end to end: editing `type:` re-buckets the card ─────────────────────────

def _render(tmp_path):
    build_kanban.configure(str(tmp_path))
    return build_kanban.render_html(build_kanban.load_items())


def test_setting_type_rebuckets_a_type_less_ticket(tmp_path):
    """Adding `type:` to a ticket in `tasks/` moves its card, leaving one card and one file."""
    bl = tmp_path / ".adt" / "backlog" / "tasks" / "planned"
    bl.mkdir(parents=True, exist_ok=True)
    f = bl / "adopted.md"
    f.write_text(
        "---\nslug: adopted\npriority: P1\nsize: M\nid: ADT-901\n---\n"
        "\n# Adopted\n\nhook.\n"
    )

    before = _render(tmp_path)
    assert before.count('data-type="tasks"') == 1
    assert 'data-type="bugs"' not in before

    # Edit the frontmatter in place; the file is not moved.
    f.write_text(f.read_text().replace("size: M\n", "size: M\ntype: bug\n"))

    after = _render(tmp_path)
    assert after.count('data-type="bugs"') == 1
    assert 'data-type="tasks"' not in after
    assert after.count('id="t-adopted"') == 1          # one card, not two
    assert len(list((tmp_path / ".adt" / "backlog").rglob("adopted.md"))) == 1
    assert (bl / "adopted.md").exists()                # still in tasks/ on disk


def test_the_backlog_readme_counts_follow_the_frontmatter(tmp_path):
    """The per-type counts in render_markdown's output use the frontmatter type."""
    bl = tmp_path / ".adt" / "backlog" / "tasks" / "planned"
    bl.mkdir(parents=True, exist_ok=True)
    for slug, typ in [("a", "bug"), ("b", "enhancement"), ("c", None)]:
        line = f"type: {typ}\n" if typ else ""
        (bl / f"{slug}.md").write_text(
            f"---\nslug: {slug}\npriority: P1\nsize: M\n{line}id: ADT-90{slug}\n---\n"
            f"\n# {slug}\n\nhook.\n"
        )
    build_kanban.configure(str(tmp_path))
    md = build_kanban.render_markdown(build_kanban.load_items())
    # All three files sit in tasks/; only the frontmatter distinguishes them.
    assert "Bugs (1)" in md
    assert "Enhancements (1)" in md
    assert "Tasks (1)" in md


# ── the no-type summary message ─────────────────────────────────────────────

def test_summary_names_the_render_based_remedy():
    out = adt_sync._summarise_no_type([31, 32, 33])
    assert "frontmatter" in out
    assert "next render" in out
    assert "stays where it is" in out          # says NOT to move the file
    assert "\n" not in out                     # one line

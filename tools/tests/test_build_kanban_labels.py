# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for the board's lane labels: the `planned` lane shows as "Plan" while
the stage key stays `planned`, and the lane headers list the right commands."""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402


def test_stage_label_overrides_planned_but_keeps_others():
    # `planned` has a display override; every other key is title-cased.
    assert build_kanban.stage_label("planned") == "Plan"
    assert build_kanban.stage_label("building") == "Building"
    assert build_kanban.stage_label("ready-to-release") == "Ready To Release"


def _load_items(tmp_path: Path):
    """Set up a backlog with one planned + one building card; return the Items."""
    build_kanban.configure(str(tmp_path))
    root = tmp_path / ".adt" / "backlog" / "enhancements"
    for status, slug in [("planned", "p"), ("building", "b")]:
        d = root / status
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{slug}.md").write_text(
            f"---\nslug: {slug}\npriority: P1\nsize: M\nid: ADT-10{slug}\n---\n\n# {slug}\n\nhook.\n"
        )
    return build_kanban.load_items()


def _render_board(tmp_path: Path) -> str:
    """Render the board HTML (lane headings + lane command reminders)."""
    return build_kanban.render_html(_load_items(tmp_path))


def test_planned_lane_heading_reads_plan(tmp_path):
    html = _render_board(tmp_path)
    # Lane heading: "Plan", not "Planned".
    assert "<h3>Plan <span" in html
    assert "<h3>Planned <span" not in html


def test_planned_ticket_status_pill_reads_plan(tmp_path):
    # The status pill lives on the per-ticket detail page (render_ticket_detail).
    planned = next(it for it in _load_items(tmp_path) if it.status == "planned")
    html = build_kanban.render_ticket_detail(planned)
    # Pill text is the label "Plan"; the CSS class stays the canonical key.
    assert '<span class="pill status planned">Plan</span>' in html
    assert ">Planned</span>" not in html


def test_planned_lane_lists_diagnose(tmp_path):
    """Skills are listed with the same plain `tok` class as commands."""
    html = _render_board(tmp_path)
    assert '<span class="tok">/adt-diagnose</span>' in html


def test_building_lane_lists_build_todone(tmp_path):
    html = _render_board(tmp_path)
    assert '<span class="tok">/adt-build-todone</span>' in html


def test_no_lane_lists_a_hook(tmp_path):
    """Lane headers list only things you type, so no hook names appear."""
    html = _render_board(tmp_path)
    for hook in ("done-guard", "deferral-guard", "phrase-linter"):
        assert f'<span class="tok">{hook}' not in html, f"lane header names hook {hook}"
    assert "hooks:" not in html and "hook:" not in html, (
        "a lane header still carries a `hook:` token")

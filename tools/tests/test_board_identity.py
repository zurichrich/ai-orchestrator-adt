# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that the board's <h1> names the repo and says the timestamp comes
from ADT, checked against the rendered HTML."""

from __future__ import annotations

import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402


def _board(tmp_path: Path, **kw) -> str:
    build_kanban.configure(str(tmp_path), **kw)
    d = tmp_path / ".adt" / "backlog" / "enhancements" / "ideas"
    d.mkdir(parents=True, exist_ok=True)
    (d / "x.md").write_text(
        "---\nslug: x\npriority: P1\nsize: S\nid: ADT-1\n"
        "created: 2026-09-01T10:00:00Z\n---\n\n# X\n\nhook.\n")
    return build_kanban.render_html(build_kanban.load_items())


def _h1(html: str) -> str:
    m = re.search(r"<h1>.*?</h1>", html, re.S)
    assert m, "board rendered no <h1>"
    return m.group(0)


def test_header_names_the_repo(tmp_path):
    h1 = _h1(_board(tmp_path, repo_name="zurichrich/ai-orchestrator-adt"))
    assert 'id="board-repo"' in h1
    # Check the repo slug text, not only the anchor.
    assert "zurichrich/ai-orchestrator-adt issues" in h1


def test_header_attributes_the_timestamp_to_adt(tmp_path):
    h1 = _h1(_board(tmp_path, repo_name="owner/repo"))
    m = re.search(r'<span class="gen-at">([^<]*)</span>', h1)
    assert m, "no gen-at span"
    text = m.group(1)
    assert text.startswith("updated at ") and text.endswith(" by ADT"), text
    # A real timestamp sits between the two.
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text), text


def test_two_boards_are_distinguishable_by_their_headers(tmp_path):
    a = _h1(_board(tmp_path / "a", repo_name="owner/alpha"))
    b = _h1(_board(tmp_path / "b", repo_name="owner/beta"))
    assert "owner/alpha issues" in a and "owner/beta issues" not in a
    assert "owner/beta issues" in b and "owner/alpha issues" not in b


def test_header_falls_back_when_no_repo_name_is_configured(tmp_path):
    """Without repo_name the header reads "Project backlog" and has no repo link."""
    h1 = _h1(_board(tmp_path))
    assert "Project backlog" in h1
    assert 'id="board-repo"' not in h1
    # The ADT attribution is not conditional on the repo name.
    assert "by ADT</span>" in h1


def test_repo_name_is_escaped(tmp_path):
    h1 = _h1(_board(tmp_path, repo_name='a<b&"c'))
    assert "<b&" not in h1.replace("<b>", "")
    assert "a&lt;b&amp;&quot;c issues" in h1



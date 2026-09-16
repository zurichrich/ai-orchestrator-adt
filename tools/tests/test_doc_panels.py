# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that every document linked from docs/references.md gets a readable
panel on the board, using this checkout's real catalogue.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban as bk  # noqa: E402

ADT = TOOLS.parent


def _board(tmp_path, refs_md: str) -> str:
    bk.configure(str(tmp_path), issues_url="https://github.com/o/r/issues")
    root = tmp_path / ".adt" / "backlog" / "tasks" / "planned"
    root.mkdir(parents=True, exist_ok=True)
    (root / "t.md").write_text(
        "---\nslug: t\npriority: P2\nid: ADT-1\nissue_number: 1\n---\n\n# t\n\nx.\n")
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "references.md").write_text(refs_md)
    bk.COMMANDS_DOC_SRC = str(docs / "references.md")
    return bk.render_html(bk.load_items())


def test_real_catalogue_links_and_panels_are_set_equal(tmp_path):
    html = _board(tmp_path, (ADT / "docs" / "references.md").read_text())
    ids = set(re.findall(r'id="(doc-[^"]+)"', html))
    hrefs = set(re.findall(r'href="#(doc-[^"]+)"', html))
    assert ids, "no doc panels rendered at all"
    assert ids == hrefs, (
        f"links without panels: {sorted(hrefs - ids)}; "
        f"panels without links: {sorted(ids - hrefs)}")


def test_the_deepest_catalogue_path_gets_a_panel(tmp_path):
    """defaults/skills/adt-diagnose/SKILL.md: four segments, mixed case."""
    html = _board(tmp_path, (ADT / "docs" / "references.md").read_text())
    assert 'id="doc-defaults-skills-adt-diagnose-skill"' in html
    assert 'href="#doc-defaults-skills-adt-diagnose-skill"' in html


def test_references_panel_and_link_exist(tmp_path):
    html = _board(tmp_path, (ADT / "docs" / "references.md").read_text())
    assert '<article class="ticket-detail" id="references"' in html
    assert 'href="#references"' in html


def test_missing_catalogue_entry_renders_as_text_with_no_panel_or_link(tmp_path):
    html = _board(tmp_path, "# R\n\nsee [gone](../../agent-dev-team/commands/nope.md).\n")
    assert 'id="doc-commands-nope"' not in html
    local = {h for h in re.findall(r'href="([^"]+)"', html)
             if not h.startswith("#") and not re.match(r"[a-z]+:", h)}
    assert local == set(), f"missing doc left a local link: {sorted(local)}"


def test_panels_are_siblings_not_nested(tmp_path):
    """Panels open with :target CSS, so a nested panel would hide itself when opened."""
    html = _board(tmp_path, (ADT / "docs" / "references.md").read_text())
    first = html.index('<article class="ticket-detail" id="references"')
    nxt = html.index('<article class="ticket-detail" id="doc-', first)
    assert "</article>" in html[first:nxt], "a doc panel opened inside the references panel"


# ── What a reader sees in a panel ───────────────────────────────────────────

def test_panel_body_has_no_raw_frontmatter(tmp_path):
    html = _board(tmp_path, (ADT / "docs" / "references.md").read_text())
    i = html.index('id="doc-commands-plan"')
    body = html[html.index('ticket-body', i):][:600]
    assert "name: adt-plan" not in body, f"raw frontmatter in panel body: {body[:200]}"
    assert "description:" not in body.split("</div>")[0]


def test_every_doc_panel_has_a_visible_title(tmp_path):
    """The body's own <h1> is hidden by CSS, so the title must be in the header."""
    html = _board(tmp_path, (ADT / "docs" / "references.md").read_text())
    panels = re.findall(
        r'<article class="ticket-detail" id="doc-[^"]+">(.*?)</article>', html, re.S)
    assert panels, "no doc panels rendered"
    # Match only the header block, so the hidden body <h1> does not count.
    untitled = []
    for p in panels:
        m = re.search(r'<div class="ticket-header">(.*?)</div>\s*<div class="ticket-body"',
                      p, re.S)
        header = m.group(1) if m else ""
        if not re.search(r"<h1>.+?</h1>", header, re.S):
            untitled.append(p)
    assert not untitled, f"{len(untitled)} of {len(panels)} doc panels have no header title"


def test_ticket_body_h1_is_still_hidden_by_css(tmp_path):
    """If this rule is removed, the header-title test above no longer needs to exist."""
    assert re.search(r"\.ticket-body h1 \{[^}]*display:\s*none", bk.TICKET_CSS), \
        "expected .ticket-body h1 to have display: none"


def test_wide_tables_scroll_themselves_not_the_page(tmp_path):
    """At every width, not only on a phone. The rule used to sit inside a 640px
    media query, so on a desktop a table holding a long path spilled out past the
    panel's right edge."""
    assert "<div class=\"table-wrap\"><table>" in bk.md_to_html("| a | b |\n|---|---|\n| 1 | 2 |")
    assert re.search(r"\.ticket-body \.table-wrap \{[^}]*overflow-x:\s*auto", bk.TICKET_CSS)
    assert "@media" not in bk.TICKET_CSS


def test_long_tokens_wrap_in_the_header_and_the_body():
    """ADT-386: a long unbroken token must wrap rather than widen the panel. The
    header sits outside .ticket-body, so it needs its own rule."""
    css = bk.TICKET_CSS
    for selector in (r"\.ticket-header", r"\.ticket-body"):
        assert re.search(selector + r"\s*\{[^}]*overflow-wrap:\s*break-word", css), selector
    assert re.search(r"\.ticket-body code\s*\{[^}]*overflow-wrap:\s*anywhere", css)

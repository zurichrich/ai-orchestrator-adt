# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that ticket detail is rendered inside kanban.html and opened by an
in-page #fragment link, so the board has no links to local files."""

from __future__ import annotations

import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402

SLUGS = ["alpha", "beta", "gamma"]


def _seed(tmp_path: Path, *, with_commits: bool = False):
    build_kanban.configure(str(tmp_path), issues_url="https://github.com/o/r/issues")
    root = tmp_path / ".adt" / "backlog" / "enhancements" / "planned"
    root.mkdir(parents=True, exist_ok=True)
    for n, slug in enumerate(SLUGS, start=1):
        commits = "commits: abc1234, def5678\n" if with_commits else ""
        # The body has a relative markdown link both bare and inside a code
        # span, so the no-local-links test has something to catch.
        body = (
            "hook text.\n\n"
            "see [plan](../../agent-dev-team/commands/plan.md) and "
            "`[plan](../../agent-dev-team/commands/plan.md)` and "
            "[gh](https://github.com/o/r) and [top](#top).\n"
        )
        (root / f"{slug}.md").write_text(
            f"---\nslug: {slug}\npriority: P1\nsize: M\nid: ADT-{100 + n}\n"
            f"issue_number: {100 + n}\n{commits}---\n\n# {slug} title\n\n{body}"
        )
    # A references catalogue with relative links, so the board renders its
    # references panel too.
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "references.md").write_text(
        "# References\n\nsee [plan](../../agent-dev-team/commands/plan.md) "
        "and [`loops.md`](../../agent-dev-team/docs/loops.md).\n"
    )
    build_kanban.COMMANDS_DOC_SRC = str(docs / "references.md")
    return build_kanban.load_items()


def _board(tmp_path: Path, **kw) -> str:
    return build_kanban.render_html(_seed(tmp_path, **kw))


def test_one_detail_article_per_ticket(tmp_path):
    html = _board(tmp_path)
    ids = re.findall(r'<article class="ticket-detail" id="t-([a-z]+)">', html)
    assert sorted(ids) == sorted(SLUGS), f"expected one article per ticket, got {ids}"


def test_card_title_links_are_in_page_fragments(tmp_path):
    html = _board(tmp_path)
    hrefs = re.findall(r'<a class="title" href="([^"]+)"', html)
    assert len(hrefs) == len(SLUGS)
    assert all(h.startswith("#t-") for h in hrefs), hrefs


def test_every_href_is_a_fragment_or_an_external_url(tmp_path):
    html = _board(tmp_path)
    local = {
        h for h in re.findall(r'href="([^"]+)"', html)
        if not h.startswith("#") and not re.match(r"[a-z]+:", h)
    }
    assert local == set(), \
        f"local file links on the board: {sorted(local)}"


def test_panel_back_link_is_hash_and_footer_links_are_https(tmp_path):
    html = _board(tmp_path)
    assert '<a class="back" href="#">' in html
    raws = re.findall(r'<a class="raw-link" href="([^"]+)"', html)
    assert raws, "no footer link rendered"
    assert all(r.startswith("https://") for r in raws), raws


def test_each_card_has_exactly_one_github_link(tmp_path):
    html = _board(tmp_path)
    cards = html.count('<a class="card-id"')
    links = len(re.findall(r'<a class="card-id"[^>]*href="https://github\.com', html))
    assert cards == len(SLUGS), f"expected {len(SLUGS)} cards, got {cards}"
    assert links == cards, f"{links} GitHub links for {cards} cards"


def test_commit_links_sit_above_the_card_click_overlay(tmp_path):
    items = _seed(tmp_path, with_commits=True)
    build_kanban.REPO_URL = "https://github.com/o/r"
    html = build_kanban.render_html(items)
    assert '<a class="commit"' in html, "commit anchors did not render"
    assert re.search(r"\.card a:not\(\.title\)[^}]*z-index", html), \
        "commit/card-id anchors are not lifted above the click overlay"
    assert re.search(r"\.card \.title::after\s*\{[^}]*inset:\s*0", html)


def test_no_tickets_dir_written_and_stale_one_removed(tmp_path):
    items = _seed(tmp_path)
    bl = tmp_path / ".adt" / "backlog"
    stale = bl / "tickets"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "orphan.html").write_text("<h1>orphan</h1>")

    build_kanban.run(project_root=str(tmp_path),
                     backlog_root=".adt/backlog", quiet=True)

    assert not stale.exists(), "stale tickets/ tree survived the render"
    assert (bl / "kanban.html").is_file()
    assert len(items) == len(SLUGS)

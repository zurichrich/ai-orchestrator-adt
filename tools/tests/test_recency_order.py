# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that each board lane lists the most recently touched card first, by
date and time, whether the frontmatter date is a full ISO-Z stamp, a bare
`YYYY-MM-DD`, or `null`. The done lane orders by close date."""

from __future__ import annotations

import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402


def _lane(tmp_path: Path, briefs) -> list[str]:
    """Render a single lane and return its card ids, top to bottom."""
    build_kanban.configure(str(tmp_path))
    d = tmp_path / ".adt" / "backlog" / "enhancements" / "ideas"
    d.mkdir(parents=True, exist_ok=True)
    for slug, fields in briefs:
        fm = "".join(f"{k}: {v}\n" for k, v in fields.items())
        (d / f"{slug}.md").write_text(
            f"---\nslug: {slug}\npriority: P1\nsize: S\nid: ADT-{slug}\n{fm}---\n"
            f"\n# {slug}\n\nhook.\n")
    html = build_kanban.render_html(build_kanban.load_items())
    # Every brief is in `ideas`, so the board's card order is the lane's.
    # Cut off the ticket-detail articles, which repeat each id.
    board = html[html.index('<div class="board">'):html.index("</body>")]
    board = board[:board.index('<article')] if '<article' in board else board
    # `Item.id` upper-cases; the slugs here are lower, so fold for comparison.
    return [s.lower() for s in
            re.findall(r'<div class="card"[^>]*data-id="ADT-(\w+)"', board)]


# ── _norm_ts ──────────────────────────────────────────────────────────


def test_norm_ts_canonicalises_every_frontmatter_shape():
    n = build_kanban._norm_ts
    assert n("2026-09-01T19:33:01Z") == "2026-09-01T19:33:01Z"
    # A bare date means start of day, so it sorts after a same-day timestamp.
    assert n("2026-09-01") == "2026-09-01T00:00:00Z"
    # A YAML null arrives as the literal string; it is not a date.
    assert n("null") == ""
    assert n(None) == "" and n("") == "" and n("  ") == ""
    assert n("garbage") == "" and n("2026-9-1") == ""


def test_norm_ts_output_is_fixed_width():
    """Sorting compares strings, so every value must be the same length."""
    n = build_kanban._norm_ts
    widths = {len(n(v)) for v in ("2026-09-01T19:33:01Z", "2026-09-01")}
    assert widths == {20}


# ── lane order ────────────────────────────────────────────────────────


def test_same_day_briefs_order_by_the_hour(tmp_path):
    order = _lane(tmp_path, [
        ("a", {"updated": "2026-09-01T09:00:00Z"}),
        ("z", {"updated": "2026-09-01T18:00:00Z"}),
        ("m", {"updated": "2026-09-01T12:00:00Z"}),
    ])
    assert order == ["z", "m", "a"], order


def test_bare_date_sorts_as_start_of_day(tmp_path):
    order = _lane(tmp_path, [
        ("bare", {"created": "2026-09-01"}),
        ("morning", {"updated": "2026-09-01T09:00:00Z"}),
    ])
    assert order == ["morning", "bare"], order


def test_null_dates_sort_last(tmp_path):
    order = _lane(tmp_path, [
        ("nulled", {"updated": "null", "closed": "null", "created": "null"}),
        ("dated", {"updated": "2026-01-01T00:00:00Z"}),
    ])
    assert order == ["dated", "nulled"], order


def test_null_updated_falls_through_to_a_real_created(tmp_path):
    order = _lane(tmp_path, [
        ("recent", {"updated": "null", "created": "2026-09-02T00:00:00Z"}),
        ("older", {"updated": "2026-09-01T00:00:00Z"}),
    ])
    assert order == ["recent", "older"], order


def test_mixed_shapes_order_correctly_against_each_other(tmp_path):
    order = _lane(tmp_path, [
        ("newest", {"updated": "2026-09-03T08:00:00Z"}),
        ("baredate", {"created": "2026-09-02"}),
        ("middle", {"updated": "2026-09-02T15:00:00Z"}),
        ("undated", {"updated": "null", "closed": "null", "created": "null"}),
        ("oldest", {"updated": "2026-08-01T23:59:59Z"}),
    ])
    assert order == ["newest", "middle", "baredate", "oldest", "undated"], order


# ── the done lane orders by close date ────────────────────────────────


def _done_lane(tmp_path, briefs) -> list[str]:
    """Render the done lane only and return its card ids, top to bottom."""
    build_kanban.configure(str(tmp_path))
    d = tmp_path / ".adt" / "backlog" / "enhancements" / "done"
    d.mkdir(parents=True, exist_ok=True)
    for slug, fields in briefs:
        fm = "".join(f"{k}: {v}\n" for k, v in fields.items())
        (d / f"{slug}.md").write_text(
            f"---\nslug: {slug}\npriority: P1\nsize: S\nid: ADT-{slug}\n{fm}---\n"
            f"\n# {slug}\n\nhook.\n")
    html = build_kanban.render_html(build_kanban.load_items())
    board = html[html.index('<div class="board">'):html.index("</body>")]
    board = board[:board.index('<article')] if '<article' in board else board
    return [s.lower() for s in
            re.findall(r'<div class="card"[^>]*data-id="ADT-(\w+)"', board)]


def test_done_lane_orders_by_closed_not_updated(tmp_path):
    """Every ticket has the same `updated`; the lane still orders by `closed`."""
    bulk = "2026-09-04T14:42:00Z"          # one edit that touched every ticket
    order = _done_lane(tmp_path, [
        ("june", {"closed": "2026-06-26T15:00:00Z", "updated": bulk}),
        ("august", {"closed": "2026-08-22T09:00:00Z", "updated": bulk}),
        ("today", {"closed": "2026-09-04T18:05:00Z", "updated": bulk}),
    ])
    assert order == ["today", "august", "june"], order


def test_done_card_chip_shows_the_close_date(tmp_path):
    build_kanban.configure(str(tmp_path))
    d = tmp_path / ".adt" / "backlog" / "enhancements" / "done"
    d.mkdir(parents=True, exist_ok=True)
    (d / "june.md").write_text(
        "---\nslug: june\npriority: P1\nsize: S\nid: ADT-june\n"
        "closed: 2026-06-26T15:00:00Z\nupdated: 2026-09-04T14:42:00Z\n---\n"
        "\n# june\n\nhook.\n")
    html = build_kanban.render_html(build_kanban.load_items())
    card = html[html.index('<div class="card"'):]
    card = card[:card.index("</div>", card.index('class="hook"'))]
    assert '<span class="updated" title="Closed">2026-06-26</span>' in card
    # The `updated` date appears nowhere on the card, including data-updated.
    # Only the card is checked because the board header has its own timestamp.
    assert "2026-09-04" not in card


def test_done_ticket_with_no_close_date_falls_back_to_updated(tmp_path):
    """A ticket in done/ whose Issue is still open has no `closed:`, so it
    sorts by `updated`."""
    order = _done_lane(tmp_path, [
        ("closedjune", {"closed": "2026-06-26T15:00:00Z",
                        "updated": "2026-09-04T14:42:00Z"}),
        ("noclose", {"updated": "2026-09-04T15:00:00Z"}),
    ])
    assert order == ["noclose", "closedjune"], order


def test_other_lanes_still_order_by_updated(tmp_path):
    """`closed` only affects the done lane."""
    order = _lane(tmp_path, [
        ("stale", {"closed": "2026-09-04T18:00:00Z",
                   "updated": "2026-08-01T00:00:00Z"}),
        ("fresh", {"updated": "2026-09-01T00:00:00Z"}),
    ])
    assert order == ["fresh", "stale"], order

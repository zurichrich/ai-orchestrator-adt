# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for the kanban header: the GitHub rate badge (GraphQL and REST pools),
its CSS size and placement, the viewport meta tag, and the auto-refresh."""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402


def _badge(tmp_path, pools):
    build_kanban.configure(str(tmp_path), rate_pools=pools)
    return build_kanban._rate_limit_badge()


def test_badge_shows_both_pools(tmp_path):
    html = _badge(tmp_path, [("GraphQL", 4880, 5000), ("REST", 22, 5000)])
    assert "GraphQL 4,880 / 5,000" in html
    assert "REST 22 / 5,000" in html
    # GraphQL is listed first. Match the visible counters, since the tooltip
    # also names both pools.
    assert html.index("GraphQL 4,880") < html.index("REST 22")


def test_badge_colour_tracks_worse_pool(tmp_path):
    # GraphQL near-exhausted must drive the class even though REST is idle.
    assert "rate-crit" in _badge(
        tmp_path, [("GraphQL", 4880, 5000), ("REST", 22, 5000)])
    assert "rate-warn" in _badge(
        tmp_path, [("GraphQL", 100, 5000), ("REST", 4200, 5000)])
    assert "rate-ok" in _badge(
        tmp_path, [("GraphQL", 100, 5000), ("REST", 200, 5000)])


def test_badge_omitted_without_snapshot(tmp_path):
    assert _badge(tmp_path, []) == ""
    assert _badge(tmp_path, None) == ""
    # A pool with no usable numbers is skipped, not rendered as garbage.
    assert _badge(tmp_path, [("GraphQL", None, 5000), ("REST", 3, 0)]) == ""


# --- placement and size, checked on the CSS in build_kanban.py --------------

import re  # noqa: E402


def _stylesheet():
    """The page CSS with comments stripped, so a font-size inside a comment is not counted."""
    src = (TOOLS / "build_kanban.py").read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", src, flags=re.S)


def _rule(selector, css=None):
    css = css if css is not None else _stylesheet()
    m = re.search(re.escape(selector) + r"\s*\{[^}]*\}", css)
    assert m, f"no rule for {selector}"
    return m.group(0)


def _css_font_size(selector):
    """The px value DECLARED for `selector`, comments excluded."""
    m = re.search(r"font-size:\s*(\d+)px", _rule(selector))
    assert m, f"no font-size declared for {selector}"
    return int(m.group(1))


def test_badge_container_is_smaller_than_meta(tmp_path):
    assert _css_font_size(".hdr-right") < _css_font_size(".meta")


def test_badge_size_matches_the_filter_label_size(tmp_path):
    assert _css_font_size(".hdr-right") == _css_font_size(".filter-group .label")


def test_badge_does_not_override_its_inherited_size(tmp_path):
    """`.rate-badge` declares no font-size, so it inherits the size set on `.hdr-right`."""
    assert "font-size" not in _rule(".rate-badge"), (
        ".rate-badge declares its own font-size, which overrides the 11px it "
        "should inherit from .hdr-right")


def test_badge_can_wrap_on_a_narrow_viewport(tmp_path):
    """A narrow-viewport media rule sets `.rate-badge` to white-space: normal."""
    css = _stylesheet()
    m = re.search(r"@media \(max-width:\s*\d+px\)\s*\{(.*?)\n\}", css, re.S)
    assert m, "no narrow-viewport rule for the header"
    assert re.search(r"\.rate-badge\s*\{[^}]*white-space:\s*normal", m.group(1)), (
        "the badge still cannot wrap at narrow widths")


def test_page_has_a_viewport_meta_tag_and_max_width_rules(tmp_path):
    """Without the viewport meta tag, phones lay out at desktop width and max-width rules never match."""
    src = (TOOLS / "build_kanban.py").read_text(encoding="utf-8")
    assert 'name="viewport"' in src, "no viewport meta — max-width rules are inert"
    assert "width=device-width" in src
    css = _stylesheet()
    assert "@media (max-width:" in css, "no max-width rule to be reachable"


def test_filter_state_survives_the_auto_refresh(tmp_path):
    """Filters are saved to localStorage and the filter buttons are restored on load."""
    src = (TOOLS / "build_kanban.py").read_text(encoding="utf-8")
    assert "localStorage" in src and "STATE_KEY" in src
    assert "saveState()" in src, "state is never written back"
    # The buttons must show the restored filter state.
    assert "btn.classList.toggle('active'" in src, "controls not re-synced on load"


def test_header_row_wraps(tmp_path):
    """`.hdr` wraps so `.hdr-right` can drop to its own row."""
    src = (TOOLS / "build_kanban.py").read_text(encoding="utf-8")
    m = re.search(r"\.hdr\s*\{[^}]*\}", src)
    assert m, ".hdr rule not found"
    assert "flex-wrap: wrap" in m.group(0)


def test_badge_is_pushed_right(tmp_path):
    src = (TOOLS / "build_kanban.py").read_text(encoding="utf-8")
    m = re.search(r"\.hdr-right\s*\{[^}]*\}", src)
    assert m and "margin-left: auto" in m.group(0)


def test_board_declares_an_auto_refresh_with_a_positive_interval(tmp_path):
    src = (TOOLS / "build_kanban.py").read_text(encoding="utf-8")
    assert 'http-equiv="refresh"' in src
    assert "REFRESH_SECONDS" in src
    assert isinstance(build_kanban.REFRESH_SECONDS, int)
    assert build_kanban.REFRESH_SECONDS > 0


def test_rendered_page_has_a_meta_refresh_and_a_timer_reload(tmp_path):
    build_kanban.configure(str(tmp_path))
    page = build_kanban.render_html([])
    assert f'http-equiv="refresh" content="{build_kanban.REFRESH_SECONDS}"' in page
    assert "location.reload()" in page, "expected a timer reload"
    # An unsubstituted placeholder coerces to NaN and the timer never fires.
    assert "__REFRESH_MS__" not in page
    assert f"}}, {build_kanban.REFRESH_SECONDS * 1000});" in page

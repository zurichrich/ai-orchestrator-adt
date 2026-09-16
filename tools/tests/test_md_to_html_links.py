# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for link handling in build_kanban.md_to_html: links inside code spans,
code spans inside link text, and local .md links mapped to panel fragments.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import build_kanban as bk  # noqa: E402

LOCAL = "../../agent-dev-team/commands/plan.md"


def _with_map(md, mapping=None):
    saved = dict(bk.DOC_FRAGMENTS)
    bk.DOC_FRAGMENTS.clear()
    bk.DOC_FRAGMENTS.update(mapping or {})
    try:
        return bk.md_to_html(md)
    finally:
        bk.DOC_FRAGMENTS.clear()
        bk.DOC_FRAGMENTS.update(saved)


# ── 1. a link inside a code span stays literal ─────────────────────────────

def test_link_inside_code_span_is_not_linkified():
    out = _with_map(f"see `[plan]({LOCAL})` here", {LOCAL: "#doc-commands-plan"})
    assert "<code>" in out
    assert "<a " not in out, f"code span produced an anchor: {out}"
    assert "[plan]" in out           # rendered literally, as written


# ── 2. a code span inside link text still links ────────────────────────────

def test_code_span_inside_link_text_still_links():
    out = _with_map("[`loops.md`](../../agent-dev-team/docs/loops.md)",
                    {"../../agent-dev-team/docs/loops.md": "#doc-docs-loops"})
    assert 'href="#doc-docs-loops"' in out
    assert "<code>loops.md</code>" in out


# ── 3. local .md links resolve to the panel fragment ───────────────────────

def test_local_md_link_becomes_a_panel_fragment():
    out = _with_map(f"[plan]({LOCAL})", {LOCAL: "#doc-commands-plan"})
    assert 'href="#doc-commands-plan"' in out
    assert LOCAL not in out          # the file path is gone from the board


def test_unresolvable_local_link_degrades_to_text():
    out = _with_map("see [x](../../nope.md) here", {})
    assert "<a " not in out
    assert ">x<" not in out and "x here" in out


# ── 4. everything else is untouched ────────────────────────────────────────

def test_external_and_fragment_links_pass_through():
    assert 'href="https://github.com/o/r"' in _with_map("[gh](https://github.com/o/r)")
    assert 'href="#top"' in _with_map("[top](#top)")


def test_plain_code_span_still_renders():
    assert "<code>--flag</code>" in _with_map("use `--flag` now")


def test_doc_slug_is_deterministic_and_path_safe():
    assert bk.doc_slug("commands/plan.md") == "doc-commands-plan"
    assert bk.doc_slug("defaults/skills/adt-diagnose/SKILL.md") == \
        "doc-defaults-skills-adt-diagnose-skill"

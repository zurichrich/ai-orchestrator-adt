# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""build_kanban.md_to_html must return on every input (#363).

A line starting with `|` that was not followed by a table separator fell
through every block branch, and the paragraph loop broke on it before reading
it, so the line index never moved and the render looped forever. The render
runs inside the sync pass lock, so one such ticket body stopped all sync for
the project. Each case runs under a timer so a regression fails the test
instead of hanging the suite.
"""
from __future__ import annotations

import os
import signal
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import build_kanban as bk  # noqa: E402

TIMEOUT_S = 3


class _Hung(Exception):
    pass


def _render(md: str) -> str:
    def _raise(*_):
        raise _Hung(f"md_to_html did not return within {TIMEOUT_S}s on {md!r}")

    old = signal.signal(signal.SIGALRM, _raise)
    signal.setitimer(signal.ITIMER_REAL, TIMEOUT_S)
    try:
        return bk.md_to_html(md)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


@pytest.mark.parametrize("md", [
    "| lone pipe line",
    "para\n| not a table",
    "    | indented pipe",
    "|",
    # The line that hung a consumer's watch: a wrapped shell pipeline in an
    # indented block the renderer does not treat as code.
    '    now: test 0 = "$(grep -vE "^COMMENT ON" schema.sql\n'
    '                     | grep -cE "<retired names>")"',
])
def test_pipe_line_that_is_not_a_table_renders_as_text(md):
    out = _render(md)
    assert "<table" not in out
    assert "|" in out, f"the | line was dropped instead of rendered: {out}"


def test_real_table_still_renders_as_a_table():
    out = _render("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table" in out
    assert "<td>1</td>" in out or ">1<" in out


def test_text_after_a_stray_pipe_line_is_kept():
    out = _render("| stray\nmore text\n\nnext para")
    assert "more text" in out
    assert "next para" in out

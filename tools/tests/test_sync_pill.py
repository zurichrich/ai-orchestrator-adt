# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""AO-006 — the header sync pill.

The board renders a validity window published by the watcher and nothing about
state; the browser decides the colour. These tests cover the four things that
can go wrong with that: the published value must never be a sentinel, the
renderer must not bake state in, the comparison must actually run, and the
watcher's own state must survive the extra write.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402


# ── 1a: the slug mirror must not drift from lib/watcher.sh ──────────────────

# Awkward on purpose: case, dots, spaces, runs of separators, leading/trailing
# punctuation, and a name that is nothing but separators.
_SLUG_NAMES = [
    "ai-orchestrator-adt",
    "My.Project",
    "Foo  Bar",
    "--weird--",
    "A_B.C",
    "UPPER",
    "trailing-",
    "-leading",
    "dots...everywhere",
    "mixed_-_separators",
    "a",
    "___",
]


def _shell_slug(name: str) -> str:
    """Run the REAL _watcher_slug from lib/watcher.sh, not a restatement of it."""
    script = REPO / "lib" / "watcher.sh"
    assert script.is_file(), f"lib/watcher.sh not found at {script}"
    # `set -euo pipefail` at the top of watcher.sh is fine under `bash -c`.
    out = subprocess.run(
        ["bash", "-c", f'source "{script}"; _watcher_slug "$1"', "_", name],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, f"shell slug failed: {out.stderr}"
    return out.stdout.rstrip("\n")


def test_slug_matches_shell():
    mismatches = []
    for name in _SLUG_NAMES:
        got, want = build_kanban.watcher_slug(name), _shell_slug(name)
        if got != want:
            mismatches.append(f"{name!r}: python={got!r} shell={want!r}")
    assert not mismatches, "watcher_slug drifted from lib/watcher.sh:\n" + "\n".join(mismatches)

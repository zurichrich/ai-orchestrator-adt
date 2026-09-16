# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests build_kanban's off-board spend: ledger spend with no card on the board,
such as `__unassigned__` turns and ids whose cache file is gone.

Card totals plus off-board spend must equal the ledger total.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_cost  # noqa: E402
import build_kanban  # noqa: E402

TICKET = """---
slug: {slug}
id: {tid}
title: {slug}
type: enhancement
priority: P1
size: S
stage: ideas
state: open
---

# {slug}

## Problem (user)
x
"""
WIDE = ("2026-01-01T00:00:00Z\t{tix}\t1000\t2000\ts\tclaude-opus-5\tstandard\t"
        "500000\t0\t100000\tmeasured")


def _board(tmp_path, rows, tickets=("ADT-1",)):
    root = tmp_path / "proj"
    (root / ".adt" / "state").mkdir(parents=True, exist_ok=True)
    (root / ".adt" / "state" / "token-usage.log").write_text(
        "".join(r + "\n" for r in rows))
    bl = root / ".adt" / "backlog" / "enhancements" / "ideas"
    bl.mkdir(parents=True, exist_ok=True)
    for tid in tickets:
        (bl / f"t{tid}.md").write_text(TICKET.format(slug=f"t{tid}", tid=tid))
    build_kanban.run(str(root), backlog_root=".adt/backlog",
                     id_prefix="ADT")
    return (root / ".adt" / "backlog" / "kanban.html").read_text()


def test_offboard_is_computed_from_cost_usage_not_from_leftovers():
    # COST_USAGE is module-global, so set it explicitly and restore it after.
    saved = dict(build_kanban.COST_USAGE)
    try:
        build_kanban.COST_USAGE.clear()
        build_kanban.COST_USAGE.update({
            "ADT-1": {"micros": 1000, "tier": "measured", "tokens": 10},
            "__UNASSIGNED__": {"micros": 5000, "tier": "measured", "tokens": 50},
        })

        class _It:
            id = "ADT-1"
        off = build_kanban._offboard_spend([_It()])
        assert off["ids"] == ["__UNASSIGNED__"]
        assert off["micros"] == 5000 and off["tokens"] == 50
    finally:
        build_kanban.COST_USAGE.clear()
        build_kanban.COST_USAGE.update(saved)


def test_carded_spend_is_never_double_counted(tmp_path):
    _board(tmp_path, [WIDE.format(tix="ADT-1")], tickets=("ADT-1",))
    assert build_kanban.OFFBOARD["micros"] is None, \
        "a ticket with a card must not also appear off-board"


def test_board_total_plus_offboard_reconciles_to_the_ledger(tmp_path):
    rows = [WIDE.format(tix="ADT-1"), WIDE.format(tix="__unassigned__"),
            WIDE.format(tix="ADT-77")]
    html = _board(tmp_path, rows, tickets=("ADT-1",))
    board = sum(int(m) for m in re.findall(r'data-cost="(\d+)"', html))
    offboard = build_kanban.OFFBOARD["micros"] or 0
    ledger = sum(v["micros"] or 0 for v in
                 adt_cost.price_ledger(rows, adt_cost.load_prices(), None).values())
    assert board + offboard == ledger, \
        f"board {board} + offboard {offboard} != ledger {ledger}"


def test_offboard_tier_is_not_measured_when_any_row_is_approximate(tmp_path):
    legacy = "2026-01-01T00:00:00Z\t__unassigned__\t400\t600\ts"
    _board(tmp_path, [WIDE.format(tix="ADT-1"), legacy], tickets=("ADT-1",))
    assert build_kanban.OFFBOARD["tier"] != "measured"



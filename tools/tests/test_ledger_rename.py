# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that build_kanban reads the cost ledger under its current name,
cost-ledger.log, and still reads the old name, token-usage.log."""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402

ROW = ("2026-01-01T00:00:00Z\tADT-1\t1000\t2000\ts\tclaude-opus-5\tstandard\t"
       "500000\t0\t100000\tmeasured")


def _proj(tmp_path, filename, rows=(ROW,)):
    root = tmp_path / "proj"
    (root / ".adt" / "state").mkdir(parents=True, exist_ok=True)
    (root / ".adt" / "state" / filename).write_text(
        "".join(r + "\n" for r in rows))
    return root


def test_the_new_name_is_the_primary():
    assert build_kanban.TOKEN_LEDGER_REL.endswith("cost-ledger.log")


def test_the_old_name_is_still_read():
    assert build_kanban.TOKEN_LEDGER_REL_PREV.endswith("token-usage.log")
    assert build_kanban.TOKEN_LEDGER_REL_PREV.startswith(".adt/")


def test_the_development_team_dotfile_is_not_read():
    assert not hasattr(build_kanban, "TOKEN_LEDGER_REL_LEGACY")


def test_reads_the_new_name(tmp_path):
    root = _proj(tmp_path, "cost-ledger.log")
    assert build_kanban.load_token_usage(root)["ADT-1"] == 3000


def test_reads_a_ledger_under_the_old_name(tmp_path):
    root = _proj(tmp_path, "token-usage.log")
    assert build_kanban.load_token_usage(root)["ADT-1"] == 3000
    assert build_kanban.load_cost_usage(root)["ADT-1"]["micros"] == 1_305_000


def test_a_ledger_under_the_old_name_is_not_read_as_costless(tmp_path):
    root = _proj(tmp_path, "token-usage.log")
    cost = build_kanban.load_cost_usage(root)
    assert cost, "expected costs from token-usage.log"
    assert cost["ADT-1"]["micros"] > 0


def test_both_files_present_does_not_double_count(tmp_path):
    """With both files present, each file's rows are counted once."""
    root = _proj(tmp_path, "cost-ledger.log")
    other = ROW.replace("ADT-1", "ADT-2")
    (root / ".adt" / "state" / "token-usage.log").write_text(other + "\n")
    got = build_kanban.load_token_usage(root)
    assert got["ADT-1"] == 3000 and got["ADT-2"] == 3000


def test_no_ledger_at_all_is_empty_not_zero(tmp_path):
    root = tmp_path / "proj"
    (root / ".adt" / "state").mkdir(parents=True, exist_ok=True)
    assert build_kanban.load_token_usage(root) == {}
    assert build_kanban.load_cost_usage(root) == {}

# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests adt_backfill_cost: rows are priced from surviving transcripts, the
ledger is backed up first, measured rows are never downgraded, and a second
run changes nothing."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_backfill_cost as bf  # noqa: E402
import adt_cost  # noqa: E402


def _msg(model="claude-opus-5", speed="standard", i=10, o=20, cr=1000, w5=0, w1=100):
    return {"type": "assistant", "message": {"role": "assistant", "model": model,
            "usage": {"input_tokens": i, "output_tokens": o,
                      "cache_read_input_tokens": cr, "speed": speed,
                      "cache_creation": {"ephemeral_5m_input_tokens": w5,
                                         "ephemeral_1h_input_tokens": w1}}}}


def _setup(tmp_path, rows, msgs=None, session="sessX"):
    root = tmp_path / "proj"
    (root / ".adt" / "state").mkdir(parents=True, exist_ok=True)
    ledger = root / ".adt" / "state" / "token-usage.log"
    ledger.write_text("".join(r + "\n" for r in rows))
    transcripts = {}
    if msgs is not None:
        t = tmp_path / f"{session}.jsonl"
        t.write_text("".join(json.dumps(m) + "\n" for m in msgs))
        transcripts[session] = str(t)
    return root, ledger, transcripts


def test_row_with_a_surviving_transcript_is_measured_exactly(tmp_path):
    """A row that sums two transcript messages gets their per-class token counts."""
    rows = ["2026-08-01T09:00:00Z\tADT-1\t20\t40\tsessX"]
    root, ledger, tx = _setup(tmp_path, rows, [_msg(), _msg()])
    st = bf.run(str(ledger), str(root), apply=True, transcripts=tx)
    assert st["newly_measured"] == 1
    out = adt_cost.parse_row(ledger.read_text().splitlines()[0])
    assert out["tier"] == "measured" and out["model"] == "claude-opus-5"
    assert out["cache_read"] == 2000 and out["cache_write_1h"] == 200


def test_row_without_a_transcript_is_estimated_not_dropped(tmp_path):
    rows = ["2026-08-01T09:00:00Z\tADT-1\t20\t40\tgone-session"]
    root, ledger, _ = _setup(tmp_path, rows)
    st = bf.run(str(ledger), str(root), apply=True, transcripts={})
    assert st["estimated"] == 1 and st["newly_measured"] == 0
    out = adt_cost.parse_row(ledger.read_text().splitlines()[0])
    assert out["tier"] == "estimated"
    assert (out["input"], out["output"]) == (20, 40), "original numbers kept"


def test_backup_is_written_before_any_rewrite(tmp_path):
    rows = ["2026-08-01T09:00:00Z\tADT-1\t20\t40\tgone"]
    root, ledger, _ = _setup(tmp_path, rows)
    original = ledger.read_text()
    bf.run(str(ledger), str(root), apply=True, transcripts={})
    backup = Path(str(ledger) + bf.BACKUP_SUFFIX)
    assert backup.exists() and backup.read_text() == original


def test_dry_run_touches_nothing(tmp_path):
    rows = ["2026-08-01T09:00:00Z\tADT-1\t20\t40\tsessX"]
    root, ledger, tx = _setup(tmp_path, rows, [_msg(), _msg()])
    before = ledger.read_text()
    bf.run(str(ledger), str(root), apply=False, transcripts=tx)
    assert ledger.read_text() == before
    assert not Path(str(ledger) + bf.BACKUP_SUFFIX).exists()


def test_running_twice_gives_the_same_file(tmp_path):
    rows = ["2026-08-01T09:00:00Z\tADT-1\t20\t40\tsessX",
            "2026-08-01T09:01:00Z\tADT-2\t5\t5\tgone"]
    root, ledger, tx = _setup(tmp_path, rows, [_msg(), _msg()])
    bf.run(str(ledger), str(root), apply=True, transcripts=tx)
    first = ledger.read_text()
    bf.run(str(ledger), str(root), apply=True, transcripts=tx)
    assert ledger.read_text() == first


def test_measured_row_is_never_downgraded_when_its_transcript_disappears(tmp_path):
    rows = ["2026-08-01T09:00:00Z\tADT-1\t20\t40\tsessX"]
    root, ledger, tx = _setup(tmp_path, rows, [_msg(), _msg()])
    bf.run(str(ledger), str(root), apply=True, transcripts=tx)
    bf.run(str(ledger), str(root), apply=True, transcripts={})   # transcript gone
    out = adt_cost.parse_row(ledger.read_text().splitlines()[0])
    assert out["tier"] == "measured" and out["cache_read"] == 2000


def test_ambiguous_range_is_left_estimated_rather_than_guessed(tmp_path):
    """No contiguous run of messages sums to the row's recorded tokens."""
    rows = ["2026-08-01T09:00:00Z\tADT-1\t999999\t999999\tsessX"]
    root, ledger, tx = _setup(tmp_path, rows, [_msg()])
    bf.run(str(ledger), str(root), apply=True, transcripts=tx)
    assert adt_cost.parse_row(ledger.read_text().splitlines()[0])["tier"] == "estimated"


def test_group_spanning_two_models_is_left_estimated(tmp_path):
    """A row holds one (model, speed), so a two-model group cannot be priced."""
    rows = ["2026-08-01T09:00:00Z\tADT-1\t20\t40\tsessX"]
    root, ledger, tx = _setup(tmp_path, rows,
                              [_msg(model="claude-opus-5"),
                               _msg(model="claude-opus-4-8")])
    bf.run(str(ledger), str(root), apply=True, transcripts=tx)
    assert adt_cost.parse_row(ledger.read_text().splitlines()[0])["tier"] == "estimated"

# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for adt_xexam, the cross-examination metric.

A ticket whose transcripts are gone reads as unmeasured (None), not zero. Slash
command expansions, tool results, notifications, system reminders and sidechain
turns are not counted as human turns.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import adt_xexam  # noqa: E402


def _turn(text, **kw):
    o = {"type": "user", "userType": "external", "isSidechain": False,
         "message": {"role": "user", "content": text}}
    o.update(kw)
    return json.dumps(o)


def _transcript(tmp_path, session, lines):
    d = tmp_path / "projects"
    d.mkdir(exist_ok=True)
    (d / (session + ".jsonl")).write_text("\n".join(lines) + "\n")
    return str(d)


def _ledger(tmp_path, rows):
    p = tmp_path / "cost-ledger.log"
    p.write_text("".join(
        "2026-08-22T00:00:00Z\t%s\t1\t1\t%s\t\tstandard\t0\t0\t0\tmeasured\n" % r
        for r in rows))
    return str(p)


# ── coverage ──────────────────────────────────────────────────────────────
def test_a_ticket_with_no_transcript_is_unmeasured_not_zero(tmp_path):
    proj = _transcript(tmp_path, "s1", [_turn("are you sure about that?")])
    ledger = _ledger(tmp_path, [("ADT-1", "s1"), ("ADT-2", "s-missing")])
    rows = {r["ticket"]: r for r in adt_xexam.measure(ledger, [proj])}
    assert rows["ADT-1"]["challenges"] == 1
    assert rows["ADT-1"]["covered"] == 1
    assert rows["ADT-2"]["challenges"] is None, "expected None for a missing transcript"
    assert rows["ADT-2"]["covered"] == 0
    assert rows["ADT-2"]["sessions"] == 1


def test_coverage_is_partial_when_some_sessions_are_missing(tmp_path):
    proj = _transcript(tmp_path, "s1", [_turn("hello")])
    ledger = _ledger(tmp_path, [("ADT-1", "s1"), ("ADT-1", "s-gone")])
    row = adt_xexam.measure(ledger, [proj])[0]
    assert (row["covered"], row["sessions"]) == (1, 2)


def test_render_shows_a_dash_not_a_zero_for_an_unmeasured_ticket(tmp_path):
    ledger = _ledger(tmp_path, [("ADT-9", "s-gone")])
    body = adt_xexam.render(adt_xexam.measure(ledger, [str(tmp_path)]))
    assert "| `ADT-9` | — | — | 0/1 |" in body
    assert "sessions with a retained transcript: 0/1" in body


# ── what counts as a human turn ───────────────────────────────────────────
def test_slash_command_expansion_is_not_a_human_turn(tmp_path):
    proj = _transcript(tmp_path, "s1", [
        _turn("<command-message>adt-plan</command-message>\n<command-name>/adt-plan</command-name>"),
        _turn("are you sure?")])
    assert list(adt_xexam.human_turns(os.path.join(proj, "s1.jsonl"))) == ["are you sure?"]


def test_tool_results_and_notifications_are_not_human_turns(tmp_path):
    proj = _transcript(tmp_path, "s1", [
        json.dumps({"type": "user", "userType": "external", "isSidechain": False,
                    "message": {"role": "user",
                                "content": [{"type": "tool_result", "content": "ok"}]}}),
        _turn("<task-notification>\n<task-id>x</task-id>"),
        _turn("<system-reminder>be good</system-reminder>"),
        _turn("build it")])
    assert list(adt_xexam.human_turns(os.path.join(proj, "s1.jsonl"))) == ["build it"]


def test_sidechain_turns_are_excluded(tmp_path):
    proj = _transcript(tmp_path, "s1", [
        _turn("are you sure?", isSidechain=True), _turn("merge it")])
    assert list(adt_xexam.human_turns(os.path.join(proj, "s1.jsonl"))) == ["merge it"]


def test_a_missing_transcript_yields_nothing_rather_than_raising(tmp_path):
    assert list(adt_xexam.human_turns(str(tmp_path / "nope.jsonl"))) == []


# ── challenge detection ───────────────────────────────────────────────────
def test_challenge_shapes_are_detected():
    for text in ["are you sure?", "did you actually run it",
                 "check if the sync really stopped",
                 "you are making that up", "that's wrong",
                 "why did you skip the test", "is that real?",
                 "119 is still showing in plan stage",
                 "explain the stale copy failure"]:
        assert adt_xexam.is_challenge(text), text


def test_plain_instructions_are_not_challenges():
    for text in ["build it", "merge", "yes, re-cut it", "plan it",
                 "what's the next step?", "call it adt-plan-quality-reviewer."]:
        assert not adt_xexam.is_challenge(text), text


# ── the join ──────────────────────────────────────────────────────────────
def test_ledger_join_skips_unassigned_pools(tmp_path):
    ledger = _ledger(tmp_path, [("__unassigned__", "s1"), ("ADT-1", "s2")])
    assert sorted(adt_xexam.ledger_sessions(ledger)) == ["ADT-1"]


def test_ledger_join_collects_every_session_for_a_ticket(tmp_path):
    ledger = _ledger(tmp_path, [("ADT-1", "s1"), ("ADT-1", "s2"), ("ADT-1", "s1")])
    assert adt_xexam.ledger_sessions(ledger)["ADT-1"] == {"s1", "s2"}

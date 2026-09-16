# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that `adt_dod.py --gate` gives the same verdict whatever a ticket's
`track:` is, including when it has none."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_dod  # noqa: E402

# A calibration record that does not exist turns the plan-quality gate off.
_NO_PLAN_GATE = ["--plan-calibration", "/nonexistent/plan-quality-calibration.md"]

COVERED_REVIEW = "\n### DoD-coverage review\n**Verdict:** COVERED\n**Gaps:** none\n"

CHECKABLE_DOD = (
    "done_evidence:\n"
    "  - must_run: 'true'\n"
    "    lane: build\n"
)


def _ticket(tmp_path, *, track, evidence, review=COVERED_REVIEW, name="ticket.md"):
    """A ticket with an explicit `track:`."""
    md = tmp_path / name
    md.write_text(
        "---\nid: T-1\ntrack: %s\n%s---\n\n# body\n%s" % (track, evidence, review))
    return str(md)


def _gate(md, capsys):
    rc = adt_dod._cli([md, "--gate"] + _NO_PLAN_GATE)
    return rc, capsys.readouterr().out


def test_fast_ticket_with_complete_artifacts_is_approvable(tmp_path, capsys):
    md = _ticket(tmp_path, track="fast", evidence=CHECKABLE_DOD)
    rc, out = _gate(md, capsys)
    assert rc == 0
    assert out.startswith("APPROVABLE")


def test_fast_ticket_with_empty_dod_is_refused(tmp_path, capsys):
    md = _ticket(tmp_path, track="fast", evidence="")
    rc, out = _gate(md, capsys)
    assert rc == 1
    assert out.startswith("REFUSE")
    assert "no done_evidence" in out


def test_fast_ticket_with_prose_condition_is_refused(tmp_path, capsys):
    md = _ticket(tmp_path, track="fast",
                 evidence="done_evidence:\n  - must_run:\n    lane: build\n")
    rc, out = _gate(md, capsys)
    assert rc == 1
    assert out.startswith("REFUSE")


def test_gate_verdict_does_not_depend_on_track(tmp_path, capsys):
    """The same ticket gets the same verdict on every tier, approving or refusing."""
    verdicts = {}
    for tier in ("fast", "standard", "full", "unrecognised-tier"):
        md = _ticket(tmp_path, track=tier, evidence=CHECKABLE_DOD,
                     name="ticket-%s.md" % tier)
        verdicts[tier] = _gate(md, capsys)
    assert len(set(verdicts.values())) == 1, verdicts

    refusals = {}
    for tier in ("fast", "standard", "full"):
        md = _ticket(tmp_path, track=tier, evidence="",
                     name="empty-%s.md" % tier)
        refusals[tier] = _gate(md, capsys)
    assert len(set(refusals.values())) == 1, refusals


def test_ticket_without_a_track_field_gets_the_same_verdict(tmp_path, capsys):
    with_tier = _ticket(tmp_path, track="fast", evidence=CHECKABLE_DOD,
                        name="with.md")
    without = tmp_path / "without.md"
    without.write_text(
        "---\nid: T-1\n%s---\n\n# body\n%s" % (CHECKABLE_DOD, COVERED_REVIEW))
    assert _gate(with_tier, capsys) == _gate(str(without), capsys)

# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that `adt_dod --gate` requires a DoD-coverage review with a COVERED
verdict, on top of a checkable DoD. These test the gate logic, not the reviewer."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import adt_dod  # noqa: E402

# Point every --gate call at a missing calibration record so the plan-quality
# gate is off and only the DoD-coverage gate is checked.
_NO_PLAN_GATE = ["--plan-calibration", "/nonexistent/plan-quality-calibration.md"]


CLEAN_DOD = "done_evidence:\n  - must_run: 'true'\n"


def _ticket(tmp_path, review_body="", tix="T-1", evidence=CLEAN_DOD):
    md = tmp_path / "ticket.md"
    md.write_text("---\nid: %s\n%s---\n\n# body\n%s" % (tix, evidence, review_body))
    return str(md)


def _review(verdict):
    return "\n### DoD-coverage review\n**Verdict:** %s\n**Gaps:** none\n" % verdict


# ── the gate ──────────────────────────────────────────────────────────────
def test_checkable_dod_without_a_review_is_refused(tmp_path, capsys):
    rc = adt_dod._cli([_ticket(tmp_path), "--gate"] + _NO_PLAN_GATE)
    out = capsys.readouterr().out
    assert rc == 1
    assert out.startswith("REFUSE\t")
    assert "DoD-coverage review" in out


def test_covered_review_approves(tmp_path, capsys):
    rc = adt_dod._cli([_ticket(tmp_path, _review("COVERED")), "--gate"] + _NO_PLAN_GATE)
    assert rc == 0
    assert capsys.readouterr().out.startswith("APPROVABLE\t")


def test_gap_verdict_is_refused(tmp_path, capsys):
    rc = adt_dod._cli([_ticket(tmp_path, _review("GAP")), "--gate"] + _NO_PLAN_GATE)
    out = capsys.readouterr().out
    assert rc == 1
    assert "GAP" in out


def test_unknown_verdict_is_refused(tmp_path, capsys):
    rc = adt_dod._cli([_ticket(tmp_path, _review("UNKNOWN")), "--gate"] + _NO_PLAN_GATE)
    out = capsys.readouterr().out
    assert rc == 1
    assert "UNKNOWN" in out


def test_review_heading_without_a_verdict_is_refused(tmp_path, capsys):
    md = _ticket(tmp_path, "\n### DoD-coverage review\n(pending)\n")
    rc = adt_dod._cli([md, "--gate"] + _NO_PLAN_GATE)
    assert rc == 1
    assert capsys.readouterr().out.startswith("REFUSE\t")


def test_prose_dod_is_refused_before_coverage_is_checked(tmp_path, capsys):
    md = _ticket(tmp_path, _review("COVERED"),
                 evidence="done_evidence:\n  - prose: 'works well'\n")
    rc = adt_dod._cli([md, "--gate"] + _NO_PLAN_GATE)
    out = capsys.readouterr().out
    assert rc == 1
    assert "uncheckable" in out


def test_empty_dod_is_refused_before_coverage_is_checked(tmp_path, capsys):
    md = _ticket(tmp_path, _review("COVERED"), evidence="")
    rc = adt_dod._cli([md, "--gate"] + _NO_PLAN_GATE)
    assert rc == 1
    assert "no done_evidence" in capsys.readouterr().out


# ── the exemption list ────────────────────────────────────────────────────
def test_the_exemption_list_is_empty(tmp_path):
    assert adt_dod._COVERAGE_GRANDFATHERED == (), (
        "an exemption was added, so that ticket skips the coverage check. "
        "Update this test if that is intended.")


def test_an_exempt_ticket_would_skip_the_check(tmp_path, capsys):
    import adt_dod as m
    old = m._COVERAGE_GRANDFATHERED
    m._COVERAGE_GRANDFATHERED = ("ADT-999",)
    try:
        assert m._cli([_ticket(tmp_path, tix="ADT-999"), "--gate"] + _NO_PLAN_GATE) == 0
        assert capsys.readouterr().out.startswith("APPROVABLE\t")
    finally:
        m._COVERAGE_GRANDFATHERED = old


# ── the reader ────────────────────────────────────────────────────────────
def test_coverage_review_reports_missing_and_present(tmp_path):
    assert adt_dod.coverage_review(_ticket(tmp_path))[0] is None
    assert adt_dod.coverage_review(_ticket(tmp_path, _review("covered")))[0] == "COVERED"


def test_missing_file_does_not_raise(tmp_path):
    assert adt_dod.coverage_review(str(tmp_path / "nope.md")) == (None, False)

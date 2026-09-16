# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for adt_dod: quoted must_run values, depends_on ordering between
conditions, and the refusal of caveats that no condition or waiver covers."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import adt_dod  # noqa: E402

# Point every --gate call at a missing calibration record so the plan-quality
# gate is off and only the DoD-coverage gate is checked.
_NO_PLAN_GATE = ["--plan-calibration", "/nonexistent/plan-quality-calibration.md"]


REVIEW = "\n### DoD-coverage review\n**Verdict:** COVERED\n**Gaps:** none\n"


def _t(tmp_path, evidence="", body="", tix="T-1"):
    md = tmp_path / "ticket.md"
    md.write_text("---\nid: %s\n%s---\n\n# body\n%s%s" % (tix, evidence, body, REVIEW))
    return str(md)


# ── Parsing must_run values ───────────────────────────────────────────────
def test_a_command_ending_in_a_quote_survives_parsing():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.md")
        with open(p, "w") as fh:
            fh.write("---\nid: T-1\ndone_evidence:\n"
                     "  - must_run: 'grep -qE \"^caught: [0-9]+\"'\n"
                     "    lane: build\n---\n\n# body\n")
        got = adt_dod.parse_done_evidence(p)[0]["must_run"]
        assert got == 'grep -qE "^caught: [0-9]+"', got
        assert got.count('"') % 2 == 0, "unbalanced quotes: %r" % got


def test_ordinary_quoted_values_still_unquote():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.md")
        with open(p, "w") as fh:
            fh.write("---\nid: T-1\ndone_evidence:\n"
                     "  - must_run: 'true'\n  - must_run: \"false\"\n---\n\n# body\n")
        evs = adt_dod.parse_done_evidence(p)
        assert [e["must_run"] for e in evs] == ["true", "false"]


# ── depends_on: a condition waits for its upstream ────────────────────────
def _dod(*rows):
    return "done_evidence:\n" + "".join(rows)


def _row(cmd, cid=None, dep=None):
    r = "  - must_run: '%s'\n" % cmd
    if cid:
        r += "    id: %s\n" % cid
    if dep:
        r += "    depends_on: %s\n" % dep
    return r + "    lane: build\n"


def test_downstream_is_not_graded_while_upstream_is_red(tmp_path):
    md = _t(tmp_path, _dod(_row("false", cid="a"), _row("true", cid="b", dep="a")))
    res = adt_dod.check(md, str(tmp_path), cwd=str(tmp_path))
    a, b = res["conditions"]
    assert a["passed"] is False
    assert b["passed"] is None, "b would run green, but its upstream is red"
    assert "upstream" in b["why"]
    assert res["all_green"] is False


def test_downstream_grades_normally_once_upstream_is_met(tmp_path):
    md = _t(tmp_path, _dod(_row("true", cid="a"), _row("true", cid="b", dep="a")))
    res = adt_dod.check(md, str(tmp_path), cwd=str(tmp_path))
    assert [c["passed"] for c in res["conditions"]] == [True, True]
    assert res["all_green"] is True


def test_conditions_without_depends_on_grade_independently(tmp_path):
    md = _t(tmp_path, _dod(_row("true"), _row("true")))
    res = adt_dod.check(md, str(tmp_path), cwd=str(tmp_path))
    assert res["all_green"] is True


def test_an_unmet_upstream_reports_cant_verify_rather_than_failure(tmp_path):
    md = _t(tmp_path, _dod(_row("false", cid="a"), _row("true", cid="b", dep="a")))
    res = adt_dod.check(md, str(tmp_path), cwd=str(tmp_path))
    assert res["infra"] is True
    assert res["conditions"][1]["passed"] is not False


# ── --gate refuses orderings that can never be satisfied ──────────────────
def test_dangling_depends_on_is_refused(tmp_path, capsys):
    md = _t(tmp_path, _dod(_row("true", cid="a", dep="nope")))
    assert adt_dod._cli([md, "--gate"] + _NO_PLAN_GATE) == 1
    out = capsys.readouterr().out
    assert "depends_on" in out and "nope" in out


def test_a_cycle_is_refused(tmp_path, capsys):
    md = _t(tmp_path, _dod(_row("true", cid="a", dep="b"), _row("true", cid="b", dep="a")))
    assert adt_dod._cli([md, "--gate"] + _NO_PLAN_GATE) == 1
    assert "cycle" in capsys.readouterr().out


def test_a_valid_chain_is_approvable(tmp_path, capsys):
    md = _t(tmp_path, _dod(_row("true", cid="a"), _row("true", cid="b", dep="a")))
    assert adt_dod._cli([md, "--gate"] + _NO_PLAN_GATE) == 0
    assert capsys.readouterr().out.startswith("APPROVABLE\t")


# ── Caveats must point at a condition or be explicitly waived ─────────────
CLEAN = _dod(_row("true"))


def test_an_unattached_caveat_is_refused(tmp_path, capsys):
    body = "### Risks\n- The renderer assumes the watcher is running.\n"
    md = _t(tmp_path, CLEAN, body)
    assert adt_dod._cli([md, "--gate"] + _NO_PLAN_GATE) == 1
    out = capsys.readouterr().out
    assert "unattached caveat" in out
    assert "assumes" in out, "the refusal should quote the caveat text"


def test_a_caveat_attached_to_a_condition_passes(tmp_path, capsys):
    body = ("### Risks\n- The renderer assumes the watcher is running. "
            "-> DoD:watchdog\n")
    md = _t(tmp_path, CLEAN, body)
    assert adt_dod._cli([md, "--gate"] + _NO_PLAN_GATE) == 0


def test_an_explicitly_waived_caveat_passes(tmp_path, capsys):
    body = ("### Test plan\n- Verified manually against the live host. "
            "-> WAIVED: no hermetic way to drive the harness.\n")
    md = _t(tmp_path, CLEAN, body)
    assert adt_dod._cli([md, "--gate"] + _NO_PLAN_GATE) == 0


def test_a_line_that_mentions_the_word_caveat_is_not_a_caveat(tmp_path):
    body = ("### Test plan\n- `test_edges.py` — caveat refusal, cycle detection, "
            "and the quote-strip regression.\n")
    assert adt_dod.unattached_caveats(_t(tmp_path, CLEAN, body)) == []


def test_a_declarative_caveat_line_is_caught(tmp_path):
    body = "### Design\n- **Caveat:** the index is rebuilt on every boot.\n"
    assert len(adt_dod.unattached_caveats(_t(tmp_path, CLEAN, body))) == 1


def test_sections_end_at_the_next_level_two_heading(tmp_path):
    body = ("### Design\n- nothing notable.\n\n"
            "## Build log (Dev)\n- the fix assumes the marker is present.\n")
    assert adt_dod.unattached_caveats(_t(tmp_path, CLEAN, body)) == []


def test_caveats_outside_the_scanned_sections_are_ignored(tmp_path):
    """Only sections that make promises about the work are scanned; Problem &
    goal is not one of them."""
    body = "### Problem & goal\n- The current code assumes a single machine.\n"
    assert adt_dod.unattached_caveats(_t(tmp_path, CLEAN, body)) == []

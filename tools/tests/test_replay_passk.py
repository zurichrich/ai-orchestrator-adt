# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for replay/passk.py: a ticket passes only if all k runs pass, and
strip_dod removes both the done_evidence frontmatter and the DoD section.

Also covers the eval set, prepare/score, the two graders, stratified reporting
and the leak check on stripped specs.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "replay"))
import passk  # noqa: E402

TICKET = """---
id: T-1
stage: done
done_evidence:
  - must_run: 'python3 -m pytest tools/tests/test_thing.py -q'
    lane: build
  - file: kanban.html
    must_contain_regex: 'T-1'
    lane: done
---

# t
## Plan (PM)
### Design
The mechanism, described.
### Definition of Done (machine-checkable)
```yaml
done_evidence:
  - must_run: 'true'
```
### Sub-steps
- 1a — do the thing
"""


# ── the metric's identity ─────────────────────────────────────────────────
def test_all_of_k_not_any_of_k():
    """A mix of passing and failing runs fails the ticket."""
    assert passk.aggregate_ticket(["COVERED", "GAP"], 2) is False
    assert passk.aggregate_ticket(["GAP", "COVERED"], 2) is False
    assert passk.aggregate_ticket(["COVERED", "COVERED"], 2) is True


def test_all_of_k_one_bad_run_in_five_fails_the_ticket():
    assert passk.aggregate_ticket(["COVERED"] * 4 + ["UNKNOWN"], 5) is False


def test_all_of_k_short_run_is_not_a_pass():
    assert passk.aggregate_ticket(["COVERED"], 3) is False
    assert passk.aggregate_ticket([], 3) is False


def test_all_of_k_unknown_is_not_covered():
    assert passk.aggregate_ticket(["UNKNOWN", "UNKNOWN"], 2) is False


# ── the strip ─────────────────────────────────────────────────────────────
def test_strip_removes_the_frontmatter_evidence():
    out = passk.strip_dod(TICKET)
    assert "done_evidence:" not in out.split("---")[1]
    assert "test_thing.py" not in out
    assert "must_contain_regex" not in out


def test_strip_removes_the_dod_section():
    out = passk.strip_dod(TICKET)
    assert "### Definition of Done" not in out


def test_strip_keeps_the_spec_the_derivation_needs():
    out = passk.strip_dod(TICKET)
    assert "### Design" in out
    assert "The mechanism, described." in out
    assert "### Sub-steps" in out
    assert "1a — do the thing" in out


# ── the eval set ──────────────────────────────────────────────────────────
def _write(tmp_path, rel, body):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_eval_set_takes_closed_tickets_with_both_a_dod_and_a_design(tmp_path):
    _write(tmp_path, "enhancements/done/good.md", TICKET)
    _write(tmp_path, "enhancements/done/no-dod.md", "---\nid: T-2\n---\n### Design\nx\n")
    _write(tmp_path, "enhancements/done/no-design.md",
           "---\nid: T-3\ndone_evidence:\n  - must_run: 'true'\n---\n# t\n")
    _write(tmp_path, "enhancements/planned/not-closed.md", TICKET)
    got = [os.path.basename(p) for p in passk.eval_set(str(tmp_path))]
    assert got == ["good.md"]


# ── prepare / score round-trip ────────────────────────────────────────────
def test_prepare_writes_k_stripped_specs_per_ticket(tmp_path):
    _write(tmp_path, "cache/enhancements/done/good.md", TICKET)
    runs = str(tmp_path / "runs")
    n = passk.prepare(runs, passk.eval_set(str(tmp_path / "cache")), 3)
    assert n == 3
    spec = open(os.path.join(runs, "good", "0.spec.md"), encoding="utf-8").read()
    assert "### Definition of Done" not in spec and "### Design" in spec


def test_score_marks_a_mixed_ticket_as_fail(tmp_path):
    _write(tmp_path, "cache/enhancements/done/good.md", TICKET)
    runs = tmp_path / "runs" / "good"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "0.verdict").write_text("**Verdict:** COVERED\n")
    (runs / "1.verdict").write_text("**Verdict:** GAP\n")
    rows, passed, attempted, incomplete = passk.score(
        str(tmp_path / "runs"), passk.eval_set(str(tmp_path / "cache")), 2)
    assert (passed, attempted, incomplete) == (0, 1, [])
    assert rows[0][2] == "fail"


def test_score_reports_an_incomplete_ticket_as_incomplete_not_zero(tmp_path):
    """Fewer than k runs, all passing, reads as incomplete."""
    _write(tmp_path, "cache/enhancements/done/good.md", TICKET)
    runs = tmp_path / "runs" / "good"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "0.verdict").write_text("**Verdict:** COVERED\n")
    (runs / "0.derived.md").write_text(CHECKABLE_DOD)
    rows, passed, attempted, incomplete = passk.score(
        str(tmp_path / "runs"), passk.eval_set(str(tmp_path / "cache")), 3)
    assert incomplete == ["good"]
    assert rows[0][2] == "incomplete"
    assert passed == 0


def test_render_states_the_corpus_limit(tmp_path):
    body = passk.render([], 0, 9, [], 3)
    assert "20-50" in body and "floor" in body


# ── the second grader ─────────────────────────────────────────────────────
# A run passes only if the coverage verdict is COVERED and the derived DoD is
# machine-checkable.
CHECKABLE_DOD = """---
id: D-1
done_evidence:
  - must_run: 'true'
    lane: build
---
# derived
"""

PROSE_DOD = """---
id: D-2
done_evidence:
  - note: 'the feature works well'
    lane: build
---
# derived
"""


def test_a_run_needs_both_graders():
    assert passk.run_passed("COVERED", True) is True
    assert passk.run_passed("COVERED", False) is False, "expected an uncheckable run to fail"
    assert passk.run_passed("GAP", True) is False


def test_checkable_rejects_a_prose_condition(tmp_path):
    good = tmp_path / "good.md"
    good.write_text(CHECKABLE_DOD)
    bad = tmp_path / "bad.md"
    bad.write_text(PROSE_DOD)
    assert passk.checkable(str(good)) is True
    assert passk.checkable(str(bad)) is False


def test_all_of_k_fails_when_a_covered_run_is_uncheckable():
    assert passk.aggregate_ticket(["COVERED", "COVERED"], 2, [True, False]) is False
    assert passk.aggregate_ticket(["COVERED", "COVERED"], 2, [True, True]) is True


def test_score_fails_a_run_whose_derived_dod_is_missing(tmp_path):
    _write(tmp_path, "cache/enhancements/done/good.md", TICKET)
    runs = tmp_path / "runs" / "good"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "0.verdict").write_text("**Verdict:** COVERED\n")
    rows, passed, attempted, _inc = passk.score(
        str(tmp_path / "runs"), passk.eval_set(str(tmp_path / "cache")), 1)
    assert passed == 0 and rows[0][2] == "fail"


# ── early stop ────────────────────────────────────────────────────────────
# The first failing run settles the ticket as `fail`, even with runs missing.
def test_a_failed_run_settles_the_ticket_even_with_runs_missing(tmp_path):
    _write(tmp_path, "cache/enhancements/done/good.md", TICKET)
    runs = tmp_path / "runs" / "good"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "0.verdict").write_text("**Verdict:** GAP\n")
    (runs / "0.derived.md").write_text(CHECKABLE_DOD)
    rows, passed, attempted, incomplete = passk.score(
        str(tmp_path / "runs"), passk.eval_set(str(tmp_path / "cache")), 3)
    assert rows[0][2] == "fail", "expected fail, not incomplete"
    assert incomplete == [], "expected no incomplete tickets"
    assert passed == 0


def test_ticket_status_distinguishes_the_three_outcomes():
    assert passk.ticket_status(["COVERED"], [True], 3) == "incomplete"
    assert passk.ticket_status(["COVERED", "GAP"], [True, True], 3) == "fail"
    assert passk.ticket_status(["COVERED"] * 3, [True] * 3, 3) == "pass"
    # an uncheckable run also settles it as fail
    assert passk.ticket_status(["COVERED"], [False], 3) == "fail"


# ── stratified reporting, uniform k ───────────────────────────────────────
def test_stratum_reads_track_then_size(tmp_path):
    a = tmp_path / "a.md"
    a.write_text("---\nid: T\ntrack: fast\nsize: L\n---\n")
    b = tmp_path / "b.md"
    b.write_text("---\nid: T\nsize: S\n---\n")
    c = tmp_path / "c.md"
    c.write_text("---\nid: T\n---\n")
    assert passk.stratum(str(a)) == "fast"
    assert passk.stratum(str(b)) == "S"
    assert passk.stratum(str(c)) == "unknown"


def test_render_splits_by_track_when_more_than_one_stratum():
    rows = [("a", ["COVERED"], "pass", "fast"), ("b", ["GAP"], "fail", "standard")]
    body = passk.render(rows, 1, 2, [], 1)
    assert "## By track" in body
    assert "| `fast` | 1/1 |" in body
    assert "| `standard` | 0/1 |" in body
    assert "k is UNIFORM across every row" in body


# ── isolation ─────────────────────────────────────────────────────────────
# prepare refuses to write a spec that still contains DoD structure, and writes
# an ISOLATION.md naming the paths a run must not read.
LEAKY_TICKET = TICKET.replace("### Sub-steps", """### DoD-coverage review
**Verdict:** COVERED
Backed by test_thing.py — 7 conditions.

### Sub-steps""")


def test_leak_check_finds_dod_residue():
    """Markers are line-anchored structure and report human-readable names."""
    assert passk.leak_check("### Definition of Done\nstuff") == [
        "### Definition of Done section"]
    assert "### Plan-critique note" in passk.leak_check("### Plan-critique\nconverged")
    assert "a must_run condition list" in passk.leak_check("  - must_run: 'true'")


def test_leak_check_passes_a_properly_stripped_spec():
    assert passk.leak_check(passk.strip_dod(TICKET)) == []
    assert passk.leak_check(passk.strip_dod(LEAKY_TICKET)) == []


def test_prepare_refuses_a_spec_that_still_leaks(tmp_path, monkeypatch):
    _write(tmp_path, "cache/enhancements/done/good.md", TICKET)
    monkeypatch.setattr(passk, "strip_dod", lambda text: text)  # a broken stripper
    try:
        passk.prepare(str(tmp_path / "runs"),
                      passk.eval_set(str(tmp_path / "cache")), 3)
    except ValueError as e:
        assert "DoD residue" in str(e)
    else:
        raise AssertionError("expected prepare to raise on a leaky spec")
    assert not (tmp_path / "runs" / "good" / "0.spec.md").exists()


def test_prepare_writes_the_isolation_contract(tmp_path):
    _write(tmp_path, "cache/enhancements/done/good.md", TICKET)
    runs = str(tmp_path / "runs")
    passk.prepare(runs, passk.eval_set(str(tmp_path / "cache")), 1,
                  cache_dir=str(tmp_path / "cache"), devteam=str(tmp_path / "dt"))
    doc = (tmp_path / "runs" / "ISOLATION.md").read_text()
    assert str(tmp_path / "cache") in doc, "expected the cache listed as excluded"
    assert "tickets" in doc, "expected the ticket pages listed as excluded"
    assert "Write once" in doc and "Self-report" in doc


def test_excluded_paths_include_the_cache_and_ticket_pages(tmp_path):
    got = passk.excluded_paths(str(tmp_path / "cache"), str(tmp_path / "dt"))
    assert any("cache" in p for p in got)
    assert any(p.endswith("tickets") for p in got)


def test_leak_check_ignores_prose_mentions_of_a_dod():
    """Inline mentions of DoD terms in prose are not flagged; only line-anchored structure is."""
    prose = (
        "### 1. The DoD has no independent counter-check\n\n"
        "`done_evidence:` is authored by the same agent that will build it.\n"
        "- C1a — the reviewer outputs a `### DoD-coverage review` block.\n"
        "The plan mentions must_run: and must_contain_regex: in passing.\n")
    assert passk.leak_check(prose) == []


def test_leak_check_still_catches_real_structure():
    assert "done_evidence: block" in passk.leak_check("done_evidence:\n  - must_run: 'x'\n")
    assert "a must_run condition list" in passk.leak_check("  - must_run: 'true'\n")
    assert "### Plan-critique note" in passk.leak_check("### Plan-critique\nconverged in 2\n")

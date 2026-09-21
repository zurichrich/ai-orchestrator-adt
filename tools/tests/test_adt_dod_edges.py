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


# ── AO-013 gap 1: borrows: declarations are graded against the source ──────
# The fixture is a real module written to a real temp repo, because the whole
# mechanism is "what does `ast` say the function is" — a mocked parse would test
# the assertion and not the thing it asserts.
_MODULE = '''\
def _backoff_due(now, quiet):
    """Two exits, and the first one is a sentinel."""
    if quiet < 3:
        return 0.0
    step = 60 * (quiet - 2)
    return now + min(step, 300)


def _save_state(cfg, quiet):
    cfg["quiet"] = quiet
'''


def _repo(tmp_path, design, module=_MODULE, lane="planned"):
    """A git repo holding the module, plus a ticket in `lane/` citing it."""
    import subprocess
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / "watch_fixture.py").write_text(module)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    cache = tmp_path / "cache" / lane
    cache.mkdir(parents=True)
    md = cache / "ticket.md"
    md.write_text("---\nid: B-1\ntrack: standard\ndone_evidence:\n"
                  "  - must_run: 'true'\n    lane: build\n---\n\n# body\n"
                  "### Design\n%s\n\n### Risks\n- none.\n%s" % (design, REVIEW))
    return str(md), str(repo)


def test_a_borrow_stopping_short_of_the_function_end_is_a_defect(tmp_path):
    """The AO-006 round-3 shape: the span ends before the sentinel return."""
    md, repo = _repo(tmp_path, "- borrows: `tools/watch_fixture.py:1-4` "
                               "`_backoff_due` — exits 4 — a deadline.")
    out = adt_dod.borrow_defects(md, root=repo)
    assert any("is 1-6, not 1-4" in d and "2 lines short" in d for d in out), out
    assert any("returns at 4, 6" in d and "declared 4" in d for d in out), out


def test_a_borrow_read_to_the_end_is_clean(tmp_path):
    md, repo = _repo(tmp_path, "- borrows: `tools/watch_fixture.py:1-6` "
                               "`_backoff_due` — exits 4, 6 — 0.0 at 4 is a "
                               "sentinel, not a time.")
    assert adt_dod.borrow_defects(md, root=repo) == []


def test_a_function_with_no_returns_declares_none(tmp_path):
    md, repo = _repo(tmp_path, "- borrows: `tools/watch_fixture.py:9-10` "
                               "`_save_state` — exits none — it writes, and "
                               "returns nothing.")
    assert adt_dod.borrow_defects(md, root=repo) == []


def test_a_design_citing_code_inside_a_function_with_no_borrow_is_a_defect(tmp_path):
    md, repo = _repo(tmp_path, "- the deadline comes from "
                               "`tools/watch_fixture.py:5`, which is a time.")
    out = adt_dod.borrow_defects(md, root=repo)
    assert len(out) == 1 and "declares no `borrows:` line" in out[0], out


def test_a_citation_outside_any_function_needs_no_borrow(tmp_path):
    """Line 7 is blank, between the two defs: nothing is being borrowed."""
    md, repo = _repo(tmp_path, "- the module at `tools/watch_fixture.py:7`.")
    assert adt_dod.borrow_defects(md, root=repo) == []


def test_borrows_none_discharges_the_no_declaration_defect(tmp_path):
    md, repo = _repo(tmp_path, "- the deadline comes from "
                               "`tools/watch_fixture.py:5`.\n"
                               "- borrows: none — the citation names the line "
                               "this diff edits, not a value it depends on.")
    assert adt_dod.borrow_defects(md, root=repo) == []


def test_a_borrow_naming_no_such_function_is_a_defect(tmp_path):
    md, repo = _repo(tmp_path, "- borrows: `tools/watch_fixture.py:1-6` "
                               "`_no_such_helper` — exits 4, 6 — a deadline.")
    out = adt_dod.borrow_defects(md, root=repo)
    assert len(out) == 1 and "no function matches" in out[0], out


def test_an_unresolvable_citation_fails_open(tmp_path):
    """A file the repo does not hold is not something a planning session can
    fix by editing the spec, so it reports nothing rather than blocking."""
    md, repo = _repo(tmp_path, "- borrows: `tools/absent.py:1-6` `_gone` — "
                               "exits 4 — a deadline.")
    assert adt_dod.borrow_defects(md, root=repo) == []


def test_a_borrow_defect_refuses_in_planned_and_only_notes_in_building(tmp_path):
    """The lane scoping, both directions, through the CLI.

    `--gate` resolves a citation against the git toplevel of the directory it is
    invoked from, so the subprocess runs in the fixture repo — the same way a
    planning session runs it in the tree it is planning against.
    """
    import shutil
    import subprocess
    md, repo = _repo(tmp_path, "- borrows: `tools/watch_fixture.py:1-4` "
                               "`_backoff_due` — exits 4 — a deadline.",
                     lane="planned")
    dod = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(adt_dod.__file__))), "tools", "adt_dod.py")
    args = [sys.executable, dod, "--gate"] + _NO_PLAN_GATE

    r = subprocess.run(args[:2] + [md] + args[2:], cwd=repo,
                       capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "REFUSE" in r.stdout and "not traced to the end" in r.stdout, r.stdout

    # The same ticket one lane on: reported on stderr, and approvable. A borrow
    # span describes the code BEFORE the diff, so once the build lands its own
    # change the span is stale by design and a refusal here would be wrong.
    building = tmp_path / "cache" / "building"
    building.mkdir(parents=True)
    moved = str(building / "ticket.md")
    shutil.move(md, moved)
    r2 = subprocess.run(args[:2] + [moved] + args[2:], cwd=repo,
                        capture_output=True, text=True)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "APPROVABLE" in r2.stdout, r2.stdout
    assert "recorded and not enforced" in r2.stderr, r2.stderr

# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for tools/adt_dod.py, the Definition of Done grader: its condition
checkers, the approval gate, the verdict order, and dod_lost()."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_dod  # noqa: E402

# A calibration record that does not exist turns the plan-quality gate off, so
# --gate calls here grade only the DoD gate.
_NO_PLAN_GATE = ["--plan-calibration", "/nonexistent/plan-quality-calibration.md"]


def _ticket(tmp_path, evidence_block, review="", tix="T-1"):
    """review: an optional `### DoD-coverage review` block appended to the body."""
    md = tmp_path / "ticket.md"
    md.write_text("---\nid: %s\n%s---\n\n# body\n%s" % (tix, evidence_block, review))
    return str(md)


COVERED_REVIEW = "\n### DoD-coverage review\n**Verdict:** COVERED\n**Gaps:** none\n"


def _devteam(tmp_path, files):
    d = tmp_path / ".adt"
    d.mkdir(exist_ok=True)
    for name, content in files.items():
        (d / name).write_text(content)
    return str(d)


def test_regex_present_passes(tmp_path):
    md = _ticket(tmp_path,
                 "done_evidence:\n"
                 "  - file: .adt/kanban.html\n"
                 "    must_contain_regex: '<a [^>]*href=\"references\\.html\"'\n")
    dt = _devteam(tmp_path, {"kanban.html": '<a class="x" href="references.html">go</a>'})
    r = adt_dod.check(md, dt)
    assert r["all_green"] is True
    assert r["conditions"][0]["passed"] is True


def test_tooltip_substring_fails(tmp_path):
    # The href text inside a tooltip must not satisfy an anchor-shaped regex.
    md = _ticket(tmp_path,
                 "done_evidence:\n"
                 "  - file: .adt/kanban.html\n"
                 "    must_contain_regex: '<a [^>]*href=\"references\\.html\"'\n")
    dt = _devteam(tmp_path, {"kanban.html": '<span title="see references.html">x</span>'})
    r = adt_dod.check(md, dt)
    assert r["all_green"] is False
    assert r["conditions"][0]["passed"] is False


def test_missing_artifact_is_infra_not_fail(tmp_path):
    md = _ticket(tmp_path,
                 "done_evidence:\n"
                 "  - file: .adt/kanban.html\n"
                 "    must_contain_regex: 'anything'\n")
    dt = _devteam(tmp_path, {})  # kanban.html absent
    r = adt_dod.check(md, dt)
    assert r["conditions"][0]["passed"] is None  # can't-verify
    assert r["infra"] is True
    assert r["all_green"] is False  # infra blocks all-green (caller fails open)


def test_assertion_targets_devteam_not_cache(tmp_path):
    # The regex is checked against .adt/<basename>, even when `file:` names a
    # cache path.
    md = _ticket(tmp_path,
                 "done_evidence:\n"
                 "  - file: some/other/cache/kanban.html\n"
                 "    must_contain_regex: 'RENDERED'\n")
    dt = _devteam(tmp_path, {"kanban.html": "RENDERED here"})
    r = adt_dod.check(md, dt)
    assert r["conditions"][0]["target"] == ".adt/kanban.html"
    assert r["conditions"][0]["passed"] is True


def test_no_evidence_is_green(tmp_path):
    md = _ticket(tmp_path, "")  # no done_evidence block
    dt = _devteam(tmp_path, {})
    r = adt_dod.check(md, dt)
    assert r["conditions"] == []
    assert r["all_green"] is True


def test_must_run_exit0_passes(tmp_path):
    md = _ticket(tmp_path, "done_evidence:\n  - must_run: 'true'\n")
    dt = _devteam(tmp_path, {})
    r = adt_dod.check(md, dt, cwd=str(tmp_path))
    assert r["conditions"][0]["kind"] == "must_run"
    assert r["conditions"][0]["passed"] is True
    assert r["all_green"] is True


def test_must_run_nonzero_fails(tmp_path):
    md = _ticket(tmp_path, "done_evidence:\n  - must_run: 'exit 1'\n")
    dt = _devteam(tmp_path, {})
    r = adt_dod.check(md, dt, cwd=str(tmp_path))
    assert r["conditions"][0]["passed"] is False
    assert "exited 1" in r["conditions"][0]["why"]


def test_lane_slicing(tmp_path):
    md = _ticket(tmp_path,
                 "done_evidence:\n"
                 "  - must_run: 'true'\n"
                 "    lane: build\n"
                 "  - must_run: 'exit 1'\n"
                 "    lane: qa\n")
    dt = _devteam(tmp_path, {})
    build = adt_dod.check(md, dt, lane="build", cwd=str(tmp_path))
    assert len(build["conditions"]) == 1 and build["all_green"] is True
    qa = adt_dod.check(md, dt, lane="qa", cwd=str(tmp_path))
    assert len(qa["conditions"]) == 1 and qa["all_green"] is False


def test_was_red_at_real_red_to_green(tmp_path):
    # A real git repo: a command that fails at the first commit and passes at HEAD.
    import subprocess as sp
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    sp.run(["git", "init", "-q"], cwd=repo, check=True)
    sp.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    sp.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "marker").write_text("absent\n")
    sp.run(["git", "add", "-A"], cwd=repo, check=True)
    sp.run(["git", "commit", "-qm", "red"], cwd=repo, check=True)
    red_ref = sp.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                     text=True).stdout.strip()
    (repo / "marker").write_text("present\n")
    sp.run(["git", "add", "-A"], cwd=repo, check=True)
    sp.run(["git", "commit", "-qm", "green"], cwd=repo, check=True)
    dt = repo / ".adt"
    dt.mkdir(exist_ok=True)
    md = repo / "ticket.md"
    cmd = "grep -q present marker"  # fails at red_ref, passes at HEAD
    md.write_text("---\ndone_evidence:\n  - must_run: '%s'\n    was_red_at: %s\n---\n"
                  % (cmd, red_ref))
    r = adt_dod.check(str(md), str(dt), cwd=str(repo))
    c = r["conditions"][0]
    assert c["passed"] is True, c["why"]
    assert c["red_verified"] is True


def test_was_red_at_always_green_fails(tmp_path):
    # A command that also passed at the recorded ref fails the condition.
    import subprocess as sp
    repo = tmp_path / "repo2"
    repo.mkdir(exist_ok=True)
    sp.run(["git", "init", "-q"], cwd=repo, check=True)
    sp.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    sp.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f").write_text("x\n")
    sp.run(["git", "add", "-A"], cwd=repo, check=True)
    sp.run(["git", "commit", "-qm", "c1"], cwd=repo, check=True)
    ref = sp.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                 text=True).stdout.strip()
    dt = repo / ".adt"
    dt.mkdir(exist_ok=True)
    md = repo / "ticket.md"
    md.write_text("---\ndone_evidence:\n  - must_run: 'true'\n    was_red_at: %s\n---\n" % ref)
    r = adt_dod.check(str(md), str(dt), cwd=str(repo))
    c = r["conditions"][0]
    assert c["passed"] is False, c["why"]
    assert "never red" in c["why"] or "always-green" in c["why"]


def test_was_red_at_plan_sentinel_is_infra(tmp_path):
    # was_red_at: plan is not a ref that can be checked out, so the result is
    # can't-verify.
    md = _ticket(tmp_path, "done_evidence:\n  - must_run: 'true'\n    was_red_at: plan\n")
    dt = _devteam(tmp_path, {})
    r = adt_dod.check(md, dt, cwd=str(tmp_path))
    assert r["conditions"][0]["passed"] is None
    assert r["infra"] is True


def test_prose_entry_blocks_all_green(tmp_path):
    # A prose entry is reported as unsupported and keeps all_green False.
    md = _ticket(tmp_path,
                 "done_evidence:\n"
                 "  - prose: 'works well and feels fast'\n"
                 "    lane: build\n"
                 "  - must_run: 'true'\n"
                 "    lane: build\n")
    dt = _devteam(tmp_path, {})
    r = adt_dod.check(md, dt, lane="build", cwd=str(tmp_path))
    kinds = [c["kind"] for c in r["conditions"]]
    assert "unsupported" in kinds
    assert r["all_green"] is False


def test_unsupported_lists_prose(tmp_path):
    md = _ticket(tmp_path,
                 "done_evidence:\n"
                 "  - prose: 'vibes'\n"
                 "  - must_run: 'true'\n")
    bad = adt_dod.unsupported(md)
    assert len(bad) == 1 and "prose" in bad[0]
    assert adt_dod.is_all_checkable(md) is False


def test_is_all_checkable_true_for_clean_dod(tmp_path):
    md = _ticket(tmp_path,
                 "done_evidence:\n"
                 "  - must_run: 'true'\n"
                 "  - file: .adt/x.html\n"
                 "    must_contain_regex: 'y'\n")
    assert adt_dod.is_all_checkable(md) is True


def test_empty_dod_not_approvable(tmp_path):
    md = _ticket(tmp_path, "")
    assert adt_dod.is_all_checkable(md) is False


def test_gate_cli_refuses_prose(tmp_path, capsys):
    md = _ticket(tmp_path,
                 "done_evidence:\n"
                 "  - prose: 'works'\n"
                 "  - must_run: 'true'\n")
    rc = adt_dod._cli([md, "--gate"] + _NO_PLAN_GATE)
    assert rc == 1
    assert capsys.readouterr().out.startswith("REFUSE\t")


def test_gate_cli_approves_clean(tmp_path, capsys):
    # Approval also needs a recorded coverage review, so this ticket has one.
    md = _ticket(tmp_path, "done_evidence:\n  - must_run: 'true'\n",
                 review=COVERED_REVIEW)
    rc = adt_dod._cli([md, "--gate"] + _NO_PLAN_GATE)
    assert rc == 0
    assert capsys.readouterr().out.startswith("APPROVABLE\t")


def test_cli_verdict_lines(tmp_path, capsys):
    # The CLI prints DENY, WARN or OK as the first field.
    md = _ticket(tmp_path,
                 "done_evidence:\n"
                 "  - file: .adt/kanban.html\n"
                 "    must_contain_regex: 'PRESENT'\n")
    dt = _devteam(tmp_path, {"kanban.html": "nope"})
    rc = adt_dod._cli([md, "--devteam", dt])
    assert rc == 0
    assert capsys.readouterr().out.startswith("DENY\t")


# ── A can't-verify condition never hides a failing one ──────────────────────

def _mixed_dod(tmp_path):
    """A DoD whose first condition cannot be verified and whose second fails."""
    md = _ticket(tmp_path,
                 "done_evidence:\n"
                 "  - file: .adt/absent.html\n"
                 "    must_contain_regex: 'anything'\n"
                 "  - must_run: 'exit 3'\n")
    return md, _devteam(tmp_path, {"kanban.html": "<p>unrelated</p>"})


def test_warn_does_not_mask_deny(tmp_path, capsys):
    md, dt = _mixed_dod(tmp_path)
    # Confirm the fixture really has one can't-verify and one failing condition.
    conds = adt_dod.check(md, dt)["conditions"]
    assert [c["passed"] for c in conds] == [None, False]

    rc = adt_dod._cli([md, "--devteam", dt])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("DENY\t"), out
    assert "exit 3" in out, "expected the DENY to name the failing condition: %r" % out


def test_verdict_prefers_deny_regardless_of_order():
    # A failing condition gives DENY whichever position it is in.
    infra = {"passed": None, "why": "cannot verify (infra)"}
    failed = {"passed": False, "why": "`x` exited 1 (FAIL)"}
    for conditions in ([infra, failed], [failed, infra]):
        decision, reason = adt_dod.verdict({"conditions": conditions}, "adt_dod")
        assert decision == "DENY"
        assert "exited 1" in reason


def test_verdict_ok_and_warn_are_unchanged(tmp_path, capsys):
    # WARN when nothing gradable failed; OK when everything passed.
    md = _ticket(tmp_path,
                 "done_evidence:\n"
                 "  - file: .adt/absent.html\n"
                 "    must_contain_regex: 'anything'\n")
    dt = _devteam(tmp_path, {"kanban.html": "<p>x</p>"})
    assert adt_dod._cli([md, "--devteam", dt]) == 0
    assert capsys.readouterr().out.startswith("WARN\t")

    md2 = _ticket(tmp_path, "done_evidence:\n  - must_run: 'true'\n")
    assert adt_dod._cli([md2, "--devteam", dt]) == 0
    assert capsys.readouterr().out.startswith("OK\t")


# --------------------------------------------------------------------------
# dod_lost(): the body shows a DoD block that the frontmatter does not have.
# --------------------------------------------------------------------------
_BODY_BLOCK = (
    "\n# T\n\n### Definition of Done (machine-checkable)\n"
    "```yaml\ndone_evidence:\n  - must_run: 'true'\n    lane: done\n```\n"
)
_FM_BLOCK = "done_evidence:\n  - must_run: 'true'\n    lane: done\n"


def _md(tmp_path, frontmatter, body, name="t.md"):
    p = tmp_path / name
    p.write_text("---\nid: ADT-273\n%s---\n%s" % (frontmatter, body))
    return str(p)


def test_dod_lost_true_for_body_only_ticket(tmp_path):
    p = _md(tmp_path, "", _BODY_BLOCK)
    assert adt_dod.parse_done_evidence(p) == []
    assert adt_dod.dod_lost(p) is True


def test_dod_lost_false_when_frontmatter_carries_it(tmp_path):
    p = _md(tmp_path, _FM_BLOCK, _BODY_BLOCK)
    assert adt_dod.dod_lost(p) is False


def test_dod_lost_false_when_no_dod_anywhere(tmp_path):
    p = _md(tmp_path, "", "\n# T\n\nno DoD here\n")
    assert adt_dod.dod_lost(p) is False


def test_dod_lost_false_when_only_build_lane_conditions(tmp_path):
    """A ticket whose conditions are all build-lane has an empty done-lane slice."""
    fm = "done_evidence:\n  - must_run: 'true'\n    lane: build\n"
    p = _md(tmp_path, fm, _BODY_BLOCK)
    assert adt_dod.check(p, str(tmp_path), lane="done")["conditions"] == []
    assert adt_dod.dod_lost(p) is False


def test_dod_lost_fails_open_on_unreadable_file(tmp_path):
    assert adt_dod.dod_lost(str(tmp_path / "nope.md")) is False


# ── ADT-359: must_run runs in the tree the grader is invoked from ────────────

def _git(*args, cwd):
    import subprocess
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo_with_worktree(tmp_path):
    """A canonical checkout holding .adt/, and a linked worktree on a branch."""
    canon, wt = tmp_path / "canon", tmp_path / "wt"
    canon.mkdir()
    _git("init", "-q", cwd=canon)
    _git("config", "user.email", "t@t", cwd=canon)
    _git("config", "user.name", "t", cwd=canon)
    (canon / ".adt").mkdir()
    _git("commit", "-q", "--allow-empty", "-m", "i", cwd=canon)
    _git("worktree", "add", "-q", str(wt), "-b", "wt", cwd=canon)
    return canon, wt


def test_default_cwd_is_the_invoking_worktree(tmp_path, monkeypatch):
    canon, wt = _repo_with_worktree(tmp_path)
    (wt / "branch_only.txt").write_text("x")
    md = _ticket(tmp_path, "done_evidence:\n"
                           "  - must_run: test -f branch_only.txt\n")
    monkeypatch.chdir(wt)
    assert os.path.realpath(adt_dod._default_cwd(str(canon / ".adt"))) \
        == os.path.realpath(str(wt))
    r = adt_dod.check(md, str(canon / ".adt"))
    assert r["conditions"][0]["passed"] is True, r["conditions"][0]["why"]


def test_default_cwd_from_the_canonical_checkout_does_not_see_the_branch(tmp_path, monkeypatch):
    canon, wt = _repo_with_worktree(tmp_path)
    (wt / "branch_only.txt").write_text("x")
    md = _ticket(tmp_path, "done_evidence:\n"
                           "  - must_run: test -f branch_only.txt\n")
    monkeypatch.chdir(canon)
    assert adt_dod.check(md, str(canon / ".adt"))["conditions"][0]["passed"] is False


def test_default_cwd_outside_git_is_the_parent_of_devteam(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    dt = tmp_path / "proj" / ".adt"
    dt.mkdir(parents=True)
    assert adt_dod._default_cwd(str(dt)) == str(tmp_path / "proj")


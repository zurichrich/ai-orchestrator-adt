# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests `adt_dod.py --dry-run`: it prints every condition's exit code, flags
missing commands (EXIT-127) and pinned conditions that were never red
(ALREADY-GREEN), and runs conditions through check().

Most cases run against the fixture tools/tests/fixtures/dod-dryrun.md.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import adt_dod  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tools", "tests", "fixtures",
                       "dod-dryrun.md")


def _run_cli():
    """Run the CLI with --dry-run on the fixture, from the repo root."""
    return subprocess.run(
        [sys.executable, "tools/adt_dod.py", FIXTURE, "--dry-run",
         "--devteam", "tools"],
        cwd=REPO, capture_output=True, text=True)


def test_the_fixture_is_present_and_declares_three_conditions():
    assert os.path.exists(FIXTURE)
    evs = adt_dod.parse_done_evidence(FIXTURE)
    assert len(evs) == 3, evs


def test_dry_run_reports_every_condition_with_an_exit_code():
    r = _run_cli()
    assert r.returncode == 0, r.stderr
    assert "3 condition(s)" in r.stdout
    for c in ("C1", "C2", "C3"):
        assert c in r.stdout, r.stdout


def test_a_missing_command_is_flagged_exit_127():
    out = _run_cli().stdout
    line = [l for l in out.splitlines() if l.startswith("C2")][0]
    assert "127" in line and "EXIT-127" in line, line


def test_an_always_green_condition_is_flagged():
    """`test -f README.md` is true at its `was_red_at` pin and true now."""
    out = _run_cli().stdout
    line = [l for l in out.splitlines() if l.startswith("C3")][0]
    assert "ALREADY-GREEN" in line, line


def test_the_unreachable_condition_is_reported_red_and_not_mislabelled():
    """C1 can never go green, but it gets an exit code and no flag."""
    out = _run_cli().stdout
    line = [l for l in out.splitlines() if l.startswith("C1")][0]
    assert "EXIT-127" not in line and "ALREADY-GREEN" not in line, line
    assert " 1 " in line, line


def test_the_flag_count_matches_the_flagged_lines():
    out = _run_cli().stdout
    rows = sum(1 for l in out.splitlines()
               if l.startswith("C") and ("EXIT-127" in l or "ALREADY-GREEN" in l))
    assert "2 flagged as authoring defects" in out
    assert rows == 2, out


def test_an_unpinned_green_condition_is_not_flagged_as_already_green(tmp_path):
    md = tmp_path / "t.md"
    md.write_text("---\nid: G-1\ndone_evidence:\n"
                  "  - must_run: 'true'\n    lane: build\n---\n\n# body\n")
    r = subprocess.run(
        [sys.executable, "tools/adt_dod.py", str(md), "--dry-run",
         "--devteam", "tools"], cwd=REPO, capture_output=True, text=True)
    assert "ALREADY-GREEN" not in r.stdout, r.stdout
    assert "0 flagged" in r.stdout, r.stdout


def test_dry_run_uses_the_graders_own_execution_path(monkeypatch, capsys):
    """dry_run() gets its results from check(), stubbed here."""
    called = {}

    def fake_check(ticket, devteam, lane=None, cwd=None):
        called["ticket"] = ticket
        return {"conditions": [{"kind": "must_run", "target": "x", "lane": "build",
                                "rc": 0, "passed": True}],
                "all_green": True, "infra": False}

    monkeypatch.setattr(adt_dod, "check", fake_check)
    adt_dod.dry_run(FIXTURE, "tools")
    assert called["ticket"] == FIXTURE
    assert "C1" in capsys.readouterr().out


def test_dry_run_never_refuses(tmp_path):
    """A failing condition still gives exit code 0."""
    md = tmp_path / "red.md"
    md.write_text("---\nid: R-1\ndone_evidence:\n"
                  "  - must_run: 'false'\n    lane: build\n---\n\n# body\n")
    r = subprocess.run(
        [sys.executable, "tools/adt_dod.py", str(md), "--dry-run",
         "--devteam", "tools"], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ── --devteam and the serializer import ─────────────────────────────────────

def test_dry_run_requires_devteam_rather_than_defaulting_it():
    r = subprocess.run(
        [sys.executable, "tools/adt_dod.py", FIXTURE, "--dry-run"],
        cwd=REPO, capture_output=True, text=True)
    assert r.returncode != 0
    assert "--dry-run requires --devteam" in r.stderr, r.stderr


def test_dry_run_names_the_directory_conditions_ran_in():
    """must_run conditions run in devteam's parent and must_contain_regex
    targets resolve under devteam, so the report prints both."""
    out = _run_cli().stdout
    assert "must_run conditions run in:" in out, out
    assert "must_contain_regex targets resolve under:" in out, out
    assert REPO in out, out


def test_every_serializer_import_goes_through_one_guarded_helper():
    """adt_dod.py imports ticket_serializer in exactly one place, _serializer()."""
    src = open(os.path.join(REPO, "tools", "adt_dod.py"), encoding="utf-8").read()
    assert "def _serializer():" in src
    # Match both `import x` and `from x import y` forms.
    sites = re.findall(r"^\s*(?:import\s+ticket_serializer|from\s+ticket_serializer\s+import)",
                       src, re.M)
    assert len(sites) == 1, \
        "expected 1 import site for ticket_serializer, found %d" % len(sites)


def test_the_reader_degrades_and_the_writer_refuses(monkeypatch, tmp_path):
    """Without the serializer, reading returns an empty list and writing raises."""
    monkeypatch.setattr(adt_dod, "_serializer", lambda: None)
    assert adt_dod._read_frontmatter_list("/any.md", "comments") == []
    md = tmp_path / "t.md"
    md.write_text("---\nid: W-1\n---\n\n# body\n")
    try:
        adt_dod._write_frontmatter_list(str(md), "comments", [{"a": 1}])
    except RuntimeError as e:
        assert "not installed beside adt_dod.py" in str(e)
    else:
        raise AssertionError("expected the writer to raise RuntimeError")


# ── ADT-359: a green run that did no work ────────────────────────────────────

def _one_condition(tmp_path, cmd):
    md = tmp_path / "t.md"
    md.write_text("---\nid: W-1\ndone_evidence:\n"
                  "  - must_run: %s\n    lane: build\n---\n\n# body\n" % cmd)
    return str(md)


def test_no_work_output_fails_a_green_run(tmp_path):
    md = _one_condition(tmp_path, "echo no tests ran in 0.01s")
    cond = adt_dod.check(md, "tools", cwd=REPO)["conditions"][0]
    assert cond["rc"] == 0
    assert cond["no_work"] is True
    assert cond["passed"] is False, cond["why"]


def test_no_work_patterns_cover_each_runner(tmp_path):
    for out in ("===== no tests ran in 0.05s =====", "Ran 0 tests in 0.000s",
                "dangling-doc-refs: 0 passed, 0 failed"):
        assert adt_dod._no_work_line(out), out
    for out in ("5 passed in 0.10s", "Ran 3 tests in 0.1s",
                "10 passed, 0 failed", "the message says no tests ran"):
        assert not adt_dod._no_work_line(out), out


def test_no_work_does_not_touch_a_real_pass(tmp_path):
    md = _one_condition(tmp_path, "echo 5 passed in 0.01s")
    cond = adt_dod.check(md, "tools", cwd=REPO)["conditions"][0]
    assert cond["passed"] is True and not cond.get("no_work"), cond["why"]


def test_no_work_is_flagged_in_dry_run(tmp_path):
    md = _one_condition(tmp_path, "echo no tests ran in 0.01s")
    r = subprocess.run(
        [sys.executable, "tools/adt_dod.py", md, "--dry-run", "--devteam", "tools"],
        cwd=REPO, capture_output=True, text=True)
    line = [l for l in r.stdout.splitlines() if l.startswith("C1")][0]
    assert "NO-WORK" in line, r.stdout
    assert "1 flagged" in r.stdout, r.stdout


# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests the egress guard in conftest.py: the probe records network attempts,
egress_hits reads the capture file correctly, and this session is armed.
"""
from __future__ import annotations

import os
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROBE = os.path.join(_REPO, "tests", "egress_probe")
sys.path.insert(0, _REPO)

import conftest  # noqa: E402  — the module under test


# ── 1. the probe records a real attempt ──────────────────────────────────────

def test_the_probe_can_actually_see_an_egress_attempt(tmp_path):
    capture = tmp_path / "egress.tsv"
    env = dict(os.environ)
    env["PYTHONPATH"] = _PROBE + os.pathsep + env.get("PYTHONPATH", "")
    env["ADT_EGRESS_CAPTURE"] = str(capture)
    env.pop("DO_NOT_TRACK", None)

    subprocess.run(
        [sys.executable, "-c",
         "import urllib.request, urllib.error\n"
         "try:\n"
         "    urllib.request.urlopen('https://adt-telemetry.zurichrich.workers.dev')\n"
         "except urllib.error.URLError:\n"
         "    pass\n"],
        cwd=_REPO, env=env, capture_output=True, text=True)

    hits = conftest.egress_hits(str(capture))
    assert len(hits) == 1, (
        "the probe recorded %d attempts, expected 1" % len(hits))
    assert "adt-telemetry" in hits[0]


def test_the_probe_records_the_pid_so_a_hit_is_traceable(tmp_path):
    capture = tmp_path / "egress.tsv"
    capture.write_text("12345\thttps://adt-telemetry.zurichrich.workers.dev/x\n")
    pid, url = conftest.egress_hits(str(capture))[0].split("\t")
    assert pid.isdigit() and url.startswith("https://")


# ── 2. the capture file is read correctly ────────────────────────────────────

def test_a_non_empty_capture_is_read_as_hits(tmp_path):
    capture = tmp_path / "egress.tsv"
    capture.write_text("111\thttps://adt-telemetry.zurichrich.workers.dev/a\n"
                       "222\thttps://adt-telemetry.zurichrich.workers.dev/b\n")
    assert len(conftest.egress_hits(str(capture))) == 2


def test_a_clean_run_reads_as_no_hits(tmp_path):
    capture = tmp_path / "egress.tsv"
    capture.write_text("")
    assert conftest.egress_hits(str(capture)) == []


def test_blank_lines_are_not_counted_as_hits(tmp_path):
    capture = tmp_path / "egress.tsv"
    capture.write_text("\n\n  \n")
    assert conftest.egress_hits(str(capture)) == []


def test_a_missing_capture_is_not_an_error(tmp_path):
    assert conftest.egress_hits(str(tmp_path / "never-written.tsv")) == []


# ── 3. the guard is armed in this session ────────────────────────────────────

def test_this_session_is_armed():
    """pytest_configure set the capture path and put the probe on PYTHONPATH."""
    assert os.environ.get("ADT_EGRESS_CAPTURE"), "pytest_configure set no capture"
    assert _PROBE in os.environ.get("PYTHONPATH", "").split(os.pathsep), (
        "tests/egress_probe is not on PYTHONPATH, so no subprocess is watched")


def test_the_in_process_guard_is_installed():
    """The pytest process itself has urlopen patched, since sitecustomize only reaches subprocesses."""
    import urllib.request
    assert urllib.request.urlopen.__name__ == "guarded", (
        "expected urllib.request.urlopen to be patched by conftest")


# ── gh never reaches GitHub from a test ──────────────────────────────────────

def test_gh_subprocess_is_answered_as_a_failure():
    out = subprocess.run(["gh", "api", "user"], capture_output=True, text=True)
    assert out.returncode == 1 and out.stdout == "", (
        "a test's gh call should fail locally, not spend the account's REST pool")

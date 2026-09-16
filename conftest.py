# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Stop the test suite sending telemetry to the production collector.

Every test runs against a fresh temp root, so each run looks like a new install
that has never pinged. The once-a-day gate in `adt_phone_home` cannot suppress
that, so without this file each test run would POST to the live endpoint.

This file sits at the repo root, so pytest loads it for every python test in
`tools/tests/` and `tests/`. The shell tests export DO_NOT_TRACK themselves.

Two switches are set. DO_NOT_TRACK is the public opt-out that
`telemetry_enabled()` honours. Blanking the endpoint means a code path that
skipped that check still has nowhere to send to.

`pytest_configure` also arms the egress probe for the whole session. It puts
`tests/egress_probe` (a `sitecustomize`) on PYTHONPATH so every subprocess a
test spawns is covered, and it wraps `urlopen` in this process, which has
already started and so cannot load a sitecustomize. `pytest_sessionfinish`
then fails the run if the capture recorded any attempt.
"""
import os
import tempfile

import pytest

_REPO = os.path.dirname(os.path.abspath(__file__))
_PROBE = os.path.join(_REPO, "tests", "egress_probe")
_HOST = "adt-telemetry.zurichrich.workers.dev"


def egress_hits(capture_path):
    """Return the recorded attempts as lines; a missing capture file means none.

    Kept separate from the session hook so `tools/tests/test_egress_guard.py`
    can test it directly.
    """
    try:
        with open(capture_path, encoding="utf-8") as fh:
            return [ln for ln in fh.read().splitlines() if ln.strip()]
    except OSError:
        return []


@pytest.fixture(autouse=True)
def _no_telemetry_egress(monkeypatch):
    """Stop every test reaching the collector, whatever it imports."""
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    try:
        import adt_phone_home
    except ImportError:
        pass            # tests that never touch the client need nothing more
    else:
        monkeypatch.setattr(adt_phone_home, "TELEMETRY_ENDPOINT", "", raising=False)


def _refuse_github(args, input_text=None):
    raise RuntimeError("a test tried to call GitHub through adt_machines; "
                       "pass gh= to test the report (ADT-384)")


@pytest.fixture(autouse=True)
def _no_machine_report_writes(monkeypatch):
    """Stop every test writing a machine report to GitHub (ADT-384).

    Any test that drives `adt_watch.watch` reaches `adt_machines.report`, which
    creates the adt:install label, Issue and comment on whatever repo the test's
    config names. The report swallows the refusal, as it swallows any failure on
    a watch tick. Tests of the report pass their own `gh=` and are unaffected."""
    try:
        import adt_machines
    except ImportError:
        return
    monkeypatch.setattr(adt_machines, "_gh", _refuse_github, raising=False)


@pytest.fixture(autouse=True)
def _no_real_gh(monkeypatch):
    """Answer every `gh` subprocess as a failed call instead of reaching GitHub.

    A test that drives `adt_watch.watch` or `_render` without stubbing the rate
    and branch-protection probes ran the real `gh api -i user` and
    `gh api repos/o/r/branches/main/protection`, at about two REST calls a second
    during a suite run, against the account's shared hourly pool. A failed call
    is what an offline machine sees, and every caller already handles it. Only
    the real binary is refused: a test that puts its own stub `gh` on PATH still
    reaches its stub, and a test that stubs `subprocess.run` replaces this."""
    import shutil
    import subprocess
    real_run = subprocess.run
    real_gh = shutil.which("gh")
    if not real_gh:
        return
    real_gh = os.path.realpath(real_gh)

    def run(args, *a, **kw):
        argv = args if isinstance(args, (list, tuple)) else str(args).split()
        path = (kw.get("env") or os.environ).get("PATH")
        found = shutil.which(str(argv[0]), path=path) if argv else None
        if found and os.path.realpath(found) == real_gh:
            text = kw.get("text") or kw.get("universal_newlines") or kw.get("encoding")
            return subprocess.CompletedProcess(
                args, 1, stdout="" if text else b"",
                stderr="gh is disabled in tests" if text else b"gh is disabled in tests")
        return real_run(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", run)


def pytest_configure(config):
    """Set the opt-out before collection, and arm the egress probe for the session."""
    os.environ["DO_NOT_TRACK"] = "1"

    # Arm the probe for every subprocess a test spawns. It is prepended, and it
    # chains to any `sitecustomize` it displaces.
    capture = os.path.join(tempfile.mkdtemp(prefix="adt-egress-"), "egress.tsv")
    os.environ["ADT_EGRESS_CAPTURE"] = capture
    existing = os.environ.get("PYTHONPATH", "")
    if _PROBE not in existing.split(os.pathsep):
        os.environ["PYTHONPATH"] = (
            _PROBE + (os.pathsep + existing if existing else ""))
    config._adt_egress_capture = capture

    # This interpreter is already running, so no sitecustomize can reach it.
    import urllib.error
    import urllib.request
    original = urllib.request.urlopen

    def guarded(url, *args, **kwargs):
        target = getattr(url, "full_url", None) or str(url)
        if _HOST in target:
            with open(capture, "a", encoding="utf-8") as fh:
                fh.write("%s\t%s\n" % (os.getpid(), target))
            raise urllib.error.URLError("blocked by the ADT egress guard")
        return original(url, *args, **kwargs)

    urllib.request.urlopen = guarded


def pytest_sessionfinish(session, exitstatus):
    """Fail the run if anything in it reached the collector, listing each PID and URL."""
    capture = getattr(session.config, "_adt_egress_capture", None)
    if not capture:
        return
    hits = egress_hits(capture)
    if hits:
        session.config._adt_egress_failed = True
        if exitstatus == 0:
            session.exitstatus = 1
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line("")
            reporter.write_line(
                "EGRESS: the suite reached the collector %d time(s):" % len(hits),
                red=True)
            for h in hits:
                reporter.write_line("  " + h, red=True)

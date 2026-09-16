# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for the pass lock, which lets only one `adt watch` / `adt_sync` pass
run at a time.

Covers: two concurrent passes create one Issue; the loser exits 0 with a
notice; a killed holder frees the lock; a skipped tick retries next time; the
adt_sync CLI and the ordered migrator take the same lock; any failure to take
the lock runs unlocked; and the lock adds no API calls.

Uses a stub `gh` on PATH; no network, no real repo.
"""
from __future__ import annotations

import contextlib
import errno
import io
import os
import stat
import subprocess
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_sync  # noqa: E402
import adt_watch  # noqa: E402

TOOLS = os.path.join(os.path.dirname(__file__), "..")

TICKET = """---
slug: sample
title: Sample ticket
type: task
priority: P2
stage: ideas
state: open
issue_number:
---

# Sample ticket

Body.
"""

# A stub `gh`. `issue create` sleeps to widen the race window, then appends to
# a counter file so the number of creates can be read from disk. Every other
# subcommand returns empty output or an empty list.
GH_STUB = '''#!/usr/bin/env python3
import os, sys, time
args = sys.argv[1:]
calls = os.environ.get("GH_STUB_CALLS")
if calls:
    with open(calls, "a") as f:
        f.write(" ".join(args[:2]) + "\\n")
if args[:2] == ["issue", "create"]:
    time.sleep(float(os.environ.get("GH_STUB_CREATE_DELAY", "0.6")))
    counter = os.environ["GH_STUB_CREATES"]
    with open(counter, "a") as f:
        f.write("x\\n")
    n = len(open(counter).read().split())
    sys.stdout.write("https://github.com/o/r/issues/%d\\n" % (100 + n))
    sys.exit(0)
if args[0] == "api":
    # A single-issue GET returns an object; collection endpoints return a list.
    path = args[1] if len(args) > 1 else ""
    import re as _re
    if _re.search(r"/issues/\\d+$", path):
        sys.stdout.write('{"number": 7, "node_id": "NODE", "title": "Sample ticket",'
                         ' "body": "", "state": "open", "labels": [], "assignees": [],'
                         ' "comments": 0}\\n')
    else:
        sys.stdout.write("[]\\n")
    sys.exit(0)
sys.stdout.write("")
'''


def _mk_project(tmp_path):
    stage = "ideas"
    """A minimal project root + cache holding one NUMBERLESS ticket."""
    root = tmp_path / "proj"
    (root / ".adt").mkdir(parents=True, exist_ok=True)
    (root / ".adt" / "state").mkdir(parents=True, exist_ok=True)
    cache = tmp_path / "cache"
    (cache / "tasks" / stage).mkdir(parents=True, exist_ok=True)
    (root / ".adt" / "config.yaml").write_text(
        f"project: proj\nrepo: o/r\nid_prefix: ADT\ncache_dir: {cache}\n"
        "stages:\n  - name: ideas\n    label: stage:ideas\n"
    )
    ticket = cache / "tasks" / stage / "sample.md"
    ticket.write_text(TICKET)
    return root, cache, ticket


def _offline_tick(monkeypatch):
    """Stub the tick's rate-limit probe and phone-home so no network is used."""
    monkeypatch.setattr(adt_watch, "_rate_limit_snapshot",
                        lambda: [("GraphQL", 0, 5000), ("REST", 0, 5000)])
    monkeypatch.setattr(adt_watch.adt_phone_home, "run_once",
                        lambda *a, **k: None)


def _mk_gh_stub(tmp_path):
    """Put the stub on PATH; return (env, the create-counter path)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text(GH_STUB)
    gh.chmod(0o755)
    counter = tmp_path / "creates.txt"
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["GH_STUB_CREATES"] = str(counter)
    env["GH_STUB_CALLS"] = str(tmp_path / "calls.txt")
    # 0.6s keeps the race window wide enough to overlap on a loaded machine.
    env["GH_STUB_CREATE_DELAY"] = "0.6"
    return env, counter


# ── two concurrent passes create exactly one Issue ──────────────────────────

def test_concurrent_create_is_serialised(tmp_path):
    """Two real `adt_watch.py --once` processes over one numberless ticket:
    one creates the Issue and the other skips."""
    root, cache, ticket = _mk_project(tmp_path)
    env, counter = _mk_gh_stub(tmp_path)

    cmd = [sys.executable, os.path.join(TOOLS, "adt_watch.py"),
           "--root", str(root), "--once"]
    procs = [subprocess.Popen(cmd, env=env, cwd=str(root),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) for _ in range(2)]
    outs = [p.communicate() for p in procs]

    creates = len(counter.read_text().split()) if counter.exists() else 0
    assert creates == 1, f"expected exactly 1 `gh issue create`, got {creates}"

    # Both exit 0; a skipped tick is not an error.
    assert [p.returncode for p in procs] == [0, 0]

    combined = "\n".join(o + e for o, e in outs)
    assert "another pass is running" in combined, combined

    # The winner's Issue number is written back to the ticket.
    data = adt_sync.parse_md(ticket.read_text())
    assert data.get("issue_number") == 101


# ── the loser exits 0 with a notice ─────────────────────────────────────────

def test_loser_exits_zero_with_notice(tmp_path, capsys, monkeypatch):
    """Hold the lock in-process; the tick must skip without calling _sync."""
    root, cache, ticket = _mk_project(tmp_path)
    cfg = adt_sync.load_config(str(root))

    _offline_tick(monkeypatch)
    calls = []
    monkeypatch.setattr(adt_watch, "_sync", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(adt_watch, "_render", lambda *a, **k: None)

    with adt_sync.pass_lock(cfg) as owned:
        assert owned
        adt_watch.watch(str(root), once=True)     # must return, not raise

    assert calls == [], "the loser ran the sync"
    assert "another pass is running" in capsys.readouterr().err


# ── a killed holder does not block the next pass ────────────────────────────

def test_lock_is_released_when_the_holder_is_killed(tmp_path):
    root, cache, ticket = _mk_project(tmp_path)
    cfg = adt_sync.load_config(str(root))

    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {TOOLS!r})
            import adt_sync
            cfg = adt_sync.load_config({str(root)!r})
            with adt_sync.pass_lock(cfg) as owned:
                assert owned
                print("held", flush=True)
                time.sleep(60)
        """)], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"

    with adt_sync.pass_lock(cfg) as owned:
        assert not owned, "the lock was not actually held"

    holder.kill()
    holder.wait(timeout=10)      # reaped: its fds are closed, so the lock is free

    with adt_sync.pass_lock(cfg) as owned:
        assert owned, "the lock outlived the killed process"


# ── a skipped tick retries on the next pass ─────────────────────────────────

def test_skipped_tick_retries_next_pass(tmp_path, monkeypatch):
    """A tick that loses the race leaves watch state untouched, so the next
    tick still syncs the change."""
    root, cache, ticket = _mk_project(tmp_path)
    cfg = adt_sync.load_config(str(root))

    _offline_tick(monkeypatch)
    calls = []
    monkeypatch.setattr(adt_watch, "_sync",
                        lambda *a, **k: (calls.append(a), True)[1])
    monkeypatch.setattr(adt_watch, "_render", lambda *a, **k: None)

    with adt_sync.pass_lock(cfg) as owned:      # tick 1 loses the race
        assert owned
        adt_watch.watch(str(root), once=True)
    assert calls == []

    adt_watch.watch(str(root), once=True)       # tick 2 — lock is free
    assert len(calls) == 1, "the skipped tick dropped the change"


# ── the adt_sync CLI takes the same lock ────────────────────────────────────

def test_sync_cli_skips_when_locked(tmp_path, capsys, monkeypatch):
    root, cache, ticket = _mk_project(tmp_path)
    cfg = adt_sync.load_config(str(root))

    def boom(*a, **k):
        raise AssertionError("reconcile_all ran while the lock was held")

    monkeypatch.setattr(adt_sync, "reconcile_all", boom)

    with adt_sync.pass_lock(cfg) as owned:
        assert owned
        rc = adt_sync.main(["--root", str(root)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "another pass is running" in out
    # adt-install.sh ignores a zero exit, so the notice has to say nothing ran.
    assert "Nothing was pushed or adopted" in out, out


# ── the ordered migrator takes the lock and refuses when it is held ─────────

def test_migrator_refuses_when_locked(tmp_path):
    """Unlike the other entry points the migrator exits non-zero, because an
    ordered migration cannot silently skip."""
    root, cache, ticket = _mk_project(tmp_path)
    cfg = adt_sync.load_config(str(root))

    with adt_sync.pass_lock(cfg) as owned:
        assert owned
        proc = subprocess.run(
            [sys.executable, os.path.join(TOOLS, "adt_migrate_ordered.py"),
             "--root", str(root)],
            capture_output=True, text=True)

    assert proc.returncode != 0, proc.stdout
    assert "another sync pass is running" in proc.stderr, proc.stderr


# ── a lock that cannot be taken runs the pass unlocked ──────────────────────


def test_flock_unsupported_fails_open(tmp_path, capsys, monkeypatch):
    """EOPNOTSUPP (for example a network mount without flock) is not treated
    as contention."""
    root, cache, ticket = _mk_project(tmp_path)
    cfg = adt_sync.load_config(str(root))
    monkeypatch.setattr(adt_sync, "_LOCK_WARNED", False)

    class Unsupported:
        LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

        @staticmethod
        def flock(fd, op):
            raise OSError(errno.EOPNOTSUPP, "Operation not supported")

    monkeypatch.setattr(adt_sync, "fcntl", Unsupported)

    # Twice, to show a later tick also gets the lock.
    for _ in range(2):
        with adt_sync.pass_lock(cfg) as owned:
            assert owned, "an unsupported flock was read as contention"

    err = capsys.readouterr().err
    assert "running UNLOCKED" in err, err
    assert err.count("pass lock unavailable") == 1, "warned more than once"


def test_fcntl_absent_fails_open(tmp_path, capsys, monkeypatch):
    """With no fcntl (a non-POSIX host) the pass proceeds with a warning."""
    root, cache, ticket = _mk_project(tmp_path)
    cfg = adt_sync.load_config(str(root))
    monkeypatch.setattr(adt_sync, "_LOCK_WARNED", False)
    monkeypatch.setattr(adt_sync, "fcntl", None)

    with adt_sync.pass_lock(cfg) as owned:
        assert owned

    assert "fcntl not available" in capsys.readouterr().err


def test_unopenable_lock_file_fails_open(tmp_path, capsys, monkeypatch):
    """An unwritable cache dir does not raise out of the context manager."""
    root, cache, ticket = _mk_project(tmp_path)
    cfg = adt_sync.load_config(str(root))
    monkeypatch.setattr(adt_sync, "_LOCK_WARNED", False)
    os.chmod(cache, stat.S_IRUSR | stat.S_IXUSR)     # r-x: no new files
    try:
        with adt_sync.pass_lock(cfg) as owned:
            assert owned
    finally:
        os.chmod(cache, 0o700)

    assert "cannot open" in capsys.readouterr().err


# ── the lock adds no API calls ──────────────────────────────────────────────


def test_lock_adds_no_api_calls(tmp_path, monkeypatch):
    """Taking the lock runs no subprocess."""
    root, cache, ticket = _mk_project(tmp_path)
    cfg = adt_sync.load_config(str(root))

    def poisoned(*a, **k):
        raise AssertionError("pass_lock made a subprocess call")

    monkeypatch.setattr(adt_sync.subprocess, "run", poisoned)
    with adt_sync.pass_lock(cfg) as owned:
        assert owned


def _count_gh_calls(tmp_path, name, fail_open, monkeypatch):
    """Run one real in-process tick over a converged cache and return the
    number of gh calls. Only the phone-home is stubbed."""
    root, cache, ticket = _mk_project(tmp_path / name)
    ticket.write_text(ticket.read_text().replace(
        "issue_number:", "issue_number: 7"))
    env, counter = _mk_gh_stub(tmp_path / name)
    calls = tmp_path / name / "calls.txt"
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setenv("GH_STUB_CALLS", str(calls))
    monkeypatch.setenv("GH_STUB_CREATES", str(counter))
    monkeypatch.setattr(adt_watch.adt_phone_home, "run_once", lambda *a, **k: None)
    if fail_open:
        monkeypatch.setattr(adt_sync, "fcntl", None)     # no lock at all
    monkeypatch.setattr(adt_sync, "_LOCK_WARNED", True)  # silence the notice
    # Clear the per-process caches so both runs start cold.
    for cache_name in ("_KNOWN_LABELS", "_PROJECT_CTX", "_BOARD_INDEX"):
        getattr(adt_sync, cache_name).clear()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        adt_watch.watch(str(root), once=True)
    assert "[sync] error" not in err.getvalue(), (
        "the tick errored, so the count is not from a normal pass:\\n"
        + err.getvalue())
    return len(calls.read_text().splitlines()) if calls.exists() else 0


def test_uncontended_tick_makes_the_same_calls_as_an_unlocked_tick(tmp_path, monkeypatch):
    """Compared against the same tick with the lock disabled, not a fixed count."""
    (tmp_path / "locked").mkdir(exist_ok=True)
    (tmp_path / "unlocked").mkdir(exist_ok=True)
    with monkeypatch.context() as m:
        locked = _count_gh_calls(tmp_path, "locked", False, m)
    with monkeypatch.context() as m:
        unlocked = _count_gh_calls(tmp_path, "unlocked", True, m)
    # Both sides at 0 would pass the equality, so require at least one call.
    assert locked > 0, "the stub gh was never invoked"
    assert locked == unlocked, (
        f"the lock changed an uncontended tick's API cost: "
        f"{locked} calls with it, {unlocked} without")


def test_contended_tick_costs_no_api_calls(tmp_path, monkeypatch):
    """The rate-limit and branch-protection probes run inside the lock, so a
    tick that loses the race makes no API calls."""
    (tmp_path / "c").mkdir(exist_ok=True)
    root, cache, ticket = _mk_project(tmp_path / "c")
    ticket.write_text(ticket.read_text().replace("issue_number:", "issue_number: 7"))
    env, counter = _mk_gh_stub(tmp_path / "c")
    calls = tmp_path / "c" / "calls.txt"
    cfg = adt_sync.load_config(str(root))

    cmd = [sys.executable, os.path.join(TOOLS, "adt_watch.py"),
           "--root", str(root), "--once"]
    with adt_sync.pass_lock(cfg) as owned:
        assert owned
        proc = subprocess.run(cmd, env=env, cwd=str(root),
                              capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    assert "another pass is running" in (proc.stdout + proc.stderr)
    n = len(calls.read_text().splitlines()) if calls.exists() else 0
    assert n == 0, f"a contended tick spent {n} API call(s): {calls.read_text()}"

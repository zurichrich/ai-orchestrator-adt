# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests adt_watch's quiet-board backoff and the rate-limit circuit breaker.

Both are persisted between ticks, because `adt_watch.py --once` exits after
every launchd tick. A skipped tick must write nothing to stdout or stderr.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_watch  # noqa: E402


# --- the backoff ladder -----------------------------------------------------

def test_backoff_stays_eager_inside_the_grace_window():
    for quiet in range(adt_watch.QUIET_GRACE):
        assert adt_watch._backoff_due(quiet, 1000.0) == 0.0, (
            "a board that just went quiet should sync on every tick")


def test_backoff_doubles_then_caps():
    now = 1000.0
    seen = [adt_watch._backoff_due(q, now) - now
            for q in range(adt_watch.QUIET_GRACE, adt_watch.QUIET_GRACE + 8)]
    assert seen[0] == adt_watch.BACKOFF_BASE
    assert seen[1] == adt_watch.BACKOFF_BASE * 2
    for a, b in zip(seen, seen[1:]):
        assert b >= a, "the backoff should never shrink"
    assert max(seen) == adt_watch.BACKOFF_CAP, "the backoff should stop at the cap"


def test_backoff_survives_an_unbounded_quiet_counter():
    """A very large quiet count returns the cap instead of overflowing."""
    now = 1000.0
    for quiet in (1026, 1027, 1028, 10 ** 4, 10 ** 6):
        due = adt_watch._backoff_due(quiet, now)      # must not raise
        assert due == now + adt_watch.BACKOFF_CAP, (
            "past the cap every quiet count should give the same next-due time")


def test_activity_snaps_back_to_every_tick():
    # quiet resets to 0 on movement, and 0 is inside the grace window.
    assert adt_watch._backoff_due(0, 1000.0) == 0.0


# --- skipped ticks are silent (select with -k silent) -----------------------

def test_self_skip_is_silent(tmp_path, monkeypatch, capsys):
    """A not-yet-due tick writes ZERO bytes and makes no sync call."""
    calls = []
    # The stored fingerprint matches the cache, so nothing changed locally and the backoff holds.
    monkeypatch.setattr(adt_watch, "_cache_fingerprint", lambda c: "FP")
    monkeypatch.setattr(adt_watch, "_watch_state",
                        lambda cfg: {"quiet": 9, "next_due": 9e18, "fp": "FP"})
    monkeypatch.setattr(adt_watch, "_sync",
                        lambda *a, **k: calls.append("sync") or True)
    monkeypatch.setattr(adt_watch, "_render",
                        lambda *a, **k: calls.append("render"))
    monkeypatch.setattr(adt_watch, "_rotate_log", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch, "_rate_limit_snapshot",
                        lambda: calls.append("rate") or [])
    monkeypatch.setattr(adt_watch.adt_sync, "load_config", lambda cfg: {})
    monkeypatch.setattr(adt_watch.adt_sync, "cache_dir", lambda cfg: str(tmp_path))

    adt_watch.watch(str(tmp_path), once=True)

    out = capsys.readouterr()
    assert out.out == "" and out.err == "", (
        "a skipped tick should write nothing")
    assert calls == [], f"a skipped tick should make no calls, got {calls}"


def test_silent_skip_costs_no_rate_limit_read_either(tmp_path, monkeypatch, capsys):
    # A skipped tick does not run the rate-limit snapshot subprocess.
    seen = []
    monkeypatch.setattr(adt_watch, "_cache_fingerprint", lambda c: "FP")
    monkeypatch.setattr(adt_watch, "_watch_state",
                        lambda cfg: {"quiet": 5, "next_due": 9e18, "fp": "FP"})
    monkeypatch.setattr(adt_watch, "_rate_limit_snapshot",
                        lambda: seen.append(1) or [])
    monkeypatch.setattr(adt_watch, "_rotate_log", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch.adt_sync, "load_config", lambda cfg: {})
    monkeypatch.setattr(adt_watch.adt_sync, "cache_dir", lambda cfg: str(tmp_path))
    adt_watch.watch(str(tmp_path), once=True)
    assert seen == []
    assert capsys.readouterr().out == ""


# --- the circuit breaker ----------------------------------------------------

def test_floor_breach_detected_as_a_share_not_a_fixed_count():
    assert adt_watch._pool_floor_breached([("graphql", 4900, 5000)]) == "graphql"
    assert adt_watch._pool_floor_breached([("core", 100, 5000)]) == ""
    # Same 100 remaining, different pool size -> different verdict.
    assert adt_watch._pool_floor_breached([("small", 900, 1000)]) == "small"


def test_floor_is_tolerant_of_a_missing_or_broken_snapshot():
    assert adt_watch._pool_floor_breached([]) == ""
    assert adt_watch._pool_floor_breached(None) == ""
    assert adt_watch._pool_floor_breached([("x", None, None)]) == ""


def test_breached_pool_skips_the_whole_pass_and_says_so(tmp_path, monkeypatch, capsys):
    """A breach skips the sync, still renders the board, and logs to stderr."""
    calls = []
    monkeypatch.setattr(adt_watch, "_watch_state", lambda cfg: {})
    monkeypatch.setattr(adt_watch, "_rotate_log", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch, "_cache_fingerprint", lambda c: "fp")
    monkeypatch.setattr(adt_watch, "_rate_limit_snapshot",
                        lambda: [("graphql", 4950, 5000)])
    monkeypatch.setattr(adt_watch, "_sync",
                        lambda *a, **k: calls.append("sync") or True)
    monkeypatch.setattr(adt_watch, "_render", lambda *a, **k: calls.append("render"))
    monkeypatch.setattr(adt_watch.adt_sync, "load_config", lambda cfg: {})
    monkeypatch.setattr(adt_watch.adt_sync, "cache_dir", lambda cfg: str(tmp_path))

    adt_watch.watch(str(tmp_path), once=True)

    # The render makes no API call, so it still runs on a breach.
    assert "sync" not in calls, "the sync should not start on a breach"
    assert calls == ["render"], "the board should still be rendered on a breach"
    assert "below the" in capsys.readouterr().err


def test_healthy_pools_let_the_pass_run(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(adt_watch, "_watch_state", lambda cfg: {})
    monkeypatch.setattr(adt_watch, "_rotate_log", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch, "_cache_fingerprint", lambda c: "fp")
    monkeypatch.setattr(adt_watch, "_rate_limit_snapshot",
                        lambda: [("graphql", 10, 5000), ("core", 6, 5000)])
    monkeypatch.setattr(adt_watch, "_sync",
                        lambda *a, **k: calls.append("sync") or False)
    monkeypatch.setattr(adt_watch, "_render", lambda *a, **k: calls.append("render"))
    monkeypatch.setattr(adt_watch, "_save_watch_state", lambda *a: None)
    monkeypatch.setattr(adt_watch.adt_sync, "load_config", lambda cfg: {})
    monkeypatch.setattr(adt_watch.adt_sync, "cache_dir", lambda cfg: str(tmp_path))

    adt_watch.watch(str(tmp_path), once=True)
    assert calls == ["sync", "render"]


# --- a local edit overrides the backoff ------------------------------------

def test_local_change_overrides_the_backoff(tmp_path, monkeypatch):
    """A changed cache fingerprint syncs straight away, even while backed off."""
    calls = []
    monkeypatch.setattr(adt_watch, "_cache_fingerprint", lambda c: "NEW")
    monkeypatch.setattr(adt_watch, "_watch_state",
                        lambda cfg: {"quiet": 9, "next_due": 9e18, "fp": "OLD"})
    monkeypatch.setattr(adt_watch, "_rotate_log", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch, "_rate_limit_snapshot", lambda: [])
    monkeypatch.setattr(adt_watch, "_sync",
                        lambda *a, **k: calls.append("sync") or True)
    monkeypatch.setattr(adt_watch, "_render", lambda *a, **k: calls.append("render"))
    monkeypatch.setattr(adt_watch, "_save_watch_state", lambda *a: None)
    monkeypatch.setattr(adt_watch.adt_sync, "load_config", lambda root: {})
    monkeypatch.setattr(adt_watch.adt_sync, "cache_dir", lambda cfg: str(tmp_path))

    adt_watch.watch(str(tmp_path), once=True)
    assert calls == ["sync", "render"], "a local edit should sync without waiting"


def test_first_tick_with_no_persisted_fp_syncs(tmp_path, monkeypatch):
    # No stored fingerprint (fresh install, cleared state) counts as a local change.
    calls = []
    monkeypatch.setattr(adt_watch, "_cache_fingerprint", lambda c: "FP")
    monkeypatch.setattr(adt_watch, "_watch_state",
                        lambda cfg: {"quiet": 9, "next_due": 9e18})
    monkeypatch.setattr(adt_watch, "_rotate_log", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch, "_rate_limit_snapshot", lambda: [])
    monkeypatch.setattr(adt_watch, "_sync",
                        lambda *a, **k: calls.append("sync") or False)
    monkeypatch.setattr(adt_watch, "_render", lambda *a, **k: calls.append("render"))
    monkeypatch.setattr(adt_watch, "_save_watch_state", lambda *a: None)
    monkeypatch.setattr(adt_watch.adt_sync, "load_config", lambda root: {})
    monkeypatch.setattr(adt_watch.adt_sync, "cache_dir", lambda cfg: str(tmp_path))

    adt_watch.watch(str(tmp_path), once=True)
    assert calls == ["sync", "render"]


# --- the backoff expires ---------------------------------------------------

def test_idle_but_due_tick_polls_remote(tmp_path, monkeypatch):
    """A due tick syncs even when nothing changed locally, so remote changes are pulled."""
    calls = []
    monkeypatch.setattr(adt_watch, "_cache_fingerprint", lambda c: "FP")
    monkeypatch.setattr(adt_watch, "_watch_state",
                        lambda cfg: {"quiet": 9, "next_due": 1.0, "fp": "FP"})
    monkeypatch.setattr(adt_watch, "_rotate_log", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch, "_rate_limit_snapshot", lambda: [])
    monkeypatch.setattr(adt_watch, "_sync",
                        lambda *a, **k: calls.append("sync") or False)
    monkeypatch.setattr(adt_watch, "_render", lambda *a, **k: calls.append("render"))
    monkeypatch.setattr(adt_watch, "_save_watch_state", lambda *a: None)
    monkeypatch.setattr(adt_watch.adt_sync, "load_config", lambda root: {})
    monkeypatch.setattr(adt_watch.adt_sync, "cache_dir", lambda cfg: str(tmp_path))

    adt_watch.watch(str(tmp_path), once=True)
    assert calls == ["sync", "render"], (
        "an expired backoff should sync")


def test_idle_but_due_tick_is_still_silent(tmp_path, monkeypatch, capsys):
    """A due tick whose sync changes nothing writes nothing."""
    monkeypatch.setattr(adt_watch, "_cache_fingerprint", lambda c: "FP")
    monkeypatch.setattr(adt_watch, "_watch_state",
                        lambda cfg: {"quiet": 9, "next_due": 1.0, "fp": "FP"})
    monkeypatch.setattr(adt_watch, "_rotate_log", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch, "_rate_limit_snapshot", lambda: [])
    monkeypatch.setattr(adt_watch, "_sync", lambda *a, **k: False)   # all-noop
    monkeypatch.setattr(adt_watch, "_render", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch, "_save_watch_state", lambda *a: None)
    monkeypatch.setattr(adt_watch.adt_sync, "load_config", lambda root: {})
    monkeypatch.setattr(adt_watch.adt_sync, "cache_dir", lambda cfg: str(tmp_path))

    adt_watch.watch(str(tmp_path), once=True)
    out = capsys.readouterr()
    assert out.out == "" and out.err == "", (
        "a tick that syncs nothing should write nothing")


def test_not_due_and_unchanged_still_holds(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(adt_watch, "_cache_fingerprint", lambda c: "FP")
    monkeypatch.setattr(adt_watch, "_watch_state",
                        lambda cfg: {"quiet": 9, "next_due": 9e18, "fp": "FP"})
    monkeypatch.setattr(adt_watch, "_rotate_log", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch, "_rate_limit_snapshot",
                        lambda: calls.append("rate") or [])
    monkeypatch.setattr(adt_watch, "_sync",
                        lambda *a, **k: calls.append("sync") or False)
    monkeypatch.setattr(adt_watch, "_render", lambda *a, **k: calls.append("render"))
    monkeypatch.setattr(adt_watch, "_save_watch_state", lambda *a: None)
    monkeypatch.setattr(adt_watch.adt_sync, "load_config", lambda root: {})
    monkeypatch.setattr(adt_watch.adt_sync, "cache_dir", lambda cfg: str(tmp_path))

    adt_watch.watch(str(tmp_path), once=True)
    assert calls == [], "a not-yet-due, unchanged tick should skip"


# --- the breaker fed by the real snapshot ----------------------------------
# These stub subprocess.run instead of `_rate_limit_snapshot`, so the real
# snapshot parsing runs.

class _R:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


def _gh_at(graphql_used, core_used, limit=5000):
    def run(args, **kwargs):
        if "graphql" in args:
            return _R(f"{graphql_used},{limit}\n")
        return _R("HTTP/2.0 200 OK\r\n"
                  f"X-Ratelimit-Limit: {limit}\r\n"
                  f"X-Ratelimit-Used: {core_used}\r\n\r\n{{}}")
    return run


def test_real_snapshot_feeds_the_breaker_at_the_floor(monkeypatch):
    """10% remaining is under the 15% floor, so the breaker fires."""
    import subprocess
    monkeypatch.setattr(subprocess, "run", _gh_at(graphql_used=4500, core_used=10))
    pools = adt_watch._rate_limit_snapshot()
    assert pools[0] == ("GraphQL", 4500, 5000)
    assert adt_watch._pool_floor_breached(pools) == "GraphQL"


def test_real_snapshot_does_not_breach_when_healthy(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", _gh_at(graphql_used=100, core_used=10))
    assert adt_watch._pool_floor_breached(adt_watch._rate_limit_snapshot()) == ""


def test_real_snapshot_does_not_breach_when_nothing_is_used(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", _gh_at(graphql_used=0, core_used=0))
    assert adt_watch._pool_floor_breached(adt_watch._rate_limit_snapshot()) == ""


def test_breach_still_renders_the_board(tmp_path, monkeypatch, capsys):
    """A breached tick skips the sync but still renders the board."""
    calls = []
    monkeypatch.setattr(adt_watch, "_watch_state", lambda cfg: {})
    monkeypatch.setattr(adt_watch, "_rotate_log", lambda *a, **k: None)
    monkeypatch.setattr(adt_watch, "_cache_fingerprint", lambda c: "fp")
    monkeypatch.setattr(adt_watch, "_rate_limit_snapshot",
                        lambda: [("GraphQL", 4950, 5000)])
    monkeypatch.setattr(adt_watch, "_sync",
                        lambda *a, **k: calls.append("sync") or True)
    monkeypatch.setattr(adt_watch, "_render", lambda *a, **k: calls.append("render"))
    monkeypatch.setattr(adt_watch.adt_sync, "load_config", lambda cfg: {})
    monkeypatch.setattr(adt_watch.adt_sync, "cache_dir", lambda cfg: str(tmp_path))

    adt_watch.watch(str(tmp_path), once=True)

    assert "render" in calls, "the board should render on a breached tick"
    assert "sync" not in calls, "the sync should be skipped"


def test_fingerprint_ignores_the_rendered_readme(tmp_path):
    """The render rewrites BACKLOG-README.md every pass; that is not a local edit."""
    ticket = tmp_path / "tasks" / "ideas" / "a.md"
    ticket.parent.mkdir(parents=True)
    ticket.write_text("---\nslug: a\n---\n")
    os.utime(ticket, (1000.0, 1000.0))
    before = adt_watch._cache_fingerprint(str(tmp_path))

    readme = tmp_path / "BACKLOG-README.md"
    readme.write_text("# board\n")
    os.utime(readme, (2000.0, 2000.0))

    assert adt_watch._cache_fingerprint(str(tmp_path)) == before == 1000.0, (
        "a re-rendered README must not reset the backoff")

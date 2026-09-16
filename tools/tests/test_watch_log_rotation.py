# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that adt watch rotates .adt/state/adt-watch.log once it passes a size
cap, and that an idle tick writes nothing to stdout or stderr."""
import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_watch  # noqa: E402


def _refs(root):
    """Create docs/references.md, so _render does not warn that it is missing."""
    d = os.path.join(str(root), "docs")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "references.md"), "w") as f:
        f.write("# References\n")


def _log(root):
    """The path the plist writes to, created empty."""
    d = os.path.join(str(root), ".adt", "state")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "adt-watch.log")
    open(p, "a").close()
    return p


def test_rotates_over_cap(tmp_path):
    log = _log(tmp_path)
    with open(log, "w") as f:
        f.write("x" * 500)

    assert adt_watch._rotate_log(str(tmp_path), max_bytes=100) is True
    assert not os.path.exists(log), "live log should be gone (recreated by the writer)"
    assert os.path.getsize(log + ".1") == 500


def test_under_cap_is_a_noop(tmp_path):
    log = _log(tmp_path)
    with open(log, "w") as f:
        f.write("x" * 50)

    assert adt_watch._rotate_log(str(tmp_path), max_bytes=100) is False
    assert os.path.getsize(log) == 50
    assert not os.path.exists(log + ".1")


def test_no_log_file_is_a_noop(tmp_path):
    """Under systemd there is no log file, so rotation does nothing."""
    assert adt_watch._rotate_log(str(tmp_path), max_bytes=100) is False
    assert not os.path.exists(_log(tmp_path) + ".1")


def test_tail_preserved_in_rotated_sibling(tmp_path):
    """The rotated file keeps every line of the old log."""
    log = _log(tmp_path)
    with open(log, "w") as f:
        f.write("early line\n" + "x" * 500 + "\n[sync] the interesting one\n")

    adt_watch._rotate_log(str(tmp_path), max_bytes=100)

    kept = open(log + ".1").read()
    assert "[sync] the interesting one" in kept
    assert "early line" in kept


def test_month_of_ticks_stays_under_cap(tmp_path):
    """43,200 ticks is one a minute for 30 days; the log and its rotated copy
    stay within twice the cap."""
    cap = 4096
    log = _log(tmp_path)
    tick = "x" * 52 + "\n"

    for _ in range(43_200):
        adt_watch._rotate_log(str(tmp_path), max_bytes=cap)
        with open(log, "a") as f:
            f.write(tick)

    total = os.path.getsize(log) + os.path.getsize(log + ".1")
    assert total <= 2 * cap, f"{total} bytes after a month — not bounded"


def test_quiet_tick_writes_nothing(tmp_path, monkeypatch):
    """An idle --once tick emits zero bytes on stdout AND stderr."""
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    _log(tmp_path)
    _refs(tmp_path)

    monkeypatch.setattr(adt_watch.adt_sync, "load_config",
                        lambda root: {"repo": "o/r", "id_prefix": "ADT"})
    monkeypatch.setattr(adt_watch.adt_sync, "cache_dir", lambda cfg: str(cache))
    # Nothing changed: reconcile_all returns only noops.
    monkeypatch.setattr(adt_watch.adt_sync, "reconcile_all",
                        lambda root, pull=True: [{"action": "noop"}])
    monkeypatch.setattr(adt_watch, "_HAVE_RENDERER", True)
    monkeypatch.setattr(adt_watch.build_kanban, "run", lambda **kw: None)

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        adt_watch.watch(str(tmp_path), once=True)

    assert out.getvalue() == "", f"idle tick printed: {out.getvalue()!r}"
    assert err.getvalue() == "", f"idle tick printed to stderr: {err.getvalue()!r}"


def test_quiet_tick_still_reports_a_real_change(tmp_path, monkeypatch):
    """A tick that syncs something still prints a [sync] line."""
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    _log(tmp_path)
    _refs(tmp_path)

    monkeypatch.setattr(adt_watch.adt_sync, "load_config",
                        lambda root: {"repo": "o/r", "id_prefix": "ADT"})
    monkeypatch.setattr(adt_watch.adt_sync, "cache_dir", lambda cfg: str(cache))
    monkeypatch.setattr(adt_watch.adt_sync, "reconcile_all",
                        lambda root, pull=True: [{"action": "updated"}])
    monkeypatch.setattr(adt_watch.adt_sync, "summarise",
                        lambda res: "noop=0, updated=1")
    monkeypatch.setattr(adt_watch, "_HAVE_RENDERER", True)
    monkeypatch.setattr(adt_watch.build_kanban, "run", lambda **kw: None)

    out = io.StringIO()
    with redirect_stdout(out):
        adt_watch.watch(str(tmp_path), once=True)

    assert "[sync]" in out.getvalue()


def test_quiet_render_suppresses_only_the_wrote_lines(tmp_path, monkeypatch):
    """_render passes quiet=True through under --once, and not otherwise."""
    seen = {}
    monkeypatch.setattr(adt_watch, "_HAVE_RENDERER", True)
    monkeypatch.setattr(adt_watch.build_kanban, "run",
                        lambda **kw: seen.update(kw))
    monkeypatch.setattr(adt_watch, "_rate_limit_snapshot", lambda: [])

    adt_watch._render(str(tmp_path), cfg={}, code_root="", quiet=True)
    assert seen["quiet"] is True

    adt_watch._render(str(tmp_path), cfg={}, code_root="", quiet=False)
    assert seen["quiet"] is False


def test_tick_rotates_before_it_writes(tmp_path, monkeypatch):
    """watch() calls _rotate_log on each tick."""
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    calls = []
    monkeypatch.setattr(adt_watch.adt_sync, "load_config",
                        lambda root: {"repo": "o/r"})
    monkeypatch.setattr(adt_watch.adt_sync, "cache_dir", lambda cfg: str(cache))
    monkeypatch.setattr(adt_watch.adt_sync, "reconcile_all",
                        lambda root, pull=True: [{"action": "noop"}])
    monkeypatch.setattr(adt_watch, "_HAVE_RENDERER", False)
    monkeypatch.setattr(adt_watch, "_rotate_log",
                        lambda root, **kw: calls.append(root) or False)

    adt_watch.watch(str(tmp_path), once=True)
    assert calls == [str(tmp_path)]

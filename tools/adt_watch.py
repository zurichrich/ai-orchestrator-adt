# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""adt watch — the required background sync companion (the cache-first migration, phase 3).

A small, foreground, long-running process per consumer project. It:
  1. watches the local .md backlog cache for changes (mtime poll),
  2. reconciles the cache -> GitHub Issues in the background (adt_sync),
  3. re-renders the local kanban preview (build_kanban) so the board is live.

It is REQUIRED for the cache-first model (commands write the cache; this
propagates to Issues) — but it is NOT app.py-class:
  - no APScheduler, no cron, no trading jobs;
  - no production credentials — it uses the developer's existing `gh` auth and
    touches only this project's Issues;
  - no inbound network listener beyond the optional localhost preview server;
  - read-only to prod data. Rule 1/2 (never background app.py / the scheduler)
    do not apply — but run it in the FOREGROUND anyway (Ctrl-C to stop); do not
    launch it from a Claude background task.

It re-renders `kanban.html` in place; open that file in a browser (or use the
project's existing preview server) for the live board. A built-in live-reload
HTTP server is a deliberate follow-up, not part of the required core — sync +
render are what the cache-first model needs.

Dependency-light: stdlib only + `gh` (via adt_sync) + the sibling renderer.
Usage:  python3 tools/adt_watch.py --root <project> [--interval 5] [--once]
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import adt_sync  # noqa: E402

# ADT-119: the launchd plist points StandardOutPath AND StandardErrorPath at
# .adt/state/adt-watch.log (install_watcher in lib/watcher.sh) and launchd
# has no rotation of its own, so the file grew forever — 16 MB / 318k lines per
# project on the machine that filed this. One generation, so the on-disk ceiling
# is 2 * this (live log + adt-watch.log.1). ~52 bytes/line measured, so 1 MiB is
# ~20k retained lines.
ADT_WATCH_LOG_MAX_BYTES = 1024 * 1024

try:
    import build_kanban  # noqa: E402
    import adt_phone_home  # noqa: E402
    import adt_machines  # noqa: E402
    _HAVE_RENDERER = True
except Exception:  # noqa: BLE001 - renderer is optional for sync-only mode
    _HAVE_RENDERER = False


_ADT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _watch_log_path(project_root: str) -> str:
    """Where the launchd plist sends this project's stdout+stderr.

    Derived from --root, NOT read from the plist or the config — that is what
    lets a merge here bound the logs on installs that already exist: every
    installed plist invokes THIS file and passes --root, so the next tick
    rotates without a plist rewrite or a reinstall (ADT-119)."""
    return os.path.join(project_root, ".adt", "state",
                        "adt-watch.log")


def _rotate_log(project_root: str,
                max_bytes: int = ADT_WATCH_LOG_MAX_BYTES) -> bool:
    """Rotate the watch log to <log>.1 once it exceeds max_bytes. Returns True
    if it rotated.

    Deliberately NOT re-pointing this process's own fds afterwards. launchd
    holds the log open, so the current tick's remaining output follows the
    renamed inode into .log.1 — which is where older lines belong anyway — and
    the NEXT tick is a fresh process whose open() resolves the path to the new
    file. Re-pointing would buy at most one tick's lines.

    A missing log is a no-op, which is also the Linux case: the systemd unit
    writes no StandardOutput=, so output goes to journald (which already
    rotates) and no such file exists. That is why lib/watcher.sh needs no
    change at all."""
    log = _watch_log_path(project_root)
    try:
        if os.path.getsize(log) <= max_bytes:
            return False
        os.replace(log, log + ".1")
        return True
    except OSError:
        # No log (Linux/systemd, foreground run, first tick) or an unwritable
        # dir — never let log housekeeping take the sync down.
        return False


def _cache_fingerprint(cache: str) -> float:
    """Cheap change signal: the max mtime across the cache's ticket .md files.

    BACKLOG-README.md is left out, as `adt_sync.iter_cache_files` leaves it out.
    The render rewrites it in the cache on every pass, so counting it made every
    tick look like a local edit: the backoff never skipped a tick, and each
    watcher spent its per-tick REST calls every 60s on a quiet board."""
    newest = 0.0
    for dirpath, _dirs, files in os.walk(cache):
        for fn in files:
            if fn.endswith(".md") and fn != "BACKLOG-README.md":
                try:
                    newest = max(newest, os.path.getmtime(
                        os.path.join(dirpath, fn)))
                except OSError:
                    pass
    return newest


def _branch_protection_snapshot(repo, branch):
    """Return (branch, "protected"|"unprotected") for the header badge, or None.

    ADT-165: the multi-agent-git-workflow rule says main is server-protected and
    nothing ever checked it, so a project installed before that ticket could not
    learn otherwise without re-running setup. The board is rendered every tick
    and is the surface a human already opens, so the check lives here.

    Response shapes measured 2026-09-04: protected -> exit 0 + the protection
    JSON; unprotected -> exit 1 + a body carrying "Branch not protected".
    Anything else (403 without admin, missing repo, offline) returns None and
    the badge is omitted — an unreadable state must never render as a confident
    "protected".

    Returns ONLY the branch name and the classification. The raw response body
    enumerates bypass actors and teams and is deliberately never carried
    forward into anything rendered.

    Best-effort, exactly like _rate_limit_snapshot: any failure drops the badge
    and never breaks the render. One REST/core point per tick, taken beside the
    rate snapshot so a backed-off or breaker-skipped tick costs nothing.
    """
    import subprocess
    if not repo or not branch:
        return None
    try:
        out = subprocess.run(
            ["gh", "api", "repos/%s/branches/%s/protection" % (repo, branch)],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if out.returncode == 0:
        return (branch, "protected")
    if "Branch not protected" in (out.stdout or ""):
        return (branch, "unprotected")
    return None


def _rate_limit_snapshot():
    """Return [(label, used, limit), …] for BOTH GitHub API pools — GraphQL
    first, then REST (core). They're separate 5000/hr budgets and the badge
    shows both (ADT-109 moved the command layer onto REST, so GraphQL flat +
    REST moving is the healthy shape a single binding-pool number would hide).

    NOT `gh api rate_limit` (ADT-153). That endpoint reports `used: 0` for every
    resource regardless of actual consumption — measured 2026-09-01: it returned
    0 both before and after 10 known REST calls while the real response header
    moved 47 -> 65, and plain `curl` with the same token returns the identical
    zeroed body, so it is not a `gh` artifact. Reading it did not merely blank
    the badge: `_pool_floor_breached` consumes the same snapshot, so it computed
    (5000-0)/5000 = 1.0 and the ADT-147 circuit breaker could never fire.

    The two sources below are the ones whose numbers match observed behaviour:
      * GraphQL — the `rateLimit` query. Accurate AND free: `used` does not
        advance across consecutive probes (verified 1721, 1721, 1721).
      * REST/core — the `X-RateLimit-*` headers of a real response. The
        `/rate_limit` endpoint's OWN headers are zeroed too, so they must come
        from a different endpoint; `gh api -i user` costs 1 core point (measured
        423 -> 424) = ~60/hr against 5,000/hr.

    Returns [] on any failure. Best-effort: missing gh / no auth / parse error
    just drops the header counter — never breaks the render."""
    import subprocess

    def _graphql_pool():
        out = subprocess.run(
            ["gh", "api", "graphql", "-f",
             "query={rateLimit{limit used}}", "--jq",
             "[.data.rateLimit.used, .data.rateLimit.limit] | @csv"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return None
        used, limit = (int(x) for x in out.stdout.strip().split(","))
        return ("GraphQL", used, limit)

    def _core_pool():
        # -i emits headers before the body; the trailing values are authoritative
        # for the CALLING request, which is what we want to report.
        out = subprocess.run(
            ["gh", "api", "-i", "user"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return None
        hdr = {}
        for line in out.stdout.splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            k = k.strip().lower()
            if k.startswith("x-ratelimit-"):
                hdr[k] = v.strip()
        try:
            return ("REST", int(hdr["x-ratelimit-used"]),
                    int(hdr["x-ratelimit-limit"]))
        except (KeyError, ValueError):
            return None

    pools = []
    for fn in (_graphql_pool, _core_pool):
        try:
            got = fn()
        except Exception:  # noqa: BLE001 - one dead pool must not drop the other
            got = None
        if got is not None:
            pools.append(got)
    return pools

def _render(cache: str, cfg: dict, code_root: str = "",
            quiet: bool = False, pools=None, protection=None) -> None:
    """Render the kanban from the shared cache dir. The cache mirrors the
    type/status tree, so the renderer treats the cache dir as its project root
    with an empty backlog_root (BL = cache). Passes the backlog repo's issues +
    board URLs so cards link to their Issue and the header links to the board.

    code_root is the project's *code* checkout (where adt watch was launched)
    — the references doc lives there (in the agent-dev-team submodule), NOT in
    the cache, so we resolve it to an absolute path and hand it to the renderer.
    Without this the references doc is unfindable from the cache and the board's
    '📖 references' link is silently dropped. It is ALSO where the gitignored
    token ledger lives (the adt-token-log hook writes it under the code tree),
    so we pass it as token_ledger_root too — else the renderer resolves the
    ledger from the cache dir, finds nothing, and every card shows "🪙 —"."""
    if not _HAVE_RENDERER:
        return
    repo = cfg.get("repo", "")
    issues_url = f"https://github.com/{repo}/issues" if repo else ""
    board_url = ""
    if cfg.get("owner") and cfg.get("project_number"):
        board_url = (f"https://github.com/users/{cfg['owner']}"
                     f"/projects/{cfg['project_number']}")
    # Resolve the references doc against the code checkout (absolute path so the
    # renderer finds it even though REPO is the cache dir). The doc lives under
    # the agent-dev-team submodule for a consuming project, but directly under
    # code_root when the team repo IS the code root (ADT-on-ADT, or any non-
    # submodule layout). Try the configured/default path first, then fall back
    # to the in-repo location — and if neither resolves, say so on stderr rather
    # than silently dropping the references page + its kanban link (ADT-58).
    commands_doc_src = ""
    if code_root:
        rel = cfg.get("commands_doc_src", "agent-dev-team/docs/references.md")
        candidates = [os.path.join(code_root, rel)]
        # Fallback for the non-submodule layout: docs/references.md under the
        # code root itself. Skip if the configured rel already points there.
        fallback = os.path.join(code_root, "docs", "references.md")
        if fallback not in candidates:
            candidates.append(fallback)
        for cand in candidates:
            if os.path.isfile(cand):
                commands_doc_src = cand
                break
        if not commands_doc_src:
            print(f"  [render] references doc not found (looked in: "
                  f"{', '.join(candidates)}); the kanban will omit the "
                  f"references page + link. Set commands_doc_src in the project "
                  f"config if it lives elsewhere.", file=sys.stderr)
    rate_pools = pools if pools is not None else _rate_limit_snapshot()
    try:
        build_kanban.run(project_root=cache, backlog_root="",
                         id_prefix=cfg.get("id_prefix", "TIX"),
                         commands_doc_src=commands_doc_src or None,
                         issues_url=issues_url, board_url=board_url,
                         rate_pools=rate_pools,
                         # ADT-172: the header names the repo. `repo` is the
                         # config's own "owner/repo"; the renderer cannot
                         # derive it, since REPO there is the cache dir.
                         repo_name=repo,
                         # ADT-165: the header says whether main is protected.
                         branch_protection=protection,
                         # ADT-384: the machine reports for the footer. They
                         # sit under the code checkout's .adt/state, not the cache.
                         machines=(adt_machines.load_machines(code_root)
                                   if code_root else None),
                         # AO-006: the window this machine last vouched for.
                         # Read from the persisted state rather than computed
                         # here, so ONE path covers every branch — a normal tick
                         # has just stamped it; a breached or PAUSED tick
                         # deliberately has not, which is what turns the button
                         # red on a board that is not being synced.
                         healthy_until=_health_stamp(cfg),
                         # ADT-119: under --once (the launchd tick) the three
                         # "Wrote …" lines are the same text 1,440 times a day.
                         # The conditional lines build_kanban prints on a real
                         # state change are NOT suppressed.
                         quiet=quiet,
                         # The token ledger is gitignored runtime state under the
                         # CODE checkout, not the cache (REPO). Without this, the
                         # renderer resolves the ledger from the cache dir, finds
                         # nothing, and every card shows "🪙 —". See the
                         # kanban-token-counts-dark fix.
                         token_ledger_root=code_root or "")
        # Drop a copy of the rendered board into the project's .adt/
        # folder so a user can right-click -> Open in Browser right where they're
        # working — the cache lives outside the checkout and isn't browsable from
        # the editor tree. .adt/ is gitignored, so this never commits.
        if code_root:
            try:
                dst_dir = os.path.join(code_root, ".adt")
                if os.path.isdir(dst_dir):
                    import shutil
                    # ADT-136: kanban.html now carries every ticket's detail
                    # inline, so there is nothing per-ticket left to copy — the
                    # ADT-58 reason for copying a whole tree is gone.
                    # ADT-143: references.html is no longer generated — its
                    # content is inlined into kanban.html as a :target panel.
                    for fname in ("kanban.html",):
                        src = os.path.join(cache, fname)
                        if os.path.isfile(src):
                            shutil.copyfile(src, os.path.join(dst_dir, fname))
                    # Remove a tree an older ADT copied here. This is a SECOND,
                    # separate directory from the cache-side one build_kanban
                    # removes: the copy was written with copytree(
                    # dirs_exist_ok=True), which only ever added, so stopping
                    # the copy would leave every page already on disk. An orphan
                    # still opens in a browser and looks current.
                    stale = os.path.join(dst_dir, "tickets")
                    if os.path.isdir(stale):
                        shutil.rmtree(stale, ignore_errors=True)
            except Exception:  # noqa: BLE001 - a copy failure must not kill the loop
                pass
    except Exception as e:  # noqa: BLE001 - a render error must not kill the loop
        print(f"  [render] skipped: {e}", file=sys.stderr)


# --------------------------------------------------------------------------
# ADT-147: two gates in front of the tick, both keyed on state that survives the
# process. Under launchd the cadence is EXTERNAL — lib/watcher.sh runs
# `adt_watch.py --once` on StartInterval — so neither of these can be a sleep:
# the process exits every tick. They are persisted self-skips instead, and a
# skipped tick costs zero API calls.
# --------------------------------------------------------------------------

# Converged backoff (1d). A converged board is all-noop, and re-asking every
# 60s buys nothing. After QUIET_GRACE consecutive all-noop ticks the interval
# doubles from BACKOFF_BASE up to BACKOFF_CAP; ANY movement snaps it back to
# every tick. The cap is 5 minutes because that is what the ticket's
# REST-ceiling arithmetic assumes for the N-machine figure.
QUIET_GRACE = 3
BACKOFF_BASE = 60.0
BACKOFF_CAP = 300.0
# AO-006. How long a completed pass vouches for the board it just rendered. The
# page goes red once the clock passes the stamp, so this must EXCEED the longest
# gap a healthy watcher can leave between passes, or a working board reads red.
# BACKOFF_CAP is that longest gap by this module's own logic (_backoff_due
# clamps to it); the extra BACKOFF_BASE covers the launchd StartInterval that
# fires the next tick. Derived rather than written as 360.0 so retuning the
# ladder cannot leave the two apart. Detection costs ~6 minutes as a result, and
# the ticket states that bound rather than implying a tighter one.
HEALTH_WINDOW = BACKOFF_CAP + BACKOFF_BASE
# Ceiling on the doubling exponent, so the ladder cannot overflow float range
# (see _backoff_due). 60s * 2**24 is 31 years — far above any cap anyone would
# set, and far below the ~1024 shifts it takes to overflow.
BACKOFF_MAX_SHIFT = 24

# Rate-limit circuit breaker (1e). A SHARE of the pool, not a fixed point count:
# N machines on one account share one 5,000/hr budget (the limits are per USER,
# not per token), so each reserving a fraction leaves headroom instead of all of
# them racing to the bottom.
#
# ADT-153: this breaker never fired once. The arithmetic below is correct; its
# INPUT was not — `_rate_limit_snapshot` read an endpoint that reports used:0
# for every resource, so `(limit - 0) / limit` was always 1.0 and could never
# drop under the floor. The pool ran to zero while the guard built to prevent
# exactly that sat in the path doing nothing. The snapshot now reads sources
# whose numbers match observed behaviour; `test_watch_backoff_breaker.py` pins
# the degenerate case so it cannot silently become unfireable again.
RATE_FLOOR_SHARE = 0.15


# AO-006. The board's stop/start button. Pausing does NOT unload the agent —
# the loop keeps running and skips its pass — which is what lets ONE process
# serve both directions. Unloading it would leave nothing alive to start it
# again, and that dead end is what three earlier designs of this ticket ran
# into.
BOARD_PORT = 8787


def _pause_flag(cache: str) -> str:
    """Where the paused marker lives. In the cache, so it is outside every git
    checkout and visible to the same process that renders the board."""
    return os.path.join(cache, ".paused")


def _is_paused(cache: str) -> bool:
    return os.path.exists(_pause_flag(cache))


def _set_paused(cfg: dict, cache: str, paused: bool) -> bool:
    """Flip the flag, and clear the backoff on the way through.

    The ladder reset is the half that is easy to miss: after QUIET_GRACE
    all-noop ticks `next_due` is up to BACKOFF_CAP away, so a resume would sit
    idle for up to five minutes and look broken. Only a resident watcher can do
    this, because the process that owns the tick state is the one taking the
    click.
    """
    flag = _pause_flag(cache)
    if paused:
        with open(flag, "w"):
            pass
    elif os.path.exists(flag):
        os.unlink(flag)
    _save_watch_state(cfg, 0, 0.0, _watch_state(cfg).get("fp") or 0.0)
    return _is_paused(cache)


def _serve_board(cfg: dict, cache: str, port: int):
    """Serve the board and accept the toggle, in this same process.

    Exactly two routes, and no file handler:

        GET  /            -> the rendered kanban.html, read fresh per request
        POST /sync/toggle -> flip, then report the new state as JSON

    Deliberately NOT SimpleHTTPRequestHandler rooted at the cache: that would
    put every ticket .md on a socket to answer one question about one file.
    Anything else gets a 404. Bound to 127.0.0.1, so it is not reachable off
    this machine. A board opened as a file:// URL still renders; its button
    disables itself, because there is nothing to POST to.
    """
    import http.server
    import json as _json
    import socketserver
    import threading

    board = os.path.join(cache, "kanban.html")

    class _H(http.server.BaseHTTPRequestHandler):
        server_version = "adt-watch"

        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path.rstrip("/") != "/sync/toggle":
                self._send(b'{"error":"not found"}', "application/json", 404)
                return
            paused = _set_paused(cfg, cache, not _is_paused(cache))
            print(f"  [adt-watch] sync {'paused' if paused else 'resumed'} "
                  f"from the board.", file=sys.stderr)
            self._send(_json.dumps({"paused": paused}).encode(),
                       "application/json")

        def do_GET(self):
            if self.path.split("?")[0].rstrip("/") not in ("", "/"):
                self._send(b"not found", "text/plain", 404)
                return
            try:
                with open(board, "rb") as fh:
                    self._send(fh.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(b"the board has not been rendered yet",
                           "text/plain", 503)

        def log_message(self, *a):
            pass                      # ADT-119: an idle tick writes zero bytes

    class _S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = _S(("127.0.0.1", port), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _health_window(now: float) -> float:
    """When the board rendered at `now` stops vouching for itself.

    UNCONDITIONAL, and that is the point. Its neighbour `_backoff_due` returns
    0.0 as an eager sentinel while `quiet < QUIET_GRACE`, which is gate 1's
    "don't gate me" marker and not a time. Published to the page, that sentinel
    reads as an hour-past deadline and paints a healthy, actively-syncing board
    red. This function takes only `now` — it cannot branch on `quiet`, so it
    cannot grow that bug back. test_health_window_never_sentinel pins it.
    """
    return now + HEALTH_WINDOW


def _health_stamp(cfg: dict) -> float | None:
    """The published window, or None when nothing has stamped one yet.

    A TOP-LEVEL key, deliberately not a fourth field inside `watch`.
    `_update_state_doc` merges at the top level only (`doc.update(updates)`), so
    its guarantee — clobbering a key another writer owns is structurally
    impossible — holds between top-level keys but NOT between fields sharing one
    dict. `_save_watch_state` writes quiet/next_due/fp as a single payload, and
    those are only known after `_sync()` returns; threading this through it
    would make the pre-sync write re-pass values it does not have, and a wrong
    `quiet` freezes the backoff ladder at eager (ADT-147's rate-limit
    conservation, silently gone). Separate key, separate writer, no shared
    payload to corrupt.

    None means "no window published" and the renderer omits the pill entirely.
    Never coerce it to 0.0, which the page would read as long expired.
    """
    if not (cfg.get("cache_dir") or cfg.get("project")):
        # adt_sync.cache_dir() ends in os.makedirs(..., exist_ok=True), so
        # reading the state doc through an unidentified cfg CREATED
        # ~/.adt/project/cache as a side effect of a read. Found in QA.
        return None
    v = adt_sync._load_state_doc(cfg).get("healthy_until")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _stamp_health(cfg: dict, now: float) -> None:
    """Publish the window. One key, so this cannot disturb `watch`."""
    adt_sync._update_state_doc(cfg, {"healthy_until": _health_window(now)})


def _watch_state(cfg: dict) -> dict:
    """Persisted per-machine tick state: {"quiet": N, "next_due": epoch, "fp": s}.

    Takes `cfg`, like every other sidecar accessor (`_load_pull_state`,
    `_load_sync_state`, `_load_state_doc`) — `watch()` already holds one, and
    re-deriving it here re-parsed the project config on every tick, including
    the idle self-skip that is supposed to be the cheap path."""
    return adt_sync._load_state_doc(cfg).get("watch") or {}


def _save_watch_state(cfg: dict, quiet: int, next_due: float, fp: str) -> None:
    adt_sync._update_state_doc(
        cfg, {"watch": {"quiet": quiet, "next_due": next_due, "fp": fp}})


def _backoff_due(quiet: int, now: float) -> float:
    """Next-due stamp for `quiet` consecutive all-noop ticks.

    The exponent is clamped, not only the product. `min(step, BACKOFF_CAP)`
    bounds the RESULT but still evaluates `2 ** (quiet - QUIET_GRACE)` in full,
    and `quiet` has no ceiling — it counts every consecutive all-noop tick a
    watcher has ever taken. At `quiet >= 1027` that integer leaves float range
    and the multiply raises `OverflowError`, killing the tick. On the capped
    300s interval a quiet board reaches it in about 3.6 days; a consumer's
    watch log carries that traceback 2,008 times (found reviewing that
    consumer's rescued telemetry, 2026-09-09).

    Clamping changes no reachable value: the ladder is already flat once
    `BACKOFF_BASE * 2**k` passes `BACKOFF_CAP`, which happens at k=3 for the
    shipped constants (60 * 8 = 480 > 300).
    """
    if quiet < QUIET_GRACE:
        return 0.0                      # still eager
    step = BACKOFF_BASE * (2 ** min(quiet - QUIET_GRACE, BACKOFF_MAX_SHIFT))
    return now + min(step, BACKOFF_CAP)


def _pool_floor_breached(pools) -> str:
    """The pool name whose REMAINING share is under the floor, or "".

    Guards the pass BEFORE it starts. The alternative — discovering exhaustion
    mid-pass — is a partial write: some tickets reconciled, the rest erroring,
    which is the state the ADT-101/109 outages left behind."""
    for label, used, limit in (pools or []):
        try:
            if limit and (limit - used) / float(limit) < RATE_FLOOR_SHARE:
                return label
        except (TypeError, ZeroDivisionError):
            continue
    return ""


def _sync(project_root: str, pull: bool = True) -> bool:
    try:
        res = adt_sync.reconcile_all(project_root, pull=pull)
        # Quiet when nothing moved (every result a noop) — the steady state now
        # that change-detection skips converged tickets. Log otherwise.
        actions = {r.get("action") for r in res}
        if actions - {"noop"}:
            print(f"  [sync] {adt_sync.summarise(res)}")
        if "rate-limited" in actions:
            print("  [sync] rate-limited — backed off; will resume when the "
                  "GitHub quota resets.", file=sys.stderr)
        # ADT-147: did anything actually move? Drives the converged backoff.
        # A duplicate cache file moves nothing and waits for a human, so it
        # is logged above but must not keep the watcher eager (ADT-354).
        return bool(actions - {"noop", "duplicate"})
    except adt_sync.GhError as e:
        print(f"  [sync] gh error (will retry next tick): {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"  [sync] error (will retry next tick): {e}", file=sys.stderr)
    return True          # an error is not convergence — stay eager


def watch(project_root: str, interval: float = 5.0, once: bool = False) -> None:
    cfg = adt_sync.load_config(project_root)
    cache = adt_sync.cache_dir(cfg)
    repo = cfg.get("repo", "?")
    # ADT-119: no banner under --once. It was the same line every 60s forever
    # in a launchd log nothing rotated. Foreground keeps it — there it is the
    # startup line a human is watching for, printed once per process, not once
    # per tick. (It also settles the old "interval=5.0s is misleading under
    # --once" problem: the cadence is external in that mode, so the only honest
    # thing to print about it was nothing.)
    if not once:
        cadence = f"interval={interval}s"
        print(f"[adt-watch] {repo}  cache={cache}  {cadence}  "
              f"render={'on' if _HAVE_RENDERER else 'off'}")

    last_fp = None
    # AO-006: the control surface, resident mode only. Under --once there is no
    # process to keep it, which is exactly why the watcher is now resident.
    board_srv = None
    if not once:
        try:
            board_srv = _serve_board(cfg, cache, int(cfg.get("board_port") or BOARD_PORT))
            print(f"[adt-watch] board http://127.0.0.1:"
                  f"{board_srv.server_address[1]}/", file=sys.stderr)
        except OSError as e:                 # port taken: the board still renders
            print(f"[adt-watch] board server not started ({e}); the pill will "
                  f"show state but not toggle.", file=sys.stderr)

    while True:
        _rotate_log(project_root)            # bound the launchd log (ADT-119)

        # --- gate 1 (ADT-147/1d): converged self-skip. MUST stay silent — a
        # skipped tick is an idle tick, and ADT-119 requires an idle tick to
        # write zero bytes to the launchd log. Only the breaker below speaks.
        st = _watch_state(cfg)
        now = time.time()

        # The fingerprint is a local mtime walk — no API calls — so it is read
        # BEFORE the backoff gate, not after. Two reasons (ADT-147 altitude
        # review): a local stage move must reach the board promptly, and the
        # two-cadence model (git-workflow §D2) says the board goes live the
        # moment a ticket is picked up — waiting out a 5-minute backoff would
        # break that for the one change the operator is watching for. And it is
        # persisted, because under launchd every tick is a fresh process, so the
        # in-process `last_fp` is always None and the fingerprint gate below
        # never fired there at all.
        #
        # ADT-149: `changed_locally` is an OVERRIDE of the timer, not a
        # precondition of the pass. It earns its place by clearing `skipping`
        # below; gating the pass on it as well made the `now >= due` branch
        # unreachable, so a pass ran ONLY on a local edit. That left the
        # backoff ladder decorative (`next_due` gated nothing) and, worse, gave
        # a change made on github.com or by another machine no path into this
        # cache at all — the `since`-watermark pull lives inside this branch.
        fp = _cache_fingerprint(cache)
        changed_locally = fp != (last_fp if last_fp is not None else st.get("fp"))
        due = float(st.get("next_due") or 0.0)
        skipping = now < due and not changed_locally

        if not skipping:
            # --- gate 3 (ADT-112): the pass lock. Acquired PER TICK, never for
            # the process lifetime — a resident --interval watcher holding it
            # for its whole life would starve every other pass, which is the
            # opposite of the goal. The whole branch is inside it, not just
            # _sync: _render writes the board with a plain write_text, so two
            # concurrent renders can leave a truncated kanban.html on disk.
            #
            # Wrapping at THIS boundary is also what stops a lost race dropping
            # the change. `last_fp` and _save_watch_state are both assigned
            # inside the branch, so a skipped tick leaves them untouched and the
            # next tick still computes changed_locally == True and syncs. The
            # work is deferred, not lost — and that follows from where the wrap
            # sits, not from a rule anyone has to remember.
            with adt_sync.pass_lock(cfg) as owned:
                if not owned:
                    # stderr, matching the breaker notice below — both land in
                    # the launchd log. A contended tick is an event, not an idle
                    # tick, so ADT-119's zero-bytes-when-idle rule doesn't cover
                    # it.
                    print("  [adt-watch] another pass is running; skipping "
                          "this tick.", file=sys.stderr)
                else:
                    # --- gate 2 (ADT-147/1e): circuit breaker. Skip the WHOLE pass
                    # rather than start one that dies mid-write. This one logs: a pool
                    # under its floor is an event a human needs to see.
                    pools = _rate_limit_snapshot()
                    # ADT-165: one REST/core point. It stays INSIDE the lock,
                    # where ADT-165 put it relative to the tick — a tick that
                    # skips makes no API call at all, and lock contention is
                    # simply a second reason to skip. The `if not owned` branch
                    # returns above this line, so a contended tick pays nothing
                    # for it (ADT-112).
                    protection = _branch_protection_snapshot(
                        cfg.get("repo", ""), cfg.get("main_branch", "main"))
                    breached = _pool_floor_breached(pools)
                    if breached:
                        print(f"  [sync] skipped — {breached} pool below the "
                              f"{int(RATE_FLOOR_SHARE * 100)}% floor; reserving headroom "
                              f"for other machines on this account (ADT-147).",
                              file=sys.stderr)
                    else:
                        # AO-006: vouch for the board BEFORE the long call, not
                        # only after it. _gh() is untimed (adt_sync.py:168) and
                        # reconcile_all calls it once per changed file, so a bulk
                        # push can outrun the window the previous pass left and
                        # paint a live, working watcher red. Stamping here gives
                        # the pass its own full window.
                        #
                        # Inside this `else`, never at lock acquisition: the
                        # breach check above would then be bypassed, and a
                        # rate-limited tick — which is genuinely not syncing —
                        # would keep refreshing its own green light forever.
                        # AO-006: paused means the loop keeps running and does
                        # not sync. The window is NOT stamped, so the pill goes
                        # red — which is truthful: the board is not being synced.
                        if _is_paused(cache):
                            moved = False
                        else:
                            _stamp_health(cfg, now)
                            moved = _sync(project_root)  # push local + pull remote
                        last_fp = _cache_fingerprint(cache)  # re-read: our own write-backs
                        # (issue_number, pulled changes) bump mtimes; capture post-sync so
                        # we don't loop on our own writes.
                        quiet_ticks = 0 if moved else int(st.get("quiet") or 0) + 1
                        _save_watch_state(cfg, quiet_ticks,
                                          _backoff_due(quiet_ticks, now), last_fp)
                        # Re-stamp from the clock NOW, not from the `now` the
                        # tick opened with: a slow pass has consumed part of the
                        # window it was given, and the board it is about to
                        # render is fresh as of this moment.
                        #
                        # Guarded, like the pre-sync stamp: a PAUSED tick must
                        # not vouch for a board it did not sync. Without this
                        # the pill stays green while sync is off — the exact
                        # false-green the ticket exists to prevent, arriving
                        # through the pause the ticket added.
                        if not _is_paused(cache):
                            _stamp_health(cfg, time.time())
                    # ADT-384: once a UTC day, this machine's ADT version and
                    # commit go on the adt:install Issue, where other machines'
                    # installers and boards read them. Before the render, so
                    # the board shows today's report. Skipped with the sync on a
                    # breached tick: it makes REST calls. Never raises.
                    if not breached:
                        adt_machines.report(project_root, _ADT_ROOT,
                                            cfg.get("repo", ""), quiet=once)
                    # ADT-153: the render sits OUTSIDE the breach branch. It makes no API
                    # call — it reads the cache and writes HTML — so skipping the SYNC is
                    # no reason to skip it, and a breached tick is exactly when a human
                    # goes looking at the board. Leaving it in the `else` froze the board
                    # precisely when something was wrong. Unreachable until now, because
                    # the breaker could never fire against a snapshot that always read 0
                    # — the same root cause as the blank badge.
                    # quiet under --once: _sync is already silent when every result is a
                    # noop, so an idle tick still writes zero bytes.
                    _render(cache, cfg, project_root, quiet=once, pools=pools,
                            protection=protection)
                    # ADT-170 3b/3c: the version advisory and the optional usage ping.
                    # Gated to once per UTC day inside run_once — the tick is 60s and
                    # docs/security-posture.md publishes "daily", so the cadence has to
                    # be enforced here, not promised in prose. Never raises.
                    # ADT-224: pass the counts PROVIDER, not a tally. run_once
                    # calls it only inside its once-a-day branch, so the logs are
                    # not walked on every 60s tick. Before this the argument was
                    # omitted entirely, so `counts` defaulted to {} and every
                    # ping ever sent carried commands/tickets/tokens as zero.
                    # ADT-284: bind the CACHE into the provider. The quality
                    # metrics live in the cache, which is outside every git
                    # checkout, so `command_counts` cannot find it on its own.
                    # Passing the bare function here is how ADT-224's zeros
                    # happened — the wiring is the thing that has to be right,
                    # not the function.
                    adt_phone_home.run_once(
                        project_root, _ADT_ROOT,
                        counts=lambda pr, rr: adt_phone_home.command_counts(
                            pr, rr, cache_dir=cache),
                        quiet=once)
        if once:
            return
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[adt-watch] stopped.")
            return


def main(argv=None):
    ap = argparse.ArgumentParser(description="adt watch — cache<->Issues sync.")
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--once", action="store_true",
                    help="one sync+render pass, then exit (for CI/tests).")
    args = ap.parse_args(argv)
    watch(args.root, interval=args.interval, once=args.once)


if __name__ == "__main__":
    main()

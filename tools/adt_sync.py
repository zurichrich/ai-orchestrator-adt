# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Reconcile the local .md backlog cache <-> GitHub Issues (the cache-first migration, phase 3).

The .md backlog is the local cache and the read/write surface for the kanban
and parallel agents; GitHub Issues is the durable backing store. This module is
the REQUIRED background sync (driven by adt_watch.py): commands never call `gh`
on the hot path — this does, idempotently.

Direction is cache->Issue authoritative for ADT-owned fields (title, body,
labels, state, assignees) and Issue->cache for GitHub-owned fields
(issue_number, issue_node_id, remote comments). One pass = reconcile_all().

Dependency-light: stdlib + the `gh` CLI (already required by setup.sh) + the
sibling ticket_serializer. No PyYAML, no requests.
"""

from __future__ import annotations

import collections
import contextlib
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:          # non-POSIX host; pass_lock fails open (ADT-112)
    fcntl = None

sys.path.insert(0, os.path.dirname(__file__))
from ticket_serializer import (FILING_LABEL, NON_TICKET_LABELS,  # noqa: E402
                               parse_md, emit_md,
                               to_issue, from_issue,
                               _strip_slug_trailer)


# --------------------------------------------------------------------------
# gh shell-out (the ONE place that talks to GitHub).
# --------------------------------------------------------------------------
class GhError(RuntimeError):
    pass


class RateLimitError(GhError):
    """gh failed specifically because the GitHub API rate limit is exhausted.
    Distinct from GhError so the caller can ABORT the whole tick (every further
    call would fail too) instead of recording 226 per-ticket errors and retrying
    them next tick — the runaway that drained the GraphQL pool and never
    recovered. See the adt-watch backoff in reconcile_all()."""


# The GraphQL porcelain this module may never call (ADT-109, enforced here
# since ADT-147). Each of these bills the 5,000-POINT GraphQL pool by query
# complexity while the flat REST equivalent costs one request from the separate
# core pool. Note what is NOT here: `issue create` / `list` / `edit` / `close` /
# `reopen` and every `gh project ...` are permitted — the first group is
# one-shot work outside the lifecycle burst, and Projects v2 has no REST API.
#
# WHY AT THE RUNNER rather than as a grep over the source.
# `tests/test_gh_api_discipline.sh` still greps the playbook layer, where calls
# are literal shell text. It cannot do this job for Python: `grep` is
# line-oriented, and every call site in this file is written as
#
#     _gh_json([
#         "issue", "view", ...
#
# so a pattern requiring the runner and the verb on one line matches nothing —
# the three sites ADT-147 removed would all have passed such a gate, which is a
# gate that cannot see the defect it exists to prevent. Checking `args` here is
# formatting-independent, and it also catches the indirection form
# (`edit_args = ["issue", "edit", ...]` then `_gh(edit_args)`) that no grep can
# follow.

def adt_state_dir(project_root, *leaf):
    """The ADT runtime-state dir, preferring `.adt/state/` (ADT-301).

    There is no legacy fallback: `.adt/` does not exist in any
    supported install, and a fallback is what let it be recreated after the
    migration removed it.
    """
    base = os.path.join(project_root, ".adt", "state")
    return os.path.join(base, *leaf) if leaf else base


def is_adt_project(project_root):
    """True when the project carries ADT's marker directory."""
    return os.path.isdir(os.path.join(project_root, ".adt"))

_BANNED_PORCELAIN = frozenset({
    ("issue", "view"), ("pr", "view"), ("pr", "create"), ("pr", "merge"),
})


#: GitHub's hard limit on an Issue body, in UTF-8 BYTES. ADT-224 D1h; corrected
#: from 65536 chars to 262144 bytes on 2026-09-07.
#:
#: The unit matters and the wrong one was load-bearing. GitHub stores the body
#: as a MySQL `mediumblob` capped at 262,144 bytes; its 422 says "Body is too
#: long (maximum is 65536 characters)" because 65,536 is what that many bytes
#: holds IF EVERY CHARACTER IS 4 BYTES. That worst-case figure was copied out of
#: the error message and applied to `len(body)` — a character count — so any
#: mostly-ASCII body between 65,536 chars and the real ceiling was refused here
#: while GitHub would have taken it. Verified against the live API: ADT-224's
#: own body, 71,640 chars / 72,187 bytes, was rejected by this check and then
#: accepted and stored in full by GitHub.
ISSUE_BODY_CAP_BYTES = 262144


def check_body_cap(body: str) -> None:
    """Raise BEFORE the request when a body would exceed GitHub's cap.

    WHY THIS EXISTS. An over-cap body raises `GhError` from the API today; the
    push loop catches it, appends an error and pops state to RETRY, and
    `adt_watch.py:406-407` prints to a background watcher's stderr. So it is
    fail-open and never converges — the tick burns an API call every 60s
    forever, and the only symptom is a line nobody reads. Nothing checked
    before ADT-224 added this.

    Raising here makes the failure local, immediate and legible, and names the
    field to move. It is not a fix for a body that IS over cap — that is a
    ticket-authoring problem — but it stops the silent retry loop.

    MEASURE BYTES, NOT CHARACTERS. The cap is a storage limit, so a body is
    over it when its UTF-8 encoding is, not when its character count is. The
    two differ by up to 4x, and the guard's first version conflated them: it
    jammed ADT-224 in `qa` for two days, because the sync sends body, labels
    and state in ONE edit, so refusing the body also dropped the `stage:done`
    move and the close. The only symptom was one error line per 60s tick in a
    background log.
    """
    if not body:
        return
    n = len(body.encode("utf-8"))
    if n > ISSUE_BODY_CAP_BYTES:
        raise GhError(
            "Issue body is %d UTF-8 bytes, over GitHub's %d-byte cap (its 422 "
            "reports this as '65536 characters' — the 4-bytes-per-char worst "
            "case). The push would fail and the watcher would retry it "
            "forever. Move bulk content into frontmatter (which is never "
            "pushed — `gate_effects` and `done_evidence` both live there for "
            "this reason) or trim the body."
            % (n, ISSUE_BODY_CAP_BYTES))


def _gh(args: list, input_text: str | None = None) -> str:
    """Run `gh <args>`; return stdout. Raise RateLimitError on a rate-limit
    failure, GhError on any other failure.

    Refuses the GraphQL porcelain outright — see `_BANNED_PORCELAIN`."""
    # ADT-224 D1h: a `-f body=...` or `--body ...` over the cap is refused here,
    # before the call, rather than by the API after it.
    for i, a in enumerate(args):
        if a == "--body" and i + 1 < len(args):
            check_body_cap(args[i + 1])
        elif isinstance(a, str) and a.startswith("body="):
            check_body_cap(a[5:])
    if tuple(args[:2]) in _BANNED_PORCELAIN:
        raise GhError(
            f"gh {' '.join(map(str, args[:2]))} is GraphQL porcelain and bills "
            f"the constrained 5,000-point pool (ADT-109/ADT-147). Use "
            f"`gh api repos/...` (REST, core pool) instead.")
    proc = subprocess.run(
        ["gh", *args],
        input=input_text,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        if "rate limit" in stderr.lower() or "api rate limit exceeded" in stderr.lower():
            raise RateLimitError(f"gh {' '.join(args)} failed: {stderr}")
        raise GhError(f"gh {' '.join(args)} failed: {stderr}")
    return proc.stdout


def _gh_json(args: list):
    out = _gh(args)
    return json.loads(out) if out.strip() else None


# --------------------------------------------------------------------------
# Config.
# --------------------------------------------------------------------------
def load_config(project_root: str) -> dict:
    """Read .adt/config.yaml (written by setup.sh --init-github). Tiny reader —
    the file is flat scalars + a `stages:` list-of-{name,label}."""
    path = os.path.join(project_root, ".adt", "config.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found — run `setup.sh --init-github` first.")
    cfg: dict = {"stages": []}
    cur = None
    for line in open(path):
        s = line.rstrip("\n")
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        if s.startswith("  - name:"):
            cur = {"name": s.split(":", 1)[1].strip()}
            cfg["stages"].append(cur)
        elif s.startswith("    label:") and cur is not None:
            cur["label"] = s.split(":", 1)[1].strip()
        elif ":" in s and not s.startswith(" "):
            k, _, v = s.partition(":")
            k = k.strip()
            if k == "stages":
                continue  # header for the list block below; don't clobber it
            cfg[k] = v.strip()

    # ADT-066 defect 3: commands_doc_src may live ONLY in the per-user config
    # (~/.adt/projects/<name>.yaml under kanban.commands_doc_src) — that's where
    # adt-install.sh records it, and it isn't always copied into .adt/config.yaml
    # (init_github may not have re-run). Fall back to it so the renderer finds
    # the references doc regardless of which config carries the value.
    if not cfg.get("commands_doc_src"):
        name = cfg.get("project") or cfg.get("repo", "").split("/")[-1]
        if name:
            user_cfg = os.path.expanduser(
                os.path.join("~/.adt/projects", f"{name}.yaml"))
            val = _yaml_scalar(user_cfg, ("kanban", "commands_doc_src"))
            if val:
                cfg["commands_doc_src"] = os.path.expanduser(val)
    return cfg


def _yaml_scalar(path: str, keys: tuple) -> str:
    """Read a single nested scalar (e.g. kanban.commands_doc_src) from a YAML
    file without a yaml dep — the per-user config is indented two spaces per
    level, same style adt-install.sh writes. Returns "" if absent/unreadable."""
    if not os.path.exists(path):
        return ""
    want_depth = 0
    try:
        for line in open(path):
            s = line.rstrip("\n")
            if not s.strip() or s.lstrip().startswith("#"):
                continue
            indent = len(s) - len(s.lstrip())
            if indent != want_depth * 2:
                continue
            key = s.strip().split(":", 1)[0].strip()
            if key != keys[want_depth]:
                continue
            if want_depth == len(keys) - 1:
                return s.split(":", 1)[1].strip().strip('"').strip("'")
            want_depth += 1
    except OSError:
        return ""
    return ""


# --------------------------------------------------------------------------
# Cache location (the cache-first migration: the cache is a SHARED dir OUTSIDE every git
# checkout, e.g. ~/.adt/<project>/cache/ — not an in-repo backlog tree inside
# the repo. Resolved from config so all sessions + the reconciler agree.)
# --------------------------------------------------------------------------
def cache_dir(cfg: dict) -> str:
    """Absolute path to the shared cache dir. From `cache_dir:` in config if
    set (with ~ expanded); else ~/.adt/<project>/cache/. Created if absent."""
    raw = cfg.get("cache_dir")
    if not raw:
        project = cfg.get("project") or cfg.get("repo", "project").split("/")[-1]
        raw = os.path.join("~", ".adt", project, "cache")
    path = os.path.abspath(os.path.expanduser(raw))
    os.makedirs(path, exist_ok=True)
    return path


# --------------------------------------------------------------------------
# Cache discovery.
# --------------------------------------------------------------------------
def iter_cache_files(cfg: dict):
    """Yield (path, parsed_dict) for every ticket .md in the shared cache.
    The cache keeps the type/status/<slug>.md folder tree (so the existing
    kanban renderer reads it unchanged); only its ROOT moves out of the repo to
    the shared cache dir. A stage-move is a file move between cache subfolders."""
    base = cache_dir(cfg)
    for dirpath, _dirs, files in os.walk(base):
        # Skip generated/asset dirs.
        if os.sep + "assets" in dirpath or os.sep + "tickets" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".md") or fn == "BACKLOG-README.md":
                continue
            path = os.path.join(dirpath, fn)
            try:
                yield path, parse_md(open(path).read())
            except Exception as e:  # noqa: BLE001 - a bad file must not stop sync
                print(f"  [skip] {path}: {e}", file=sys.stderr)


# --------------------------------------------------------------------------
# Sync-state: per-file content hash of the last SUCCESSFUL reconcile, so a
# converged ticket is skipped before its (GraphQL-costing) issue fetch. This is
# the fix for the runaway that fetched all 226 issues every tick and drained the
# rate limit. State lives next to the cache (not in git), keyed by absolute path.
# --------------------------------------------------------------------------
def _state_path(cfg: dict) -> str:
    return os.path.join(cache_dir(cfg), ".adt-sync-state.json")


# --------------------------------------------------------------------------
# The pass lock (ADT-112)
# --------------------------------------------------------------------------
# Two passes running at once against one cache both read `issue_number: null`
# before either has written back, so both take the CREATE branch in
# reconcile_one and one ticket becomes two Issues. The losing Issue then has no
# cache file bound to its number, so the next pull reconstructs one and the
# duplicate becomes self-healing. An advisory flock serialises the passes: the
# loser skips its tick instead of corrupting the backlog.
#
# The double-create is the loudest race but not the only one, and the lock is
# sized to the PASS for all three: `_persist_state` rewrites the whole sidecar
# (last writer wins, so an overlapping pass silently discards the other's
# hashes), and the render writes the board with a plain write_text
# (build_kanban.py), so two concurrent renders can leave a truncated
# kanban.html. A finer lock around just the CREATE branch would leave both open.
#
# Scoped to the CACHE DIR, not the project — two projects sync concurrently by
# design and must not block each other, and the cache dir is what racing
# processes actually share. The lock file is invisible to everything that walks
# the cache (iter_cache_files and _cache_fingerprint both filter on `.md`), so
# it can neither be synced as a ticket nor retrigger the watcher.
#
# Costs zero `gh` calls: it is pure filesystem, so a converged tick's API budget
# is untouched. Crash-safety comes from the kernel — flock is released when the
# process's descriptors close, including on kill -9 — so there is no staleness
# timeout and no PID-liveness check to get wrong.
LOCK_FILENAME = ".adt-sync.lock"

_LOCK_WARNED = False


def lock_path(cfg: dict) -> str:
    return os.path.join(cache_dir(cfg), LOCK_FILENAME)


def _warn_lock_unavailable(detail: str) -> None:
    """Say ONCE per process that this pass is running without the lock.

    Once, not per tick, because the conditions that reach here are persistent
    (a filesystem that cannot lock does not start being able to mid-run) and
    ADT-119 keeps this log readable. On a machine where the lock works it never
    fires."""
    global _LOCK_WARNED
    if _LOCK_WARNED:
        return
    _LOCK_WARNED = True
    print(f"[adt-sync] pass lock unavailable ({detail}) — running UNLOCKED; "
          "concurrent passes are not serialised.", file=sys.stderr)


@contextlib.contextmanager
def pass_lock(cfg: dict):
    """Yield True if this process owns the pass, False if another holds it.

    **Every failure to take the lock fails OPEN** — yields True with a one-time
    warning — and only genuine contention yields False. That asymmetry is the
    whole safety property: an unavailable lock must never end up STRONGER than
    no lock at all. Read as contention, an `flock` that the filesystem does not
    support (EOPNOTSUPP on some network mounts) or that runs out of kernel locks
    (ENOLCK) would make every pass skip, for ever, silently — the board would
    simply stop syncing, which is a worse failure than the double-create this
    exists to prevent. So the contention branch is `BlockingIOError` (EAGAIN /
    EWOULDBLOCK) exactly, and every other OSError falls through to fail-open.

    The same rule covers the guarded `fcntl` import (a non-POSIX host) and a
    lock file that cannot be opened at all (an unwritable cache dir): warn,
    proceed unlocked, never raise out of the context manager's entry.
    """
    if fcntl is None:
        _warn_lock_unavailable("fcntl not available on this platform")
        yield True
        return
    path = lock_path(cfg)
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as e:
        _warn_lock_unavailable(f"cannot open {path}: {e}")
        yield True
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:          # the ONLY contention signal
            yield False
            return
        except OSError as e:             # unsupported / no locks left -> open
            _warn_lock_unavailable(f"flock failed on {path}: {e}")
            yield True
            return
        yield True
    finally:
        os.close(fd)          # closing the last fd releases the flock


# The sidecar's top-level schema — the ONE place the known keys are declared.
# A key here is recognised as "new format" by _load_state_doc; add new state
# (like the ADT-101 `pull` block) to this tuple, not to ad-hoc `in` checks.
_STATE_KEYS = ("file_hashes", "board_index", "pull", "project_ctx", "watch")


def _load_state_doc(cfg: dict) -> dict:
    """Whole sidecar: {"file_hashes": {path: hash}, "board_index": {num: {...}},
    "pull": {"watermark": iso-ts, "last_full_pull": epoch-s}}.
    Back-compat: an older flat {path: hash} file is read as file_hashes only.
    Empty/corrupt → empty doc (forces a full resync + board re-walk)."""
    try:
        with open(_state_path(cfg)) as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {"file_hashes": {}, "board_index": {}}
    if not isinstance(data, dict):
        return {"file_hashes": {}, "board_index": {}}
    if any(k in data for k in _STATE_KEYS):
        data.setdefault("file_hashes", {})
        data.setdefault("board_index", {})
        return data
    # Legacy flat {path: hash} → migrate in memory.
    return {"file_hashes": data, "board_index": {}}


def _save_state_doc(cfg: dict, doc: dict) -> None:
    """Best-effort atomic write — a failure here just means more work next tick,
    never a crash. temp+rename so a killed write can't corrupt the file."""
    try:
        tmp = _state_path(cfg) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f)
        os.replace(tmp, _state_path(cfg))
    except OSError:
        pass


def _update_state_doc(cfg: dict, updates: dict) -> None:
    """Read-modify-write the sidecar, setting ONLY the keys in `updates`. Every
    writer goes through this, so clobbering a key another writer owns (the
    ADT-101 failure: the push-side write erasing the pull watermark each tick)
    is structurally impossible, not a per-writer discipline."""
    doc = _load_state_doc(cfg)
    doc.update(updates)
    _save_state_doc(cfg, doc)


def _load_sync_state(cfg: dict) -> dict:
    """{abs_path: last_synced_content_hash} — the file-change map."""
    return _load_state_doc(cfg).get("file_hashes", {})


def _persist_state(cfg: dict, file_hashes: dict) -> None:
    """Write the sidecar: the file-change map + a snapshot of the in-process
    board index (issue_number -> {item_id, status}) so the next process skips
    the Projects board re-walk. Board-index keys are ints; JSON stringifies
    them, and _board_index() casts back on load.

    If this run never touched the board (all-noop → _board_index never called →
    in-process cache empty), KEEP the previously-persisted index rather than
    clobbering it with {} — otherwise an idle tick would discard the cache and
    force the next change to re-walk the board. Writes via _update_state_doc,
    so keys this writer doesn't own (the ADT-101 pull state) survive."""
    updates = {"file_hashes": file_hashes}
    key = f"{cfg.get('owner')}/{cfg.get('project_number')}"
    board = _BOARD_INDEX.get(key)
    if board:
        updates["board_index"] = {str(k): v for k, v in board.items()}
    # Same keep-don't-clobber rule as the board index: an idle tick never
    # resolved the ctx, so persist only what this run actually holds (ADT-147).
    if _PROJECT_CTX:
        updates["project_ctx"] = dict(_PROJECT_CTX)
    _update_state_doc(cfg, updates)


def _load_pull_state(cfg: dict) -> dict:
    """Pull-side sidecar state: {"watermark": iso-ts|None,
    "last_full_pull": epoch-s|None}. Missing/corrupt -> {} (forces a full
    sweep — degraded cost, never wrong data)."""
    pull = _load_state_doc(cfg).get("pull")
    return pull if isinstance(pull, dict) else {}


def _save_pull_state(cfg: dict, pull: dict) -> None:
    _update_state_doc(cfg, {"pull": pull})


def _push_hash(data: dict) -> str:
    """Change signal for the PUSH side: hash the ticket MINUS the pull-owned
    fields (ADT-147).

    WHY NOT THE RAW BYTES. `_PULL_OWNED` is written onto existing cache files by
    `pull_all`, and `updated` moves every time the Issue does — including when
    the Issue moved because ADT itself wrote a register comment. Hashing raw
    bytes put those fields inside the push's trigger, so a pull-only write
    re-armed the push, and the push is where the GraphQL spend lives
    (`_current_issue`, `_project_ctx`). That closed a loop:

        register upsert -> Issue.updated_at moves -> pull writes `updated`
        -> file hash moves -> push re-runs -> (writes) -> goto 1

    It is a branching process with factor R = N x f, where f is the chance a
    pulled change provokes a push write. Measured on the incident: f = 0.71,
    so N_crit = 1/f = 1.41 — ONE machine decays, TWO diverge, which is exactly
    what was observed (one watcher ran for weeks; the second install exhausted
    the account's whole GraphQL pool in 39 minutes).

    Excluding `_PULL_OWNED` sets **f = 0 by construction** — a pull-only change
    cannot reach an API call — so R = 0 for ANY machine count. That is the
    difference from per-machine self-echo suppression, which only removes a
    machine's OWN writes from its own pull and leaves R = (N-1) x f: subcritical
    at N=2, supercritical again at N=3.

    The fields stay IN the file (the board's recency sort reads `updated`, and
    ADT-139's staleness check needs it) — they are only outside the hash.

    Takes the already-parsed dict `iter_cache_files` yields, so this is also one
    fewer file read than the byte hash it replaces.
    """
    # The body is ~94% of a ticket and needs no JSON escaping to be hashed —
    # feeding it to sha1 directly, after the canonicalised frontmatter and a
    # domain separator, measured 7.2ms/tick over a 353-ticket board against
    # 18.8ms for json.dumps-everything (and 8.2ms for the byte hash this
    # replaced). This runs per ticket per tick, so it is the tick's floor cost.
    payload = {k: v for k, v in data.items()
               if k not in _PULL_OWNED and k != "body"}
    h = hashlib.sha1()
    h.update(json.dumps(payload, sort_keys=True, default=str,
                        ensure_ascii=False).encode("utf-8"))
    h.update(b"\x00")                       # separator: frontmatter | body
    h.update((data.get("body") or "").encode("utf-8"))
    return h.hexdigest()


# --------------------------------------------------------------------------
# Label diffing.
# --------------------------------------------------------------------------
def _desired_labels(issue: dict) -> set:
    return {l["name"] for l in issue.get("labels", []) if l.get("name")}


# Cache of label names known to exist in the repo (per process), so we don't
# re-query/re-create on every ticket.
_KNOWN_LABELS: dict = {}


def _ensure_labels_exist(repo: str, names: set) -> None:
    """Create any label that doesn't exist yet. Tickets carry free-form tags
    (e.g. `launchd`, `incident`) beyond the bootstrap taxonomy; `gh issue
    create --label X` hard-fails if X is absent, so create-on-demand. Idempotent
    (`--force` upserts). Caches per process."""
    if repo not in _KNOWN_LABELS:
        existing = _gh_json(["label", "list", "--repo", repo, "--limit", "500",
                             "--json", "name"]) or []
        _KNOWN_LABELS[repo] = {l["name"] for l in existing}
    for name in names:
        if name not in _KNOWN_LABELS[repo]:
            try:
                _gh(["label", "create", name, "--repo", repo,
                     "--color", "ededed", "--force"])
            except GhError:
                pass  # racing/exists — tolerate; the create/edit will surface
            _KNOWN_LABELS[repo].add(name)


def _current_issue(repo: str, number: int) -> dict:
    """The per-dirty-ticket read, on REST (ADT-147).

    This is the hot one: it runs for EVERY ticket the push finds dirty. As
    `gh issue view --json` it billed the 5,000-point GraphQL pool by query
    complexity; `GET /repos/{repo}/issues/{n}` is one flat request from the
    separate core pool and returns the same fields. The shape comes back
    through `_issue_from_rest` so the serializer's field-map stays the single
    place Issue <-> ticket is defined (ADT-101 built that adapter for the list
    path; this reuses it rather than adding a second mapping)."""
    item = _gh_json(["api", f"repos/{repo}/issues/{number}"]) or {}
    return _issue_from_rest(item) if item else {}


# --------------------------------------------------------------------------
# Standard GitHub Projects board: add each Issue + set its Status column.
# This is what makes the Project board the kanban (Issues otherwise sit off
# the board). Rebuilt clean from the first attempt's working approach:
#   - item-add takes the issue URL (--url), idempotent server-side;
#   - the Status single-select is set via item-edit with the option ID;
#   - a per-run board index avoids an item-list call per ticket;
#   - column moves are skipped when already correct (so re-runs stay noop).
# All board work is a no-op if the config has no project_number (board sync is
# optional — labels still carry the stage).
# --------------------------------------------------------------------------
_PROJECT_CTX: dict = {}

# How long a persisted Projects context is trusted before it is re-walked
# (ADT-147). An hour matches the pull's `last_full_pull` sweep cadence and costs
# 2 GraphQL calls per hour instead of 2 per tick — ~98% of the saving, while
# leaving no entry that is never re-verified.
PROJECT_CTX_TTL = 3600.0


def _invalidate_board_caches(cfg: dict, issue_number: int | None = None) -> None:
    """Drop BOTH persisted board caches for a failed write, so the next call
    re-derives instead of failing identically forever.

    Two caches feed a board write and either can be the stale one:
      * `project_ctx` — the field/option ids. No idempotent repair exists, which
        is why it also carries a TTL.
      * `board_index` — the item_id. `ensure_on_board` self-heals this ONLY when
        the issue is absent from the index (`cur is None` -> idempotent
        `item-add`). A present-but-stale entry — a card removed from the board by
        hand — makes `cur` non-None, so the repair never runs and that ticket
        fails every tick.
    Invalidating only the context would leave that second case broken AND spend
    two GraphQL calls per tick re-walking a context that was fine — the per-tick
    cost this ticket exists to remove, burnt exactly when the pool is stressed.
    So one helper covers both, and it drops a single index entry rather than the
    whole index: the other few hundred are almost certainly still good."""
    key = f"{cfg.get('owner')}/{cfg.get('project_number')}"
    _PROJECT_CTX.pop(key, None)
    if issue_number is not None:
        _BOARD_INDEX.get(key, {}).pop(int(issue_number), None)
    try:
        doc = _load_state_doc(cfg)
        updates = {}
        ctx_doc = doc.get("project_ctx", {}) or {}
        if key in ctx_doc:
            ctx_doc.pop(key, None)
            updates["project_ctx"] = ctx_doc
        if issue_number is not None:
            idx_doc = doc.get("board_index", {}) or {}
            if str(issue_number) in idx_doc:
                idx_doc.pop(str(issue_number), None)
                updates["board_index"] = idx_doc
        if updates:
            _update_state_doc(cfg, updates)
    except Exception:  # noqa: BLE001 - invalidation must never break the pass
        pass
_BOARD_INDEX: dict = {}


def _project_ctx(cfg: dict):
    """Cache + return {project_id, status_field_id, options{name:id}} for the
    board, or None if no project_number configured."""
    number, owner = cfg.get("project_number"), cfg.get("owner")
    if not number or not owner:
        return None
    key = f"{owner}/{number}"
    if key not in _PROJECT_CTX:
        # Seed from the sidecar first (ADT-147). Every launchd tick is a fresh
        # `--once` process, so an in-process-only cache is cold EVERY time: two
        # GraphQL calls (`project view` + `field-list`) on any tick that carries
        # a dirty file. `_board_index` already persists for exactly this reason.
        #
        # BUT NOT ON `_board_index`'s TERMS. That index self-heals because its
        # staleness is *membership* drift, and `ensure_on_board` repairs it with
        # an idempotent `item-add`. There is no equivalent for FIELD/OPTION-ID
        # drift: delete and recreate the board's Status field — routine board
        # admin — and a persisted status_field_id is wrong forever, because
        # nothing here would ever re-walk. Caching it in-process only was
        # accidentally safe: every tick was a fresh process, so it was always
        # refetched. Persisting it trades "always fresh" for "never verified"
        # unless something re-checks, so two things do:
        #   * this TTL, which bounds the SILENT failure (a wrong option id makes
        #     _set_status a no-op that logs nothing), and
        #   * _invalidate_project_ctx on a board-write error, which recovers
        #     from the LOUD one on the very next tick.
        persisted = _load_state_doc(cfg).get("project_ctx", {}).get(key)
        if (persisted and persisted.get("project_id")
                and (time.time() - float(persisted.get("fetched_at") or 0)
                     < PROJECT_CTX_TTL)):
            _PROJECT_CTX[key] = persisted
            return _PROJECT_CTX[key]
        proj = _gh_json(["project", "view", str(number), "--owner", owner,
                         "--format", "json"]) or {}
        fields = _gh_json(["project", "field-list", str(number), "--owner",
                           owner, "--format", "json"]) or {"fields": []}
        status = next((f for f in fields["fields"]
                       if f.get("name") == "Status"), None)
        _PROJECT_CTX[key] = {
            "fetched_at": time.time(),      # ADT-147: drives PROJECT_CTX_TTL
            "project_id": proj.get("id"),
            "status_field_id": status.get("id") if status else None,
            "options": {o["name"]: o["id"]
                        for o in (status.get("options", []) if status else [])},
        }
    return _PROJECT_CTX[key]


def _board_index(cfg: dict) -> dict:
    """issue_number -> {item_id, status}. Cached per process AND persisted across
    processes in the sync-state sidecar, so a fresh `--once` run doesn't re-walk
    the whole Projects board (a multi-page GraphQL query — the cold-start cost
    that, in the old crash-loop, was paid every few seconds). Resolution order:
    in-process cache → persisted doc → live `item-list` walk (last resort).
    Item ids + columns are stable, and ensure_on_board self-heals any miss
    (item-add is idempotent), so a stale persisted index is safe."""
    key = f"{cfg.get('owner')}/{cfg.get('project_number')}"
    if key not in _BOARD_INDEX:
        # Seed from the persisted doc (JSON keys are strings → back to int).
        persisted = _load_state_doc(cfg).get("board_index", {})
        seeded = {}
        for k, v in persisted.items():
            try:
                seeded[int(k)] = v
            except (TypeError, ValueError):
                continue
        if seeded:
            _BOARD_INDEX[key] = seeded
        else:
            items = _gh_json(["project", "item-list", str(cfg["project_number"]),
                              "--owner", cfg["owner"], "--format", "json",
                              "--limit", "2000"]) or {"items": []}
            idx = {}
            for it in items["items"]:
                num = it.get("content", {}).get("number")
                if num is not None:
                    idx[num] = {"item_id": it["id"], "status": it.get("status")}
            _BOARD_INDEX[key] = idx
    return _BOARD_INDEX[key]


def _set_status(cfg, ctx, item_id: str, stage: str) -> None:
    opt = ctx["options"].get(stage)
    if opt and ctx["status_field_id"]:
        _gh(["project", "item-edit", "--id", item_id,
             "--project-id", ctx["project_id"],
             "--field-id", ctx["status_field_id"],
             "--single-select-option-id", opt])


def ensure_on_board(cfg: dict, issue_number: int, stage: str) -> str | None:
    """Add the Issue to the board (idempotent) + set its Status column to the
    stage, only moving the column when it differs. Returns a short change label
    ('added'/'moved->X') or None if no change / no board configured."""
    ctx = _project_ctx(cfg)
    if not ctx or not ctx.get("project_id") or not issue_number:
        return None
    idx = _board_index(cfg)
    try:
        return _board_write(cfg, ctx, idx, issue_number, stage)
    except RateLimitError:
        raise            # exhausted pool, not stale ids — keep the ctx
    except GhError:
        # The cached ids are the likeliest cause — either the field/option ids
        # or this issue's item_id — so drop both so the next tick re-derives
        # instead of failing identically forever (ADT-147).
        _invalidate_board_caches(cfg, issue_number)
        raise


def _board_write(cfg, ctx, idx, issue_number: int, stage: str) -> str | None:
    cur = idx.get(issue_number)
    if cur is None:
        url = f"https://github.com/{cfg['repo']}/issues/{issue_number}"
        added = _gh_json(["project", "item-add", str(cfg["project_number"]),
                          "--owner", cfg["owner"], "--url", url,
                          "--format", "json"])
        item_id = (added or {}).get("id")
        if not item_id:
            return None
        _set_status(cfg, ctx, item_id, stage)
        idx[issue_number] = {"item_id": item_id, "status": stage}
        return "added"
    if cur.get("status") != stage:
        _set_status(cfg, ctx, cur["item_id"], stage)
        cur["status"] = stage
        return f"moved->{stage}"
    return None


# --------------------------------------------------------------------------
# Reconcile one ticket.
# --------------------------------------------------------------------------
def _body_differs(current_body, desired_body) -> bool:
    """True when the Issue body needs a push, IGNORING the slug trailer.

    ADT-322. The trailer was designed to ride a body push that was happening
    anyway (ticket_serializer._SLUG_TRAILER). Comparing raw bodies made it
    *initiate* one instead, in both directions:

      - ADOPT. A pre-existing Issue carries no trailer and the desired body
        does, so every body in the backlog differed and every one was
        rewritten. 353 of a consumer's 360 Issues had `updatedAt` stamped
        with the install time, which GitHub owns and no API can set back.
      - STRIP. A ticket whose cache file has no usable `slug:` produces a
        desired body with NO trailer (_with_slug_trailer returns the base),
        while the Issue still carries one -> the push removed it. That is the
        08:14:50 strip in that consumer's Issue #1 edit history, and it
        re-armed the adopt rewrite for the next install.

    Comparing with the trailer off both sides makes a trailer-only difference
    a no-op, which is what "rides an existing push" was always supposed to
    mean. A real content change still pushes, and the desired body carries the
    trailer when it goes.
    """
    return (_strip_slug_trailer(current_body or "")
            != _strip_slug_trailer(desired_body or ""))


def reconcile_one(path: str, data: dict, cfg: dict, dry_run: bool = False) -> dict:
    """Reconcile a single cache .md to its Issue. Returns an action summary.
    Writes issue_number/issue_node_id back into the .md when an Issue is
    created. Idempotent: a converged ticket produces {'action': 'noop'}."""
    repo = cfg["repo"]
    issue = to_issue(data)
    # A title is mandatory for `gh issue create/edit` — never pass None. Fall
    # back to the slug for the rare ticket with neither a `title:` nor a body
    # H1 (2 such in one consumer's backlog). Normalise once so create AND update
    # are both safe.
    if not issue.get("title"):
        issue["title"] = data.get("slug") or "untitled"
    number = data.get("issue_number")
    actions = {"path": path, "slug": data.get("slug"), "action": None}

    if not number:
        # CREATE: no Issue yet.
        if dry_run:
            actions["action"] = "would-create"
            return actions
        want = _desired_labels(issue)
        _ensure_labels_exist(repo, want)
        label_args = []
        for name in want:
            label_args += ["--label", name]
        out = _gh([
            "issue", "create", "--repo", repo,
            "--title", issue["title"] or data.get("slug", "untitled"),
            "--body", issue.get("body", "") or "(no body)",
            *label_args,
        ])
        url = out.strip().splitlines()[-1]
        new_number = int(url.rstrip("/").split("/")[-1])
        # The node_id lookup, on REST (ADT-147): the flat issue object carries
        # `node_id` directly, so this needs no GraphQL and no adapter.
        node = _gh_json(["api", f"repos/{repo}/issues/{new_number}"])
        data["issue_number"] = new_number
        data["issue_node_id"] = node.get("node_id") if node else None
        # ADT-9: the issue number IS the ticket id. Derive it here at the adopt
        # point so a ticket filed as issue #31 becomes <PREFIX>-31 — no local
        # counter, no virgin-repo requirement. Only set when absent so an
        # already-stamped id (e.g. set synchronously at feature-brief) is kept.
        if not data.get("id"):
            data["id"] = f'{cfg.get("id_prefix", "TIX")}-{new_number:03d}'
        _write_back(path, data)
        ensure_on_board(cfg, new_number, data.get("stage") or "ideas")
        # gh creates issues OPEN; honour a desired closed state (done/cancelled).
        if (issue.get("state") or "open").lower() == "closed":
            api_reason = issue.get("stateReason") or "completed"
            cli_reason = {"not_planned": "not planned",
                          "completed": "completed",
                          "duplicate": "duplicate"}.get(api_reason, "completed")
            _gh(["issue", "close", str(new_number), "--repo", repo,
                 "--reason", cli_reason])
        actions["action"] = "created"
        actions["number"] = new_number
        # A fresh Issue got its stage label at creation — same lifecycle
        # event as a stage push for the token checkpoint (ADT-100).
        actions["stage_label_changed"] = True
        return actions

    # UPDATE: Issue exists — diff and patch only what drifted.
    current = _current_issue(repo, int(number))
    if not current:
        actions["action"] = "error-missing-issue"
        return actions
    if _is_archive(current):
        # A cache file exists for the archive (built before it was labelled).
        # Pushing it would strip the label, since the push removes every label
        # the file lacks, and put the archive back on the board.
        actions["action"] = "noop"
        return actions

    want_labels = _desired_labels(issue)
    have_labels = _desired_labels(current)
    add = want_labels - have_labels
    if add:
        _ensure_labels_exist(repo, add)
    remove = have_labels - want_labels
    changed = []

    if dry_run:
        if add or remove:
            changed.append(f"labels +{sorted(add)} -{sorted(remove)}")
        if (current.get("title") or "") != (issue.get("title") or ""):
            changed.append("title")
        if _body_differs(current.get("body"), issue.get("body")):
            changed.append("body")
        want_state = (issue.get("state") or "open").lower()
        if (current.get("state") or "").lower() != want_state:
            changed.append(f"state->{want_state}")
        actions["action"] = "would-update" if changed else "noop"
        actions["changes"] = changed
        return actions

    edit_args = ["issue", "edit", str(number), "--repo", repo]
    if (current.get("title") or "") != (issue.get("title") or ""):
        edit_args += ["--title", issue["title"]]
        changed.append("title")
    if _body_differs(current.get("body"), issue.get("body")):
        edit_args += ["--body", issue.get("body", "")]
        changed.append("body")
    for name in add:
        edit_args += ["--add-label", name]
    for name in remove:
        edit_args += ["--remove-label", name]
    if add or remove:
        changed.append(f"labels +{len(add)}/-{len(remove)}")
        # ADT-100: a stage-label push is a lifecycle event — the token
        # checkpoint uses it as a trigger (see checkpoint_tokens).
        if any(n.startswith("stage:") for n in add):
            actions["stage_label_changed"] = True
    if len(edit_args) > 5:  # more than the base verb -> something to edit
        _gh(edit_args)

    # State (open/closed) is a separate gh verb.
    want_state = (issue.get("state") or "open").lower()
    if (current.get("state") or "").lower() != want_state:
        if want_state == "closed":
            # The API stateReason enum is completed/not_planned/reopened, but
            # `gh issue close --reason` wants the human form completed|"not
            # planned"|duplicate. Map it.
            api_reason = issue.get("stateReason") or "completed"
            cli_reason = {"not_planned": "not planned",
                          "completed": "completed",
                          "duplicate": "duplicate"}.get(api_reason, "completed")
            _gh(["issue", "close", str(number), "--repo", repo,
                 "--reason", cli_reason])
        else:
            _gh(["issue", "reopen", str(number), "--repo", repo])
        changed.append(f"state->{want_state}")

    # Board: ensure on the board + Status column matches the stage.
    board = ensure_on_board(cfg, int(number), data.get("stage") or "ideas")
    if board:
        changed.append(f"board:{board}")

    actions["action"] = "updated" if changed else "noop"
    actions["changes"] = changed
    return actions


def _write_back(path: str, data: dict) -> None:
    """Re-serialise the .md with updated GitHub-owned fields, preserving body."""
    with open(path, "w") as f:
        f.write(emit_md(data))


# --------------------------------------------------------------------------
# TOKEN CHECKPOINT (ADT-100): persist per-machine token subtotals to the Issue.
#
# The cost ledger (.adt/state/cost-ledger.log; named
# token-usage.log before ADT-115 / 5c) is gitignored,
# machine-local runtime state — it never leaves the machine that did the work.
# So a second ADT picking the Issue up from GitHub started from zero. This
# section maintains ONE Issue comment per (machine, ticket) — a cumulative
# register the machine alone writes:
#
#   <!-- adt:tokens machine=<id> total=<N> --> 🪙 N tokens ...
#
# Single line by contract: the frontmatter comment scalar emitter is
# single-line, and build_kanban greps the marker from raw cache text. Comments
# are the one Issue surface that is append-only and per-author partitionable —
# each machine PATCHes only its own comment by stored id, so no write races
# another machine's (the not-last-writer-wins requirement, structurally).
#
# Cursor: .adt/state/token-checkpoint/<TIX> holds
# "<checkpointed_sum>\t<comment_id>". The comment total always equals the
# cursor sum; the uncheckpointed ledger tail is (ledger_sum - cursor_sum).
# The cursor advances only AFTER a successful upsert, so a failed call retries
# the same delta next pass — exact, never double-counted.
#
# API frugality (the 5,000/hr pool is shared and has been exhausted by
# watchers before): a checkpoint fires only on a stage-label push, at done,
# or when the drift crosses CHECKPOINT_DRIFT — never per idle tick. The
# trigger test is purely local (ledger + cursor), so no-drift ticks cost 0
# API calls.
# --------------------------------------------------------------------------
_CHECKPOINT_LEAF = ("token-checkpoint",)
CHECKPOINT_DRIFT = 25_000

# ADT-115 / 5b: the STAMP checkpoint. Distinct from the token/cost REGISTERS
# above, and the distinction is the whole point:
#   register = live ledger, ONE COMMENT PER MACHINE, moves while work continues
#   stamp    = the frontmatter `cost_usd:` written once at close, ONE COMMENT
#              PER TICKET, machine-independent, and the number of record
# Before this, a closed ticket showed three different figures — a stale prose
# line in the release notes, a live per-machine register, and an authoritative
# frontmatter value that never reached GitHub at all. The stamp is what a
# reader should trust, so it says so in its own body.
_STAMP_LEAF = ("stamp-checkpoint",)

# The register contract (marker template + regex + machine id + canon id) is
# defined ONCE in build_kanban and imported lazily here — build_kanban is
# import-safe (constants only at module level), sits on the same sys.path
# entry, and is where the READERS live, so writer and parser can't drift
# apart (the ADT-58 "patched one site" class). The lazy wrappers keep this
# module importable without build_kanban's transitive constants.


def _canon_tix(tix: str) -> str:
    from build_kanban import canon_tix
    return canon_tix(tix)


def _machine_id() -> str:
    from build_kanban import machine_id
    return machine_id()


def _register_body(machine: str, total: int, micros=None,
                   tier: str = "measured") -> str:
    from build_kanban import register_body
    return register_body(machine, total, micros, tier)


def _ledger_costs(project_root: str) -> dict:
    """One priced ledger scan -> {canon_tix: {"micros", "tier"}} (ADT-115).
    Reuses the kanban's pricer with an explicit root, exactly as
    _ledger_totals reuses its parser — one implementation, two callers."""
    from build_kanban import load_cost_usage
    try:
        return load_cost_usage(project_root)
    except Exception:
        return {}   # fail-open: a pricing problem must not stall the sync


def _ledger_totals(project_root: str) -> dict:
    """One ledger scan → {canon_tix: input+output}. Reuses the kanban's
    parser (the single ledger parser in python) with an explicit root —
    NOT its _canonical_root() default, which resolves from the module's own
    globals, not this sync's project."""
    from build_kanban import load_token_usage
    return load_token_usage(project_root)


def _read_cursor(ck_dir: str, tix_canon: str) -> tuple:
    """-> (checkpointed_sum, comment_id or None)."""
    try:
        raw = open(os.path.join(ck_dir, tix_canon)).read().strip()
        parts = raw.split("\t")
        return int(parts[0]), (parts[1] if len(parts) > 1 and parts[1] else None)
    except (OSError, ValueError):
        return 0, None


def _write_cursor(ck_dir: str, tix_canon: str, total: int, comment_id) -> None:
    os.makedirs(ck_dir, exist_ok=True)
    with open(os.path.join(ck_dir, tix_canon), "w") as fh:
        fh.write(f"{total}\t{comment_id or ''}\n")


def _upsert_register(repo: str, number: int, machine: str, total: int,
                     comment_id, micros=None, tier: str = "measured") -> str | None:
    """Create or PATCH the machine's register comment; return its id.
    A 404 on PATCH (human deleted the comment) recreates it."""
    body = _register_body(machine, total, micros, tier)
    if comment_id:
        try:
            _gh(["api", "-X", "PATCH",
                 f"repos/{repo}/issues/comments/{comment_id}",
                 "-f", f"body={body}"])
            return str(comment_id)
        except GhError as e:
            # Non-404 (incl. RateLimitError, whose message has no "404")
            # re-raises; only a deleted comment falls through to recreate.
            if "404" not in str(e) and "Not Found" not in str(e):
                raise
    out = _gh_json(["api", "-X", "POST",
                    f"repos/{repo}/issues/{number}/comments",
                    "-f", f"body={body}"])
    return str(out["id"]) if out and out.get("id") is not None else None


def _stamp_marker(tix: str) -> str:
    """How every stamp comment for `tix` begins. The writer below and the
    lookup in checkpoint_stamp both use it, so the two cannot drift apart."""
    return f"<!-- adt:stamp tix={tix} "


def _stamp_body(tix: str, cost_usd, tier: str, tokens, estimator=None) -> str:
    """The stamp comment — machine-readable marker plus a human line."""
    cost_txt = ("not captured" if str(cost_usd) == "unattributed"
                else f"${float(cost_usd):,.2f}")
    approx = "" if tier == "measured" or cost_txt == "not captured" else "~"
    tok_txt = ("not captured" if str(tokens) == "unattributed"
               else f"{int(tokens):,} tokens")
    est = f" estimator={estimator}" if estimator else ""
    return (f"{_stamp_marker(tix)}cost_usd={cost_usd} tier={tier} "
            f"tokens={tokens}{est} -->\n"
            f"\U0001f4cc **Final cost of work: {approx}{cost_txt}** "
            f"({tier}) \u00b7 {tok_txt}\n\n"
            f"From the ticket's `cost_usd:` — this is the number of record. It "
            f"is seeded at close and RECOMPUTED for 7 days afterwards, because "
            f"the tokens spent closing land after the stamp is first written "
            f"(ADT-277), so expect it to settle rather than to be final the "
            f"moment it appears. The \U0001fa99/\U0001f4b5 register comments "
            f"are *live per-machine ledger* figures and keep moving for as long "
            f"as work continues; they are not the final cost. Priced at "
            f"Anthropic list rates (ADT-115).")


def checkpoint_stamp(project_root: str, cfg: dict) -> list:
    """Push each stamped ticket's `cost_usd:` to its Issue as ONE comment.

    Runs from the sync, not from a playbook step, so the number reaches GitHub
    whether or not whoever closed the ticket remembered to write it in prose.
    Idempotent: the payload is hashed into a checkpoint file, so an unchanged
    stamp costs zero API calls. Fail-open per ticket."""
    results = []
    if not is_adt_project(project_root):
        return results
    ck_dir = adt_state_dir(project_root, *_STAMP_LEAF)
    repo = cfg["repo"]
    for path, data in iter_cache_files(cfg):
        number, tix = data.get("issue_number"), data.get("id")
        cost = data.get("cost_usd")
        if not number or not tix or cost in (None, "", "None"):
            continue
        tix_canon = _canon_tix(str(tix))
        body = _stamp_body(tix_canon, cost,
                           str(data.get("cost_tier") or "measured"),
                           data.get("tokens", "unattributed"))
        # Hash the payload rather than the cost alone: a tier or token change
        # must also re-push, and an unchanged stamp must cost zero API calls.
        # (_read_cursor is not reused here — it coerces its first field to int,
        # and this checkpoint stores a hex digest.)
        digest = hashlib.sha256(body.encode()).hexdigest()[:16]
        prev_digest, comment_id = "", None
        try:
            raw = open(os.path.join(ck_dir, tix_canon)).read().strip().split("\t")
            prev_digest = raw[0]
            comment_id = raw[1] if len(raw) > 1 and raw[1] else None
        except OSError:
            pass
        if prev_digest == digest:
            continue                      # unchanged -> no API call
        try:
            new_id = _upsert_comment(repo, int(number), body, comment_id,
                                     marker=_stamp_marker(tix_canon))
            os.makedirs(ck_dir, exist_ok=True)
            with open(os.path.join(ck_dir, tix_canon), "w") as fh:
                fh.write(f"{digest}\t{new_id or ''}\n")
            results.append({"path": path, "slug": data.get("slug"),
                            "action": "cost-stamp", "tix": tix_canon,
                            "cost_usd": cost})
        except RateLimitError:
            raise
        except GhError as e:
            results.append({"path": path, "action": "error",
                            "error": f"cost-stamp: {e}"})
    return results


# --------------------------------------------------------------------------
# RESTAMP (ADT-277): a closed ticket's cost keeps converging.
#
# /adt-close stamps `cost_usd:` DURING the closing turn, so the tokens spent
# closing land at the Stop afterwards and can never be inside the number it
# wrote. Measured on ADT-264: stamped $87.14, ledger $89.44, the difference one
# row at 15:49:23 worth $2.29. Late subagent rows and a dead session's swept
# tail arrive after the stamp for the same reason.
#
# So the stamp is a seed rather than a final value. Each tick recomputes
# recently-closed tickets and rewrites the frontmatter when the figure has
# moved; checkpoint_stamp then re-pushes it because its body hash changed.
#
# API frugality, the same way checkpoint_tokens does it: the TRIGGER IS LOCAL.
# A per-ticket cursor holds the priced local ledger sum as at the last restamp,
# and a ticket whose local sum has not moved costs zero API calls. Only a moved
# sum shells out to adt-token-total.sh, which is the same cross-machine helper
# /adt-close calls — no combining rule is reimplemented here.
_RESTAMP_LEAF = ("restamp-cursor",)
RESTAMP_WINDOW_DAYS = 7

# adt-token-total.sh degrades to the LOCAL sum when gh is unavailable or the
# repo/issue cannot be resolved, announcing it on stderr. Writing that answer
# over a stamp that already carries other machines' spend would silently shrink
# the number, so the degrade notice means SKIP — not "write what we got".
_DEGRADE_NOTE = "printing LOCAL sum only"


def _token_total_script(project_root: str) -> str | None:
    """The installed per-project copy first, then the source tree — the same
    resolution order adt-token-sum.sh uses to find the pricer (ADT-090)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(project_root, ".claude", "hooks",
                              "adt-token-total.sh"),
                 os.path.join(here, os.pardir, "defaults", "hooks",
                              "adt-token-total.sh")):
        if os.path.isfile(cand):
            return cand
    return None


def _closed_within(data: dict, days: int, now=None) -> bool:
    """True when `closed:` is present and within the window. A ticket with no
    parseable close date is OUT: the window exists to stop old tickets being
    re-fetched forever, and an unreadable date must not opt one back in."""
    raw = str(data.get("closed") or "").strip()
    if not raw:
        return False
    try:
        ts = datetime.datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    now = now or datetime.datetime.utcnow()
    return (now - ts) <= datetime.timedelta(days=days)


def restamp_closed(project_root: str, cfg: dict, now=None) -> list:
    """Rewrite `cost_usd:`/`cost_tier:` on recently-closed tickets whose local
    ledger sum has moved since the last restamp. Fail-open per ticket."""
    results: list = []
    if not is_adt_project(project_root):
        return results
    script = _token_total_script(project_root)
    if not script:
        return results
    ck_dir = adt_state_dir(project_root, *_RESTAMP_LEAF)
    costs = _ledger_costs(project_root)
    for path, data in iter_cache_files(cfg):
        tix, cost = data.get("id"), data.get("cost_usd")
        if not tix or cost in (None, "", "None"):
            continue
        if str(data.get("stage") or "") != "done":
            continue
        if not _closed_within(data, RESTAMP_WINDOW_DAYS, now):
            continue
        tix_canon = _canon_tix(str(tix))
        local = (costs.get(tix_canon) or {}).get("micros")
        if local is None:
            continue
        prev, _ = _read_cursor(ck_dir, tix_canon)
        if prev == local:
            continue                      # local sum unmoved -> 0 API calls
        try:
            p = subprocess.run(["bash", script, tix_canon, project_root,
                                "--cost"],
                               capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as e:
            results.append({"path": path, "action": "error",
                            "error": f"restamp: {e}"})
            continue
        if _DEGRADE_NOTE in (p.stderr or ""):
            # Local-only answer. Leave the stamp alone AND leave the cursor
            # alone, so the next tick with gh working retries the same delta.
            results.append({"path": path, "action": "restamp-degraded",
                            "tix": tix_canon})
            continue
        parts = (p.stdout or "").strip().split("\t")
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        micros, tier = int(parts[0]), (parts[1] or "measured")
        new_cost = round(micros / 1_000_000.0, 2)
        # `tokens:` is written by the same helper at close, for the same reason,
        # and is rendered on the SAME line of the stamp comment. Correcting the
        # dollars and leaving the token count stale would put a fresh number and
        # a stale one side by side and give the reader no way to tell which is
        # which, so both move together or neither does.
        new_tokens = None
        try:
            q = subprocess.run(["bash", script, tix_canon, project_root],
                               capture_output=True, text=True, timeout=120)
            if _DEGRADE_NOTE not in (q.stderr or ""):
                cand = (q.stdout or "").strip()
                if cand.isdigit() or cand == "unattributed":
                    new_tokens = cand
        except (OSError, subprocess.SubprocessError):
            new_tokens = None
        try:
            same = abs(float(cost) - new_cost) < 0.005
        except (TypeError, ValueError):
            same = False                  # e.g. `unattributed` -> always write
        if same and str(data.get("cost_tier") or "") == tier:
            _write_cursor(ck_dir, tix_canon, local, None)
            continue
        data["cost_usd"] = new_cost
        data["cost_tier"] = tier
        if new_tokens is not None:
            data["tokens"] = int(new_tokens) if new_tokens.isdigit() else new_tokens
        _write_back(path, data)
        _write_cursor(ck_dir, tix_canon, local, None)
        results.append({"path": path, "slug": data.get("slug"),
                        "action": "restamp", "tix": tix_canon,
                        "cost_usd": new_cost, "was": cost})
    return results


def _find_comment(repo: str, number: int, marker: str) -> str | None:
    """Id of the first comment on the Issue whose body starts with `marker`."""
    page = 1
    while True:
        batch = _gh_json(["api", f"repos/{repo}/issues/{number}/comments"
                          f"?per_page={_REST_PAGE_SIZE}&page={page}"]) or []
        if not isinstance(batch, list):
            return None                   # an error object, not a page
        for c in batch:
            if (c.get("body") or "").startswith(marker):
                return str(c["id"])
        if len(batch) < _REST_PAGE_SIZE:
            return None
        page += 1


def _upsert_comment(repo: str, number: int, body: str, comment_id,
                    marker: str | None = None) -> str | None:
    """PATCH an existing comment, else POST a new one. A 404 on PATCH (someone
    deleted it) falls through to a fresh POST.

    With no stored id, the comment starting with `marker` is looked up on the
    Issue first (ADT-354). The id lives in a checkpoint under `.adt/`, which
    uninstall deletes, so after a reinstall the stamp was on the Issue but its
    id was not, and a second copy was posted. `_upsert_register` has no lookup:
    its marker carries the install id, which a reinstall also replaces."""
    if not comment_id and marker:
        comment_id = _find_comment(repo, number, marker)
    if comment_id:
        try:
            _gh(["api", "-X", "PATCH",
                 f"repos/{repo}/issues/comments/{comment_id}",
                 "-f", f"body={body}"])
            return str(comment_id)
        except GhError as e:
            if "404" not in str(e) and "Not Found" not in str(e):
                raise
    out = _gh_json(["api", "-X", "POST",
                    f"repos/{repo}/issues/{number}/comments",
                    "-f", f"body={body}"])
    return str(out["id"]) if out and out.get("id") is not None else None


def checkpoint_tokens(project_root: str, cfg: dict,
                      stage_changed: set | None = None) -> list:
    """One checkpoint pass over the cache. `stage_changed` holds the cache
    paths whose Issue stage label this tick's push changed. Fail-open per
    ticket (GhError recorded, pass continues); RateLimitError propagates so
    the caller aborts the tick like the push loop does."""
    stage_changed = stage_changed or set()
    results = []
    # Neither marker under this root -> no ledger can exist; skip the
    # whole pass (mirrors the hooks' `[ -d root/.adt ] || exit 0`).
    if not is_adt_project(project_root):
        return results
    ck_dir = adt_state_dir(project_root, *_CHECKPOINT_LEAF)
    machine = _machine_id()
    repo = cfg["repo"]
    ledger_totals = _ledger_totals(project_root)   # ONE scan per pass
    ledger_costs = _ledger_costs(project_root)     # ONE priced scan per pass
    for path, data in iter_cache_files(cfg):
        number = data.get("issue_number")
        tix = data.get("id")
        if not number or not tix:
            continue
        tix_canon = _canon_tix(str(tix))
        ledger_total = ledger_totals.get(tix_canon, 0)
        cur_sum, comment_id = _read_cursor(ck_dir, tix_canon)
        tail = ledger_total - cur_sum
        if tail < 0:
            # Ledger pruned since the last checkpoint: realign the cursor to
            # the new (smaller) sum so future deltas are measured from it.
            # The register comment keeps the pre-prune total — cumulative.
            _write_cursor(ck_dir, tix_canon, ledger_total, comment_id)
            continue
        if tail == 0:
            continue
        stage = (data.get("stage") or "").strip().lower()
        if not (path in stage_changed or stage == "done"
                or tail >= CHECKPOINT_DRIFT):
            continue
        new_total = cur_sum + tail
        try:
            # ADT-115: the cost rides in a SECOND marker on the same comment,
            # so the pull half needs no change — the reconstruct pass already
            # copies whole comment bodies into the cache file, and both markers
            # travel together.
            cost = ledger_costs.get(tix_canon) or {}
            new_id = _upsert_register(repo, int(number), machine,
                                      new_total, comment_id,
                                      cost.get("micros"),
                                      cost.get("tier") or "measured")
            # Cursor advances only after the successful write (retry-exact).
            _write_cursor(ck_dir, tix_canon, new_total, new_id)
            results.append({"path": path, "slug": data.get("slug"),
                            "action": "token-checkpoint",
                            "machine": machine, "total": new_total})
        except RateLimitError:
            raise
        except GhError as e:
            results.append({"path": path, "action": "error",
                            "error": f"token-checkpoint: {e}"})
    return results


# --------------------------------------------------------------------------
# PULL: GitHub Issues -> cache.
#
# Conflict policy (the directions own DISJOINT field sets, so there is no
# two-writer conflict to resolve):
#   - PUSH owns cache-authoritative content: title, body, labels (priority/
#     track/stage/gates/tags), open/closed state. Push runs FIRST.
#   - PULL owns GitHub-authoritative fields only: issue_number, issue_node_id,
#     assignees, and (for a MISSING cache file) full reconstruction.
# So pull never overwrites content a session wrote locally; it only brings back
# what GitHub owns (who created/claimed it, the issue number) and rebuilds a
# cache file that doesn't exist yet (e.g. created on another machine).
# --------------------------------------------------------------------------
# GitHub-owned fields pull may write onto an EXISTING cache file.
# GitHub-owned fields the pull writes back onto EXISTING cache files (they are
# also the fields `_push_hash` excludes, so a pull-only write can never re-arm
# the push — see that docstring).
#
# `closed` joined them for the done lane's order: it used to be written only on
# the reconstruct branch, so a ticket whose cache file already existed never got
# one, and the board fell back to `updated` — which ADT-174 moved on every
# ticket at once (a slug trailer on every Issue body), floating June's done
# tickets to the top of the lane. `closed` is set by the close and by nothing
# else, so pulling it gives the lane a date no later edit can move.
_PULL_OWNED = ("issue_number", "issue_node_id", "assignees", "updated",
               "closed")

# Status-label -> cache subfolder. type is read from a `type:` field/label or
# defaults to tasks; status from the stage: label.
def _summarise_no_type(numbers: list) -> str:
    """One line for a whole pull's worth of type-less Issues (ADT-135).

    Adopting an existing repo printed the old per-issue WARN once per Issue —
    40 identical lines that named no remedy. Split out as a helper so the
    message is testable without a `gh` round trip."""
    shown = ", ".join("#" + str(n) for n in numbers[:10])
    more = " …" if len(numbers) > 10 else ""
    # ADT-155: the previous text promised the one thing that cannot happen — "a
    # later pull buckets it correctly". Nothing relocates a ticket between type
    # folders; _cache_path_for runs only on the reconstruct branch below, for an
    # Issue with no local file. The board no longer asks the folder, so the
    # remedy is a frontmatter edit that takes effect on the next render, and the
    # file stays where it is. Say that, because an operator who follows the old
    # sentence edits 300 files and sees nothing change.
    return (f"[adt-sync] WARN {len(numbers)} issue(s) had no `type:` label — "
            f"bucketed as tasks: {shown}{more}. "
            f"Remedy: set `type:` in each ticket's cache frontmatter — the board "
            f"reads that field, so the next render buckets the card by it and "
            f"the file stays where it is. The next push also writes a "
            f"type:<value> label, so GitHub and any rebuilt cache agree.")


def _cache_path_for(cfg: dict, data: dict) -> str:
    base = cache_dir(cfg)
    # ADT-73 made the `or "tasks"` fallback VISIBLE so a mis-bucketed backlog
    # could not hide. ADT-135 keeps the visibility but not the volume: adopting
    # an existing repo fired this once per Issue (40 identical lines, no
    # remedy). The caller now counts the no-type adoptions and prints one
    # summary line for the pull; this stays a pure path helper.
    typ = (data.get("type") or "tasks")
    typ = typ + "s" if typ in ("bug", "enhancement", "task") else typ
    stage = data.get("stage") or "ideas"
    slug = data.get("slug") or f"issue-{data.get('issue_number')}"
    return os.path.join(base, typ, stage, f"{slug}.md")


# --------------------------------------------------------------------------
# Pull-side API discipline (ADT-101). The pull used to run a GraphQL
# `gh issue list` fetching every Issue's full body + nested label/assignee
# connections every tick — across two watch agents that drained the whole
# 5000-point GraphQL pool each hour (the push side got change-detection after
# the first runaway; the pull side never did). The pull now asks REST "what
# changed since the watermark?" — billed per REQUEST from the separate core
# pool, so a converged board costs ~1 near-empty request per tick and zero
# GraphQL points. A full sweep (no `since`) runs only when the watermark is
# missing/corrupt or LAST_FULL is older than _FULL_SWEEP_INTERVAL — the sweep
# is what still reconstructs a locally-deleted cache file whose Issue hasn't
# updated, which an incremental pull can never see.
# --------------------------------------------------------------------------
_FULL_SWEEP_INTERVAL = 3600.0  # s between watermark-bypassing full pulls
_REST_PAGE_SIZE = 100


def _issue_from_rest(item: dict) -> dict:
    """Map one REST /issues item onto the camelCase shape from_issue()
    consumes (the field names the old gh --json list produced), so the
    serializer field-map stays the single source of truth. REST's
    state/state_reason values are already the cache's lowercase convention;
    labels/assignees/milestone/user sub-shapes match what from_issue reads."""
    return {
        "number": item.get("number"),
        "id": item.get("node_id"),
        "title": item.get("title"),
        "body": item.get("body") or "",
        "state": item.get("state"),
        "stateReason": item.get("state_reason"),
        "labels": item.get("labels") or [],
        "assignees": item.get("assignees") or [],
        "milestone": item.get("milestone"),
        "createdAt": item.get("created_at"),
        "updatedAt": item.get("updated_at"),
        "closedAt": item.get("closed_at"),
        "author": item.get("user"),
    }


def _comments_from_rest(items: list) -> list:
    """Map `GET /repos/{repo}/issues/{n}/comments` onto the shape `from_issue`
    reads (ADT-147).

    Deliberately NOT `_issue_from_rest`: that adapter maps a flat ISSUE, and
    this is a different endpoint returning a comments array. REST spells the
    author `user` and the timestamp `created_at`; from_issue reads `author.login`
    and `createdAt` (the old GraphQL spelling). One small map here keeps the
    serializer untouched."""
    if not isinstance(items, list):
        return []          # gh api error object / empty body -> no comments
    return [
        {"author": {"login": ((c.get("user") or {}).get("login"))},
         "createdAt": c.get("created_at"),
         "body": c.get("body")}
        for c in items
    ]


def _list_issues_rest(repo: str, since: str | None = None) -> list:
    """Issues (open+closed) changed since `since` (None -> everything), via
    REST. Manual page loop rather than `gh api --paginate`: --paginate emits
    CONCATENATED json arrays, which _gh_json's single json.loads can't parse
    (and --slurp is gh-version-dependent). PRs share the /issues endpoint;
    drop them by their `pull_request` marker."""
    issues, page = [], 1
    while True:
        url = (f"repos/{repo}/issues?state=all"
               f"&per_page={_REST_PAGE_SIZE}&page={page}")
        if since:
            url += f"&since={since}"
        batch = _gh_json(["api", url]) or []
        issues.extend(_issue_from_rest(it) for it in batch
                      if "pull_request" not in it)
        if len(batch) < _REST_PAGE_SIZE:
            return issues
        page += 1


# --- Not reconstructing an Issue that is still being filed (ADT-116) --------
#
# Two independent guards, because they fail in different places. The CLAIM is
# the real mechanism: /adt-brief states "I am filing #N" and the pull believes
# it. The grace window is a backstop for the small hole the claim cannot cover.
#
# Why not the window alone: it does not observe anything, it guesses how long
# /adt-brief takes. Measured gaps on ADT-114/115 were ~85s, but the flow can
# include an AskUserQuestion — a human-in-the-loop pause is unbounded, so no
# constant is safe. A tunable that silently rots is the wrong primary guard for
# a defect whose symptom is silent.
#
# Why not the claim alone: some Issues are created without one. Someone runs
# `gh issue create` by hand and then writes the cache file, or a session files
# into another project's repo, where claiming is wrong (git-workflow §D6). The
# window covers the first. For the second nothing on this side writes a cache
# file, so the window only delays the card.
#
# Precedent: adt-mark-tix.sh writes a `<session>.locked` claim that
# adt-usage-log.sh's prose-matching heuristic must not override. Same shape — an
# explicit statement of intent outranks an inference.

# How long a claimed Issue is honoured. The claim rides on the Issue itself, so
# it is not self-expiring the way a local file's mtime is — this bound is what
# guarantees an orphaned label can never strand a ticket that nothing rebuilds.
# Generous: the cost of honouring a stale claim is a delayed reconstruct, while
# the cost of expiring a live one is the duplicate this exists to prevent.
FILING_CLAIM_MAX_AGE_SECONDS = 3600

# Backstop window for Issues with no claim at all. Deliberately much shorter:
# it is the weaker signal, so it gets the smaller mandate. 120s covers the ~85s
# gap measured above. It was 300s when /adt-brief added its claim after the
# create call. The claim now rides on the create call, so the window no longer
# guards /adt-brief, and every unclaimed card was waiting 5 minutes for nothing.
RECONSTRUCT_GRACE_SECONDS = 120


def _is_archive(issue: dict) -> bool:
    """True for an Issue that is deliberately not a ticket (NON_TICKET_LABELS):
    uninstall's telemetry archive (ADT-354) or the machine-report Issue
    (ADT-384). Without this the pull rebuilt a cache file for it, and the push
    then put it on the board."""
    return bool(NON_TICKET_LABELS & _desired_labels(issue))


def _has_filing_claim(issue: dict, now: float | None = None) -> bool:
    """True when this Issue says, on itself, that /adt-brief is still writing
    its cache file — the FILING_LABEL applied atomically by `gh issue create`.

    On the Issue rather than on disk (ADT-116) so it is visible to every
    machine: a watch running on another host sees the same claim, which a
    machine-local marker could never give us.

    Bounded by age regardless: if a filing dies and the push that would
    auto-release the label never runs, the claim expires rather than blocking
    reconstruction forever. Fail-open in both directions — no label, no
    timestamp, or a malformed one all return False and reconstruct as before."""
    names = {lbl.get("name", "") for lbl in (issue.get("labels") or [])}
    if FILING_LABEL not in names:
        return False
    return _created_within_grace(issue.get("createdAt"), now=now,
                                 window=FILING_CLAIM_MAX_AGE_SECONDS)


def _created_within_grace(created_at: str | None,
                          now: float | None = None,
                          window: int | None = None) -> bool:
    """True when `created_at` (GitHub's ISO8601-Z) is inside the reconstruct
    grace window — i.e. this Issue is probably mid-filing, so leave it alone.

    FAIL-OPEN toward the pre-ADT-116 behaviour: a missing, malformed, or
    future-dated timestamp returns False, so the Issue is reconstructed exactly
    as before. The failure we must never introduce is an Issue that is silently
    NEVER reconstructed; a duplicate stub is recoverable, a missing ticket that
    nothing ever rebuilds is not."""
    if not created_at:
        return False
    try:
        ts = datetime.datetime.strptime(
            created_at, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=datetime.timezone.utc).timestamp()
    except (ValueError, TypeError):
        return False
    age = (time.time() if now is None else now) - ts
    # age < 0 (clock skew, or a future-dated timestamp) falls through to False.
    return 0 <= age < (RECONSTRUCT_GRACE_SECONDS if window is None else window)


def pull_all(cfg: dict) -> list:
    """Bring GitHub-owned fields back into the cache; reconstruct missing cache
    files. Returns action summaries. Idempotent.

    Incremental (ADT-101): fetch only Issues changed since the persisted
    watermark; full-sweep when the watermark is absent or the last sweep is
    older than _FULL_SWEEP_INTERVAL. `since` is inclusive, so the newest Issue
    re-appears every tick — accepted: the _PULL_OWNED comparison no-ops it,
    and the alternative (watermark+1s) could miss same-second updates."""
    repo = cfg["repo"]
    pull_state = _load_pull_state(cfg)
    pull_watermark = pull_state.get("watermark")
    last_full = pull_state.get("last_full_pull")
    full_sweep = (not pull_watermark or not isinstance(last_full, (int, float))
                  or (time.time() - last_full) >= _FULL_SWEEP_INTERVAL)
    issues = _list_issues_rest(repo,
                               since=None if full_sweep else pull_watermark)

    results = []
    no_type: list = []   # ADT-135: counted, summarised once below
    deferred_updated: list = []   # ADT-342: updatedAt of everything deferred
    if issues:
        # Index existing cache files by issue_number — only when the fetch
        # returned something. On the converged steady-state tick (issues == [])
        # this whole-cache walk + frontmatter parse would be the dominant
        # per-tick cost, spent feeding a loop with zero iterations.
        by_number = {}
        for path, data in iter_cache_files(cfg):
            n = data.get("issue_number")
            if n is not None:
                by_number[int(n)] = (path, data)

        for issue in issues:
            num = issue.get("number")
            if num in by_number:
                path, data = by_number[num]
                recovered = from_issue(issue, base={})  # GH view of the fields
                changed = False
                for k in _PULL_OWNED:
                    if data.get(k) != recovered.get(k):
                        data[k] = recovered.get(k)
                        changed = True
                if changed:
                    _write_back(path, data)
                    results.append({"action": "pulled", "number": num,
                                    "slug": data.get("slug")})
            else:
                # No local cache file for this Issue -> reconstruct it —
                # UNLESS the Issue was created moments ago (ADT-116).
                #
                # /adt-brief creates the Issue FIRST so the ticket id can be the
                # real issue number (ADT-9), then writes the cache file. A tick
                # landing in that gap sees an Issue with no local file and
                # reconstructs a stub at <type>/ideas/issue-<N>.md — and then
                # the playbook writes the authored file, leaving TWO cache files
                # for one Issue and two cards on the board. #99/#101/#114/#115
                # were all filed that way before it was noticed.
                #
                # The reconstruct branch itself is right: it is how a fresh
                # clone, or an Issue filed on github.com, gets a cache file.
                # What it cannot tell apart is "no local file because external"
                # from "no local file YET because we are mid-filing". Two
                # discriminators, strongest first: an explicit filing claim from
                # /adt-brief, then the Issue's age as a backstop. See the
                # constants above for why neither alone is sufficient.
                #
                # Deferring, not dropping: once the window passes, the next tick
                # reconstructs as before. So a /adt-brief that dies between the
                # two steps still recovers — a few minutes later instead of
                # immediately. That is the whole cost of the fix.
                if _is_archive(issue):
                    continue      # uninstall's telemetry archive, not a ticket
                if (_has_filing_claim(issue)
                        or _created_within_grace(issue.get("createdAt"))):
                    results.append({"action": "deferred", "number": num,
                                    "slug": f"issue-{num}"})
                    # ADT-342: remember WHEN, so the watermark below cannot
                    # advance past an Issue we are only postponing.
                    if issue.get("updatedAt"):
                        deferred_updated.append(issue["updatedAt"])
                    continue
                # ADT-100: neither the REST list nor its mapper carries the
                # comments, so fetch them once for the reconstructed file —
                # this is how machine-tagged token registers reach a fresh
                # clone's cache (and its kanban). One extra call per MISSING
                # file only, never per tick. The fetch (I/O) lives here; the
                # SHAPE stays in the serializer's field-map (from_issue), the
                # one module that maps Issue <-> ticket.
                try:
                    issue["comments"] = _comments_from_rest(
                        _gh_json(["api",
                                  f"repos/{repo}/issues/{num}/comments"]) or [])
                except RateLimitError:
                    raise
                except GhError:
                    pass  # fail-open: reconstruct without comments
                data = from_issue(issue, base={})
                # derive a slug if the Issue has none (title-prefixed [TIX-N]
                # form is handled by the migration; fall back to issue number).
                data.setdefault("slug", f"issue-{num}")
                if not data.get("type"):
                    no_type.append(num)
                path = _cache_path_for(cfg, data)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                _write_back(path, data)
                results.append({"action": "reconstructed", "number": num,
                                "slug": data.get("slug")})

    # Advance pull state only on a SUCCESSFUL pass — a RateLimitError/GhError
    # in the fetch above propagates before this line, leaving the watermark
    # untouched so nothing is skipped once the quota recovers. Skip the write
    # when nothing moved (the idle tick) — no point re-writing the sidecar.
    seen = [i.get("updatedAt") for i in issues if i.get("updatedAt")]
    if pull_watermark:
        seen.append(pull_watermark)
    advance = max(seen) if seen else None
    # ADT-342: never advance PAST an Issue this pass deferred. A deferral is a
    # postponement, but the Issue stays in `issues` and so fed this max() — and
    # its own updatedAt never moves while it sits untouched. One newer sibling
    # in the same batch was therefore enough to carry the watermark over it, and
    # `since=<watermark>` never returned it again; only the hourly full sweep
    # recovered it. #342 lost itself this way.
    #
    # Clamp, don't filter: dropping the deferred entries from `seen` is a no-op,
    # because the max comes from the NEWER sibling, not from the deferred Issue.
    #
    # Self-limiting: an Issue stops being deferred once the grace window and
    # any filing claim expire (see RECONSTRUCT_GRACE_SECONDS and
    # FILING_CLAIM_MAX_AGE_SECONDS), at which point it is reconstructed and the
    # clamp lifts. The cost until then is refetching from a pinned `since` —
    # the same window the hourly full sweep already fetches.
    if advance and deferred_updated:
        advance = min(advance, min(deferred_updated))
    new_pull = {"watermark": advance,
                "last_full_pull": time.time() if full_sweep else last_full}
    if new_pull != pull_state:
        _save_pull_state(cfg, new_pull)

    # ADT-135 defect 4: adopting an existing repo printed this once per Issue —
    # 40 identical warnings with no remedy. One line, with the count and the
    # fix, so the operator learns what to do instead of scrolling past it.
    if no_type:
        print(_summarise_no_type(no_type), file=sys.stderr)
    return results


# --------------------------------------------------------------------------
# Reconcile all (push, then optionally pull).
# --------------------------------------------------------------------------
def reconcile_all(project_root: str, dry_run: bool = False,
                  pull: bool = False) -> list:
    """One full pass: push the cache to Issues, then optionally pull back.

    ADT-112 invariant: every entrypoint that reaches a non-dry create must hold
    `pass_lock` (see the pass-lock note above for what it protects). Today that
    is `main()` below, `adt_watch.watch()`'s tick, and `adt_migrate_ordered`'s
    CLI — the last of which reaches the CREATE branch through `reconcile_one`
    directly, not through this function, which is why the invariant is stated
    about ENTRYPOINTS and not about this function's own callers.
    """
    cfg = load_config(project_root)
    results = []
    # Per-file content hashes from the last successful sync. A ticket whose .md
    # is byte-identical to last time is already converged on the push side, so
    # we skip it WITHOUT the per-ticket issue fetch — collapsing a converged
    # 226-ticket board from 226 API calls/tick to ~0. (dry_run still visits
    # everything: it makes no writes and the caller wants the full diff.)
    state = {} if dry_run else _load_sync_state(cfg)
    new_state = dict(state)

    # Two cache files for one Issue (ADT-354): whichever is pushed last wins,
    # and a stale stub that says `state: open` reopens a closed Issue and moves
    # its card back. Push neither and name both, so a human picks the real one.
    files = list(iter_cache_files(cfg))
    per_number = collections.Counter(int(d["issue_number"]) for _, d in files
                                     if d.get("issue_number"))

    # PUSH first (cache -> Issues): cache is authoritative for content/stage.
    for path, data in files:
        n = data.get("issue_number")
        if n and per_number[int(n)] > 1:
            results.append({"path": path, "action": "duplicate",
                            "number": int(n)})
            continue
        h = _push_hash(data)
        if not dry_run and h and state.get(path) == h:
            results.append({"path": path, "slug": data.get("slug"),
                            "action": "noop"})
            continue
        try:
            r = reconcile_one(path, data, cfg, dry_run=dry_run)
            results.append(r)
            # Record the POST-reconcile hash by RE-PARSING the file:
            # reconcile_one may have written issue_number back, and that field
            # is pull-owned, so the re-read is what lets convergence land on the
            # same value the next tick computes from disk (ADT-147).
            if not dry_run and r.get("action") != "error":
                try:
                    new_state[path] = _push_hash(parse_md(open(path).read()))
                except OSError:
                    new_state[path] = h
        except RateLimitError as e:
            # The whole pool is exhausted — every remaining call would fail too.
            # Stop the pass now (don't record 226 errors and retry them next
            # tick); the unsaved hashes mean we simply re-try these files once
            # the limit resets. This is the backoff that ends the runaway.
            results.append({"path": path, "action": "rate-limited",
                            "error": str(e)})
            _persist_state(cfg, new_state)
            return results
        except GhError as e:
            results.append({"path": path, "action": "error", "error": str(e)})
            new_state.pop(path, None)  # force a retry next tick

    # TOKEN CHECKPOINT (ADT-100): after the push pass, persist any per-machine
    # token drift to the Issues as register comments. Runs OUTSIDE the
    # hash-skip above on purpose — tokens accrue in the gitignored ledger
    # without touching the .md, so a byte-identical cache file can still owe a
    # checkpoint. The trigger test is local; idle ticks cost 0 API calls.
    if not dry_run:
        stage_flags = {r.get("path") for r in results
                       if r.get("stage_label_changed")}
        try:
            results.extend(checkpoint_tokens(project_root, cfg, stage_flags))
            # ADT-277: correct the stamp BEFORE pushing it. restamp_closed
            # rewrites `cost_usd:` in the cache file; checkpoint_stamp hashes
            # that value into its comment body, so running it first means one
            # tick both corrects and publishes rather than two.
            results.extend(restamp_closed(project_root, cfg))
            results.extend(checkpoint_stamp(project_root, cfg))
        except RateLimitError as e:
            results.append({"action": "rate-limited",
                            "error": f"token-checkpoint: {e}"})

    # PULL second (Issues -> cache): GitHub-owned fields + missing files.
    if pull and not dry_run:
        try:
            results.extend(pull_all(cfg))
        except RateLimitError as e:
            results.append({"action": "rate-limited", "error": f"pull: {e}"})
        except GhError as e:
            results.append({"action": "error", "error": f"pull: {e}"})

    if not dry_run:
        _persist_state(cfg, new_state)
    return results


def summarise_adopt(results: list) -> str:
    """The human line for an adopt/--pull pass (ADT-174).

    `summarise()` prints machine counts — `deferred=1` — and adt-install.sh used
    to follow them with a static, unconditional `pulled existing issues` tick
    composed from nothing the pull returned. The two lines together read as
    unqualified success while a ticket was in fact missing from the cache, which
    is exactly what a newcomer hits: file a ticket, then set up a second machine
    or reinstall, and the board comes up empty under a green tick.

    `deferred` is not an error and not a loss — RECONSTRUCT_GRACE_SECONDS exists
    so a tick landing between `gh issue create` and the cache file being written
    cannot reconstruct a duplicate stub (ADT-116). It just needs saying, in
    words, with the fact that the next sync collects them."""
    adopted = sum(1 for r in results if r.get("action") == "reconstructed")
    deferred = sum(1 for r in results if r.get("action") == "deferred")
    line = "[adt-sync] adopted %d issue(s)" % adopted
    if deferred:
        line += (" · %d deferred (created in the last %d min, so a /adt-brief "
                 "still writing its cache file is not raced) — the next sync "
                 "picks them up" % (deferred, RECONSTRUCT_GRACE_SECONDS // 60))
    return line


def summarise(results: list) -> str:
    by = {}
    for r in results:
        by[r["action"]] = by.get(r["action"], 0) + 1
    return ", ".join(f"{k}={v}" for k, v in sorted(by.items())) or "nothing"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Reconcile .md cache <-> Issues.")
    ap.add_argument("--root", default=os.getcwd(), help="project root")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pull", action="store_true",
                    help="also pull GitHub-owned fields + reconstruct missing "
                         "cache files (Issues -> cache).")
    args = ap.parse_args(argv)

    if args.dry_run:
        # A dry run takes no lock, on purpose. It cannot corrupt the backlog:
        # reconcile_one returns "would-create" without calling `gh issue
        # create`, and the state sidecar is never persisted. Locking it would
        # buy nothing and cost twice — a human inspecting the diff would be told
        # to come back later, and a launchd tick would skip because someone was
        # reading (ADT-112).
        res = reconcile_all(args.root, dry_run=True, pull=args.pull)
    else:
        cfg = load_config(args.root)
        with pass_lock(cfg) as owned:
            if not owned:
                # Not an error: exit 0. This entrypoint races the launchd tick
                # by design (a human running --pull), and a skipped pass is the
                # correct outcome, not a failure to report.
                #
                # Say what did NOT happen, though. adt-install.sh runs this as
                # its adopt pass and only prints an error on a NON-zero exit, so
                # a bare "skipping" would leave a re-installing newcomer with a
                # step that announced itself and then said nothing — the exact
                # shape ADT-174 fixed on the success path. The reassurance is
                # true precisely when this branch can be reached: something else
                # holds the lock, and the only thing that takes it on a timer is
                # the watcher, which will carry the work within a tick.
                print("[adt-sync] another pass is running; skipping. Nothing "
                      "was pushed or adopted — the background watcher picks "
                      "this up on its next tick.")
                return 0
            res = reconcile_all(args.root, pull=args.pull)

    for r in res:
        if r["action"] not in ("noop",):
            # Some results (e.g. a pull-side error) carry no 'path'; never index
            # it directly — that KeyError was the crash that masked the real bug.
            label = r.get("slug") or r.get("path") or r.get("error") or "?"
            print(f"  {r['action']:18} {label}"
                  + (f"  {r.get('changes')}" if r.get("changes") else "")
                  + (f"  #{r['number']}" if r.get("number") else ""))
    print(f"[adt-sync] {summarise(res)}")
    if args.pull:
        # The adopt pass has a human audience (the installer runs it), so it
        # gets a sentence, not only counts (ADT-174).
        print(summarise_adopt(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())

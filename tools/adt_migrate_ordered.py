# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Ordered one-shot migration: create Issues in strict id order so that
GitHub issue #N == <PREFIX>-N (issue-number-as-id).

The id prefix is read from the project config (`id_prefix:`, e.g. TIX for
TIX for one consumer, ADT for agent-dev-team) — NOT hardcoded — so the migrator works for
any ADT-managed backlog, not just the first one. (Origin: agent-dev-team ADT-1,
which hit the hardcoded-TIX bug while standing ADT up as its own project.)

GitHub assigns issue numbers sequentially by creation order and won't let you
choose a number. So to make #N == <PREFIX>-N we MUST create issues in ascending
id order with NO skips: for every N from 1..max, either the real <PREFIX>-N
ticket is created, or — if N is a gap (no such ticket) — a closed placeholder
issue is created to keep the counter aligned.

REQUIRES a virgin target repo (issue counter at 0). Run once. Idempotent-ish:
re-running skips tickets whose cache .md already carries an issue_number, but
the strict-order guarantee only holds on a clean first run — so verify the repo
is empty before starting (the driver asserts this).

Reuses adt_sync.reconcile_one for the actual create (labels + board + write-back
of issue_number), so the field-map + board logic is shared, not duplicated.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import adt_sync  # noqa: E402


def _id_prefix(cfg: dict) -> str:
    """The project's ticket-id prefix (TIX, ADT, …) from config; default TIX."""
    return (cfg.get("id_prefix") or "TIX").strip()


def _id_re(prefix: str) -> "re.Pattern":
    """Regex matching `id: <PREFIX>-N` for the configured prefix."""
    return re.compile(r"^id:\s*" + re.escape(prefix) + r"-(\d+)\s*$", re.M)


def _index_by_num(cfg: dict) -> dict:
    """ticket_number -> (path, data) for every cache .md with an id: <PREFIX>-N."""
    id_re = _id_re(_id_prefix(cfg))
    out = {}
    for path, data in adt_sync.iter_cache_files(cfg):
        m = id_re.search(open(path).read())
        if m:
            out[int(m.group(1))] = (path, data)
    return out


def _assert_virgin(repo: str, prefix: str) -> None:
    issues = adt_sync._gh_json(
        ["issue", "list", "--repo", repo, "--state", "all", "--limit", "1",
         "--json", "number"]) or []
    if issues:
        raise SystemExit(
            f"ABORT: {repo} already has issues (#{issues[0]['number']}). "
            f"Ordered migration needs a virgin repo so #N == {prefix}-N. "
            "Use a fresh repo or accept non-aligned numbers.")


def _create_placeholder(cfg: dict, n: int) -> int:
    """Create a closed placeholder issue to consume number n (an id gap).
    Returns the created number (asserted == n by the caller)."""
    repo = cfg["repo"]
    prefix = _id_prefix(cfg)
    out = adt_sync._gh([
        "issue", "create", "--repo", repo,
        "--title", f"{prefix}-{n} (gap — id never assigned)",
        "--body", f"Placeholder to keep #N == {prefix}-N alignment. {prefix}-{n} "
                  "was never assigned in the source backlog. Safe to leave closed.",
    ])
    num = int(out.strip().splitlines()[-1].rstrip("/").split("/")[-1])
    adt_sync._gh(["issue", "close", str(num), "--repo", repo,
                  "--reason", "not planned"])
    return num


def migrate(project_root: str, dry_run: bool = False) -> dict:
    cfg = adt_sync.load_config(project_root)
    repo = cfg["repo"]
    prefix = _id_prefix(cfg)
    by_tix = _index_by_num(cfg)
    if not by_tix:
        raise SystemExit(f"No {prefix}-numbered tickets found in the cache.")
    lo, hi = min(by_tix), max(by_tix)

    if dry_run:
        gaps = [n for n in range(1, hi + 1) if n not in by_tix]
        return {"range": (lo, hi), "tickets": len(by_tix), "gaps": gaps,
                "would_create": hi, "dry_run": True}

    _assert_virgin(repo, prefix)
    created, placeholders, misaligned = 0, 0, []

    for n in range(1, hi + 1):
        if n in by_tix:
            path, data = by_tix[n]
            res = adt_sync.reconcile_one(path, data, cfg, dry_run=False)
            got = res.get("number")
        else:
            got = _create_placeholder(cfg, n)
            placeholders += 1
        # Verify the assigned number equals N — the whole point.
        if got != n:
            misaligned.append((n, got))
        else:
            created += 1
        if n % 25 == 0:
            print(f"  …#{n}/{hi}")

    return {"range": (lo, hi), "created_aligned": created,
            "placeholders": placeholders, "misaligned": misaligned}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Ordered <PREFIX>-N == #N migration (prefix from config).")
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        # Writes nothing, so it takes no lock — same rule as adt_sync's CLI.
        print(migrate(args.root, dry_run=True))
        sys.exit(0)
    # ADT-112: this is the THIRD writer that reaches the create path. It does
    # not go through reconcile_all — it calls reconcile_one (and
    # _create_placeholder) directly — so the lock has to be taken here, or a
    # migration racing an `adt watch` tick reopens exactly the double-create
    # this ticket closes. A virgin repo is the migrator's precondition, not a
    # guarantee that no watcher is ticking against the same cache.
    cfg = adt_sync.load_config(args.root)
    with adt_sync.pass_lock(cfg) as owned:
        if not owned:
            sys.exit("[adt-migrate] another sync pass is running. Stop the "
                     "watcher before migrating — an ordered migration cannot "
                     "share the create path with a concurrent pass.")
        print(migrate(args.root, dry_run=False))

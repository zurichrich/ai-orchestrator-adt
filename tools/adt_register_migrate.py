#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Re-key this machine's register comments from a hostname to its install UUID.

ADT-254 B2. 58 GitHub Issues carry `<!-- adt:tokens machine=<hostname> ... -->`
register comments — a hostname beside per-ticket spend, and a hostname routinely
carries a person's name. ADT-254 B1 made the ledger and the register writer read
an anonymous UUID instead; this re-keys what was already written.

SELF-SCOPED, AND THAT IS THE WHOLE DESIGN. This tool rewrites ONLY the registers
whose key matches THIS machine's own hostname, using THIS machine's own
`install-id`. It never touches another machine's comments and never needs
another machine's id.

The alternative — a hostname→uuid map built here, covering every machine — was
designed and rejected, and the reason is worth keeping: this host cannot read
another machine's `install-id`, so a map would have to FABRICATE one. That
machine would then read its OWN file on its next run and begin a second,
unlinked identity. Its past and future spend would fragment permanently, under
exactly the one-stable-id-per-install invariant this ticket exists to establish,
and nothing would catch it. Self-scoping removes the possibility rather than
guarding against it, and removes the map — which would have been a
deanonymisation table sitting in a repo that is about to go public.

The cost, disclosed rather than hidden: another machine's registers stay
hostname-keyed until that machine runs this tool itself. That is ADT-254 B2f,
an operational step, and it must happen before the repo is published.

NOTE ON `platform.node()` HERE. This module uses the hostname deliberately — to
FIND the old registers, which are keyed by it. That is the opposite of using it
AS the identity, which is what B1 removed. Nothing here writes a hostname.

DRY-RUN IS THE DEFAULT. `--apply` is required to write anything. This is the one
irreversible step in ADT-254: another machine's totals exist ONLY in these
comments, since its ledger is not on this host.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_kanban as bk  # noqa: E402

TOKENS_RE = re.compile(r"<!-- adt:tokens machine=(\S+) total=(\d+) -->")
COST_RE = re.compile(r"<!-- adt:cost machine=(\S+) micros=(\d+) tier=(\w+) -->")


def own_hostname() -> str:
    """The key the OLD registers were written under — not an identity."""
    return (platform.node().split(".")[0] or "unknown-machine").lower()


def register_of(body: str):
    """-> (machine, total, micros, tier) for a register comment, else None."""
    m = TOKENS_RE.search(body or "")
    if not m:
        return None
    c = COST_RE.search(body)
    return (m.group(1), int(m.group(2)),
            int(c.group(2)) if c else None,
            c.group(3) if c else None)


def rewrite_body(body: str, old_key: str, new_key: str) -> str:
    """Swap the machine key in BOTH markers and in the prose that mirrors it.

    Rebuilding the body from `register_body()` rather than string-replacing is
    deliberate: the prose carries the key too (``spent on machine `mac` ``), and
    a marker-only swap would leave the hostname visible in the very sentence a
    reader sees — which is the exposure this migration exists to remove.
    """
    reg = register_of(body)
    if not reg or reg[0] != old_key:
        return body
    _, total, micros, tier = reg
    return bk.register_body(new_key, total, micros, tier or "measured")


def plan(comments, own_key: str, new_key: str):
    """-> [(comment_id, old_body, new_body)] for THIS machine's registers only."""
    out = []
    for c in comments:
        body = c.get("body") or ""
        reg = register_of(body)
        if not reg or reg[0] != own_key:
            continue                      # not a register, or not ours
        new_body = rewrite_body(body, own_key, new_key)
        if new_body != body:
            out.append((c.get("id"), body, new_body))
    return out


def snapshot(comments) -> dict:
    """{comment_id: (machine, total, micros)} for EVERY register, ours or not.

    Other machines' rows are snapshotted precisely because we must be able to
    prove afterwards that we did not touch them.
    """
    out = {}
    for c in comments:
        reg = register_of(c.get("body") or "")
        if reg:
            out[c.get("id")] = (reg[0], reg[1], reg[2])
    return out


def verify(before: dict, after: dict, own_key: str, new_key: str):
    """-> [problems]. Empty means the migration preserved everything.

    This is what separates a MIGRATION from a DELETION. "Zero old-format
    registers remain" passes identically for both, and deletion is the outcome
    that destroys another machine's history irrecoverably — so the check is on
    the amounts, not on the absence of a string.
    """
    problems = []
    for cid, (machine, total, micros) in before.items():
        if cid not in after:
            problems.append("comment %s disappeared (was %s total=%s)"
                            % (cid, machine, total))
            continue
        now_machine, now_total, now_micros = after[cid]
        if total != now_total or micros != now_micros:
            problems.append("comment %s amounts changed: total %s->%s micros %s->%s"
                            % (cid, total, now_total, micros, now_micros))
        if machine == own_key and now_machine != new_key:
            problems.append("comment %s was ours and did not re-key (%s)"
                            % (cid, now_machine))
        if machine != own_key and now_machine != machine:
            problems.append("comment %s belongs to %s and was modified to %s"
                            % (cid, machine, now_machine))
    return problems


# ---------------------------------------------------------------- gh plumbing

def _gh_json(args):
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("gh %s failed: %s" % (" ".join(args), proc.stderr.strip()))
    return json.loads(proc.stdout or "[]")


def issues_with_registers(repo: str):
    """Issue numbers whose comments carry a register for anyone."""
    q = ("repo:%s in:comments \"adt:tokens machine\"" % repo)
    data = _gh_json(["api", "-X", "GET", "search/issues",
                     "-f", "q=" + q, "-f", "per_page=100", "--paginate"])
    items = data.get("items", []) if isinstance(data, dict) else []
    return [i["number"] for i in items]


def comments_of(repo: str, number: int):
    return _gh_json(["api", "repos/%s/issues/%d/comments" % (repo, number),
                     "--paginate"])


def patch_comment(repo: str, comment_id, body: str):
    subprocess.run(["gh", "api", "-X", "PATCH",
                    "repos/%s/issues/comments/%s" % (repo, comment_id),
                    "-f", "body=" + body],
                   capture_output=True, text=True, check=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Re-key THIS machine's register comments to its install UUID.")
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="report what would change and write nothing. This is "
                         "the DEFAULT; it exists as an explicit flag so the "
                         "safe mode is visible in --help rather than implied "
                         "by the absence of --apply.")
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without it this is a DRY RUN and "
                         "nothing is modified — the default, because another "
                         "machine's totals exist only in these comments.")
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if ANY register on this repo is still "
                         "keyed by this machine's hostname. A GitHub search for "
                         "`machine=mac` cannot answer this — it matches the "
                         "substring in `machine=r-mac-mini` too, so it reports "
                         "24 remaining when the true answer is 0. This compares "
                         "the parsed key exactly.")
    ap.add_argument("--verify-totals", action="store_true",
                    help="re-read every register afterwards and assert this "
                         "machine's amounts survived and other machines' rows "
                         "are untouched")
    args = ap.parse_args(argv)

    own_key = own_hostname()
    new_key = bk.machine_id()
    if own_key == new_key:
        print("nothing to do: registers are already keyed by the install id")
        return 0

    numbers = issues_with_registers(args.repo)
    print("scanning %d issue(s) carrying registers" % len(numbers))

    before, work = {}, []
    for n in numbers:
        cs = comments_of(args.repo, n)
        before.update(snapshot(cs))
        for cid, old, new in plan(cs, own_key, new_key):
            work.append((n, cid, old, new))

    mine = sum(1 for v in before.values() if v[0] == own_key)
    theirs = len(before) - mine
    print("registers found: %d total — %d keyed %r (ours), %d other machines'"
          % (len(before), mine, own_key, theirs))
    print("would re-key %d comment(s) to %s" % (len(work), new_key))

    if args.check:
        if work:
            print("\nCHECK FAILED: %d register(s) still keyed %r" % (len(work), own_key),
                  file=sys.stderr)
            return 1
        print("\nCHECK OK: no register is keyed %r" % own_key)
        return 0

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to migrate.")
        return 0

    for n, cid, _old, new in work:
        patch_comment(args.repo, cid, new)
    print("re-keyed %d comment(s)" % len(work))

    if args.verify_totals:
        after = {}
        for n in numbers:
            after.update(snapshot(comments_of(args.repo, n)))
        problems = verify(before, after, own_key, new_key)
        if problems:
            print("\nVERIFICATION FAILED:", file=sys.stderr)
            for p in problems:
                print("  " + p, file=sys.stderr)
            return 1
        print("verified: %d register(s) preserved, %d other machines' untouched"
              % (len(before), theirs))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
"""Classify every done ticket's cost figure by how well it is actually known.

ADT-247 criterion 10. A `$` figure that ships inside a public Issue must not
claim more precision than it has, and the failure this exists to prevent is a
ticket silently left at `measured` whose subagent spend was never counted.

WHY THIS IS NOT OBVIOUS FROM THE LEDGER ALONE. ADT-224 shipped a `SubagentStop`
hook that writes a `subagent:<name>` row per dispatched agent. It records
FORWARD ONLY. Every ticket closed before that hook went live has main-session
rows and nothing else — which is indistinguishable, in the ledger, from a ticket
that genuinely dispatched no subagents. Absence is ambiguous, and treating it as
zero reproduces the 76% understatement ADT-224 measured.

The hook's ship date resolves the ambiguity, because after it the absence of a
subagent row IS evidence that none ran:

    no ledger rows at all                  -> legacy    (pre-ledger work)
    rows, all before the hook, no subagent -> estimated (unmeasured, unknowable)
    rows after the hook, no subagent row   -> measured  (absence means none ran)
    any subagent row                       -> measured  (counted)

A ticket classed `estimated` can only be promoted by recomputing from its
subagent transcripts, and those are pruned on a rolling basis — this tool
reports, per ticket, whether they still exist, so the report can say which
figures could have been recovered and which are gone for good.

Usage:
    python3 tools/adt_cost_coverage.py <ledger> <cache-dir> [--apply]

Without --apply it prints the classification and changes nothing.
"""

import os
import re
import sys
import glob

# The first subagent row the ledger ever carried. Read from the data rather than
# hardcoded, so this stays correct if the ledger is rebuilt or trimmed.
def hook_live_from(rows):
    stamps = [r[0] for r in rows if r[-1].startswith("subagent:")]
    return min(stamps) if stamps else None


def read_ledger(path):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) < 11:
                continue
            # Pad so the optional trailing marker column is always addressable.
            while len(f) < 12:
                f.append("")
            rows.append(f)
    return rows


def normalise(tix):
    """ADT-034 and ADT-34 are one ticket. Joining on the raw string splits a
    ticket's spend across two keys (ADT-247, criterion 10, joining hazard 3)."""
    m = re.match(r"^([A-Za-z]+)-0*(\d+)$", tix.strip())
    return f"{m.group(1).upper()}-{int(m.group(2)):03d}" if m else tix.strip()


def transcripts_survive(session_ids, projects_root):
    """True when at least one of the ticket's sessions still has a subagents/
    directory on disk — i.e. the figure could still be recomputed."""
    for sid in session_ids:
        for d in glob.glob(os.path.join(projects_root, "*", sid, "subagents")):
            if os.path.isdir(d):
                return True
    return False


def frontmatter(path):
    out, inside = {}, False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.rstrip("\n") == "---":
                if inside:
                    break
                inside = True
                continue
            if inside:
                m = re.match(r"^([a-z_]+):\s*(.*)$", line.rstrip("\n"))
                if m:
                    out[m.group(1)] = m.group(2).strip()
    return out


def classify(ledger_path, cache_dir, projects_root):
    rows = read_ledger(ledger_path)
    live_from = hook_live_from(rows)

    by_tix = {}
    for r in rows:
        key = normalise(r[1])
        by_tix.setdefault(key, []).append(r)

    results = []
    for path in sorted(glob.glob(os.path.join(cache_dir, "*", "done", "*.md"))):
        fm = frontmatter(path)
        tid = fm.get("id", "")
        if not tid:
            continue
        key = normalise(tid)
        mine = by_tix.get(key, [])

        if not mine:
            tier, why = "legacy", "no ledger rows — predates the ledger"
        elif any(r[11].startswith("subagent:") for r in mine):
            tier, why = "measured", "subagent rows present and counted"
        elif live_from and max(r[0] for r in mine) > live_from:
            tier, why = "measured", "active after the hook went live; no subagents dispatched"
        else:
            sids = {r[4] for r in mine}
            recoverable = transcripts_survive(sids, projects_root)
            tier = "estimated"
            why = ("subagent spend never recorded; transcripts SURVIVE and could be recomputed"
                   if recoverable else
                   "subagent spend never recorded; transcripts pruned — unrecoverable")

        results.append({
            "path": path, "id": tid, "key": key,
            "was": fm.get("cost_tier", ""), "now": tier,
            "cost": fm.get("cost_usd", ""), "rows": len(mine), "why": why,
        })
    return results, live_from


def apply_tier(path, tier):
    with open(path, encoding="utf-8") as fh:
        s = fh.read()
    if re.search(r"^cost_tier:.*$", s, re.M):
        s = re.sub(r"^cost_tier:.*$", f"cost_tier: {tier}", s, count=1, flags=re.M)
    else:
        # Insert inside the frontmatter, after cost_usd if present, else before
        # the closing fence. Never append to the body.
        if re.search(r"^cost_usd:.*$", s, re.M):
            s = re.sub(r"^(cost_usd:.*)$", r"\1\ncost_tier: " + tier, s, count=1, flags=re.M)
        else:
            parts = s.split("---", 2)
            if len(parts) >= 3:
                parts[1] = parts[1].rstrip("\n") + f"\ncost_tier: {tier}\n"
                s = "---".join(parts)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(s)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply_ = "--apply" in sys.argv
    if len(args) < 2:
        print(__doc__.strip().splitlines()[-3], file=sys.stderr)
        return 2
    ledger, cache = args[0], args[1]
    projects_root = os.path.expanduser("~/.claude/projects")

    results, live_from = classify(ledger, cache, projects_root)
    print(f"subagent recording live from: {live_from or '(never)'}")
    print(f"{'ticket':<10} {'was':<10} {'now':<10} {'rows':>5}  reason")
    counts = {}
    for r in results:
        counts[r["now"]] = counts.get(r["now"], 0) + 1
        changed = "*" if r["was"] != r["now"] else " "
        print(f"{changed}{r['id']:<9} {r['was'] or '-':<10} {r['now']:<10} {r['rows']:>5}  {r['why']}")
        if apply_ and r["was"] != r["now"]:
            apply_tier(r["path"], r["now"])
    print()
    print("totals: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"changed: {sum(1 for r in results if r['was'] != r['now'])} of {len(results)}"
          + (" (applied)" if apply_ else " (dry run — pass --apply to write)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

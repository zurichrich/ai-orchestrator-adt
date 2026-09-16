#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
"""adt_metrics — what ADT can honestly measure about its own deliveries (ADT-114).

ONE metric is real here, and the limits of the others are stated rather than
papered over. That is deliberate: a metric whose baseline does not exist is worse
than no metric, because it looks like evidence.

  follow-on rate  — MEASURABLE. Tickets spawned per ticket delivered, read from
                    `follow_ons:` frontmatter stamped at close. Expected to trend
                    to ~zero: under enforced rule 2 a follow-on needs an
                    unforeseen circumstance, a significant deviation, AND human
                    approval, so a routine one is a defect in the delivery.

  spawns blocked  — MEASURABLE. Denies in the deferral-guard log. This is NOT the
                    interrupt rate and is not reported as one (see below).

  interrupt rate  — NOT MEASURABLE, and this tool says so instead of substituting
                    something adjacent. The brief assumed it could be baselined
                    over the last N closed tickets; measured at plan time, ALL 24
                    closed tickets contained zero `BLOCKED` markers. /adt-block
                    has effectively never been used — the agent interrupts
                    conversationally ("what should I do?"), which leaves no
                    artifact. That confirms mechanism 4's thesis and destroys the
                    metric at the same time. A blocked spawn is not an interrupt;
                    reporting it as one would be swapping the quantity while
                    keeping the name.

  post-merge defects — MEASURABLE, and MANDATORY. This is the counter-metric.
                    Bugs filed after a ticket closed that name it. Without it the
                    follow-on rate is a Goodhart machine: drive follow-ons to zero
                    by cramming half-finished work into the delivery and the
                    defects show up here instead. Neither number means anything
                    alone — the pair is the measurement.

  cross-examination rate — NOT MEASURABLE without transcript mining. The brief
                    already calls it a secondary proxy.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

FOLLOW_ONS_RE = re.compile(r"^follow_ons:\s*(\d+)\s*$", re.MULTILINE)
ID_RE = re.compile(r"^id:\s*(\S+)\s*$", re.MULTILINE)
BLOCKED_RE = re.compile(r"^##\s+.*\bBLOCKED\b", re.MULTILINE)
TRACK_FM_RE = re.compile(r"^track:\s*(\S+)\s*$", re.MULTILINE)


def closed_tickets(cache_dir):
    """Every ticket .md under a `done/` folder, sorted for stable output."""
    out = []
    for root, dirs, files in os.walk(cache_dir):
        if os.path.basename(root) != "done":
            continue
        for name in sorted(files):
            if name.endswith(".md"):
                out.append(os.path.join(root, name))
    return sorted(out)


def read_ticket(path):
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None
    fm = text.split("---", 2)
    block = fm[1] if len(fm) >= 3 else text
    m = FOLLOW_ONS_RE.search(block)
    tid = ID_RE.search(block)
    updated = re.search(r"^updated:\s*(\S+)", block, re.MULTILINE)
    closed = re.search(r"^closed:\s*(\S+)", block, re.MULTILINE)
    closed_on = closed.group(1) if closed else None
    if closed_on in (None, "null", "~", ""):
        # No close date on the file -> fall back to `updated:`, the next-closest
        # thing the cache carries. It is only an approximation: `updated` moves
        # on any edit to the Issue, so a bulk rewrite (ADT-174 stamped every
        # Issue in one afternoon) restamps every ticket with that day. Prefer
        # `closed:`, which the sync now pulls onto existing files.
        closed_on = updated.group(1) if updated else None
    return {
        "path": path,
        "id": tid.group(1) if tid else os.path.basename(path)[:-3],
        "closed_on": closed_on,
        # A ticket closed before the field existed is UNSTAMPED, which is not the
        # same as zero. Counting it as zero would flatter the baseline.
        "follow_ons": int(m.group(1)) if m else None,
        "blocked_markers": len(BLOCKED_RE.findall(text)),
        # ADT-205: which lifecycle tier this ticket actually ran. Unstamped is
        # kept distinct from a tier, for the same reason follow_ons keeps
        # unstamped distinct from zero -- a ticket closed before `track:`
        # existed did not run `standard`, it ran un-tiered.
        "track": (TRACK_FM_RE.search(block).group(1)
                  if TRACK_FM_RE.search(block) else "unset"),
    }


CREATED_RE = re.compile(r"^created:\s*(\S+)", re.MULTILINE)
TYPE_RE = re.compile(r"^type:\s*(\S+)", re.MULTILINE)


def all_tickets(cache_dir):
    """Every ticket .md in the cache, whatever its stage."""
    out = []
    for root, dirs, files in os.walk(cache_dir):
        for name in sorted(files):
            if name.endswith(".md"):
                out.append(os.path.join(root, name))
    return sorted(out)


def post_merge_defects(cache_dir, closed):
    """THE COUNTER-METRIC. Bugs filed after a ticket closed that name it.

    Paired with the follow-on rate so neither can be improved by shifting work
    into the other: drive follow-ons to zero by shipping half-finished work and
    the remainder resurfaces here as defects. A loop optimised on one number
    alone is a Goodhart machine.

    Deliberately crude — a bug that NAMES the ticket. It will miss a defect filed
    without the reference, so it is a floor, not a census. Said here rather than
    discovered later."""
    by_id = {c["id"]: c for c in closed if c.get("id") and c.get("closed_on")}
    counts = {tid: 0 for tid in by_id}
    for path in all_tickets(cache_dir):
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            continue
        fm = text.split("---", 2)
        block = fm[1] if len(fm) >= 3 else text
        t = TYPE_RE.search(block)
        if not t or t.group(1).strip().lower() not in ("bug", "bugs"):
            continue
        created = CREATED_RE.search(block)
        if not created:
            continue
        for tid, c in by_id.items():
            # filed AFTER the ticket closed, and names it
            if created.group(1) > c["closed_on"] and re.search(
                    r"\b%s\b" % re.escape(tid), text):
                counts[tid] += 1
    return counts


def guard_denies(project_root):
    # adt-deferral-guard.sh appends to <project>/.adt/state/deferral-guard.log.
    # The count used to read .claude/hooks/deferral-guard.log, a location the
    # hook stopped writing to, so the figure froze where that old file ended.
    log = os.path.join(project_root, ".adt", "state", "deferral-guard.log")
    if not os.path.isfile(log):
        return None
    try:
        return sum(1 for line in open(log, encoding="utf-8") if line.startswith("DENY"))
    except OSError:
        return None


def by_track(rows, defects):
    """{track: {closed, stamped, follow_ons, defects}} (ADT-205).

    WHY this split matters: `post_merge_defects` already measures the benefit
    side of ADT's central claim, but attributes it to nothing -- so it cannot
    answer "do the extra gates reduce post-merge defects?", which is the only
    question a `track:` recommendation rests on. This groups the SAME numbers
    the existing functions compute; it does not recount them.

    Read the `n` before the rate. A tier with one closed ticket supports no
    claim, and the caller prints n for exactly that reason.
    """
    out = {}
    for r in rows:
        acc = out.setdefault(r["track"], {"closed": 0, "stamped": 0,
                                          "follow_ons": 0, "defects": 0})
        acc["closed"] += 1
        if r["follow_ons"] is not None:
            acc["stamped"] += 1
            acc["follow_ons"] += r["follow_ons"]
        acc["defects"] += defects.get(r["id"], 0)
    return out


# ─────────────────────────────────────────────────────────── ADT-224 Phase D
# The gate side of the ledger: did a gate RUN, and did it CHANGE anything?
#
# Criterion 4 asks for both, derived from a recorded field rather than from
# prose. The recording lives in each ticket's `gate_effects:` frontmatter and is
# written by `adt_dod --record-verdict`; nothing here re-derives it, and in
# particular nothing here calls `plan_gating_enabled()`. That function is global
# and per-run, so consulting it at REPORT time would mis-describe every
# historical ticket the moment the flag flips — in both directions. The gating
# state travels with the row that was written under it.

def gate_effect_rows(cache_dir):
    """{gate: {ran, caused_edit, under_recorded, tickets}} across the cache."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import adt_dod
    out = {}
    for path in all_tickets(cache_dir):
        for gate, eff in adt_dod.gate_effects(path).items():
            acc = out.setdefault(gate, {"ran": 0, "caused_edit": 0,
                                        "under_recorded": 0, "tickets": 0})
            acc["tickets"] += 1
            acc["ran"] += eff["ran"]
            acc["caused_edit"] += 1 if eff["caused_edit"] else 0
            acc["under_recorded"] += 1 if eff["under_recorded"] else 0
    return out


def handbacks(ledger_path, tix=None):
    """{tix: count} — how many times a run handed back to the human.

    Criterion 5, derived from the ledger the Stop hook already writes rather
    than from `/adt-block` being used: a turn boundary IS a handback, and
    `/adt-block` only captures the ones formal enough to leave an artifact.

    SUBAGENT ROWS ARE EXCLUDED. Since ADT-224 B2a the SubagentStop hook writes
    rows carrying the same ticket and their own timestamps, so counting distinct
    timestamps naively would score every dispatched reviewer as a handback to
    the human — inflating the number most on exactly the tier that hands back
    least. The `subagent:` prefix in column 12 is what discriminates them, which
    is why that prefix is a cross-phase contract and not a label.
    """
    seen = {}
    try:
        fh = open(ledger_path, encoding="utf-8")
    except OSError:
        return {}
    with fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            command = parts[11] if len(parts) > 11 else ""
            if command.startswith("subagent:"):
                continue
            t = parts[1]
            if not t or (tix and t != tix):
                continue
            seen.setdefault(t, set()).add(parts[0])
    return {t: len(stamps) for t, stamps in seen.items()}


def report(cache_dir, repo_root, out=sys.stdout, show_by_track=False):
    rows = [r for r in (read_ticket(p) for p in closed_tickets(cache_dir)) if r]
    stamped = [r for r in rows if r["follow_ons"] is not None]
    total = sum(r["follow_ons"] for r in stamped)
    blocked = sum(r["blocked_markers"] for r in rows)
    denies = guard_denies(repo_root)
    defects = post_merge_defects(cache_dir, rows)
    defect_total = sum(defects.values())

    p = lambda s: print(s, file=out)
    p("ADT delivery metrics")
    p("=" * 60)
    p("closed tickets:            %d" % len(rows))
    p("  with follow_ons stamped: %d" % len(stamped))
    if stamped:
        p("follow-on rate:            %.2f per delivered ticket (%d over %d)"
          % (total / len(stamped), total, len(stamped)))
    else:
        p("follow-on rate:            no stamped tickets yet — no baseline")
    p("post-merge defects:        %d  <- THE COUNTER-METRIC" % defect_total)
    p("    bugs filed after a ticket closed that name it. Paired with the")
    p("    follow-on rate so neither can be improved by shifting work into the")
    p("    other. A floor, not a census: a defect filed without the reference is")
    p("    not counted.")
    p("spawns blocked (guard):    %s"
      % ("%d" % denies if denies is not None else "no log on this machine"))
    p("")
    p("NOT measured, deliberately:")
    p("  interrupt rate — %d BLOCKED markers across %d closed tickets."
      % (blocked, len(rows)))
    p("    /adt-block leaves an artifact; a conversational 'what should I do?'")
    p("    does not, and that is how interrupts actually happen. A blocked spawn")
    p("    is NOT an interrupt and is not reported as one.")
    p("  cross-examination rate — needs transcript mining; a secondary proxy.")
    p("")
    p("gates (ADT-224) — did the gate run, and did it change anything?")
    effects = gate_effect_rows(cache_dir)
    if not effects:
        p("  no ticket carries a gate_effects record yet — no baseline")
    else:
        p("  %-14s%8s%14s%16s" % ("gate", "ran", "caused edit", "under-recorded"))
        for gate in sorted(effects):
            a = effects[gate]
            p("  %-14s%8d%14d%16d"
              % (gate, a["ran"], a["caused_edit"], a["under_recorded"]))
        p("    `caused edit` is a NEGATIVE verdict whose graded-text hash moved")
        p("    by the next round — the gate ran, said no, and the text changed.")
        p("    `under-recorded` is a verdict block with no record: NOT the same")
        p("    as a gate that never caused an edit, and never reported as one.")
        p("    A gate that has never caused an edit reads 0 here, visibly.")
    if show_by_track:
        p("")
        p("by track (ADT-205) — the benefit side, attributed to a lane:")
        bt = by_track(rows, defects)
        p("  %-16s%8s%10s%12s" % ("track", "closed", "defects", "follow-ons"))
        for name in sorted(bt):
            a = bt[name]
            rate = ("%.2f" % (a["follow_ons"] / a["stamped"])
                    if a["stamped"] else "—")
            p("  %-16s%8d%10d%12s"
              % ("track: " + name, a["closed"], a["defects"], rate))
        p("    n is the `closed` column. A tier with one closed ticket supports")
        p("    no claim; the split is reported so the thinness is visible rather")
        p("    than averaged away.")
    if stamped:
        p("")
        p("per ticket:")
        for r in sorted(stamped, key=lambda r: -r["follow_ons"]):
            p("  %-10s %d" % (r["id"], r["follow_ons"]))
    return {"closed": len(rows), "stamped": len(stamped), "follow_ons": total,
            "blocked_markers": blocked, "denies": denies,
            "post_merge_defects": defect_total, "defects_by_ticket": defects,
            "by_track": by_track(rows, defects),
            "gate_effects": effects}


def main(argv=None):
    ap = argparse.ArgumentParser(description="ADT delivery metrics (ADT-114).")
    ap.add_argument("--cache-dir", required=True,
                    help="the backlog cache root (holds <type>/done/*.md)")
    ap.add_argument("--repo-root", default=".",
                    help="repo root, for .adt/state/adt-deferral-guard.log")
    ap.add_argument("--by-track", action="store_true",
                    help="split post-merge defects and follow-ons by `track:` (ADT-205)")
    args = ap.parse_args(argv)
    report(args.cache_dir, args.repo_root, show_by_track=args.by_track)
    return 0


if __name__ == "__main__":
    sys.exit(main())

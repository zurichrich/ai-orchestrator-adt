#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
"""Freeze the timing join for ADT-339. Run once; output is committed."""
import csv, json, os, sys, glob, re, datetime as dt, collections

REPO = sys.argv[1]; CACHE = os.path.expanduser(sys.argv[2]); OUT = sys.argv[3]
CAP = 900  # a gap longer than 15 min is the operator away, not a step running

def ts(s):
    try: return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception: return None

rows = []
with open(os.path.join(REPO, ".adt/state/cost-ledger.log")) as fh:
    for r in csv.reader(fh, delimiter="\t"):
        if len(r) < 12: continue
        t = ts(r[0])
        if t: rows.append((t, r[1], r[11]))
rows.sort()

# --- waits: gaps between consecutive main-session turns, attributed to the
# command bound at the LATER turn (the turn you waited for).
waits = collections.defaultdict(lambda: collections.defaultdict(
    lambda: {"capped_s": 0, "raw_s": 0, "longest_gap_s": 0, "gaps": 0}))
prev = None
for t, tix, cmd in rows:
    if cmd and not cmd.startswith("subagent:") and tix != "__unassigned__":
        if prev and prev[1] == tix:
            g = (t - prev[0]).total_seconds()
            if g > 0:
                d = waits[tix][cmd]
                d["raw_s"] += g
                d["gaps"] += 1
                if g <= CAP:
                    d["capped_s"] += g
                    d["longest_gap_s"] = max(d["longest_gap_s"], g)
        prev = (t, tix, cmd)

# --- dispatches: contiguous runs of one subagent on one ticket = one dispatch.
disp = collections.defaultdict(list)
cur = None
for t, tix, cmd in rows:
    if not (cmd or "").startswith("subagent:"):
        continue
    name = cmd.split(":", 1)[1]
    # ADT-339: the reviewers were renamed mid-history (dod-coverage-reviewer ->
    # adt-dod-coverage-reviewer, and the same for plan-quality). The ledger holds
    # rows under both strings for the SAME reviewer, so bucketing on the raw
    # string splits one agent into two and halves its per-dispatch figures.
    # Normalise to the current canonical name.
    name = name if name.startswith("adt-") or name == "general-purpose" else "adt-" + name
    key = (tix, name)
    if cur and cur["key"] == key and (t - cur["end"]).total_seconds() <= 120:
        cur["end"] = t; cur["turns"] += 1
    else:
        if cur: disp[cur["key"][1]].append(
            {"ticket": cur["key"][0], "span_s": round((cur["end"] - cur["start"]).total_seconds()), "turns": cur["turns"]})
        cur = {"key": key, "start": t, "end": t, "turns": 1}
if cur: disp[cur["key"][1]].append(
    {"ticket": cur["key"][0], "span_s": round((cur["end"] - cur["start"]).total_seconds()), "turns": cur["turns"]})

# --- gate_wait: the operator's ACTUAL wait on a gate. A main-session gap that
# contains subagent rows for the same ticket is time spent waiting on that gate.
# Stronger than a dispatch span, which excludes the tail between the reviewer's
# last row and the main session's next turn.
def canon(n):
    return n if n.startswith("adt-") or n == "general-purpose" else "adt-" + n

subrows = [(t_, tix, canon(c.split(":", 1)[1])) for t_, tix, c in rows
           if (c or "").startswith("subagent:")]
gate_by_agent = collections.defaultdict(lambda: {"wait_s": 0, "gaps": 0})
gate_total = other_total = 0.0
gate_n = other_n = 0
prev = None
for t_, tix, cmd in rows:
    if not cmd or cmd.startswith("subagent:") or tix == "__unassigned__":
        continue
    if prev and prev[1] == tix:
        g = (t_ - prev[0]).total_seconds()
        if 0 < g <= CAP:
            inside = [s for s in subrows if prev[0] < s[0] <= t_ and s[1] == tix]
            if inside:
                top = collections.Counter(s[2] for s in inside).most_common(1)[0][0]
                gate_by_agent[top]["wait_s"] += g
                gate_by_agent[top]["gaps"] += 1
                gate_total += g; gate_n += 1
            else:
                other_total += g; other_n += 1
    prev = (t_, tix, cmd)
gate_wait = {"by_agent": {k: {"wait_s": round(v["wait_s"]), "gaps": v["gaps"]}
                         for k, v in gate_by_agent.items()},
             "gate_s": round(gate_total), "gate_gaps": gate_n,
             "other_s": round(other_total), "other_gaps": other_n}

# --- gate_effects from the done cache
ge = {}
for f in glob.glob(os.path.join(CACHE, "*/done/*.md")) + glob.glob(os.path.join(CACHE, "*/*/*.md")):
    txt = open(f, encoding="utf-8").read()
    if "gate_effects:" not in txt: continue
    m = re.search(r"^id:\s*(\S+)", txt, re.M)
    if not m: continue
    blk = txt.split("gate_effects:", 1)[1].split("\n---", 1)[0]
    recs = blk.count("kind: record")
    caused = "caused_edit: true" in blk
    ge[m.group(1)] = {"records": recs, "caused_edit": caused}

# --- arms: exact wall-clock from ADT-205 (duration_ms per run, stop_points too)
arms = {}
# The ADT-205 run inputs went to the private archive when ADT was published
# (ADT-315). Without them this section cannot be recomputed, and a silent empty
# `arms` would look like a measurement rather than a missing input, so say so.
p = os.path.join(REPO, "docs/gate-tax-runs/live-runs.json")
if not os.path.exists(p):
    raise SystemExit(
        "missing %s — the ADT-205 run inputs live in the private archive "
        "(zurichrich/adt-private). Re-run this capture there, or against a "
        "checkout that has them." % p)
if os.path.exists(p):
    per = collections.defaultdict(list)
    for r in json.load(open(p)):
        per[r["arm"]].append(r)
    arms = {a: {"runs": len(v),
                "mean_min": round(sum(x["duration_ms"] for x in v) / len(v) / 60000, 1),
                "min_min": round(min(x["duration_ms"] for x in v) / 60000, 1),
                "max_min": round(max(x["duration_ms"] for x in v) / 60000, 1),
                "stop_points": sorted({x["stop_points"] for x in v})}
            for a, v in sorted(per.items())}

cap = {
    "captured": dt.date.today().isoformat(),
    "gap_cap_s": CAP,
    "commands": {
        "waits": "python3 capture.py — gaps between consecutive main-session rows in .adt/state/cost-ledger.log, attributed to the command bound at the later turn",
        "dispatches": "same ledger, rows whose command column is subagent:<name>, grouped into contiguous runs per ticket",
        "gate_effects": "gate_effects: frontmatter scraped from ~/.adt/agent-dev-team/cache/*/done/*.md",
        "arms": "docs/gate-tax-runs/live-runs.json in the private archive (ADT-205, wall-clock is exact)",
        "gate_wait": "same ledger, main-session gaps that contain subagent rows for the same ticket; reviewer names normalised across the adt- rename",
    },
    "waits": {t: {c: {k: (round(v) if isinstance(v, float) else v) for k, v in d.items()}
                  for c, d in cs.items()} for t, cs in waits.items()},
    "dispatches": dict(disp),
    "gate_effects": ge,
    "arms": arms,
    "gate_wait": gate_wait,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(cap, open(OUT, "w"), indent=1, sort_keys=True)
print("tickets with waits:", len(cap["waits"]))
print("agents with dispatches:", len(cap["dispatches"]))
print("tickets with gate_effects:", len(cap["gate_effects"]))
print("arms:", cap["arms"])

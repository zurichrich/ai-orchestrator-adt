#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
"""adt_lane_cost — what each LANE of the lifecycle costs (ADT-205).

The question this answers: ADT asks an operator to accept a slower path on the
promise that the gates buy quality. `adt_metrics.py` measures the benefit side
(post-merge defects) but attributes it to nothing. Nothing measured the cost
side at all, so every `track:` recommendation was intuition.

WHY THERE IS NO NEW INSTRUMENTATION HERE. The two halves of "cost per lane" were
already being recorded and had simply never been joined:

  * Lane boundaries live on GitHub. `adt_sync` pushes `stage:*` labels, and
    GitHub timestamps every label add/remove, so `GET /issues/<n>/events` is a
    complete lane history for every ticket the sync has ever touched --
    retrospectively, including tickets closed long before this tool existed.
  * Per-turn cost lives in `.adt/state/cost-ledger.log`: an ISO
    timestamp, a ticket id, and token counts, one row per turn.

Joining them is an interval bucket -- each ledger row's timestamp falls inside
exactly one lane interval. The rejected alternative was a `stage_history:` field
appended by a hook on the cache `mv`; it is strictly worse, because it puts new
code on the write path and could only ever measure tickets closed after it
shipped.

WHAT "LANE" MEANS HERE. The board state when the turn ENDED, which is what the
label events record -- not what the agent believed it was doing. That puts a
ticket's `/adt-close` turns in the `done` bucket, which is correct: that work
happened with the board in `done`. Stated because it is a real modelling choice,
not an accident.

COVERAGE IS PARTIAL AND SAYS SO. The cross-machine token checkpoints on an Issue
carry a subtotal, not per-turn timestamps, so they cannot be bucketed: only
tickets with rows in the LOCAL ledger can be split by lane. The summary prints
per-track `n` and names the tickets it could not cover, because a per-lane
average over an unstated denominator is the kind of number that gets quoted
later without its caveat.

Also carries the three report-side checks the ticket's Definition of Done runs
(`--check-experiment`, `--check-report`, `--reconcile`). They live here rather
than in shell conditions for a demonstrated reason: the first draft of that DoD
asserted table rows with `grep -cE "^\\| arm-[ABC] \\|.*\\$[0-9]"`, and inside
double quotes bash reads `$[0-9]` as arithmetic expansion, so the pattern was
mangled and returned zero matches on a line that does match. That condition
could never have gone green. As ordinary Python it is testable, and
`--reconcile` is a pure function of committed inputs, so it needs no live `gh`
and no machine-local ledger on whatever host grades it (the ADT-100 failure).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt_cost  # noqa: E402

def _adt_state(root, *leaf):
    """ADT's runtime-state dir. No legacy fallback (ADT-301)."""
    base = os.path.join(str(root), ".adt", "state")
    return os.path.join(base, *leaf) if leaf else base

LEDGER_LEAF = "cost-ledger.log"
STAGE_PREFIX = "stage:"


# ------------------------------------------------------------------ time

def parse_iso(s: str) -> _dt.datetime | None:
    """GitHub and the ledger both write `...Z`; `fromisoformat` wants +00:00
    before 3.11. Returns None rather than raising -- a malformed timestamp must
    drop one row, never abort a whole report."""
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------------ lanes

def lane_intervals(events: list) -> list:
    """GitHub label events -> [(start, end|None, lane)], in time order.

    Only `stage:*` labels are considered. A `labeled` opens an interval and the
    next stage transition closes it; the final interval is left open (end=None)
    because the ticket is still sitting in that lane.

    An `unlabeled` is deliberately NOT treated as a close on its own. GitHub
    emits the add and the remove of a stage swap with the SAME timestamp, and
    their order in the payload is not guaranteed -- closing on the remove would
    make a zero-length or negative interval depending on which arrived first.
    The next `labeled` is the unambiguous boundary.
    """
    stamped = []
    for ev in events or []:
        if ev.get("event") != "labeled":
            continue
        name = ((ev.get("label") or {}).get("name") or "")
        if not name.startswith(STAGE_PREFIX):
            continue
        at = parse_iso(ev.get("created_at") or "")
        if at:
            stamped.append((at, name[len(STAGE_PREFIX):]))
    stamped.sort(key=lambda p: p[0])

    out = []
    for i, (at, lane) in enumerate(stamped):
        end = stamped[i + 1][0] if i + 1 < len(stamped) else None
        # A same-timestamp re-label leaves a zero-width interval; keep it out of
        # the output rather than emitting a lane no row can ever fall into.
        if end is not None and end <= at:
            continue
        out.append((at, end, lane))
    return out


# ADT-224 C2a -- which lane a command's turns belong to.
#
# The ledger's 12th column names a COMMAND; this module's axis is LANES, so the
# mapping has to be explicit. It is deliberately PARTIAL. Five commands name no
# lane at all (`decide`, `unblock`, the three `review-*`), and `build-todone`
# crosses plan->build->qa->done inside one autonomous run, so it is not one lane
# either. Those fall back to the label inference rather than being guessed at,
# which is what makes this change never WORSE than the behaviour it replaces --
# only better where a command does name a lane.
#
# Measured on this repo's usage.log (2026-09-06): 177 of 236 command
# invocations (75%) map to a lane -- plan 41, close 34, brief 32, qa-run 25,
# build 24, release-check 13, plan-fasttrack 7, block 1 -- and 59 (25%) fall
# back, of which build-todone is 17.
COMMAND_LANE = {
    "brief": "ideas",
    "plan": "planned",
    "plan-fasttrack": "planned",
    "build": "building",
    "qa-run": "qa",
    "release-check": "ready-to-release",
    "close": "done",
    "block": "blocked",
}


def command_lane(command: str):
    """-> lane name, or None when the command names no single lane.

    Tolerates the `adt-` prefix the usage hook writes verbatim, and the
    `subagent:<type>` values ADT-224 Phase B puts in the same column: a
    subagent's turns belong to whatever lane dispatched it, not to a lane of
    their own, so they fall through to the interval inference.
    """
    if not command:
        return None
    name = command.strip()
    if name.startswith("subagent:"):
        return None
    if name.startswith("adt-"):
        name = name[4:]
    return COMMAND_LANE.get(name)


def bucket_rows(lines, tix: str, intervals: list) -> dict:
    """{lane: [ledger lines]} for one ticket. Rows before the first interval
    (a turn that ran before the sync ever labelled the Issue) go to `pre`, which
    is reported rather than silently dropped -- an unexplained gap between a
    ticket's total and the sum of its lanes is worse than a named bucket.

    ADT-224: the ledger's own command column WINS over the label inference
    wherever it names a lane. The inference measures board state, not activity --
    visible in this module's own output before the change, where `track:
    standard` showed 77 turns in `ideas` against 2 in `building` because tickets
    were worked without the board moving. A row naming its command is direct
    evidence of what was being done; a stage label is evidence of what someone
    remembered to move.
    """
    want = adt_cost.canon_tix(tix)
    out: dict = {}
    for line in lines:
        row = adt_cost.parse_row(line)
        if not row or adt_cost.canon_tix(row["tix"]) != want:
            continue
        at = parse_iso(row["ts"])
        if at is None:
            continue
        # ADT-339 C5: the STAMPED lane wins over both the command column and the
        # label inference. Column 12 records the BINDING, not the lane, and
        # nothing re-stamped it on a lane move — measured 2026-09-10, `adt-brief`
        # held 509 of 906 minutes including 134 of ADT-301's plan work. The stamp
        # is read from the cache directory the ticket file sat in at the moment
        # of the turn, so it is direct evidence of where the work happened.
        # An empty or unrecognised value falls through, so pre-change rows and a
        # future lane rename both degrade to today's behaviour rather than
        # inventing a bucket.
        lane = row.get("lane") or None
        if lane is not None and lane not in LANE_ORDER:
            lane = None
        if lane is None:
            lane = command_lane(row.get("command", ""))
        if lane is None:
            for start, end, name in intervals:
                if at >= start and (end is None or at < end):
                    lane = name
                    break
            lane = lane or "pre"
        out.setdefault(lane, []).append(line)
    return out


def lane_summary(buckets: dict, intervals: list, prices, estimator) -> dict:
    """{lane: {turns, tokens, micros, tier, wall_s}} for one ticket.

    Cost is delegated to `adt_cost.price_ledger` per bucket rather than
    re-implemented, so a lane total is priced by exactly the code that prices a
    ticket total -- including its worst-wins tier rule.
    """
    wall: dict = {}
    for start, end, lane in intervals:
        if end is not None:
            wall[lane] = wall.get(lane, 0.0) + (end - start).total_seconds()

    out: dict = {}
    for lane, lines in buckets.items():
        priced = adt_cost.price_ledger(lines, prices, estimator)
        micros, tokens, tier = 0, 0, "measured"
        rank = {"measured": 0, "estimated": 1, "legacy": 2}
        any_priced = False
        for acc in priced.values():
            tokens += acc["tokens"]
            if acc["micros"] is not None:
                micros += acc["micros"]
                any_priced = True
            if rank.get(acc["tier"], 1) > rank[tier]:
                tier = acc["tier"]
        out[lane] = {
            "turns": len(lines),
            "tokens": tokens,
            "micros": micros if any_priced else None,
            "tier": tier,
            "wall_s": wall.get(lane, 0.0),
        }
    return out


# ------------------------------------------------------------------ inputs

def read_config(root: str) -> dict:
    """`repo:` etc. from .adt/config.yaml. Flat `key: value` only -- the same
    shape build_kanban's reader assumes, and all this file needs. Never
    hard-code a repo or a path (portability rule 1)."""
    out = {}
    try:
        with open(os.path.join(root, ".adt", "config.yaml"), encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^([A-Za-z_]+):\s*(.+?)\s*$", line)
                if m:
                    out[m.group(1)] = m.group(2)
    except OSError:
        pass
    return out


def frontmatter(path: str) -> dict:
    """Fence-aware frontmatter read (ADT-155: a `sed -n '1,40p' | grep '^key:'`
    runs past the closing fence and counts body text as a field, and it only
    ever over-counts, so it reads as confirmation and nobody re-checks it)."""
    out = {}
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return out
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return out
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_]+):\s*(.*?)\s*$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def gh_events(repo: str, number: str) -> list:
    """Issue label events. REST/core via `gh api` -- never the `gh issue`
    porcelain, which bills the separate GraphQL pool (ADT-109)."""
    try:
        r = subprocess.run(
            ["gh", "api", f"repos/{repo}/issues/{number}/events?per_page=100"],
            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return []
        return json.loads(r.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def ledger_lines(root: str, path: str | None = None) -> list:
    try:
        return open(path or _adt_state(root, LEDGER_LEAF), encoding="utf-8").readlines()
    except OSError:
        return []


# ------------------------------------------------------- document contracts

# The protocol must be PRE-REGISTERED and complete: the arms, the corpus, the
# traps, the metric list and the stop rule. Fixed strings, matched literally
# (ADT-168: a co-occurrence check over a window grades the WORDS and not the
# CLAIM, and no blocklist of negations closes that -- fixing the sentence and
# matching it literally makes polarity stop being something the check reasons
# about). NOTE the ASCII hyphen in the arm headings: an em dash here fails the
# match, which was verified at plan time and is why the spec fixes the spelling.
REQUIRED_EXPERIMENT = [
    "Arm A - control (no gates)",
    "Arm B - fast lane",
    "Arm C - full lane",
    "Traps are committed before any arm runs",
    "## Corpus",
    "## Metrics",
    "## Stop rule",
]

ARM_RE = re.compile(r"^\|\s*(arm-[ABC])\s*\|(.+)$", re.M)
TRACK_RE = re.compile(r"^\|\s*(track:\s*(?:fast|standard|full))\s*\|(.+)$", re.M)
TRAP_RE = re.compile(r"^\|\s*(trap-[123])\s*\|(.+)$", re.M)
MARGINAL_RE = re.compile(
    r"^Marginal cost per trap caught:\s*\$([0-9]+(?:\.[0-9]+)?)\b.*?\b([0-9]+) of 3 traps caught",
    re.M)
BLIND_RE = re.compile(r"^Arms run blind to the traps:\s*([0-9]+) of 9\b", re.M)
EVIDENCE_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|", re.M)
USD_RE = re.compile(r"\$\s*[0-9]+(?:\.[0-9]+)?")
# A range may carry a currency mark on BOTH ends ("$4.10-$9.30"), so the second
# number is allowed its own "$" -- without it a money range does not read as a
# range and every arm row graded short by exactly one measure.
RANGE_RE = re.compile(r"[0-9][0-9,.]*\s*[-–]\s*\$?\s*[0-9][0-9,.]*")


def _cells(rest: str) -> list:
    return [c.strip() for c in rest.rstrip().rstrip("|").split("|")]


def check_experiment(path: str) -> list:
    """Missing-element list; empty means the protocol is complete."""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return [f"cannot read {path}"]
    return [f"missing required section/phrase: {p!r}"
            for p in REQUIRED_EXPERIMENT if p not in text]


def check_report(path: str) -> list:
    """Missing-element list for the results report; empty means well-formed.

    Asserts ALL FOUR measures on each arm row, not the USD column alone: a
    coverage review found the first version graded only money, while success
    criterion 1 asks for wall-clock, tokens, USD and stop-points, each a range.
    """
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return [f"cannot read {path}"]
    problems: list = []
    ids: set = set()

    # Scan the RESULTS tables only above "## Evidence". The provenance table
    # uses the same ids in the same leading cell, so a whole-document scan let
    # each Evidence row overwrite its results row in these dicts (last match
    # wins) -- and an Evidence row carries a command and a date, never a USD
    # figure or a range, so every arm silently graded as malformed. Caught by
    # the contract tests, which is what they are for.
    head = text.split("## Evidence", 1)[0]

    arms = {m.group(1): _cells(m.group(2)) for m in ARM_RE.finditer(head)}
    for want in ("arm-A", "arm-B", "arm-C"):
        if want not in arms:
            problems.append(f"missing results row for {want}")
            continue
        ids.add(want)
        cells = arms[want]
        if not any(USD_RE.search(c) for c in cells):
            problems.append(f"{want}: no USD figure")
        # runs + four measures; the four measures must each read as a range.
        if len(RANGE_RE.findall(" ".join(cells))) < 4:
            problems.append(
                f"{want}: fewer than 4 range-shaped measures "
                "(wall-clock, tokens, USD, stop-points)")

    tracks = {re.sub(r"\s+", " ", m.group(1)): _cells(m.group(2))
              for m in TRACK_RE.finditer(head)}
    for want in ("track: fast", "track: standard", "track: full"):
        if want not in tracks:
            problems.append(f"missing retrospective row for {want!r}")
            continue
        ids.add(want)
        if not any(re.search(r"\bn=\d+", c) for c in tracks[want]):
            problems.append(f"{want!r}: no n= sample size")

    traps = {m.group(1): _cells(m.group(2)) for m in TRAP_RE.finditer(head)}
    for want in ("trap-1", "trap-2", "trap-3"):
        if want not in traps:
            problems.append(f"missing per-trap row for {want}")
            continue
        ids.add(want)
        joined = " ".join(traps[want])
        if not re.search(r"\barm-[ABC]\b", joined) and "none" not in joined.lower():
            problems.append(f"{want}: names no catching arm (or 'none')")
        if not USD_RE.search(joined):
            problems.append(f"{want}: no marginal cost figure")

    if not MARGINAL_RE.search(text):
        problems.append("missing/!malformed 'Marginal cost per trap caught:' line")
    else:
        ids.add("marginal-cost")
    if not BLIND_RE.search(text):
        problems.append("missing/malformed 'Arms run blind to the traps: N of 9' line")
    else:
        ids.add("blind-count")

    # Provenance (success criterion 6). This was originally left OUT of the DoD
    # on the stated ground that adt-phrase-linter.sh enforced it; that hook is a Stop
    # hook parsing the live transcript's last turn, warn-only and fail-open, and
    # never opens a committed file -- so a wrong reason had been standing in for
    # a check. Every quantitative id must carry a command and an ISO date.
    if "## Evidence" not in text:
        problems.append("missing '## Evidence' provenance table")
    else:
        ev = text.split("## Evidence", 1)[1]
        cited = {}
        for m in EVIDENCE_RE.finditer(ev):
            key = re.sub(r"\s+", " ", m.group(1)).strip()
            if key.lower() in ("id", "---", ""):
                continue
            cited[key] = (m.group(2).strip(), m.group(3))
        for i in sorted(ids):
            if i not in cited:
                problems.append(f"no Evidence row for {i!r} (command + date)")
            elif not cited[i][0] or set(cited[i][0]) <= {"-", " "}:
                problems.append(f"Evidence row for {i!r} has an empty command")
        for extra in sorted(set(cited) - ids):
            problems.append(f"Evidence row {extra!r} cites an id no table uses")
    return problems


# ------------------------------------------------------------- reconcile

# The committed-inputs contract. `--reconcile` recomputes every figure in the
# report from THESE files, so the report is reproducible by anyone with the repo
# and cannot be typed by hand. Both halves are covered: an earlier version
# reconciled only the live arm rows and left the retrospective `track:` rows
# assertable from the runner's recollection -- exactly what success criterion 3
# warns against, and a symmetric-half miss (ADT-101).
#
#   <runs>/retro-tickets.json   [{"id": "ADT-156", "track": "standard"}, ...]
#   <runs>/retro-ledger.log     the ledger rows those tickets were priced from
#   <runs>/retro-events/<ID>.json  that ticket's `stage:*` label events, which
#                               are what turn a flat ledger into per-lane cost
#   <runs>/retro-census.json    ONE `--by-track` capture (closed + post-merge
#                               defects per tier), cited by BOTH this report and
#                               docs/adt-metrics-baseline.md
#   <runs>/live-arms.json       {"arm-A": ["SPEED-1", ...], "arm-B": [...], ...}
#   <runs>/live-ledger.log      the scratch repo's own ledger
#   <runs>/live-runs.json       per-run tokens / duration_ms / stop_points --
#                               the only source for three of the arm table's
#                               four measures
#   <runs>/live-gitlog.txt      "<sha> <iso-date> <subject>" per line; trap
#                               commits are subject-prefixed "trap:", arm
#                               commits "arm-A:" / "arm-B:" / "arm-C:"
#   <runs>/detect.py            the trap detectors, held out of the repo the
#                               arms saw
#   <runs>/run-trees/<run>/     the 9 final trees, so a third party can re-run
#                               the detectors rather than trust their output
#   <runs>/trap-detections.json the detectors' recorded output over those trees
#
# QA (2026-09-05) mutation-tested the first version of this function: six
# single-figure corruptions of the report -- arm tokens, arm wall-clock, arm
# stop-points, a track row's plan-lane USD, a track row's qa-lane USD, and a
# defect count -- ALL still exited 0, because it compared only per-arm USD, the
# track totals and `n=`. A reconciler that passes a report whose headline
# separation (the 4.8x/6.2x wall-clock ratio) has been replaced with invented
# numbers is not reconciling the report. Every column of every results table is
# now recomputed, and the missing inputs above are what makes that possible.

def _usd(micros):
    return None if micros is None else round(micros / 1_000_000.0, 2)


def _totals_by_group(lines, groups: dict, prices, estimator) -> dict:
    """{group: usd} where `groups` maps a group name -> [ticket ids]."""
    priced = adt_cost.price_ledger(lines, prices, estimator)
    out = {}
    for name, ids in groups.items():
        micros, seen = 0, False
        for i in ids:
            acc = priced.get(adt_cost.canon_tix(i))
            if acc and acc["micros"] is not None:
                micros += acc["micros"]
                seen = True
        out[name] = _usd(micros) if seen else None
    return out


# The report's tables name their own columns, so the checks below address cells
# BY NAME rather than by position: a reordered or renamed column then fails
# loudly ("no <x> column") instead of silently reconciling the wrong cell
# against the right number.

def _table_rows(text: str, row_re) -> dict:
    """{row-key: {column-name: cell}} for the table whose rows match `row_re`."""
    header, out = None, {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            header = None
            continue
        cells = _cells(stripped.lstrip("|"))
        m = row_re.match(line)
        if m:
            if header:
                out[re.sub(r"\s+", " ", m.group(1)).strip()] = dict(
                    zip(header, [m.group(1)] + _cells(m.group(2))))
        elif set("".join(cells)) <= set("-: "):
            continue                                  # the |---|---| separator
        else:
            header = [c.strip().lower() for c in cells]
    return out


def _col(row: dict, *names):
    """The cell whose column name contains any of `names`; None if absent."""
    for want in names:
        for col, cell in row.items():
            if want in col:
                return cell
    return None


# No leading "-": the tables write ranges as "1.8-2.9", so a signed pattern
# reads the separator as a minus and returns (-2.9, 1.8) for a correct cell --
# a check that fails against the right answer. No figure in these tables is
# negative, so the sign has no legitimate use here.
_NUM_RE = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?")


def _nums(cell) -> list:
    """Every number in a cell, commas and currency marks stripped."""
    if cell is None:
        return []
    return [float(x.replace(",", "")) for x in _NUM_RE.findall(cell)]


def _span(cell):
    """(lo, hi) for a cell holding a range or a single value; None if numberless.

    A single number is a degenerate range, which is what lets `2-2` and `2` both
    reconcile against a set of three equal observations."""
    ns = _nums(cell)
    return (min(ns), max(ns)) if ns else None


def _agrees(cell, lo, hi, tol) -> bool:
    got = _span(cell)
    return got is not None and abs(got[0] - lo) <= tol and abs(got[1] - hi) <= tol


# The derived figures -- ratios, per-ticket means, marginal costs -- are stated
# as FIXED sentences and checked by prefix, the same idiom as the "Marginal cost
# per trap caught:" line (ADT-168: fix the sentence in the spec and grep it,
# rather than have a check reason about prose). They live outside the tables
# because a reader reads them in the argument, not in a grid -- and QA pass 2
# proved that "outside the tables" had meant "outside the checker": the report's
# headline 4.8x/6.2x wall-clock ratios matched no statistic at all, survived two
# passes, and were found only by recomputing them by hand.

def _stated(text: str, prefix: str):
    """{label: value} from the line beginning `prefix`; None if the line is absent.

    Absent is None rather than {} so the caller can report a MISSING sentence,
    which is a different failure from a sentence whose numbers disagree -- and a
    missing one must never read as "nothing to check"."""
    m = re.search(r"^" + re.escape(prefix) + r"\s*(.+)$", text, re.M)
    if m is None:
        return None
    out = {}
    for lm in re.finditer(
            r"(arm-[ABC]|track: (?:fast|standard|full))\s+\$?([0-9]+(?:\.[0-9]+)?)",
            m.group(1)):
        out[lm.group(1)] = float(lm.group(2))
    if not out:                      # a bare single-figure sentence
        n = re.search(r"\$?([0-9]+(?:\.[0-9]+)?)", m.group(1))
        if n:
            out[""] = float(n.group(1))
    return out


def _check_stated(text, prefix, want: dict, tol, problems, unit=""):
    """Compare one fixed sentence against recomputed values."""
    got = _stated(text, prefix)
    if got is None:
        problems.append(f"report states no {prefix!r} line")
        return
    for label in sorted(want):
        if label not in got:
            problems.append(f"{prefix!r}: no figure for {label or 'the value'}")
        elif abs(got[label] - want[label]) > tol:
            problems.append(
                f"{prefix!r}: {label or 'value'} is {got[label]}{unit}, "
                f"recomputed {round(want[label], 2)}{unit} from committed inputs")


def _lane_totals(runs_dir: str, tickets: list, lines: list, prices,
                 estimator) -> dict:
    """{track: {lane: micros}} -- the per-lane join, from committed inputs only.

    This is `collect()`'s join with the cache walk removed: `retro-tickets.json`
    already carries the (id, track) pairs the cache was read for, and the label
    events are committed per ticket. That is what makes the retrospective half a
    pure function of the repo, the same property the live half has."""
    agg: dict = {}
    for rec in tickets or []:
        tix = adt_cost.canon_tix(rec.get("id", ""))
        if not tix:
            continue
        try:
            with open(os.path.join(runs_dir, "retro-events", f"{tix}.json"),
                      encoding="utf-8") as fh:
                events = json.load(fh)
        except (OSError, ValueError):
            continue
        iv = lane_intervals(events)
        lanes = lane_summary(bucket_rows(lines, tix, iv), iv, prices, estimator)
        by_lane = agg.setdefault(f"track: {rec.get('track', 'unset')}", {})
        for lane, v in lanes.items():
            by_lane[lane] = by_lane.get(lane, 0) + (v["micros"] or 0)
    return agg


def reconcile(report_path: str, runs_dir: str, root: str) -> list:
    """Recompute the report from committed inputs. [] means every figure agrees."""
    problems: list = []

    def _load(name, default):
        try:
            with open(os.path.join(runs_dir, name), encoding="utf-8") as fh:
                return json.load(fh) if name.endswith(".json") else fh.readlines()
        except (OSError, ValueError):
            problems.append(f"missing/unreadable committed input: {name}")
            return default

    try:
        text = open(report_path, encoding="utf-8").read()
    except OSError:
        return [f"cannot read {report_path}"]

    prices = adt_cost.load_prices()
    # Prefer an estimator committed WITH the runs, so --reconcile stays a pure
    # function of the committed inputs and needs nothing from the grading host.
    estimator = None
    try:
        with open(os.path.join(runs_dir, "cost-estimator.json"), encoding="utf-8") as fh:
            estimator = json.load(fh)
    except (OSError, ValueError):
        estimator = adt_cost.load_estimator(root)
    # Results tables only -- the Evidence table repeats every id in the same
    # leading cell, so a whole-document scan lets provenance rows shadow the
    # rows being reconciled (the same defect check_report had; fixed in both
    # places rather than only where it was first seen).
    head = text.split("## Evidence", 1)[0]

    # --- retrospective half: the track: rows ------------------------------
    tickets = _load("retro-tickets.json", [])
    retro_lines = _load("retro-ledger.log", [])
    census = _load("retro-census.json", {})
    by_track: dict = {}
    for t in tickets or []:
        by_track.setdefault(f"track: {t.get('track', 'unset')}", []).append(t.get("id"))
    got = _totals_by_group(retro_lines, by_track, prices, estimator)
    lanes = _lane_totals(runs_dir, tickets, retro_lines, prices, estimator)
    counts = (census or {}).get("by_track", {})
    rows = _table_rows(head, TRACK_RE)
    for name, ids in sorted(by_track.items()):
        row = rows.get(name)
        if row is None:
            continue                      # check_report already reports absence
        joined = " ".join(row.values())
        m = re.search(r"\bn=(\d+)", joined)
        if m and int(m.group(1)) != len(ids):
            problems.append(
                f"{name}: report says n={m.group(1)}, committed inputs have "
                f"n={len(ids)}")
        want = got.get(name)
        if want is not None:
            cell = _col(row, "total usd")
            if cell is None:
                problems.append(f"{name}: no 'total USD' column to reconcile")
            elif not _agrees(cell, want, want, 0.02):
                problems.append(
                    f"{name}: report total {cell!r} does not match ${want:.2f} "
                    "recomputed from committed rows")
            per = round(want / len(ids), 2) if ids else None
            cell = _col(row, "per ticket")
            if per is not None and cell is not None and not _agrees(cell, per, per, 0.02):
                problems.append(
                    f"{name}: report per-ticket {cell!r} does not match "
                    f"${per:.2f} (total / n)")
        # The per-lane split IS the question this ticket was filed to answer, and
        # it was the one column nothing recomputed.
        for label, lane in (("plan lane", "planned"), ("qa lane", "qa")):
            cell = _col(row, label)
            if cell is None:
                problems.append(f"{name}: no {label!r} column to reconcile")
                continue
            usd = lanes.get(name, {}).get(lane, 0) / 1_000_000.0
            if not _agrees(cell, usd, usd, 0.02):
                problems.append(
                    f"{name}: report {label} {cell!r} does not match ${usd:.2f} "
                    "recomputed from the committed label events + ledger")
        # "<defects> of <closed> closed", against the ONE frozen census both
        # this report and docs/adt-metrics-baseline.md cite.
        cell = _col(row, "defect")
        key = name.split(":", 1)[1].strip()
        if cell is None:
            problems.append(f"{name}: no post-merge-defect column to reconcile")
        elif key in counts:
            ns = _nums(cell)
            want_d, want_c = counts[key]["defects"], counts[key]["closed"]
            if len(ns) < 2 or int(ns[0]) != want_d or int(ns[1]) != want_c:
                problems.append(
                    f"{name}: report defects {cell!r} does not match "
                    f"{want_d} of {want_c} closed in retro-census.json")
        elif counts:
            problems.append(f"{name}: retro-census.json carries no {key!r} tier")

    # The tier cost ratio. Found by the whole-report mutation sweep AFTER the
    # other derived figures were checked: "`full` costs 2.1x `standard`" is a
    # primary claim, not a restatement, and it fell outside all three categories
    # the report's own coverage paragraph enumerates. Same class as the defect
    # that failed QA twice -- a derived ratio stated in prose.
    ref = "track: standard"
    if got.get(ref):
        _check_stated(head, "Per-ticket cost ratio against track: standard:",
                      {name: (got[name] / len(by_track[name]))
                              / (got[ref] / len(by_track[ref]))
                       for name in by_track
                       if name != ref and got.get(name) and by_track[name]},
                      0.05, problems, "x")

    # The tier table's defect CELLS are checked above; this is the derived rate
    # the prose argues from, which is a different figure and was unchecked.
    if counts:
        _check_stated(
            head, "Post-merge defects per closed ticket:",
            {f"track: {k}": v["defects"] / v["closed"]
             for k, v in counts.items()
             if v.get("closed") and f"track: {k}" in rows},
            0.005, problems)

    # --- live half: the arm- rows -----------------------------------------
    arms = _load("live-arms.json", {})
    live_lines = _load("live-ledger.log", [])
    runs_meta = _load("live-runs.json", [])
    ids_seen = {adt_cost.canon_tix(r["tix"])
                for r in (adt_cost.parse_row(l) for l in live_lines)
                if r and r["tix"]}
    if len(ids_seen) < 9:
        problems.append(
            f"live ledger carries {len(ids_seen)} distinct run ids, expected >= 9 "
            "(3 arms x 3 matched tickets)")
    # PER-RUN, not per-arm-total. The report states each measure as a range
    # across an arm's runs (the protocol's metric list requires it), so summing
    # the arm and comparing that to a range compares two different quantities --
    # which is exactly what this check caught on its first real run.
    priced_live = adt_cost.price_ledger(live_lines, prices, estimator)
    arm_rows = _table_rows(head, ARM_RE)
    per_arm: dict = {}
    run_usd: dict = {}
    for r in runs_meta or []:
        per_arm.setdefault(r.get("arm"), []).append(r)
        acc = priced_live.get(adt_cost.canon_tix(r.get("run_id", "")))
        if acc and acc.get("micros") is not None:
            run_usd[(r.get("arm"), r.get("ticket"))] = acc["micros"] / 1_000_000.0
    for name, ids in sorted((arms or {}).items()):
        row = arm_rows.get(name)
        if row is None:
            continue
        per_run = [priced_live[adt_cost.canon_tix(i)]["micros"] / 1_000_000.0
                   for i in ids
                   if priced_live.get(adt_cost.canon_tix(i), {}).get("micros") is not None]
        if per_run:
            cell = _col(row, "usd")
            if cell is None:
                problems.append(f"{name}: no USD column to reconcile")
            elif not _agrees(cell, round(min(per_run), 2), round(max(per_run), 2), 0.02):
                problems.append(
                    f"{name}: report USD {cell!r} does not match "
                    f"{min(per_run):.2f}-{max(per_run):.2f} recomputed from "
                    "committed rows")
        # Three of the arm table's four measures live ONLY in live-runs.json.
        # Until this loop existed that file was committed and never opened, so
        # the wall-clock ratio the report leads with rested on nothing.
        obs = per_arm.get(name) or []
        if not obs:
            problems.append(f"{name}: live-runs.json carries no runs for this arm")
            continue
        cell = _col(row, "runs")
        if cell is not None and _nums(cell) and int(_nums(cell)[0]) != len(obs):
            problems.append(
                f"{name}: report says {cell!r} runs, live-runs.json has {len(obs)}")
        for label, key, scale, tol in (
                ("wall-clock", "duration_ms", 1 / 60000.0, 0.06),
                ("tokens", "tokens", 1.0, 0.5),
                ("stop-points", "stop_points", 1.0, 0.0)):
            vals = [r[key] * scale for r in obs if r.get(key) is not None]
            if not vals:
                problems.append(f"{name}: live-runs.json has no {key} to reconcile")
                continue
            cell = _col(row, label)
            if cell is None:
                problems.append(f"{name}: no {label!r} column to reconcile")
            elif not _agrees(cell, min(vals), max(vals), tol):
                problems.append(
                    f"{name}: report {label} {cell!r} does not match "
                    f"{min(vals):.1f}-{max(vals):.1f} in live-runs.json")

    # Derived arm figures: per-ticket money, mean wall-clock, the two ratio
    # families and the marginal costs. All are arithmetic over inputs already
    # loaded here, which is exactly why leaving them unchecked was indefensible.
    if per_arm and priced_live:
        mean_usd, mean_wall = {}, {}
        for name, ids in (arms or {}).items():
            vals = [priced_live[adt_cost.canon_tix(i)]["micros"] / 1_000_000.0
                    for i in ids
                    if priced_live.get(adt_cost.canon_tix(i), {}).get("micros") is not None]
            if vals:
                mean_usd[name] = sum(vals) / len(vals)
            obs = [r["duration_ms"] / 60000.0 for r in per_arm.get(name, [])
                   if r.get("duration_ms") is not None]
            if obs:
                mean_wall[name] = sum(obs) / len(obs)
        _check_stated(head, "Per ticket (mean of the arm's 3 runs):",
                      mean_usd, 0.02, problems)
        _check_stated(head, "Mean wall-clock:", mean_wall, 0.05, problems, " min")
        base = "arm-A"
        for prefix, src, tol in (
                ("Cost ratio to the control (mean/mean):", mean_usd, 0.05),
                ("Wall-clock ratio to the control (mean/mean):", mean_wall, 0.05)):
            if src.get(base):
                _check_stated(head, prefix,
                              {a: v / src[base] for a, v in src.items() if a != base},
                              tol, problems, "x")
        # Marginal costs are differences of the ROUNDED per-ticket figures, which
        # is what the report states and what a reader can redo by subtracting the
        # printed numbers. The tolerance carries the cent that convention costs.
        if base in mean_usd:
            r = {a: round(v, 2) for a, v in mean_usd.items()}
            _check_stated(head, "Marginal cost per ticket over the control:",
                          {a: r[a] - r[base] for a in r if a != base}, 0.02, problems)
            if "arm-B" in r and "arm-C" in r:
                _check_stated(
                    head,
                    "Marginal cost per ticket of the full lane over the fast lane:",
                    {"": r["arm-C"] - r["arm-B"]}, 0.02, problems)

    # --- traps: the detectors' recorded output ----------------------------
    # The trap half was previously graded for SHAPE only ("names an arm", "has a
    # USD figure"), so "3 of 3 caught" and "9/9 features landed" were assertions.
    det = _load("trap-detections.json", {})
    dets = {r.get("run"): r for r in (det or {}).get("runs", [])}
    if dets:
        landed = sum(1 for r in dets.values() if r.get("feature_landed"))
        survived = sum(1 for r in dets.values() if r.get("trap_survived"))
        for label, want, pat in (
                ("features landed", landed, r"(\d+)\s*/\s*(\d+)\s+features landed"),
                ("traps survived", survived, r"(\d+)\s*/\s*(\d+)\s+traps survived")):
            m = re.search(pat, text)
            if not m:
                problems.append(f"report states no '<n>/<n> {label}' count")
            elif (int(m.group(1)), int(m.group(2))) != (want, len(dets)):
                problems.append(
                    f"report says {m.group(1)}/{m.group(2)} {label}, "
                    f"trap-detections.json has {want}/{len(dets)}")
        caught, marginals = 0, []
        for row_key, row in sorted(_table_rows(head, TRAP_RE).items()):
            ticket = "t" + row_key.split("-")[1]
            joined = " ".join(row.values())
            hits = [r for r in dets.values()
                    if r.get("ticket") == ticket and r.get("feature_landed")
                    and not r.get("trap_survived")]
            caught += 1 if hits else 0
            m = re.search(r"\barm-([ABC])\b", joined)
            if m:
                arm = f"arm-{m.group(1)}"
                if not any(r.get("arm") == arm for r in hits):
                    problems.append(
                        f"{row_key}: report credits {arm}, but trap-detections.json "
                        f"has no {arm} run of {ticket} that landed the feature "
                        "with the trap gone")
                else:
                    # WHICH catcher is credited decides the marginal cost, and
                    # the marginal cost is what the recommendation argues from:
                    # crediting a dearer arm than the cheapest one that caught it
                    # invents a gate benefit. Cheapest = lowest USD in the arm
                    # table, the report's own ordering.
                    cheapest = min(
                        (r.get("arm") for r in hits),
                        key=lambda a: (_span(_col(arm_rows.get(a, {}), "usd"))
                                       or (float("inf"),))[0],
                        default=None)
                    if cheapest and cheapest != arm:
                        problems.append(
                            f"{row_key}: report credits {arm}, but {cheapest} "
                            "also caught it and costs less -- the marginal cost "
                            "of the gate is overstated")
                    # The marginal-cost CELL. Success criterion 2 defines it as
                    # what the catching arm spent over the arm that shipped the
                    # feature, so it is (credited arm's run) - (cheapest run that
                    # landed the feature) for THIS ticket. It was the last cell
                    # in the three results tables that nothing recomputed, while
                    # the report claimed all three were covered (QA pass 2).
                    landed = [r for r in dets.values()
                              if r.get("ticket") == ticket and r.get("feature_landed")]
                    costs = {r["arm"]: run_usd[(r["arm"], ticket)]
                             for r in landed if (r["arm"], ticket) in run_usd}
                    cell = _col(row, "marginal")
                    if cell is None:
                        problems.append(f"{row_key}: no marginal-cost column to reconcile")
                    elif arm in costs and costs:
                        want = costs[arm] - min(costs.values())
                        marginals.append(want)
                        if not _agrees(cell, want, want, 0.02):
                            problems.append(
                                f"{row_key}: marginal cost {cell!r} does not match "
                                f"${want:.2f} ({arm}'s run of {ticket} over the "
                                "cheapest run that landed the feature)")
            elif hits:
                problems.append(
                    f"{row_key}: report names no catching arm, but "
                    f"trap-detections.json shows {len(hits)} run(s) that caught it")
        m = MARGINAL_RE.search(text)
        if m and int(m.group(2)) != caught:
            problems.append(
                f"report says {m.group(2)} of 3 traps caught, "
                f"trap-detections.json shows {caught}")
        # ...and its DOLLAR figure, which is the per-trap column summarised:
        # total marginal cost over the traps that were caught. Checking the
        # count but not the money left the headline number of the whole trap
        # section assertable (QA pass 2's whole-report sweep).
        if m and caught and marginals:
            want = sum(marginals) / len(marginals)
            if abs(float(m.group(1)) - want) > 0.02:
                problems.append(
                    f"report says ${m.group(1)} marginal cost per trap caught, "
                    f"recomputed ${want:.2f} from the per-trap column")
        m = BLIND_RE.search(text)
        if m and int(m.group(1)) != len(dets):
            problems.append(
                f"report says {m.group(1)} of 9 arms ran blind, "
                f"trap-detections.json carries {len(dets)} runs")

    # --- ordering: traps were planted BEFORE any arm ran ------------------
    gitlog = _load("live-gitlog.txt", [])
    traps, arm_commits = [], []
    for line in gitlog or []:
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        when, subject = parse_iso(parts[1]), parts[2]
        if when is None:
            continue
        if subject.startswith("trap:"):
            traps.append(when)
        elif re.match(r"^arm-[ABC]:", subject):
            arm_commits.append(when)
    if not traps:
        problems.append("live-gitlog.txt carries no 'trap:' commits")
    elif arm_commits and max(traps) >= min(arm_commits):
        problems.append(
            "a trap commit is not strictly earlier than the first arm commit — "
            "the arms did not run blind")
    return problems


# ------------------------------------------------------------------ report

LANE_ORDER = ["pre", "ideas", "planned", "building", "qa", "blocked",
              "ready-to-release", "done"]


def collect(cache_dir: str, root: str, repo: str, ledger_path=None,
            events_dir=None) -> dict:
    """Walk the closed backlog and build {ticket_id: {track, lanes, covered}}.

    `events_dir` reads label events from committed JSON instead of calling `gh`,
    which is what makes this testable offline and what `--reconcile` relies on.
    """
    lines = ledger_lines(root, ledger_path)
    prices = adt_cost.load_prices()
    estimator = adt_cost.load_estimator(root)
    have = {adt_cost.canon_tix(r["tix"])
            for r in (adt_cost.parse_row(l) for l in lines) if r and r["tix"]}

    out: dict = {}
    for dirpath, _dirs, files in os.walk(cache_dir):
        if os.path.basename(dirpath) != "done":
            continue
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            fm = frontmatter(os.path.join(dirpath, name))
            tix = adt_cost.canon_tix(fm.get("id", ""))
            num = fm.get("issue_number", "")
            if not tix:
                continue
            rec = {"track": fm.get("track", "unset"), "lanes": {},
                   "covered": tix in have}
            if rec["covered"] and num:
                if events_dir:
                    try:
                        with open(os.path.join(events_dir, f"{tix}.json"),
                                  encoding="utf-8") as fh:
                            events = json.load(fh)
                    except (OSError, ValueError):
                        events = []
                else:
                    events = gh_events(repo, num)
                iv = lane_intervals(events)
                rec["lanes"] = lane_summary(bucket_rows(lines, tix, iv), iv,
                                            prices, estimator)
            out[tix] = rec
    return out


def render(data: dict) -> str:
    """Per-track lane table. Prints `n` and names what it could not cover: a
    per-lane average over an unstated denominator gets quoted without its
    caveat, and this tool's whole point is putting a defensible number on the
    table."""
    tracks: dict = {}
    for tix, rec in data.items():
        tracks.setdefault(rec["track"], []).append((tix, rec))
    lines = ["ADT lane cost", "=" * 60]
    for track in sorted(tracks):
        members = tracks[track]
        covered = [r for _t, r in members if r["covered"]]
        lines.append(f"\ntrack: {track}   n={len(covered)} covered "
                     f"of {len(members)} closed")
        if not covered:
            lines.append("  (no local ledger rows — cannot be split by lane)")
            continue
        agg: dict = {}
        for rec in covered:
            for lane, v in rec["lanes"].items():
                a = agg.setdefault(lane, {"turns": 0, "tokens": 0,
                                          "micros": 0, "wall_s": 0.0})
                a["turns"] += v["turns"]
                a["tokens"] += v["tokens"]
                a["micros"] += v["micros"] or 0
                a["wall_s"] += v["wall_s"]
        lines.append(f"  {'lane':<18}{'turns':>7}{'tokens':>12}"
                     f"{'USD':>10}{'wall (h)':>10}")
        for lane in LANE_ORDER + sorted(set(agg) - set(LANE_ORDER)):
            if lane not in agg:
                continue
            a = agg[lane]
            lines.append(f"  {lane:<18}{a['turns']:>7}{a['tokens']:>12,}"
                         f"{_usd(a['micros']):>10.2f}{a['wall_s']/3600:>10.1f}")
    missing = sorted(t for t, r in data.items() if not r["covered"])
    if missing:
        lines.append(f"\nno local ledger rows ({len(missing)}): "
                     + ", ".join(missing))
    return "\n".join(lines)


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Per-lane cost of the ADT lifecycle (ADT-205).")
    ap.add_argument("--cache-dir", help="backlog cache root (holds <type>/done/*.md)")
    ap.add_argument("--repo-root", default=".", help="repo root (ledger + config)")
    ap.add_argument("--ledger", help="override the ledger path")
    ap.add_argument("--events-dir", help="read label events from committed JSON")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--check-experiment", metavar="PATH")
    ap.add_argument("--check-report", metavar="PATH")
    ap.add_argument("--reconcile", metavar="PATH")
    ap.add_argument("--runs", metavar="DIR", help="committed run inputs for --reconcile")
    a = ap.parse_args(argv)

    for flag, fn in (("check_experiment", check_experiment),
                     ("check_report", check_report)):
        path = getattr(a, flag)
        if path:
            problems = fn(path)
            for p in problems:
                print(f"FAIL\t{p}")
            if not problems:
                print(f"OK\t{path}")
            return 1 if problems else 0

    if a.reconcile:
        if not a.runs:
            print("FAIL\t--reconcile requires --runs <dir>")
            return 2
        problems = reconcile(a.reconcile, a.runs, a.repo_root)
        for p in problems:
            print(f"FAIL\t{p}")
        if not problems:
            print(f"OK\t{a.reconcile} reconciles against {a.runs}")
        return 1 if problems else 0

    if not a.cache_dir:
        ap.error("--cache-dir is required unless a --check-*/--reconcile flag is given")
    cfg = read_config(a.repo_root)
    data = collect(a.cache_dir, a.repo_root, cfg.get("repo", ""),
                   a.ledger, a.events_dir)
    print(json.dumps(data, indent=2, default=str) if a.json else render(data))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())

#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
"""pass^k over DoD DERIVATION — the narrowed reliability metric (ADT-126).

WHAT THIS IS NOT. Full `pass^k` re-runs a whole ticket k times and requires all
k to pass. That needs a harness that can drive a ticket brief->merge unattended,
an automated per-run grader, and k x full build cost per ticket. None of that
exists, and scoping it as one bullet is what made ADT-126's first draft
over-scoped. This replays the ONE stage that already has a grader.

WHAT IT IS. For each closed ticket in the eval set: strip the Definition of Done
(both the `done_evidence:` frontmatter and the `### Definition of Done` section),
hand the remaining spec to a fresh derivation k times, and score the ticket
**1 only if ALL k derivations pass BOTH graders**.

TWO GRADERS, NOT ONE. `dod-coverage-reviewer` answers "does this DoD cover the
spec?"; `adt_dod.unsupported()` answers "is every condition actually checkable?".
Grading with the LLM alone would sit at its ceiling before the work started —
asked whether a DoD derived FROM a spec covers that spec, a model says yes — and
the mechanical grader is the only half that can return a red on its own. A prose
condition ("works well") is exactly what a re-derived DoD produces, and only
`adt_dod` sees it.

    pass^k = (tickets where all k derivations passed) / (tickets attempted)

`pass@k` — "at least one of k succeeded" — is the wrong estimator here and will
look flattering while reliability does not move. The distinction is the whole
metric, so `aggregate_ticket()` is `all(...)` and is pinned by its own test.

AGENT IN THE LOOP, LIKE THE CALIBRATION HARNESS. A python script cannot spawn a
subagent, and pretending otherwise would put a fake grader in the loop. So this
splits, exactly as tools/calibration/ does:

    --prepare   write the k stripped specs per ticket into a runs directory
    --score     read the saved verdicts back, aggregate, write the baseline doc

The verdict files in between are written by real separate-context reviewers.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

DONE_EVIDENCE_RE = re.compile(
    r"^done_evidence:\n(?:[ \t]+.*\n|\n(?=[ \t]))*", re.MULTILINE)
DOD_SECTION_RE = re.compile(
    r"^### Definition of Done.*?(?=^### |\Z)", re.MULTILINE | re.DOTALL)
# Sections that DISCUSS the DoD rather than contain it. A `### DoD-coverage
# review` block names conditions ("backed by test_x", "22 -> 35 conditions") and a
# `### Plan-critique` note summarises what the DoD gained each round. Leaving
# either in hands the derivation the answer in prose — found when a derivation
# agent self-reported seeing `test_tick_rotates_before_it_writes` and "the
# original had 7 conditions" in a spec whose DoD had supposedly been stripped.
DOD_DISCUSSION_RE = re.compile(
    r"^### (?:DoD-coverage review|Plan-quality review|Plan-critique).*?(?=^### |\Z)",
    re.MULTILINE | re.DOTALL)
VERDICT_RE = re.compile(r"^\*\*Verdict:\*\*\s*([A-Za-z-]+)", re.MULTILINE)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import adt_dod  # noqa: E402

DESIGN_RE = re.compile(r"^### Design\s*$", re.MULTILINE)


def strip_dod(text):
    """Remove the DoD from a ticket, leaving the spec that must re-derive it.

    THREE things go, not two: the `done_evidence:` frontmatter block (what the
    grader reads), the `### Definition of Done` prose section (where it is
    authored), and any section that DISCUSSES the DoD — a recorded coverage or
    plan-quality review, or a plan-critique note. The third was missed at first
    and is the subtler leak: those blocks name individual conditions and their
    counts, so a spec can look stripped while still telling the derivation what to
    write."""
    out = DONE_EVIDENCE_RE.sub("", text)
    out = DOD_SECTION_RE.sub("", out)
    out = DOD_DISCUSSION_RE.sub("", out)
    return out


def has_dod(text):
    return bool(DONE_EVIDENCE_RE.search(text))


def eval_set(cache_dir):
    """Closed tickets carrying BOTH a DoD and a Design — the replayable corpus.

    A ticket with no Design gives the derivation nothing to work from; a ticket
    with no DoD has no ground truth to be compared against. Either way it is not
    an eval case, and counting it would flatter the denominator."""
    out = []
    for root, _dirs, files in os.walk(cache_dir):
        if os.path.basename(root) != "done":
            continue
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            try:
                text = open(path, encoding="utf-8").read()
            except OSError:
                continue
            if has_dod(text) and DESIGN_RE.search(text):
                out.append(path)
    return sorted(out)


def read_verdict(path):
    """The recorded verdict string, '' if unparseable, None if the run is absent."""
    if not os.path.isfile(path):
        return None
    m = VERDICT_RE.search(open(path, encoding="utf-8").read())
    return m.group(1).strip().upper() if m else ""


def checkable(derived_md_path):
    """True when adt_dod finds every derived condition mechanically gradeable.

    The second grader. `unsupported()` returns the conditions it cannot check —
    prose, un-runnable commands, a `file:` pointed at a source path. Empty means
    the derived DoD is gradeable; anything in it means the derivation produced
    something that only LOOKS like a condition."""
    try:
        return not adt_dod.unsupported(derived_md_path)
    except Exception:
        return False


def run_passed(verdict, is_checkable):
    """ONE derivation passes only if BOTH graders pass it."""
    return verdict == "COVERED" and bool(is_checkable)


def aggregate_ticket(verdicts, k, checkables=None):
    """pass^k for ONE ticket: 1 only if ALL k runs pass BOTH graders.

    THE metric's identity lives here. `any(...)` would be pass@k and would score
    a ticket that succeeded once in five as a success. A short list — fewer than
    k runs recorded — is NOT a pass: an unrun derivation is unknown, and unknown
    is not success."""
    if len(verdicts) < k:
        return False
    if checkables is None:
        checkables = [True] * len(verdicts)
    if len(checkables) < k:
        return False
    return all(run_passed(v, c) for v, c in zip(verdicts[:k], checkables[:k]))


TRACK_RE = re.compile(r"^track:\s*(\S+)", re.MULTILINE)
SIZE_RE = re.compile(r"^size:\s*(\S+)", re.MULTILINE)


def stratum(ticket_path):
    """The ticket's `track:` (falling back to `size:`) for stratified reporting.

    k stays UNIFORM across tickets — varying it by complexity would make pass^3
    and pass^5 rows incomparable, and would key the bar on `track:`/`size:` fields
    the graded planning process set itself. Complexity-sensitivity belongs in the
    REPORT, not in the measurement."""
    try:
        text = open(ticket_path, encoding="utf-8").read()
    except OSError:
        return "unknown"
    m = TRACK_RE.search(text) or SIZE_RE.search(text)
    return m.group(1) if m else "unknown"


def ticket_status(verdicts, checkables, k):
    """"pass" | "fail" | "incomplete" for one ticket.

    EARLY-STOP IS EXACT. `pass^k` requires ALL k runs, so the first failing run
    settles the ticket at 0 — the remaining runs cannot change it and need never
    be spent. So a recorded failure is a **fail**, not an incomplete, even with
    fewer than k verdicts on disk. `incomplete` is reserved for its real meaning:
    every recorded run passed but the ticket has not finished its k runs yet.
    Conflating the two would either report an already-settled failure as unknown
    (understating unreliability) or a genuinely unfinished ticket as failed."""
    for v, c in zip(verdicts, checkables):
        if not run_passed(v, c):
            return "fail"
    return "pass" if len(verdicts) >= k else "incomplete"


def score(runs_dir, tickets, k):
    """Return (rows, passed, attempted, incomplete)."""
    rows, passed, attempted, incomplete = [], 0, 0, []
    for path in tickets:
        slug = os.path.splitext(os.path.basename(path))[0]
        present, checks = [], []
        for i in range(k):
            v = read_verdict(os.path.join(runs_dir, slug, "%d.verdict" % i))
            if v is None:
                continue
            present.append(v)
            derived = os.path.join(runs_dir, slug, "%d.derived.md" % i)
            checks.append(checkable(derived) if os.path.isfile(derived) else False)
        status = ticket_status(present, checks, k)
        if status == "incomplete":
            incomplete.append(slug)
        attempted += 1
        passed += (status == "pass")
        rows.append((slug, present, status, stratum(path)))
    return rows, passed, attempted, incomplete


# ── isolation: what a script CAN and CANNOT enforce ───────────────────────
# A python script cannot sandbox a subagent's filesystem. What it CAN do is
# (a) guarantee the spec it hands over carries no residue of the answer, and
# (b) emit the exclusion list, so the operator's prompt is GENERATED rather than
# hand-written and cannot silently omit a vector. Both matter: the 2026-08-22 run
# leaked three ways, and each was found only because an agent volunteered it.
# STRUCTURE, not mentions. These are line-anchored on purpose: a ticket ABOUT
# DoDs (ADT-114 is one) says "`done_evidence:` is authored by the same agent" and
# "output a `### DoD-coverage review` block" in its own prose, and a substring
# match flags both. That is a false positive, and a leak check that fires on
# every DoD-related ticket would be turned off within a week. What makes residue
# residue is that it is a HEADING or a CONDITION LIST, at line start.
LEAK_MARKERS = (
    (r"^done_evidence:", "done_evidence: block"),
    (r"^### Definition of Done", "### Definition of Done section"),
    (r"^### DoD-coverage review", "### DoD-coverage review block"),
    (r"^### Plan-quality review", "### Plan-quality review block"),
    (r"^### Plan-critique", "### Plan-critique note"),
    (r"^[ \t]+- must_run:", "a must_run condition list"),
    (r"^[ \t]+(?:- )?must_contain_regex:", "a must_contain_regex condition"),
)


def leak_check(spec_text):
    """Residue of the DoD left in a spec that is supposed to be stripped.

    Returns human-readable names of what was found — empty means clean. Matches
    STRUCTURE (line-anchored headings and condition lists), never prose mentions,
    so a ticket that discusses DoDs is not flagged for discussing them. This is
    the half of isolation a script can actually guarantee, so `--prepare` refuses
    rather than warns."""
    return [name for pat, name in LEAK_MARKERS
            if re.search(pat, spec_text, re.MULTILINE)]


def excluded_paths(cache_dir, devteam=None):
    """Paths that hold the answers and must be unreachable during a derivation.

    Generated, not remembered. Each entry here is a vector that actually leaked."""
    out = [os.path.abspath(cache_dir)]
    if devteam:
        out.append(os.path.join(os.path.abspath(devteam), "tickets"))
    return out


ISOLATION_DOC = """# Isolation contract for this replay run

A derivation agent that reads any path below has seen the answer, and its run is
void. A script cannot enforce this — it is filesystem access the harness does not
control — so it is stated here and MUST be pasted into every derivation prompt.

## Do not read

%s

## Also require of the agent

- **Write once.** Decide the full condition set before writing, then write the
  output file exactly once. A revising agent produces verdicts graded against
  versions that no longer exist.
- **Self-report.** If an original DoD is seen accidentally, say so explicitly.
  Every leak in the 2026-08-22 run was found this way and no other way.

## What the harness already guarantees

Every prepared spec passed `leak_check()` — no `done_evidence:`, no
`### Definition of Done`, and no DoD-*discussing* section (`### DoD-coverage
review`, `### Plan-quality review`, `### Plan-critique`), which name individual
conditions and are the subtler leak.
"""


def prepare(runs_dir, tickets, k, cache_dir=None, devteam=None):
    """Write the k stripped specs a reviewer will be handed, one dir per ticket.

    REFUSES on any spec that still carries DoD residue — a leaky spec silently
    inflates the score, so failing loudly here is the whole point. Also writes
    ISOLATION.md naming the paths that must be unreachable."""
    made, leaky = 0, []
    for path in tickets:
        slug = os.path.splitext(os.path.basename(path))[0]
        d = os.path.join(runs_dir, slug)
        os.makedirs(d, exist_ok=True)
        stripped = strip_dod(open(path, encoding="utf-8").read())
        found = leak_check(stripped)
        if found:
            leaky.append((slug, found))
            continue
        for i in range(k):
            with open(os.path.join(d, "%d.spec.md" % i), "w", encoding="utf-8") as fh:
                fh.write(stripped)
            made += 1
    if leaky:
        raise ValueError("prepared specs still carry DoD residue: " + "; ".join(
            "%s -> %s" % (s, ", ".join(f)) for s, f in leaky))
    if cache_dir:
        os.makedirs(runs_dir, exist_ok=True)
        with open(os.path.join(runs_dir, "ISOLATION.md"), "w", encoding="utf-8") as fh:
            fh.write(ISOLATION_DOC % "\n".join(
                "- `%s`" % p for p in excluded_paths(cache_dir, devteam)))
    return made


def render(rows, passed, attempted, incomplete, k, corpus_note=True):
    rate = (float(passed) / attempted) if attempted else 0.0
    lines = [
        "# pass^k — DoD-derivation replay baseline",
        "",
        "Generated by `tools/replay/passk.py --score`. **This is not full",
        "ticket-replay `pass^k`** — see that file's docstring for why the full",
        "form was descoped. It replays DoD *derivation*, the one stage that",
        "already has a grader (`dod-coverage-reviewer`).",
        "",
        "k = %d" % k,
        "",
        "pass^k = %d/%d = %.2f" % (passed, attempted, rate),
        "",
        "A ticket scores 1 only if **all %d** derivations passed BOTH graders —" % k,
        "`dod-coverage-reviewer` (COVERED) *and* `adt_dod` (every condition",
        "mechanically checkable). The LLM half alone would sit at its ceiling:",
        "asked whether a DoD derived from a spec covers that spec, a model says yes.",
        "`pass@k` (at least one of %d) is the wrong estimator and would look" % k,
        "flattering while reliability did not move.",
        "",
        "| ticket | verdicts | result | track |",
        "|---|---|---|---|",
    ]
    lines += ["| `%s` | %s | %s | %s |" % (slug, ", ".join(v or "(unparsed)" for v in vs) or "—", res, strat)
              for slug, vs, res, strat in rows]
    strata = {}
    for _slug, _vs, res, strat in rows:
        d = strata.setdefault(strat, [0, 0])
        d[1] += 1
        d[0] += (res == "pass")
    if len(strata) > 1:
        lines += ["", "## By track — same k everywhere, split only in the report", "",
                  "| track | pass^%d |" % k, "|---|---|"]
        lines += ["| `%s` | %d/%d |" % (s, v[0], v[1]) for s, v in sorted(strata.items())]
        lines += ["", "k is UNIFORM across every row. Varying it by complexity would",
                  "make the rows incomparable (all-of-5 is a harder bar than all-of-3)",
                  "and would key the bar on fields the graded planning process set",
                  "itself. Complexity-sensitivity lives here, in the split.", ""]
    if incomplete:
        lines += ["", "> **Incomplete: %d ticket(s) that have not finished k runs** — %s."
                       % (len(incomplete), ", ".join("`%s`" % s for s in incomplete)),
                  "> Every recorded run passed; they simply have not run k times yet.",
                  "> They are scored as NOT passing: an unrun derivation is unknown,",
                  "> and unknown is not success. A ticket with a FAILED run is",
                  "> reported `fail`, not incomplete — pass^k requires all k, so the",
                  "> first failure settles it and the rest are never spent."]
    if corpus_note:
        lines += ["", "## Corpus size — a stated limit, not a footnote", "",
                  "The eval set is every closed ticket carrying both a `### Design`",
                  "and a `done_evidence:` block. That is %d tickets." % attempted,
                  "Anthropic's evals guidance suggests 20-50 tasks drawn from real",
                  "failures; this is below that, so the number is a floor with wide",
                  "error bars, not a precise rate. It grows as tickets close.", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir", required=True, help="the project's backlog cache")
    ap.add_argument("--runs-dir", required=True, help="where specs + verdicts live")
    ap.add_argument("-k", type=int, default=3, help="derivations per ticket (default 3)")
    ap.add_argument("--out", default=None, help="baseline doc to write")
    ap.add_argument("--devteam", default=None,
                    help="the project's .adt/ — its kanban.html renders "
                         "the original DoD and is named in the isolation contract")
    ap.add_argument("--prepare", action="store_true", help="write the stripped specs")
    ap.add_argument("--score", action="store_true", help="aggregate the saved verdicts")
    args = ap.parse_args(argv)
    if not (args.prepare or args.score):
        ap.error("pass --prepare or --score")

    tickets = eval_set(args.cache_dir)
    if args.prepare:
        n = prepare(args.runs_dir, tickets, args.k, args.cache_dir, args.devteam)
        print("prepared %d specs across %d tickets (k=%d); isolation contract at %s"
              % (n, len(tickets), args.k, os.path.join(args.runs_dir, "ISOLATION.md")))
    if args.score:
        rows, passed, attempted, incomplete = score(args.runs_dir, tickets, args.k)
        body = render(rows, passed, attempted, incomplete, args.k)
        if args.out:
            os.makedirs(os.path.dirname(args.out), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(body)
            print("wrote %s — pass^%d = %d/%d%s"
                  % (args.out, args.k, passed, attempted,
                     ", %d incomplete" % len(incomplete) if incomplete else ""))
        else:
            print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())

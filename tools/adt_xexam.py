#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
"""Cross-examination rate — how often the human has to challenge the agent (ADT-126).

ADT-114 wanted this and could not have it: "needs transcript mining". The mining
turns out to be cheap, because the join key already exists —
`.adt/state/cost-ledger.log` maps session id -> ticket for the
whole history. What is NOT cheap is the transcripts: they are retained per
machine and expire, so most closed tickets have no transcript left to read.

THAT IS WHY COVERAGE IS REPORTED WITH EVERY COUNT. A ticket whose transcripts
are gone must read as **unmeasured**, never as **quiet** — otherwise the metric
improves every time a transcript ages out, which is the most flattering possible
failure mode for a trust metric.

THE CHALLENGE DETECTOR IS A STATED PROXY. A regex cannot know intent. It is
labelled a proxy in the record it writes, and its coverage figure travels beside
every number so a gap cannot read as a good result.

Not every `role: user` line is a human. Verified against real transcripts, the
same role carries slash-command expansions (`<command-message>`), tool results
(list content of `tool_result` blocks), task notifications and system reminders.
Counting those would measure the harness, not the human.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

# Reuse the EXISTING session-id -> transcript-path index rather than writing a
# second one. `adt_backfill_cost.index_transcripts()` already globs the Claude
# Code project dirs and is covered by tools/tests/test_cost_backfill.py; a second
# indexer that drifts from the first is the canon-normalisation failure class ADT
# has already paid for. Its sibling `session_messages()` is NOT reused — it
# returns assistant usage dicts, and this tool needs the human turns.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adt_backfill_cost import index_transcripts  # noqa: E402

# ── what counts as a human turn (verified against real transcripts) ────────
_MACHINE_PREFIXES = ("<command-message>", "<command-name>", "<local-command",
                     "<task-notification>", "<system-reminder>", "<bash-input>",
                     "<bash-stdout>", "<user-prompt-submit-hook>")

# ── the proxy ──────────────────────────────────────────────────────────────
# Challenge-shaped: the human disputing, verifying, or correcting a claim — not
# merely instructing. "build it" and "merge" are instructions; "are you sure" and
# "check if that is real" are cross-examination.
_CHALLENGE_RE = re.compile(
    r"\b("
    r"are you sure|is that (true|right|correct)|did you (actually|really)"
    r"|really\b.*\?|prove it|verify (that|this|it)|check (if|whether)"
    r"|mak(e|ing) (it|that|stuff|things) up|made (that|it) up|making stuff up"
    r"|that'?s (wrong|not right|incorrect)|you (said|claimed|told me)"
    r"|why did you|why are you|why is (it|that|there)"
    r"|(is|are) (this|that|these|those) real|i don'?t believe"
    r"|still (showing|broken|failing|wrong)|but you"
    r"|explain (the|why|how|what)"
    r")",
    re.IGNORECASE)


def human_turns(transcript_path):
    """Yield the text of each turn a PERSON typed, in order.

    Excludes sidechains (subagent turns), non-external userTypes, list content
    (tool results), and machine-generated `role: user` text."""
    try:
        fh = open(transcript_path, encoding="utf-8")
    except OSError:
        return
    with fh:
        for line in fh:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") != "user" or o.get("isSidechain"):
                continue
            if o.get("userType") not in (None, "external"):
                continue
            content = (o.get("message") or {}).get("content")
            if not isinstance(content, str):
                continue          # list content is tool_result / attachments
            text = content.strip()
            if not text or text.startswith(_MACHINE_PREFIXES):
                continue
            yield text


def is_challenge(text):
    return bool(_CHALLENGE_RE.search(text))


def ledger_sessions(ledger_path):
    """{ticket: {session_id, ...}} from the cost ledger (tab-separated)."""
    out = {}
    try:
        fh = open(ledger_path, encoding="utf-8")
    except OSError:
        return out
    with fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            tix, session = parts[1].strip(), parts[4].strip()
            if not session or not tix or tix.startswith("__"):
                continue
            out.setdefault(tix, set()).add(session)
    return out


def transcript_index(projects_dirs=None):
    """{session_id: path}. Delegates to the shared indexer; a list of directories
    is turned into the glob patterns it expects."""
    if not projects_dirs:
        return index_transcripts()
    return index_transcripts([os.path.join(d, "*.jsonl") for d in projects_dirs])


def measure(ledger_path, projects_dirs=None):
    """Per-ticket challenge counts WITH coverage.

    `covered`/`sessions` is the fraction of a ticket's sessions whose transcript
    still exists. When it is 0 the ticket is unmeasured and its count is None —
    not 0. A count of 0 means "we read the transcripts and the human never
    challenged"; None means "we could not look"."""
    index = transcript_index(projects_dirs)
    rows = []
    for tix, sessions in sorted(ledger_sessions(ledger_path).items()):
        found = [(s, index.get(s)) for s in sorted(sessions)]
        present = [p for _s, p in found if p]
        if not present:
            rows.append({"ticket": tix, "challenges": None, "turns": None,
                         "covered": 0, "sessions": len(found)})
            continue
        challenges = turns = 0
        for path in present:
            for text in human_turns(path):
                turns += 1
                challenges += is_challenge(text)
        rows.append({"ticket": tix, "challenges": challenges, "turns": turns,
                     "covered": len(present), "sessions": len(found)})
    return rows


def render(rows):
    measured = [r for r in rows if r["challenges"] is not None]
    total_sessions = sum(r["sessions"] for r in rows)
    total_covered = sum(r["covered"] for r in rows)
    lines = [
        "# Cross-examination rate",
        "",
        "Generated by `tools/adt_xexam.py`. Challenge-shaped human turns per",
        "ticket — the human disputing, verifying or correcting a claim rather",
        "than instructing.",
        "",
        "**This is a stated proxy.** A regex cannot know intent; it matches",
        "challenge-shaped phrasing. Read it as a trend, never as a count of",
        "times the agent was wrong.",
        "",
        "sessions with a retained transcript: %d/%d" % (total_covered, total_sessions),
        "tickets measurable: %d/%d" % (len(measured), len(rows)),
        "",
        "A ticket with no retained transcript is **unmeasured**, not quiet. Its",
        "row reads `—`, never `0`. Without that distinction this metric improves",
        "every time a transcript ages out.",
        "",
        "| ticket | challenges | human turns | transcripts |",
        "|---|---|---|---|",
    ]
    for r in rows:
        lines.append("| `%s` | %s | %s | %d/%d |" % (
            r["ticket"],
            "—" if r["challenges"] is None else r["challenges"],
            "—" if r["turns"] is None else r["turns"],
            r["covered"], r["sessions"]))
    if measured:
        c = sum(r["challenges"] for r in measured)
        t = sum(r["turns"] for r in measured)
        lines += ["", "Across the measurable tickets: %d challenge-shaped turns of %d"
                       % (c, t),
                  "human turns (%.0f%%)." % (100.0 * c / t if t else 0.0)]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", required=True, help="cost-ledger.log")
    ap.add_argument("--projects-dir", action="append", default=[],
                    help="a ~/.claude/projects/<slug> dir (repeatable)")
    ap.add_argument("--out", default=None, help="write the record here")
    args = ap.parse_args(argv)
    body = render(measure(args.ledger, args.projects_dir))
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(body)
        print("wrote " + args.out)
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
"""Score the plan-quality-reviewer against the calibration cases (ADT-126).

Reads one `cases/<name>.verdict` per case (the reviewer's output block, saved
verbatim), compares it to `expected.json`, and writes
`docs/plan-quality-calibration.md`.

Deliberately dumb: it does not judge the reviewer's prose, only whether the
verdict matched and whether a flawed case named the thing it was supposed to
catch. The point is a number a human can read, plus ONE machine-readable line —
`gating: ENABLED|DISABLED` — that `adt_dod.py` reads to decide whether it may
refuse on this judge's say-so.

THE BAR IS DECLARED IN README.md AND MIRRORED HERE. It was committed before the
first verdict existed, so it cannot be fitted to the score.

A POOR SCORE IS A LEGITIMATE RESULT. It leaves `gating: DISABLED`, which means
the reviewer still runs and its verdict is still recorded — only the automatic
refusal is withheld. The correct response is to leave it disabled, never to
re-run the calibration until it looks good. `--require-complete` therefore fails
on an UNRUN case, never on a low score.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, "cases")
# HERE is tools/calibration/plan-quality → three levels up is the repo root.
ADT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
OUT = os.path.join(ADT_DIR, "docs", "plan-quality-calibration.md")

# ── THE BAR (see README.md — declared before any verdict existed) ──────────
#
# GATING IS DECIDED ON THE HELD-OUT SUBSET ONLY (ADT-126, found by the
# plan-quality-reviewer reviewing ADT-126 itself). The first six flawed cases are
# drawn from the same INSTANCES the reviewer's own prompt cites as worked
# examples, so their score measures recall on material the judge was handed — it
# is near-guaranteed to clear any bar and says nothing about generalisation.
# Declaring the bar before scoring stops goalpost-moving; it does nothing about a
# contaminated eval set. The held-out cases use instances the prompt never
# mentions, so they are the only ones that can support a gating decision. The
# contaminated cases are still scored and still reported — as a recall floor.
MIN_HELD_OUT_CAUGHT = 2   # of 3 held-out gapped cases
MAX_FALSE_POSITIVES = 1   # of 2 clean cases

VERDICT_RE = re.compile(r"^\*\*Verdict:\*\*\s*([A-Za-z-]+)", re.MULTILINE)


def read_verdict(path):
    """Return (verdict, full_text) or (None, '') when the case was not run."""
    if not os.path.isfile(path):
        return None, ""
    text = open(path, encoding="utf-8").read()
    m = VERDICT_RE.search(text)
    return (m.group(1).strip().upper() if m else ""), text


def score(expected):
    """Return (rows, missing, caught, gapped, fp, clean, held_caught, held_gapped)."""
    rows, missing = [], []
    caught = gapped = fp = clean = held_caught = held_gapped = 0
    for name in sorted(expected):
        spec = expected[name]
        verdict, text = read_verdict(os.path.join(CASES, name + ".verdict"))
        want = spec["expect"]
        is_gapped = want == "FLAWED"
        held = bool(spec.get("held_out"))
        gapped += is_gapped
        held_gapped += (is_gapped and held)
        clean += (not is_gapped)
        if verdict is None:
            missing.append(name)
            rows.append((name, want, "(not run)", "—",
                         ("HELD OUT · " if held else "") + (spec["failure_class"] or "—")))
            continue
        hit = verdict == want
        # A flawed case only counts as caught if it also NAMED the defect — a
        # FLAWED verdict pointing at the wrong thing is a lucky guess.
        named = all(tok.lower() in text.lower() for tok in spec.get("must_mention", []))
        if is_gapped:
            caught += bool(hit and named)
            held_caught += bool(hit and named and held)
            note = "caught" if (hit and named) else (
                "verdict right, defect not named" if hit else "MISSED")
        else:
            fp += (not hit)
            note = "ok" if hit else "FALSE POSITIVE"
        rows.append((name, want, verdict, note,
                     ("HELD OUT · " if held else "") + (spec["failure_class"] or "—")))
    return rows, missing, caught, gapped, fp, clean, held_caught, held_gapped


def gating_decision(held_caught, fp, missing):
    """ENABLED only if the calibration is complete AND the HELD-OUT subset clears
    the declared bar. The contaminated cases cannot buy gating."""
    if missing:
        return False
    return held_caught >= MIN_HELD_OUT_CAUGHT and fp <= MAX_FALSE_POSITIVES


def render(rows, missing, caught, gapped, fp, clean, held_caught, held_gapped,
           expected=None):
    expected = expected or {}
    enabled = gating_decision(held_caught, fp, missing)
    lines = [
        "# plan-quality-reviewer — calibration record",
        "",
        "Generated by `tools/calibration/plan-quality/score.py`. See that",
        "directory's README for why this is a record a human reads rather than a",
        "test: an LLM judge cannot be graded hermetically, so this measures whether",
        "the reviewer is *worth* gating on — and the one line below is what",
        "`adt_dod.py` reads to decide whether it may refuse on its say-so.",
        "",
        "gating: %s" % ("ENABLED" if enabled else "DISABLED"),
        "",
        "The bar (declared in the README before any verdict existed):",
        "**held-out** caught >= %d of %d, false positives <= %d of %d clean."
        % (MIN_HELD_OUT_CAUGHT, held_gapped, MAX_FALSE_POSITIVES, clean),
        "",
        "held-out caught: %d/%d   <- THIS decides gating" % (held_caught, held_gapped),
        "caught (all gapped): %d/%d" % (caught, gapped),
        "",
        "**Why two numbers.** The non-held-out flawed cases are drawn from the same",
        "INSTANCES the reviewer's prompt cites as worked examples. Their score is a",
        "recall floor on material the judge was handed and says nothing about",
        "generalisation, so it cannot buy gating. The held-out cases use instances",
        "the prompt never mentions. Found by the plan-quality-reviewer reviewing",
        "ADT-126 itself — the ticket that built it.",
        "",
        "- **caught** — flawed cases where the reviewer returned FLAWED *and* named",
        "  the defect. A FLAWED verdict pointing at the wrong thing is not a catch.",
        "- **false positives: %d/%d** — clean cases wrongly flagged. A judge that" % (fp, clean),
        "  flags everything is as useless as one that flags nothing.",
        "",
        "| case | expected | got | result | failure class |",
        "|---|---|---|---|---|",
    ]
    lines += ["| `%s` | %s | %s | %s | %s |" % r for r in rows]
    anomalies = [(n, expected[n]["anomaly"]) for n in sorted(expected)
                 if expected[n].get("anomaly")]
    if anomalies:
        lines += ["", "## Anomalies — read these before trusting a row", ""]
        for name, note in anomalies:
            lines += ["- **`%s`** — %s" % (name, note), ""]
    if missing:
        lines += ["", "> **Incomplete: %d case(s) not run** — %s. The score above is"
                       % (len(missing), ", ".join("`%s`" % m for m in missing)),
                  "> not a calibration until every case has a verdict, and gating",
                  "> stays DISABLED regardless of the partial numbers."]
    lines += ["", "## What to do with this number", "",
              "A poor score is a legitimate result, not a failure of the exercise.",
              "It leaves `gating: DISABLED`: the reviewer still runs and its verdict",
              "is still recorded in the ticket, and only the automatic refusal is",
              "withheld. The correct response is to leave it disabled — never to",
              "re-run the calibration until it looks good.", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=OUT, help="where to write the record")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the record instead of writing it")
    ap.add_argument("--require-complete", action="store_true",
                    help="exit non-zero unless every case has a verdict and the "
                         "record is written. Fails on an UNRUN case, never on a "
                         "low score — the score is not this flag's business.")
    args = ap.parse_args(argv)

    expected = json.load(open(os.path.join(HERE, "expected.json"), encoding="utf-8"))
    rows, missing, caught, gapped, fp, clean, held_caught, held_gapped = score(expected)
    body = render(rows, missing, caught, gapped, fp, clean, held_caught,
                  held_gapped, expected)

    if args.dry_run:
        print(body)
        return 1 if (args.require_complete and missing) else 0

    if args.require_complete and missing:
        print("INCOMPLETE\t%d case(s) not run: %s" % (len(missing), ", ".join(missing)),
              file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(body)
    print("wrote %s — held-out: %d/%d, all-gapped: %d/%d, false positives: %d/%d, "
          "gating: %s%s"
          % (args.out, held_caught, held_gapped, caught, gapped, fp, clean,
             "ENABLED" if gating_decision(held_caught, fp, missing) else "DISABLED",
             ", %d NOT RUN" % len(missing) if missing else ""))
    if args.require_complete and not os.path.isfile(args.out):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

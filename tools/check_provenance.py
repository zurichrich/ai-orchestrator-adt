#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Every counted claim carries the command that produced it and the date it ran.

ADT-224 Phase E, criterion 7. The problem is not that ADT's docs contain wrong
numbers — it is that a reader cannot tell a measured number from a remembered
one. `docs/gate-tax-report.md` quotes token counts, dollar figures and multiples
across a dozen sections, and the ones that carry their provenance and the ones
that do not look identical on the page.

WHAT COUNTS AS A CLAIM. A statistic: a token count, a dollar figure, a
percentage, an "N tickets" count, a multiple. Not a version number, a line
citation, a date, a table of contents entry, or a number inside a code fence —
those are references, not measurements, and demanding provenance for them would
train the author to spray tags rather than to source claims.

WHAT COUNTS AS PROVENANCE. On the claim's own line or the line before it, either
an inline command in backticks, or a path to a committed artifact, AND a date.
Both halves: a command with no date cannot be re-run against the same state, and
a date with no command is a memory with a timestamp on it.

THE NEGATIVE CONTROL MATTERS MORE THAN THE CHECK. `docs/security-posture.md` has
four lines with digits and no statistic at all, so running this over that file
alone passes by enumerating zero claims — a checker that cannot fail. `--control`
proves the opposite: strip a tag and the exit code must be non-zero.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

# A statistic. Deliberately narrow: each alternative names a SHAPE a measurement
# takes, so that widening it is a decision rather than an accident.
CLAIM = re.compile(r"""
      \$[0-9][0-9,]*(?:\.[0-9]+)?          # $67.81, $284
    | \b[0-9][0-9,]{3,}\b                  # 550,652 / 1239140 — token counts
    | \b(?!100\b)[0-9]+(?:\.[0-9]+)?\s*%   # 76%, but never a rhetorical "100%"
                                           # ("100% checkable, 100% green" is a
                                           # figure of speech; a real measurement
                                           # of exactly 100% is written "all")
    | \b[0-9]+(?:\.[0-9]+)?x\b             # 4.8x
""", re.VERBOSE)

DATE = re.compile(r"\b20[0-9]{2}-[0-9]{2}-[0-9]{2}\b")
# Backticks are NOT required. The first draft demanded them and flagged the
# report's own provenance TABLE — rows that name the command and the date in
# adjacent cells, which is the most sourced thing in the document. A checker
# that rejects the correct form teaches the author to work around it.
COMMAND = re.compile(r"(?:grep|awk|wc|ls|git|gh|python3|node|bash|pytest|"
                     r"wrangler|sed|find|cut|sort|uniq)\b")
ARTIFACT = re.compile(r"[\w./-]+\.(?:json|log|md|tsv|csv|jsonl)\b")

# Lines that are references, not measurements.
SKIP = re.compile(r"""
      ^\s*\#                               # a heading
    | ^\s*\|?\s*-{3,}                      # a table rule
    | :\s*[0-9]+\b                         # a file:line citation
    | \bv?[0-9]+\.[0-9]+\.[0-9]+\b         # a semver
    | \bADT-[0-9]+\b                       # a ticket id
""", re.VERBOSE)


def claims(path):
    """-> [(lineno, line)] for every line carrying a statistic."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out, fenced = [], False
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or SKIP.search(line):
            continue
        if CLAIM.search(line):
            out.append((n, line))
    return out, lines


def has_provenance(lines, n):
    """Provenance on the claim's line, or in the block it belongs to.

    A TABLE is one claim-bearing block, not eight independent lines: a source
    line above the header covers every row, which is how a reader uses it and
    how the report was already written. So for a table row, walk back past the
    contiguous rows and look above the header. Otherwise look at the claim's own
    line and the few above it — enough to cross the blank line that separates a
    source note from the paragraph it sources.
    """
    i = n - 1                                   # 0-based index of the claim
    # Walk back to the top of the contiguous block the claim sits in — a table
    # or a paragraph. A source note above the block covers every line of it,
    # which is how a reader uses one; anchoring to the claim's own line instead
    # would demand the note be repeated on each row of a table.
    start = i
    while start > 0 and lines[start - 1].strip():
        start -= 1
    lo = max(0, start - 3)
    window = "\n".join(lines[lo:i + 1])
    dated = bool(DATE.search(window))
    sourced = bool(COMMAND.search(window) or ARTIFACT.search(window))
    return dated and sourced


def check(paths, out=sys.stdout):
    bad, total = [], 0
    for path in paths:
        found, lines = claims(path)
        total += len(found)
        for n, line in found:
            if not has_provenance(lines, n):
                bad.append((path, n, line.strip()[:90]))
    for path, n, line in bad:
        print("%s:%d: counted claim with no command+date: %s" % (path, n, line),
              file=out)
    print("checked %d counted claim(s) across %d file(s); %d unsourced"
          % (total, len(paths), len(bad)), file=out)
    return total, bad


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Every counted claim carries its command and date (ADT-224 E).")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--control", action="store_true",
                    help="prove the checker can fail: strip provenance from a "
                         "copy of each file and require a non-zero result")
    args = ap.parse_args(argv)

    if args.control:
        import tempfile
        ok = True
        for path in args.paths:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            stripped = DATE.sub("(date removed)", text)
            with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                             encoding="utf-8") as tmp:
                tmp.write(stripped)
                probe = tmp.name
            total, bad = check([probe], out=open(os.devnull, "w"))
            os.unlink(probe)
            if total and not bad:
                print("CONTROL FAILED: %s still passes with every date stripped "
                      "— the check cannot fail on it" % path)
                ok = False
            elif not total:
                print("CONTROL SKIPPED: %s carries no counted claim, so passing "
                      "it proves nothing" % path)
        return 0 if ok else 1

    total, bad = check(args.paths)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

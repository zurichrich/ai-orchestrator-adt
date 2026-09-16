#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
"""Frequency-ranked summary of ADT command usage.

Reads <project>/.adt/.adt-usage.log (written by the adt-usage-log
UserPromptSubmit hook — one line per /adt-* invocation: ISO8601<TAB>command)
and prints which commands actually get used, so you can prune the ones that
don't.

    python3 usage-report.py [--project-root .] [--since YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(description="ADT command usage report")
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--since", default=None, help="only count entries on/after this ISO date")
    args = ap.parse_args(argv)

    # the .adt-state consolidation: the usage log moved under .adt/state/. Prefer the
    # new path; fall back to the pre-consolidation legacy path if only that exists.
    dt = Path(args.project_root) / ".adt"
    log = dt / ".adt-state" / "usage.log"
    if not log.exists():
        legacy = dt / ".adt-usage.log"
        if legacy.exists():
            log = legacy
        else:
            print(f"No usage log yet at {log}")
            print("(the usage-log hook writes one line per /adt-* invocation)")
            return

    counts: Counter = Counter()
    total = 0
    first = last = None
    for line in log.read_text().splitlines():
        if "\t" not in line:
            continue
        ts, cmd = line.split("\t", 1)
        if args.since and ts[:10] < args.since:
            continue
        counts[cmd.strip()] += 1
        total += 1
        first = first or ts
        last = ts

    if not total:
        print("No invocations recorded" + (f" since {args.since}" if args.since else ""))
        return

    print(f"ADT command usage — {total} invocations"
          + (f", {first[:10]} … {last[:10]}" if first else ""))
    print("-" * 48)
    width = max(len(c) for c in counts)
    for cmd, n in counts.most_common():
        bar = "█" * n
        print(f"  {cmd:<{width}}  {n:>3}  {bar}")
    print("-" * 48)
    print(f"  {len(counts)} distinct commands used")
    print("\nCommands NOT in this list have gone unused — prune candidates.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Roll this install's ledger up into one shareable file per month.

ADT-254 Phase D. Per-ticket TOTALS already cross machines — each install posts a
register comment and `adt-token-total.sh` reconciles them. Per-turn DETAIL does
not: `adt_lane_cost` says so directly, because the register carries a subtotal
rather than timestamped rows. So per-lane, per-command and per-install
breakdowns stop at the machine boundary while the money crosses it, and a
dashboard would show the two side by side with nothing marking which is which.

THE PROPERTY THAT MAKES THIS SCALE IS THE FILENAME. Each install writes exactly
one file per month, named for its own id, and never touches another's. No
locking, no merge, no reconciliation, no server, and adding a machine needs no
code change — point it at the same directory and its file appears. Files that
are never co-written need no sync protocol; that is the whole transport.

WHAT IS IN IT: counts, and an anonymous id. No ticket id, no session id, no
path, no branch, no repo name, no prose. So the artifact is safe to put in any
synced directory without dragging a privacy question along with it.

WHY THE COMMAND AND NOT THE LANE. Resolving a lane needs GitHub label events —
a network call inside the writer, and it would bake one machine's view of a
ticket's history into a file another machine reads later. The reader maps
command -> lane through `adt_lane_cost.COMMAND_LANE` at display time, which
keeps this offline and deterministic and means the dashboard makes no network
calls at all.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt_cost  # noqa: E402
import build_kanban as bk  # noqa: E402


def _adt_state(root, *leaf):
    """ADT's runtime-state dir. No legacy fallback (ADT-301)."""
    base = os.path.join(str(root), ".adt", "state")
    return os.path.join(base, *leaf) if leaf else base

HEADER = ["date", "install", "command", "turns", "tokens", "micros"]


def default_rollup_dir(project_root: str) -> str:
    return _adt_state(project_root, "rollup")


def aggregate(lines, install_id: str, prices=None, estimator=None) -> dict:
    """{(date, command): [turns, tokens, micros]} for THIS install's rows.

    A turn is a distinct timestamp: one turn writes one row per (model, speed)
    group, and a model split is not a turn. Rows carrying another install's id
    are skipped — this file is ours and only ours, which is what makes it
    conflict-free.

    Rows written before ADT-254 carry no id at all. They are OURS by
    construction (they came from this machine's ledger) so they are included;
    the dashboard reports them separately because the ledger cannot prove it.
    """
    seen_turns: dict = {}
    out: dict = {}
    for line in lines:
        row = adt_cost.parse_row(line)
        if not row:
            continue
        owner = row.get("install") or ""
        if owner and owner != install_id:
            continue
        day = (row["ts"] or "")[:10]
        if not day:
            continue
        command = row.get("command") or ""
        key = (day, command)
        acc = out.setdefault(key, [0, 0, 0])
        turn_key = (key, row["ts"])
        if turn_key not in seen_turns:
            seen_turns[turn_key] = True
            acc[0] += 1
        acc[1] += row["input"] + row["output"]
        if prices is not None:
            micros, _tier = adt_cost.price_row(row, prices, estimator)
            acc[2] += micros or 0
    return out


def render(rows: dict, install_id: str) -> str:
    out = ["\t".join(HEADER)]
    for (day, command), (turns, tokens, micros) in sorted(rows.items()):
        out.append("\t".join([day, install_id, command,
                              str(turns), str(tokens), str(micros)]))
    return "\n".join(out) + "\n"


def months(rows: dict) -> set:
    return {day[:7] for (day, _c) in rows}


def write(rollup_dir: str, install_id: str, rows: dict) -> list:
    """One file per month. Returns the paths written."""
    os.makedirs(rollup_dir, exist_ok=True)
    written = []
    for month in sorted(months(rows)):
        subset = {k: v for k, v in rows.items() if k[0].startswith(month)}
        path = os.path.join(rollup_dir, "%s-%s.tsv" % (install_id, month))
        body = render(subset, install_id)
        # Idempotent: an unchanged month is not rewritten, so a synced directory
        # does not churn and a file's mtime means something.
        try:
            if open(path, encoding="utf-8").read() == body:
                continue
        except OSError:
            pass
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        written.append(path)
    return written


def read_all(rollup_dir: str) -> list:
    """Every install's rows — the union a dashboard reads.

    A malformed row is skipped rather than aborting the read: one machine
    writing badly must not blind the reader to every other machine.
    """
    out = []
    try:
        names = sorted(os.listdir(rollup_dir))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".tsv"):
            continue
        try:
            with open(os.path.join(rollup_dir, name), encoding="utf-8") as fh:
                for n, line in enumerate(fh):
                    parts = line.rstrip("\n").split("\t")
                    if n == 0 and parts == HEADER:
                        continue
                    if len(parts) != len(HEADER):
                        continue
                    try:
                        out.append({"date": parts[0], "install": parts[1],
                                    "command": parts[2], "turns": int(parts[3]),
                                    "tokens": int(parts[4]), "micros": int(parts[5])})
                    except ValueError:
                        continue
        except OSError:
            continue
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Roll this install's ledger into a shareable per-month file.")
    ap.add_argument("--project-root", default=None,
                    help="the project checkout (default: this repo's canonical tree)")
    ap.add_argument("--rollup-dir", default=None,
                    help="where to write. Point several machines at one synced "
                         "directory and each writes only its own file.")
    ap.add_argument("--print", action="store_true",
                    help="write nothing; print the rollup to stdout")
    args = ap.parse_args(argv)

    root = args.project_root or str(bk.canonical_root())
    install_id = bk.machine_id(root)
    ledger = _adt_state(root, "cost-ledger.log")
    try:
        with open(ledger, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        print("no ledger at %s — nothing to roll up" % ledger)
        return 0

    prices = adt_cost.load_prices()
    estimator = adt_cost.load_estimator(root)
    rows = aggregate(lines, install_id, prices, estimator)

    if args.print:
        sys.stdout.write(render(rows, install_id))
        return 0

    target = args.rollup_dir or default_rollup_dir(root)
    written = write(target, install_id, rows)
    print("install %s: %d row(s) across %d month(s); wrote %d file(s) to %s"
          % (install_id, len(rows), len(months(rows)), len(written), target))
    return 0


if __name__ == "__main__":
    sys.exit(main())

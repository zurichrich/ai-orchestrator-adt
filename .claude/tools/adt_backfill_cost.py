#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
"""ADT-115 backfill — give every historical ticket a cost, honestly labelled.

THE PROBLEM. The ledger only started recording model/speed/cache classes with
ADT-115. Everything before it is a 5-column row: a timestamp, a ticket, an
input+output pair, a session id. That pair is ~10% of what the work actually
cost, and there is no model on the row to price it with.

WHAT IS RECOVERABLE, MEASURED NOT ASSUMED. The 5th column is the session id and
Claude Code names transcripts `<session_id>.jsonl`, so where the transcript
still exists the true per-class counts can be recovered exactly. Where it has
been pruned, they cannot be — and pruning is the common case (measured on this
machine at filing: 19/236 ADT rows and 478/1562 consumer rows still had one).
So exact backfill is the minority path and the estimator carries the rest.

THE THREE TIERS. Every row ends up labelled with how its number was reached:
    measured   transcript found; per-class counts recovered; priced exactly
    estimated  transcript gone; priced from the derived ratio
    (untouched) a row already measured is never downgraded — see the ratchet

RECOVERING A ROW'S MESSAGE RANGE. A ledger row stores the DELTA for every
message since the last Stop, not the cursor range. So we replay the session's
assistant messages in order and match cumulative (input, output) against each
row's recorded pair. An ambiguous match is left `estimated` rather than guessed:
a wrong range would silently mis-price, and a visible approximation is better
than an invisible error.

SAFETY. The ledger is backed up before any write, the rewrite is atomic, and the
run is convergent: running it twice produces identical output.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt_cost  # noqa: E402


def _adt_state(root, *leaf):
    """ADT's runtime-state dir. No legacy fallback (ADT-301)."""
    base = os.path.join(str(root), ".adt", "state")
    return os.path.join(base, *leaf) if leaf else base

BACKUP_SUFFIX = ".pre-ADT-115.bak"


def index_transcripts(roots=None) -> dict:
    """{session_id: path} across the Claude Code project dirs."""
    pats = roots or [os.path.expanduser("~/.claude/projects/*/*.jsonl")]
    out = {}
    for pat in pats:
        for p in glob.glob(pat):
            out[os.path.basename(p)[:-6]] = p
    return out


def session_messages(path) -> list:
    """Assistant messages, in order, as per-message usage dicts."""
    msgs = []
    try:
        fh = open(path, errors="ignore")
    except OSError:
        return msgs
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            m = rec.get("message")
            if not isinstance(m, dict) or m.get("role") != "assistant":
                continue
            u = m.get("usage")
            if not isinstance(u, dict):
                continue
            cc = u.get("cache_creation")
            cc = cc if isinstance(cc, dict) else {}
            msgs.append({
                "model": str(m.get("model") or "unknown"),
                "speed": str(u.get("speed") or "standard"),
                "input": int(u.get("input_tokens") or 0),
                "output": int(u.get("output_tokens") or 0),
                "cache_read": int(u.get("cache_read_input_tokens") or 0),
                "cache_write_5m": int(cc.get("ephemeral_5m_input_tokens") or 0),
                "cache_write_1h": int(cc.get("ephemeral_1h_input_tokens") or 0),
            })
    return msgs


def match_ranges(rows, msgs):
    """-> {row_index: [messages]} for rows whose (input, output) delta matches
    a contiguous run. Walks forward; a row that does not match exactly at the
    cursor is skipped (left to the estimator) rather than force-fitted."""
    out, cur = {}, 0
    for i, row in enumerate(rows):
        want_in, want_out = row["input"], row["output"]
        acc_in = acc_out = 0
        j = cur
        while j < len(msgs) and (acc_in < want_in or acc_out < want_out):
            acc_in += msgs[j]["input"]
            acc_out += msgs[j]["output"]
            j += 1
        if acc_in == want_in and acc_out == want_out and j > cur:
            out[i] = msgs[cur:j]
            cur = j
        else:
            # No exact match at this position: stop trying to align the rest of
            # this session rather than shifting everything by one and
            # mis-attributing every later row.
            break
    return out


def enrich(rows, transcripts):
    """Fill per-class counts where a transcript survives. Returns (rows, stats)."""
    by_session = {}
    for i, r in enumerate(rows):
        by_session.setdefault(r["session"], []).append(i)
    measured = 0
    for sess, idxs in by_session.items():
        path = transcripts.get(sess)
        if not path:
            continue
        msgs = session_messages(path)
        if not msgs:
            continue
        sub = [rows[i] for i in idxs]
        for k, group in match_ranges(sub, msgs).items():
            row = rows[idxs[k]]
            if row["tier"] == "measured" and row["model"]:
                continue        # ratchet: never re-touch an already-measured row
            agg = {}
            for m in group:
                key = (m["model"], m["speed"])
                a = agg.setdefault(key, dict(input=0, output=0, cache_read=0,
                                             cache_write_5m=0, cache_write_1h=0))
                for f in a:
                    a[f] += m[f]
            # One row can only carry one (model, speed). A group spanning two is
            # left estimated rather than silently collapsed onto one rate.
            if len(agg) != 1:
                continue
            (model, speed), a = next(iter(agg.items()))
            row.update(model=model, speed=speed, tier="measured", **a)
            measured += 1
    return rows, measured


def derive_estimator(rows, prices) -> dict | None:
    """$/M recorded (input+output) tokens, from the rows we DID measure.

    Derived at run time from this machine's own measured population, never
    hard-coded — a hard-coded ratio would silently rot as the model mix moves.
    Carries a derivation_id for the same reason the price table carries a
    version: a stamped estimate must say which ratio produced it."""
    samples = []
    for r in rows:
        if r["tier"] != "measured" or not r["model"]:
            continue
        recorded = r["input"] + r["output"]
        if recorded < 1000:
            continue                     # tiny rows make a noisy ratio
        micros, _ = adt_cost.price_row(r, prices, None)
        if micros:
            # $/MTok reduces to micros/recorded: dollars = micros/1e6 and
            # MTok = recorded/1e6, so the two 1e6 factors cancel. Spelling the
            # division out longhand is how this shipped 1e6 too large the first
            # time — the dry run caught it, which is why the dry run exists.
            samples.append(micros / recorded)
    if len(samples) < 3:
        return None
    samples.sort()
    def pct(p):
        return samples[min(len(samples) - 1, max(0, int(p * len(samples))))]
    med = statistics.median(samples)
    return {
        "usd_per_mtok": med,
        "p10": pct(0.1),
        "p90": pct(0.9),
        "n_samples": len(samples),
        "derivation_id": "d%d-n%d-m%d" % (len(samples), len(rows), int(med)),
        "note": ("Derived from measured rows on this machine. The observed "
                 "p10-p90 spread is why an estimated cost renders with a ~ "
                 "prefix: it is right to within a factor, not a percent."),
    }


def format_row(r) -> str:
    return "\t".join([
        r["ts"], r["tix_raw"], str(r["input"]), str(r["output"]), r["session"],
        r["model"] or "", r["speed"] or "standard", str(r["cache_read"]),
        str(r["cache_write_5m"]), str(r["cache_write_1h"]), r["tier"],
    ])


def run(ledger_path, root, apply=False, transcripts=None) -> dict:
    raw = open(ledger_path).read().splitlines()
    rows = []
    for line in raw:
        r = adt_cost.parse_row(line)
        if r:
            r["tix_raw"] = line.split("\t")[1]
            rows.append(r)
    before_measured = sum(1 for r in rows if r["tier"] == "measured")
    rows, newly = enrich(rows, transcripts if transcripts is not None
                         else index_transcripts())
    prices = adt_cost.load_prices()
    est = derive_estimator(rows, prices)
    for r in rows:
        if r["tier"] != "measured":
            r["tier"] = "estimated"
    stats = {
        "rows": len(rows),
        "measured_before": before_measured,
        "measured_after": sum(1 for r in rows if r["tier"] == "measured"),
        "newly_measured": newly,
        "estimated": sum(1 for r in rows if r["tier"] == "estimated"),
        "estimator": est,
    }
    if apply:
        backup = ledger_path + BACKUP_SUFFIX
        if not os.path.exists(backup):
            shutil.copy2(ledger_path, backup)     # BEFORE any write
        tmp = ledger_path + ".tmp"
        with open(tmp, "w") as fh:
            for r in rows:
                fh.write(format_row(r) + "\n")
        os.replace(tmp, ledger_path)              # atomic
        if est:
            p = _adt_state(root, adt_cost.ESTIMATOR_LEAF)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                json.dump(est, fh, indent=2)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ADT-115 cost backfill")
    ap.add_argument("--root", default=".", help="project root owning the ledger")
    ap.add_argument("--apply", action="store_true",
                    help="write (default is a dry run that touches nothing)")
    a = ap.parse_args(argv)
    root = os.path.abspath(os.path.expanduser(a.root))
    ledger = _adt_state(root, "cost-ledger.log")
    if not os.path.exists(ledger):   # 5c: pre-rename name
        ledger = os.path.join(root,
                              ".adt/state/token-usage.log")
    if not os.path.exists(ledger):
        print("no ledger at %s" % ledger, file=sys.stderr)
        return 1
    st = run(ledger, root, apply=a.apply)
    print("rows=%(rows)d  measured=%(measured_after)d (+%(newly_measured)d)  "
          "estimated=%(estimated)d" % st)
    if st["estimator"]:
        e = st["estimator"]
        print("estimator: $%.0f/MTok (p10 $%.0f, p90 $%.0f, n=%d) id=%s"
              % (e["usd_per_mtok"], e["p10"], e["p90"], e["n_samples"],
                 e["derivation_id"]))
    else:
        print("estimator: NOT derived (needs >=3 measured rows)")
    if not a.apply:
        print("[dry-run] nothing written. Pass --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# adt-bundle: v0.1.0

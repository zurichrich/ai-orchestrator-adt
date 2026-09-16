#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
"""ADT cost pricer (ADT-115) — the ONE place a ledger row becomes money.

WHY a separate module: `build_kanban.py` is already the single home of the
ledger parser, the register marker writer+parser and `machine_id()`, co-located
so a format tweak cannot silently orphan its reader. Pricing needs the same
treatment, but the *hooks* need it too (`adt-token-sum.sh` /
`adt-token-total.sh` are per-project copies), and `build_kanban` is a renderer
with a board-rendering `main()`. So the pricer lives here, standalone and with
no import of `build_kanban` — `build_kanban` imports THIS, one direction, no
cycle — and the hooks reach it through the CLI at the bottom, resolved by the
same relative-path walk `adt-dod.sh` uses.

THE BILL (the thing the old hook got wrong). The Messages API bills input in
three DISJOINT classes; `input_tokens` is only the uncached remainder after the
last cache breakpoint:

    total_input = input_tokens + cache_creation_input_tokens + cache_read_input_tokens

Cache rates are multiples of the model's BASE INPUT rate — 0.1x read, 1.25x a
5-minute write, 2x a 1-hour write — so the table stores two numbers per model
and derives the other three. Fast mode is not a surcharge on output: it rebases
the base rate itself, and the cache multipliers stack on top of it.

TIERS. Every priced row carries how it was arrived at, and the tier is what
stops an estimate ever being mistaken for a measurement:

    measured   — an 11-column row with real per-class counts. Priced exactly.
    estimated  — a row the backfill could not recover a transcript for. Priced
                 from the derived estimator ratio.
    legacy     — a 5-column row (no model): pre-ADT-115, or written by a machine
                 still running the old hook. Priced from the same ratio.
                 PERMANENT, not a migration state — an un-upgraded INSTALL keeps
                 appending short rows for as long as it goes un-upgraded, and
                 there is no event that forces the upgrade.

                 (Corrected 2026-08-21. This originally said "hooks load at
                 session start, so an un-upgraded machine keeps appending short
                 rows forever" — measured and false. Replacing the hook FILE
                 takes effect on the very next Stop: the reinstall landed at
                 13:03:30Z and the same session's first 11-column row was
                 13:07:10Z. It is the per-project COPY in .claude/hooks that
                 goes stale, not the session. The conclusion is unchanged and
                 in fact better evidenced — one consumer is sitting on 1,569
                 five-column rows right now.)

`None` is NOT a tier. A ticket with no ledger rows at all prices to None and
renders "—" (ADT-72): never $0.00, because "we did not capture it" and "it was
free" are different facts and only one of them is true.

WHAT A MISSING PRICE DOES (ADT-277). A model id the table does not carry used to
contribute nothing and say nothing: `rates_for` returned None, the name was
dropped on that line, and all that reached the reader was a tier label. Two live
rows carrying `claude-haiku-4-5-20251001` against a table key of
`claude-haiku-4-5` took $0.12 off ADT-224 that way. Two changes:

  * a miss retries once with a trailing `-YYYYMMDD` stripped, because a dated
    snapshot is the same model at the same rate;
  * anything still unmatched is COLLECTED by model id — per ticket in
    `price_ledger`, across a whole ledger by `unpriced()`, which the CLI reports
    and exits 1 on. A total that is short now names what made it short.

And a row worth zero tokens no longer votes on the tier. Worst-wins ranking let
one zero-token `<synthetic>` row mark a whole ticket `estimated`; the money in
such a row is zero, so it cannot be the reason a total is uncertain.
"""

from __future__ import annotations

import json
import os
import re
import sys

# ---------------------------------------------------------------- ids

def canon_tix(tix: str) -> str:
    """Mirrors build_kanban.canon_tix and adt-token-sum.sh's embedded copy:
    strip leading zeros so ADT-58 and ADT-058 sum to ONE bucket. Duplicated
    rather than imported to keep this module free of a build_kanban import
    (see the module docstring); the bash hook already carries the same copy
    with the same note. If these three ever disagree, a ticket's spend splits
    silently across two cards — the ADT-58 incident."""
    s = (tix or "").strip().upper()
    m = re.fullmatch(r"([A-Z]+)-0*([0-9]+)", s)
    return f"{m.group(1)}-{m.group(2)}" if m else s


# ---------------------------------------------------------------- price table

# Resolution order. An installed hook is a COPY with no $ADT_DIR (the ADT-090
# problem), so we look next to ourselves first — install-defaults.sh drops
# pricing.json into .claude/tools/ alongside this file — then fall back to the
# source tree's defaults/.
def _pricing_candidates(explicit=None):
    if explicit:
        yield explicit
    env = os.environ.get("ADT_PRICING")
    if env:
        yield env
    here = os.path.dirname(os.path.abspath(__file__))
    yield os.path.join(here, "pricing.json")                    # installed
    yield os.path.join(here, os.pardir, "defaults", "pricing.json")  # source tree


def load_prices(path=None) -> dict | None:
    """The price table, or None when no table can be found. None means every
    row prices to None — a missing table must never be read as free work."""
    for cand in _pricing_candidates(path):
        try:
            with open(cand) as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("models"):
            d.setdefault("version", "unknown")
            d.setdefault("multipliers", {})
            return d
    return None


# A dated snapshot id: `claude-haiku-4-5-20251001` for the table's
# `claude-haiku-4-5`. The suffix names a release date, not a different model, so
# stripping it and retrying is the SAME model at the same rate — not the
# neighbouring-model guess the docstring below rules out. Anchored to exactly
# eight trailing digits so it cannot eat a version segment (`claude-opus-4-5`
# keeps its `-5`).
_DATED_SUFFIX = re.compile(r"-\d{8}$")


def rates_for(prices: dict, model: str, speed: str) -> dict | None:
    """Per-class $/MTok for one (model, speed), cache rates DERIVED.

    An unknown model returns None — never a guess at a neighbouring model's
    rate. An unknown *speed* on a known model falls back to `standard`: speed
    is an optional request parameter that defaults to standard, so its absence
    is a documented default rather than missing information. An unknown model
    is genuinely missing information, and the two are not the same.

    ADT-277: a miss retries ONCE with a trailing `-YYYYMMDD` removed. Two live
    rows carrying `claude-haiku-4-5-20251001` priced to nothing and contributed
    $0 to ADT-224's total, silently — a dict lookup that misses has no way to
    say so, which is the defect `unpriced()` below exists to close.
    """
    m = (prices.get("models") or {}).get(model)
    if not isinstance(m, dict) and model:
        stripped = _DATED_SUFFIX.sub("", model)
        if stripped != model:
            m = (prices.get("models") or {}).get(stripped)
    if not isinstance(m, dict):
        return None
    base = m.get(speed) or m.get("standard")
    if not isinstance(base, dict):
        return None
    mult = prices.get("multipliers") or {}
    inp = float(base.get("input", 0.0))
    return {
        "input": inp,
        "output": float(base.get("output", 0.0)),
        "cache_read": inp * float(mult.get("cache_read", 0.1)),
        "cache_write_5m": inp * float(mult.get("cache_write_5m", 1.25)),
        "cache_write_1h": inp * float(mult.get("cache_write_1h", 2.0)),
    }


# ---------------------------------------------------------------- estimator

def _adt_state(root, *leaf):
    """ADT's runtime-state dir. No legacy fallback (ADT-301)."""
    base = os.path.join(str(root), ".adt", "state")
    return os.path.join(base, *leaf) if leaf else base

ESTIMATOR_LEAF = "cost-estimator.json"


def load_estimator(root) -> dict | None:
    """{usd_per_mtok, p10, p90, derivation_id, ...} or None.

    Written by adt_backfill_cost.py from the rows it could measure. Carries a
    `derivation_id` for the same reason the price table carries a `version`: a
    stamped estimated cost has to say WHICH ratio produced it, or two machines
    can stamp different numbers for one ticket with no way to tell them apart.
    """
    try:
        with open(_adt_state(root, ESTIMATOR_LEAF)) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("usd_per_mtok") else None


# ---------------------------------------------------------------- rows

def parse_row(line: str):
    """One ledger line -> dict, or None if unusable.

    Handles BOTH widths by design: the ADT-115 columns are appended, so the
    original five keep their positions and a 5-column row still parses here
    exactly as it did in the old reader.
    """
    parts = line.rstrip("\n").split("\t")
    if len(parts) < 4:
        return None
    try:
        inp, out = int(parts[2]), int(parts[3])
    except (ValueError, IndexError):
        return None
    row = {
        "ts": parts[0],
        "tix": canon_tix(parts[1]),
        "input": inp,
        "output": out,
        "session": parts[4] if len(parts) > 4 else "",
        "model": None, "speed": "standard",
        "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0,
        "tier": "legacy",
    }
    if len(parts) >= 11:
        def _i(x):
            try:
                return int(x)
            except (TypeError, ValueError):
                return 0
        row.update(model=(parts[5] or None), speed=(parts[6] or "standard"),
                   cache_read=_i(parts[7]), cache_write_5m=_i(parts[8]),
                   cache_write_1h=_i(parts[9]),
                   tier=(parts[10] or "measured"))
    # ADT-224 C1b: the 12th column is the command active on that turn. Appended
    # like the ADT-115 six, so an 11-column row simply has none. Note the gate
    # above stays `>= 11` rather than `== 11`: were it an equality, a 12-column
    # row would skip that block entirely and read back as `tier: legacy`, priced
    # as input+output with every cache class discarded — 68.9% of ADT-205's
    # tokens, silently.
    row["command"] = parts[11] if len(parts) > 11 else ""
    # ADT-254 C2a: the 13th column is the install's anonymous id. Appended like
    # the 12th and the ADT-115 six, so a narrower row simply has none — and, as
    # with those, the gate above stays `>= 11` rather than becoming an equality,
    # or a wider row would fall through to `tier: legacy` and be priced as
    # input+output with every cache class discarded.
    row["install"] = parts[12] if len(parts) > 12 else ""
    # ADT-339 C5 — column 14: the ticket's LANE at the moment of the turn, read
    # from the cache directory its file sat in. Absent on every row written
    # before the hook change, so "" is a real state and every reader must treat
    # it as "fall back", never as "no lane".
    row["lane"] = parts[13] if len(parts) > 13 else ""
    return row


def price_row(row: dict, prices, estimator):
    """-> (micros:int|None, tier:str). Micro-dollars as an INT: the board sums
    these in JS and floats drift over a board-sized sum.

    micros == tokens * rate exactly, since rate is $/MTok:
        tokens * (rate / 1e6) dollars == tokens * rate micro-dollars.
    """
    tier = row.get("tier") or "legacy"
    ms = _priceable(row, prices)
    if ms:
        r = rates_for(prices, *ms)
        if r:
            micros = (row["input"] * r["input"]
                      + row["output"] * r["output"]
                      + row["cache_read"] * r["cache_read"]
                      + row["cache_write_5m"] * r["cache_write_5m"]
                      + row["cache_write_1h"] * r["cache_write_1h"])
            return int(round(micros)), "measured"
        # Known-shaped row, unknown model: fall through to the estimator rather
        # than invent a rate. Loud in the sense that it renders "~", not "$".
        tier = "estimated"
    # legacy / estimated / unknown-model: the ratio applies to what WAS
    # recorded (input+output), which is the only quantity such a row has.
    if estimator:
        recorded = row["input"] + row["output"]
        micros = recorded * float(estimator["usd_per_mtok"])
        return int(round(micros)), ("legacy" if tier == "legacy" else "estimated")
    return None, tier


def row_tokens(row: dict) -> int:
    """Every billable class on the row. `tokens` in the per-ticket accumulator
    is input+output only (what the pre-ADT-115 ledger recorded, and what the
    board's 🪙 badge has always shown), so this is deliberately a second
    quantity rather than a change to that one."""
    return (row["input"] + row["output"] + row["cache_read"]
            + row["cache_write_5m"] + row["cache_write_1h"])


def _priceable(row: dict, prices):
    """-> (model, speed) when the row is a candidate for exact pricing, else
    None. ONE definition of that precondition: price_row and unpriced_model both
    ask it, and if the two ever disagreed a ticket would report an unpriced
    model that priced perfectly well."""
    if not row.get("model") or not prices:
        return None
    if (row.get("tier") or "legacy") != "measured":
        return None
    return row["model"], (row.get("speed") or "standard")


def unpriced_model(row: dict, prices) -> str | None:
    """The model id on `row` that has no rate, or None when the row is
    priceable (or carries no model at all, which is the `legacy` case and a
    different fact).

    Kept separate from price_row rather than returned by it: three callers
    unpack price_row as a 2-tuple, and widening that contract to carry one more
    fact would break them for no gain."""
    ms = _priceable(row, prices)
    if not ms:
        return None
    return None if rates_for(prices, *ms) else ms[0]


def price_ledger(lines, prices, estimator) -> dict:
    """-> {canon_tix: {"micros": int|None, "tier": str, "tokens": int,
                       "unpriced": {model: tokens}}}.

    A ticket is `measured` only when EVERY one of its rows was measured; one
    estimated row makes the whole ticket approximate, because the total is.
    That is deliberately pessimistic: a number shown without "~" has to mean
    every component of it was measured.
    """
    # Tier ranking, worst-wins: a total is only as trustworthy as its least
    # trustworthy component. `legacy` outranks `estimated` because it says
    # something more specific about WHY the number is soft (the row predates
    # the schema, rather than its transcript being gone).
    RANK = {"measured": 0, "estimated": 1, "legacy": 2}
    out: dict = {}
    for line in lines:
        row = parse_row(line)
        if not row or not row["tix"]:
            continue
        micros, tier = price_row(row, prices, estimator)
        acc = out.setdefault(row["tix"],
                             {"micros": 0, "tier": "measured", "tokens": 0,
                              "unpriced": {}, "_priced": 0, "_rows": 0})
        acc["tokens"] += row["input"] + row["output"]
        acc["_rows"] += 1
        if micros is None:
            # Unpriceable row. The ticket keeps its other rows' money, but the
            # total is now known to be short, so it can never read as measured.
            tier = "legacy" if row["model"] is None else "estimated"
        else:
            acc["micros"] += micros
            acc["_priced"] += 1
        # ADT-277: carry the model id OUT. Before this the name was discarded at
        # the rates_for miss and all that survived was a tier label, so the
        # board could say "approximate" but never "approximate because of this".
        # ADT-277: a row worth nothing cannot make a total uncertain, and is
        # not a missing price either. The tier qualifies a NUMBER, so a row
        # contributing nothing to that number cannot qualify it — the rule holds
        # without reference to what writes such rows, and nothing in this repo
        # does write them. (The live ledger carries 15 with the model
        # `<synthetic>`, zero tokens and no rate; worst-wins ranking let one of
        # them mark ADT-224 ~$411.03 approximate when every dollar in it was
        # measured.) Such a row has
        # already contributed its (zero) micros above, so no arithmetic changes;
        # it just stops voting on the tier and stops being reported as unpriced.
        # ONE predicate for both, or the two answers could disagree.
        toks = row_tokens(row)
        if toks == 0:
            continue
        bad = unpriced_model(row, prices)
        if bad:
            acc["unpriced"][bad] = acc["unpriced"].get(bad, 0) + toks
        if RANK.get(tier, 1) > RANK[acc["tier"]]:
            acc["tier"] = tier
    for acc in out.values():
        if acc.pop("_priced") == 0:
            acc["micros"] = None    # nothing priced at all -> "—", never $0.00
        acc.pop("_rows")
    return out


# ---------------------------------------------------------------- formatting

def fmt_cost(micros, tier: str = "measured") -> str:
    """Board badge text. "—" when unknown (never "$0.00" — ADT-72); "~" prefix
    whenever the number is not fully measured, so an estimate can never read as
    exact. Abbreviates above four figures for the same reason fmt_tokens does:
    the card badge row is the most crowded line and must not wrap on mobile."""
    if micros is None:
        # "$ —", not a bare "—": the badge sits immediately after the token
        # badge, and two adjacent dashes render as "🪙 ——", where the reader
        # cannot tell which badge is which. The "$" is what makes the empty
        # state legible as a COST that was not captured. (Caught in the UI
        # walkthrough by reading the rendered row, not the code.)
        return "$ —"
    d = micros / 1_000_000.0
    pre = "" if tier == "measured" else "~"
    if d >= 10_000:
        return f"{pre}${d/1000:.0f}k"
    if d >= 1_000:
        return f"{pre}${d/1000:.1f}k"
    if d >= 1:
        return f"{pre}${d:,.2f}"
    if d > 0:
        return f"{pre}${d:.2f}" if d >= 0.005 else f"{pre}<$0.01"
    return f"{pre}$0.00"


# ---------------------------------------------------------------- CLI

def unpriced(lines, prices) -> dict:
    """-> {model: {"rows": int, "tokens": int}} for every model id with no rate.

    Rows worth zero tokens are EXCLUDED. `<synthetic>` is a permanent
    non-billable marker rather than a model whose price is missing, so counting
    it would make the live ledger permanently non-zero and the check useless as
    a gate."""
    out: dict = {}
    for line in lines:
        row = parse_row(line)
        if not row:
            continue
        tokens = row_tokens(row)
        if tokens == 0:
            continue                    # see price_ledger: not a missing price
        model = unpriced_model(row, prices)
        if not model:
            continue
        acc = out.setdefault(model, {"rows": 0, "tokens": 0})
        acc["rows"] += 1
        acc["tokens"] += tokens
    return out


def _read_all(paths):
    """-> (lines, unreadable_paths).

    The unreadable list is returned rather than swallowed because the two
    callers need OPPOSITE things from it. `sum` is handed the current ledger AND
    the pre-rename legacy path, and the legacy one is normally absent, so a miss
    there is routine. `unpriced` is a gate: if it read nothing it must not
    report success, which is the ADT-72 rule ("we did not capture it" and "it
    was free" are different facts) applied one level up."""
    lines, unreadable = [], []
    for p in paths:
        try:
            with open(p) as fh:
                lines.extend(fh.readlines())
        except OSError:
            unreadable.append(p)
    return lines, unreadable


def _cli_unpriced(argv) -> int:
    """`adt_cost.py unpriced <ledger>...` — name every model id with no rate.

    Exits 1 when any has tokens behind it, so it can be a DoD condition. The
    whole point is that it SAYS the id: a total that is silently short reads
    exactly like a correct one."""
    if not argv:
        print("usage: adt_cost.py unpriced <ledger>...", file=sys.stderr)
        return 2
    prices = load_prices()
    if prices is None:
        print("no price table found — every row would price to None",
              file=sys.stderr)
        return 2
    lines, unreadable = _read_all(argv)
    if len(unreadable) == len(argv):
        # Read NOTHING. Reporting "all rows priced" here would be a green check
        # that never looked at anything — exactly the silent-shortfall shape
        # this subcommand exists to make impossible.
        print("no ledger could be read: " + ", ".join(unreadable),
              file=sys.stderr)
        return 2
    if unreadable:
        # Partial: grade what WAS read, but never let the missing part pass
        # unmentioned.
        print("warning: could not read " + ", ".join(unreadable),
              file=sys.stderr)
    found = unpriced(lines, prices)
    if not found:
        print("all rows priced")
        return 0
    for model in sorted(found):
        a = found[model]
        print(f"{model}\t{a['rows']} rows\t{a['tokens']} tokens")
    return 1


def _cli(argv) -> int:
    """`adt_cost.py sum <TIX> <ledger>...` -> "<tokens>\\t<micros|->\\t<tier>".

    For the bash hooks (adt-token-sum.sh / adt-token-total.sh), which are
    per-project copies with no path back to $ADT_DIR and so reach this file the
    way adt-dod.sh reaches adt_dod.py.
    """
    if argv and argv[0] == "unpriced":
        return _cli_unpriced(argv[1:])
    if len(argv) < 3 or argv[0] != "sum":
        print("usage: adt_cost.py sum <TIX> <ledger>... | "
              "adt_cost.py unpriced <ledger>...", file=sys.stderr)
        return 2
    want = canon_tix(argv[1])
    # `sum` is routinely handed a legacy ledger path that does not exist, and a
    # ticket with no rows already answers `unattributed` rather than 0, so an
    # unreadable path needs no separate signal here.
    lines, _ = _read_all(argv[2:])
    root = os.environ.get("ADT_ROOT") or "."
    totals = price_ledger(lines, load_prices(), load_estimator(root))
    acc = totals.get(want)
    if acc is None:
        # No row attributed to this id at all: the honest answer is the
        # sentinel, never 0 (ADT-72).
        print("unattributed\t-\tunattributed")
        return 0
    micros = acc["micros"]
    print(f"{acc['tokens']}\t{'-' if micros is None else micros}\t{acc['tier']}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))

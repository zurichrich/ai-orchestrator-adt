# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests adt_lane_cost: bucketing ledger rows into kanban lanes by label
events, the experiment and report document checks, and --reconcile, which
recomputes a report's figures from its committed inputs.

Each document check is tested both ways: a correct document passes, and a
document with one element removed or wrong fails.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import adt_lane_cost as alc  # noqa: E402


def _ev(name, at, event="labeled"):
    return {"event": event, "label": {"name": name}, "created_at": at}


# A ticket filed, planned, moved back to ideas, planned again, then done.
EVENTS = [
    _ev("stage:ideas", "2026-09-01T18:59:02Z"),
    _ev("stage:planned", "2026-09-01T19:19:56Z"),
    _ev("stage:ideas", "2026-09-01T19:19:56Z", "unlabeled"),
    _ev("stage:ideas", "2026-09-01T19:21:04Z"),
    _ev("stage:planned", "2026-09-01T19:33:10Z"),
    _ev("stage:done", "2026-09-01T20:15:08Z"),
    _ev("P1", "2026-09-01T18:59:02Z"),          # non-stage label: ignored
]


# A minimal history with a `planned` lane and a `qa` lane.
EVENTS_FAST = [
    _ev("stage:planned", "2026-09-01T11:00:00Z"),
    _ev("stage:qa", "2026-09-01T12:00:00Z"),
    _ev("stage:done", "2026-09-01T13:00:00Z"),
]


def _row(ts, tix="ADT-1", inp=10, out=100):
    return (f"{ts}\t{tix}\t{inp}\t{out}\tsess\tclaude-opus-5\tstandard"
            f"\t0\t0\t0\tmeasured\n")


def _erow(ts, tix, tokens):
    """An `estimated` row: no model, so it is priced by the fixture's estimator."""
    return (f"{ts}\t{tix}\t0\t{tokens}\tgate-tax\t\tstandard"
            f"\t0\t0\t0\testimated\n")


def test_lane_intervals_orders_and_closes_on_the_next_label():
    iv = alc.lane_intervals(EVENTS)
    assert [l for _s, _e, l in iv] == ["ideas", "planned", "ideas", "planned", "done"]
    assert iv[-1][1] is None, "the final lane stays open"


def test_zero_width_interval_is_dropped():
    """Two labels at the same timestamp do not create an empty lane."""
    iv = alc.lane_intervals([
        _ev("stage:ideas", "2026-09-01T10:00:00Z"),
        _ev("stage:planned", "2026-09-01T10:00:00Z"),
        _ev("stage:done", "2026-09-01T11:00:00Z"),
    ])
    assert [l for _s, _e, l in iv] == ["planned", "done"]


def test_bucketing_handles_a_lane_revisited_and_the_open_final_lane():
    lines = [
        _row("2026-09-01T19:04:07Z"),   # first ideas spell
        _row("2026-09-01T19:20:30Z"),   # the 2-minute planned spell
        _row("2026-09-01T19:23:37Z"),   # back in ideas
        _row("2026-09-01T19:34:18Z"),   # planned again
        _row("2026-09-01T21:00:00Z"),   # AFTER the final transition -> done
    ]
    b = alc.bucket_rows(lines, "ADT-1", alc.lane_intervals(EVENTS))
    assert {k: len(v) for k, v in b.items()} == {
        "ideas": 2, "planned": 2, "done": 1}
    assert sum(len(v) for v in b.values()) == len(lines), "no row lost"


def test_rows_before_the_first_label_go_in_a_pre_bucket():
    b = alc.bucket_rows([_row("2026-09-01T00:00:00Z")], "ADT-1",
                        alc.lane_intervals(EVENTS))
    assert list(b) == ["pre"], "expected the row in the 'pre' bucket"


def test_other_tickets_rows_are_ignored():
    b = alc.bucket_rows([_row("2026-09-01T19:34:18Z", tix="ADT-999")],
                        "ADT-1", alc.lane_intervals(EVENTS))
    assert b == {}


def test_canonicalised_ids_land_in_the_same_bucket():
    """A ledger id of ADT-058 matches the ticket ADT-58."""
    b = alc.bucket_rows([_row("2026-09-01T19:34:18Z", tix="ADT-058")],
                        "ADT-58", alc.lane_intervals(EVENTS))
    assert {k: len(v) for k, v in b.items()} == {"planned": 1}


# ------------------------------------------------------- document contracts

GOOD_EXPERIMENT = """# Gate-tax experiment protocol

## Arms
Arm A - control (no gates)
Arm B - fast lane
Arm C - full lane

## Corpus
Three matched changes.

## Traps
Traps are committed before any arm runs.

## Metrics
Stop-points, tokens, USD.

## Stop rule
Stop after part 2.
"""

# The fixture report has every table header and column, because --reconcile
# finds cells by column name.
GOOD_REPORT = """# Gate-tax report

9 runs, 3 planted defects, 9/9 features landed, 7/9 traps survived.

| arm  | runs | wall-clock (min) | tokens      | USD          | stop-points |
|---|---|---|---|---|---|
| arm-A | 3 | 12-19 | 40,000-61,000 | $4.00-$6.10 | 1-1 |
| arm-B | 3 | 20-31 | 70,000-92,000 | $7.00-$9.20 | 2-3 |
| arm-C | 3 | 44-70 | 150,000-190,000 | $15.00-$19.00 | 6-8 |

| tier | n | total USD | per ticket | plan lane | qa lane | post-merge defects |
|---|---|---|---|---|---|---|
| track: fast | n=1 | $25.33 | $25.33 | $0.00 | $0.00 | 0 of 2 closed |
| track: standard | n=12 | $557.81 | $46.48 | $0.00 | $0.00 | 12 of 38 closed |
| track: full | n=9 | $852.28 | $94.70 | $0.00 | $0.00 | 4 of 14 closed |

| trap | class | caught by | gate | marginal cost |
|---|---|---|---|---|
| trap-1 | ripple | caught by arm-C | gate qa | $11.00 |
| trap-2 | config | caught by arm-B | gate build | $2.60 |
| trap-3 | ordering | none | gate - | $0.00 |

Marginal cost per trap caught: $6.80 (mean over the traps caught), 2 of 3 traps caught.
Arms run blind to the traps: 9 of 9.

Per ticket (mean of the arm's 3 runs): arm-A $5.20, arm-B $8.10, arm-C $17.00.
Mean wall-clock: arm-A 15.3 min, arm-B 25.3 min, arm-C 56.3 min.
Cost ratio to the control (mean/mean): arm-B 1.6x, arm-C 3.3x.
Wall-clock ratio to the control (mean/mean): arm-B 1.7x, arm-C 3.7x.
Marginal cost per ticket over the control: arm-B $2.90, arm-C $11.80.
Marginal cost per ticket of the full lane over the fast lane: $8.90.
Post-merge defects per closed ticket: track: fast 0.00, track: standard 0.32, track: full 0.29.
Per-ticket cost ratio against track: standard: track: full 2.0x, track: fast 0.5x.
Recommendation: work of shape doc-only should run track: fast, because the gates it adds cost $2 and catch nothing.

## Evidence
| id | command | date |
| arm-A | python3 tools/adt_lane_cost.py --runs docs/gate-tax-runs/ | 2026-09-05 |
| arm-B | python3 tools/adt_lane_cost.py --runs docs/gate-tax-runs/ | 2026-09-05 |
| arm-C | python3 tools/adt_lane_cost.py --runs docs/gate-tax-runs/ | 2026-09-05 |
| track: fast | python3 tools/adt_lane_cost.py --cache-dir ~/.adt/x/cache | 2026-09-05 |
| track: standard | python3 tools/adt_lane_cost.py --cache-dir ~/.adt/x/cache | 2026-09-05 |
| track: full | python3 tools/adt_lane_cost.py --cache-dir ~/.adt/x/cache | 2026-09-05 |
| trap-1 | git log docs/gate-tax-runs/ | 2026-09-05 |
| trap-2 | git log docs/gate-tax-runs/ | 2026-09-05 |
| trap-3 | git log docs/gate-tax-runs/ | 2026-09-05 |
| marginal-cost | python3 tools/adt_lane_cost.py --reconcile | 2026-09-05 |
| blind-count | git log docs/gate-tax-runs/ | 2026-09-05 |
"""


def _write(tmp_path, name, text):
    p = os.path.join(str(tmp_path), name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


def test_check_experiment_passes_a_complete_protocol(tmp_path):
    assert alc.check_experiment(_write(tmp_path, "e.md", GOOD_EXPERIMENT)) == []


def test_check_experiment_fails_on_each_missing_section(tmp_path):
    for required in alc.REQUIRED_EXPERIMENT:
        p = _write(tmp_path, "e.md", GOOD_EXPERIMENT.replace(required, "REMOVED"))
        problems = alc.check_experiment(p)
        assert any(repr(required) in x for x in problems), required


def test_check_experiment_rejects_an_em_dash_arm_heading(tmp_path):
    """The arm heading must use a hyphen; an em dash does not match."""
    bad = GOOD_EXPERIMENT.replace("Arm A - control", "Arm A — control")
    assert alc.check_experiment(_write(tmp_path, "e.md", bad)) != []


def test_check_report_passes_a_well_formed_report(tmp_path):
    assert alc.check_report(_write(tmp_path, "r.md", GOOD_REPORT)) == []


def test_check_report_needs_all_three_arms(tmp_path):
    bad = GOOD_REPORT.replace("| arm-C | 3 | 44-70", "| arm-X | 3 | 44-70")
    assert any("arm-C" in x for x in
               alc.check_report(_write(tmp_path, "r.md", bad)))


def test_check_report_rejects_usd_only_arm_row(tmp_path):
    """An arm row needs four measures as ranges, not just USD."""
    bad = GOOD_REPORT.replace("| arm-A | 3 | 12-19 | 40,000-61,000 | $4.00-$6.10 | 1-1 |",
                              "| arm-A | 3 | | | $4.10 | |")
    problems = alc.check_report(_write(tmp_path, "r.md", bad))
    assert any("fewer than 4 range-shaped measures" in x for x in problems)


def test_check_report_needs_sample_sizes_on_track_rows(tmp_path):
    bad = GOOD_REPORT.replace("| track: full | n=9 |", "| track: full | many |")
    assert any("n=" in x for x in alc.check_report(_write(tmp_path, "r.md", bad)))


def test_check_report_needs_every_trap_row(tmp_path):
    bad = GOOD_REPORT.replace("| trap-2 | config | caught by arm-B | gate build | $2.60 |", "")
    assert any("trap-2" in x for x in alc.check_report(_write(tmp_path, "r.md", bad)))


def test_check_report_needs_the_marginal_and_blind_lines(tmp_path):
    for line, token in (("Marginal cost per trap caught:", "Marginal"),
                        ("Arms run blind to the traps:", "blind")):
        bad = "\n".join(l for l in GOOD_REPORT.splitlines()
                        if not l.startswith(line))
        problems = alc.check_report(_write(tmp_path, "r.md", bad))
        assert any(token in x for x in problems), line


def test_check_report_requires_provenance_for_every_id(tmp_path):
    """Every id in the report needs a row in the Evidence table."""
    bad = GOOD_REPORT.replace(
        "| trap-3 | git log docs/gate-tax-runs/ | 2026-09-05 |\n", "")
    assert any("trap-3" in x and "Evidence" in x
               for x in alc.check_report(_write(tmp_path, "r.md", bad)))


def test_check_report_rejects_a_missing_evidence_table(tmp_path):
    bad = GOOD_REPORT.split("## Evidence")[0]
    assert any("Evidence" in x for x in alc.check_report(_write(tmp_path, "r.md", bad)))


def test_check_report_rejects_an_empty_command_cell(tmp_path):
    bad = GOOD_REPORT.replace(
        "| blind-count | git log docs/gate-tax-runs/ | 2026-09-05 |",
        "| blind-count | - | 2026-09-05 |")
    assert any("blind-count" in x and "empty command" in x
               for x in alc.check_report(_write(tmp_path, "r.md", bad)))


# ------------------------------------------------------------- reconcile

# live-runs.json values matching GOOD_REPORT's arm table; trap-detections.json
# matching its trap table (t1 caught by arm-C only, t2 by arm-B only, t3 by
# nobody -> "2 of 3 traps caught", 9/9 features landed, 7/9 traps survived).
LIVE_RUNS = [
    {"run_id": "SPEED-1", "arm": "arm-A", "ticket": "t1",
     "tokens": 40000, "duration_ms": 12 * 60000, "stop_points": 1},
    {"run_id": "SPEED-2", "arm": "arm-A", "ticket": "t2",
     "tokens": 55000, "duration_ms": 15 * 60000, "stop_points": 1},
    {"run_id": "SPEED-3", "arm": "arm-A", "ticket": "t3",
     "tokens": 61000, "duration_ms": 19 * 60000, "stop_points": 1},
    {"run_id": "SPEEDB-1", "arm": "arm-B", "ticket": "t1",
     "tokens": 70000, "duration_ms": 20 * 60000, "stop_points": 2},
    {"run_id": "SPEEDB-2", "arm": "arm-B", "ticket": "t2",
     "tokens": 81000, "duration_ms": 25 * 60000, "stop_points": 3},
    {"run_id": "SPEEDB-3", "arm": "arm-B", "ticket": "t3",
     "tokens": 92000, "duration_ms": 31 * 60000, "stop_points": 2},
    {"run_id": "SPEEDC-1", "arm": "arm-C", "ticket": "t1",
     "tokens": 150000, "duration_ms": 44 * 60000, "stop_points": 6},
    {"run_id": "SPEEDC-2", "arm": "arm-C", "ticket": "t2",
     "tokens": 170000, "duration_ms": 55 * 60000, "stop_points": 8},
    {"run_id": "SPEEDC-3", "arm": "arm-C", "ticket": "t3",
     "tokens": 190000, "duration_ms": 70 * 60000, "stop_points": 7},
]


def _detections():
    caught = {"t1": "arm-C", "t2": "arm-B"}     # t3: nobody caught it
    return {"detector": "detect.py", "captured": "2026-09-05", "runs": [
        {"run": "%s-%s" % (r["arm"].replace("arm-", "arm"), r["ticket"]),
         "arm": r["arm"], "ticket": r["ticket"], "feature_landed": True,
         "trap_survived": caught.get(r["ticket"]) != r["arm"]}
        for r in LIVE_RUNS]}


CENSUS = {"by_track": {"fast": {"closed": 2, "defects": 0},
                       "standard": {"closed": 38, "defects": 12},
                       "full": {"closed": 14, "defects": 4}}}


def _runs_dir(tmp_path, usd_rows=None, gitlog=None, arms=None, tickets=None,
              retro_rows=None, events=None, live_runs=None, detections=None,
              census=None):
    """Build a committed-inputs directory. The defaults match GOOD_REPORT; each
    test changes one thing."""
    d = os.path.join(str(tmp_path), "runs")
    os.makedirs(d, exist_ok=True)
    tickets = tickets if tickets is not None else [
        {"id": "ADT-1", "track": "fast"}]
    with open(os.path.join(d, "retro-tickets.json"), "w") as fh:
        json.dump(tickets, fh)
    with open(os.path.join(d, "retro-ledger.log"), "w") as fh:
        fh.write("".join(retro_rows or []))
    os.makedirs(os.path.join(d, "retro-events"), exist_ok=True)
    for rec in tickets:
        with open(os.path.join(d, "retro-events", "%s.json" % rec["id"]), "w") as fh:
            json.dump(events if events is not None else [], fh)
    with open(os.path.join(d, "retro-census.json"), "w") as fh:
        json.dump(census if census is not None else CENSUS, fh)
    with open(os.path.join(d, "live-runs.json"), "w") as fh:
        json.dump(LIVE_RUNS if live_runs is None else live_runs, fh)
    with open(os.path.join(d, "trap-detections.json"), "w") as fh:
        json.dump(_detections() if detections is None else detections, fh)
    arms = arms if arms is not None else {
        "arm-A": ["SPEED-%d" % i for i in (1, 2, 3)],
        "arm-B": ["SPEEDB-%d" % i for i in (1, 2, 3)],
        "arm-C": ["SPEEDC-%d" % i for i in (1, 2, 3)],
    }
    with open(os.path.join(d, "live-arms.json"), "w") as fh:
        json.dump(arms, fh)
    # A flat estimator and rows with LIVE_RUNS' token counts make the USD
    # figures the same on any host.
    with open(os.path.join(d, "cost-estimator.json"), "w") as fh:
        json.dump({"usd_per_mtok": 100.0, "derivation_id": "test"}, fh)
    with open(os.path.join(d, "live-ledger.log"), "w") as fh:
        fh.write("".join(usd_rows if usd_rows is not None else [
            _erow("2026-09-05T10:00:00Z", r["run_id"], r["tokens"])
            for r in LIVE_RUNS]))
    with open(os.path.join(d, "live-gitlog.txt"), "w") as fh:
        fh.write(gitlog if gitlog is not None else (
            "aaa 2026-09-05T09:00:00Z trap: plant the rename ripple\n"
            "bbb 2026-09-05T09:05:00Z trap: plant the config default\n"
            "ccc 2026-09-05T10:00:00Z arm-A: fix it\n"))
    return d


def test_reconcile_flags_a_trap_planted_after_an_arm_ran(tmp_path):
    """Every trap commit must come before every arm commit."""
    d = _runs_dir(tmp_path, gitlog=(
        "ccc 2026-09-05T09:00:00Z arm-A: fix it\n"
        "aaa 2026-09-05T10:00:00Z trap: planted AFTER the arm ran\n"))
    problems = alc.reconcile(_write(tmp_path, "r.md", GOOD_REPORT), d, ".")
    assert any("not strictly earlier" in p for p in problems)


def test_reconcile_flags_a_missing_trap_commit(tmp_path):
    d = _runs_dir(tmp_path, gitlog="ccc 2026-09-05T10:00:00Z arm-A: fix it\n")
    assert any("no 'trap:' commits" in p
               for p in alc.reconcile(_write(tmp_path, "r.md", GOOD_REPORT), d, "."))


def test_reconcile_flags_too_few_run_ids(tmp_path):
    """The design is 9 runs; fewer means an arm did not complete."""
    d = _runs_dir(tmp_path, usd_rows=[
        _row("2026-09-05T10:00:00Z", tix="SPEED-%d" % i) for i in range(1, 5)])
    assert any("distinct run ids" in p
               for p in alc.reconcile(_write(tmp_path, "r.md", GOOD_REPORT), d, "."))


def test_reconcile_flags_a_track_row_whose_n_disagrees(tmp_path):
    d = _runs_dir(tmp_path, tickets=[{"id": "ADT-%d" % i, "track": "fast"}
                                     for i in range(1, 6)])   # n=5, report says n=1
    problems = alc.reconcile(_write(tmp_path, "r.md", GOOD_REPORT), d, ".")
    assert any("track: fast" in p and "n=" in p for p in problems)


def test_reconcile_passes_a_correct_report(tmp_path):
    """A correct report against complete inputs produces no problems at all."""
    d = _runs_dir(tmp_path)
    assert alc.reconcile(_write(tmp_path, "r.md", GOOD_REPORT), d, ".") == []


def test_reconcile_reports_missing_committed_inputs(tmp_path):
    empty = os.path.join(str(tmp_path), "empty")
    os.makedirs(empty, exist_ok=True)
    problems = alc.reconcile(_write(tmp_path, "r.md", GOOD_REPORT), empty, ".")
    assert any("missing/unreadable committed input" in p for p in problems)


def test_reconcile_compares_per_run_range_not_the_arm_total(tmp_path):
    """A report stating the per-run USD range is not compared to the arm total."""
    d = _runs_dir(tmp_path)
    with open(os.path.join(d, "cost-estimator.json"), "w") as fh:
        json.dump({"usd_per_mtok": 174.58, "derivation_id": "test"}, fh)
    # price the fixture's own rows, then state THOSE numbers in the report
    import adt_cost
    est = {"usd_per_mtok": 174.58, "derivation_id": "test"}
    priced = adt_cost.price_ledger(
        open(os.path.join(d, "live-ledger.log")).readlines(), None, est)
    vals = sorted(round(priced[adt_cost.canon_tix(i)]["micros"] / 1e6, 2)
                  for i in ("SPEED-1", "SPEED-2", "SPEED-3"))
    good = GOOD_REPORT.replace("$4.00-$6.10", "$%.2f-$%.2f" % (vals[0], vals[-1]))
    problems = alc.reconcile(_write(tmp_path, "r.md", good), d, ".")
    assert not [p for p in problems if "arm-A" in p and "does not match" in p], problems


def test_reconcile_flags_a_report_usd_that_disagrees_with_the_rows(tmp_path):
    d = _runs_dir(tmp_path)
    est = os.path.join(d, "cost-estimator.json")
    with open(est, "w") as fh:
        json.dump({"usd_per_mtok": 174.58, "derivation_id": "test"}, fh)
    bad = GOOD_REPORT.replace("$4.00-$6.10", "$999.00-$1000.00")
    problems = alc.reconcile(_write(tmp_path, "r.md", bad), d, ".")
    assert any("arm-A" in p and "does not match" in p for p in problems), problems


# --- one wrong figure per column ---------------------------------------------
#
# Each case changes one figure in an otherwise correct report and expects a
# problem naming that column.

@pytest.mark.parametrize("before,after,expect", [
    ("| 40,000-61,000 |", "| 1-2 |", "tokens"),
    ("| 12-19 |", "| 99-99 |", "wall-clock"),
    ("$4.00-$6.10 | 1-1 |", "$4.00-$6.10 | 77-77 |", "stop-points"),
    ("| track: fast | n=1 | $25.33 | $25.33 | $0.00 |",
     "| track: fast | n=1 | $25.33 | $25.33 | $999.99 |", "plan lane"),
    # More than 2 cents off, because the comparison allows a cent of rounding.
    ("$25.33 | $0.00 | $0.00 | 0 of 2 closed",
     "$25.33 | $0.00 | $88.00 | 0 of 2 closed", "qa lane"),
    ("0 of 2 closed", "99 of 2 closed", "defects"),
])
def test_reconcile_flags_each_wrong_column(tmp_path, before, after, expect):
    d = _runs_dir(tmp_path)
    assert before in GOOD_REPORT, before
    bad = GOOD_REPORT.replace(before, after, 1)
    problems = alc.reconcile(_write(tmp_path, "r.md", bad), d, ".")
    assert any(expect in p for p in problems), (expect, problems)


def test_reconcile_flags_a_per_ticket_figure_that_is_not_total_over_n(tmp_path):
    """`per ticket` must equal total USD divided by n."""
    d = _runs_dir(tmp_path, tickets=[{"id": "ADT-1", "track": "fast"},
                                     {"id": "ADT-2", "track": "fast"}],
                  retro_rows=[_erow("2026-09-01T11:30:00Z", "ADT-1", 100000),
                              _erow("2026-09-01T11:30:00Z", "ADT-2", 100000)],
                  events=EVENTS_FAST)
    # total $20.00 over n=2 is $10.00 per ticket; the report says $25.33.
    bad = GOOD_REPORT.replace("| track: fast | n=1 |", "| track: fast | n=2 |", 1)
    problems = alc.reconcile(_write(tmp_path, "r.md", bad), d, ".")
    assert any("per-ticket" in p for p in problems), problems


def test_reconcile_recomputes_the_lane_split_from_events_plus_ledger(tmp_path):
    d = _runs_dir(tmp_path,
                  retro_rows=[_erow("2026-09-01T11:30:00Z", "ADT-1", 30000),
                              _erow("2026-09-01T12:30:00Z", "ADT-1", 70000)],
                  events=EVENTS_FAST)
    # planned holds the 11:30 row ($3.00), qa the 12:30 row ($7.00).
    good = GOOD_REPORT.replace(
        "| track: fast | n=1 | $25.33 | $25.33 | $0.00 | $0.00 |",
        "| track: fast | n=1 | $10.00 | $10.00 | $3.00 | $7.00 |", 1)
    assert alc.reconcile(_write(tmp_path, "g.md", good), d, ".") == []
    bad = good.replace("| $3.00 | $7.00 |", "| $7.00 | $3.00 |", 1)
    problems = alc.reconcile(_write(tmp_path, "b.md", bad), d, ".")
    assert sum("lane" in p for p in problems) == 2, problems


def test_reconcile_flags_a_missing_column(tmp_path):
    """A dropped column is reported as missing rather than skipped."""
    d = _runs_dir(tmp_path)
    bad = GOOD_REPORT.replace(" | 0 of 2 closed |", " |", 1).replace(
        "| qa lane | post-merge defects |", "| qa lane |", 1)
    problems = alc.reconcile(_write(tmp_path, "r.md", bad), d, ".")
    assert any("no post-merge-defect column" in p for p in problems), problems


def test_reconcile_flags_a_trap_credited_to_an_arm_that_did_not_catch_it(tmp_path):
    """The arm credited with a trap must match the detector output."""
    d = _runs_dir(tmp_path)
    bad = GOOD_REPORT.replace("| trap-1 | ripple | caught by arm-C |",
                              "| trap-1 | ripple | caught by arm-A |", 1)
    problems = alc.reconcile(_write(tmp_path, "r.md", bad), d, ".")
    assert any("trap-1" in p and "arm-A" in p for p in problems), problems


def test_reconcile_flags_a_trap_credited_to_a_dearer_arm_than_caught_it(tmp_path):
    """A trap caught by several arms must be credited to the cheapest one."""
    d = _runs_dir(tmp_path, detections={"runs": [
        dict(r, trap_survived=False) if r["ticket"] == "t1" else r
        for r in _detections()["runs"]]})     # now every arm caught t1
    problems = alc.reconcile(_write(tmp_path, "r.md", GOOD_REPORT), d, ".")
    assert any("trap-1" in p and "costs less" in p for p in problems), problems


def test_reconcile_flags_a_trap_count_that_overstates_the_detectors(tmp_path):
    d = _runs_dir(tmp_path)
    bad = GOOD_REPORT.replace("2 of 3 traps caught", "3 of 3 traps caught", 1)
    problems = alc.reconcile(_write(tmp_path, "r.md", bad), d, ".")
    assert any("traps caught" in p for p in problems), problems


def test_reconcile_flags_a_headline_that_overstates_what_landed(tmp_path):
    d = _runs_dir(tmp_path)
    bad = GOOD_REPORT.replace("7/9 traps survived", "0/9 traps survived", 1)
    problems = alc.reconcile(_write(tmp_path, "r.md", bad), d, ".")
    assert any("traps survived" in p for p in problems), problems


def test_reconcile_flags_a_blind_count_the_runs_do_not_support(tmp_path):
    d = _runs_dir(tmp_path, detections={"runs": _detections()["runs"][:6]})
    problems = alc.reconcile(_write(tmp_path, "r.md", GOOD_REPORT), d, ".")
    assert any("ran blind" in p for p in problems), problems


def test_reconcile_flags_an_arm_missing_from_live_runs(tmp_path):
    d = _runs_dir(tmp_path, live_runs=[r for r in LIVE_RUNS if r["arm"] != "arm-C"])
    problems = alc.reconcile(_write(tmp_path, "r.md", GOOD_REPORT), d, ".")
    assert any("arm-C" in p and "no runs" in p for p in problems), problems


def test_reconcile_flags_a_run_count_that_disagrees_with_live_runs(tmp_path):
    d = _runs_dir(tmp_path)
    bad = GOOD_REPORT.replace("| arm-A | 3 |", "| arm-A | 5 |", 1)
    problems = alc.reconcile(_write(tmp_path, "r.md", bad), d, ".")
    assert any("arm-A" in p and "runs" in p for p in problems), problems


def test_a_range_is_not_read_as_a_negative_number(tmp_path):
    """A range such as "12-19" parses as (12, 19), not (12, -19)."""
    assert alc._span("12-19") == (12.0, 19.0)
    assert alc._span("50,610-55,690") == (50610.0, 55690.0)
    assert alc._span("$8.84-$9.72") == (8.84, 9.72)


# --- derived figures ----------------------------------------------------------
#
# Each derived figure is a fixed sentence in the report. Each case changes one
# figure and expects a problem naming it.

@pytest.mark.parametrize("before,after,expect", [
    ("arm-B $8.10", "arm-B $9.99", "Per ticket"),
    ("arm-B 25.3 min", "arm-B 99.9 min", "Mean wall-clock"),
    ("Cost ratio to the control (mean/mean): arm-B 1.6x",
     "Cost ratio to the control (mean/mean): arm-B 9.9x", "Cost ratio"),
    ("Wall-clock ratio to the control (mean/mean): arm-B 1.7x",
     "Wall-clock ratio to the control (mean/mean): arm-B 9.9x", "Wall-clock ratio"),
    ("over the control: arm-B $2.90", "over the control: arm-B $9.90",
     "Marginal cost per ticket over the control"),
    ("over the fast lane: $8.90", "over the fast lane: $1.10",
     "full lane over the fast lane"),
    ("track: standard 0.32", "track: standard 0.99", "Post-merge defects"),
    ("| gate qa | $11.00 |", "| gate qa | $99.00 |", "trap-1"),
])
def test_reconcile_flags_each_wrong_derived_figure(tmp_path, before, after, expect):
    d = _runs_dir(tmp_path)
    assert before in GOOD_REPORT, before
    bad = GOOD_REPORT.replace(before, after, 1)
    problems = alc.reconcile(_write(tmp_path, "r.md", bad), d, ".")
    assert any(expect in p for p in problems), (expect, problems)


@pytest.mark.parametrize("prefix", [
    "Per ticket (mean of the arm's 3 runs):",
    "Mean wall-clock:",
    "Cost ratio to the control (mean/mean):",
    "Wall-clock ratio to the control (mean/mean):",
    "Marginal cost per ticket over the control:",
    "Marginal cost per ticket of the full lane over the fast lane:",
    "Post-merge defects per closed ticket:",
])
def test_a_deleted_derived_sentence_is_reported_missing(tmp_path, prefix):
    d = _runs_dir(tmp_path)
    line = [l for l in GOOD_REPORT.splitlines() if l.startswith(prefix)]
    assert len(line) == 1, prefix
    bad = GOOD_REPORT.replace(line[0] + "\n", "", 1)
    problems = alc.reconcile(_write(tmp_path, "r.md", bad), d, ".")
    assert any("states no" in p and prefix in p for p in problems), problems


def test_the_trap_marginal_cost_column_cannot_be_dropped(tmp_path):
    d = _runs_dir(tmp_path)
    bad = (GOOD_REPORT.replace("| trap | class | caught by | gate | marginal cost |",
                               "| trap | class | caught by | gate |", 1)
                      .replace("| gate qa | $11.00 |", "|", 1))
    problems = alc.reconcile(_write(tmp_path, "r.md", bad), d, ".")
    assert any("no marginal-cost column" in p for p in problems), problems


def test_a_median_ratio_stated_as_mean_over_mean_is_rejected(tmp_path):
    """On one dataset, the mean ratio passes and the median ratio fails."""
    skewed = [dict(r) for r in LIVE_RUNS]
    for r in skewed:                       # arm-A: 1, 1, 10 min -> mean 4, median 1
        if r["arm"] == "arm-A":
            r["duration_ms"] = (10 if r["ticket"] == "t3" else 1) * 60000
        elif r["arm"] == "arm-B":          # arm-B: flat 10 min -> mean 10, median 10
            r["duration_ms"] = 10 * 60000
    d = _runs_dir(tmp_path, live_runs=skewed)
    mean_ratio, median_ratio = 10.0 / 4.0, 10.0 / 1.0      # 2.5x vs 10.0x

    def ratio_problems(value):
        rep = GOOD_REPORT.replace(
            "Wall-clock ratio to the control (mean/mean): arm-B 1.7x",
            "Wall-clock ratio to the control (mean/mean): arm-B %.1fx" % value, 1)
        return [p for p in alc.reconcile(_write(tmp_path, "r.md", rep), d, ".")
                if "Wall-clock ratio" in p and "arm-B" in p]

    assert ratio_problems(mean_ratio) == [], "the correct statistic must pass"
    assert ratio_problems(median_ratio), "the median ratio must be rejected"


def test_reconcile_flags_a_wrong_marginal_cost_per_trap_caught(tmp_path):
    d = _runs_dir(tmp_path)
    bad = GOOD_REPORT.replace("Marginal cost per trap caught: $6.80",
                              "Marginal cost per trap caught: $0.00", 1)
    problems = alc.reconcile(_write(tmp_path, "r.md", bad), d, ".")
    assert any("marginal cost per trap caught" in p for p in problems), problems


def test_reconcile_flags_a_wrong_tier_cost_ratio(tmp_path):
    """Checks the "Per-ticket cost ratio against track: standard" sentence."""
    # Each tier uses its own id range so no ticket is in two tiers.
    tiers = {"standard": (["ADT-1%02d" % i for i in range(1, 13)], 40000),   # $4.00 each
             "full":     (["ADT-2%02d" % i for i in range(1, 10)], 80000),   # $8.00 each
             "fast":     (["ADT-301"], 20000)}                               # $2.00
    tickets = [{"id": i, "track": tier} for tier, (ids, _tok) in tiers.items() for i in ids]
    rows = [_erow("2026-09-01T11:30:00Z", i, tok)
            for _tier, (ids, tok) in tiers.items() for i in ids]
    d = _runs_dir(tmp_path, tickets=tickets, retro_rows=rows, events=EVENTS_FAST)
    # full/standard = 8/4 = 2.0x; fast/standard = 2/4 = 0.5x
    good = GOOD_REPORT.replace(
        "Per-ticket cost ratio against track: standard: track: full 2.0x, track: fast 0.5x.",
        "Per-ticket cost ratio against track: standard: track: full 2.0x, track: fast 0.5x.", 1)
    assert [p for p in alc.reconcile(_write(tmp_path, "g.md", good), d, ".")
            if "cost ratio against" in p] == []
    bad = good.replace("track: full 2.0x", "track: full 9.0x", 1)
    assert [p for p in alc.reconcile(_write(tmp_path, "b.md", bad), d, ".")
            if "cost ratio against" in p], "a wrong tier ratio must be rejected"

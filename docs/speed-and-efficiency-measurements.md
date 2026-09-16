# Speed and efficiency — before and after (ADT-260)

Every figure below is recomputed by the command named beside it, against this
repo. The two fixed-format sentences below are not machine-checked: ADT-260's tests
were removed on the operator's instruction. Re-derive them with the commands
named beside each figure rather than trusting the recorded number.

## What was ruled out first

The suite is not the cost. Measured 2026-09-07 on `main` at `6e2d6b8`:

| what | command | result |
|---|---|---|
| python half | `python3 -m pytest tools/tests/ tests/ -q` | 802 tests, 45s |
| shell half | `bash tests/run-shell-suite.sh` | 32 scripts, 93s |
| CI `tests` job | the workflow-runs API on the private repo, before ADT-280 removed the workflows | 3:10-3:50 |
| PreToolUse hooks | 5 runs each of `adt-done-guard.sh`, `adt-deferral-guard.sh` | ~126ms per tool call |

Nor are the review subagents: across the ledger rows carrying a command
(`.adt/state/cost-ledger.log`, 12-column rows), they are 44 of
89 turns but **3.8% of output tokens and 1.7% of cache_read**, while holding
three of ADT-205's four real catches. The cost was the main session re-executing
checks nothing had invalidated.

## Suite runs

Counted from the playbooks, for this ticket's 14 sub-steps on a diff nothing has
touched since the build-time run.

*Before* — `build.md:76` ran the discovered runner per sub-step (14), `build.md`
self-review ran the full suite (1), `qa-run.md:43` ran it again unconditionally
(1), `qa-run.md:65` ran targeted regression (1).
*After* — build self-review (1), plus the done-lane regression (1). QA cites
build's run unless the PR head moved; `release-check` already did.

Suite runs per ticket on an unchanged diff: before 17, after 2

*Source: `grep -n "Run the tests this sub-step AFFECTS" commands/build.md` and
`grep -n "working-style #7" commands/qa-run.md`, 2026-09-07.*

## Worktree cycles

The grader replayed each pinned condition in its own throwaway worktree. A plan
pins every condition to the SHA it was written at, so the count was the number of
pinned conditions; it is now the number of DISTINCT refs.

Measured on this ticket: 27 conditions, 22 pinned, 1 distinct ref.

Worktree cycles per DoD grade at one ref: before 22, after 1

*Source:*
```
python3 -c "import sys;sys.path.insert(0,'tools');from adt_dod import parse_done_evidence as p;\
e=p('<ticket.md>');k=[x for x in e if x.get('was_red_at') not in (None,'plan')];\
print(len(e),len(k),len({x['was_red_at'] for x in k}))"
```
*2026-09-07. On ADT-254 the same command reported 75 / 63 / 1, and the grader was
run three times in one build.*

## What did not change

`commands/review-*.md` and the five reviewer agents are untouched — asserted by a
DoD condition on the PR diff. They are 1.7% of cache_read and hold most of the
measured catches; buying speed by weakening them would fail this ticket.

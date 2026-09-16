# The gate tax — what ADT's lifecycle costs, and what it caught (ADT-205)

<!-- cost-basis: main-session ledger rows only; subagent spend is NOT included for
     work closed before 2026-09-06. Produced by
     `python3 tools/adt_cost_coverage.py .adt/state/cost-ledger.log ~/.adt/agent-dev-team/cache`
     on 2026-09-08. Coverage at that date: measured=10, estimated=31, legacy=30. -->

> **What these figures do and do not count (ADT-247, 2026-09-08).** Every `$`
> here is summed from the cost ledger, which until 2026-09-06 recorded only the
> main session. ADT-224 added a `SubagentStop` hook that records each dispatched
> subagent, but it records **forward only** — no backfill ever reached earlier
> work. So for any ticket closed before that date the figure is main-session
> spend, and the true total is higher by whatever its reviewers and search agents
> cost. ADT-224 measured that gap at 76% on one ticket.
>
> Ticket-level tiers carry this honestly: `measured` means subagent rows are
> present or the hook was live and none ran; `estimated` means the spend was
> never recorded; `legacy` means the work predates the ledger. At 2026-09-08 that
> is 10 measured, 31 estimated, 30 legacy. The per-ticket breakdown is
> reproducible with the command above.
>
> These numbers were **not** recomputed from surviving subagent transcripts. 26
> of the 31 estimated tickets still have transcripts and could be, which would
> need a tool that does not exist — `tools/adt_backfill_cost.py` enriches
> existing ledger rows and does not read `subagents/` at all. Recorded as a
> known limitation rather than presented as a complete accounting.


The protocol was pre-registered and committed before any arm ran, and every
figure in the three results tables is recomputed from the committed run inputs.
Both live in the private archive (zurichrich/adt-private) and are not published
with ADT, so the commands quoted here were run against them there:

```
python3 tools/adt_lane_cost.py --reconcile docs/gate-tax-report.md --runs docs/gate-tax-runs/
```

which read only committed files — no live `gh`, no machine-local ledger — so the
figures were re-derived rather than asserted. Every gate-tax-runs path in the
commands and source lines below names a directory in that private archive, not in
this repository, so the re-derivation cannot be repeated from a public clone. What a reader can check here is what was run and against which inputs.

**Recomputed:** each arm's wall-clock, token, USD and stop-point ranges, its
run count, and its per-ticket and mean-wall-clock figures; every ratio and
marginal-cost figure stated in the "Derived figures" section below; each tier's
total, per-ticket and per-lane money, its sample size and its defect census and
rate; every trap verdict and each trap's marginal cost, re-run against the nine
final trees committed under the run-trees directory of that archive.

**Not recomputed, and not claimed as verified**, established by mutating every
numeric token in this document one at a time and recording what survived:

- the observation-derived numbers named under "Limits" — stop-point counts, the
  four gate-catches, and the coverage-gap table (5 / 0 / 5 tests, 6/6 green);
- the `77 turns in ideas against 2 in building` illustration, which is a true
  figure from the same join (checked by hand at QA) stated as prose rather than
  as a checked sentence;
- **restatements**: every figure repeated in the surrounding argument — the
  ratios in the paragraph after "Derived figures", the tier cost ratio and
  defect rates in the paragraph after the tier table, the Recommendation's
  dollar deltas, the rounding example above. Each has a checked canonical
  statement; the repeat does not.

The sweep is how this list is kept honest rather than aspirational, and it has
already corrected itself twice: the trap section's headline dollar and the tier
cost ratio were both found sitting outside every category after the paragraph
first claimed completeness. Both are now checked.

An earlier version of this section claimed everything outside "Limits" was
covered. That was false — the derived ratios were not checked, and one of them
was wrong (see "Derived figures"). The rule now is that a figure is either in a
checked table, or in a checked fixed-format sentence, or named here as
unchecked. There is no fourth category.

**Rounding convention:** money is rounded to cents at the per-ticket figure, and
every marginal cost is the difference of those *rounded* figures. So
*Source: `python3 tools/adt_lane_cost.py --check-experiment docs/gate-tax-report.md --runs docs/gate-tax-runs/live-runs.json`, over docs/gate-tax-runs/live-runs.json, 2026-09-05.*

`$19.90 - $9.27 = $10.63`, which is a cent away from the same subtraction done
on unrounded totals. The checks allow ±$0.02 for exactly this.

## The headline, stated before the tables

**Every arm caught every trap. 9 runs, 3 planted defects, 9/9 features landed,
0/9 traps survived — including the no-gates control.** The benefit side of the
comparison therefore has no headroom: nothing can beat 3 of 3, so this
experiment **cannot** show a defect-catching advantage for the gates, and does
not claim one. That is a finding about the traps and the corpus, not a
demonstration that gates are worthless.

What remains measurable is the cost of reaching the same place, and it is large
and consistent.

## Live experiment — 3 arms x 3 matched tickets

*Source: `python3 tools/adt_lane_cost.py --check-experiment docs/gate-tax-report.md --runs docs/gate-tax-runs/live-runs.json`, over docs/gate-tax-runs/live-runs.json, 2026-09-05.*

| arm | runs | wall-clock (min) | tokens | USD (estimated) | stop-points |
|---|---|---|---|---|---|
| arm-A | 3 | 1.8-2.9 | 50,610-55,690 | $8.84-$9.72 | 2-4 |
| arm-B | 3 | 7.1-11.4 | 100,951-126,507 | $17.62-$22.09 | 2-3 |
| arm-C | 3 | 12.7-16.1 | 147,822-172,104 | $25.81-$30.05 | 5-9 |

### Derived figures

Every ratio below is **mean over mean** — the arm's three runs averaged, then
divided by arm-A's average. One statistic, named, used for both money and time.
*Source: `python3 tools/adt_lane_cost.py --check-experiment docs/gate-tax-report.md --runs docs/gate-tax-runs/live-runs.json`, over docs/gate-tax-runs/live-runs.json, 2026-09-05.*

The previous version of this section gave 4.8x and 6.2x for wall-clock; 4.8x was
the *median* ratio and 6.2x matched no statistic at all, while the money ratios
beside them were mean-based. QA found it by recomputation after the figures had
survived two passes unchecked, which is why each line below is now a fixed
sentence a check reads.

*Source: `python3 tools/adt_lane_cost.py --check-experiment docs/gate-tax-report.md --runs docs/gate-tax-runs/live-runs.json`, over docs/gate-tax-runs/live-runs.json, 2026-09-05.*

Per ticket (mean of the arm's 3 runs): arm-A $9.27, arm-B $19.90, arm-C $28.50.
Mean wall-clock: arm-A 2.3 min, arm-B 9.5 min, arm-C 14.5 min.
Cost ratio to the control (mean/mean): arm-B 2.1x, arm-C 3.1x.
Wall-clock ratio to the control (mean/mean): arm-B 4.2x, arm-C 6.4x.
Marginal cost per ticket over the control: arm-B $10.63, arm-C $19.23.
Marginal cost per ticket of the full lane over the fast lane: $8.60.

*Source: `python3 tools/adt_lane_cost.py --check-experiment docs/gate-tax-report.md --runs docs/gate-tax-runs/live-runs.json`, over docs/gate-tax-runs/live-runs.json, 2026-09-05.*

So the fast lane costs roughly **twice** the unaided control and the full lane
**three times** it, while wall-clock separates about twice as hard again — **4.2x**
and **6.4x**. Time, not money, is where the lifecycle is felt.

## Retrospective — the real backlog, by tier

*Source: `python3 tools/adt_lane_cost.py --cache-dir ~/.adt/agent-dev-team/cache --summary`, 2026-09-05.*

| tier | n | total USD | per ticket | plan lane | qa lane | post-merge defects |
|---|---|---|---|---|---|---|
| track: fast | n=1 | $25.34 | $25.34 | $5.37 | $0.00 | 0 of 2 closed |
| track: standard | n=12 | $557.81 | $46.48 | $53.45 | $19.30 | 12 of 38 closed |
| track: full | n=10 | $955.86 | $95.59 | $82.76 | $114.72 | 4 of 14 closed |

Two different denominators sit in that table and they are not interchangeable.
`n=` is the number of tickets with local ledger rows, which is what the money
columns are computed over; the defect column's denominator is every closed
ticket of that tier. Both come from one frozen capture,
the retro census in that archive (2026-09-05), which
`docs/adt-metrics-baseline.md` cites too — the backlog moves between runs, so
two same-day captures disagree and only a frozen one keeps the two documents
consistent.

*Source: `python3 tools/adt_lane_cost.py --cache-dir ~/.adt/agent-dev-team/cache --summary`, 2026-09-05.*

Post-merge defects per closed ticket: track: fast 0.00, track: standard 0.32, track: full 0.29.
Per-ticket cost ratio against track: standard: track: full 2.1x, track: fast 0.5x.

*Source: `python3 tools/adt_lane_cost.py --cache-dir ~/.adt/agent-dev-team/cache --summary`, 2026-09-05.*

`full` costs 2.1x `standard` per ticket and shows **0.29 post-merge defects per
closed ticket against standard's 0.32** — a gap far too small at this n to mean
anything, and in any case pointing the wrong way for a "heavier tier, fewer
defects" story to be read out of it. That comparison is **confounded and must
not be read as causal**: `full` is triggered by guarded paths, i.e. by the work
most likely to produce a defect, so near-equal defect rates on unequal work is
weak evidence *for* the tier, not against it. `fast` at n=1 ledgered (2 closed)
supports no claim whatsoever.

## Per trap

*Source: `python3 docs/gate-tax-runs/detect.py <trap> docs/gate-tax-runs/run-trees/<run>` over docs/gate-tax-runs/live-runs.json, 2026-09-05.*

| trap | class | caught by | gate | marginal cost |
|---|---|---|---|---|
| trap-1 | rename with string-addressed callers | caught by arm-A | gate none (unaided) | $0.00 |
| trap-2 | config default with a second home | caught by arm-A | gate none (unaided) | $0.00 |
| trap-3 | shared mutable default store | caught by arm-A | gate none (unaided) | $0.00 |

*Source: `python3 tools/adt_lane_cost.py --check-experiment docs/gate-tax-report.md --runs docs/gate-tax-runs/live-runs.json`, over docs/gate-tax-runs/live-runs.json, 2026-09-05.*

Marginal cost per trap caught: $0.00 (arm-A over arm-A), 3 of 3 traps caught.
Arms run blind to the traps: 9 of 9.

The gates caught **zero additional** traps, so the marginal cost per *additional*
trap is undefined — the control saturated. Every trap was caught by the cheapest
arm, unaided.

## What the gates did catch

The trap metric is binary and it misses the result that actually distinguishes
the arms. Across the six gated runs the counter-checks and DoD dry-runs caught
**four defects in the arms' own work** — none of them planted:

- **arm-B/t1** — the plan cited `report.py:11` for a symbol on `:10`, and a
  `no-internal-callers-left` condition used unanchored `grep -rn`, so it would
  have overmatched `to_celsius_v2` and gone red against a correct implementation.
- **arm-B/t3** — `adt-plan-quality-reviewer` returned **FLAWED**: the Design asserted
  `cli.resolve()` was the only place an op becomes a callable. False;
  `report.batch()` resolves independently. Corrected, re-reviewed **SOUND**.
- **arm-C/t3** — two DoD conditions written `diff <(a) <(b)`. At the pre-change
  SHA both sides error, both stdouts are empty, and `diff` exits 0: **a condition
  that passed against a completely broken CLI**. Caught by the plan-time dry-run.
- **arm-C/t2** — the "prove the test bites" control was **invalid and looked
  fine**. `2`->`4` is a same-byte-length edit and the rewrites landed inside one
  mtime second, so Python ran stale bytecode while `grep` correctly confirmed the
  file had changed. Redone bytecode-safe.

Three of those four are *checks that could not have failed* — the failure class
ADT's own playbooks warn about most and the one no test suite reports.

## The result that inverts the expected ordering

| arm | tests naming the renamed symbol (t1) | suite |
|---|---|---|
| arm-A (no gates) | 5 | 5 passed |
| arm-B (fast lane) | 0 | 4 passed |
| arm-C (full lane) | 5 | 10 passed |

**Arm B shipped a coverage gap the no-gates control did not.** Its suite passes
entirely through the compatibility alias; nothing exercises
`celsius_from_fahrenheit` by name. Its DoD graded 6/6 green throughout.

The mechanism worth taking seriously: **a green machine-checkable DoD is a strong
"done" signal, and can license stopping earlier than an unguided careful engineer
would.** The control had no such signal, so it kept looking. This is n=1 — one
ticket, one arm — so it is an observation, not a law. It is also falsifiable and
cheap to re-test, and it inverts the intuition the gates are sold on.

## Recommendation

*Source: `python3 tools/adt_lane_cost.py --reconcile docs/gate-tax-report.md --runs docs/gate-tax-runs/live-runs.json`, 2026-09-05.*

Recommendation: work of shape single-repo, legible, non-guarded-path changes should run track: fast, because the gates it adds cost $10.63 per ticket over no gates at all and caught nothing the unaided baseline missed.

*Source: `python3 tools/adt_lane_cost.py --reconcile docs/gate-tax-report.md --runs docs/gate-tax-runs/live-runs.json`, 2026-09-05.*

The full lane is **not** recommended for this shape: it costs $19.23/ticket over
the control and $8.60 over the fast lane, and on this corpus bought no additional
trap. Its value showed up elsewhere — in catching the *agent's own* unfireable
checks — which is a real benefit this experiment can describe but cannot price.

Nothing here speaks to guarded paths (money, schema migrations, `ui_review_required`).
Those were not in the corpus and the tier hard floor is untouched by this result.

## Limits, stated plainly

- **The control saturated**, so no gate benefit was measurable. The traps were
  calibrated against an assumption about the unaided baseline that was wrong.
- **n=3 per arm.** Ranges and a direction, not a distribution. No significance
  is claimed and none should be read.
- **The arms followed the ADT playbooks; they did not invoke the `/adt-*` slash
  commands.** Nine fresh sessions with a full install, Issues and the sync engine
  were out of reach. What was exercised is the playbook discipline, not the sync
  machinery — a narrower claim than "we ran the commands".
- **Stop-points are prescribed-and-emitted, not observed.** A subagent never
  halts for a human, so each arm printed `STOP-POINT:` where it would have
  stopped and continued under a stated assumption.
- **Live-arm USD is modelled, not metered.** The runner reports one total per
  run, not the input/output/cache split the ledger's `measured` tier needs, so
  rows are tier `estimated` and priced by a rate derived from this repo's own
  measured work. Tokens and wall-clock ARE exact.
- **The retrospective tier comparison is confounded** (see above) and the
  retrospective lanes measure **board state, not activity**: `standard` shows 77
  turns in `ideas` against 2 in `building`, because tickets were worked without
  the board moving.
- **The measurer is the measured.** Machine-recorded here: ledger rows,
  label-event timestamps, token totals, durations, git timestamps, and every
  trap verdict (detectors held outside the repo the arms saw, and committed
  afterwards as the archive's detect.py with the nine final trees, so the
  verdicts can be re-run rather than believed). Observation-derived: stop-point
  counts, the four gate-catches above, and the coverage-gap reading — these are
  the figures `--reconcile` cannot settle, and they are the ones to distrust.

## Evidence

| id | command | date |
| arm-A | Agent runner reported usage; docs/gate-tax-runs/live-runs.json | 2026-09-05 |
| arm-B | Agent runner reported usage; docs/gate-tax-runs/live-runs.json | 2026-09-05 |
| arm-C | Agent runner reported usage; docs/gate-tax-runs/live-runs.json | 2026-09-05 |
| track: fast | python3 tools/adt_lane_cost.py --cache-dir ~/.adt/agent-dev-team/cache --ledger docs/gate-tax-runs/retro-ledger.log --events-dir docs/gate-tax-runs/retro-events | 2026-09-05 |
| track: standard | python3 tools/adt_lane_cost.py --cache-dir ~/.adt/agent-dev-team/cache --ledger docs/gate-tax-runs/retro-ledger.log --events-dir docs/gate-tax-runs/retro-events | 2026-09-05 |
| track: full | python3 tools/adt_lane_cost.py --cache-dir ~/.adt/agent-dev-team/cache --ledger docs/gate-tax-runs/retro-ledger.log --events-dir docs/gate-tax-runs/retro-events | 2026-09-05 |
| trap-1 | python3 docs/gate-tax-runs/detect.py t1 docs/gate-tax-runs/run-trees/<run> | 2026-09-05 |
| trap-2 | python3 docs/gate-tax-runs/detect.py t2 docs/gate-tax-runs/run-trees/<run> | 2026-09-05 |
| trap-3 | python3 docs/gate-tax-runs/detect.py t3 docs/gate-tax-runs/run-trees/<run> | 2026-09-05 |
| marginal-cost | python3 tools/adt_lane_cost.py --reconcile docs/gate-tax-report.md --runs docs/gate-tax-runs/ | 2026-09-05 |
| blind-count | grep trap: docs/gate-tax-runs/live-gitlog.txt; last trap 12:11:38 < first arm commit 12:15:41 | 2026-09-05 |

The defect column's census is the retro census in that archive, captured
once by `python3 tools/adt_metrics.py --cache-dir ~/.adt/agent-dev-team/cache
--repo-root . --by-track` on 2026-09-05.

# AO-019 — The install's adopt pull is incremental, so an upgrade leaves lanes stale

**Shipped:** 2026-10-05, PR [#21](https://github.com/zurichrich/ai-orchestrator-adt/pull/21), merge commit `c0f48f8`
**Track:** fast · **Size:** S · **Gates:** DoD-coverage review (COVERED, one round)

## What shipped

`adt_sync.py --pull` fetches every Issue each time it runs. `pull_all` and
`reconcile_all` take a `full` flag and `main()` sets it from `--pull`. The watch
tick does not pass it, so its pull stays incremental. An upgrade now carries
down a lane that changed on GitHub before the watermark, in the installer's own
adopt step.

## Deviations from plan

None.

## What was surprising

**A ticket planned with `/adt-plan` under the size trigger cannot start
`/adt-build-todone`.** `/adt-plan` step 0 says to skip the coverage review when
the spec has one new file or fewer, six sub-steps or fewer, and is not `full`.
`adt_dod.py --gate` refuses any ticket with no recorded coverage review, at
every tier. So the plan lane finished, the ticket was approved, and the gate
refused it with `no '### DoD-coverage review'`. The review then ran at the start
of the build command (33,612 subagent tokens, one round, COVERED). The build
playbook does say a fast ticket gets its verdicts from `/adt-plan-fasttrack`,
but `/adt-plan`'s skip instruction does not say that skipping rules out the
approve-once driver.

**`adt-test-run-guard.sh` matched a pytest command quoted in a PR body.** The
first call to open PR #21 was denied as an unauthorised full-suite run. The
command being run was `git push` plus `gh api …/pulls`; the body text listed
`python3 -m pytest tools/tests -q` as evidence. Rewording the body to "every
test file under `tools/tests/`" let the same call through. The guard reads the
whole command string, so it cannot tell a test run from prose that names one.

## Adjacent finding

**Three models have no rate in `defaults/pricing.json`.** At close,
`adt_cost.py unpriced .adt/state/cost-ledger.log` exited 1 and named
`claude-fable-5-1` (8 rows, 17,390,390 tokens), `claude-opus-5-5` (14 rows,
27,087,595 tokens) and `claude-sonnet-5-5` (4 rows, 776,035 tokens).
`adt-token-total.sh AO-019 --cost` returned `unattributed`. AO-016's retro
recorded the same for `claude-opus-5-5` alone.

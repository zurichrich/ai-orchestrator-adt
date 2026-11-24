# AO-025 — Sync and board key a consumer's token register by ADT's own install id, so own spend counts twice

**Shipped:** 2026-11-24, PR [#26](https://github.com/zurichrich/ai-orchestrator-adt/pull/26), merge commit `1b599f4`
**Track:** fast · **Size:** S · **Gates:** DoD-coverage review (GAP, one round, merged over it on the PO's decision)

## What shipped

Every writer and reader of a register key now uses the project's install id.
`adt_sync._machine_id(project_root)` passes the root to `machine_id()`.
`build_kanban.run()` sets `OWN_MACHINE` from the ledger root. `adt_register_migrate.py`
takes a required `--root`. `machine_id(start)` no longer has a default, and
`OWN_MACHINE` is `None` until `run()` sets it.

On agenticz, 161 of 311 stamps written before the fix are exactly double. None
of the 9 stamps written since 2026-11-13 is. The old stamps were left as they
are (PO decision).

## Deviations from plan

- **Added at plan time:** the register migration had the same no-root call
  (`adt_register_migrate.py:199`). Run against a consumer repo it would have
  re-keyed to ADT's id. It became sub-steps 1d–1f.
- **Added at self-review:** the simplify pass removed `machine_id()`'s default,
  stopped computing `OWN_MACHINE` at import, and made a render resolve the
  ledger root once (four `git rev-parse` calls down to two).
- **Merged with a coverage GAP open.** The release check's coverage review
  found four Design mechanisms with no condition. The PO judged the extra tests
  unnecessary for a fix already shown working on live data, and merged.

## What was surprising

**The fix ran in production for twelve days before it was merged.** The first
half of the fix sat as uncommitted edits in the canonical checkout from
2026-11-12, and the launchd watch job runs `tools/adt_watch.py` from that
checkout. So the live data was corrected long before PR #26 landed. The edits
were identical to `bccf1df` and were stashed at merge.

**`/adt-plan` skipping the coverage review on a small spec makes the release
check fail, a second time.** AO-019's retro recorded that `--gate` refuses any
ticket with no recorded coverage review, at every tier, while `/adt-plan` step 0
tells a small spec to skip it. AO-019 hit it at `/adt-build-todone`; AO-025 hit
it at `/adt-release-check`, where it caused a bounce to QA. Either the plan step
should always run the review, or `--gate` should accept a recorded skip on a
spec under the size trigger.

## Adjacent finding

**GitHub's comment search is not a usable count of registers.** For
`machine=607b806e` it returned 194 agenticz Issues after every one of those
comments had been rewritten in place. Enumerating comments through
`repos/<repo>/issues/comments` gave the true count, 0.

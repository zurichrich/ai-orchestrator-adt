# AO-007 — Pull never carries a ticket's lane down, so every machine after the first goes silently stale

**Shipped:** 2026-09-20, PR [#9](https://github.com/zurichrich/ai-orchestrator-adt/pull/9), merge commit `b54d554`
**Track:** fast · **Size:** M · **Gates:** neither counter-check (one new file, five sub-steps — below both triggers)

## What shipped

The pull now moves a cache file into the stage folder the Issue's `stage:` label
names, guarded by four conditions, and `reconcile_all` records the moved file's
hash under its new path before persisting the sidecar so the next tick makes no
call. `state:` is derived from the destination lane, never copied from the Issue.
`tools/adt_sync.py` (+168), `tools/tests/test_pull_lane_convergence.py` (new,
six tests, two caches and the real renderer in the loop), `docs/backlog-sync.md`
(+43).

## Deviations from plan

**Three, all recorded in the build log as they happened.**

1. The plan's `_relocate_to_stage(path, data, issue_stage, issue_state, cfg,
   state)` became `(path, data, recovered, cfg, pushed_hashes)` — the call site
   already holds the whole `recovered` dict. No behaviour change.
2. The plan said `pull_all`'s signature would not change. It gained an optional
   `pushed_hashes` parameter during the review pass. The plan's reason for
   keeping it fixed was the number of test call sites, which is not a design
   argument; the parameter makes guard 3's correctness a property of the call
   instead of of `reconcile_all`'s internal ordering, and the default keeps every
   existing call site working.
3. The `docs` condition's literal wrapped across two lines. The doc was reflowed
   rather than the pattern shortened, so the approved condition stayed as
   written.

## What was surprising

### 1. The fix the ticket originally specified would have been worse than the bug

The brief's own "The fix" section said to write `stage`/`state` into the
frontmatter and leave the file where it was, and rejected moving the file as
"unnecessary" because "the board renders the lane from the `stage:` frontmatter
field, not the folder". That premise is false — `build_kanban.load_items` takes
the lane from the folder and `sync_stage_frontmatter` rewrites `stage:` from the
folder on every render. The frontmatter-only fix is reverted by the next render,
which re-arms the push, which sends the stale lane back up: `gh issue reopen` on
every tick, on both machines, forever. ADT-147's branching process with `f = 1`,
started deliberately.

The brief was confidently wrong in a specific, traceable way, and the confusion
is understandable: ADT-155 made `type:` frontmatter-authoritative, and
`build_kanban.py`'s own comment explains that stage is the reverse *because
stage has a mover*. Reading the ADT-155 rule and generalising it to stage is a
one-step mistake.

The general lesson is about where a plan's premises get checked. A brief can
carry a factual claim about the system that no gate examines — the DoD grades
the built thing, and both counter-check gates were below their size trigger here.
The claim was only caught because the review question was "is this the correct
fix?" rather than "build this".

### 2. The same defect the fix exists to prevent, reintroduced through a second field

The first build copied `state:` from the Issue onto the relocated file. The
renderer derives `state` from the folder, so for an Issue closed under a
**non-done** lane label — seven of the twelve tickets in this ticket's own
measurement looked exactly like that — the pull wrote `state: closed` into e.g.
`blocked/`, the render rewrote it to `open`, `state` is inside `_push_hash`, the
push re-armed, and `gh issue reopen` ran every tick.

Reproduced before fixing (tick 1 issued `api repos/o/r/issues/7`) and after (no
`gh` verb at all). Fixed with `ticket_serializer._derive_state`, the rule the
serializer and the renderer already share.

Worth naming precisely: the fix reasoned carefully about one field and then
handled the second by copying it, and every test passed. The carve-out in the
conflict policy is *per field*, and it has to be closed under the renderer's
derivation rules for **every** field it writes, not just the one the ticket is
about.

### 3. A DoD condition that passes while the test it names does not exist

The conditions were first authored as `pytest <file> -q -k <name>`. On an
existing file, `-k` with no match prints `23 deselected` and **exits 0**, and
`adt_dod._NO_WORK` only matches `no tests ran` / `Ran 0 tests` / `0 passed, 0
failed`. So a renamed or never-written test grades green. Rewritten to the
node-id form `pytest "<file>::<name>" -q`, which exits 4 and prints `no tests
ran` when the name is absent.

### 4. AO-005's lesson recurred one ticket later, because it was only written in a retro

The `docs` condition's literal wrapped across two lines and could not match —
which is exactly the finding AO-005's retro recorded a day earlier. CLAUDE.md
says a lesson worth keeping is written into the playbook or rule it changes, and
that there is no separate lessons archive. AO-005's lesson went into its retro
and nowhere else, so the next ticket repeated it.

Both this and finding 3 are now in `commands/plan.md`, in the DoD-authoring
checks. That is the actionable part of this retro.

## Adjacent findings

Held from the build, recorded here and nowhere else.

- **The default lane list still lives in four places, and two already disagree.**
  This ticket removed the copy it would have added (`_lane_names` now falls back
  to `build_kanban.STATUSES`), but `build_kanban.py`, `adt_lane_cost.py`,
  `lib/github-bootstrap.sh` and `lib/git-helpers.sh` each carry one, and the two
  shell copies differ in order. Nothing detects the drift.
- **The sidecar is parsed twice per tick.** `_load_pull_state` and
  `_load_sync_state` each read and parse the whole file; this ticket removed a
  third read it had introduced. ~0.2 ms per parse on a 400-ticket board.
- **`pull_all`'s comment about the idle tick is optimistic.** The comment above
  the `by_number` index says the whole-cache walk is skipped on a converged tick,
  but `pull_all`'s own docstring records that `since` is inclusive so the newest
  Issue comes back every tick. The walk therefore runs most ticks. Pre-existing,
  and cheap enough that nothing is obviously owed.

## What worked

The `/simplify` pass earned its cost here and it is worth saying why: the
`state`-copy defect (finding 2) was found by the **altitude** agent, whose brief
was explicitly not to hunt for correctness bugs. It surfaced as "this carve-out
is not closed under the renderer's derivation rules", which is an altitude
observation that happened to have a live defect underneath it. Three of the four
agents also independently converged on passing the pre-push hash map into
`pull_all`.

Every finding was re-verified locally before being applied, and two agent claims
were wrong on detail (one mis-stated which config file carries `stages:`, one
mis-predicted that the real `load_items` would be impure in a test) — both cheap
to check, and checking is what made the rest trustworthy.

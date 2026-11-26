# AO-028 — Management report, then Epic and Feature tickets

**Shipped:** phase 1 in ADT 0.4.0 (PR [#33](https://github.com/zurichrich/ai-orchestrator-adt/pull/33)); phase 2 in ADT 0.5.0 (PR [#37](https://github.com/zurichrich/ai-orchestrator-adt/pull/37), merge commit `ffeddf6`)
**Track:** full · **Size:** L · **Gates:** plan-quality, DoD-coverage, design review (2 rounds per phase), QA (2 passes in phase 2)

## What shipped

Phase 1 added `.adt/management-report.html`: Epic › Feature › Ticket on a
timeline, with owners, devs, plan against actual, and cost. Epic and Feature
were free-text `epic:`/`feature:` names on each ticket.

Phase 2 made Epics and Features tickets with their own Issues. A ticket names
its Feature with `parent:`, a Feature names its Epic, and the sync mirrors each
link as a GitHub sub-issue. The board shows `Epic:` and `Feature:` cards whose
lane is derived from their children. The plan gate needs a parent chain, and
`/adt-group` groups an existing backlog once the product owner approves an
outline. ADT's own 13 tickets were grouped under 5 Epics and 7 Features after
the merge.

## Deviations from plan

- The plan said `lib/install-defaults.sh` needed no change. Project sessions
  only reach `.claude/tools/`, so every tool a playbook calls had to be
  installed there.
- The surface catalogue needed three changes the plan did not list:
  `BOARD_EXEMPT`, `docs/surface-review.md`, and the command count in
  `docs/references.md`.
- The schema needed separate `epic` and `feature` rows, because a DoD condition
  greps for each.
- The board's lane derivation was built a sub-step early, because the board did
  not load the new `epics/` and `features/` folders and the report tests needed
  it.

## What was surprising

**Fixture data disagreed with real data in two places.** The board titles a
card from the body H1 while the CLI reads `title:`, and the render rewrites
`id:` from `issue_number:`. Real tickets are consistent on both because
`create()` writes them together. The fixtures were not, and the first test
failures came from the fixtures.

**QA's mutation sweep found two figures no test watched.** These were an open
Epic's own slip and the page's `children:` line. Both were plausible code that
the author's own tests happened to miss. Running the page's JavaScript under
`node` in a pytest test was cheap and closed the second gap.

**`rsync` skipped a version bump.** At release, `rsync -a` kept 16 of 50
`.claude/` files at `v0.4.0`, because "v0.4.0" and "v0.5.0" are the same length
and the copies were written in the same second. `rsync -ac` (compare checksums)
fixed it. `tests/test_installed_copies_match_source.sh` passed both times,
because it compares bodies and not the bundle stamp. Any release refresh that
copies an install over `.claude/` must compare contents.

## Decisions

ADR-038 to ADR-042 in `docs/decisions.md`.

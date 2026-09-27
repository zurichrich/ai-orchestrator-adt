# AO-016 — Tickets filed by /adt-brief never appear on the local kanban

**Shipped:** 2026-09-27, PR [#17](https://github.com/zurichrich/ai-orchestrator-adt/pull/17), merge commit `39f74ec`
**Track:** fast · **Size:** S · **Gates:** none (below the counter-check size trigger)

## What shipped

`load_items()` in `tools/build_kanban.py` reads `bug/`, `enhancement/` and
`task/` as well as the plural type folders, so a ticket file an agent put in a
singular folder shows on the local board. The six `<type>/` path lines in the
brief, block, unblock and close playbooks now name `<bugs|enhancements|tasks>/`.

## Deviations from plan

None.

## What was surprising

**`pytest <file>::<name> <file>` exits 0 when the named test does not exist.**
The first draft of the DoD's C1 ran the new test by node id and its file in one
call, to get the regression run in the same condition. pytest runs the file's
other tests and ignores the missing node id: checked on the pre-build tree, node
alone exits 4 and node plus file exits 0 (16 passed). The condition would have
graded green with the new test never written, and the dry-run cannot tell,
because it only reports the exit code. `commands/plan.md` check 9 already says
to name a test by node id rather than `-k`; this is a second way to lose the
node id's "exits 4 when absent" property. C1 was rewritten as
`pytest <node> -q && pytest <file> -q`.

## Adjacent finding

**`claude-opus-5-5` has no rate in `defaults/pricing.json`.** At close,
`adt_cost.py unpriced .adt/state/cost-ledger.log` exited 1 and named it:
6 rows, 13,825,964 tokens. `adt-token-total.sh AO-016 --cost` returned
`unattributed`, so this ticket's `cost_usd` is unattributed, and every ticket
run on that model will be too until the rate is added.

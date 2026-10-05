# AO-020 — Price table missing the 5.5 and 5.1 models; per-model cache-read rate

**Shipped:** 2026-10-05, PR [#23](https://github.com/zurichrich/ai-orchestrator-adt/pull/23), merge commit `65beeaf`
**Track:** fast · **Size:** S · **Gates:** coverage (GAP, then COVERED), plan-quality (SOUND)

## What shipped

`defaults/pricing.json` has entries for `claude-opus-5-5`, `claude-sonnet-5-5`,
`claude-fable-5-1` and `claude-mythos-5-1`, at version `2026-09-30`. A model
entry can carry `cache_read_mult`, and `rates_for` in `tools/adt_cost.py` uses it
in place of the global 0.1x cache-read ratio. AO-016, which had closed as
`unattributed`, was re-stamped by hand.

## Deviations from plan

`.claude/.adt-manifest.json` is in the diff and the plan did not list it. The
installer rewrites `source_commit` and the file hashes whenever it regenerates
`.claude/`, so any ticket that refreshes an installed copy changes it.

## What was surprising

**The coverage gate caught a condition that would have passed on the wrong
number.** The first AO-016 condition was `^cost_usd: [1-9]`. The plan's own
Design named $12.14 as the figure a missing per-model ratio produces, and that
figure satisfied the condition. It now pins `8.1` and `cost_tier: measured`.

**A fixture pins the table version.**
`tools/tests/fixtures/cost-ledger/expected.json` holds `price_table_version`,
and `test_cost_fixture.py` asserts it equals the table's. Every version bump
fails that test until the fixture moves too. The brief did not mention it; a
grep for the version string found it.

## Adjacent findings

**`--check-authoring` reads prose in a `Depends-on-unmodified:` line as file
names.** The coverage reviewer's round-1 line had a parenthetical: "(the
manifest-driven comparison covers `.claude/tools/pricing.json` and
`adt_cost.py`)". After the spec changed, `--check-authoring` printed six
`REOPENED` lines. Three named the words `and`, `it` and `is` as files, and one
named `.claude/tools/pricing.json`, which the reviewer had mentioned and not
declared. The parser is `_RESTS_ON_RE` in `tools/adt_dod.py`. Asking the
reviewer to write bare `path:symbol` entries cleared it.

**`restamp_closed` still skips a ticket whose rows are all unpriced in ADT's
table.** `tools/adt_sync.py` reads `local is None` and continues. A project that
patches its installed table before ADT has the model gets no re-stamp, which is
why agenticz re-stamped 16 tickets by hand. This ticket put the two tables level
and left the skip in place, so the next new model repeats it.

**`/adt-plan` does not tell the human how to approve.** `/adt-plan-fasttrack`
ends by printing the `plan_approved: true` line the human must add. `/adt-plan`
ends at the move to `planned/` and says nothing about it. On this ticket the PO
typed "approved" twice and ran `/adt-build-todone` twice before the line was in
the frontmatter, and each run was refused.

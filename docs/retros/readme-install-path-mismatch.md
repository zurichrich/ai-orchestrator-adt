# AO-005 — README's install commands break for anyone who clones into a directory named after the repo

**Shipped:** 2026-09-20, PR [#8](https://github.com/zurichrich/ai-orchestrator-adt/pull/8), merge commit `3bc3ca7`
**Track:** fast · **Size:** S · **Gates:** DoD-coverage (2 rounds), no plan-quality (below the size trigger)

## What shipped

The documented clone target moved from `~/agent-dev-team` to
`~/ai-orchestrator-adt` in 22 places across `README.md`,
`docs/e2e-install-walkthrough.md` and `docs/install-model.md`, plus the `--help`
header in `adt-install.sh`. The README gained a `### Prerequisites` section and a
platforms paragraph, and its first H2 stopped naming the old project.

## Deviations from plan

**One, structural.** Sub-step 1d said to add `### Prerequisites` after
`## Install` and before the clone command. Doing only that leaves the clone
instructions dangling under the Prerequisites heading, so the build also added a
sibling `### Clone and run it` heading the plan had not named. The DoD did not
notice, because `readme-prerequisites` only asserts the ordering of the two
anchors, and both still hold.

## What was surprising

**A `grep -qF` condition on prose is silently unsatisfiable if the prose wraps.**
`readme-platforms` greps for the literal `on macOS as a launchd user agent and on
Linux as a `. The build wrote that sentence wrapped at 80 columns, so the literal
straddled a newline, and `grep -F` matches within a line. The condition went red
against text that said exactly what it was supposed to say.

The plan-time dry-run cannot catch this. It runs each condition against a tree
where the target prose does not exist yet, so the condition is red for the
expected reason and looks correctly authored. The mismatch only appears once the
prose is written.

The rule that follows: when a condition greps a literal the build has yet to
write, the build must keep that literal on one line, and the plan should say so
in the sub-step. Sub-step 1e named its three literals, which made the fix obvious
— reflow the paragraph so each one is a whole line — but it did not say they had
to be unbroken.

**The rename would have killed a test without failing it.**
`tests/test_walkthrough_doc.sh` built its list of named scripts by grepping the
walkthrough for `~/agent-dev-team/<path>`. After the rename that grep matches
nothing, the `while` loop body never runs, `missing` stays `0`, and the script
prints PASS. This was caught at plan time and fixed in the same diff: the test
now counts what it extracts, fails on zero, and puts the count in its pass line.
It was control-tested against a synthetic document naming no paths.

The general shape is worth keeping. Any check whose subject is selected by a
pattern — a grep, a glob, a path filter — reports success when the pattern stops
matching, because there is nothing left to find fault with. A count in the pass
line is the cheap fix, and the DoD should grade the count rather than the exit
code.

## Adjacent findings

**The coverage gate has no tier-aware withholding, and its playbook assumes it
does.** `commands/plan.md` step 0 tells the author to skip the DoD-coverage
review below a size trigger and say so in the Plan-critique. But
`tools/adt_dod.py --gate` refuses any ticket with no recorded coverage verdict,
whatever the tier: the `plan_gate_applies()` function that scales the
plan-quality gate by `track:` has no counterpart for coverage. A `fast` ticket
that follows the playbook literally cannot pass the gate `/adt-build-todone`
runs. This is the same instruction-versus-enforcement mismatch ADT-306 fixed for
plan-quality, still open on the other gate. On this ticket the cost was one
review that the playbook's own trigger said to skip.

**Unquoted `must_run` scalars depend on a lenient parser.** The sync round-trips
ticket frontmatter and re-emits `must_run:` values without their single quotes,
so a condition beginning with `!` is stored as `must_run: ! grep -rq ...`. A
leading `!` is a tag indicator in YAML, and `cut -d: -f1` inside another
condition puts a `: ` sequence in the value. The project's own parser reads both
correctly and every condition graded as authored. The dependency is on that
parser staying lenient; nothing in the repo records it as a requirement.

## What went right

The DoD's first draft graded only the absence of the old path, which a deletion
satisfies without the new path ever being documented. The coverage reviewer
returned GAP on three conditions of exactly that shape, including one on the
`--help` header that would have passed against a deleted line. All three were
closed by widening existing conditions rather than adding new ones, so the `fast`
tier's cap of five conditions held.

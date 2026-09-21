# Self-verifiable stages — how ADT grades itself

**One line:** every stage boundary is decided by something that *runs*, and the
set of things that run is itself checked for coverage.

The problem this answers is not that ADT lacked checks. It had them. The problem
is that **the thing grading a claim took the agent's own output as its input** —
so a human had to cross-examine every plan ("are you sure?", "prove it", "why did
you give me a caveat and then ignore it?") to do by hand the checking the system
could not do for itself. That manual cross-examination was the blocker on running
a ticket design→delivery unattended.

Everything below is one idea in five places: **bind each grade to evidence the
agent cannot author.**

---

## The two enforced rules

Both were prose in `defaults/rules/working-style.md` for months and both kept
losing, because nothing graded them. That is the argument for grading them.

### Rule 1 — verify before asserting (working-style #10)

A claim about the state of the system must be traceable to something the session
actually ran or read *in that turn*, or it is not made.

Enforced by **`defaults/hooks/adt-phrase-linter.sh`** (a `Stop` hook), which pairs a

**Warn-only, alongside the two enforced rules: the counted claim (AO-013).**
`adt-phrase-linter.sh` check (e) flags a counted or completeness claim whose command cannot support it —
a sentence carrying both a quantity and a completeness word in a turn where
nothing counted anything. Two such claims shipped in one AO-006 session, "9
distinct markers, all ok" from a grep that stopped at newlines and "the only two
remaining mentions" from a three-phrase grep that never counted mentions, and a
reviewer caught both. The standard is working-style #13; `/adt-brief` has
required it of `rnd-*` research notes for longer, and this is the same rule for
every report. What counts as counting includes `len(` inside an inline `python3`
script, because that is how this project's own counts are usually produced.

**Warn-only, alongside the two enforced rules: the write budget (ADT-260).**
`adt-phrase-linter.sh` also flags a ticket log entry longer than 12 lines. It is a
WARNING, not a denial. Not because it could not: exit 2 on a Stop hook blocks the stop and hands stderr back as the reason, which is what `adt-close-complete.sh` does (ADT-336). This one warns because refusing prose on length
would be worse than the verbosity. It exists because the read side was already
budgeted (`adt-budget:` in every `commands/*.md`) and the write side was not: a
retitle of three frontmatter fields produced a 30-line log entry, chosen by the
writer with no budget to hit. The entry scales with the DIFF, not with the
reasoning that produced it. Not covered by a test.
*claim* regex against the turn's `tool_use` records. Two claim shapes:

| claim | evidence required |
|---|---|
| a done-claim about a rendered artifact ("on the board", "the link works") | a tool call opening a `.adt/*.html` file this turn |
| a pass-claim ("the tests pass", "lint is green", "exits 0") | a `Bash` call this turn whose command contains the claim's **named subject** |

The second one matches the claim *to the command*, not merely to the presence of
one. "Some Bash call happened" is near-placebo — a working turn almost always
runs something — so that weak form is only the fallback for claims that name
nothing extractable, and `test_phrase_linter_claims.sh` case 5 documents that
weakness rather than hiding it.

Warn-only: a `Stop` hook cannot deny.

### Rule 2 — no casual deferral

Filing a follow-on ticket mid-build removes work from the contract the current
ticket is graded against. It is a **loosening** action, it reads as tidy
bookkeeping, and it is actually scope reduction with a paper trail — the ticket
grades green while the job is not done and a human absorbs the remainder.

A deferral needs **all three**, each stated with evidence:

1. **Unforeseen** — not reasonably visible at plan time. "It wasn't in the plan"
   does not qualify; the plan not mentioning it is the normal case.
2. **Significant deviation required** — a named conflict, a different root cause
   in different files, or a decision the human owns. **Effort and size are not
   grounds.**
3. **Human approved** — via `/adt-block`, or simply by asking for a ticket in
   prose ("create a ticket for X"), which the hook reads from the last human
   turn. There is no autonomous path to filing: an agent cannot author a turn
   that survives the `isSidechain`/`userType`/machine-prefix filter.

Enforced by **`defaults/hooks/adt-deferral-guard.sh`** (a `PreToolUse` hook), which
**denies** `gh issue create` unless the transcript authorises it.

#### Why authorisation is read only from user turns

This is the load-bearing decision in the whole design.

**An agent cannot write a `role: user` transcript turn** — *provided the reader
filters correctly.* ADT-153 found that premise false as originally implemented: a
Task-tool-spawned subagent's SEED record is the orchestrating agent's own prompt
text, arrives as `role: user`, and wears no `<command-name>`/`<command-message>`
wrapper. The hook now excludes `isSidechain` records, non-`external` `userType`,
and the full machine-prefix set, in parity with `tools/adt_xexam.py`'s
`human_turns()` — which had solved this correctly for reporting while the
security control had not. Every other candidate
signal — a marker file, an env var, the session's ticket binding — is written by
the agent and can be deleted by it, so a gate keyed on one is bypassable by `rm`
and only *looks* like a gate. The first draft of this design used the ticket
binding; review caught it.

Two things authorise a create, both from user turns:

- the current turn is a **`/adt-brief` invocation** — a human deliberately filing;
- a human **typed `ADT-APPROVE-FOLLOWON`**.

A literal token, not a natural-language match: a false positive here *is* the
failure being prevented, so the check fails toward denying.

One subtlety found by probing before building (and pinned by test 6): a slash
command puts **two** `role: user` records in the transcript — the invocation and
the *expanded playbook body*. So a playbook that merely **documented** the token
would have forged its own approval on every invocation. Expansion records carry a
`<command-message>`/`<command-name>` wrapper; a typed message does not.

Both hooks **fail open** on infra trouble (no transcript, unreadable, parse
error) and fail closed only on a check that actually ran.

---

## The plan-side gates

`adt-dod.sh <ticket.md> --gate` refuses a plan before a build starts, for seven reasons — the last two are new:

1. **No `done_evidence`** — nothing to grade.
2. **A prose condition** — the grader can't decide it.
3. **No `### DoD-coverage review`, or a `GAP`/`UNKNOWN` verdict.**
4. **An unattached caveat.**
5. **A dependency defect** — a dangling `depends_on` or a cycle.
6. **A `borrows:` declaration that does not match the source** (AO-013) — the
   declared span is not the function's span, or the declared exit lines are not
   its `return`s. A Design that cites a line inside a function and declares
   nothing is the same defect.
7. **A recorded verdict whose declared dependency the spec has moved past**
   (AO-013) — the graded text changed since that verdict was recorded and the
   file it named is still in the sub-steps.

**6 and 7 refuse only while the ticket is in `ideas/` or `planned/`; elsewhere
they print a NOTE on stderr.** A borrow declaration describes the code as it was
before the diff, so it goes stale the moment the build lands and a refusal at
release would fail a correct ticket. The lane is read from the folder, not from
`stage:`, which can lag it by a tick. `--check-authoring` runs 1, 2, 4, 5, 6 and
7 at plan time, before any reviewer is dispatched.

### 3. The coverage counter-check

`adt_dod.py` verifies each condition **passes**. Nothing verified the list
**covers the spec** — and a DoD can be 100% checkable, 100% green, and still
never assert the thing the ticket was for.

The counter-check is **`defaults/agents/adt-dod-coverage-reviewer.md`**, a subagent,
because a separate context window is where the independence comes from. It
returns COVERED / GAP / **UNKNOWN**, and UNKNOWN is a first-class answer it is
told to use freely — surfaced to the human, never read as a pass.

**The gate is on the record, not on the judge being right.** An LLM judge cannot
be graded hermetically — pytest has no model — so the two jobs are split:

- the *gate logic* is `tools/tests/test_dod_coverage_gate.py`;
- the *judge's quality* is a calibration record a human reads,
  `docs/dod-coverage-calibration.md`, built from cases in
  `tools/calibration/dod-coverage/`.

Conflating those is how a rubber stamp gets mistaken for a gate. A reviewer that
always answers COVERED would make this gate **worse than no gate**.

### 3b. The plan-quality counter-check — and why its gate is CONDITIONAL

Gate 3 asks whether the DoD *covers* the spec. It cannot ask whether the spec was
worth building. **A wrong Design with a DoD that faithfully covers it passes gate
3, passes every condition, and grades green with the wrong thing built.** That is
the question the PO otherwise has to ask by hand on every ticket — *"is this the
correct, simplest solution?"* — so ADT-126 gave it a reviewer:
`defaults/agents/adt-plan-quality-reviewer.md`, verdicts **SOUND / FLAWED /
UNKNOWN**, recorded as a `### Plan-quality review` block and read by
`adt_dod.plan_review()`.

It reads `## Problem (user)` **before** the Design, so the Design cannot define
the problem it is judged against. (Note the name: `adt-design-reviewer` is a
different agent that reviews a **UI diff**.)

**The difference from gate 3 is that this gate is conditional.** Coverage is a
set-comparison with ground truth in the artifact; "is this the simplest design
that solves the Problem" has none — the judge must hold an opinion. So an
always-SOUND reviewer is a live risk, and an uncalibrated judge that can *refuse*
is worse than no judge at all. `adt_dod.plan_gating_enabled()` therefore reads one
line from `docs/plan-quality-calibration.md`:

    gating: ENABLED | DISABLED

Below the bar the reviewer still runs and its verdict is still recorded — only
the automatic refusal is withheld, and the verdict is reported on stderr rather
than swallowed. It degrades to **recording**, never to rubber-stamping, and fails
toward not-gating when the record is missing. This is the dod-coverage README's
own instruction turned from prose into a code path.

**The bar is measured on a held-out subset.** `tools/calibration/plan-quality/`
holds eleven cases; six flawed ones are drawn from the same *instances* the
reviewer's prompt cites as worked examples, so their score is a recall floor that
says nothing about generalisation and cannot buy gating. Three `held-*` cases use
instances the prompt never mentions, and only those decide `gating:`. Two clean
controls bound the false-positive rate.

That split was not planned. It was found by running the reviewer against
**ADT-126 itself** — the ticket that builds it — which returned FLAWED and named
the contamination. The counter-check catching its own author is the strongest
evidence available that it is not a rubber stamp.


### 3c. Recording what the gates DID (ADT-224)

Both gates above now leave a machine-readable trace, because otherwise nothing
answers the question the gates exist for: *did this gate change anything?*
`post_merge_defects` is a floor, and `follow_ons` reads 0 on all 28 tickets that
stamp it — a value that is always the same number is a value nobody is
measuring. ADT-205's experiment found four real gate catches and found them only
by reading agent prose.

At verdict time the playbook writes the block, then runs one call per gate:

```
python3 tools/adt_dod.py <ticket.md> --record-verdict coverage
python3 tools/adt_dod.py <ticket.md> --record-verdict plan-quality
```

That appends a `kind: record` row to `gate_effects:` frontmatter carrying the
round, the verdict, and the **graded-text hash** — a digest of every `###`
section the reviewer read plus `done_evidence`, whitespace-normalised so a
reflow is not an edit. `adt_dod` then derives, per gate, `ran`, `caused_edit`
(a NEGATIVE verdict at round N whose hash differs from round N+1),
`under_recorded`, and the `gating_enabled` state that was in force — recorded
rather than re-evaluated at report time, since `plan_gating_enabled()` is global
and per-run and would otherwise mis-describe every historical ticket the moment
it flips. `adt_metrics.py` reports the totals.

Three properties are load-bearing:

- **The agent never types the verdict and never computes the hash.**
  `--record-verdict` takes only the gate name and parses the verdict out of the
  block it is canonicalising; an agent-typed `SOUND` over a pasted `FLAWED`
  would silently flatter `caused_edit`, which fires only on a negative verdict.
- **The gates run serially** — coverage to `COVERED`, then plan-quality — so
  each grades text only it could have moved and its hash pair attributes.
- **`under_recorded` records, it does not refuse.** A dispatch that legitimately
  produces no block (an interrupted reviewer, an `UNKNOWN`) would otherwise make
  the ticket permanently un-releasable three lanes later, with no reconciliation
  path. Under-crediting is the conservative direction, and a rebuilt cache
  produces exactly this state because frontmatter is never pushed to the Issue.

### 4. Caveats must attach

A caveat in Design / Risks / Test plan ("assumes X", "verified manually") used to
have no edge to anything, so it was stated and then dropped. Now it must carry
either `-> DoD:<id>` (the condition that resolves it) or `-> WAIVED: <reason>`
(a recorded carve-out), in the same block. The refusal **names** the offender.

The failure being caught is the **silent** omission. A carve-out that names
itself and gives its reason has been handled honestly — which is why the
calibration includes a clean case whose obligation is discharged by a waiver.

Detection is deliberately conservative: the markers are phrases ADT actually got
burned by, not every hedge. An over-eager gate gets worked around, and a
worked-around gate is worse than none.

### 5. Ordering

`done_evidence` entries take optional `id:` and `depends_on:`. Where B depends on
A, B is reported **can't-verify** while A is red — not failed. The claim is not
that B is broken, only that it cannot honestly be called green ahead of what it
rests on. Absent keys mean no edges, i.e. exactly the previous behaviour.

A cycle or a dangling target is an **authoring defect**, refused at `--gate`
rather than discovered mid-loop.

---

## The build-side third path

A plan is a prediction; the build is where it meets the repo. `commands/build.md`
used to offer exactly two responses to anything unplanned: a prohibition that
said what *not* to do, and `/adt-block`. With only those, every surprise resolved
as drift or an interrupt — so sessions invented an unsanctioned third exit and
filed a follow-on ticket.

The **deviation loop** is the sanctioned third path. Three gates, in order:

1. **Is it really an issue?** Reproduce it, or the loop exits.
2. **Is this the simplest way to solve it?** State the one-line fix first;
   anything larger must name what breaks the one-liner.
3. **Does this actually change the plan?** The outcomes are **not peers** — the
   default is *fix it now, in this diff*.

`/adt-block` is the loop's **overflow**, not its first move.

### Amending the DoD mid-build

Tightening (add a condition, add a test, widen the fix) is **autonomous** — it is
always safe unattended. Loosening (waive, reduce scope, defer, declare out of
scope) is **never self-approved**. That asymmetry is what makes running the loop
unattended safe at all.

---

## What is measured, and what deliberately is not

`tools/adt_metrics.py`. The honesty is the design:

- **follow-on rate** — measurable, from `follow_ons:` stamped at close.
- **post-merge defects** — measurable, and the **mandatory counter-metric**.
  Paired with the follow-on rate so neither can be improved by shifting work into
  the other: drive follow-ons to zero by shipping half-finished work and the
  remainder resurfaces here. A loop optimised on one number alone is a Goodhart
  machine.
- **interrupt rate** — **not measurable**, and the tool says so rather than
  substituting something adjacent. Measured across every closed ticket: zero
  `BLOCKED` markers. `/adt-block` leaves an artifact; a conversational "what
  should I do?" does not, and that is how interrupts actually happen. A blocked
  spawn is a *different quantity* — reporting it as the interrupt rate would be
  swapping the number while keeping the name.

Baseline: `docs/adt-metrics-baseline.md`.

---

## Installing changes to any of this

**Editing `defaults/hooks/` does not change the hook that runs.**
`lib/install-defaults.sh` installs hooks as **copies** into a project's
`.claude/hooks/`, and it is that copy the harness executes. A merged change to
`defaults/hooks/` is inert until it is installed — which is why the done lane
asserts the installed copies carry the change, not just that source does.

What installing does *not* require: a session restart. Replacing the hook file
takes effect on the **next invocation** of that hook — measured and corrected
upstream on 2026-08-21 (`tools/adt_cost.py`), where the previous claim that
"hooks load at session start" was tested and found false. So the sequence is
merge → reinstall → the next Stop/PreToolUse runs the new code.

# ai-orchestrator-adt — decision log

Curated ADRs. Records the *why* behind non-trivial choices.
Numbering is global, not per-feature.


---

ADR-004 to ADR-019 are in the private archive, zurichrich/adt-private.
They were taken before ADT was published and name a consumer project, so
the public log starts at ADR-020.

## ADR-020 — Token-attribution hook is project-prefix-aware, keyed on CLAUDE_CODE_SESSION_ID

*Renumbered from ADR-001.*

**Date:** 2026-06-26
**Decided by:** lead-developer
**Triggered by:** kanban-last-edited-and-token-attribution (ADT-54)
**Context:** Token attribution had been billing to `__unassigned__` for weeks on
ADT-on-ADT. Build-time verification of ADT-54 found three concrete mechanisms,
all reproduced live: (a) `usage-log.sh` hardcodes `ID_PREFIX` to `TIX` and
`ADT_ID_PREFIX` is never exported, so an explicit `ADT-54` in the prompt never
matches the `TIX-[0-9]+` regex (line 83) → marker never written; (b) the session
id available to a skill-invoked Bash shell is `CLAUDE_CODE_SESSION_ID`, **not**
`CLAUDE_SESSION_ID` (the plan's assumption was wrong — verified against the
ledger's session column and the cursor filename); (c) conversational pickup never
binds because no `/adt-*`+id is typed.
**Decision:** Make the hook prefix project-aware — `usage-log.sh` resolves
`id_prefix` from the project config instead of defaulting to `TIX`. Ship
`adt-mark-tix.sh` (bind-on-pickup) keyed on `CLAUDE_CODE_SESSION_ID` with a
transcript-path fallback. The prefix fix is the dominant root cause and is folded
into ADT-54 (same cause, not new work).
**Rationale:** A hardcoded `TIX` default silently breaks every non-TIX project —
behavioural ("export ADT_ID_PREFIX") guidance loses because nothing enforces the
export. Reading the prefix from the config that already exists makes it
structural. Keying on the verified env var avoids a silent no-op — the exact
failure class that hid this for weeks.
**Implications:** the hook gains a config read (fail-open to `TIX` if no config).
`adt-mark-tix.sh` is new and ships via `defaults/hooks/`. QA must verify on a
non-TIX project specifically, since a TIX project would mask the prefix bug.
**Reversibility:** High — both changes are localized to the hook layer and
fail open; reverting restores the prior (broken) behaviour without data loss.

**Since:** the hook is `defaults/hooks/adt-usage-log.sh`, and the prefix now
resolves in three steps — `$ADT_ID_PREFIX`, then `id_prefix:` in the in-repo
`.adt/config.yaml`, then `TIX` as the fail-open default. The decision is
unchanged; the config it reads moved in-project with ADR-018.

## ADR-021 — done-evidence is a declared, executed assertion (not inferred)

*Renumbered from ADR-002.*

**Date:** 2026-06-29
**Decided by:** PO + lead-developer
**Triggered by:** done-claim-gate-and-deploy-freshness (ADT-061), confirmed alongside /adt-build-todone (ADT-81)
**Context:** ADT-061's artifact gate must "assert the specific element exists in
the rendered file the human opens." A PreToolUse hook cannot *infer* which element
a given ticket claims — nothing in the ticket text says "this ticket added
`<a href="references.html">`." Two designs: (A) the ticket *declares* its evidence
in frontmatter and the gate *executes* the declaration; (B) the gate *infers* the
element to check from the ticket's diff/plan.
**Decision:** (A) declared. A ticket that touched a generated page carries a
`done_evidence:` list — `{file, must_contain_regex}` entries — and the done guard
asserts each regex against the named file in the rendered copy the human opens,
never the cache or the source .md, on the git-mv-into-done. No `done_evidence` →
nothing to assert (the unconditional 404 gate still runs).
**Rationale:** Inference is fragile and fails silently — a gate that guesses the
wrong element gives false confidence, the exact ADT-58 failure class this ticket
exists to kill. Declaration keeps the generic hook dumb (it runs a stated
assertion) and puts the specific knowledge with the ticket author, who knows what
they changed. The same `done_evidence:` block doubles as the machine-checkable
Definition of Done that /adt-build-todone (ADT-81) grades its autonomous loop
against — one artifact, two consumers.
**Implications:** Ticket authors writing a generated-page change must add a
`done_evidence:` block, or the artifact gate has nothing to assert (the 404 +
freshness gates still apply). The regex must target the real element
(`<a … href=…>`), not a substring — that is what defeats the tooltip-match miss.
**Reversibility:** High — `done_evidence` is optional and the reader fails open on
absence/parse error; removing the block reverts a ticket to 404+freshness-only.

**Since:** the hook is `defaults/hooks/adt-done-guard.sh`, and the rendered copy
is `.adt/kanban.html` — ADR-018 moved it out of `development-team/`, which no
longer exists.

## ADR-022 — done_evidence is the machine-checkable DoD, graded by one shared module

*Renumbered from ADR-003.*

**Date:** 2026-06-30
**Decided by:** PO + lead-developer
**Triggered by:** /adt-build-todone (ADT-81)
**Context:** ADR-021 made `done_evidence:` a declared, executed assertion checked
by the done guard on the done-move. ADT-81's autonomous loop needs to grade the
same DoD *each pass, mid-lifecycle* — and needs more than a rendered-page regex:
a command that exits 0, and a real red→green test.
**Decision:** `done_evidence:` is the single Definition-of-Done artifact, extended
with `must_run` (exit 0), `was_red_at` (verify red→green by re-running at the ref
in a throwaway worktree — not an always-green), and per-entry `lane:`. ONE grader,
`tools/adt_dod.py`, is the only thing that decides a condition met; both the done
guard (done-lane slice) and `/adt-build-todone` (all lanes) call it, so the
done-gate and the loop can never drift. No agent narration is ever an input.
**Rationale:** An autonomous loop that grades itself against prose-it-reads is
unsafe. Keeping every condition objective (process exit / file regex) and the
grading in one shared module is what makes suspending the per-gate stop
(working-style #8 carve-out) safe. A prose condition makes the loop refuse to start.
**Implications:** Plan authors writing a `/adt-build-todone` ticket must express the
DoD as `must_run`/`must_contain_regex` entries with lanes; `plan_approved: true` is
the human's one decision that licenses the autonomous run. The done guard's
behaviour is unchanged (it grades only the done-lane slice via the same module).
**Reversibility:** High — the loop is opt-in; the done guard falls back / fails open
if the grader is absent; manual /adt-build keeps gate-per-turn regardless.

## ADR-023 — Authorisation is read only from `role: user` transcript turns

*Renumbered from ADR-010.*

**Date:** 2026-08-21 · **Ticket:** ADT-114 · **Status:** accepted

**Context.** Enforced rule 2 needs "a human approved this" to be machine-checkable.
The first design used the session's ticket binding (`adt-mark-tix.sh`'s per-session
marker) to tell a legitimate `/adt-brief` filing from a mid-build spawn.

**Decision.** Read authorisation *only* from `role: user` transcript records —
either a `/adt-brief` invocation or a typed `ADT-APPROVE-FOLLOWON`.

**Why.** An agent cannot write a `role: user` turn. The binding marker is a plain
file the agent writes and can delete, so a gate keyed on it is bypassable by `rm`
— the chain would have been only as strong as its weakest link. Review caught it
before it shipped.

**Consequence.** A human filing a ticket from a bound session must type the token.
Accepted: a false positive here is the failure being prevented, so the check fails
toward denying.

**Since:** the source is still `role: user` turns only, but the set of accepted
authorisations is wider than this entry describes. `adt-deferral-guard.sh` now
also accepts `approve follow-on` and a bare `approve` as the whole message in the
turn in flight, alongside the token and a live `/adt-brief`.

## ADR-024 — The coverage gate is on the record, not on the judge being right

*Renumbered from ADR-011.*

**Date:** 2026-08-21 · **Ticket:** ADT-114 · **Status:** accepted

**Context.** `--gate` refuses a plan whose DoD has not been counter-checked for
coverage by the `dod-coverage-reviewer` subagent. That reviewer is a model.

**Decision.** Split the two jobs. `--gate` enforces that a review *happened* and
did not come back UNKNOWN (`tools/tests/test_dod_coverage_gate.py` tests that
logic hermetically). The judge's *quality* is a calibration record a human reads
(`docs/dod-coverage-calibration.md`).

**Why.** An LLM judge cannot be graded hermetically — pytest has no model.
Conflating the two is how a rubber stamp gets mistaken for a gate: a reviewer that
always answers COVERED would make `--gate` report the coverage question settled
when nothing checked it, which is the very failure mechanism 1 exists to close.

**Consequence.** The gate is only as good as the calibration behind it, and that
number is a human decision point. First run: caught 6/6, 0 false positives.

## ADR-025 — The graph framing was rejected on evidence

*Renumbered from ADR-012.*

**Date:** 2026-08-21 · **Ticket:** ADT-114 · **Status:** accepted

**Context.** ADT-114 was filed as "Graph engineering for ADT". Anthropic's own
guidance does not use the term and argues against adding topology until simpler
things fall short.

**Decision.** No graph engine, no `/adt-graph` command, no multi-session topology.
Ordering ships as two optional `done_evidence` keys (`id`, `depends_on`) plus a
sort in a 314-line module. Everything else is an instance of two patterns ADT
already had: a `PreToolUse` deny and a `Stop`-hook claim/evidence pairing.

**Why.** The bar was "what specifically breaks if we do not build a graph?" —
nothing did. The counter-check turned out to be a standard evaluator-optimizer
pattern to instantiate, not a topology to design.

**Consequence.** The ticket was retitled to name the change rather than a
candidate solution.

## ADR-026 — Ordering makes a downstream condition can't-verify, never failed

*Renumbered from the first of the two ADR-013 entries.*

**Date:** 2026-08-21 · **Ticket:** ADT-114 · **Status:** accepted

**Decision.** Where condition B `depends_on` A, and A is not met, B is reported
`passed=None` ("upstream not met"), not `False`.

**Why.** We are not asserting B is broken — only refusing to claim it is green
ahead of what it rests on. Can't-verify already blocks `all_green` and shows in
the scorecard, so the done guard and the build-todone loop needed no change.

**Consequence.** Absent keys mean no edges, so every existing DoD grades exactly
as before.

## ADR-027 — The interrupt rate is not measured, rather than approximated

*Renumbered from ADR-014.*

**Date:** 2026-08-21 · **Ticket:** ADT-114 · **Status:** accepted

**Context.** The brief made "a measured drop in interrupts" a success criterion.
Measured at plan time: all closed tickets contain **zero** `BLOCKED` markers.

**Decision.** Report it as not measurable. Do not substitute deferral-guard denies.

**Why.** `/adt-block` leaves an artifact; a conversational "what should I do?"
does not, and that is how interrupts actually happen — which confirms mechanism
4's thesis and destroys its metric at once. A blocked spawn is a different
quantity; keeping the metric's name while swapping the number is the substitution
working-style #7 forbids.

**Consequence.** The gradable pair is follow-on rate and post-merge defects.
`/adt-close` stamps the first as `follow_ons: N`.

## ADR-028 — The plan-quality gate is conditional on a held-out calibration

*Renumbered from the second of the two ADR-013 entries.*

**Date:** 2026-08-22 · **Ticket:** ADT-126 · **Status:** accepted

**Context.** `dod-coverage-reviewer` (ADT-114) checks that a DoD covers its spec.
Nothing checked that the spec was worth building, so a wrong Design with a
faithful DoD passed every gate and graded green. ADT-126 adds
`plan-quality-reviewer` to ask that question.

**The problem with gating on it.** Coverage is a set-comparison with ground truth
in the artifact. "Is this the simplest design that solves the Problem" has none —
the judge holds an opinion. An always-SOUND reviewer is therefore a live risk,
and an uncalibrated judge that can REFUSE is worse than no judge at all: the gate
reports the design question settled when nothing checked it. That is ADT-114's
own failure rebuilt one level up.

**Decision.** `adt_dod.py --gate` refuses on a plan-quality verdict **only if**
`docs/plan-quality-calibration.md` records `gating: ENABLED`, written by
`tools/calibration/plan-quality/score.py` only when the reviewer clears a bar
declared in the README before any verdict existed. Below the bar the reviewer
still runs and its verdict is still recorded and reported — only the automatic
refusal is withheld. The gate degrades to RECORDING, never to rubber-stamping,
and fails toward not-gating when the record is missing or unreadable.

**And the bar is measured on a held-out subset.** Six of the flawed calibration
cases are drawn from the same *instances* the reviewer's prompt cites as worked
examples; their score is a recall floor that says nothing about generalisation
and cannot buy gating. Three `held-*` cases use instances the prompt never
mentions, and only those decide `gating:`.

**Why this split exists.** It was not planned. Running the reviewer against
ADT-126 itself returned FLAWED and named the contamination as "a check that
cannot fail". The counter-check caught its own author, which is the evidence the
mechanism is not a rubber stamp.

**Consequences.** A second conditional gate is more machinery than an
unconditional one, and the conditionality is a code path that must be tested in
both directions (`tools/tests/test_plan_quality_gate.py`). Tickets planned before
this landed carry no `### Plan-quality review` and are refused until reviewed —
ADT-112 is the one in flight. No grandfather list was added: exempting a ticket
is a loosening action and is not self-approvable.

**Since:** the calibration cleared its bar — `docs/plan-quality-calibration.md`
records `gating: ENABLED` on held-out 3/3. The gate refuses.

## ADR-029 — The deferral guard reads the command's words, covers `gh api`, and accepts approval on its own line

**Date:** 2026-09-11 · **Ticket:** ADT-354 · **Status:** accepted

**Context.** The deferral guard enforces rule 2 (no casual deferral) by denying an
unauthorised Bash command that creates an Issue. It searched the command's text,
so it failed both ways on one reinstall. It denied a read-only `grep` whose
pattern named the create command. It never looked at `gh api`, which creates an
Issue just as well and is the form CLAUDE.md rule 5 steers agents towards. The
human's approval also failed twice: once as a request with a typo ("should be
files in adt"), and once as a numbered answer ("1. approved 2. ok 3. merge").

**Decision.**
- The guard tokenises the command with `shlex`, splits it into the commands a
  shell would run (at operators, newlines, `$( )` and backticks), and checks
  every `gh` word in each one, not only the first word. That catches a create
  behind any wrapper (`sudo -u root`, `timeout 30`, `find -exec`) without a list
  of wrapper names, and it skips gh's own `-R <repo>` flag. Strings passed to
  `bash -c` or `eval` are tokenised again. A command `shlex` cannot parse falls
  back to the old text match.
- It also denies `gh api` POSTs to `repos/<owner>/<repo>/issues` (an explicit
  POST, or fields with no method, since `gh api` then POSTs) and
  `gh api graphql` calls containing `createIssue`. The PO approved this
  tightening on 2026-09-11.
- `approve` or `approved` counts when it is a whole line of the latest human
  message, optionally after a list marker. It does not count inside a one-line
  list such as `1. approved 2. ok`.
- The intent regex, which reads "file a ticket for X" as a request, is not
  widened.

**Why.**
- Reading the words fixes both failures at once. A quoted pattern becomes one
  token and is not a command, and a `gh api` call is recognised by its endpoint
  and method rather than by the phrase.
- A list of wrapper names was built first and rejected in review. Its check
  doubled in cost with every wrapper-named word, so one line of prose in a
  heredoc could stall every Bash call for seconds, and a wrapper nobody listed
  (`timeout`, `nice`) let a create through that the old text match had caught.
- Splitting on `;` and `|` before tokenising was rejected because Issue bodies
  contain those characters inside quotes.
- A one-line numbered reply is rejected because the guard cannot tell which
  item an `approved` answered. It could authorise a filing that was item 2 when
  the human meant item 1.
- Widening the intent regex to accept "files" and "bug" was rejected because
  ordinary instructions would then authorise filings: "write a regression test
  for that bug", "review these files for issues" and "open the file and fix the
  bug" all match. The human's typo was answered by the numbered-approval fix
  instead, which lets their next reply through.

**Consequence.** A command inside a script file (`bash file.sh`) is still not
seen, as before. `lib/uninstall.sh` creates its telemetry archive with `gh api`,
and that is not affected: the guard sees only the agent's Bash command, and
running the uninstall script sends `bash lib/uninstall.sh`, not the `gh api`
line inside it.

**Reversibility.** High. The matcher lives in one function in
`defaults/hooks/adt-deferral-guard.sh`, and every case is in
`defaults/hooks/tests/test_deferral_guard_matcher.sh`.

## ADR-030 — The grader runs `must_run` conditions in the tree it is invoked from

**Date:** 2026-09-12 · **Ticket:** ADT-359 · **Status:** accepted

**Context.** `tools/adt_dod.py` ran `must_run` conditions in the parent directory
of `--devteam`. `.adt/` is gitignored and exists only in the canonical checkout,
while `multi-agent-git-workflow.md` §A.3 requires code work to happen in a linked
worktree. So a build graded from its worktree passed the canonical `.adt/`, and
every condition ran against a tree that did not have the branch's files. In
TIX-886 that produced a build-lane DENY, worked around with a symlink.

**Decision.**
- When the caller names no directory, `check()` and `dry_run()` both use
  `_default_cwd()`: the git toplevel of the directory the grader is invoked from.
  Outside any git repo it falls back to the parent of `--devteam`, the old answer.
- `must_contain_regex` targets still resolve under `--devteam`, because the
  rendered board lives in the canonical checkout.
- `adt-done-guard.sh` passes `cwd=root` (the canonical checkout) explicitly. The
  done lane runs after the merge, so that tree holds the work.
- There is no `--cwd` flag.

**Why.**
- The playbooks run `adt-dod.sh` from the tree being built, so the invoking
  directory is already the right tree. A flag would be one more argument an
  agent has to remember, and forgetting it reproduces the bug.
- The one caller that needs a different tree, the done-guard, is Python and can
  pass `cwd` itself.

**Consequence.** A DoD condition written against the old default can grade
differently: a path-relative command now resolves against the tree the grader
was run from. `--dry-run` prints the directory it used. The change does not make
the grader harder to steer: an agent that runs it from the wrong tree still gets
the wrong answer, as before with the wrong `--devteam`.

## ADR-031 — The telemetry dashboard shows the build problems QA found instead of the Definition of Done panel

**Date:** 2026-09-14 · **Ticket:** ADT-371 · **Status:** accepted

**Context.** The dashboard's Definition of Done panel showed "proven red then
green": the number of DoD conditions that carry a `was_red_at:` field. Telemetry
counts whether the field is present and never checks that the failure was
confirmed. A condition that was red and then green shows the check can fail. It
does not show ADT catching something the build got wrong. The owner asked for
that figure instead: how often a DoD review caught a problem in what the build
delivered.

**Decision.**
- The Definition of Done panel is removed. A "build problems QA found" panel
  takes its place, with two rows: QA checks that sent the build back, and
  tickets sent back at least once.
- A QA result is a `**Result:**` line under a ticket's `## QA report` heading,
  and a FAIL is QA sending the build back. Each install counts these from its
  own cache and sends four integers (`qa_checks`, `qa_fails`, `qa_tickets`,
  `qa_tickets_failed`) as report schema 5.
- The collector accepts schemas 1 to 5. `dod_conditions` and `dod_pinned` are
  still sent and still stored; the page just stops showing them.
- "Changed the plan" and "bugs after" stay in the gates and tracks panels
  rather than being repeated in the new panel.

**Why.**
- `/adt-qa-run` is the review that grades the finished build against its DoD,
  and the ticket's QA report is the only place its outcome is recorded. The sync
  keeps no history of `qa/ → building/` moves, so the file is what can be
  counted.
- The collector refuses any key it does not know, so new fields need a new
  schema version. Adding them to schema 4 would have made updated installs'
  reports fail until the collector was redeployed.
- Dropping the two DoD fields from the report would need its own schema change
  on both ends and gains nothing for the page.

**Consequence.** Deploy the collector before any machine running `adt watch`
pulls this change. An updated install talking to an old collector loses that
day's report without an error. A QA run whose result was not written into the
ticket is not counted, so the panel can undercount.

## ADR-032 — A ticket cannot move into planned/ without a track

**Date:** 2026-09-14 · **Ticket:** ADT-371 · **Status:** accepted

**Context.** Only `/adt-plan` and `/adt-plan-fasttrack` write `track:`, and
nothing checked that it was set. No hook, sync step or `/adt-close` step failed
without it, so tickets closed as `unset`. On 2026-09-14 the telemetry dashboard
showed 332 of 441 closed tickets as `unset`, which leaves the tracks panel unable
to compare tiers.

**Decision.** `adt-done-guard.sh` denies a `mv` whose destination is a
`planned/` folder unless the moved ticket's frontmatter sets `track:` to `fast`,
`standard` or `full`. An allowed move prints nothing. A file the guard cannot
read, or one with no frontmatter, is allowed, like the guard's other checks.
`commands/plan.md` step 5 says the move is denied without a track.

**Why.**
- `/adt-plan` step 4 sets the track and step 5 moves the file, so the move is
  the last point before planning ends. Checking there stops a plan that skipped
  the track without adding a step to the playbook.
- The done guard already parses every ticket `mv` and already has an installed
  copy, a catalogue row and tests. A new hook would need its own registration in
  `defaults/settings.hooks.json`, an installed copy, a catalogue row and a test
  file.

**Consequence.** A ticket that skips `/adt-plan` entirely, such as a small change
built straight from `ideas/`, never moves through `planned/` and can still close
with no track. A ticket in `blocked/` with no track is denied when
`/adt-unblock` moves it back to `planned/`; the deny message says to set the
track and move it again.

## ADR-033 — The telemetry dashboard ranks installs by rate, sorted and paged by the Worker

**Date:** 2026-09-14 · **Ticket:** ADT-378 · **Status:** accepted

**Context.** The dashboard showed only figures added up across every install, and a list of installs with raw figures. The owner could not see whether ADT helps some installs more than others, and a raw list of hundreds of installs cannot be ranked. Raw counts also put the biggest installs at the top of every column whatever ADT did for them. The page carries no JavaScript by design.

**Decision.**
- The page opens with four figures in the order a ticket meets them (checks run, caught at plan, caught at QA, escaped), then the totals, then a compare installs table.
- After its usage columns, every compare figure is a rate: a percentage of plans reviewed or QA checks, or a number per closed ticket or per 100 closed tickets. A rate whose denominator is below a chosen minimum (10, 25 or 50) is hidden and ranks last.
- The Worker reads the view from the page address (`install`, `sort`, `dir`, `page`, `q`, `dormant`, `min`), checks each value, and sorts, filters and pages the table before rendering 25 rows. Column headings and page numbers are links; the search box, the dormant option and the minimum are a GET form.
- Selecting an install narrows every query with `AND index1 = '<id>'`. Only an id matching the UUID pattern is ever written into SQL. The top sections then show that install with the all-installs figure beside each one.
- The collector stores the schema each daily report was sent with, as double 19, so the page can show "not reported" instead of 0 for a figure an older schema did not send.
- The installs, cost and where-the-tokens-go panels are removed. Cost moves into tickets and tokens.

**Why.**
- Rates make a 20-ticket install and a 300-ticket one comparable; counts rank by size.
- Sorting 500 rows in the Worker costs nothing extra, because it already holds every install's figures to compute the rates, and it keeps the page free of script.
- One narrowing clause gives every panel its per-install form, so the totals and the one-install view cannot be computed differently.
- Every install reports the same ADT version whatever schema it sends, so the version cannot say which figures an install carries.

**Consequence.** A page for all installs runs 16 queries and a one-install page runs 21, each plus one KV list, under the Workers limit of 50 subrequests. Each new per-install query returns at most one row per install, because Analytics Engine documents no row limit; `collector/check-queries.mjs` confirms them against the live dataset after deploy. Until each install sends its first daily report after the collector is deployed, its row has no stored schema and shows "not reported" for QA, autonomy and reviewer figures.

## ADR-034 — A second machine joins the committed ADT install, and a newer ADT upgrades it on a branch

**Date:** 2026-09-16 · **Ticket:** ADT-384 · **Status:** accepted

**Context.** Several machines working on one repo is a core use case, but every machine took the first-install path. A second machine was asked again for name, repo, prefix and board, and could create a second board. The `.claude/` layer is committed, so it belongs to every machine that pulls the project, yet every install path (`adt-install.sh`, a bare `setup.sh`, `/adt-close` step 9) copied the local ADT clone into the working tree. A newer clone upgraded the team as a side effect, and an older one downgraded it. Each machine's sync agent also runs its own clone against the same Issues, and no machine could see another's version.

**Decision.**
- The installer reads `.claude/.adt-manifest.json` from `origin/<main>` before its first prompt. If ADT is installed there, the machine joins: it takes name, repo, main branch, prefix and board title from the committed `.claude/adt-project.yaml`, and sets up its own config, cache and watcher. The GitHub bootstrap still runs: labels are refreshed idempotently and the board is found by the committed title, never created. It never writes the shared layer into its working tree.
- `lib/adt-layer.sh` compares the committed `source_commit` with the clone. An older clone, or one that lacks the committed commit, is refused with the pull command. A newer or diverged clone writes the layer to a local `chore/adt-upgrade-<sha>` branch built in a temporary worktree, and no branch is made when only the manifest would change. A first install, and ADT's own repo, still write in place. `setup.sh` and `/adt-close` step 9 use the same rule.
- Uninstall follows the same rule. On a committed layer, `adt-install.sh --uninstall` removes only this machine (watcher, telemetry archive, `.adt/`, per-user config) and leaves the tracked files alone. `--uninstall --everyone` also writes the removal of the layer and `.claude/adt-project.yaml` to a local `chore/adt-uninstall` branch through the same branch writer as the upgrade. With no committed layer, uninstall removes everything from the working tree as before.
- Once a UTC day each machine's watch tick updates its own comment on a closed Issue labelled `adt:install` with its ADT version, clone commit and OS. The label is on the create call. The sync skips the Issue like `adt:archive`. Readers keep only owner, member and collaborator comments with a 40-hex commit from the last 7 days. The board footer lists them, and the installer names the machines an upgrade leaves behind.

**Why.**
- `.claude/adt-project.yaml` and not `.adt/project.yaml`: install writes `.adt/` into every consumer's `.gitignore`, and uninstall deletes the folder, so one machine's uninstall, once committed, would delete the team's file.
- Detection keys on the committed manifest, not on labels or the board: without a committed layer there is nothing to join, and the committed board title already stops a second board.
- A branch rather than a working-tree write turns an upgrade into a reviewed PR, and it cannot strand worktrees with an uncommitted layer (one consumer Issue, #883). Removal is the same kind of change in the other direction, so it takes the same route: before this, one machine's uninstall deleted the committed layer from its working tree, which removed ADT for everyone once committed.
- An uninstall does not delete the machine's `adt:install` comment: the 7-day window drops it, and a delete would add a GitHub call to a path that must work offline.
- The report carries no telemetry install id and no hostname: next to the comment's author either would tie the anonymous telemetry id or the machine's name to a GitHub login. The comment id identifies the machine.
- Only collaborators' comments count because anyone can comment on a public repo, and a reported commit reaches `git merge-base` and the board's HTML.
- A machine silent for 7 days is not listed: the report runs in the watch tick, so a machine that stopped reporting stopped syncing too.
- Reports are written whether telemetry is on or off, because they go to the user's own repo, not to ADT.
- The Issue is not pinned: pinning has no REST API, and GraphQL is kept for what has none (CLAUDE.md rule 5). Readers find it by label, and all of them use the lowest-numbered one if two machines create it at once.

**Consequence.** An upgrade reaches the team only when someone merges the branch, and other machines' sync agents stay on their own clones until they pull ADT; the installer lists those machines. An install committed before ADT-384 has no `.claude/adt-project.yaml`, so its next join asks as before and writes the file for the team to commit. Offline, the comparison uses the refs already fetched: a stale `equal` writes nothing, and a stale `newer` can only produce a branch. Reversibility: Medium — the join and branch rules live in two scripts and one playbook step, but teams will have committed `.claude/adt-project.yaml` and an `adt:install` Issue that a reversal would leave behind.

## ADR-035 — Telemetry derives updates from the daily report and receives uninstall as an event

**Date:** 2026-09-16 · **Ticket:** ADT-391 · **Status:** accepted

**Context.** The telemetry dashboard could not say which installs updated to a release, or which were uninstalled. The versions panel counted daily reports, not installs. Uninstall sent nothing and deleted `.adt/state/install-id`, so an uninstalled install looked the same as one that turned telemetry off or whose watcher stopped. The collector kept only `first_seen` per install in KV.

**Decision.**
- Nothing new is sent on update. Every daily report already carries the install id and the version, so the collector's `lifecycle` query reads the first and last report per (install, version) from Analytics Engine. An install counts as updated to X when it reported a lower version first, both inside the 30-day window.
- Uninstall sends one event, `{event, install_id, version, os}`, from `lib/uninstall.sh` after the watcher stops and before `.adt/` is deleted. It is sent only when telemetry is on and an install id already exists, and it never creates one.
- The event is its own body shape, not a report schema. `handle()` sends a body with an `event` key to `validateEvent()` instead of `validate()`, and `event` is a closed set with one member, `uninstall`.
- The collector writes the event as one Analytics Engine row and never writes KV for it.
- The dashboard gives each install that reported in the window one status: uninstalled (an uninstall at or after its last report), gone quiet (no report for 7 days) or reporting. An uninstall from an id with no report in the window is ignored. Both panels are on the all-installs page only.

**Why.**
- An update event would need a "last version" stamp on the client and a new send path, and it would still depend on the watcher running after the update, which is the same condition the daily report depends on. It would add nothing the report does not already give.
- Uninstall cannot be derived: after it, nothing arrives, whether the install was removed or went quiet. One event is the least that tells them apart.
- A `last_seen` per install in KV would be a put per install per day, the cost ADT-224 removed, and it still could not tell uninstalled from quiet.
- A schema 6 report with an `event` field would have to carry every report field, because the collector requires all allowed keys to be present, and it would write about 21 rows to say one thing.
- The event skips KV because an uninstall from a novel id comes from an install that never reported, and registering it would add an install on its way out.
- Ignoring an uninstall from an id with no reports means random uuids posted to the public endpoint cannot inflate the count. Faking one for a real install needs that install's id, which only ever goes to the collector.
- The install id is not kept across uninstall so a reinstall can be recognised. Uninstall removes `.adt/` completely, and keeping an identifier after the user asked for removal is the wrong trade.

**Consequence.** A reinstall counts as a new install. An update is counted only when both versions reported inside the window. The collector must be deployed before a client release that sends the event, or those uninstalls are lost without an error (`collector/DEPLOY.md` step 2). The all-installs page runs 17 queries and the one-install page still runs 21. Reversibility: Easy — the event is one client function, one call in uninstall and one branch in the collector; removing them leaves only Analytics Engine rows that age out.

## ADR-036 — No CI on ai-orchestrator-adt

**Date:** 2026-09-16
**Decided by:** user (zurichrich)
**Triggered by:** publish-adt-public
**Context:** ADR-011 settled that this repo runs no CI and said to revisit that
when it goes public. Publication is that moment, and ADR-011 itself moves to the
private archive, where the five shipped files that cite it cannot reach it.
**Decision:** The public repo runs no CI. The gate stays a local suite run by the
operator before a merge. Five citations of ADR-011 (`CLAUDE.md`, `CLA.md`,
`LICENSING.md`, `defaults/rules/multi-agent-git-workflow.md` and its installed
copy) now cite this ADR, and `tests/test_ci_claims_match_reality.sh` asserts the
new sentence.
**Rationale:** ADR-011's two reasons still hold. One committer runs the suite
locally, so a server-side repeat buys nothing, and the previous workflows spent
~2,500 of 3,000 monthly Actions minutes in seven days by running the shell half
on a macOS runner. Publication changes who can read the repo, not who commits to
it. The fork case is already answered: `CLA.md` and ADR-013 state that nothing
automated checks an outside contributor's fork PR.
**Implications:** No workflow file ships, so no required status check can be
registered and `tests/test_no_orphan_required_checks.sh` stays green. A merge
queue remains impossible, so the git-workflow rule's §A.8-10 (a stale green is
spent) is the whole mitigation. Revisit when a second committer appears, and add
a Linux runner for the pytest half only.
**Reversibility:** High — adding a workflow and registering its context is a
small diff, in that order.

## ADR-037 — ai-orchestrator-adt is a new ADT project with the AO prefix

**Date:** 2026-09-16
**Decided by:** user (zurichrich)
**Triggered by:** publish-adt-public
**Context:** The public repo is a fresh repo, so its Issue numbers restart at 1.
The sync matches a cache file to an Issue by number alone, and the cost ledger is
keyed by ticket id, so pointing the existing backlog at the new repo would merge
unrelated tickets and attribute old costs to new ones. Shipped files carry about
2,176 `ADT-NNN` references.
**Decision:** `ai-orchestrator-adt` is installed as a separate ADT project with
ticket prefix `AO`: its own per-user yaml, cache, board and `.adt/state`. The
`agent-dev-team` backlog stays as the record. `ADT-NNN` in a shipped file always
means the private tracker. Retros keep being written to `docs/retros/`, which is
public from the flip.
**Rationale:** A separate install cannot mix the two backlogs, because nothing
reads one against the other, and it starts an empty ledger. A different prefix
keeps every existing reference unambiguous once new tickets start at 1. This
replaces ADR-016's premise that internal ids resolve to readable Issues the
moment the repo is public: with a fresh repo they never resolve publicly, and
they are kept for the same reason ADR-016 kept them — they explain why the code
is the way it is.
**Implications:** The public log starts at ADR-020 and says where 004-019 are.
Old retros stay private; new ones are public, and no shipped gate scans them for
a consumer name once the audit script leaves. `/adt-close` and `/adt-decide`
need no change.
**Reversibility:** Medium — the prefix is one line in the per-user yaml, but
tickets already stamped with it would have to be renamed.

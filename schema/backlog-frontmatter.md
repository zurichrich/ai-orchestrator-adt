# Backlog frontmatter contract

Every ticket is a markdown file at
`~/.adt/<project>/cache/<type>/<status>/<slug>.md` with fenced YAML
frontmatter. The **folder is the source of truth for status**; the generator
mirrors it into a `stage:` field.

## Fields

| Field | Required | Values / format | Notes |
|---|---|---|---|
| `slug` | yes | kebab-case | filename + branch name |
| `id` | auto | `<PREFIX>-NNN` (e.g. `a consumer ticket`) | assigned by the generator; never hand-write or reuse |
| `type` | yes | `bug` \| `enhancement` \| `task` | **authoritative for presentation** — the board buckets the card by this field, not by the folder. The `bugs`/`enhancements`/`tasks` dir is create-time bucketing only (see below). |
| `stage` | auto | `ideas` \| `planned` \| `building` \| `qa` \| `ready-to-release` \| `blocked` \| `done` | **derived mirror of the folder**, written/validated by the generator (see below). Don't hand-edit — move the file instead. |
| `priority` | yes | `P0` \| `P1` \| `P2` \| `P3` | |
| `size` | yes | `XS` \| `S` \| `M` \| `L` | |
| `created` | yes | `YYYY-MM-DD` | |
| `created_by` | yes | who filed it | |
| `updated` | no | `YYYY-MM-DD` | |
| `commits` | no | comma-separated short SHAs | linked on the board when the work ships |
| `ui_review_required` | no | `true` \| `false` | gates the design review |
| `security_review_required` | no | `true` \| `false` | gates the security review |
| `closed` | no | `YYYY-MM-DD` | set when moved to `done/` |
| `plan_approved` | no | `true` \| `false` | the human's one approval that licenses `/adt-build-todone` to run autonomously (ADT-81) |
| `done_evidence` | no | list (see below) | the machine-checkable Definition of Done — asserted by `done-guard.sh` on the done-move AND graded by `/adt-build-todone` each pass (recorded in the decision log) |
| `gate_effects` | no | list (see below) | ADT-224 D1a — what each counter-check gate did. `kind: record` rows are appended by `adt_dod --record-verdict <gate>` (one per round: `gate`, `round`, `verdict`, `hash`); `kind: derived` rows are written from them by the same tool (`ran`, `caused_edit`, `under_recorded`, `gating_enabled`). Flat, because the serializer cannot nest. Never pushed to the Issue, so it costs nothing against the 65536-char body cap — and a rebuilt cache loses it, which reads as `under_recorded`, not as a true zero. |
| `dod_snapshot` | no | list (see below) | ADT-224 D3a — the `done_evidence` digest at a point in time (`n`, `hash`). The amendment count is the number of distinct digests minus one, so it measures conditions actually changing between the plan-time dry-run and first green rather than a file existing. |
| `tokens` | no | integer \| `unattributed` | cost-of-work stamped at `/adt-close` from `adt-token-total.sh` — the cross-machine sum (the cost-of-work work). `unattributed` = never captured; never coerce to `0` |
| `cost_usd` | no | number \| `unattributed` | the same work in money, stamped at `/adt-close` from `adt-token-total.sh --cost` (ADT-115). Anthropic list rates, priced per ledger row at that row's own model **and** speed. **Not necessarily complete:** the ledger recorded only the main session until 2026-09-06, so for work closed before then this excludes subagent spend entirely — read `cost_tier` before quoting the number (ADT-247). `unattributed` = never captured; never coerce to `0` |
| `cost_tier` | no | `measured` \| `estimated` \| `legacy` \| `unattributed` | how `cost_usd` was arrived at (ADT-115). Anything other than `measured` renders with a `~` prefix on the board. Never inferred from the value — it travels with it |
| `recurring_cost` | when the diff touches infrastructure | string | what this ticket costs **per month from now on**, and how that was measured — e.g. `~2500 Actions min/month (measured 2026-09-07)`. `none` is a valid answer. Required by done-guard gate #4 when the ticket's commits touch a CI workflow, Dockerfile, terraform, unit file or deploy config (ADT-280) |

## `recurring_cost` — the bill that outlives the ticket (ADT-280)

`cost_usd` counts model tokens: what it cost to *do* the work, once. A ticket
that switches on infrastructure also starts a meter that runs every month
afterwards, and nothing in ADT was watching it.

ADT-033 turned on four GitHub Actions workflows. One ran on a macOS runner, which
GitHub bills at ten times the Linux rate. That spent roughly 2,500 of a
3,000-minute monthly allowance in seven days, and the first anyone knew was
GitHub's 90%-usage email. The runner choice was written down — in a code comment
inside the workflow file. No ticket, board card or close report ever carried a
monthly figure, so there was no surface on which anyone could have noticed.

`recurring_cost:` is that surface. done-guard gate #4 refuses the move to `done/`
when the ticket's own commits touched an infrastructure path and the field is
absent. The gate does not judge the value: `recurring_cost: none` passes, and so
does a rough figure. What it enforces is that somebody had to answer the
question before the ticket closed.

Write what it costs and how you know:

```yaml
recurring_cost: ~2500 GitHub Actions min/month, measured 2026-09-07 from job
  durations x the 10x macOS rate
```

Fail-open by design — a ticket with no `commits:` block, or a SHA git cannot
resolve, is not a cost problem and is never blocked on it. Proved by
`defaults/hooks/tests/test_recurring_cost_gate.sh`.

## Reserved labels — `adt:filing` (ADT-116)

Frontmatter round-trips to Issue **labels**: `priority`/`track`/`stage` become
`P1`/`track:*`/`stage:*`, the review gates become `gate:*`, and any label the
serializer does not recognise falls through into `tags`.

`adt:filing` is the one exception — sync machinery, never ticket metadata, and
**it must never appear in a ticket's `tags`**. It is a transient claim applied
by `gh issue create` while `/adt-brief` is still writing the cache file, so a
watch tick in that gap does not reconstruct a duplicate stub (see the git
workflow rule §D6). `from_issue` drops it on read, which is also what releases
it: the label is absent from the cache file, so the next push computes it as a
label to remove and deletes it from the Issue.

If it ever did reach `tags`, the claim would become **immortal** — the push
writes the Issue's label set back from the cache, so the label it was supposed
to release would be rewritten on every tick, and that Issue could never be
reconstructed again. The playbooks carry this; ADT-280 removed the test that
asserted their wording.

## `tokens` + the per-machine register comments (ADT-100)

The live token count accrues in a **gitignored machine-local ledger**
(`.adt/state/cost-ledger.log`), so on its own it never reaches
another machine. Two durable surfaces carry it across machines:

1. **Register comments on the GitHub Issue** — one per machine, maintained by
   the sync (`adt_sync.checkpoint_tokens`, triggered by a stage-label push,
   the done stage, or ≥25k tokens of un-checkpointed drift — never per idle
   tick). Single-line by contract:

   ```
   <!-- adt:tokens machine=<hostname> total=<N> --> 🪙 N tokens … (ADT-100)
   <!-- adt:cost machine=<hostname> micros=<N> tier=<tier> --> 💵 $N.NN … (ADT-115)

Distinct from those per-machine **registers** is the per-ticket **stamp**
(ADT-115 / 5b), pushed by the sync from the frontmatter once `cost_usd:` is
set — one comment per ticket, machine-independent, and the number of record:

   <!-- adt:stamp tix=<ID> cost_usd=<N> tier=<tier> tokens=<N> --> 📌 Final … 

The registers move while work continues; the stamp does not. A reader comparing
them should trust the stamp.
   ```

   `total` is cumulative and only grows. Each machine PATCHes only its own
   comment (id cached in `.adt/state/token-checkpoint/<TIX>`,
   as `<checkpointed_sum>\t<comment_id>`), so cross-machine totals **sum** —
   never last-writer-wins. Readers (`adt-token-total.sh`, `build_kanban.py`)
   combine: `max(own ledger, own register) + Σ other machines' registers`.

2. **The `tokens:` frontmatter stamp** — written once at `/adt-close` with the
   cross-machine total from `adt-token-total.sh`; top of the board's
   precedence (stamp → ledger+registers → 🪙 —).

## `done_evidence` — the machine-checkable DoD (recorded in the decision log)

A list of **objective** conditions a ticket must satisfy — each checkable by
`tools/adt_dod.py` with no agent narration as input. `done-guard.sh` asserts the
`done`-lane entries on the `mv` into `done/`; `/adt-build-todone` grades the
whole list, sliced by `lane`, each loop pass. Three entry kinds:

**This frontmatter list is the only copy anyone authors.** The
`### Definition of Done` fenced block in the spec body is generated at push time
from this list and overwritten on every push, so a hand-edit there is discarded
(ADT-273). It used to be a second hand-maintained copy: only the frontmatter was
ever read, so the two drifted — 4 of 29 tickets carrying both disagreed on
condition count — and a reconstruct, which rebuilds a cache file from the Issue
body alone, produced a ticket with no DoD at all in 10 of 73 cases.
`done-guard.sh` now denies a `done` move whose body advertises a DoD the
frontmatter does not carry.

```yaml
done_evidence:
  - must_run: 'python3 -m pytest tools/tests/test_x.py'  # passes when it exits 0
    was_red_at: <git-sha-or-"plan">    # optional: assert a real red→green (the
                                       #   command FAILED at this ref, passes now;
                                       #   "plan" = no checkoutable ref → unverified)
    lane: build                        # plan | build | qa | done (default: build)
  - file: .adt/kanban.html              # the RENDERED file the human opens
    must_contain_regex: '<a [^>]*href="references\.html"'   # the real element, not a substring
    lane: done
```

- `must_run` — a shell command; **exit 0 = met**. The general objective check.
- `was_red_at` — re-runs `must_run` at the recorded ref in a throwaway worktree to
  confirm a genuine red→green (not an always-green that was never broken).
- `must_contain_regex` — the regex must match in the named `.adt/`
  rendered copy (never the cache or the source `.md`) — defeats a tooltip/substring
  false-positive by requiring the real element.
- `lane` — which stage the condition gates; the loop advances a stage only when
  that stage's slice is all-green.

**Prose is not allowed** — a condition `adt_dod.py` can't decide makes
`/adt-build-todone` refuse to start (the guardrail against a loop grading its own
homework).

### Ordering: `id` + `depends_on` (ADT-114)

Two optional modifiers. Absent, nothing changes — a DoD with no edges grades
exactly as it always did.

```yaml
done_evidence:
  - must_run: 'python3 -m pytest tools/tests/test_parser.py -q'
    id: parser
    lane: build
  - must_run: 'python3 -m pytest tools/tests/test_end_to_end.py -q'
    depends_on: parser          # space- or comma-separated ids
    lane: build
```

- `id` — a short name other entries can point at.
- `depends_on` — the ids this condition is meaningless without.

Where B depends on A, the grader will **not report B green while A is red**. B
becomes *can't-verify* ("upstream 'A' is not met"), not *failed* — the claim is
not that B is broken, only that it cannot honestly be called green ahead of what
it rests on. Can't-verify already blocks `all_green`, so both callers
(`done-guard`, the build-todone loop) need no change.

An **unsatisfiable** ordering — a cycle, or a `depends_on` naming an id no entry
declares — is an **authoring defect**, refused by `adt-dod.sh <ticket.md> --gate`
before a build starts rather than discovered mid-loop.

### Caveats must attach: `-> DoD:<id>` / `-> WAIVED: <reason>` (ADT-114)

A caveat written into `### Design`, `### Risks` or `### Test plan` ("assumes X",
"verified manually", "won't handle Y") used to have no edge to anything. Nothing
graded it, so it was stated and then dropped — the "you gave me a caveat and then
ignored it" failure. `--gate` now refuses a plan carrying an unattached one, and
**names it** in the refusal.

Discharge a caveat in the same block, either way:

```markdown
### Risks
- The renderer assumes the watcher is running. -> DoD:watchdog
### Test plan
- Verified manually on the prod host. -> WAIVED: no hermetic way to drive the
  harness's permission layer from a test.
```

`-> DoD:<id>` points at the condition that resolves it; `-> WAIVED: <reason>`
records it as a deliberate carve-out. Both are visible to a reader, which is the
point: the failure being caught is the **silent** omission, not the acknowledged
one. A carve-out that names itself and gives its reason has been handled
honestly.

Detection is deliberately conservative — the markers are the phrases ADT actually
got burned by, not every hedge. An over-eager gate gets worked around, and a
worked-around gate is worse than none. Widen it only with an incident to point at.

## `stage` is a derived mirror — folder is truth

`stage:` exists so the status is **queryable** (`grep '^stage:'`) and
**portable** (survives a brief being read out of its folder), but the folder
under `backlog/<type>/<status>/` remains the single source of truth — and the
only thing that reads status at runtime (the kanban generator).

On every run the generator **sets `stage:` to the folder value** — inserts it
if missing, corrects it if stale (e.g. right after you `mv` a brief to a
new folder). Folder always wins. So you change status by moving the file
between folders; editing `stage:` by hand does nothing lasting — the next run
brings it back into line with the folder.

## `type` is the reverse — frontmatter is truth

`type:` works the OPPOSITE way round to `stage:` above, and the asymmetry is
deliberate rather than an oversight. **Stage** can be folder-derived because
stage has a *mover*: every playbook `mv`s the brief across cache lanes on each
transition, so the folder is always current and the generator can safely
overwrite `stage:` from it. **Type** never had a mover — nothing in `adt_sync`
relocates a ticket between type folders (`_cache_path_for` runs only when an
Issue has no local cache file at all), so a type decided at create time could
never be changed afterwards.

That made the type dimension unrecoverable for any backlog *adopted* from
pre-existing Issues: with no `type:` label to read, every ticket landed in
`tasks/` and stayed there, and the sync's own printed remedy — edit the
frontmatter and push — could not work, because nothing downstream read the
field (ADT-155).

So the generator reads `type:` from frontmatter and falls back to the folder
only when the field is absent, blank, or not one of the three values. Both the
singular (`bug`) and plural (`bugs`) forms are accepted, case-insensitively.
Practical consequences:

- **To re-type a ticket, edit `type:` — do not move the file.** The next board
  render buckets the card by the new value. The file stays where it is, so
  there is never a second cache file or a duplicate card.
- **The folder is storage.** A ticket may sit in `tasks/` and render as a Bug;
  that is expected on an adopted backlog, not a defect.
- **New files are still bucketed by the same field.** `_cache_path_for` writes
  `<type>/<stage>/` from `type:` at create time, so folder and frontmatter agree
  for anything filed after adoption, and a cache rebuilt from Issues lands
  everything in the right folder.

## Deprecated fields (orchestrator-era — being phased out)

`state` (→ replaced by `stage`; the generator strips it), `strike_count`,
`last_blocker`, `kickoff`, `specialism_input` — leftovers from the removed orchestrator
orchestrator daemon. Harmless if present on old briefs; not required on new ones.


## `gate_effects` — did a gate change anything? (ADT-224)

Two row kinds, deliberately flat: `_parse_dictlist` reads `- k: v` / `  k: v`
one level deep, and `_emit_pair` sends a nested dict to `_emit_scalar`, which
stringifies it. `record` rows have cardinality N per gate and `derived` rows
cardinality 1, so they cannot share an item shape.

```yaml
gate_effects:
  - kind: record            # appended at verdict time, one per round
    gate: plan-quality      # coverage | plan-quality
    round: 3
    verdict: FLAWED
    hash: <graded-text hash>
    rests_on: tools/adt_watch.py:_save_watch_state   # optional; see below
  - kind: derived           # rewritten from the records; never clobbers them
    gate: plan-quality
    ran: 3
    caused_edit: true
    under_recorded: false
    gating_enabled: true
```

`ran` is that gate's `record` count — not a ledger dispatch count, which would
count a reviewer that was interrupted before it produced a verdict.

`caused_edit` is a NEGATIVE verdict at round N whose `hash` differs from the
same gate's round N+1. Not "a later block exists": `commands/plan.md` re-runs
BOTH gates after any rewrite, so that would credit the coverage gate for a
plan-quality fix.

The **graded-text hash** covers every `###` section the plan-quality reviewer
reads plus `done_evidence`, whitespace-normalised so a reflow is not an edit. A
heading is matched with an optional parenthetical suffix, so the template's
`### Sub-steps  (DERIVED from Design + Impact — not invented)` counts (AO-013 1g:
before that, no ticket's sub-steps were ever in the hash). A section ends at the
next `###` or the next `##`, which keeps an appended build-log line out of it. It
is computed by `adt_dod`, never by an agent, and written at verdict time —
the cache is outside every git checkout, so an unstamped round is unrecoverable.

`under_recorded` is a verdict BLOCK present with zero records. That is a
different fact from "the gate never caused an edit", and reporting it as the
latter would credit a gate nobody measured. It does not refuse: a dispatch that
legitimately produces no block (an interrupted reviewer, an `UNKNOWN` verdict)
would otherwise make the ticket permanently un-releasable with no reconciliation
path. Under-crediting is the conservative direction.

`rests_on` is the file:symbol list a coverage verdict rests on staying unmodified (AO-013).
It is written only when the reviewer's block carries a
`**Depends-on-unmodified:**` line, so an absent key and a verdict that assumes
nothing stay distinguishable. `reopened_dependencies()` reports a dependency in
doubt when the graded text has moved since that row was written AND the file is
still named by the current `### Sub-steps` — one half alone is a typo in Risks or
a spec nobody has touched. Only the LAST row per gate is checked: an earlier
row's dependency was either re-stated by the round that followed it or dropped on
purpose. `--record-verdict` prints a NOTE when a block carries no
`**Depends-on-unmodified:**` line at all, and names the justification phrase when
the block also uses one ("existing, unmodified code", "needs no test").

The match is on the FILE, not the symbol, and the limit is worth knowing. At plan
time nothing has been built, so "the diff touches that code" is not observable —
the only available signal that the spec now INTENDS to touch it is the spec's own
work-set. So this narrows across dependencies (one naming a file the sub-steps
never mention does not fire) and not within a file: on a ticket that edits the
declared file throughout, the second half is always true and the rule reduces to
"the graded text moved". The symbol is carried into the message, so the reviewer
is asked about the right thing even where the test could not be.

Before this key, AO-006's round-3 coverage verdict passed an obligation because
the code behind it was not changing, round 6 modified that code, and nothing
reopened the verdict.

`gating_enabled` is RECORDED rather than re-evaluated at report time.
`plan_gating_enabled()` is global and per-run, so a report generated after the
flag flips would mis-describe every historical ticket, in both directions.

Note `round` and `ran` come back from the parser as STRINGS: `_parse_dictlist`
calls `_coerce_scalar(v)` without `key`, and `INT_KEYS` is consulted only when a
key is passed. Readers coerce.

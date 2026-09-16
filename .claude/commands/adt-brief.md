---
adt_managed: ADT-087
name: adt-brief
description: Capture an idea (new, CX feedback, or an R&D finding) as a backlog file in ideas/
adt-budget: 170
---

# /adt-brief

Gets an idea into the backlog as a structured `ideas/` file. It has two modes:
**capture** a new idea, or **triage** `cx-*`/`rnd-*` files someone dropped into
`ideas/`.

**Describe the problem the user has, and leave the solution out.** A brief that
prescribes the solution makes the plan's decisions for it. Bugs are P0/P1 and
enhancements P2. Cite the observation, screenshot or data rather than an opinion.

**Write success criteria as outcomes someone could observe.** "The export button
produces a CSV the user can open in Excel" can be checked; "exports work well"
cannot. You are not designing the solution, but criteria you could observe or
measure give `/adt-plan` a starting point for its machine-checkable DoD.

## Mode A — capture

**Pre-flight:** `gh` must be authenticated. Filing creates the Issue first, so the
ticket id is the real issue number. If `gh` is unavailable, stop and say so. Do
not write a ticket with no number.

1. **Ask the user for:** slug (kebab-case; it becomes the filename and branch),
   **type** (`bug`/`enhancement`/`task`, REQUIRED), problem, success criteria,
   priority (P0/P1/P2), size (XS/S/M/L), whether a UI is involved
   (`ui_review_required`), and whether auth or data exposure is involved
   (`security_review_required`).

   `type:` decides how the ticket is shown. The board groups by it, and the sync
   pushes it as the only label that survives a cache rebuild. If it is missing,
   every rebuilt cache files the ticket as a task.

2. **Create the Issue first, and claim it on the same call.** The issue number is
   the ticket id. Read `repo` from `.adt/config.yaml`:

   ```bash
   # Provision the claim label first (idempotent; 422 = already exists).
   gh api -X POST "repos/<repo>/labels" -f name='adt:filing' -f color='ededed' \
          -f description='Transient: /adt-brief is mid-filing.' >/dev/null 2>&1 || true
   url=$(gh issue create --repo <repo> --title "<title>" \
           --body "<problem + success criteria>" --label "stage:ideas" \
           --label "<P0|P1|P2>" --label "adt:filing")
   num=${url##*/}
   node=$(gh api "repos/<repo>/issues/$num" --jq .node_id)   # REST, not GraphQL
   ```

   **Put the `adt:filing` label on the create call itself, not on a follow-up
   command.** Between the Issue existing and the cache file existing, an
   `adt watch` tick sees an Issue with no local file and rebuilds a stub for it.
   That leaves two cache files and two cards for one ticket, with no warning. The
   label tells the sync to hold off rebuilding. The first push after the cache
   file exists removes it automatically, so you never need to remove it yourself.

   **Filing into a repo other than the `repo` in your own `.adt/config.yaml`?**
   Do not use this step as written. Follow "Filing into another project's
   backlog" below.

3. **Write the cache file** at `~/.adt/<project>/cache/<type>/ideas/<slug>.md`,
   stamped `id: <PREFIX>-<num>`, `issue_number: <num>`, `issue_node_id: <node>`.
   Never make up a local id. Take it from the number GitHub assigned.

```markdown
---
slug: <slug>
id: <PREFIX>-<num>
title: <Feature title>
type: <bug|enhancement|task>
created: YYYY-MM-DD
created_by: po
stage: ideas
state: open
state_reason: null
priority: <P0|P1|P2>
size: <XS|S|M|L>
assignees: []
milestone: null
security_review_required: <true|false>
ui_review_required: <true|false>
issue_number: <num>
issue_node_id: <node>
comments: []
---

# <Feature title>

## Problem (user)
<problem>

## Success criteria (user)
- <criterion 1>

## R&D notes
(empty unless invoked)

## Plan (PM)
(empty until /adt-plan runs)

## Build log (Dev)
(empty)

## QA report (QA)
(empty)

## Release notes (RM)
(empty until shipped)

## Log

## YYYY-MM-DD HH:MM — po (<your-name>)
[idea] Filed <slug> (priority <P>, size <S>).
```

4. **Bind the session to the ticket, then check the binding. If the check fails,
   stop.** Run `.claude/hooks/adt-mark-tix.sh <PREFIX>-<num>`, then immediately
   `.claude/hooks/adt-verify-bind.sh <PREFIX>-<num>`. Filing counts as picking
   the ticket up, so the tokens spent filing are billed to it. The mark hook
   exits 0 and writes nothing when an input is missing, so a failed bind looks
   like a successful one. If verify exits non-zero, **STOP**, re-run both, and
   do not continue unbound.

5. **Nothing to commit.** The file you wrote is the cache, and the cache lives
   outside every git checkout. `adt watch` pushes it to the Issue on the next
   tick. Never run git against a cache path: git walks up the directory tree to
   find a repo and finds `$HOME`.

6. **Print:**
```
Filed: <slug>.md in ~/.adt/<project>/cache/<type>/ideas/  (sync → GitHub Issue)
Type: <type>, Priority: <P>, Size: <S>, UI: <bool>, Security: <bool>
```

## Filing into another project's backlog

Use this when the Issue goes into a repo other than the `repo` in your own
`.adt/config.yaml`, for example a consumer project's session filing a ticket for ADT.
Step 1 applies as written. Step 2 applies with the changes below. Steps 3 and 4
do not apply.

- **Leave off the `adt:filing` label.** The label tells the sync that a cache
  file for this Issue is about to be written, and only the push of that file
  removes it. From another project nothing writes or pushes that file, so the
  label stays on for its full hour and the ticket is missing from the receiving
  board for that hour (#358, #359). Without the label, the receiving project's
  `adt watch` builds the cache file from the Issue on its next tick.
- **Put on the Issue everything the cache file needs**, because it is built from
  the Issue alone. Add `--label "type:<bug|enhancement|task>"` (without it the
  ticket is filed as a task), keep `stage:ideas` and the priority label, and add
  `gate:ui` or `gate:security` when those reviews apply.
- **End the body with the slug trailer**, after a blank line, as the very last
  line: `<!-- adt: slug=<slug> -->`. The rebuilt cache file is named from it.
  Without it the file is `issue-<N>.md`.
- **Write no cache file and bind nothing.** Your cache belongs to your own
  project. The sync pushes every file in it to your own repo by issue number, so
  a file carrying this number would overwrite your project's Issue with the same
  number.

Print the Issue URL and say which project's board it will appear on.

## Mode B — triage dropped input

When `cx-*.md` (CX feedback) or `rnd-*.md` (R&D findings) files are already in
`ideas/`, list them. For each one, do one of these:

- **Accept.** Rename it to a clean slug, set `created_by`, priority and size,
  confirm the problem and criteria, then finish with the capture flow above.
- **Drop.** Move it to `ideas/.archive/`. Never delete it.
- **Merge.** Append the observation to an existing idea's `## Problem (user)`,
  then archive the file.

Nothing produces `cx-*`/`rnd-*` files automatically. Someone pastes the
observation into a file with that prefix in `ideas/`, and this command triages
it. No separate command is needed.

## Research work (producing an `rnd-*` finding)

When you investigate before building, for example into prior art or
feasibility, stay **read-only and base every conclusion on evidence**. A
prototype to test feasibility is fine. Turning it into production code belongs
to the build.

**Every counted claim includes the command that produced it and the date it
ran.** Write `grep -c '^cost_usd:' tasks/done/*.md → 2 of 31, 2026-09-02`, not
"two tickets carry a cost". Nobody can spot-check a bare number in prose, so
nobody does, and an evidence record that other tickets cite goes stale without
anyone noticing.

**A claim taken from a simulation mode names the flag and what the real run does
differently.** `--dry-run`, `--check` and `--what-if` often skip the shortcuts
that make the real run cheap, so their output shows the diff, not what would be
written. For example, `adt_sync.py --dry-run` discards the push-hash map
(`tools/adt_sync.py:1541`), so it diffs every ticket, while a real pass skips the
tickets whose hash is unchanged and writes nothing. A data-loss bug was once
filed from a correctly quoted dry-run line for this reason. Citing the command
is not enough on its own: also cite the code path.

<!-- adt-bundle: v0.1.0 -->

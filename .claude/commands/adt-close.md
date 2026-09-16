---
adt_managed: ADT-087
name: adt-close
description: After the merge — verify done from the diff, stamp the cost, move to done/, write the retro, tear down the branch
adt-budget: 160
---

# /adt-close

Run this AFTER the `feat:` PR is merged. The move to `done/` is the **only**
lifecycle move tied to the code. Earlier moves happen independently and
`adt watch` carries them. `done` means the code is integrated, so this command
runs only once the code is on `main`.

**Submodules:** if the ticket changed a submodule, the code reaches `main` as a
pin bump. Do **not** squash the submodule's own history, because that orphans
the pinned SHA. Fast-forward the submodule's `main` to the pinned commit so the
branch contains what the superproject pins. Check with
`git -C <submodule> branch -r --contains <sha>`, which must show `origin/main`.

## Pre-flight

**Bind the session to the ticket, then check the binding. If the check fails,
stop.** Run `.claude/hooks/adt-mark-tix.sh <ID>`, then immediately
`.claude/hooks/adt-verify-bind.sh <ID>`. The mark hook exits 0 even when it wrote
nothing, so do not assume it worked. If verify exits non-zero, **STOP**, re-run
both, and do not close unbound.

The binding matters for more than cost. `adt-close-complete.sh` is the `Stop`
hook that checks this close actually reached `done/`. It finds the ticket through
`current-tix.d/<session>`, and with no marker it lets the session end without
checking. An unbound close is one that hook cannot check.

## Steps

1. **Confirm the code PR is merged to `main`.** If it isn't, abort.

2. **Verify done from the diff, not from intent.**
   - **Grade the DoD one last time.** Every condition must pass. The done-gate
     hook checks the done-lane conditions when the file moves. A failing
     condition means the ticket is not done.
   - For each **Design** and **Impact** claim, point to the line in the diff that
     does it. A claim with no line behind it is **not done**: say so and stop. A
     doc the spec said would change must have changed.
   - **Does it run?** If a user or a scheduled job triggers the path now, does
     the fix execute? Code that nothing calls is not done.
   - If the ticket was abandoned instead, mark `status: cancelled` with a
     one-line reason. Cancelled is the only other end state.

3. **Append `## Release notes`:**

```
## Release notes
**Shipped:** YYYY-MM-DD HH:MM, BUILD_VERSION X.Y.Z
**Merged PR:** <link>
**Deviations from plan:** <list, or "none">
**Follow-ups:** <"none" — or, per ticket, which of the three conditions it
  cleared (unforeseen / significant-deviation-required / human-approved) and the
  evidence. A follow-on with no cleared bar recorded is a DEFECT in this
  delivery. Default is "none": work found mid-build is fixed in the diff.>
**Follow-on count:** <N — also stamped as `follow_ons: N`. The metric is
  tickets-spawned-per-ticket-delivered; it should trend to zero.>
**Retro learnings (project):** <retros/<slug>.md, or "none">
```

> **Every path you record there must exist.** Run `test -e` on each one before
> writing the block, and write "none" instead of a path for anything you decided
> not to produce. Naming a file does not create it. Step 2 checks the ticket's
> code, not the files this close is meant to write, so nothing else will notice
> a retro that was named but never written (working-style #5).

4. **Update the frontmatter.** The cache comes first: the sync closes the Issue,
   so never run `gh issue close` by hand.

   > **If the ticket has no cache file, create it in `done/` now.** A ticket
   > filed with a bare `gh issue create` instead of `/adt-brief` has no cache
   > file, so this step's update and step 5's move have nothing to act on. That
   > is not a reason to skip them. It is the thing to fix. Write the full
   > frontmatter (`slug`, `id`, `title`, `type`, `priority`, `track`,
   > `stage: done`, `state: closed`, `state_reason`, `issue_number`, `commits`,
   > `tokens`, `cost_usd`, `cost_tier`) straight into `<type>/done/<slug>.md`, in
   > one atomic step: write a temp file in the same directory, then `mv` it into
   > place, so the watcher never reads a half-written file. Then stop and let the
   > `adt watch` tick (about every 60s) update the Issue.
   >
   > **Never change the Issue directly instead.** Setting its state or the
   > `stage:done` label by hand leaves the cache, which is the source of truth,
   > with no entry. The card never appears on the board, and the next sync will
   > reopen the Issue. Every other signal looks correct while this is wrong.
   - Set `state_reason: completed` (or `not_planned` if cancelled), and append
     the ticket's commit SHAs to `commits:`.
   - **Do not set `stage:` or `state:` in this step.** The file is still in
     `ready-to-release/`, and the rest of this step takes long enough for a
     watch tick to land in the middle. The render treats the folder as the
     truth, so it rewrites a done stage back to `ready-to-release` and a closed
     state back to `open`, and the next sync pushes that: the Issue closes, then
     reopens. Step 5 sets both after the move, when the folder and the
     frontmatter agree. `state_reason` is safe to set here because the sync
     reads it only when it closes an Issue.
   - **Post the closing rationale yourself.** A `comments:` entry in frontmatter
     stays in the cache. The sync only pulls comments from GitHub and will not
     push it. So:
     ```bash
     gh api -X POST repos/<owner>/<repo>/issues/<n>/comments -F body=@<file.md>
     ```
     Then **read the Issue back and confirm the comment is there.** The cache
     file, the closed state, the label and the card all look correct whether or
     not the reasoning was posted, so reading the Issue is the only way to know.
     A reason that exists only in a local cache file has not been recorded.
   - **Stamp tokens and cost.** The ledger on this machine is gitignored and
     never sees other machines' spend. Use the helper, which combines it with
     each machine's register comments on the Issue:
     ```bash
     .claude/hooks/adt-token-total.sh <TICKET-ID>
     .claude/hooks/adt-token-total.sh <TICKET-ID> --cost
     ```
     Write the first output exactly as printed into `tokens:`. The second prints
     `<micro-dollars><TAB><tier>`; write it as `cost_usd:` (divided by 1,000,000)
     and `cost_tier:`. **Always write all three fields.** `unattributed` is a
     valid answer. **Never write `0` in its place**: a wrong "this was free" is
     worse than "not captured". A `cost_usd` without its `cost_tier` looks exact
     when it may not be. If the tier is `estimated` or `legacy`, record the
     estimator's `derivation_id` in the release notes, so a later re-price knows
     which ratio produced the number.
     **Then check that nothing in the total is unpriced.** A total that is too
     low looks the same as a correct one:
     ```bash
     python3 .claude/tools/adt_cost.py unpriced .adt/state/cost-ledger.log
     ```
     Exit 0 means every row has a price. A non-zero exit NAMES the model ids that
     have no rate. Record them in the release notes next to the stamp, because
     the figure you are writing is short by whatever those rows cost. Do not make
     up a rate. Add the id to `defaults/pricing.json` if it is a real model, or
     file it as unknown.

     The stamp is a starting value. The tokens spent closing are logged at the
     Stop after you write it, so **the sync recalculates it over the next few
     ticks** and rewrites `cost_usd:` until it stops changing. Don't re-stamp by
     hand when the figure moves. That is the correction working.
   - **Stamp `recurring_cost:` if this ticket touched infrastructure.** A CI
     workflow, a Dockerfile, terraform, a unit/plist file or a deploy config
     starts a bill that keeps running after the ticket closes, and `cost_usd`
     only counts the tokens spent building it. Write the monthly figure and how
     you measured it, or `none` if it really adds nothing. The done-guard hook
     (gate #4) denies the move to `done/` without it on those paths. A measured
     range is better than a blank field; precision is not the point.
   - **Do not repeat the dollar figure in prose.** The sync pushes the stamped
     `cost_usd:` as a structured comment and pushes it again when it changes. A
     number typed into prose never updates, and the ticket ends up showing
     several different figures.

5. **Move the file** `ready-to-release/ → done/`.
   **Then** set `stage: done` and `state: closed` in the moved file.
   **Use a plain `mv`, never `git mv`.** The cache is outside every git checkout,
   and `git mv` commits into whatever repo git finds walking up the tree. Make no
   `gh issue` call: the sync sets `state: closed` and the `stage:done` label.
   Moving first means a render at any point agrees with what you write: once the
   file is in `done/`, the render produces the same two values.

   **Then read the Issue back and confirm it closed.** After the next sync (the
   watch tick every 60s, or a pass you run), print its state and stage label:
   ```bash
   gh api repos/<owner>/<repo>/issues/<n> --jq '"\(.state) \([.labels[].name | select(startswith("stage:"))] | join(","))"'
   ```
   Expect `closed stage:done`. Anything else means the cache and the Issue
   disagree. Check the cache file still reads `stage: done` / `state: closed`,
   run one pass with `python3 "$ADT_DIR/tools/adt_sync.py" --root <project>`, and
   read the Issue back again. Do not report the close as done until it prints
   `closed stage:done`. The cache file, the board and the closing comment all
   look right while the Issue sits reopened, so this read is the only check that
   sees it.

   **Then check the Issue for a duplicated stamp.** If the launchd tick (every
   60s) and a manual sync run land in the same second, both read the register
   checkpoint before either writes, and both post a comment. The extra one is
   orphaned and no later update reaches it. There is no lock across processes,
   so this check is what catches it:
   ```bash
   gh api repos/<owner>/<repo>/issues/<n>/comments \
     --jq '.[] | select(.body|test("adt:(stamp|tokens)")) | "\(.id)\t\(.body[0:40])"'
   ```
   If there is more than one of either, delete the extra with
   `gh api -X DELETE repos/<owner>/<repo>/issues/comments/<id>`.

6. **Go through the adjacent findings, then decide on a retro.** Close is the
   **only** place an adjacent finding is recorded. Anything you noticed during
   the build that wasn't needed for the task was held until now, instead of being
   reported mid-stage or offered as a ticket. For each one, ask: **would a
   competent maintainer change something because of it?** If not, drop it and
   don't mention it again. If so, it goes in the retro below, and nowhere else.
   **Do not raise a ticket for one.** Reaching close does not authorise that.

   Write `docs/retros/<slug>.md` if there were deviations, anything surprising, a
   material adjacent finding, or the feature was larger than S.

7. **Commit the close only if it wrote a TRACKED file.** Check before branching:
   `git check-ignore -v <each path you wrote>`. A close that produced only a
   retro, an ADR and the ticket move has nothing to commit, because all three are
   gitignored or live in the cache outside every checkout. Say so and skip to
   step 8. ADT's own `.adt/` is ignored in full, so in this repo that is the
   normal case.

   If the close DID write a tracked file (a playbook fix, a doc), commit it on
   `release/<slug>` in a worktree, never in the canonical checkout
   (`git worktree add <path> -b release/<slug>`, or branch from the ticket's
   existing `dev/<slug>` tree). Message: `release: <slug>`. Push, open a PR, and
   merge it yourself.

8. **Remove the branch and worktree.** It is safe now because the code is on
   `main`. Run `git worktree list`, then `git worktree remove <path>`, then
   `git branch -d <branch>` (**the safe delete, which refuses an unmerged
   branch; never `-D`**), and `git push origin --delete <branch>` if the merge
   didn't delete it. **Print what was removed**, and say so if nothing matched.
   Closing a ticket that never had a worktree still succeeds.

9. **Refresh the installed command copies if the merge touched `commands/` or
    `defaults/`.** Sessions run the copies under `.claude/commands/`, not the
    sources. A playbook fix on `main` changes nothing until the copies are
    refreshed, and a lesson recorded as applied can quietly stop applying in the
    meantime.

    **In a consumer project,** run `lib/adt-layer.sh apply <ADT_DIR> <PROJECT_PATH>`.
    The copies are committed, so they belong to every machine that pulls the
    project, and this compares the layer committed on `origin/<main>` with the
    ADT clone before writing anything (ADT-384). If they match, it writes
    nothing. If the clone is older, it refuses and prints the pull. If the clone
    is newer, it commits the refreshed copies to a local
    `chore/adt-upgrade-<sha>` branch and leaves your working tree alone. Report
    that branch to the human: merging it upgrades ADT for every machine, so it
    is their call, and it is not part of closing this ticket.
    **In ADT's own repo,** where source and destination are the same tree, run
    `bash lib/resync.sh` instead. It runs the same install and then reports
    which installed playbooks changed. The harness reads a playbook once, at
    session start, so that report is the only warning that a session already
    running is following instructions that have since changed.

    Either way, note in the release notes that other consumer projects keep the
    old copies until they pull ADT and re-run its installer. There is no
    `adt update` command. State this gap; don't assume it away.

<!-- adt-bundle: v0.2.0 -->

# Multi-agent git workflow — how agents + humans share one repo safely

**One line:** worktrees isolate work on disk, branches isolate history, PRs +
branch protection protect main, and task partitioning prevents conflicts in
the first place.

This is an ADT **default**, installed into a project's `.claude/rules/`. It is
always-on (no `paths:` key), so install `@`-imports it into the project's
CLAUDE.md (a managed `ADT:rules` block) — that import is what actually loads it
into context every session; the `.claude/rules/` symlink alone does not (the
harness doesn't auto-read that directory). It replaces the scattered "check
before you push" guidance (the former CLAUDE.md Rule 13 + the worktree
memories) with one structural model. The root problem it counters:
*behavioural* discipline ("re-anchor, be careful") loses the race against a
parallel session's `git checkout` between your own tool calls — so make the
safety structural, not behavioural.

Origin: a consumer project, 2026-06-10, after parallel sessions stranded a commit
and conflicted on generated HTML across two consecutive incidents.

---

## A. Rules of the road

1. **Never commit directly to `main`.** All changes land via PR. (Enforced by
   branch protection, §B.)
2. **One short-lived branch per task.** Branch off `main`, do the work, merge,
   delete. Keep a single naming convention (e.g. `dev/<slug>` for code,
   `pm/<slug>` for plans, `chore/<slug>` for housekeeping) — pick one, make it
   the rule, don't mix.
3. **One worktree per active agent — mandatory, not "the default."** Agents
   never share a working directory. Any session that will write a tracked file
   works in its own worktree **before the first edit** — `git worktree add
   <path> -b <branch>` is the portable default; a `<your-repo>/scripts/new-worktree.sh` helper is a
   consumer override where a project ships one (ADT itself does not). The canonical checkout is reserved for the deploy/`main` path on the
   prod host only. This is the single change that prevents the branch from
   switching under you — in a tree only you hold, no other session can move it.
   *Enforced where a project turns it on:* ADT ships
   `defaults/hooks/adt-worktree-guard.sh`, a PreToolUse hook that **hard-denies**
   a Write/Edit in the canonical checkout to any path matching
   `worktree_guard_paths:` in the project config `~/.adt/projects/<name>.yaml`
   (space-separated fnmatch patterns, quoted, e.g.
   `worktree_guard_paths: "src/* tools/*"`), and names `git worktree add` as the
   remedy. Install copies the value into the generated `.adt/config.yaml`, where
   the hook reads it, so re-run install after changing it; a value typed into
   `.adt/config.yaml` directly is lost at the next install. With no value it does
   nothing, so a project lists the code paths it wants guarded; docs and rules
   usually stay unlisted. It fails open.
4. **Partition work, or serialize it.** Assign non-overlapping
   directories/modules where possible (two agents on different files merge
   clean). Where work *can't* be partitioned (see §D3 — submodule/`.claude/`
   changes span everything), **serialize**: one merges, the next rebases. Plan
   waves so that no two in-flight tickets touch the same files.
5. **Small, atomic commits.** Each commit one logical change that builds.
6. **Rebase on `main` before opening a PR.** `git fetch origin && git rebase
   origin/main` keeps conflicts tiny. (This is also the mitigation for the
   stale-merge gap noted in §B.)
7. **Conventional commits with the *why*.** `<type>(<scope>): <summary>`
   (`feat|fix|chore|docs|refactor|test`), body explaining *why*, and for agent
   commits **note the ticket / motivating prompt** so the change is auditable.

### While the PR is open

`main` moves during review, and on a busy repo it moves often — 9 merges in 97
minutes against 5 open PRs, measured 2026-09-12. Two separate things cause the
rebase churn that follows, and only the second is this rule's. A **shared
mutable line** (a hand-edited version string, a counter) makes a conflict
certain between any two branches that touch it; fix that in the project by
deriving the value at build time, isolating it behind a merge driver, or
bumping it only at release. The **gate decaying** is what rules 8-10 cover.

8. **Being behind `main` is not a blocker.** A PR reading `mergeable: clean`
   merges correctly however far behind it is. Rebase only when one of three
   things is true: git reports a real conflict; `main` changed code this diff
   depends on; or the gate has to run on the tree that will actually merge. For
   the middle one, check rather than guess — intersect `git diff --name-only
   origin/main...HEAD` with what moved on `main` since the merge-base, and read
   the overlap. Otherwise leave the branch alone. A reactive rebase costs
   conflict resolution plus a full re-verification round, orphans every
   `was_red_at` pin the ticket holds, and can take `main`'s copy of a
   `merge=ours` generated file (§D1) and leave a stale one behind.

9. **The gate decays as soon as you stop.** §C's green suite describes one tree
   at one instant. A review round, a permission prompt, or a human stepping away
   opens a window in which `main` moves and the result no longer describes the
   tree that would merge. A stale green reads identically to a fresh one, so
   nothing warns you. Run the suite as the last action before the merge, and if
   anything delays the merge after it, treat the result as spent.

10. **Where a project has CI, a merge queue is the real fix.** GitHub merge
    queue, Bors, Mergify and Zuul validate the prospective merge result and
    merge atomically, so the window in #9 is zero. Each needs a machine-visible
    required status check. A project whose gate is an agent running the suite on
    a laptop has none — the server cannot see that run — so it cannot have a
    merge queue, and rules 8-9 are the whole mitigation. On this repo that is
    ADR-036 (no CI) plus §B's no-CI gap.

## B. Branch protection on `main` (server-enforced, no CI required)

The payload (no `required_status_checks` context — works without CI):

```bash
gh api -X PUT repos/<owner>/<repo>/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": null,
  "enforce_admins": false,
  "required_pull_request_reviews": { "required_approving_review_count": 0 },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

`setup.sh --init-github` reads this state and, on an unprotected branch,
offers to apply the payload above — and **applies it only on an explicit yes**.
A repo where you declined is unprotected, and the board's header badge says so
on every render. Nothing is applied silently, and an already-protected repo is
reported and left untouched: this `PUT` REPLACES the whole ruleset, so writing
to a repo that already has one would silently weaken it (ADT-165).

Load-bearing parts: **`allow_force_pushes: false` + `allow_deletions: false`**
(a stranded/clobbered `main` becomes impossible) and **PR required** (no direct
commits). `enforce_admins: false` + `required_approving_review_count: 0` keep a
solo maintainer able to merge on the prod host; raise both if the team grows.

**Gap to know (the no-CI cost):** without a named status check, GitHub can't
enforce "branch up-to-date before merge" server-side. So the *stale-merge*
failure (merging a PR from an older SHA than what was pushed, which strands the
newer commit) is mitigated **behaviourally** by §A.6 (rebase) + a
re-fetch-before-merge step, not prevented structurally. Know this; don't assume
the server catches it.

**And the re-fetch has a footgun that makes it useless.** `git fetch <remote>
<branch>` does NOT move the remote-tracking ref: `git fetch origin main` leaves
`origin/main` exactly where it was, so a behind checkout compares itself against
a stale ref and reads as up to date. Use a bare `git fetch origin`, an explicit
refspec, or compare against `FETCH_HEAD`. Found by ADT-061's own freshness gate,
which would have silently passed the stale deploy it exists to catch — on a
happy-path machine the manual check never showed it
(recorded in the done-claim-gate retro, which lives in the private archive).

**Required status checks — and how to add one (ADT-236).** The payload above is
the *initial* protection state, and it is the state a repo with no CI stays in:
`required_status_checks` stays null and no context is registered. This is ADT's
own position (ADR-036), and `tests/test_no_orphan_required_checks.sh` pins the
invariant that a required context must be produced by a workflow that exists —
a context whose workflow was deleted blocks every PR forever on a check that can
never report.

Once CI does exist, adding a context is a different operation from writing the
payload: use the contexts endpoint, never a re-`PUT` of the ruleset.

```bash
# Additive: adds ONE context and leaves the rest of the ruleset untouched.
gh api -X POST \
  repos/<owner>/<repo>/branches/main/protection/required_status_checks/contexts \
  -f 'contexts[]=license-headers'
```

Two reasons this is the endpoint and not the `PUT`. The `PUT` REPLACES the whole
ruleset (ADT-165), so reaching for it to add a context silently drops every
context already registered — on this repo that would be `cla`, i.e. the CLA gate
would come off in the act of adding the licence gate. And registering a context
carries an **ordering** constraint: with `strict: true`, a required check that
has never reported on `main` blocks every PR including the one that would land
it, so the workflow must be on `main` *before* its context is registered — never
in the same step.

## C. The local merge gate (what "green" means without CI)

Before a PR merges, the author session runs and reports the project's real
checks — not a generic `make test`. For one consumer that is: `pytest`,
`npm run build`, `npm run lint`, `health-check.sh`, plus the
**trading-path-reviewer** (money path), **adt-design-reviewer** + the **Rule-10 UI
walkthrough** (`ui_review_required: true`). This is *stronger* than a generic CI
job; if CI is ever added it must run these, not a weaker target.

## D. Carve-outs a generic workflow misses

**D1. Generated files must never 3-way-merge.** Files generated from source
(here: `kanban.html`, `BACKLOG-README.md`,
regenerated from backlog `.md`) conflict on every overlap even when the source
doesn't. Mark them `merge=ours` in `.gitattributes` (scoped to *exactly* the
generated globs, never authored source) so git stops reconciling them, and let
the post-merge auto-regen rebuild them authoritatively on `main`. Register the
driver: `git config merge.ours.driver true`. Note this write is **shared**, not
per-tree — `git config` without `--worktree` lands in `.git/config`, so running
it from a worktree also sets it for the canonical checkout. That is the intended
effect here (the driver is wanted repo-wide and the write is idempotent), and it
is the one sanctioned exception to §D5's "never write shared repo config": a
repo-wide setting the user would choose anyway, not a value another checkout
depends on. Setting it once in the canonical checkout is equivalent.

**D2. Two-cadence merge model — decouple lifecycle from code.** The kanban is a
*coordination surface* ("who's doing what"), not a mirror of `main`'s code.
- `building`/`qa` **stage-moves land eagerly**, independent of the code branch —
  the board stays live the moment a ticket is picked up. Since the cache-first migration the
  backlog lives in the local cache, which is outside every git checkout, so the
  move is a plain `mv` and there is **nothing to commit**; the sync (`adt
  watch`) carries it to the Issue + board within a tick. Never `git mv` a cache
  file: git walks UP from the cache to find a repo, and the one it finds is
  `$HOME`.
- The **code merges via its own PR** on its own timeline.
- **Only the `done/` move is gated on the code PR landing** (Rule 12 =
  integrated); it *follows* the code merge, never rides inside it.
- Net: board-state and code-state may differ everywhere except `done/`. This
  dissolves the "it's merged, can it go to done?" friction — `done` is *defined*
  as "the code PR landed."

**D3. Submodule pin discipline.** When a submodule (e.g. `agent-dev-team/`) is
involved: the superproject may squash-merge (the pin is a tree change, it
survives), but the **submodule's own history must NOT be squashed** (squashing
orphans the pinned SHA). **Fast-forward the submodule's `main` to the pinned
commit** post-merge so its branch contains what the superproject pins. And
**submodule/`.claude/` changes can't be partitioned to a directory** — they span
everything — so serialize them (§A.4).

**D4. Impact-tier gate — lifecycle proportional to risk.** A ticket's `track:`
field decides which gates fire, not its lane:
- `fast` — copy/doc/rename-no-callsite/config: brief → build → done, no
  separate plan/QA/reviewer.
- `standard` (default) — plan → build → qa → done.
- `full` — guarded path: no gate skipped.
- **Hard floor (non-negotiable):** a guarded path — trade/money files, schema
  migration, `ui_review_required: true` — forces `full` regardless of `track:`,
  and its path-triggered reviewers are mandatory. `track: fast` on such a brief
  is rejected. The *path* triggers the gate, not the author's read of impact.
  The PM sets `track:` at plan time (recorded once, not re-litigated per stage);
  the kanban renders no tier chip on the card — the tier drives which gates
  fire, and is read from the ticket, not from the board.

**D5. Portability + shared-config safety (open-source readiness of the portable
layer).** The SDT layer (`agent-dev-team/` + the portable scripts/hooks/rules)
must carry no embedded secrets and no hardcoded user home/host — read paths from
config / `git rev-parse --show-toplevel`. A consuming project's own private
config (its `.env`, prod-host assumptions, secrets) stays in the project layer
and is out of the portable layer's scope.

**A worktree helper must never write shared repo config.** `remote.origin.url`
lives in `.git/config`, which the canonical checkout and every worktree share —
so "cleaning" a token out of it from a new tree de-authenticates the canonical
checkout and every pipeline running there. Detect an inline-token `origin` and
**warn with remediation; never rewrite.** Not rewriting exposes nothing new: the
token is already in the shared config before the helper runs, and the new
worktree inherits it either way — so the rewrite removed no exposure the helper
created, and only broke the checkout it left behind. The remediation belongs to
the human (move auth to `gh auth` / a credential helper), which is what the
warning must say.

**`git config --worktree` is not an escape hatch for this.** With
`extensions.worktreeConfig` set it does write a per-tree value, but
`remote.origin.url` then has *two* values: `git config --get-all` returns the
shared one first, and **`git remote get-url` returns the shared one** — so the
override is inert and the token is still used (verified, git 2.50.1; the
`git-worktree(1)` caveat on worktree-scoped `remote.<name>` settings says the
same). Per-tree config is only usable for keys the remote machinery does not
read.

Origin: a consumer project shipped the rewrite; it broke the canonical checkout's
deploy cron for ~24h / ~700 failed runs before anyone noticed — the warning
scrolled past in tool output, and interactive pushes kept working through the
shell credential helper, which masked it (ADT-036).

**D6. Never create an Issue in your own project without claiming it.** Any
command that runs `gh issue create` for a *ticket* must pass `--label "adt:filing"` **on the create
call itself** — not a follow-up command. Between the Issue existing on GitHub
and its cache file existing locally, a tick of `adt watch` sees an Issue with no
local file and reconstructs a stub at `<type>/ideas/issue-<N>.md`; the playbook
then writes the real file, and one ticket has two cache files and two cards.
Nothing warns — both files are valid frontmatter (ADT-116; it hit four tickets
before anyone noticed). The label is a *claim*: the sync defers reconstruction
while it is set, and the first push after the cache file exists removes it
automatically, so there is nothing to release. `--label` on a create is what
closes the gap a follow-up command would leave; the label is idempotently
provisioned by the playbook because `--label` on an unknown label is an error.
The claim expires after an hour so an orphaned label can never strand a ticket
nothing rebuilds.
The claim is only for a session that will write that cache file, which means a
session filing into its own project's repo. A session filing into *another*
project's repo must not claim. Nothing on that side will write or push the cache
file, so the label stays on for the full hour and the ticket is missing from the
receiving board until it expires (#358, #359). File it unclaimed, with the
`type:` label and the slug trailer on the Issue, and the receiving project's
watch builds the cache file on its next tick (`/adt-brief`, "Filing into another
project's backlog"). This is a *ticket* rule — an Issue that is deliberately not a
ticket (e.g. the telemetry archive `lib/uninstall.sh` files) must not claim one.
It carries `adt:archive` instead, and the sync skips any Issue with that label
on both sides: the pull builds no cache file for it and the push never edits it
or puts it on the board. Before the label, a reinstall turned the archive into a
ticket (ADT-354).
*Carried by the playbooks themselves — ADT-280 removed the test that asserted
their wording. A project authoring its own issue-creating command carries the
rule itself.*

## E. What this replaces

This rule supersedes the former CLAUDE.md "Rule 13" (commit-and-push /
re-anchor) and the two worktree memories (`worktree_isolation_for_money_path`,
`worktree_per_task_default`). Rule 13's "re-anchor and reconcile" is no longer
needed as a behavioural tripwire because §A.3 (mandatory worktree) makes the
branch unable to move under you. A project's CLAUDE.md should point here for the
workflow and keep only its project-specific prod assumptions (host, deploy,
paths) locally.

## Acceptance proofs (the walkthroughs that prove it works)

1. **Two parallel sessions:** two worktrees each move a ticket `building → qa`
   and merge code, with **zero** stranding and **zero** conflict on generated
   HTML.
2. **Clean-clone of the portable layer:** the SDT artifact cloned by a non-owner
   with no token carries no embedded secret and no hardcoded path, and the
   worktree/branch flow runs against a generic repo.

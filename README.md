# AI Orchestrator - ADT

## What ADT is

- 5x your AI Development speed and cut the bugs your AI writes and control your costs.
- Stop your agents writing the wrong thing and over complicating your requests.
- Run multiple agents in parallel on the same project without compromising quality.
- Shrink your development team and lifecycle to the minimum.
- Give visibility to your Product Owner with access to knowledge stored in the tickets and in the code.
- Let your agents do the building while you do the thinking.

ADT guides you and your agents through a controlled process that follows
developer best practices and uses the latest multi-agent AI methods (commands,
loops, skills, tools), leaving you to add the human value.

- **Tight control of your agents at each step of the development pipeline.**
  Every work item moves through `ideas → planned → building → qa → ready-to-release →
  done` under close control.
- **Agents write clear and tight tickets.** `/adt-brief` turns an idea into a
  structured GitHub Issue with the user-facing problem and observable success
  criteria.
- **Every issue is planned in detail, with a clear Definition of Done.**
  `/adt-plan` produces a spec: a design reasoned through, an impact analysis of
  every surface the change touches, and a **machine-checkable** DoD.
- **The plan is quality-checked so the simplest solution is followed.** A
  separate-context `adt-plan-quality-reviewer` asks whether this is the right thing
  to build and the simplest thing that solves it, and a `adt-dod-coverage-reviewer`
  asks whether the DoD actually covers the spec.
- **Delivery follows the plan.** Build works the plan's sub-steps and grades
  each against the DoD it was given, not against its own account of what it did.
- **Deviations from the plan are surfaced and discussed with a human.** Work
  found mid-build is fixed in the diff or raised; there is no autonomous path for
  the LLM to ignore it as someone else's problem.
- **Delivery is quality-checked against the DoD, plus pre-release checks.**
  `/adt-qa-run` grades the delivery and diffs the work against the plan;
  `/adt-release-check` gates on all-green before anything is offered for merge.
- **Merged only with human approval.** Agents never merge silently. The gates
  pass, the agent offers, and the user decides who merges.
- **The issue is closed fully.** `/adt-close` verifies done-from-diff, stamps
  the cost, moves the card, and writes the retro. No half-closed tickets.
- **Token cost is tracked and attributed per ticket.** Every session's spend
  lands on the ticket, priced from a versioned table (assuming full token prices).
- **Retros capture what the run learned.** Each close writes one; a lesson worth
  carrying between projects is written into the playbook it changes.

**An orchestrator for your AI Development team, installed into any repo.**

ADT walks a work item through a kanban pipeline (idea → plan → build → QA →
release → done), adds a **portable behavioural layer** that makes the agent
diagnose before it fixes, and keeps the backlog in **GitHub Issues** — mirrored
to a fast local cache the agent works against, so it never waits on the API.
It is built for individual developers and small teams shipping with an agent.

You drive it by **typing `/adt-*` slash commands in Claude Code.** There is no
SaaS — each command is a playbook the agent follows when you invoke it, keyed to
the kanban lane.

ADT ships for GitHub and Claude Code today; GitLab, Jira, other agent harnesses and
models are planned for future releases.

## What you get

- **Spec-driven development.** `/adt-plan` turns a brief into a **spec**, not a
  task list: a *design* reasoned through (simplest-first, alternatives weighed),
  an *impact analysis* of every surface the change touches, and a
  **machine-checkable Definition of Done** (`done_evidence` — a command that
  exits 0, a regex on a rendered file, a red→green test; never prose). Every
  downstream stage **measures against that same DoD** — build grades its slice,
  QA grades the contract, release gates on all-green, and `/adt-build-todone`
  can drive the whole thing autonomously *because* the DoD is objective, not the
  agent's say-so.
- **A backlog that is durable and fast at once.** The source of record is
  **GitHub Issues** (the ticket id is the issue number GitHub assigns), and the
  agent works against a local mirror at
  `~/.adt/<project>/cache/<type>/<stage>/<slug>.md` with no API latency on the
  hot path. Your code repo stays code-only. Full model:
  [`docs/backlog-sync.md`](docs/backlog-sync.md).
- **"Done" means integrated.** A ticket reaches `done` only when its code PR
  landed and runs on the live path, and closing it closes the Issue.
- **Slash commands, one per lane.** `/adt-brief`, `/adt-plan`, `/adt-build`,
  `/adt-qa-run`, `/adt-release-check`, `/adt-close`, `/adt-block`, … flat in
  `commands/`. Full catalogue, by lane and loop-tagged:
  [`docs/references.md`](docs/references.md).
- **Review agents.** Isolated-context subagents returning a single verdict with
  `file:line` findings: `adt-design-reviewer`, `adt-security-reviewer`,
  `adt-critical-path-reviewer`, `adt-forensic-investigator`, and the two plan-time
  counter-checks `adt-dod-coverage-reviewer` and `adt-plan-quality-reviewer`. Summoned
  automatically on guarded paths.
- **Quality checks that are themselves calibrated.** The two plan-time
  counter-checks don't gate on their own say-so. Each carries a calibration
  record scored against fixture cases with the bar declared before any verdict
  exists, and `adt_dod.py` reads that record at runtime to decide whether it may
  refuse a plan. Un-calibrated means the reviewer still runs and its verdict is
  still recorded — only the automatic refusal is withheld.
- **Gates that fire on the *path*.** Ticket frontmatter (`track: full`,
  `ui_review_required`, `security_review_required`) mechanically decides which
  reviews are mandatory. A guarded path can't be waved through by
  an optimistic agent.
- **Loops, made explicit.** The spine is a forward-only ratchet with two
  bounded loop edges (`qa fail → building`, and `/adt-build-todone` driving an
  approved checkable plan to the merge-offer). Which commands loop vs. run once
  is tagged per-command; the reasoning is in [`docs/loops.md`](docs/loops.md).
- **Every ticket carries a dollar cost.** A `Stop` hook writes an append-only
  ledger; a versioned price table turns rows into money; `/adt-close` stamps
  `cost_usd:` onto the ticket and the Issue. ([`docs/cost.md`](docs/cost.md))
  `adt_lane_cost` takes that further and prices the lifecycle itself — what each
  lane costs, and what the gates caught for it
  ([`docs/gate-tax-report.md`](docs/gate-tax-report.md)); every figure in it is
  recomputed from committed inputs, so you can re-derive the numbers rather than
  take them. `docs/lane-overhead-review.md` walks the command path at three
  ticket sizes and ranks what to cut by the minutes it saves.
- **A dependency-free engine** in `tools/`: `adt_sync` (cache ⇄ Issues),
  `adt_watch` (background sync + board render), `ticket_serializer` (the lossless
  field-map), `build_kanban` (the renderer), `adt_dod` (the DoD grader — one
  grader shared by the `todone` loop and the done-gate), `adt_cost` +
  `adt_backfill_cost`, `adt_metrics` + `adt_xexam`, `adt_lane_cost` (what each
  lifecycle lane costs, joined from the `stage:*` label events and the ledger),
  `replay/` (pass^k),
  `calibration/` (the judge scorers), and `usage-report`.

## Install

### Prerequisites

`adt-install.sh` checks for these before it writes anything, and stops with an
install hint for each one it cannot find:

`gh` · `jq` · `yq` · `git` · `claude`

`yq` is the one that is usually missing — it ships with neither macOS nor most
Linux distributions. `python3` is needed by the tools and the watcher, and
macOS and most distributions do ship that. `gh` must also be authenticated, so
check `gh auth status` before you start; the `project` scope is not something to
arrange up front, because the installer checks for it and offers you the grant
at the point of need.

**Platforms.** The board-sync watcher (`lib/watcher.sh`) runs
on macOS as a launchd user agent and on Linux as a `systemd --user` timer.
On any other platform the installer warns, skips it, and you run `adt watch`
yourself. The preflight's install hints are written for Homebrew; on Linux
install the five tools with your own package manager.

### Clone and run it

One-time, clone the ADT repo somewhere outside your project:

```bash
git clone https://github.com/zurichrich/ai-orchestrator-adt.git ~/ai-orchestrator-adt
```

Then `cd` into the project you want ADT to run:

```bash
~/ai-orchestrator-adt/adt-install.sh
```

`adt-install.sh` infers what it can (project name, repo, branch from your git
remote), interviews you for the rest (ticket prefix, which board), and writes a
per-user config to `~/.adt/projects/<name>.yaml` — kept out of the team repo, so
your paths never enter git. Then it runs the whole setup: installs the `/adt-*`
commands + `.claude/` defaults, creates the GitHub labels + Projects board,
**adopts your existing issues into the cache**, and renders the board. When it
finishes it prints, and writes into `.adt/BACKLOG.md`, the links to
your **GitHub board** and your local **`kanban.html`**.

**A second machine, or a teammate, joins the install.** Run the same command in
their clone. Before it asks anything, the installer reads the ADT layer
committed on `origin/<main>`. If ADT is already installed there, it says so,
takes the project name, repo, main branch, prefix and board from the committed
`.claude/adt-project.yaml`, and sets up that machine's config, cache and
watcher. It finds the existing board by its committed title rather than creating
a second one, and it never writes the shared `.claude/` layer into the working tree,
so a checkout that is behind `main` just needs a `git pull`. If that machine's
ADT clone is older than the committed layer, the installer stops and prints the
`git pull` for the clone. The first install writes `.claude/adt-project.yaml`;
commit it with the rest of `.claude/`.

**Anonymous usage stats are on by default** — a random install id, the ADT
version, your OS, daily counts, and what your token ledger cost in dollars
(priced on your machine; the model names and per-row detail never leave it),
sent daily and as one last report when you uninstall; never prompts, code,
paths, ticket titles or repo names. `DO_NOT_TRACK=1` or `.adt/state/telemetry-off`
turns them off ([`docs/security-posture.md`](docs/security-posture.md)).

**Prerequisites:** You do **not** need to arrange the `project` scope yourself:
the installer checks for it up front and, when it's missing, **offers the grant
at the point of need** and continues once you approve. 

Then, in Claude Code from your project:

```text
/adt-brief     # capture your first idea
```

**Updating an existing install — re-run the installer.** From your project:

```bash
git -C ~/ai-orchestrator-adt pull && ~/ai-orchestrator-adt/adt-install.sh
```

There is no separate `update` command. Re-running it picks up **new** ADT files (a freshly added hook, command,
rule, or skill), not just changed ones: every install step is a directory scan,
not a fixed list, so a new artifact in `defaults/` is propagated on the next run.
All surfaces are **copies**, not symlinks: update overwrites each ADT-managed copy with
the new snapshot, removes files ADT no longer ships, and **preserves any copy you
edited**. Run it after every `git pull` of the ADT repo.

**The upgrade lands on a branch.** The `.claude/` copies are committed, so an
upgrade changes them for everyone who pulls. Once they are on `origin/<main>`,
a re-run from a newer ADT clone does not write them into your working tree. It
commits them to a local `chore/adt-upgrade-<sha>` branch, prints the push
command, and lists the machines whose last report shows an older ADT. Those
machines need `git pull` in their ADT clone after you merge it. A re-run from
the same ADT version writes nothing.

What a re-run touches, beyond the copied files:

- **`.adt/config.yaml` is rewritten every time.** It is generated from
  `~/.adt/projects/<name>.yaml`, so put your own settings (such as
  `worktree_guard_paths`) in that file. A line typed into `.adt/config.yaml` is
  lost at the next install.
- **GitHub and the watcher.** A full run also re-applies labels and the board,
  pulls Issues into the cache, renders the board and reinstalls the watcher
  (on macOS, only when its plist would change). That spends GitHub API calls.
  `adt-install.sh --no-github` skips those steps and is enough when the ADT
  change you pulled does not touch labels, the board or the watcher. When you
  are not sure, run the full install.
- **A copy you edited is not updated.** The install prints
  `keeping your edited copy of <file>` and moves on, so that file stays on the
  old version. To take the new one, delete the file and re-run.
- **Which machine runs which ADT.** Each machine's watcher reports its ADT
  version and commit once a day on a closed `adt:install` Issue, and the board's
  footer lists every machine that reported in the last 7 days. The installer
  compares the committed layer with your ADT clone for you.

**Uninstall is the exact inverse — and safe to repeat.** From your project:

```bash
~/ai-orchestrator-adt/adt-install.sh --uninstall
```

It archives the local cost ledger (`.adt-state/cost-ledger.log`) into a single GitHub issue (then
deletes the local copy), removes the `.claude/` ADT copies recorded in
`.adt-manifest.json` (keeping any copy you edited) + the hook entries, removes the
generated configs, and deletes the regenerable `.adt/` working files. Your `docs/decisions.md` and
`docs/retros/` are tracked in git and are never touched.
It **never** touches your GitHub Issues, labels, or board, or your ticket cache
(`~/.adt/<name>/cache`).

**On a repo where ADT is committed, uninstall removes only your machine.** The
`.claude/` copies, the hooks in `settings.json`, the `CLAUDE.md` block and
`.gitignore` block are committed, so they belong to everyone who pulls the
repo. Once they are on `origin/<main>`, `--uninstall` removes this machine's
watcher, `.adt/` folder and config and leaves those files alone, and says so.
To take ADT out of the repo for every machine:

```bash
~/ai-orchestrator-adt/adt-install.sh --uninstall --everyone
```

It uninstalls this machine the same way, and commits the removal of the
committed files to a local `chore/adt-uninstall` branch for you to push and
merge. It also lists the machines whose watchers are still reporting; each of
them runs `--uninstall` to stop its own. On a repo where ADT was never
committed, `--uninstall` already removes everything, with or without
`--everyone`.

> **Your ADRs and retros live in `docs/` and are committed.** Uninstall removes
> the whole `.adt/` folder — it holds only generated working state — and never
> touches `docs/`. `decision_log:` defaults to `docs/decisions.md`; point it
> elsewhere if your project keeps ADRs somewhere else, and ADT reads and appends
> there instead. Retros are written to `docs/retros/` by `/adt-close`. So
> install → uninstall → install loses nothing: the cache rebuilds from Issues on
> the next `--pull`, and your authored docs were never in ADT's delete path.
>
> Before ADT first tracks a path it previously ignored, it scans those files for
> secret-shaped content and asks you to confirm. Retros are postmortems, so read
> them once before agreeing.


## How it works with GitHub

ADT treats GitHub as both the **merge/review surface** (for code) and the
**durable backlog store** (Issues).

**The backlog is GitHub Issues**, with a fast local cache and a background
reconciler so sessions never pay API latency on the hot path. Full model — the
cache, the two-way sync (`adt watch`), the field-map, the id-from-issue-number
rule, and the tools — is documented in **[`docs/backlog-sync.md`](docs/backlog-sync.md)**.
In short: Issues are the source of truth; `~/.adt/<project>/cache/` is the
working copy; `adt watch` keeps them in lockstep and renders the board; the
code repo stays code-only.

For the **code** path:
- **Branch-per-ticket, PR-per-merge.** Agents never commit to `main`, and
  the installer offers to set server-side branch protection that works
  without CI, applied only if you say yes, and the board's header tells you
  which state your repo is actually in. Each session writes
  in its own **git worktree** so parallel agents can't clobber each other's
  files.
- **Two-cadence.** A stage-move is a cache write that `adt watch` syncs to the
  Issue's label + Project column, independent of code; only the `done` close
  follows the code PR. Board-state and code-state may differ anywhere except
  `done`.

## How it works with your repo

ADT lives in **its own checkout, outside your project** (clone it once, then run
`adt-install.sh` from your project — see Install). Your project gets only the
installed `.claude/` layer + a generated config; the backlog lives in GitHub
Issues + a cache outside the checkout:

```
your-repo/                     # CODE ONLY — no backlog tree
  .claude/                     # rules/skills/agents/hooks/commands/tools copied from ~/ai-orchestrator-adt,
                               #   listed in .claude/.adt-manifest.json so update/uninstall act on
                               #   exactly what was written; your own additions live here too
  .adt/config.yaml             # repo/owner/cache_dir (gitignored, machine-local)
  .adt/                        # config.yaml, state/, inbox/, rendered board (gitignored by ADT)
  docs/decisions.md            # your ADRs — committed, and untouched by uninstall
  docs/retros/                 # your postmortems — likewise

~/ai-orchestrator-adt/         # ADT itself — playbooks, roles, defaults, tools
~/.adt/projects/<name>.yaml    # YOUR project config (generated by adt-install.sh)
~/.adt/<project>/cache/        # YOUR tickets: <type>/<stage>/<slug>.md
                               # (outside the checkout; synced to GitHub Issues)
```

Editing a playbook in your ADT checkout makes it live in your project's next
command, so using ADT and improving it happen in the same place. You can also
vendor ADT as a **git submodule** and pin a SHA.

**In production:** ADT runs daily against a private project.

## Safety rails

- Agents work on branches in worktrees and **never merge silently**. Once the
  gates pass, the agent offers and the human decides who merges.
- `/adt-block` exists for any decision an agent shouldn't make alone: it parks
  the item and emails the human; it does not guess.
- Agents never start long-running production processes.
- Review gates are path-triggered and non-negotiable: money-path code, schema
  migrations and user-visible UI each summon their reviewer, however small the
  diff looks.

### Two rules enforced by hooks

Two rules are enforced by hooks and tests. **Verify before asserting**: a claim
about the system ("the tests pass") must trace to something the session
actually ran in that turn, matched by `phrase-linter.sh` against the command
that ran. **No casual deferral**: filing a follow-on ticket mid-build needs
unforeseen + significant + human-approved, and there is no autonomous path to
it: `deferral-guard.sh` denies an unauthorised Issue-creating
call, reading the authorisation only from a `role: user` turn, because every
other signal is agent-writable. Work found mid-build gets fixed in the diff.

Full model, the calibration records, and the baselines:
**[`docs/self-verification.md`](docs/self-verification.md)**.

## What the work cost — tokens and dollars, per ticket

Every session's `Stop` appends a row to a machine-local, gitignored ledger.
`tools/adt_cost.py` is the only place a row becomes money, from a versioned
per-model table in `defaults/pricing.json`. A stamped cost records which price
version priced it, so a rate correction never silently rewrites history.

Every number says how it was reached (`measured`, `estimated`, `legacy`), and
anything not fully measured renders with a `~`. A ticket with no rows renders
`$ —`, never `$0.00`: "we didn't capture it" and "it was free" are different
facts. The board shows a per-card cost and a running total across the cards
currently visible. Full model: **[`docs/cost.md`](docs/cost.md)**.

## Install from a release

Releases are versioned (`VERSION` + a semver tag) and carry the advisory
manifest ADT checks against. To pin a release rather than track `main`:

```bash
git clone --branch v0.2.0 --depth 1 https://github.com/zurichrich/ai-orchestrator-adt.git ~/ai-orchestrator-adt
```

Then, from your project:

```bash
~/ai-orchestrator-adt/adt-install.sh
```

The installer copies the playbooks, rules, agents and hooks into your project's
`.claude/`, records a `{path, sha256}` manifest so update and uninstall act on
exactly what was written, and stamps the bundle version into that manifest.

## Licence

**Apache License 2.0**, the whole repository. No split, no reserved use, no
commercial tier. Run it, fork it, modify it, ship it in production, build a
product on it — the only obligations are Apache-2.0's own: keep the notices,
state what you changed, and don't use the project's name to imply endorsement.

See `LICENSING.md` for what that means in practice and why the earlier
MIT/BSL split was withdrawn.

Contributions require the CLA in `CLA.md` — sole copyright is what keeps future
versions relicensable, and it is the only thing the CLA is for. Security
contact: `SECURITY.md`. What ADT does and does not send over the network:
`docs/security-posture.md`.

See `operating-model.md` for the full workflow and `CLAUDE.md` for the
context any Claude session opening this repo should read.

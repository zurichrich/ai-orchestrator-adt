# Surface review — commands, agents, hooks, skills (ADT-214)

Reviewed 2026-09-05 against `ead1300`; reconciled and removals applied 2026-09-06. One verdict per surface on disk:
**keep** (earns its place as-is), **integrate better** (used or useful, but
unreachable/undocumented/miswired), **drop** (no evidence of use and nothing
depends on it).

`tests/test_surface_catalogue.py::test_review_doc_classifies_every_surface`
holds this file to every surface on disk by *identity*, so a surface cannot be
quietly skipped and a stale row cannot cancel a missing one.

## Method, and what each number is worth

| Evidence | How it was produced | What it proves |
|---|---|---|
| `typed=` | `awk -F'\t' '{print $2}' .adt/state/usage.log \| sort \| uniq -c` | direct slash-command invocations only |
| `dispatches=` | `subagent_type` counted over `tool_use` records in `~/.claude/projects/<project>/*.jsonl` | agent runs, however invoked |
| `playbooks=` | `grep -l <name> commands/*.md` | how many playbooks reference it |
| wiring | parsed from `defaults/settings.hooks.json` | what the harness actually fires |

Three caveats, because each one changes how a row should be read:

1. **`typed=0` is not "unused".** `adt-usage-log.sh` records only slash-command
   tokens at `UserPromptSubmit`. A reviewer dispatched from inside a playbook
   never appears — `adt-design-reviewer` has 6 dispatches while `/adt-review-designer`
   has `typed=0`.
2. **`usage.log` is a weak narrator.** It contains entries that are not commands
   at all (`adt-dod`, `adt-mark-tix`, `adt-token-log`, `adt-wt-*`), so its counts
   are a lower bound with noise, not a census.
3. **Transcript counts are a floor.** Transcripts are Claude Code's internal
   store — prunable, per-machine, not a stable interface. This is precisely why
   phase 4 gives agent/skill dispatch a durable home.

## Commands (14)

| Surface | Evidence | Verdict |
|---|---|---|
| `/adt-plan` | typed=30, playbooks=8; dispatches both plan-time counter-checks | keep |
| `/adt-brief` | typed=28, playbooks=2; the only sanctioned filing path under ENFORCED RULE 2 | keep |
| `/adt-close` | typed=27, playbooks=3 | keep |
| `/adt-build` | typed=20, playbooks=7 | keep |
| `/adt-qa-run` | typed=19, playbooks=3 | keep |
| `/adt-build-todone` | typed=15, playbooks=2 | keep |
| `/adt-release-check` | typed=11, playbooks=2; enforces the hard-floor reviewer gate | keep |
| `/adt-plan-fasttrack` | typed=6, playbooks=1 | keep |
| `/adt-review-critical-path` | typed=3, agent dispatched 3, playbooks=2; `release-check.md` gate 8 requires its verdict before any merge-offer. Was in no lane header — now in ready-to-release | keep |
| `/adt-decide` | typed=2 understates it: six ADRs (001-006) are cited 116 times across the backlog. Cross-cutting, so no lane owns it — recorded in BOARD_EXEMPT with its reason rather than forced into one | keep |
| `/adt-review-security` | typed=1, agent dispatched 10 via plan.md and release-check.md | keep |
| `/adt-block` | typed=1, playbooks=9 — the most-referenced command in the repo; value is availability, not frequency | keep |
| `/adt-review-designer` | typed=0 but agent dispatched 6 — used from inside playbooks. `ui_review_required: true` forces track:full and needs a manual summon. Was in no lane header — now in qa | keep |
| `/adt-unblock` | typed=0, playbooks=1 — the counterpart to `/adt-block`; an escape hatch whose value is not frequency | keep |

## Review agents (6)

| Surface | Evidence | Verdict |
|---|---|---|
| `adt-dod-coverage-reviewer` | dispatches=50, playbooks=3; `adt_dod.py --gate` refuses a plan without its verdict | keep |
| `adt-plan-quality-reviewer` | dispatches=37, playbooks=3; caught this ticket's own mis-tiering | keep |
| `adt-security-reviewer` | dispatches=10, playbooks=3 | keep |
| `adt-design-reviewer` | dispatches=6, playbooks=1 | keep |
| `adt-critical-path-reviewer` | dispatches=3, playbooks=2; mandatory on a guarded path | keep |
| `adt-forensic-investigator` | dispatches=0, playbooks=0 — but NOT unwired: `defaults/skills/adt-diagnose/SKILL.md:54-58` carries a real `Agent({subagent_type: "adt-forensic-investigator"})` block with a stated trigger. 0 dispatches reflects a rarely-typed human-only skill and rare escalation, not a dead wire | keep |

## Hooks (13)

Classified by what `defaults/settings.hooks.json` actually wires, not by what
the catalogue claims: **6 wired**, **1 template**, **6 helpers**.

| Surface | Evidence | Verdict |
|---|---|---|
| `adt-close-complete` | wired Stop; the close-path terminal-state gate (ADT-336) | keep |
| `adt-done-guard` | wired PreToolUse; 267 candidate-done-actions logged; the done gate | keep |
| `adt-deferral-guard` | wired PreToolUse; 44 decisions logged, 18 authorised via `/adt-brief`, 8 by a human turn | keep |
| `adt-test-run-guard` | wired PreToolUse; new (ADT-264 follow-up) — no decisions logged yet, so this verdict is a design claim and not yet an observation | keep |
| `adt-destructive-git-guard` | wired PreToolUse; new (ADT-359) — no decisions logged yet, so this verdict is a design claim and not yet an observation | keep |
| `adt-worktree-guard` | wired PreToolUse; new (ADT-359), inert until a project sets `worktree_guard_paths:` — a design claim, not yet an observation | keep |
| `adt-phrase-linter` | wired Stop; ENFORCED RULE 1 claim-side gate | keep |
| `adt-token-log` | wired Stop; writes the cost ledger `/adt-close` stamps from | keep |
| `adt-subagent-cost` | wired SubagentStop; bills a subagent's tokens to the dispatching ticket, without which the ledger is blind to the gates ADT charges for | keep |
| `adt-terminal-title` | wired SessionStart, UserPromptSubmit, Stop | keep |
| `adt-usage-log` | wired UserPromptSubmit, but its log contains non-command entries, so its counts are noisy — the attribution half is load-bearing, the analytics half is not trustworthy as-is | integrate better |
| `adt-dod` | helper; referenced by 6 playbooks; the DoD grader every gate calls — and absent from the catalogue until this ticket | keep |
| `adt-mark-tix` | helper; referenced by 6 playbooks; the pickup binding writer — absent from the catalogue until this ticket | keep |
| `adt-verify-bind` | helper; referenced by 6 playbooks; the fail-closed half of the binding pair — absent from the catalogue until this ticket | keep |
| `adt-token-total` | helper; referenced by 1 playbook (`/adt-close`); cross-machine cost total | keep |
| `adt-token-sum` | helper; referenced by 0 playbooks, but composed by `adt-token-total` by contract | keep |
| `adt-reattribute` | helper; referenced by 0 playbooks; a manual one-shot ledger repair tool with no documented entry point | integrate better |
| `adt-deploy-guard` | template; deliberately unwired — a project copies it, fills in its patterns and wires it in its own settings | keep |

## Skills (1)

| Surface | Evidence | Verdict |
|---|---|---|
| `/adt-diagnose` | typed=1; correctly typed as a skill and rendered purple on the board. `disable-model-invocation: true` is deliberate — a human-typed discipline tool, so a low typed count is the expected shape. It carries a real `adt-forensic-investigator` dispatch block at SKILL.md:54-58, contrary to this review's first draft | keep |

## What the verdicts add up to

**Keep: 32. Integrate better: 2. Removed: 3** (of 34 surfaces remaining: 14 commands, 6 agents, 13 hooks, 1 skill).

### Removed 2026-09-06, on the human's decision

- `/adt-triage` — typed=0, referenced by no playbook; cold on both halves.
- `ticket-triage` — dispatches=0; its only caller was `/adt-triage`, removed above.
- `/adt-prioritise` — typed=0, referenced by nothing, though board-surfaced.

Removal was in scope without a re-plan: install is glob-driven
(`for f in "$DEF"/agents/*.md`, `commands/*.md`), so deleting a file
de-installs it; no `tools/` or `lib/` code named any of them; `adt-usage-log` matches
the `/adt-*` prefix generically and `adt-deferral-guard` matches the literal
`/adt-brief`. The only hardcoded references were `STAGE_TOOLS`,
`operating-model.md` and `defaults/rules/multi-agent-git-workflow.md` — all three
reconciled in the same diff.

**`adt-forensic-investigator` was approved for removal and then NOT removed.** The
approval rested on this review stating it was referenced "in prose only". That
was wrong: `defaults/skills/adt-diagnose/SKILL.md:54-58` carries a real dispatch
block. Since `/adt-diagnose` is kept, removing its escalation target would have
degraded a surface this review keeps. Corrected to **keep**, and the correction
was surfaced rather than acted on silently.

The lane-header gaps this review found are now closed rather than merely noted:
`/adt-review-critical-path` sits in ready-to-release, `/adt-review-designer` in
qa, and `/adt-decide` — genuinely cross-cutting, so no lane owns it — is recorded
in `BOARD_EXEMPT` with its reason. Those three moved from *integrate better* to
*keep* because the integration is done, not because the verdict softened.

Two **integrate better** verdicts remain, both documentation rather than wiring:
`adt-usage-log` writes a log containing entries that are not commands, so its
analytics half is not trustworthy as a census even though its attribution half is
load-bearing; and `adt-reattribute` had no catalogue row and no documented entry
point, which it now has.

One finding this review surfaces without acting on it: six ADRs (001-006) are
cited **116 times** across the backlog, but `/adt-decide` writes to
`docs/decisions.md`, which does not exist — and `.adt/`
is gitignored, so even once written it would be per-machine and unshared.

Nothing here proposes new work beyond the reconciliation phase 3 already owns.

---

## Appendix — overlap with Claude Code's built-in commands (2026-09-09)

Reviewed against Claude Code v2.1.266 (the version installed on this machine)
and the built-in command table at `code.claude.com/docs/en/commands`.

This appendix asks a different question from the review above. The review asks
whether a surface earns its place on disk; this asks whether the harness already
ships it. No verdict above changes — every surface here is still a keep. The
point is to record where ADT carries a re-implementation, so a future session
knows it is choosing to and not doing it by accident.

Caveat on the built-in side: the bundled skills vary by version and by what an
install exposes. `/verify` and `/batch` are documented but did not appear in this
session's model-invocable skill list, so check `/help` before depending on either
pairing.

### Four collisions

| ADT surface | Built-in | Relationship | What only ADT has |
|---|---|---|---|
| `/adt-review-security` | `/security-review` | duplicate | project incident classes, the plan-time threat model, and a verdict shaped so `/adt-release-check` can read it as a gate |
| `/adt-qa-run` | `/verify` | duplicate | the diff-versus-plan comparison and the `qa fail → building` lane move |
| `/adt-build`'s self-review step | `/code-review` (alias `/review`), `/simplify` | duplicate | nothing. The built-in adds effort levels, PR/branch/path targeting, `--fix` and `--comment`, none of which the hand-rolled step has |
| `/adt-review-critical-path` | `/code-review high\|xhigh\|max` | partial | the guarded-path hard floor — the path triggers the reviewer, not the author's read of impact |

### Four weaker resemblances

Pairs that look alike and are not. Recorded so a later review does not mistake
them for collisions.

- `/adt-build-todone` and `/goal`. `/goal` also works across turns until a
  condition is met, but the model judges the condition. ADT grades
  `done_evidence:` through `adt-dod`. That difference is the command.
- `/adt-plan` and `/plan`. `/plan` enters plan mode and persists nothing past
  the session. `/adt-plan` writes the spec into the ticket and moves the file
  ideas→planned.
- `/adt-build` and `/batch`. `/batch` splits work into 5-30 units, one
  background subagent per unit in its own git worktree, one PR each — the same
  worktree isolation as `multi-agent-git-workflow.md` §A.3, applied to fan-out
  rather than to one gated ticket.
- `/adt-diagnose` and `/debug`. Unrelated despite the name. `/debug` turns on
  session debug logging and reads the log back.

### Eight surfaces with no built-in counterpart

`/adt-brief`, `/adt-plan-fasttrack`, `/adt-release-check`, `/adt-close`,
`/adt-block`, `/adt-unblock`, `/adt-decide`, `/adt-review-designer` — plus the
cache, the Issue↔file sync and the kanban render. Nothing in the harness models
a ticket that has a lane, a `track:`, and a transition a hook can refuse.

Built-ins that overlap ADT's supporting tooling rather than its commands:
`/worktree` (view and delete worktrees Claude created), `/doctor` (finds unused
skills, offers to trim a bloated `CLAUDE.md`), `/fewer-permission-prompts`,
`/skill-doctor`.

### How Claude handles GitHub Issues without ADT

There is no built-in issue command, and its absence is a decision:
`/pr-comments` existed and was removed in v2.1.91, and its entry now reads "Ask
Claude directly to view pull request comments instead." Issues are handled the
same way, in three places:

1. **Locally** — `gh` through Bash. An issue is a prompt source: read it, do the
   work, maybe close it. Nothing tracks a stage, gates a transition, or mirrors
   the issue to a local file.
2. **In the repo's CI** — the Claude GitHub App plus
   `anthropics/claude-code-action`, installed by `/install-github-app`, holding
   Issues read-and-write. Interactive mode fires on `@claude` in an issue body, a
   new issue's title, an issue comment or a PR review comment; Claude replies as
   a comment on that issue and can push commits and open a PR. Automation mode
   takes a `prompt` input and runs on any event including cron. Both require the
   triggering user to have write access and to not be a bot.
3. **In the cloud** — `/autofix-pr` spawns a Claude Code on the web session that
   watches the current branch's PR and pushes fixes when CI fails or a reviewer
   comments.

The difference from ADT is the shape of the state. Claude treats an issue as a
conversation: a thread it is mentioned in, answered in that thread, with the work
happening in a PR. ADT treats it as a state machine: frontmatter carries
`track:`, `plan_approved:` and `done_evidence:`, the lane is a directory, and a
hook can refuse the move. That is why only the four review-and-verify commands
collide.

One billing trap if a built-in is ever wired into an ADT flow. ADT talks to
GitHub through `gh api repos/…` (CLAUDE.md rule 5) to stay off the 5000-point
GraphQL pool that `gh pr` / `gh issue` porcelain bills. The built-in paths do
not: `/autofix-pr` detects its PR with `gh pr view`, and the Actions workflows
use the GitHub MCP tools. ADT-109 is the ticket that pool exhaustion was filed
under.

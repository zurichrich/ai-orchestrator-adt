---
name: adt-review-critical-path
description: Hand the staged diff to the adt-critical-path-reviewer subagent
adt-budget: 50
---

# /adt-review-critical-path

Run this after any change to the project's **critical path**: the money,
safety or data-integrity code where a bug is expensive or irreversible.
`/adt-build`'s hard floor makes this review mandatory on such a diff, and this
command runs it. Setting `track:` cannot skip it on a guarded-path diff, because
the path triggers the gate whatever tier the author chose.

## Steps

1. Delegate to the `adt-critical-path-reviewer` subagent:
   ```
   Agent({subagent_type: "adt-critical-path-reviewer",
          description: "Critical-path review of staged diff",
          prompt: "Review the staged diff (git diff --cached, else main...HEAD)
                   against the project's critical-path invariants and known
                   incident classes. Return blocker/nit findings with exact
                   file:line citations."})
   ```
2. Paste its verdict into a `## Critical-path review` section of the backlog file.
3. **Any blocker:** fix it and re-run, or `/adt-block`. Don't ship.

Each project sets the reviewer's scope and invariants in
`.claude/agents/adt-critical-path-reviewer.md`.

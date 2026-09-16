---
adt_managed: ADT-087
name: adt-decide
description: Record an architecture decision (ADR) in decisions.md
adt-budget: 60
---

# /adt-decide

Record a judgement call you would want to re-read in six months.

**Worth recording:** "chose strategy A over B because Z"; "one setting per user
rather than per portfolio"; "kept the inline calculation rather than
abstracting it".
**Not worth recording:** naming, where a test file lives, a list comprehension.

## Steps

1. **Find the next ADR number.** The log's path is the `decision_log` field in
   `~/.adt/projects/<project>.yaml`. The default is `docs/decisions.md`, and a
   project may point it elsewhere. If the file doesn't exist, create it. ADT
   does not create one in advance.

2. **Append the ADR:**

   ```markdown
   ## ADR-NNN — <title, decision-shaped>
   **Date:** YYYY-MM-DD
   **Decided by:** <operator / user — name if the user>
   **Triggered by:** <feature slug, or "ad-hoc">
   **Context:** <what situation forced the decision>
   **Decision:** <what was chosen>
   **Rationale:** <why this option and not the alternatives>
   **Implications:** <what changes downstream>
   **Reversibility:** Low | Medium | High — <cost to reverse, one sentence>
   ```

3. **Log it in the ticket:**

   ```
   ## YYYY-MM-DD HH:MM — decide
   [decide] ADR-NNN recorded: <title>
   ```

4. **Do not commit.** This command is called from inside a stage command, and
   that stage command makes the commit. Where the decision log is gitignored
   (ADT's own is), there is nothing to stage.

<!-- adt-bundle: v0.2.0 -->

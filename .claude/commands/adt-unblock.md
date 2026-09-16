---
adt_managed: ADT-087
name: adt-unblock
description: Record the user's answer to a blocked ticket and move it back to its previous folder
adt-budget: 60
---

# /adt-unblock

Resolves a ticket that `/adt-block` parked while it waited for the user.

## Steps

1. **Pick the ticket.** Use `<slug>` if given. Otherwise list
   `~/.adt/<project>/cache/<type>/blocked/` and ask which one. Read it.

2. **Print the `## Blocked` section**: the question, the reason, the
   recommended default, and who blocked it.

3. **Ask the user, and record the answer word for word.** Their words are the
   record, so do not paraphrase them into the resolution.

4. **Append the resolution:**

   ```
   ## Resolution
   **Date:** YYYY-MM-DD HH:MM
   **Answer:** <the user's answer, verbatim>
   **ADR:** <ADR-NNN if /adt-decide was called, else "none">
   ```

5. **Move the file back to its previous stage.** The previous stage is the
   ticket's `blocked_from:` frontmatter. If that is missing, use whichever of
   `planned` / `building` / `qa` / `ready-to-release` it was in. Move it to
   `~/.adt/<project>/cache/<type>/<prior stage>/<slug>.md`. Use a plain `mv`,
   never `git mv`. There is nothing to commit, and `adt watch` carries the stage.

6. **Then set** `stage: <prior>`, `strike_count: 0`, `last_blocker: null`. Move
   first: the render treats the folder as the truth, so a stage set while the
   file is still in `blocked/` is rewritten back to `blocked`, and the next sync
   pushes that.

7. **Log it:**

   ```
   ## YYYY-MM-DD HH:MM — po (<your-name>)
   [resume] Unblocked <slug>: <answer>. File → <prior stage>/.
   ```

8. **If the answer is a real judgement call, run `/adt-decide`** so it can be
   found in six months, not only in this ticket.

<!-- adt-bundle: v0.1.0 -->

---
name: adt-block
description: Pause work on a ticket, write down the question, move it to blocked/, and email the user
adt-budget: 70
---

# /adt-block

Use this when you hit a question **only the user can answer**: a UI/UX call,
scope, an external service, or design intent that is genuinely ambiguous.

Do not use it for technical decisions you can make yourself. If the plan is
unclear about *intent*, block. If it is unclear about *implementation*, decide
and carry on.

**Always propose an answer when you block.** Bring the question and the default
you would take if no answer came.

## Steps

1. **Append a `## Blocked` section** to the backlog file:

   ```
   ## YYYY-MM-DD HH:MM — BLOCKED
   Feature: <slug>
   Question: <one-line question>
   Reason: <why you can't decide>
   Recommended default: <what you'd do if no answer comes>
   ```

2. **Move the file** to
   `~/.adt/<project>/cache/<type>/blocked/<slug>.md`, then set `stage: blocked`
   in it. Use a plain `mv`, never `git mv`. There is **nothing to commit**. The
   cache lives outside every git checkout, so `git` would walk up the directory
   tree and find `$HOME`. `adt watch` carries the new stage to the Issue and the
   board. Move first: the render treats the folder as the truth, so a stage set
   before the move is rewritten back to the old lane, and the next sync pushes
   that.

3. **Email the user:**

   ```bash
   "$ADT_DIR/lib/notify.sh" block <project> <slug> \
     "BLOCKED: <slug>" \
     "Need your input on <slug>.

     Question: <question>
     Recommended default: <default>

     Unblock: run /adt-unblock <slug>"
   ```

4. **Exit cleanly.** Push any finished commits on the ticket's branch. Never push
   half-finished code.

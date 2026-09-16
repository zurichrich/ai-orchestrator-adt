---
adt_managed: ADT-087
name: adt-review-security
description: Send the staged diff to the adt-security-reviewer subagent
adt-budget: 50
---

# /adt-review-security

Run this after any change that touches auth, secrets, user-data writes, external
APIs, or dependencies.

## Steps

0. **Typing `/adt-review-security` is the confirmation.** Every other dispatch of
   this subagent must stop and ask first (see `/adt-plan` step 1), because it is
   the most expensive step in the lane. Here the human asked for it by name, so
   do not ask again. Say in one line what it will review and what it costs, then
   run it.

1. Hand the review to the `adt-security-reviewer` subagent. It applies the OWASP
   top-10 checks, auth, RLS, a dependency audit, a secret scan and the project's
   own incident classes, and returns one verdict:
   ```
   Agent({subagent_type: "adt-security-reviewer",
          description: "Security review of staged diff",
          prompt: "Review the staged diff (git diff --cached, else main...HEAD).
                   Return APPROVE / APPROVE-WITH-FIXES / REJECT with file:line
                   findings."})
   ```
2. Paste its verdict into a `## Security review` section of the backlog file.
3. **On REJECT or any HIGH finding**, fix it and re-run, or use `/adt-block` to
   ask the user. Do not ship.

The same agent reviews the *plan* for threats at plan time (see `/adt-plan`),
and the release gate runs it again on the final diff (see `/adt-release-check`).

<!-- adt-bundle: v0.1.0 -->

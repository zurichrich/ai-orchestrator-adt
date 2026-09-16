---
adt_managed: ADT-087
name: adt-review-designer
description: Send the staged UI diff to the adt-design-reviewer subagent
adt-budget: 40
---

# /adt-review-designer

Use this after frontend changes, before opening the PR (during the self-review
step of `/adt-build`).

## Steps

1. Hand the review to the `adt-design-reviewer` subagent. It reviews the staged
   UI diff at desktop, tablet and mobile widths, in dark mode, against the theme
   tokens and for responsive behaviour, and returns one verdict:
   ```
   Agent({subagent_type: "adt-design-reviewer",
          description: "UI review of staged diff",
          prompt: "Review the staged UI diff across viewports, dark mode, theme
                   tokens, and responsive behaviour. Return APPROVE /
                   APPROVE-WITH-FIXES / REJECT with file:line findings."})
   ```
2. Paste its verdict into a `## Design review` section of the backlog file.
3. If it lists fixes you must make, make them, then re-run the build self-review.
4. If you disagree with a finding marked "must fix", use `/adt-block` to ask the
   user.

<!-- adt-bundle: v0.2.0 -->

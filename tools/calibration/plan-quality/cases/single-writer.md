---
id: CALP-4
title: /adt-brief should create the ticket's Issue as it files the brief
---
# Brief creates the Issue
## Problem (user)
Filing a brief leaves a local cache file and no Issue until the next sync, so
the ticket is invisible on GitHub for minutes.
## Plan (PM)
### Problem & goal
Make the Issue appear when the brief is filed.
### Design
- **Approach:** `/adt-brief` writes the cache `.md`, then calls the Issue-creating
  API directly and writes the returned number back into the file's frontmatter as
  `issue_number:`. Two steps, in that order, in the playbook.
- **Why this approach:** the simplest thing — no new state, no queue, no lock;
  the playbook already knows both halves.
### Sub-steps
- 1a — create the Issue from the playbook after writing the file
- 1b — write `issue_number:` back into the frontmatter
### Risks
- An API failure leaves a file with no Issue; the next sync reconciles it.

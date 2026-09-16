---
id: CALP-2
title: Deferring work must require a human's approval
---
# No casual deferral
## Problem (user)
The agent files a follow-on ticket for work it should have finished, and the
ticket then grades green with the job unfinished. Filing must not be something
the agent can decide for itself.
## Plan (PM)
### Problem & goal
Make the deferral un-self-approvable.
### Design
- **Approach:** a `PreToolUse` hook intercepts the Issue-creating tool call and
  denies it unless the session is authorised. Authorisation is recorded by
  writing the token into the session's active-ticket marker file under
  `development-team/.adt-state/current-tix.d/<session>`, which the playbook sets
  when the human approves. The hook reads that marker and allows the call.
- **Why this approach:** the marker already exists for token attribution, so no
  new state is introduced — the simplest thing that could work.
### Sub-steps
- 1a — the hook and its matcher
- 1b — read the marker and decide
- 1c — the playbook writes the marker on approval
### Risks
- A stale marker could authorise a later call; markers are per-session.

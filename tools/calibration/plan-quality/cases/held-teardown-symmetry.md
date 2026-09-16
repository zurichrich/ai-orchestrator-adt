---
id: CALP-9
title: /adt-close should finish the ticket lifecycle
---
# Close the ticket properly
## Problem (user)
When a ticket closes, the repo is left littered. I still find branches and
worktrees from tickets that merged weeks ago, and I have to clean them up by
hand. Closing should leave nothing behind.
## Plan (PM)
### Problem & goal
Make `/adt-close` complete the lifecycle so the human is not left tidying up.
### Design
- **Approach:** at close, stamp the ticket's final cost into frontmatter, move the
  backlog file from `ready-to-release/` to `done/`, set `state: closed`, and let
  the sync reconcile the Issue and the board column. Then write the retro if one
  is warranted.
- **Why this approach:** the close is a bookkeeping step and the sync already
  owns the GitHub side, so the simplest thing is to move the file and stamp the
  state; nothing else is needed.
### Sub-steps
- 1a — stamp cost into frontmatter
- 1b — move the file to `done/` and set `state: closed`
- 1c — write the retro when warranted
### Test plan
- Unit tests: the stamp lands in frontmatter; the move sets the right stage.
### Risks
- A close on a ticket that never reached release would move it early; the command
  checks the stage first.

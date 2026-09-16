---
id: CALP-7
title: The watcher's log grows without bound
---
# Bound the watch log
## Problem (user)
`adt watch`'s log file grows forever on the macOS path. Nothing rotates it.
## Plan (PM)
### Problem & goal
Bound the log so a long-running watcher cannot fill the disk.
### Design
- **Approach:** at the top of each tick, if the log exceeds
  `ADT_WATCH_LOG_MAX_BYTES` (default 2 MiB), rename it to `<name>.1` and start a
  fresh file, keeping exactly one rotated sibling. Separately, a tick that finds
  nothing to do prints nothing, so an idle watcher stops growing the log at all.
- **Why this approach:** this is the simplest thing that works. `logrotate` and
  `newsyslog` would each add an external dependency and a per-host config for a
  single file the process already owns; nothing about one log file forces that.
### Sub-steps
- 1a — size check + rename at tick start
- 1b — silence the banner on an idle tick
- 1c — document the cap and the rotated filename
### Test plan
- Unit tests: a simulated month of ticks stays bounded; a quiet tick writes
  nothing; the tail survives rotation.
### Risks
- A diagnostic could be lost at the rotation boundary; one sibling is retained.

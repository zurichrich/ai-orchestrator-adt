---
id: CALP-1
title: Close the trust gap between the agent and the PO
---
# Trust gap
## Problem (user)
I cannot trust what the agent tells me. When it says a thing is done, I have to
cross-examine it to find out whether the design was right in the first place.
The question I keep having to ask is "is this the correct, simplest solution?"
and nothing in the system asks it for me.
## Plan (PM)
### Problem & goal
The agent asserts completion from intent and states caveats it then drops. Make
those two failures structurally impossible.
### Design
- **Approach:** three mechanisms. A `Stop` hook lints claim-shaped sentences and
  refuses ones that trace to nothing run or read in the turn. A `PreToolUse` hook
  denies a deferral unless a human authorised it. A caveat written in the plan
  must attach to a DoD condition or be an explicit recorded waiver.
- **Why this approach:** each of the three is a real failure with a real
  post-mortem behind it, and each becomes a hook rather than more prose.
### Sub-steps
- 1a — the claim linter
- 1b — the deferral guard
- 1c — the caveat-attachment check
### Risks
- The linter may flag legitimate sentences; it warns rather than blocks.

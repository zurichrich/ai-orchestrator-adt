---
id: CALP-11
title: The done-gate must assert a rendered page, not just the source
---
# Grade the rendered artifact
## Problem (user)
A change edited a source document, every check passed, and the published page
was broken in half. The greps all passed because the string was still present.
The gate has to see what a human actually opens.
## Plan (PM)
### Problem & goal
Make the done-gate assert the rendered output, not only the source it came from.
### Design
- **Approach:** extend `done_evidence:` with a `file:` + `must_contain_regex:`
  form, and have the gate open that file and match the pattern. Tickets whose
  change renders a page add one such condition pointed at the page, e.g.
  `file: docs/references.md` with a regex for the table row that must survive.
- **Why this approach:** the gate already parses `done_evidence:`, so adding one
  condition kind is smaller than a separate renderer check, and pointing it at
  the document under `docs/` keeps the path obvious to whoever writes it.
### Sub-steps
- 1a — parse the `file:` + `must_contain_regex:` form
- 1b — open the named file and match
- 1c — document the new condition kind
### Test plan
- Unit tests: a matching file passes; a non-matching file denies.
### Risks
- A regex that is too loose passes a broken page; authors are told to pick a
  distinctive element.

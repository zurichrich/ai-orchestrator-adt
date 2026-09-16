---
id: CAL-5
title: Document the token-attribution model in the README
---
# Document token attribution
## Plan (PM)
### Problem & goal
Nobody can tell how per-ticket token costs are attributed.
### Design
Add a "Token attribution" section to the repo-root `README.md`.
### Sub-steps
- 1a — write the section in `README.md`
### Test plan
- Assert the section is present.
---
done_evidence:
  - file: README.md
    must_contain_regex: 'Token attribution'
    lane: done

---
id: CAL-1
title: Rename the deploy-guard hook to release-guard
---
# Rename the deploy-guard hook to release-guard
## Plan (PM)
### Problem & goal
`deploy-guard.sh` is misnamed — it guards releases, not deploys. Rename it.
### Design
Rename the file and every reference to it.
### Impact / ripple analysis
- `defaults/settings.hooks.json` → the wiring names the old file → 1a
- `lib/uninstall.sh` → the adt_scripts tuple names the old file → 1b
- `docs/references.md` → the hook catalogue row names the old hook → 1c
### Sub-steps
- 1a — update the settings fragment
- 1b — update the uninstall tuple
- 1c — update the references catalogue row
### Test plan
- `test_uninstall.sh` covers the tuple.
---
done_evidence:
  - must_run: 'grep -q release-guard defaults/settings.hooks.json'
    lane: build
  - must_run: 'grep -q release-guard lib/uninstall.sh'
    lane: build
  - must_run: 'bash tests/test_uninstall.sh'
    lane: qa

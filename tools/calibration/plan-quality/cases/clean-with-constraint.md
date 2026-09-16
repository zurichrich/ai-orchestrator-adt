---
id: CALP-8
title: Commands must stop exhausting the GraphQL quota pool
---
# REST, not porcelain
## Problem (user)
One heavy PR session exhausts the API quota and everything stalls.
## Plan (PM)
### Problem & goal
Stop the command layer draining the smaller of the two quota pools.
### Design
- **Approach:** every command, hook and library call goes through the REST
  endpoints (`gh api repos/...`) instead of the `gh pr` / `gh issue` porcelain,
  and a test walks the command and hook trees failing the build on any porcelain
  call. GraphQL is retained only where no REST endpoint exists (Projects v2).
- **Why this approach:** the obvious simpler option — "use the porcelain but call
  it less" — does not work: the porcelain bills a *separate* 5000-point GraphQL
  pool that one heavy session exhausts regardless of call count, while the REST
  core pool is an order of magnitude larger. The constraint forces the rewrite,
  and the enforcing test is what stops it regressing the next time someone reaches
  for the friendlier command.
### Sub-steps
- 1a — port the command layer to REST
- 1b — port the hooks and lib
- 1c — the discipline test over both trees
### Test plan
- `tests/test_gh_api_discipline.sh` fails on any porcelain call in the trees.
### Risks
- Projects v2 has no REST equivalent; that one call stays on GraphQL and is
  documented as the exception.

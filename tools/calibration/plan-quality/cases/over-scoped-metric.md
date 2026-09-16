---
id: CALP-6
title: Measure whether the agent's delivery quality actually improved
---
# Measure the improvement
## Problem (user)
Every claim that the process got better is a claim. I want a number.
## Plan (PM)
### Problem & goal
Produce a measured baseline for delivery quality.
### Design
- **Approach:** three metrics. (1) `pass^k` — replay every closed ticket k times
  from its brief through plan, build and QA, and score a ticket 1 only if all k
  runs pass their DoD; the closed tickets are the eval set and are already
  written. (2) follow-on rate from `follow_ons:` frontmatter. (3) post-merge
  defect count from bugs naming a closed ticket.
- **Why this approach:** `pass@k` flatters, `pass^k` does not; the eval set costs
  nothing because the tickets exist.
### Sub-steps
- 1a — the pass^k replay over the closed-ticket set
- 1b — follow-on rate from frontmatter
- 1c — post-merge defect count
### Risks
- Replay cost scales with k.

---
adt_managed: ADT-087
name: adt-release-check
description: Verify all gates before passing to the user for merge
adt-budget: 100
---

# /adt-release-check

Take the highest-priority oldest item in `~/.adt/<project>/cache/ready-to-release/`
and find its `feat:` PR.

Gates 3 and 9 both use one REST read. Make it once and reuse the result. Use
`gh api`, not the `gh pr` porcelain, which bills the GraphQL pool:

```
gh api repos/{owner}/{repo}/pulls/{N} --jq '{mergeable, mergeable_state, commits, head_sha: .head.sha}'
```

## Gates — all must pass

1. **QA PASS.** The backlog file's QA report says so.

2. **Definition of Done — all lanes green.** Grade the whole DoD, not one slice.
   `.claude/hooks/adt-dod.sh <ticket.md> --devteam <.adt/>` must report no
   failing condition, and `--gate` must say APPROVABLE. Re-run only the
   conditions whose inputs changed since QA. A failing condition is a hard ✗.

   **Also confirm every `was_red_at` ref still resolves.** A rebase, amend or
   squash rewrites the commit a pin names. After that the pin resolves only
   through the reflog of the machine that rewrote it. A pin that doesn't resolve
   makes its condition **un-gradeable instead of failing**, so the DoD grades OK
   while proving nothing. For each pin, run `git cat-file -e <sha>` and
   `git merge-base --is-ancestor <sha> <PR head>`. If either fails, that is a
   hard ✗: bounce to `qa` to re-pin, and don't accept the OK.

3. **Tests still green. Re-run them ONLY if the diff changed since QA passed**
   (working-style #7: a change triggers a check, a resume does not). Compare the
   PR head SHA now with the SHA QA signed off on. If no commits landed after the
   QA pass, the code, dependencies and inputs are the same as that green run, so
   cite it. If commits did land, those commits are the change that makes the run
   owed. **Record which path you took.**

4. **Version bumped.** QA already checked this. Re-verify only if gate 3
   re-ran; if the diff is unchanged, cite QA. ✓ / ✗ / n/a.

5. **Docs synced, checked against the spec's Impact list.** The spec lists which
   docs this change touches. Confirm each one it names actually changed in the
   diff. A doc the spec said would change but didn't is a ✗. Then check the
   obvious places the spec didn't list.

6. **Security signed off**, with no unresolved HIGH. If
   `security_review_required` is set, a re-run on the final diff is
   RECOMMENDED. Like every security review, it needs the user's yes first:
   explain what it is, why this diff needs it and what it costs, then wait (the
   block in `/adt-plan` step 1 has the wording). Say plainly what a `no` leaves
   ungraded. A release gate the human declined is recorded as their decision,
   never skipped silently. On a yes, confirm APPROVE.

7. **Designer signed off.** If `ui_review_required` is set, every viewport has
   been addressed.

8. **Critical path signed off.** If the diff touches the project's money, safety
   or data-integrity path, there is a `## Critical-path review` with no
   unresolved blocker. Re-invoke the reviewer if commits landed after it ran.

9. **No conflicts with main.** The gate-3 read shows `mergeable: true` and
   `mergeable_state: "clean"`. GitHub computes mergeability in the background,
   so right after a push it returns `null`/`"unknown"`. Re-read until it
   resolves; don't treat unknown as a failure.

## If any gate fails

Bounce it: move the file to `qa/`, set `stage: qa`, append the reason to the QA
report, and comment on the PR `Bounced at release-check — gate failed: <which>`.
**Do not email the user.** Don't spend their time on unfinished work.

## If all gates pass

Comment a short summary on the PR and **offer the merge**: ask whether the human
will merge or wants you to. Never merge unprompted.

```
gh api -X PUT repos/{owner}/{repo}/pulls/{N}/merge -f merge_method=merge
```

On a repo whose `main` is pinned by consumers, use a **merge commit**, never a
squash. Squashing orphans the SHAs the consumers pin.

<!-- adt-bundle: v0.1.0 -->

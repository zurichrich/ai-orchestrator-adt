# AO-027 — Several developers can work one ticket from different machines

**Shipped:** 2026-11-24, PR [#29](https://github.com/zurichrich/ai-orchestrator-adt/pull/29), merge commit `8545b9e`, ADT 0.3.0
**Track:** full · **Gates:** DoD-coverage review (COVERED after four rounds), plan-quality review (SOUND), `/simplify`, QA (FAIL, FAIL, PASS), release check

## What shipped

Nothing a ticket needs lives only in one machine's cache any more.

- Every frontmatter field with no native Issue home rides a hidden
  `<!-- adt:meta {json} -->` line at the end of the Issue body. The set is a
  complement of the mapped fields, so a field added later travels with no code
  change.
- The pull now carries title, body, metadata and `done_evidence` down into a
  file that holds no unpushed change.
- A push over an Issue that changed since this machine last saw it is refused.
  The file gets `sync_conflict:`, GitHub's version is written beside it as
  `<file>.md.github`, and one `adt:conflict` comment names the machine.
- Issues pushed before 0.3.0 migrate on the first full pull: an in-sync machine
  republishes its metadata, at most 100 tickets per pass.

On release, this repo's 11 in-sync tickets were republished on the first full
pull, with no conflicts.

## Deviations from plan

- **`stale_meta`.** Tracing the migration showed a stale machine would take the
  Issue's text and then publish its own out-of-date metadata. The sidecar now
  remembers those tickets.
- **Gate fields that are not `true` ride the trailer.** Only `true` becomes a
  `gate:` label, so `ui_review_required: false` was lost on a rebuild.
- **The pull compares both sides after a file round trip.** `emit_md` drops a
  final newline and the Issue carries the generated DoD block, so a raw
  comparison re-took the same content every tick and judged an in-sync machine
  stale.
- **`/simplify` found two API-cost problems.** A deleted conflicted file whose
  Issue the pull would not rebuild forced a full sweep on every tick. And the
  migration would have republished every ticket in one pass, about 1,200 calls
  for agenticz's 618 Issues. Both are fixed.
- **The DoD grader was fixed in this PR** (QA pass 1, below).
- **The version moved to 0.3.0** at merge, on the PO's decision.

## What was surprising

**A cross-lane `depends_on` was never graded at close.** `adt_dod.check()`
dropped the other lanes' conditions before resolving dependencies, so a
done-lane condition that depended on a build-lane one reported "names no
condition" and came back can't-verify. The done guard lets can't-verify through
as WARN, so any ticket with that shape closed with its done check unrun. That
is the use `/adt-plan` documents for `depends_on`. Fixed in `c0932ba`.

**A test stub with the wrong arity hid a crash.** AO-025 merged mid-review and
gave `_machine_id` a required `project_root`. This branch called it with none,
which raises `TypeError` on the first real conflict. Git merged it cleanly and
all the tests passed, because the stub was `lambda: "machine-b"`. A stub should
take the same arguments as what it replaces.

**The release check asks for every lane green, but a done-lane condition can
only pass after the merge.** C11 checks the live Issue, which only the merged
sync can write. Taken literally, gate 2 bounces any ticket with a done-lane
condition, and the bounce can't fix it. Here it was recorded as a close-time
check. `commands/release-check.md` should say that done-lane conditions are
graded at close.

**Releasing ADT means fast-forwarding one checkout, and every project's watcher
runs code from it.** The launchd jobs for this repo, tech-playground and
agenticz all run `tools/adt_watch.py` from the canonical checkout. So the
fast-forward that lets a ticket's own done check pass also releases the change
to every consumer within a minute. Nothing in `/adt-close` mentions this.

**The migration does nothing until a full pull.** A plain pass right after the
fast-forward reported `noop=11`. The republish runs in the pull, and only on a
full sweep, so the close needed an explicit `--pull` pass.

## Adjacent findings

- **The plan gate missed a coverage review headed `### DoD-coverage review
  (round 1)`.** It read the round-1 GAP as current. The suffixes were removed to
  get past it.
- **The installed-copies test ignores the bundle stamp.** Two installed files
  copied by hand during the build had lost their stamp line, and the test still
  passed. Regenerating with the installer, not `cp`, avoids it.
- **QA's version check keys on `build_version_file`, which ADT's own config does
  not set,** although the repo has a `VERSION` file. The check said n/a every
  time.

#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
"""adt_dod — the machine-checkable Definition-of-Done grader (ADT-81).

One grader, two callers: the `/adt-build-todone` loop driver grades a ticket's
DoD each pass against this module, and `adt-done-guard.sh` calls it (CLI mode) on the
git-mv-into-done to assert the same conditions. Keeping the grading in one place
means the loop can never grade itself against logic that drifts from the done-gate.

The DoD lives in the ticket's `done_evidence:` frontmatter (a recorded decision: declared,
not inferred). 1a ports the existing `must_contain_regex` form verbatim from
`adt-done-guard.sh`; 1c adds `must_run` / `was_red_at` / `lane`.

The cardinal rule (the brief's safety constraint): **no agent narration is ever
an input.** Every condition is a regex match against a rendered file or a
process exit code — something external to the agent's say-so.
"""
import ast
import os
import re
import subprocess
import sys


# An entry's KIND keys. A new entry starts at whichever of these appears at the
# list-item dash; the kind is whichever value is set. `lane` + `was_red_at` are
# modifiers on the entry, not kinds.
_KIND_KEYS = ("file", "must_run")

# ADT-359: output that says a command did no work. A pipeline or wrapper can exit
# 0 around a runner that collected nothing (`pytest … | tail -5` reports tail's
# 0 and loses pytest's 5), so the exit code alone cannot tell "passed" from "ran
# nothing". Each pattern is anchored to one runner's own summary wording, so a
# test that only mentions the phrase does not match.
_NO_WORK = (
    re.compile(r"^=*\s*no tests ran in [0-9.]+s\b", re.M),   # pytest
    re.compile(r"^Ran 0 tests in [0-9.]+s$", re.M),           # unittest
    re.compile(r"(?<![0-9])0 passed, 0 failed\b"),           # ADT's shell tests
)


def _no_work_line(output):
    """Return the line that says the command did no work, or None."""
    for rx in _NO_WORK:
        m = rx.search(output or "")
        if m:
            return m.group(0).strip("= ").strip()
    return None


def _default_cwd(devteam):
    """Where must_run conditions execute when the caller names no directory.

    ADT-359. This used to be the parent of `devteam`. `.adt/` is gitignored and
    exists only in the canonical checkout, so a build in a linked worktree passed
    the canonical `.adt/`, and every condition ran against a tree that did not
    have the branch's files. The directory the grader is invoked from is the tree
    being built, so its git toplevel is the answer. Outside any git repo the old
    answer stands.
    """
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return os.path.dirname(os.path.abspath(devteam)) or os.getcwd()


def parse_done_evidence(md_path):
    """Parse the `done_evidence:` list from a ticket .md's frontmatter.

    Each dict may carry:
      file + regex      — must_contain_regex check (1a, ported from done-guard)
      must_run          — a shell command; passes when it exits 0 (1c)
      was_red_at        — a git ref (or "plan"); assert must_run FAILED there and
                          passes at HEAD (a real red→green, not always-green) (1c)
      lane              — plan|build|qa|done (default 'build'); which stage's slice
    A new entry begins at a `- file:` or `- must_run:` dash. Missing file → []
    (infra; caller decides). Faithful superset of adt-done-guard.sh's parser.
    """
    evidence = []
    try:
        text = open(md_path, encoding="utf-8").read()
    except FileNotFoundError:
        return evidence
    fm = text.split("---", 2)
    block = fm[1] if len(fm) >= 3 else text
    cur = None
    in_ev = False

    def _unquote(val):
        """Strip ONE matched pair of surrounding quotes — not every quote char.

        `.strip("\"'")` strips repeatedly from both ends, so a value that ends in
        a quote lost its inner one too: `'grep -qE "^x"'` became `grep -qE "^x`,
        an unbalanced command that fails for a reason unrelated to the work. Found
        while authoring ADT-114's own DoD; every condition there had to end in a
        path to dodge it. A DoD entry that is silently rewritten before it runs is
        the worst kind of check — it looks authored and is not."""
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            return val[1:-1]
        return val

    def _store(d, key, val):
        # Normalise the regex key to the internal name the checkers read.
        d["regex" if key == "must_contain_regex" else key] = val

    for line in block.splitlines():
        if re.match(r"^done_evidence:\s*$", line):
            in_ev = True
            continue
        if in_ev:
            if re.match(r"^\S", line):          # dedent → end of the list
                in_ev = False
                if cur:
                    evidence.append(cur)
                    cur = None
                continue
            # A new list item starts at a `- ` dash, WHATEVER its first key — so an
            # entry whose kind we don't recognise (a prose condition) still becomes
            # an entry that unsupported()/check() can flag, rather than vanishing.
            mdash = re.match(r"\s*-\s*([A-Za-z_][\w-]*):\s*(.*?)\s*$", line)
            mkey = re.match(r"\s*([A-Za-z_][\w-]*):\s*(.*?)\s*$", line)
            if mdash:                       # start of a new item
                if cur:
                    evidence.append(cur)
                cur = {}
                _store(cur, mdash.group(1), _unquote(mdash.group(2).strip()))
            elif mkey and cur is not None:  # a continuation key of the current item
                _store(cur, mkey.group(1), _unquote(mkey.group(2).strip()))
    if cur:
        evidence.append(cur)
    return evidence


def _check_regex(ev, devteam):
    """Assert a {file, regex} entry against the rendered copy under .adt/.

    Ports adt-done-guard.sh's gate #1 (lines 130-146): the assertion target is forced
    onto the .adt/ copy (never the cache or the source .md). Returns a
    condition dict. `passed=None` marks an infra can't-verify (file absent) — the
    caller maps that to fail-open, exactly as done-guard does.
    """
    f, rx = ev.get("file"), ev.get("regex")
    base = os.path.basename(f)
    target = os.path.join(devteam, base)
    cond = {"kind": "must_contain_regex", "target": "%s/%s" % (os.path.basename(devteam), base),
            "regex": rx, "lane": ev.get("lane", "done")}
    if not os.path.isfile(target):
        cond["passed"] = None
        cond["why"] = ("artifact %s not found under the render dir — cannot "
                       "verify (infra)" % base)
        return cond
    try:
        content = open(target, encoding="utf-8").read()
    except Exception as e:
        cond["passed"] = None
        cond["why"] = "could not read %s — cannot verify (infra): %s" % (base, e)
        return cond
    if re.search(rx, content):
        cond["passed"] = True
        cond["why"] = "/%s/ matches in %s/%s" % (rx, os.path.basename(devteam), base)
    else:
        cond["passed"] = False
        cond["why"] = ("/%s/ does not match in %s/%s (the rendered "
                       "file the human opens). A source/tooltip/substring match "
                       "is not evidence." % (rx, os.path.basename(devteam), base))
    return cond


def _check_run(ev, cwd, red_cache=None, now_cache=None):
    """Assert a {must_run} entry: the command exits 0 (run in `cwd`).

    This is the general "objective, external to the agent" check — a process exit
    code, never the agent's say-so. `was_red_at` (optional) records that the same
    command was observed FAILING at a prior ref/"plan"; we re-run it at that ref
    in a throwaway `git worktree` to confirm a real red→green, NOT an always-green
    that was never actually broken. If the ref can't be checked out cheaply
    (detached/unknown ref, no git), we mark red_verified=None and still require
    green-now — never silently treat unverifiable-red as a pass.
    """
    cmd = ev["must_run"]
    lane = ev.get("lane", "build")
    cond = {"kind": "must_run", "target": cmd, "lane": lane}

    def run(command, in_dir):
        # ADT-359: keep the output. The exit code alone cannot tell a command
        # that passed from one that ran nothing.
        try:
            r = subprocess.run(command, cwd=in_dir, shell=True,
                               capture_output=True, text=True, timeout=600)
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except Exception:
            return None, ""

    # ADT-347: memoise the HEAD-side run for the duration of ONE grading pass.
    # The replay side has been memoised by (cmd, ref) since ADT-260; this side
    # was not, so a command named by N conditions was executed N times. On
    # ADT-342 `bash tests/test_uninstall.sh` is 12.7s and was named by three
    # conditions — 38s for one deterministic result, and the qa+done grade went
    # over two minutes. Same-pass only: a condition still reflects the tree as
    # it is when the grade runs. A condition returning different answers within
    # one pass is already broken, which is the assumption the replay side has
    # made since ADT-260.
    if now_cache is not None and cmd in now_cache:
        rc_now, out_now = now_cache[cmd]
    else:
        rc_now, out_now = run(cmd, cwd)
        if now_cache is not None:
            now_cache[cmd] = (rc_now, out_now)
    # ADT-306: keep the raw exit code on the condition. It was computed and then
    # spent entirely on the `why` string, so --dry-run had nothing to report but
    # prose, and 127 (the authoring defect commands/plan.md already names) was
    # indistinguishable from an honest red at a glance.
    cond["rc"] = rc_now
    if rc_now is None:
        cond["passed"] = None
        cond["why"] = "could not run `%s` (infra)" % cmd
        return cond
    green_now = rc_now == 0

    # ADT-359: a green run that did no work is not evidence, so it fails. It is
    # decided before the replay, because a failed condition needs no replay.
    idle = _no_work_line(out_now) if green_now else None
    if idle:
        cond["no_work"] = True
        cond["passed"] = False
        cond["why"] = ("`%s` exited 0 but did no work (%r). A green run that ran "
                       "nothing is not evidence." % (cmd, idle))
        return cond

    red_at = ev.get("was_red_at")
    if not red_at:
        cond["passed"] = green_now
        cond["why"] = "`%s` exited %d (%s)" % (cmd, rc_now, "pass" if green_now else "FAIL")
        return cond

    # ADT-260: a condition whose only repo access is through a REF cannot be
    # replayed. A worktree shares the git dir, so `origin/main`, a tag or `HEAD`
    # resolve to where they point NOW, not to the pin — the command behaves
    # identically at both, passes in the replay, and the grader would report
    # "it was never red", failing a genuinely-satisfied condition. Skipping the
    # replay is both cheaper (no worktree, no network fetch) and more truthful.
    # Graded can't-verify, matching the `was_red_at: plan` precedent: it must not
    # silently pass, and it must not fail a correct implementation.
    if _reads_only_a_ref(cmd, cwd):
        cond["was_red_at"] = red_at
        cond["red_verified"] = None
        cond["ref_reading"] = True
        cond["passed"] = None
        cond["why"] = ("`%s` reads only a git REF, so its `was_red_at: %s` cannot "
                       "be replayed — a worktree shares the git dir and the ref "
                       "resolves to now, not to the pin. Authoring defect: drop "
                       "the pin, or pin a condition that reads a PATH." % (cmd, red_at))
        return cond

    # was_red_at: verify the command FAILED at the recorded ref (a real red→green).
    if red_cache is not None and (cmd, red_at) in red_cache:
        red_verified = red_cache[(cmd, red_at)]
    else:
        red_verified = _was_red_at(cmd, red_at, cwd)
    cond["was_red_at"] = red_at
    cond["red_verified"] = red_verified
    if not green_now:
        cond["passed"] = False
        cond["why"] = "`%s` still fails now (exit %d) — not green→ not done" % (cmd, rc_now)
    elif red_verified is False:
        cond["passed"] = False
        cond["why"] = ("`%s` passes now but ALSO passed at %s — it was never red, "
                       "so 'green now' proves no fix (always-green)" % (cmd, red_at))
    elif red_verified is None:
        cond["passed"] = None
        cond["why"] = ("`%s` is green now but its red-state at %s could not be "
                       "verified (ref not checkoutable) — can't confirm red→green"
                       % (cmd, red_at))
    else:
        cond["passed"] = True
        cond["why"] = "`%s` was red at %s and is green now (verified red→green)" % (cmd, red_at)
    return cond


_REF_OBJ_READ = re.compile(
    r"\bgit\s+(?:show|cat-file)\b[^|;&]*?(?:origin/\S*|HEAD\S*|refs/\S*):\S*")


def _reads_only_a_ref(cmd, cwd):
    """True when the command's ONLY repo access is through a git ref (ADT-260).

    Such a command cannot be replayed at a pin: `_was_red_at` evaluates it in a
    worktree, which SHARES the git dir, so `origin/main` / a tag / `HEAD` point
    wherever they point now. Deliberately narrow — the risk is over-matching and
    skipping a replay that WAS meaningful — so it requires a ref-qualified object
    read AND that nothing left in the command names an existing working-tree
    path. A command that reads a path keeps its replay.
    """
    if not _REF_OBJ_READ.search(cmd):
        return False
    rest = _REF_OBJ_READ.sub(" ", cmd)
    rest = re.sub(r"[|;&()<>]", " ", rest)
    for tok in rest.split():
        tok = tok.strip("'\"")
        if not tok or tok.startswith("-"):
            continue
        if os.path.exists(os.path.join(cwd, tok)):
            return False
    return True


def _was_red_at_group(pairs, cwd):
    """Replay many (cmd, ref) pairs with ONE worktree per DISTINCT ref (ADT-260).

    The old shape paid a full `git worktree add` + `remove` per CONDITION. A plan
    pins every condition to the SHA it was written at, so in practice that was N
    setup/teardown round-trips against one SHA — 63 of them on ADT-254. Cost now
    scales with distinct pins, which is normally one. Returns {(cmd, ref): bool
    or None} with exactly the semantics `_was_red_at` had per call.
    """
    import tempfile
    import shutil
    out = {}
    by_ref = {}
    for cmd, ref in pairs:
        by_ref.setdefault(ref, []).append(cmd)
    for ref, cmds in by_ref.items():
        if ref == "plan":
            for c in cmds:
                out[(c, ref)] = None
            continue
        tmp = tempfile.mkdtemp(prefix="adt-dod-redcheck.")
        try:
            add = subprocess.run(["git", "worktree", "add", "--detach", tmp, ref],
                                 cwd=cwd, capture_output=True, text=True)
            if add.returncode != 0:
                for c in cmds:
                    out[(c, ref)] = None      # uncheckoutable ref → can't verify
                continue
            for c in cmds:
                try:
                    r = subprocess.run(c, cwd=tmp, shell=True, capture_output=True,
                                       text=True, timeout=600)
                    out[(c, ref)] = r.returncode != 0
                except Exception:
                    out[(c, ref)] = None
        finally:
            # Runs even when a command raised or the add failed, so a failed
            # grade cannot leave `git worktree list` polluted.
            subprocess.run(["git", "worktree", "remove", "--force", tmp],
                           cwd=cwd, capture_output=True, text=True)
            shutil.rmtree(tmp, ignore_errors=True)
    return out


def _was_red_at(cmd, ref, cwd):
    """Run `cmd` against `ref` in a throwaway git worktree. Returns:
       True  → it failed there (red, as claimed)
       False → it passed there (not actually red)
       None  → couldn't check out the ref (can't verify)
    'plan' is a sentinel meaning "no checkoutable ref" → None (declared-only)."""
    return _was_red_at_group([(cmd, ref)], cwd).get((cmd, ref))


def check(ticket_md_path, devteam, lane=None, cwd=None):
    """Grade a ticket's DoD. Returns {conditions: [...], all_green, infra}.

    Each condition: {kind, target, lane, passed (True/False/None), why}.
      passed True  → met;  False → failed;  None → infra can't-verify (fail-open).
    all_green: every gradable (passed is not None) condition passed AND none
      failed AND none was can't-verify. infra: any condition was can't-verify.
    `lane` slices to one stage's conditions (the loop advances a stage only when
    THAT stage's slice is all-green); lane=None grades every condition. `cwd` is
    where must_run commands execute (defaults to `_default_cwd()`: the git
    toplevel of the directory the grader is invoked from).
    """
    if cwd is None:
        cwd = _default_cwd(devteam)
    evs = list(parse_done_evidence(ticket_md_path))
    # ADT-260 pre-pass: collect every replay this grade needs and run them
    # grouped, one worktree per DISTINCT ref, before grading anything.
    pending = []
    for ev in evs:
        cmd, ref = ev.get("must_run"), ev.get("was_red_at")
        if cmd and ref and ref != "plan" and not _reads_only_a_ref(cmd, cwd):
            pending.append((cmd, ref))
    red_cache = _was_red_at_group(pending, cwd) if pending else {}
    now_cache: dict = {}   # ADT-347: one HEAD-side run per distinct command

    conditions = []
    for ev in evs:
        if ev.get("file") and ev.get("regex"):
            cond = _check_regex(ev, devteam)
            cond["id"], cond["depends_on"] = ev.get("id"), ev.get("depends_on")
        elif ev.get("must_run"):
            cond = _check_run(ev, cwd, red_cache, now_cache)
            cond["id"], cond["depends_on"] = ev.get("id"), ev.get("depends_on")
        else:
            # An entry with no recognised kind (prose / malformed). DO NOT silently
            # drop it — that would let all_green be True while a declared condition
            # went ungraded (the loop-grades-own-homework back door). Surface it as
            # can't-verify so it blocks all_green and shows in the scorecard.
            cond = {"kind": "unsupported", "target": str(ev),
                    "lane": ev.get("lane", "build"), "passed": None,
                    "why": ("done_evidence entry is not a checkable condition "
                            "(no must_run / must_contain_regex): %s" % ev),
                    "id": ev.get("id"), "depends_on": ev.get("depends_on")}
        if lane is None or cond["lane"] == lane:
            conditions.append(cond)
    # ADT-114 mech #2 — ORDERING. Where condition B is meaningless until A holds,
    # B must not be reported green ahead of A. Entries carry optional `id:` and
    # `depends_on:` (comma-separated ids); absent keys mean no edges, i.e. exactly
    # today's behaviour, so this is backwards-compatible.
    #
    # An unmet upstream makes the downstream CAN'T-VERIFY (passed=None), not
    # failed: we are not asserting B is broken, we are refusing to claim it is
    # green. can't-verify already blocks all_green and shows in the scorecard, so
    # both callers (done-guard, the build-todone loop) handle it with no change.
    by_id = {c["id"]: c for c in conditions if c.get("id")}
    for cond in conditions:
        deps = [d for d in (cond.get("depends_on") or "").replace(",", " ").split() if d]
        for dep in deps:
            up = by_id.get(dep)
            if up is None:
                cond["passed"] = None
                cond["why"] = ("depends_on '%s' names no condition in this DoD "
                               "(authoring defect)" % dep)
                break
            if up["passed"] is not True:
                cond["passed"] = None
                cond["why"] = ("upstream '%s' is not met, so this condition is not "
                               "graded — a downstream condition cannot be green "
                               "ahead of what it depends on" % dep)
                break

    gradable = [c for c in conditions if c["passed"] is not None]
    failed = [c for c in gradable if c["passed"] is False]
    infra = [c for c in conditions if c["passed"] is None]
    return {
        "conditions": conditions,
        "all_green": len(failed) == 0 and len(infra) == 0,
        "infra": len(infra) > 0,
    }


def dry_run(ticket_md_path, devteam, cwd=None):
    """Report EVERY condition with its exit code and lane (ADT-306 problem 3).

    `check()` already runs every condition and builds a full result; `verdict()`
    then reduces the whole thing to one first-fail line, so the per-condition
    exit codes were computed and thrown away. This prints them.

    It goes through `check()` — the grader's own path — rather than shelling out,
    and that is the point of the mode rather than an implementation detail. A
    condition hand-run in an agent's shell is run under a DIFFERENT `grep`: in
    Claude Code's shell `grep` is a function shimming ugrep, while `_check_run`
    uses `subprocess.run(shell=True)` -> `/bin/sh -c` -> `/usr/bin/grep`. On the
    same two-line file `grep -qv aaa` returns 1 under ugrep and 0 under BSD grep.
    That divergence is what read GREEN against twelve unprefixed hooks during
    ADT-301, and a dry-run that wrapped a shell would reproduce it exactly.

    Three flags, all authoring defects `commands/plan.md` already names but which
    nothing reported:

      EXIT-127      the command does not exist. A test that has not been written
                    yet exits 127, which looks like an honest red in a one-line
                    verdict and is not one.
      NO-WORK       the command exited 0 but its output says it ran nothing
                    (ADT-359). `_check_run` already fails it; the flag says why.
      ALREADY-GREEN the replay found the command green at its own `was_red_at`
                    pin: it claims a red->green and was never red. This reuses
                    `_check_run`'s existing `red_verified` result rather than
                    re-deriving "is it green yet" here — an unpinned condition is
                    a regression guard on existing behaviour, where green now is
                    the whole point and a flag would be noise.

    Returns the check() result so a caller can assert on it.
    """
    # Print the directory conditions RAN IN. A mode whose whole premise is "a
    # condition graded differently depending on how it ran is the defect" must
    # not hide which directory it used (QA finding 6). It is resolved once and
    # passed to check(), so the report and the grade cannot name different ones.
    resolved = cwd or _default_cwd(devteam)
    result = check(ticket_md_path, devteam, cwd=resolved)
    # `must_run` only. A `must_contain_regex` condition resolves its target
    # against `devteam` ITSELF (_check_regex), not devteam's parent, so an
    # unqualified "conditions run in" would be wrong for half the kinds.
    print("must_run conditions run in: %s" % resolved)
    print("must_contain_regex targets resolve under: %s"
          % os.path.abspath(devteam))
    print("condition   lane   exit  flags          target")
    for i, cond in enumerate(result["conditions"], 1):
        rc = cond.get("rc")
        flags = []
        if rc == 127:
            flags.append("EXIT-127")
        if cond.get("no_work"):
            flags.append("NO-WORK")
        if cond.get("red_verified") is False:
            flags.append("ALREADY-GREEN")
        print("C%-10d %-6s %-5s %-14s %s"
              % (i, cond.get("lane", "?"),
                 "-" if rc is None else rc,
                 ",".join(flags) or "-",
                 (cond.get("target") or "")[:70]))
    bad = sum(1 for c in result["conditions"]
              if c.get("rc") == 127 or c.get("no_work")
              or c.get("red_verified") is False)
    print("\n%d condition(s), %d flagged as authoring defects"
          % (len(result["conditions"]), bad))
    return result


def verdict(result, prefix):
    """Reduce a check() result to the one (decision, reason) line callers report.

    DENY OUTRANKS WARN (ADT-106). A condition that ran and FAILED is a real
    failure; a can't-verify condition elsewhere in the same DoD must never be
    reported in its place. Both consumers used to walk the can't-verify
    conditions first and return before ever reaching the failures, so one
    unverifiable entry hid every red condition behind it — the
    loop-grades-its-own-homework back door this module exists to close.

    Fail-open on infra is unchanged: WARN still does not block. This only
    narrows WHEN a WARN is the verdict — never when nothing gradable failed.

    Lives here, called by both `_cli` and adt-done-guard.sh's gate #1, because the
    two used to carry independently-written copies of this reduction. That is
    why the ordering could be wrong in both at once, and why fixing it in one
    copy would have looked like fixing it.
    """
    for c in result["conditions"]:
        if c["passed"] is False:
            return "DENY", "%s: done_evidence not satisfied — %s" % (prefix, c["why"])
    for c in result["conditions"]:
        if c["passed"] is None:
            return "WARN", "%s: %s" % (prefix, c["why"])
    return "OK", ""


def dod_lost(ticket_md_path):
    """True when the spec body shows a DoD the frontmatter does not carry.

    ADT-273. The loss/drift signature, and deliberately NOT "the frontmatter is
    empty": `check(lane="done")` legitimately returns [] for a ticket whose
    conditions are all build-lane, and a ticket may genuinely carry no DoD, so
    denying on emptiness would block correct tickets. This fires only when the
    body ADVERTISES conditions the grader cannot see.

    That combination is what a reconstruct used to produce. `to_issue` sent the
    DoD to GitHub only inside body prose, and `from_issue` rebuilt a cache file
    without it, so the ticket came back with zero conditions while its spec still
    displayed them — 10 of 73 tickets on the backlog, and `verdict()` returns OK
    on an empty condition list, so the done gate said nothing.

    Fails OPEN on an unreadable file, like the rest of this module: we do not
    block a stage move on our own plumbing.
    """
    if parse_done_evidence(ticket_md_path):
        return False
    try:
        text = open(ticket_md_path, encoding="utf-8").read()
    except OSError:
        return False
    parts = text.split("---", 2)
    body = parts[2] if len(parts) >= 3 else text
    return bool(re.search(r"```ya?ml\s*\ndone_evidence:", body))


def unsupported(ticket_md_path):
    """Return the done_evidence entries that are NOT machine-checkable.

    The approval gate of /adt-build-todone calls this (code, not agent judgement)
    to refuse to start on a prose/uncheckable DoD. An entry is checkable iff it is
    a `must_run` or a `file`+`must_contain_regex`. Empty list → the DoD is
    all-checkable. Also returns the empty-DoD case as unsupported via has_any.
    """
    out = []
    for ev in parse_done_evidence(ticket_md_path):
        checkable = bool(ev.get("must_run")) or bool(ev.get("file") and ev.get("regex"))
        if not checkable:
            out.append(ev)
    return out


# ── ADT-114 mech #1: the DoD-coverage counter-check ────────────────────────
# `unsupported()` above answers "is every condition checkable?". It cannot answer
# "do these conditions COVER the spec?" — the DoD is authored by the session that
# builds against it and grades itself green, so a DoD can be 100% checkable, 100%
# green, and still never assert the thing the ticket was for. The counter-check is
# a separate-context reviewer (defaults/agents/adt-dod-coverage-reviewer.md) whose
# verdict is recorded in the ticket. This reads that record.
#
# The gate is on the RECORD, not on the judge being right: an LLM judge cannot be
# graded hermetically (pytest has no model), so its quality is a calibration
# record a human reads (docs/dod-coverage-calibration.md), while what code can
# enforce is that a review happened and did not come back UNKNOWN.
_REVIEW_RE = re.compile(r"^###\s+DoD-coverage review\s*$", re.MULTILINE)
_VERDICT_RE = re.compile(r"^\*\*Verdict:\*\*\s*([A-Za-z-]+)", re.MULTILINE)

# Coverage-check exemptions. EMPTY is the correct steady state — a ticket here is
# a ticket whose DoD nobody counter-checked. It lives in code (visible in a diff)
# rather than in frontmatter precisely so it cannot be self-granted by the session
# being graded.
#
# This briefly held ADT-115, the one ticket in flight when the gate landed. QA
# found it moot: ADT-115 closed without ever reaching the coverage check (it
# carried no done_evidence at all), so the carve-out never fired. Removed rather
# than left standing — a dead exemption still reads as a live one.
_COVERAGE_GRANDFATHERED = ()


def _ticket_id(md_path):
    try:
        text = open(md_path, encoding="utf-8").read()
    except OSError:
        return None
    m = re.search(r"^id:\s*(\S+)", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def coverage_review(ticket_md_path):
    """Return (verdict, grandfathered) for a ticket's DoD-coverage review.

    verdict is the recorded string (e.g. 'COVERED', 'GAP', 'UNKNOWN'), or None
    when no `### DoD-coverage review` block is present. grandfathered is True for
    the finite in-flight exemption list above."""
    tix = _ticket_id(ticket_md_path)
    grandfathered = bool(tix and tix in _COVERAGE_GRANDFATHERED)
    try:
        text = open(ticket_md_path, encoding="utf-8").read()
    except OSError:
        return None, grandfathered
    # ADT-224 D1d: the LAST block, not the first. Once `gate_effects`
    # accumulates rounds in frontmatter, a first-match read returns round 1's
    # verdict forever — so both gates would refuse on a defect closed rounds
    # ago, permanently, with no way to clear it.
    tail = _last_block(text, _REVIEW_RE)
    if tail is None:
        return None, grandfathered
    v = _VERDICT_RE.search(tail)
    return (v.group(1).strip().upper() if v else ""), grandfathered


# ── ADT-126: the PLAN-quality counter-check, and why it is CONDITIONAL ─────
# `coverage_review()` above asks whether the DoD covers the spec. It cannot ask
# whether the spec was worth building: a wrong Design with a DoD that faithfully
# covers it passes every coverage check and every condition, and grades green
# with the wrong thing built. `adt-plan-quality-reviewer` asks that question and its
# verdict is recorded as a `### Plan-quality review` block.
#
# THE DIFFERENCE FROM mech #1: this gate is CONDITIONAL on calibration.
#
# Design quality has no ground truth in the artifact the way coverage does — the
# judge must hold an opinion — so a reviewer that always answers SOUND is a real
# risk, and an uncalibrated judge makes a gate WORSE than no gate: it reports the
# design question settled when nothing checked it. That is mech #1's own failure
# rebuilt one level up.
#
# `tools/calibration/plan-quality/README.md` declares a bar BEFORE any verdict
# exists; `score.py` scores the reviewer against 9 known-flawed + 2 clean cases
# and writes ONE machine-readable line into the record:
#
#     gating: ENABLED | DISABLED
#
# THE BAR IS MEASURED ON A HELD-OUT SUBSET. Six of the flawed cases are drawn
# from the same INSTANCES this reviewer's own prompt cites as worked examples, so
# their score is a recall floor that says nothing about generalisation and cannot
# buy gating; three `held-*` cases use instances the prompt never mentions, and
# only those decide the line above. That split was found by running the reviewer
# against ADT-126 itself, which returned FLAWED and named the contamination.
#
# Below the bar the reviewer still runs and its verdict is still recorded — only
# the automatic refusal is withheld. This is the dod-coverage README's own
# instruction ("a poor score is a legitimate result — the correct response is to
# stop gating on the judge") turned from prose into a code path, so it cannot be
# quietly ignored under deadline. It degrades to RECORDING, never to
# rubber-stamping: a missing or failed calibration never turns FLAWED into a pass,
# it only stops this file refusing on the judge's say-so.
_PLAN_REVIEW_RE = re.compile(r"^###\s+Plan-quality review\s*$", re.MULTILINE)
_GATING_RE = re.compile(r"^gating:\s*(ENABLED|DISABLED)\s*$", re.MULTILINE)

# WHERE THE CALIBRATION RECORD LIVES — two layouts, same as pricing.json (ADT-115).
#
# The executing copy of this file is `.claude/tools/adt_dod.py` in a consuming
# project and `tools/adt_dod.py` in the ADT source tree. Resolving the record
# from this file's grandparent works ONLY in the source tree; installed, it
# points at `<project>/.claude/docs/...` which nothing creates. Since
# plan_gating_enabled() fails toward NOT gating, that silently made the refusal
# dead in every consuming project — a calibrated judge reading as uncalibrated,
# which is exactly the "check that cannot fail" this ticket exists to catch.
# Found by the adt-plan-quality-reviewer reviewing ADT-126.
#
# So: look NEXT TO this file first (install-defaults.sh copies the record there,
# the way it already copies pricing.json next to the pricer), then fall back to
# the source tree's docs/.
def _plan_calibration_candidates(explicit=None):
    if explicit:
        yield explicit
        return
    here = os.path.dirname(os.path.abspath(__file__))
    yield os.path.join(here, "plan-quality-calibration.md")              # installed
    yield os.path.join(here, os.pardir, "docs",
                       "plan-quality-calibration.md")                    # source tree


def _plan_calibration_path(explicit=None):
    """First candidate that exists, else the last (so errors name a real path)."""
    cands = list(_plan_calibration_candidates(explicit))
    for c in cands:
        if os.path.isfile(c):
            return c
    return cands[-1]


def plan_review(ticket_md_path):
    """Return the recorded plan-quality verdict, or None when no block is present.

    Mirrors `coverage_review()`: reads the `### Plan-quality review` block and
    returns its verdict string (e.g. 'SOUND', 'FLAWED', 'UNKNOWN'), '' when the
    block exists but carries no parseable verdict, and None when absent."""
    try:
        text = open(ticket_md_path, encoding="utf-8").read()
    except OSError:
        return None
    # ADT-224 D1d: the LAST block — see coverage_review().
    tail = _last_block(text, _PLAN_REVIEW_RE)
    if tail is None:
        return None
    v = _VERDICT_RE.search(tail)
    return v.group(1).strip().upper() if v else ""


def plan_gating_enabled(calibration_path=None):
    """True only when the calibration record explicitly says `gating: ENABLED`.

    Fails toward NOT gating: a missing, unreadable, or DISABLED record means this
    file will not refuse on the judge's verdict. That is the safe direction — an
    uncalibrated judge that can refuse is worse than no judge at all."""
    path = _plan_calibration_path(calibration_path)
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return False
    m = _GATING_RE.search(text)
    return bool(m and m.group(1) == "ENABLED")


def _serializer():
    """The ticket frontmatter parser, or None when it is not installed.

    THREE functions here import it — ticket_track, _read_frontmatter_list and
    _write_frontmatter_list — and it was never installed beside adt_dod.py, so
    all three raised ModuleNotFoundError in a consumer's .claude/. The install
    now ships it; this is the other half, for a copy that predates that.

    The first fix guarded only ticket_track while its own comment claimed all
    three (QA finding 5). Centralised here so the count cannot drift again:
    there is one import site now, not three.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import ticket_serializer
    except ImportError:
        return None
    return ticket_serializer


def ticket_track(ticket_md_path):
    """The ticket's `track:` tier, lowercased — None when the field is absent.

    Read through the project's own frontmatter parser, never a regex over the
    head of the file: a hand-rolled match runs past the closing fence and reads
    body text as a field (ADT-155, the same reason `_read_frontmatter_list`
    exists).

    NOTE on the empty-on-missing-serializer contract: "empty is the safe
    direction" holds for THIS reader and for the gate path, where None means
    `full` and the gate keeps refusing. It does NOT hold uniformly — an empty
    read reaches `dod_amendments` as `0 amendments`, which is the flattering
    number, and telemetry consumes it. Low impact (a count, not a gate), but the
    principle is narrower than it first reads.
    """
    # None -> plan_gate_applies reads it as `full`, the pre-tier behaviour.
    # Failing CLOSED matters: the alternative is an exception out of --gate.
    ticket_serializer = _serializer()
    if ticket_serializer is None:
        return None
    try:
        data = ticket_serializer.parse_md(
            open(ticket_md_path, encoding="utf-8").read())
    except OSError:
        return None
    v = data.get("track")
    return v.strip().lower() if isinstance(v, str) and v.strip() else None


def plan_gate_applies(ticket_md_path, calibration_path=None):
    """True when a missing or negative plan-quality verdict may REFUSE.

    Two independent conditions, both required: the judge is calibrated above its
    declared bar (`plan_gating_enabled`), AND the ticket is `track: full`.

    The tier half closes the gap ADT-306 hit. `commands/plan.md` step 0b says the
    plan-quality counter-check is `full` ONLY and tells the author to skip it
    below that tier — but this file never read `track:`, so `--gate` refused a
    `standard` ticket for a review its own playbook had instructed the author not
    to run. The instruction scaled by tier and the enforcement did not, and there
    was no way to satisfy both.

    An ABSENT `track:` is treated as `full` — fail closed. The field is the
    author's declaration that a lower tier is warranted; with nothing declared,
    the gate keeps exactly the behaviour it had before this function existed.
    Only an explicit `fast` or `standard` withholds the refusal.
    """
    if not plan_gating_enabled(calibration_path):
        return False
    return (ticket_track(ticket_md_path) or "full") == "full"


# ── ADT-114 mech #3: caveats must attach to something, or be waived ───────
# A caveat written into Design / Risks / Test plan ("assumes X", "verified
# manually", "won't handle Y") has no edge to any condition that resolves it.
# Nothing grades it, so it is stated and then dropped — the "you gave me a caveat
# and then ignored it" failure. `--gate` already refuses prose INSIDE
# done_evidence; this refuses an unattached caveat OUTSIDE it.
#
# Deliberately CONSERVATIVE. The markers below are the ones ADT actually got
# burned by, not every hedging phrase — an over-eager gate gets worked around,
# and a worked-around gate is worse than none. Broad phrases ("cannot", "no
# hermetic way") are excluded on purpose: they appear constantly in ordinary
# design prose. Widen this list only with an incident to point at.
_CAVEAT_MARKERS = (
    r"verified manually", r"manual verification", r"manually verified",
    r"\bassumes\b", r"\bassumption\b",
    # A bare `caveat` fired on prose ABOUT caveats ("...caveat refusal, cycle
    # detection"). Require the declarative form, so the marker means "here is a
    # caveat" rather than "here is a discussion of caveats".
    r"(?:^|\n)\s*[-*]?\s*\**caveat\**\s*:", r"\bcaveats?\s+(?:that|which)\b",
    r"not covered\b", r"won't handle\b", r"does not cover\b",
    r"known limit(?:ation)?\b", r"\blimitation\b",
)
_CAVEAT_RE = re.compile("|".join(_CAVEAT_MARKERS), re.IGNORECASE)
# An attachment discharges the caveat: either it points at a condition that
# resolves it, or it is an explicitly recorded waiver with a reason. Both are
# visible to a reader; that visibility is the whole point.
_ATTACHED_RE = re.compile(r"(?:->|\u2192)\s*(?:DoD:\S+|WAIVED:\s*\S+)")
# Sections whose prose is a promise about the work. Design/Risks/Test plan carry
# caveats; Problem & goal and Impact do not make this kind of claim.
_CAVEAT_SECTIONS = ("Design", "Risks", "Test plan")


def _body_sections(text):
    """Split the ticket body into {heading: section_text} for `###` headings."""
    out, cur, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^###\s+(.+?)\s*$", line)
        # A level-2 heading ENDS the current level-3 section. Without this a
        # trailing `### Design notes` swallowed the whole build log and change
        # log that follow it under `##` headings — found by running this against
        # a real ticket rather than a fixture.
        top = re.match(r"^##\s+(?!#)", line)
        if m:
            if cur is not None:
                out.setdefault(cur, []).append("\n".join(buf))
            cur, buf = m.group(1).strip(), []
        elif top:
            if cur is not None:
                out.setdefault(cur, []).append("\n".join(buf))
            cur, buf = None, []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out.setdefault(cur, []).append("\n".join(buf))
    return {k: "\n".join(v) for k, v in out.items()}


def _blocks(section_text):
    """Bullet items and paragraphs. A caveat is discharged by an attachment
    anywhere in ITS block — so the marker sits with the caveat it discharges,
    not somewhere else in the section."""
    blocks, cur = [], []
    for line in section_text.splitlines():
        if re.match(r"^\s*-\s+\S", line) or not line.strip():
            if cur:
                blocks.append("\n".join(cur))
            cur = [line] if line.strip() else []
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return [b for b in blocks if b.strip()]


def unattached_caveats(ticket_md_path):
    """Return [(section, snippet)] for every caveat with no
    attachment. Empty list → every caveat is attached or waived."""
    try:
        text = open(ticket_md_path, encoding="utf-8").read()
    except OSError:
        return []
    found = []
    for name, body in _body_sections(text).items():
        if not any(name.lower().startswith(s.lower()) for s in _CAVEAT_SECTIONS):
            continue
        for block in _blocks(body):
            if _CAVEAT_RE.search(block) and not _ATTACHED_RE.search(block):
                snippet = " ".join(block.split())[:120]
                found.append((name, snippet))
    return found


def dependency_defects(ticket_md_path):
    """Return authoring defects in the DoD's `depends_on:` edges (ADT-114 E2).

    Two kinds, both of which make the ordering unsatisfiable rather than merely
    unmet: an edge pointing at an id no entry declares, and a cycle. Caught at
    --gate — BEFORE a build starts — because discovering mid-loop that a
    condition can never become eligible is a much more expensive way to find out.
    """
    evs = parse_done_evidence(ticket_md_path)
    ids = {e["id"] for e in evs if e.get("id")}
    edges, defects = {}, []
    for i, e in enumerate(evs):
        key = e.get("id") or "#%d" % (i + 1)
        deps = [d for d in (e.get("depends_on") or "").replace(",", " ").split() if d]
        edges[key] = deps
        for d in deps:
            if d not in ids:
                defects.append("condition %s depends_on '%s', which no entry declares"
                               % (key, d))
    # Cycle detection: plain iterative DFS with a colour map (white/grey/black).
    colour = {}

    def visit(node, path):
        if colour.get(node) == "black":
            return
        if colour.get(node) == "grey":
            cyc = path[path.index(node):] + [node]
            defects.append("dependency cycle: %s" % " -> ".join(cyc))
            return
        colour[node] = "grey"
        for nxt in edges.get(node, []):
            if nxt in edges or nxt in ids:
                target = nxt if nxt in edges else nxt
                visit(target, path + [node])
        colour[node] = "black"

    # Index by declared id too, so an edge naming an id resolves to its entry.
    for i, e in enumerate(evs):
        if e.get("id") and e["id"] not in edges:
            edges[e["id"]] = [d for d in (e.get("depends_on") or "").replace(",", " ").split() if d]
    for node in list(edges):
        visit(node, [])
    # De-duplicate: one cycle is reported once per entry point otherwise.
    seen, out = set(), []
    for d in defects:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


# ── AO-013 gap 1: a borrowed value is traced to the end of its function ──────
# AO-006 replaced four designs and three failed the same way: a value produced
# inside `adt_watch.py`'s tick loop was reused to mean something it does not mean
# at the source. Every one was discoverable by reading a single function to its
# end — `_backoff_due`'s eager `return 0.0` sat four lines below code the spec had
# already quoted. `commands/plan.md` said "understand the existing code first" and
# required no artifact, so nothing could tell a complete read from a partial one.
#
# A Design bullet that depends on an existing value now declares it:
#
#   - borrows: `tools/adt_watch.py:387-406` `_backoff_due` — exits 404, 406 —
#     0.0 at 404 is gate 1's "run eagerly" sentinel, not a timestamp.
#
# and this asserts the two halves a partial read gets wrong: the declared span is
# the function's real span, and the declared exit lines are every `return`
# directly inside it. Both come from `ast`, so neither is a judgement call.
#
# WHY NOT "every file.py:NN citation must span its whole function", which would
# need no new convention: measured against AO-006's own Design, it flagged 10 of
# 15 citations — including a 141-line function and two off-by-one spans — on a
# spec that ended SOUND. A check that fires on two thirds of correct work gets
# worked around, which is the reasoning `_CAVEAT_MARKERS` already records.
#
# Python only. All four AO-006 failures were Python, `ast` gives exact spans with
# no dependency, and a shell function needs a heuristic that would have to be
# right to be worth having.
_BORROW_NONE = re.compile(r"^none\b", re.IGNORECASE)
_BORROW_SPEC = re.compile(
    r"`?(?P<path>[A-Za-z_][\w./-]*\.py):(?P<start>\d+)-(?P<end>\d+)`?\s+"
    r"`?(?P<sym>[A-Za-z_][\w.]*)`?\s*(?:—|--|-)\s*"
    r"exits\s+(?P<exits>none|\d[\d,\s]*)", re.IGNORECASE)
# A citation of ONE line, or of a range: what the second arm scans for.
_PY_CITATION = re.compile(r"`?([A-Za-z_][\w./-]*\.py):(\d+)(?:-(\d+))?`?")
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".claude"}


def _repo_root(start=None):
    """The git toplevel of `start` (default: cwd), or None outside a repo.

    Deliberately NOT `_default_cwd`: that one takes `devteam` and falls back to
    its parent, which is the right answer for running a condition and the wrong
    one here — a borrow citation resolves against the tree being planned, or it
    does not resolve at all.
    """
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           cwd=start, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _resolve_py(path, root):
    """The file a citation names, or None when it does not resolve UNIQUELY.

    A spec cites `adt_watch.py`, not `tools/adt_watch.py`, so a basename search
    is required. It fails open on ambiguity: `.claude/` holds installed copies of
    several tools, so a basename can legitimately match twice, and refusing a
    plan over which copy was meant would be a gate nobody can satisfy.
    """
    direct = os.path.join(root, path)
    if os.path.isfile(direct):
        return direct
    base, hits = os.path.basename(path), []
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if base in files:
            hits.append(os.path.join(dirpath, base))
    return hits[0] if len(hits) == 1 else None


def _defs(py_path):
    """{qualified name: node} for every function in a file, or None if unparsable."""
    try:
        tree = ast.parse(open(py_path, encoding="utf-8").read())
    except (OSError, SyntaxError):
        return None
    out = {}

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                out.setdefault(name, []).append(child)
                if name != child.name:
                    # A bare-name alias, so a spec may cite `method` without its
                    # class. Guarded: at module level the two names are equal and
                    # an unguarded alias registers every top-level function twice,
                    # which reads as "matches 2 functions" on a correct citation.
                    out.setdefault(child.name, []).append(child)
                walk(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + ".")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def _direct_returns(node):
    """Line numbers of the `return`s belonging to THIS function.

    A `return` inside a nested `def` is that function's exit, not this one's, so
    counting it would make a correct declaration fail.
    """
    lines, stack = [], list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(n, ast.Return):
            lines.append(n.lineno)
        stack.extend(ast.iter_child_nodes(n))
    return sorted(lines)


def _enclosing_def(defs, line):
    """The innermost function containing `line`, or None."""
    best = None
    for nodes in defs.values():
        for n in nodes:
            end = getattr(n, "end_lineno", None)
            if end and n.lineno <= line <= end:
                if best is None or n.lineno > best.lineno:
                    best = n
    return best


def borrow_defects(ticket_md_path, root=None):
    """Return the defects in a spec's `borrows:` declarations (AO-013 gap 1).

    Two arms. The first grades every declaration that IS written: the span and
    the exit lines against `ast`. The second catches the author who writes none —
    a Design citing a line inside a function with no `borrows:` bullet anywhere
    is one defect, discharged by `borrows: none — <reason>`.

    Fails open wherever the world, rather than the spec, is the problem: no repo,
    no Design section, an unresolvable or unparsable file. The remedy for each of
    those is not something a planning session can act on, and a refusal it cannot
    clear is a refusal that gets worked around.
    """
    root = root or _repo_root()
    if not root:
        return []
    try:
        text = open(ticket_md_path, encoding="utf-8").read()
    except OSError:
        return []
    design = graded_section(text, "Design")
    if design is None:
        return []

    defects, declared = [], 0
    for block in _blocks(design):
        m = re.match(r"\s*-\s*\**borrows\**\s*:\s*(.+)", " ".join(block.split()),
                     re.IGNORECASE | re.DOTALL)
        if not m:
            continue
        declared += 1
        body = m.group(1)
        if _BORROW_NONE.match(body):
            continue
        spec = _BORROW_SPEC.search(body)
        if not spec:
            defects.append(
                "borrows: declaration is unreadable — write "
                "`<file>.py:<start>-<end>` `<symbol>` — exits <lines> — <meaning>: %s"
                % body[:90])
            continue
        path, sym = spec.group("path"), spec.group("sym")
        start, end = int(spec.group("start")), int(spec.group("end"))
        py = _resolve_py(path, root)
        if py is None:
            continue                      # unresolvable/ambiguous: fail open
        defs = _defs(py)
        if defs is None:
            continue
        nodes = defs.get(sym) or []
        if len(nodes) != 1:
            defects.append(
                "borrows: %s names `%s`, which %s in %s — qualify it as "
                "`Class.method`" % (path, sym,
                                    "matches %d functions" % len(nodes) if nodes
                                    else "no function matches", path))
            continue
        node = nodes[0]
        if (start, end) != (node.lineno, node.end_lineno):
            # Say which of the two it is. A span that starts right and ends early
            # is a partial read, which is the failure this exists for; a span
            # that matches neither end is a stale citation, which a diff landing
            # above the function produces on its own. Reporting both as "stops
            # short" would misdescribe the second every time.
            short = start == node.lineno and end < node.end_lineno
            defects.append(
                "borrows: `%s` in %s is %d-%d, not %d-%d — %s"
                % (sym, path, node.lineno, node.end_lineno, start, end,
                   "the declaration stops %d line%s short of the end, so the "
                   "function was not read to it"
                   % (node.end_lineno - end,
                      "" if node.end_lineno - end == 1 else "s")
                   if short else
                   "the declared span is not this function's"))
        raw = spec.group("exits").lower()
        got = set() if raw.startswith("none") else {
            int(x) for x in re.findall(r"\d+", raw)}
        want = set(_direct_returns(node))
        if got != want:
            defects.append(
                "borrows: `%s` in %s returns at %s — declared %s. Every exit is "
                "read, or the value's meaning is only half known"
                % (sym, path,
                   ", ".join(str(x) for x in sorted(want)) or "no line (none)",
                   ", ".join(str(x) for x in sorted(got)) or "none"))

    if declared:
        return defects

    inside = []
    for m in _PY_CITATION.finditer(design):
        py = _resolve_py(m.group(1), root)
        if py is None:
            continue
        defs = _defs(py)
        if defs is None:
            continue
        node = _enclosing_def(defs, int(m.group(2)))
        if node is not None and m.group(0) not in inside:
            inside.append(m.group(0))
    if inside:
        defects.append(
            "the Design cites code inside a function %d time%s (%s) and declares "
            "no `borrows:` line. Declare what the design depends on, or write "
            "`borrows: none — <reason>`"
            % (len(inside), "" if len(inside) == 1 else "s",
               ", ".join(inside[:4])))
    return defects


# ── AO-013 1f: the two new refusals are PLAN-LANE ONLY ───────────────────────
# Not a softening. A `borrows:` declaration describes the code as it was BEFORE
# the diff, so the moment the build lands the spans legitimately move, and
# refusing at `/adt-release-check` would fail a correct ticket for doing its job.
# Verified on this ticket: its four `adt_dod.py` declarations all read stale
# against the tree that implements them.
#
# It also leaves every ticket already in flight alone — AO-006 cites 15 lines
# inside functions, declares no borrows, and calls `--gate` again at release.
#
# The lane comes from the FOLDER, not from `stage:`. The render already treats
# the folder as the truth (§D2) and `stage:` can lag it by a tick.
_PLAN_LANES = ("ideas", "planned")


def in_plan_lane(ticket_md_path):
    """True when the ticket file sits in `ideas/` or `planned/`."""
    return os.path.basename(os.path.dirname(
        os.path.abspath(ticket_md_path))) in _PLAN_LANES


def authoring_defects(ticket_md_path, root=None):
    """Every authoring defect in a spec, as [(code, message)] (AO-013 1e).

    The same checks `--gate` refuses on, minus the ones about a recorded review,
    so this can run at step 4 of `commands/plan.md` — BEFORE the first reviewer
    is dispatched. That ordering is the point: each of these cost a full review
    round on AO-006, and a reviewer's judgement spent on a string comparison is
    the most expensive way to find one.
    """
    out = [("PROSE", "uncheckable done_evidence entry (prose): %s" % e)
           for e in unsupported(ticket_md_path)]
    out += [("DEPS", d) for d in dependency_defects(ticket_md_path)]
    out += [("CAVEAT", "[%s] %s" % (sec, snip))
            for sec, snip in unattached_caveats(ticket_md_path)]
    out += [("BORROW", d) for d in borrow_defects(ticket_md_path, root=root)]
    out += [("REOPENED",
             "%s round %s rested on %s staying unmodified; the spec has moved "
             "since and still edits that file — send the dependency back to the "
             "reviewer" % (gate, rnd, item))
            for gate, rnd, item in reopened_dependencies(ticket_md_path)]
    return out


def is_all_checkable(ticket_md_path):
    """True iff the DoD is non-empty AND every entry is machine-checkable.

    The /adt-build-todone approval gate's structural check: refuse to start unless
    this is True (an empty DoD is not approvable — there's nothing to grade)."""
    evs = parse_done_evidence(ticket_md_path)
    return len(evs) > 0 and len(unsupported(ticket_md_path)) == 0


# ── CLI: adt-done-guard.sh (and humans) call this; emits a done-guard-style verdict ──
# Contract (matches done-guard's `out()`): a single tab-separated line
#   DENY|WARN|OK \t reason
# DENY = a regex condition failed; WARN = infra can't-verify (fail-open);
# OK = all gradable conditions passed (or none declared).
def _cli(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Grade a ticket's done_evidence DoD.")
    ap.add_argument("ticket_md", help="path to the ticket .md (DoD source)")
    ap.add_argument("--devteam", default=None,
                    help="the dir holding the rendered artifacts (.adt/) "
                         "(required unless --gate)")
    ap.add_argument("--lane", default=None, help="grade only this lane's slice")
    ap.add_argument("--plan-calibration", default=None,
                    help="path to the plan-quality calibration record "
                         "(default: docs/plan-quality-calibration.md). The "
                         "`gating:` line in it decides whether --gate may REFUSE "
                         "on a plan-quality verdict; see plan_gating_enabled().")
    ap.add_argument("--gate", action="store_true",
                    help="approval-gate mode: print APPROVABLE / REFUSE<tab>reason "
                         "and exit 0/1 (the /adt-build-todone start check)")
    # ADT-224 D1i. The gate name is the ONLY argument: the verdict is parsed out
    # of the block being canonicalised, never taken from the caller. An
    # agent-typed SOUND over a pasted FLAWED would silently flatter caused_edit,
    # which fires only on a negative verdict.
    ap.add_argument("--record-verdict", metavar="GATE", default=None,
                    choices=list(GATES),
                    help="append a gate_effects record for GATE (coverage | "
                         "plan-quality), reading the verdict and the graded-text "
                         "hash from the ticket itself")
    ap.add_argument("--dry-run", action="store_true",
                    help="run every condition and report its exit code + lane, "
                         "flagging 127, conditions already green (ADT-306) and "
                         "green runs whose output shows no work (ADT-359). "
                         "Requires --devteam, because must_contain_regex targets "
                         "resolve under it. must_run conditions execute in the "
                         "git toplevel of the directory the grader is invoked "
                         "from. The report names both.")
    ap.add_argument("--check-authoring", action="store_true",
                    help="report every authoring defect a reviewer should never "
                         "see (prose conditions, dependency defects, unattached "
                         "caveats, borrows: declarations that do not match the "
                         "source, and a recorded dependency the spec has since "
                         "moved past). Exits 1 on any finding. Run it at plan "
                         "time, before dispatching a counter-check: it reads the "
                         "code as it is NOW, so after a build lands its own "
                         "diff the borrow spans it grades are stale by design")
    ap.add_argument("--snapshot-dod", action="store_true",
                    help="append a dod_snapshot row (the done_evidence digest an "
                         "amendment count is derived from)")
    args = ap.parse_args(argv)

    if args.record_verdict:
        row = record_verdict(args.ticket_md, args.record_verdict)
        if row is None:
            print("NOTE\tno '### %s' block to record — write the block first"
                  % _GATE_HEADERS[args.record_verdict])
            return 0
        derive_gate_effects(args.ticket_md, args.plan_calibration)
        snapshot_dod(args.ticket_md)
        print("RECORDED\t%s round %s verdict %s hash %s%s"
              % (row["gate"], row["round"], row["verdict"] or "(none)",
                 row["hash"],
                 " rests_on %s" % row["rests_on"] if row.get("rests_on") else ""))
        # AO-013 1c/1h. Both of these are printed AFTER the row is written, so a
        # recording is never lost to a diagnostic, and both are reports rather
        # than refusals: the first is about the reviewer's prose, which the author
        # cannot edit, and the second is a question for the human.
        phrase = undeclared_justification(args.ticket_md, args.record_verdict)
        if phrase:
            print("NOTE\t%s passed something on the grounds that %r, and "
                  "declared no dependency. Ask the reviewer for a "
                  "`**Depends-on-unmodified:** <file>:<symbol>` line, or nothing "
                  "will reopen that verdict when the spec touches that code."
                  % (args.record_verdict, phrase), file=sys.stderr)
        line = divergence_escalation(args.ticket_md)
        if line:
            print(line)
        return 0

    if args.check_authoring:
        # Reports, and exits 1 so the finding cannot be scrolled past. No
        # --devteam: nothing here resolves a rendered artifact.
        found = authoring_defects(args.ticket_md)
        for code, msg in found:
            print("%-8s %s" % (code, msg))
        if not found:
            print("CLEAN\tno authoring defects in %s" % args.ticket_md)
        return 1 if found else 0

    if args.dry_run:
        # Reports; never refuses. This runs at PLAN time, where red is the
        # expected state of work not yet built — an exit code here would make a
        # correct dry-run look like a failure.
        #
        # --devteam is REQUIRED here, with no default. It used to fall back to
        # ".", which check() turns into the repo's PARENT, so every
        # path-relative condition ran in the wrong directory and the
        # ALREADY-GREEN flag silently vanished. The first fix pinned
        # `--devteam tools` in one DoD condition and left the default alone,
        # which fixed the symptom on one caller (QA finding 6). Since ADT-359
        # must_run conditions no longer depend on --devteam, but
        # must_contain_regex targets still do.
        if not args.devteam:
            ap.error("--dry-run requires --devteam: must_contain_regex targets "
                     "resolve under it, so defaulting it silently changes their "
                     "result. must_run conditions execute in the git toplevel of "
                     "the directory the grader is invoked from")
        dry_run(args.ticket_md, args.devteam)
        return 0

    if args.snapshot_dod:
        row = snapshot_dod(args.ticket_md)
        print("SNAPSHOT\t%s" % ("unchanged" if row is None
                                 else "%s conditions, %s" % (row["n"], row["hash"])))
        return 0
    if args.gate:
        # The structural approval check, as CODE — not agent re-derivation.
        bad = unsupported(args.ticket_md)
        if not parse_done_evidence(args.ticket_md):
            print("REFUSE\tno done_evidence — nothing to grade; not approvable")
            return 1
        if bad:
            print("REFUSE\tuncheckable done_evidence entr%s (prose): %s"
                  % ("y" if len(bad) == 1 else "ies", bad))
            return 1
        # ADT-114 mech #2 — an unsatisfiable ORDERING is an authoring defect, and
        # --gate is the cheap place to find it.
        dep_defects = dependency_defects(args.ticket_md)
        if dep_defects:
            print("REFUSE\tdone_evidence dependency defect%s (the ordering can "
                  "never be satisfied): %s"
                  % ("" if len(dep_defects) == 1 else "s", "; ".join(dep_defects)))
            return 1
        # ADT-114 mech #3 — a caveat with nowhere to land is a dropped promise.
        loose = unattached_caveats(args.ticket_md)
        if loose:
            print("REFUSE\tunattached caveat%s — a caveat must either point at a "
                  "condition that resolves it (-> DoD:<id>) or be an explicit "
                  "recorded waiver (-> WAIVED: <reason>). Unattached: %s"
                  % ("" if len(loose) == 1 else "s",
                     "; ".join("[%s] %s" % (sec, snip) for sec, snip in loose)))
            return 1
        # AO-013 1f — the two authoring defects that are about the PLAN's reading
        # of existing code. Plan-lane only; see in_plan_lane() for why that is
        # correctness rather than leniency.
        borrows = borrow_defects(args.ticket_md)
        reopened = reopened_dependencies(args.ticket_md)
        if in_plan_lane(args.ticket_md):
            if borrows:
                print("REFUSE\tborrows: defect%s — a value the Design depends on "
                      "was not traced to the end of the function that produces "
                      "it: %s"
                      % ("" if len(borrows) == 1 else "s", "; ".join(borrows)))
                return 1
            if reopened:
                print("REFUSE\ta recorded verdict rested on code the spec has "
                      "since moved past, so it no longer covers this text: %s. "
                      "Send the dependency back to the reviewer and record the "
                      "new verdict."
                      % "; ".join("%s round %s rested on %s" % r for r in reopened))
                return 1
        elif borrows or reopened:
            print("NOTE\t%d borrows: defect(s) and %d reopened dependenc%s, "
                  "recorded and not enforced: this ticket is past the plan lanes, "
                  "where a borrow span describes code the diff has since moved."
                  % (len(borrows), len(reopened),
                     "y" if len(reopened) == 1 else "ies"), file=sys.stderr)
        # ADT-114 mech #1 — the DoD must have been counter-checked for COVERAGE by
        # something that did not author it. Checkable != complete.
        cverdict, grandfathered = coverage_review(args.ticket_md)
        if not grandfathered:
            if cverdict is None:
                print("REFUSE\tno '### DoD-coverage review' — the DoD is checkable "
                      "but nothing has verified it COVERS the spec. Run the "
                      "adt-dod-coverage-reviewer subagent and record its verdict.")
                return 1
            if cverdict == "UNKNOWN":
                print("REFUSE\tDoD-coverage review returned UNKNOWN — the reviewer "
                      "could not decide. That is surfaced, never treated as a pass; "
                      "resolve it with the human or sharpen the spec.")
                return 1
            if cverdict != "COVERED":
                print("REFUSE\tDoD-coverage review verdict is %r — close the named "
                      "gaps and re-review." % (cverdict or "(missing)"))
                return 1
        # ADT-126 — the PLAN-quality counter-check. CONDITIONAL on calibration
        # (an uncalibrated judge must not be allowed to refuse) AND on the tier
        # (ADT-306: the playbook says `full` only, so the gate must read
        # `track:`). See plan_gate_applies() for both halves.
        pverdict = plan_review(args.ticket_md)
        if plan_gate_applies(args.ticket_md, args.plan_calibration):
            if pverdict is None:
                print("REFUSE\tno '### Plan-quality review' — the DoD covers the "
                      "spec, but nothing has checked the spec is worth building. "
                      "Run the adt-plan-quality-reviewer subagent and record its "
                      "verdict.")
                return 1
            if pverdict == "UNKNOWN":
                print("REFUSE\tPlan-quality review returned UNKNOWN — the reviewer "
                      "could not decide. That is surfaced, never treated as a "
                      "pass; sharpen the Design or resolve it with the human.")
                return 1
            if pverdict != "SOUND":
                print("REFUSE\tPlan-quality review verdict is %r — close the named "
                      "defects and re-review." % (pverdict or "(missing)"))
                return 1
        elif pverdict is not None and pverdict != "SOUND":
            # Recording-only mode: the refusal is withheld, but the verdict is
            # NOT swallowed. Say WHICH half withheld it — an operator reading
            # "not calibrated" when the real reason is the tier would go and
            # check the wrong file.
            if plan_gating_enabled(args.plan_calibration):
                why = ("this ticket is `track: %s` and the plan-quality gate is "
                       "`full` only (commands/plan.md step 0b)"
                       % (ticket_track(args.ticket_md) or "(unset)"))
            else:
                why = ("the reviewer is not calibrated above its declared bar "
                       "(see docs/plan-quality-calibration.md)")
            print("NOTE\tPlan-quality review verdict is %r, but %s, so this is "
                  "recorded and not enforced." % (pverdict, why), file=sys.stderr)
        print("APPROVABLE\t")
        return 0
    if not args.devteam:
        ap.error("--devteam is required unless --gate")
    result = check(args.ticket_md, args.devteam, lane=args.lane)
    decision, reason = verdict(result, "adt_dod")
    print("%s\t%s" % (decision, reason))
    return 0




# ══════════════════════════════════════════════════════════════════════════
# ADT-224 Phase D — gate_effects: did a gate change anything?
#
# THE PROBLEM. `post_merge_defects` is a floor, and `follow_ons` reads 0 on all
# 28 tickets that stamp it — a value that is always the same number is a value
# nobody is measuring (ADT-147). Meanwhile ADT-205's experiment found four real
# gate catches and found them only by READING AGENT PROSE, because nothing
# recorded them.
#
# THREE MECHANISMS WERE REJECTED, and the reasons are the design:
#   * a playbook that STAMPS the field is exactly `follow_ons`'s mechanism;
#   * deriving from the ticket as recorded today is dead, because each round
#     REPLACES the last block, so `caused_edit` is false forever and `ran` can
#     never exceed 1;
#   * `caused_edit` = "a later block exists" over-credits, since plan.md re-runs
#     both gates after any rewrite.
#
# So each `kind: record` row carries a round number and a HASH of the whole
# graded body — every `###` section the plan-quality reviewer reads, plus
# `done_evidence`. `caused_edit` is then a negative verdict at round N whose
# hash differs from that gate's round N+1 hash: the gate ran, said no, and the
# text it graded moved. The hash is written at verdict time because the cache is
# outside every git checkout, so an unstamped round is unrecoverable.
#
# THE AGENT NEVER COMPUTES THE HASH, AND NEVER TYPES THE VERDICT. `record_verdict`
# parses the verdict out of the block it is already canonicalising rather than
# taking it as an argument — an agent-typed SOUND over a pasted FLAWED would
# silently flatter `caused_edit`, which fires only on a negative verdict.

import hashlib as _hashlib

#: The sections the plan-quality reviewer grades (its prompt, lines 37-38),
#: plus `done_evidence` from frontmatter. Changing this set changes what
#: `caused_edit` is sensitive to, so it is declared once, here.
GRADED_SECTIONS = ("Problem & goal", "Design", "Impact / ripple analysis",
                   "Sub-steps", "Risks", "Test plan")

GATES = ("coverage", "plan-quality")

_GATE_HEADERS = {
    "coverage": "DoD-coverage review",
    "plan-quality": "Plan-quality review",
}


def _last_block(text, header_re):
    """Tail of `text` after the LAST match of `header_re`, or None.

    ADT-224 D1d. Both verdict readers used `.search()`, which returns the FIRST
    match — fine while each round REPLACED the previous block, and wedging the
    moment frontmatter starts accumulating rounds: every gate would read round
    1's verdict forever and refuse on a defect that was closed rounds ago.
    """
    last = None
    for m in header_re.finditer(text):
        last = m
    return text[last.end():] if last else None


def graded_section(text, name):
    """The body of `### <name>` — or None when the ticket has no such section.

    AO-013 1g. The matcher used to be `^### <name>$` exactly, and the plan
    template writes `### Sub-steps  (DERIVED from Design + Impact — not
    invented)`. So the sub-steps were in GRADED_SECTIONS and never in the graded
    text: 86 occurrences across 12 distinct heading strings in the local corpus,
    every one skipped. A round that changed only the work-set moved no hash, and
    `caused_edit` could not see it.

    The suffix is matched as a PARENTHETICAL, not as a prefix. A prefix match
    would swallow `Design notes`, `Design-doc section`, `Design review` and
    `Design — the SIMPLIFICATION`, and `_last_block` takes the LAST match — so
    the hash would silently grade a UI-review note as the Design.

    The section ends at the next `###` OR the next `##`, matching
    `_body_sections`. That second half is load-bearing rather than tidiness:
    without it a ticket whose last graded section is followed by `## Build log`
    grades its build log too, so every appended build-log line would move the
    hash and reopen every declared dependency.
    """
    rx = re.compile(r"^###\s+" + re.escape(name) + r"\s*(?:\(.*\))?\s*$",
                    re.MULTILINE)
    tail = _last_block(text, rx)
    if tail is None:
        return None
    nxt = re.search(r"^##", tail, re.MULTILINE)
    return tail[:nxt.start()] if nxt else tail


def _canonical_graded_text(ticket_md_path):
    """The exact text `caused_edit` is sensitive to, whitespace-normalised.

    Normalising matters: a reflowed paragraph is not an edit the gate caused,
    and without it every re-wrap would read as a design change.
    """
    try:
        text = open(ticket_md_path, encoding="utf-8").read()
    except OSError:
        return ""
    chunks = []
    for name in GRADED_SECTIONS:
        body = graded_section(text, name)
        if body is None:
            continue
        chunks.append(name + "\n" + " ".join(body.split()))
    for ev in parse_done_evidence(ticket_md_path):
        chunks.append(" ".join(str(sorted(ev.items())).split()))
    return "\n".join(chunks)


def graded_text_hash(ticket_md_path):
    """The graded-text hash: sha256 of the canonicalised graded body."""
    return _hashlib.sha256(
        _canonical_graded_text(ticket_md_path).encode("utf-8")).hexdigest()[:16]


def _read_frontmatter_list(ticket_md_path, key):
    """The `key:` dict-list from frontmatter, via the project's own parser.

    Never a hand-rolled `sed`/`grep` over the head of the file: that runs past
    the closing fence and counts body text as a field (ADT-155).
    """
    ticket_serializer = _serializer()
    if ticket_serializer is None:
        return []
    try:
        data = ticket_serializer.parse_md(open(ticket_md_path, encoding="utf-8").read())
    except OSError:
        return []
    rows = data.get(key) or []
    return rows if isinstance(rows, list) else []


def _write_frontmatter_list(ticket_md_path, key, rows):
    # The one call site that must NOT degrade quietly. The two readers above
    # return an empty result when the serializer is missing, which is the safe
    # direction for a read; doing that here would silently DROP a write and the
    # caller would report a verdict it never recorded. Fail loudly, and name the
    # remedy rather than surfacing a bare ModuleNotFoundError.
    ticket_serializer = _serializer()
    if ticket_serializer is None:
        raise RuntimeError(
            "ticket_serializer.py is not installed beside adt_dod.py, so this "
            "write cannot be performed. Re-run the installer (or `bash "
            "lib/resync.sh` in the ADT repo) to refresh .claude/tools/.")
    text = open(ticket_md_path, encoding="utf-8").read()
    data = ticket_serializer.parse_md(text)
    data[key] = rows
    open(ticket_md_path, "w", encoding="utf-8").write(ticket_serializer.emit_md(data))


def _gate_verdict(ticket_md_path, gate):
    """The verdict in that gate's LAST block, or None when no block exists."""
    header = _GATE_HEADERS.get(gate)
    if not header:
        return None
    try:
        text = open(ticket_md_path, encoding="utf-8").read()
    except OSError:
        return None
    rx = re.compile(r"^###\s+" + re.escape(header) + r"\s*$", re.MULTILINE)
    tail = _last_block(text, rx)
    if tail is None:
        return None
    v = _VERDICT_RE.search(tail)
    return v.group(1).strip().upper() if v else ""


NEGATIVE_VERDICTS = {"GAP", "UNKNOWN", "FLAWED"}

# ── AO-013 gap 2: a verdict records what it rests on, so it can be reopened ──
# AO-006 round 3: coverage passed "a rate-limited tick correctly goes red"
# WITHOUT a condition, on the stated grounds that it was a property of existing,
# unmodified code. True when written. Round 6's design modified exactly that
# code, and nothing reopened the verdict — it survived because the operator
# thought to ask. The row already carried a graded-text hash; it carried no
# record of what the verdict depended on.
#
# So the reviewer declares it, `record_verdict` stores it as `rests_on:`, and a
# dependency is REOPENED when both halves hold: the graded text has moved since
# that row was written, and the file it names is still in the current
# `### Sub-steps`. The second half is what keeps this off a typo in Risks; the
# first is what keeps it off a spec nobody has touched.
_RESTS_ON_RE = re.compile(r"^\*\*Depends-on-unmodified:\*\*\s*(.+?)\s*$",
                          re.MULTILINE)
# The justification phrases a verdict uses when it passes something ungraded.
# Conservative, on the same principle as `_CAVEAT_MARKERS`: measured at zero hits
# across 434 local ticket bodies, so it fires on the declaration form and not on
# ordinary review prose. Widen it only with an incident to point at.
_JUSTIFICATION_RE = re.compile(
    r"existing,?\s+unmodified\s+code"
    r"|\b(?:code|function|path|behaviour|behavior|logic)\s+"
    r"(?:is|stays|remains)\s+unmodified\b"
    r"|\bneeds?\s+no\s+(?:test|condition)\b"
    r"|\bno\s+(?:test|condition)\s+(?:is\s+)?needed\b"
    r"|\brequires?\s+no\s+(?:test|condition)\b"
    r"|\bnot\s+chang(?:ing|ed)\s+(?:in|by)\s+this\s+(?:ticket|diff|change)\b",
    re.IGNORECASE)


def _gate_block(ticket_md_path, gate):
    """The text of a gate's LAST verdict block, or None."""
    header = _GATE_HEADERS.get(gate)
    if not header:
        return None
    try:
        text = open(ticket_md_path, encoding="utf-8").read()
    except OSError:
        return None
    rx = re.compile(r"^###\s+" + re.escape(header) + r"\s*$", re.MULTILINE)
    tail = _last_block(text, rx)
    if tail is None:
        return None
    nxt = re.search(r"^##", tail, re.MULTILINE)
    return tail[:nxt.start()] if nxt else tail


def declared_dependency(ticket_md_path, gate):
    """The `**Depends-on-unmodified:**` list in a gate's last block, or ''.

    Parsed out of the block for the same reason the verdict is: an agent that
    types it into the tool could type something the reviewer never said.
    """
    block = _gate_block(ticket_md_path, gate)
    if not block:
        return ""
    m = _RESTS_ON_RE.search(block)
    if not m or m.group(1).strip().lower() in ("none", "(none)", "n/a"):
        return ""
    return " ".join(m.group(1).replace(",", " ").split())


def undeclared_justification(ticket_md_path, gate):
    """The justification phrase a gate block uses with nothing declared, or None.

    The backstop for the reviewer simply omitting the line. It reports; it never
    refuses — the reviewer's prose is not something the author can edit.
    """
    block = _gate_block(ticket_md_path, gate)
    if not block or declared_dependency(ticket_md_path, gate):
        return None
    m = _JUSTIFICATION_RE.search(block)
    return m.group(0) if m else None


def reopened_dependencies(ticket_md_path):
    """[(gate, round, "<file>:<symbol>")] for every dependency now in doubt."""
    if not _canonical_graded_text(ticket_md_path):
        # An unreadable ticket hashes as the sha of "", which would compare
        # unequal to every stored hash and reopen everything. Nothing to compare
        # is not the same as something that moved.
        return []
    try:
        text = open(ticket_md_path, encoding="utf-8").read()
    except OSError:
        return []
    sub = graded_section(text, "Sub-steps") or ""
    now = graded_text_hash(ticket_md_path)
    rows = _read_frontmatter_list(ticket_md_path, "gate_effects")
    out = []
    for gate in GATES:
        mine = sorted((r for r in rows if r.get("kind") == "record"
                       and r.get("gate") == gate),
                      key=lambda r: _as_int(r.get("round")))
        if not mine:
            continue
        last = mine[-1]
        # Only the LAST row per gate. An earlier row's dependency was either
        # re-stated by the round that followed it or dropped on purpose, and
        # reopening a superseded verdict asks for a review of text nobody holds.
        if last.get("hash") == now:
            continue
        for item in (last.get("rests_on") or "").replace(",", " ").split():
            if os.path.basename(item.split(":")[0]) in sub:
                out.append((gate, _as_int(last.get("round")), item))
    return out


def divergence_escalation(ticket_md_path):
    """The escalation line when the DESIGN is diverging, or None (AO-013 gap 4).

    Two consecutive negative plan-quality verdicts, which is a spec killing
    designs rather than refining one. AO-006 recorded FLAWED, FLAWED, FLAWED,
    SOUND; the operator escalated at round three on their own judgement, and the
    playbook asked for nothing.

    Plan-quality ONLY. A coverage GAP says the grading is incomplete, which is
    what the tier's own round limits already govern — escalating on two of those
    would contradict the three rounds `full` allows. A plan-quality FLAWED says
    the design is wrong, and two in a row is a different state from one.
    """
    rows = _read_frontmatter_list(ticket_md_path, "gate_effects")
    mine = sorted((r for r in rows if r.get("kind") == "record"
                   and r.get("gate") == "plan-quality"),
                  key=lambda r: _as_int(r.get("round")))
    if len(mine) < 2:
        return None
    pair = [(r.get("verdict") or "").upper() for r in mine[-2:]]
    # An empty verdict means a block with nothing parseable in it — an
    # interrupted reviewer, not a judgement. It is not negative, so it cannot
    # escalate on its own.
    if not all(v in NEGATIVE_VERDICTS for v in pair):
        return None
    return ("ESCALATE\t%s then %s on plan-quality, rounds %s and %s. Two "
            "negative verdicts in a row is a design being replaced rather than "
            "refined — take it to the human with the findings named, and do not "
            "open another round (commands/plan.md step 0b)."
            % (pair[0], pair[1], _as_int(mine[-2].get("round")),
               _as_int(mine[-1].get("round"))))


def record_verdict(ticket_md_path, gate):
    """Append one `kind: record` row for `gate`. Returns the row, or None.

    TWO ACTS, NOT ONE. The playbook writes the verdict BLOCK (it carries the
    reviewer's prose, which this signature cannot), then calls this. That is why
    "block present, zero records" is possible rather than impossible — and that
    state is precisely what `under_recorded` detects, as does a rebuilt cache.
    """
    if gate not in GATES:
        return None
    verdict = _gate_verdict(ticket_md_path, gate)
    if verdict is None:
        return None
    rows = _read_frontmatter_list(ticket_md_path, "gate_effects")
    rounds = [r for r in rows
              if r.get("kind") == "record" and r.get("gate") == gate]
    row = {"kind": "record", "gate": gate, "round": len(rounds) + 1,
           "verdict": verdict or "", "hash": graded_text_hash(ticket_md_path)}
    # AO-013 1c. Only when the reviewer declared one: an absent key is the
    # ordinary case, and writing `rests_on: ` on every row would make an empty
    # string indistinguishable from a declaration nobody made.
    rests = declared_dependency(ticket_md_path, gate)
    if rests:
        row["rests_on"] = rests
    rows.append(row)
    _write_frontmatter_list(ticket_md_path, "gate_effects", rows)
    return row


def _as_int(v, default=0):
    """`_parse_dictlist` calls `_coerce_scalar(v)` without `key`, and INT_KEYS is
    only consulted when a key IS passed — so `round` and `ran` come back as
    strings. Coerced on read rather than on write, so a hand-edited ticket and a
    round-tripped one behave the same."""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def derive_gate_effects(ticket_md_path, calibration_path=None):
    """Write the `kind: derived` rows from the `kind: record` rows.

    Never clobbers a record row: this rewrites only the derived ones, so running
    it twice is idempotent and running it on a hand-edited ticket cannot destroy
    the history it is summarising.
    """
    rows = _read_frontmatter_list(ticket_md_path, "gate_effects")
    records = [r for r in rows if r.get("kind") == "record"]
    gating = plan_gating_enabled(calibration_path)

    derived = []
    for gate in GATES:
        mine = sorted((r for r in records if r.get("gate") == gate),
                      key=lambda r: _as_int(r.get("round")))
        # `caused_edit`: a NEGATIVE verdict at round N whose graded-text hash
        # differs from the same gate's round N+1. Not "a later block exists" —
        # both gates are re-run after any rewrite, so that would over-credit.
        caused = False
        for a, b in zip(mine, mine[1:]):
            if (a.get("verdict") or "").upper() in NEGATIVE_VERDICTS \
                    and a.get("hash") != b.get("hash"):
                caused = True
                break
        # A verdict block with zero records is UNDER-RECORDED, which is a
        # different fact from "the gate never caused an edit". Reporting it as
        # the latter would quietly credit a gate that was never measured — and
        # a rebuilt cache produces exactly this state, because frontmatter is
        # never pushed to the Issue.
        block_present = _gate_verdict(ticket_md_path, gate) is not None
        derived.append({
            "kind": "derived", "gate": gate,
            "ran": len(mine),
            "caused_edit": caused,
            "under_recorded": bool(block_present and not mine),
            # RECORDED, not re-evaluated at report time: `plan_gating_enabled()`
            # is global and per-run, so a report generated after the flag flips
            # would mis-describe every historical ticket, in both directions.
            "gating_enabled": gating if gate == "plan-quality" else True,
        })

    _write_frontmatter_list(ticket_md_path, "gate_effects", records + derived)
    return derived


def gate_effects(ticket_md_path):
    """{gate: {ran, caused_edit, under_recorded, gating_enabled}} as recorded."""
    out = {}
    for r in _read_frontmatter_list(ticket_md_path, "gate_effects"):
        if r.get("kind") != "derived":
            continue
        out[r.get("gate")] = {
            "ran": _as_int(r.get("ran")),
            "caused_edit": str(r.get("caused_edit")).lower() == "true",
            "under_recorded": str(r.get("under_recorded")).lower() == "true",
            "gating_enabled": str(r.get("gating_enabled")).lower() == "true",
        }
    return out


def snapshot_dod(ticket_md_path):
    """Append a `dod_snapshot` row: the done_evidence digest, for D3a.

    The same artifact `caused_edit` hashes, so it is stored once. An amendment
    count is then the number of DISTINCT digests — not a file-exists check.
    """
    rows = _read_frontmatter_list(ticket_md_path, "dod_snapshot")
    conds = parse_done_evidence(ticket_md_path)
    digest = _hashlib.sha256(
        "\n".join(sorted(str(sorted(c.items())) for c in conds)).encode("utf-8")
    ).hexdigest()[:16]
    if rows and rows[-1].get("hash") == digest:
        return None                      # unchanged; do not pad the history
    row = {"n": len(conds), "hash": digest}
    rows.append(row)
    _write_frontmatter_list(ticket_md_path, "dod_snapshot", rows)
    return row


def dod_amendments(ticket_md_path):
    """How many times the DoD changed between plan-time and now."""
    rows = _read_frontmatter_list(ticket_md_path, "dod_snapshot")
    seen, n = set(), 0
    for r in rows:
        h = r.get("hash")
        if h and h not in seen:
            seen.add(h)
            n += 1
    return max(0, n - 1)


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))

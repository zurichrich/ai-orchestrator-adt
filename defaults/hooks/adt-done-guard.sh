#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. PreToolUse hook.
# Checks a ticket before it moves into done/. When a command moves a ticket's
# .md into a done/ folder (`mv` or `git mv`), the hook runs five checks and
# denies the move if one of them fails:
#   #0 Close stamp (runs first). The frontmatter must have a non-empty
#      `tokens:`, `cost_usd:` and `cost_tier:`. `/adt-close` step 4 writes all
#      three before step 5 moves the file, so a move without them skipped the
#      close. Any value passes, `unattributed` included. `closed:` is not
#      checked: the sync writes it from the Issue after the move (ADT-359).
#   #1 Done evidence. If the ticket's frontmatter has a `done_evidence:` list,
#      each entry's `must_contain_regex` must match in the named rendered file
#      under .adt/ (the copy the human opens, not the cache or the source .md).
#      A ticket whose body shows a Definition of Done that the frontmatter does
#      not carry is denied, because the grader reads frontmatter only.
#   #2 Broken links. Every local href/src in .adt/kanban.html must point at a
#      file that exists next to it.
#   #3 Deploy freshness. The checkout that renders the board (the launchd
#      watcher's ADT_DIR) must be at or ahead of origin/main. If no watcher is
#      installed, the hook warns and allows the move.
#   #4 Recurring cost. If the ticket's commits touched an infrastructure path
#      (a CI workflow, a Dockerfile, terraform, a unit file, a deploy config),
#      the frontmatter must have a `recurring_cost:` value. Any value passes,
#      `none` included. `cost_usd` counts model tokens only, so this makes sure
#      someone wrote down the monthly cost before done.
#
# It also checks a move INTO a planned/ folder (ADT-371): the ticket's
# frontmatter must set `track:` to fast, standard or full, or the move is
# denied. /adt-plan step 4 sets the track and step 5 moves the file, so a plan
# that never set one stops here. An allowed planned/ move prints nothing.
#
# If the hook itself cannot run a check (a file is missing, the root cannot be
# resolved, a parse error), it allows the move. It denies only when a check ran
# and failed.
#
# Input and output (Claude Code PreToolUse contract):
#   - stdin JSON has tool_name, tool_input.command and cwd.
#   - To deny, exit 0 and print {"hookSpecificOutput":{"hookEventName":
#     "PreToolUse","permissionDecision":"deny","permissionDecisionReason":"…"}}.
#   - Otherwise it prints a systemMessage warning. The ticket .md (the mv
#     source) is still in place, so it resolves at $cwd/<src>. .adt/ resolves
#     from the canonical checkout, found the same way as adt-verify-bind.sh.
#
# On a done move it also warns if a worktree for the ticket still exists, so it
# can be torn down. That warning never blocks.
set -euo pipefail

# Find the directory holding adt_dod.py by a relative path. Two layouts:
#   - installed: .claude/hooks/adt-done-guard.sh  → ../tools   (= .claude/tools/)
#   - source:    defaults/hooks/adt-done-guard.sh → ../../tools (= repo-root tools/)
# ADT_TOOLS is passed to the Python gate below.
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADT_TOOLS=""
for _cand in "$_here/../tools" "$_here/../../tools"; do
  [ -f "$_cand/adt_dod.py" ] && { ADT_TOOLS="$(cd "$_cand" && pwd)"; break; }
done

input="$(cat)"
cmd="$(printf '%s' "$input" | /usr/bin/python3 -c 'import sys,json; d=json.load(sys.stdin); ti=d.get("tool_input",{}); print(ti.get("command","") or ti.get("file_path",""))' 2>/dev/null || true)"
cwd="$(printf '%s' "$input" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("cwd","") or "")' 2>/dev/null || true)"
[ -z "$cwd" ] && cwd="$PWD"

# Match the ADT cache shape only: a path segment .../done/<file>, a stage-set in
# frontmatter, or a `git mv` whose DESTINATION is a .../done/ folder.
case "$cmd" in
  */done/*|*$'\n'"stage: done"*|*"stage: done"$'\n'*|*"git mv"*"/done/"*)
    log="${CLAUDE_PROJECT_DIR:-.}/.adt/state/done-guard.log"; mkdir -p "$(dirname "$log")" 2>/dev/null || true
    printf '%s  candidate-done-action: %s\n' "$(date -u +%FT%TZ)" "$cmd" >> "$log" 2>/dev/null || true

    # ── The gates. Python resolves paths, runs the checks and prints one verdict
    # line. If Python errors there is no verdict and the move is allowed.
    # The script goes to a temp file because a heredoc inside $(...) mis-parses
    # quotes and apostrophes in the Python body. ──
    _gate_py="$(mktemp -t done-guard.XXXXXX.py)"
    cat > "$_gate_py" <<'PY'
import os, re, sys, subprocess

cmd = os.environ.get("CMD", "")
cwd = os.environ.get("CWD") or os.getcwd()

def out(decision, reason):
    # decision: DENY | WARN | OK ; reason: single-line human text
    print(decision + "\t" + reason.replace("\n", " ").strip())

# Only a move into done/ has a ticket file to check. Match plain `mv` as well
# as `git mv`: the backlog cache is outside any git checkout, so the playbooks
# move tickets with plain `mv`.
m = re.match(r"\s*(?:git\s+)?mv\s+(?:-\S+\s+)*(\S+)\s+(\S+)", cmd)
if not m:
    out("OK", "")  # stage-set / bare done path → nothing to assert here
    sys.exit(0)
src, dst = m.group(1), m.group(2)
if "/done/" not in dst:
    out("OK", "")
    sys.exit(0)

# Resolve the ticket .md (mv source, still in place at PreToolUse time).
md_path = src if os.path.isabs(src) else os.path.join(cwd, src)

# Resolve the canonical checkout from cwd, the same way adt-verify-bind.sh does,
# so .adt/ is the tree the watcher renders into. A linked worktree's
# --git-common-dir points at the main .git, whose parent is the canonical checkout.
def run(*a):
    try:
        return subprocess.run(a, cwd=cwd, capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""

root = run("git", "rev-parse", "--show-toplevel") or cwd
common = run("git", "rev-parse", "--git-common-dir")
if common:
    if not os.path.isabs(common):
        common = os.path.join(root, common)
    canon = os.path.dirname(os.path.abspath(common))
    if os.path.isdir(os.path.join(canon, ".adt")):
        root = canon
devteam = os.path.join(root, ".adt")

def _frontmatter(path):
    try:
        text = open(path, encoding="utf-8").read()
    except Exception:
        return None
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    # Add back the trailing newline the capture drops, so the line-anchored
    # searches below still match a block that ends the frontmatter.
    return (m.group(1) + "\n") if m else None

# ── Gate #0: close stamp — /adt-close ran ──────────────────────────────────
# A ticket moved by hand lands in done/ with no cost stamp. The three fields are
# the ones /adt-close step 4 writes before step 5 moves the file. The gate checks
# they are present, never that the cost is a number: `unattributed` is a valid
# value and must not be coerced to 0. An unreadable file or one with no
# frontmatter is not checked.
_close_fm = _frontmatter(md_path)
if _close_fm is not None:
    missing = []
    for key in ("tokens", "cost_usd", "cost_tier"):
        got = re.search(r"^%s:[ \t]*(.*)$" % key, _close_fm, re.M)
        if not got or got.group(1).strip().lower() in ("", "null", "~"):
            missing.append(key)
    if missing:
        out("DENY", "done-guard: close gate — the ticket has no %s. /adt-close "
                    "writes tokens:, cost_usd: and cost_tier: before it moves "
                    "the file, so this move skipped the close. Run /adt-close "
                    "for the ticket instead of moving it by hand. "
                    "`unattributed` is a valid value for all three."
                    % ", ".join("`%s:`" % k for k in missing))
        sys.exit(0)

# ── Gate #1: done_evidence artifact assertion ──────────────────────────────
# Uses tools/adt_dod.py, the same grader /adt-build-todone calls, so the two
# always agree. The grader reads `done_evidence:` and checks each
# must_contain_regex against the rendered copy under .adt/. If the grader cannot
# be imported (a project without tools/ on disk), this gate is skipped and the
# other gates still run.
adt_tools = os.environ.get("ADT_TOOLS", "")
if adt_tools and os.path.isdir(adt_tools):
    sys.path.insert(0, adt_tools)
try:
    import adt_dod
except Exception:
    adt_dod = None
if adt_dod is not None:
    # The body shows a DoD but the frontmatter has none. The grader reads
    # frontmatter only, so it would find no conditions and return OK. getattr
    # skips this check when the project's adt_dod.py predates dod_lost().
    if getattr(adt_dod, "dod_lost", lambda _p: False)(md_path):
        out("DENY", "done-guard: body-only DoD — the spec shows a Definition of "
                    "Done that the frontmatter does not carry, and the grader "
                    "reads frontmatter only, so this move would grade against "
                    "nothing. Restore the done_evidence: block to the ticket's "
                    "frontmatter.")
        sys.exit(0)
    # Grade in the canonical checkout, named explicitly. The done lane runs
    # after the merge, so that tree holds the work; check()'s own default is the
    # directory this process runs in, which a hook cannot rely on (ADT-359).
    result = adt_dod.check(md_path, devteam, lane="done", cwd=root)
    # adt_dod.verdict decides which result wins (a failure beats can't-verify).
    # It is called here rather than copied so the CLI and this gate agree.
    decision, reason = adt_dod.verdict(result, "done-guard")
    if decision != "OK":
        out(decision, reason)             # DENY blocks; WARN is still fail-open
        sys.exit(0)

# ── Gate #4: recurring cost — infrastructure a ticket switches on ──────────
# `cost_usd` counts model tokens only, but a ticket that switches on paid
# infrastructure bills every month afterwards. If the ticket's commits touched
# an infrastructure path, the frontmatter must have a `recurring_cost:` value.
# The gate does not judge the value; `recurring_cost: none` passes.
#
# A ticket with no `commits:`, or a SHA git cannot resolve, is allowed.
INFRA = (
    ".github/workflows/", ".gitlab-ci.yml", "azure-pipelines.yml",
    ".circleci/", "Jenkinsfile",
    "Dockerfile", "docker-compose",
    "wrangler.toml", "vercel.json", "fly.toml", "netlify.toml",
    "serverless.yml", "serverless.yaml",
    "crontab",
)
INFRA_SUFFIX = (".tf", ".tfvars", ".service", ".timer", ".plist")

fm = _frontmatter(md_path)
if fm is not None:
    shas = []
    cm = re.search(r"^commits:\s*\n((?:\s*-\s*.*\n)+)", fm, re.M)
    if cm:
        for line in cm.group(1).splitlines():
            for tok in re.split(r"[,\s]+", line.strip().lstrip("-").strip()):
                if re.fullmatch(r"[0-9a-f]{7,40}", tok):
                    shas.append(tok)

    touched_infra = []
    for sha in shas:
        files = run("git", "show", "--name-only", "--pretty=format:", sha)
        if not files:
            continue                       # unresolvable SHA — fail open
        for f in files.splitlines():
            f = f.strip()
            if not f:
                continue
            if any(k in f for k in INFRA) or f.endswith(INFRA_SUFFIX):
                touched_infra.append(f)

    if touched_infra:
        got = re.search(r"^recurring_cost:\s*(.*)$", fm, re.M)
        val = (got.group(1).strip() if got else "")
        if val.lower() in ("", "null", "~"):
            sample = ", ".join(sorted(set(touched_infra))[:3])
            out("DENY", "done-guard: recurring-cost gate — this ticket's commits "
                        "touch infrastructure (%s) but the frontmatter carries no "
                        "`recurring_cost:`. Infrastructure a ticket switches on "
                        "bills every month afterwards and `cost_usd` counts only "
                        "model tokens, so the monthly cost has to be written down "
                        "before done. Add `recurring_cost:` with the figure and "
                        "how it was measured — or `recurring_cost: none` if it "
                        "adds none." % sample)
            sys.exit(0)

# ── Gate #2: 404 — every local link in the human board resolves ────────────
board = os.path.join(devteam, "kanban.html")
if os.path.isfile(board):
    try:
        html = open(board, encoding="utf-8").read()
    except Exception:
        html = None
    if html is not None:
        links = re.findall(r'(?:href|src)\s*=\s*["\']([^"\']+)["\']', html)
        for link in links:
            if re.match(r'[a-z]+:', link) or link.startswith("#") or link.startswith("//"):
                continue  # external / anchor / protocol-relative — not our sibling
            rel = link.split("#", 1)[0].split("?", 1)[0]
            if not rel:
                continue
            if not os.path.exists(os.path.join(devteam, rel)):
                out("DENY", "done-guard: 404 gate — .adt/kanban.html links '%s' but .adt/%s does not exist. The board the human opens has a broken link." % (link, rel))
                sys.exit(0)

# ── Gate #3: deploy freshness — the rendering checkout is at/ahead of origin/main ──
# There is one watcher and one canonical checkout. Read the directory
# adt_watch.py runs from out of the installed launchd plist. With no watcher,
# warn and allow the move.
def find_watcher_adt_dir():
    # ADT_LAUNCHAGENTS_DIR overrides the agents dir so tests can use a fixture.
    la = os.environ.get("ADT_LAUNCHAGENTS_DIR") or os.path.expanduser("~/Library/LaunchAgents")
    if not os.path.isdir(la):
        return None
    for name in os.listdir(la):
        if "adt" not in name.lower() or not name.endswith(".plist"):
            continue
        try:
            p = open(os.path.join(la, name), encoding="utf-8").read()
        except Exception:
            continue
        if "adt_watch.py" not in p:
            continue
        m2 = re.search(r"<string>([^<]*)/tools/adt_watch\.py</string>", p)
        if m2:
            return m2.group(1)
    return None

adt_dir = find_watcher_adt_dir()
if not adt_dir:
    out("WARN", "done-guard: deploy-freshness — no board watcher found; cannot verify the rendering checkout is current (not blocking).")
    sys.exit(0)

def git_in(d, *a):
    try:
        return subprocess.run(("git", "-C", d) + a, capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""

head = git_in(adt_dir, "rev-parse", "HEAD")
# Fetch with an explicit refspec. `git fetch origin main` alone writes only
# FETCH_HEAD and leaves origin/main stale, so a behind checkout would look
# current. Use the tracking ref, falling back to FETCH_HEAD.
git_in(adt_dir, "fetch", "origin", "main:refs/remotes/origin/main")
origin_main = git_in(adt_dir, "rev-parse", "origin/main") or git_in(adt_dir, "rev-parse", "FETCH_HEAD")
if head and origin_main and head != origin_main:
    # behind only if origin/main is NOT an ancestor of head (head is behind/diverged)
    anc = subprocess.run(("git", "-C", adt_dir, "merge-base", "--is-ancestor", origin_main, head),
                         capture_output=True)
    if anc.returncode != 0:
        out("DENY", "done-guard: deploy-freshness — rendering checkout %s is at %s but origin/main is %s. The board the human sees runs stale code. Pull/deploy first." % (adt_dir, head[:8], origin_main[:8]))
        sys.exit(0)

out("OK", "")
PY
    verdict="$(CMD="$cmd" CWD="$cwd" ADT_TOOLS="$ADT_TOOLS" /usr/bin/python3 "$_gate_py" 2>/dev/null || true)"
    rm -f "$_gate_py" 2>/dev/null || true

    decision="$(printf '%s' "$verdict" | head -1 | cut -f1)"
    reason="$(printf '%s' "$verdict" | head -1 | cut -f2-)"

    if [ "$decision" = "DENY" ]; then
      # Deny the move: exit 0 with permissionDecision "deny".
      REASON="$reason" /usr/bin/python3 -c 'import os,json; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":os.environ["REASON"]}}))' 2>/dev/null || true
      exit 0
    fi

    # Not denied. Build the warning: the standing reminder plus any WARN reason.
    msg="⚠ done-guard: a ticket reaches done only when integrated end-to-end OR cancelled. Diff what shipped against what the ticket claimed and confirm the live path runs it before this lands."
    if [ "$decision" = "WARN" ] && [ -n "$reason" ]; then
      msg="$msg
⚠ $reason"
    fi

    # Warn if a worktree for this ticket still exists.
    slug="$(printf '%s' "$cmd" | /usr/bin/sed -nE 's#.*/([^/]+)\.md.*#\1#p' | head -1)"
    if [ -n "$slug" ] && git worktree list 2>/dev/null | grep -q -- "$slug"; then
      msg="$msg
⚠ done-guard: a worktree for '$slug' still exists. Tear it down once its code is merged: 'git worktree remove <path> && git branch -d <branch>' (or the project's scripts/rm-worktree.sh)."
    fi

    printf '%s' "$msg" | /usr/bin/python3 -c 'import sys,json; print(json.dumps({"systemMessage": sys.stdin.read()}))' 2>/dev/null || true
    ;;
  *mv*/planned/*)
    # The plan gate. Silent unless the moved ticket has no valid track. The
    # track is read by adt_dod.ticket_track(), the reader the plan-quality gate
    # uses, so a quoted `track: "fast"` counts here too. Anything it cannot read
    # is allowed, like the gates above.
    CMD="$cmd" CWD="$cwd" ADT_TOOLS="$ADT_TOOLS" /usr/bin/python3 -c '
import json, os, re, sys
cmd = os.environ.get("CMD", "")
cwd = os.environ.get("CWD") or os.getcwd()
m = re.match(r"\s*(?:git\s+)?mv\s+(?:-\S+\s+)*(\S+)\s+(\S+)", cmd)
if not m or "/planned/" not in m.group(2) or not os.environ.get("ADT_TOOLS"):
    sys.exit(0)
src = os.path.expanduser(m.group(1).strip(chr(34) + chr(39)))
md = src if os.path.isabs(src) else os.path.join(cwd, src)
if not os.path.isfile(md):
    sys.exit(0)
sys.path.insert(0, os.environ["ADT_TOOLS"])
import adt_dod
if adt_dod.ticket_track(md) in ("fast", "standard", "full"):
    sys.exit(0)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "done-guard: plan gate - the ticket has no track. Set track: to fast, standard or full (/adt-plan step 4), then move it into planned/."}}))
' 2>/dev/null || true
    ;;
esac
exit 0

#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. PreToolUse hook on Write|Edit.
# Enforces multi-agent-git-workflow.md §A.3: code is edited in a linked worktree,
# never in the canonical checkout, where a parallel session's `git checkout` can
# switch the branch under you. It denies a Write or Edit when both hold:
#   - the file's git tree is the canonical checkout (--git-dir equals
#     --git-common-dir; in a linked worktree they differ), and
#   - the file's path, relative to that checkout, matches a pattern in the
#     `worktree_guard_paths:` line of its .adt/config.yaml.
#
# Set the patterns in the project config, ~/.adt/projects/<name>.yaml, NOT in
# .adt/config.yaml: install copies them into .adt/config.yaml, which it rewrites
# on every install and deletes on uninstall. The value is space-separated
# shell-style patterns, matched with Python fnmatch, where `*` also matches `/`.
# Quote it, since a leading `*` is YAML syntax:
#   worktree_guard_paths: "src/* tools/*.py"
# Re-run install (or setup.sh) after changing it. With no value the hook allows
# everything, so installing it changes nothing until a project names what to
# guard. Docs, rules and the backlog usually stay unlisted: editing them in the
# canonical checkout is normal in single-session use.
#
# Adapted from a consumer's hook (ADT-359, was ADT-325 item 2), which hard-coded
# its paths. Fails open: bad JSON, no file path, a path outside any git repo, a
# git query that fails, or an unreadable config all allow.
set -uo pipefail

input="$(cat 2>/dev/null || true)"
[ -n "$input" ] || exit 0

printf '%s' "$input" | /usr/bin/python3 -c '
import fnmatch, json, os, subprocess, sys

try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
if d.get("tool_name") not in ("Write", "Edit"):
    sys.exit(0)
path = (d.get("tool_input") or {}).get("file_path") or ""
if not path:
    sys.exit(0)
if not os.path.isabs(path):
    path = os.path.join(d.get("cwd") or os.getcwd(), path)

# The nearest directory that exists: a new file may sit in a new directory.
fdir = os.path.dirname(path)
while fdir and not os.path.isdir(fdir):
    parent = os.path.dirname(fdir)
    if parent == fdir:
        sys.exit(0)
    fdir = parent

def rev_parse(*args):
    try:
        r = subprocess.run(["git", "-C", fdir, "rev-parse", "--path-format=absolute"] + list(args),
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    out = r.stdout.strip()
    return os.path.realpath(out) if r.returncode == 0 and out else None

git_dir, common, top = rev_parse("--git-dir"), rev_parse("--git-common-dir"), rev_parse("--show-toplevel")
if not (git_dir and common and top) or git_dir != common:
    sys.exit(0)                        # not a repo, or a linked worktree: allow

patterns = []
try:
    for line in open(os.path.join(top, ".adt", "config.yaml"), encoding="utf-8"):
        if line.startswith("worktree_guard_paths:"):
            patterns = [p.strip("\"'"'"'") for p in line.split(":", 1)[1].split()]
            break
except Exception:
    sys.exit(0)
if not patterns:
    sys.exit(0)

rel = os.path.relpath(os.path.realpath(path), top)
if rel.startswith(".."):
    sys.exit(0)
hit = next((p for p in patterns if fnmatch.fnmatch(rel, p)), None)
if not hit:
    sys.exit(0)

name = os.path.basename(top)
reason = ("worktree-guard: `%s` is in the canonical checkout and matches `%s` in "
          "worktree_guard_paths in the project config. Here, a `git checkout` in a "
          "parallel session can switch the branch under you "
          "(multi-agent-git-workflow.md §A.3). Create a worktree and edit there: "
          "git worktree add ../%s-wt-<branch> -b <branch>" % (rel, hit, name))
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                  "permissionDecision": "deny", "permissionDecisionReason": reason}}))
' 2>/dev/null || true
exit 0

# adt-bundle: v0.1.0

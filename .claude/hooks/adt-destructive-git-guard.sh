#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT default. PreToolUse hook on Bash.
# Denies a git command that would destroy uncommitted work (ADT-359). In one
# consumer's session `git reset --hard HEAD~1`, run to undo a temporary commit,
# also destroyed four uncommitted source edits. The rule "restore from a copy,
# never from git" was in the build playbook, and the agent had read it.
#
# The hook asks git what the command would lose, rather than guessing:
#   reset --hard, checkout -f, switch -f / --discard-changes
#       deny when `git diff HEAD --name-only` lists anything
#   checkout -- <paths>, checkout <ref> -- <paths>, restore <paths>
#       deny when `git diff HEAD --name-only -- <paths>` lists anything.
#       `restore --staged` on its own only unstages, so it is allowed.
#   clean -f
#       deny when the same `git clean` with -n in place of -f lists anything
#
# The denial names the files and says to commit, or `git stash push -u`, and
# re-run. Stashing is the way through for a deliberate discard: the work stays
# recoverable and the tree is clean, so the command then passes.
#
# The command is read as words (shlex), the way adt-deferral-guard.sh reads it,
# so a quoted string that only names `git reset --hard` does not match, and a
# heredoc body is data unless it is fed to a shell. `cd <dir>` and `git -C <dir>`
# decide which tree a git command acts on.
#
# Silent when it allows. Fails open: bad JSON, a command shlex cannot read, a
# directory that does not exist, or a git query that fails all allow.
set -uo pipefail

input="$(cat 2>/dev/null || true)"
[ -n "$input" ] || exit 0

_py="$(mktemp -t destructive-git-guard.XXXXXX.py)" || exit 0
trap 'rm -f "$_py"' EXIT
cat > "$_py" <<'PY'
import json, os, re, shlex, subprocess, sys

try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
if d.get("tool_name") != "Bash":
    sys.exit(0)
cmd = (d.get("tool_input") or {}).get("command", "") or ""
if not re.search(r"\bgit\b", cmd):
    sys.exit(0)
base = d.get("cwd") or os.getcwd()

OPS = "();<>|&`\n"
SHELLS = {"bash", "sh", "zsh"}
# git's own options that take a value in the next word.
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}


def strip_heredoc_bodies(text):
    """Drop every heredoc body that is not fed to a shell: it is data."""
    out, i = [], 0
    while True:
        m = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z_0-9]*)\1", text[i:])
        if not m:
            out.append(text[i:])
            return "".join(out)
        start = i + m.start()
        head = text[i:start]
        out.append(head)
        rest = text[i + m.end():]
        term = re.search(r"^[ \t]*%s[ \t]*$" % re.escape(m.group(2)), rest, re.M)
        if not term:
            out.append(rest)
            return "".join(out)
        seg = re.split(r"[|;&\n]", head)[-1].split()
        if seg and os.path.basename(seg[0]) in SHELLS:
            out.append(rest[:term.start()])
        out.append("\n")
        i = i + m.end() + term.end()


def simple_commands(text):
    lex = shlex.shlex(text.replace("\\\n", " "), posix=True, punctuation_chars=OPS)
    lex.whitespace, lex.whitespace_split, lex.commenters = " \t\r", True, ""
    argv = []
    for t in list(lex) + [";"]:
        if t and all(ch in OPS for ch in t):
            if argv:
                yield argv
            argv = []
        else:
            argv.append(t)


def git(tree, prefix, *args):
    """stdout lines of a git query, or None if it failed."""
    try:
        r = subprocess.run(["git", "-C", tree] + prefix + list(args),
                           capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return [l for l in r.stdout.splitlines() if l.strip()]


def split_paths(args, value_opts=()):
    """Paths named by a checkout/restore: everything after `--`, else the
    non-option words, skipping the values of options that take one."""
    if "--" in args:
        return args[args.index("--") + 1:], True
    paths, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in value_opts:
            skip = True
        elif not a.startswith("-"):
            paths.append(a)
    return paths, False


def lost(tree, prefix, sub, args):
    """(what, files) the git command would destroy, or None."""
    if sub == "reset" and "--hard" in args:
        return "uncommitted changes", git(tree, prefix, "diff", "HEAD", "--name-only")
    if sub == "switch" and set(args) & {"-f", "--force", "--discard-changes"}:
        return "uncommitted changes", git(tree, prefix, "diff", "HEAD", "--name-only")
    if sub == "checkout":
        if set(args) & {"-f", "--force"}:
            return "uncommitted changes", git(tree, prefix, "diff", "HEAD", "--name-only")
        paths, explicit = split_paths(args, {"-b", "-B", "--orphan"})
        if not explicit:
            # No `--`: a word is a path only when it exists; otherwise it is a
            # branch, and git itself refuses to overwrite local changes.
            paths = [p for p in paths if os.path.exists(os.path.join(tree, p))]
        if paths:
            return "uncommitted changes", git(tree, prefix, "diff", "HEAD", "--name-only", "--", *paths)
    if sub == "restore":
        staged = bool(set(args) & {"--staged", "-S"})
        worktree = bool(set(args) & {"--worktree", "-W"})
        if staged and not worktree:
            return None
        paths, _ = split_paths(args, {"-s", "--source"})
        if paths:
            return "uncommitted changes", git(tree, prefix, "diff", "HEAD", "--name-only", "--", *paths)
    if sub == "clean":
        force = "--force" in args or any(
            a.startswith("-") and not a.startswith("--") and "f" in a for a in args)
        if not force or set(args) & {"-n", "--dry-run"}:
            return None
        dry = []
        for a in args:
            if a == "--force":
                continue
            if a.startswith("-") and not a.startswith("--"):
                a = a.replace("f", "")
                if a == "-":
                    continue
            dry.append(a)
        listed = git(tree, prefix, "clean", "-n", *dry)
        return "untracked files", listed and [re.sub(r"^Would (?:remove|skip repository) ", "", l) for l in listed]
    return None


def check(argv, cwd):
    """Return a deny reason for the first destructive git word in argv."""
    for k, word in enumerate(argv):
        name = os.path.basename(word)
        if name in SHELLS or name == "eval":
            rest = argv[k + 1:]
            if name == "eval":
                return check_text(" ".join(rest), cwd)
            for j, a in enumerate(rest[:-1]):
                if a.startswith("-") and not a.startswith("--") and "c" in a:
                    return check_text(rest[j + 1], cwd)
            continue
        if name != "git":
            continue
        tree, prefix, it = cwd, [], iter(argv[k + 1:])
        sub = None
        for a in it:
            if a == "-C":
                tree = os.path.join(tree, os.path.expanduser(next(it, "")))
            elif a in GIT_VALUE_OPTS:
                prefix += [a, next(it, "")]
            elif a.startswith("--git-dir=") or a.startswith("--work-tree="):
                prefix.append(a)
            elif a.startswith("-"):
                continue
            else:
                sub = a
                break
        if not sub or not os.path.isdir(tree):
            continue
        args = list(it)
        res = lost(tree, prefix, sub, args)
        if not res or not res[1]:
            continue
        what, files = res
        shown = ", ".join(files[:5]) + (" (+%d more)" % (len(files) - 5) if len(files) > 5 else "")
        return ("destructive-git-guard: `git %s` would destroy %s in %s: %s. "
                "Commit them, or set them aside with `git stash push -u`, then "
                "re-run. To test a check against a broken file, copy the file "
                "first and restore from the copy, never from git."
                % (" ".join([sub] + args), what, os.path.abspath(tree), shown))
    return None


def check_text(text, cwd):
    for argv in simple_commands(text):
        if argv[0] == "cd":
            target = argv[1] if len(argv) > 1 else os.path.expanduser("~")
            cwd = os.path.join(cwd, os.path.expanduser(target))
            continue
        reason = check(argv, cwd)
        if reason:
            return reason
    return None


try:
    reason = check_text(strip_heredoc_bodies(cmd), base)
except Exception:
    sys.exit(0)
if reason:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))
PY
printf '%s' "$input" | /usr/bin/python3 "$_py" 2>/dev/null || true
exit 0

# adt-bundle: v0.2.0

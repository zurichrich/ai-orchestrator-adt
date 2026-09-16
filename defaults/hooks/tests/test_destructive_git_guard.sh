#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
#
# Tests adt-destructive-git-guard.sh: it denies a git command that would destroy
# uncommitted work, allows the same command on a clean tree, and fails open.
#
# Run:  bash defaults/hooks/tests/test_destructive_git_guard.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$HERE/../adt-destructive-git-guard.sh"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

D="$(mktemp -d)"
trap 'rm -rf "$D"' EXIT
git init -q "$D/r"
git -C "$D/r" config user.email t@t
git -C "$D/r" config user.name t
echo a > "$D/r/a.txt"; echo b > "$D/r/b.txt"
git -C "$D/r" add . && git -C "$D/r" commit -qm init
R="$D/r"

# drive <command> [cwd] — prints the hook's output
drive() {
  python3 -c 'import json,sys; print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]},"cwd":sys.argv[2]}))' \
    "$1" "${2:-$R}" | bash "$GUARD" 2>/dev/null
}
denies() { drive "$@" | grep -q '"permissionDecision": "deny"'; }
allows() { [ -z "$(drive "$@")" ]; }

echo "== clean tree: nothing to lose, everything allowed"
for c in "git reset --hard" "git checkout -f" "git switch --discard-changes main" \
         "git checkout -- a.txt" "git restore a.txt" "git clean -fd"; do
  allows "$c" && ok "clean tree allows: $c" || bad "clean tree should allow: $c"
done

echo "== a modified tracked file"
echo x >> "$R/a.txt"
for c in "git reset --hard" "git reset --hard HEAD~1" "git checkout -f" "git checkout --force main" \
         "git switch -f main" "git switch --discard-changes main" \
         "git checkout -- a.txt" "git checkout HEAD -- a.txt" "git checkout a.txt" \
         "git checkout -- ." "git restore a.txt" "git restore --staged --worktree a.txt"; do
  denies "$c" && ok "dirty tree denies: $c" || bad "dirty tree should deny: $c"
done
out="$(drive "git reset --hard")"
printf '%s' "$out" | grep -q 'a.txt' && printf '%s' "$out" | grep -q 'git stash push -u' \
  && ok "the denial names the file and the stash remedy" \
  || bad "denial should name a.txt and git stash push -u, got: $out"

echo "== targeted and harmless forms stay allowed in a dirty tree"
for c in "git restore b.txt" "git checkout -- b.txt" "git restore --staged a.txt" \
         "git status" "git diff" "git stash push -u" "git checkout main" \
         "git log --oneline -1" "git reset --soft HEAD" "git clean -n"; do
  allows "$c" && ok "allowed: $c" || bad "should allow: $c"
done

echo "== reading the command as words"
allows "grep -n 'git reset --hard' a.txt" && ok "a quoted mention does not match" || bad "a quoted mention should not match"
allows "git commit -F - <<'EOF'
git reset --hard
EOF" && ok "a heredoc body fed to git is data" || bad "a heredoc body fed to git should be data"
denies "bash <<'EOF'
git reset --hard
EOF" && ok "a heredoc body fed to bash is a command" || bad "a heredoc body fed to bash should be checked"
denies "bash -c 'git reset --hard'" && ok "bash -c is read" || bad "bash -c should be read"
denies "true && git reset --hard" && ok "a later command in a chain is read" || bad "a chained command should be read"

echo "== which tree"
denies "git -C $R checkout -- a.txt" / && ok "git -C names the tree" || bad "git -C should name the tree"
denies "cd $R && git reset --hard" / && ok "cd names the tree" || bad "cd should name the tree"
allows "git reset --hard" "$D" && ok "outside a repo: allowed" || bad "outside a repo should allow"
allows "cd $D/missing && git reset --hard" / && ok "cd to a missing directory: allowed" || bad "a missing directory should fail open"

echo "== untracked files and git clean"
echo a > "$R/a.txt"   # back to the committed content, by writing it
echo u > "$R/u.txt"
denies "git clean -fd" && ok "clean -fd with an untracked file denies" || bad "clean -fd should deny"
denies "git clean --force" && ok "clean --force denies" || bad "clean --force should deny"
out="$(drive "git clean -fd")"
printf '%s' "$out" | grep -q 'u.txt' && ! printf '%s' "$out" | grep -q 'Would remove' \
  && ok "the clean denial lists the file without git's prefix" || bad "clean denial should list u.txt plainly, got: $out"
allows "git reset --hard" && ok "reset --hard ignores untracked files" || bad "reset --hard should ignore untracked files"
git -C "$R" stash push -q -u
allows "git clean -fd" && allows "git reset --hard" && ok "after git stash push -u both are allowed" \
  || bad "a stashed tree should allow both"

echo "== fail open"
[ -z "$(printf 'not json' | bash "$GUARD" 2>/dev/null)" ] && ok "bad JSON allows" || bad "bad JSON should allow"
[ -z "$(printf '{"tool_name":"Write","tool_input":{"file_path":"x"}}' | bash "$GUARD" 2>/dev/null)" ] \
  && ok "a non-Bash tool is ignored" || bad "a non-Bash tool should be ignored"
allows "git reset --hard 'unbalanced" && ok "an unparseable command allows" || bad "an unparseable command should allow"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]

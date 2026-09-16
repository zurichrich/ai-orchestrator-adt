#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
#
# Tests adt-worktree-guard.sh: it denies a Write/Edit in the canonical checkout
# to a path listed in .adt/config.yaml worktree_guard_paths, allows everything
# else, and fails open.
#
# Run:  bash defaults/hooks/tests/test_worktree_guard.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$HERE/../adt-worktree-guard.sh"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

D="$(mktemp -d)"
trap 'rm -rf "$D"' EXIT
C="$D/canon"; W="$D/wt"
git init -q "$C"
git -C "$C" config user.email t@t
git -C "$C" config user.name t
mkdir -p "$C/src/deep" "$C/docs" "$C/.adt"
echo x > "$C/src/a.py"; echo y > "$C/src/deep/b.py"; echo n > "$C/docs/n.md"
git -C "$C" add src docs && git -C "$C" commit -qm init
git -C "$C" worktree add -q "$W" -b wt

# drive <tool> <file_path> [cwd]
drive() {
  python3 -c 'import json,sys; print(json.dumps({"tool_name":sys.argv[1],"tool_input":{"file_path":sys.argv[2]},"cwd":sys.argv[3]}))' \
    "$1" "$2" "${3:-$C}" | bash "$GUARD" 2>/dev/null
}
denies() { drive "$@" | grep -q '"permissionDecision": "deny"'; }
allows() { [ -z "$(drive "$@")" ]; }

echo "== no worktree_guard_paths line"
allows Edit "$C/src/a.py" && ok "no config file: allowed" || bad "no config should allow"
echo "id_prefix: T" > "$C/.adt/config.yaml"
allows Edit "$C/src/a.py" && ok "config without the key: allowed" || bad "config without the key should allow"
printf 'worktree_guard_paths:\n' > "$C/.adt/config.yaml"
allows Edit "$C/src/a.py" && ok "an empty key: allowed" || bad "an empty key should allow"

echo "== key set"
printf 'id_prefix: T\nworktree_guard_paths: src/* "tools/*.py"\n' > "$C/.adt/config.yaml"
denies Edit "$C/src/a.py" && ok "canonical Edit to a listed path: denied" || bad "canonical listed Edit should deny"
denies Write "$C/src/deep/b.py" && ok "* matches across directories" || bad "src/* should match src/deep/b.py"
denies Write "$C/src/new/c.py" && ok "a new file in a new directory: denied" || bad "a new file under src/ should deny"
denies Write "$C/tools/x.py" && ok "a quoted pattern is unquoted" || bad "\"tools/*.py\" should match tools/x.py"
denies Edit "src/a.py" "$C" && ok "a relative path resolves against cwd" || bad "a relative path should resolve"
out="$(drive Edit "$C/src/a.py")"
printf '%s' "$out" | grep -q 'git worktree add' && printf '%s' "$out" | grep -q 'src/a.py' \
  && ok "the denial names the file and git worktree add" || bad "denial should name the file and git worktree add, got: $out"
allows Edit "$C/docs/n.md" && ok "an unlisted path: allowed" || bad "an unlisted path should allow"
# Install writes the whole value in double quotes (lib/github-bootstrap.sh).
printf 'worktree_guard_paths: "src/* tools/*.py"\n' > "$C/.adt/config.yaml"
denies Edit "$C/src/a.py" && denies Write "$C/tools/x.py" && allows Edit "$C/docs/n.md" \
  && ok "the quoted form install writes is read" || bad "the quoted value install writes should be read"
printf 'id_prefix: T\nworktree_guard_paths: src/* "tools/*.py"\n' > "$C/.adt/config.yaml"
allows Edit "$W/src/a.py" && ok "a linked worktree: allowed" || bad "a linked worktree should allow"
allows Edit "$D/elsewhere/x.py" && ok "a file outside any repo: allowed" || bad "outside any repo should allow"

echo "== fail open"
[ -z "$(printf 'not json' | bash "$GUARD" 2>/dev/null)" ] && ok "bad JSON allows" || bad "bad JSON should allow"
[ -z "$(printf '{"tool_name":"Bash","tool_input":{"command":"x"}}' | bash "$GUARD" 2>/dev/null)" ] \
  && ok "a Bash call is ignored" || bad "a Bash call should be ignored"
[ -z "$(printf '{"tool_name":"Edit","tool_input":{}}' | bash "$GUARD" 2>/dev/null)" ] \
  && ok "no file_path allows" || bad "no file_path should allow"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]

#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests adt-close-complete.sh: the Stop hook that blocks (exit 2) when an
# /adt-close session ends without the ticket in done/.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$HERE/../adt-close-complete.sh"
[ -x "$HOOK" ] || { echo "FAIL: $HOOK not executable"; exit 1; }

FAILS=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

SESSION="sess-close-336"

# ── fixture -----------------------------------------------------------------
# Rebuild from scratch so no case inherits another's markers or one-shot file.
setup() {
  rm -rf "$TMP/proj" "$TMP/cache"
  ROOT="$TMP/proj"
  CACHE="$TMP/cache"
  mkdir -p "$ROOT/.adt/state/current-cmd.d" "$ROOT/.adt/state/current-tix.d"
  mkdir -p "$CACHE/bugs/done" "$CACHE/enhancements/done" "$CACHE/tasks/done"
  printf 'cache_dir: %s\n' "$CACHE" > "$ROOT/.adt/config.yaml"
  printf 'adt-close\n' > "$ROOT/.adt/state/current-cmd.d/$SESSION"
  printf 'ADT-336\n'   > "$ROOT/.adt/state/current-tix.d/$SESSION"
}

# Write a ticket into <type>/done/ with the given stage/state/reason.
write_done() {  # $1=type $2=slug $3=id $4=stage $5=state $6=reason
  cat > "$CACHE/$1/done/$2.md" <<EOF
---
slug: $2
id: $3
title: fixture
type: bug
stage: $4
state: $5
state_reason: $6
---

body
EOF
}

stdin_json() {  # $1=session $2=stop_hook_active
  printf '{"session_id":"%s","hook_event_name":"Stop","cwd":"%s","stop_hook_active":%s,"transcript_path":"%s/t.jsonl"}' \
    "$1" "$ROOT" "$2" "$TMP"
}

run_hook() {  # $1=session $2=stop_hook_active ; echoes exit code
  stdin_json "$1" "$2" | "$HOOK" >"$TMP/out" 2>"$TMP/err"
  echo $?
}

check() {  # $1=label $2=expected $3=actual
  if [ "$2" = "$3" ]; then
    echo "  ok   $1 (exit $3)"
  else
    echo "  FAIL $1 — expected exit $2, got $3"
    [ -s "$TMP/err" ] && sed 's/^/       | /' "$TMP/err" | head -4
    FAILS=$((FAILS + 1))
  fi
}

echo "== adt-close-complete.sh =="

# 1. No done/ file → block.
setup
check "no done/ file blocks" 2 "$(run_hook "$SESSION" false)"
grep -q "ADT-336 is not in done/" "$TMP/err" \
  || { echo "  FAIL block message does not name the ticket"; FAILS=$((FAILS+1)); }
grep -q "step 5" "$TMP/err" \
  || { echo "  FAIL block message does not restate the remaining steps"; FAILS=$((FAILS+1)); }

# 2. Correct terminal state → pass.
setup
write_done bugs issue-336 ADT-336 done closed completed
check "done/ file with stage:done+state:closed passes" 0 "$(run_hook "$SESSION" false)"

# 3. A cancelled ticket (state_reason: not_planned) in done/ → pass.
setup
write_done bugs issue-336 ADT-336 done closed not_planned
check "cancelled (not_planned) passes" 0 "$(run_hook "$SESSION" false)"

# 4. The ticket is found under any <type>/done/, not only tasks/done/.
setup
write_done enhancements issue-336 ADT-336 done closed completed
check "found under enhancements/done too" 0 "$(run_hook "$SESSION" false)"

# 5. In done/ but the frontmatter still says building/open → block.
setup
write_done bugs issue-336 ADT-336 building open null
check "done/ file with wrong frontmatter blocks" 2 "$(run_hook "$SESSION" false)"

# 6. A different ticket in done/ does not satisfy this session.
setup
write_done bugs issue-999 ADT-999 done closed completed
check "another ticket in done/ does not satisfy" 2 "$(run_hook "$SESSION" false)"

# ── cases where the hook cannot check, so it exits 0 ------------------------

# 7. Not an /adt-close session → untouched.
setup
printf 'adt-build\n' > "$ROOT/.adt/state/current-cmd.d/$SESSION"
check "cmd is not adt-close → untouched" 0 "$(run_hook "$SESSION" false)"

# 8. No cmd marker → untouched.
setup
rm -f "$ROOT/.adt/state/current-cmd.d/$SESSION"
check "no cmd marker → fails open" 0 "$(run_hook "$SESSION" false)"

# 9. No tix marker → fails open.
setup
rm -f "$ROOT/.adt/state/current-tix.d/$SESSION"
check "no tix marker → fails open" 0 "$(run_hook "$SESSION" false)"

# 10. No cache dir → fails open.
setup
rm -rf "$CACHE"
check "no cache root → fails open" 0 "$(run_hook "$SESSION" false)"

# 11. stop_hook_active (another hook already continued this Stop) → exits 0.
setup
check "stop_hook_active → exits 0" 0 "$(run_hook "$SESSION" true)"

# 12. Not an ADT project at all.
setup
rm -rf "$ROOT/.adt"
check "no .adt/ → fails open" 0 "$(run_hook "$SESSION" false)"

# 13. Malformed stdin.
setup
printf 'not json' | "$HOOK" >/dev/null 2>&1
check "unparseable stdin → fails open" 0 "$?"

# ── blocks once per session ------------------------------------------------
# The second Stop in the same session only warns.
setup
first="$(run_hook "$SESSION" false)"
second="$(run_hook "$SESSION" false)"
check "first block blocks" 2 "$first"
check "second block becomes a warning" 0 "$second"
[ -f "$ROOT/.adt/state/close-complete.d/$SESSION" ] \
  || { echo "  FAIL one-shot marker not written"; FAILS=$((FAILS+1)); }
grep -q "not blocking again" "$TMP/out" \
  || { echo "  FAIL second run did not emit the warning"; FAILS=$((FAILS+1)); }

# A different session still gets its one block.
printf 'adt-close\n' > "$ROOT/.adt/state/current-cmd.d/other-sess"
printf 'ADT-336\n'   > "$ROOT/.adt/state/current-tix.d/other-sess"
check "one-shot is per session" 2 "$(run_hook "other-sess" false)"

echo
if [ "$FAILS" -eq 0 ]; then
  echo "PASS — adt-close-complete.sh"
  exit 0
fi
echo "FAIL — $FAILS assertion(s) failed"
exit 1

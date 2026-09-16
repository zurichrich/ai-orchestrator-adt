#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that adt-verify-bind.sh exits 0 when the session is bound to the given
# ticket and 1 otherwise. Binds with adt-mark-tix.sh first.
#
# Run:  bash agent-dev-team/defaults/hooks/tests/test_adt_verify_bind.sh
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARK="$HOOK_DIR/adt-mark-tix.sh"
VERIFY="$HOOK_DIR/adt-verify-bind.sh"
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

# A throwaway project: a git repo with an .adt/ directory.
newproj() {
  local d; d="$(mktemp -d)"
  git -C "$d" init -q
  mkdir -p "$d/.adt"
  printf '%s' "$d"
}

echo "== adt-verify-bind.sh =="

# 1. Bound + matching → exit 0.
P="$(newproj)"
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessA" bash "$MARK" "ADT-61" )
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessA" bash "$VERIFY" "ADT-61" ) 2>/dev/null
if [ "$?" = 0 ]; then ok "bound + matching ticket → exit 0"
else bad "bound session wrongly rejected (should exit 0)"; fi

# 2. The match ignores case: bound ADT-61, verify "adt-61" → exit 0.
P="$(newproj)"
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessCI" bash "$MARK" "ADT-61" )
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessCI" bash "$VERIFY" "adt-61" ) 2>/dev/null
if [ "$?" = 0 ]; then ok "verify id is case-normalised → exit 0"
else bad "case-insensitive match failed"; fi

# 3. No marker at all → exit 1.
P="$(newproj)"
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessNB" bash "$VERIFY" "ADT-61" ) 2>/dev/null
if [ "$?" = 1 ]; then ok "no marker → exit 1 (unbound pickup stops)"
else bad "missing marker did not exit 1"; fi

# 4. Marker names a different ticket → exit 1.
P="$(newproj)"
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessWT" bash "$MARK" "ADT-99" )
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessWT" bash "$VERIFY" "ADT-61" ) 2>/dev/null
if [ "$?" = 1 ]; then ok "marker for another ticket → exit 1"
else bad "wrong-ticket binding was accepted (should exit 1)"; fi

# 5. Another session's marker is present, ours is absent → exit 1.
P="$(newproj)"
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessOther" bash "$MARK" "ADT-61" )
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessMine" bash "$VERIFY" "ADT-61" ) 2>/dev/null
if [ "$?" = 1 ]; then ok "another session's marker doesn't satisfy ours → exit 1"
else bad "cross-session marker wrongly accepted"; fi

# 6. No session id → exit 1.
P="$(newproj)"
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessX" bash "$MARK" "ADT-61" )
( cd "$P" && env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_TRANSCRIPT_PATH bash "$VERIFY" "ADT-61" ) 2>/dev/null
if [ "$?" = 1 ]; then ok "no session id → exit 1 (cannot verify → stop)"
else bad "missing session id did not exit 1"; fi

# 7. No ticket arg → exit 1.
P="$(newproj)"
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessNA" bash "$VERIFY" ) 2>/dev/null
if [ "$?" = 1 ]; then ok "no ticket arg → exit 1"
else bad "missing arg did not exit 1"; fi

# 8. The marker is written in the canonical checkout; verify run from a linked
#    worktree finds it. Skipped if `git worktree` is unavailable.
P="$(newproj)"
( cd "$P" && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m init )
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessWT8" bash "$MARK" "ADT-61" )   # writes in canonical P
WT="$(mktemp -d)/wt"
if ( cd "$P" && git worktree add -q "$WT" -b wt-branch ) 2>/dev/null; then
  # The worktree has no .adt/ of its own; resolution must walk to P.
  ( cd "$WT" && CLAUDE_CODE_SESSION_ID="sessWT8" bash "$VERIFY" "ADT-61" ) 2>/dev/null
  if [ "$?" = 0 ]; then ok "verify from a worktree resolves to the canonical marker → exit 0"
  else bad "verify from a worktree missed the canonical marker"; fi
  ( cd "$P" && git worktree remove --force "$WT" ) 2>/dev/null || true
else
  ok "(skipped worktree case — git worktree unavailable)"
fi

echo ""
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]

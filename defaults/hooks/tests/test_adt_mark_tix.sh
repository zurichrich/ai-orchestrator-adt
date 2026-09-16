#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that adt-mark-tix.sh writes the per-session ticket marker, and that
# adt-usage-log.sh resolves the ticket id prefix when it binds one.
#
# Run:  bash agent-dev-team/defaults/hooks/tests/test_adt_mark_tix.sh
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARK="$HOOK_DIR/adt-mark-tix.sh"
USAGE_HOOK="$HOOK_DIR/adt-usage-log.sh"
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

echo "== adt-mark-tix.sh =="

# 1. Writes the per-session marker with the id upper-cased.
P="$(newproj)"
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessA" bash "$MARK" "adt-54" )
MK="$P/.adt/state/current-tix.d/sessA"
if [ -f "$MK" ] && [ "$(cat "$MK")" = "ADT-54" ]; then ok "writes per-session marker, upper-cased"
else bad "per-session marker not written / wrong value (got '$(cat "$MK" 2>/dev/null)')"; fi

# 2. Does not write the flat marker shared by every session.
if [ ! -f "$P/.adt/state/current-tix" ]; then ok "does not write the flat shared marker"
else bad "flat shared marker was written"; fi

# 3. No session id: writes nothing and exits 0.
P="$(newproj)"
( cd "$P" && env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_TRANSCRIPT_PATH bash "$MARK" "ADT-9" )
rc=$?
if [ "$rc" = 0 ] && [ ! -e "$P/.adt/state/current-tix.d" ]; then ok "no session id → no-op, exit 0"
else bad "no-session case wrote a marker or non-zero exit (rc=$rc)"; fi

# 4. Without the session id env var, uses the transcript file name.
P="$(newproj)"
( cd "$P" && env -u CLAUDE_CODE_SESSION_ID CLAUDE_TRANSCRIPT_PATH="/x/y/sessFB.jsonl" bash "$MARK" "ADT-7" )
if [ "$(cat "$P/.adt/state/current-tix.d/sessFB" 2>/dev/null)" = "ADT-7" ]; then ok "falls back to transcript-path basename"
else bad "transcript-path fallback did not bind"; fi

# 5. An id that is not PREFIX-NNN is rejected.
P="$(newproj)"
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessG" bash "$MARK" "not a ticket" )
if [ ! -e "$P/.adt/state/current-tix.d/sessG" ]; then ok "rejects non PREFIX-NNN id"
else bad "wrote a marker for a garbage id"; fi

# 6. No argument: writes nothing and exits 0.
P="$(newproj)"
( cd "$P" && CLAUDE_CODE_SESSION_ID="sessN" bash "$MARK" )
rc=$?
if [ "$rc" = 0 ] && [ ! -e "$P/.adt/state/current-tix.d" ]; then ok "no arg → no-op, exit 0"
else bad "no-arg case wrote a marker or non-zero exit (rc=$rc)"; fi

echo "== adt-usage-log.sh prefix resolution =="

# 7. Reads id_prefix from .adt/config.yaml, so an ADT-prefixed id binds.
P="$(newproj)"
mkdir -p "$P/.adt"
printf 'project: x\nid_prefix: ADT\ncache_dir: %s/cache\n' "$P" > "$P/.adt/config.yaml"
json="$(printf '{"prompt":"ADT-54 /adt-build","cwd":"%s","session_id":"sessP"}' "$P")"
printf '%s' "$json" | bash "$USAGE_HOOK"
MK="$P/.adt/state/current-tix.d/sessP"
if [ "$(cat "$MK" 2>/dev/null)" = "ADT-54" ]; then ok "ADT-prefixed explicit id latches via config id_prefix"
else bad "ADT-54 did not latch (got '$(cat "$MK" 2>/dev/null)')"; fi

# 8. With no config, the prefix defaults to TIX.
P="$(newproj)"
json="$(printf '{"prompt":"TIX-12 /adt-build","cwd":"%s","session_id":"sessT"}' "$P")"
printf '%s' "$json" | bash "$USAGE_HOOK"
if [ "$(cat "$P/.adt/state/current-tix.d/sessT" 2>/dev/null)" = "TIX-12" ]; then ok "falls open to TIX with no config"
else bad "TIX-12 did not latch without a config"; fi

# 9. The ADT_ID_PREFIX env var overrides the config.
P="$(newproj)"
mkdir -p "$P/.adt"; printf 'id_prefix: ADT\n' > "$P/.adt/config.yaml"
json="$(printf '{"prompt":"FOO-3 /adt-build","cwd":"%s","session_id":"sessE"}' "$P")"
printf '%s' "$json" | ADT_ID_PREFIX=FOO bash "$USAGE_HOOK"
if [ "$(cat "$P/.adt/state/current-tix.d/sessE" 2>/dev/null)" = "FOO-3" ]; then ok "ADT_ID_PREFIX env overrides config"
else bad "env override did not win (got '$(cat "$P/.adt/state/current-tix.d/sessE" 2>/dev/null)')"; fi

echo ""
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]

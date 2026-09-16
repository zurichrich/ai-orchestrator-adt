#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests adt-terminal-title.sh: the terminal tab title it writes for a prompt.
# Run:  bash defaults/hooks/tests/test_adt_terminal_title.sh
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$HOOK_DIR/adt-terminal-title.sh"
PASS=0 FAIL=0

# The hook keeps per-session title memory in $TMPDIR; give the suite its own.
export TMPDIR="$(mktemp -d)/"
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

newproj() {
  local d; d="$(mktemp -d)"
  git -C "$d" init -q
  mkdir -p "$d/.adt/state" "$d/.adt"
  printf 'id_prefix: ADT\n' > "$d/.adt/config.yaml"
  printf '%s' "$d"
}

# Drive the hook and print the title it wrote (OSC 0 payload, markers stripped).
title() {
  local proj="$1" json="$2" out
  out="$(mktemp)"
  printf '%s' "$json" | ADT_TITLE_TTY="$out" bash "$HOOK" >/dev/null 2>&1
  LC_ALL=C tr -d '\007' < "$out" | sed -E 's/^.*\]0;//'
}

echo "== adt-terminal-title.sh =="

# 1. Ticket named in the prompt + a topic → "<TIX> · <topic>".
P="$(newproj)"
T="$(title "$P" "{\"session_id\":\"s1\",\"cwd\":\"$P\",\"prompt\":\"ADT-123 fix the kanban cost badge on done cards\"}")"
case "$T" in
  "✳ ADT-123 · fix the kanban cost badge on done cards") ok "ticket from prompt + topic" ;;
  *) bad "ticket+topic wrong (got '$T')" ;;
esac

# 2. Topic sticks across a bare confirmation (no prompt words to work with).
T="$(title "$P" "{\"session_id\":\"s1\",\"cwd\":\"$P\",\"prompt\":\"yes\"}")"
case "$T" in
  "✳ ADT-123 · fix the kanban cost badge on done cards") ok "bare confirmation keeps the stored topic" ;;
  *) bad "topic not sticky (got '$T')" ;;
esac

# 3. Stop event (no prompt at all) reproduces the same title.
T="$(title "$P" "{\"session_id\":\"s1\",\"cwd\":\"$P\",\"hook_event_name\":\"Stop\"}")"
case "$T" in
  "✳ ADT-123 · fix the kanban cost badge on done cards") ok "Stop event replays ticket + topic" ;;
  *) bad "Stop event title wrong (got '$T')" ;;
esac

# 4. Ticket falls back to the per-session marker when the prompt names none.
mkdir -p "$P/.adt/state/current-tix.d"
printf 'ADT-99\n' > "$P/.adt/state/current-tix.d/s2"
T="$(title "$P" "{\"session_id\":\"s2\",\"cwd\":\"$P\",\"prompt\":\"why does the render drop the badge\"}")"
case "$T" in
  "✳ ADT-99 · why does the render drop the badge") ok "ticket from the per-session marker" ;;
  *) bad "marker fallback wrong (got '$T')" ;;
esac

# 5. Leading zeros are stripped from the ticket number.
T="$(title "$P" "{\"session_id\":\"s3\",\"cwd\":\"$P\",\"prompt\":\"ADT-007 rewrite the sync tick guard\"}")"
case "$T" in
  "✳ ADT-7 · rewrite the sync tick guard") ok "leading zeros stripped (ADT-007 → ADT-7)" ;;
  *) bad "zero-strip wrong (got '$T')" ;;
esac

# 6. No ticket anywhere → topic only, no stray separator.
P2="$(newproj)"
T="$(title "$P2" "{\"session_id\":\"s9\",\"cwd\":\"$P2\",\"prompt\":\"tidy up the installer manifest writer\"}")"
case "$T" in
  "✳ tidy up the installer manifest writer") ok "no ticket → topic only" ;;
  *) bad "no-ticket title wrong (got '$T')" ;;
esac

# 7. Malformed stdin still exits 0.
printf 'not json' | ADT_TITLE_TTY="$(mktemp)" bash "$HOOK" >/dev/null 2>&1
if [ $? = 0 ]; then ok "malformed input exits 0"; else bad "malformed input non-zero exit"; fi

# 8. No tty found → exit 0, nothing written. ADT_TITLE_TTY=none means "no tty";
#    leaving it unset would let the hook find and rename the real terminal tab.
printf '%s' '{"session_id":"s1","cwd":"/","prompt":"anything at all here"}' \
  | ADT_TITLE_TTY=none bash "$HOOK" >/dev/null 2>&1
if [ $? = 0 ]; then ok "unresolvable tty exits 0"; else bad "unresolvable tty non-zero exit"; fi

# 9. A repo without .adt/ still gets a title, and the hook creates nothing in it.
P3="$(mktemp -d)"
git -C "$P3" init -q
BEFORE="$(ls -A "$P3" | wc -l | tr -d ' ')"
T="$(title "$P3" "{\"session_id\":\"s10\",\"cwd\":\"$P3\",\"prompt\":\"tidy up the installer manifest writer\"}")"
AFTER="$(ls -A "$P3" | wc -l | tr -d ' ')"
case "$T" in
  "✳ tidy up the installer manifest writer") ok "non-ADT repo still gets a usable title" ;;
  *) bad "non-ADT repo title wrong (got '$T')" ;;
esac
if [ "$BEFORE" = "$AFTER" ]; then ok "non-ADT repo gains no directory"
else bad "hook created something in a non-ADT repo ($BEFORE -> $AFTER entries)"; fi

# 10. A slash command replaces the stored topic.
P4="$(newproj)"
T="$(title "$P4" "{\"session_id\":\"s11\",\"cwd\":\"$P4\",\"prompt\":\"ADT-123 rewrite the sync tick guard\"}")"
T="$(title "$P4" "{\"session_id\":\"s11\",\"cwd\":\"$P4\",\"prompt\":\"/adt-build\"}")"
case "$T" in
  "✳ ADT-123 · /adt-build") ok "slash command replaces the stored topic" ;;
  *) bad "slash command not the topic (got $T)" ;;
esac

# 11. A command keeps its arguments.
T="$(title "$P4" "{\"session_id\":\"s12\",\"cwd\":\"$P4\",\"prompt\":\"/adt-qa-run 170\"}")"
case "$T" in
  "✳ /adt-qa-run 170") ok "command keeps its arguments" ;;
  *) bad "command arguments dropped (got $T)" ;;
esac

# 12. A two-word prompt becomes the topic.
T="$(title "$P4" "{\"session_id\":\"s13\",\"cwd\":\"$P4\",\"prompt\":\"rerender board\"}")"
case "$T" in
  "✳ rerender board") ok "short comment becomes the topic" ;;
  *) bad "short comment dropped (got $T)" ;;
esac

printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]

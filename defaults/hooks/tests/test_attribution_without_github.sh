#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that after an install without GitHub, adt-usage-log.sh binds the session
# to a ticket with the project's own id prefix and adt-terminal-title.sh shows it.
# Both hooks read id_prefix from .adt/config.yaml and fall back to "TIX" without it.
#
# Run:  bash agent-dev-team/defaults/hooks/tests/test_attribution_without_github.sh
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

export HOME="$TMP/home"
export ADT_USER_PROJECTS="$TMP/userp"
export ADT_EDITOR_CONFIG_HOME="$TMP/editor"
mkdir -p "$HOME" "$ADT_USER_PROJECTS" "$ADT_EDITOR_CONFIG_HOME"

PROJ="$TMP/proj"; mkdir -p "$PROJ"
git -C "$PROJ" init -q .

# The prefix is not TIX, so the fallback prefix cannot make the test pass.
cat > "$ADT_USER_PROJECTS/proj.yaml" <<YAML
path: $PROJ
github:
  repo: someone/proj
  owner: someone
kanban:
  id_prefix: WEB
main_branch: main
YAML

( cd "$PROJ" && bash "$ADT_DIR/setup.sh" --project proj ) >"$TMP/setup.log" 2>&1

if [ -f "$PROJ/.adt/config.yaml" ]; then
  pass "precondition: a --no-github install wrote .adt/config.yaml"
else
  fail "precondition failed: no .adt/config.yaml"
  sed 's/^/     /' "$TMP/setup.log" | tail -15
  echo; echo "FAIL: $FAILS assertion(s)"; exit 1
fi

PROMPT='/adt-build WEB-42 fix the export'
SESSION='sess-1c'
payload() { printf '{"cwd":"%s","prompt":"%s","session_id":"%s"}' "$PROJ" "$PROMPT" "$SESSION"; }

echo "== 1. adt-usage-log.sh latches the ticket =="

payload | bash "$PROJ/.claude/hooks/adt-usage-log.sh" >/dev/null 2>&1
MARKER="$PROJ/.adt/state/current-tix.d/$SESSION"

if [ -f "$MARKER" ]; then
  pass "the session marker was written"
else
  fail "no session marker was written"
fi

got="$(head -1 "$MARKER" 2>/dev/null | tr -d '[:space:]' || true)"
if [ "$got" = "WEB-42" ]; then
  pass "the marker holds WEB-42 (the project's real prefix, not the TIX fallback)"
else
  fail "the marker holds '${got:-<empty>}', expected WEB-42"
fi

echo "== 2. adt-terminal-title.sh renders the ticket =="

OUT="$TMP/title.out"; : > "$OUT"
payload | ADT_TITLE_TTY="$OUT" bash "$PROJ/.claude/hooks/adt-terminal-title.sh" >/dev/null 2>&1

if grep -q "WEB-42" "$OUT" 2>/dev/null; then
  pass "the tab title names the ticket"
else
  fail "the tab title does not name the ticket: $(tr -d '\001-\037' < "$OUT")"
fi

if grep -q "adt-build" "$OUT" 2>/dev/null; then
  pass "the tab title also carries the topic from the latest prompt"
else
  fail "the tab title lost the topic: $(tr -d '\001-\037' < "$OUT")"
fi

echo "== 3. the control: without the config, both halves fail =="

# Remove only .adt/config.yaml and re-run: the hooks fall back to "TIX", which
# cannot match WEB-42.
#
# A new session id is needed because adt-terminal-title.sh reuses the last
# ticket it cached for a session when the current prompt names none.
CONTROL_SESSION='sess-1c-control'
CONTROL_MARKER="$PROJ/.adt/state/current-tix.d/$CONTROL_SESSION"
control_payload() { printf '{"cwd":"%s","prompt":"%s","session_id":"%s"}' "$PROJ" "$PROMPT" "$CONTROL_SESSION"; }

rm -f "$PROJ/.adt/config.yaml"
control_payload | bash "$PROJ/.claude/hooks/adt-usage-log.sh" >/dev/null 2>&1

if [ ! -f "$CONTROL_MARKER" ]; then
  pass "control: with no config.yaml, usage-log latches nothing"
else
  fail "control failed: a marker appeared without config.yaml"
fi

: > "$OUT"
control_payload | ADT_TITLE_TTY="$OUT" bash "$PROJ/.claude/hooks/adt-terminal-title.sh" >/dev/null 2>&1
if ! grep -q "WEB-42" "$OUT" 2>/dev/null; then
  pass "control: with no config.yaml, the title carries no ticket"
else
  fail "control failed: the title named the ticket without config.yaml"
fi

echo
if [ "$FAILS" -eq 0 ]; then
  echo "All attribution-without-github tests passed."
else
  echo "FAIL: $FAILS assertion(s)"
fi
[ "$FAILS" -eq 0 ]

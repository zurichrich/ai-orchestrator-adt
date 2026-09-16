#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that a real adt-install.sh run sets terminal.integrated.tabs.title to
# "${sequence}" in VS Code and Cursor settings:
#   1. key absent           -> written as "${sequence}", and reported
#   2. key present          -> left at its own value, and reported as left
#   3. editor not installed -> skipped silently, no file created
#   4. Linux path layout    -> handled as well as the macOS one
#   5. re-running (the update path on a second machine) -> idempotent
#
# Run:  bash agent-dev-team/tests/test_install_writes_editor_setting.sh
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

# shellcheck source=tests/lib/editor-setting-fixture.sh
source "$ADT_DIR/tests/lib/editor-setting-fixture.sh"

key_of() {  # key_of <settings.json> -> the value, or <missing>
  /usr/bin/python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("<unparseable>"); raise SystemExit
print(d.get("terminal.integrated.tabs.title", "<missing>"))
' "$1" 2>/dev/null || echo "<missing>"
}

echo "== 1. key absent in VS Code, and Cursor is not installed at all =="

editor_fixture_init
trap 'rm -rf "$TMP"' EXIT
editor_seed_settings Code '{
    "editor.fontSize": 13
}'
editor_run_install

CODE="$(editor_settings_path Code)"
if [ "$(key_of "$CODE")" = '${sequence}' ]; then
  pass 'VS Code: the key was written as "${sequence}"'
else
  fail "VS Code: key is '$(key_of "$CODE")', expected \${sequence}"
  sed 's/^/     /' "$TMP/install.log" | tail -20
fi

if /usr/bin/python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); raise SystemExit(0 if d.get("editor.fontSize")==13 else 1)' "$CODE"; then
  pass "VS Code: the operator's own settings survived the write"
else
  fail "VS Code: an existing key was lost"
fi

if [ ! -e "$(editor_settings_path Cursor)" ]; then
  pass "Cursor: not installed, so nothing was created for it"
else
  fail "Cursor: a settings.json was created for an editor that is not installed"
fi

if grep -q "set terminal.integrated.tabs.title" "$TMP/install.log"; then
  pass "the write was reported in the install output"
else
  fail "the write was not reported in the install output"
fi
rm -rf "$TMP"

echo "== 2. key already present, with a value the operator chose =="

editor_fixture_init
trap 'rm -rf "$TMP"' EXIT
editor_seed_settings Code '{
    "terminal.integrated.tabs.title": "${cwdFolder}"
}'
editor_run_install

if [ "$(key_of "$(editor_settings_path Code)")" = '${cwdFolder}' ]; then
  pass "an existing value was left exactly as the operator set it"
else
  fail "an existing value was overwritten: now '$(key_of "$(editor_settings_path Code)")'"
fi

if grep -q "already set to" "$TMP/install.log"; then
  pass "leaving it alone was reported"
else
  fail "leaving it alone was not reported"
fi
rm -rf "$TMP"

echo "== 3. the Linux path layout =="

editor_fixture_init
trap 'rm -rf "$TMP"' EXIT
editor_seed_settings Cursor '{}' linux
editor_run_install

if [ "$(key_of "$(editor_settings_path Cursor linux)")" = '${sequence}' ]; then
  pass "Cursor under ~/.config (Linux layout) was written"
else
  fail "Linux layout not handled: key is '$(key_of "$(editor_settings_path Cursor linux)")'"
fi
rm -rf "$TMP"

echo "== 4. the update path: running the install a second time =="

editor_fixture_init
trap 'rm -rf "$TMP"' EXIT
editor_seed_settings Code '{
    "editor.fontSize": 13
}'
editor_run_install
first="$(shasum -a 256 "$(editor_settings_path Code)" | awk '{print $1}')"
editor_run_install
second="$(shasum -a 256 "$(editor_settings_path Code)" | awk '{print $1}')"

if [ "$first" = "$second" ]; then
  pass "a second install left the file byte-identical (idempotent)"
else
  fail "a second install rewrote the file"
fi

if grep -q "already set to" "$TMP/install.log"; then
  pass "the second run reported the key as already set"
else
  fail "the second run did not report the existing key"
fi

echo
if [ "$FAILS" -eq 0 ]; then
  echo "All install-writes-editor-setting tests passed."
else
  echo "FAIL: $FAILS assertion(s)"
fi
[ "$FAILS" -eq 0 ]

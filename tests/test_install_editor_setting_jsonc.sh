#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that the installer leaves an editor settings.json it cannot parse as
# strict JSON (comments, trailing commas) unchanged, warns, and names the path.
#
# Run:  bash agent-dev-team/tests/test_install_editor_setting_jsonc.sh
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

# shellcheck source=tests/lib/editor-setting-fixture.sh
source "$ADT_DIR/tests/lib/editor-setting-fixture.sh"

echo "== a settings.json with comments and a trailing comma =="

editor_fixture_init
trap 'rm -rf "$TMP"' EXIT

JSONC='{
    // I keep my font small on this machine
    "editor.fontSize": 11,

    /* and the terminal bigger */
    "terminal.integrated.fontSize": 14,
}'
editor_seed_settings Code "$JSONC"
P="$(editor_settings_path Code)"
before="$(shasum -a 256 "$P" | awk '{print $1}')"

editor_run_install

after="$(shasum -a 256 "$P" | awk '{print $1}')"

if [ "$before" = "$after" ]; then
  pass "the file is byte-identical"
else
  fail "the file was modified (expected it unchanged)"
  echo "     --- what it looks like now ---"; sed 's/^/     /' "$P"
fi

if grep -q "not strict JSON" "$TMP/install.log"; then
  pass "the install warned"
else
  fail "expected a 'not strict JSON' warning in the install log"
  sed 's/^/     /' "$TMP/install.log" | tail -20
fi

if grep -qF "$P" "$TMP/install.log"; then
  pass "the warning names the path"
else
  fail "the warning does not name the file it skipped"
fi

if [ -d "$PROJ/.claude" ]; then
  pass "the install completed"
else
  fail "the install did not complete"
fi

echo "== the same run still writes another editor's parseable file =="

rm -rf "$TMP"
editor_fixture_init
trap 'rm -rf "$TMP"' EXIT
editor_seed_settings Code "$JSONC"
editor_seed_settings Cursor '{}'
editor_run_install

got="$(/usr/bin/python3 -c '
import json,sys
print(json.load(open(sys.argv[1])).get("terminal.integrated.tabs.title","<missing>"))
' "$(editor_settings_path Cursor)" 2>/dev/null || echo '<missing>')"

if [ "$got" = '${sequence}' ]; then
  pass "Cursor was written even though VS Code's file was unparseable"
else
  fail "Cursor was not written: key is '$got'"
fi

echo
if [ "$FAILS" -eq 0 ]; then
  echo "All editor-setting JSONC tests passed."
else
  echo "FAIL: $FAILS assertion(s)"
fi
[ "$FAILS" -eq 0 ]

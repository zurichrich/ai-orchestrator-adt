#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Checks that uninstall leaves the editor's terminal tab-title setting, which
# install wrote, unchanged.
#
# Run:  bash agent-dev-team/tests/test_uninstall_keeps_editor_setting.sh
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

# shellcheck source=tests/lib/editor-setting-fixture.sh
source "$ADT_DIR/tests/lib/editor-setting-fixture.sh"

editor_fixture_init
trap 'rm -rf "$TMP"' EXIT

echo "== install writes it, uninstall leaves it =="

editor_seed_settings Code '{
    "editor.fontSize": 13
}'
editor_run_install

P="$(editor_settings_path Code)"
got="$(/usr/bin/python3 -c '
import json,sys
print(json.load(open(sys.argv[1])).get("terminal.integrated.tabs.title","<missing>"))
' "$P" 2>/dev/null || echo '<missing>')"

if [ "$got" = '${sequence}' ]; then
  pass "precondition: the install wrote the key"
else
  fail "precondition failed: the install did not write the key (got '$got')"
  echo "     --- install log ---"; sed 's/^/     /' "$TMP/install.log" | tail -15
  echo; echo "FAIL: $FAILS assertion(s)"; exit 1
fi

after_install="$(shasum -a 256 "$P" | awk '{print $1}')"

( cd "$PROJ" && printf 'y\ny\ny\n' | \
  PATH="$BIN:$PATH" HOME="$FAKE_HOME" ADT_USER_PROJECTS="$USERP" \
  ADT_EDITOR_CONFIG_HOME="$EDITOR_HOME" \
  bash "$ADT_DIR/adt-install.sh" --uninstall --no-github ) >"$TMP/uninstall.log" 2>&1 || true

if [ ! -f "$P" ]; then
  fail "uninstall DELETED the operator's settings.json"
  echo; echo "FAIL: $FAILS assertion(s)"; exit 1
fi

after_uninstall="$(shasum -a 256 "$P" | awk '{print $1}')"

if [ "$after_install" = "$after_uninstall" ]; then
  pass "settings.json is byte-identical after uninstall"
else
  fail "uninstall modified settings.json"
  echo "     --- now ---"; sed 's/^/     /' "$P"
fi

got="$(/usr/bin/python3 -c '
import json,sys
print(json.load(open(sys.argv[1])).get("terminal.integrated.tabs.title","<missing>"))
' "$P" 2>/dev/null || echo '<missing>')"
if [ "$got" = '${sequence}' ]; then
  pass "the tab-title setting survives an uninstall"
else
  fail "the setting was reverted: now '$got'"
fi

# Confirm the uninstall ran. It removes the manifest and .adt/ but keeps .claude/.
if [ ! -e "$PROJ/.claude/.adt-manifest.json" ] && [ ! -d "$PROJ/.adt" ]; then
  pass "control: the uninstall really ran (manifest and .adt/ are gone)"
else
  fail "control failed: uninstall did not run"
  echo "     --- uninstall log ---"; sed 's/^/     /' "$TMP/uninstall.log" | tail -15
fi

echo
if [ "$FAILS" -eq 0 ]; then
  echo "All uninstall-keeps-editor-setting tests passed."
else
  echo "FAIL: $FAILS assertion(s)"
fi
[ "$FAILS" -eq 0 ]

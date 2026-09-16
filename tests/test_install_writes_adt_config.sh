#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Checks that setup.sh writes .adt/config.yaml on an install without
# --init-github:
#   1. .adt/config.yaml exists.
#   2. It has the project's own id_prefix, not the "TIX" fallback.
#   3. project_number is present and empty.
#   4. A second install keeps a project_number that was already set.
#
# Run:  bash agent-dev-team/tests/test_install_writes_adt_config.sh
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# A throwaway project and per-user config dir, with HOME redirected into the sandbox.
export HOME="$TMP/home"
export ADT_EDITOR_CONFIG_HOME="$TMP/editor"
export ADT_USER_PROJECTS="$TMP/userp"
mkdir -p "$HOME" "$ADT_EDITOR_CONFIG_HOME" "$ADT_USER_PROJECTS"

PROJ="$TMP/proj"
mkdir -p "$PROJ"
git -C "$PROJ" init -q .

cat > "$ADT_USER_PROJECTS/proj.yaml" <<YAML
path: $PROJ
github:
  repo: someone/proj
  owner: someone
kanban:
  id_prefix: WEB
main_branch: main
YAML

echo "== 1. a --no-github install writes .adt/config.yaml =="

( cd "$PROJ" && bash "$ADT_DIR/setup.sh" --project proj ) >"$TMP/log1" 2>&1
CFG="$PROJ/.adt/config.yaml"

if [ -f "$CFG" ]; then
  pass ".adt/config.yaml exists after a --no-github install"
else
  fail ".adt/config.yaml was not written"
  echo "     --- setup.sh output ---"; sed 's/^/     /' "$TMP/log1"
fi

echo "== 2. it carries the project's real id_prefix =="

got="$(grep -E '^id_prefix:' "$CFG" 2>/dev/null | sed -E 's/^id_prefix:[[:space:]]*//; s/[[:space:]]*$//')"
if [ "$got" = "WEB" ]; then
  pass "id_prefix is 'WEB'"
else
  fail "id_prefix is '${got:-<missing>}', expected 'WEB'"
fi

echo "== 3. project_number is present and empty =="

if grep -qE '^project_number:[[:space:]]*$' "$CFG" 2>/dev/null; then
  pass "project_number is present and empty"
else
  fail "expected an empty project_number, got: $(grep -E '^project_number:' "$CFG" || echo '<missing>')"
fi

echo "== 4. the update path: a second run preserves an existing project_number =="

# Set project_number as --init-github would have.
/usr/bin/python3 - "$CFG" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
open(p, "w").write(s.replace("project_number: \n", "project_number: 42\n"))
PY

( cd "$PROJ" && bash "$ADT_DIR/setup.sh" --project proj ) >"$TMP/log2" 2>&1

got="$(grep -E '^project_number:' "$CFG" 2>/dev/null | sed -E 's/^project_number:[[:space:]]*//; s/[[:space:]]*$//')"
if [ "$got" = "42" ]; then
  pass "a re-install kept project_number=42"
else
  fail "a re-install wrote project_number='${got:-<empty>}', expected '42'"
fi

got="$(grep -E '^id_prefix:' "$CFG" 2>/dev/null | sed -E 's/^id_prefix:[[:space:]]*//; s/[[:space:]]*$//')"
if [ "$got" = "WEB" ]; then
  pass "a re-install keeps id_prefix 'WEB'"
else
  fail "after re-install id_prefix is '${got:-<missing>}', expected 'WEB'"
fi

echo
if [ "$FAILS" -eq 0 ]; then
  echo "All install-writes-adt-config tests passed."
else
  echo "FAIL: $FAILS assertion(s)"
fi
[ "$FAILS" -eq 0 ]

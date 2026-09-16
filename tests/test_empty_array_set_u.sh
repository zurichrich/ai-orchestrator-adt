#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Checks that setup.sh survives an empty array under `set -u`. bash 3.2, the
# macOS system bash, treats expanding an empty array as an unbound variable.
# Newer bash does not, so on Linux these pass without exercising anything.
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# ── 1. setup.sh survives a machine with ZERO configured projects ────────────
echo "[test] setup.sh with no configured projects does not die on an empty array"

# setup.sh exits early if any of these tools is missing, so skip in that case.
# Keep this list in step with setup.sh's own preflight list.
missing=""
for t in gh claude jq yq curl git; do
  command -v "$t" >/dev/null 2>&1 || missing="$missing $t"
done

if [[ -n "$missing" ]]; then
  echo "  SKIP: setup.sh preflight needs$missing"
else
  # A sandbox HOME, because setup.sh deletes old ~/.claude/commands/adt-*.md links.
  # setup.sh also reads $ADT_DIR/projects/*.yaml, which holds only example.yaml;
  # setup.sh skips that file, so the project list is empty.
  mkdir -p "$TMP/home" "$TMP/noprojects"
  out="$(env HOME="$TMP/home" ADT_USER_PROJECTS="$TMP/noprojects" \
         "$ADT_DIR/setup.sh" 2>&1)" || true

  if grep -q 'unbound variable' <<<"$out"; then
    fail "setup.sh died on an empty array: $(grep 'unbound variable' <<<"$out" | head -1)"
  else
    pass "no unbound-variable death with an empty project list"
  fi

  if grep -q 'no projects configured' <<<"$out"; then
    pass "reaches its own 'no projects configured' message"
  else
    fail "expected 'no projects configured', got: $(tail -3 <<<"$out" | tr '\n' ' ')"
  fi

  # ── 1b. the same empty array, with --project ────────────────────────────
  # Checked on the message because setup.sh exits 1 here whether or not it
  # hits the bug.
  out="$(env HOME="$TMP/home" ADT_USER_PROJECTS="$TMP/noprojects" \
         "$ADT_DIR/setup.sh" --project nosuch 2>&1)" || true

  if grep -q 'unbound variable' <<<"$out"; then
    fail "--project died on an empty array: $(grep 'unbound variable' <<<"$out" | head -1)"
  else
    pass "--project: no unbound-variable death with an empty project list"
  fi

  if grep -q "no config for project 'nosuch'" <<<"$out"; then
    pass "--project: reaches its own 'no config for project' message"
  else
    fail "--project: expected 'no config for project', got: $(tail -3 <<<"$out" | tr '\n' ' ')"
  fi
fi

# ── 2. the guarded expansion works on the running bash ──────────────────────
echo "[test] the guarded expansion is safe on this bash under set -u"
if "$BASH" -c 'set -euo pipefail; a=(); printf "%s" ${a[@]+"${a[@]}"}; exit 0' 2>/dev/null; then
  pass "\${a[@]+\"\${a[@]}\"} expands an empty array without tripping set -u"
else
  fail "the guarded form itself failed on $("$BASH" --version | head -1)"
fi

echo ""
if [[ $FAILS -eq 0 ]]; then
  printf '\033[32mall empty-array tests passed\033[0m\n'; exit 0
else
  printf '\033[31m%d empty-array test(s) failed\033[0m\n' "$FAILS"; exit 1
fi

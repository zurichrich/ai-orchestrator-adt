#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Checks that every required status check on `main` is a job defined by a
# workflow file in this repo. Section A tests the comparison against fixtures,
# offline. Section B runs it against this repo's branch protection, and skips
# when gh is missing or cannot reach the repo.
#
# Run:  bash agent-dev-team/tests/test_no_orphan_required_checks.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }
skip() { printf '  \033[33mSKIP\033[0m %s\n' "$1"; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# ── The contexts a workflow file can report: job ids + their `name:` overrides ──
# A regex scan rather than a YAML parse, so yq is not needed.
contexts_defined_in() {   # contexts_defined_in <workflows-dir>
  local d="$1"
  [ -d "$d" ] || return 0
  find "$d" -maxdepth 1 \( -name '*.yml' -o -name '*.yaml' \) 2>/dev/null | sort |
  while IFS= read -r f; do
    /usr/bin/python3 - "$f" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r"^jobs:\s*$(.*)", text, re.S | re.M)
if not m:
    sys.exit(0)
for line in m.group(1).splitlines():
    job = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
    if job:
        print(job.group(1)); continue
    nm = re.match(r"^    name:\s*(.+?)\s*$", line)
    if nm:
        print(nm.group(1).strip(chr(34) + chr(39)))
PY
  done
}

# Prints "ORPHAN <ctx>" for each required context no workflow defines.
orphans() {   # orphans <workflows-dir> <required-contexts-file>
  local defined; defined="$(contexts_defined_in "$1")"
  while IFS= read -r ctx; do
    [ -n "$ctx" ] || continue
    printf '%s\n' "$defined" | grep -qxF "$ctx" || printf 'ORPHAN %s\n' "$ctx"
  done < "$2"
}

echo "== Section A: the comparison, against fixtures =="

mkdir -p "$TMP/wf"
printf 'name: tests\non:\n  pull_request:\njobs:\n  tests:\n    runs-on: ubuntu-latest\n    steps:\n      - run: true\n' > "$TMP/wf/tests.yml"
printf 'name: lint\non:\n  pull_request:\njobs:\n  lint-job:\n    name: license-headers\n    runs-on: ubuntu-latest\n    steps:\n      - run: true\n' > "$TMP/wf/lint.yml"

got="$(contexts_defined_in "$TMP/wf" | sort | tr '\n' ' ')"
[ "$got" = "license-headers lint-job tests " ] \
  && pass "job ids and name: overrides both parsed ($got)" \
  || fail "parser returned '$got'"

printf 'tests\nlicense-headers\n' > "$TMP/req_ok"
out="$(orphans "$TMP/wf" "$TMP/req_ok")"
[ -z "$out" ] && pass "all required contexts defined → no orphan reported" \
              || fail "expected none, got: $out"

printf 'tests\ncla\n' > "$TMP/req_orphan"
out="$(orphans "$TMP/wf" "$TMP/req_orphan")"
[ "$out" = "ORPHAN cla" ] && pass "a required context with no workflow is reported" \
                          || fail "expected 'ORPHAN cla', got: $out"

printf 'tests\ncla\nlicense-headers\n' > "$TMP/req_all"
out="$(orphans "$TMP/empty-wf" "$TMP/req_all" 2>/dev/null | sort | tr '\n' ' ')"
[ "$out" = "ORPHAN cla ORPHAN license-headers ORPHAN tests " ] \
  && pass "no workflows at all + 3 required contexts → all 3 orphaned" \
  || fail "expected 3 orphans, got: $out"

: > "$TMP/req_none"
[ -z "$(orphans "$TMP/wf" "$TMP/req_none")" ] && pass "zero required contexts → nothing to orphan" \
                                              || fail "expected no output"

echo
echo "== Section B: this repo's real branch protection =="

if ! command -v gh >/dev/null 2>&1; then
  skip "gh not installed — server half unchecked"
elif ! SLUG="$(git -C "$REPO" remote get-url origin 2>/dev/null \
                | sed -E 's#^(https://github\.com/|git@github\.com:)##; s#\.git$##')" \
     || [ -z "$SLUG" ]; then
  skip "no github origin remote — server half unchecked"
elif ! gh api "repos/$SLUG" --jq .full_name >/dev/null 2>&1; then
  skip "gh cannot reach $SLUG (unauthenticated or offline) — server half unchecked"
else
  gh api "repos/$SLUG/branches/main/protection/required_status_checks" --jq '.contexts[]' \
    > "$TMP/req_live" 2>/dev/null || : > "$TMP/req_live"
  n="$(grep -c . "$TMP/req_live" || true)"
  out="$(orphans "$REPO/.github/workflows" "$TMP/req_live")"
  if [ -z "$out" ]; then
    pass "$SLUG: $n required context(s), all defined by a workflow in this repo"
  else
    while IFS= read -r line; do
      ctx="${line#ORPHAN }"
      fail "$SLUG requires '$ctx' but no workflow here reports it, so every PR will block. Remove it: gh api -X DELETE repos/$SLUG/branches/main/protection/required_status_checks/contexts -f 'contexts[]=$ctx'"
    done <<< "$out"
  fi
fi

printf '\n'
if [ "$FAILS" -eq 0 ]; then
  echo "All required-check/workflow consistency tests passed."
else
  echo "FAIL: $FAILS assertion(s)"
  exit 1
fi

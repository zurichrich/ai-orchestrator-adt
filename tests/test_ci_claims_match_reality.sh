#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that no reader-facing file claims a workflow checks PRs when the repo
# has no workflows.
#
#   No workflow files  -> each named file must contain its no-CI sentence.
#   Workflow files     -> skipped; test_no_orphan_required_checks.sh covers the
#                         server side.

set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

pass=0 fail=0
ok()  { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }

# has_workflows <root> — true when the repo defines at least one workflow.
has_workflows() {
  local root="$1"
  [ -d "$root/.github/workflows" ] || return 1
  find "$root/.github/workflows" -maxdepth 1 -name '*.yml' -o -maxdepth 1 -name '*.yaml' 2>/dev/null \
    | grep -q .
}

echo "== A. fixtures: workflow detection"

FIX="$(mktemp -d)"; trap 'rm -rf "$FIX"' EXIT
mkdir -p "$FIX/empty" "$FIX/withci/.github/workflows"
printf 'name: t\n' > "$FIX/withci/.github/workflows/t.yml"

if has_workflows "$FIX/withci"; then ok "a repo WITH a workflow is detected"
else bad "workflow present but not detected"; fi

if ! has_workflows "$FIX/empty"; then ok "a repo with NO workflows is detected"
else bad "no workflows present but detected anyway"; fi

mkdir -p "$FIX/dironly/.github/workflows"
if ! has_workflows "$FIX/dironly"; then ok "an EMPTY workflows directory is not a workflow"
else bad "empty .github/workflows counted as CI"; fi

echo "== B. this repo"

# Each entry is file|sentence: a fixed string the file must contain when the
# repo has no CI.
CLAIMS=(
  "CLA.md|This repository runs no CI (ADR-036), so nothing"
  "LICENSING.md|tests/test_licence_header_coverage.sh\` fails when a shipped source file is"
  "defaults/rules/multi-agent-git-workflow.md|it is the state a repo with no CI stays in"
  ".claude/rules/multi-agent-git-workflow.md|it is the state a repo with no CI stays in"
)

if has_workflows "."; then
  echo "  SKIP — this repo defines workflows; the CI claims may be true again."
  echo "         test_no_orphan_required_checks.sh covers the server-side half."
else
  for entry in "${CLAIMS[@]}"; do
    f="${entry%%|*}"; sentence="${entry#*|}"
    if [ ! -f "$f" ]; then
      bad "$f is missing — cannot check its CI claim"
      continue
    fi
    if grep -qF "$sentence" "$f"; then
      ok "$f states what actually checks it, with no CI"
    else
      bad "$f does not carry its corrected sentence — it may still claim a workflow gates PRs"
    fi
  done
fi

echo
printf 'ci-claims: %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

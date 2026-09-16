#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that .gitattributes marks the generated .claude/ tree `merge=ours` and
# leaves authored source unmarked. Checked with `git check-attr`.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADT_DIR="${1:-$(cd "$HERE/.." && pwd)}"
cd "$ADT_DIR"
fails=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fails=$((fails+1)); }

echo "[test] generated paths are marked merge=ours"
for p in .claude/commands/adt-plan.md .claude/tools/adt_cost.py \
         .claude/rules/multi-agent-git-workflow.md .claude/.adt-manifest.json; do
  v="$(git check-attr merge -- "$p" | sed 's/.*: //')"
  [ "$v" = "ours" ] && pass "$p -> merge=ours" || fail "$p -> merge=$v (expected ours)"
done

echo "[test] authored source is not marked"
for p in commands/plan.md defaults/rules/multi-agent-git-workflow.md \
         tools/adt_cost.py tests/test_uninstall.sh CLAUDE.md; do
  v="$(git check-attr merge -- "$p" | sed 's/.*: //')"
  [ "$v" = "ours" ] && fail "$p is marked merge=ours (expected unmarked)" \
                    || pass "$p is not marked"
done

echo "[test] .gitattributes names the merge driver setting it needs"
grep -q "merge.ours.driver" .gitattributes \
  && pass "'git config merge.ours.driver true' is recorded" \
  || fail ".gitattributes does not mention 'git config merge.ours.driver true'"

echo
[ "$fails" -eq 0 ] && { echo "All generated-merge-attribute tests passed."; exit 0; }
echo "test_generated_merge_attributes: FAIL ($fails)"; exit 1

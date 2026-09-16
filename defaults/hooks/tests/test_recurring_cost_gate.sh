#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests the recurring-cost gate in adt-done-guard.sh: a ticket whose commits touch
# infrastructure files is denied done until it has a `recurring_cost:` value.
# Each case builds a real repo, since the gate reads the commits with `git show`.
#
# Run:  bash agent-dev-team/defaults/hooks/tests/test_recurring_cost_gate.sh
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUARD="$HOOK_DIR/adt-done-guard.sh"
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

# Point the deploy-freshness gate at an empty agents dir so it never reads this
# machine's ~/Library/LaunchAgents.
ADT_LAUNCHAGENTS_DIR="$(mktemp -d)/no-agents"; mkdir -p "$ADT_LAUNCHAGENTS_DIR"
export ADT_LAUNCHAGENTS_DIR

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

is_deny()      { printf '%s' "$1" | grep -q '"permissionDecision": *"deny"'; }
mentions_cost() { printf '%s' "$1" | grep -q 'recurring-cost gate'; }

# repo_with <path-the-commit-touches> — a git repo with one commit touching it.
# Echoes the repo path and the SHA.
repo_with() {
  local d="$TMP/r$RANDOM$RANDOM" f="$1"
  mkdir -p "$d/$(dirname "$f")" 2>/dev/null || mkdir -p "$d"
  git -C "$d" init -q
  git -C "$d" config user.email t@t.t; git -C "$d" config user.name t
  echo "x" > "$d/$f"
  git -C "$d" add -A; git -C "$d" commit -q -m "touch $f"
  printf '%s %s\n' "$d" "$(git -C "$d" rev-parse --short HEAD)"
}

# ticket <repo> <sha> <recurring_cost-line-or-empty> — writes bugs/qa/t.md
ticket() {
  local d="$1" sha="$2" cost="$3"
  mkdir -p "$d/bugs/qa" "$d/bugs/done" "$d/.adt"
  {
    echo "---"
    echo "id: ADT-999"
    echo "tokens: unattributed"
    echo "cost_usd: unattributed"
    echo "cost_tier: unattributed"
    echo "stage: qa"
    echo "commits:"
    echo "  - $sha"
    [ -n "$cost" ] && echo "$cost"
    echo "---"
    echo
    echo "# ticket"
  } > "$d/bugs/qa/t.md"
}

drive() {  # drive <repo> — the done move, run from inside the repo
  printf '{"tool_input":{"command":"mv bugs/qa/t.md bugs/done/t.md"},"cwd":"%s"}' "$1" \
    | bash "$GUARD" 2>/dev/null
}

echo "== adt-done-guard.sh: recurring-cost gate =="

# ── 1. A CI workflow, no recurring_cost: → DENY ────────────────────────────
read -r R SHA <<<"$(repo_with '.github/workflows/tests.yml')"
ticket "$R" "$SHA" ""
out="$(drive "$R")"
if is_deny "$out" && mentions_cost "$out"; then
  ok "commit touches .github/workflows/ + no recurring_cost: → DENY"
else
  bad "expected a recurring-cost DENY, got: $out"
fi

# ── 2. the same ticket with the field → not denied for cost ────────────────
ticket "$R" "$SHA" "recurring_cost: ~2500 Actions min/month (measured 2026-09-07)"
out="$(drive "$R")"
if mentions_cost "$out"; then
  bad "recurring_cost: present but the gate still fired: $out"
else
  ok "same ticket + recurring_cost: → cost gate does not fire"
fi

# ── 3. `none` satisfies the gate ───────────────────────────────────────────
ticket "$R" "$SHA" "recurring_cost: none"
out="$(drive "$R")"
if mentions_cost "$out"; then
  bad "'none' should satisfy the gate, got: $out"
else
  ok "recurring_cost: none satisfies the gate"
fi

# ── 4. An empty value does not satisfy the gate ────────────────────────────
ticket "$R" "$SHA" "recurring_cost:"
out="$(drive "$R")"
if is_deny "$out" && mentions_cost "$out"; then
  ok "recurring_cost: with no value → still DENY"
else
  bad "an empty value should not satisfy the gate, got: $out"
fi

# ── 5. A commit that touches no infrastructure is not gated ────────────────
read -r R2 SHA2 <<<"$(repo_with 'tools/adt_sync.py')"
ticket "$R2" "$SHA2" ""
out="$(drive "$R2")"
if mentions_cost "$out"; then
  bad "a tools/ commit must not trigger the cost gate: $out"
else
  ok "commit touches only tools/ → cost gate silent"
fi

# ── 6. Each kind of infrastructure path is detected ────────────────────────
for path in \
    ".github/workflows/ci.yml" "Dockerfile" "docker-compose.yml" \
    "infra/main.tf" "deploy/wrangler.toml" "etc/adt.service" \
    "Library/LaunchAgents/adt.plist" "Jenkinsfile"; do
  read -r RR SS <<<"$(repo_with "$path")"
  ticket "$RR" "$SS" ""
  out="$(drive "$RR")"
  if is_deny "$out" && mentions_cost "$out"; then
    ok "infrastructure path detected: $path"
  else
    bad "expected DENY for $path, got: $out"
  fi
done

# ── 7. No commits: block → the gate stays silent ───────────────────────────
read -r R3 SHA3 <<<"$(repo_with '.github/workflows/x.yml')"
mkdir -p "$R3/bugs/qa" "$R3/bugs/done"
printf -- '---\nid: ADT-998\nstage: qa\ntokens: unattributed\ncost_usd: unattributed\ncost_tier: unattributed\n---\n\n# t\n' > "$R3/bugs/qa/t.md"
out="$(drive "$R3")"
if mentions_cost "$out"; then
  bad "no commits: block → gate should stay silent, got: $out"
else
  ok "ticket with no commits: → gate silent"
fi

# ── 8. A SHA git cannot resolve → the gate stays silent ────────────────────
ticket "$R3" "deadbee" ""
out="$(drive "$R3")"
if mentions_cost "$out"; then
  bad "unresolvable SHA → gate should stay silent, got: $out"
else
  ok "unresolvable commit SHA → gate silent"
fi

printf '\nPASS=%d FAIL=%d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1

#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that install keeps a project's own agent with a generic name
# (security-reviewer.md) and also installs ADT's adt-prefixed agent beside it.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; ADT="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0
ok(){ echo "  PASS $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL $1"; FAIL=$((FAIL+1)); }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
P="$T/proj"; mkdir -p "$P/.claude/agents"; git -C "$P" init -q . 2>/dev/null

printf -- '---\nname: security-reviewer\n---\nTHE PROJECT OWN REVIEWER\n' \
  > "$P/.claude/agents/security-reviewer.md"
bash "$ADT/lib/install-defaults.sh" "$ADT" "$P" >/dev/null 2>&1

grep -q "THE PROJECT OWN REVIEWER" "$P/.claude/agents/security-reviewer.md" \
  && ok "the project's own agent is untouched" || bad "ADT clobbered the project's agent"
[ -f "$P/.claude/agents/adt-security-reviewer.md" ] \
  && ok "ADT's agent installs alongside it" || bad "adt-security-reviewer.md not installed"
grep -q '^name: adt-security-reviewer' "$P/.claude/agents/adt-security-reviewer.md" \
  && ok "its frontmatter name is prefixed too (subagent_type resolves on name)" \
  || bad "frontmatter name not prefixed"
grep -rq 'subagent_type: "adt-security-reviewer"' "$P/.claude/commands/" \
  && ok "the dispatch site names the prefixed agent" || bad "dispatch site still generic"
echo; echo "PASS=$PASS FAIL=$FAIL"; [ "$FAIL" -eq 0 ]

#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests lib/secret-scan.sh: the built-in patterns with and without a project
# config, project patterns added on top, and the re-scope confirmation prompt.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; ADT="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0
ok(){ echo "  PASS $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL $1"; FAIL=$((FAIL+1)); }

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
unset ADT_PROJECT_CONFIG

# 1. built-in patterns work with no project config
printf 'Postmortem.\nAKIAIOSFODNN7EXAMPLE was in the log.\n' > "$T/a.md"
bash "$ADT/lib/secret-scan.sh" "$T/a.md" >/dev/null 2>&1 \
  && bad "baseline missed an AWS key with no project config" \
  || ok "baseline catches an AWS key with no project config"

printf -- '-----BEGIN RSA PRIVATE KEY-----\nMII...\n' > "$T/b.md"
bash "$ADT/lib/secret-scan.sh" "$T/b.md" >/dev/null 2>&1 \
  && bad "baseline missed a private key" || ok "baseline catches a private key"

printf 'db: postgres://user:hunter2@db.example.com/x\n' > "$T/c.md"
bash "$ADT/lib/secret-scan.sh" "$T/c.md" >/dev/null 2>&1 \
  && bad "baseline missed credentials in a URL" || ok "baseline catches URL credentials"

# 2. clean file passes
printf 'We hit the egress quota and raised the cap. No keys here.\n' > "$T/clean.md"
bash "$ADT/lib/secret-scan.sh" "$T/clean.md" >/dev/null 2>&1 \
  && ok "a clean retro passes" || bad "clean retro wrongly flagged"

# 3. project patterns are added to the built-in ones
cat > "$T/config.yaml" <<'Y'
security:
  secret_patterns: ["ACME-INTERNAL-[0-9]+"]
Y
printf 'ref ACME-INTERNAL-4471\n' > "$T/d.md"
ADT_PROJECT_CONFIG="$T/config.yaml" bash "$ADT/lib/secret-scan.sh" "$T/d.md" >/dev/null 2>&1 \
  && bad "project pattern not applied" || ok "project pattern applied"
ADT_PROJECT_CONFIG="$T/config.yaml" bash "$ADT/lib/secret-scan.sh" "$T/a.md" >/dev/null 2>&1 \
  && bad "baseline lost when a project supplies its own" \
  || ok "baseline still applies alongside a project pattern"

# 4. the re-scope prompt warns even on a clean scan, and refuses without a terminal
out=$(cd "$T" && ADT_RESCOPE_STAMP="$T/stamp" bash -c \
  'source "'"$ADT"'/lib/secret-scan.sh"; adt_confirm_rescope docs/retros/x.md' </dev/null 2>&1); rc=$?
[ $rc -ne 0 ] && ok "non-interactive re-scope refuses" \
             || bad "non-interactive re-scope proceeded silently"
printf '%s' "$out" | grep -q "previously gitignored" \
  && ok "the warning names the tracking change" || bad "warning text missing"

ADT_RESCOPE_STAMP="$T/stamp2" ADT_ASSUME_YES=1 bash -c \
  'source "'"$ADT"'/lib/secret-scan.sh"; adt_confirm_rescope docs/retros/x.md' >/dev/null 2>&1 \
  && ok "explicit ADT_ASSUME_YES proceeds" || bad "ADT_ASSUME_YES did not proceed"
[ -f "$T/stamp2" ] && ok "acknowledgement is stamped" || bad "no stamp written"

echo; echo "PASS=$PASS FAIL=$FAIL"; [ "$FAIL" -eq 0 ]

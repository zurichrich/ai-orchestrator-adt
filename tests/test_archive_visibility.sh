#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that the uninstall telemetry archive checks repo visibility before it
# posts: a private repo gets an Issue, and a public or unknown repo gets only a
# local gzip.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; ADT="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0
ok(){ echo "  PASS $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL $1"; FAIL=$((FAIL+1)); }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; mkdir -p "$T/bin"

stub() { # $1 = the literal .private answer: true | false | (empty = API failed)
  cat > "$T/bin/gh" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$T/gh.log"
case "\$*" in
  *private*) printf '%s\n' '$1' ;;
  *issues/*/comments*) echo '{"id":1}' ;;
  *"-X POST"*issues*) echo 7 ;;
  *) echo "" ;;
esac
STUB
  chmod +x "$T/bin/gh"
}
seed(){ mkdir -p "$1/.adt/state"; printf 'date\tclones\n2026-09-04\t1\n' > "$1/.adt/state/traffic-log.tsv"; }
run(){ ( set +u; PATH="$T/bin:$PATH"; source "$ADT/lib/uninstall.sh" >/dev/null 2>&1 || true
         _archive_telemetry o/r "$1" ); }

# visibility is read at all
R="$T/a"; seed "$R"; stub true; : > "$T/gh.log"; run "$R" >/dev/null 2>&1
grep -q -- "--jq .private" "$T/gh.log" && ok "visibility is queried before anything is posted" \
  || bad "no visibility query"

# private -> Issue
grep -q "issues/7/comments" "$T/gh.log" && ok "private repo uses the Issue path" || bad "private repo did not post"

# public -> never the Issue
R="$T/b"; seed "$R"; stub false; : > "$T/gh.log"; run "$R" >/dev/null 2>&1
grep -q "issues/7/comments" "$T/gh.log" && bad "PUBLIC repo posted collaborator-only traffic data" \
  || ok "public repo never reaches the Issue path"
[ -f "$R/docs/history/telemetry/traffic-log.tsv.gz" ] && ok "public repo writes a local gzip" \
  || bad "public repo wrote no local archive"

# unknown (API failed) -> treated as public
R="$T/c"; seed "$R"; stub ""; : > "$T/gh.log"; run "$R" >/dev/null 2>&1
grep -q "issues/7/comments" "$T/gh.log" && bad "unknown visibility posted anyway" \
  || ok "unknown visibility is treated as public, not assumed private"

echo; echo "PASS=$PASS FAIL=$FAIL"; [ "$FAIL" -eq 0 ]

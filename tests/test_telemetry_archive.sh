#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that uninstall archives all four telemetry files, splits them across
# comments to fit GitHub's 65,536-character limit, and keeps any file it failed
# to archive. `gh` is stubbed on PATH, so no network call is made.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; ADT="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0
ok(){ echo "  PASS $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL $1"; FAIL=$((FAIL+1)); }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

mkstub() { # $1 = private|public|fail-post
  mkdir -p "$T/bin"
  cat > "$T/bin/gh" <<STUB
#!/usr/bin/env bash
LOG="$T/gh.log"; printf '%s\n' "\$*" >> "\$LOG"
case "\$*" in
  *"repos/o/r --jq .private"*|*"repos/o/r"*--jq*private*) echo "$( [ "$1" = public ] && echo false || echo true )";;
  *issues/*/comments*) [ "$1" = fail-post ] && exit 1; echo '{"id":1}';;
  *"-X POST repos/o/r/issues"*) echo 7;;
  *"repos/o/r/issues?state=all"*) echo "";;
  *) echo "";;
esac
STUB
  chmod +x "$T/bin/gh"
}

seed() {
  local root="$1"; mkdir -p "$root/.adt/state"
  # Over 65,536 chars, so it has to be split.
  head -c 200000 /dev/zero | tr '\0' 'x' > "$root/.adt/state/cost-ledger.log"
  printf '2026-09-09T00:00:00Z\tadt-brief\n' > "$root/.adt/state/usage.log"
  printf '2026-09-09T00:00:00Z\ts\tagent\tadt-security-reviewer\t1\n' > "$root/.adt/state/surface-log.tsv"
  printf 'date\tclones\n2026-09-04\t1\n' > "$root/.adt/state/traffic-log.tsv"
  printf 'noise\n' > "$root/.adt/state/adt-watch.log"
}

run() { ( set +u; PATH="$T/bin:$PATH"; source "$ADT/lib/uninstall.sh" >/dev/null 2>&1 || true
          _archive_telemetry o/r "$1" ); }

# --- private repo: Issue path, all four archived, chunked -------------------
P="$T/priv"; seed "$P"; mkstub private; : > "$T/gh.log"
run "$P" >/dev/null 2>&1; rc=$?
[ $rc -eq 0 ] && ok "private repo archives cleanly" || bad "private archive rc=$rc"
for f in cost-ledger.log usage.log surface-log.tsv traffic-log.tsv; do
  [ -f "$P/.adt/state/$f" ] && bad "$f not deleted after archiving" || ok "$f archived and removed"
done
[ -f "$P/.adt/state/adt-watch.log" ] && ok "adt-watch.log left alone" \
  || bad "adt-watch.log was deleted"
n=$(grep -c "issues/7/comments" "$T/gh.log")
[ "$n" -ge 5 ] && ok "chunked into $n comments (200 KB > one 65,536-char body)" \
  || bad "expected multiple comments, saw $n"

# --- public repo: refuses the Issue, writes a local gzip --------------------
Q="$T/pub"; seed "$Q"; mkstub public; : > "$T/gh.log"
run "$Q" >/dev/null 2>&1
grep -q "issues/7/comments" "$T/gh.log" && bad "posted telemetry to a public repo" \
  || ok "public repo refuses the Issue path"
[ -f "$Q/docs/history/telemetry/traffic-log.tsv.gz" ] \
  && ok "public repo archives traffic-log locally instead" || bad "no local gzip written"

# --- a failed post keeps the file ------------------------------------------
R="$T/failpost"; seed "$R"; mkstub fail-post; : > "$T/gh.log"
run "$R" >/dev/null 2>&1
[ -f "$R/.adt/state/cost-ledger.log" ] \
  && ok "a failed archive keeps the local file" || bad "deleted a file it never archived"

echo; echo "PASS=$PASS FAIL=$FAIL"; [ "$FAIL" -eq 0 ]

#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that uninstall's telemetry archive Issue carries the `adt:archive`
# label, so the sync never turns it into a ticket (ADT-354). Without the label
# a reinstall rebuilt a cache file for the archive and put it on the board.
# Covers the three ways uninstall finds its archive: none exists (create it
# labelled), one is found by label (use it, no title search, no create), and an
# older one is found only by title (label it). `gh` is stubbed on PATH and logs
# every call, so no network call is made.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; ADT="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0
ok(){ echo "  PASS $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL $1"; FAIL=$((FAIL+1)); }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

mkstub() { # $1 = none | bylabel | bytitle
  mkdir -p "$T/bin"
  cat > "$T/bin/gh" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$T/gh.log"
case "\$*" in
  *"repos/o/r --jq .private"*) echo true;;
  *"repos/o/r/labels "*) exit 0;;
  *"issues?labels=adt:archive"*) [ "$1" = bylabel ] && echo 42 || echo "";;
  *"issues?state=all&per_page=100"*) [ "$1" = bytitle ] && echo 55 || echo "";;
  *"issues/"*"/labels"*) echo '[]';;
  *"issues/"*"/comments"*) echo '{"id":1}';;
  *"-X POST repos/o/r/issues "*) echo 7;;
  *) echo "";;
esac
STUB
  chmod +x "$T/bin/gh"
}

run() { # $1 = stub mode
  local root="$T/$1"; mkdir -p "$root/.adt/state"
  printf '2026-09-11T00:00:00Z\tadt-brief\n' > "$root/.adt/state/usage.log"
  mkstub "$1"; : > "$T/gh.log"
  ( set +u; PATH="$T/bin:$PATH"; source "$ADT/lib/uninstall.sh" >/dev/null 2>&1 || true
    _archive_telemetry o/r "$root" ) >/dev/null 2>&1
}
log_has()  { grep -qF -- "$1" "$T/gh.log"; }
created()  { grep -E -- '-X POST repos/o/r/issues( |$)' "$T/gh.log"; }

echo "[test] no archive yet: create it with the label"
run none
log_has "repos/o/r/labels -f name=adt:archive" && ok "label provisioned" || bad "label not provisioned"
c="$(created)"
[ -n "$c" ] && ok "archive created" || bad "no archive created"
echo "$c" | grep -qF "labels[]=adt:archive" && ok "create call carries labels[]=adt:archive" \
  || bad "create call has no adt:archive label: $c"
log_has "issues/7/comments" && ok "telemetry posted to the new archive" || bad "nothing posted to #7"

echo "[test] archive found by label: reuse it"
run bylabel
log_has "issues?state=all&per_page=100" && bad "searched by title after the label found it" \
  || ok "no title search"
[ -z "$(created)" ] && ok "no second archive created" || bad "created a second archive"
log_has "issues/42/comments" && ok "telemetry posted to #42" || bad "nothing posted to #42"

echo "[test] older archive found only by title: label it"
run bytitle
log_has "-X POST repos/o/r/issues/55/labels -f labels[]=adt:archive" && ok "labels POST sent to #55" \
  || bad "archive #55 was not labelled"
[ -z "$(created)" ] && ok "no second archive created" || bad "created a second archive"
log_has "issues/55/comments" && ok "telemetry posted to #55" || bad "nothing posted to #55"

echo; echo "PASS=$PASS FAIL=$FAIL"; [ "$FAIL" -eq 0 ]

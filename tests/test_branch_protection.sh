#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that install offers branch protection, applies it only on an explicit
# yes, and never applies it silently.
#
# Runs offline. The `gh` stub logs every call, so "no write happened" is
# checked against what the code tried to call. The response bodies below are
# the shapes GitHub's API really returns.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADT_DIR="${1:-$(cd "$HERE/.." && pwd)}"
export ADT_DIR

fails=0
pass() { echo "  ok   — $1"; }
fail() { echo "  FAIL — $1" >&2; fails=$((fails+1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; mkdir -p "$BIN"
GHLOG="$TMP/gh.log"

PROTECTED_BODY='{"url":"https://api.github.com/repos/o/r/branches/main/protection","enforce_admins":{"enabled":false},"allow_force_pushes":{"enabled":false}}'
UNPROTECTED_BODY='{"message":"Branch not protected","documentation_url":"https://docs.github.com/rest","status":"404"}'
FORBIDDEN_BODY='{"message":"Resource not accessible by personal access token","status":"403"}'

# _stub_gh <mode> — mode drives what the protection READ returns.
#   protected    : always exit 0 + the protection JSON
#   unprotected  : always exit 1 + "Branch not protected"
#   forbidden    : always exit 1 + a 403 body
#   became-protected : the read says protected. Used with _run_prompt, which is
#                  given a stale `unprotected` state, to model the branch
#                  becoming protected while the prompt was open.
_stub_gh() {
  cat > "$BIN/gh" <<STUB
#!/usr/bin/env bash
echo "\$*" >> "$GHLOG"
case "\$*" in
  *"-X PUT"*) exit 0 ;;                       # the write; recorded above
  *protection*)
    case "$1" in
      protected)   echo '$PROTECTED_BODY'; exit 0 ;;
      forbidden)   echo '$FORBIDDEN_BODY'; exit 1 ;;
      became-protected) echo '$PROTECTED_BODY'; exit 0 ;;
      *)           echo '$UNPROTECTED_BODY'; exit 1 ;;
    esac ;;
esac
exit 0
STUB
  chmod +x "$BIN/gh"
  : > "$GHLOG"
}

_yaml() {  # a per-user project config, optionally carrying a recorded decline
  local f="$TMP/proj.yaml"
  { echo "name: proj"; echo "main_branch: main"; echo "github:";
    echo "  repo: o/r"; echo "  owner: o";
    [[ "${1:-}" == declined ]] && echo "  branch_protection: declined"; } > "$f"
  echo "$f"
}

_puts() { grep -c -- "-X PUT" "$GHLOG" 2>/dev/null || true; }

# Run the real code under the same `set -euo pipefail` lib/github-bootstrap.sh
# uses, then echo a sentinel, so the test can check the install kept going.
#
# _run calls the full entry point. It checks for a tty with `[[ -t 0 ]]`, and a
# test has none, so _run always takes the non-interactive path.
_run() {  # _run <yaml> ; echoes the function output + SENTINEL
  local yaml="$1"
  ( set -euo pipefail
    export PATH="$BIN:$PATH"
    # shellcheck disable=SC1091
    source "$ADT_DIR/lib/install-helpers.sh"
    # shellcheck disable=SC1091
    source "$ADT_DIR/lib/github-bootstrap.sh"
    _ensure_branch_protection "$yaml" "o/r" "main"
    echo "SENTINEL-REACHED"
  ) < /dev/null 2>&1
}

# _run_prompt takes the path a tty would reach, with stdin supplying the answer.
# It is the only way to test the path that writes to GitHub.
_run_prompt() {  # _run_prompt <yaml> <stdin-source> ; output + SENTINEL
  local yaml="$1" stdin="$2"
  ( set -euo pipefail
    export PATH="$BIN:$PATH"
    # shellcheck disable=SC1091
    source "$ADT_DIR/lib/install-helpers.sh"
    # shellcheck disable=SC1091
    source "$ADT_DIR/lib/github-bootstrap.sh"
    _run_protection_action "$yaml" "o/r" "main" unprotected prompt
    echo "SENTINEL-REACHED"
  ) < "$stdin" 2>&1
}

echo "[test] the state classifier distinguishes all three states"
# shellcheck disable=SC1091
source "$ADT_DIR/lib/install-helpers.sh"
s_prot="$(adt_protection_state 0 "$PROTECTED_BODY")"
s_unprot="$(adt_protection_state 1 "$UNPROTECTED_BODY")"
s_unknown="$(adt_protection_state 1 "$FORBIDDEN_BODY")"
[[ "$s_prot" == protected ]] && pass "200 + protection JSON -> protected" \
  || fail "200 -> '$s_prot', want protected"
[[ "$s_unprot" == unprotected ]] && pass "404 'Branch not protected' -> unprotected" \
  || fail "404 -> '$s_unprot', want unprotected"
[[ "$s_unknown" == unknown ]] && pass "403 -> unknown (NOT unprotected)" \
  || fail "403 -> '$s_unknown', want unknown"
[[ "$s_prot" != "$s_unprot" && "$s_unprot" != "$s_unknown" && "$s_prot" != "$s_unknown" ]] \
  && pass "the three outcomes differ from each other" \
  || fail "the classifier collapses two or more states together"

echo "[test] the action matrix"
_act() { adt_protection_action "$1" "$2" "$3"; }
[[ "$(_act protected false true)"    == noop ]]          && pass "protected            -> noop"          || fail "protected -> $(_act protected false true)"
[[ "$(_act protected true false)"    == noop ]]          && pass "protected wins over declined/no-tty"   || fail "protected+declined -> $(_act protected true false)"
[[ "$(_act unprotected false true)"  == prompt ]]        && pass "unprotected, tty     -> prompt"        || fail "unprotected/tty -> $(_act unprotected false true)"
[[ "$(_act unprotected false false)" == report-only ]]   && pass "unprotected, no tty  -> report-only"   || fail "unprotected/no-tty -> $(_act unprotected false false)"
[[ "$(_act unprotected true true)"   == skip-declined ]] && pass "unprotected, declined-> skip-declined" || fail "unprotected/declined -> $(_act unprotected true true)"
[[ "$(_act unprotected true false)"  == skip-declined ]] && pass "declined wins over no-tty"             || fail "declined/no-tty -> $(_act unprotected true false)"
[[ "$(_act unknown false true)"      == report-only ]]   && pass "unknown, tty         -> report-only"   || fail "unknown/tty -> $(_act unknown false true)"
[[ "$(_act unknown false false)"     == report-only ]]   && pass "unknown, no tty      -> report-only"   || fail "unknown/no-tty -> $(_act unknown false false)"

echo "[test] non-interactive: reports, writes nothing, install continues"
_stub_gh unprotected
out="$(_run "$(_yaml)")"
[[ "$(_puts)" == 0 ]] && pass "no -X PUT was attempted" || fail "a PUT was attempted: $(grep -- '-X PUT' "$GHLOG")"
grep -q "UNPROTECTED" <<<"$out" && pass "it says the branch is unprotected" || fail "no report in output"
grep -q "SENTINEL-REACHED" <<<"$out" && pass "returns 0 under set -e — the install continues" \
  || fail "aborted under set -e: $out"

echo "[test] a recorded decline is not re-asked, and still writes nothing"
_stub_gh unprotected
out="$(_run "$(_yaml declined)")"
[[ "$(_puts)" == 0 ]] && pass "no -X PUT was attempted" || fail "a PUT was attempted"
grep -q "declined before" <<<"$out" && pass "it says the answer is on record" || fail "no decline notice: $out"
grep -qi "Enable branch protection on" <<<"$out" && fail "it re-asked despite the record" \
  || pass "it did not re-ask"
grep -q "SENTINEL-REACHED" <<<"$out" && pass "install continues" || fail "aborted under set -e"

echo "[test] already protected: no prompt, no API write"
_stub_gh protected
out="$(_run "$(_yaml)")"
[[ "$(_puts)" == 0 ]] && pass "no -X PUT — an existing ruleset is never replaced" || fail "a PUT was attempted"
grep -q "already protected" <<<"$out" && pass "it reports the existing protection" || fail "no report: $out"
grep -qi "Enable branch protection on" <<<"$out" && fail "it prompted on a protected repo" \
  || pass "it did not prompt"

echo "[test] the state could not be read: reports, writes nothing"
_stub_gh forbidden
out="$(_run "$(_yaml)")"
[[ "$(_puts)" == 0 ]] && pass "no -X PUT on an unreadable state" || fail "a PUT was attempted"
grep -q "could not read" <<<"$out" && pass "it says the state is unknown" || fail "no report: $out"

echo "[test] an explicit yes writes the protection"
_stub_gh unprotected
printf 'y\n' > "$TMP/yes"
out="$(_run_prompt "$(_yaml)" "$TMP/yes")"
[[ "$(_puts)" -ge 1 ]] && pass "a -X PUT was attempted on an explicit yes" || fail "no PUT on yes: $out"
grep -q -- "-X PUT repos/o/r/branches/main/protection" "$GHLOG" \
  && pass "the PUT targets the configured repo and branch" \
  || fail "wrong PUT target: $(grep -- '-X PUT' "$GHLOG")"

echo "[test] the payload has the required settings"
payload="$( ( export PATH="$BIN:$PATH"
              source "$ADT_DIR/lib/install-helpers.sh"
              source "$ADT_DIR/lib/github-bootstrap.sh"
              _protection_payload ) )"
grep -q '"allow_force_pushes": false' <<<"$payload" && pass "allow_force_pushes: false" || fail "force-push not disabled"
grep -q '"allow_deletions": false' <<<"$payload"   && pass "allow_deletions: false"   || fail "deletion not disabled"
grep -q '"required_approving_review_count": 0' <<<"$payload" \
  && pass "review count 0 — a solo maintainer can still self-merge" || fail "review count not 0"
grep -q '"required_status_checks": null' <<<"$payload" && pass "no CI required" || fail "status checks not null"

echo "[test] an explicit no at the prompt is recorded, and writes nothing"
_stub_gh unprotected
printf 'n\n' > "$TMP/no"
yaml_n="$(_yaml)"
out="$(_run_prompt "$yaml_n" "$TMP/no")"
[[ "$(_puts)" == 0 ]] && pass "no -X PUT on an explicit no" || fail "a PUT was attempted"
grep -q 'branch_protection: declined' "$yaml_n" \
  && pass "the decline is recorded in the project config" \
  || fail "decline not recorded: $(cat "$yaml_n")"

echo "[test] branch became protected during the prompt -> no overwrite"
_stub_gh became-protected
printf 'y\n' > "$TMP/yes"
out="$(_run_prompt "$(_yaml)" "$TMP/yes")"
[[ "$(_puts)" == 0 ]] \
  && pass "no -X PUT — a ruleset added during the prompt is not replaced" \
  || fail "it overwrote protection added during the prompt: $(grep -- '-X PUT' "$GHLOG")"
grep -q "became protected while you were deciding" <<<"$out" \
  && pass "it says why it backed off" || fail "no explanation: $out"

echo "[test] interactive prompt, then EOF: treated as no, nothing recorded"
_stub_gh unprotected
: > "$TMP/empty"
yaml="$(_yaml)"
out="$(_run_prompt "$yaml" "$TMP/empty")"
[[ "$(_puts)" == 0 ]] && pass "no -X PUT after an unanswered prompt" || fail "a PUT was attempted"
grep -q "SENTINEL-REACHED" <<<"$out" \
  && pass "adt_ask's non-zero EOF return did not abort under set -e" \
  || fail "aborted under set -e on EOF: $out"
grep -q "branch_protection" "$yaml" \
  && fail "an unanswered prompt was recorded as a decline" \
  || pass "nothing recorded — nobody actually answered"

echo
if [[ $fails -eq 0 ]]; then
  echo "test_branch_protection: PASS"
else
  echo "test_branch_protection: FAIL ($fails)" >&2
  exit 1
fi

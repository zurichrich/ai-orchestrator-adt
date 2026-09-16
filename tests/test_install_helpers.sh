#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests the helper functions in lib/install-helpers.sh one at a time.
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }
eq()   { [[ "$2" == "$3" ]] && pass "$1" || fail "$1 (got '$2', want '$3')"; }

# shellcheck disable=SC1091
source "$ADT_DIR/lib/install-helpers.sh"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# ── 1. adt_ask: answered blank vs never answered ────────────────────────────
echo "[test] adt_ask distinguishes a supplied blank line from EOF"

# A supplied blank line IS an answer: take the default, exit 0.
got="$(printf '\n' | adt_ask 'Project name' 'proj' false 2>/dev/null)"
eq "blank line supplied -> default" "$got" "proj"

# A supplied value wins over the default.
got="$(printf 'other\n' | adt_ask 'Project name' 'proj' false 2>/dev/null)"
eq "supplied value -> that value" "$got" "other"

# ── 1b. adt_ask times out on a stdin that is open but silent ────────────────
echo "[test] adt_ask is bounded when stdin is a silent non-tty"

# A pipe that stays open with nothing on it: `sleep` holds the write end, so the
# reader sees neither data nor EOF.
silent_pipe_ask() {  # $1 = assume_yes ; echoes the answer, or times out
  ADT_ASK_TIMEOUT=2 adt_ask 'Project name' 'proj' "$1" 2>/dev/null < <(sleep 30)
}

start=$SECONDS
got="$(silent_pipe_ask true)"; rc=$?
elapsed=$(( SECONDS - start ))
eq "silent stdin + --yes -> the default" "$got" "proj"
[[ $elapsed -lt 10 ]] \
  && pass "silent stdin returns in ${elapsed}s" \
  || fail "silent stdin took ${elapsed}s (expected under 10s)"

start=$SECONDS
silent_pipe_ask false >/dev/null 2>&1; rc=$?
elapsed=$(( SECONDS - start ))
[[ $rc -ne 0 && $elapsed -lt 10 ]] \
  && pass "silent stdin without --yes refuses (rc=$rc) instead of hanging" \
  || fail "silent stdin without --yes: rc=$rc after ${elapsed}s"

# A piped answer still wins when a timeout is set.
got="$(ADT_ASK_TIMEOUT=2 adt_ask 'Project name' 'proj' false 2>/dev/null <<< 'other')"
eq "piped answer still wins with a timeout set" "$got" "other"

# ── 1c. adt_ask works on a terminal under set -u (bash 3.2) ─────────────────
# On a tty adt_ask expands an empty array, which bash 3.2 rejects under set -u.
# A pty is needed because `[[ -t 0 ]]` is the branch under test.
echo "[test] adt_ask works on a real tty under set -u (bash 3.2 empty array)"

# `script -q /dev/null CMD` is the macOS form of script(1); util-linux differs,
# so this runs on macOS only.
pty_ask() {  # $1 = what to type ; echoes the pty transcript (prompt + RESULT)
  { printf '%s\n' "$1"; sleep 1; } | script -q /dev/null /bin/bash -c \
    'set -euo pipefail; source "$0"/lib/install-helpers.sh
     a=$(adt_ask "Project name" "proj" false); printf "RESULT=%s\n" "$a"' \
    "$ADT_DIR" 2>&1 | tr -d '\r'
}

if [[ "$(uname)" != Darwin ]] || ! command -v script >/dev/null 2>&1; then
  echo "  SKIP tty test: needs macOS script(1) for a pty"
else
  # `read -p` prints the prompt without a newline, so RESULT= shares its line.
  out="$(pty_ask 'mine')"
  grep -q 'unbound variable' <<<"$out" \
    && fail "tty prompt dies under set -u with 'unbound variable'" \
    || pass "tty prompt survives set -u"
  eq "tty: typed answer wins" "$(sed -n "s/.*RESULT=//p" <<<"$out")" "mine"

  out="$(pty_ask '')"
  eq "tty: blank line -> default" "$(sed -n "s/.*RESULT=//p" <<<"$out")" "proj"
fi

# EOF with no --yes: refuse.
out="$(adt_ask 'Project name' 'proj' false </dev/null 2>&1)"; rc=$?
[[ $rc -ne 0 ]] && pass "EOF without --yes refuses (rc=$rc)" \
                || fail "EOF without --yes returned 0 (expected non-zero)"
grep -q -- "--yes" <<<"$out" && pass "refusal names the --yes remedy" \
                             || fail "refusal does not name the remedy"

# EOF with --yes: take the default and exit 2 to say nothing was answered. The
# installer calls adt_ask through `$(...)`, so the check uses a subshell too.
got="$(adt_ask 'Project name' 'proj' true </dev/null 2>/dev/null)"; rc=$?
eq "EOF with --yes -> default" "$got" "proj"
eq "EOF with --yes signals 'defaulted' THROUGH a subshell" "$rc" "2"

got="$(printf '\n' | adt_ask 'Project name' 'proj' true 2>/dev/null)"; rc=$?
eq "an answered blank line is NOT flagged as defaulted" "$rc" "0"

got="$(printf 'x\n' | adt_ask 'Project name' 'proj' true 2>/dev/null)"; rc=$?
eq "a supplied value is not flagged as defaulted" "$rc" "0"

# ── 2. adt_derive_id_prefix ────────────────────────────────────────────────
echo "[test] adt_derive_id_prefix reads the prefix out of commit scopes"
R="$TMP/repo"; mkdir -p "$R"
git -C "$R" init -q; git -C "$R" config user.email t@t.t; git -C "$R" config user.name t
for m in "fix(ADT-1): a" "feat(ADT-2): b" "chore(ADT-3): c" "fix(TIX-9): d" "docs: no scope"; do
  git -C "$R" commit -q --allow-empty -m "$m"
done
eq "most frequent scope wins" "$(adt_derive_id_prefix "$R")" "ADT"

R2="$TMP/repo2"; mkdir -p "$R2"
git -C "$R2" init -q; git -C "$R2" config user.email t@t.t; git -C "$R2" config user.name t
git -C "$R2" commit -q --allow-empty -m "initial commit"
eq "no scoped commits -> empty (caller keeps its default)" "$(adt_derive_id_prefix "$R2")" ""

# ── 3. adt_prefix_conflict ─────────────────────────────────────────────────
echo "[test] adt_prefix_conflict"
eq "derived == chosen -> ok"            "$(adt_prefix_conflict ADT ADT false)" "ok"
eq "derived != chosen -> stop"          "$(adt_prefix_conflict ADT TIX false)" "stop"
eq "disagreement + --prefix -> ok"      "$(adt_prefix_conflict ADT TIX true)"  "ok"
eq "nothing derived -> ok (no false stop)" "$(adt_prefix_conflict '' TIX false)" "ok"

# ── 4. adt_scopes_have_project ─────────────────────────────────────────────
echo "[test] adt_scopes_have_project distinguishes project from read:project"
have() { adt_scopes_have_project "$1" && echo yes || echo no; }
eq "read:project ONLY is rejected" \
   "$(have "  - Token scopes: 'gist', 'read:org', 'read:project', 'repo', 'workflow'")" "no"
eq "project is accepted" \
   "$(have "  - Token scopes: 'gist', 'project', 'read:org', 'repo', 'workflow'")" "yes"
eq "unquoted scope list is accepted" "$(have "  - Token scopes: gist, project, repo")" "yes"
eq "read:project alone is rejected"  "$(have "  - Token scopes: 'read:project'")" "no"
eq "empty scope line is rejected"    "$(have "")" "no"
eq "both scopes held -> accepted" \
   "$(have "  - Token scopes: 'project', 'read:project'")" "yes"

# ── 5. adt_scope_action ────────────────────────────────────────────────────
echo "[test] adt_scope_action over all four combinations"
eq "scope held, interactive        -> ok"      "$(adt_scope_action true  true)"  "ok"
eq "scope held, non-interactive    -> ok"      "$(adt_scope_action true  false)" "ok"
eq "scope missing, interactive     -> refresh" "$(adt_scope_action false true)"  "refresh"
eq "scope missing, non-interactive -> abort"   "$(adt_scope_action false false)" "abort"

echo
if [[ $FAILS -eq 0 ]]; then printf '\033[32mall install-helper tests passed\033[0m\n'; else
  printf '\033[31m%s test(s) failed\033[0m\n' "$FAILS"; fi
exit $(( FAILS > 0 ))

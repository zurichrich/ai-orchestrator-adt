#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# End-to-end test of the adt-install.sh interview: runs the real installer
# and checks its prompts, refusals, prefix detection and token-scope check.
#
# The fake $HOME has no .claude/commands, as on a fresh machine. Do not create
# it here; the install must work without it.
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PROJ="$TMP/proj"; mkdir -p "$PROJ"
git -C "$PROJ" init -q
git -C "$PROJ" config user.email t@t.t; git -C "$PROJ" config user.name t
for m in "fix(ADT-1): a" "feat(ADT-2): b" "chore(ADT-3): c" "fix(TIX-9): d"; do
  git -C "$PROJ" commit -q --allow-empty -m "$m"
done
USERP="$TMP/userp"; mkdir -p "$USERP"
BIN="$TMP/bin"; mkdir -p "$BIN"

_stub_gh() {  # _stub_gh <token-scopes-line>
  cat > "$BIN/gh" <<EOF
#!/usr/bin/env bash
case "\$* " in
  *"auth status"*) echo "  - Token scopes: $1"; exit 0 ;;
  *"repo view"*"nameWithOwner"*) echo "me/proj"; exit 0 ;;
  *) exit 0 ;;
esac
EOF
  chmod +x "$BIN/gh"
}
run() { ( cd "$PROJ" && PATH="$BIN:$PATH" HOME="$TMP/home" \
          ADT_USER_PROJECTS="$USERP" bash "$ADT_DIR/adt-install.sh" "$@" ) 2>&1; }
CFG="$USERP/proj.yaml"
_stub_gh "'project'"

# Prompt order: name, repo, main_branch, prefix, proceed.
echo "[test] the install runs clean (no shell errors leaking into output)"
out="$(printf '\n\n\n\ny\n' | run --no-github)"
grep -q "command not found" <<<"$out" \
  && fail "shell error in install output: $(grep -m1 'command not found' <<<"$out")" \
  || pass "no 'command not found' in output"
[[ -f "$CFG" ]] && pass "config written" || fail "config NOT written"

echo "[test] prefix is derived from the repo's own commit history"
grep -q '^  id_prefix: ADT$' "$CFG" \
  && pass "id_prefix derived as ADT, not the TIX constant" \
  || fail "id_prefix wrong: $(grep '^  id_prefix:' "$CFG" || echo none)"

echo "[test] an unanswered run refuses"
out="$(run --no-github </dev/null)"; rc=$?
[[ $rc -ne 0 ]] && pass "refused (rc=$rc)" || fail "proceeded on a totally unanswered run"
grep -q -- "--yes" <<<"$out" && pass "names the --yes remedy" || fail "no remedy printed"
grep -q "Will configure" <<<"$out" \
  && fail "printed a 'Will configure' block for a run nobody answered" \
  || pass "no confirmed-looking block on a refused run"

echo "[test] --yes proceeds but labels the run as defaults"
rm -f "$CFG"
out="$(run --no-github --yes </dev/null)"
grep -q "non-interactive: every value below is a DEFAULT" <<<"$out" \
  && pass "defaults-only run is visibly labelled" \
  || fail "banner did not fire — a defaults run looks answered"
[[ -f "$CFG" ]] && pass "--yes still completes the install" || fail "--yes did not install"

echo "[test] a prefix disagreeing with history stops the install"
rm -f "$CFG"
out="$(printf '\n\n\nTIX\ny\n' | run --no-github)"; rc=$?
[[ $rc -ne 0 ]] && pass "stopped (rc=$rc)" || fail "proceeded with a mismatched prefix"
grep -q "mismatch" <<<"$out" && pass "says why" || fail "no mismatch message"
grep -q -- "--prefix TIX" <<<"$out" && pass "names the override" || fail "no override named"
[[ ! -f "$CFG" ]] && pass "no config written on the stop" || fail "config written despite the stop"

echo "[test] --prefix overrides the history check"
out="$(printf '\n\n\ny\n' | run --no-github --prefix TIX)"; rc=$?
[[ $rc -eq 0 ]] && pass "override proceeds" || fail "override still stopped (rc=$rc)"
grep -q '^  id_prefix: TIX$' "$CFG" \
  && pass "override value is what lands in the config" \
  || fail "override value not applied: $(grep '^  id_prefix:' "$CFG" || echo none)"

echo "[test] a read:project-only token is refused"
_stub_gh "'gist', 'read:org', 'read:project', 'repo'"
out="$(printf '\n\n\nADT\ny\n' | run)"; rc=$?
[[ $rc -ne 0 ]] && pass "refused a read-only token (rc=$rc)" \
                || fail "a read:project token passed the write-scope check"
grep -q "missing the 'project' scope" <<<"$out" && pass "says which scope" || fail "no scope message"
grep -qi "authenticated with the 'project' scope" <<<"$out" \
  && fail "claimed the scope was present" || pass "did not claim a scope it lacks"

echo "[test] a full 'project' token passes the gate"
_stub_gh "'gist', 'project', 'repo'"
out="$(printf '\n\n\nADT\ny\n' | run --no-github)"
grep -q "mismatch" <<<"$out" && fail "unexpected mismatch" || pass "clean run with a valid token"

echo
if [[ $FAILS -eq 0 ]]; then printf '\033[32mall install-interview tests passed\033[0m\n'; else
  printf '\033[31m%s test(s) failed\033[0m\n' "$FAILS"; fi
exit $(( FAILS > 0 ))

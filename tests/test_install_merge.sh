#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Checks that adt-install.sh merges its core fields into an existing per-user
# config instead of overwriting it:
#   1. Hand-written keys survive, and the core fields install owns are refreshed.
#   2. The ticket prefix comes from the existing config, not the first three
#      letters of the project name.
#   3. commands_doc_src is the absolute path $ADT_DIR/docs/references.md.
#   4. A second run neither duplicates nor drops keys.
#   5. After an uninstall moves the config to .bak, a reinstall merges from .bak.
#   6. worktree_guard_paths, set in the project config, reaches the generated
#      .adt/config.yaml on every install (it is rewritten each time) and through
#      an uninstall round-trip, and the installed worktree guard acts on it.
# Runs offline, with --no-github and a stub gh.
set -euo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# A throwaway git project install will run against.
PROJ="$TMP/proj"
mkdir -p "$PROJ"
git -C "$PROJ" init -q
git -C "$PROJ" config user.email t@t.t
git -C "$PROJ" config user.name t
git -C "$PROJ" commit -q --allow-empty -m init

# Stub gh so preflight and repo detection pass offline. `gh repo view --json
# nameWithOwner` returns the project repo.
BIN="$TMP/bin"; mkdir -p "$BIN"
cat > "$BIN/gh" <<'SH'
#!/usr/bin/env bash
case "$* " in
  *"auth status"*) echo "Token scopes: 'project'"; exit 0 ;;
  *"repo view"*"nameWithOwner"*) echo "me/proj"; exit 0 ;;
  *) exit 0 ;;
esac
SH
chmod +x "$BIN/gh"

USERP="$TMP/userp"; mkdir -p "$USERP"

# An existing config with core fields plus hand-written keys install does not write.
cat > "$USERP/proj.yaml" <<EOF
# my own top comment
name: proj
path: /stale/old/path
main_branch: main
remote: origin
claude_md: CLAUDE.md
cache_dir: /stale/cache
decision_log: docs/decisions.md
merger: me
custom_field: preserve-me
test_commands:
  backend: pytest -v
github:
  repo: me/proj
  owner: me
  board_title: "proj backlog"
kanban:
  id_prefix: TIX
  board_out: /stale/cache/kanban.html
  stage_tools:
    ideas: "/adt-brief"
security:
  rls_check_required: true
notifications:
  email:
    to: me@example.com
deploy:
  mode: auto
sync_docs:
  - PROGRESS.md
worktree_guard_paths: "src/* tools/*.py"
EOF
mkdir -p "$PROJ/src" && echo x > "$PROJ/src/a.py"

run_install() {  # run_install <answers-via-defaults>
  # Blank lines accept every prompt's default. --no-github skips the board and watcher.
  ( cd "$PROJ" && printf '\n\n\n\n\n\ny\n' | \
    PATH="$BIN:$PATH" HOME="$TMP/home" ADT_USER_PROJECTS="$USERP" \
    bash "$ADT_DIR/adt-install.sh" --no-github ) >/dev/null 2>&1
}

CFG="$USERP/proj.yaml"
GEN="$PROJ/.adt/config.yaml"
WGP_LINE='worktree_guard_paths: "src/* tools/*.py"'
# guard_denies — the INSTALLED hook denies an Edit to src/a.py in this checkout
guard_denies() {
  python3 -c 'import json,sys; print(json.dumps({"tool_name":"Edit","tool_input":{"file_path":sys.argv[1]},"cwd":sys.argv[2]}))' \
    "$PROJ/src/a.py" "$PROJ" | bash "$PROJ/.claude/hooks/adt-worktree-guard.sh" 2>/dev/null | grep -q '"deny"'
}

# ── Run 1: merge onto the rich config ──────────────────────────────────────
echo "[test] install merges onto a rich config"
run_install
g() { yq -r "$1 // \"\"" "$CFG"; }

[[ "$(g '.custom_field')" == "preserve-me" ]]      && pass "custom field preserved"      || fail "custom field LOST"
[[ "$(g '.test_commands.backend')" == "pytest -v" ]]  && pass "test_commands preserved"      || fail "test_commands LOST"
[[ "$(g '.security.rls_check_required')" == "true" ]] && pass "security preserved"           || fail "security LOST"
[[ "$(g '.notifications.email.to')" == "me@example.com" ]] && pass "notifications preserved" || fail "notifications LOST"
[[ "$(g '.deploy.mode')" == "auto" ]]                 && pass "deploy preserved"             || fail "deploy LOST"
[[ "$(g '.sync_docs[0]')" == "PROGRESS.md" ]]         && pass "sync_docs preserved"          || fail "sync_docs LOST"
[[ "$(g '.kanban.stage_tools.ideas')" == "/adt-brief" ]] && pass "stage_tools preserved" || fail "stage_tools LOST"
[[ "$(g '.path')" == "$PROJ" ]]                       && pass "path refreshed to checkout"   || fail "path NOT refreshed ($(g '.path'))"
# name[:3] would give 'PRO'.
[[ "$(g '.kanban.id_prefix')" == "TIX" ]]             && pass "prefix from config (not name[:3])" || fail "prefix wrong: $(g '.kanban.id_prefix')"
[[ "$(g '.kanban.commands_doc_src')" == "$ADT_DIR/docs/references.md" ]] && pass "commands_doc_src absolute" || fail "commands_doc_src wrong: $(g '.kanban.commands_doc_src')"
[[ "$(g '.worktree_guard_paths')" == "src/* tools/*.py" ]] && pass "worktree_guard_paths preserved in the project config" || fail "worktree_guard_paths LOST from the project config"
grep -qxF "$WGP_LINE" "$GEN" && pass "worktree_guard_paths written to .adt/config.yaml" || fail "worktree_guard_paths missing from $GEN"
guard_denies && pass "the installed worktree guard denies a listed path" || fail "the installed worktree guard did not deny src/a.py"

# ── Run 2: idempotent ───────────────────────────────────────────────────────
echo "[test] second run is idempotent"
run_install
[[ "$(g '.custom_field')" == "preserve-me" ]]      && pass "custom field still present"   || fail "custom field lost on re-run"
[[ "$(yq -r '[.kanban.id_prefix] | length' "$CFG")" == "1" ]] && pass "no key duplication"   || fail "keys duplicated"
[[ "$(grep -c '^worktree_guard_paths:' "$GEN")" == "1" ]] && pass "worktree_guard_paths written once on re-run" || fail "worktree_guard_paths not written exactly once on re-run"

# ── Run 3: uninstall→reinstall round-trip via .bak ──────────────────────────
echo "[test] uninstall backs up; reinstall merges from .bak"
# Do what lib/uninstall.sh does to the config: move it to .bak.
mv -f "$CFG" "$CFG.bak"
[[ ! -f "$CFG" && -f "$CFG.bak" ]] && pass "config backed up to .bak" || fail "no .bak"
run_install
[[ -f "$CFG" ]]                                       && pass "reinstall recreated live config" || fail "live config not recreated"
[[ "$(g '.custom_field')" == "preserve-me" ]]      && pass "rich field recovered from .bak" || fail "rich field NOT recovered"
[[ ! -f "$CFG.bak" ]]                                 && pass ".bak promoted + removed"          || fail ".bak left behind"
grep -qxF "$WGP_LINE" "$GEN" && guard_denies && pass "worktree_guard_paths survives the uninstall round-trip" \
  || fail "worktree_guard_paths lost across uninstall and reinstall"

# ── Run 4: removing the key turns the guard off at the next install ─────────
echo "[test] removing worktree_guard_paths turns the guard off"
yq -i 'del(.worktree_guard_paths)' "$CFG"
run_install
! grep -q '^worktree_guard_paths:' "$GEN" && ! guard_denies && pass "no key: nothing written, the guard allows" \
  || fail "the guard still acts after the key was removed"

echo
if [[ "$FAILS" -eq 0 ]]; then echo "All install-merge tests passed."; else echo "$FAILS assertion(s) FAILED."; exit 1; fi

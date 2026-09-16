#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests lib/uninstall.sh and the install side it reverses. Uninstall removes
# what ADT installed (symlinks or manifest copies, hook entries, env keys,
# configs, .adt/) and keeps the ticket cache, the project's own files, and any
# copy the user edited. The token ledger is archived before it is deleted, and
# kept if the archive fails.
# Runs offline with a stub gh.
set -euo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ── Shared fixture builder ─────────────────────────────────────────────────
build_fixture() {  # build_fixture <dir> <gh_create_exit>
  local d="$1" gh_create_exit="$2"
  local adt="$d/adt" proj="$d/proj" userp="$d/userp" bin="$d/bin" cache="$d/cache"
  mkdir -p "$adt/defaults/rules" "$adt/defaults/hooks" \
           "$proj/.claude/rules" "$proj/.claude/hooks" "$proj/.adt" \
           "$proj/.adt/state/token-cursor" \
           "$proj/.adt/retros" "$userp" "$bin" "$cache"
  cp "$ADT_DIR/lib/uninstall.sh" "$adt/uninstall.sh"
  # uninstall.sh sources these from its own directory. No watcher is
  # installed, so uninstall_watcher does nothing, and the project has no origin,
  # so adt-layer.sh reports no committed layer (ADT-384).
  cp "$ADT_DIR/lib/watcher.sh" "$adt/watcher.sh"
  cp "$ADT_DIR/lib/adt-layer.sh" "$adt/adt-layer.sh"
  cp "$ADT_DIR/lib/commands-marker.sh" "$adt/commands-marker.sh"
  echo "rule"     > "$adt/defaults/rules/working-style.md"
  echo "#!/bin/sh" > "$adt/defaults/hooks/adt-done-guard.sh"

  ln -s "$adt/defaults/rules/working-style.md" "$proj/.claude/rules/working-style.md"
  echo "MY OWN RULE" > "$proj/.claude/rules/project-rule.md"
  ln -s "$adt/defaults/hooks/adt-done-guard.sh" "$proj/.claude/hooks/adt-done-guard.sh"

  # Uninstall recognises an ADT symlink by a "defaults/<sub>/" segment in its
  # link text, wherever it points.
  #   (i) a link into a different ADT checkout
  local other_adt="$d/other-adt"
  mkdir -p "$other_adt/defaults/hooks"
  echo "#!/bin/sh" > "$other_adt/defaults/hooks/x.sh"
  ln -s "$other_adt/defaults/hooks/x.sh" "$proj/.claude/hooks/external-clone.sh"
  #   (ii) a dangling ADT link whose target does not exist
  ln -s "../../../gone-adt/defaults/hooks/y.sh" "$proj/.claude/hooks/dangling.sh"
  #   (iii) a non-ADT link, with no "defaults/<sub>/" segment, which must be kept
  echo "not adt" > "$proj/.claude/hooks/not-adt-target.sh"
  ln -s "$proj/.claude/hooks/not-adt-target.sh" "$proj/.claude/hooks/not-adt-link.sh"

  cat > "$proj/.claude/settings.json" <<'JSON'
{ "permissions": {"allow": ["Bash(git status)"]},
  "hooks": { "PreToolUse": [ {"matcher":"Bash","hooks":[
    {"type":"command","command":"${CLAUDE_PROJECT_DIR}/.claude/hooks/adt-done-guard.sh"},
    {"type":"command","command":"/usr/local/bin/my-own-hook.sh"} ]} ] } }
JSON

  printf '2026-06-25T10:00:00\tADT-1\t100\t200\tsess-a\n' \
    > "$proj/.adt/state/token-usage.log"
  echo 1 > "$proj/.adt/state/token-cursor/sess-a"
  echo "# decisions" > "$proj/.adt/decisions.md"
  echo "# backlog"   > "$proj/.adt/BACKLOG.md"
  echo "ticket"      > "$cache/ticket.md"
  printf 'repo: me/myproj-backlog\nowner: me\n' > "$proj/.adt/config.yaml"
  printf 'name: myproj\npath: %s\ngithub:\n  repo: me/myproj-backlog\ncache_dir: %s\ncustom_field: preserve-me\n' \
    "$proj" "$cache" > "$userp/myproj.yaml"

  cat > "$bin/gh" <<SH
#!/usr/bin/env bash
case "\$1 \$2" in
  "issue list") echo "" ;;
  "issue create") exit $gh_create_exit ;;
  "issue view") echo "" ;;
  "issue edit") exit $gh_create_exit ;;
esac
exit 0
SH
  chmod +x "$bin/gh"
}

run_uninstall() {  # run_uninstall <dir>
  local d="$1"
  # A sandbox HOME so watcher removal cannot touch the real ~/Library/LaunchAgents.
  mkdir -p "$d/home"
  PATH="$d/bin:$PATH" ADT_USER_PROJECTS="$d/userp" HOME="$d/home" \
    /usr/bin/env bash -c \
    'source "'"$d"'/adt/uninstall.sh"; uninstall_project "'"$d"'/adt" myproj "'"$d"'/proj"' \
    >/dev/null 2>&1
}

# ── Test 1: uninstall of an older symlink install ─────────────────────────────
# The fixture has symlinks and no .adt-manifest.json, so uninstall uses its
# symlink removal path. Test 5 covers the copy + manifest install.
echo "[test] uninstall happy path (symlink install, no manifest)"
D1="$TMP/happy"; build_fixture "$D1" 0; run_uninstall "$D1"
P="$D1/proj"
[[ ! -d "$P/.adt" ]]                            && pass ".adt/ removed entirely" || fail ".adt/ kept"
[[ ! -e "$P/.claude/rules/working-style.md" ]]             && pass "ADT rule symlink removed" || fail "ADT symlink kept"
[[ -f "$P/.claude/rules/project-rule.md" ]]                && pass "authored rule KEPT" || fail "authored file deleted!"
[[ ! -e "$P/.claude/hooks/adt-done-guard.sh" ]]                && pass "ADT hook symlink removed" || fail "ADT hook symlink kept"
# `-L` tests the link itself; `-e` is false for a dangling link even when it exists.
[[ ! -L "$P/.claude/hooks/external-clone.sh" ]]            && pass "external-checkout ADT symlink removed" || fail "external-checkout symlink kept"
[[ ! -L "$P/.claude/hooks/dangling.sh" ]]                  && pass "dangling ADT symlink removed" || fail "dangling symlink kept"
[[ -L "$P/.claude/hooks/not-adt-link.sh" ]]                && pass "non-ADT symlink KEPT" || fail "non-ADT symlink wrongly removed"
[[ ! -f "$P/.adt/config.yaml" ]]                            && pass "project config removed" || fail "project config kept"
# The per-user config is moved to .bak, not deleted, so a reinstall can merge it.
[[ ! -f "$D1/userp/myproj.yaml" ]]                          && pass "live user config moved aside" || fail "live user config kept"
[[ -f "$D1/userp/myproj.yaml.bak" ]]                        && pass "user config backed up to .bak" || fail "user config NOT backed up!"
grep -q 'custom_field: preserve-me' "$D1/userp/myproj.yaml.bak" 2>/dev/null \
  && pass "hand-authored field survives in .bak" || fail "authored field lost from .bak"
[[ -f "$D1/cache/ticket.md" ]]                              && pass "DURABLE ticket cache KEPT" || fail "cache deleted!"
python3 -c "
import json,sys
s=json.load(open('$P/.claude/settings.json'))
cmds=[h['command'] for b in s.get('hooks',{}).get('PreToolUse',[]) for h in b.get('hooks',[])]
sys.exit(0 if (not any('done-guard' in c for c in cmds)
               and any('my-own-hook' in c for c in cmds)
               and s['permissions']['allow']==['Bash(git status)']) else 1)
" && pass "settings.json: ADT hook un-merged, own hook+perms kept" || fail "settings.json merge wrong"

# ── Test 2: archive failure → folder + ledger preserved ────────────────────
echo "[test] archive-failure safety"
# On a public or unknown-visibility repo the archive is a local gzip under
# docs/history/telemetry/ instead of an Issue. Either way the ledger must be
# still in place or in that archive.
D2="$TMP/fail"; build_fixture "$D2" 1; run_uninstall "$D2"
if [[ -f "$D2/proj/.adt/state/token-usage.log" ]]; then
  pass "ledger KEPT in place when the archive could not complete"
elif compgen -G "$D2/proj/docs/history/telemetry/*.gz" >/dev/null; then
  pass "ledger retrievable from the local gzip archive (unknown visibility → never post)"
else
  fail "ledger is gone: neither in place nor archived"
fi
# .adt/ is removed only once its telemetry is archived.
if [[ -d "$D2/proj/development-team" ]]; then
  pass ".adt/ KEPT while it still holds unarchived data"
else
  compgen -G "$D2/proj/docs/history/telemetry/*.gz" >/dev/null \
    && pass ".adt/ removed only after its telemetry was archived" \
    || fail ".adt/ removed with nothing archived"
fi

# ── Test 2b: the uninstall event ─────────────────────────────────────────────
# Uninstall sends one last telemetry report while .adt/state/install-id still
# exists. The egress probe records the request and refuses it, so nothing leaves
# the machine; the capture file is this test's own, so the shell runner's stays
# empty. DO_NOT_TRACK is removed for the run because telemetry has to be on.
echo "[test] uninstall event"
# An empty DO_NOT_TRACK counts as telemetry on.
run_uninstall_telemetry() {  # run_uninstall_telemetry <dir>
  DO_NOT_TRACK= PYTHONPATH="$ADT_DIR/tests/egress_probe" ADT_EGRESS_CAPTURE="$1/capture" \
    run_uninstall "$1"
}
telemetry_fixture() {  # telemetry_fixture <dir>
  local d="$1"
  build_fixture "$d" 0
  mkdir -p "$d/adt/tools"
  cp "$ADT_DIR/tools/adt_phone_home.py" "$d/adt/tools/"
  echo "0.1.0" > "$d/adt/VERSION"
  echo "3f2504e0-4f89-11d3-9a0c-0305e82c3301" > "$d/proj/.adt/state/install-id"
}
captured() {  # captured <dir>: how many requests the probe refused
  if [[ -f "$1/capture" ]]; then grep -c "adt-telemetry.zurichrich.workers.dev" "$1/capture" || true; else echo 0; fi
}

D2E="$TMP/event"; telemetry_fixture "$D2E"; run_uninstall_telemetry "$D2E"
[[ "$(captured "$D2E")" == 1 && ! -d "$D2E/proj/.adt" ]] \
  && pass "uninstall sends one uninstall event before .adt/ is removed" \
  || fail "uninstall sent $(captured "$D2E") event(s); .adt/ present: $([[ -d "$D2E/proj/.adt" ]] && echo yes || echo no)"

D2O="$TMP/event-off"; telemetry_fixture "$D2O"
touch "$D2O/proj/.adt/state/telemetry-off"; run_uninstall_telemetry "$D2O"
[[ "$(captured "$D2O")" == 0 ]] \
  && pass "uninstall sends no event when telemetry is off" \
  || fail "uninstall sent $(captured "$D2O") event(s) with telemetry off"

# ── Test 3: CLAUDE.md @-import block ─────────────────────────────────────────
# Install writes one ADT:rules @-import block into CLAUDE.md, even when run twice.
# Uninstall removes it and leaves the file as it was.
echo "[test] CLAUDE.md @-import block"
D3="$TMP/rules-import"
mkdir -p "$D3/proj"
# The real defaults/rules, where both rules have no paths: key.
RULES="$ADT_DIR/defaults/rules"
ORIG="$D3/proj/CLAUDE.md"
printf '# My Project\n\nAuthored content the project owns.\n' > "$ORIG"
cp "$ORIG" "$D3/orig.md"

# install writer (run twice — must be idempotent)
bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$D3/proj" >/dev/null 2>&1
bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$D3/proj" >/dev/null 2>&1

grep -q '<!-- ADT:rules:start -->' "$ORIG" \
  && pass "CLAUDE.md gained the ADT:rules block" || fail "no ADT:rules block written"
[[ "$(grep -c '<!-- ADT:rules:start -->' "$ORIG")" -eq 1 ]] \
  && pass "block written once (idempotent re-install)" || fail "duplicate ADT:rules block"
grep -q '^@.claude/rules/working-style.md$' "$ORIG" \
  && pass "@-imports working-style.md" || fail "working-style.md not imported"
grep -q '^@.claude/rules/multi-agent-git-workflow.md$' "$ORIG" \
  && pass "@-imports multi-agent-git-workflow.md" || fail "multi-agent rule not imported"
# every imported rule is an always-on one (no paths: key)
import_ok=1
while IFS= read -r r; do
  f="$RULES/${r#@.claude/rules/}"
  [[ -f "$f" ]] && ! grep -qE '^paths:' "$f" || import_ok=0
done < <(grep -oE '^@.claude/rules/[^ ]+' "$ORIG")
[[ "$import_ok" -eq 1 ]] && pass "only always-on rules imported" || fail "a path-scoped rule was imported"

# uninstall remover → lossless roundtrip
( source "$ADT_DIR/lib/uninstall.sh"; _remove_rules_import "$ORIG" ) >/dev/null 2>&1
! grep -q '<!-- ADT:rules' "$ORIG" \
  && pass "uninstall removed the ADT:rules block" || fail "block survived uninstall"
diff -q "$D3/orig.md" "$ORIG" >/dev/null \
  && pass "install→uninstall is lossless (canonical CLAUDE.md)" || fail "roundtrip not lossless"

# no-op when CLAUDE.md is absent (writer must not author one)
D3b="$TMP/no-claude-md"; mkdir -p "$D3b/proj"
bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$D3b/proj" >/dev/null 2>&1
[[ ! -f "$D3b/proj/CLAUDE.md" ]] \
  && pass "no CLAUDE.md authored when project has none" || fail "writer created a CLAUDE.md"

# ── Test 4: commands install per project as marked copies ─────────────────────
# Install copies commands into <project>/.claude/commands/ with an adt_managed
# marker. Uninstall removes the marked copies and keeps the project's own
# adt-*.md. Setup's migration removes old global ~/.claude/commands links.
echo "[test] commands per-project install/uninstall"
D4="$TMP/commands"; mkdir -p "$D4/proj/.claude/commands"
# a project-authored adt-*.md (own command, NO marker) that must survive
printf -- '---\nname: adt-mine\n---\n# project owns this\nSENTINEL4\n' \
  > "$D4/proj/.claude/commands/adt-mine.md"

bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$D4/proj" >/dev/null 2>&1

# install: real-file copy, not a symlink, carrying the marker
[[ -f "$D4/proj/.claude/commands/adt-plan.md" && ! -L "$D4/proj/.claude/commands/adt-plan.md" ]] \
  && pass "command copied as a real file (not symlink)" || fail "adt-plan.md not a real-file copy"
grep -qx 'adt_managed: ADT-087' "$D4/proj/.claude/commands/adt-plan.md" \
  && pass "copied command carries the provenance marker" || fail "marker missing on copy"
# install: all source commands present
src_n=$(ls "$ADT_DIR"/commands/*.md | wc -l | tr -d ' ')
inst_n=$(grep -lx 'adt_managed: ADT-087' "$D4/proj/.claude/commands/"adt-*.md 2>/dev/null | wc -l | tr -d ' ')
[[ "$inst_n" -eq "$src_n" ]] && pass "all $src_n commands copied per-project" || fail "copied $inst_n of $src_n"
# install: did NOT clobber the project's own authored command
grep -q SENTINEL4 "$D4/proj/.claude/commands/adt-mine.md" \
  && pass "project-authored adt-mine.md preserved on install" || fail "authored command clobbered"
! grep -q adt_managed "$D4/proj/.claude/commands/adt-mine.md" \
  && pass "no marker injected into authored command" || fail "marker wrongly injected into authored file"

# opt-in: a project dir that never installed ADT has no /adt-* commands
D4b="$TMP/no-adt"; mkdir -p "$D4b/proj"
[[ ! -d "$D4b/proj/.claude/commands" ]] \
  && pass "non-installed project has no .claude/commands (opt-in)" || fail "commands appeared without install"

# uninstall: remove marked copies, KEEP the authored one
( source "$ADT_DIR/lib/uninstall.sh"; _remove_adt_commands "$D4/proj/.claude" ) >/dev/null 2>&1
[[ ! -e "$D4/proj/.claude/commands/adt-plan.md" ]] \
  && pass "uninstall removed the ADT command copy" || fail "ADT copy survived uninstall"
[[ -f "$D4/proj/.claude/commands/adt-mine.md" ]] && grep -q SENTINEL4 "$D4/proj/.claude/commands/adt-mine.md" \
  && pass "uninstall KEPT the project-authored command" || fail "authored command deleted by uninstall!"

# migration: setup removes an old global adt-*.md link. HOME is a sandbox.
D4c="$TMP/migration"; FAKE_HOME="$D4c/home"; mkdir -p "$FAKE_HOME/.claude/commands"
ln -s "$ADT_DIR/commands/plan.md" "$FAKE_HOME/.claude/commands/adt-legacy.md"
HOME="$FAKE_HOME" bash -c '
  find "$HOME/.claude/commands" -maxdepth 1 -name "adt-*.md" -type l -delete 2>/dev/null || true
'   # the same command setup.sh runs
[[ ! -e "$FAKE_HOME/.claude/commands/adt-legacy.md" ]] \
  && pass "legacy global command link removed by migration (HOME-sandboxed)" \
  || fail "legacy global link survived migration"

# A command with no frontmatter still installs, with the marker added. A sandbox
# ADT_DIR holds the malformed command so the real commands/ is untouched.
D4d="$TMP/badcmd"; FAKE_ADT="$D4d/adt"
mkdir -p "$FAKE_ADT/lib" "$FAKE_ADT/commands" "$FAKE_ADT/defaults/rules" \
         "$FAKE_ADT/defaults/hooks" "$FAKE_ADT/defaults/skills" "$FAKE_ADT/defaults/agents" \
         "$D4d/proj"
cp "$ADT_DIR/lib/commands-marker.sh" "$ADT_DIR/lib/install-defaults.sh" "$FAKE_ADT/lib/"
cp "$ADT_DIR"/commands/*.md "$FAKE_ADT/commands/"
[[ -f "$ADT_DIR/defaults/settings.hooks.json" ]] && cp "$ADT_DIR/defaults/settings.hooks.json" "$FAKE_ADT/defaults/"
printf '# no frontmatter command\nbody\n' > "$FAKE_ADT/commands/badcmd.md"
if bash "$FAKE_ADT/lib/install-defaults.sh" "$FAKE_ADT" "$D4d/proj" >/dev/null 2>&1; then
  pass "frontmatter-less command does not abort install (set -e safe)"
else
  fail "frontmatter-less command aborted install"
fi
[[ -f "$D4d/proj/.claude/commands/adt-badcmd.md" ]] \
  && grep -qx 'adt_managed: ADT-087' "$D4d/proj/.claude/commands/adt-badcmd.md" \
  && pass "frontmatter-less command installed + marked" || fail "bad command not marked/installed"
n_bad=$(ls "$D4d/proj/.claude/commands/"adt-*.md 2>/dev/null | wc -l | tr -d ' ')
[[ "$n_bad" -eq $(( $(ls "$ADT_DIR"/commands/*.md | wc -l | tr -d ' ') + 1 )) ]] \
  && pass "all commands installed despite the malformed one (no mid-loop abort)" \
  || fail "only $n_bad commands landed (mid-loop abort)"

# ── Test 5: copy + manifest install and uninstall ─────────────────────────────
#   (i)   every installed file is a real file; .claude/ has no symlinks
#   (ii)  adt_dod.py is copied to .claude/tools/ and the copied launcher finds it
#         at ../tools
#   (iii) install writes .adt-manifest.json with a sha256 per file
#   (iv)  uninstall deletes the recorded files and the manifest, and keeps a
#         project's own file
echo "[test] copy + manifest install/uninstall"
D5="$TMP/copymodel"; mkdir -p "$D5/proj"
echo "MY OWN RULE" > "$D5/proj_seed_authored"   # staged below after .claude exists
bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$D5/proj" >/dev/null 2>&1
PC="$D5/proj/.claude"

# (i) zero ADT symlinks anywhere under .claude/
[[ -z "$(find "$PC" -type l 2>/dev/null)" ]] \
  && pass "install creates no symlinks" \
  || fail "symlink(s) present: $(find "$PC" -type l)"
# every surface is a real file
[[ -f "$PC/rules/working-style.md" && ! -L "$PC/rules/working-style.md" ]] \
  && pass "rule installed as a real-file copy" || fail "rule not a real copy"

# (ii) adt_dod.py copied locally + the copied launcher reaches it by ../tools
[[ -f "$PC/tools/adt_dod.py" && ! -L "$PC/tools/adt_dod.py" ]] \
  && pass "adt_dod.py copied to .claude/tools/ (no symlink, no \$ADT_DIR)" \
  || fail "adt_dod.py not copied locally"
ticket="$D5/proj/t.md"
# --gate also needs a DoD-coverage review and a Plan-quality review, so the
# fixture has both. These assertions only test that the launcher finds the grader.
printf -- '---\nid: ADT-X\ndone_evidence:\n  - must_run: %s\n    lane: build\n---\n# t\n\n### DoD-coverage review\n**Verdict:** COVERED\n**Gaps:** none\n\n### Plan-quality review\n**Verdict:** SOUND\n**Defects:** none\n' "'true'" > "$ticket"
gate_out="$( cd "$D5/proj" && bash "$PC/hooks/adt-dod.sh" "$ticket" --gate 2>&1 )"; gate_rc=$?
[[ "$gate_out" == APPROVABLE* && "$gate_rc" -eq 0 ]] \
  && pass "copied launcher reached the LOCAL grader (../tools, no walk)" \
  || fail "launcher did not reach local grader (out='$gate_out' rc=$gate_rc)"

# (ii-b) run from the ADT source tree, the launcher finds the grader at ../../tools
src_out="$( bash "$ADT_DIR/defaults/hooks/adt-dod.sh" "$ticket" --gate 2>&1 )"; src_rc=$?
[[ "$src_out" == APPROVABLE* && "$src_rc" -eq 0 ]] \
  && pass "launcher resolves the grader from the ADT SOURCE tree (../../tools)" \
  || fail "source-tree launcher did not reach grader (out='$src_out' rc=$src_rc)"

# (iii) manifest exists with a sha256 per file
[[ -f "$PC/.adt-manifest.json" ]] && grep -q '"sha256"' "$PC/.adt-manifest.json" \
  && pass "install wrote .adt-manifest.json with a sha256 per file" \
  || fail "manifest missing or has no sha256"

# (iv) uninstall-by-manifest: removes recorded files + manifest, keeps authored
echo "MY OWN RULE" > "$PC/rules/project-rule.md"   # project-authored (not in manifest)
( source "$ADT_DIR/lib/uninstall.sh"; _remove_by_manifest "$PC" ) >/dev/null 2>&1
[[ ! -f "$PC/rules/working-style.md" ]] \
  && pass "uninstall-by-manifest removed an ADT copy" || fail "ADT copy survived"
[[ ! -f "$PC/tools/adt_dod.py" ]] \
  && pass "uninstall-by-manifest removed the local adt_dod.py" || fail "adt_dod.py survived"
[[ ! -f "$PC/.adt-manifest.json" ]] \
  && pass "uninstall deleted the manifest" || fail "manifest survived"
[[ -f "$PC/rules/project-rule.md" ]] \
  && pass "project-authored file KEPT (not in manifest)" || fail "authored file deleted!"

# ── Test 6: update and uninstall keep a user-edited copy ──────────────────────
echo "[test] checksum preserves a user-edited copy"
D6="$TMP/useredit"; mkdir -p "$D6/proj"
bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$D6/proj" >/dev/null 2>&1
P6="$D6/proj/.claude"
printf '\nMY LOCAL EDIT\n' >> "$P6/rules/working-style.md"   # user edits a copy
# update: re-run install — must PRESERVE the edit, not overwrite
bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$D6/proj" >/dev/null 2>&1
grep -q "MY LOCAL EDIT" "$P6/rules/working-style.md" \
  && pass "update preserves a user-edited copy (sha mismatch → keep)" \
  || fail "update clobbered the user's edit"
# uninstall: must KEEP the edited copy, surface it, not delete
( source "$ADT_DIR/lib/uninstall.sh"; _remove_by_manifest "$P6" ) >/dev/null 2>&1
grep -q "MY LOCAL EDIT" "$P6/rules/working-style.md" 2>/dev/null \
  && pass "uninstall keeps a user-edited copy (never deletes your edit)" \
  || fail "uninstall deleted the user's edited copy"

# ── settings.json: hook entries and env keys, install and uninstall ───────────
echo "[test] settings.json hooks + env un-merge"
D7="$TMP/settings-env"; mkdir -p "$D7/proj/development-team" "$D7/proj/.claude"
git -C "$D7/proj" init -q
# The project has two env keys before install: one ADT also ships, with a
# different value, and one ADT does not ship.
cat > "$D7/proj/.claude/settings.json" <<'JSON'
{ "env": { "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "0", "MY_OWN_KEY": "keep" } }
JSON
bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$D7/proj" >/dev/null 2>&1

grep -q '"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "0"' "$D7/proj/.claude/settings.json" \
  && pass "install does not clobber a project's own value for a key ADT ships" \
  || fail "install overwrote the project's env value"
grep -q "adt-terminal-title" "$D7/proj/.claude/settings.json" \
  && pass "install wired the tab-title hook" || fail "tab-title hook not wired"

( source "$ADT_DIR/lib/uninstall.sh"; _unmerge_hooks "$ADT_DIR" "$D7/proj/.claude/settings.json" ) >/dev/null 2>&1
# Every hook ADT registers or lists in the manifest must be gone from settings.json.
leaked=$(/usr/bin/python3 - \
  "$D7/proj/.claude/settings.json" "$ADT_DIR" "$D7/proj/.claude/.adt-manifest.json" <<'PYEOF'
import json, os, sys
settings, adt_dir, manifest = sys.argv[1], sys.argv[2], sys.argv[3]
owned = set()
with open(os.path.join(adt_dir, "defaults", "settings.hooks.json")) as fh:
    for blocks in json.load(fh).get("hooks", {}).values():
        for b in blocks:
            for h in b.get("hooks", []):
                owned.add(os.path.basename(h["command"]))
try:
    with open(manifest) as fh:
        for e in json.load(fh).get("files", []):
            path = e.get("path", "")
            if path.startswith("hooks/") and path.endswith(".sh"):
                owned.add(os.path.basename(path))
except Exception:
    pass
blob = open(settings).read()
print(" ".join(sorted(n for n in owned if n in blob)))
PYEOF
)
[[ -z "$leaked" ]] \
  && pass "no ADT hook survives un-merge" \
  || fail "no ADT hook survives un-merge (still wired: $leaked)"
grep -q "MY_OWN_KEY" "$D7/proj/.claude/settings.json" \
  && pass "uninstall keeps an env key ADT never shipped" \
  || fail "uninstall deleted the project's own env key"
grep -q '"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "0"' "$D7/proj/.claude/settings.json" \
  && pass "uninstall keeps a project-owned value for a key ADT ships" \
  || fail "uninstall removed a key whose value the project owns"

# ── a project's own adt-prefixed hook survives uninstall ─────────────────────
# ADT did not install this hook, so uninstall must keep its settings entry
# even though the name starts with adt-.
echo "[test] a project's own adt-prefixed hook survives uninstall"
D7B="$TMP/settings-own-adt-hook"; mkdir -p "$D7B/proj/development-team" "$D7B/proj/.claude"
git -C "$D7B/proj" init -q
cat > "$D7B/proj/.claude/settings.json" <<'JSON'
{ "hooks": { "PreToolUse": [ { "matcher": "Bash", "hooks": [
  { "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/adt-my-project-hook.sh" } ] } ] } }
JSON
bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$D7B/proj" >/dev/null 2>&1
( source "$ADT_DIR/lib/uninstall.sh"; _unmerge_hooks "$ADT_DIR" "$D7B/proj/.claude/settings.json" ) >/dev/null 2>&1
grep -q "adt-my-project-hook.sh" "$D7B/proj/.claude/settings.json" \
  && pass "adt-my-project-hook.sh survives uninstall" \
  || fail "adt-my-project-hook.sh was stripped from settings.json"
# ...and the ADT hooks around it still went.
! grep -q "adt-done-guard.sh" "$D7B/proj/.claude/settings.json" \
  && pass "ADT's own hooks still go when a project hook is present" \
  || fail "ADT hooks survived alongside the project's own"

# An env key with ADT's own value is removed, and so is the emptied env block.
D8="$TMP/settings-env-adt"; mkdir -p "$D8/proj/development-team"
git -C "$D8/proj" init -q
bash "$ADT_DIR/lib/install-defaults.sh" "$ADT_DIR" "$D8/proj" >/dev/null 2>&1
grep -q "CLAUDE_CODE_DISABLE_TERMINAL_TITLE" "$D8/proj/.claude/settings.json" \
  && pass "install merged the env block" || fail "env block not merged"
( source "$ADT_DIR/lib/uninstall.sh"; _unmerge_hooks "$ADT_DIR" "$D8/proj/.claude/settings.json" ) >/dev/null 2>&1
! grep -q "CLAUDE_CODE_DISABLE_TERMINAL_TITLE" "$D8/proj/.claude/settings.json" \
  && pass "uninstall removes ADT's own env key" \
  || fail "env key survived uninstall"
! grep -q '"env"' "$D8/proj/.claude/settings.json" \
  && pass "an emptied env block is pruned" || fail "empty env block left behind"

echo
if [[ "$FAILS" -eq 0 ]]; then echo "All uninstall tests passed."; else echo "$FAILS assertion(s) FAILED."; exit 1; fi

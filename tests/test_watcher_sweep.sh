#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests lib/watcher.sh: install_watcher removes any other adt_watch.py agent for
# the project before installing ADT's own, leaves an identical agent alone on a
# reinstall, replaces a changed one, and uninstall_watcher leaves none.
# macOS only. Uses a fake launchctl and a temporary HOME.
set -euo pipefail

[[ "$(uname -s)" == "Darwin" ]] || { echo "[skip] watcher sweep test is Darwin-only"; exit 0; }

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PROJ="$TMP/proj"; mkdir -p "$PROJ/.adt/state"
HOME_DIR="$TMP/home"; LA="$HOME_DIR/Library/LaunchAgents"; mkdir -p "$LA"
BIN="$TMP/bin"; mkdir -p "$BIN"
# Fake launchctl: logs each call to $LOG and succeeds, except `print` (is the
# agent loaded?), which fails while $NOT_LOADED exists.
LOG="$TMP/launchctl.log"; NOT_LOADED="$TMP/not-loaded"
cat > "$BIN/launchctl" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$LOG"
[[ "\$1" == print && -e "$NOT_LOADED" ]] && exit 113
exit 0
EOF
chmod +x "$BIN/launchctl"

# A watcher with a non-ADT name already targeting this project.
write_foreign_watcher() {
  cat > "$LA/com.acme.custom-watch.plist" <<EOF
<plist><dict>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/python3</string>
    <string>$ADT_DIR/tools/adt_watch.py</string>
    <string>--root</string><string>$PROJ</string><string>--once</string>
  </array>
</dict></plist>
EOF
}
write_foreign_watcher

# An unrelated agent that must be left alone.
cat > "$LA/com.other.unrelated.plist" <<EOF
<plist><dict><key>ProgramArguments</key><array>
  <string>/usr/bin/true</string></array></dict></plist>
EOF

run() { PATH="$BIN:$PATH" HOME="$HOME_DIR" bash -c \
  'source "'"$ADT_DIR"'/lib/watcher.sh"; '"$1" 2>/dev/null; }

# Count plists in $LA that both invoke adt_watch.py AND reference $PROJ. Plain
# loop (no pipeline) so it's safe under set -e even when the glob is empty.
count_targeting() {
  local n=0 f
  for f in "$LA"/*.plist; do
    [[ -f "$f" ]] || continue
    grep -q 'adt_watch\.py' "$f" 2>/dev/null && grep -Fq -- "$PROJ" "$f" 2>/dev/null && n=$((n+1))
  done
  echo "$n"
}

# ── install removes the other watcher and leaves only ADT's own ───────────
echo "[test] install_watcher removes a foreign watcher, leaves one ADT agent"
run "install_watcher '$ADT_DIR' myproj '$PROJ'"
[[ ! -f "$LA/com.acme.custom-watch.plist" ]] && pass "other watcher removed" || fail "other watcher still present"
[[ -f "$LA/com.adt.myproj.watch.plist" ]]    && pass "ADT watcher installed"   || fail "ADT watcher missing"
[[ -f "$LA/com.other.unrelated.plist" ]]     && pass "unrelated agent untouched" || fail "unrelated agent removed!"
# Exactly one adt_watch.py agent now targets the project.
n=$(count_targeting)
[[ "$n" == "1" ]] && pass "exactly one watcher targets the project" || fail "got $n watchers targeting project"

# ── an identical reinstall leaves ADT's agent alone ───────────────────────
# Rewriting the plist, even byte-for-byte, makes macOS register a new background
# item and alert when python3 is unsigned. A second install with nothing changed
# must not write the file or reload the agent, but must still sweep a foreign one.
echo "[test] an identical reinstall keeps the plist and does not reload the agent"
ADT_PLIST="$LA/com.adt.myproj.watch.plist"
touch -t 202001010000 "$ADT_PLIST"
before=$(stat -f %m "$ADT_PLIST")
write_foreign_watcher
: > "$LOG"
out=$(run "install_watcher '$ADT_DIR' myproj '$PROJ'")
[[ "$(stat -f %m "$ADT_PLIST")" == "$before" ]] \
  && pass "identical reinstall leaves the plist untouched" || fail "identical reinstall rewrote the plist"
if grep -q "bootout gui/[0-9]*/com.adt.myproj.watch" "$LOG" || grep -q "^bootstrap" "$LOG"; then
  fail "identical reinstall reloaded the agent: $(tr '\n' ';' < "$LOG")"
else
  pass "identical reinstall calls neither bootout nor bootstrap"
fi
[[ "$out" == *"launchd watcher unchanged: com.adt.myproj.watch"* ]] \
  && pass "identical reinstall reports the watcher unchanged" || fail "no 'unchanged' report, got: $out"
[[ ! -f "$LA/com.acme.custom-watch.plist" ]] \
  && pass "foreign watcher removed on an identical reinstall" || fail "foreign watcher survived an identical reinstall"
n=$(count_targeting)
[[ "$n" == "1" ]] && pass "still exactly one watcher targets the project" || fail "got $n watchers targeting project"

# ── an identical plist whose agent is not loaded is loaded again ──────────
echo "[test] an identical plist with the agent not loaded is reloaded"
touch "$NOT_LOADED"; : > "$LOG"
run "install_watcher '$ADT_DIR' myproj '$PROJ'" >/dev/null
rm -f "$NOT_LOADED"
grep -q "^bootstrap gui/[0-9]* $ADT_PLIST" "$LOG" \
  && pass "identical plist with the agent not loaded is reloaded" || fail "agent not reloaded: $(tr '\n' ';' < "$LOG")"

# ── a changed plist is replaced and reloaded ──────────────────────────────
echo "[test] a changed interval replaces the plist and reloads the agent"
: > "$LOG"
run "install_watcher '$ADT_DIR' myproj '$PROJ' 120" >/dev/null
# AO-006: the interval is an argv string now, not a StartInterval integer — the
# agent is resident and runs the loop itself. Asserted as the argument that
# FOLLOWS --interval, which pins the pairing; the old `<integer>120</integer>`
# grep would have matched that number anywhere in the file.
if grep -A1 -- "--interval" "$ADT_PLIST" | grep -q "<string>120</string>" \
    && grep -q "bootout gui/[0-9]*/com.adt.myproj.watch" "$LOG" \
    && grep -q "^bootstrap gui/[0-9]* $ADT_PLIST" "$LOG"; then
  pass "changed interval replaces the plist and reloads it"
else
  fail "changed interval not applied: $(grep -A1 -- '--interval' "$ADT_PLIST" | tr '\n' ' '); calls: $(tr '\n' ';' < "$LOG")"
fi

# ── uninstall removes every watcher for the project ───────────────────────
echo "[test] uninstall_watcher leaves zero watchers for the path"
run "uninstall_watcher myproj '$PROJ'"
[[ ! -f "$LA/com.adt.myproj.watch.plist" ]] && pass "ADT watcher removed on uninstall" || fail "ADT watcher kept"
n=$(count_targeting)
[[ "$n" == "0" ]] && pass "zero watchers target the project" || fail "$n watchers still target project"
[[ -f "$LA/com.other.unrelated.plist" ]]     && pass "unrelated agent still untouched" || fail "unrelated agent removed!"

echo
if [[ "$FAILS" -eq 0 ]]; then echo "All watcher-sweep tests passed."; else echo "$FAILS assertion(s) FAILED."; exit 1; fi

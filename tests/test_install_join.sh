#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT-384: adt-install.sh on a repo where ADT is already installed joins that
# install instead of starting a fresh one.
#
# Runs the real installer from a scratch copy of this tree, against projects
# whose origin is a bare repo on disk. `gh` and `launchctl` are stubs that log
# every call, so "creates no board" is read from what was actually called and a
# test run can never load a launchd job on the machine running it. The daily
# advisory and telemetry are pre-stamped as done and DO_NOT_TRACK is set, so the
# watch tick the installer runs makes no network call.
# Prints plain `ok: <claim>` lines (the DoD greps them); exits 1 on any failure.
set -uo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
ok()  { printf 'ok: %s\n' "$1"; }
bad() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
# shellcheck source=lib/scratch-adt.sh
source "$SRC/tests/lib/scratch-adt.sh"   # ADT at commit A, git + HOME isolated
export DO_NOT_TRACK=1
USERP="$TMP/userp"; mkdir -p "$USERP"

# ── B: a change to a shipped rule ────────────────────────────────────────────
echo "# layer change B" >> "$ADT/defaults/rules/working-style.md"
git -C "$ADT" commit -qam B; B="$(git -C "$ADT" rev-parse HEAD)"

# ── Stubs ────────────────────────────────────────────────────────────────────
BIN="$TMP/bin"; mkdir -p "$BIN"; GHLOG="$TMP/gh.log"; : > "$GHLOG"
cat > "$BIN/gh" <<'EOF'
#!/usr/bin/env bash
echo "gh $*" >> "$GHLOG"
case "$* " in
  *"auth status"*)                 echo "  - Token scopes: 'gist', 'project', 'repo'" ;;
  *"repo view"*)                   echo "${STUB_REPO:-me/agz}" ;;
  *"project list"*)                echo '{"projects":[{"title":"agz backlog","number":7}]}' ;;
  *"project create"*)              echo '{"number":99}' ;;
  *"project field-list"*)          echo '{"fields":[]}' ;;
  *"/protection "*)                echo '{"url":"x"}' ;;
  *"issues?labels=adt%3Ainstall"*) echo "${STUB_ISSUES:-[]}" ;;
  *"/comments?per_page"*)          echo "${STUB_COMMENTS:-[]}" ;;
  *"api "*)                        echo '[]' ;;
esac
exit 0
EOF
printf '#!/usr/bin/env bash\necho "launchctl $*" >> "%s"\n[[ "$1" == print ]] && exit 1\nexit 0\n' \
  "$TMP/launchctl.log" > "$BIN/launchctl"
chmod +x "$BIN/gh" "$BIN/launchctl"
export GHLOG

# ── The team repo: machine 1 installs ADT at A and commits it ────────────────
git init -q --bare "$TMP/team.git"
git clone -q "$TMP/team.git" "$TMP/m1" 2>/dev/null
echo "# agz" > "$TMP/m1/README.md"; echo "# agz" > "$TMP/m1/CLAUDE.md"
git -C "$TMP/m1" add -A && git -C "$TMP/m1" commit -q -m init
# The history uses ZZZ while the committed settings say AGZ: a join must take
# the committed prefix, not stop on the history check (ADT-066/ADT-135).
for m in "fix(ZZZ-1): a" "feat(ZZZ-2): b"; do git -C "$TMP/m1" commit -q --allow-empty -m "$m"; done
git -C "$TMP/m1" push -q origin main 2>/dev/null
behind_clone() {  # behind_clone <name> — cloned before the layer commit lands
  git clone -q "$TMP/team.git" "$TMP/$1" 2>/dev/null
  mkdir -p "$TMP/$1/.adt/state"
  local today; today="$(date -u +%Y-%m-%d)"
  for n in advisory telemetry; do echo "$today" > "$TMP/$1/.adt/state/$n-last"; done
}
for c in m2 m3 m5 m6; do behind_clone "$c"; done
adt_at "$A"
"$ADT/lib/install-defaults.sh" "$ADT" "$TMP/m1" >/dev/null 2>&1
cat > "$TMP/m1/.claude/adt-project.yaml" <<'EOF'
name: agz
repo: me/agz
main_branch: main
id_prefix: AGZ
board_title: "agz backlog"
EOF
git -C "$TMP/m1" add -A && git -C "$TMP/m1" commit -q -m "install ADT at A" && git -C "$TMP/m1" push -q origin main 2>/dev/null

install_in() {  # install_in <dir> <stdin> [flags…] — sets OUT and RC
  local d="$1" input="$2"; shift 2
  : > "$GHLOG"
  OUT="$(cd "$d" && printf '%b' "$input" | PATH="$BIN:$PATH" ADT_USER_PROJECTS="$USERP" \
          bash "$ADT/adt-install.sh" "$@" 2>&1)"; RC=$?
}
cfg() { yq -r "$1" "$USERP/$2.yaml" 2>/dev/null; }

echo "[test] --help"
bash "$SRC/adt-install.sh" --help | grep -q "this machine joins that install" \
  && ok "adt-install.sh --help describes joining an existing install" \
  || bad "--help does not describe joining"

echo "[test] a second machine joins"
git -C "$TMP/m2" fetch -q origin
behind="$(git -C "$TMP/m2" rev-list --count main..origin/main)"
[[ ! -e "$TMP/m2/.claude/.adt-manifest.json" && "$behind" -ge 1 ]] \
  || bad "fixture: m2 is not a behind checkout without a manifest"
install_in "$TMP/m2" 'y\n'
if [[ $RC -eq 0 ]] && grep -q "is already installed" <<<"$OUT"; then
  ok "a clone whose local main is behind origin and has no manifest on disk is detected as already installed"
else
  bad "join not detected: rc=$RC out: $OUT"
fi
grep -qF "ADT $FIXTURE_VERSION (source ${A:0:7}) is already installed in me/agz; this machine will join it" <<<"$OUT" \
  && ok "the install prints that ADT is already installed with its bundle and source commit and that this machine will join it" \
  || bad "no join line with bundle and source commit"
# Only "y" was piped in, and there is no --yes. Any question before Proceed
# would have taken that "y" and the next one would have hit EOF and failed.
if [[ $RC -eq 0 && "$(cfg .kanban.id_prefix agz)" == AGZ && "$(cfg .github.repo agz)" == me/agz \
      && "$(cfg .main_branch agz)" == main && "$(cfg .github.board_title agz)" == "agz backlog" ]]; then
  ok "join mode asks for no name, repo, main branch or prefix and uses the committed values"
else
  bad "join config: rc=$RC prefix=$(cfg .kanban.id_prefix agz) repo=$(cfg .github.repo agz) board=$(cfg .github.board_title agz)"
fi
if [[ $RC -eq 0 && "$(cfg .kanban.id_prefix agz)" == AGZ ]] && ! grep -qi "prefix mismatch" <<<"$OUT"; then
  ok "a join takes the committed prefix even when the repo's commit history uses another"
else
  bad "committed prefix vs history: rc=$RC prefix=$(cfg .kanban.id_prefix agz) out: $OUT"
fi
if grep -q "gh project list" "$GHLOG" && ! grep -q "gh project create" "$GHLOG"; then
  ok "join mode creates no board"
else
  bad "board: $(grep 'gh project' "$GHLOG" | tr '\n' ';')"
fi

echo "[test] --prefix still overrides on a join"
rm -f "$USERP"/*.yaml
install_in "$TMP/m3" 'y\n' --no-github --prefix XYZ
[[ $RC -eq 0 && "$(cfg .kanban.id_prefix agz)" == XYZ ]] \
  && ok "--prefix overrides the committed prefix during a join" \
  || bad "--prefix on a join: rc=$RC prefix=$(cfg .kanban.id_prefix agz) out: $OUT"

echo "[test] an older ADT clone stops before any prompt"
git init -q --bare "$TMP/newer.git"
git clone -q "$TMP/newer.git" "$TMP/n1" 2>/dev/null
echo "# n" > "$TMP/n1/README.md"
git -C "$TMP/n1" add -A && git -C "$TMP/n1" commit -q -m init
adt_at "$B"; "$ADT/lib/install-defaults.sh" "$ADT" "$TMP/n1" >/dev/null 2>&1
git -C "$TMP/n1" add -A && git -C "$TMP/n1" commit -q -m "ADT at B" && git -C "$TMP/n1" push -q origin main 2>/dev/null
git clone -q "$TMP/newer.git" "$TMP/n2" 2>/dev/null
rm -f "$USERP"/*.yaml
adt_at "$A"
install_in "$TMP/n2" ''
# Nothing was piped in and there is no --yes, so reaching any prompt would also
# have failed, but with exit 1 and "No answer", not exit 3 and the pull command.
if [[ $RC -eq 3 ]] && grep -qF "git -C $ADT pull" <<<"$OUT" && ! grep -q "Will configure\|No answer" <<<"$OUT" \
   && [[ -z "$(ls "$USERP")" ]]; then
  ok "an ADT clone older than the committed layer stops the install before any prompt and prints the pull command"
else
  bad "older clone: rc=$RC configs=[$(ls "$USERP")] out: $OUT"
fi

echo "[test] a newer ADT clone names the machines left behind"
rm -f "$USERP"/*.yaml
adt_at "$B"
recent="$(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)"
export STUB_ISSUES='[{"number": 5, "labels": [{"name": "adt:install"}]}]'
export STUB_COMMENTS="[{\"id\": 1, \"author_association\": \"OWNER\", \"user\": {\"login\": \"teammate\"}, \"updated_at\": \"$recent\", \"body\": \"x <!-- adt-machine {\\\"version\\\": \\\"0.1.0\\\", \\\"commit\\\": \\\"$A\\\", \\\"os\\\": \\\"Linux\\\"} -->\"}]"
install_in "$TMP/m5" 'y\n'
if [[ $RC -eq 0 ]] && grep -q "these machines must pull ADT" <<<"$OUT" && grep -q "teammate · Linux · v0.1.0 @${A:0:7}" <<<"$OUT" \
   && git -C "$TMP/m5" show-ref --quiet --verify "refs/heads/chore/adt-upgrade-${B:0:12}"; then
  ok "an install from a newer clone lists the machines whose reported ADT is older than the new layer"
else
  bad "newer clone list: rc=$RC out: $OUT"
fi
unset STUB_ISSUES STUB_COMMENTS

rm -f "$USERP"/*.yaml
install_in "$TMP/m6" 'y\n' --no-github
[[ $RC -eq 0 ]] && grep -q -- "--no-github: skipped the list of machines" <<<"$OUT" \
  && ok "an install from a newer clone with --no-github says the machines list was skipped" \
  || bad "newer clone --no-github: rc=$RC out: $OUT"

echo "[test] a fresh install writes the shared settings"
git init -q --bare "$TMP/solo.git"
git clone -q "$TMP/solo.git" "$TMP/solo" 2>/dev/null
echo "# solo" > "$TMP/solo/README.md"
git -C "$TMP/solo" add -A && git -C "$TMP/solo" commit -q -m init && git -C "$TMP/solo" push -q origin main 2>/dev/null
rm -f "$USERP"/*.yaml
adt_at "$A"
STUB_REPO=me/solo install_in "$TMP/solo" '\n\n\n\ny\n' --no-github
SH="$TMP/solo/.claude/adt-project.yaml"
if [[ $RC -eq 0 && -f "$SH" ]] && ! grep -q "already installed" <<<"$OUT" \
   && [[ "$(yq -r .name "$SH")" == solo && "$(yq -r .repo "$SH")" == "$(cfg .github.repo solo)" \
      && "$(yq -r .id_prefix "$SH")" == "$(cfg .kanban.id_prefix solo)" \
      && "$(yq -r .main_branch "$SH")" == main \
      && "$(yq -r .board_title "$SH")" == "$(cfg .github.board_title solo)" ]]; then
  ok "a fresh install writes .claude/adt-project.yaml with the interviewed values"
else
  bad "fresh shared file: rc=$RC file=$(cat "$SH" 2>/dev/null | tr '\n' ';') out: $OUT"
fi

echo
if [[ $FAILS -eq 0 ]]; then echo "All install-join tests passed."; else echo "$FAILS install-join test(s) FAILED."; exit 1; fi

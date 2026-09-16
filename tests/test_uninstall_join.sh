#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# ADT-384: on a repo whose ADT layer is committed, `adt-install.sh --uninstall`
# removes one machine and leaves the team's committed files alone;
# `--uninstall --everyone` proposes removing ADT from the repo on a branch.
#
# Runs the real installer's uninstall from a scratch copy of this tree against
# projects whose origin is a bare repo on disk. `gh` and `launchctl` are stubs,
# so no test run can touch GitHub or a real launchd job.
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

BIN="$TMP/bin"; mkdir -p "$BIN"
cat > "$BIN/gh" <<'EOF'
#!/usr/bin/env bash
case "$* " in
  *"issues?labels=adt%3Ainstall"*) echo "${STUB_ISSUES:-[]}" ;;
  *"/comments?per_page"*)          echo "${STUB_COMMENTS:-[]}" ;;
  *"api "*)                        echo '[]' ;;
esac
exit 0
EOF
printf '#!/usr/bin/env bash\n[[ "$1" == print ]] && exit 1\nexit 0\n' > "$BIN/launchctl"
chmod +x "$BIN/gh" "$BIN/launchctl"

# ── The team repo: ADT at A committed, with its shared settings ──────────────
git init -q --bare "$TMP/team.git"
git clone -q "$TMP/team.git" "$TMP/m1" 2>/dev/null
echo "# agz" > "$TMP/m1/README.md"; echo "# agz" > "$TMP/m1/CLAUDE.md"
git -C "$TMP/m1" add -A && git -C "$TMP/m1" commit -q -m init
"$ADT/lib/install-defaults.sh" "$ADT" "$TMP/m1" >/dev/null 2>&1
printf 'name: agz\nrepo: me/agz\nmain_branch: main\nid_prefix: AGZ\nboard_title: "agz backlog"\n' \
  > "$TMP/m1/.claude/adt-project.yaml"
git -C "$TMP/m1" add -A && git -C "$TMP/m1" commit -q -m "install ADT" && git -C "$TMP/m1" push -q origin main 2>/dev/null

machine() {  # machine <dir> <origin.git> — a machine with ADT set up on it
  local d="$TMP/$1"
  git clone -q "$2" "$d" 2>/dev/null
  mkdir -p "$d/.adt/state"
  printf 'repo: me/agz\nmain_branch: main\n' > "$d/.adt/config.yaml"
  echo '{"issue": 5, "comment_id": 1}' > "$d/.adt/state/machine-report.json"
  printf 'name: %s\npath: %s\ngithub:\n  repo: me/agz\n' "$1" "$d" > "$USERP/$1.yaml"
}
uninstall_in() {  # uninstall_in <dir> [flags…] — sets OUT and RC
  local d="$1"; shift
  OUT="$(cd "$d" && PATH="$BIN:$PATH" ADT_USER_PROJECTS="$USERP" \
          bash "$ADT/adt-install.sh" "$@" </dev/null 2>&1)"; RC=$?
}
clean() { [[ -z "$(git -C "$1" status --porcelain)" ]]; }

echo "[test] --help and the flag check"
bash "$SRC/adt-install.sh" --help | grep -q -- "--everyone" \
  && ok "adt-install.sh --help documents --everyone" \
  || bad "--help does not mention --everyone"
machine refuse "$TMP/team.git"
uninstall_in "$TMP/refuse" --everyone --no-github
if [[ $RC -ne 0 ]] && grep -q -- "--everyone only works with --uninstall" <<<"$OUT" && [[ -d "$TMP/refuse/.adt" ]]; then
  ok "--everyone without --uninstall is refused"
else
  bad "--everyone alone: rc=$RC out: $OUT"
fi

echo "[test] a committed layer: --uninstall removes this machine only"
machine one "$TMP/team.git"
uninstall_in "$TMP/one" --uninstall --no-github
if [[ $RC -eq 0 && ! -e "$TMP/one/.adt" && -f "$USERP/one.yaml.bak" && ! -e "$USERP/one.yaml" \
      && -f "$TMP/one/.claude/.adt-manifest.json" && -f "$TMP/one/.claude/adt-project.yaml" ]] \
   && clean "$TMP/one"; then
  ok "on a repo whose ADT layer is committed, --uninstall removes this machine's .adt/ and config and leaves every tracked file unchanged"
else
  bad "committed --uninstall: rc=$RC status=[$(git -C "$TMP/one" status --porcelain | tr '\n' ' ')] out: $OUT"
fi
if grep -q "stays installed" <<<"$OUT" && grep -qF -- "--uninstall --everyone" <<<"$OUT"; then
  ok "--uninstall says ADT stays installed for the other machines and names --uninstall --everyone"
else
  bad "no stays-installed message naming --uninstall --everyone. out: $OUT"
fi

echo "[test] a committed layer: --uninstall --everyone proposes the removal on a branch"
machine two "$TMP/team.git"
git -C "$TMP/two" checkout -q -b work
uninstall_in "$TMP/two" --uninstall --everyone --no-github
changes="$(git -C "$TMP/two" diff --name-status origin/main chore/adt-uninstall 2>/dev/null)"
if [[ $RC -eq 0 ]] && grep -qx "D	.claude/.adt-manifest.json" <<<"$changes" \
   && grep -qx "D	.claude/adt-project.yaml" <<<"$changes" \
   && [[ "$(git -C "$TMP/two" rev-list --count origin/main..chore/adt-uninstall)" == 1 \
         && "$(git -C "$TMP/two" branch --show-current)" == work \
         && -f "$TMP/two/.claude/.adt-manifest.json" && ! -e "$TMP/two/.adt" \
         && "$(git -C "$TMP/two" worktree list | wc -l | tr -d ' ')" == 1 ]] \
   && clean "$TMP/two"; then
  ok "--uninstall --everyone commits the removal of the ADT layer and .claude/adt-project.yaml to a chore/adt-uninstall branch and leaves the current branch and working tree untouched"
else
  bad "--everyone branch: rc=$RC changes=[$(tr '\n' ';' <<<"$changes")] branch=$(git -C "$TMP/two" branch --show-current) out: $OUT"
fi
grep -q -- "--no-github: skipped the list of machines" <<<"$OUT" \
  && ok "--uninstall --everyone with --no-github says the machines list was skipped" \
  || bad "no --no-github skip message. out: $OUT"

machine three "$TMP/team.git"
recent="$(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)"
export STUB_ISSUES='[{"number": 5, "labels": [{"name": "adt:install"}]}]'
export STUB_COMMENTS="[{\"id\": 7, \"author_association\": \"COLLABORATOR\", \"user\": {\"login\": \"teammate\"}, \"updated_at\": \"$recent\", \"body\": \"x <!-- adt-machine {\\\"version\\\": \\\"0.1.0\\\", \\\"commit\\\": \\\"$A\\\", \\\"os\\\": \\\"Linux\\\"} -->\"}]"
uninstall_in "$TMP/three" --uninstall --everyone
if [[ $RC -eq 0 ]] && grep -q "still have to run" <<<"$OUT" && grep -q "teammate · Linux · v0.1.0 @${A:0:7}" <<<"$OUT"; then
  ok "--uninstall --everyone lists the machines that still have to run --uninstall"
else
  bad "machines list on --everyone: rc=$RC out: $OUT"
fi
unset STUB_ISSUES STUB_COMMENTS

echo "[test] no committed layer: uninstall works on the working tree as before"
solo() {  # solo <name> — ADT installed into the working tree, never committed
  git init -q --bare "$TMP/$1.git"
  git clone -q "$TMP/$1.git" "$TMP/$1" 2>/dev/null
  echo "# $1" > "$TMP/$1/CLAUDE.md"
  git -C "$TMP/$1" add -A && git -C "$TMP/$1" commit -q -m init && git -C "$TMP/$1" push -q origin main 2>/dev/null
  "$ADT/lib/install-defaults.sh" "$ADT" "$TMP/$1" >/dev/null 2>&1
  printf 'name: %s\nrepo: me/%s\n' "$1" "$1" > "$TMP/$1/.claude/adt-project.yaml"
  mkdir -p "$TMP/$1/.adt/state"; printf 'repo: me/%s\nmain_branch: main\n' "$1" > "$TMP/$1/.adt/config.yaml"
  printf 'name: %s\npath: %s\ngithub:\n  repo: me/%s\n' "$1" "$TMP/$1" "$1" > "$USERP/$1.yaml"
}
solo plain
uninstall_in "$TMP/plain" --uninstall --no-github
if [[ $RC -eq 0 && ! -e "$TMP/plain/.claude/.adt-manifest.json" && ! -e "$TMP/plain/.claude/adt-project.yaml" \
      && ! -e "$TMP/plain/.adt" ]]; then
  ok "on a repo with no committed ADT layer, --uninstall removes the layer and .claude/adt-project.yaml from the working tree as before"
else
  bad "uncommitted --uninstall: rc=$RC out: $OUT"
fi
solo everyone
uninstall_in "$TMP/everyone" --uninstall --everyone --no-github
if [[ $RC -eq 0 && ! -e "$TMP/everyone/.claude/.adt-manifest.json" && ! -e "$TMP/everyone/.claude/adt-project.yaml" \
      && ! -e "$TMP/everyone/.adt" ]] \
   && ! git -C "$TMP/everyone" show-ref --quiet --verify refs/heads/chore/adt-uninstall \
   && [[ "$(git -C "$TMP/plain" status --porcelain | sort)" == "$(git -C "$TMP/everyone" status --porcelain | sed 's/everyone/plain/g' | sort)" ]]; then
  ok "on a repo with no committed ADT layer, --uninstall --everyone does exactly what --uninstall does and makes no branch"
else
  bad "uncommitted --everyone: rc=$RC branches=[$(git -C "$TMP/everyone" branch | tr '\n' ' ')] out: $OUT"
fi

echo
if [[ $FAILS -eq 0 ]]; then echo "All uninstall-join tests passed."; else echo "$FAILS uninstall-join test(s) FAILED."; exit 1; fi

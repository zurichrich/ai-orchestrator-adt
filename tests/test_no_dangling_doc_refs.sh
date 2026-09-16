#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that every backticked repo path in a tracked text file exists on disk.
# Markdown links are not checked. tools/calibration/*/cases/ is skipped because
# it is recorded agent output.
# Section A checks the resolver against fixtures; section B runs it on this repo.

set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

pass=0 fail=0
ok()  { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }

# Top-level directories whose contents are addressable as repo paths. A path is
# only checked when its first segment is one of these, so ordinary backticked
# prose (`git log`, `main`, `--apply`) is never mistaken for a file.
ROOTS='commands|defaults|docs|lib|schema|scripts|tests|tools|collector|release|learnings|projects'

# dangling <file> — prints "file:path" for every backticked repo path in <file>
# that does not exist on disk.
dangling() {
  local f="$1" p
  grep -oE '`('"$ROOTS"')/[A-Za-z0-9._/-]+`' "$f" 2>/dev/null \
    | tr -d '`' | sort -u | while IFS= read -r p; do
        [ -e "$p" ] || printf '%s:%s\n' "$f" "$p"
      done
}

echo "== A. fixtures"

FIX="$(mktemp -d)"; trap 'rm -rf "$FIX"' EXIT
printf 'see `tools/adt_cost.py` for details\n'          > "$FIX/good.md"
# Built from fragments so this file does not match its own scan in section B.
_l='learn'; _g='ings'
printf 'see `%s%s/2026-01-01-gone.md` for details\n' "$_l" "$_g" > "$FIX/bad.md"
printf 'run `git log` and pass `--apply` to write\n'    > "$FIX/prose.md"

[ -z "$(dangling "$FIX/good.md")" ] && ok "an existing path resolves" \
  || bad "existing path reported as dangling"

[ -n "$(dangling "$FIX/bad.md")" ] && ok "a missing path is reported" \
  || bad "missing path not reported"

[ -z "$(dangling "$FIX/prose.md")" ] && ok "ordinary backticked prose is not treated as a path" \
  || bad "prose misread as a repo path: $(dangling "$FIX/prose.md")"

echo "== B. this repo"

found=0
while IFS= read -r f; do
  case "$f" in
    tools/calibration/*/cases/*) continue ;;
  esac
  [ -f "$f" ] || continue
  out="$(dangling "$f")"
  if [ -n "$out" ]; then
    found=$((found + 1))
    printf '%s\n' "$out" | sed 's/^/        /'
  fi
done < <(git ls-files '*.md' '*.verdict' '*.sh' '*.py' '*.yaml' '*.toml')

if [ "$found" -eq 0 ]; then
  ok "every backticked repo path in a shipped file resolves"
else
  bad "$found file(s) cite a path that does not exist (listed above)"
fi

echo
printf 'dangling-doc-refs: %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

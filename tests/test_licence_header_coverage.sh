#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests that every tracked .py and .sh file has an SPDX header near the top,
# unless it sits under the exclusion LICENSING.md states.
# Section A checks the header test against fixtures; section B runs it on this repo.

set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

POLICY="LICENSING.md"
pass=0 fail=0
ok()  { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }

# Only the first five lines count, so a file that quotes the identifier further
# down does not pass.
has_spdx() { head -5 "$1" | grep -q 'SPDX-License-Identifier: Apache-2.0'; }

# excluded_paths — the excluded path prefixes, read from the backticked path
# after "one stated exclusion:" in the policy file.
excluded_paths() {
  grep -oE 'one stated exclusion:\*\* `[^`]+`' "$POLICY" | grep -oE '`[^`]+`' | tr -d '`'
}

echo "== A. fixtures"

FIX="$(mktemp -d)"; trap 'rm -rf "$FIX"' EXIT
printf '#!/usr/bin/env bash\n# SPDX-License-Identifier: Apache-2.0\necho hi\n' > "$FIX/good.sh"
printf '#!/usr/bin/env bash\necho hi\n'                                        > "$FIX/bare.sh"
printf '#\n#\n#\n#\n#\n# SPDX-License-Identifier: Apache-2.0\n'                > "$FIX/late.sh"

has_spdx "$FIX/good.sh"  && ok "a headed file is accepted"            || bad "headed file rejected"
has_spdx "$FIX/bare.sh"  && bad "an unheaded file was accepted"       || ok "an unheaded file is rejected"
has_spdx "$FIX/late.sh"  && bad "a header below line 5 was accepted"  || ok "a header buried below line 5 does not count"

echo "== B. this repo"

# B1 — the policy states at least one exclusion this test can read.
# A while-read loop instead of `mapfile`, which macOS bash 3.2 lacks.
EXCL=()
while IFS= read -r _p; do [ -n "$_p" ] && EXCL=("${EXCL[@]+"${EXCL[@]}"}" "$_p"); done < <(excluded_paths)
if [ "${#EXCL[@]}" -ge 1 ]; then
  ok "$POLICY states ${#EXCL[@]} exclusion path(s): ${EXCL[*]+${EXCL[*]}}"
elif tr '\n' ' ' < "$POLICY" | grep -qF "under version control, with no stated exclusion"; then
  ok "$POLICY states no exclusion, and the B2 sweep below reports any unheaded file"
else
  bad "$POLICY states neither an exclusion path nor the no-exclusion policy"
fi

# B2 — the policy covers non-source files.
if grep -qF "Files that are not source." "$POLICY"; then
  ok "$POLICY states a policy for non-source shipped files"
else
  bad "$POLICY has no 'Files that are not source.' section for .md/.yaml/.js/.toml/.html"
fi

is_excluded() {
  local p="$1" e
  for e in ${EXCL[@]+"${EXCL[@]}"}; do
    [ -n "$e" ] && case "$p" in "$e"*) return 0 ;; esac
  done
  return 1
}

missing=0 excluded=0 checked=0
while IFS= read -r f; do
  [ -f "$f" ] || continue
  if is_excluded "$f"; then excluded=$((excluded+1)); continue; fi
  checked=$((checked+1))
  has_spdx "$f" || { missing=$((missing+1)); printf '        %s\n' "$f"; }
done < <(git ls-files '*.py' '*.sh')

if [ "$missing" -eq 0 ]; then
  ok "all $checked non-excluded .py/.sh carry an SPDX header ($excluded excluded by policy)"
else
  bad "$missing of $checked non-excluded .py/.sh have no SPDX header (listed above)"
fi

# B3 — the exclusion still leaves more than 100 files checked.
if [ "$checked" -gt 100 ]; then
  ok "the exclusion is narrow: $checked files still checked"
else
  bad "only $checked files checked (expected more than 100)"
fi

echo
printf 'licence-header coverage: %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

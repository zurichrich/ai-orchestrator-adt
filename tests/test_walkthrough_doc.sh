#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Tests docs/e2e-install-walkthrough.md: every fenced bash block parses, and
# every ADT script it names exists in this repo.
set -uo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOC="$ADT_DIR/docs/e2e-install-walkthrough.md"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

[[ -f "$DOC" ]] || { echo "  FAIL walkthrough missing: $DOC"; exit 1; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

echo "[test] every fenced bash block parses"
/usr/bin/python3 - "$DOC" "$TMP" <<'PYEOF'
import os, re, sys
doc, out = sys.argv[1], sys.argv[2]
text = open(doc, encoding="utf-8").read()
blocks = re.findall(r"```bash\n(.*?)```", text, re.S)
if not blocks:
    sys.exit("no bash blocks found — the walkthrough has no runnable commands")
for i, b in enumerate(blocks):
    open(os.path.join(out, "block-%02d.sh" % i), "w").write(b)
print(len(blocks))
PYEOF
n=0; bad=0
for b in "$TMP"/block-*.sh; do
  n=$((n+1))
  bash -n "$b" 2>/dev/null || {
    bad=$((bad+1))
    fail "block $(basename "$b") is not valid bash: $(bash -n "$b" 2>&1 | head -1)"
  }
done
# Print the pass line only when every block parsed.
[[ $n -gt 0 && $bad -eq 0 ]] && pass "$n fenced bash block(s) parse"

echo "[test] every ADT script the walkthrough names exists"
missing=0
while IFS= read -r rel; do
  [[ -e "$ADT_DIR/$rel" ]] || { fail "walkthrough names ~/agent-dev-team/$rel — not in this repo"; missing=1; }
done < <(grep -oE '~/agent-dev-team/[A-Za-z0-9_./-]+' "$DOC" \
          | sed 's|~/agent-dev-team/||' | sort -u)
[[ $missing -eq 0 ]] && pass "all named ADT scripts exist"

if [[ $FAILS -eq 0 ]]; then
  echo; echo "All walkthrough-doc tests passed."
else
  echo; echo "$FAILS test(s) FAILED."; exit 1
fi

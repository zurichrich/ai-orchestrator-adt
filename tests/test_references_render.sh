#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Checks that commands_doc_src reaches the board:
#   1. _write_adt_config writes commands_doc_src into .adt/config.yaml when set,
#      and load_config falls back to the per-user yaml when it is not there.
#   2. A render with a references doc outside the project inlines the catalogue
#      and the docs it links as panels in kanban.html.
set -euo pipefail

ADT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PROJ="$TMP/proj"; mkdir -p "$PROJ"
# A references doc outside the project, at an absolute path, as in a separate ADT clone.
DOCS="$TMP/adt-clone/docs"; mkdir -p "$DOCS"
# The catalogue links a real doc in the clone's commands/, so there is a panel to inline.
mkdir -p "$TMP/adt-clone/commands"
printf '# adt-build\n\nThe building lane.\n' > "$TMP/adt-clone/commands/plan.md"
printf '# References\n\n- /adt-build — build it\n\nSee [plan](../../agent-dev-team/commands/plan.md).\n' > "$DOCS/references.md"
REFDOC="$DOCS/references.md"

# ── 1. _write_adt_config emits commands_doc_src into .adt/config.yaml ────────
echo "[test] _write_adt_config writes commands_doc_src into .adt/config.yaml"
( source "$ADT_DIR/lib/github-bootstrap.sh"
  _write_adt_config "$PROJ" "me/repo" "me" "TIX" ".adt/backlog" \
                    "7" "proj" "$TMP/cache" "$REFDOC" "main" ideas planned done
) >/dev/null 2>&1
CFG="$PROJ/.adt/config.yaml"
grep -q "^commands_doc_src: $REFDOC\$" "$CFG" && pass "commands_doc_src in .adt/config.yaml" || fail "commands_doc_src NOT in .adt/config.yaml"
# load_config is what the renderer reads.
got="$(python3 -c "import sys;sys.path.insert(0,'$ADT_DIR/tools');import adt_sync;print(adt_sync.load_config('$PROJ').get('commands_doc_src',''))")"
[[ "$got" == "$REFDOC" ]] && pass "load_config surfaces commands_doc_src" || fail "load_config got: $got"

# An empty value leaves the key out.
PROJ2="$TMP/proj2"; mkdir -p "$PROJ2"
( source "$ADT_DIR/lib/github-bootstrap.sh"
  _write_adt_config "$PROJ2" "me/repo" "me" "TIX" ".adt/backlog" \
                    "7" "proj2" "$TMP/cache" "" "main" ideas done
) >/dev/null 2>&1
grep -q '^commands_doc_src:' "$PROJ2/.adt/config.yaml" && fail "empty commands_doc_src emitted (should be absent)" || pass "empty commands_doc_src omitted"

# ── 1b. load_config falls back to the per-user yaml when .adt/config.yaml
#        has no commands_doc_src ─────────────────────────────────────────────
echo "[test] load_config falls back to per-user yaml for commands_doc_src"
HOME_DIR="$TMP/home"; mkdir -p "$HOME_DIR/.adt/projects"
printf 'name: proj3\nkanban:\n  id_prefix: TIX\n  commands_doc_src: %s\n' "$REFDOC" \
  > "$HOME_DIR/.adt/projects/proj3.yaml"
PROJ3="$TMP/proj3"; mkdir -p "$PROJ3/.adt"
# .adt/config.yaml without commands_doc_src.
printf 'project: proj3\nrepo: me/proj3\nstages:\n  - name: ideas\n    label: stage:ideas\n' \
  > "$PROJ3/.adt/config.yaml"
got3="$(HOME="$HOME_DIR" python3 -c "import sys;sys.path.insert(0,'$ADT_DIR/tools');import adt_sync;print(adt_sync.load_config('$PROJ3').get('commands_doc_src',''))")"
[[ "$got3" == "$REFDOC" ]] && pass "fallback to per-user yaml works" || fail "fallback got: $got3"

# ── 2. Render with that doc produces the references link ─────────────────────
echo "[test] render inlines the references + doc panels into kanban.html"
BOARD="$TMP/board"; mkdir -p "$BOARD"
python3 - "$ADT_DIR" "$BOARD" "$REFDOC" <<'PY'
import sys
adt_dir, board, refdoc = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, adt_dir + "/tools")
import build_kanban
build_kanban.run(project_root=board, backlog_root="", id_prefix="TIX",
                 commands_doc_src=refdoc, issues_url="", board_url="")
PY
# The catalogue is inlined into kanban.html; no separate references.html is written.
[[ -f "$BOARD/references.html" ]] && fail "references.html still emitted (should be inlined)" || pass "no standalone references.html"
grep -q 'href="#references"' "$BOARD/kanban.html" && pass "kanban.html links the #references panel (📖)" || fail "kanban.html missing the #references link"
grep -q '<article class="ticket-detail" id="references"' "$BOARD/kanban.html" && pass "references panel inlined" || fail "references panel NOT inlined"
grep -q 'id="doc-commands-plan"' "$BOARD/kanban.html" && pass "doc-commands-plan panel inlined" || fail "doc-commands-plan panel missing"
grep -q 'href="#doc-commands-plan"' "$BOARD/kanban.html" && pass "catalogue links the doc-commands-plan panel" || fail "doc-commands-plan not linked"

echo
if [[ "$FAILS" -eq 0 ]]; then echo "All references-render tests passed."; else echo "$FAILS assertion(s) FAILED."; exit 1; fi

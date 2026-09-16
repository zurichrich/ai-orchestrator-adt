#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Shared fixture for the editor tab-title tests. It runs the real
# `adt-install.sh --no-github` against a throwaway project, with HOME and the
# editor config base pointed into a temp dir so the machine's own settings are
# never touched.
#
# Usage:  source tests/lib/editor-setting-fixture.sh
#         editor_fixture_init            # sets TMP, PROJ, EDITOR_HOME, ...
#         editor_seed_settings Code '{...}'
#         editor_run_install
#         editor_settings_path Code

editor_fixture_init() {
  TMP="$(mktemp -d)"
  PROJ="$TMP/proj"
  BIN="$TMP/bin"
  USERP="$TMP/userp"
  FAKE_HOME="$TMP/home"
  EDITOR_HOME="$TMP/editorhome"
  mkdir -p "$PROJ" "$BIN" "$USERP" "$FAKE_HOME" "$EDITOR_HOME"

  git -C "$PROJ" init -q
  git -C "$PROJ" config user.email t@t.t
  git -C "$PROJ" config user.name t
  git -C "$PROJ" commit -q --allow-empty -m init

  # Stub gh so preflight and repo detection pass with no network.
  cat > "$BIN/gh" <<'SH'
#!/usr/bin/env bash
case "$* " in
  *"auth status"*) echo "Token scopes: 'project'"; exit 0 ;;
  *"repo view"*"nameWithOwner"*) echo "me/proj"; exit 0 ;;
  *) exit 0 ;;
esac
SH
  chmod +x "$BIN/gh"

  cat > "$USERP/proj.yaml" <<YAML
name: proj
path: $PROJ
main_branch: main
github:
  repo: me/proj
  owner: me
kanban:
  id_prefix: TIX
YAML
}

# editor_settings_path <Code|Cursor> [macos|linux]
editor_settings_path() {
  local editor="$1" layout="${2:-macos}"
  case "$layout" in
    macos) printf '%s/Library/Application Support/%s/User/settings.json' "$EDITOR_HOME" "$editor" ;;
    linux) printf '%s/.config/%s/User/settings.json' "$EDITOR_HOME" "$editor" ;;
  esac
}

# editor_seed_settings <Code|Cursor> <json-text> [macos|linux]
editor_seed_settings() {
  local p; p="$(editor_settings_path "$1" "${3:-macos}")"
  mkdir -p "$(dirname "$p")"
  printf '%s' "$2" > "$p"
}

# Runs the REAL installer. Blank lines accept every prompt default.
# Output is captured to $TMP/install.log for assertions on what was reported.
editor_run_install() {
  ( cd "$PROJ" && printf '\n\n\n\n\n\ny\n' | \
    PATH="$BIN:$PATH" HOME="$FAKE_HOME" ADT_USER_PROJECTS="$USERP" \
    ADT_EDITOR_CONFIG_HOME="$EDITOR_HOME" \
    bash "$ADT_DIR/adt-install.sh" --no-github ) >"$TMP/install.log" 2>&1
  return 0
}

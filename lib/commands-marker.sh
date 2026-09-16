#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Provenance marker for per-project command COPIES (ADT-087).
#
# Commands install per-project as COPIES (not symlinks) so a project gets a
# stable snapshot — editing the ADT repo never changes an installed project's
# commands until it re-installs. The cost of "copy" is that uninstall can no
# longer tell an ADT-installed command from a project-authored `adt-*.md` by its
# symlink target (there is none). So every ADT-copied command carries a marker
# in its YAML frontmatter; install writes it, uninstall reads it. One file owns
# the definition so the two sides can never drift.
#
# Why a frontmatter key and not a leading comment: command files start with `---`
# on line 1, so a comment before it would break frontmatter parsing. Claude Code
# ignores unknown frontmatter keys (verified against code.claude.com/docs skills
# frontmatter reference), so an extra `adt_managed:` key is inert to the harness.
#
# Sourced by install-defaults.sh (writer) and uninstall.sh (reader).

ADT_COMMAND_MARKER_KEY="adt_managed"

# adt_install_command <src.md> <dest.md>
# Copy an ADT command playbook to <dest>, injecting the provenance marker as a
# frontmatter key. Idempotent: a re-copy overwrites, so the marker is written
# once regardless of how many times install runs.
adt_install_command() {
  local src="$1" dest="$2"
  # Insert `adt_managed: ADT-087` right after the opening `---`. If the source has
  # NO leading `---` (a malformed/future command), synthesise a minimal
  # frontmatter carrying the marker rather than failing — so every copy is marked
  # and installable regardless of source shape (a bare `exit` here would abort the
  # install under the caller's `set -e`).
  awk -v key="$ADT_COMMAND_MARKER_KEY" '
    NR==1 && $0=="---" { print; print key ": ADT-087"; next }
    NR==1             { print "---"; print key ": ADT-087"; print "---"; print "" }
    { print }
  ' "$src" > "$dest"
}

# adt_is_managed_command <file.md>
# True (exit 0) iff <file> carries the ADT provenance marker in its frontmatter.
# Scoped to the frontmatter block (between the first two `---`) so a stray
# "adt_managed" in a command's prose body can never be mistaken for the marker.
adt_is_managed_command() {
  local f="$1"
  [[ -f "$f" ]] || return 1
  awk -v key="$ADT_COMMAND_MARKER_KEY" '
    NR==1 && $0!="---" { exit 1 }          # no frontmatter → not managed
    NR>1 && $0=="---" { exit 1 }            # reached end of frontmatter, no marker
    NR>1 && $0 ~ "^" key ": " { found=1; exit 0 }
    END { exit (found ? 0 : 1) }
  ' "$f"
}

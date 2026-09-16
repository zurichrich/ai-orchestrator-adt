#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Sourced by the ADT-384 shell tests (test_adt_layer.sh, test_install_join.sh).
# Needs SRC (this checkout) and TMP (a scratch dir) set by the caller.
#
# Isolates git config and HOME, so a commit here cannot pick up the user's hooks
# or signing, then builds a scratch "ADT clone": a copy of this tree (tracked
# and untracked files, not ignored ones, so the work under test is included)
# committed as A. Tests add their own commits on top and move with adt_at.
export GIT_CONFIG_GLOBAL="$TMP/gitconfig" GIT_CONFIG_NOSYSTEM=1
git config --global user.name t; git config --global user.email t@t.t
git config --global init.defaultBranch main
export HOME="$TMP/home"; mkdir -p "$HOME"

ADT="$TMP/adt"; mkdir -p "$ADT"
( cd "$SRC" && git ls-files -co --exclude-standard -z \
    | while IFS= read -r -d '' f; do [[ -e "$f" ]] && printf '%s\0' "$f"; done \
    | tar -cf - --null -T - ) | tar -xf - -C "$ADT"
# A fixture version, not this checkout's. An assertion that reads the same
# VERSION the installer reads agrees with itself whatever either says.
FIXTURE_VERSION="7.7.7"; export FIXTURE_VERSION
printf '%s\n' "$FIXTURE_VERSION" > "$ADT/VERSION"
git -C "$ADT" init -q && git -C "$ADT" add -A && git -C "$ADT" commit -q -m A
A="$(git -C "$ADT" rev-parse HEAD)"
adt_at() { git -C "$ADT" checkout -q "$1"; }

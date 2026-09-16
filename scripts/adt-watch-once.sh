#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# One reconcile+render pass of an ADT project's backlog sync (cache <-> Issues).
# Designed to be invoked every N seconds by a launchd/cron agent so a crashed or
# hung pass is simply replaced on the next interval (vs. a resident loop).
#
# REPO is derived from this script's own location (its parent of parent = the
# ADT checkout), so the file is portable — no machine-specific path. The backlog
# repo, cache path, and id prefix all come from the project config that
# adt_watch.py reads; nothing project-specific is hardcoded here.
#
# No secrets — only `gh` + local files. Run it in the user security session so
# the gh keychain credential resolves.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$REPO/tools/adt_watch.py" --root "$REPO" --once

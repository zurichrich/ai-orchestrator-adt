#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Cut an ADT release: tag, publish, attach the advisory manifest.
#
# Run AFTER the version bump has merged to main — a tag must point at a commit
# that actually contains the VERSION it names. Idempotent: re-running for an
# existing tag/release updates the asset rather than erroring.
#
# REST/core only (`gh api`), never the `gh release`/`gh pr` porcelain, which
# bills the separate GraphQL pool (ADT-109, CLAUDE.md rule 5).
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"

[[ -f VERSION ]] || { echo "no VERSION file at repo root" >&2; exit 1; }
ver="$(tr -d '[:space:]' < VERSION)"
[[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "VERSION '$ver' is not semver" >&2; exit 1; }
tag="v$ver"

# Derive the repo from the git remote, NOT from .adt/config.yaml: that file is
# gitignored and per-machine, so a clean clone has none and the script would only
# ever work on one laptop. `|| true` is load-bearing — under `set -o pipefail` a
# non-matching pipeline kills the assignment before the check below can report,
# which is how this failed silently at rc=2 with no diagnostic.
origin="$(git remote get-url origin 2>/dev/null || true)"
repo="$(printf '%s' "$origin" | sed -E 's#^.*github\.com[:/]##; s#\.git$##' || true)"
[[ "$repo" == */* ]] || {
  echo "cannot derive owner/repo from origin remote ('${origin:-unset}')" >&2
  exit 1
}

# The manifest must advertise the version being cut, or the advisory lies.
manifest_ver="$(python3 -c 'import json;print(json.load(open("release/advisory.json"))["current"])')"
[[ "$manifest_ver" == "$ver" ]] || {
  echo "release/advisory.json says current=$manifest_ver but VERSION says $ver" >&2
  echo "Fix the manifest before cutting — an advisory that names the wrong" >&2
  echo "version is worse than none." >&2
  exit 1
}

if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
  echo "tag $tag exists — reusing"
else
  git tag -a "$tag" -m "ADT $ver"
  git push origin "$tag"
fi

# gh prints the 404 body to STDOUT, so `|| true` alone captures the error JSON
# as the id and every later call builds a URL out of it. Require a numeric id;
# anything else means "no release yet". An error must never read as data.
id="$(gh api "repos/$repo/releases/tags/$tag" --jq .id 2>/dev/null || true)"
[[ "$id" =~ ^[0-9]+$ ]] || id=""
if [[ -z "$id" ]]; then
  id="$(gh api -X POST "repos/$repo/releases" \
        -f tag_name="$tag" -f name="ADT $ver" \
        -F draft=false -F prerelease=false --jq .id)"
  echo "created release $tag (id $id)"
else
  echo "release $tag exists (id $id) — updating asset"
  old="$(gh api "repos/$repo/releases/$id/assets" --jq '.[] | select(.name=="advisory.json") | .id' || true)"
  [[ -n "$old" ]] && gh api -X DELETE "repos/$repo/releases/assets/$old"
fi

gh api -X POST "https://uploads.github.com/repos/$repo/releases/$id/assets?name=advisory.json" \
  -H "Content-Type: application/json" --input release/advisory.json --jq .browser_download_url

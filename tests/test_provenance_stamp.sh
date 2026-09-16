#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Checks the install's provenance stamp: bundle_version in the manifest, a
# bundle header in every installed file, matching hashes, and a manifest shape
# uninstall can read.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
fail=0
ok()   { echo "  ok: $1"; }
bad()  { echo "  FAIL: $1"; fail=1; }

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/proj/development-team" "$tmp/proj/.claude"

# Run as a script, not sourced: install-defaults.sh calls `exit`, which would end this test.
bash "$PWD/lib/install-defaults.sh" "$PWD" "$tmp/proj" >/dev/null 2>&1 || true

mf="$tmp/proj/.claude/.adt-manifest.json"
if [[ -f "$mf" ]]; then
  # 1. manifest half
  if python3 -c "import json,sys; m=json.load(open('$mf')); sys.exit(0 if m.get('bundle_version') else 1)"; then
    ok "manifest carries bundle_version"
  else bad "manifest has no bundle_version"; fi

  # 2. every stampable installed file has the bundle header
  unstamped="$(python3 -c "
import json,os
m=json.load(open('$mf')); out=[]
for e in m['files']:
    p=os.path.join('$tmp/proj/.claude',e['path'])
    if os.path.splitext(p)[1] not in ('.md','.sh','.py','.yml','.yaml'): continue
    try: t=open(p,errors='replace').read()
    except OSError: continue
    if 'adt-bundle: v' not in t: out.append(e['path'])
print(' '.join(out))")"
  if [[ -z "$unstamped" ]]; then
    ok "every stampable installed file carries the bundle header"
  else
    bad "unstamped installed files: $unstamped"
  fi

  # 3. the recorded sha256 matches the stamped file on disk
  p="$(python3 -c "
import json;m=json.load(open('$mf'));e=m['files'][0];print(e['path'],e['sha256'])")"
  set -- $p
  actual="$(shasum -a 256 "$tmp/proj/.claude/$1" | cut -d' ' -f1)"
  if [[ "$actual" == "$2" ]]; then ok "recorded sha matches the stamped file"
  else bad "sha mismatch: manifest says $2, disk has $actual"; fi

  # 4. the manifest is schema 2 with {path, sha256} entries, as uninstall expects
  if python3 -c "
import json;m=json.load(open('$mf'))
assert all('path' in e and 'sha256' in e for e in m['files'])
assert m.get('schema')==2"; then ok "uninstall's {path,sha256} contract intact at schema 2"
  else bad "manifest shape broke uninstall's contract"; fi
else
  bad "install wrote no manifest at $mf"
fi

(( fail )) && { echo "PROVENANCE STAMP: FAIL"; exit 1; }
echo "PROVENANCE STAMP: PASS"

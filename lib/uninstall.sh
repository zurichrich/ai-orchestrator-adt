#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Uninstall ADT from one project. The exact inverse of install for the
# REGENERABLE bits; a strict no-op for everything durable. Install→uninstall→
# install must lose nothing — the local ticket cache rebuilds from Issues via
# `adt_sync.py --pull`, and the durable backlog (GitHub Issues / labels / board)
# is never touched.
#
# On a repo whose ADT layer is committed on origin (ADT-384), the TRACKED items
# below (.claude/ copies, settings.json hooks, the CLAUDE.md and .gitignore
# blocks, .claude/adt-project.yaml) belong to every machine: a plain uninstall
# leaves them alone, and --everyone writes their removal to a chore/adt-uninstall
# branch instead. See uninstall_tracked_layer.
#
# What it removes (regenerable):
#   - ADT symlinks in <project>/.claude/{rules,skills,agents,hooks}/  (symlinks
#     into the ADT repo ONLY — never a real file the project authored)
#   - the ADT hook entries merged into <project>/.claude/settings.json
#   - the in-project config <project>/.adt/config.yaml (ADT authors all of it)
#   - the entire <project>/.adt/ folder — ADT-generated working state. Your
#     docs/decisions.md and docs/retros/ are tracked in git and untouched.
#     (runtime state, BACKLOG.md pointer, rendered kanban.html, decisions.md,
#     retros). The token ledger inside it is archived to an Issue first (below).
#
# What it PRESERVES, not deletes (ADT-066 defect 1):
#   - the per-user config ~/.adt/projects/<name>.yaml → backed up to <name>.yaml.bak.
#     It carries hand-authored, non-inferable fields (security, notifications,
#     deploy, sync_docs, kanban.stage_tools,
#     commands_doc_src, …) that ADT does NOT author and CANNOT regenerate from
#     Issues or git. Deleting it made the "lose nothing" promise above false in
#     practice (hit live on a consumer). Moving it to .bak leaves the slate visibly
#     clean (no live config → setup won't re-adopt the project) while the authored
#     content survives for the next install to deep-merge from.
#
# What it does with the token ledger (per request):
#   - archives .adt/state/cost-ledger.log (+ the pre-rename
#     token-usage.log and the older dotfile path) into
#     a single find-or-updated GitHub issue, THEN deletes the local ledger +
#     cursor dir. Empty/absent ledger → no issue, pure no-op.
#
# What it NEVER touches (the durable backlog):
#   - GitHub Issues, labels, the Projects board
#   - the ticket cache ~/.adt/<name>/cache/**
# (Reinstall rebuilds .adt/ + the config from these.)
#
# Commands (ADT-087): installed per-project as COPIES under
# <project>/.claude/commands/adt-*.md (carrying an `adt_managed` marker), removed
# here by _remove_adt_commands. The old GLOBAL ~/.claude/commands/adt-*.md links
# are a legacy-model leftover, cleaned only by `setup.sh --uninstall` (repo-wide),
# not per-project.
#
# Usage: uninstall_project <ADT_DIR> <project_name> <project_path> [--no-github] [--everyone]

set -euo pipefail

# Shared command-provenance marker (ADT-087): the reader half — the writer is in
# install-defaults.sh. Sourced from this script's own dir so it resolves whether
# uninstall is run standalone or sourced.
_uninstall_libdir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=commands-marker.sh
source "$_uninstall_libdir/commands-marker.sh"

# Archive the token ledger into a find-or-updated GitHub issue, then delete it.
# Args: <repo> <ledger_root>  (ledger_root = the code checkout that owns it)
# Safe + idempotent: no rows → no-op; one issue per repo, updated not duplicated.
_archive_telemetry() {
  # ADT-301 sub-steps 4a-4c. Archive the machine-local telemetry that nothing
  # regenerates, then delete only what was archived.
  #
  # Four files, not one. The cost ledger had an exit; usage.log, surface-log.tsv
  # and traffic-log.tsv had none and were deleted outright, which is why the
  # 2026-09-09 uninstall on a consumer project needed a manual rescue sweep.
  #
  # adt-watch.log is deliberately NOT archived: ADT-119 bounds it at 1 MiB with a
  # single rotation and it is operational noise, not a record. That is a recorded
  # decision (docs/decisions.md), not a silent deletion.
  #
  # Three rules this obeys, all of them bought by a finding:
  #   1. VISIBILITY. traffic-log.tsv is GitHub's own clone/view data, which GitHub
  #      serves only to collaborators with push access. Posting it into an Issue
  #      on a PUBLIC repo would route around a permission GitHub enforces, so a
  #      public target refuses the Issue path and writes a local gzip instead.
  #   2. COMMENTS, not one body. The previous version pasted the whole ledger into
  #      an issue body and, on re-archive, re-posted that body plus the new block.
  #      A 234 KB ledger against a 65,536-character body limit simply fails, and
  #      re-posting is quadratic. Each chunk is now its own comment.
  #   3. REST only. The gh issue porcelain (list/create/edit/comment) sits on the
  #      GraphQL pool, which CLAUDE.md rule 5 bans in lib/.
  local repo="$1" root="$2"
  local state="$root/.adt/state"

  local -a names=("cost-ledger.log" "usage.log" "surface-log.tsv" "traffic-log.tsv")
  local -a present=()
  local f n
  for n in "${names[@]}"; do
    f="$state/$n"; [[ -s "$f" ]] && present+=("$f")
  done
  # Pre-rename ledger names, still archived if a project never migrated.
  for f in "$state/token-usage.log"; do
    [[ -s "$f" ]] && present+=("$f")
  done

  if [[ ${#present[@]} -eq 0 ]]; then
    echo "  [uninstall] no telemetry to archive (skipping)"
    rm -rf "$state/token-cursor" 2>/dev/null || true
    return 0
  fi

  local private
  private="$(gh api "repos/$repo" --jq .private 2>/dev/null || echo "")"
  if [[ "$private" != "true" ]]; then
    # Unknown or public: both refuse the Issue. Unknown is treated as public
    # because guessing wrong in that direction publishes collaborator-only data.
    local dest="$root/docs/history/telemetry"
    mkdir -p "$dest" 2>/dev/null || true
    local ok=true
    for f in "${present[@]}"; do
      gzip -c "$f" > "$dest/$(basename "$f").gz" 2>/dev/null || ok=false
    done
    if [[ "$ok" == true ]]; then
      echo "  [uninstall] repo is not private (visibility='${private:-unknown}') — archived ${#present[@]} telemetry file(s) to docs/history/telemetry/ instead of a public Issue" >&2
      for f in "${present[@]}"; do rm -f "$f"; done
      return 0
    fi
    echo "  [uninstall] WARN: could not write the local telemetry archive — KEEPING the files" >&2
    return 1
  fi

  # The archive carries `adt:archive` so the sync never turns it into a ticket
  # (ADT-354). Without the label a reinstall's pull rebuilt a cache file for it
  # and the push put it on the board.
  local title="ADT telemetry archive" label="adt:archive"
  _archive_label() {  # creating a label that exists returns 422, which is fine
    gh api "repos/$repo/labels" -f name="$label" -f color=ededed \
      -f description="ADT telemetry archive, not a ticket" >/dev/null 2>&1 || true
  }
  local num
  # By label first: the title search below reads only the newest 100 Issues, so
  # on a busy repo it misses an older archive and files a second one.
  num="$(gh api "repos/$repo/issues?labels=$label&state=all&per_page=1" \
           --jq '.[0].number // empty' 2>/dev/null || echo "")"
  if [[ -z "$num" ]]; then
    num="$(gh api "repos/$repo/issues?state=all&per_page=100" \
             --jq "[.[] | select(.title == \"$title\")] | first | .number // empty" 2>/dev/null || echo "")"
    # An archive filed before the label existed: label it now.
    if [[ -n "$num" ]]; then
      _archive_label
      gh api -X POST "repos/$repo/issues/$num/labels" -f "labels[]=$label" >/dev/null 2>&1 \
        || echo "  [uninstall] WARN: could not label archive issue #$num as $label" >&2
    fi
  fi
  if [[ -z "$num" ]]; then
    _archive_label
    num="$(gh api -X POST "repos/$repo/issues" -f title="$title" -f "labels[]=$label" \
             -f body="Append-only archive of machine-local ADT telemetry, written by uninstall. Not part of the active backlog. Each file arrives as one or more comments." \
             --jq '.number // empty' 2>/dev/null || echo "")"
    [[ -n "$num" ]] || {
      echo "  [uninstall] WARN: could not create the archive issue — KEEPING the telemetry" >&2; return 1; }
  fi

  # 60000 < GitHub's 65536-character limit, leaving room for the fence + header.
  local archived=0
  for f in "${present[@]}"; do
    local base part total rc=0
    base="$(basename "$f")"
    local tmpd; tmpd="$(mktemp -d)"
    split -b 60000 "$f" "$tmpd/part." 2>/dev/null || { rm -rf "$tmpd"; echo "  [uninstall] WARN: could not split $base — KEEPING it" >&2; continue; }
    total=$(find "$tmpd" -name 'part.*' | wc -l | tr -d ' ')
    local i=0
    for part in "$tmpd"/part.*; do
      i=$((i + 1))
      { printf '### %s (chunk %d/%d)\n\n```\n' "$base" "$i" "$total"; cat "$part"; printf '\n```\n'; } > "$tmpd/body.md"
      gh api -X POST "repos/$repo/issues/$num/comments" -F body=@"$tmpd/body.md" >/dev/null 2>&1 || rc=1
    done
    rm -rf "$tmpd"
    if [[ $rc -eq 0 ]]; then
      rm -f "$f"; archived=$((archived + 1))
      echo "  [uninstall] archived $base ($total comment(s)) → issue #$num"
    else
      echo "  [uninstall] WARN: $base did not archive cleanly — KEEPING it" >&2
    fi
  done

  rm -rf "$state/token-cursor" 2>/dev/null || true
  # ANY file that did not archive is a failure. Returning 0 on partial success
  # let the caller delete a folder still holding the file that failed.
  [[ $archived -eq ${#present[@]} ]] || return 1
  return 0
}

# True when any archivable telemetry is still on disk, in either layout. The old
# recheck named two files in one location, so a surviving usage.log or
# surface-log.tsv read as "nothing left" and the folder was removed.
_telemetry_remains() {
  local root="$1" d n
  for d in "$root/.adt/state"; do
    for n in cost-ledger.log usage.log surface-log.tsv traffic-log.tsv token-usage.log; do
      [[ -s "$d/$n" ]] && return 0
    done
  done
  return 1
}

# Back-compat name for the single-ledger era.
_archive_token_ledger() { _archive_telemetry "$@"; }

# Remove every file the manifest records (ADT-94, the primary path). The
# .claude/.adt-manifest.json written by install lists {path, sha256} for each
# copied file. We delete exactly those paths — for ANY file type (rules, hooks,
# the adt_dod.py tool, skill-dir files, commands) — and then the manifest. A copy
# whose on-disk sha no longer matches the manifest = the user edited it: KEEP it
# and surface it (never delete a file the user made their own). Returns 0 if a
# manifest was found and consumed (so the caller skips the legacy fallback), 1 if
# there was no manifest (a pre-ADT-94 install → fall back to symlink/marker removal).
_remove_by_manifest() {
  local claude="$1"
  local mf="$claude/.adt-manifest.json"
  [[ -f "$mf" ]] || return 1
  # Emit "rel\tsha" per recorded file; delete or preserve each.
  /usr/bin/python3 - "$mf" <<'PY' | while IFS=$'\t' read -r rel sha; do
import json, sys
try:
    m = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
for e in m.get("files", []):
    print("%s\t%s" % (e.get("path", ""), e.get("sha256", "")))
PY
    [[ -z "$rel" ]] && continue
    local dest="$claude/$rel"
    if [[ ! -e "$dest" ]]; then
      continue   # already gone — nothing to do
    fi
    local cur; cur="$(shasum -a 256 "$dest" | awk '{print $1}')"
    if [[ "$cur" != "$sha" ]]; then
      echo "  [uninstall] left your edited copy of $rel (changed since install)"
      continue
    fi
    rm -f "$dest"
    echo "  [uninstall] removed $rel"
  done
  rm -f "$mf"
  # Tidy now-empty ADT dirs (harmless if a project keeps its own files there).
  local sub
  for sub in rules skills agents hooks tools commands; do
    [[ -d "$claude/$sub" ]] && find "$claude/$sub" -type d -empty -delete 2>/dev/null || true
  done
  echo "  [uninstall] removed by manifest; deleted .adt-manifest.json"
  return 0
}

# Remove only the ADT symlinks under <project>/.claude/<sub>/. A symlink counts
# as ADT's by its LINK TEXT, not by where it resolves (ADT-065). install-defaults.sh
# always writes a relative link to "$ADT_DIR/defaults/<sub>/<file>", so every ADT
# symlink's readlink text contains the segment "defaults/<sub>/" (e.g.
# "../../defaults/hooks/x.sh" for an in-repo submodule, or
# "../../../agent-dev-team/defaults/hooks/x.sh" for an external clone). Matching on
# that structural segment — rather than resolving the target and prefix-matching a
# single $ADT_DIR (the old check) — removes the symlink whether it points at the
# submodule, a *different* ADT checkout (external clone), or a now-missing target
# (dangling, where the old `cd` to the target failed → no match → silent miss).
# Real files (project-authored) are never touched (the `-L` guard). A symlink we
# choose to KEEP (link text isn't ADT-shaped) is surfaced, so uninstall is never
# silent about what it left behind.
# $1 (the old ADT_DIR) is accepted for call-site stability but no longer used for
# the match — the link-text shape is checkout-independent.
_remove_adt_symlinks() {
  local _adt_dir="$1" claude="$2" sub d link
  for sub in rules skills agents hooks; do
    d="$claude/$sub"
    [[ -d "$d" ]] || continue
    for f in "$d"/*; do
      [[ -L "$f" ]] || continue   # only symlinks; never touch real files
      # The link TEXT (not the resolved target) is the discriminator — survives a
      # dangling target and an external-clone target alike.
      link="$(readlink "$f")"
      if [[ "$link" == *"defaults/$sub/"* ]]; then
        rm -f "$f"
        echo "  [uninstall] removed symlink $sub/$(basename "$f")"
      else
        # Kept a symlink that isn't ADT-shaped — say so (no silent miss, ADT-065).
        echo "  [uninstall] left non-ADT symlink $sub/$(basename "$f") -> $link"
      fi
    done
    # Drop the dir if we emptied it (rmdir fails harmlessly if not empty).
    rmdir "$d" 2>/dev/null || true
  done
}

# Remove ADT-installed command COPIES from <project>/.claude/commands/ (ADT-087).
# Unlike rules/skills/agents/hooks (symlinks, matched by link text), commands are
# real-file copies — so we identify ours by the `adt_managed` frontmatter marker
# (adt_is_managed_command, from commands-marker.sh), NOT by filename. A project's
# own authored adt-*.md (no marker) is KEPT and surfaced — never silently deleted.
_remove_adt_commands() {
  local claude="$1" d f
  d="$claude/commands"
  [[ -d "$d" ]] || return 0
  for f in "$d"/adt-*.md; do
    [[ -e "$f" ]] || continue          # no glob match → skip the literal
    if adt_is_managed_command "$f"; then
      rm -f "$f"
      echo "  [uninstall] removed command $(basename "$f")"
    else
      # A real adt-*.md without our marker = the project authored it (no silent
      # miss, mirrors _remove_adt_symlinks's non-ADT branch).
      echo "  [uninstall] left non-ADT command $(basename "$f") (project-authored)"
    fi
  done
  # Drop the dir if we emptied it (harmless if the project keeps its own).
  rmdir "$d" 2>/dev/null || true
}

# Un-merge the ADT hook entries from settings.json, keeping everything else.
# Removes only commands that point into $ADT_DIR or the project's own
# .claude/hooks/ ADT scripts; prunes now-empty blocks and the empty "hooks" key.
_unmerge_hooks() {
  local adt_dir="$1" settings="$2"
  [[ -f "$settings" ]] || return 0
  /usr/bin/python3 - "$settings" "$adt_dir" <<'PY'
import json, sys, os
settings_path, adt_dir = sys.argv[1], sys.argv[2]
try:
    cur = json.load(open(settings_path))
except Exception:
    sys.exit(0)
hooks = cur.get("hooks")
if not isinstance(hooks, dict):
    sys.exit(0)

def _adt_script_names(adt_dir, settings_path):
    """Basenames of every hook script ADT owns in this project.

    Two sources, both manifests the installer already writes, so neither can
    drift from what install did:

      * `defaults/settings.hooks.json` — what install WIRED into settings.json.
      * `<project>/.claude/.adt-manifest.json` — what install COPIED into
        .claude/hooks/, which is exactly the set uninstall deletes. This covers
        a hook that ships but is not auto-wired: adt-deploy-guard.sh is a
        template a project fills in and wires itself, and its FILE still goes on
        uninstall, so its wiring has to go with it.

    A hardcoded tuple stood here and went out of date three times. The comment
    on it asked every future author to remember; adt-test-run-guard.sh,
    adt-subagent-cost.sh and adt-close-complete.sh were each added without it,
    and the last of those landed between ADT-342 being filed and being planned
    (ADT-342; ADT-114 before it). Deriving the names removes the need to
    remember.

    Deliberately NOT a glob on `adt-*.sh`. That keys on a naming convention
    rather than on provenance, so it would also strip a project's OWN
    adt-prefixed hook — a file ADT never installed and never deletes — leaving
    the project with a hook it still owns and no longer runs.
    """
    names = set()
    try:
        with open(os.path.join(adt_dir, "defaults", "settings.hooks.json")) as fh:
            for blocks in json.load(fh).get("hooks", {}).values():
                for block in blocks:
                    for h in block.get("hooks", []):
                        cmd = h.get("command")
                        if isinstance(cmd, str):
                            names.add(os.path.basename(cmd))
    except Exception:
        pass
    try:
        manifest = os.path.join(os.path.dirname(settings_path), ".adt-manifest.json")
        with open(manifest) as fh:
            for entry in json.load(fh).get("files", []):
                path = entry.get("path", "") if isinstance(entry, dict) else str(entry)
                if path.startswith("hooks/") and path.endswith(".sh"):
                    names.add(os.path.basename(path))
    except Exception:
        pass
    return names


# FROZEN legacy floor — do NOT add to this tuple (ADT-342).
#
# It is not the maintained list that used to live in is_adt(); it is the set of
# hooks that existed BEFORE install wrote .adt-manifest.json. A project from
# that era has neither manifest, and its ADT checkout may be long gone, so
# nothing can be derived — but it also cannot be wired to any hook added since,
# because those hooks did not exist when it was installed. So this list is
# complete for every install that can reach it, and stays complete without
# anyone updating it.
_LEGACY_ADT_SCRIPTS = ("adt-done-guard.sh", "adt-deferral-guard.sh",
                       "adt-phrase-linter.sh", "adt-usage-log.sh",
                       "adt-token-log.sh", "adt-terminal-title.sh")

adt_scripts = _adt_script_names(adt_dir, settings_path)
if not adt_scripts:
    adt_scripts = set(_LEGACY_ADT_SCRIPTS)
    print("  [uninstall] note: no settings.hooks.json or .adt-manifest.json to "
          "read — falling back to the pre-manifest hook list for %s"
          % settings_path, file=sys.stderr)


def is_adt(cmd):
    if not isinstance(cmd, str):
        return False
    return adt_dir in cmd or any(s in cmd for s in adt_scripts)

removed = 0
for event in list(hooks.keys()):
    blocks = hooks[event]
    if not isinstance(blocks, list):
        continue
    new_blocks = []
    for b in blocks:
        inner = b.get("hooks", []) if isinstance(b, dict) else []
        kept = [h for h in inner if not is_adt(h.get("command"))]
        removed += len(inner) - len(kept)
        if kept:
            b["hooks"] = kept
            new_blocks.append(b)
        # block with no hooks left is dropped
    if new_blocks:
        hooks[event] = new_blocks
    else:
        del hooks[event]

if not hooks:
    cur.pop("hooks", None)

# The install side merges an `env` block from defaults/settings.hooks.json
# (additively — `env.setdefault`, so a project's own value is never clobbered).
# Uninstall is the symmetric half and has to remove it again, or the project is
# left WORSE than before ADT: adt-terminal-title.sh is deleted with the rest of
# the hooks while CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 stays behind, and the
# project's terminals get no tab title at all — neither ADT's nor Claude's.
#
# Read the key names from the fragment rather than hardcoding them, so this stays
# correct when the fragment gains a key (the `adt_scripts` tuple above is the
# hardcoded-list mistake this deliberately does not repeat).
#
# Removal is by NAME ONLY where the value still matches what ADT ships. A project
# that pre-set the key before its first `setup.sh` kept its own value through the
# additive merge; that value is the project's, not ADT's, and uninstall leaves it.
env = cur.get("env")
if isinstance(env, dict):
    try:
        with open(os.path.join(adt_dir, "defaults", "settings.hooks.json")) as fh:
            shipped = json.load(fh).get("env", {})
    except Exception:
        shipped = {}
    for k, v in shipped.items():
        if env.get(k) == v:
            del env[k]
            removed += 1
    if not env:
        cur.pop("env", None)

json.dump(cur, open(settings_path, "w"), indent=2)
open(settings_path, "a").write("\n")
print(f"  [uninstall] removed {removed} ADT hook/env entr{'y' if removed==1 else 'ies'} from {settings_path}")
PY
}

# Strip the managed ADT:rules @-import block from the project's CLAUDE.md
# (ADT-068). The exact inverse of the block install-defaults.sh writes. We touch
# ONLY the marker-delimited block; all authored content is left byte-for-byte
# intact, so install→uninstall→install round-trips the CLAUDE.md losslessly. No
# block (or no CLAUDE.md) → pure no-op.
_remove_rules_import() {
  local claude_md="$1"
  [[ -f "$claude_md" ]] || return 0
  /usr/bin/python3 - "$claude_md" <<'PY'
import re, sys
claude_md = sys.argv[1]
start, end = "<!-- ADT:rules:start -->", "<!-- ADT:rules:end -->"
cur = open(claude_md).read()
# Match the block AND the separator newlines install inserted before it (the
# `\n*` prefix + optional trailing `\n`) — the EXACT inverse of the append in
# install-defaults.sh, so a CLAUDE.md that ended in one newline before install
# ends in one newline after uninstall (lossless roundtrip; the invariant the
# uninstall header promises). The markers are unique + ADT-authored, so the
# greedy `\n*` only ever reclaims the separator install owns, never authored
# text.
seg = re.compile(r"\n*" + re.escape(start) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
if not seg.search(cur):
    sys.exit(0)  # nothing to remove
new = seg.sub("", cur)
# Restore a single trailing newline if authored content remains (install's
# `base.rstrip("\n") + "\n\n"` had normalised to that; mirror it back).
if new.strip():
    new = new.rstrip("\n") + "\n"
open(claude_md, "w").write(new)
print("  [uninstall] removed ADT:rules @-import block from " + claude_md)
PY
}

# Strip the managed ADT:gitignore block from the project's .gitignore (ADT-174).
# The exact inverse of the block install-defaults.sh writes: only the
# marker-delimited segment is touched, so a .gitignore the project authored is
# left byte-for-byte intact. If the file holds NOTHING but our block, install
# created it — remove the file too, so install->uninstall is lossless for a
# project that never had one. No block (or no file) -> pure no-op.
_remove_gitignore_block() {
  local gi="$1"
  [[ -f "$gi" ]] || return 0
  /usr/bin/python3 - "$gi" <<'GITIGNORE_PY'
import os, re, sys
gi = sys.argv[1]
start, end = "# ADT:gitignore:start", "# ADT:gitignore:end"
cur = open(gi).read()
seg = re.compile(r"\n*" + re.escape(start) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
if not seg.search(cur):
    sys.exit(0)  # nothing to remove
new = seg.sub("", cur)
if new.strip():
    new = new.rstrip("\n") + "\n"
    open(gi, "w").write(new)
    print("  [uninstall] removed ADT:gitignore block from " + gi)
else:
    os.remove(gi)
    print("  [uninstall] removed " + gi + " (ADT created it; nothing else in it)")
GITIGNORE_PY
}

# The part of an uninstall that changes TRACKED files: the .claude/ copies, the
# hook entries in settings.json, the CLAUDE.md rules block, the .gitignore block
# and the shared .claude/adt-project.yaml. On a committed install these belong
# to every machine, so uninstall_project runs this on the working tree only when
# nothing is committed, and otherwise on a branch (--everyone) or not at all.
uninstall_tracked_layer() {  # <ADT_DIR> <ROOT>
  local adt_dir="$1" root="$2" claude="$2/.claude"
  # ADT-94, copy + manifest. Primary path: remove exactly what the manifest
  # recorded (any file type), preserving user-edited copies. Legacy fallback
  # (pre-ADT-94 install: no manifest): symlink-by-link-text + command-by-marker.
  if ! _remove_by_manifest "$claude"; then
    echo "  [uninstall] no manifest (pre-ADT-94 install) — using legacy symlink/marker removal"
    _remove_adt_symlinks "$adt_dir" "$claude"
    _remove_adt_commands "$claude"
  fi
  _unmerge_hooks "$adt_dir" "$claude/settings.json"
  _remove_rules_import "$root/CLAUDE.md"         # ADT-068
  _remove_gitignore_block "$root/.gitignore"     # ADT-174
  if [[ -f "$claude/adt-project.yaml" ]]; then    # ADT-384
    rm -f "$claude/adt-project.yaml"
    echo "  [uninstall] removed .claude/adt-project.yaml"
  fi
}

# Entry point.
uninstall_project() {
  local adt_dir="${1:?usage: uninstall_project <ADT_DIR> <name> <path> [--no-github] [--everyone]}"
  local name="${2:?need project name}"
  local path="${3:?need project path}"
  local do_github=true everyone=false arg
  for arg in "${@:4}"; do
    case "$arg" in
      --no-github) do_github=false ;;
      --everyone)  everyone=true ;;
    esac
  done

  local user_cfg="${ADT_USER_PROJECTS:-$HOME/.adt/projects}/$name.yaml"
  local proj_cfg="$path/.adt/config.yaml"

  echo "[uninstall] $name ($path)"

  # 0. The background board-sync watcher (launchd/systemd user agent). Local OS
  # state, independent of GitHub — removed on every uninstall. watcher.sh is a
  # sibling of this file; resolve it relative to here (not $adt_dir, whose layout
  # a test fixture may flatten). Tolerate absence so uninstall never hard-fails on
  # an older/partial layout.
  local _here; _here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "$_here/watcher.sh" ]]; then
    # shellcheck disable=SC1091
    source "$_here/watcher.sh"
    uninstall_watcher "$name" "$path"
  fi

  # 0b. One last telemetry report (ADT-391). Sent here, once the watcher has
  # stopped and while .adt/state/install-id still exists: step 5 deletes that
  # file, and without this report an uninstalled install reads the same as one
  # that turned telemetry off. The client sends nothing when telemetry is off or
  # no id exists, gives up after a 5s network timeout, and always exits 0.
  if [[ -f "$adt_dir/tools/adt_phone_home.py" ]]; then
    python3 "$adt_dir/tools/adt_phone_home.py" --uninstall-event "$path" || true
  fi

  # 1. Token ledger → issue, then delete (needs the backlog repo from config).
  # ledger_safe tracks whether the ledger is either archived or absent. If an
  # archive ATTEMPT fails, ledger_safe stays false and we must NOT delete the
  # .adt/ folder below (it still holds unarchived data).
  local ledger_safe=true
  if [[ "$do_github" == true ]]; then
    local repo=""
    [[ -f "$proj_cfg" ]] && repo=$(grep -E '^repo:' "$proj_cfg" | head -1 | cut -d: -f2- | tr -d ' ')
    [[ -z "$repo" && -f "$user_cfg" ]] && repo=$(yq -r '.github.repo // ""' "$user_cfg" 2>/dev/null || true)
    if [[ -n "$repo" ]]; then
      _archive_token_ledger "$repo" "$path" || ledger_safe=false
    else
      echo "  [uninstall] no backlog repo in config — skipping ledger archive (ledger left in place)"
      _telemetry_remains "$path" && ledger_safe=false
    fi
  else
    echo "  [uninstall] --no-github: token ledger left in place (not archived/deleted)"
    _telemetry_remains "$path" && ledger_safe=false
  fi

  # 2. The tracked layer (ADT-384). Asked of origin/<main>, before step 4
  # deletes the .adt/config.yaml that names the main branch. When the layer is
  # committed there it belongs to every machine that pulls the repo: a plain
  # uninstall removes only this machine, and --everyone proposes removing ADT
  # from the repo on a branch. Before ADT-384 this step edited the working tree
  # unconditionally, so one machine leaving removed ADT for everyone once it
  # was committed.
  local layer_state="none"
  if [[ -f "$_here/adt-layer.sh" ]]; then
    # shellcheck disable=SC1091
    source "$_here/adt-layer.sh"
    adt_layer_state "$adt_dir" "$path"
    layer_state="$ADT_LAYER_STATE"
  else
    echo "  [uninstall] WARN: lib/adt-layer.sh is missing next to uninstall.sh, so the committed layer cannot be checked; treating it as not committed" >&2
  fi
  case "$layer_state" in
    none|self)
      uninstall_tracked_layer "$adt_dir" "$path"
      [[ "$everyone" == true ]] && \
        echo "  [uninstall] --everyone: ADT is not committed on origin, so removing it here is already all of it."
      ;;
    *)
      local where; where="$(_adt_layer_github_repo "$path")"; where="${where:-this repo}"
      if [[ "$everyone" == true ]]; then
        if _adt_layer_on_branch "$path" "$ADT_LAYER_MAIN" "chore/adt-uninstall" "" \
             "chore(adt): remove ADT from this repo" \
             "Written by adt-install.sh --uninstall --everyone. Merging this removes the ADT playbooks, hooks and settings for every machine that pulls $ADT_LAYER_MAIN; each machine still runs adt-install.sh --uninstall to stop its own watcher (ADT-384)." \
             uninstall_tracked_layer "$adt_dir"; then
          if [[ -n "$ADT_LAYER_BRANCH" && "$ADT_LAYER_BRANCH_EXISTED" != true ]]; then
            echo "  [uninstall] wrote the removal of ADT from $where to branch $ADT_LAYER_BRANCH (not pushed). Your current branch and working tree are unchanged."
            echo "  [uninstall] merging it removes ADT for every machine that pulls $ADT_LAYER_MAIN:"
            _adt_layer_say_push "$path" "$ADT_LAYER_MAIN" "$ADT_LAYER_BRANCH" 2>&1
          fi
        else
          echo "  [uninstall] WARN: could not write the chore/adt-uninstall branch; ADT stays in the repo. This machine is still uninstalled." >&2
        fi
        if [[ "$do_github" == true && -n "${repo:-}" ]]; then
          python3 "$adt_dir/tools/adt_machines.py" --list --repo "$repo" --adt-dir "$adt_dir" || true
        elif [[ "$do_github" != true ]]; then
          echo "  [uninstall] --no-github: skipped the list of machines that still have to run adt-install.sh --uninstall."
        fi
      else
        echo "  [uninstall] ADT stays installed in $where for the other machines: its layer is committed on origin/$ADT_LAYER_MAIN, so only this machine was removed."
        echo "  [uninstall] To remove ADT from the repo for everyone, run: adt-install.sh --uninstall --everyone"
      fi
      ;;
  esac

  # 4a. The in-project config (.adt/config.yaml) is regenerable — ADT authors
  # all of it — so it's safe to delete.
  [[ -f "$proj_cfg" ]] && { rm -f "$proj_cfg"; rmdir "$path/.adt" 2>/dev/null || true; echo "  [uninstall] removed $proj_cfg"; }

  # 4b. The per-user config (ADT-066 defect 1): DO NOT DELETE — back it up.
  # It carries hand-authored fields ADT cannot regenerate (see header). Moving
  # it to <name>.yaml.bak keeps the slate clean (no live config → setup won't
  # re-adopt the project) while the authored content survives for reinstall to
  # merge from. A prior .bak is overwritten — the live config is the freshest
  # source of truth.
  if [[ -f "$user_cfg" ]]; then
    mv -f "$user_cfg" "$user_cfg.bak"
    echo "  [uninstall] backed up $user_cfg → $(basename "$user_cfg").bak (hand-authored fields preserved for reinstall)"
  fi

  # 5. The .adt/ folder — ALL of it. It holds only ADT-generated
  # working files (runtime state under .adt-state/, the BACKLOG.md pointer, the
  # rendered kanban.html, decisions.md, retros). Uninstall means uninstall: the
  # durable backlog is GitHub Issues + the cache (both untouched), so nothing
  # here needs to survive. The token ledger was already archived to an Issue in
  # step 1 before we delete it here.
  #
  # ADT-115 added two artifacts under .adt-state/ — token-usage.log.pre-ADT-115.bak
  # (the pre-backfill ledger) and cost-estimator.json. Both are removed by this
  # rm -rf, and neither needs its own archive step: the .bak is a strict SUBSET
  # of the archived ledger (same rows, fewer columns), and the estimator is
  # re-derivable from the rows themselves. Recorded so "not archived" reads as a
  # decision rather than an omission.
  if [[ -d "$path/.adt" ]]; then
    if [[ "$ledger_safe" == true ]]; then
      rm -rf "$path/.adt"
      echo "  [uninstall] removed $path/.adt/"
    else
      echo "  [uninstall] KEPT $path/.adt/ — its token ledger isn't archived yet (archive failed / no repo). Archive or remove it manually." >&2
    fi
  fi

  echo "[uninstall] done. KEPT: GitHub Issues + board + the ticket cache (~/.adt/$name/cache) — the durable backlog."
  echo "            Reinstall any time with ./adt-install.sh — it rebuilds everything else from there (cache via --pull)."
}

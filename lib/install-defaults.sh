#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Install the ADT "defaults" (the Karpathy+++ behavioural layer) into a
# project's .claude/. Idempotent. COPIES rules/skills/agents/hooks (+ the DoD
# grader tool) into the project as real files — a clean snapshot with NO live
# linkage back to the ADT repo (ADT-94, the copy + manifest model; supersedes
# ADT-090's symlink model). Records every copied path + a sha256 in
# .claude/.adt-manifest.json so update/uninstall act on exactly what was written
# (the pip RECORD / macOS .pkg BOM pattern). Also merges the hooks settings
# fragment into .claude/settings.json without clobbering existing keys.
#
# Usage: install-defaults.sh <ADT_DIR> <PROJECT_PATH>
set -euo pipefail

ADT_DIR="$1"
PROJECT_PATH="$2"
DEF="$ADT_DIR/defaults"
CLAUDE="$PROJECT_PATH/.claude"

mkdir -p "$CLAUDE/rules" "$CLAUDE/skills" "$CLAUDE/agents" "$CLAUDE/hooks" "$CLAUDE/tools"

# ── Manifest accumulator (ADT-94) ──────────────────────────────────────────
# Every copy records a "<.claude-relative-path>\t<sha256>" line here; emitted to
# .claude/.adt-manifest.json at the end. This is the provenance record uninstall
# removes by, and the checksum update/uninstall compare against to detect a
# user-edited copy (and preserve it rather than clobber).
MANIFEST_LINES=()
_sha256() { shasum -a 256 "$1" | awk '{print $1}'; }
_record() {  # _record <dest-abs> [<sha-override>] — add path + sha to the manifest
  # Normally records the file's CURRENT sha. For a preserved user-edited copy we
  # pass the ORIGINAL (pristine) sha so the entry stays permanently flagged as
  # drifted — otherwise update would re-baseline the edit as pristine and a later
  # uninstall would delete a file the user made their own.
  local dest="$1" sha="${2:-}" rel
  rel="${dest#"$CLAUDE"/}"
  [[ -z "$sha" ]] && sha="$(_sha256 "$dest")"
  MANIFEST_LINES+=("$rel"$'\t'"$sha")
}

copy() {  # copy <src> <dest> — install a real file (no symlink, ADT-94)
  local src="$1" dest="$2"
  # 0. ADT-306 — never write a file onto its own source. With
  #    ADT_DIR == PROJECT_PATH and a .claude/ entry symlinked back into
  #    defaults/, `dest` resolves THROUGH that link onto `src`: the `rm -f`
  #    below unlinks the source, the `cp` then fails with its own source gone,
  #    and `set -e` aborts the install part-written. Reproduced on 2e1b9d6,
  #    where it deleted defaults/skills/adt-diagnose/SKILL.md.
  #    The test is on the PAIR, not `[[ -L "$dest" ]]`: dest is a plain path
  #    A hard link also satisfies `-ef` while being SAFE to `rm -f` (src keeps
  #    its own directory entry, verified). The guard refuses it anyway: it fails
  #    CLOSED, no install has ever hard-linked into the source tree, and
  #    separating the two would mean walking dest's path for a symlinked
  #    component to buy nothing. Not mentioned in the refusal text, which should
  #    describe the cause an operator will actually have.
  #    whose PARENT is the link, so a symlink test on dest never fires. `-ef`
  #    compares device+inode after following links, which is exactly the claim.
  #    FIRST, ahead of every other guard: src == dest is never a valid copy,
  #    whatever the manifest says. The 'keeping project's own file' guard
  #    below returns before `rm -f` and so MASKS this whenever the file is
  #    unmanaged — the real 2e1b9d6 case only reached the delete because the
  #    file WAS in the manifest. Ordering it first removes that dependence.
  #    Fail fast and whole: a half-written .claude/ is worse than no run, and
  #    `lib/resync.sh` runs this in place by design, so it must hear about a
  #    symlink loudly rather than skip the file and look successful.
  #    The `! -L "$dest"` conjunct is load-bearing and was missing in the first
  #    version (found in QA). `-ef` is ALSO true when `dest` is itself a symlink
  #    pointing at `src` — which is the pre-ADT-94 install model the `rm -f`
  #    below exists to migrate, and which line 84's comment says is handled. A
  #    bare `-ef` refused that upgrade on a NORMAL install and left it
  #    incomplete. The two cases have different shapes, and that is what makes
  #    the conjunct exact: in the 2e1b9d6 case `dest` is a plain path whose
  #    PARENT is the link, so `-L "$dest"` is false and the guard still fires;
  #    in the upgrade case `dest` IS the link, so `rm -f` drops it as intended.
  if [[ "$src" -ef "$dest" && ! -L "$dest" ]]; then
    echo "  [defaults] REFUSING: destination is its own source — $(basename "$dest")" >&2
    echo "             src:  $src" >&2
    echo "             dest: $dest" >&2
    echo "             A PARENT DIRECTORY of this destination is a symlink into" >&2
    echo "             the source tree, so the destination resolves onto the file" >&2
    echo "             being copied. Replace that directory symlink with a real" >&2
    echo "             directory, then re-run." >&2
    exit 1
  fi
  # 1. Never clobber a project-authored file. A real file we never recorded =
  #    project-authored → keep. (A leftover symlink from a pre-ADT-94 install is
  #    ours: overwrite it with a real copy.)
  if [[ -e "$dest" && ! -L "$dest" ]] && ! _is_managed "$dest"; then
    echo "  [defaults] keeping project's own $(basename "$dest") (not ADT-managed)"
    return
  fi
  # 2. Don't clobber a user-EDITED ADT copy (sha drifted from the old manifest).
  #    Preserve it + warn, and re-record the ORIGINAL (pristine) sha — NOT the
  #    edited one — so the entry stays flagged as drifted: update won't revert it
  #    and a later uninstall keeps it (never deletes a file the user made theirs).
  if _user_edited "$dest"; then
    echo "  [defaults] keeping your edited copy of $(basename "$dest") (changed since install; re-run with the file removed to take the new ADT version)"
    _record "$dest" "$(_old_sha "${dest#"$CLAUDE"/}")"
    return
  fi
  rm -f "$dest"                      # drop any pre-ADT-94 symlink in place
  cp "$src" "$dest"
  _stamp "$dest"
  _record "$dest"
}

# ADT-170 A10 — stamp the bundle into each installed file, so an install's
# version is legible from the file itself and not only from the manifest beside
# it. TRAILING, not leading, and that is deliberate: most of these files are
# prompt text Claude Code reads every session, and the opening lines are the
# highest-value context in them. A provenance line does not belong there.
# Comment syntax follows the file type; anything unrecognised is left alone
# rather than corrupted. Runs BEFORE _record so the recorded sha256 matches what
# is actually on disk — otherwise every file reads as user-edited on next update.
_stamp() {
  local dest="$1" ver="" line=""
  [[ -f "$ADT_DIR/VERSION" ]] && ver="$(tr -d '[:space:]' < "$ADT_DIR/VERSION")"
  [[ -n "$ver" ]] || return 0
  case "$dest" in
    *.md)          line="<!-- adt-bundle: v$ver -->" ;;
    *.sh|*.py|*.yml|*.yaml) line="# adt-bundle: v$ver" ;;
    *.json)        return 0 ;;      # no comment syntax; the manifest covers it
    *)             return 0 ;;
  esac
  printf '\n%s\n' "$line" >> "$dest"
}

# Pre-ADT-94 → ADT-94 migration + user-edit guard, keyed off the OLD manifest
# read once at start. A dest is "managed" (safe to overwrite) if the old manifest
# listed it; if it lists it with a DIFFERENT sha than on disk, the user edited the
# copy — keep it (the default is preserve). Absent old manifest (fresh or
# pre-ADT-94 install) → copy()'s leftover-symlink branch handles the old model.
#
# Stored as a newline-delimited "<rel>\t<sha>" string (NOT a bash-4 associative
# array — the rest of ADT targets stock macOS bash 3.2; no `declare -A`).
_OLD_MANIFEST="$(
  mf="$CLAUDE/.adt-manifest.json"
  if [[ -f "$mf" ]]; then
    /usr/bin/python3 - "$mf" <<'PY'
import json, sys
try:
    m = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
for e in m.get("files", []):
    print("%s\t%s" % (e.get("path", ""), e.get("sha256", "")))
PY
  fi
  true   # never let the substitution exit nonzero (set -e would kill the assign)
)"
_old_sha() {  # _old_sha <rel> — echo the old manifest sha for a path, empty if absent
  printf '%s\n' "$_OLD_MANIFEST" | awk -F'\t' -v r="$1" '$1==r {print $2; exit}'
}
_is_managed() {  # _is_managed <dest-abs> — was this path written by a prior ADT install?
  local rel; rel="${1#"$CLAUDE"/}"
  printf '%s\n' "$_OLD_MANIFEST" | awk -F'\t' -v r="$rel" '$1==r {f=1} END{exit !f}'
}
_user_edited() {  # _user_edited <dest-abs> — in old manifest but sha changed on disk
  local dest="$1" rel old; rel="${dest#"$CLAUDE"/}"
  old="$(_old_sha "$rel")"
  [[ -n "$old" && -f "$dest" ]] || return 1
  [[ "$(_sha256 "$dest")" != "$old" ]]
}

# Rules (always-on behavioural rule) — COPY (ADT-94)
for f in "$DEF"/rules/*.md; do
  [[ -e "$f" ]] && copy "$f" "$CLAUDE/rules/$(basename "$f")"
done

# Make "loads every session" TRUE by construction (ADT-068). Symlinking a rule
# into .claude/rules/ does NOT load it — that directory is inert to the harness;
# the one native mechanism that pulls a file into context every session is an
# `@`-import from CLAUDE.md. So for every ALWAYS-ON rule (one with no `paths:`
# key — the rules that claim to load on every turn), ensure a managed
# `@.claude/rules/<file>` line sits inside a marker-delimited block in the
# project's CLAUDE.md. Path-scoped rules (WITH a `paths:` key) are deliberately
# excluded — they have no loader yet and must not be imported unconditionally.
#
# Idempotent: the block is keyed by the ADT:rules markers, rewritten in place on
# re-run (never appended twice). We only ever touch the marked block — authored
# CLAUDE.md content is left byte-for-byte intact. If the project has no CLAUDE.md
# we skip rather than author one the project didn't ask for. Uninstall strips the
# block (lib/uninstall.sh:_remove_rules_import), keeping install→uninstall
# lossless.
CLAUDEMD="$PROJECT_PATH/CLAUDE.md"
if [[ -f "$CLAUDEMD" ]]; then
  /usr/bin/python3 - "$CLAUDEMD" "$DEF/rules" <<'PY'
import os, re, sys
claude_md, rules_dir = sys.argv[1], sys.argv[2]

# The always-on rules: a *.md in defaults/rules/ whose frontmatter has no
# `paths:` key. (A path-scoped rule starts a line with `paths:`.)
always_on = []
for name in sorted(os.listdir(rules_dir)):
    if not name.endswith(".md"):
        continue
    text = open(os.path.join(rules_dir, name)).read()
    if not re.search(r'(?m)^paths:', text):
        always_on.append(name)

start, end = "<!-- ADT:rules:start -->", "<!-- ADT:rules:end -->"
if always_on:
    imports = "\n".join(f"@.claude/rules/{n}" for n in always_on)
    block = (f"{start}\n"
             "<!-- Managed by ADT (adt-068). Always-on behavioural rules, "
             "@-imported so they load every session. Do not edit by hand — "
             "install rewrites this block; uninstall removes it. -->\n"
             f"{imports}\n{end}")
else:
    block = None  # no always-on rules → ensure no stale block lingers

cur = open(claude_md).read()
# Match the block AND the separator newlines we inserted before it, so removal
# (here on re-tier-to-none, and in uninstall) restores the original bytes
# exactly. The leading `\n*` is greedy over the separator we own; it never eats
# authored text because the markers are unique and ADT-authored.
seg = re.compile(r"\n*" + re.escape(start) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
if seg.search(cur):
    # Strip the existing managed segment back to the original content first,
    # then (if we still have rules) re-append cleanly — so re-runs converge to
    # one canonical form regardless of the prior block's exact spacing.
    base = seg.sub("", cur)
else:
    base = cur

if block:
    # Append the block separated from authored content by exactly one blank
    # line, with a single trailing newline. The separator (`\n\n`) and trailing
    # `\n` are the bytes the remover (seg, above; uninstall's identical regex)
    # reclaims — so install→uninstall returns `base` unchanged for a CLAUDE.md
    # that ended in one newline (the canonical, POSIX text-file form every real
    # CLAUDE.md uses). NOTE: this normalises EOF whitespace — a file ending in a
    # trailing blank line or with no final newline comes back with exactly one
    # trailing newline. That's authored *content*-lossless (only EOF whitespace,
    # which git/formatters normalise anyway), not byte-lossless on malformed EOF.
    new = base.rstrip("\n") + "\n\n" + block + "\n" if base.strip() else block + "\n"
else:
    new = base

if new != cur:
    open(claude_md, "w").write(new)
    print(f"  [defaults] @-imported {len(always_on)} always-on rule(s) into {claude_md}")
else:
    print(f"  [defaults] CLAUDE.md rules block already current")
PY
else
  echo "  [defaults] no CLAUDE.md at $CLAUDEMD — skipping rules @-import (not authoring one)"
fi

# ── .gitignore: keep ADT's machine-local + generated paths out of git (ADT-174)
# The README has always described `.adt/config.yaml` and `.adt/` as
# "gitignored by ADT" (README:228-229, 315). Nothing shipped that: no install
# path touched .gitignore at all, so a fresh install left ~50 ADT files
# untracked and the first `git add -A` committed a 205 KB generated kanban.html
# that changes on every 60s sync, plus a config carrying an absolute
# home-directory path. Both existing consuming repos implement this policy by
# hand — it existed as a policy and was simply never installed.
#
# `.claude/` is deliberately NOT ignored: the commands and rules are exactly
# what a teammate should get from a clone.
#
# Same marker-keyed idiom as the ADT:rules block above — rewritten in place on
# re-run, never appended twice, authored content left byte-for-byte intact, and
# removed by uninstall (lib/uninstall.sh:_remove_gitignore_block). Unlike
# CLAUDE.md we DO create the file when absent: a missing .gitignore is exactly
# the case the defect bites, and uninstall removes a file it created.
/usr/bin/python3 - "$PROJECT_PATH/.gitignore" <<'GITIGNORE_PY'
import os, re, sys
path = sys.argv[1]
start, end = "# ADT:gitignore:start", "# ADT:gitignore:end"
block = (start + "\n"
         "# Managed by ADT (ADT-174). Machine-local + generated paths. Do not\n"
         "# edit by hand - install rewrites this block; uninstall removes it.\n"
         ".adt/\n"
         + end)

existed = os.path.exists(path)
cur = open(path).read() if existed else ""
seg = re.compile(r"\n*" + re.escape(start) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
# Rewrite the block WHERE IT IS; append only when there is no block yet.
#
# The previous version deleted the block (consuming the newlines around it) and
# re-appended it at the end. Two silent failures, both found by running this on
# ADT's own repo (ADT-205 close):
#   1. `\n*` ate the newline BEFORE the block, so the line above it was joined
#      to whatever followed -- `.wrangler/` + a comment became
#      `.wrangler/# ADT-205's committed run trees...`, one inert pattern. That
#      un-ignored the very directory ADT-170 added after it leaked an account id
#      and an email into a commit. A .gitignore rewriter that silently disarms a
#      secret-ignoring rule is worse than one that does nothing.
#   2. Moving the block to the end silently relocates anything the author put
#      after it. .gitignore is last-match-wins, so a negation deliberately
#      placed below the block (the only place it CAN go -- the block re-adds
#      `.adt/`) becomes inert without a word.
# In-place rewrite keeps the "install rewrites this block" contract and touches
# nothing else.
if seg.search(cur):
    # A block at the very top of the file gets no blank lines above it. The
    # separator is only for a block below the author's own lines; adding it at
    # the top made every reinstall of a block-only .gitignore a diff, which
    # would put noise in every ADT upgrade branch (ADT-384).
    new = seg.sub(lambda m: ("" if m.start() == 0 else "\n\n") + block + "\n", cur)
elif cur.strip():
    new = cur.rstrip("\n") + "\n\n" + block + "\n"
else:
    new = block + "\n"
if new != cur:
    open(path, "w").write(new)
    print("  [defaults] %s ADT block in .gitignore" % ("refreshed" if existed else "created"))
else:
    print("  [defaults] .gitignore ADT block already current")
GITIGNORE_PY

# Skills (each is a DIRECTORY with SKILL.md) — COPY the whole tree, recording
# each contained file in the manifest so uninstall removes every piece (ADT-94).
for d in "$DEF"/skills/*/; do
  [[ -d "$d" ]] || continue
  sname="$(basename "$d")"
  while IFS= read -r sf; do
    rel="${sf#"$d"}"                     # path within the skill dir
    dest="$CLAUDE/skills/$sname/$rel"
    mkdir -p "$(dirname "$dest")"
    copy "$sf" "$dest"
  done < <(find "$d" -type f)
done

# Agents — COPY the generic/templated ones (project-authored ones are preserved
# by copy()'s not-ADT-managed guard).
for f in "$DEF"/agents/*.md; do
  [[ -e "$f" ]] && copy "$f" "$CLAUDE/agents/$(basename "$f")"
done

# Hooks (shell scripts) — COPY + chmod the installed copy executable (ADT-94).
for f in "$DEF"/hooks/*.sh; do
  [[ -e "$f" ]] || continue
  dest="$CLAUDE/hooks/$(basename "$f")"
  copy "$f" "$dest"
  [[ -f "$dest" ]] && chmod +x "$dest" 2>/dev/null || true
done

# The DoD grader tool (ADT-94) — COPY adt_dod.py into the project so the copied
# hooks + the playbooks reach it by a LOCAL path (.claude/tools/adt_dod.py), with
# no symlink-walk back to the ADT repo. It is stdlib-only (no sibling imports),
# so a single-file copy is self-contained.
[[ -e "$ADT_DIR/tools/adt_dod.py" ]] && copy "$ADT_DIR/tools/adt_dod.py" "$CLAUDE/tools/adt_dod.py"
# adt_dod.py imports ticket_serializer at THREE call sites (ticket_track,
# _read_frontmatter_list, _write_frontmatter_list) and it was never installed
# beside it, so every one of them raised ModuleNotFoundError in an installed
# copy. ADT-306 found it via test_uninstall.sh once ADT-312 put `ticket_track`
# on the --gate path and turned a latent break into a crash on every consumer's
# gate. Pure stdlib (json, re), so it travels the same way adt_cost.py does.
[[ -e "$ADT_DIR/tools/ticket_serializer.py" ]] && copy "$ADT_DIR/tools/ticket_serializer.py" "$CLAUDE/tools/ticket_serializer.py"
# ADT-115: the pricer + its price table travel the same way, for the same
# reason — adt-token-sum.sh is a per-project COPY with no $ADT_DIR, so it
# resolves adt_cost.py at ../tools/ (installed) or ../../tools/ (source).
# pricing.json lands NEXT TO the pricer because that is where adt_cost looks
# first; a project may still override it with .adt/pricing.json or $ADT_PRICING.
[[ -e "$ADT_DIR/tools/adt_cost.py" ]] && copy "$ADT_DIR/tools/adt_cost.py" "$CLAUDE/tools/adt_cost.py"
[[ -e "$ADT_DIR/tools/adt_backfill_cost.py" ]] && copy "$ADT_DIR/tools/adt_backfill_cost.py" "$CLAUDE/tools/adt_backfill_cost.py"
[[ -e "$ADT_DIR/defaults/pricing.json" ]] && copy "$ADT_DIR/defaults/pricing.json" "$CLAUDE/tools/pricing.json"

# The plan-quality calibration record lands NEXT TO the grader for the same
# reason (ADT-126): adt_dod.py reads `gating: ENABLED` from it to decide whether
# --gate may refuse on a plan-quality verdict, and it looks beside itself first.
# Without this the record is unreachable from an installed copy, plan_gating_enabled()
# fails toward off, and the refusal is silently dead in every consuming project.
[[ -e "$ADT_DIR/docs/plan-quality-calibration.md" ]] && copy "$ADT_DIR/docs/plan-quality-calibration.md" "$CLAUDE/tools/plan-quality-calibration.md"

# Commands (ADT-087) — COPY per-project (NOT symlink, unlike the surfaces above).
# A copy is a stable snapshot: editing the ADT repo never changes an installed
# project's commands until it re-installs, and a project that never installed ADT
# sees no /adt-* commands (opt-in). The copy carries a provenance marker so
# uninstall can tell an ADT-installed command from one the project authored.
# (This replaces the former GLOBAL ~/.claude/commands symlink install — the one
# ADT surface that leaked across every project on the machine.)
source "$ADT_DIR/lib/commands-marker.sh"
mkdir -p "$CLAUDE/commands"
for f in "$ADT_DIR"/commands/*.md; do
  [[ -e "$f" ]] || continue
  dest="$CLAUDE/commands/adt-$(basename "$f")"
  # Never clobber a project's OWN authored adt-*.md (a real file without our
  # marker). A prior ADT copy (carries the marker) is refreshed.
  if [[ -e "$dest" ]] && ! adt_is_managed_command "$dest"; then
    echo "  [defaults] keeping project's own commands/$(basename "$dest") (not ADT-managed)"
    continue
  fi
  # Don't clobber a command the user EDITED — the same sha-drift guard copy()
  # applies to every other surface (see branch 2 there). Commands cannot simply
  # be routed through copy(): copy() installs with a plain `cp`, while commands
  # must go through adt_install_command, which injects the `adt_managed` marker
  # that _remove_adt_commands selects by on uninstall. So the guard is applied
  # here rather than inherited (ADT-174). Re-record the ORIGINAL (pristine) sha,
  # not the edited one, so the entry stays flagged as drifted — update won't
  # revert it and uninstall keeps it.
  if _user_edited "$dest"; then
    echo "  [defaults] keeping your edited copy of commands/$(basename "$dest") (changed since install; re-run with the file removed to take the new ADT version)"
    _record "$dest" "$(_old_sha "${dest#"$CLAUDE"/}")"
    continue
  fi
  adt_install_command "$f" "$dest"
  # Commands bypass copy(), so they bypass its _stamp too — that is how 16 of
  # 43 installed files shipped unstamped while the A10 test passed, because the
  # test checked one .md and the one it happened to pick came through copy().
  # Same BEFORE-_record ordering, for the same reason.
  _stamp "$dest"
  _record "$dest"   # ADT-94: commands join the manifest too (uninstall by manifest)
done

# Merge the hooks settings fragment into the project's settings.json.
SETTINGS="$CLAUDE/settings.json"
FRAGMENT="$DEF/settings.hooks.json"
if [[ -f "$FRAGMENT" ]]; then
  /usr/bin/python3 - "$SETTINGS" "$FRAGMENT" <<'PY'
import json, sys, os
settings_path, fragment_path = sys.argv[1], sys.argv[2]
frag = json.load(open(fragment_path))
frag.pop("_comment", None)
cur = {}
if os.path.exists(settings_path):
    try: cur = json.load(open(settings_path))
    except Exception: cur = {}
def cmds(block):  # set of command strings in a hook block
    return {h.get("command") for h in block.get("hooks", []) if isinstance(h, dict)}

def drop_repeats(block):
    # A command listed twice in one block runs twice on every tool call. An
    # earlier fragment shipped adt-test-run-guard.sh twice (ADT-354), and the
    # merge only ever adds, so collapse repeats already sitting in the file.
    seen, kept = set(), []
    for h in block.get("hooks", []):
        c = h.get("command") if isinstance(h, dict) else None
        if c is not None and c in seen:
            continue
        seen.add(c)
        kept.append(h)
    block["hooks"] = kept

hooks = cur.setdefault("hooks", {})
for event, entries in frag.get("hooks", {}).items():
    existing = hooks.setdefault(event, [])
    for frag_block in entries:
        matcher = frag_block.get("matcher")
        # Find an existing block with the same matcher to merge into (so we
        # don't append a near-duplicate block on re-run).
        target = next((b for b in existing if b.get("matcher") == matcher), None)
        if target is None:
            existing.append(frag_block)
            continue
        drop_repeats(target)
        have = cmds(target)
        for h in frag_block.get("hooks", []):
            if h.get("command") not in have:
                target["hooks"].append(h)
                have.add(h.get("command"))
# Env the defaults need (CLAUDE_CODE_DISABLE_TERMINAL_TITLE, so
# adt-terminal-title.sh owns the tab title instead of racing Claude's own
# writer). Additive only — a key the project already set is never clobbered.
env = cur.setdefault("env", {})
for k, v in frag.get("env", {}).items():
    env.setdefault(k, v)
if not env:
    cur.pop("env", None)

json.dump(cur, open(settings_path, "w"), indent=2)
open(settings_path, "a").write("\n")
print(f"  [defaults] merged hooks into {settings_path}")
PY

# The tab-title setting is NOT written here (ADT-334). It lives in the operator's
# editor config, outside the repo, and this file is the internals — `lib/resync.sh`
# calls it to regenerate `.claude/`, which is not an install. `adt-install.sh`
# owns that write, which also keeps it off the entry point ten tests call
# directly without isolating $HOME.
fi

# ── Update semantic: remove files a PRIOR install wrote that this one didn't ──
# (ADT-94) Any path in the OLD manifest that we did NOT just record is an
# upstream-deleted surface — drop it on update, unless the user edited it (then
# preserve + warn). This is what makes "update" a real re-sync, not just an
# overwrite.
{
  # Paths recorded THIS run, newline-delimited (bash-3.2-safe; no associative array).
  _new_paths=""
  for line in ${MANIFEST_LINES[@]+"${MANIFEST_LINES[@]}"}; do
    [[ -z "$line" ]] && continue
    _new_paths="$_new_paths${line%%$'\t'*}"$'\n'
  done
  # For each path in the OLD manifest not re-shipped this run: remove it (unless
  # the user edited it — then keep + warn).
  while IFS=$'\t' read -r rel old_sha; do
    [[ -z "$rel" ]] && continue
    printf '%s' "$_new_paths" | grep -qxF "$rel" && continue   # still shipped → leave it
    stale_dest="$CLAUDE/$rel"
    if [[ -f "$stale_dest" ]] && [[ "$(_sha256 "$stale_dest")" != "$old_sha" ]]; then
      echo "  [defaults] upstream-removed $rel but you edited it — keeping your copy"
      continue
    fi
    rm -f "$stale_dest" 2>/dev/null && echo "  [defaults] removed upstream-deleted $rel"
  done < <(printf '%s\n' "$_OLD_MANIFEST")
}

# ── Emit the manifest (ADT-94) — the install receipt: {path, sha256} per file ──
/usr/bin/python3 - "$CLAUDE/.adt-manifest.json" "$ADT_DIR" <<'PY' "${MANIFEST_LINES[@]:-}"
import json, os, re, sys, subprocess
out_path, adt_dir = sys.argv[1], sys.argv[2]
files = []
for line in sys.argv[3:]:
    if not line:
        continue
    path, _, sha = line.partition("\t")
    files.append({"path": path, "sha256": sha})
try:
    commit = subprocess.check_output(
        ["git", "-C", adt_dir, "rev-parse", "HEAD"], text=True).strip()
except Exception:
    commit = None
# ADT was republished from a fresh history once (AO-2), so a pinned commit can
# belong to a lineage a later clone does not share. Recording the origin makes
# that case distinguishable from "this clone is simply behind".
try:
    source_repo = subprocess.check_output(
        ["git", "-C", adt_dir, "remote", "get-url", "origin"], text=True).strip()
    source_repo = re.sub(r"^.*github\.com[:/]", "", source_repo)
    source_repo = re.sub(r"\.git$", "", source_repo) or None  # matches _adt_layer_origin_id
except Exception:
    source_repo = None
# ADT-170 A10: bundle_version makes an install's version legible to support
# ("which bundle are you running") and to the staleness calculation. Distinct
# from source_commit, which names a commit; this names a release.
try:
    with open(os.path.join(adt_dir, "VERSION")) as _v:
        bundle_version = _v.read().strip() or None
except Exception:
    bundle_version = None
manifest = {"schema": 2, "bundle_version": bundle_version,
            "source_commit": commit, "source_repo": source_repo,
            "files": sorted(files, key=lambda e: e["path"])}
json.dump(manifest, open(out_path, "w"), indent=2)
open(out_path, "a").write("\n")
print("  [defaults] wrote %s (%d files, sha256 each)" % (out_path, len(files)))
PY

echo "  [defaults] installed rules/skills/agents/hooks/commands/tools into $CLAUDE (copies; tracked in .adt-manifest.json)"

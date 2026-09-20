# The ADT install / update / uninstall model

**One line:** ADT **copies** its files into a project and records exactly what it
wrote in a **manifest**; update re-copies; uninstall deletes what the manifest
lists. A project owns its copies — **nothing in `.claude/` points back into the
ADT source repo.**

Origin: ADT-94, 2026-06-30. This supersedes the earlier symlink-based model
(ADT-090). The standard install/update/uninstall pattern is copy-in + an install
receipt — pip's `RECORD`, the macOS `.pkg` BOM, dpkg's per-package file list all
do exactly this. ADT now follows it.

## The model

### Install — copy + write a manifest
Install copies every consumer-facing surface into the project's `.claude/` as
**real files** (no symlinks):

| Surface | Lands at | How |
|---|---|---|
| rules | `.claude/rules/*.md` | file copy |
| agents | `.claude/agents/*.md` | file copy |
| hooks | `.claude/hooks/*.sh` | file copy (`chmod +x`) |
| skills | `.claude/skills/<dir>/` | dir copied file-by-file |
| commands | `.claude/commands/adt-*.md` | file copy (carries an `adt_managed` marker) |
| DoD grader | `.claude/tools/adt_dod.py` | file copy (stdlib-only, self-contained) |

Install then writes **`.claude/.adt-manifest.json`** — the install receipt:
one `{path, sha256}` entry per copied file, plus a schema version and the ADT
commit the snapshot was cut from. The per-file `sha256` mirrors pip's `RECORD`
and the `.pkg` BOM; it is what lets update and uninstall tell an ADT-pristine
copy from one the user edited.

### Update — re-run the installer (idempotent)
There is no separate `update` command; re-running the installer **is** the
update, and the migration for an older install.

**Once the layer is committed on `origin/<main>`, it belongs to the team
(ADT-384).** Before writing anything, `lib/adt-layer.sh` compares the manifest's
`source_commit` on `origin/<main>` with this machine's ADT clone:
- **same commit:** nothing is written;
- **clone older** (or missing that commit): the install stops and prints the
  `git pull` for the clone;
- **clone newer:** the update below is written to a local
  `chore/adt-upgrade-<sha>` branch in a temporary worktree, never into your
  working tree. Merging it upgrades every machine, and the installer lists the
  machines whose reported ADT is older.

A second machine installing into such a repo joins it: it takes the name, repo,
main branch, prefix and board title from the committed
`.claude/adt-project.yaml` and asks for none of them.

On a first install (nothing committed yet) and in ADT's own repo, the update
writes into the working tree as described here. It:
- overwrites each ADT-managed copy with the new version,
- copies any newly-shipped file,
- **removes any file the prior manifest listed that this install no longer
  ships** (a deleted-upstream rule disappears on update, not just on uninstall),
- and **preserves a user-edited copy**: if a file's on-disk `sha256` no longer
  matches the manifest, the user changed it — update keeps it and warns, rather
  than clobbering the edit (delete the file first to take the new ADT version).
  The manifest keeps the *original* sha for such a file so it stays flagged.
  The warning is one line in the install output and nothing else records it,
  so an edited file can stay on an old version indefinitely.

A re-run does more than copy files. It regenerates `.adt/config.yaml` from
`~/.adt/projects/<name>.yaml` every time, so a setting only survives if it is
kept in the project config. Without `--no-github` it also re-applies labels and
the board, pulls Issues, renders the board and reinstalls the watcher
(on macOS, only when its plist would change; `adt-install.sh:445-481`).
`--no-github` skips those, and is enough for an ADT change that does not touch
labels, the board or the watcher; a change that adds a label or a stage needs
the full run. The manifest's `source_commit` records which ADT commit the
committed layer came from, and the installer compares it with your clone as
above. Each machine's watcher also reports its own ADT version and commit once a
day on a closed `adt:install` Issue, and the board footer lists them.

### Uninstall — delete what the manifest lists
**On a repo whose layer is committed on `origin/<main>` (ADT-384),** a plain
`--uninstall` removes only this machine: its watcher, the `.adt/` folder and its
config. The committed `.claude/` copies, hook entries, `CLAUDE.md` block,
`.gitignore` block and `.claude/adt-project.yaml` belong to every machine and
stay. `--uninstall --everyone` also writes their removal to a local
`chore/adt-uninstall` branch for the team to merge.

On a first install, or with nothing committed, uninstall reads the manifest, deletes exactly those paths (any file type), then
deletes the manifest and `.claude/adt-project.yaml`. A copy whose `sha256` no longer matches (user-edited) is
**kept and surfaced**, never deleted. It touches no project-authored file (a real
file the manifest never recorded). A pre-ADT-94 install with no manifest falls
back to the legacy symlink/marker removal, so old installs still uninstall
cleanly; the first re-install converts them to the copy+manifest model.

## Clean separation — no project→source linkage
After install, a project is **self-contained**: `find <project>/.claude -type l`
finds no ADT symlink, and no copied file points back into
`~/ai-orchestrator-adt`. The copied hooks (`adt-done-guard.sh`, `adt-dod.sh`) reach the copied grader by a plain
local path (`../tools/adt_dod.py`) — no symlink-walk, no `$ADT_DIR`. The accepted
trade: an edit in the ADT repo does **not** auto-appear in a project; you re-run
the installer to pull it. That is the point — a snapshot you own, not a live tie.

## The watcher is ADT-owned — not part of the per-project install
The board-sync **watcher** (`adt_watch.py` on a launchd/systemd timer) is owned
and run by ADT, from the ADT install; it operates *on* a project's cache via
`--root <project>`. A project never contains it, never invokes it — so it sits
outside this copy model entirely. Its unit referencing the ADT install path is
how an ADT-owned job finds its own program, not a project→source linkage.

## Adding a new surface
Drop the file into the right `defaults/` (or `tools/`) location and add it to the
install copy step; it joins the manifest automatically, so uninstall removes it
with no new code. The manifest — not a per-file-type marker — is the single
provenance record for every copied surface.

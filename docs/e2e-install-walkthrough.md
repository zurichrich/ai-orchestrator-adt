# End-to-end walkthrough — install, use, update and uninstall ADT

**What this is.** The whole ADT lifecycle, run against a repo that has never
seen it, with the exact commands and what each one should print. Follow it top
to bottom on a throwaway repo and you have verified the install path yourself;
skim it and you have the shape of what ADT does to a project.

It exists because until ADT-174 nobody had done this. Every install had been
onto a repo that already had ADT, on a machine that already had its state — so
six divergences between the README and the shipped behaviour survived
unnoticed, including one that committed a 205 KB generated file into the user's
history and one that silently rewrote every other project on the machine.

> **Time:** about 15 minutes. **Cost:** one private GitHub repo and one Projects
> board, both deleted at the end.

---

## 0. Prerequisites

```bash
for c in gh jq yq git claude python3; do
  command -v "$c" >/dev/null || echo "MISSING: $c"
done
gh auth status
```

You need all six on `PATH`, and `gh` authenticated. The `project` scope is
**not** something to arrange up front — the installer checks for it and offers
the grant at the point of need.

Nothing else to prepare. A machine that has never installed global Claude
slash-commands has no `~/.claude/commands` directory, and the install runs
through that unchanged (ADT-099 fixed the abort that used to make it the one
undocumented prerequisite).

---

## 1. Clone ADT and create a throwaway project

```bash
git clone https://github.com/zurichrich/ai-orchestrator-adt.git ~/ai-orchestrator-adt
gh api -X POST /user/repos -f name='adt-walkthrough' -F private=true -F auto_init=true
git clone https://github.com/$(gh api user --jq .login)/adt-walkthrough ~/adt-walkthrough
```

---

## 2. Install

```bash
cd ~/adt-walkthrough
~/ai-orchestrator-adt/adt-install.sh
```

The installer interviews you for what it cannot infer, then prints its plan
before doing anything. Expect, in order:

```
▸ Checking prerequisites…      ✓ all tools present
▸ Detecting your project…
▸ Will configure:              name / path / repo / main_branch / id_prefix / cache_dir
▸ Writing config…              ✓ config: ~/.adt/projects/adt-walkthrough.yaml
▸ Installing slash commands + project defaults…
▸ Bootstrapping GitHub board + labels…    board: created 'adt-walkthrough backlog'
                                          protection: …@main is UNPROTECTED (offered, never applied silently)
▸ Adopting existing issues into the cache…    [adt-sync] adopted 0 issue(s)
▸ Rendering the board…
▸ Installing the background board-sync watcher…
 ADT installed for: adt-walkthrough
```

**Without a terminal it refuses rather than guessing** — an unanswered prompt is
an error, not a silent default:

```bash
~/ai-orchestrator-adt/adt-install.sh </dev/null   # → "No answer for 'Project name' (stdin closed)."
```

Pass `--yes` when you genuinely want every default; the run is then labelled as
defaults-only in its own output.

### Check what it wrote

```bash
cd ~/adt-walkthrough
ls .claude/commands | head            # the /adt-* playbooks
jq '{schema, source_commit, n: (.files|length)}' .claude/.adt-manifest.json
cat .adt/config.yaml                  # generated, machine-local
cat ~/.adt/projects/adt-walkthrough.yaml
git status --porcelain                # .claude/ only — NOT .adt/ or development-team/
```

`git status` is the one that matters. `.adt/` and `.adt/` must be
absent from it: the installer writes an `# ADT:gitignore` block covering both.
`.claude/` **is** listed, deliberately — the commands and rules are what a
teammate should get from a clone.

---

## 3. Use it: file a ticket and drive it to done

In Claude Code, from the project:

```text
/adt-brief
```

Answer the interview — including **Type** (`bug` / `enhancement` / `task`),
which is required: the board buckets the card by it and it is the only thing
that survives a cache rebuild.

Then watch the ticket move. Each lane change is a file move in the cache plus a
`stage:` update; the sync reconciles it to the Issue's label and the board's
column:

```bash
python3 ~/ai-orchestrator-adt/tools/adt_sync.py --root ~/adt-walkthrough
gh api "repos/$(gh api user --jq .login)/adt-walkthrough/issues/1" --jq '[.labels[].name]'
```

You should see `stage:ideas` become `stage:planned`, `stage:building` and
`stage:qa`, and the card move column for column.

The `done` move is the one that does **not** close the Issue. `adt_sync.py`
derives an Issue's open/closed state from the ticket's `state:` field, not from
its folder, so moving the file into `done/` relabels and re-columns it and
leaves it open. `/adt-close` is what writes `state: closed`, and the next sync
closes the Issue — which is what "done means integrated" is about. Verified in
the run-3 walkthrough record in the private archive, divergence 1.

---

## 4. Update — re-run the installer

```bash
cd ~/ai-orchestrator-adt && git pull
cd ~/adt-walkthrough && ~/ai-orchestrator-adt/adt-install.sh
```

There is no separate update command; the installer **is** the update path, and
it is idempotent. It picks up files ADT has newly shipped (every step is a
directory scan, not a fixed list), overwrites the copies you have not touched,
removes files ADT no longer ships — and **keeps any copy you edited**:

```bash
echo "<!-- my note -->" >> .claude/commands/adt-block.md
~/ai-orchestrator-adt/adt-install.sh --yes
grep -c "my note" .claude/commands/adt-block.md    # 1 — your edit survived
```

The run prints `keeping your edited copy of commands/adt-block.md`. Delete the
file and re-run to take ADT's version again.

**It installs into this project only.** If you have other projects configured,
confirm they were not touched:

```bash
ls ~/.adt/projects/                                # every configured project
git -C ~/your-other-project status --porcelain     # must be unchanged
```

A bare `~/ai-orchestrator-adt/setup.sh` (no `--project`) is the machine-wide
reconciler — use it deliberately after a `git pull` of ADT when you *do* want
every project refreshed.

---

## 5. Uninstall — the exact inverse

```bash
cd ~/adt-walkthrough
~/ai-orchestrator-adt/adt-install.sh --uninstall
```

It removes what the manifest records and nothing else: the `.claude/` ADT
copies (keeping any you edited), the ADT hook entries, the `ADT:rules` and
`ADT:gitignore` managed blocks, the generated configs, and the whole
regenerable `.adt/` tree. It **never** touches your GitHub Issues,
labels, board, or the ticket cache in `~/.adt/<name>/cache`.

```bash
ls -a                          # your own files intact; .adt/ and development-team/ gone
find .claude -type f           # only what you authored or edited
git status --porcelain
```

> **Keep authored docs outside `.adt/`.** Uninstall deletes that
> whole tree. Point `decision_log:` at your project's own `docs/` and ADRs and
> retros stay outside the delete path.

---

## 6. Reinstall — nothing was lost

```bash
rm -rf ~/.adt/adt-walkthrough/cache     # prove the cache rebuilds from Issues
cd ~/adt-walkthrough && ~/ai-orchestrator-adt/adt-install.sh
```

Run this one **from a terminal**: step 5 backed the project config up to
`.yaml.bak`, so the reinstall has nothing to read and re-runs the interview.

The backlog lives in GitHub Issues, so the cache rebuilds from them and the
ticket comes back with its slug and type intact.

**One thing to expect:** an Issue created in the last two minutes is *deferred*
rather than rebuilt, and the adopt line says so:

```
[adt-sync] adopted 0 issue(s) · 1 deferred (created in the last 2 min, so a
/adt-brief still writing its cache file is not raced) — the next sync picks
them up
```

That window is deliberate. It stops a sync tick landing between `gh issue
create` and the cache file being written from reconstructing a duplicate stub
(ADT-116). Wait it out, or run the pull again:

```bash
python3 ~/ai-orchestrator-adt/tools/adt_sync.py --root ~/adt-walkthrough --pull
```

---

## 7. Tear down

```bash
cd ~/adt-walkthrough && ~/ai-orchestrator-adt/adt-install.sh --uninstall
rm -rf ~/adt-walkthrough ~/.adt/adt-walkthrough ~/.adt/projects/adt-walkthrough.yaml*
```

The board and the repo are separate, and neither is ADT's to remove — uninstall
deliberately never touches your GitHub state.

Delete the board yourself:

```bash
gh api graphql -f query='{user(login:"YOUR-LOGIN"){projectV2(number:N){id}}}'
gh api graphql -f query='mutation{deleteProjectV2(input:{projectId:"PVT_..."}){projectV2{id}}}'
```

Deleting the repo needs the **`delete_repo`** scope, which `gh auth login` does
not grant by default — a plain `repo` scope returns
`403 Must have admin rights to Repository`. Either grant it once:

```bash
gh auth refresh -h github.com -s delete_repo
gh api -X DELETE "repos/$(gh api user --jq .login)/adt-walkthrough"
```

or delete the repo from its Settings page in the browser. (Found by running
this walkthrough: the teardown step as first written failed exactly here.)

---

## What this walkthrough is checked by

`tests/test_walkthrough_doc.sh` parses every fenced `bash` block here and
asserts each script path named above exists in the repo, so the document cannot
drift into naming a command ADT no longer ships. That grades the doc's
mechanics, not its truth — the truth check is running it, which is what
the run-2 walkthrough record in the private archive records.

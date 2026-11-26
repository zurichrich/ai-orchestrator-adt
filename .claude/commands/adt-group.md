---
adt_managed: ADT-087
name: adt-group
description: Put every ticket under a Feature and an Epic — propose the grouping, show it to the product owner, and create the Epic and Feature tickets on their yes
adt-budget: 80
---

# /adt-group

Every ticket sits under a Feature ticket, and every Feature under an Epic
ticket. A ticket names its Feature with `parent: <ID>`, and a Feature names its
Epic the same way. This command groups the tickets that are not in that shape
yet. The usual case is a project updating from ADT 0.4.0 or earlier, where the
install prints how many tickets need grouping. Done tickets are included, so the
management report covers the project's history too.

It is safe to run again. Epics and Features that already exist, here or on
GitHub, are reused, and a ticket that already has the right parent is left
alone.

## Steps

1. **Read the config.** `repo` and `id_prefix` come from `.adt/config.yaml`; the
   cache is `~/.adt/<project>/cache/`. Use `.claude/tools/adt_hierarchy.py`
   (installed) or `tools/adt_hierarchy.py` (in the ADT repo).

2. **Write the proposal.**

   ```
   python3 .claude/tools/adt_hierarchy.py --propose <cache> > .adt/grouping.md
   ```

   The outline lists the Epic and Feature tickets that exist, then groups the
   rest by the phase 1 `epic:` and `feature:` names on each ticket. Every
   ticket with neither name is listed under `# Ungrouped`.

3. **Place the ungrouped tickets.** Read each one's title and problem, and move
   its line under the Feature it belongs to. Add a new `## Feature:` or
   `# Epic:` heading where none fits. Give every Epic an `owner:` line with the
   product owner's GitHub login. A ticket you cannot place stays under
   `# Ungrouped`, and you ask the product owner about it in step 4. Do not invent
   a grouping to empty the list.

4. **Show the outline to the product owner and wait for a yes.** Print the
   outline, then the dry run, which lists every Epic and Feature Issue it would
   create and how many tickets get a parent:

   ```
   python3 .claude/tools/adt_hierarchy.py --apply .adt/grouping.md <cache>
   ```

   The dry run changes nothing. Creating the Issues publishes them on GitHub,
   so this needs the product owner's explicit yes in chat. Apply their edits to
   the outline and show it again until they approve it.

5. **Apply.**

   ```
   python3 .claude/tools/adt_hierarchy.py --apply .adt/grouping.md <cache> --yes
   ```

   It takes this machine's sync lock and the `adt:grouping` label on the repo,
   so a watch tick or another machine cannot group at the same time. If it
   reports that another machine holds the label, wait for that run to finish
   and start again from step 2. The new Epic and Feature Issues are created
   claimed with `adt:filing`, and the next `adt watch` tick pushes their cache
   files, links each child as a GitHub sub-issue, and moves each Epic and
   Feature to the lane its children are in.

6. **Check the result.**

   ```
   python3 .claude/tools/adt_hierarchy.py --status <cache>
   python3 .claude/tools/management_report.py --check-assigned <cache>
   ```

   `--status` prints nothing when every ticket is grouped. `--check-assigned`
   also names any Epic with no owner and any sub-issue link GitHub refused.
   Tickets the product owner left ungrouped are still reported; that is
   expected until they decide.

Delete `.adt/grouping.md` when you are done. It is a working file and nothing
reads it later.

<!-- adt-bundle: v0.4.0 -->

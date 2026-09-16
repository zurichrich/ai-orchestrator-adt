# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Which ADT each machine runs, reported where the other machines can see it
(ADT-384).

Every machine's sync agent runs the ADT clone on that machine, against the same
Issues. A `git pull` in one clone changes what that machine's sync does, and no
other machine could tell. ADT-147 (two machines spent the GraphQL pool in 39
minutes) and ADT-116 (duplicate Issues) were both sync defects of that kind.

So once a UTC day each machine's watch tick writes one comment on a closed Issue
labelled `adt:install`: its ADT version, the clone's commit and its OS. It
updates the same comment in place every day. The installer reads the comments
to name the machines an upgrade leaves behind, and the board lists them.

What is deliberately NOT in the comment:
- the telemetry install id. Next to the comment's author it would tie the
  anonymous telemetry id to a GitHub login.
- the hostname, for the same reason `adt_phone_home.install_id` rejects it.
The comment id is the machine's identity; `.adt/state/machine-report.json`
remembers it.

What is read back: only comments by the repo's OWNER, MEMBERs and
COLLABORATORs (anyone can comment on a public repo), whose marker parses, whose
commit is 40 lowercase hex characters (it reaches `git merge-base`), and that
were updated in the last 7 days. A machine that stopped reporting stopped
syncing too, so it is no longer acting on the Issues.

All calls are `gh api` REST (CLAUDE.md rule 5). Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adt_lane_cost import parse_iso  # noqa: E402
from adt_phone_home import due_today, read_version  # noqa: E402
from adt_sync import _gh  # noqa: E402
from ticket_serializer import INSTALL_LABEL  # noqa: E402

TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
MAX_AGE = datetime.timedelta(days=7)
_MARKER = re.compile(r"<!-- adt-machine (\{[^\n]*?\}) -->")
_SHA = re.compile(r"^[0-9a-f]{40}$")

ISSUE_TITLE = "ADT machines"
ISSUE_BODY = (
    "ADT keeps one comment per machine here, updated once a day by that "
    "machine's board sync: the ADT version and commit it runs. `adt-install.sh` "
    "reads them to name the machines an ADT upgrade leaves behind, and the "
    "board lists them.\n\n"
    "This Issue is closed on purpose, and the sync never treats an Issue "
    "labelled `adt:install` as a ticket.")


def _state_path(project_root: str, name: str) -> str:
    return os.path.join(project_root, ".adt", "state", name)


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def lowest_install_issue(repo: str, gh=None):
    """The lowest-numbered Issue labelled adt:install, or None. Two machines can
    create one at the same moment; everyone reading the lowest keeps them on one."""
    gh = gh or _gh
    issues = json.loads(gh(["api", f"repos/{repo}/issues?labels=adt%3Ainstall"
                                   f"&state=all&per_page=100"]) or "[]")
    numbers = [i["number"] for i in issues if "pull_request" not in i]
    return min(numbers) if numbers else None


def read_machines(repo: str, issue, own_comment_id=None, gh=None, now=None) -> list:
    """The trusted, recent machine reports on `issue`, newest first."""
    gh = gh or _gh
    if issue is None:
        return []
    now = now or datetime.datetime.now(datetime.timezone.utc)
    comments = json.loads(gh(["api", f"repos/{repo}/issues/{issue}/comments"
                                     f"?per_page=100"]) or "[]")
    out = []
    for c in comments:
        if c.get("author_association") not in TRUSTED_ASSOCIATIONS:
            continue
        m = _MARKER.search(c.get("body") or "")
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        updated = parse_iso(c.get("updated_at") or "")
        if updated is None or not isinstance(data, dict):
            continue
        commit = data.get("commit")
        if not isinstance(commit, str) or not _SHA.match(commit):
            continue
        if now - updated > MAX_AGE:
            continue
        out.append({
            "login": str((c.get("user") or {}).get("login", "")),
            "os": str(data.get("os", ""))[:40],
            "version": str(data.get("version", ""))[:40],
            "commit": commit,
            "reported_at": c["updated_at"],
            "comment_id": c.get("id"),
            "this_machine": own_comment_id is not None and c.get("id") == own_comment_id,
        })
    out.sort(key=lambda m: m["reported_at"], reverse=True)
    return out


def _comment_body(version: str, commit: str, os_name: str) -> str:
    marker = json.dumps({"version": version, "commit": commit, "os": os_name},
                        separators=(", ", ": "))
    return (f"ADT {version or '?'} at `{commit[:7]}` on {os_name}. Written once a "
            f"day by this machine's ADT board sync.\n\n<!-- adt-machine {marker} -->")


def report(project_root: str, adt_root: str, repo: str, gh=None,
           today: str = None, now=None, quiet: bool = True):
    """Write this machine's report and refresh `.adt/state/machines.json`.
    At most once a UTC day. Returns the machine list, or None when it did not
    run. Never raises: it rides on the watch tick.

    `gh` defaults to the module's `_gh`, looked up at call time rather than bound
    at definition, so the test suite's conftest.py can replace it for every test
    that does not pass its own."""
    gh = gh or _gh
    if not repo or not due_today(project_root, "machines", today):
        return None
    try:
        commit = subprocess.run(["git", "-C", adt_root, "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
        if not _SHA.match(commit):
            return None
        version = read_version(adt_root)
        os_name = platform.system()

        try:
            gh(["api", "-X", "POST", f"repos/{repo}/labels",
                "-f", f"name={INSTALL_LABEL}", "-f", "color=ededed",
                "-f", "description=ADT machine reports; not a ticket"])
        except RuntimeError:
            pass  # 422: it exists already

        issue = lowest_install_issue(repo, gh)
        if issue is None:
            # The label rides on the create call itself (git-workflow §D6). A
            # watch tick between a create and a later label call would rebuild
            # this Issue as a stub ticket (ADT-116, ADT-354).
            created = json.loads(gh(["api", "-X", "POST", f"repos/{repo}/issues",
                                     "-f", f"title={ISSUE_TITLE}",
                                     "-f", f"body={ISSUE_BODY}",
                                     "-f", f"labels[]={INSTALL_LABEL}"]))
            issue = created["number"]
            gh(["api", "-X", "PATCH", f"repos/{repo}/issues/{issue}",
                "-f", "state=closed"])

        body = _comment_body(version, commit, os_name)
        state_file = _state_path(project_root, "machine-report.json")
        try:
            with open(state_file) as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            state = {}
        comment_id = None
        if state.get("issue") == issue and state.get("comment_id"):
            try:
                gh(["api", "-X", "PATCH",
                    f"repos/{repo}/issues/comments/{int(state['comment_id'])}",
                    "-f", f"body={body}"])
                comment_id = int(state["comment_id"])
            except (RuntimeError, ValueError, TypeError):
                comment_id = None  # deleted, or not ours any more: post again
        if comment_id is None:
            posted = json.loads(gh(["api", "-X", "POST",
                                    f"repos/{repo}/issues/{issue}/comments",
                                    "-f", f"body={body}"]))
            comment_id = posted["id"]
        _write_json(state_file, {"issue": issue, "comment_id": comment_id})

        machines = read_machines(repo, issue, comment_id, gh, now)
        _write_json(_state_path(project_root, "machines.json"), machines)
        return machines
    except Exception as e:  # noqa: BLE001 - a report failure must not kill the tick
        if not quiet:
            print(f"  [machines] report skipped: {e}", file=sys.stderr)
        return None


def load_machines(project_root: str) -> list:
    """What the last report wrote, for the board. Empty when there is none."""
    try:
        with open(_state_path(project_root, "machines.json")) as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def age(reported_at: str, now=None):
    """'5m', '3h' or '2d' since `reported_at`; None when it does not parse."""
    then = parse_iso(reported_at)
    if then is None:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    secs = max(0, int((now - then).total_seconds()))
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def older_than(machines: list, sha: str, adt_root: str) -> list:
    """(machine, "older"|"unknown") for each machine whose commit is a strict
    ancestor of `sha` in the ADT clone, or is a commit the clone does not have."""
    out = []
    for m in machines:
        c = m["commit"]
        if c == sha:
            continue
        known = subprocess.run(["git", "-C", adt_root, "cat-file", "-e", f"{c}^{{commit}}"],
                               capture_output=True).returncode == 0
        if not known:
            out.append((m, "unknown"))
        elif subprocess.run(["git", "-C", adt_root, "merge-base", "--is-ancestor", c, sha],
                            capture_output=True).returncode == 0:
            out.append((m, "older"))
    return out


def _line(m: dict) -> str:
    return (f"{m['login']} · {m['os']} · v{m['version']} @{m['commit'][:7]} · "
            f"reported {age(m['reported_at']) or '?'} ago")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--older-than", metavar="SHA",
                       help="list the machines whose reported ADT commit is older than SHA")
    which.add_argument("--list", action="store_true",
                       help="list every machine that reported in the last 7 days "
                            "(adt-install.sh --uninstall --everyone)")
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--adt-dir", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args(argv)
    if a.list:
        try:
            machines = read_machines(a.repo, lowest_install_issue(a.repo))
        except Exception as e:  # noqa: BLE001
            print(f"  [machines] could not read the machine reports on {a.repo}: {e}")
            return 0
        if not machines:
            print("  [machines] no machine has reported ADT in the last 7 days.")
            return 0
        print("  [machines] these machines report ADT and still have to run "
              "adt-install.sh --uninstall to stop their watchers:")
        for m in machines:
            print(f"    {_line(m)}")
        return 0
    if not _SHA.match(a.older_than):
        print("  [machines] --older-than needs a full 40-character commit", file=sys.stderr)
        return 2
    try:
        machines = read_machines(a.repo, lowest_install_issue(a.repo))
    except Exception as e:  # noqa: BLE001
        print(f"  [machines] could not read the machine reports on {a.repo}: {e}")
        return 0
    behind = older_than(machines, a.older_than, a.adt_dir)
    if not behind:
        print("  [machines] no machine has reported an ADT older than this one in the last 7 days.")
        return 0
    print("  [machines] after the upgrade is merged, these machines must pull ADT:")
    for m, why in behind:
        note = "older than the new layer" if why == "older" else "its commit is unknown to this ADT clone"
        print(f"    {_line(m)} — {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

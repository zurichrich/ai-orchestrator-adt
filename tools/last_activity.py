#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
"""Recover a backlog's true last-activity dates after an adoption rewrote them.

ADT-322. Adopting a pre-existing backlog rewrote every Issue body to carry the
slug trailer, and GitHub stamped `updatedAt` on each one with the install time.
That field is server-owned: no REST or GraphQL call sets it on an existing
Issue, so the original values cannot be restored in place. What CAN be restored
is ADT's view of them, because the evidence the install did not touch is still
there — creation, closure, comment timestamps, timeline events, and the body's
own edit history.

WHERE THE ANSWER LIVES, AND WHY NOT IN ADT.
One tab-separated table per repo under the PROJECT's own state directory,
`~/.adt/<project>/last-activity/`. Never inside the ADT checkout: ADT is the
portable team definition and a recovered backlog is one consumer's private
history, so a table committed there would ride into every clone of the team repo
(the portability rule in the git-workflow default, D5). The table is generated
per project by this tool, or handed over as a one-off file and dropped into
place.

The sync neither reads nor writes it and the board reads it at render time. That
choice avoids three separate traps that a `last_activity:` frontmatter field
walks into:

  - `_PULL_OWNED` (adt_sync.py) is overwritten from GitHub every tick, and
    GitHub has no source for a reconstructed date, so the field would be
    cleared within 60 seconds of being written.
  - Every OTHER frontmatter key enters the push hash (adt_sync.py, the payload
    filter), so backfilling 360 files marks 360 tickets dirty in one tick.
  - A cache rebuild drops the value entirely, because the sync reconstructs a
    ticket from its Issue and the Issue has nothing to give. Uninstall and
    reinstall is a tested path here, so that is not hypothetical.

A table outside the cache has none of those problems and needs no change to the
sync at all. It also survives an uninstall, which `~/.adt` does not.

NOTHING HERE WRITES TO GITHUB. Stamping a correction onto the Issue is what
ADT-174 did, and it is the direct cause of this incident. Reads only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

COLUMNS = ("issue", "updated_at_now", "true_last_activity", "basis")

# The trailer, repeated here rather than imported: this tool reads bodies out of
# GitHub's edit history, which is a different shape from a cache file, and the
# serializer's regex is anchored to a whole body (\Z). A body edit counts as
# real only if the text WITHOUT the trailer changed, which is what makes the
# adoption's own churn invisible to the reconstruction.
_TRAILER_RE = re.compile(r"\n*<!-- adt: slug=[^\n>]* -->\n*\Z")

# Events that are not activity ON the ticket. A commit or another Issue
# mentioning this one does not mean anyone touched it. This decides a handful of
# tickets either way, so it is stated here rather than buried in a filter.
_NOT_ACTIVITY = {
    "cross-referenced", "referenced", "mentioned", "subscribed", "unsubscribed",
    # Board plumbing. Putting a ticket on a project board, or a tool moving its
    # column, is not someone working the ticket — it is the same class of event
    # as a label, and ADT does it in bulk when it adopts.
    "added_to_project", "removed_from_project", "moved_columns_in_project",
    "added_to_project_v2", "removed_from_project_v2",
    "project_v2_item_status_changed",
}

# Label churn is activity normally, and noise during an adoption: ADT stamps
# type:/stage:/track: on every Issue as it adopts. Excluded only INSIDE a
# declared window, so a genuine label change on another day still counts.
_LABEL_EVENTS = {"labeled", "unlabeled"}


def project_dir(project: str) -> Path:
    """`~/.adt/<project>` — a project's machine-local ADT state.

    Survives an uninstall (which preserves `~/.adt/<name>/`) and is in no git
    checkout, so a recovered backlog never reaches ADT's own history or a
    consumer's code repo."""
    return Path(os.path.expanduser("~")) / ".adt" / project


def table_path(base_dir, repo: str) -> Path:
    """`<base>/last-activity/<owner>-<repo>.tsv`, base being the project dir.

    Keyed by the GitHub repo, not the project name: the numbers in the table are
    Issue numbers, and those belong to a repo. Two project configs pointing at
    one backlog therefore share one table instead of silently keeping two."""
    return Path(base_dir) / "last-activity" / (
        repo.replace("/", "-") + ".tsv")


def _load_column(base_dir, repo: str, col: int) -> dict:
    """{issue number: value of column `col`}. Empty when there is no table.

    Fails open on every error, and per row: a row too short to carry the column,
    or whose issue number is not an integer, is skipped rather than raised on.
    This is read on the render path, where a missing or half-written table must
    degrade to "no repaired dates" and never take the board down.

    Both columns the board reads come through here so they cannot drift apart.
    `load` and `load_stamps` are compared against each other per ticket, and a
    difference in what either one silently drops would decide a date."""
    out = {}
    try:
        p = table_path(base_dir, repo)
        if not p.is_file():
            return out
        for i, line in enumerate(p.read_text().splitlines()):
            if i == 0 or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) <= col:
                continue
            try:
                num = int(parts[0])
            except ValueError:
                continue
            if parts[col].strip():
                out[num] = parts[col].strip()
    except Exception:  # noqa: BLE001 - never break a render
        return {}
    return out


def load(base_dir, repo: str) -> dict:
    """{issue number: the reconstructed last-activity timestamp}."""
    return _load_column(base_dir, repo, COLUMNS.index("true_last_activity"))


def load_stamps(base_dir, repo: str) -> dict:
    """{issue number: the `updatedAt` the adoption stamped on the Issue}.

    ADT-381. This is the reading the repair REPLACES, and the board needs it to
    tell the two cases apart. A ticket nobody has touched since the table was
    built still carries that stamp in `updated:`, so the repaired date is the
    better date. A ticket edited afterwards carries a LATER `updated:`, and that
    edit is real activity the repaired date knows nothing about. Without the
    stamp the board cannot see the difference, and it kept the repaired date for
    both — so an edited ticket never moved up its lane."""
    return _load_column(base_dir, repo, COLUMNS.index("updated_at_now"))


def write_table(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ["\t".join(COLUMNS)]
    for r in sorted(rows, key=lambda r: r["issue"]):
        body.append("\t".join(str(r[c]) for c in COLUMNS))
    path.write_text("\n".join(body) + "\n")


# --------------------------------------------------------------------------
# Reconstruction.
# --------------------------------------------------------------------------

def _gh(args: list) -> str:
    r = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "").strip()[:400])
    return r.stdout


def _parse_window(spec: str) -> tuple:
    """`2026-09-09T08:00:00Z..2026-09-09T08:30:00Z` -> (start, end).

    The windows are a REQUIRED argument, never a default. They are specific to
    one install run on one day, and a hardcoded window silently mis-handles the
    next project that adopts — the reconstruction would treat that project's
    genuine label changes as adoption noise, or its adoption noise as genuine."""
    try:
        a, b = spec.split("..")
        return (_iso(a), _iso(b))
    except Exception:
        raise SystemExit(f"bad --window {spec!r}: want START..END in ISO 8601")


def _iso(s: str) -> datetime:
    s = (s or "").strip().replace("Z", "+00:00")
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def _in_any(ts: datetime, windows: list) -> bool:
    return any(a <= ts <= b for a, b in windows)


def _strip_trailer(body: str) -> str:
    return _TRAILER_RE.sub("", body or "").rstrip()


def _issues(repo: str) -> list:
    """Every Issue in the repo, PRs excluded, via REST (core pool)."""
    out, page = [], 1
    while True:
        raw = _gh(["api", f"repos/{repo}/issues"
                          f"?state=all&per_page=100&page={page}"])
        batch = json.loads(raw)
        if not batch:
            break
        out += [i for i in batch if not i.get("pull_request")]
        page += 1
    return out


_EDITS_Q = """query($owner:String!,$name:String!){repository(owner:$owner,name:$name){%s}}"""


def _body_edits(repo: str, numbers: list, chunk: int = 20) -> dict:
    """{number: [(editedAt, body-after-that-edit)]}, newest last.

    GraphQL because `userContentEdits` has no REST equivalent — this is the
    exception CLAUDE.md rule 5 allows, and it is batched by alias so the whole
    backlog costs a couple of dozen queries rather than one per Issue."""
    owner, name = repo.split("/", 1)
    out = {}
    for i in range(0, len(numbers), chunk):
        part = numbers[i:i + chunk]
        sel = "".join(
            f'i{n}: issue(number:{n}){{userContentEdits(last:30)'
            f'{{nodes{{editedAt diff}}}}}} ' for n in part)
        raw = _gh(["api", "graphql", "-f", f"query={_EDITS_Q % sel}",
                   "-F", f"owner={owner}", "-F", f"name={name}"])
        data = (json.loads(raw).get("data") or {}).get("repository") or {}
        for n in part:
            node = data.get(f"i{n}") or {}
            edits = ((node.get("userContentEdits") or {}).get("nodes") or [])
            # OLDEST FIRST, sorted rather than trusted: the API returns these
            # newest-first, and walking them in that order compares each edit
            # to its SUCCESSOR. The adoption's trailer-add is the newest edit
            # on every adopted Issue, so the genuine edit before it compared
            # equal once the trailer was stripped and vanished. One consumer
            # Issue (#605) grew by 8KB at 09:15 and read as unchanged.
            out[n] = sorted(
                [(e.get("editedAt"), e.get("diff") or "")
                 for e in edits if e.get("editedAt")],
                key=lambda e: e[0])
    return out


def _timeline(repo: str, number: int) -> list:
    """(event, created_at) pairs, REST."""
    try:
        raw = _gh(["api", f"repos/{repo}/issues/{number}/timeline"
                          f"?per_page=100"])
    except RuntimeError:
        return []
    out = []
    for ev in json.loads(raw):
        at = ev.get("created_at")
        if at:
            out.append((ev.get("event") or "", at))
    return out


def reconstruct(repo: str, windows: list, verbose=False) -> list:
    """One row per Issue: the latest moment someone genuinely touched it.

    max(created, closed, last real comment, real timeline event, real body edit)
    where "real" drops cross-references, drops label churn inside a declared
    adoption window, and counts a body edit only when the text minus the trailer
    actually changed."""
    issues = _issues(repo)
    numbers = [i["number"] for i in issues]
    edits = _body_edits(repo, numbers)
    rows = []
    for it in issues:
        n = it["number"]
        best, basis = _iso(it["created_at"]), "created"
        if it.get("closed_at"):
            t = _iso(it["closed_at"])
            if t > best:
                best, basis = t, "closed"
        for ev, at in _timeline(repo, n):
            t = _iso(at)
            if ev in _NOT_ACTIVITY:
                continue
            if ev in _LABEL_EVENTS and _in_any(t, windows):
                continue
            if t > best:
                best, basis = t, f"timeline:{ev}"
        prev = None
        for at, body in edits.get(n, []):
            t, cur = _iso(at), _strip_trailer(body)
            # A body edit inside an adoption window is ADT rewriting the body,
            # which is the defect itself. Stripping the trailer is not enough to
            # see that: the push rebuilds the whole body from the cache, so
            # normalisation and the DoD block change bytes the trailer regex
            # never touches, and the edit reads as genuine content.
            if (prev is not None and cur != prev and t > best
                    and not _in_any(t, windows)):
                best, basis = t, "body edit"
            prev = cur
        rows.append({"issue": n,
                     "updated_at_now": it.get("updated_at") or "",
                     "true_last_activity": best.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "basis": basis})
        if verbose:
            print(f"  #{n}\t{it.get('updated_at')}\t"
                  f"{best:%Y-%m-%dT%H:%M:%SZ}\t{basis}", file=sys.stderr)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("reconstruct", help="rebuild the table from GitHub")
    r.add_argument("--repo", required=True, help="owner/name")
    r.add_argument("--window", action="append", default=[], required=True,
                   metavar="START..END",
                   help="adoption window to discount label churn inside; "
                        "repeatable, ISO 8601")
    r.add_argument("--project", default="",
                   help="ADT project name; the table lands in "
                        "~/.adt/<project>/last-activity/")
    r.add_argument("--dir", dest="dir_", default="",
                   help="explicit base dir, overriding --project")
    r.add_argument("--out", default="")
    r.add_argument("-v", "--verbose", action="store_true")

    s = sub.add_parser("show", help="print what the board would use")
    s.add_argument("--repo", required=True)
    s.add_argument("--project", default="")
    s.add_argument("--dir", dest="dir_", default="")

    a = ap.parse_args(argv)
    if a.cmd == "show":
        base = a.dir_ or project_dir(a.project or a.repo.split("/")[-1])
        print(f"table: {table_path(base, a.repo)}")
        t = load(base, a.repo)
        print(f"{len(t)} repaired dates for {a.repo}")
        for k in sorted(t)[:20]:
            print(f"  #{k}\t{t[k]}")
        return 0

    windows = [_parse_window(w) for w in a.window]
    rows = reconstruct(a.repo, windows, verbose=a.verbose)
    base = a.dir_ or project_dir(a.project or a.repo.split("/")[-1])
    out = Path(a.out) if a.out else table_path(base, a.repo)
    write_table(out, rows)
    stamped = sum(1 for r in rows
                  if r["updated_at_now"][:10] != r["true_last_activity"][:10])
    print(f"{len(rows)} issues -> {out}")
    print(f"{stamped} recovered to a different date than GitHub now reports")
    return 0


if __name__ == "__main__":
    sys.exit(main())

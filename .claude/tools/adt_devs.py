# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Stamp a ticket's `devs:` with the GitHub handles of its commit authors (AO-028).

Git author names are not GitHub handles, so each linked commit is resolved
through REST (`gh api repos/<repo>/commits/<sha>`) and its `author.login` read.
A commit with no GitHub login is skipped, never guessed. When no commit
resolves, an empty `devs:` line is written, which records that the ticket was
checked and shows no dev. A ticket with no linked commits is left alone.

  adt_devs.py <ticket.md> [--repo owner/repo]   stamp one ticket (/adt-close)
  adt_devs.py --all <cache> [--repo owner/repo] stamp every ticket with commits
  adt_devs.py --check <cache>                   exit 1 if a ticket with commits
                                                has no `devs:` key
The repo defaults to `repo:` in the project's .adt/config.yaml.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ticket_serializer import parse_md  # noqa: E402

_DEVS_LINE = re.compile(r"^devs:.*$", re.M)


def commits_of(text: str) -> list[str]:
    """Linked commit SHAs, from the list form or a comma-separated scalar."""
    raw = parse_md(text).get("commits") or []
    if isinstance(raw, str):
        raw = [raw]
    return [s.strip() for item in raw for s in str(item).split(",") if s.strip()]


def login(repo: str, sha: str) -> str | None:
    """The GitHub login that authored a commit, or None when it has none."""
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}/commits/{sha}", "--jq", ".author.login // empty"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    name = out.stdout.strip()
    return name if out.returncode == 0 and name and name != "null" else None


def _has_devs(text: str) -> bool:
    fm = text.split("\n---", 1)[0] if text.startswith("---") else ""
    return bool(_DEVS_LINE.search(fm))


def stamp(path: Path, repo: str) -> list[str] | None:
    """Write `devs:` into one ticket. None when it has no linked commits."""
    text = path.read_text(encoding="utf-8")
    shas = commits_of(text)
    if not shas or not text.startswith("---\n"):
        return None
    devs: list[str] = []
    for sha in shas:
        who = login(repo, sha)
        if who and who not in devs:
            devs.append(who)
    line = "devs: " + ", ".join(devs) if devs else "devs:"
    head, sep, rest = text[4:].partition("\n---")
    if _DEVS_LINE.search(head):
        head = _DEVS_LINE.sub(line, head, count=1)
    else:
        head = head + "\n" + line
    path.write_text("---\n" + head + sep + rest, encoding="utf-8")
    return devs


def tickets(cache: Path):
    for p in sorted(cache.glob("*/*/*.md")):
        yield p


def default_repo() -> str:
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True).stdout.strip()
        cfg = Path(top or ".") / ".adt" / "config.yaml"
        for ln in cfg.read_text(encoding="utf-8").splitlines():
            if ln.startswith("repo:"):
                return ln.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Stamp devs: from linked commits")
    ap.add_argument("ticket", nargs="?")
    ap.add_argument("--all", metavar="CACHE")
    ap.add_argument("--check", metavar="CACHE")
    ap.add_argument("--repo")
    a = ap.parse_args(argv)

    if a.check:
        missing = [p for p in tickets(Path(a.check))
                   if commits_of(p.read_text(encoding="utf-8"))
                   and not _has_devs(p.read_text(encoding="utf-8"))]
        for p in missing:
            print(f"{p}: commits but no devs:")
        return 1 if missing else 0

    repo = a.repo or default_repo()
    if not repo:
        print("no repo: pass --repo or set repo: in .adt/config.yaml", file=sys.stderr)
        return 2
    paths = list(tickets(Path(a.all))) if a.all else [Path(a.ticket)] if a.ticket else []
    if not paths:
        ap.error("give a ticket, --all <cache> or --check <cache>")
    for p in paths:
        devs = stamp(p, repo)
        if devs is not None:
            print(f"{p.name}: devs: {', '.join(devs) or '(none resolved)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# adt-bundle: v0.3.0

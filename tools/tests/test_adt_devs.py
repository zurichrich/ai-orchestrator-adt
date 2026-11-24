# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""adt_devs.py (AO-028): `devs:` from the GitHub logins of linked commits.
`gh` is a stub on PATH that answers from a sha -> login table."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_devs  # noqa: E402

LOGINS = {"aaa111": "amy", "bbb222": "bo", "ccc333": ""}  # ccc333: no GitHub account


def _stub_gh(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    table = "\n".join(f'  *"/commits/{k}") echo "{v}";;' for k, v in LOGINS.items())
    gh = bin_ / "gh"
    gh.write_text(f'#!/bin/sh\ncase "$2" in\n{table}\n  *) exit 1;;\nesac\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")


def _ticket(tmp_path, body: str) -> Path:
    d = tmp_path / "cache" / "tasks" / "done"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"t{len(list(d.iterdir()))}.md"
    p.write_text(f"---\nslug: t\nid: T-1\n{body}\n---\n\n# T\n")
    return p


def _devs_line(p: Path):
    for ln in p.read_text().split("\n---", 1)[0].splitlines():
        if ln.startswith("devs:"):
            return ln
    return None


def test_stamps_handles_from_linked_commits(tmp_path, monkeypatch):
    _stub_gh(tmp_path, monkeypatch)
    p = _ticket(tmp_path, "commits:\n  - aaa111\n  - bbb222\n  - aaa111")
    assert adt_devs.main([str(p), "--repo", "o/r"]) == 0
    assert _devs_line(p) == "devs: amy, bo"
    # Re-stamping replaces the line rather than adding a second one.
    adt_devs.main([str(p), "--repo", "o/r"])
    assert p.read_text().count("\ndevs:") == 1


def test_reads_list_and_comma_commit_forms(tmp_path):
    assert adt_devs.commits_of("---\ncommits:\n  - aaa111\n  - bbb222\n---\n") == ["aaa111", "bbb222"]
    assert adt_devs.commits_of("---\ncommits: aaa111, bbb222\n---\n") == ["aaa111", "bbb222"]
    assert adt_devs.commits_of("---\ncommits: aaa111\n---\n") == ["aaa111"]


def test_no_commits_writes_no_devs_line(tmp_path, monkeypatch):
    _stub_gh(tmp_path, monkeypatch)
    p = _ticket(tmp_path, "commits: []")
    before = p.read_text()
    assert adt_devs.main([str(p), "--repo", "o/r"]) == 0
    assert p.read_text() == before


def test_commit_without_login_is_skipped(tmp_path, monkeypatch):
    _stub_gh(tmp_path, monkeypatch)
    mixed = _ticket(tmp_path, "commits:\n  - ccc333\n  - bbb222")
    adt_devs.main([str(mixed), "--repo", "o/r"])
    assert _devs_line(mixed) == "devs: bo"
    none = _ticket(tmp_path, "commits:\n  - ccc333")
    adt_devs.main([str(none), "--repo", "o/r"])
    assert _devs_line(none) == "devs:"


def test_check_fails_when_a_committed_ticket_has_no_devs(tmp_path, monkeypatch, capsys):
    _stub_gh(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    _ticket(tmp_path, "commits: []")
    p = _ticket(tmp_path, "commits:\n  - ccc333")
    assert adt_devs.main(["--check", str(cache)]) == 1
    assert p.name in capsys.readouterr().out
    # An empty devs: line counts as checked.
    assert adt_devs.main(["--all", str(cache), "--repo", "o/r"]) == 0
    assert adt_devs.main(["--check", str(cache)]) == 0

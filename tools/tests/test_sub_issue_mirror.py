# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""AO-028: a ticket's `parent:` is mirrored to a native GitHub sub-issue by the
push. Driven through reconcile_all with `_gh` replaced by a fake GitHub that
records every call, so each test asserts on the calls the push made."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_sync  # noqa: E402
from ticket_serializer import from_issue, parse_md  # noqa: E402


class FakeGitHub:
    def __init__(self, parents=None, post_error=None):
        self.parents = dict(parents or {})     # child number -> parent number
        self.post_error = post_error
        self.calls = []
        self.next = 500

    def __call__(self, args, input_text=None):
        self.calls.append(list(args))
        if args[:2] == ["issue", "create"]:
            self.next += 1
            return f"https://github.com/o/r/issues/{self.next}\n"
        if args[0] != "api":
            return ""
        path = next(a for a in args if a.startswith("repos/"))
        if path.endswith("/parent"):
            n = int(path.split("/")[-2])
            if n not in self.parents:
                raise adt_sync.GhError(f"gh api {path} failed: HTTP 404: Not Found")
            return json.dumps({"number": self.parents[n]})
        if path.endswith("/sub_issues"):
            if self.post_error:
                raise adt_sync.GhError(f"gh api {path} failed: {self.post_error}")
            child = int(next(a for a in args if a.startswith("sub_issue_id="))[13:]) - 1000
            self.parents[child] = int(path.split("/")[-2])
            return "{}"
        n = int(path.split("/")[-1])
        return json.dumps({"number": n, "id": 1000 + n, "node_id": f"N{n}"})

    def posts(self):
        return [c for c in self.calls if "POST" in c and c[3].endswith("/sub_issues")]


def _file(cache: Path, folder: str, tid: str, typ: str, number=None, **fm) -> Path:
    d = cache / folder / "ideas"
    d.mkdir(parents=True, exist_ok=True)
    fields = {"slug": tid.lower(), "id": tid, "title": tid, "type": typ,
              "stage": "ideas", "state": "open", **fm}
    if number:
        fields["issue_number"] = number
    p = d / f"{tid.lower()}.md"
    p.write_text("---\n" + "\n".join(f"{k}: {v}" for k, v in fields.items())
                 + f"\n---\n\n# {tid}\n")
    return p


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = {"repo": "o/r", "cache_dir": str(tmp_path), "id_prefix": "T"}
    monkeypatch.setattr(adt_sync, "load_config", lambda root: cfg)
    monkeypatch.setattr(adt_sync, "ensure_on_board", lambda *a, **k: None)
    monkeypatch.setattr(adt_sync, "_ensure_labels_exist", lambda *a, **k: None)
    monkeypatch.setattr(adt_sync, "checkpoint_tokens", lambda *a, **k: [])
    monkeypatch.setattr(adt_sync, "restamp_closed", lambda *a, **k: [])
    monkeypatch.setattr(adt_sync, "checkpoint_stamp", lambda *a, **k: [])
    # The Issue already matches the file, so the only calls left are the mirror's.
    monkeypatch.setattr(adt_sync, "_current_issue", lambda repo, n: _issue_for(tmp_path, n))

    def run(gh):
        monkeypatch.setattr(adt_sync, "_gh", gh)
        return adt_sync.reconcile_all(str(tmp_path), pull=False)
    return tmp_path, run


def _issue_for(cache: Path, n: int) -> dict:
    for p in cache.rglob("*.md"):
        d = parse_md(p.read_text())
        if d.get("issue_number") == n:
            return adt_sync.to_issue(d)
    return {}


def _tree(cache: Path):
    _file(cache, "epics", "T-1", "epic", 1)
    _file(cache, "features", "T-2", "feature", 2, parent="T-1")
    return _file(cache, "enhancements", "T-3", "enhancement", 3, parent="T-2")


def test_push_links_a_child_to_its_parent(env):
    cache, run = env
    _tree(cache)
    gh = FakeGitHub(parents={2: 1})
    run(gh)
    assert [c[3] for c in gh.posts()] == ["repos/o/r/issues/2/sub_issues"]
    assert "sub_issue_id=1003" in gh.posts()[0]
    assert ["api", "repos/o/r/issues/3/parent"] in gh.calls


def test_unchanged_parent_makes_no_post(env):
    cache, run = env
    _tree(cache)
    gh = FakeGitHub(parents={2: 1, 3: 2})
    run(gh)
    assert gh.posts() == []


def test_moved_parent_is_replaced(env):
    cache, run = env
    _tree(cache)
    _file(cache, "features", "T-4", "feature", 4, parent="T-1")
    gh = FakeGitHub(parents={2: 1, 4: 1, 3: 4})
    run(gh)
    assert len(gh.posts()) == 1
    post = gh.posts()[0]
    assert post[3] == "repos/o/r/issues/2/sub_issues" and "replace_parent=true" in post
    assert gh.parents[3] == 2


def test_refused_link_is_not_retried(env):
    cache, run = env
    child = _tree(cache)
    gh = FakeGitHub(parents={2: 1}, post_error="HTTP 422: sub-issue limit reached")
    results = run(gh)
    assert any(r.get("mirror") == "refused" for r in results)
    assert "sub_issue_refused: HTTP 422: sub-issue limit reached" in child.read_text()
    gh2 = FakeGitHub(parents={2: 1}, post_error="HTTP 422: sub-issue limit reached")
    run(gh2)
    assert gh2.calls == []                       # the hash was kept: nothing to push


def test_other_failures_retry(env):
    cache, run = env
    _tree(cache)
    results = run(FakeGitHub(parents={2: 1}, post_error="HTTP 500: server error"))
    assert any(r["action"] == "error" for r in results)
    gh = FakeGitHub(parents={2: 1})
    run(gh)
    assert len(gh.posts()) == 1                  # the hash was dropped: retried


def test_parents_push_before_children(env):
    cache, run = env
    _file(cache, "enhancements", "T-3", "enhancement", parent="T-2")
    _file(cache, "features", "T-2", "feature", parent="T-1")
    _file(cache, "epics", "T-1", "epic")
    gh = FakeGitHub()
    run(gh)
    titles = [c[c.index("--title") + 1] for c in gh.calls if c[:2] == ["issue", "create"]]
    assert titles == ["T-1", "T-2", "T-3"]
    # Each parent had its number in time: both links were posted.
    assert [c[3] for c in gh.posts()] == ["repos/o/r/issues/501/sub_issues",
                                          "repos/o/r/issues/502/sub_issues"]


def test_epic_issue_rebuilds_into_epics_folder(tmp_path):
    cfg = {"repo": "o/r", "cache_dir": str(tmp_path)}
    for label, folder in (("type:epic", "epics"), ("type:feature", "features")):
        issue = {"number": 7, "title": "x", "body": "b\n\n<!-- adt: slug=x -->",
                 "state": "OPEN", "labels": [{"name": label}, {"name": "stage:ideas"}]}
        data = from_issue(issue)
        assert data["type"] == folder[:-1]
        assert adt_sync._cache_path_for(cfg, data) == str(tmp_path / folder / "ideas" / "x.md")
        assert f"type:{folder[:-1]}" in {l["name"] for l in adt_sync.to_issue(data)["labels"]}

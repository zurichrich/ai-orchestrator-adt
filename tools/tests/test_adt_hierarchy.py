# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""adt_hierarchy.py (AO-028): Epic and Feature tickets and the grouping that
creates them. `gh` is a stub on PATH that answers from a scenario file and logs
every call, so a test can assert which calls were made and in what order."""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_hierarchy as h  # noqa: E402

STUB = r'''#!/usr/bin/env python3
import json, os, sys
st = json.load(open(os.environ["GH_STATE"]))
args = sys.argv[1:]
open(os.environ["GH_LOG"], "a").write(json.dumps(args) + "\n")
method = args[args.index("-X") + 1] if "-X" in args else "GET"
path = next(a for a in args[1:] if a.startswith("repos/"))
fields = [args[i + 1] for i, a in enumerate(args) if a == "-f"]
def save(): json.dump(st, open(os.environ["GH_STATE"], "w"))
if "/labels" in path and method == "POST":
    name = next(f[5:] for f in fields if f.startswith("name="))
    if name == "adt:grouping" and st.get("lock"):
        sys.stderr.write("HTTP 422: already_exists"); sys.exit(1)
    if name == "adt:grouping":
        st["lock"] = next(f[12:] for f in fields if f.startswith("description=")); save()
    print("{}"); sys.exit(0)
if path.endswith("adt%3Agrouping") and method == "GET":
    print(st.get("steal") or st.get("lock", "")); sys.exit(0)
if path.endswith("adt%3Agrouping") and method == "DELETE":
    st["lock"] = ""; save(); sys.exit(0)
if "/issues?" in path:
    typ = path.split("labels=type:")[1].split("&")[0]
    page = int(path.split("page=")[-1])
    items = [i for i in st.get("remote", []) if i["type"] == typ and i.get("page", 1) == page]
    if "state=all" not in path:
        items = [i for i in items if i.get("state") != "closed"]
    filler = [{"number": 9000 + n, "title": f"filler {n}", "body": ""}
              for n in range(100)] if page == 1 and st.get("two_pages") == typ else []
    print(json.dumps(filler + [{"number": i["number"], "title": i["title"],
                                "body": i.get("body", "")} for i in items])); sys.exit(0)
if path.endswith("/issues") and method == "POST":
    if st.get("fail_create"):
        sys.stderr.write("HTTP 500"); sys.exit(1)
    st["next"] = st.get("next", 100) + 1
    if st.get("steal_after_create"):
        st["steal"] = "othermachine:1 " + str(int(st["now"]))
    save(); print(json.dumps({"number": st["next"], "node_id": f"N{st['next']}"})); sys.exit(0)
sys.stderr.write("unexpected " + " ".join(args)); sys.exit(1)
'''


def _stub(tmp_path, monkeypatch, **state):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    gh = bin_ / "gh"
    gh.write_text(STUB)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    state.setdefault("now", time.time())
    (tmp_path / "state.json").write_text(json.dumps(state))
    monkeypatch.setenv("GH_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("GH_LOG", str(tmp_path / "log"))
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")


def _calls(tmp_path):
    log = tmp_path / "log"
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def _creates(tmp_path):
    return [c for c in _calls(tmp_path) if "-X" in c and c[c.index("-X") + 1] == "POST"
            and c[3].endswith("/issues")]


def _state(tmp_path):
    return json.loads((tmp_path / "state.json").read_text())


def _ticket(cache: Path, tid: str, typ="enhancement", lane="ideas", **fm):
    d = cache / f"{typ}s" / lane
    d.mkdir(parents=True, exist_ok=True)
    fields = {"slug": tid.lower(), "id": tid, "type": typ, **fm}
    head = "\n".join(f"{k}: {v}" for k, v in fields.items())
    p = d / f"{tid.lower()}.md"
    p.write_text(f"---\n{head}\n---\n\n# {fm.get('title', 'Title of ' + tid)}\n")
    return p


def _fm(p: Path) -> str:
    return p.read_text().split("\n---", 1)[0]


OUTLINE = """# Epic: Billing
owner: amy
## Feature: Invoices
- T-1 one
- T-2 two
## Feature: Refunds
- T-3 three

# Ungrouped
- T-9 nine
"""


def _apply(tmp_path, cache, outline=OUTLINE, me="me:1"):
    return h.apply(h.parse_outline(outline), cache, "o/r", "T", me=me)


def test_propose_groups_by_name_and_lists_the_rest(tmp_path):
    _ticket(tmp_path, "T-1", epic="Billing", feature="Invoices", product_owner="amy")
    _ticket(tmp_path, "T-2", epic="Billing", feature="Refunds")
    _ticket(tmp_path, "T-3")
    out = h.propose(h.load(tmp_path))
    assert "# Epic: Billing\nowner: amy\n## Feature: Invoices\n- T-1 " in out
    assert "## Feature: Refunds\n- T-2 " in out
    assert out.split("# Ungrouped\n")[1].startswith("- T-3 ")


def test_apply_without_yes_changes_nothing(tmp_path, monkeypatch, capsys):
    _stub(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    p = _ticket(cache, "T-1", epic="Billing", feature="Invoices")
    before = p.read_text()
    (tmp_path / "o.md").write_text(OUTLINE)
    assert h.main(["--apply", str(tmp_path / "o.md"), str(cache)]) == 0
    assert "create Epic: Billing" in capsys.readouterr().out
    assert _calls(tmp_path) == [] and p.read_text() == before
    assert not (cache / "epics").exists()


def test_apply_creates_epics_then_features_then_parents(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    kids = [_ticket(cache, f"T-{n}", epic="Billing", feature="Invoices") for n in (1, 2, 3)]
    _apply(tmp_path, cache)
    titles = [next(f[6:] for f in c if f.startswith("title=")) for c in _creates(tmp_path)]
    assert titles == ["Billing", "Invoices", "Refunds"]
    t = h.load(cache)
    epic = next(x for x in t.values() if x["type"] == "epic")
    assert epic["id"] == "T-101" and "product_owner: amy" in _fm(epic["path"])
    assert t["T-102"]["parent"] == "T-101" and t["T-103"]["parent"] == "T-101"
    assert [t[f"T-{n}"]["parent"] for n in (1, 2, 3)] == ["T-102", "T-102", "T-103"]
    for k in kids:
        assert "epic:" not in _fm(k) and "feature:" not in _fm(k)


def test_apply_twice_creates_nothing(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    for n in (1, 2, 3):
        _ticket(cache, f"T-{n}")
    _apply(tmp_path, cache)
    snap = {p: p.read_text() for p in cache.rglob("*.md")}
    n_creates = len(_creates(tmp_path))
    assert _apply(tmp_path, cache) == []
    assert len(_creates(tmp_path)) == n_creates
    assert {p: p.read_text() for p in cache.rglob("*.md")} == snap


def test_apply_stops_while_another_machine_holds_the_lock(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, lock=f"other:9 {int(time.time())}")
    cache = tmp_path / "cache"
    _ticket(cache, "T-1")
    (tmp_path / "o.md").write_text(OUTLINE)
    assert h.main(["--apply", str(tmp_path / "o.md"), str(cache), "--yes",
                   "--repo", "o/r", "--prefix", "T"]) == 1
    assert _creates(tmp_path) == []
    assert _state(tmp_path)["lock"].startswith("other:9 ")


def test_stale_lock_is_taken_over(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, lock=f"other:9 {int(time.time()) - 7200}")
    cache = tmp_path / "cache"
    _ticket(cache, "T-1")
    _apply(tmp_path, cache)
    assert len(_creates(tmp_path)) == 3
    # Taken over at the same moment by another machine: stop after the first create.
    tmp2 = tmp_path / "b"
    tmp2.mkdir()
    _stub(tmp2, monkeypatch, lock=f"other:9 {int(time.time()) - 7200}", steal_after_create=True)
    cache2 = tmp2 / "cache"
    _ticket(cache2, "T-1")
    try:
        _apply(tmp2, cache2)
        raise AssertionError("expected a stop")
    except h.HierarchyError as e:
        assert "another machine" in str(e)
    assert len(_creates(tmp2)) == 1


def test_resolve_follows_the_chain(tmp_path):
    _ticket(tmp_path, "T-1", typ="epic")
    _ticket(tmp_path, "T-2", typ="feature", parent="T-1")
    _ticket(tmp_path, "T-3", parent="T-02")
    _ticket(tmp_path, "T-4", parent="T-3")          # parent is a plain ticket
    _ticket(tmp_path, "T-5", parent="T-99")         # missing
    _ticket(tmp_path, "T-6", typ="feature")         # Feature with no Epic
    _ticket(tmp_path, "T-7", parent="T-6")
    t = h.load(tmp_path)
    assert h.resolve(t, "T-3") == ("T-2", "T-1")
    assert [h.resolve(t, x) for x in ("T-4", "T-5", "T-7", "T-6")] == [None] * 4
    assert h.resolve_path(t["T-3"]["path"]) and not h.resolve_path(t["T-7"]["path"])


def test_status_counts_every_unresolved_ticket(tmp_path, capsys):
    _ticket(tmp_path, "T-1", typ="epic")
    _ticket(tmp_path, "T-2", typ="feature", parent="T-1")
    _ticket(tmp_path, "T-3", parent="T-2")
    assert h.main(["--status", str(tmp_path)]) == 0
    assert capsys.readouterr().out == ""
    _ticket(tmp_path, "T-4")                           # orphan
    _ticket(tmp_path, "T-5", lane="done")              # done orphan
    _ticket(tmp_path, "T-6", typ="feature")            # Feature with no Epic
    _ticket(tmp_path, "T-7", parent="T-6")
    assert h.main(["--status", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("4 ticket(s) are not in an Epic and a Feature yet") and "/adt-group" in out


def _remote(number, title, typ, parent=None, **kw):
    body = f"x\n\n<!-- adt:meta {json.dumps({'parent': parent})} -->" if parent else "x"
    return {"number": number, "title": title, "type": typ,
            "body": body + f"\n\n<!-- adt: slug={typ}-{number} -->", **kw}


def test_apply_finds_epics_another_machine_made(tmp_path, monkeypatch):
    # Labelled adt:filing on GitHub and absent here: the pull would skip both.
    _stub(tmp_path, monkeypatch, remote=[_remote(50, "Billing", "epic"),
                                         _remote(51, "Invoices", "feature", "T-50")])
    cache = tmp_path / "cache"
    _ticket(cache, "T-1")
    _apply(tmp_path, cache, "# Epic: Billing\n## Feature: Invoices\n- T-1 one\n")
    assert _creates(tmp_path) == []
    assert h.load(cache)["T-1"]["parent"] == "T-51"


def test_listing_reads_every_page_and_closed_issues(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, two_pages="epic",
          remote=[_remote(50, "Billing", "epic", page=2, state="closed")])
    cache = tmp_path / "cache"
    _ticket(cache, "T-1")
    _apply(tmp_path, cache, "# Epic: Billing\n## Feature: Invoices\n- T-1 one\n")
    assert [next(f[6:] for f in c if f.startswith("title=")) for c in _creates(tmp_path)] \
        == ["Invoices"]


def test_same_feature_title_under_another_epic_is_created(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, remote=[_remote(50, "A", "epic"), _remote(52, "B", "epic"),
                                         _remote(51, "Search", "feature", "T-50")])
    cache = tmp_path / "cache"
    _ticket(cache, "T-1")
    _apply(tmp_path, cache, "# Epic: B\n## Feature: Search\n- T-1 one\n")
    created = _creates(tmp_path)
    assert len(created) == 1 and "title=Search" in created[0]
    t = h.load(cache)
    assert t[t["T-1"]["parent"]]["parent"] == "T-52"


def test_lock_is_released_after_success_and_failure(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    _ticket(cache, "T-1")
    _apply(tmp_path, cache)
    assert _state(tmp_path)["lock"] == ""
    tmp2 = tmp_path / "b"
    tmp2.mkdir()
    _stub(tmp2, monkeypatch, fail_create=True)
    _ticket(tmp2 / "cache", "T-1")
    try:
        _apply(tmp2, tmp2 / "cache")
        raise AssertionError("expected the create to fail")
    except h.HierarchyError:
        pass
    assert _state(tmp2)["lock"] == ""

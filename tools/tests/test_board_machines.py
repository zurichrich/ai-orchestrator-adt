# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""The board's footer lists the machines syncing the repo and the ADT each last
reported (ADT-384), checked against the rendered HTML."""

from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import build_kanban  # noqa: E402


def _ago(hours):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _machine(login="zurichrich", commit="fc1c22d00b300757355f93f6b657f039b03cec65",
             hours=2, this=False, version="0.1.0", os_name="Darwin"):
    return {"login": login, "os": os_name, "version": version, "commit": commit,
            "reported_at": _ago(hours), "comment_id": 1, "this_machine": this}


def _board(tmp_path: Path, machines) -> str:
    build_kanban.configure(str(tmp_path), machines=machines)
    d = tmp_path / ".adt" / "backlog" / "enhancements" / "ideas"
    d.mkdir(parents=True, exist_ok=True)
    (d / "x.md").write_text(
        "---\nslug: x\npriority: P1\nsize: S\nid: ADT-1\n"
        "created: 2026-09-01T10:00:00Z\n---\n\n# X\n\nhook.\n")
    return build_kanban.render_html(build_kanban.load_items())


def _footer(html: str) -> str:
    m = re.search(r'<footer id="board-machines".*?</footer>', html, re.S)
    assert m, "board rendered no machines footer"
    return m.group(0)


def test_footer_lists_each_machine_version_commit_and_last_report(tmp_path):
    f = _footer(_board(tmp_path, [
        _machine(hours=2, this=True),
        _machine(login="teammate", commit="8c6284f816d7498cf4fd67da4f2cb4db277e30ce",
                 hours=30, os_name="Linux"),
    ]))
    lines = re.findall(r'<p class="machine">(.*?)</p>', f)
    assert lines == [
        "zurichrich · Darwin · v0.1.0 @fc1c22d · reported 2h ago (this machine)",
        "teammate · Linux · v0.1.0 @8c6284f · reported 1d ago",
    ], lines


def test_footer_lists_newest_report_first(tmp_path):
    f = _footer(_board(tmp_path, [
        _machine(login="oldest", hours=100),
        _machine(login="newest", hours=1),
        _machine(login="middle", hours=20),
    ]))
    order = re.findall(r'<p class="machine">([a-z]+) ·', f)
    assert order == ["newest", "middle", "oldest"], order


def test_footer_escapes_reported_values(tmp_path):
    f = _footer(_board(tmp_path, [_machine(
        login="<img src=x onerror=alert(1)>", version='1"><script>x</script>',
        os_name="Dar&win")]))
    assert "<img" not in f and "<script>" not in f
    assert "&lt;img src=x onerror=alert(1)&gt;" in f
    assert "Dar&amp;win" in f


def test_render_passes_machines_from_state_file_to_the_board(tmp_path):
    # The live path: adt_watch._render reads .adt/state/machines.json under the
    # code checkout and hands it to build_kanban.run, which renders the footer.
    import json
    import adt_watch
    code = tmp_path / "code"
    (code / ".adt" / "state").mkdir(parents=True)
    (code / ".adt" / "state" / "machines.json").write_text(json.dumps([_machine(this=True)]))
    cache = tmp_path / "cache"
    (cache / "tasks" / "ideas").mkdir(parents=True)
    adt_watch._render(str(cache), {"repo": "o/r", "id_prefix": "ADT"},
                      code_root=str(code), quiet=True, pools=[], protection=None)
    f = _footer((cache / "kanban.html").read_text())
    assert "zurichrich · Darwin · v0.1.0 @fc1c22d · reported 2h ago (this machine)" in f


def test_no_footer_without_machines_file(tmp_path):
    import adt_machines
    # No .adt/state/machines.json: what adt_watch._render passes is [].
    assert adt_machines.load_machines(str(tmp_path)) == []
    for machines in (adt_machines.load_machines(str(tmp_path)), None):
        html = _board(tmp_path, machines)
        assert 'id="board-machines"' not in html

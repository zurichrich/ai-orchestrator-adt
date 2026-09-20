# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""AO-007: a lane move made on one machine reaches every other machine.

The push sends a lane up and nothing brought one back, so every machine after
the first kept whatever lane it last saw. These tests run TWO caches against one
set of Issues, which is what the rest of the sync suite never does — the defect
cannot appear with a single cache, because up-only is sufficient when one
machine makes all the moves.

The renderer is in the loop on purpose. `build_kanban.sync_stage_frontmatter`
rewrites `stage:` FROM the folder on every render, so a fix that writes the
pulled lane into the frontmatter and leaves the file where it is gets reverted,
re-arms the push, and sends the stale lane back up — on every tick, on both
machines. That flip only shows from the second tick onwards, which is why
`test_converges_with_zero_api_calls_over_three_ticks` runs three.

No `gh` call is made; `_gh` is monkeypatched.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_sync  # noqa: E402
import build_kanban  # noqa: E402
from ticket_serializer import parse_md  # noqa: E402


# --------------------------------------------------------------------- fakes
class FakeGh:
    """Serves the REST issues list; records every argv it was handed."""

    def __init__(self, issues):
        self.issues, self.calls = issues, []

    def __call__(self, args, input_text=None):
        self.calls.append(args)
        if args[0] == "api" and "/issues?" in args[-1]:
            return json.dumps(self.issues)
        return "{}"

    def per_ticket_calls(self):
        """Every call EXCEPT the pull's one issue-list request.

        The list request is the pull's fixed per-tick cost and is not what
        ADT-147 was about. The spend lives in the per-ticket push path
        (`_current_issue`, `gh issue edit`, `close`/`reopen`, the board write),
        and that is what must stay at zero however many machines are syncing.
        """
        return [c for c in self.calls
                if not (c[0] == "api" and "/issues?" in c[-1])]


def _rest_issue(number=7, stage="done", state="closed", updated="2026-09-20T10:00:00Z"):
    return {
        "number": number,
        "node_id": f"I_node{number}",
        "title": "Sample",
        "body": "# Sample\n\nBody text.\n",
        "state": state,
        "state_reason": "completed" if state == "closed" else None,
        "labels": [{"name": "P1"}, {"name": f"stage:{stage}"},
                   {"name": "type:bug"}],
        "assignees": [],
        "milestone": None,
        "created_at": "2026-09-01T09:00:00Z",
        "updated_at": updated,
        "closed_at": "2026-09-20T10:00:00Z" if state == "closed" else None,
        "user": {"login": "someone"},
    }


TICKET = """---
slug: t
id: AO-7
title: Sample
type: bug
priority: P1
stage: {stage}
state: {state}
created: 2026-09-01T09:00:00Z
updated: 2026-09-20T09:00:00Z
closed: null
assignees: []
issue_number: 7
issue_node_id: I_node7
comments: []
---

# Sample

Body text.
"""


def _cache_with_ticket(root, lane="blocked", state="open", typ="bugs"):
    """Write one ticket into <root>/<typ>/<lane>/t.md and return its path."""
    d = pathlib.Path(root) / typ / lane
    d.mkdir(parents=True, exist_ok=True)
    p = d / "t.md"
    p.write_text(TICKET.format(stage=lane, state=state))
    return str(p)


def _cfg(cache):
    return {"repo": "o/r", "cache_dir": str(cache)}


def _seed_converged(cfg, path):
    """Record `path` in the sidecar as already-pushed.

    Guard 3 declines when the file's hash is absent from the sidecar, so a test
    that skips this exercises nothing — the relocation never runs and every
    assertion about it passes vacuously.
    """
    adt_sync._persist_state(cfg, {path: adt_sync._push_hash(
        parse_md(open(path).read()))})


def _render_frontmatter(cache):
    """Run the REAL renderer's frontmatter pass over `cache`.

    `build_kanban.configure()` re-points the module-level `BL` root, which is
    the only thing that made `load_items()` awkward to call from a test; three
    existing board tests do the same. Going through the real loader is the
    point of this helper — the failure it exists to catch is "the render
    reverts the lane the pull just carried down", and a hand-rolled copy of
    the loader would keep passing after the renderer changed.

    Returns the number of files `sync_stage_frontmatter` rewrote. Zero means
    the render agreed with what the pull left behind.
    """
    build_kanban.configure(str(cache), backlog_root=".")
    return build_kanban.sync_stage_frontmatter(build_kanban.load_items())


def _mk_project(tmp_path, name):
    """A project root + its own cache, the shape reconcile_all's load_config
    expects. Two of these is what makes a test two machines."""
    root = tmp_path / name
    (root / ".adt").mkdir(parents=True, exist_ok=True)
    cache = tmp_path / f"{name}-cache"
    cache.mkdir(parents=True, exist_ok=True)
    (root / ".adt" / "config.yaml").write_text(
        f"project: {name}\nrepo: o/r\nid_prefix: AO\ncache_dir: {cache}\n")
    return str(root), cache


# ------------------------------------------------------------------- moving
def test_relocates_to_github_lane(tmp_path, monkeypatch):
    """The pulled lane MOVES the file, and the frontmatter follows it."""
    cache = tmp_path / "cache"
    path = _cache_with_ticket(cache, lane="blocked", state="open")
    cfg = _cfg(cache)
    _seed_converged(cfg, path)
    monkeypatch.setattr(adt_sync, "_gh", FakeGh([_rest_issue()]))

    res = adt_sync.pull_all(cfg)

    assert [r["action"] for r in res] == ["relocated"]
    moved = cache / "bugs" / "done" / "t.md"
    assert moved.is_file(), "the file must be in the lane GitHub names"
    assert not (cache / "bugs" / "blocked" / "t.md").exists()
    d = parse_md(moved.read_text())
    assert d["stage"] == "done"
    assert d["state"] == "closed"
    # The type folder is carried over, never re-derived: nothing in ADT
    # relocates a ticket between type folders (ADT-155).
    assert moved.parent.parent.name == "bugs"
    # A render now agrees with the pull instead of reverting it.
    assert _render_frontmatter(cache) == 0

    # And the recorded hash is the one the NEXT tick computes off disk. If
    # these differ the push re-arms, which is the whole failure being fixed.
    assert res[0]["hash"] == adt_sync._push_hash(parse_md(moved.read_text()))
    assert res[0]["old_path"] == path


def test_relocation_refuses_an_unknown_lane(tmp_path, monkeypatch):
    """Guard 1: a hand-added `stage:` label must not invent a folder."""
    cache = tmp_path / "cache"
    path = _cache_with_ticket(cache, lane="blocked")
    cfg = _cfg(cache)
    _seed_converged(cfg, path)
    monkeypatch.setattr(adt_sync, "_gh",
                        FakeGh([_rest_issue(stage="not-a-lane", state="open")]))

    adt_sync.pull_all(cfg)

    assert (cache / "bugs" / "blocked" / "t.md").is_file()
    assert not (cache / "bugs" / "not-a-lane").exists()


def test_relocation_refuses_to_overwrite_a_file_already_there(tmp_path,
                                                              monkeypatch):
    """Guard 4: os.rename overwrites silently, so a collision must refuse.

    Asserts on the DESTINATION's bytes, not on the call returning — the whole
    risk is that a real ticket is deleted.
    """
    cache = tmp_path / "cache"
    path = _cache_with_ticket(cache, lane="blocked")
    clash = cache / "bugs" / "done"
    clash.mkdir(parents=True, exist_ok=True)
    (clash / "t.md").write_text("---\nslug: t\nstage: done\n---\n\nOTHER\n")
    cfg = _cfg(cache)
    _seed_converged(cfg, path)
    monkeypatch.setattr(adt_sync, "_gh", FakeGh([_rest_issue()]))

    adt_sync.pull_all(cfg)

    assert "OTHER" in (clash / "t.md").read_text(), "existing ticket destroyed"
    assert (cache / "bugs" / "blocked" / "t.md").is_file()


# ------------------------------------------------------- local work survives
def test_unpushed_local_move_is_not_overwritten(tmp_path, monkeypatch):
    """The conflict rule, both halves.

    Guard 3: a file carrying an unpushed local change keeps its lane.
    Guard 2: a `mv` no render has caught up with yet keeps its lane.
    """
    # -- guard 3: folder and frontmatter agree, but the hash does not match --
    cache = tmp_path / "c3"
    path = _cache_with_ticket(cache, lane="qa")
    cfg = _cfg(cache)
    adt_sync._persist_state(cfg, {path: "a-stale-hash-from-an-older-push"})
    monkeypatch.setattr(adt_sync, "_gh",
                        FakeGh([_rest_issue(stage="building", state="open")]))

    adt_sync.pull_all(cfg)

    assert (cache / "bugs" / "qa" / "t.md").is_file(), \
        "an unpushed local lane move was overwritten by the pull"
    assert not (cache / "bugs" / "building" / "t.md").exists()

    # -- guard 2: a local mv the render has not caught up with --------------
    # The file sits in qa/ while its frontmatter still reads `stage: building`,
    # which is exactly the window between a playbook's `mv` and the next render.
    cache2 = tmp_path / "c2"
    d = cache2 / "bugs" / "qa"
    d.mkdir(parents=True, exist_ok=True)
    p2 = d / "t.md"
    p2.write_text(TICKET.format(stage="building", state="open"))
    cfg2 = _cfg(cache2)
    _seed_converged(cfg2, str(p2))
    monkeypatch.setattr(adt_sync, "_gh", FakeGh([_rest_issue()]))

    adt_sync.pull_all(cfg2)

    assert p2.is_file(), "a local mv in flight was overwritten by the pull"
    assert not (cache2 / "bugs" / "done" / "t.md").exists()


def test_closed_issue_under_a_non_done_lane_does_not_reopen(tmp_path,
                                                            monkeypatch):
    """`state` must be DERIVED from the new lane, never copied from the Issue.

    The case: machine A moved the ticket to `blocked`, then someone closed the
    Issue on github.com. So the Issue is closed while its `stage:` label reads
    a non-done lane — which is what 7 of the 12 tickets in this ticket's own
    measurement looked like. Machine B has it in a third lane, so the
    relocation fires.

    Copying `state: closed` onto a file that lands in `blocked/` means the next
    render derives `open` from the folder and rewrites it, `state` is inside
    the push hash, the push re-arms, and `gh issue reopen` runs every tick on
    both machines. That is ADT-147's flip coming back through the second field.
    """
    root, cache = _mk_project(tmp_path, "b")
    path = _cache_with_ticket(cache, lane="building", state="open")
    _seed_converged(_cfg(cache), path)

    fake = FakeGh([_rest_issue(stage="blocked", state="closed")])
    monkeypatch.setattr(adt_sync, "_gh", fake)
    for fn in ("checkpoint_tokens", "restamp_closed", "checkpoint_stamp"):
        monkeypatch.setattr(adt_sync, fn, lambda *a, **k: [])

    for _ in range(3):
        adt_sync.reconcile_all(root, pull=True)
        # Zero means the render found nothing to correct. A non-zero count here
        # IS the flip: the render disagreeing with what the pull wrote.
        assert _render_frontmatter(cache) == 0, \
            "the render rewrote the file the pull just wrote — push will re-arm"

    assert fake.per_ticket_calls() == [], \
        "a closed Issue under a non-done lane provoked a per-ticket call"
    moved = cache / "bugs" / "blocked" / "t.md"
    assert moved.is_file()
    assert parse_md(moved.read_text())["state"] == "open", \
        "`state` must follow the lane, not the Issue"


# --------------------------------------------------- the f = 0 property
def test_converges_with_zero_api_calls_over_three_ticks(tmp_path, monkeypatch):
    """Two machines, three ticks, a render after every one.

    Machine A already has the ticket in `done/`; machine B is the stale one.
    After B's first tick repairs itself, neither machine may make a per-ticket
    call again — that is `f = 0`, and it is what stops the ADT-147 storm.

    One tick is not enough to see this. The frontmatter-only version of this fix
    also looks correct on tick one; the render reverts it afterwards and the
    flip appears on tick two.
    """
    root_a, cache_a = _mk_project(tmp_path, "a")
    root_b, cache_b = _mk_project(tmp_path, "b")
    path_a = _cache_with_ticket(cache_a, lane="done", state="closed")
    path_b = _cache_with_ticket(cache_b, lane="blocked", state="open")
    _seed_converged(_cfg(cache_a), path_a)
    _seed_converged(_cfg(cache_b), path_b)

    fake = FakeGh([_rest_issue()])
    monkeypatch.setattr(adt_sync, "_gh", fake)
    # The token checkpoint is a separate mechanism with its own suite; stubbing
    # it here keeps the calls this test counts to the push/pull loop it is about.
    for fn in ("checkpoint_tokens", "restamp_closed", "checkpoint_stamp"):
        monkeypatch.setattr(adt_sync, fn, lambda *a, **k: [])

    seen = []
    for tick in range(3):
        for root, cache in ((root_a, cache_a), (root_b, cache_b)):
            before = len(fake.per_ticket_calls())
            res = adt_sync.reconcile_all(root, pull=True)
            _render_frontmatter(cache)
            seen.append({"tick": tick, "root": root,
                         "actions": sorted({r["action"] for r in res}),
                         "calls": len(fake.per_ticket_calls()) - before})

    # Tick 1 is where B repairs itself.
    assert (cache_b / "bugs" / "done" / "t.md").is_file(), seen
    assert not (cache_b / "bugs" / "blocked" / "t.md").exists()

    # Ticks 2 and 3 must be silent on BOTH machines. A machine that keeps
    # talking is the flip this ticket exists to prevent.
    after_repair = [s for s in seen if s["tick"] > 0]
    assert all(s["calls"] == 0 for s in after_repair), seen
    assert all(s["actions"] in ([], ["noop"]) for s in after_repair), seen

    # And neither board drifted back.
    for cache in (cache_a, cache_b):
        d = parse_md((cache / "bugs" / "done" / "t.md").read_text())
        assert (d["stage"], d["state"]) == ("done", "closed"), seen

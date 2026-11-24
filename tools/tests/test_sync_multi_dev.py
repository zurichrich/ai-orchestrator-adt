# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""AO-027: two developers, on two machines, work the same ticket.

Every test here runs two caches (machine A and machine B) against ONE fake
GitHub that keeps state: an edit A pushes is what B's pull reads. That is the
only way to see the defects this ticket fixes. With one cache, an up-only body
and frontmatter that never leaves the machine both look correct.

The sync is driven through its real entry point, `adt_sync.reconcile_all`, one
call per machine per tick. Only `_gh` (the GitHub boundary) and the three token
and cost side passes, which talk to ledgers these tests do not have, are
stubbed.
"""
from __future__ import annotations

import copy
import itertools
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_sync  # noqa: E402
import ticket_serializer  # noqa: E402
from ticket_serializer import emit_md, parse_md, to_issue  # noqa: E402

REPO = "o/r"


# --------------------------------------------------------------------- fakes
class FakeGitHub:
    """One repo's Issues and comments, mutated by the calls the sync makes."""

    def __init__(self):
        self.issues: dict[int, dict] = {}
        self.comments: dict[int, list] = {}
        self.calls: list = []
        self._clock = itertools.count(1)
        self._ids = itertools.count(9001)

    # -- state helpers ---------------------------------------------------
    def _stamp(self, issue):
        issue["updated_at"] = "2026-10-01T10:%02d:%02dZ" % divmod(
            next(self._clock), 60)

    def seed(self, number, ticket: dict, body=None):
        """Put an Issue on GitHub as a push of `ticket` would have left it."""
        want = to_issue(ticket)
        issue = {
            "number": number, "node_id": f"I_{number}",
            "title": want["title"],
            "body": want["body"] if body is None else body,
            "state": want["state"] or "open",
            "state_reason": want.get("stateReason"),
            "labels": [dict(l) for l in want["labels"]],
            "assignees": [], "milestone": None,
            "created_at": "2026-09-01T09:00:00Z", "closed_at": None,
            "user": {"login": "someone"},
        }
        self._stamp(issue)
        self.issues[number] = issue
        self.comments.setdefault(number, [])

    def label_names(self, number):
        return sorted(l["name"] for l in self.issues[number]["labels"])

    def conflict_comments(self, number):
        return [c for c in self.comments.get(number, [])
                if c["body"].startswith("<!-- adt:conflict ")]

    def edits(self):
        return [c for c in self.calls if c[:2] == ["issue", "edit"]]

    def per_ticket_calls(self):
        """Every call except the pull's issue-list request."""
        return [c for c in self.calls
                if not (c[0] == "api" and "/issues?" in c[-1])]

    # -- the gh boundary -------------------------------------------------
    def __call__(self, args, input_text=None):
        args = list(args)
        self.calls.append(args)
        if args[0] == "api":
            return self._api(args)
        if args[0] == "issue":
            return self._issue(args)
        if args[0] == "label":
            return "[]" if args[1] == "list" else ""
        raise AssertionError(f"unexpected gh call {args}")

    @staticmethod
    def _flag(args, name):
        vals, it = [], iter(range(len(args)))
        for i in it:
            if args[i] == name and i + 1 < len(args):
                vals.append(args[i + 1])
        return vals

    def _api(self, args):
        method = args[args.index("-X") + 1] if "-X" in args else "GET"
        endpoint = next(a for a in args if a.startswith("repos/"))
        path = endpoint.split("?")[0][len(f"repos/{REPO}/"):]
        parts = path.split("/")
        fields = dict(f.split("=", 1) for f in self._flag(args, "-f"))
        if parts == ["issues"]:
            return json.dumps([copy.deepcopy(i) for i in self.issues.values()])
        if parts[0] == "issues" and parts[1] == "comments" and method == "PATCH":
            cid = int(parts[2])
            for lst in self.comments.values():
                for c in lst:
                    if c["id"] == cid:
                        c["body"] = fields["body"]
                        return json.dumps(c)
            raise adt_sync.GhError("HTTP 404: Not Found")
        number = int(parts[1])
        if len(parts) == 2:
            return json.dumps(copy.deepcopy(self.issues.get(number) or {}))
        if parts[2] == "comments" and method == "POST":
            c = {"id": next(self._ids), "body": fields["body"],
                 "user": {"login": "machine"},
                 "created_at": "2026-10-01T11:00:00Z"}
            self.comments.setdefault(number, []).append(c)
            self._stamp(self.issues[number])
            return json.dumps(c)
        if parts[2] == "comments":
            page = 1
            if "page=" in endpoint:
                page = int(endpoint.split("page=")[1].split("&")[0])
            return json.dumps(copy.deepcopy(
                self.comments.get(number, [])) if page == 1 else [])
        raise AssertionError(f"unexpected api call {args}")

    def _issue(self, args):
        verb, number = args[1], int(args[2])
        issue = self.issues[number]
        if verb == "edit":
            for t in self._flag(args, "--title"):
                issue["title"] = t
            for b in self._flag(args, "--body"):
                issue["body"] = b
            names = {l["name"] for l in issue["labels"]}
            names |= set(self._flag(args, "--add-label"))
            names -= set(self._flag(args, "--remove-label"))
            issue["labels"] = [{"name": n} for n in sorted(names)]
        elif verb == "close":
            issue["state"], issue["state_reason"] = "closed", "completed"
        elif verb == "reopen":
            issue["state"], issue["state_reason"] = "open", None
        else:
            raise AssertionError(f"unexpected issue verb {args}")
        self._stamp(issue)
        return ""


# ------------------------------------------------------------------ fixtures
BODY = """# Shared ticket

## Problem (user)
Two people work this.

## Plan (PM)
### Definition of Done (machine-checkable)

(the plan as first written)

## Build log (Dev)
(empty)
"""


def _ticket(**over) -> dict:
    """A ticket carrying every field from AO-027's gap A, an unknown future
    field (`epic`, as AO-028 will add), and a DoD."""
    t = {
        "slug": "shared", "id": "AO-042", "title": "Shared ticket",
        "type": "enhancement", "priority": "P1", "track": "full",
        "stage": "building", "state": "open", "state_reason": None,
        "created": "2026-09-01", "created_by": "po", "size": "L",
        "assignees": [], "milestone": None,
        "security_review_required": False, "ui_review_required": False,
        "issue_number": 42, "issue_node_id": "I_42", "comments": [],
        "commits": ["abc1234", "def5678"], "tokens": "94098",
        "cost_usd": "8.1", "cost_tier": "measured", "follow_ons": "0",
        "recurring_cost": "none", "plan_approved": True, "merged_at": "0889e80",
        "epic": "Delivery overview",
        "gate_effects": [{"kind": "record", "gate": "coverage", "round": "1",
                          "verdict": "COVERED", "hash": "051a1356b22f5780"}],
        "dod_snapshot": [{"n": "1", "hash": "fd8fa17bc7a2a5b9"}],
        "done_evidence": [{"id": "C1", "must_run": "true", "lane": "build"}],
        "body": BODY,
    }
    t.update(over)
    # Normalise through the file format, as a real cache file would be.
    return parse_md(emit_md(t))


class Machine:
    """One install: a project root with its own cache, driven by real ticks."""

    def __init__(self, tmp_path, name):
        self.root = tmp_path / name
        (self.root / ".adt").mkdir(parents=True)
        self.cache = tmp_path / f"{name}-cache"
        self.cache.mkdir()
        (self.root / ".adt" / "config.yaml").write_text(
            f"project: {name}\nrepo: {REPO}\nid_prefix: AO\n"
            f"cache_dir: {self.cache}\n")

    def path(self, lane="building"):
        return self.cache / "enhancements" / lane / "shared.md"

    def write(self, ticket: dict, lane="building"):
        p = self.path(lane)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(emit_md(ticket))
        return p

    def read(self, lane="building") -> dict:
        return parse_md(self.path(lane).read_text())

    def edit(self, fn, lane="building"):
        d = self.read(lane)
        fn(d)
        self.write(d, lane)

    def converge(self, lane="building"):
        """Record the file as already pushed: the pre-AO-027 steady state."""
        cfg = adt_sync.load_config(str(self.root))
        p = str(self.path(lane))
        adt_sync._persist_state(cfg, {p: adt_sync._push_hash(
            parse_md(open(p).read()))})

    def tick(self, gh: FakeGitHub):
        before = len(gh.calls)
        res = adt_sync.reconcile_all(str(self.root), pull=True)
        return res, gh.calls[before:]


def _wire(monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setattr(adt_sync, "_gh", gh)
    monkeypatch.setattr(adt_sync, "_machine_id", lambda: "machine-b")
    for fn in ("checkpoint_tokens", "restamp_closed", "checkpoint_stamp"):
        monkeypatch.setattr(adt_sync, fn, lambda *a, **k: [])
    return gh


def _two_machines(tmp_path, monkeypatch, ticket=None):
    """A and B hold the same ticket and GitHub holds its push. One warm-up
    tick each, so both have a baseline, as any upgraded install has after its
    first full sweep."""
    gh = _wire(monkeypatch)
    t = ticket or _ticket()
    gh.seed(42, t)
    a, b = Machine(tmp_path, "a"), Machine(tmp_path, "b")
    a.write(t)
    b.write(t)
    a.tick(gh)
    b.tick(gh)
    return gh, a, b


def _set_plan(text):
    def fn(d):
        d["body"] = d["body"].replace("(the plan as first written)", text)
    return fn


def _set_log(text):
    def fn(d):
        d["body"] = d["body"].replace("## Build log (Dev)\n(empty)",
                                      "## Build log (Dev)\n" + text)
    return fn


# --------------------------------------------------------------------- tests
def test_rebuild_restores_every_frontmatter_field(tmp_path, monkeypatch):
    """C1: a cache file rebuilt from its Issue gets every field back,
    including one this code has never heard of."""
    gh = _wire(monkeypatch)
    a = _ticket()
    gh.seed(42, a)
    b = Machine(tmp_path, "b")

    b.tick(gh)

    rebuilt = b.read()
    # Fields that come from the Issue's own native fields, not the trailer.
    native = set(adt_sync._PULL_OWNED) | {"created", "created_by", "comments",
                                          "state_reason"}
    for key, value in a.items():
        if key in native or key == "body":
            continue
        assert rebuilt.get(key) == value, key
    assert rebuilt["epic"] == "Delivery overview"
    assert rebuilt["plan_approved"] is True
    assert to_issue(rebuilt)["body"] == to_issue(a)["body"]
    # The trailer's exclusions must cover what the pull owns, or a pulled
    # field would also travel up in the body and the two would fight.
    assert set(adt_sync._PULL_OWNED) <= ticket_serializer._META_EXCLUDED


def test_body_edit_on_one_machine_reaches_the_other(tmp_path, monkeypatch):
    """C2: A's plan and DoD reach B within one tick of A pushing, and B does
    not send them back."""
    gh, a, b = _two_machines(tmp_path, monkeypatch)
    new_dod = [{"id": "C1", "must_run": "python3 -c 'pass'", "lane": "build"},
               {"id": "C2", "must_run": "true", "lane": "qa"}]

    def edit(d):
        _set_plan("A's real plan")(d)
        d["done_evidence"] = new_dod
    a.edit(edit)

    a.tick(gh)
    b.tick(gh)

    got = b.read()
    assert "A's real plan" in got["body"]
    assert got["done_evidence"] == parse_md(emit_md(
        dict(_ticket(), done_evidence=new_dod)))["done_evidence"]
    _, calls = b.tick(gh)
    assert not [c for c in calls if c[:2] == ["issue", "edit"]], calls


def test_both_changed_keeps_both_and_flags(tmp_path, monkeypatch):
    """C3: both machines edited; neither copy is overwritten, the conflict is
    flagged where both developers see it, and it stays put across ticks."""
    gh, a, b = _two_machines(tmp_path, monkeypatch)
    labels_before = gh.label_names(42)
    state_before = gh.issues[42]["state"]
    a.edit(_set_plan("A's plan"))

    # B edits the body AND moves the lane, on top of the body A replaced.
    def b_edit(d):
        _set_log("B's build notes")(d)
        d["stage"] = "qa"
    b_file = b.read()
    b_edit(b_file)
    b.path().unlink()
    b.write(b_file, lane="qa")

    a.tick(gh)
    for _ in range(3):
        b.tick(gh)

    issue = gh.issues[42]
    assert "A's plan" in issue["body"]
    assert "B's build notes" not in issue["body"]
    assert gh.label_names(42) == labels_before, "a lane move leaked through"
    assert issue["state"] == state_before
    mine = b.read(lane="qa")
    assert "B's build notes" in mine["body"]
    assert "A's plan" not in mine["body"], "the pull overwrote B's work"
    assert mine["stage"] == "qa"
    assert mine.get("sync_conflict")
    copy_path = pathlib.Path(str(b.path("qa")) + ".github")
    assert "A's plan" in copy_path.read_text()
    assert len(gh.conflict_comments(42)) == 1


def test_resolving_a_conflict_pushes_the_merged_copy(tmp_path, monkeypatch):
    """C4: B merges by hand and deletes `sync_conflict:`; the merge goes up."""
    gh, a, b = _two_machines(tmp_path, monkeypatch)
    a.edit(_set_plan("A's plan"))
    b.edit(_set_log("B's build notes"))
    a.tick(gh)
    b.tick(gh)
    assert b.read().get("sync_conflict")

    def merge(d):
        _set_plan("A's plan")(d)
        d.pop("sync_conflict")
    b.edit(merge)
    b.tick(gh)

    body = gh.issues[42]["body"]
    assert "A's plan" in body and "B's build notes" in body
    assert not pathlib.Path(str(b.path()) + ".github").exists()
    assert "sync_conflict" not in b.read()
    [comment] = gh.conflict_comments(42)
    assert "resolved" in comment["body"]
    # And A receives the merge, so the two machines end up equal.
    a.tick(gh)
    assert "B's build notes" in a.read()["body"]


def test_keep_github_version_by_deleting_the_file(tmp_path, monkeypatch):
    """C12: deleting the conflicted file is the other way to resolve it."""
    gh, a, b = _two_machines(tmp_path, monkeypatch)
    a.edit(_set_plan("A's plan"))
    b.edit(_set_log("B's build notes"))
    a.tick(gh)
    b.tick(gh)
    copy_path = pathlib.Path(str(b.path()) + ".github")
    assert copy_path.exists()
    body_before = gh.issues[42]["body"]

    b.path().unlink()
    _, calls = b.tick(gh)

    rebuilt = b.read()
    assert "A's plan" in rebuilt["body"]
    assert rebuilt["plan_approved"] is True and rebuilt["epic"]
    assert not copy_path.exists()
    cfg = adt_sync.load_config(str(b.root))
    assert "42" not in (adt_sync._load_state_doc(cfg).get("conflicts") or {})
    [comment] = gh.conflict_comments(42)
    assert "resolved" in comment["body"]
    assert gh.issues[42]["body"] == body_before
    assert not [c for c in calls if c[:2] == ["issue", "edit"]]


def test_second_remote_change_before_resolution_reconflicts(tmp_path,
                                                            monkeypatch):
    """C13: deleting `sync_conflict:` is a resolution only if GitHub has not
    moved again since the conflict was found."""
    gh, a, b = _two_machines(tmp_path, monkeypatch)
    a.edit(_set_plan("A's plan"))
    b.edit(_set_log("B's build notes"))
    a.tick(gh)
    b.tick(gh)
    a.edit(lambda d: d.update(body=d["body"].replace(
        "A's plan", "A's plan, second pass")))
    a.tick(gh)
    edits_before = len(gh.edits())

    b.edit(lambda d: d.pop("sync_conflict"))
    _, calls = b.tick(gh)

    assert len(gh.edits()) == edits_before, "B overwrote A's second edit"
    assert "A's plan, second pass" in gh.issues[42]["body"]
    assert b.read().get("sync_conflict")
    copy_text = pathlib.Path(str(b.path()) + ".github").read_text()
    assert "A's plan, second pass" in copy_text
    assert len(gh.conflict_comments(42)) == 1


def test_meta_trailer_round_trip_is_byte_identical(tmp_path, monkeypatch):
    """C14: a value containing `-->`, and prose that names the marker, both
    survive the trip to the Issue and back."""
    body = ("# T\n\nThe trailer looks like `<!-- adt:meta {json} -->`.\n"
            "<!-- adt:meta {\"x\":1} -->\n\n")
    t = _ticket(body=body, epic="tricky --> value", note="a\nb")
    issue = to_issue(t)
    # The writer's line is the LAST marker-shaped line; the body's own copy
    # above it must be left as prose.
    meta_lines = [ln for ln in issue["body"].splitlines()
                  if ln.startswith("<!-- adt:meta {")]
    assert meta_lines[0] == '<!-- adt:meta {"x":1} -->'
    assert "tricky --\\u003e value" in meta_lines[-1]
    assert "-->" not in meta_lines[-1][:-3]
    back = ticket_serializer.from_issue(issue, base={})
    assert back["body"] == t["body"]
    assert back["epic"] == "tricky --> value"
    assert ticket_serializer.ticket_meta(back) == ticket_serializer.ticket_meta(t)


def test_plan_approved_reaches_second_machine(tmp_path, monkeypatch):
    """C5: the approval /adt-build-todone starts on travels between machines."""
    gh, a, b = _two_machines(tmp_path, monkeypatch,
                             ticket=_ticket(plan_approved=False))
    a.edit(lambda d: d.update(plan_approved=True))

    a.tick(gh)
    b.tick(gh)

    assert b.read()["plan_approved"] is True


def test_single_machine_converges_with_no_extra_calls(tmp_path, monkeypatch):
    """C6: one developer on one machine sees no conflicts and no extra calls."""
    gh = _wire(monkeypatch)
    t = _ticket()
    gh.seed(42, t)
    a = Machine(tmp_path, "a")
    a.write(t)
    a.tick(gh)
    a.edit(_set_plan("my plan"))
    res, _ = a.tick(gh)
    assert "my plan" in gh.issues[42]["body"]

    for _ in range(3):
        res, calls = a.tick(gh)
        assert [c for c in calls
                if not (c[0] == "api" and "/issues?" in c[-1])] == [], calls
        assert not [r for r in res if r.get("action") == "conflict"], res
    assert not gh.conflict_comments(42)


def test_existing_issue_without_meta_gains_it_from_an_in_sync_machine(
        tmp_path, monkeypatch):
    """C7: the migration. Issues pushed before AO-027 carry no meta trailer.
    The in-sync machine publishes its metadata; the stale machine takes the
    Issue's text, keeps its own metadata, and never pushes."""
    gh = _wire(monkeypatch)
    current = _ticket()
    legacy = to_issue(current)["body"]
    legacy = (ticket_serializer._strip_meta_trailer(
        ticket_serializer._strip_slug_trailer(legacy))
        + "\n\n<!-- adt: slug=shared -->\n")
    gh.seed(42, current, body=legacy)
    a, b = Machine(tmp_path, "a"), Machine(tmp_path, "b")
    a.write(current)
    a.converge()
    stale = _ticket(body=BODY.replace("(the plan as first written)",
                                      "an old draft"), size="S")
    b.write(stale)
    b.converge()

    edits_by_b = []
    for _ in range(3):
        _, calls = b.tick(gh)
        edits_by_b += [c for c in calls if c[:2] == ["issue", "edit"]]
    took = b.read()
    assert "an old draft" not in took["body"]
    assert took["size"] == "S", "B lost its own metadata to an Issue with none"
    assert edits_by_b == [], "the stale machine pushed"

    a.tick(gh)
    a.tick(gh)
    meta = ticket_serializer.meta_from_body(
        ticket_serializer._strip_slug_trailer(gh.issues[42]["body"]))
    assert meta is not None and meta["size"] == "L"
    assert "(the plan as first written)" in gh.issues[42]["body"]

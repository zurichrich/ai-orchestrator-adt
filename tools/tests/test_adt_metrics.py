# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for adt_metrics: follow-on counts, post-merge defects, and the by-track split.

Unstamped tickets are kept apart from zero, and blocked spawns are never reported as interrupts.
"""
from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import adt_metrics  # noqa: E402


def _ticket(cache, typ, stage, name, follow_ons=None, blocked=0):
    d = os.path.join(cache, typ, stage)
    os.makedirs(d, exist_ok=True)
    fm = "---\nid: %s\n" % name.upper()
    if follow_ons is not None:
        fm += "follow_ons: %d\n" % follow_ons
    fm += "---\n\n# body\n"
    fm += "".join("## 2026-01-0%d 10:00 — BLOCKED\n" % (i + 1) for i in range(blocked))
    path = os.path.join(d, name + ".md")
    with open(path, "w") as fh:
        fh.write(fm)
    return path


def test_only_done_tickets_are_counted(tmp_path):
    c = str(tmp_path)
    _ticket(c, "bugs", "done", "a", follow_ons=0)
    _ticket(c, "enhancements", "done", "b", follow_ons=2)
    _ticket(c, "tasks", "ideas", "c", follow_ons=9)      # not closed
    _ticket(c, "tasks", "building", "d", follow_ons=9)   # not closed
    assert len(adt_metrics.closed_tickets(c)) == 2


def test_follow_on_rate(tmp_path, capsys):
    c = str(tmp_path)
    _ticket(c, "bugs", "done", "a", follow_ons=0)
    _ticket(c, "bugs", "done", "b", follow_ons=3)
    r = adt_metrics.report(c, str(tmp_path), out=io.StringIO())
    assert (r["stamped"], r["follow_ons"]) == (2, 3)


def test_ticket_without_follow_ons_is_unstamped_not_zero(tmp_path):
    c = str(tmp_path)
    _ticket(c, "bugs", "done", "old")                   # no follow_ons key
    _ticket(c, "bugs", "done", "new", follow_ons=2)
    r = adt_metrics.report(c, str(tmp_path), out=io.StringIO())
    assert r["closed"] == 2 and r["stamped"] == 1
    assert adt_metrics.read_ticket(
        os.path.join(c, "bugs", "done", "old.md"))["follow_ons"] is None


def test_no_stamped_tickets_reports_no_baseline_not_zero(tmp_path):
    c = str(tmp_path)
    _ticket(c, "bugs", "done", "old")
    buf = io.StringIO()
    adt_metrics.report(c, str(tmp_path), out=buf)
    assert "no baseline" in buf.getvalue()
    assert "follow-on rate:            0" not in buf.getvalue()


def test_blocked_spawns_are_never_reported_as_interrupts(tmp_path):
    c = str(tmp_path)
    _ticket(c, "bugs", "done", "a", follow_ons=0)
    # adt-deferral-guard.sh writes the project's .adt/state/deferral-guard.log.
    log = tmp_path / ".adt" / "state"
    log.mkdir(parents=True, exist_ok=True)
    (log / "deferral-guard.log").write_text(
        "DENY\tno human authorisation\tgh issue create\n"
        "ALLOW\t/adt-brief invocation\tgh issue create\n"
        "DENY\tno human authorisation\tgh issue create\n")
    buf = io.StringIO()
    r = adt_metrics.report(c, str(tmp_path), out=buf)
    text = buf.getvalue()
    assert r["denies"] == 2, "ALLOW lines must not be counted as denies"
    assert "spawns blocked" in text
    assert "NOT measured" in text and "interrupt rate" in text
    # the deny count must not appear on the interrupt line
    interrupt_line = [l for l in text.splitlines() if "interrupt rate" in l][0]
    assert "2" not in interrupt_line


def test_missing_guard_log_is_reported_as_no_log(tmp_path):
    c = str(tmp_path)
    _ticket(c, "bugs", "done", "a", follow_ons=1)
    buf = io.StringIO()
    r = adt_metrics.report(c, str(tmp_path), out=buf)
    assert r["denies"] is None
    assert "no log on this machine" in buf.getvalue()


def test_blocked_markers_are_counted_but_named_unmeasured(tmp_path):
    c = str(tmp_path)
    _ticket(c, "bugs", "done", "a", follow_ons=0, blocked=2)
    buf = io.StringIO()
    r = adt_metrics.report(c, str(tmp_path), out=buf)
    assert r["blocked_markers"] == 2
    assert "NOT measured" in buf.getvalue()


def test_empty_cache_does_not_crash(tmp_path):
    r = adt_metrics.report(str(tmp_path), str(tmp_path), out=io.StringIO())
    assert r["closed"] == 0


# ── the counter-metric ────────────────────────────────────────────────────
def test_post_merge_defects_counts_bugs_filed_after_a_close(tmp_path):
    """Counts bugs filed after a ticket closed that name that ticket."""
    c = str(tmp_path)
    d = os.path.join(c, "enhancements", "done")
    os.makedirs(d)
    with open(os.path.join(d, "feat.md"), "w") as fh:
        fh.write("---\nid: ADT-200\ntype: enhancement\nupdated: 2026-08-01\n"
                 "follow_ons: 0\n---\n\n# feat\n")
    b = os.path.join(c, "bugs", "ideas")
    os.makedirs(b)
    # after the close AND names it → counts
    with open(os.path.join(b, "after.md"), "w") as fh:
        fh.write("---\nid: ADT-201\ntype: bug\ncreated: 2026-08-09\n---\n\n"
                 "# regression from ADT-200\n")
    # after the close but names nothing → not counted
    with open(os.path.join(b, "unrelated.md"), "w") as fh:
        fh.write("---\nid: ADT-202\ntype: bug\ncreated: 2026-08-09\n---\n\n# unrelated\n")
    # names it but filed BEFORE the close → not a post-merge defect
    with open(os.path.join(b, "before.md"), "w") as fh:
        fh.write("---\nid: ADT-203\ntype: bug\ncreated: 2026-07-01\n---\n\n# ADT-200 again\n")
    r = adt_metrics.report(c, str(tmp_path), out=io.StringIO())
    assert r["post_merge_defects"] == 1
    assert r["defects_by_ticket"]["ADT-200"] == 1


def test_the_counter_metric_appears_in_the_report(tmp_path):
    c = str(tmp_path)
    _ticket(c, "bugs", "done", "a", follow_ons=0)
    buf = io.StringIO()
    adt_metrics.report(c, str(tmp_path), out=buf)
    assert "COUNTER-METRIC" in buf.getvalue()
    assert "post-merge defects" in buf.getvalue()


def test_non_bug_types_are_not_counted_as_defects(tmp_path):
    c = str(tmp_path)
    d = os.path.join(c, "enhancements", "done")
    os.makedirs(d)
    with open(os.path.join(d, "f.md"), "w") as fh:
        fh.write("---\nid: ADT-300\ntype: enhancement\nupdated: 2026-08-01\n"
                 "follow_ons: 0\n---\n\n# f\n")
    t = os.path.join(c, "tasks", "ideas")
    os.makedirs(t)
    with open(os.path.join(t, "chore.md"), "w") as fh:
        fh.write("---\nid: ADT-301\ntype: task\ncreated: 2026-08-09\n---\n\n# ADT-300 chore\n")
    r = adt_metrics.report(c, str(tmp_path), out=io.StringIO())
    assert r["post_merge_defects"] == 0


# ── by-track split ────────────────────────────────────────────────────────

def _tracked(cache, typ, stage, name, track=None, follow_ons=None):
    d = os.path.join(cache, typ, stage)
    os.makedirs(d, exist_ok=True)
    fm = "---\nid: %s\ntype: %s\nclosed: 2026-01-01\n" % (name.upper(), typ[:-1])
    if track is not None:
        fm += "track: %s\n" % track
    if follow_ons is not None:
        fm += "follow_ons: %d\n" % follow_ons
    fm += "---\n\n# body\n"
    path = os.path.join(d, name + ".md")
    with open(path, "w") as fh:
        fh.write(fm)
    return path


def test_by_track_groups_closed_tickets_by_tier(tmp_path):
    cache = str(tmp_path)
    _tracked(cache, "tasks", "done", "a", track="fast", follow_ons=0)
    _tracked(cache, "tasks", "done", "b", track="full", follow_ons=2)
    _tracked(cache, "tasks", "done", "c", track="full", follow_ons=0)
    rows = [r for r in (adt_metrics.read_ticket(p)
                        for p in adt_metrics.closed_tickets(cache)) if r]
    bt = adt_metrics.by_track(rows, {})
    assert bt["fast"]["closed"] == 1
    assert bt["full"]["closed"] == 2
    assert bt["full"]["follow_ons"] == 2


def test_by_track_puts_a_ticket_without_track_under_unset(tmp_path):
    cache = str(tmp_path)
    _tracked(cache, "tasks", "done", "old", track=None, follow_ons=0)
    rows = [r for r in (adt_metrics.read_ticket(p)
                        for p in adt_metrics.closed_tickets(cache)) if r]
    bt = adt_metrics.by_track(rows, {})
    assert "standard" not in bt
    assert bt["unset"]["closed"] == 1


def test_by_track_attributes_post_merge_defects_to_the_tier(tmp_path):
    cache = str(tmp_path)
    _tracked(cache, "tasks", "done", "shipped", track="fast", follow_ons=0)
    rows = [r for r in (adt_metrics.read_ticket(p)
                        for p in adt_metrics.closed_tickets(cache)) if r]
    bt = adt_metrics.by_track(rows, {"SHIPPED": 3})
    assert bt["fast"]["defects"] == 3


def test_by_track_section_appears_only_when_asked_for(tmp_path):
    cache = str(tmp_path)
    _tracked(cache, "tasks", "done", "a", track="fast", follow_ons=0)
    plain, split = io.StringIO(), io.StringIO()
    adt_metrics.report(cache, str(tmp_path), out=plain)
    adt_metrics.report(cache, str(tmp_path), out=split, show_by_track=True)
    assert "by track" not in plain.getvalue()
    assert "by track" in split.getvalue()

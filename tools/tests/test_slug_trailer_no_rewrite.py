# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that the sync never edits an Issue body only to add or remove the
slug trailer (`<!-- adt: slug=... -->`). Bodies are compared with the trailer
removed from both sides; a real body change still pushes, with the trailer.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ticket_serializer as ts  # noqa: E402
import adt_sync  # noqa: E402

CFG = {"repo": "owner/repo"}
BODY = "Description of Epic 1"
TRAILER = "<!-- adt: slug=issue-1 -->"
WITH_TRAILER = BODY + "\n\n" + TRAILER + "\n"


def _ticket(**over):
    """A cache ticket already linked to Issue 1, so reconcile takes the update
    path. Its labels match the Issue's, so only the body can trigger an edit."""
    data = {"slug": "issue-1", "title": "Epic 1", "body": BODY,
            "issue_number": 1, "issue_node_id": "I_x", "state": "open"}
    data.update(over)
    return data


def _capture(monkeypatch, current_body, ticket):
    """Run one reconcile against an Issue whose body is `current_body`, and
    return every gh argv it produced."""
    calls = []
    issue = ts.to_issue(ticket)
    monkeypatch.setattr(adt_sync, "_current_issue", lambda repo, n: {
        "title": issue.get("title"), "body": current_body, "state": "open",
        "labels": [{"name": n} for n in adt_sync._desired_labels(issue)],
    })
    monkeypatch.setattr(adt_sync, "_gh", lambda args, **kw: calls.append(args) or "")
    monkeypatch.setattr(adt_sync, "_ensure_labels_exist", lambda *a, **k: None)
    adt_sync.reconcile_one("t.md", ticket, CFG)
    return calls


def _bodies_pushed(calls):
    return [a[a.index("--body") + 1] for a in calls if "--body" in a]


# -- adopting an Issue that has no trailer ---------------------------------

def test_adopt_does_not_edit_a_body_for_the_trailer_alone(monkeypatch):
    # The Issue has no trailer; otherwise the content matches.
    calls = _capture(monkeypatch, BODY, _ticket())
    assert _bodies_pushed(calls) == [], (
        "expected no body push when only the trailer differs")


def test_adopt_is_a_noop_in_the_dry_run_report(monkeypatch):
    ticket = _ticket()
    issue = ts.to_issue(ticket)
    monkeypatch.setattr(adt_sync, "_current_issue", lambda repo, n: {
        "title": issue.get("title"), "body": BODY, "state": "open",
        "labels": [{"name": n} for n in adt_sync._desired_labels(issue)],
    })
    r = adt_sync.reconcile_one("t.md", ticket, CFG, dry_run=True)
    assert r["action"] == "noop", r


# -- a ticket with no usable slug ------------------------------------------

def test_a_slugless_ticket_does_not_strip_the_trailer(monkeypatch):
    # With no slug the desired body has no trailer, while the Issue has one.
    calls = _capture(monkeypatch, WITH_TRAILER, _ticket(slug=None))
    assert _bodies_pushed(calls) == [], (
        "expected no body push that removes the trailer")


def test_an_unsafe_slug_does_not_strip_the_trailer(monkeypatch):
    calls = _capture(monkeypatch, WITH_TRAILER, _ticket(slug="not a slug!"))
    assert _bodies_pushed(calls) == []


# -- A real edit still goes out --------------------------------------------

def test_a_real_body_change_still_pushes_and_carries_the_trailer(monkeypatch):
    calls = _capture(monkeypatch, WITH_TRAILER, _ticket(body="Rewritten."))
    pushed = _bodies_pushed(calls)
    assert len(pushed) == 1, pushed
    assert pushed[0].startswith("Rewritten.")
    assert TRAILER in pushed[0], "expected the pushed body to include the trailer"


def test_a_real_change_on_an_untrailered_issue_still_pushes(monkeypatch):
    calls = _capture(monkeypatch, BODY, _ticket(body="Rewritten."))
    assert len(_bodies_pushed(calls)) == 1


# -- _with_slug_trailer and _body_differs ---------------------------------

def test_with_no_slug_the_body_is_returned_untouched(monkeypatch):
    assert ts._with_slug_trailer(WITH_TRAILER, None) == WITH_TRAILER
    assert ts._with_slug_trailer(WITH_TRAILER, "") == WITH_TRAILER
    assert ts._with_slug_trailer(WITH_TRAILER, "not a slug!") == WITH_TRAILER
    assert ts._with_slug_trailer(BODY, None) == BODY


def test_with_a_slug_the_trailer_is_replaced_not_doubled():
    once = ts._with_slug_trailer(BODY, "issue-1")
    assert once.count("adt: slug=") == 1
    assert ts._with_slug_trailer(once, "issue-1") == once


def test_body_differs_ignores_the_trailer_only():
    assert not adt_sync._body_differs(BODY, WITH_TRAILER)
    assert not adt_sync._body_differs(WITH_TRAILER, BODY)
    assert not adt_sync._body_differs(None, "")
    assert adt_sync._body_differs(BODY, "Rewritten.")
    assert adt_sync._body_differs(WITH_TRAILER, "Rewritten.\n\n" + TRAILER + "\n")

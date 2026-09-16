# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that the sync reads Issues and comments through `gh api` (REST), and
that the Projects context (`_PROJECT_CTX`) is saved to the sync-state sidecar,
re-fetched after its TTL, and dropped when a board write fails."""
from __future__ import annotations

import json
import os
import time
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_sync  # noqa: E402


class RecordingGh:
    def __init__(self, replies=None):
        self.calls, self.replies = [], replies or {}

    def __call__(self, args, input_text=None):
        self.calls.append(args)
        for needle, reply in self.replies.items():
            if needle in " ".join(args):
                return json.dumps(reply)
        return "{}"

    @property
    def banned(self):
        """Any call still using the GraphQL porcelain argv form."""
        return [c for c in self.calls if c[:2] in (["issue", "view"],
                                                   ["pr", "view"])]


REST_ISSUE = {
    "number": 58, "node_id": "NODE58", "title": "T", "body": "B",
    "state": "open", "state_reason": None, "labels": [{"name": "P1"}],
    "assignees": [], "milestone": None, "created_at": "2026-08-01T00:00:00Z",
    "updated_at": "2026-08-02T00:00:00Z", "closed_at": None,
    "user": {"login": "someone"},
}


# --- reading one Issue ------------------------------------------------------

def test_current_issue_uses_rest_not_issue_view(monkeypatch):
    gh = RecordingGh({"repos/o/r/issues/58": REST_ISSUE})
    monkeypatch.setattr(adt_sync, "_gh", gh)
    out = adt_sync._current_issue("o/r", 58)

    assert gh.calls[0][0] == "api"
    assert gh.calls[0][-1] == "repos/o/r/issues/58"
    assert not gh.banned
    # The REST reply is mapped to the shape the serializer expects.
    assert out["number"] == 58 and out["id"] == "NODE58"
    assert out["updatedAt"] == "2026-08-02T00:00:00Z"


# --- the node_id lookup after a create --------------------------------------

def test_node_id_lookup_uses_rest(monkeypatch):
    gh = RecordingGh({"repos/o/r/issues/58": REST_ISSUE})
    monkeypatch.setattr(adt_sync, "_gh", gh)
    node = adt_sync._gh_json(["api", "repos/o/r/issues/58"])
    # The REST issue has node_id directly.
    assert node["node_id"] == "NODE58"
    assert not gh.banned


# --- comments --------------------------------------------------------------

def test_comments_from_rest_maps_the_comments_endpoint():
    # REST names the author `user` and the timestamp `created_at`; from_issue
    # reads `author.login` and `createdAt`.
    out = adt_sync._comments_from_rest([
        {"user": {"login": "adt"}, "created_at": "2026-08-02T10:00:00Z",
         "body": "<!-- adt:tokens machine=m1 total=5 -->"},
    ])
    assert out == [{"author": {"login": "adt"},
                    "createdAt": "2026-08-02T10:00:00Z",
                    "body": "<!-- adt:tokens machine=m1 total=5 -->"}]


def test_comments_from_rest_tolerates_a_non_list():
    # gh api can return an error object instead of a list.
    assert adt_sync._comments_from_rest({"message": "Not Found"}) == []
    assert adt_sync._comments_from_rest(None) == []


def test_no_site_emits_the_issue_view_argv(monkeypatch):
    gh = RecordingGh({"repos/o/r/issues/58": REST_ISSUE,
                      "issues/58/comments": []})
    monkeypatch.setattr(adt_sync, "_gh", gh)
    adt_sync._current_issue("o/r", 58)
    adt_sync._gh_json(["api", "repos/o/r/issues/58/comments"])
    assert not gh.banned, gh.calls


# --- _PROJECT_CTX persistence ----------------------------------------------

CFG = {"owner": "o", "project_number": 7, "repo": "o/r"}


def test_project_ctx_seeds_from_the_sidecar_without_any_gh_call(monkeypatch):
    stored = {"o/7": {"project_id": "PVT_1", "status_field_id": "F1",
                      "fetched_at": time.time(),
                      "options": {"building": "OPT1"}}}
    monkeypatch.setattr(adt_sync, "_load_state_doc",
                        lambda cfg: {"project_ctx": stored})
    gh = RecordingGh()
    monkeypatch.setattr(adt_sync, "_gh", gh)
    # Start with an empty in-process cache, as a new process would.
    monkeypatch.setattr(adt_sync, "_PROJECT_CTX", {})

    ctx = adt_sync._project_ctx(CFG)

    assert ctx["project_id"] == "PVT_1"
    assert gh.calls == [], "expected no gh calls with a saved ctx"


def test_project_ctx_walks_when_the_sidecar_is_empty(monkeypatch):
    monkeypatch.setattr(adt_sync, "_load_state_doc", lambda cfg: {})
    gh = RecordingGh({
        "project view": {"id": "PVT_1"},
        "field-list": {"fields": [{"name": "Status", "id": "F1",
                                   "options": [{"name": "qa", "id": "O1"}]}]},
    })
    monkeypatch.setattr(adt_sync, "_gh", gh)
    monkeypatch.setattr(adt_sync, "_PROJECT_CTX", {})

    ctx = adt_sync._project_ctx(CFG)

    assert ctx["project_id"] == "PVT_1"
    assert ctx["options"] == {"qa": "O1"}
    assert len(gh.calls) == 2, "expected two gh calls on a cold start"


def test_project_ctx_is_declared_in_the_state_schema():
    # _load_state_doc uses _STATE_KEYS to recognise the sidecar format.
    assert "project_ctx" in adt_sync._STATE_KEYS
    assert "watch" in adt_sync._STATE_KEYS


# --- a saved ctx is re-fetched when expired or when a board write fails -----

def test_stale_persisted_ctx_is_re_walked_after_the_ttl(monkeypatch):
    stale = {"o/7": {"project_id": "PVT_OLD", "status_field_id": "F_OLD",
                     "fetched_at": time.time() - adt_sync.PROJECT_CTX_TTL - 1,
                     "options": {"qa": "OPT_OLD"}}}
    monkeypatch.setattr(adt_sync, "_load_state_doc",
                        lambda cfg: {"project_ctx": stale})
    gh = RecordingGh({
        "project view": {"id": "PVT_NEW"},
        "field-list": {"fields": [{"name": "Status", "id": "F_NEW",
                                   "options": [{"name": "qa", "id": "OPT_NEW"}]}]},
    })
    monkeypatch.setattr(adt_sync, "_gh", gh)
    monkeypatch.setattr(adt_sync, "_PROJECT_CTX", {})

    ctx = adt_sync._project_ctx(CFG)

    assert ctx["project_id"] == "PVT_NEW", "expected an expired entry to be re-fetched"
    assert ctx["status_field_id"] == "F_NEW"
    assert len(gh.calls) == 2


def test_ctx_with_no_fetched_at_is_treated_as_expired(monkeypatch):
    # An entry with no fetched_at stamp counts as expired.
    monkeypatch.setattr(adt_sync, "_load_state_doc",
                        lambda cfg: {"project_ctx": {"o/7": {"project_id": "OLD"}}})
    gh = RecordingGh({"project view": {"id": "NEW"},
                      "field-list": {"fields": []}})
    monkeypatch.setattr(adt_sync, "_gh", gh)
    monkeypatch.setattr(adt_sync, "_PROJECT_CTX", {})
    assert adt_sync._project_ctx(CFG)["project_id"] == "NEW"


def test_board_write_failure_invalidates_the_persisted_ctx(monkeypatch):
    # A rejected board write drops the saved ids so the next pass re-fetches them.
    written = {}
    monkeypatch.setattr(adt_sync, "_load_state_doc",
                        lambda cfg: {"project_ctx": {"o/7": {"project_id": "P"}}})
    monkeypatch.setattr(adt_sync, "_update_state_doc",
                        lambda cfg, upd: written.update(upd))
    monkeypatch.setattr(adt_sync, "_PROJECT_CTX",
                        {"o/7": {"project_id": "P", "status_field_id": "F",
                                 "options": {"qa": "O"}}})
    monkeypatch.setattr(adt_sync, "_board_index", lambda cfg: {})
    monkeypatch.setattr(adt_sync, "_board_write",
                        lambda *a: (_ for _ in ()).throw(
                            adt_sync.GhError("field not found")))

    try:
        adt_sync.ensure_on_board(CFG, 58, "qa")
    except adt_sync.GhError:
        pass
    else:                                     # pragma: no cover
        raise AssertionError("expected the error to propagate")

    assert "o/7" not in adt_sync._PROJECT_CTX, "expected the in-process entry dropped"
    assert written.get("project_ctx") == {}, "expected the sidecar entry dropped"


def test_rate_limit_does_not_invalidate_the_ctx(monkeypatch):
    # A rate limit says nothing about whether the ids are valid, so they are kept.
    monkeypatch.setattr(adt_sync, "_PROJECT_CTX",
                        {"o/7": {"project_id": "P", "status_field_id": "F",
                                 "options": {}}})
    monkeypatch.setattr(adt_sync, "_board_index", lambda cfg: {})
    monkeypatch.setattr(adt_sync, "_board_write",
                        lambda *a: (_ for _ in ()).throw(
                            adt_sync.RateLimitError("exhausted")))
    try:
        adt_sync.ensure_on_board(CFG, 58, "qa")
    except adt_sync.RateLimitError:
        pass
    assert "o/7" in adt_sync._PROJECT_CTX, "expected the ctx kept after a rate limit"

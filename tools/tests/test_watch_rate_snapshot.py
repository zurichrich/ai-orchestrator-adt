# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for adt_watch._rate_limit_snapshot: GraphQL and REST usage come from
sources that report real numbers, and each pool fails independently.

subprocess.run is stubbed so the real function runs against canned gh output.
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_watch  # noqa: E402


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


_GRAPHQL_OK = "1720,5000\n"
# Real `gh api -i user` shape: status line, headers, blank line, body.
_CORE_OK = (
    "HTTP/2.0 200 OK\r\n"
    "Content-Type: application/json\r\n"
    "X-Ratelimit-Limit: 5000\r\n"
    "X-Ratelimit-Remaining: 4895\r\n"
    "X-Ratelimit-Used: 105\r\n"
    "X-Ratelimit-Resource: core\r\n"
    "\r\n"
    '{"login":"someone"}\n'
)


def _fake_run(graphql=_GRAPHQL_OK, core=_CORE_OK, gql_rc=0, core_rc=0):
    def run(args, **kwargs):
        if "graphql" in args:
            return _Result(graphql, gql_rc)
        return _Result(core, core_rc)
    return run


def test_snapshot_reports_real_nonzero_usage(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run())
    pools = adt_watch._rate_limit_snapshot()
    assert pools == [("GraphQL", 1720, 5000), ("REST", 105, 5000)]
    assert all(used > 0 for _label, used, _limit in pools)


def test_snapshot_does_not_read_the_zeroed_endpoint(monkeypatch):
    """`gh api rate_limit` always reports used:0, so no executed command may call it."""
    seen = []

    def run(args, **kwargs):
        seen.append(list(args))
        return _Result(_GRAPHQL_OK if "graphql" in args else _CORE_OK, 0)

    monkeypatch.setattr(subprocess, "run", run)
    adt_watch._rate_limit_snapshot()
    assert seen, "no subprocess call was made"
    for argv in seen:
        assert "rate_limit" not in argv, f"rate_limit endpoint queried: {argv}"


def test_graphql_pool_comes_from_the_ratelimit_query(monkeypatch):
    seen = []

    def run(args, **kwargs):
        seen.append(" ".join(str(a) for a in args))
        return _Result(_GRAPHQL_OK if "graphql" in args else _CORE_OK, 0)

    monkeypatch.setattr(subprocess, "run", run)
    adt_watch._rate_limit_snapshot()
    assert any("rateLimit" in c for c in seen), seen


def test_core_pool_comes_from_response_headers(monkeypatch):
    """Core usage is read from the headers of another endpoint, fetched with `-i`."""
    seen = []

    def run(args, **kwargs):
        seen.append(list(args))
        return _Result(_GRAPHQL_OK if "graphql" in args else _CORE_OK, 0)

    monkeypatch.setattr(subprocess, "run", run)
    adt_watch._rate_limit_snapshot()
    core = [a for a in seen if "graphql" not in a]
    assert core and "-i" in core[0], core


def test_one_dead_pool_does_not_drop_the_other(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(gql_rc=1))
    assert adt_watch._rate_limit_snapshot() == [("REST", 105, 5000)]
    monkeypatch.setattr(subprocess, "run", _fake_run(core_rc=1))
    assert adt_watch._rate_limit_snapshot() == [("GraphQL", 1720, 5000)]


def test_total_failure_returns_empty_not_zeros(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(gql_rc=1, core_rc=1))
    assert adt_watch._rate_limit_snapshot() == []


def test_missing_headers_degrade_to_absent(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(core="HTTP/2.0 200 OK\r\n\r\n{}"))
    assert adt_watch._rate_limit_snapshot() == [("GraphQL", 1720, 5000)]


def test_exception_in_one_pool_is_contained(monkeypatch):
    def run(args, **kwargs):
        if "graphql" in args:
            raise OSError("gh missing")
        return _Result(_CORE_OK, 0)

    monkeypatch.setattr(subprocess, "run", run)
    assert adt_watch._rate_limit_snapshot() == [("REST", 105, 5000)]

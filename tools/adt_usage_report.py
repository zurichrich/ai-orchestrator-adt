#!/usr/bin/env python3
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Read per-command usage back out of the Analytics Engine dataset (ADT-224 A5a).

WHY THIS EXISTS AT ALL. ADT-224 moved usage out of KV and into Analytics Engine
so that storage cost stops scaling with users. That is a write-path change, and
the read path did not survive it: `wrangler kv key list` — which is how this
ticket's own evidence was gathered — now returns `install:` keys and nothing
else. Version, os and every per-command count live in a dataset reachable only
through a different API, with its own token and its own quota. Success criterion
11 is that a human can SEE per-command counts, so without this tool the dataset
is write-only and the criterion ships structurally unmet.

SAMPLING IS NOT OPTIONAL TO HANDLE. Analytics Engine samples under load and
hands back `_sample_interval` per row: a row with an interval of 10 stands for
ten real events. Summing the raw doubles therefore UNDER-reports, silently and
by an amount that grows exactly as adoption grows — the numbers would look fine
and drift low forever. Every aggregate here is `SUM(_sample_interval * ...)`.

THE TOKEN IS THE OPERATOR'S. It is read from the environment and never written
to the repo, never cached to disk, and never logged. With it unset the tool
exits cleanly with instructions rather than raising — an unconfigured read is a
normal state, not a crash.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# Must match CMDSET_V1 in collector/collector.js, position for position: the
# writer stores per-command counts as positional doubles against the `cmdset-v1`
# blob, so a reader with a different order silently attributes every count to
# the wrong command. The blob is the guard — a future reordering becomes
# cmdset-v2 and old rows keep meaning what they meant.
CMDSET_V1 = [
    "block", "brief", "build", "build-todone", "close", "decide",
    "plan", "plan-fasttrack", "qa-run", "release-check",
    "review-critical-path", "review-designer", "review-security", "unblock",
]

DATASET = "adt_usage_v2"
_TIMEOUT = 20

# ADT-263: tickets and tokens are appended after the 14 command doubles, so they
# sit at 15 and 16. Must match commandDoubles() in collector/collector.js. This
# is the SECOND place the two files agree about positions — the `cmdset-v1` blob
# guard versions 1-14 only, and says nothing about these two.
_D_TICKETS = len(CMDSET_V1) + 1
_D_TOKENS = len(CMDSET_V1) + 2


def _sql_v1(days: int) -> str:
    """ADT-284 — per command from the `totals-v1` rows.

    The dimension is in `blob5`, not in a double position, so this is one query
    whatever the command set becomes. It must agree with `dashboardQueries()` in
    collector/collector.js: both read the same rows and a disagreement shows up
    as two different numbers for one fact.

    Two readings, and NEITHER is a SUM over rows. Every double here is a lifetime
    cumulative total re-sent whole on every ping, so `argMax` per install gives
    where things stand and `MAX - MIN` per install gives what happened in the
    window. Summing them is the bug this replaced: an install that had run `plan`
    three times contributed 3 on Monday, 3 on Tuesday and 3 on Wednesday.

    No `_sample_interval` on the value reads either. That weight corrects a count
    of ROWS, where a surviving row stands for N dropped siblings; multiplying a
    lifetime total by it would invent activity. `pings` counts rows and keeps it.
    """
    return (
        "SELECT name, SUM(total) AS total, SUM(total_win) AS total_win\n"
        "FROM (\n"
        "  SELECT blob5 AS name, index1,\n"
        "    argMax(double1, timestamp) AS total,\n"
        "    MAX(double1) - MIN(double1) AS total_win\n"
        "  FROM %s\n"
        "  WHERE timestamp > NOW() - INTERVAL '%d' DAY\n"
        "    AND blob3 = 'totals-v1' AND blob4 = 'command'\n"
        "  GROUP BY blob5, index1\n"
        ")\nGROUP BY name ORDER BY total DESC" % (DATASET, days))


def _sql(days: int) -> str:
    """The legacy `cmdset-v1` rows.

    Read with the SAME rule as `_sql_v1`, because they are the same kind of
    value: `command_counts()` has always recounted the whole log, so every
    cmdset-v1 double is a lifetime total too. `pings` and the install estimate
    count rows and stay weighted.
    """
    cols = ",\n  ".join(
        "SUM(%s) AS %s, SUM(%s_win) AS %s_win"
        % (name.replace("-", "_"), name.replace("-", "_"),
           name.replace("-", "_"), name.replace("-", "_"))
        for name in CMDSET_V1)
    inner = ",\n".join(
        "    argMax(double%d, timestamp) AS %s,\n"
        "    MAX(double%d) - MIN(double%d) AS %s_win"
        % (d, a, d, d, a)
        for a, d in ([("tickets", _D_TICKETS), ("tokens", _D_TOKENS)]
                     + [(n.replace("-", "_"), i + 1) for i, n in enumerate(CMDSET_V1)]))
    return (
        # `installs_estimate`, NOT `installs`. AE's `indexes` is a SAMPLING key,
        # so a distinct-count over it is an estimate that reads low and degrades
        # as volume grows — never obviously wrong, just quietly short. The exact
        # adoption count is the KV `install:` prefix, which is why identity was
        # kept in KV rather than derived from AE alone. The name carries that
        # distinction so a reader cannot mistake one for the other.
        "SELECT\n  SUM(pings) AS pings,\n  "
        "COUNT() AS installs_estimate,\n  "
        "SUM(tickets) AS tickets, SUM(tickets_win) AS tickets_win,\n  "
        "SUM(tokens) AS tokens, SUM(tokens_win) AS tokens_win,\n  " + cols +
        "\nFROM (\n"
        "  SELECT index1 AS install,\n"
        "    SUM(_sample_interval) AS pings,\n" + inner +
        "\n  FROM %s\n  WHERE timestamp > NOW() - INTERVAL '%d' DAY\n"
        "    AND blob3 = 'cmdset-v1'\n  GROUP BY index1\n)" % (DATASET, days))


def query(account_id: str, token: str, sql: str) -> dict:
    url = ("https://api.cloudflare.com/client/v4/accounts/%s/analytics_engine/sql"
           % account_id)
    req = urllib.request.Request(
        url, data=sql.encode("utf-8"),
        headers={"Authorization": "Bearer %s" % token,
                 "Content-Type": "text/plain"},
        method="POST")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def render(rows: list, v1: list = None) -> str:
    if not rows and not v1:
        return "no usage rows in the window"
    rows = rows or [{}]
    row = rows[0]
    out = ["pings:             %s" % row.get("pings", 0),
           "installs (estimate): %s   <- sampled; see note below"
           % row.get("installs_estimate", 0),
           "tickets:           %s" % row.get("tickets", 0),
           "tokens:            %s" % row.get("tokens", 0),
           "",
           "per command (lifetime totals, both layouts):"]
    counts = [(name, row.get(name.replace("-", "_"), 0) or 0) for name in CMDSET_V1]
    for r in (v1 or []):
        name = r.get("name") or ""
        counts = [(n, c) for n, c in counts if n != name] + [
            (name, (dict(counts).get(name) or 0) + (r.get("total") or 0))]
    width = max(len(n) for n, _ in counts) if counts else 1
    for name, n in sorted(counts, key=lambda kv: -float(kv[1])):
        out.append("  %-*s %s" % (width, name, n))
    out += ["",
            "The install figure above is an ESTIMATE: Analytics Engine samples on",
            "its index, so a distinct-count reads low and drifts further low as",
            "adoption grows. For the exact count, ask KV — that is why install",
            "identity lives there:",
            "",
            "  npx wrangler kv key list --namespace-id <STATS id> --remote \\",
            "    | grep -c '\"name\": *\"install:'"]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Per-command ADT usage from the Analytics Engine dataset.")
    ap.add_argument("--days", type=int, default=30,
                    help="window in days (default: 30)")
    ap.add_argument("--print-sql", action="store_true",
                    help="print the query and exit, without contacting Cloudflare")
    args = ap.parse_args(argv)

    if args.print_sql:
        print(_sql(args.days))
        print()
        print(_sql_v1(args.days))
        return 0

    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not token:
        print("adt_usage_report: set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN "
              "to read the dataset.\n"
              "The token is the operator's and is deliberately not stored in this "
              "repo. It needs Account Analytics: Read.\n"
              "Run with --print-sql to see the query without contacting Cloudflare.",
              file=sys.stderr)
        return 0

    try:
        body = query(account_id, token, _sql(args.days))
        body_v1 = query(account_id, token, _sql_v1(args.days))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        print("adt_usage_report: query failed: %s" % exc, file=sys.stderr)
        return 1

    print(render(body.get("data") or [], body_v1.get("data") or []))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests adt_sync.restamp_closed(), which updates `cost_usd:` on a recently
closed ticket when its ledger total has grown since it was stamped."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))
import adt_sync  # noqa: E402

TICKET = """---
slug: a-closed-one
id: ADT-900
title: a closed one
type: task
stage: {stage}
state: closed
closed: {closed}
issue_number: 900
cost_usd: {cost}
cost_tier: measured
tokens: 1000
---

# a closed one
"""

# What the helper prints on success: "<micros>\t<tier>".
FAKE_TOTAL = """#!/bin/bash
echo "{micros}\t{tier}"
"""

# What it prints when gh is unavailable: the local sum on stdout, the notice on
# stderr. Copied from adt-token-total.sh's degrade().
FAKE_DEGRADED = """#!/bin/bash
echo "adt-token-total: gh unavailable or issue fetch failed \
— printing LOCAL sum only" >&2
echo "{micros}\t{tier}"
"""


class RestampTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, ".adt", "state"))
        os.makedirs(os.path.join(self.root, ".claude", "hooks"))
        self.cache = os.path.join(self.root, "cache", "tasks", "done")
        os.makedirs(self.cache)
        self.path = os.path.join(self.cache, "a-closed-one.md")
        self.cfg = {"cache_dir": os.path.join(self.root, "cache"),
                    "repo": "o/r"}
        # _ledger_costs is patched per test; restore it so the stub does not leak.
        self._real_ledger_costs = adt_sync._ledger_costs

    def tearDown(self):
        adt_sync._ledger_costs = self._real_ledger_costs
        self.tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def _ticket(self, cost="87.14", stage="done", closed="2026-09-07T15:25:29Z"):
        Path(self.path).write_text(
            TICKET.format(cost=cost, stage=stage, closed=closed))

    def _helper(self, template, micros=89435346, tier="measured"):
        p = os.path.join(self.root, ".claude", "hooks", "adt-token-total.sh")
        Path(p).write_text(template.format(micros=micros, tier=tier))
        os.chmod(p, 0o755)

    def _ledger(self, micros):
        """Stub the priced local ledger that the pass reads through _ledger_costs."""
        adt_sync._ledger_costs = lambda root: {"ADT-900": {"micros": micros,
                                                           "tier": "measured"}}

    def _run(self, now=None):
        import datetime
        return adt_sync.restamp_closed(
            self.root, self.cfg,
            now or datetime.datetime(2026, 9, 7, 18, 0, 0))

    def _cost_now(self):
        from ticket_serializer import parse_md
        return parse_md(Path(self.path).read_text()).get("cost_usd")

    # -- restamping ------------------------------------------------------
    def test_a_grown_ledger_rewrites_the_stamp(self):
        self._ticket(cost="87.14")
        self._helper(FAKE_TOTAL, micros=89435346)
        self._ledger(89435346)
        out = self._run()
        self.assertEqual([r["action"] for r in out], ["restamp"])
        self.assertEqual(float(self._cost_now()), 89.44)

    def test_an_unmoved_ledger_writes_nothing_and_calls_nothing(self):
        """The helper touches a file when called; on the second run, with the
        ledger unchanged, the file must not reappear."""
        witness = os.path.join(self.root, "called")
        self._ticket(cost="89.44")
        self._helper('#!/bin/bash\ntouch "%s"\necho "89435346\tmeasured"\n'
                     % witness)
        self._ledger(89435346)
        self._run()                                   # seeds the cursor
        self.assertTrue(os.path.exists(witness),
                        "expected the helper to be called on the first run")
        os.unlink(witness)
        out = self._run()
        self.assertEqual(out, [])
        self.assertFalse(os.path.exists(witness),
                         "expected no helper call when the local sum is unchanged")
        self.assertEqual(float(self._cost_now()), 89.44)

    def test_a_ticket_closed_outside_the_window_is_skipped(self):
        self._ticket(cost="87.14", closed="2026-08-01T10:00:00Z")
        self._helper(FAKE_TOTAL, micros=89435346)
        self._ledger(89435346)
        self.assertEqual(self._run(), [])
        self.assertEqual(float(self._cost_now()), 87.14)

    def test_a_degraded_helper_makes_no_write(self):
        """When the helper reports a local-only total, neither the stamp nor the
        cursor changes."""
        self._ticket(cost="87.14")
        self._helper(FAKE_DEGRADED, micros=50000000)
        self._ledger(89435346)
        out = self._run()
        self.assertEqual([r["action"] for r in out], ["restamp-degraded"])
        self.assertEqual(float(self._cost_now()), 87.14,
                         "expected the stamp to be unchanged")
        prev, _ = adt_sync._read_cursor(
            adt_sync.adt_state_dir(self.root, *adt_sync._RESTAMP_LEAF), "ADT-900")
        self.assertEqual(prev, 0)

    # -- guards ----------------------------------------------------------
    def test_a_ticket_that_is_not_done_is_skipped(self):
        self._ticket(cost="87.14", stage="qa")
        self._helper(FAKE_TOTAL, micros=89435346)
        self._ledger(89435346)
        self.assertEqual(self._run(), [])

    def test_a_ticket_with_no_stamp_is_left_for_close_to_seed(self):
        Path(self.path).write_text(
            TICKET.format(cost="87.14", stage="done",
                          closed="2026-09-07T15:25:29Z")
            .replace("cost_usd: 87.14\n", ""))
        self._helper(FAKE_TOTAL, micros=89435346)
        self._ledger(89435346)
        self.assertEqual(self._run(), [])


if __name__ == "__main__":
    unittest.main()

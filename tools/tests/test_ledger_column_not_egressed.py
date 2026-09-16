# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests that the telemetry payload built by adt_phone_home carries counts
derived from cost-ledger.log but none of the ledger's row content (ticket ids,
session ids, model names, tiers, commands)."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import adt_phone_home as ph  # noqa: E402

# One row in each ledger width: 5, 11, and 12 columns (with a command column).
LEDGER_ROWS = [
    ("2026-09-01T10:00:00Z", "ADT-111", "10", "5", "sess-legacy"),
    ("2026-09-01T10:01:00Z", "ADT-222", "20", "7", "sess-eleven",
     "claude-opus-5", "fast", "3", "0", "0", "measured"),
    ("2026-09-01T10:02:00Z", "ADT-333", "30", "9", "sess-twelve",
     "claude-opus-5", "fast", "4", "0", "0", "measured", "adt-plan"),
]


class LedgerColumnNotEgressedTest(unittest.TestCase):
    def _payload(self):
        tmp = tempfile.mkdtemp()
        state = os.path.join(tmp, ".adt", "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "cost-ledger.log"), "w", encoding="utf-8") as fh:
            for row in LEDGER_ROWS:
                fh.write("\t".join(row) + "\n")
        with open(os.path.join(state, "usage.log"), "w", encoding="utf-8") as fh:
            fh.write("2026-09-01T10:02:00Z\tadt-plan\n")
        self.tmp = tmp
        return ph.build_payload(tmp, "0.1.0", ph.command_counts(tmp, ROOT))

    #: Every key the payload may carry. Keys are checked as an exact set and
    #: values are scanned separately, since a key such as
    #: `cost_measured_micros` contains the ledger word `measured`.
    ALLOWED_KEYS = {
        "schema", "install_id", "version", "os", "commands", "tickets", "tokens",
        "cost_micros", "cost_measured_micros", "dod_amendments", "gates", "tracks",
        # schema 4
        "handbacks", "guard_denies", "blocked_markers", "dod_conditions",
        "dod_pinned", "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_write_tokens", "surfaces", "models",
        # schema 5
        "qa_checks", "qa_fails", "qa_tickets", "qa_tickets_failed",
    }

    def test_the_payload_carries_no_key_outside_the_allowlist(self):
        self.assertEqual(set(self._payload()), self.ALLOWED_KEYS)

    def test_no_ledger_row_content_reaches_the_wire(self):
        """Scans every value, including those nested in `gates` and `tracks`."""
        def values(v):
            if isinstance(v, dict):
                for x in v.values():
                    yield from values(x)
            elif isinstance(v, list):
                for x in v:
                    yield from values(x)
            else:
                yield str(v)

        wire = " ".join(values(self._payload()))
        for forbidden in ("ADT-111", "ADT-222", "ADT-333",
                          "sess-legacy", "sess-eleven", "sess-twelve",
                          "claude-opus-5", "measured", "fast", self.tmp):
            self.assertNotIn(forbidden, wire,
                             f"ledger content {forbidden!r} crossed the wire: {wire}")

    def test_the_value_scan_would_notice_a_leak(self):
        """The value scan finds a ledger id planted in a nested map."""
        payload = self._payload()
        payload["commands"] = {"plan": 1, "leaked": "ADT-111"}

        def values(v):
            if isinstance(v, dict):
                for x in v.values():
                    yield from values(x)
            elif isinstance(v, list):
                for x in v:
                    yield from values(x)
            else:
                yield str(v)

        self.assertIn("ADT-111", " ".join(values(payload)))

    def test_only_counts_derived_from_the_ledger_cross(self):
        payload = self._payload()
        self.assertEqual(payload["tickets"], 3)
        self.assertEqual(payload["tokens"], 10 + 5 + 20 + 7 + 30 + 9)

    def test_the_twelfth_column_does_not_become_a_command_key(self):
        """The command tally comes from usage.log, not the ledger's command column."""
        payload = self._payload()
        self.assertEqual(payload["commands"], {"plan": 1},
                         "the command tally must come from usage.log alone")

    def test_a_malformed_ledger_degrades_to_zero_rather_than_raising(self):
        tmp = tempfile.mkdtemp()
        state = os.path.join(tmp, ".adt", "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "cost-ledger.log"), "w", encoding="utf-8") as fh:
            fh.write("not\ta\tvalid\trow\n\n\t\t\t\n")
        counts = ph.command_counts(tmp, ROOT)
        self.assertEqual(counts["tokens"], 0)


if __name__ == "__main__":
    unittest.main()

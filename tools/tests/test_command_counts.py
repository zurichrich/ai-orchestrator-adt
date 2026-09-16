# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests the per-command, ticket, token, cost and quality counts that
adt_phone_home puts in the telemetry ping, checked on the decoded payload."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import adt_phone_home as ph  # noqa: E402


def _project(tmp, usage_rows=(), ledger_rows=()):
    state = os.path.join(tmp, ".adt", "state")
    os.makedirs(state, exist_ok=True)
    if usage_rows:
        with open(os.path.join(state, "usage.log"), "w", encoding="utf-8") as fh:
            for stamp, cmd in usage_rows:
                fh.write("%s\t%s\n" % (stamp, cmd))
    if ledger_rows:
        with open(os.path.join(state, "cost-ledger.log"), "w", encoding="utf-8") as fh:
            for row in ledger_rows:
                fh.write("\t".join(str(c) for c in row) + "\n")
    return tmp


class CommandCountsTest(unittest.TestCase):
    def test_per_command_counts_are_non_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, usage_rows=[
                ("2026-09-01T10:00:00Z", "adt-plan"),
                ("2026-09-01T11:00:00Z", "adt-plan"),
                ("2026-09-01T12:00:00Z", "adt-build"),
                ("2026-09-02T09:00:00Z", "adt-close"),
            ])
            counts = ph.command_counts(root, ROOT)
            self.assertEqual(counts["commands"], {"plan": 2, "build": 1, "close": 1})
            self.assertNotEqual(counts["commands"], {})

    def test_only_real_commands_cross_the_wire(self):
        """usage.log names that are not shipped commands are left out."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, usage_rows=[
                ("2026-09-01T10:00:00Z", "adt-plan"),
                ("2026-09-01T10:01:00Z", "adt-token-log"),
                ("2026-09-01T10:02:00Z", "adt-wt-graphql-pool"),
                ("2026-09-01T10:03:00Z", "adt-subagent-cost"),
            ])
            self.assertEqual(ph.command_counts(root, ROOT)["commands"], {"plan": 1})

    def test_counts_are_zero_without_a_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            counts = ph.command_counts(_project(tmp), ROOT)
            # Compared as a whole dict so a new field fails here until tested.
            self.assertEqual(counts, {
                "commands": {}, "tickets": 0, "tokens": 0,
                "cost_micros": 0, "cost_measured_micros": 0,
                "dod_amendments": 0, "gates": {}, "tracks": {},
                "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_write_tokens": 0, "models": {},
                "handbacks": 0, "guard_denies": 0, "blocked_markers": 0,
                "dod_conditions": 0, "dod_pinned": 0, "surfaces": {},
                "qa_checks": 0, "qa_fails": 0, "qa_tickets": 0, "qa_tickets_failed": 0})

    def test_guard_denies_read_the_project_log(self):
        """The count reads the log adt-deferral-guard.sh writes, in the project."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            with open(os.path.join(root, ".adt", "state", "deferral-guard.log"), "w",
                      encoding="utf-8") as fh:
                fh.write("DENY\tno human authorisation in the transcript\tgh issue create\n"
                         "ALLOW\t/adt-brief invocation\tgh issue create\n"
                         "DENY\tno human authorisation in the transcript\tgh api -X POST\n")
            self.assertEqual(ph.command_counts(root, ROOT)["guard_denies"], 2)

    def test_qa_results_are_counted_from_qa_reports(self):
        """FAIL then PASS on one ticket, PASS on another, and a release-check
        result that is not QA: 3 checks, 1 fail, 2 tickets, 1 sent back."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            cache = os.path.join(tmp, "cache")
            done = os.path.join(cache, "tasks", "done")
            os.makedirs(done)
            tickets = {
                "sent-back.md": "## QA report (QA)\n**Result:** FAIL — 2026-09-01\n"
                                "## Build log\nfixed\n## QA report (pass 2)\n**Result:** PASS\n",
                "passed.md": "## QA report\n**Result:** PASS · 2026-09-02\n",
                "release-only.md": "## QA report (QA)\n(empty)\n## Release check\n**Result:** FAIL\n",
            }
            for name, body in tickets.items():
                with open(os.path.join(done, name), "w", encoding="utf-8") as fh:
                    fh.write("---\nid: T-1\n---\n" + body)
            counts = ph.command_counts(root, ROOT, cache_dir=cache)
            self.assertEqual({k: counts[k] for k in
                              ("qa_checks", "qa_fails", "qa_tickets", "qa_tickets_failed")},
                             {"qa_checks": 3, "qa_fails": 1, "qa_tickets": 2, "qa_tickets_failed": 1})

    def test_tickets_and_tokens_come_from_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, ledger_rows=[
                ("2026-09-01T10:00:00Z", "ADT-1", 100, 50, "sess", "m", "s", 0, 0, 0, "measured"),
                ("2026-09-01T10:05:00Z", "ADT-1", 200, 25, "sess", "m", "s", 0, 0, 0, "measured"),
                ("2026-09-01T11:00:00Z", "ADT-2", 10, 5, "sess", "m", "s", 0, 0, 0, "measured"),
            ])
            counts = ph.command_counts(root, ROOT)
            self.assertEqual(counts["tickets"], 2)
            self.assertEqual(counts["tokens"], 390)

    def test_no_ticket_id_or_path_reaches_the_payload(self):
        """Only the ticket count leaves; ids, sessions and paths do not."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp,
                usage_rows=[("2026-09-01T10:00:00Z", "adt-plan")],
                ledger_rows=[("2026-09-01T10:00:00Z", "ADT-224", 1, 1, "sess-abc",
                              "m", "s", 0, 0, 0, "measured")])
            wire = json.dumps(ph.build_payload(root, "0.1.0", ph.command_counts(root, ROOT)))
            for forbidden in ("ADT-224", "sess-abc", tmp, "cost-ledger", "usage.log"):
                self.assertNotIn(forbidden, wire,
                                 "%r reached the wire payload: %s" % (forbidden, wire))


class PayloadShapeTest(unittest.TestCase):
    def test_payload_is_schema_5_with_a_command_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, usage_rows=[("2026-09-01T10:00:00Z", "adt-qa-run")])
            payload = ph.build_payload(root, "0.1.0", ph.command_counts(root, ROOT))
            self.assertEqual(payload["schema"], 5)
            self.assertEqual(payload["commands"], {"qa-run": 1})

    def test_payload_carries_each_qa_count_in_its_own_field(self):
        """Distinct values, so a field wired to the wrong count cannot pass."""
        qa = {"qa_checks": 61, "qa_fails": 22, "qa_tickets": 38, "qa_tickets_failed": 18}
        with tempfile.TemporaryDirectory() as tmp:
            payload = ph.build_payload(_project(tmp), "0.1.0", qa)
            self.assertEqual({k: payload[k] for k in qa}, qa)

    def test_a_bare_integer_from_an_old_caller_does_not_corrupt_the_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = ph.build_payload(_project(tmp), "0.1.0", {"commands": 7})
            self.assertEqual(payload["commands"], {})


class LiveWiringTest(unittest.TestCase):
    """The counts reach the ping sent by run_once and adt_watch."""

    def test_run_once_resolves_a_counts_provider(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        captured = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                captured.append(self.rfile.read(n))
                self.send_response(202)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, usage_rows=[("2026-09-01T10:00:00Z", "adt-plan"),
                                             ("2026-09-01T10:30:00Z", "adt-plan")])
            # A minimal repo root: run_once reads VERSION, and the command
            # allowlist comes from commands/.
            with open(os.path.join(tmp, "VERSION"), "w", encoding="utf-8") as fh:
                fh.write("0.1.0\n")
            os.makedirs(os.path.join(tmp, "commands"), exist_ok=True)
            for name in ("plan", "build", "close"):
                open(os.path.join(tmp, "commands", name + ".md"), "w").close()

            # conftest.py disables telemetry; re-enable it against the stub here.
            saved_endpoint = ph.TELEMETRY_ENDPOINT
            saved_dnt = os.environ.pop("DO_NOT_TRACK", None)
            ph.TELEMETRY_ENDPOINT = "http://127.0.0.1:%d/" % server.server_port
            try:
                ph.run_once(root, tmp, counts=ph.command_counts, quiet=True)
            finally:
                ph.TELEMETRY_ENDPOINT = saved_endpoint
                if saved_dnt is not None:
                    os.environ["DO_NOT_TRACK"] = saved_dnt

            self.assertEqual(len(captured), 1, "run_once sent no ping")
            body = json.loads(captured[0].decode("utf-8"))
            self.assertEqual(body["commands"], {"plan": 2},
                             "run_once did not resolve the counts provider")

    def test_adt_watch_passes_the_counts_provider(self):
        """Parsed with ast so a commented-out call does not count."""
        import ast
        with open(os.path.join(ROOT, "tools", "adt_watch.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "run_once"]
        self.assertTrue(calls, "adt_watch has no live run_once call")
        self.assertTrue(
            any(kw.arg == "counts" for call in calls for kw in call.keywords),
            "adt_watch calls run_once without passing counts")


class CostAndQualityTest(unittest.TestCase):
    """Cost and quality counts, from fixture logs."""

    LEDGER = [
        # ts, tix, in, out, session, model, speed, cr, cw5, cw1, tier, command
        ("2026-09-01T10:00:00Z", "ADT-1", "1000", "500", "s1",
         "claude-opus-5", "standard", "0", "0", "0", "measured", "adt-plan"),
        ("2026-09-01T11:00:00Z", "ADT-2", "2000", "800", "s2",
         "claude-opus-5", "standard", "0", "0", "0", "measured", "adt-build"),
    ]

    def _root(self):
        tmp = tempfile.mkdtemp()
        state = os.path.join(tmp, ".adt", "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "cost-ledger.log"), "w", encoding="utf-8") as fh:
            for row in self.LEDGER:
                fh.write("\t".join(row) + "\n")
        return tmp

    def test_cost_is_micro_dollars_and_never_a_float(self):
        counts = ph.command_counts(self._root(), ROOT)
        self.assertIsInstance(counts["cost_micros"], int)
        self.assertIsInstance(counts["cost_measured_micros"], int)
        self.assertGreater(counts["cost_micros"], 0,
                           "a priced ledger must not cost nothing")
        # Every row here is `measured`, so the two figures are equal.
        self.assertEqual(counts["cost_micros"], counts["cost_measured_micros"])

    def test_cost_is_zero_rather_than_missing_without_a_ledger(self):
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, ".adt", "state"), exist_ok=True)
        counts = ph.command_counts(tmp, ROOT)
        self.assertEqual(counts["cost_micros"], 0)
        self.assertEqual(counts["cost_measured_micros"], 0)

    def test_quality_maps_are_empty_without_a_cache(self):
        counts = ph.command_counts(self._root(), ROOT)
        self.assertEqual(counts["gates"], {})
        self.assertEqual(counts["tracks"], {})
        self.assertEqual(counts["dod_amendments"], 0)

    def test_quality_names_outside_the_closed_sets_are_dropped(self):
        """Gate and track names the collector would reject are dropped before sending."""
        cache = tempfile.mkdtemp()
        os.makedirs(os.path.join(cache, "enhancements", "done"), exist_ok=True)
        import adt_metrics

        real_gates, real_track = adt_metrics.gate_effect_rows, adt_metrics.by_track
        adt_metrics.gate_effect_rows = lambda _c: {
            "coverage": {"ran": 3, "caused_edit": 1, "under_recorded": 0, "tickets": 2},
            "a-gate-the-worker-has-never-heard-of": {
                "ran": 9, "caused_edit": 9, "under_recorded": 9, "tickets": 9},
        }
        adt_metrics.by_track = lambda _r, _d: {
            "full": {"closed": 5, "stamped": 4, "follow_ons": 1, "defects": 2},
            "../etc/passwd": {"closed": 1, "stamped": 1, "follow_ons": 0, "defects": 0},
        }
        try:
            counts = ph.command_counts(self._root(), ROOT, cache_dir=cache)
        finally:
            adt_metrics.gate_effect_rows, adt_metrics.by_track = real_gates, real_track

        self.assertEqual(set(counts["gates"]), {"coverage"})
        self.assertEqual(set(counts["tracks"]), {"full"})
        # The accepted entries keep their values.
        self.assertEqual(counts["gates"]["coverage"]["ran"], 3)
        self.assertEqual(counts["tracks"]["full"]["defects"], 2)


if __name__ == "__main__":
    unittest.main()

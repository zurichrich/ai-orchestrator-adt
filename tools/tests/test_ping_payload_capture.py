# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests the telemetry ping by capturing what send_ping puts on the wire and
checking the user-facing disclosures against the decoded payload.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import adt_phone_home as ph  # noqa: E402

POSTURE = os.path.join(ROOT, "docs", "security-posture.md")


class _Capture(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        _Capture.received.append(self.rfile.read(n))
        self.send_response(202)
        self.end_headers()

    def log_message(self, *args):
        pass


class PingPayloadCaptureTest(unittest.TestCase):
    def setUp(self):
        _Capture.received = []
        self.server = HTTPServer(("127.0.0.1", 0), _Capture)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.endpoint = "http://127.0.0.1:%d/" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    @contextmanager
    def _telemetry_on(self, dnt=None):
        """conftest.py sets DO_NOT_TRACK=1 and blanks the endpoint. Lift both for
        the block, point the client at the loopback capture server, then restore.
        `dnt` sets DO_NOT_TRACK to that value instead of removing it."""
        real_ep, real_dnt = ph.TELEMETRY_ENDPOINT, os.environ.get("DO_NOT_TRACK")
        ph.TELEMETRY_ENDPOINT = self.endpoint
        if dnt is None:
            os.environ.pop("DO_NOT_TRACK", None)
        else:
            os.environ["DO_NOT_TRACK"] = dnt
        try:
            yield
        finally:
            ph.TELEMETRY_ENDPOINT = real_ep
            if real_dnt is None:
                os.environ.pop("DO_NOT_TRACK", None)
            else:
                os.environ["DO_NOT_TRACK"] = real_dnt

    def _send(self):
        tmp = tempfile.mkdtemp()
        state = os.path.join(tmp, ".adt", "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "usage.log"), "w", encoding="utf-8") as fh:
            fh.write("2026-09-01T10:00:00Z\tadt-plan\n")
            fh.write("2026-09-01T11:00:00Z\tadt-plan\n")
            fh.write("2026-09-01T12:00:00Z\tadt-build\n")
        payload = ph.build_payload(tmp, "0.1.0", ph.command_counts(tmp, ROOT))
        self.assertTrue(ph.send_ping(tmp, payload, endpoint=self.endpoint))
        self.assertEqual(len(_Capture.received), 1, "nothing was captured")
        return json.loads(_Capture.received[0].decode("utf-8"))

    def test_a_captured_payload_carries_non_zero_per_command_counts(self):
        body = self._send()
        self.assertEqual(body["schema"], 5)
        self.assertEqual(body["commands"], {"plan": 2, "build": 1})
        self.assertNotEqual(body["commands"], 0)

    def test_disclosure_matches_decoded_payload_fields(self):
        """Every field on the wire is named in docs/security-posture.md."""
        body = self._send()
        with open(POSTURE, encoding="utf-8") as fh:
            posture = fh.read()
        for field in body:
            self.assertIn(field, posture,
                          "field %r is sent but not disclosed in security-posture.md" % field)

    def test_a_project_with_no_usage_log_sends_an_empty_commands_map(self):
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, ".adt", "state"), exist_ok=True)
        payload = ph.build_payload(tmp, "0.1.0", ph.command_counts(tmp, ROOT))
        self.assertTrue(ph.send_ping(tmp, payload, endpoint=self.endpoint))
        self.assertEqual(json.loads(_Capture.received[0].decode("utf-8"))["commands"], {})


    # ------------------------------------------------------------------

    #: Every file that tells a user what the ping sends.
    DISCLOSURES = (
        os.path.join(ROOT, "docs", "security-posture.md"),
        os.path.join(ROOT, "README.md"),
        os.path.join(ROOT, "adt-install.sh"),
    )

    def test_schema4_fields_are_disclosed_on_every_surface(self):
        """Checks the three files above and `first_run_notice()` against a sent payload."""
        body = self._send()
        self.assertEqual(body["schema"], 5)
        for path in self.DISCLOSURES:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for field in body:
                # The installer and README describe the payload in prose rather
                # than by field name, so the money fields are matched on the
                # concept they disclose.
                if field in ("cost_micros", "cost_measured_micros"):
                    self.assertIn("dollar", text.lower(),
                                  f"{path} does not disclose cost in dollars")
                elif path.endswith("security-posture.md"):
                    self.assertIn(field, text,
                                  f"{field!r} is sent but not disclosed in {path}")

        notice = ph.first_run_notice(tempfile.mkdtemp())
        self.assertIsNotNone(notice, "expected a first-run notice")
        self.assertIn("dollar", notice.lower(),
                      "the first-run notice does not disclose cost in dollars")

    def test_posture_doc_does_not_name_a_field_that_is_never_sent(self):
        with open(self.DISCLOSURES[0], encoding="utf-8") as fh:
            posture = fh.read()
        self.assertNotIn("prompt_text", posture,
                         "the doc names a field that is never sent")

    def test_watcher_wiring_sends_the_quality_fields(self):
        """Drives `run_once` with the counts provider bound the way
        tools/adt_watch.py binds it, and checks the quality fields arrive with
        real values."""
        cache = os.path.expanduser("~/.adt/agent-dev-team/cache")
        if not os.path.isdir(cache):
            self.skipTest("no local cache to read quality metrics from")
        tmp = tempfile.mkdtemp()
        state = os.path.join(tmp, ".adt", "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "usage.log"), "w", encoding="utf-8") as fh:
            fh.write("2026-09-01T10:00:00Z\tadt-plan\n")

        with self._telemetry_on():
            ph.run_once(tmp, ROOT,
                        counts=lambda pr, rr: ph.command_counts(pr, rr, cache_dir=cache),
                        quiet=True)

        self.assertEqual(len(_Capture.received), 1, "run_once sent nothing")
        body = json.loads(_Capture.received[0].decode("utf-8"))
        self.assertEqual(body["schema"], 5)
        # These fields are only filled when the cache is read.
        self.assertTrue(body["gates"], "gates arrived empty")
        self.assertIn("coverage", body["gates"])
        self.assertTrue(body["tracks"], "tracks arrived empty")
        self.assertGreater(body["gates"]["coverage"]["ran"], 0)

    # ------------------------------------------------------------------
    # The uninstall event

    def _uninstall(self, with_id=True, off_file=False, dnt=None):
        """Run send_uninstall_event against the capture server, with telemetry on
        unless `off_file` or `dnt` turns it off."""
        tmp = tempfile.mkdtemp()
        state = os.path.join(tmp, ".adt", "state")
        os.makedirs(state, exist_ok=True)
        if with_id:
            with open(os.path.join(state, "install-id"), "w", encoding="utf-8") as fh:
                fh.write("3f2504e0-4f89-11d3-9a0c-0305e82c3301\n")
        if off_file:
            open(os.path.join(state, "telemetry-off"), "w").close()
        with self._telemetry_on(dnt):
            sent = ph.send_uninstall_event(tmp, ROOT)
        return tmp, sent

    def test_uninstall_event_sends_exactly_four_disclosed_fields(self):
        _, sent = self._uninstall()
        self.assertTrue(sent)
        self.assertEqual(len(_Capture.received), 1, "nothing was captured")
        body = json.loads(_Capture.received[0].decode("utf-8"))
        self.assertEqual(sorted(body), ["event", "install_id", "os", "version"])
        self.assertEqual(body["event"], "uninstall")
        self.assertEqual(body["install_id"], "3f2504e0-4f89-11d3-9a0c-0305e82c3301")
        self.assertEqual(body["version"], ph.read_version(ROOT))

        # Each field must be named in the section that describes this event, not
        # merely somewhere in the doc: `version` and `os` are in the daily table.
        with open(POSTURE, encoding="utf-8") as fh:
            posture = fh.read()
        start = posture.find("\n### The uninstall event payload\n")
        self.assertNotEqual(start, -1, "security-posture.md has no uninstall event payload section")
        rest = posture[start + 1:]
        ends = [i for i in (rest.find("\n## ", 1), rest.find("\n### ", 1)) if i != -1]
        section = rest[:min(ends)] if ends else rest
        for field in body:
            self.assertIn("`%s`" % field, section,
                          "field %r is sent on uninstall but not in that section" % field)

    def test_uninstall_event_sends_nothing_when_telemetry_is_off(self):
        _, sent = self._uninstall(dnt="1")
        self.assertFalse(sent)
        _, sent = self._uninstall(off_file=True)
        self.assertFalse(sent)
        self.assertEqual(_Capture.received, [], "an opted-out install reported its uninstall")

    def test_uninstall_event_never_mints_an_install_id(self):
        tmp, sent = self._uninstall(with_id=False)
        self.assertFalse(sent)
        self.assertEqual(_Capture.received, [])
        self.assertFalse(os.path.exists(os.path.join(tmp, ".adt", "state", "install-id")),
                         "the uninstall event created an install id")

    def test_uninstall_event_is_disclosed_on_every_surface(self):
        phrase = "one last report when you uninstall"
        for path in self.DISCLOSURES:
            with open(path, encoding="utf-8") as fh:
                self.assertIn(phrase, fh.read(), f"{path} does not disclose the uninstall event")
        self.assertIn(phrase, ph.first_run_notice(tempfile.mkdtemp()),
                      "the first-run notice does not disclose the uninstall event")

        # The egress table is where a reader looks for what goes where, so the
        # collector's own row has to say it too.
        with open(POSTURE, encoding="utf-8") as fh:
            rows = [ln for ln in fh if ln.startswith("| `adt-telemetry.zurichrich.workers.dev` |")]
        self.assertEqual(len(rows), 1, "expected one egress row for the collector")
        self.assertIn(phrase, rows[0], "the collector's egress row does not mention the uninstall event")

    def test_without_the_cache_the_quality_fields_are_empty(self):
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, ".adt", "state"), exist_ok=True)
        payload = ph.build_payload(tmp, "0.1.0", ph.command_counts(tmp, ROOT))
        self.assertEqual(payload["gates"], {})
        self.assertEqual(payload["tracks"], {})


if __name__ == "__main__":
    unittest.main()

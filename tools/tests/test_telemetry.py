# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests the anonymous usage ping in adt_phone_home: the exact payload fields,
each way to turn it off, the first-run notice, the daily cadence, that failures
never raise, and the User-Agent it sends."""
import json
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_phone_home as ph  # noqa: E402

# The field table in docs/security-posture.md (schema 5).
ALLOWED = {"schema", "install_id", "version", "os", "commands", "tickets", "tokens",
           "cost_micros", "cost_measured_micros", "dod_amendments", "gates", "tracks",
           "handbacks", "guard_denies", "blocked_markers", "dod_conditions",
           "dod_pinned", "input_tokens", "output_tokens", "cache_read_tokens",
           "cache_write_tokens", "surfaces", "models",
           "qa_checks", "qa_fails", "qa_tickets", "qa_tickets_failed"}


class PayloadTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_payload_is_exactly_the_documented_allowlist(self):
        p = ph.build_payload(self.d, "0.1.0", {"commands": 3, "tickets": 2, "tokens": 99})
        self.assertEqual(set(p), ALLOWED)

    def test_payload_carries_no_paths_prompts_or_repo_names(self):
        p = ph.build_payload(self.d, "0.1.0", {"commands": 1})
        blob = json.dumps(p)
        for leak in (self.d, os.path.expanduser("~"), "agent-dev-team", "/Users"):
            self.assertNotIn(leak, blob, f"payload leaked {leak!r}")

    def test_install_id_is_a_random_uuid_not_a_fingerprint(self):
        import platform
        a = ph.install_id(self.d)
        self.assertEqual(len(a), 36)
        self.assertNotIn(platform.node(), a)
        b = ph.install_id(self.d)
        self.assertEqual(a, b, "expected the same id on a second call")
        other = ph.install_id(tempfile.mkdtemp())
        self.assertNotEqual(a, other, "expected a different id for another install")


class SuppressionTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_disabled_when_endpoint_unset(self):
        with mock.patch.object(ph, "TELEMETRY_ENDPOINT", ""):
            self.assertFalse(ph.telemetry_enabled(self.d))

    def test_do_not_track_suppresses(self):
        with mock.patch.object(ph, "TELEMETRY_ENDPOINT", "https://x.example"), \
             mock.patch.dict(os.environ, {"DO_NOT_TRACK": "1"}):
            self.assertFalse(ph.telemetry_enabled(self.d))

    def test_off_switch_file_suppresses(self):
        os.makedirs(os.path.join(self.d, ".adt", "state"), exist_ok=True)
        open(os.path.join(self.d, ".adt", "state", "telemetry-off"), "w").close()
        with mock.patch.object(ph, "TELEMETRY_ENDPOINT", "https://x.example"), \
             mock.patch.dict(os.environ, {"DO_NOT_TRACK": ""}):
            self.assertFalse(ph.telemetry_enabled(self.d))

    def test_enabled_only_when_all_three_allow(self):
        with mock.patch.object(ph, "TELEMETRY_ENDPOINT", "https://x.example"), \
             mock.patch.dict(os.environ, {"DO_NOT_TRACK": ""}):
            self.assertTrue(ph.telemetry_enabled(self.d))

    def test_first_run_notice_shows_once_then_never(self):
        with mock.patch.object(ph, "TELEMETRY_ENDPOINT", "https://x.example"):
            first = ph.first_run_notice(self.d)
            second = ph.first_run_notice(self.d)
        self.assertIn("DO_NOT_TRACK", first)
        self.assertEqual(second, "")

    # first_run_notice() marks the notice as shown, so a quiet run must not call it.
    def test_quiet_run_does_not_consume_the_disclosure(self):
        sent = []
        with mock.patch.object(ph, "TELEMETRY_ENDPOINT", "https://x.example"), \
             mock.patch.dict(os.environ, {"DO_NOT_TRACK": ""}), \
             mock.patch.object(ph, "send_ping", lambda *a, **k: sent.append(1)), \
             mock.patch.object(ph, "read_version", lambda *a, **k: "9.9.9"), \
             mock.patch.object(ph, "check_advisory", lambda *a, **k: ""):
            ph.run_once(self.d, self.d, quiet=True)
            self.assertTrue(sent, "expected the ping to be sent on a quiet run")
            self.assertIn("DO_NOT_TRACK", ph.first_run_notice(self.d))

    def test_loud_run_shows_the_disclosure_and_then_it_is_spent(self):
        with mock.patch.object(ph, "TELEMETRY_ENDPOINT", "https://x.example"), \
             mock.patch.dict(os.environ, {"DO_NOT_TRACK": ""}), \
             mock.patch.object(ph, "send_ping", lambda *a, **k: None), \
             mock.patch.object(ph, "read_version", lambda *a, **k: "9.9.9"), \
             mock.patch.object(ph, "check_advisory", lambda *a, **k: ""):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ph.run_once(self.d, self.d, quiet=False)
            self.assertIn("DO_NOT_TRACK", err.getvalue())
            self.assertEqual(ph.first_run_notice(self.d), "")


class InstallTimeDisclosureTest(unittest.TestCase):
    """The installer and README mention telemetry and how to turn it off."""

    def test_installer_discloses_telemetry(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        src = io.open(os.path.join(root, "adt-install.sh"), encoding="utf-8").read()
        self.assertIn("DO_NOT_TRACK", src)
        self.assertIn("telemetry-off", src)

    def test_readme_discloses_telemetry(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        src = io.open(os.path.join(root, "README.md"), encoding="utf-8").read()
        self.assertIn("DO_NOT_TRACK", src)


class FailOpenTest(unittest.TestCase):
    """send_ping returns False instead of raising."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.payload = ph.build_payload(self.d, "0.1.0", {})

    def test_connection_error_returns_false_not_raises(self):
        with mock.patch.object(ph.urllib.request, "urlopen",
                               side_effect=ph.urllib.error.URLError("down")):
            self.assertFalse(ph.send_ping(self.d, self.payload, "https://x.example"))

    def test_timeout_returns_false_not_raises(self):
        with mock.patch.object(ph.urllib.request, "urlopen", side_effect=TimeoutError()):
            self.assertFalse(ph.send_ping(self.d, self.payload, "https://x.example"))

    def test_non_2xx_returns_false_not_raises(self):
        resp = mock.MagicMock(); resp.status = 500
        resp.__enter__ = lambda s: s; resp.__exit__ = lambda *a: False
        with mock.patch.object(ph.urllib.request, "urlopen", return_value=resp):
            self.assertFalse(ph.send_ping(self.d, self.payload, "https://x.example"))

    def test_unset_endpoint_never_calls_out(self):
        called = []
        with mock.patch.object(ph.urllib.request, "urlopen",
                               side_effect=lambda *a, **k: called.append(1)):
            self.assertFalse(ph.send_ping(self.d, self.payload, ""))
        self.assertEqual(called, [], "expected no connection with no endpoint")


if __name__ == "__main__":
    unittest.main()


class CadenceTest(unittest.TestCase):
    """due_today() lets each channel fire once per day."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_fires_once_per_day_then_stops(self):
        self.assertTrue(ph.due_today(self.d, "advisory", "2026-09-04"))
        self.assertFalse(ph.due_today(self.d, "advisory", "2026-09-04"))
        self.assertFalse(ph.due_today(self.d, "advisory", "2026-09-04"))

    def test_new_day_reopens(self):
        ph.due_today(self.d, "advisory", "2026-09-04")
        self.assertTrue(ph.due_today(self.d, "advisory", "2026-09-05"))

    def test_channels_are_independent(self):
        ph.due_today(self.d, "advisory", "2026-09-04")
        self.assertTrue(ph.due_today(self.d, "telemetry", "2026-09-04"),
                        "expected the telemetry channel to be due after the advisory fired")

    def test_unstampable_state_dir_does_not_fire_forever(self):
        blocked = os.path.join(tempfile.mkdtemp(), "nope")
        open(blocked, "w").close()          # a file where the dir should be
        self.assertFalse(ph.due_today(blocked, "advisory", "2026-09-04"),
                         "expected not due when the stamp cannot be written")


class WiringTest(unittest.TestCase):
    """adt_watch calls run_once, and run_once behaves safely."""

    def test_adt_watch_calls_run_once(self):
        """Parse the source so a commented-out call does not count."""
        import ast
        here = os.path.dirname(__file__)
        with open(os.path.join(here, "..", "adt_watch.py")) as fh:
            tree = ast.parse(fh.read())
        found = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "run_once"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "adt_phone_home"
            for n in ast.walk(tree))
        self.assertTrue(found,
                        "expected adt_watch to call adt_phone_home.run_once")

    def test_run_once_never_raises(self):
        d = tempfile.mkdtemp()
        with mock.patch.object(ph, "read_version", side_effect=RuntimeError("boom")):
            ph.run_once(d, d)               # must not raise

    def test_run_once_respects_the_daily_gate(self):
        d = tempfile.mkdtemp()
        calls = []
        with mock.patch.object(ph, "read_version", return_value="0.1.0"), \
             mock.patch.object(ph, "check_advisory", side_effect=lambda v: calls.append(v)):
            ph.run_once(d, d)
            ph.run_once(d, d)
        self.assertEqual(len(calls), 1, f"expected 1 advisory fetch in a day, got {len(calls)}")


class ReadCapTest(unittest.TestCase):
    def test_oversized_body_fails_open(self):
        big = b'{"revoked":[]}' + b" " * (ph._MAX_BYTES + 10)
        resp = mock.MagicMock(); resp.status = 200
        resp.read = lambda n=None: big[:n] if n else big
        resp.__enter__ = lambda s: s; resp.__exit__ = lambda *a: False
        with mock.patch.object(ph.urllib.request, "urlopen", return_value=resp):
            self.assertIsNone(ph._get_json("https://x.example/a.json"))


class UserAgentTest(unittest.TestCase):
    """Requests send an `adt/` User-Agent instead of urllib's default, which
    Cloudflare rejects."""

    def _captured_request(self, fn):
        seen = {}
        def fake(req, timeout=None):
            seen["req"] = req
            raise ph.urllib.error.URLError("stop here")
        with mock.patch.object(ph.urllib.request, "urlopen", fake):
            fn()
        return seen.get("req")

    def test_ping_sends_a_named_user_agent(self):
        d = tempfile.mkdtemp()
        p = ph.build_payload(d, "0.1.0", {})
        req = self._captured_request(lambda: ph.send_ping(d, p, "https://x.example"))
        ua = req.get_header("User-agent") or ""
        self.assertTrue(ua.startswith("adt/"), f"UA was {ua!r}")
        self.assertNotIn("Python-urllib", ua)

    def test_advisory_fetch_sends_a_named_user_agent(self):
        req = self._captured_request(lambda: ph._get_json("https://x.example/a.json"))
        ua = req.get_header("User-agent") or ""
        self.assertTrue(ua.startswith("adt/"), f"UA was {ua!r}")
        self.assertNotIn("Python-urllib", ua)


class SurfaceNamesTest(unittest.TestCase):
    """ADT's agents gained an adt- prefix on 2026-09-09. One agent is one entry."""

    def _log(self, project, rows):
        state = os.path.join(project, ".adt", "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "surface-log.tsv"), "w", encoding="utf-8") as fh:
            for name, n in rows:
                fh.write("2026-09-09T10:00:00Z\tsess\tagent\t%s\t%d\n" % (name, n))

    def test_a_renamed_agent_counts_under_its_adt_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._log(tmp, [("dod-coverage-reviewer", 3), ("adt-dod-coverage-reviewer", 2),
                            ("general-purpose", 1)])
            self.assertEqual(ph._surfaces(tmp),
                             {"adt-dod-coverage-reviewer": 5, "general-purpose": 1})

    def test_a_project_own_agent_keeps_its_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".claude", "agents"))
            open(os.path.join(tmp, ".claude", "agents", "security-reviewer.md"), "w").close()
            self._log(tmp, [("security-reviewer", 4), ("adt-security-reviewer", 1)])
            self.assertEqual(ph._surfaces(tmp),
                             {"security-reviewer": 4, "adt-security-reviewer": 1})

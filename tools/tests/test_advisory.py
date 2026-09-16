# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for the version advisory check in adt_phone_home.

Every error path must fail open: return None and raise nothing.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adt_phone_home as ph  # noqa: E402


def _fake_get(payload):
    return mock.patch.object(ph, "_get_json", lambda url: payload)


class AdvisoryTest(unittest.TestCase):
    def test_revoked_version_returns_advisory(self):
        with _fake_get({"schema": 1, "current": "0.2.0", "revoked": ["0.1.0"]}):
            msg = ph.check_advisory("0.1.0")
        self.assertIsNotNone(msg)
        self.assertIn("0.1.0", msg)
        self.assertIn("0.2.0", msg)          # names the version to upgrade to

    def test_unrevoked_version_is_silent(self):
        with _fake_get({"schema": 1, "current": "0.2.0", "revoked": ["0.0.9"]}):
            self.assertIsNone(ph.check_advisory("0.1.0"))

    # --- fail-open, one test per error path ------------------------------
    def test_network_failure_fails_open(self):
        with _fake_get(None):
            self.assertIsNone(ph.check_advisory("0.1.0"))

    def test_malformed_json_fails_open(self):
        # Patch urlopen rather than _get_json so the real parse path runs.
        with mock.patch.object(ph.urllib.request, "urlopen",
                               side_effect=ValueError("truncated")):
            self.assertIsNone(ph.check_advisory("0.1.0"))

    def test_unexpected_schema_fails_open(self):
        for bad in ([], "a string", {"revoked": "0.1.0"}, {"no": "revoked"}, 7):
            with self.subTest(bad=bad), _fake_get(bad):
                self.assertIsNone(ph.check_advisory("0.1.0"))

    def test_timeout_fails_open(self):
        with mock.patch.object(ph.urllib.request, "urlopen",
                               side_effect=TimeoutError()):
            self.assertIsNone(ph.check_advisory("0.1.0"))

    def test_non_200_fails_open(self):
        resp = mock.MagicMock(); resp.status = 503
        resp.__enter__ = lambda s: s; resp.__exit__ = lambda *a: False
        with mock.patch.object(ph.urllib.request, "urlopen", return_value=resp):
            self.assertIsNone(ph.check_advisory("0.1.0"))

    def test_empty_version_never_fetches(self):
        called = []
        with mock.patch.object(ph, "_get_json", lambda u: called.append(u)):
            self.assertIsNone(ph.check_advisory(""))
        self.assertEqual(called, [], "must not fetch without a version")

    def test_url_names_the_installed_version_rather_than_latest(self):
        seen = {}
        with mock.patch.object(ph, "_get_json",
                               lambda u: seen.setdefault("url", u) and None):
            ph.check_advisory("1.2.3")
        self.assertIn("v1.2.3", seen["url"])
        self.assertNotIn("latest", seen["url"])


if __name__ == "__main__":
    unittest.main()

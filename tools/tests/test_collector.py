# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0

"""Tests for the telemetry collector: schema validation, rate limiting, and what the client may send.

Runs the real Worker code (collector/collector.js) under node. Tests that need node skip without it.
"""
import os
import shutil
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SUITE = os.path.join(ROOT, "collector", "test-collector.mjs")


class CollectorTest(unittest.TestCase):
    def test_worker_logic_suite_passes(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node not installed")
        r = subprocess.run([node, SUITE], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, f"collector suite failed:\n{r.stdout}\n{r.stderr}")
        self.assertIn("checks passed", r.stdout)

    def test_allowlist_matches_the_client_payload(self):
        """The collector's field allowlists and the client's payload keys are the same set."""
        import sys
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import adt_phone_home as ph
        import tempfile
        client = set(ph.build_payload(tempfile.mkdtemp(), "0.1.0", {}))
        with open(os.path.join(ROOT, "collector", "collector.js")) as fh:
            js = fh.read()
        import re
        # The collector splits its allowlist across several lists; the union is
        # what the current schema may carry.
        server = set()
        for name in ("ALLOWED", "ALLOWED_V3", "ALLOWED_V4", "ALLOWED_V5"):
            m = re.search(r'export const %s = \[(.*?)\];' % name, js, re.S)
            self.assertIsNotNone(m, f"{name} not found in collector.js")
            server |= set(re.findall(r'"(\w+)"', m.group(1)))
        self.assertEqual(client, server,
                         f"client sends {client}, collector allows {server}")

    def test_the_clients_payload_is_schema_5_and_passes_validate(self):
        """Uses the real command counts from this machine's cache when it exists."""
        import subprocess
        import shutil
        node = shutil.which("node")
        if node is None:
            self.skipTest("node not installed")
        import sys
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import adt_phone_home as ph
        import json
        import tempfile
        cache = os.path.expanduser("~/.adt/agent-dev-team/cache")
        root = tempfile.mkdtemp()
        payload = ph.build_payload(
            root, "0.1.0",
            ph.command_counts(root, ROOT, cache_dir=cache if os.path.isdir(cache) else None))
        r = subprocess.run(
            [node, "--input-type=module", "-e",
             "import {validate} from './collector/collector.js';"
             "const b = JSON.parse(process.argv[1]);"
             "const v = validate(b);"
             "if (!v.ok) { console.error(v.why); process.exit(1); }"
             "if (b.schema !== 5) { console.error('not schema 5'); process.exit(1); }",
             json.dumps(payload)],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0,
                         f"the collector rejects the client's own payload: {r.stderr}")

    def _fixture_project(self):
        import sys
        import tempfile
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        tmp = tempfile.mkdtemp()
        state = os.path.join(tmp, ".adt", "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "usage.log"), "w", encoding="utf-8") as fh:
            fh.write("2026-09-01T10:00:00Z\tadt-plan\n")
            fh.write("2026-09-01T10:05:00Z\tadt-wt-graphql-pool\n")
        with open(os.path.join(state, "cost-ledger.log"), "w", encoding="utf-8") as fh:
            fh.write("2026-09-01T10:00:00Z\tADT-224\t10\t5\tsess-deadbeef\t"
                     "claude-opus-5\tfast\t0\t0\t0\tmeasured\n")
        return tmp

    def test_a_fixture_projects_payload_passes_validate(self):
        """Catches field type changes, which the allowlist test cannot see."""
        node = shutil.which("node")
        if node is None:
            self.skipTest("node not installed")
        import json
        import adt_phone_home as ph

        tmp = self._fixture_project()
        payload = ph.build_payload(tmp, "0.1.0", ph.command_counts(tmp, ROOT))
        collector = os.path.join(ROOT, "collector", "collector.js")
        src = os.path.join(tmp, "check.mjs")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write('import { validate } from "%s";\n'
                     "const r = validate(%s);\n"
                     'if (!r.ok) { console.error(r.why); process.exit(1); }\n'
                     % (collector, json.dumps(payload)))
        r = subprocess.run([node, src], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0,
                         f"the collector rejected the client's own payload: {r.stderr}")

    def test_no_field_carries_args_paths_or_ids(self):
        """No ticket id, session id, path, log name, branch or user name appears in the encoded payload."""
        import json
        import adt_phone_home as ph

        tmp = self._fixture_project()
        payload = ph.build_payload(tmp, "0.1.0", ph.command_counts(tmp, ROOT))
        wire = json.dumps(payload)
        for forbidden in ("ADT-224", "sess-deadbeef", tmp,
                          "usage.log", "cost-ledger", "adt-wt-graphql-pool",
                          "dev/", "zurichrich"):
            self.assertNotIn(forbidden, wire,
                             f"{forbidden!r} is in the payload: {wire}")

    def test_a_model_id_appears_only_in_the_models_map(self):
        """The models map holds only token and cost counts per model."""
        import json
        import adt_phone_home as ph

        tmp = self._fixture_project()
        payload = ph.build_payload(tmp, "0.1.0", ph.command_counts(tmp, ROOT))
        models = set(payload["models"])
        self.assertTrue(models, "expected the fixture ledger to name a model")

        without = dict(payload)
        without.pop("models")
        wire = json.dumps(without)
        for model in models:
            self.assertNotIn(model, wire,
                             f"model id {model!r} appears outside the models map")

        # Each model entry has counts only.
        for name, row in payload["models"].items():
            self.assertEqual(set(row), {"tokens", "micros"},
                             f"models.{name} carries more than counts: {row}")


if __name__ == "__main__":
    unittest.main()

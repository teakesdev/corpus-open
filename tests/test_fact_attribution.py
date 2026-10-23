"""Assertion attribution: agent work is recorded as agent work, with the
person who directed it, never under the CLI's legacy "human" default.

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import store  # noqa: E402

CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("MATTER_AUTHOR", "MATTER_DIRECTED_BY")}


class TestAttributionHelper(unittest.TestCase):
    def test_default_is_legacy_human(self):
        self.assertEqual(store.attribution(None), "human")

    def test_agent_directed_by_person(self):
        self.assertEqual(store.attribution("agent:model-x", "Pat Example"),
                         "agent:model-x; directed-by:Pat Example")

    def test_directed_by_requires_agent_author(self):
        with self.assertRaises(ValueError):
            store.attribution(None, "Pat Example")
        with self.assertRaises(ValueError):
            store.attribution("human", "Pat Example")

    def test_separator_and_newline_rejected(self):
        for bad in ("agent:x; directed-by:Someone Else", "agent:x\nforged"):
            with self.assertRaises(ValueError):
                store.attribution(bad)
        with self.assertRaises(ValueError):
            store.attribution("agent:x", "A;B")


class TestCliAttribution(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self._run("init", self.dir)
        with open(os.path.join(self.dir, "Evidence", "note.txt"), "w") as f:
            f.write("synthetic source\n")
        self._run("ingest", self.dir)

    def _run(self, *argv, env=None, check=True):
        return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *argv],
                              capture_output=True, text=True, check=check, env=env or CLEAN_ENV)

    def _created_by(self):
        conn = sqlite3.connect(os.path.join(self.dir, ".matter", "matter.db"))
        rows = [r[0] for r in conn.execute("SELECT created_by FROM assertions ORDER BY created_at")]
        conn.close()
        return rows

    def test_legacy_default_unchanged(self):
        self._run("fact", "add", self.dir, "--text", "t", "--status", "unknown")
        self.assertEqual(self._created_by(), ["human"])

    def test_agent_directed_by_recorded_and_listed(self):
        out = self._run("fact", "add", self.dir, "--text", "t", "--status", "documented-fact",
                        "--source", "Evidence/note.txt@line 1",
                        "--author", "agent:model-x", "--directed-by", "Pat Example").stdout
        self.assertIn("by agent:model-x; directed-by:Pat Example", out)
        self.assertEqual(self._created_by(), ["agent:model-x; directed-by:Pat Example"])
        listed = self._run("fact", "list", self.dir).stdout
        self.assertIn("by agent:model-x; directed-by:Pat Example", listed)

    def test_env_defaults(self):
        env = dict(CLEAN_ENV, MATTER_AUTHOR="agent:model-y", MATTER_DIRECTED_BY="Pat Example")
        self._run("fact", "add", self.dir, "--text", "t", "--status", "hypothesis", env=env)
        self.assertEqual(self._created_by(), ["agent:model-y; directed-by:Pat Example"])

    def test_invalid_attribution_writes_nothing(self):
        r = self._run("fact", "add", self.dir, "--text", "t", "--status", "unknown",
                      "--directed-by", "Pat Example", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid attribution", r.stderr)
        self.assertEqual(self._created_by(), [])


class TestMcpAttribution(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "init", self.dir],
                       check=True, capture_output=True, env=CLEAN_ENV)

    def _call(self, arguments):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "matter.fact_add", "arguments": arguments}}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")],
                           input=json.dumps(req) + "\n", capture_output=True, text=True,
                           env=dict(CLEAN_ENV, MATTER_DIR=self.dir), check=True)
        return json.loads(p.stdout.strip().splitlines()[-1])["result"]

    def test_directed_by_composed(self):
        res = self._call({"text": "t", "status": "UNKNOWN", "created_by": "agent:model-x",
                          "directed_by": "Pat Example"})
        self.assertNotIn("isError", res)
        self.assertIn("by agent:model-x; directed-by:Pat Example", res["content"][0]["text"])

    def test_default_agent_and_invalid_is_error(self):
        self.assertIn("by agent", self._call({"text": "t", "status": "UNKNOWN"})["content"][0]["text"])
        bad = self._call({"text": "t", "status": "UNKNOWN", "created_by": "human",
                          "directed_by": "Pat Example"})
        self.assertTrue(bad.get("isError"))


if __name__ == "__main__":
    unittest.main()

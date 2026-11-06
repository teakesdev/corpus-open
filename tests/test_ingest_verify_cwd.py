"""Week 0: documents.rel_path is matter-root-relative and cwd-independent.

Synthetic tmp matters only. Never a live case directory.
Run: python3 -m pytest tests/test_ingest_verify_cwd.py -v
     python3 -m unittest tests.test_ingest_verify_cwd -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matterkit import ingest, store  # noqa: E402
from matterkit.store import new_id  # noqa: E402

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATTER_PY = os.path.join(KIT, "matter.py")
MATTER_MCP = os.path.join(KIT, "matter-mcp.py")


class TestIngestVerifyCwd(unittest.TestCase):
    def setUp(self):
        self._matter = tempfile.TemporaryDirectory()
        self._foreign = tempfile.TemporaryDirectory()
        self.addCleanup(self._matter.cleanup)
        self.addCleanup(self._foreign.cleanup)
        self.dir = self._matter.name
        self.foreign = self._foreign.name
        r = subprocess.run(
            [sys.executable, MATTER_PY, "init", self.dir],
            check=True, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        os.makedirs(os.path.join(self.dir, "Research"), exist_ok=True)
        self.foo = os.path.join(self.dir, "Research", "foo.md")
        with open(self.foo, "w") as f:
            f.write("hello week0\n")

    def _cli(self, args, cwd):
        return subprocess.run(
            [sys.executable, MATTER_PY, *args],
            cwd=cwd, capture_output=True, text=True)

    def _rel_paths(self):
        conn = store.connect(self.dir)
        return [r["rel_path"] for r in conn.execute(
            "SELECT rel_path FROM documents ORDER BY rel_path")]

    def test_ingest_from_three_cwds_identical_rel_path(self):
        saved = os.getcwd()
        rels = []
        cwds = ("/tmp", self.dir, KIT)
        try:
            for cwd in cwds:
                os.chdir(cwd)
                conn = store.connect(self.dir)
                conn.execute("DELETE FROM documents")
                conn.commit()
                added, skipped, excluded = ingest.ingest_paths(
                    conn, self.dir, [self.foo])
                self.assertEqual((added, skipped, excluded), (1, 0, 0), cwd)
                rel = conn.execute("SELECT rel_path FROM documents").fetchone()["rel_path"]
                rels.append(rel)
                cwd_rel = os.path.relpath(self.foo, os.getcwd())
                # Compare via realpath: macOS tempfile is often /var vs /private/var.
                if os.path.realpath(os.getcwd()) != os.path.realpath(self.dir):
                    self.assertNotEqual(rel, cwd_rel, cwd)
                else:
                    self.assertEqual(
                        rel,
                        os.path.relpath(os.path.abspath(self.foo),
                                        os.path.abspath(self.dir)))
        finally:
            os.chdir(saved)
        expected = os.path.join("Research", "foo.md")
        self.assertEqual(set(rels), {expected})
        for rel in rels:
            self.assertFalse(os.path.isabs(rel))
            self.assertNotIn("..", rel.split(os.sep))
            self.assertNotIn(os.path.abspath(self.dir), rel)

    def test_cli_ingest_from_tmp_stores_research_foo(self):
        r = self._cli(["ingest", self.dir, self.foo], cwd="/tmp")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        rels = self._rel_paths()
        self.assertEqual(rels, [os.path.join("Research", "foo.md")])
        self.assertNotIn("..", rels[0])
        self.assertFalse(os.path.isabs(rels[0]))
        self.assertNotIn(os.path.abspath(self.dir), rels[0])

    def test_verify_from_three_cwds_identical_counts(self):
        r = self._cli(["ingest", self.dir, self.foo], cwd="/tmp")
        self.assertEqual(r.returncode, 0, r.stderr)
        abs_matter = os.path.abspath(self.dir)
        outputs = []
        for cwd in ("/tmp", self.dir, KIT):
            r = self._cli(["verify", abs_matter], cwd=cwd)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
            self.assertEqual(lines[0], f"root={abs_matter}")
            self.assertIn("· 0 changed · 0 missing", r.stdout)
            outputs.append(r.stdout)
        self.assertEqual(len(set(outputs)), 1)

    def test_delete_after_ingest_is_missing_nonzero(self):
        self._cli(["ingest", self.dir, self.foo], cwd="/tmp")
        os.remove(self.foo)
        r = self._cli(["verify", os.path.abspath(self.dir)], cwd="/tmp")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("MISSING", r.stdout)

    def test_bytes_change_is_changed_nonzero(self):
        self._cli(["ingest", self.dir, self.foo], cwd="/tmp")
        with open(self.foo, "w") as f:
            f.write("tampered\n")
        r = self._cli(["verify", os.path.abspath(self.dir)], cwd=KIT)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("CHANGED", r.stdout)

    def test_missing_case_dir_nonzero_and_does_not_create(self):
        missing = os.path.join(self.foreign, "no-such-matter")
        self.assertFalse(os.path.exists(missing))
        r = self._cli(["verify", missing], cwd="/tmp")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("root="), r.stdout)
        self.assertFalse(os.path.exists(missing))

    def test_case_dir_not_a_directory_nonzero(self):
        path = os.path.join(self.foreign, "not-a-dir")
        with open(path, "w") as f:
            f.write("x\n")
        r = self._cli(["verify", path], cwd="/tmp")
        self.assertNotEqual(r.returncode, 0)

    def test_old_cwd_relative_row_missing_until_reingest_no_silent_update(self):
        conn = store.connect(self.dir)
        bogus = os.path.relpath(self.foo, self.foreign)  # a sibling temp dir: "/tmp" holds the matter on Linux
        self.assertIn("..", bogus.split(os.sep) or [bogus])
        conn.execute(
            "INSERT INTO documents (id, sha256, rel_path, bytes, mtime, kind)"
            " VALUES (?,?,?,?,?,?)",
            (new_id(), "0" * 64, bogus, 1, 0, "research"))
        conn.commit()
        ok, bad, missing, failures = ingest.verify_integrity(conn, self.dir)
        self.assertGreater(missing, 0)
        kinds = {k for k, _ in failures}
        self.assertTrue(kinds & {"MISSING", "ESCAPED"})
        still = conn.execute("SELECT rel_path FROM documents").fetchone()["rel_path"]
        self.assertEqual(still, bogus)

    def test_escaped_locator_is_not_read(self):
        secret = os.path.join(self.foreign, "secret.txt")
        with open(secret, "w") as f:
            f.write("do-not-hash-me\n")
        digest = ingest.sha256_file(secret)
        escaped = os.path.relpath(secret, self.dir)
        self.assertTrue(escaped.startswith(".."), escaped)
        conn = store.connect(self.dir)
        conn.execute(
            "INSERT INTO documents (id, sha256, rel_path, bytes, mtime, kind)"
            " VALUES (?,?,?,?,?,?)",
            (new_id(), digest, escaped, os.path.getsize(secret), 0, "research"))
        conn.commit()
        ok, bad, missing, failures = ingest.verify_integrity(conn, self.dir)
        self.assertEqual(ok, 0)
        self.assertGreater(missing, 0)
        self.assertIn(("ESCAPED", escaped), failures)


class TestMcpIngestVerify(unittest.TestCase):
    def setUp(self):
        self._matter = tempfile.TemporaryDirectory()
        self.addCleanup(self._matter.cleanup)
        self.dir = self._matter.name
        subprocess.run(
            [sys.executable, MATTER_PY, "init", self.dir],
            check=True, capture_output=True)
        os.makedirs(os.path.join(self.dir, "Research"), exist_ok=True)
        self.foo = os.path.join(self.dir, "Research", "foo.md")
        with open(self.foo, "w") as f:
            f.write("mcp ingest\n")

    def _mcp(self, msgs, extra_env=None, cwd=None):
        env = dict(os.environ, MATTER_DIR=os.path.abspath(self.dir))
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, MATTER_MCP],
            input="\n".join(json.dumps(m) for m in msgs) + "\n",
            capture_output=True, text=True, env=env,
            cwd=cwd or "/tmp", timeout=30)

    def test_tools_list_includes_ingest_path_and_verify(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        r = self._mcp(msgs)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [json.loads(l) for l in r.stdout.strip().splitlines() if l.strip()]
        tools = [t["name"] for t in lines[-1]["result"]["tools"]]
        self.assertIn("matter.ingest_path", tools)
        self.assertIn("matter.verify", tools)

    def test_ingest_path_and_verify_from_foreign_cwd(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "matter.ingest_path",
                        "arguments": {"path": self.foo}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "matter.verify", "arguments": {}}},
        ]
        r = self._mcp(msgs, cwd="/tmp")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [json.loads(l) for l in r.stdout.strip().splitlines() if l.strip()]
        ingest_text = lines[1]["result"]["content"][0]["text"]
        counts = json.loads(ingest_text)
        self.assertEqual(counts["added"], 1)
        verify_text = lines[2]["result"]["content"][0]["text"]
        self.assertIn(f"root={os.path.abspath(self.dir)}", verify_text)
        self.assertIn("· 0 changed · 0 missing", verify_text)
        conn = store.connect(self.dir)
        rel = conn.execute("SELECT rel_path FROM documents").fetchone()["rel_path"]
        self.assertEqual(rel, os.path.join("Research", "foo.md"))

    def test_ingest_path_refuses_escape(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "matter.ingest_path",
                        "arguments": {"path": "/etc/passwd"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "matter.ingest_path",
                        "arguments": {"path": "../outside.md"}}},
        ]
        r = self._mcp(msgs, cwd="/tmp")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [json.loads(l) for l in r.stdout.strip().splitlines() if l.strip()]
        for msg in lines[1:]:
            self.assertTrue(msg["result"].get("isError"), msg)
            self.assertIn("escape", msg["result"]["content"][0]["text"].lower())
        conn = store.connect(self.dir)
        n = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()

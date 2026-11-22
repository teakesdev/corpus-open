"""Follow-ups DeepSeek's round 4 review of PR #6 left open.

1. The MCP server's brief check and cite lookup read a link that points out of the case folder.
2. A case folder reached through a link refuses its own real path ("/real/case/Evidence/x.md"
   when the case was set as "/alias").
3. The plugin's matter_ingest_path drops the kit's reason when it skips a file inside a folder.
4. A path that leaves the case through a linked folder ("Evidence/dirout/x.txt") is skipped with
   no word, not refused.
5. The practice case's terminal recipe only works from the kit's own folder, and does not say so.

matter.py's own draft_path is left alone on purpose: a person at a terminal may already name any
absolute path, so refusing a link there protects nothing.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hermes_plugin import PluginCase  # noqa: E402

from matterkit import cases  # noqa: E402
from matterkit import ingest as ing  # noqa: E402

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETBLOCK = os.path.join(KIT, "tests", "netblock_sitecustomize")
SECRET = "SECRET-WORDS See Smith v. Jones, 123 F.4th 456 (2024).\n"


def write(path, text=SECRET):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class TestResolveInMatter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.real = os.path.join(self._tmp.name, "real")
        with redirect_stdout(io.StringIO()):
            cases.init(self.real)
        self.alias = os.path.join(self._tmp.name, "alias")
        os.symlink(self.real, self.alias)

    def test_lexical_paths_unchanged(self):
        self.assertEqual(ing.resolve_in_matter("Evidence/a.md", self.alias),
                         os.path.join(self.alias, "Evidence", "a.md"))
        self.assertIsNone(ing.resolve_in_matter("../x.md", self.alias))

    def test_real_path_of_a_case_reached_through_a_link_maps_back(self):
        real_file = os.path.join(os.path.realpath(self.real), "Evidence", "a.md")
        self.assertEqual(ing.resolve_in_matter(real_file, self.alias),
                         os.path.join(self.alias, "Evidence", "a.md"))
        self.assertEqual(ing.resolve_in_matter(os.path.realpath(self.real), self.alias), self.alias)

    def test_a_link_at_the_leaf_is_not_followed_to_decide(self):
        # Only the folder part is resolved, so a file link keeps its own name and later link checks
        # still see it.
        outside = write(os.path.join(self._tmp.name, "out.md"))
        os.symlink(outside, os.path.join(self.real, "Evidence", "l.md"))
        got = ing.resolve_in_matter(os.path.join(os.path.realpath(self.real), "Evidence", "l.md"), self.alias)
        self.assertEqual(got, os.path.join(self.alias, "Evidence", "l.md"))

    def test_outside_stays_outside(self):
        self.assertIsNone(ing.resolve_in_matter(os.path.join(self._tmp.name, "other", "x.md"), self.alias))


class TestPluginFollowUps(PluginCase):
    def test_case_through_a_link_takes_its_real_path(self):
        alias = os.path.join(self._tmp.name, "alias")
        os.symlink(self.dir, alias)
        write(os.path.join(self.dir, "Evidence", "brief.md"), "The hearing is Tuesday.\n")
        p = self.plugin(matter_dir=alias)
        real_file = os.path.join(os.path.realpath(self.dir), "Evidence", "brief.md")
        self.assertNotIn("outside the case folder", p.command(f"check {real_file}"))
        r = self.call("matter_ingest_path", {"path": real_file})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["result"]["added"], 1)

    def test_ingest_of_a_folder_says_why_a_file_was_skipped(self):
        self.plugin()
        write(os.path.join(self.dir, "Evidence", "real.md"), "synthetic\n")
        os.symlink(write(os.path.join(self._tmp.name, "out.md")), os.path.join(self.dir, "Evidence", "out.md"))
        r = self.call("matter_ingest_path", {"path": "Evidence"})
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["result"]["added"], r["result"]["excluded"]), (1, 1))
        notes = r["result"]["notes"]
        self.assertEqual(len(notes), 1)
        self.assertIn("Evidence/out.md", notes[0])
        self.assertIn("outside", notes[0])
        self.assertNotIn(self._tmp.name, notes[0])  # case-relative, not the full path

    def test_nothing_skipped_has_no_notes(self):
        self.plugin()
        write(os.path.join(self.dir, "Evidence", "real.md"), "synthetic\n")
        self.assertEqual(self.call("matter_ingest_path", {"path": "Evidence"})["result"]["notes"], [])

    def test_a_path_through_a_linked_folder_out_of_the_case_is_refused(self):
        self.plugin()
        outdir = os.path.join(self._tmp.name, "outdir")
        write(os.path.join(outdir, "x.txt"))
        os.symlink(outdir, os.path.join(self.dir, "Evidence", "dirout"))
        r = self.call("matter_ingest_path", {"path": "Evidence/dirout/x.txt"})
        self.assertFalse(r["ok"])
        self.assertIn("outside the case folder", r["error"])


class TestMcpFollowUps(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "matter")
        with redirect_stdout(io.StringIO()):
            cases.init(self.dir)

    def mcp(self, *calls, matter_dir=None):
        msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "t", "version": "0"}}}]
        for i, (name, args) in enumerate(calls, 1):
            msgs.append({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                         "params": {"name": name, "arguments": args}})
        env = {k: v for k, v in os.environ.items() if k != "COURTLISTENER_TOKEN"}
        env.update(MATTER_DIR=matter_dir or self.dir, PYTHONPATH=NETBLOCK,
                   MATTER_KIT_CONFIG=os.path.join(self._tmp.name, "cfg"))
        r = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")],
                           input="\n".join(map(json.dumps, msgs)) + "\n", capture_output=True, text=True,
                           env=env, cwd=self._tmp.name, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return {m["id"]: m for m in map(json.loads, r.stdout.splitlines())}

    def test_brief_check_and_cite_lookup_refuse_a_link_out(self):
        os.symlink(write(os.path.join(self._tmp.name, "secret.md")), os.path.join(self.dir, "Evidence", "l.md"))
        out = self.mcp(("matter.brief_check", {"path": "Evidence/l.md"}),
                       ("matter.cite_lookup", {"path": "Evidence/l.md"}))
        for i in (1, 2):
            with self.subTest(call=i):
                self.assertTrue(out[i]["result"].get("isError"), out[i])
                text = out[i]["result"]["content"][0]["text"]
                self.assertIn("outside", text)
                self.assertNotIn("SECRET-WORDS", text)

    def test_a_link_inside_the_case_is_still_read(self):
        write(os.path.join(self.dir, "Drafts", "d.md"), "The hearing is Tuesday.\n")
        os.symlink(os.path.join(self.dir, "Drafts", "d.md"), os.path.join(self.dir, "Evidence", "in.md"))
        out = self.mcp(("matter.brief_check", {"path": "Evidence/in.md"}))
        self.assertFalse(out[1]["result"].get("isError"), out[1])

    def test_case_through_a_link_takes_its_real_path(self):
        alias = os.path.join(self._tmp.name, "alias")
        os.symlink(self.dir, alias)
        real_file = write(os.path.join(os.path.realpath(self.dir), "Evidence", "a.md"), "synthetic\n")
        out = self.mcp(("matter.ingest_path", {"path": real_file}), matter_dir=alias)
        self.assertFalse(out[1]["result"].get("isError"), out[1])
        self.assertEqual(json.loads(out[1]["result"]["content"][0]["text"])["added"], 1)


class TestDemoReadmeFolder(unittest.TestCase):
    def test_says_where_to_run_the_recipe(self):
        with open(os.path.join(KIT, "demo-matter", "README.md"), encoding="utf-8") as f:
            text = f.read()
        before = text[:text.index("```bash")]
        self.assertIn("the folder that holds matter.py", before)


if __name__ == "__main__":
    unittest.main()

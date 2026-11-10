"""P3s DeepSeek's review of PR #7 left open.

F1. The case folder's own real path (case set up through a link) got an internal message.
F2. The reply never said which name a registered path was recorded under.
F3. The MCP server's matter.ingest_path dropped the kit's skip reasons.
F4. A direct link out of the case was refused with "leads through a link", which fits only a
    linked folder on the way.
F5. The README's "one level up from this README" is wrong in the copied practice case.
F6. notes stripped any run of "!"/" " and replaced the case root anywhere in the line; the tool
    schema did not mention notes.
F7. The kit's skip branch and the command line were pinned only through the plugin.
F8. On a case-insensitive disk, another capitalization of an in-case path was refused.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hermes_plugin import PluginCase  # noqa: E402
from test_pr6_followups import KIT, TestMcpFollowUps, write  # noqa: E402

from matterkit import cases  # noqa: E402
from matterkit import ingest as ing  # noqa: E402
from matterkit import store  # noqa: E402


def case_insensitive(folder):
    probe = os.path.join(folder, "CaseProbe")
    open(probe, "w").close()
    try:
        return os.path.exists(os.path.join(folder, "caseprobe"))
    finally:
        os.remove(probe)


class TestPluginP3s(PluginCase):
    def alias(self):
        alias = os.path.join(self._tmp.name, "alias")
        os.symlink(self.dir, alias)
        return alias

    def test_f1_real_case_root_says_it_is_the_case_folder(self):
        p = self.plugin(matter_dir=self.alias())
        real_root = os.path.realpath(self.dir)
        r = self.call("matter_ingest_path", {"path": real_root})
        self.assertFalse(r["ok"])
        self.assertIn("case folder itself", r["error"])
        self.assertNotIn("invalid matter-relative", r["error"])
        out = p.command(f"check {real_root}")
        self.assertIn("case folder itself", out)

    def test_f2_reply_says_what_name_the_path_was_recorded_under(self):
        write(os.path.join(self.dir, "Drafts", "d.md"), "synthetic\n")
        os.symlink(os.path.join(self.dir, "Drafts"), os.path.join(self.dir, "Evidence", "dirlink"))
        self.plugin(matter_dir=self.alias())
        real = os.path.join(os.path.realpath(self.dir), "Evidence", "dirlink", "d.md")
        r = self.call("matter_ingest_path", {"path": real})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["result"]["added"], 1)
        with closing(store.connect(self.dir)) as conn:
            recorded = [row[0] for row in conn.execute("SELECT rel_path FROM documents")]
        self.assertIn(r["result"]["recorded_as"], recorded)

    def test_f2_folder_reply_names_the_folder(self):
        write(os.path.join(self.dir, "Evidence", "a.md"), "synthetic\n")
        self.plugin()
        r = self.call("matter_ingest_path", {"path": "Evidence"})
        self.assertEqual(r["result"]["recorded_as"], "Evidence")

    def test_f4_direct_link_out_says_it_is_a_link(self):
        os.symlink(write(os.path.join(self._tmp.name, "out.md")), os.path.join(self.dir, "Evidence", "l.md"))
        self.plugin()
        r = self.call("matter_ingest_path", {"path": "Evidence/l.md"})
        self.assertFalse(r["ok"])
        self.assertIn("is a link to a file outside the case folder", r["error"])

    def test_f6_notes_keep_a_root_spelled_twice_and_strip_only_the_marker(self):
        # A folder whose case-relative path repeats the case root's full spelling.
        inner = os.path.join(self.dir, "Evidence", self.dir.lstrip(os.sep))
        os.makedirs(inner)
        os.symlink(write(os.path.join(self._tmp.name, "out.md")), os.path.join(inner, "!x.md"))
        self.plugin()
        r = self.call("matter_ingest_path", {"path": "Evidence"})
        notes = r["result"]["notes"]
        self.assertEqual(len(notes), 1)
        rel = os.path.relpath(os.path.join(inner, "!x.md"), self.dir)
        self.assertTrue(notes[0].endswith(": " + rel), notes)

    def test_f6_schema_mentions_notes_and_recorded_as(self):
        self.plugin()
        desc = self.ctx.tools["matter_ingest_path"][1]["description"]
        self.assertIn("notes", desc)
        self.assertIn("recorded_as", desc)

    def test_f8_other_capitalization_of_an_in_case_path(self):
        if not case_insensitive(self._tmp.name):
            self.skipTest("this disk tells capital letters apart")
        write(os.path.join(self.dir, "Evidence", "a.md"), "synthetic\n")
        self.plugin()
        other = os.path.join(os.path.dirname(self.dir), os.path.basename(self.dir).upper(), "evidence", "a.md")
        r = self.call("matter_ingest_path", {"path": other})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["result"]["added"], 1)


class TestRound1P3s(PluginCase):
    """DeepSeek's round 1 review of PR #9."""

    def test_recorded_as_names_the_stored_spelling_after_a_skip(self):
        write(os.path.join(self.dir, "Evidence", "a.md"), "synthetic\n")
        os.symlink(os.path.join(self.dir, "Evidence"), os.path.join(self.dir, "Filings", "ev"))
        self.plugin()
        first = self.call("matter_ingest_path", {"path": "Evidence/a.md"})["result"]
        self.assertEqual((first["added"], first["recorded_as"]), (1, "Evidence/a.md"))
        again = self.call("matter_ingest_path", {"path": "Filings/ev/a.md"})
        self.assertTrue(again["ok"], again)
        self.assertEqual((again["result"]["skipped"], again["result"]["recorded_as"]), (1, "Evidence/a.md"))

    def test_double_slash_spelling_of_the_case(self):
        write(os.path.join(self.dir, "Evidence", "a.md"), "synthetic\n")
        p = self.plugin()
        r = self.call("matter_ingest_path", {"path": "/" + self.dir})
        self.assertIn("case folder itself", r["error"])
        r = self.call("matter_ingest_path", {"path": "/" + os.path.join(self.dir, "Evidence", "a.md")})
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["result"]["added"], r["result"]["recorded_as"]), (1, "Evidence/a.md"))
        self.assertIn("case folder itself", p.command("check /" + self.dir))

    def test_a_double_slash_case_setting_still_registers(self):
        write(os.path.join(self.dir, "Evidence", "a.md"), "synthetic\n")
        self.plugin(matter_dir="/" + self.dir)
        r = self.call("matter_ingest_path", {"path": "Evidence/a.md"})
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["result"]["added"], r["result"]["recorded_as"]), (1, "Evidence/a.md"))
        r = self.call("matter_ingest_path", {"path": "Evidence"})
        self.assertTrue(r["ok"], r)

    def test_notes_cut_a_double_slash_root(self):
        self.assertEqual(ing.case_relative_notes("  ! not found: /x/case/E/a.md\n", "//x/case"),
                         ["not found: E/a.md"])


class TestResolveP3s(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "matter")
        with redirect_stdout(io.StringIO()):
            cases.init(self.dir)

    def test_f8_case_folding_maps_under_the_case(self):
        if not case_insensitive(self._tmp.name):
            self.skipTest("this disk tells capital letters apart")
        other = os.path.join(os.path.dirname(self.dir), "MATTER", "Evidence", "a.md")
        self.assertEqual(ing.resolve_in_matter(other, self.dir),
                         os.path.join(self.dir, "Evidence", "a.md"))

    def test_f8_a_sibling_folder_is_still_outside(self):
        sib = os.path.join(self._tmp.name, "matter2")
        os.makedirs(sib)
        self.assertIsNone(ing.resolve_in_matter(os.path.join(sib, "a.md"), self.dir))
        self.assertIsNone(ing.resolve_in_matter(os.path.join(self._tmp.name, "a.md"), self.dir))

    def test_f7_kit_skip_branch_says_why(self):
        write(os.path.join(self.dir, "Evidence", "ok.md"), "synthetic\n")
        os.symlink(write(os.path.join(self._tmp.name, "out.md")), os.path.join(self.dir, "Evidence", "out.md"))
        os.symlink(os.path.join(self.dir, "Evidence", "gone.md"), os.path.join(self.dir, "Evidence", "dang.md"))
        said = io.StringIO()
        with redirect_stdout(said), closing(store.connect(self.dir)) as conn:
            counts = ing.ingest_paths(conn, self.dir, [os.path.join(self.dir, "Evidence")])
        self.assertEqual(counts, (1, 0, 2))
        text = said.getvalue()
        self.assertIn("links outside this matter", text)
        self.assertIn("not found", text)

    def test_f7_command_line_reports_the_skip(self):
        os.symlink(write(os.path.join(self._tmp.name, "out.md")), os.path.join(self.dir, "Evidence", "out.md"))
        r = subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "ingest", self.dir],
                           capture_output=True, text=True, timeout=60, cwd=self._tmp.name)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("links outside this matter", r.stdout)
        self.assertIn("1 excluded", r.stdout)


class TestMcpP3s(unittest.TestCase):
    setUp = TestMcpFollowUps.setUp
    mcp = TestMcpFollowUps.mcp

    def test_mcp_recorded_as_after_a_skip_and_double_slash(self):
        write(os.path.join(self.dir, "Evidence", "a.md"), "synthetic\n")
        os.symlink(os.path.join(self.dir, "Evidence"), os.path.join(self.dir, "Filings", "ev"))
        out = self.mcp(("matter.ingest_path", {"path": "Evidence/a.md"}),
                       ("matter.ingest_path", {"path": "Filings/ev/a.md"}),
                       ("matter.ingest_path", {"path": "/" + self.dir}))
        second = json.loads(out[2]["result"]["content"][0]["text"])
        self.assertEqual((second["skipped"], second["recorded_as"]), (1, "Evidence/a.md"))
        self.assertTrue(out[3]["result"].get("isError"), out[3])
        self.assertIn("case folder itself", out[3]["result"]["content"][0]["text"])

    def test_f3_mcp_ingest_says_why_a_file_was_skipped(self):
        write(os.path.join(self.dir, "Evidence", "ok.md"), "synthetic\n")
        os.symlink(write(os.path.join(self._tmp.name, "out.md")), os.path.join(self.dir, "Evidence", "out.md"))
        out = self.mcp(("matter.ingest_path", {"path": "Evidence"}))
        reply = json.loads(out[1]["result"]["content"][0]["text"])
        self.assertEqual((reply["added"], reply["excluded"]), (1, 1))
        self.assertEqual(len(reply["notes"]), 1)
        self.assertIn("Evidence/out.md", reply["notes"][0])
        self.assertNotIn(self._tmp.name, reply["notes"][0])
        self.assertEqual(reply["recorded_as"], "Evidence")

    def test_f1_mcp_real_case_root_is_not_said_to_escape(self):
        alias = os.path.join(self._tmp.name, "alias")
        os.symlink(self.dir, alias)
        out = self.mcp(("matter.ingest_path", {"path": os.path.realpath(self.dir)}), matter_dir=alias)
        self.assertTrue(out[1]["result"].get("isError"), out[1])
        text = out[1]["result"]["content"][0]["text"]
        self.assertIn("case folder itself", text)
        self.assertNotIn("escapes", text)


class TestDemoReadmeP3(unittest.TestCase):
    def test_f5_no_one_level_up_clause(self):
        with open(os.path.join(KIT, "demo-matter", "README.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("one level up", text)
        self.assertIn("the folder that holds matter.py", text)


if __name__ == "__main__":
    unittest.main()

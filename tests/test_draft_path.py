"""A draft path is read relative to the case folder, as the README shows, wherever the command runs.

`brief check`, `cite lookup` and `cite extract` used to open the path relative to the current
directory, so `matter.py cite lookup ~/cases/my-case Filings/drafts/RESPONSE_v1.md` crashed with a
traceback unless it ran from inside the case folder. A path relative to the current directory
still works when the case folder has no such file. No test reaches the network.
"""
import os
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_cite_lookup import DRAFT, Matter, run_cli  # noqa: E402

REL = os.path.join("Filings", "drafts", "RESPONSE_v1.md")


class DraftPath(Matter):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.dir, "Filings", "drafts"))
        with open(os.path.join(self.dir, REL), "w", encoding="utf-8") as f:
            f.write(DRAFT)
        self.elsewhere = tempfile.mkdtemp(dir=self._tmp.name)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.elsewhere)  # never inside the case folder


class TestCaseRelative(DraftPath):
    def test_cite_lookup_dry_run(self):
        code, out, err = run_cli("cite", "lookup", self.dir, REL, "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("123 F.4th 456", out)
        self.assertNotIn("Traceback", err)

    def test_brief_check(self):
        code, out, err = run_cli("brief", "check", self.dir, REL)
        self.assertNotIn("refused", err)
        self.assertIn("# Brief check: RESPONSE_v1.md", out)
        self.assertIn(code, (0, 1, 2))

    def test_cite_extract(self):
        code, out, err = run_cli("cite", "extract", self.dir, REL)
        self.assertEqual(code, 0, err)
        self.assertIn("citation(s) from", out)


class TestOtherForms(DraftPath):
    def test_absolute_path(self):
        code, out, err = run_cli("cite", "lookup", self.dir, os.path.join(self.dir, REL), "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("123 F.4th 456", out)

    def test_path_relative_to_cwd_still_works(self):
        with open(os.path.join(self.elsewhere, "outside.md"), "w", encoding="utf-8") as f:
            f.write(DRAFT)
        code, out, err = run_cli("cite", "lookup", self.dir, "outside.md", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("123 F.4th 456", out)

    def test_case_folder_wins_when_both_exist(self):
        os.makedirs(os.path.join(self.elsewhere, "Filings", "drafts"))
        with open(os.path.join(self.elsewhere, REL), "w", encoding="utf-8") as f:
            f.write("No cites here.\n")
        code, out, err = run_cli("cite", "lookup", self.dir, REL, "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("123 F.4th 456", out)


class TestMissing(DraftPath):
    def test_cite_lookup_missing_is_a_message(self):
        code, out, err = run_cli("cite", "lookup", self.dir, "Filings/drafts/NOPE.md", "--dry-run")
        self.assertNotEqual(code, 0)
        self.assertIn("no such draft", err)
        self.assertIn("NOPE.md", err)
        self.assertNotIn("Traceback", err)

    def test_cite_extract_missing_is_a_message(self):
        code, out, err = run_cli("cite", "extract", self.dir, "Filings/drafts/NOPE.md")
        self.assertNotEqual(code, 0)
        self.assertIn("no such draft", err)

    def test_brief_check_missing_is_refused(self):
        code, out, err = run_cli("brief", "check", self.dir, "Filings/drafts/NOPE.md")
        self.assertNotEqual(code, 0)
        self.assertIn("refused", err)


if __name__ == "__main__":
    unittest.main()

"""Subcases: a nested matter with its own store, a hard ingest/verify boundary, and
hash-checked `parent:` references. Nothing a subcase does may write to the parent store.

Synthetic tempdirs only. Run: python3 -m unittest discover -s tests -v
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import ingest, store  # noqa: E402

CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("MATTER_AUTHOR", "MATTER_DIRECTED_BY")}
FOLDERS = ["Filings", "Evidence", "Correspondence", "Case_Tracking", "Research", "Media"]


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def sha_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class SubBase(unittest.TestCase):
    """A parent matter with one registered filing; `self.child` is where a subcase lives."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.parent = os.path.join(self._tmp.name, "Parent-Case")
        self.run_cli("init", self.parent)
        write(os.path.join(self.parent, "Filings", "order.txt"), "Synthetic parent order. Quote: sealed.\n")
        self.run_cli("ingest", self.parent)
        self.child = os.path.join(self.parent, "Subcases", "child")

    def run_cli(self, *argv, env=None, check=True):
        return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *argv],
                              capture_output=True, text=True, check=check, env=env or CLEAN_ENV)

    def make_subcase(self, slug="child", title="Child matter", *extra):
        return self.run_cli("subcase", "init", self.parent, slug, "--title", title, *extra)

    def rows(self, matter, sql, params=()):
        conn = sqlite3.connect(os.path.join(matter, ".matter", "matter.db"))
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def rel_paths(self, matter):
        return [r[0] for r in self.rows(matter, "SELECT rel_path FROM documents ORDER BY rel_path")]

    def parent_db_bytes(self):
        with open(os.path.join(self.parent, ".matter", "matter.db"), "rb") as f:
            return f.read()


class TestSubcaseInit(SubBase):
    def test_creates_matter_with_standard_folders_store_and_manifest(self):
        out = self.make_subcase("child", "Spin-off libel matter").stdout
        self.assertIn(self.child, out)
        for f in FOLDERS:
            self.assertTrue(os.path.isdir(os.path.join(self.child, f)), f)
        self.assertTrue(os.path.isfile(os.path.join(self.child, ".matter", "matter.db")))
        with open(os.path.join(self.child, "matter.json")) as f:
            m = json.load(f)
        self.assertEqual(m["parent"], "../..")
        self.assertEqual(m["relation"], "subcase")
        self.assertEqual(m["title"], "Spin-off libel matter")
        self.assertEqual(m["created_by"], "human")
        self.assertRegex(m["created_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(set(m), {"parent", "relation", "title", "created_at", "created_by"})
        # the manifest's relative parent really leads back to the parent matter
        self.assertTrue(os.path.samefile(os.path.normpath(os.path.join(self.child, m["parent"])), self.parent))

    def test_agent_attribution_recorded_not_human(self):
        self.make_subcase("child", "T", "--author", "agent:model-x", "--directed-by", "Pat Example")
        with open(os.path.join(self.child, "matter.json")) as f:
            self.assertEqual(json.load(f)["created_by"], "agent:model-x; directed-by:Pat Example")

    def test_env_attribution_default(self):
        env = dict(CLEAN_ENV, MATTER_AUTHOR="agent:model-y", MATTER_DIRECTED_BY="Pat Example")
        self.run_cli("subcase", "init", self.parent, "child", "--title", "T", env=env)
        with open(os.path.join(self.child, "matter.json")) as f:
            self.assertEqual(json.load(f)["created_by"], "agent:model-y; directed-by:Pat Example")

    def test_invalid_attribution_creates_nothing(self):
        r = self.run_cli("subcase", "init", self.parent, "child", "--title", "T",
                         "--directed-by", "Pat Example", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid attribution", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.parent, "Subcases")))

    def test_refuses_parent_without_store_and_creates_nothing(self):
        bare = os.path.join(self._tmp.name, "not-a-matter")
        os.makedirs(bare)
        r = self.run_cli("subcase", "init", bare, "child", "--title", "T", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no matter store", r.stderr)
        self.assertEqual(os.listdir(bare), [])

    def test_refuses_missing_parent_dir_without_creating_it(self):
        gone = os.path.join(self._tmp.name, "gone")
        r = self.run_cli("subcase", "init", gone, "child", "--title", "T", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no matter store", r.stderr)
        self.assertFalse(os.path.exists(gone))

    def test_bad_slugs_refused_and_nothing_created(self):
        for bad in ("", "../x", "a/b", "a\\b", "..", ".hidden", "a b", "a..b", "x" * 82, "/etc/x", "é"):
            r = self.run_cli("subcase", "init", self.parent, bad, "--title", "T", check=False)
            self.assertNotEqual(r.returncode, 0, repr(bad))
            self.assertIn("slug", r.stderr, repr(bad))
        self.assertFalse(os.path.exists(os.path.join(self.parent, "Subcases")))
        self.assertFalse(os.path.exists(os.path.join(self._tmp.name, "x")))

    def test_good_slugs_accepted(self):
        for ok in ("a", "Related-Claim", "v1.2_final", "x" * 81):
            self.make_subcase(ok, "T")
            self.assertTrue(os.path.isfile(os.path.join(self.parent, "Subcases", ok, "matter.json")), ok)

    def test_title_required_and_not_blank(self):
        r = self.run_cli("subcase", "init", self.parent, "child", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--title", r.stderr)
        r = self.run_cli("subcase", "init", self.parent, "child", "--title", "   ", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--title", r.stderr)
        self.assertFalse(os.path.exists(self.child))

    def test_refuses_existing_folder_and_leaves_it_alone(self):
        self.make_subcase("child", "First")
        before = open(os.path.join(self.child, "matter.json")).read()
        r = self.run_cli("subcase", "init", self.parent, "child", "--title", "Second", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already exists", r.stderr)
        self.assertEqual(open(os.path.join(self.child, "matter.json")).read(), before)
        # a plain pre-existing folder (no store) is refused too
        os.makedirs(os.path.join(self.parent, "Subcases", "plain"))
        r = self.run_cli("subcase", "init", self.parent, "plain", "--title", "T", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(os.listdir(os.path.join(self.parent, "Subcases", "plain")), [])

    def test_refuses_symlinked_subcases_folder(self):
        elsewhere = os.path.join(self._tmp.name, "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, os.path.join(self.parent, "Subcases"))
        r = self.run_cli("subcase", "init", self.parent, "child", "--title", "T", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("symlink", r.stderr)
        self.assertEqual(os.listdir(elsewhere), [])

    def test_parent_store_bytes_unchanged_by_init(self):
        before = self.parent_db_bytes()
        self.make_subcase()
        self.assertEqual(self.parent_db_bytes(), before)
        self.assertFalse(os.path.exists(os.path.join(self.parent, ".matter", "matter.db-journal")))


class TestIngestBoundary(SubBase):
    """A folder holding its own .matter/ is a boundary: the parent never registers its files."""

    def setUp(self):
        super().setUp()
        self.make_subcase()
        write(os.path.join(self.child, "Evidence", "child-only.txt"), "synthetic child evidence\n")
        write(os.path.join(self.child, "Subcases", "grand", "Evidence", "g.txt"), "grandchild\n")
        store.connect(os.path.join(self.child, "Subcases", "grand")).close()  # a matter nested in the child

    def test_parent_ingest_does_not_register_subcase_files(self):
        out = self.run_cli("ingest", self.parent).stdout
        self.assertIn("+0 new", out)  # order.txt already registered; nothing from Subcases/
        self.assertEqual(self.rel_paths(self.parent), ["Filings/order.txt"])
        self.assertFalse([p for p in self.rel_paths(self.parent) if "Subcases" in p])

    def test_parent_ingest_of_new_parent_file_still_works(self):
        write(os.path.join(self.parent, "Filings", "new.txt"), "another parent filing\n")
        out = self.run_cli("ingest", self.parent).stdout
        self.assertIn("+1 new", out)
        self.assertEqual(self.rel_paths(self.parent), ["Filings/new.txt", "Filings/order.txt"])

    def test_subcase_own_ingest_still_registers_its_files(self):
        out = self.run_cli("ingest", self.child).stdout
        self.assertIn("+", out)
        rels = self.rel_paths(self.child)
        self.assertIn("Evidence/child-only.txt", rels)
        self.assertIn("matter.json", rels)
        self.assertFalse([p for p in rels if p.startswith("Subcases")])  # its own nested matter is a boundary too

    def test_explicit_path_into_a_subcase_is_excluded(self):
        for target in (os.path.join(self.child, "Evidence", "child-only.txt"),
                       os.path.join(self.child, "Evidence"),
                       self.child):
            out = self.run_cli("ingest", self.parent, target).stdout
            self.assertIn("+0 new", out, target)
            self.assertIn("1 excluded", out, target)
            self.assertIn("nested matter", out, target)
        self.assertEqual(self.rel_paths(self.parent), ["Filings/order.txt"])

    def test_plain_folder_and_dot_matter_file_are_not_boundaries(self):
        write(os.path.join(self.parent, "Subcases", "notes", "n.txt"), "plain folder, not a matter\n")
        write(os.path.join(self.parent, "Research", "odd", ".matter"), "a file named .matter is not a store\n")
        write(os.path.join(self.parent, "Research", "odd", "o.txt"), "kept\n")
        self.run_cli("ingest", self.parent)
        rels = self.rel_paths(self.parent)
        self.assertIn("Subcases/notes/n.txt", rels)
        self.assertIn("Research/odd/o.txt", rels)

    def test_walk_files_never_descends_into_nested_matter_but_root_itself_is_fine(self):
        seen = list(ingest.walk_files(self.parent))
        self.assertTrue(any(p.endswith("order.txt") for p in seen))
        self.assertFalse(any(os.sep + "Subcases" + os.sep in p for p in seen))
        self.assertTrue(any(p.endswith("child-only.txt") for p in ingest.walk_files(self.child)))

    def test_mcp_ingest_path_respects_boundary(self):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "matter.ingest_path",
                          "arguments": {"path": os.path.join(self.child, "Evidence", "child-only.txt")}}}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input=json.dumps(req) + "\n",
                           capture_output=True, text=True, env=dict(CLEAN_ENV, MATTER_DIR=self.parent), check=True)
        res = json.loads(json.loads(p.stdout)["result"]["content"][0]["text"])
        self.assertEqual((res["added"], res["excluded"]), (0, 1))

    def test_parent_verify_counts_only_its_own_documents(self):
        self.run_cli("ingest", self.parent)
        out = self.run_cli("verify", self.parent).stdout
        self.assertIn("integrity: 1 ok · 0 changed · 0 missing", out)

    def test_rows_registered_before_a_folder_became_a_matter_are_still_verified(self):
        # The boundary stops new registration. It never hides a claim the parent already made:
        # verify walks stored rows, so a legacy row under a later-nested matter is still checked.
        legacy = os.path.join(self.parent, "Evidence", "legacy")
        write(os.path.join(legacy, "old.txt"), "registered first\n")
        self.run_cli("ingest", self.parent)
        store.connect(legacy).close()  # the folder becomes a matter afterwards
        self.assertIn("Evidence/legacy/old.txt", self.rel_paths(self.parent))
        self.assertIn("2 ok", self.run_cli("verify", self.parent).stdout)
        write(os.path.join(legacy, "old.txt"), "tampered\n")
        r = self.run_cli("verify", self.parent, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("CHANGED: Evidence/legacy/old.txt", r.stdout)


if __name__ == "__main__":
    unittest.main()

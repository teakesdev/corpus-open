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


class ParentBase(SubBase):
    """A subcase exists; helpers to cite parent documents from it."""

    def setUp(self):
        super().setUp()
        self.make_subcase()
        self.parent_file = os.path.join(self.parent, "Filings", "order.txt")
        self.parent_sha = sha_of(self.parent_file)

    def fact(self, *sources, status="documented-fact", text="The court sealed the order.", check=False, extra=()):
        argv = ["fact", "add", self.child, "--text", text, "--status", status, *extra]
        for src in sources:
            argv += ["--source", src]
        return self.run_cli(*argv, check=check)

    def child_state(self):
        return (self.rows(self.child, "SELECT id, rel_path, sha256, kind, notes FROM documents ORDER BY rel_path"),
                self.rows(self.child, "SELECT id FROM assertions"),
                self.rows(self.child, "SELECT assertion_id, document_id, locator FROM assertion_sources"))

    def assert_refused(self, r, needle):
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn(needle, r.stderr)
        self.assertEqual(self.child_state(), ([], [], []), "a refused parent reference must change nothing")


class TestParentReferences(ParentBase):
    def test_reference_is_recorded_without_copying_bytes(self):
        r = self.fact("parent:Filings/order.txt@p. 2", check=True)
        self.assertIn("[DOCUMENTED FACT]", r.stdout)
        self.assertNotIn("UNSUPPORTED", r.stdout)
        docs, asserts, sources = self.child_state()
        self.assertEqual(len(docs), 1)
        doc_id, rel, sha, kind, notes = docs[0]
        self.assertEqual(rel, "parent:Filings/order.txt")
        self.assertEqual(sha, self.parent_sha)
        parent_row = self.rows(self.parent, "SELECT id, kind FROM documents WHERE rel_path='Filings/order.txt'")[0]
        self.assertEqual(kind, parent_row[1])
        self.assertIn(f"reference to parent matter document {parent_row[0]}", notes)
        self.assertIn("bytes live in the parent", notes)
        self.assertEqual(sources, [(asserts[0][0], doc_id, "p. 2")])
        copies = [f for _, _, fs in os.walk(self.child) for f in fs if f == "order.txt"]
        self.assertEqual(copies, [], "no bytes may be copied into the subcase")

    def test_same_parent_document_is_registered_once(self):
        self.fact("parent:Filings/order.txt@p. 1", check=True)
        self.fact("parent:Filings/order.txt@p. 2", text="Another fact.", check=True)
        docs, asserts, sources = self.child_state()
        self.assertEqual(len(docs), 1)
        self.assertEqual(len(asserts), 2)
        self.assertEqual(sorted(x[2] for x in sources), ["p. 1", "p. 2"])

    def test_same_hash_already_registered_locally_is_reused_and_said_so(self):
        write(os.path.join(self.child, "Evidence", "copy.txt"), open(self.parent_file).read())
        self.run_cli("ingest", self.child)
        r = self.fact("parent:Filings/order.txt@p. 1", check=True)
        self.assertIn("reused", r.stdout)
        self.assertIn("Evidence/copy.txt", r.stdout)
        docs, _, sources = self.child_state()
        self.assertFalse([d for d in docs if d[1].startswith("parent:")])
        local = [d for d in docs if d[1] == "Evidence/copy.txt"][0]
        self.assertEqual(sources[0][1], local[0])

    def test_escape_refused(self):
        for bad in ("parent:../x", "parent:Filings/../order.txt", "parent:/etc/hosts", "parent:", "parent:..",
                    "parent:../Subcases/child/matter.json"):
            self.assert_refused(self.fact(bad), "parent")
        self.assertEqual(self.rows(self.parent, "SELECT COUNT(*) FROM documents")[0][0], 1)

    def test_escape_message_says_escape(self):
        r = self.fact("parent:../x")
        self.assertIn("escapes", r.stderr)

    def test_unregistered_in_parent_refused_even_if_file_exists(self):
        write(os.path.join(self.parent, "Filings", "unregistered.txt"), "on disk, never ingested\n")
        self.assert_refused(self.fact("parent:Filings/unregistered.txt"), "not registered in the parent")
        self.assert_refused(self.fact("parent:Filings/ghost.txt"), "not registered in the parent")

    def test_parent_file_changed_refused(self):
        write(self.parent_file, "edited after it was registered\n")
        self.assert_refused(self.fact("parent:Filings/order.txt"), "changed in parent")

    def test_parent_file_missing_refused(self):
        os.remove(self.parent_file)
        self.assert_refused(self.fact("parent:Filings/order.txt"), "missing in parent")

    def test_refused_parent_ref_aborts_the_whole_fact_even_with_a_good_source(self):
        write(os.path.join(self.child, "Evidence", "local.txt"), "local evidence\n")
        self.run_cli("ingest", self.child)
        before = self.child_state()
        r = self.fact("Evidence/local.txt@l. 1", "parent:../x")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.child_state(), before)

    def test_rebaselined_parent_hash_refused_and_rolled_back(self):
        self.fact("parent:Filings/order.txt@p. 1", check=True)
        write(self.parent_file, "the parent re-registered this path with new bytes\n")
        conn = sqlite3.connect(os.path.join(self.parent, ".matter", "matter.db"))
        conn.execute("UPDATE documents SET sha256=? WHERE rel_path='Filings/order.txt'", (sha_of(self.parent_file),))
        conn.commit()
        conn.close()
        before = self.child_state()
        r = self.fact("parent:Filings/order.txt@p. 2", text="A second fact.")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("different sha256", r.stderr)
        self.assertEqual(self.child_state(), before, "the new assertion must roll back with the refusal")

    def test_not_a_subcase_refused(self):
        r = self.run_cli("fact", "add", self.parent, "--text", "t", "--status", "unknown",
                         "--source", "parent:Filings/order.txt", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a subcase", r.stderr)
        self.assertEqual(self.rows(self.parent, "SELECT COUNT(*) FROM assertions")[0][0], 0)

    def test_broken_manifest_refused(self):
        manifest = os.path.join(self.child, "matter.json")
        for content, needle in (("{not json", "not a subcase"), ('{"relation": "subcase"}', "not a subcase"),
                                ('{"parent": ""}', "not a subcase"), ('[1]', "not a subcase"),
                                ('{"parent": "."}', "itself")):
            write(manifest, content)
            self.assert_refused(self.fact("parent:Filings/order.txt"), needle)
        os.remove(manifest)
        self.assert_refused(self.fact("parent:Filings/order.txt"), "not a subcase")

    def test_parent_without_store_refused_and_none_is_created(self):
        elsewhere = os.path.join(self._tmp.name, "no-store-here")
        os.makedirs(elsewhere)
        write(os.path.join(self.child, "matter.json"),
              json.dumps({"parent": os.path.relpath(elsewhere, self.child), "relation": "subcase"}))
        self.assert_refused(self.fact("parent:Filings/order.txt"), "no store")
        self.assertEqual(os.listdir(elsewhere), [])

    def test_plain_sources_behave_exactly_as_before(self):
        r = self.fact("Evidence/ghost.txt@l. 1", check=True)  # unrecognized: warned, not refused
        self.assertIn("unrecognized source refs", r.stdout)
        self.assertIn("UNSUPPORTED", r.stdout)

    def test_fact_list_shows_parent_reference(self):
        self.fact("parent:Filings/order.txt@p. 2", check=True)
        out = self.run_cli("fact", "list", self.child).stdout
        self.assertIn("parent:Filings/order.txt@p. 2", out)

    def test_agent_attribution_recorded_on_a_parent_cited_fact(self):
        self.fact("parent:Filings/order.txt", extra=("--author", "agent:model-x", "--directed-by", "Pat Example"),
                  check=True)
        self.assertEqual(self.rows(self.child, "SELECT created_by FROM assertions")[0][0],
                         "agent:model-x; directed-by:Pat Example")
        self.assertEqual(len(self.child_state()[2]), 1)  # and the parent source really attached

    def _mcp(self, arguments):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "matter.fact_add", "arguments": arguments}}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input=json.dumps(req) + "\n",
                           capture_output=True, text=True, env=dict(CLEAN_ENV, MATTER_DIR=self.child), check=True)
        return json.loads(p.stdout.strip().splitlines()[-1])["result"]

    def test_mcp_fact_add_accepts_parent_refs_and_refuses_escapes(self):
        ok = self._mcp({"text": "t", "status": "DOCUMENTED FACT", "sources": ["parent:Filings/order.txt@p. 1"]})
        self.assertNotIn("isError", ok, ok)
        self.assertEqual(self.rows(self.child, "SELECT rel_path FROM documents")[0][0], "parent:Filings/order.txt")
        before = self.child_state()
        bad = self._mcp({"text": "t2", "status": "DOCUMENTED FACT", "sources": ["parent:../x"]})
        self.assertTrue(bad.get("isError"))
        self.assertIn("escapes", bad["content"][0]["text"])
        write(self.parent_file, "changed\n")
        bad = self._mcp({"text": "t3", "status": "DOCUMENTED FACT", "sources": ["parent:Filings/order.txt"]})
        self.assertTrue(bad.get("isError"))
        self.assertIn("changed in parent", bad["content"][0]["text"])
        self.assertEqual(self.child_state(), before)

    def test_parent_prefix_is_reserved_so_a_local_file_cannot_masquerade(self):
        write(os.path.join(self.child, "parent:fake.txt"), "a local file with a reserved name\n")
        out = self.run_cli("ingest", self.child).stdout
        self.assertIn("reserved", out)
        self.assertFalse([p for p in self.rel_paths(self.child) if p.startswith("parent:")])


class TestVerifyParentReferences(ParentBase):
    def setUp(self):
        super().setUp()
        self.fact("parent:Filings/order.txt@p. 1", check=True)

    def verify(self):
        r = self.run_cli("verify", self.child, check=False)
        lines = r.stdout.splitlines()
        return r.returncode, [l for l in lines if l.startswith("integrity:")][0], [l for l in lines if "⚠" in l]

    def test_intact_reference_counts_as_ok_in_the_same_format(self):
        self.assertEqual(self.verify(), (0, "integrity: 1 ok · 0 changed · 0 missing", []))

    def test_counts_combine_with_local_documents(self):
        write(os.path.join(self.child, "Evidence", "local.txt"), "local\n")
        self.run_cli("ingest", self.child)
        self.assertEqual(self.verify()[1], "integrity: 3 ok · 0 changed · 0 missing")  # local.txt, matter.json, parent ref

    def test_changed_in_parent_is_changed(self):
        write(self.parent_file, "edited later\n")
        code, line, fails = self.verify()
        self.assertEqual((code, line), (1, "integrity: 0 ok · 1 changed · 0 missing"))
        self.assertEqual(fails, ["  ⚠ CHANGED: parent:Filings/order.txt"])

    def test_missing_in_parent_is_missing(self):
        os.remove(self.parent_file)
        code, line, fails = self.verify()
        self.assertEqual((code, line), (1, "integrity: 0 ok · 0 changed · 1 missing"))
        self.assertEqual(fails, ["  ⚠ MISSING: parent:Filings/order.txt"])

    def test_parent_dropping_its_registration_is_missing(self):
        conn = sqlite3.connect(os.path.join(self.parent, ".matter", "matter.db"))
        conn.execute("DELETE FROM documents WHERE rel_path='Filings/order.txt'")
        conn.commit()
        conn.close()
        code, line, _ = self.verify()
        self.assertEqual((code, line), (1, "integrity: 0 ok · 0 changed · 1 missing"))

    def test_parent_registering_a_different_hash_is_changed(self):
        conn = sqlite3.connect(os.path.join(self.parent, ".matter", "matter.db"))
        conn.execute("UPDATE documents SET sha256=? WHERE rel_path='Filings/order.txt'", ("0" * 64,))
        conn.commit()
        conn.close()
        self.assertEqual(self.verify()[:2], (1, "integrity: 0 ok · 1 changed · 0 missing"))

    def test_unreachable_parent_store_is_missing_not_a_crash(self):
        os.rename(os.path.join(self.parent, ".matter"), os.path.join(self.parent, ".matter-gone"))
        code, line, _ = self.verify()
        self.assertEqual((code, line), (1, "integrity: 0 ok · 0 changed · 1 missing"))
        self.assertFalse(os.path.exists(os.path.join(self.parent, ".matter")), "verify must not create a parent store")

    def test_tampered_reference_that_escapes_is_flagged_like_a_local_escape(self):
        conn = sqlite3.connect(os.path.join(self.child, ".matter", "matter.db"))
        conn.execute("INSERT INTO documents (id,sha256,rel_path) VALUES ('x1',?,?)", ("1" * 64, "parent:../secret"))
        conn.commit()
        conn.close()
        code, line, fails = self.verify()
        self.assertEqual((code, line), (1, "integrity: 1 ok · 0 changed · 1 missing"))
        self.assertEqual(fails, ["  ⚠ ESCAPED: parent:../secret"])

    def test_mcp_verify_resolves_parent_rows(self):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "matter.verify", "arguments": {}}}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input=json.dumps(req) + "\n",
                           capture_output=True, text=True, env=dict(CLEAN_ENV, MATTER_DIR=self.child), check=True)
        self.assertIn("integrity: 1 ok · 0 changed · 0 missing", json.loads(p.stdout)["result"]["content"][0]["text"])


class TestDeadlineSourcesInSubcase(ParentBase):
    def deadline_id(self):
        self.run_cli("deadline", "add", self.child, "--label", "Reply", "--due", "2099-01-01")
        return self.rows(self.child, "SELECT id FROM deadlines")[0][0]

    def test_parent_source_is_resolved_registered_and_recorded(self):
        did = self.deadline_id()
        self.run_cli("deadline", "update", self.child, did, "--state", "met", "--reason", "Filed",
                     "--source", "parent:Filings/order.txt@p. 3")
        self.assertEqual(self.rows(self.child, "SELECT rel_path FROM documents")[0][0], "parent:Filings/order.txt")
        sources = json.loads(self.rows(self.child, "SELECT sources FROM events ORDER BY rowid DESC")[0][0])
        self.assertEqual(sources, ["parent:Filings/order.txt@p. 3"])

    def test_changed_or_escaping_parent_source_refused_and_nothing_changes(self):
        did = self.deadline_id()
        n_events = self.rows(self.child, "SELECT COUNT(*) FROM events")[0][0]
        for src in ("parent:../x",):
            r = self.run_cli("deadline", "update", self.child, did, "--state", "met", "--reason", "x",
                             "--source", src, check=False)
            self.assertNotEqual(r.returncode, 0)
        write(self.parent_file, "edited\n")
        r = self.run_cli("deadline", "update", self.child, did, "--state", "met", "--reason", "x",
                         "--source", "parent:Filings/order.txt", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("changed in parent", r.stderr)
        self.assertEqual(self.rows(self.child, "SELECT state FROM deadlines")[0][0], "open")
        self.assertEqual(self.rows(self.child, "SELECT COUNT(*) FROM events")[0][0], n_events)
        self.assertEqual(self.rows(self.child, "SELECT COUNT(*) FROM documents")[0][0], 0)


def tree_digest(root, skip=("Subcases",)):
    """Relative path -> sha256 of every file under root, ignoring top-level folders in `skip`."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath == root:
            dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            out[os.path.relpath(fp, root)] = sha_of(fp)
    return out


class TestParentIsNeverWrittenBySubcaseOperations(ParentBase):
    def test_parent_store_and_files_are_byte_identical_after_a_battery_of_subcase_operations(self):
        db_before = self.parent_db_bytes()
        files_before = tree_digest(self.parent)
        write(os.path.join(self.child, "Evidence", "local.txt"), "local\n")
        mcp = lambda name, args: subprocess.run(  # noqa: E731
            [sys.executable, os.path.join(KIT, "matter-mcp.py")], capture_output=True, text=True, check=True,
            input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": name, "arguments": args}}) + "\n",
            env=dict(CLEAN_ENV, MATTER_DIR=self.child))
        self.make_subcase("second", "Second subcase")
        self.run_cli("ingest", self.child)
        self.fact("parent:Filings/order.txt@p. 1", check=True)
        self.fact("parent:../x")                                   # refused
        self.fact("parent:Filings/ghost.txt")                      # refused
        self.run_cli("fact", "list", self.child)
        self.run_cli("verify", self.child)
        self.run_cli("status", self.child)
        self.run_cli("packet", self.child)
        self.run_cli("deadline", "add", self.child, "--label", "L", "--due", "2020-01-01")
        did = self.rows(self.child, "SELECT id FROM deadlines")[0][0]
        self.run_cli("deadline", "update", self.child, did, "--state", "lapsed", "--reason", "r",
                     "--source", "parent:Filings/order.txt")
        self.run_cli("deadline", "list", self.child, "--all")
        mcp("matter.fact_add", {"text": "m", "status": "UNKNOWN", "sources": ["parent:Filings/order.txt"]})
        mcp("matter.verify", {})
        mcp("matter.status", {})
        mcp("matter.deadline_list", {})
        self.assertEqual(self.parent_db_bytes(), db_before)
        self.assertEqual(sorted(os.listdir(os.path.join(self.parent, ".matter"))), ["matter.db"],
                         "no journal, wal or other file may appear in the parent store folder")
        self.assertEqual(tree_digest(self.parent), files_before, "parent files must not be added, moved or edited")



class TestStatus(SubBase):
    def deadline(self, matter, label, due, state=None):
        self.run_cli("deadline", "add", matter, "--label", label, "--due", due)
        if state:
            did = self.rows(matter, "SELECT id FROM deadlines WHERE label=?", (label,))[0][0]
            self.run_cli("deadline", "update", matter, did, "--state", state, "--reason", "test")

    def status(self, matter):
        return self.run_cli("status", matter).stdout

    def test_parent_without_subcases_prints_no_subcase_section(self):
        out = self.status(self.parent)
        self.assertNotIn("Subcase", out)

    def test_parent_lists_subcases_with_title_open_and_overdue_counts(self):
        self.make_subcase("alpha", "Alpha libel matter")
        self.make_subcase("beta", "Beta matter")
        a = os.path.join(self.parent, "Subcases", "alpha")
        self.deadline(a, "Past open", "2020-01-01")
        self.deadline(a, "Past open 2", "2020-02-01")
        self.deadline(a, "Future", "2099-01-01")
        self.deadline(a, "Past but met", "2020-03-01", state="met")
        out = self.status(self.parent)
        alpha = next(l for l in out.splitlines() if "alpha" in l)
        beta = next(l for l in out.splitlines() if "beta" in l)
        self.assertIn("Alpha libel matter", alpha)
        self.assertIn("Open deadlines: 3", alpha)   # met one is not open
        self.assertIn("⚠ OVERDUE: 2", alpha)
        self.assertIn("Beta matter", beta)
        self.assertIn("Open deadlines: 0", beta)
        self.assertIn("OVERDUE: 0", beta)
        self.assertNotIn("⚠", beta)
        self.assertIn("Subcases: 2", out)

    def test_only_immediate_children_with_store_and_manifest_are_listed(self):
        self.make_subcase("real", "Real one")
        os.makedirs(os.path.join(self.parent, "Subcases", "plain-folder"))               # no store
        store.connect(os.path.join(self.parent, "Subcases", "no-manifest")).close()      # store, no matter.json
        write(os.path.join(self.parent, "Subcases", "a-file.txt"), "not a directory\n")
        real = os.path.join(self.parent, "Subcases", "real")
        self.run_cli("subcase", "init", real, "grand", "--title", "Grandchild")          # not immediate
        out = self.status(self.parent)
        self.assertIn("Subcases: 1", out)
        self.assertIn("real", out)
        for absent in ("plain-folder", "no-manifest", "a-file.txt", "grand", "Grandchild"):
            self.assertNotIn(absent, out)

    def test_unreadable_child_store_is_reported_not_fatal(self):
        self.make_subcase("broken", "Broken one")
        with open(os.path.join(self.parent, "Subcases", "broken", ".matter", "matter.db"), "wb") as f:
            f.write(b"this is not a sqlite database" * 50)
        out = self.status(self.parent)
        line = next(l for l in out.splitlines() if "broken" in l)
        self.assertIn("store unreadable", line)

    def test_status_reads_children_without_writing_to_them(self):
        self.make_subcase("alpha", "Alpha")
        db = os.path.join(self.parent, "Subcases", "alpha", ".matter", "matter.db")
        before = open(db, "rb").read()
        self.status(self.parent)
        self.assertEqual(open(db, "rb").read(), before)

    def test_subcase_status_names_its_parent(self):
        self.make_subcase("alpha", "Alpha libel matter")
        out = self.status(os.path.join(self.parent, "Subcases", "alpha"))
        line = next(l for l in out.splitlines() if "Subcase of" in l)
        self.assertIn("Parent-Case", line)
        parent_path = line.split(" (", 1)[1].split(")", 1)[0]
        self.assertTrue(os.path.samefile(parent_path, self.parent))
        self.assertIn("title: Alpha libel matter", line)
        self.assertNotIn("Subcases:", out)  # a subcase with no children lists none

    def test_subcase_status_leaves_parent_store_untouched(self):
        self.make_subcase("alpha", "Alpha")
        before = self.parent_db_bytes()
        self.status(os.path.join(self.parent, "Subcases", "alpha"))
        self.assertEqual(self.parent_db_bytes(), before)

    def test_plain_matter_status_is_unchanged(self):
        out = self.status(self.parent)
        self.assertNotIn("Subcase of", out)
        self.assertIn("Documents registered: **1**", out)

    def test_mcp_status_includes_subcases(self):
        self.make_subcase("alpha", "Alpha libel matter")
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "matter.status", "arguments": {}}}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input=json.dumps(req) + "\n",
                           capture_output=True, text=True, env=dict(CLEAN_ENV, MATTER_DIR=self.parent), check=True)
        self.assertIn("Alpha libel matter", json.loads(p.stdout)["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()

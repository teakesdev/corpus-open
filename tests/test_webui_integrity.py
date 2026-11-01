"""Local UI file and store integrity under races and failures (Astra round 1, F1-F5).

Each test drives the WSGI app directly with a fixed interleaving: a scan, a failure, or a
folder swapped in the middle of a request. After every one, the case's files and its store
must agree: no registered document is missing, no file is left that nothing registered, and
nothing lands in or is removed from a folder the request did not pin.

Design: docs/superpowers/specs/2026-10-31-local-frontend-design.md §4, §8. Synthetic data only.
Run: python3 -m unittest discover -s tests -v
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webui_helpers import (Body, build_workspace, call, cases, direct_app, documents, facts_add_direct,  # noqa: E402
                           ingest, quiet, store, subcase, tree_snapshot, webui)
from matterkit import facts  # noqa: E402


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk-integrity-")
        self.ws = os.path.join(self.tmp, "workspace")
        os.makedirs(self.ws)
        self.alpha, self.beta = build_workspace(self.ws)
        conn = store.connect(self.alpha)  # start with every file registered, so a scan's count is only the race's
        quiet(ingest.ingest_paths, conn, self.alpha, [self.alpha])
        conn.close()
        self.app = direct_app(self.ws)
        self.ids = {c["rel"]: c["id"] for c in cases.discover(self.ws)}
        self.a = self.ids["Alpha v. City"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name, data, folder="Evidence", case=None, **kw):
        return call(self.app, "PUT", f"/cases/{case or self.a}/files?folder={folder}&name={name}", raw=data, **kw)

    def scan(self, case=None):
        return call(self.app, "POST", f"/cases/{case or self.a}/scan", {})

    def assert_consistent(self, case_dir):
        """Every registered document is on disk with its bytes, and nothing under the taxonomy
        folders or .matter/incoming is unregistered junk from an upload."""
        status, v = call(self.app, "POST", f"/cases/{self.ids[os.path.relpath(case_dir, self.ws).replace(os.sep, '/')]}/verify", {})
        self.assertEqual(status, 200)
        self.assertEqual((v["changed"], v["missing"]), (0, 0), v)
        incoming = os.path.join(case_dir, ".matter", "incoming")
        self.assertEqual(os.listdir(incoming) if os.path.isdir(incoming) else [], [])

    def outside_case(self):
        outside = os.path.join(self.tmp, "outside-case")
        quiet(cases.init, outside)
        return outside


class TestUploadAndScan(Workspace):
    """F1: a scan never sees a half-written upload, and never makes an upload delete a file."""

    def test_scan_while_the_body_is_streaming(self):
        seen = []
        status, data = self.put("streamed.txt", b"fresh synthetic bytes\n",
                                stream=Body(b"fresh synthetic bytes\n", on_first=lambda: seen.append(self.scan())))
        self.assertEqual(seen[0][0], 200)
        self.assertEqual(seen[0][1]["added"], 0)  # the partial upload is invisible to the walk
        self.assertEqual(status, 200, data)
        self.assertIn("Evidence/streamed.txt", documents(self.alpha))
        self.assert_consistent(self.alpha)

    def test_scan_after_the_body_is_read(self):
        seen = []
        status, data = self.put("after.txt", b"after-body bytes\n",
                                stream=Body(b"after-body bytes\n", on_end=lambda: seen.append(self.scan())))
        self.assertEqual(seen[0][1]["added"], 0)
        self.assertEqual(status, 200, data)
        self.assert_consistent(self.alpha)

    def test_scan_racing_registration(self):
        """A scan that finds the published file before the upload commits waits for the store's
        write lock, then counts the file as already registered; both succeed."""
        real = cases.Dir.link_from
        results = []

        def link_then_scan(dest, *a, **kw):
            real(dest, *a, **kw)
            t = threading.Thread(target=lambda: results.append(self.scan()))
            t.start()
            t.join(0.3)  # the scan is now blocked on the upload's write lock, or has not reached it
            results.append(t)
        with mock.patch.object(cases.Dir, "link_from", link_then_scan):
            status, data = self.put("raced.txt", b"raced bytes\n")
        results[0].join(10)
        self.assertEqual(status, 200, data)
        self.assertEqual(results[1][0], 200, results[1])
        self.assertEqual(results[1][1]["added"], 0)
        self.assertIn("Evidence/raced.txt", documents(self.alpha))
        self.assert_consistent(self.alpha)

    def test_same_upload_sent_twice(self):
        """Identical bytes to the same name again (a retry after a lost response) is already done."""
        self.assertEqual(self.put("again.txt", b"sent twice\n")[0], 200)
        before = tree_snapshot(self.ws)
        status, data = self.put("again.txt", b"sent twice\n")
        self.assertEqual((status, data["already_registered"]), (200, True))
        self.assertEqual(tree_snapshot(self.ws), before)
        self.assert_consistent(self.alpha)

    def test_upload_then_scan(self):
        self.assertEqual(self.put("plain.txt", b"plain bytes\n")[0], 200)
        status, data = self.scan()
        self.assertEqual((status, data["added"]), (200, 0))
        self.assert_consistent(self.alpha)

    def test_ingest_counts_a_row_registered_meanwhile_as_already_there(self):
        """The shared ingest path (CLI, MCP, scan): a UNIQUE clash on insert, because another
        writer registered the same file after the lookup, is a skip, not an error."""
        p = os.path.join(self.alpha, "Correspondence", "letter.txt")
        with open(p, "w") as f:
            f.write("synthetic letter\n")
        conn = store.connect(self.alpha)

        class Racing:
            def __init__(self, inner):
                self.inner = inner

            def execute(self, sql, *args):
                cur = self.inner.execute(sql, *args)
                if sql.startswith("SELECT id FROM documents WHERE sha256"):
                    other = store.connect(self.inner_dir)
                    other.execute("INSERT INTO documents (id, sha256, rel_path, bytes, mtime, kind) VALUES (?,?,?,?,?,?)",
                                  ("d_other", args[0][0], args[0][1], 1, 0.0, "correspondence"))
                    other.commit()
                    other.close()
                return cur

            def commit(self):
                self.inner.commit()
        racing = Racing(conn)
        racing.inner_dir = self.alpha
        try:
            added, skipped, excluded = quiet(ingest.ingest_paths, racing, self.alpha, [p])
        finally:
            conn.close()
        self.assertEqual((added, skipped, excluded), (0, 1, 0))


class TestUploadFailures(Workspace):
    """F3: whatever fails after the bytes arrive, the request leaves no file and no row behind,
    and the same upload can simply be sent again."""

    def assert_clean_then_retry(self, before, name, data):
        self.assertEqual(tree_snapshot(self.ws), before)
        self.assertNotIn(f"Evidence/{name}", documents(self.alpha))
        status, body = self.put(name, data)
        self.assertEqual(status, 200, body)
        self.assert_consistent(self.alpha)

    def test_disconnect_mid_body(self):
        before = tree_snapshot(self.ws)
        status, _ = self.put("cut.txt", b"cut short\n", stream=Body(b"cut short\n", fail=ConnectionResetError("gone")))
        self.assertGreaterEqual(status, 400)
        self.assert_clean_then_retry(before, "cut.txt", b"cut short\n")

    def test_store_cannot_be_opened(self):
        before = tree_snapshot(self.ws)
        with mock.patch.object(webui.store, "connect_existing", side_effect=sqlite3.OperationalError("disk I/O error")):
            status, _ = self.put("noopen.txt", b"no store\n")
        self.assertGreaterEqual(status, 400)
        self.assert_clean_then_retry(before, "noopen.txt", b"no store\n")

    def test_insert_fails(self):
        before = tree_snapshot(self.ws)
        with mock.patch.object(webui, "_insert_document", side_effect=sqlite3.OperationalError("database is locked")):
            status, _ = self.put("noinsert.txt", b"no insert\n")
        self.assertGreaterEqual(status, 400)
        self.assert_clean_then_retry(before, "noinsert.txt", b"no insert\n")

    def test_store_busy_is_a_plain_retry_message(self):
        before = tree_snapshot(self.ws)
        holder = store.connect(self.alpha)
        holder.execute("BEGIN IMMEDIATE")
        try:
            with mock.patch.object(webui, "BUSY_TIMEOUT", 0.1):
                status, data = self.put("busy.txt", b"busy store\n")
        finally:
            holder.rollback()
            holder.close()
        self.assertEqual(status, 503)
        self.assertIn("try again", data["error"].lower())
        self.assert_clean_then_retry(before, "busy.txt", b"busy store\n")

    def test_same_bytes_twice_at_once(self):
        """Two uploads of identical bytes under different names, both fully received before either
        registers: one is kept, the other is told it is a duplicate; no 500, no stray file."""
        barrier = threading.Barrier(2)
        out = {}

        def send(name):
            out[name] = self.put(name, b"twin bytes\n", stream=Body(b"twin bytes\n", on_end=lambda: barrier.wait(5)))
        threads = [threading.Thread(target=send, args=(n,)) for n in ("twin-a.txt", "twin-b.txt")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(15)
        self.assertEqual(sorted(s for s, _ in out.values()), [200, 409], out)
        kept = [n for n, (s, _) in out.items() if s == 200][0]
        self.assertEqual(sorted(p for p in os.listdir(os.path.join(self.alpha, "Evidence")) if p.startswith("twin")), [kept])
        self.assert_consistent(self.alpha)


class TestSwapsMidRequest(Workspace):
    """F4: a request is pinned to the folders it checked. A rename or a swapped-in link part way
    makes it fail without writing anywhere else, and cleanup removes only what it created."""

    def test_case_folder_swapped_for_a_link_after_lookup(self):
        outside = self.outside_case()
        real = cases.resolve

        def swapped(ws, cid):
            entry = real(ws, cid)
            os.rename(self.alpha, os.path.join(self.ws, "moved-away"))
            os.symlink(outside, self.alpha)
            return entry
        outside_before = tree_snapshot(outside)
        with mock.patch.object(webui.cases, "resolve", swapped):
            status, _ = self.put("escaped.txt", b"race bytes\n")
        self.assertEqual(status, 409)
        self.assertEqual(tree_snapshot(outside), outside_before)
        self.assertEqual(documents(outside), {})
        self.assertNotIn("Evidence/escaped.txt", documents(os.path.join(self.ws, "moved-away")))

    def test_case_folder_swapped_while_streaming(self):
        outside = self.outside_case()
        moved = os.path.join(self.ws, "moved-away")

        def swap():
            os.rename(self.alpha, moved)
            os.symlink(outside, self.alpha)
        outside_before = tree_snapshot(outside)
        status, _ = self.put("mid.txt", b"mid-stream bytes\n", stream=Body(b"mid-stream bytes\n", on_first=swap))
        self.assertEqual(status, 409)
        self.assertEqual(tree_snapshot(outside), outside_before)
        self.assertFalse(os.path.exists(os.path.join(moved, "Evidence", "mid.txt")))
        self.assertNotIn("Evidence/mid.txt", documents(moved))
        self.assertEqual(os.listdir(os.path.join(moved, ".matter", "incoming")), [])

    def test_taxonomy_folder_swapped_while_streaming(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        evidence = os.path.join(self.alpha, "Evidence")

        def swap():
            os.rename(evidence, os.path.join(self.alpha, "Evidence-moved"))
            os.symlink(elsewhere, evidence)
        status, _ = self.put("tax.txt", b"taxonomy race\n", stream=Body(b"taxonomy race\n", on_first=swap))
        self.assertEqual(status, 409)
        self.assertEqual(os.listdir(elsewhere), [])
        self.assertFalse(os.path.exists(os.path.join(self.alpha, "Evidence-moved", "tax.txt")))
        self.assertNotIn("Evidence/tax.txt", documents(self.alpha))

    def test_cleanup_spares_a_replaced_staging_file(self):
        """The request's own partial file is renamed and something else put at its name before a
        disconnect: cleanup must leave the stranger alone."""
        incoming = os.path.join(self.alpha, ".matter", "incoming")

        def replace():
            (part,) = os.listdir(incoming)
            os.rename(os.path.join(incoming, part), os.path.join(self.tmp, "taken.part"))
            with open(os.path.join(incoming, part), "w") as f:
                f.write("someone else's file\n")
            replace.part = part
        status, _ = self.put("gone.txt", b"partial\n",
                             stream=Body(b"partial\n", on_first=replace, fail=ConnectionResetError("gone")))
        self.assertGreaterEqual(status, 400)
        with open(os.path.join(incoming, replace.part)) as f:
            self.assertEqual(f.read(), "someone else's file\n")

    def test_staging_file_replaced_without_an_error(self):
        """Something swaps the arriving file for its own: the stranger is neither published nor
        registered under the upload's hash, and is left where it is."""
        incoming = os.path.join(self.alpha, ".matter", "incoming")

        def replace():
            (part,) = os.listdir(incoming)
            os.rename(os.path.join(incoming, part), os.path.join(self.tmp, "taken.part"))
            with open(os.path.join(incoming, part), "w") as f:
                f.write("someone else's file\n")
            replace.part = part
        status, _ = self.put("swapped.txt", b"the real bytes\n", stream=Body(b"the real bytes\n", on_end=replace))
        self.assertEqual(status, 409)
        self.assertFalse(os.path.exists(os.path.join(self.alpha, "Evidence", "swapped.txt")))
        self.assertNotIn("Evidence/swapped.txt", documents(self.alpha))
        self.assertTrue(os.path.exists(os.path.join(incoming, replace.part)))

    def test_cleanup_spares_a_replaced_published_file(self):
        evidence = os.path.join(self.alpha, "Evidence")

        def replace_then_fail(*a, **kw):
            os.rename(os.path.join(evidence, "pub.txt"), os.path.join(self.tmp, "pub-moved.txt"))
            with open(os.path.join(evidence, "pub.txt"), "w") as f:
                f.write("a different file now\n")
            raise sqlite3.OperationalError("disk I/O error")
        with mock.patch.object(webui, "_insert_document", replace_then_fail):
            status, _ = self.put("pub.txt", b"published bytes\n")
        self.assertGreaterEqual(status, 400)
        with open(os.path.join(evidence, "pub.txt")) as f:
            self.assertEqual(f.read(), "a different file now\n")
        self.assertNotIn("Evidence/pub.txt", documents(self.alpha))

    def test_brief_check_reads_the_draft_it_was_shown(self):
        """The draft changes between the list and the check: the report would describe other
        bytes, so the request is refused."""
        from matterkit import briefcheck
        draft = os.path.join(self.alpha, "Filings", "drafts", "response_v1.md")
        real = briefcheck.check

        def edited_meanwhile(matter_dir, path, **kw):
            with open(draft, "a") as f:
                f.write("\nA sentence added during the check.\n")
            return real(matter_dir, path, **kw)
        with mock.patch.object(briefcheck, "check", edited_meanwhile):
            status, data = call(self.app, "POST", f"/cases/{self.a}/brief-check",
                                {"path": "Filings/drafts/response_v1.md"})
        self.assertEqual(status, 409, data)

    def test_brief_check_refuses_a_draft_swapped_for_a_link(self):
        secret = os.path.join(self.tmp, "outside.md")
        with open(secret, "w") as f:
            f.write("# Not this case\n")
        draft = os.path.join(self.alpha, "Filings", "drafts", "response_v1.md")
        real = webui._drafts

        def listed_then_swapped(root):
            out = real(root)
            os.remove(draft)
            os.symlink(secret, draft)
            return out
        with mock.patch.object(webui, "_drafts", listed_then_swapped):
            status, _ = call(self.app, "POST", f"/cases/{self.a}/brief-check", {"path": "Filings/drafts/response_v1.md"})
        self.assertEqual(status, 409)

    def test_new_case_rollback_spares_a_replaced_folder(self):
        target = os.path.join(self.ws, "Gamma")

        def init_after_swap(case_dir):
            os.rename(target, os.path.join(self.ws, "Gamma-ours"))
            os.makedirs(os.path.join(target, "Evidence"))
            with open(os.path.join(target, "Evidence", "keep.txt"), "w") as f:
                f.write("not created by this request\n")
            raise OSError("disk full")
        with mock.patch.object(cases, "init", init_after_swap):
            status, _ = call(self.app, "POST", "/cases", {"name": "Gamma"})
        self.assertGreaterEqual(status, 400)
        self.assertTrue(os.path.isfile(os.path.join(target, "Evidence", "keep.txt")))


class TestLinkedStores(Workspace):
    """F2: a case is its own folder, its own `.matter/` and its own `matter.db`, none of them a link."""

    def test_linked_matter_folder_is_not_a_case(self):
        outside = self.outside_case()
        os.makedirs(os.path.join(self.ws, "Alias"))
        os.symlink(os.path.join(outside, ".matter"), os.path.join(self.ws, "Alias", ".matter"))
        self.assertNotIn("Alias", [c["rel"] for c in cases.discover(self.ws)])
        import hashlib
        alias_id = hashlib.sha256(b"Alias").hexdigest()[:16]
        status, _ = call(self.app, "POST", f"/cases/{alias_id}/facts", {"text": "probe", "status": "UNKNOWN"})
        self.assertEqual(status, 404)
        conn = store.connect_readonly(outside)
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM assertions").fetchone()["c"], 0)
        conn.close()

    def test_linked_database_file_is_not_a_case(self):
        outside = self.outside_case()
        os.makedirs(os.path.join(self.ws, "Alias2", ".matter"))
        os.symlink(store.db_path(outside), os.path.join(self.ws, "Alias2", ".matter", "matter.db"))
        self.assertNotIn("Alias2", [c["rel"] for c in cases.discover(self.ws)])

    def test_matter_folder_swapped_for_a_link_after_lookup(self):
        outside = self.outside_case()
        meta = os.path.join(self.beta, ".matter")
        real = cases.resolve

        def swapped(ws, cid):
            entry = real(ws, cid)
            os.rename(meta, os.path.join(self.beta, ".matter-moved"))
            os.symlink(os.path.join(outside, ".matter"), meta)
            return entry
        with mock.patch.object(webui.cases, "resolve", swapped):
            status, _ = call(self.app, "POST", f"/cases/{self.ids['Beta Matter']}/facts", {"text": "probe", "status": "UNKNOWN"})
        self.assertEqual(status, 409)
        conn = store.connect_readonly(outside)
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM assertions").fetchone()["c"], 0)
        conn.close()

    def test_scan_skips_a_file_linked_outside_the_case(self):
        secret = os.path.join(self.tmp, "outside.txt")
        with open(secret, "w") as f:
            f.write("outside the workspace\n")
        os.symlink(secret, os.path.join(self.beta, "Evidence", "linked.txt"))
        os.symlink(os.path.join(self.alpha, "Evidence", "memo.md"), os.path.join(self.beta, "Evidence", "other-client.md"))
        status, data = self.scan(self.ids["Beta Matter"])
        self.assertEqual(status, 200)
        self.assertEqual((data["added"], data["excluded"]), (0, 2))
        self.assertEqual(documents(self.beta), {})

    def test_cli_ingest_skips_a_file_linked_outside_the_case(self):
        secret = os.path.join(self.tmp, "outside.txt")
        with open(secret, "w") as f:
            f.write("outside the workspace\n")
        os.symlink(secret, os.path.join(self.beta, "Evidence", "linked.txt"))
        conn = store.connect(self.beta)
        try:
            added, _, excluded = quiet(ingest.ingest_paths, conn, self.beta, [self.beta])
        finally:
            conn.close()
        self.assertEqual((added, excluded), (0, 1))

    def test_cli_ingest_skips_a_link_into_a_subcase(self):
        child = os.path.join(self.alpha, "Subcases", "Related-Claim")
        with open(os.path.join(child, "Evidence", "child.txt"), "w") as f:
            f.write("the subcase's own file\n")
        link = os.path.join(self.alpha, "Evidence", "to-child.txt")
        os.symlink(os.path.join(child, "Evidence", "child.txt"), link)
        conn = store.connect(self.alpha)
        try:
            added, _, excluded = quiet(ingest.ingest_paths, conn, self.alpha, [link])
        finally:
            conn.close()
        self.assertEqual((added, excluded), (0, 1))

    def test_link_inside_the_case_is_still_fine(self):
        """A link to another file in the same case is not an escape; its bytes are a duplicate."""
        os.symlink(os.path.join(self.alpha, "Evidence", "memo.md"), os.path.join(self.alpha, "Research", "memo-link.md"))
        status, data = self.scan()
        self.assertEqual((status, data["excluded"]), (200, 0))


class TestParentSourcesById(Workspace):
    """F5: a parent document picked by its id in a subcase is re-hashed against the parent, as a
    `parent:` reference is, before a fact can cite it."""

    def setUp(self):
        super().setUp()
        self.child = os.path.join(self.alpha, "Subcases", "Related-Claim")
        self.order = os.path.join(self.alpha, "Evidence", "memo.md")
        facts_add_direct(self.child, "first fact", "DOCUMENTED FACT", ["parent:Evidence/memo.md"])
        conn = store.connect_readonly(self.child)
        self.ref_id = conn.execute("SELECT id FROM documents WHERE rel_path='parent:Evidence/memo.md'").fetchone()["id"]
        conn.close()
        self.cid = self.ids["Alpha v. City/Subcases/Related-Claim"]

    def add(self, sources):
        return call(self.app, "POST", f"/cases/{self.cid}/facts",
                    {"text": "via the picker", "status": "DOCUMENTED FACT", "sources": sources})

    def counts(self):
        conn = store.connect_readonly(self.child)
        try:
            return (conn.execute("SELECT COUNT(*) c FROM assertions").fetchone()["c"],
                    conn.execute("SELECT COUNT(*) c FROM assertion_sources").fetchone()["c"])
        finally:
            conn.close()

    def test_unchanged_parent_is_cited(self):
        status, data = self.add([{"document_id": self.ref_id, "locator": "p. 1"}])
        self.assertEqual(status, 200, data)
        self.assertEqual((data["fact"]["sources_attached"], data["fact"]["unsupported"]), (1, False))

    def test_changed_parent_is_refused_and_nothing_written(self):
        with open(self.order, "a") as f:
            f.write("changed after it was referenced\n")
        before = self.counts()
        status, data = self.add([{"document_id": self.ref_id}])
        self.assertEqual(status, 400, data)
        self.assertIn("changed", data["error"])
        self.assertEqual(self.counts(), before)

    def test_missing_parent_is_refused(self):
        os.remove(self.order)
        before = self.counts()
        status, _ = self.add([{"document_id": self.ref_id}])
        self.assertEqual(status, 400)
        self.assertEqual(self.counts(), before)

    def test_cli_id_reference_is_rechecked_too(self):
        with open(self.order, "a") as f:
            f.write("changed after it was referenced\n")
        with self.assertRaises(subcase.SubcaseError) as cm:
            facts_add_direct(self.child, "by id on the CLI", "DOCUMENTED FACT", [self.ref_id])
        self.assertEqual(cm.exception.kind, "CHANGED")

    def test_typed_source_resolves_the_exact_id(self):
        """A file whose name is another document's id cannot capture a typed id source."""
        decoy = os.path.join(self.child, self.ref_id)  # rel_path == the id
        with open(decoy, "w") as f:
            f.write("decoy named like an id\n")
        conn = store.connect(self.child)
        try:
            quiet(ingest.ingest_paths, conn, self.child, [decoy])
            r = facts.add(conn, self.child, "typed", "DOCUMENTED FACT", [{"document_id": self.ref_id, "locator": "p. 2"}])
            got = conn.execute("SELECT document_id, locator FROM assertion_sources WHERE assertion_id=?", (r["id"],)).fetchall()
        finally:
            conn.close()
        self.assertEqual([(g["document_id"], g["locator"]) for g in got], [(self.ref_id, "p. 2")])

    def test_at_sign_filename_through_the_picker(self):
        p = os.path.join(self.alpha, "Evidence", "email@example.txt")
        with open(p, "w") as f:
            f.write("synthetic email\n")
        conn = store.connect(self.alpha)
        quiet(ingest.ingest_paths, conn, self.alpha, [p])
        did = conn.execute("SELECT id FROM documents WHERE rel_path='Evidence/email@example.txt'").fetchone()["id"]
        conn.close()
        status, data = call(self.app, "POST", f"/cases/{self.a}/facts",
                            {"text": "the email says so", "status": "DOCUMENTED FACT",
                             "sources": [{"document_id": did, "locator": "line 2"}]})
        self.assertEqual(status, 200, data)
        _, listed = call(self.app, "GET", f"/cases/{self.a}/facts")
        mine = [f for f in listed["facts"] if f["text"] == "the email says so"][0]
        self.assertEqual(mine["sources"], [{"rel_path": "Evidence/email@example.txt", "locator": "line 2"}])


if __name__ == "__main__":
    unittest.main()

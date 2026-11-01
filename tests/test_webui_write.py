"""Local UI write API: new case, upload, scan, add a fact. Uploads never replace a file, never
land outside a case folder, and a write to one client's case never touches another's.

Design: docs/superpowers/specs/2026-10-31-local-frontend-design.md §4, §6 task 4. Synthetic data only.
Run: python3 -m unittest discover -s tests -v
"""
import os
import subprocess
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webui_helpers import KIT, OPERATOR, ServerCase, store, tree_snapshot, webui  # noqa: E402


def doc_id(case_dir, rel_path):
    conn = store.connect_readonly(case_dir)
    try:
        return conn.execute("SELECT id FROM documents WHERE rel_path=?", (rel_path,)).fetchone()["id"]
    finally:
        conn.close()


class TestNewCase(ServerCase):
    def test_create(self):
        status, _, data = self.api("POST", "/cases", {"name": "Gamma v. County"})
        self.assertEqual(status, 200)
        self.assertEqual(data["case"]["rel"], "Gamma v. County")
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "Gamma v. County", ".matter", "matter.db")))
        self.assertTrue(os.path.isdir(os.path.join(self.ws, "Gamma v. County", "Evidence")))
        _, _, listed = self.api("GET", "/cases")
        self.assertIn("Gamma v. County", [c["rel"] for c in listed["cases"]])

    def test_bad_names_refused_and_nothing_written(self):
        before = tree_snapshot(self.ws)
        for name in ("", "../escape", "a/b", ".hidden", "trailing.", "x" * 81, "Alpha v. City", None, 7):
            with self.subTest(name=name):
                status, _, _ = self.api("POST", "/cases", {"name": name})
                self.assertEqual(status, 400)
        self.assertEqual(tree_snapshot(self.ws), before)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape")))


class TestUpload(ServerCase):
    max_upload = 4096

    def put(self, folder, name, data, case="Alpha v. City"):
        from urllib.parse import quote
        return self.api("PUT", self.case_path(case, f"/files?folder={quote(folder)}&name={quote(name)}"), raw=data)

    def test_upload_registers_the_file(self):
        status, _, data = self.put("Evidence", "photo log.txt", b"synthetic bytes\n")
        self.assertEqual(status, 200)
        self.assertEqual(data["rel_path"], "Evidence/photo log.txt")
        with open(os.path.join(self.alpha, "Evidence", "photo log.txt"), "rb") as f:
            self.assertEqual(f.read(), b"synthetic bytes\n")
        _, _, docs = self.api("GET", self.case_path("Alpha v. City", "/documents"))
        self.assertIn("Evidence/photo log.txt", [d["rel_path"] for d in docs["documents"]])

    def test_existing_name_is_never_replaced(self):
        before = tree_snapshot(self.ws)
        status, _, data = self.put("Evidence", "memo.md", b"different bytes\n")
        self.assertEqual(status, 409)
        self.assertIn("never replaces", data["error"])
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_refused_shapes_leave_the_tree_unchanged(self):
        before = tree_snapshot(self.ws)
        shapes = [("Nowhere", "x.txt"), (".matter", "x.txt"), ("../Beta Matter", "x.txt"), ("", "x.txt"),
                  ("Evidence", "../x.txt"), ("Evidence", "sub/x.txt"), ("Evidence", ".env"), ("Evidence", ""),
                  ("Evidence", "a\\b.txt"), ("Evidence", "bad\x00name.txt"), ("Evidence", "x" * 201)]
        for folder, name in shapes:
            with self.subTest(folder=folder, name=name):
                status, _, _ = self.put(folder, name, b"x")
                self.assertEqual(status, 400)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_oversize_and_empty(self):
        before = tree_snapshot(self.ws)
        self.assertEqual(self.put("Evidence", "big.bin", b"x" * 4097)[0], 413)
        self.assertEqual(self.put("Evidence", "empty.txt", b"")[0], 400)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_same_bytes_already_in_the_case(self):
        with open(os.path.join(self.alpha, "Evidence", "memo.md"), "rb") as f:
            same = f.read()
        before = tree_snapshot(self.ws)
        status, _, data = self.put("Filings", "copy-of-memo.md", same)
        self.assertEqual(status, 409)
        self.assertIn("Evidence/memo.md", data["error"])
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_symlinked_folder_is_not_written_through(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        media = os.path.join(self.beta, "Media")
        os.rmdir(media)
        os.symlink(outside, media)
        status, _, _ = self.put("Media", "x.txt", b"x", case="Beta Matter")
        self.assertEqual(status, 400)
        self.assertEqual(os.listdir(outside), [])

    def test_link_planted_at_the_name_is_not_followed(self):
        target = os.path.join(self.tmp, "target.txt")
        with open(target, "w") as f:
            f.write("original\n")
        os.symlink(target, os.path.join(self.alpha, "Evidence", "planted.txt"))
        status, _, _ = self.put("Evidence", "planted.txt", b"overwrite?\n")
        self.assertEqual(status, 409)
        with open(target) as f:
            self.assertEqual(f.read(), "original\n")


class TestScan(ServerCase):
    def test_scan_registers_files_copied_in_by_hand(self):
        with open(os.path.join(self.alpha, "Correspondence", "letter.txt"), "w") as f:
            f.write("synthetic letter\n")
        status, _, data = self.api("POST", self.case_path("Alpha v. City", "/scan"), {})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(data["added"], 1)
        _, _, docs = self.api("GET", self.case_path("Alpha v. City", "/documents"))
        self.assertIn("Correspondence/letter.txt", [d["rel_path"] for d in docs["documents"]])


class TestFactAdd(ServerCase):
    def test_add_with_a_picked_source(self):
        did = doc_id(self.alpha, "Evidence/memo.md")
        status, _, data = self.api("POST", self.case_path("Alpha v. City", "/facts"),
                                   {"text": "The officer arrived at 9:14 PM", "status": "documented-fact",
                                    "sources": [{"document_id": did, "locator": "para 1"}]})
        self.assertEqual(status, 200)
        self.assertEqual(data["fact"]["status"], "DOCUMENTED FACT")
        self.assertFalse(data["fact"]["unsupported"])
        _, _, listed = self.api("GET", self.case_path("Alpha v. City", "/facts"))
        self.assertEqual(listed["facts"][0]["sources"], [{"rel_path": "Evidence/memo.md", "locator": "para 1"}])

    def test_add_without_a_source_is_unsupported(self):
        status, _, data = self.api("POST", self.case_path("Alpha v. City", "/facts"),
                                   {"text": "Witness may have seen it", "status": "HYPOTHESIS"})
        self.assertEqual(status, 200)
        self.assertTrue(data["fact"]["unsupported"])

    def test_refusals_write_nothing(self):
        did_beta_side = None
        with open(os.path.join(self.beta, "Evidence", "b.txt"), "w") as f:
            f.write("beta-only bytes\n")
        self.api("POST", self.case_path("Beta Matter", "/scan"), {})
        did_beta_side = doc_id(self.beta, "Evidence/b.txt")
        before = self.fact_count(self.alpha)
        bad = [{"text": "x", "status": "TRUE"}, {"text": "   ", "status": "UNKNOWN"}, {"status": "UNKNOWN"},
               {"text": "x", "status": "UNKNOWN", "sources": [{"document_id": "nope"}]},
               {"text": "x", "status": "UNKNOWN", "sources": [{"document_id": did_beta_side}]},
               {"text": "x", "status": "UNKNOWN", "sources": ["Evidence/memo.md"]},
               {"text": "x", "status": "UNKNOWN", "sources": [{"document_id": doc_id(self.alpha, "Evidence/memo.md"),
                                                               "locator": "p1\nINJECT"}]},
               {"text": "x", "status": "UNKNOWN", "sources": "Evidence/memo.md"}]
        for body in bad:
            with self.subTest(body=body):
                status, _, _ = self.api("POST", self.case_path("Alpha v. City", "/facts"), body)
                self.assertEqual(status, 400)
        self.assertEqual(self.fact_count(self.alpha), before)

    def test_json_content_type_required(self):
        status, _, _ = self.api("POST", self.case_path("Alpha v. City", "/facts"),
                                raw=b'{"text":"x","status":"UNKNOWN"}', headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)


class TestClientIsolation(ServerCase):
    def test_writes_to_one_case_leave_the_other_byte_identical(self):
        beta_before = tree_snapshot(self.beta)
        did = doc_id(self.alpha, "Evidence/memo.md")
        self.api("POST", self.case_path("Alpha v. City", "/facts"),
                 {"text": "x", "status": "UNKNOWN", "sources": [{"document_id": did}]})
        self.api("PUT", self.case_path("Alpha v. City", "/files?folder=Evidence&name=new.txt"), raw=b"new\n")
        self.api("POST", self.case_path("Alpha v. City", "/scan"), {})
        self.assertEqual(tree_snapshot(self.beta), beta_before)


class TestConcurrentWriters(ServerCase):
    def test_ui_and_cli_writes_all_land(self):
        n = 8
        errors = []

        def ui(i):
            status, _, data = self.api("POST", self.case_path("Alpha v. City", "/facts"),
                                       {"text": f"ui fact {i}", "status": "UNKNOWN"})
            if status != 200:
                errors.append(data)

        env = {k: v for k, v in os.environ.items() if k not in ("MATTER_AUTHOR", "MATTER_DIRECTED_BY")}

        def cli(i):
            r = subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "fact", "add", self.alpha,
                                "--text", f"cli fact {i}", "--status", "unknown"],
                               capture_output=True, text=True, env=env)
            if r.returncode:
                errors.append(r.stderr)

        threads = [threading.Thread(target=ui, args=(i,)) for i in range(n)] + \
                  [threading.Thread(target=cli, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(self.fact_count(self.alpha), 2 * n)
        _, _, data = self.api("GET", self.case_path("Alpha v. City", "/facts"))
        authors = {f["created_by"] for f in data["facts"]}
        self.assertEqual(authors, {f"human:{OPERATOR}", "human"})


if __name__ == "__main__":
    unittest.main()

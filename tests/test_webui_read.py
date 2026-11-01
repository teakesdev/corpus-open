"""Local UI read API: cases, status, documents, verify, deadlines, facts, drafts, brief check, packet.

Design: docs/superpowers/specs/2026-10-31-local-frontend-design.md §2, §6 task 3. Synthetic data only.
Run: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webui_helpers import ServerCase, facts_add_direct, store, tree_snapshot  # noqa: E402


class TestSessionAndCases(ServerCase):
    def test_session(self):
        status, _, data = self.api("GET", "/session")
        self.assertEqual(status, 200)
        self.assertEqual(data["workspace"], "workspace")
        self.assertIn("Evidence", data["folders"])
        self.assertIn("DOCUMENTED FACT", data["labels"])
        self.assertEqual(data["layers"], ["30s", "3min", "15min"])

    def test_cases_lists_clients_and_nests_subcases(self):
        _, _, data = self.api("GET", "/cases")
        by_rel = {c["rel"]: c for c in data["cases"]}
        self.assertEqual(set(by_rel), {"Alpha v. City", "Alpha v. City/Subcases/Related-Claim", "Beta Matter"})
        self.assertEqual(by_rel["Alpha v. City/Subcases/Related-Claim"]["parent_id"], by_rel["Alpha v. City"]["id"])
        self.assertEqual(by_rel["Alpha v. City/Subcases/Related-Claim"]["title"], "Related state claim")
        self.assertIsNone(by_rel["Beta Matter"]["parent_id"])
        self.assertEqual(set(by_rel["Beta Matter"]), {"id", "rel", "title", "parent_id"})


class TestStatus(ServerCase):
    def test_status_counts_and_overdue(self):
        status, _, data = self.api("GET", self.case_path("Alpha v. City", "/status"))
        self.assertEqual(status, 200)
        s = data["status"]
        self.assertEqual(s["documents_total"], 1)
        self.assertEqual(s["open_deadlines"], 2)
        self.assertEqual(s["overdue_deadlines"], 1)
        self.assertNotIn("matter_dir", s)
        self.assertEqual(data["case"]["title"], "Alpha v. City")

    def test_reads_do_not_write(self):
        before = tree_snapshot(self.ws)
        for sub in ("/status", "/documents", "/deadlines", "/facts", "/drafts", "/packet"):
            with self.subTest(sub=sub):
                self.assertEqual(self.api("GET", self.case_path("Alpha v. City", sub))[0], 200)
        self.assertEqual(self.api("POST", self.case_path("Alpha v. City", "/verify"), {})[0], 200)
        self.assertEqual(tree_snapshot(self.ws), before)


class TestDocumentsAndVerify(ServerCase):
    def test_documents(self):
        _, _, data = self.api("GET", self.case_path("Alpha v. City", "/documents"))
        self.assertEqual([d["rel_path"] for d in data["documents"]], ["Evidence/memo.md"])
        self.assertEqual(len(data["documents"][0]["sha256"]), 64)

    def test_verify_reports_a_changed_file(self):
        _, _, data = self.api("POST", self.case_path("Alpha v. City", "/verify"), {})
        self.assertEqual((data["ok"], data["changed"], data["missing"]), (1, 0, 0))
        with open(os.path.join(self.alpha, "Evidence", "memo.md"), "a") as f:
            f.write("edited\n")
        _, _, data = self.api("POST", self.case_path("Alpha v. City", "/verify"), {})
        self.assertEqual(data["changed"], 1)
        self.assertEqual(data["failures"], [{"kind": "CHANGED", "rel_path": "Evidence/memo.md"}])


class TestDeadlines(ServerCase):
    def test_open_deadlines_with_overdue_flag(self):
        _, _, data = self.api("GET", self.case_path("Alpha v. City", "/deadlines"))
        rows = {d["label"]: d for d in data["deadlines"]}
        self.assertTrue(rows["Reply brief"]["overdue"])
        self.assertFalse(rows["Status report"]["overdue"])
        self.assertEqual(rows["Reply brief"]["verification"], "unverified")
        self.assertIsNone(rows["Reply brief"]["owner"])

    def test_owner_is_returned(self):
        conn = store.connect(self.alpha)
        conn.execute("UPDATE deadlines SET owner='theirs' WHERE label='Reply brief'")
        conn.commit()
        conn.close()
        _, _, data = self.api("GET", self.case_path("Alpha v. City", "/deadlines"))
        self.assertEqual({d["label"]: d["owner"] for d in data["deadlines"]}["Reply brief"], "theirs")

    def test_all_includes_closed(self):
        conn = store.connect(self.alpha)
        conn.execute("UPDATE deadlines SET state='met' WHERE label='Reply brief'")
        conn.commit()
        conn.close()
        _, _, open_only = self.api("GET", self.case_path("Alpha v. City", "/deadlines"))
        _, _, everything = self.api("GET", self.case_path("Alpha v. City", "/deadlines?all=1"))
        self.assertEqual(len(open_only["deadlines"]), 1)
        self.assertEqual(len(everything["deadlines"]), 2)
        met = [d for d in everything["deadlines"] if d["state"] == "met"][0]
        self.assertFalse(met["overdue"])  # closed deadlines are never overdue


class TestFacts(ServerCase):
    def test_unsupported_fact_is_marked(self):
        facts_add_direct(self.alpha, "No source for this", "ALLEGATION", [])
        facts_add_direct(self.alpha, "The officer arrived at 9:14 PM", "DOCUMENTED FACT", ["Evidence/memo.md@para 1"])
        _, _, data = self.api("GET", self.case_path("Alpha v. City", "/facts"))
        rows = {f["text"]: f for f in data["facts"]}
        self.assertTrue(rows["No source for this"]["unsupported"])
        self.assertEqual(rows["No source for this"]["sources"], [])
        self.assertFalse(rows["The officer arrived at 9:14 PM"]["unsupported"])
        self.assertEqual(rows["The officer arrived at 9:14 PM"]["sources"],
                         [{"rel_path": "Evidence/memo.md", "locator": "para 1"}])

    def test_hostile_text_comes_back_as_data(self):
        evil = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
        facts_add_direct(self.alpha, evil, "UNKNOWN", [])
        status, headers, data = self.api("GET", self.case_path("Alpha v. City", "/facts"))
        self.assertTrue(headers["content-type"].startswith("application/json"))
        self.assertEqual(data["facts"][0]["text"], evil)  # the page sets it with textContent


class TestDraftsAndBriefCheck(ServerCase):
    def test_drafts_marks_word_files_unsupported(self):
        _, _, data = self.api("GET", self.case_path("Alpha v. City", "/drafts"))
        rows = {d["path"]: d["supported"] for d in data["drafts"]}
        self.assertTrue(rows["Filings/drafts/response_v1.md"])
        self.assertFalse(rows["Filings/drafts/response_v1.docx"])
        self.assertTrue(rows["Evidence/memo.md"])
        self.assertNotIn(".matter", str(rows))

    def test_drafts_stop_at_a_subcase(self):
        with open(os.path.join(self.alpha, "Subcases", "Related-Claim", "Filings", "sub.md"), "w") as f:
            f.write("x\n")
        _, _, data = self.api("GET", self.case_path("Alpha v. City", "/drafts"))
        self.assertNotIn("Subcases/Related-Claim/Filings/sub.md", [d["path"] for d in data["drafts"]])

    def test_brief_check_runs(self):
        status, _, data = self.api("POST", self.case_path("Alpha v. City", "/brief-check"),
                                   {"path": "Filings/drafts/response_v1.md"})
        self.assertEqual(status, 200)
        self.assertIn(data["exit_code"], (0, 1, 2))
        self.assertIn("cited or tagged sentences", data["summary"])
        self.assertTrue(data["report"])

    def test_brief_check_word_file_explains(self):
        status, _, data = self.api("POST", self.case_path("Alpha v. City", "/brief-check"),
                                   {"path": "Filings/drafts/response_v1.docx"})
        self.assertEqual(status, 400)
        self.assertIn("Plain Text", data["error"])

    def test_brief_check_refuses_paths_outside_the_list(self):
        for path in ("../Beta Matter/x.md", "/etc/hosts", ".matter/matter.db", "Evidence/../../x.md", None, 3):
            with self.subTest(path=path):
                status, _, _ = self.api("POST", self.case_path("Alpha v. City", "/brief-check"), {"path": path})
                self.assertEqual(status, 400)

    def test_brief_check_bad_mode(self):
        status, _, _ = self.api("POST", self.case_path("Alpha v. City", "/brief-check"),
                                {"path": "Filings/drafts/response_v1.md", "mode": "motion"})
        self.assertEqual(status, 400)


class TestPacket(ServerCase):
    def test_layers(self):
        for layer in ("30s", "3min", "15min"):
            with self.subTest(layer=layer):
                status, _, data = self.api("GET", self.case_path("Alpha v. City", f"/packet?layer={layer}"))
                self.assertEqual(status, 200)
                self.assertIn(f"Counsel packet ({layer} view)", data["text"])

    def test_bad_layer(self):
        status, _, _ = self.api("GET", self.case_path("Alpha v. City", "/packet?layer=1hr"))
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()

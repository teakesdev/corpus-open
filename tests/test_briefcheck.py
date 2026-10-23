"""brief check: every record cite, pleading cite, authority, quotation and factual
sentence in a draft is tied to a source in the store or visibly flagged.

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import briefcheck, ingest, store  # noqa: E402

MOTION = ("Defendants move to dismiss.\f"
          "The Complaint does not allege that the Chief participated in any way.\f"
          "Plaintiffs were \"never denied bail\" according to Defendants.")
ORDER = "ORDER granting the motion for an extension. Responses due 11/2/2026."
COMPLAINT_TEXT = ("1. Plaintiff Pat Example lives in Exampleton.\n"
                  "2. On January 1, Officer Doe arrested Plaintiff without a warrant.\n"
                  "3. The clerk said \"the file is closed\" and refused the request.\n")


class Matter:
    """A synthetic matter with a small docket, authorities and assertions."""

    def __init__(self, root):
        self.dir = os.path.join(root, "case")
        os.makedirs(os.path.join(self.dir, "Filings", "PACER"))
        self.write("Filings/PACER/case-16-MotionDismiss.txt", MOTION)
        self.write("Filings/PACER/case-27-Order.txt", ORDER)
        # a PDF whose text layer the kit cannot read, plus a named text copy
        self.write_bytes("Filings/PACER/case-19-Memo.pdf", b"%PDF-1.7\n%synthetic, no text layer\n")
        self.write("Filings/plaintext/complaint_final_text.md", COMPLAINT_TEXT)
        self.write_bytes("Filings/PACER/case-15-Answer.pdf", b"%PDF-1.7\n%answer\n")
        self.write("Evidence/clerk_call_transcript.md", "Clerk: the file is closed, sir.")
        conn = store.connect(self.dir)
        ingest.ingest_paths(conn, self.dir, [self.dir])
        self.conn = conn
        self.docket({
            "entries": {"16": "Filings/PACER/case-16-MotionDismiss.txt",
                        "19": "Filings/PACER/case-19-Memo.pdf",
                        "27": "Filings/PACER/case-27-Order.txt"},
            "pleadings": {"Compl.": "Filings/plaintext/complaint_final_text.md"},
        })
        self.authority("Dietz v. Bouldin, 579 U.S. 40 (2016)", "source-checked",
                       via="https://example.org/dietz.pdf",
                       evidence=json.dumps({"pins": ["45-47"], "source_url": "https://example.org/dietz.pdf"}))
        self.authority("Bell Atl. Corp. v. Twombly, 550 U.S. 544 (2007)", "unverified")
        self.authority("Ashcroft v. Iqbal, 556 U.S. 662 (2009)", "resolved", via="https://example.org/iqbal")
        self.authority("Fed. R. Civ. P. 15", "source-checked", via="https://example.org/frcp",
                       evidence="opened official text of Rule 15")
        self.authority("L.U.Civ.R. 7", "source-checked", via="https://example.org/lucivr",
                       evidence="opened local rules PDF")
        self.authority("42 U.S.C. § 1983", "source-checked", via="https://example.org/1983",
                       evidence="opened official text")
        doc = conn.execute("SELECT id FROM documents WHERE rel_path=?",
                           ("Evidence/clerk_call_transcript.md",)).fetchone()["id"]
        self.fact_ok = self.assertion("The clerk said the file was closed.", "DOCUMENTED FACT", doc)
        self.fact_alleg = self.assertion("Officer Doe acted out of spite.", "ALLEGATION", None)
        conn.commit()

    def write(self, rel, text):
        self.write_bytes(rel, text.encode("utf-8"))

    def write_bytes(self, rel, data):
        p = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)

    def docket(self, d):
        with open(os.path.join(self.dir, ".matter", "docket.json"), "w") as f:
            json.dump(d, f)

    def authority(self, cite, status, via=None, evidence=None):
        self.conn.execute("INSERT INTO authorities (id,citation,status,checked_via,check_evidence)"
                          " VALUES (?,?,?,?,?)", (store.new_id("auth_"), cite, status, via, evidence))

    def assertion(self, text, status, doc_id):
        aid = store.new_id("a_")
        self.conn.execute("INSERT INTO assertions (id,text,status,created_by,created_at) VALUES (?,?,?,?,?)",
                          (aid, text, status, "human", store.now_iso()))
        if doc_id:
            self.conn.execute("INSERT INTO assertion_sources VALUES (?,?,?)", (aid, doc_id, "00:01"))
        return aid

    def check(self, draft, **kw):
        path = os.path.join(os.path.dirname(self.dir), "draft.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(draft)
        kw.setdefault("use_pdftotext", False)
        return briefcheck.check(self.dir, path, **kw)


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def codes(report, severity):
    return [f.code for f in report.findings if f.severity == severity]


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.m = Matter(self._tmp.name)

    def tearDown(self):
        self.m.conn.close()
        self._tmp.cleanup()


class TestRecordCites(Base):
    def test_cite_to_docket_entry_not_in_store_blocks(self):
        r = self.m.check("The complaint was filed in July [Doc. 1].")
        self.assertIn("record-not-in-store", codes(r, "BLOCK"))
        self.assertEqual(r.exit_code, 1)

    def test_multi_cite_resolves_each_entry(self):
        r = self.m.check("Extension granted [Docs. 16 and 27].")
        self.assertEqual(codes(r, "BLOCK"), [])
        resolved = {c.target for row in r.rows for c in row.cites if c.kind == "record"}
        self.assertEqual(resolved, {"Filings/PACER/case-16-MotionDismiss.txt",
                                    "Filings/PACER/case-27-Order.txt"})

    def test_pin_beyond_page_count_blocks(self):
        r = self.m.check("Defendants say so. Doc. 16 at 9.")
        self.assertIn("pin-out-of-range", codes(r, "BLOCK"))

    def test_pin_within_page_count_ok(self):
        r = self.m.check("Defendants say so. Dkt. 16 at 2.")
        self.assertEqual(codes(r, "BLOCK"), [])

    def test_filename_candidate_without_docket_entry_needs_confirmation(self):
        # case-15-Answer.pdf is registered but not in docket.json
        r = self.m.check("The County answered [ECF No. 15].")
        self.assertIn("record-unconfirmed", codes(r, "UNCHECKED"))
        self.assertNotIn("record-not-in-store", codes(r, "BLOCK"))

    def test_changed_source_blocks(self):
        self.m.write("Filings/PACER/case-27-Order.txt", ORDER + " (edited)")
        r = self.m.check("The Court granted the extension [Doc. 27].")
        self.assertIn("source-changed", codes(r, "BLOCK"))

    def test_docket_override_file(self):
        alt = os.path.join(os.path.dirname(self.m.dir), "alt-docket.json")
        with open(alt, "w") as f:
            json.dump({"entries": {"1": "Filings/PACER/case-27-Order.txt"}}, f)
        r = self.m.check("The complaint [Doc. 1].", docket_path=alt)
        self.assertNotIn("record-not-in-store", codes(r, "BLOCK"))
        self.assertIn("alt-docket.json", r.render())

    def test_docket_gaps_listed(self):
        r = self.m.check("Nothing cited here.")
        self.assertIn("1–14", " ".join(r.docket_gaps))


class TestQuotes(Base):
    def test_quote_found_on_pinned_page(self):
        r = self.m.check('Defendants argue that the Complaint "does not allege that the Chief '
                         'participated in any way." Doc. 16 at 2.')
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual(r.rows[0].quotes[0].status, "found")

    def test_quote_not_in_source_blocks(self):
        r = self.m.check('Defendants concede that "the arrest was unlawful." Doc. 16 at 2.')
        self.assertIn("quote-not-found", codes(r, "BLOCK"))

    def test_quote_on_other_page_warns(self):
        r = self.m.check('Defendants say Plaintiffs were "never denied bail." Doc. 16 at 2.')
        self.assertIn("quote-wrong-page", codes(r, "WARN"))

    def test_quote_in_unreadable_pdf_is_unchecked_not_passed(self):
        r = self.m.check('The memorandum says "something important." Doc. 19 at 1.')
        self.assertIn("quote-unchecked", codes(r, "UNCHECKED"))
        self.assertEqual(r.exit_code, 2)

    def test_named_text_copy_is_used_and_reported(self):
        self.m.write("Filings/plaintext/memo19.md", "The memorandum says something important.")
        ingest.ingest_paths(self.m.conn, self.m.dir, [os.path.join(self.m.dir, "Filings/plaintext/memo19.md")])
        self.m.docket({"entries": {"19": "Filings/PACER/case-19-Memo.pdf"},
                       "text_copies": {"Filings/PACER/case-19-Memo.pdf": "Filings/plaintext/memo19.md"}})
        r = self.m.check('The memorandum says "something important." Doc. 19.')
        q = r.rows[0].quotes[0]
        self.assertEqual(q.status, "found")
        self.assertIn("Filings/plaintext/memo19.md", q.checked_against)

    def test_curly_quotes_and_ellipsis(self):
        r = self.m.check("The Complaint “does not allege … participated in any way.” Doc. 16 at 2.")
        self.assertEqual(r.rows[0].quotes[0].status, "found")


class TestPleadingCites(Base):
    def test_paragraph_present_ok_and_quote_checked_within_it(self):
        r = self.m.check('The clerk said "the file is closed." Compl. ¶ 3.')
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual(r.rows[0].quotes[0].status, "found")

    def test_missing_paragraph_blocks(self):
        r = self.m.check("Plaintiff was held for days. Compl. ¶¶ 3–5.")
        self.assertIn("paragraph-not-found", codes(r, "BLOCK"))


class TestAuthorities(Base):
    def test_source_checked_case_ok(self):
        r = self.m.check("Courts manage their dockets. Dietz v. Bouldin, 579 U.S. 40, 45-47 (2016).")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual(r.authorities[0].store_status, "source-checked")

    def test_unlogged_case_blocks(self):
        r = self.m.check("Pro se pleadings are construed liberally. Erickson v. Pardus, 551 U.S. 89, 94 (2007).")
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))

    def test_unverified_case_blocks_and_resolved_needs_check(self):
        r = self.m.check("Plausibility is required. Twombly, 550 U.S. 544, 555 (2007); "
                         "Ashcroft v. Iqbal, 556 U.S. 662, 678 (2009).")
        self.assertIn("authority-unverified", codes(r, "BLOCK"))
        self.assertIn("authority-not-source-checked", codes(r, "UNCHECKED"))

    def test_id_binds_to_preceding_authority(self):
        r = self.m.check("First point. Twombly, 550 U.S. 544, 555 (2007). Second point. "
                         "Dietz v. Bouldin, 579 U.S. 40, 45 (2016). Third point. Id. at 46.")
        last = r.rows[-1].cites[0]
        self.assertEqual(last.kind, "authority")
        self.assertIn("579 U.S. 40", last.display)

    def test_id_after_record_cite_binds_to_record(self):
        r = self.m.check("Defendants say so. Doc. 16 at 1. They repeat it. Id. at 2.")
        last = r.rows[-1].cites[0]
        self.assertEqual((last.kind, last.target), ("record", "Filings/PACER/case-16-MotionDismiss.txt"))

    def test_short_form_binds_to_full_cite(self):
        r = self.m.check("Full cite first. Dietz v. Bouldin, 579 U.S. 40, 45 (2016). Later. Dietz, 579 U.S. at 46.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual(len(r.authorities), 1)

    def test_rules_and_local_rules_match(self):
        r = self.m.check("Leave is freely given. Fed. R. Civ. P. 15(a)(2). Responses are due in 14 days. "
                         "L.U.Civ.R. 7(b)(4). Bare rule too. Rule 15(a)(1).")
        self.assertEqual(codes(r, "BLOCK"), [])

    def test_rule_list_continuation_checks_each_rule(self):
        r = self.m.check("Exhibits are required. L.U.Civ.R. 7(b)(2) and 15.")
        unlogged = [f.cite for f in r.findings if f.code == "authority-not-logged"]
        self.assertEqual(unlogged, ["L.U.Civ.R. 15"])  # 7 is logged; 15 is not

    def test_statute_matches_and_short_section_binds(self):
        r = self.m.check("Claims arise under 42 U.S.C. § 1983. A § 1983 claim needs a person.")
        self.assertEqual(codes(r, "BLOCK"), [])

    def test_pin_not_covered_by_recorded_check_warns(self):
        r = self.m.check("Courts manage dockets. Dietz v. Bouldin, 579 U.S. 40, 52 (2016).")
        self.assertIn("pin-not-in-check", codes(r, "WARN"))


class TestFacts(Base):
    def test_uncited_sentence_in_fact_section_flagged(self):
        draft = ("STATEMENT OF FACTS\n\nOn January 1, Plaintiff was arrested at home by two officers.\n\n"
                 "ARGUMENT\n\nThe motion should be denied because the claims are plausible.\n")
        r = self.m.check(draft)
        uncited = [f for f in r.findings if f.code == "uncited-fact"]
        self.assertEqual(len(uncited), 1)
        self.assertIn("arrested at home", uncited[0].cite)

    def test_fact_tag_on_allegation_flagged_and_documented_fact_ok(self):
        draft = (f"The clerk said the file was closed. <!-- fact:{self.m.fact_ok} -->\n\n"
                 f"Officer Doe acted out of spite. {{fact:{self.m.fact_alleg}}}\n")
        r = self.m.check(draft)
        self.assertIn("fact-not-documented", codes(r, "WARN"))
        self.assertIn("fact-unsupported", codes(r, "WARN"))
        self.assertEqual([f for f in r.findings if f.code == "fact-not-documented"][0].line, 3)

    def test_unknown_fact_tag_blocks(self):
        r = self.m.check("Something happened. {fact:a_doesnotexist}")
        self.assertIn("fact-unknown", codes(r, "BLOCK"))


class TestSafety(Base):
    def test_store_is_not_written(self):
        db = store.db_path(self.m.dir)
        self.m.conn.close()
        before = _sha(db)
        self.m.check('Many cites. Doc. 1. Doc. 16 at 2. Dietz v. Bouldin, 579 U.S. 40, 45 (2016). "x y z"')
        self.assertEqual(_sha(db), before)
        self.m.conn = store.connect(self.m.dir)

    def test_report_states_limits_and_inputs(self):
        r = self.m.check("Courts manage their dockets. Dietz v. Bouldin, 579 U.S. 40, 45 (2016).")
        text = r.render()
        self.assertIn("does not mean", text)
        self.assertIn(_sha(r.draft_path), text)


class TestSuggest(unittest.TestCase):
    def test_filename_shapes(self):
        rows = ["Filings/PACER/2026-10-07_Dkt28/Dkt28_2026-10-07_Order.pdf", "Filings/PACER/27.pdf",
                "Filings/PACER/23-1.pdf", "Filings/PACER/Case-vs-city-16-Motion.pdf",
                "Filings/PACER/3-26-cv-00001-ABC-DEF.zip", "Filings/PACER/PROVENANCE.md",
                "Research/2026-09-18_notes.md"]

        class Conn:
            def execute(self, *_):
                return [{"rel_path": r} for r in rows]
        got = briefcheck.suggest_docket(Conn())
        self.assertEqual(got["entries"], {"16": rows[3], "23-1": rows[2], "27": rows[1], "28": rows[0]})
        self.assertNotIn("ambiguous", got)


class TestMcp(Base):
    def test_brief_check_tool_is_read_only_and_reports_exit(self):
        draft = os.path.join(self.m.dir, "Filings", "draft.md")
        with open(draft, "w") as f:
            f.write("The complaint was filed [Doc. 1].")
        self.m.conn.close()
        before = _sha(store.db_path(self.m.dir))
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "matter.brief_check", "arguments": {"path": "Filings/draft.md", "pdftotext": False}}}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input=json.dumps(req) + "\n",
                           capture_output=True, text=True, env=dict(os.environ, MATTER_DIR=self.m.dir), check=True)
        text = json.loads(p.stdout.strip().splitlines()[-1])["result"]["content"][0]["text"]
        self.assertTrue(text.startswith("exit=1"))
        self.assertIn("record-not-in-store", text)
        self.assertEqual(_sha(store.db_path(self.m.dir)), before)
        self.m.conn = store.connect(self.m.dir)


class TestCli(Base):
    def run_cli(self, *args):
        env = {k: v for k, v in os.environ.items()}
        env["PATH"] = ""  # no pdftotext: deterministic
        return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *args],
                              capture_output=True, text=True, env=env)

    def test_exit_codes_and_report_file(self):
        draft = os.path.join(self._tmp.name, "d.md")
        with open(draft, "w") as f:
            f.write("The complaint was filed [Doc. 1].")
        out = os.path.join(self._tmp.name, "report.md")
        p = self.run_cli("brief", "check", self.m.dir, draft, "--out", out)
        self.assertEqual(p.returncode, 1, p.stderr)
        with open(out, encoding="utf-8") as f:
            self.assertIn("BLOCKED", f.read())
        with open(draft, "w") as f:
            f.write("Courts manage their dockets. Dietz v. Bouldin, 579 U.S. 40, 45 (2016).")
        p = self.run_cli("brief", "check", self.m.dir, draft)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_docket_suggest_lists_filename_candidates(self):
        p = self.run_cli("docket", "suggest", self.m.dir)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["entries"]["15"], "Filings/PACER/case-15-Answer.pdf")


if __name__ == "__main__":
    unittest.main()


class TestSubcaseParentRefs(unittest.TestCase):
    """A subcase draft can cite and quote a parent document through a parent: reference."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.parent = os.path.join(self._tmp.name, "parent")
        env = dict(os.environ, PATH="")
        run = lambda *a: subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *a],  # noqa: E731
                                        capture_output=True, text=True, env=env)
        self.run_cli = run
        self.assertEqual(run("init", self.parent).returncode, 0)
        os.makedirs(os.path.join(self.parent, "Media"), exist_ok=True)
        with open(os.path.join(self.parent, "Media", "article.txt"), "w") as f:
            f.write("The sheriff's office named the man arrested on Tuesday.")
        run("ingest", self.parent)
        p = run("subcase", "init", self.parent, "Reporting-Claim", "--title", "Reporting claim")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.sub = os.path.join(self.parent, "Subcases", "Reporting-Claim")
        p = run("fact", "add", self.sub, "--text", "The article named him.", "--status", "documented-fact",
                "--source", "parent:Media/article.txt@para1")
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(self.sub, ".matter", "docket.json"), "w") as f:
            json.dump({"entries": {"5": "parent:Media/article.txt"}}, f)
        self.draft = os.path.join(self.sub, "Filings", "notice.md")
        os.makedirs(os.path.dirname(self.draft), exist_ok=True)

    def check(self, text):
        with open(self.draft, "w") as f:
            f.write(text)
        return briefcheck.check(self.sub, self.draft, use_pdftotext=False)

    def test_quote_found_in_parent_document(self):
        r = self.check('The station reported that the office "named the man arrested on Tuesday." Doc. 5.')
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual(r.rows[0].quotes[0].status, "found")

    def test_changed_parent_document_blocks(self):
        with open(os.path.join(self.parent, "Media", "article.txt"), "a") as f:
            f.write(" (edited)")
        r = self.check("The station reported it. Doc. 5.")
        self.assertIn("source-changed", codes(r, "BLOCK"))

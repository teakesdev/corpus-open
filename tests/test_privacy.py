"""Filing privacy check (Fed. R. Civ. P. 5.2): what is flagged, what is masked, what is never repeated.

Every number, name, date and address here is made up: Social Security numbers use the 9xx/987-65 style test
ranges or fail the issuing rules, card numbers are the networks' published test numbers, and phone numbers
use 555-01xx. Run: python3 -m unittest discover -s tests -v
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import draft_builders as db  # noqa: E402
from matterkit import cases, privacy as pv, rules  # noqa: E402
from test_rules_attach import make_pack  # noqa: E402

# Synthetic values that must never appear in a report.
SSN = "987-65-4329"
ITIN = "912-78-1234"
EIN = "12-3456789"
CARD = "4111 1111 1111 1111"  # Visa's published test number
ACCT = "0012 3456 7890"
CHILD = "Emily Sample"
DOB = "March 14, 1988"
PHONE = "(601) 555-0142"
STREET = "1234 Magnolia Street"
SECRETS = (SSN, ITIN, EIN, CARD, ACCT, CHILD, DOB, PHONE, STREET, "987654329", "4111111111111111", "Jacob",
           "Magnolia", "03/14/1988", "0142")

LEAKY = (f"Plaintiff Jane Q. Example, SSN {SSN}, was born on {DOB} (DOB: 03/14/1988).\n"
         f"Her ITIN is {ITIN} and the shop's EIN {EIN}. My daughter, {CHILD}, age 9, lives with me.\n"
         f"My son Jacob attends school. Account number {ACCT}. Card {CARD}.\n"
         f"Call me at {PHONE}. I live at {STREET}, Apt 5B, Jackson.\n")

CLEAN = ("The lease began on March 3, 2025, and rent of $1,250.00 was due on the first of each month.\n"
         "Docket No. 3:24-cv-00123. The routing number on the check is 123456789.\n"
         "Already redacted: SSN XXX-XX-4329, account ending in 7890, born in 1988, and the minor child E.S.\n"
         "See Smith v. Jones, 123 F.4th 456 (5th Cir. 2024) (minors may sue by a next friend).\n")


def kinds(text):
    return [(f.kind, f.masked) for f in pv.scan_text(text)]


class TestScanText(unittest.TestCase):
    def test_finds_each_restricted_kind_masked(self):
        got = kinds(LEAKY)
        for item in [(pv.SSN, "***-**-4329"), (pv.BIRTH, "[month and day hidden] 1988"), (pv.TIN, "***-**-1234"),
                     (pv.TIN, "**-***6789"), (pv.MINOR, "E. S."), (pv.MINOR, "J."), (pv.ACCOUNT, "**** **** 7890"),
                     (pv.CARD, "**** **** **** 1111"), (pv.PHONE, "(***) ***-**42")]:
            self.assertIn(item, got)
        self.assertEqual(sum(1 for k, _ in got if k == pv.BIRTH), 2)
        self.assertIn(pv.ADDRESS, [k for k, _ in got])

    def test_levels(self):
        levels = {f.kind: f.level for f in pv.scan_text(LEAKY)}
        for k in (pv.SSN, pv.TIN, pv.BIRTH, pv.MINOR, pv.ACCOUNT, pv.CARD):
            self.assertEqual(levels[k], pv.RESTRICTED, k)
        self.assertEqual(levels[pv.PHONE], pv.ADVISORY)
        self.assertEqual(levels[pv.ADDRESS], pv.ADVISORY)

    def test_masks_never_hold_the_value(self):
        for f in pv.scan_text(LEAKY):
            for secret in SECRETS:
                self.assertNotIn(secret, f.masked)

    def test_what_the_rule_allows_is_not_flagged(self):
        self.assertEqual(kinds(CLEAN), [])

    def test_nine_digits_need_a_word_that_says_what_they_are(self):
        self.assertEqual(kinds("Invoice 987654329 was paid."), [])
        self.assertEqual(kinds("SSN: 987654329"), [(pv.SSN, "*****4329")])
        self.assertEqual(kinds("taxpayer identification number 987654329"), [(pv.TIN, "*****4329")])

    def test_never_issued_ssn_groups_are_placeholders(self):
        self.assertEqual(kinds("SSN 000-12-3456 or 123-00-4567 or 123-45-0000"), [])

    def test_a_date_is_a_birth_date_only_near_birth_words(self):
        self.assertEqual(kinds("The hearing is set for 04/15/2026."), [])
        for text in ("Date of birth: 1988-03-14.", "born 14 March 1988", "DOB 3/14/88", "Birthday: March 14"):
            self.assertEqual([k for k, _ in kinds(text)], [pv.BIRTH], text)
        self.assertEqual(kinds("DOB 3/14/88"), [(pv.BIRTH, "[date hidden]")])

    def test_account_numbers_need_an_account_word_and_more_than_four_digits(self):
        self.assertEqual(kinds("Claim number 12345678 and invoice 98765432."), [])
        self.assertEqual(kinds("Account balance $12,500.00 on account of rent."), [])
        self.assertEqual(kinds("account ending in 7890"), [])
        self.assertEqual(kinds("checking account 123456"), [(pv.ACCOUNT, "**3456")])

    def test_card_numbers_are_found_by_checksum_without_a_word(self):
        self.assertEqual(kinds("Paid with 5555-5555-5555-4444."), [(pv.CARD, "****-****-****-4444")])
        self.assertEqual(kinds("Bates 1234567890123."), [])  # fails the checksum

    def test_minor_names_only_near_minor_words_and_not_adults_named_by_role(self):
        self.assertEqual(kinds("Emily Sample, age 9, attends Lincoln Elementary."), [(pv.MINOR, "E. S.")])
        self.assertEqual(kinds("The minor child resides with Respondent Mark Example."), [])
        self.assertEqual(kinds("The child's mother, Ann Example, filed. Their daughter Rosa Example-Lane (age 4) "
                               "stays."), [(pv.MINOR, "R. E.")])
        self.assertEqual(kinds("Plaintiff's son, A.B., is 12 years old."), [])
        self.assertEqual(kinds("Mark Example signed the lease."), [])

    def test_line_numbers(self):
        lines = {f.kind: f.line for f in pv.scan_text(LEAKY)}
        self.assertEqual(lines[pv.SSN], 1)
        self.assertEqual(lines[pv.CARD], 3)
        self.assertEqual(lines[pv.PHONE], 4)


class FilesBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "case")
        with contextlib.redirect_stdout(io.StringIO()):
            cases.init(self.dir)
        self.drafts = os.path.join(self.dir, "Drafts")
        os.makedirs(self.drafts)

    def write(self, name, data):
        path = os.path.join(self.drafts, name)
        with open(path, "wb" if isinstance(data, bytes) else "w") as f:
            f.write(data)
        return path

    def snapshot(self):
        out = {}
        for root, _dirs, names in os.walk(self.dir):
            for n in names:
                p = os.path.join(root, n)
                st = os.stat(p)
                with open(p, "rb") as f:
                    out[p] = (hashlib.sha256(f.read()).hexdigest(), st.st_mtime_ns)
        return out

    def assertNoLeak(self, text):
        for secret in SECRETS:
            self.assertNotIn(secret, text)


class TestFiles(FilesBase):
    def test_text_draft_report_is_masked_and_located(self):
        self.write("motion.md", LEAKY)
        report = pv.check([self.drafts], case_dir=self.dir)
        out = report.render()
        self.assertNoLeak(out)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("Drafts/motion.md, line 1 · SOCIAL SECURITY NUMBER · `***-**-4329` · Rule 5.2(a)(1)", out)
        self.assertIn("## Advisory (not listed in Rule 5.2(a))", out)
        self.assertIn(pv.NOT_PROOF, out)

    def test_nothing_is_written(self):
        self.write("motion.md", LEAKY)
        before = self.snapshot()
        pv.check([self.drafts], case_dir=self.dir).render()
        self.assertEqual(self.snapshot(), before)

    def test_clean_text_exits_zero_and_still_says_not_proof(self):
        self.write("motion.md", CLEAN)
        report = pv.check([self.drafts], case_dir=self.dir)
        self.assertEqual(report.exit_code, 0)
        out = report.render()
        self.assertIn("Nothing that looks restricted or advisory was found", out)
        self.assertIn(pv.NOT_PROOF, out)

    def test_advisory_only_exits_two(self):
        self.write("letter.txt", f"Reach me at {PHONE}.")
        self.assertEqual(pv.check([self.drafts], case_dir=self.dir).exit_code, 2)

    def test_unscannable_files_are_listed_with_reasons(self):
        self.write("motion.md", CLEAN)
        self.write("photo.jpg", b"\xff\xd8\xff\xe0 not really a jpeg")
        self.write("old.doc", b"\xd0\xcf\x11\xe0 old word")
        self.write("notes.txt", b"abc\x00def")
        self.write("fake.docx", "plain text under a Word name")
        report = pv.check([self.drafts], case_dir=self.dir)
        reasons = dict(report.unscanned)
        self.assertIn("an image", reasons["Drafts/photo.jpg"])
        self.assertIn(".doc", reasons["Drafts/old.doc"])
        self.assertIn("binary", reasons["Drafts/notes.txt"])
        self.assertIn("not a zip", reasons["Drafts/fake.docx"])
        self.assertEqual(report.exit_code, 2)
        self.assertIn("## Not scanned", report.render())

    def test_missing_path_is_listed(self):
        report = pv.check([os.path.join(self.drafts, "nope.md")], case_dir=self.dir)
        self.assertEqual(report.unscanned, [("Drafts/nope.md", "no such file or folder")])

    def test_folder_scan_skips_the_store_and_hidden_files(self):
        self.write("motion.md", CLEAN)
        self.write(".hidden.md", LEAKY)
        report = pv.check([self.dir + os.sep], case_dir=self.dir)
        self.assertNotIn(".matter", report.render())
        self.assertEqual(report.restricted, [])

    def test_link_out_of_the_case_is_not_read_when_confined(self):
        outside = os.path.join(self._tmp.name, "outside.md")
        with open(outside, "w") as f:
            f.write(LEAKY)
        os.symlink(outside, os.path.join(self.drafts, "link.md"))
        report = pv.check([self.drafts], case_dir=self.dir, confine=self.dir)
        self.assertEqual(report.restricted, [])
        self.assertIn("outside the case folder", dict(report.unscanned)["Drafts/link.md"])

    def test_docx_body_footnote_header_comment_and_text_box_are_all_read(self):
        box = ('<w:r><w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml"><v:textbox><w:txbxContent>'
               f'{db.para("Box: SSN " + SSN)}</w:txbxContent></v:textbox></v:shape></w:pict></w:r>')
        hdr = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<w:hdr {db.NS}>{db.para("Filed by Jane Q. Example, phone " + PHONE)}</w:hdr>')
        comments = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    f'<w:comments {db.NS}><w:comment w:id="0" w:author="x">{db.para("her DOB is " + DOB)}'
                    '</w:comment></w:comments>')
        data = db.docx(db.para("My daughter, ", CHILD, ", age 9.", db.fnref(1)) + db.para("Intro.", box),
                       footnotes={"1": f"Card {CARD}."},
                       parts={"word/header1.xml": hdr, "word/comments.xml": comments})
        self.write("motion.docx", data)
        report = pv.check([self.drafts], case_dir=self.dir)
        out = report.render()
        self.assertNoLeak(out)
        where = {x.kind: x.location() for _f, x in report.restricted + report.advisory}
        self.assertEqual(where[pv.MINOR], "para. 1")
        self.assertEqual(where[pv.CARD], "fn. 1")
        self.assertEqual(where[pv.SSN], "text box, para. 1")
        self.assertEqual(where[pv.PHONE], "header 1, para. 1")
        self.assertEqual(where[pv.BIRTH], "comment, para. 1")
        self.assertNotIn("text boxes", out)

    def test_docx_with_tracked_changes_is_not_scanned_and_says_why(self):
        ins = f'<w:ins w:id="1" w:author="x">{db.run("added")}</w:ins>'
        self.write("motion.docx", db.docx(db.para("Text ", ins)))
        report = pv.check([self.drafts], case_dir=self.dir)
        why = dict(report.unscanned)["Drafts/motion.docx"]
        self.assertIn("tracked change", why)
        self.assertIn("still inside the file", why)
        self.assertNotIn("brief check", why)

    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext not installed")
    def test_pdf_lines_are_read_including_headers(self):
        pages = [[(72, 760, f"SSN {SSN}"), (72, 700, "Body text one."), (72, 680, f"born {DOB}")],
                 [(72, 760, f"SSN {SSN}"), (72, 700, "Body text two.")]]
        self.write("filed.pdf", db.pdf(pages))
        report = pv.check([self.drafts], case_dir=self.dir)
        self.assertNoLeak(report.render())
        locs = [x.location() for _f, x in report.restricted if x.kind == pv.SSN]
        self.assertEqual(len(locs), 2)
        self.assertTrue(all(loc.startswith("p. ") for loc in locs), locs)
        self.assertIn(pv.BIRTH, [x.kind for _f, x in report.restricted])

    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext not installed")
    def test_pdf_without_text_is_listed_not_scanned(self):
        self.write("scan.pdf", db.pdf([[]]))
        report = pv.check([self.drafts], case_dir=self.dir)
        self.assertIn("no text layer", dict(report.unscanned)["Drafts/scan.pdf"])

    def test_pdf_without_pdftotext_is_listed(self):
        self.write("filed.pdf", db.pdf([[(72, 700, "text")]]))
        report = pv.check([self.drafts], case_dir=self.dir, use_pdftotext=False)
        self.assertIn("pdftotext", dict(report.unscanned)["Drafts/filed.pdf"])


class TestRuleQuote(FilesBase):
    def test_no_pack_points_to_the_rule_and_quotes_nothing(self):
        self.write("motion.md", CLEAN)
        out = pv.check([self.drafts], case_dir=self.dir).render()
        self.assertIn("is not quoted here", out)
        self.assertIn(pv.RULE_SOURCE, out)

    def test_attached_pack_is_quoted_with_its_version(self):
        text = "\n".join(["Rule 5.2. Privacy Protection (synthetic test text)",
                          "(a) Redacted Filings. Synthetic paragraph text for the test.",
                          "Rule 7. Pleadings Allowed",
                          "(a) Pleadings. Only these pleadings are allowed."]) + "\n"
        pack = make_pack(self._tmp.name, "frcp", text, pack_id="example-frcp", version="2030-12-01",
                         title="Example Federal Rules", cite_prefix="Fed. R. Civ. P.", current_as_of="2030-12-01")
        rules.attach(self.dir, pack, "human")
        self.write("motion.md", CLEAN)
        out = pv.check([self.drafts], case_dir=self.dir).render()
        self.assertIn("> Fed. R. Civ. P. 5.2(a) · pack example-frcp@2030-12-01", out)
        self.assertIn("Synthetic paragraph text for the test.", out)
        self.assertNotIn("is not quoted here", out)


class TestCli(FilesBase):
    def run_cli(self, *args):
        env = {k: v for k, v in os.environ.items()}
        env["MATTER_KIT_CONFIG"] = os.path.join(self._tmp.name, "no-config")
        return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *args], capture_output=True,
                              text=True, env=env)

    def test_exit_codes_and_masked_output(self):
        self.write("motion.md", LEAKY)
        p = self.run_cli("privacy", "check", self.dir, "Drafts/motion.md")
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("SOCIAL SECURITY NUMBER", p.stdout)
        self.assertNoLeak(p.stdout + p.stderr)
        self.write("clean.md", CLEAN)
        self.assertEqual(self.run_cli("privacy", "check", self.dir, "Drafts/clean.md").returncode, 0)

    def test_out_writes_the_report_and_refuses_to_write_inside_what_it_checks(self):
        self.write("motion.md", LEAKY)
        out = os.path.join(self._tmp.name, "report.md")
        p = self.run_cli("privacy", "check", self.dir, "Drafts", "--out", out)
        self.assertEqual(p.returncode, 1, p.stderr)
        with open(out, encoding="utf-8") as f:
            self.assertNoLeak(f.read())
        p = self.run_cli("privacy", "check", self.dir, "Drafts", "--out", os.path.join(self.drafts, "r.md"))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("inside what is being checked", p.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.drafts, "r.md")))


class TestMcp(FilesBase):
    def call(self, args):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "matter.privacy_check", "arguments": args}}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input=json.dumps(req) + "\n",
                           capture_output=True, text=True, env=dict(os.environ, MATTER_DIR=self.dir), check=True)
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_tool_reports_exit_and_masks(self):
        self.write("motion.md", LEAKY)
        before = self.snapshot()
        res = self.call({"path": "Drafts"})
        text = res["result"]["content"][0]["text"]
        self.assertTrue(text.startswith("exit=1"), text[:80])
        self.assertNoLeak(text)
        self.assertEqual(self.snapshot(), before)

    def test_path_outside_is_refused(self):
        res = self.call({"path": "../elsewhere"})
        self.assertIn("escapes", json.dumps(res))

    def test_listed(self):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input=json.dumps(req) + "\n",
                           capture_output=True, text=True, env=dict(os.environ, MATTER_DIR=self.dir), check=True)
        names = [t["name"] for t in json.loads(p.stdout.strip().splitlines()[-1])["result"]["tools"]]
        self.assertIn("matter.privacy_check", names)


class TestHermes(FilesBase):
    def test_tool_returns_masked_report(self):
        from test_hermes_plugin import Ctx, hp, no_network
        self.write("motion.md", LEAKY)
        ctx = Ctx({"matter_dir": self.dir})
        with mock.patch("urllib.request.urlopen", no_network):
            hp.register(ctx, secret=lambda n: None)
            res = json.loads(ctx.tools["matter_privacy_check"][2]({"path": "Drafts"}))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["result"]["exit"], 1)
        self.assertNoLeak(json.dumps(res, ensure_ascii=False))
        bad = json.loads(ctx.tools["matter_privacy_check"][2]({"path": "."}))
        self.assertFalse(bad["ok"])
        self.assertIn("case folder itself", bad["error"])


if __name__ == "__main__":
    unittest.main()

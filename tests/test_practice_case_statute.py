"""The practice case's statute cite reads IN CORPUS for the section Corpus holds.

The practice draft holds three planted mistakes and three things that are right: a real case, a
real statute and a real quotation. Its README says a check should pass the right ones. The statute
is Title VII's retaliation provision, written as a brief writes it: "42 U.S.C. § 2000e-3(a)". What
could go wrong, and so what these tests pin:

- The row reads NOT MATCHED IN CORPUS for a real statute, on the first thing a newcomer tries.
  This happened on 2026-11-10, for three reasons that all had to be fixed:
  - Corpus did not return the section for the request the plugin sends (fixed in Corpus);
  - the plugin lost the heading Corpus sent: the parser only read a numbered heading line that
    ended in a full stop ("... tenant holding over."), and United States Code headings do not
    ("42 U.S.C. § 2000e–3 — Other unlawful employment practices"), so the hit was known only by
    its node id and no United States Code section could match (fixed here, TestHeadingLines);
  - the cite has a hyphen where Corpus's heading has a long dash, and a subsection letter the
    heading does not have (fixed by the dash and subsection matching this branch stacks on).
- The draft's sentence says more than the cited section does. On 2026-11-10 the cite was swapped
  for 29 U.S.C. § 2615 because that one matched; that section only covers practices the Family and
  Medical Leave Act makes unlawful, so the sentence over-claimed. The Title VII cite is back.
- The row claims more than was checked: Corpus holds sections, so a match on § 2000e–3 does not
  check subsection (a)'s text, and the row has to say so.

TITLE_VII is what Corpus sent on 2026-11-10 (after its own fix) to the plugin's request for
"42 U.S.C. § 2000e-3(a)" (federal law, five results); FMLA is what it sent for "29 U.S.C. § 2615".
Each excerpt is cut short. Nothing here reaches the network.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_citescreen_guard import DRAFT_REL, KIT, Ctx, cases, corpus, guard, hp, no_network  # noqa: E402

CITE = "42 U.S.C. § 2000e-3(a)"
TITLE_VII = (
    '5 of 187 results for "42 U.S.C. § 2000e-3(a)" in US (hybrid):\n\n'
    "1. 42 U.S.C. § 2000e\u20133 \u2014 Other unlawful employment practices\n   United States · USC\n"
    "   (a) Discrimination for making charges, testifying, assisting, or participating in enforcement\n"
    "   node_id: 741ab9f4-75f5-42de-bbbf-c549d9a1ca14\n\n"
    "2. 29 U.S.C. § 628 \u2014 Rules and regulations; exemptions\n   United States · USC\n"
    "   In accordance with the provisions of subchapter II of chapter 5 of title 5\n"
    "   node_id: 8173e4a4-ca34-44c0-86ea-0e516ef0135b\n\n"
    "3. 29 C.F.R. Appendix A to Part 1636 \u2014 Interpretive Guidance on the Pregnant Workers Fairness Act\n"
    "   United States · CFR\n   internal citations and quotation marks omitted). 177 See 42 U.S.C. 2000e-3(a).\n"
    "   node_id: d38553e7-3607-4927-9a18-1cd12c11f481\n\n"
    "4. 42 U.S.C. § 2000e\u20132 \u2014 Unlawful employment practices\n   United States · USC\n"
    "   (a) Employer practicesIt shall be an unlawful employment practice for an employer\n"
    "   node_id: a5276414-1f38-43bc-a7eb-4ffcffa08b05\n\n"
    "5. 29 C.F.R. Appendix to Part 1630 \u2014 Interpretive Guidance on Title I of the Americans With Disabilities Act\n"
    "   United States · CFR\n   Title I of the ADA prohibits disability-based discrimination in employment.\n"
    "   node_id: 74c05897-7db5-4b92-912c-217681fdbe2b")
FMLA = (
    '5 of 172 results for "29 U.S.C. § 2615" in US (hybrid):\n\n'
    "1. 29 U.S.C. § 2615 — Prohibited acts\n   United States · USC\n"
    "   (a) Interference with rights(1) Exercise of rightsIt shall be unlawful for any employer to\n"
    "   node_id: fc2e2d79-f423-40c9-b703-ac7f74f11046\n\n"
    "2. 7 U.S.C. § 2615 — Finding and issuance of plan\n   United States · USC\n"
    "   After notice and opportunity for hearing, the Secretary shall issue a plan if he finds\n"
    "   node_id: 859f4e13-9fcd-439f-a590-bae045660af1\n\n"
    "3. 12 U.S.C. § 2615 — Contracts and liens; validity\n   United States · USC\n"
    "   Nothing in this chapter shall affect the validity or enforceability of any sale\n"
    "   node_id: 4691c027-566f-404d-9212-2af23049d689\n\n"
    "4. 10 U.S.C. § 2615 — Military museums and military education programs: cooperative agreement authority\n"
    "   United States · USC\n   (a) Use Authorized.—The Secretary concerned may enter into a cooperative agreement\n"
    "   node_id: 73597373-b190-4268-ab98-ce04fe01d0d3\n\n"
    "5. 15 U.S.C. § 2615 — Penalties\n   United States · USC\n"
    "   (a) Civil(1) Any person who violates a provision of section 2614 or 2689 of this title\n"
    "   node_id: fb4dee67-e22f-43fb-b4ac-726500375aa4")


class TestPracticeCaseStatute(unittest.TestCase):
    def setUp(self):
        p = mock.patch("urllib.request.urlopen", no_network)
        p.start()
        self.addCleanup(p.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "matter")
        with redirect_stdout(io.StringIO()):
            cases.init(self.dir)
        os.makedirs(os.path.join(self.dir, "Drafts"))
        shutil.copy(os.path.join(KIT, "demo-matter", DRAFT_REL), os.path.join(self.dir, DRAFT_REL))
        env = {k: v for k, v in os.environ.items() if k != "COURTLISTENER_TOKEN"}
        env["MATTER_KIT_CONFIG"] = os.path.join(tmp.name, "config", "matter-kit")
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.asked = []

        def rpc(_adapter, payload):
            if payload["method"] == "initialize":
                return {"jsonrpc": "2.0", "id": 1, "result": {}}
            self.asked.append(payload["params"]["arguments"])
            return {"jsonrpc": "2.0", "id": 2, "result": {"text": TITLE_VII, "isError": False}}
        p = mock.patch.object(corpus.CorpusSearchAdapter, "_rpc", rpc)
        p.start()
        self.addCleanup(p.stop)

    def draft(self):
        with open(os.path.join(KIT, "demo-matter", DRAFT_REL), encoding="utf-8") as f:
            return f.read()

    def test_the_draft_cites_one_statute_and_it_is_the_one_corpus_answers(self):
        self.assertEqual(guard.statute_cites(self.draft()), [CITE])

    def test_the_cite_is_written_as_a_brief_writes_it(self):
        # A hyphen where Corpus prints a long dash, and a subsection letter Corpus's heading lacks:
        # the two forms that could not match before. The draft keeps them on purpose.
        (cite,) = guard.statute_cites(self.draft())
        self.assertIn("2000e-3", cite)
        self.assertTrue(cite.endswith("(a)"))

    def test_the_sentence_is_about_the_statute_it_cites(self):
        # The sentence was once left as it was while the cite became a leave statute.
        (line,) = [ln for ln in self.draft().splitlines() if CITE in ln]
        self.assertEqual(line, "Federal law forbids firing a worker for opposing an unlawful practice. " + CITE + ".")
        self.assertNotIn("§ 2615", self.draft())

    def test_matter_check_reads_the_statute_as_in_corpus(self):
        plugin = hp.Plugin(Ctx(dict(matter_dir=self.dir)), secret=lambda _name: None)
        out = plugin.command(f"check {DRAFT_REL}")
        self.assertEqual(self.asked, [{"query": CITE, "limit": 5, "jurisdiction": "US"}])
        (row,) = [ln for ln in out.splitlines() if CITE in ln and ln.startswith("- ")]
        self.assertIn("IN CORPUS", row)
        self.assertNotIn("NOT MATCHED", row)
        self.assertIn("Corpus: Other unlawful employment practices", row)  # the heading Corpus gave
        self.assertIn("741ab9f4-75f5-42de-bbbf-c549d9a1ca14", row)  # linked to the section, not a neighbour

    def test_the_row_says_the_subsection_was_not_checked(self):
        plugin = hp.Plugin(Ctx(dict(matter_dir=self.dir)), secret=lambda _name: None)
        out = plugin.command(f"check {DRAFT_REL}")
        (row,) = [ln for ln in out.splitlines() if CITE in ln and ln.startswith("- ")]
        self.assertIn("subsection (a) not checked against its text", row)


class TestHeadingLines(unittest.TestCase):
    def headings(self, text):
        return [h["citation"] for h in corpus._extract_hits(text)]

    def test_a_heading_with_no_full_stop_is_read(self):
        self.assertEqual(self.headings(FMLA)[:2],
                         ["29 U.S.C. § 2615 — Prohibited acts", "7 U.S.C. § 2615 — Finding and issuance of plan"])
        self.assertEqual(self.headings(TITLE_VII)[0], "42 U.S.C. § 2000e–3 — Other unlawful employment practices")

    def test_a_heading_keeps_a_colon_and_a_semicolon(self):
        got = self.headings(FMLA)
        self.assertEqual(got[2], "12 U.S.C. § 2615 — Contracts and liens; validity")
        self.assertEqual(got[3], "10 U.S.C. § 2615 — Military museums and military education programs: "
                                 "cooperative agreement authority")

    def test_a_final_full_stop_is_still_dropped(self):
        text = ("1 of 1 results:\n\n1. Miss. Code Ann. § 89-7-27 — Proceedings against tenant holding over.\n"
                "   Mississippi · MCA\n   A tenant or lessee at will\n"
                "   node_id: 37495c6b-7c51-4053-b24b-9a966a1a4492")
        self.assertEqual(self.headings(text), ["Miss. Code Ann. § 89-7-27 — Proceedings against tenant holding over"])

    def test_a_block_with_no_numbered_line_is_known_by_its_node_id(self):
        text = "   stray text\n   node_id: 37495c6b-7c51-4053-b24b-9a966a1a4492"
        self.assertEqual(self.headings(text), ["37495c6b-7c51-4053-b24b-9a966a1a4492"])


if __name__ == "__main__":
    unittest.main()

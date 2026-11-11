"""The practice case's statute cite is one Corpus answers with that section.

The practice draft holds three planted mistakes and three things that are right: a real case, a
real statute and a real quotation. Its README says a check should pass the right ones. What could
go wrong, and so what these tests pin:

- The draft cites a statute Corpus does not return for the request the plugin sends. The row then
  reads NOT MATCHED IN CORPUS for a real statute, on the first thing a newcomer tries. This happened:
  on 2026-11-10 Corpus's five results for "42 U.S.C. § 2000e-3(a)" did not include that section.
- The draft's cite is written in a form the match cannot equal to Corpus's heading (a subsection
  letter, or a hyphenated section, which Corpus writes with a long dash).
- The plugin loses the heading Corpus sent. This happened too: the parser only read a numbered
  heading line that ended in a full stop ("... tenant holding over."), and United States Code
  headings do not ("29 U.S.C. § 2615 — Prohibited acts"), so the hit was known only by its node id
  and no United States Code section could match.

ANSWER is what Corpus sent on 2026-11-10 to the plugin's own request for "29 U.S.C. § 2615"
(federal law, five results), with each excerpt cut short. Nothing here reaches the network.
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

CITE = "29 U.S.C. § 2615"
ANSWER = (
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
            return {"jsonrpc": "2.0", "id": 2, "result": {"text": ANSWER, "isError": False}}
        p = mock.patch.object(corpus.CorpusSearchAdapter, "_rpc", rpc)
        p.start()
        self.addCleanup(p.stop)

    def draft(self):
        with open(os.path.join(KIT, "demo-matter", DRAFT_REL), encoding="utf-8") as f:
            return f.read()

    def test_the_draft_cites_one_statute_and_it_is_the_one_corpus_answers(self):
        self.assertEqual(guard.statute_cites(self.draft()), [CITE])

    def test_the_cite_has_no_subsection_letter_and_no_hyphenated_section(self):
        # Corpus's heading has no "(a)", and it writes "2000e–3" with a long dash, so neither form
        # can equal the heading.
        (cite,) = guard.statute_cites(self.draft())
        self.assertNotRegex(cite, r"[()]")
        self.assertNotRegex(cite.split("§", 1)[1], r"[-–—]")

    def test_matter_check_reads_the_statute_as_in_corpus(self):
        plugin = hp.Plugin(Ctx(dict(matter_dir=self.dir)), secret=lambda _name: None)
        out = plugin.command(f"check {DRAFT_REL}")
        self.assertEqual(self.asked, [{"query": CITE, "limit": 5, "jurisdiction": "US"}])
        (row,) = [ln for ln in out.splitlines() if CITE in ln and ln.startswith("- ")]
        self.assertIn("IN CORPUS", row)
        self.assertNotIn("NOT MATCHED", row)
        self.assertIn("Corpus: Prohibited acts", row)  # the heading Corpus gave, after the cite


class TestHeadingLines(unittest.TestCase):
    def headings(self, text):
        return [h["citation"] for h in corpus._extract_hits(text)]

    def test_a_heading_with_no_full_stop_is_read(self):
        self.assertEqual(self.headings(ANSWER)[:2],
                         ["29 U.S.C. § 2615 — Prohibited acts", "7 U.S.C. § 2615 — Finding and issuance of plan"])

    def test_a_heading_keeps_a_colon_and_a_semicolon(self):
        got = self.headings(ANSWER)
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

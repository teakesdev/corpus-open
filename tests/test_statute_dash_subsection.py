"""A statute cite that differs from Corpus's heading only by its dash, or by a trailing subsection,
is matched to the section Corpus returned.

What could go wrong, and so what these tests pin:

- The United States Code prints dashed section numbers with an en dash ("42 U.S.C. § 2000e–3"),
  and briefs write a hyphen ("§ 2000e-3"). Compared character by character, the row reads NOT
  MATCHED IN CORPUS for a section Corpus holds. The reverse (a pasted en dash against a state code
  that prints a hyphen) fails the same way.
- The extractor reads only up to the en dash ("§ 2000e"), or stops at a parenthesis inside a CFR
  number ("26 C.F.R. § 1.1031(a)-1" read as "§ 1.1031"), so the kit looks up a different section.
- A cite to a subsection ("§ 2000e-3(a)(1)") never equals Corpus's section heading. The section
  match is real but weaker: the row must say the subsection was not checked, never imply it was.
- The fix is too loose: another section ("2000e-2"), or a cut that strips a parenthesis inside a
  CFR number ("1.1031(a)-1" read as "1.1031-1"), must stay unmatched.

Hits are stand-ins with the two attributes the matchers read. Corpus is stubbed; nothing reaches
the network.
"""
import importlib
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hermes_plugin import PKG, PluginCase, corpus, guard  # noqa: E402

citations = importlib.import_module(PKG + ".matterkit.citations")  # the copy the guard itself reads


def hit(heading):
    return types.SimpleNamespace(citation=heading, provenance={"url": None})


USC = hit("42 U.S.C. § 2000e–3 — Other unlawful employment practices")
USC_OTHER = hit("42 U.S.C. § 2000e–2 — Unlawful employment practices")
MISS = hit("Miss. Code Ann. § 89-7-27 — Proceedings against tenant holding over.")
CFR = hit("26 C.F.R. § 1.1031(a)-1 — Property held for productive use in trade or business or for investment.")
CFR_OTHER = hit("26 C.F.R. § 1.1031-1 — Something else.")


class TestDashes(unittest.TestCase):
    def test_hyphen_matches_en_dash_heading(self):
        self.assertEqual(guard.match_statute("42 U.S.C. § 2000e-3", [USC]), (USC.citation, None))

    def test_en_dash_matches_hyphen_heading(self):
        self.assertIsNotNone(guard.match_statute("Miss. Code Ann. § 89–7–27", [MISS]))

    def test_em_dash_and_nonbreaking_hyphen_match(self):
        self.assertIsNotNone(guard.match_statute("42 U.S.C. § 2000e—3", [USC]))
        self.assertIsNotNone(guard.match_statute("42 U.S.C. § 2000e‑3", [USC]))

    def test_other_section_does_not_match(self):
        self.assertIsNone(guard.match_statute("42 U.S.C. § 2000e-3", [USC_OTHER]))


class TestSectionOfSubsection(unittest.TestCase):
    def test_subsection_matches_its_section(self):
        self.assertEqual(guard.match_section("42 U.S.C. § 2000e-3(a)(1)", [USC]),
                         (USC.citation, None, "(a)(1)"))

    def test_exact_match_is_not_a_section_match(self):
        self.assertIsNone(guard.match_section("42 U.S.C. § 2000e-3", [USC]))

    def test_subsection_of_another_section_does_not_match(self):
        self.assertIsNone(guard.match_section("42 U.S.C. § 2000e-3(a)", [USC_OTHER]))
        self.assertIsNone(guard.match_section("Miss. Code Ann. § 89-7-28(a)", [MISS]))

    def test_parenthesis_inside_a_cfr_number_is_kept(self):
        self.assertIsNotNone(guard.match_statute("26 C.F.R. § 1.1031(a)-1", [CFR]))
        self.assertIsNone(guard.match_section("26 C.F.R. § 1.1031(a)-1", [CFR_OTHER]))


class TestExtraction(unittest.TestCase):
    def cites(self, text):
        return [r["parsed_citation"] for r in citations.extract_citations(text) if r["status"] == "extracted"]

    def test_en_dash_usc_cite_is_read_whole(self):
        self.assertIn("42 U.S.C. § 2000e–3", self.cites("See 42 U.S.C. § 2000e–3 for retaliation."))

    def test_cfr_number_with_inner_parenthesis_is_read_whole(self):
        self.assertIn("26 C.F.R. § 1.1031(a)-1", self.cites("See 26 C.F.R. § 1.1031(a)-1 here."))

    def test_year_after_a_cite_is_not_swallowed(self):
        self.assertIn("17 C.F.R. § 240.10b5-1", self.cites("Under 17 C.F.R. § 240.10b5-1 (2024), insiders"))

    def test_en_dash_state_cite_is_read_whole(self):
        self.assertIn("Miss. Code Ann. § 89–7–27", self.cites("Under Miss. Code Ann. § 89–7–27 the landlord"))


USC_TEXT = (
    "3 of 188 results for \"42 U.S.C. § 2000e-3(a)\" in US (hybrid):\n\n"
    # Corpus prints this heading with no final full stop; reading such headings is pull request 10's
    # fix (corpus_search.head_pat), so the stand-in carries one to keep these tests independent of it.
    "1. 42 U.S.C. § 2000e–3 — Other unlawful employment practices.\n"
    "   United States · USC\n"
    "   (a) Discrimination for making charges, testifying, assisting, or participating ...\n"
    "   node_id: 741ab9f4-75f5-42de-bbbf-c549d9a1ca14")


def usc_rpc(self, payload):
    if payload["method"] == "initialize":
        return {"jsonrpc": "2.0", "id": 1, "result": {}}
    return {"jsonrpc": "2.0", "id": 2, "result": {"text": USC_TEXT, "isError": False}}


class TestPluginReply(PluginCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(corpus.CorpusSearchAdapter, "_rpc", usc_rpc)
        p.start()
        self.addCleanup(p.stop)

    def test_hyphenated_subsection_cite_is_in_corpus_with_subsection_unchecked(self):
        self.grant_all()
        reply = "Under 42 U.S.C. § 2000e-3(a) an employer may not retaliate."
        block = self.plugin().on_reply(response_text=reply)
        self.assertIn("IN CORPUS", block)
        self.assertNotIn("NOT MATCHED", block)
        self.assertIn("subsection (a) not checked", block)

    def test_hyphenated_section_cite_is_in_corpus(self):
        self.grant_all()
        block = self.plugin().on_reply(response_text="Under 42 U.S.C. § 2000e-3 an employer may not retaliate.")
        self.assertIn("IN CORPUS", block)
        self.assertNotIn("NOT MATCHED", block)
        self.assertNotIn("not checked", block)


if __name__ == "__main__":
    unittest.main()

"""Sentence splitting keeps a state abbreviation with the cite it starts: "W. Va. Code § 21-23-7".

The splitter treats "Va." as a sentence end unless it knows the abbreviation, so a draft sentence
citing "W. Va. Code § 21-23-7" was cut after "Va." and the statute head was lost: the bare "§ 21-23-7"
left behind falls back to a short form. The 26 state abbreviations below were missing ("Ala.",
"Miss.", "Tex." and six others already worked). They match case-sensitively, because "ill.", "me.",
"mass." and "wash." also end ordinary sentences, and merging two sentences can hide an uncited fact.

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import briefcheck  # noqa: E402
from test_briefcheck import Base, codes  # noqa: E402

MISSING = ["Ariz", "Colo", "Conn", "Del", "Haw", "Ill", "Ind", "Kan", "Ky", "Me", "Md", "Mass", "Mich",
           "Minn", "Mo", "Mont", "Neb", "Nev", "Okla", "Or", "Pa", "Vt", "Va", "Wash", "Wis", "Wyo"]


def sentences(text):
    return [text[s:e] for s, e in briefcheck._sentence_spans(text, 0, len(text))]


class TestSplitterKeepsStateAbbreviations(unittest.TestCase):
    def test_each_missing_state_stays_in_its_sentence(self):
        for st in MISSING:
            with self.subTest(state=st):
                text = f"The clerk acted under {st}. Rev. Stat. § 1-2-3, as the order notes."
                self.assertEqual(sentences(text), [text])

    def test_west_virginia_head_is_not_cut(self):
        text = "The clerk acted under W. Va. Code § 21-23-7, as the order notes. The order says so."
        self.assertEqual(sentences(text), ["The clerk acted under W. Va. Code § 21-23-7, as the order notes.",
                                           "The order says so."])

    def test_lowercase_words_still_end_sentences(self):
        for text in ("He was ill. The doctor came.", "She called me. Then she left.",
                     "It reached critical mass. The reaction began.", "The trade was a wash. The parties settled."):
            with self.subTest(text=text):
                self.assertEqual(len(sentences(text)), 2)

    def test_previously_known_states_unchanged(self):
        text = "The clerk acted under Miss. Code Ann. § 21-23-7, as the order notes."
        self.assertEqual(sentences(text), [text])


class TestCaseNameKeepsStateAbbreviation(unittest.TestCase):
    def test_state_party_name_is_whole(self):
        displays = [d for _, d, *_ in briefcheck.scan_authorities(
            "See Smith v. Pa. Dep't of Corr., 1 F.3d 1 (3d Cir. 2000).")]
        self.assertIn("Smith v. Pa. Dep't of Corr., 1 F.3d 1", displays)

    def test_lowercase_sentence_end_still_bounds_the_name(self):
        displays = [d for _, d, *_ in briefcheck.scan_authorities(
            "She called me. Smith v. Jones, 1 F.3d 1 (3d Cir. 2000).")]
        self.assertIn("Smith v. Jones, 1 F.3d 1", displays)


class TestKnownGaps(Base):
    """Known behaviour, pinned so a change to it is deliberate (SWE-2 P3-1, P3-2, 2026-10-24).

    A sentence that really ends in a state abbreviation runs on into the next one, as "Jackson,
    Miss." already did before STATE_ABBREV. In a Facts section the merged sentence takes the next
    one's cite, so an uncited fact goes unflagged. Planned follow-up: keep a sentence whole only
    where a cite crosses the boundary. Update these tests when that lands.
    """

    def test_sentence_ending_in_a_state_runs_on(self):
        text = "Plaintiff lives in Richmond, Va. She was arrested on May 1."
        self.assertEqual(sentences(text), [text])

    def test_uncited_fact_ending_in_a_state_is_not_flagged(self):
        r = self.m.check("STATEMENT OF FACTS\n\nPlaintiff lives in Richmond, Va. The clerk refused the request. "
                         "Doc. 16 at 1.\n")
        self.assertNotIn("uncited-fact", codes(r, "UNCHECKED"))
        self.assertEqual(r.exit_code, 0)

    def test_case_name_display_takes_the_previous_sentence(self):
        found = [(k, d) for k, d, *_ in briefcheck.scan_authorities(
            "Plaintiff lives in Richmond, Va. Smith v. Jones, 1 F.3d 1 (2000).")]
        # display only: the key comes from volume, reporter and page
        self.assertEqual(found, [("case:1 f3d 1", "in Richmond, Va. Smith v. Jones, 1 F.3d 1")])


class TestStateCiteBindsInBriefCheck(Base):
    def only(self, cite):
        self.m.conn.execute("DELETE FROM authorities")
        self.m.authority(cite, "source-checked", via="https://example.org/code", evidence="opened official text")
        self.m.conn.commit()

    def assert_bound(self, cite, draft):
        self.only(cite)
        r = self.m.check(draft)
        self.assertEqual([row.text for row in r.rows], [draft])
        self.assertEqual([(a.display, a.store_citation, a.store_status) for a in r.authorities],
                         [(cite, cite, "source-checked")])
        self.assertNotIn("short-form-without-full-cite", codes(r, "WARN"))
        self.assertEqual(r.exit_code, 0)

    def test_west_virginia_code(self):
        self.assert_bound("W. Va. Code § 21-23-7", "The clerk acted under W. Va. Code § 21-23-7, as the order notes.")

    def test_oregon_revised_statutes(self):
        self.assert_bound("Or. Rev. Stat. § 12.110", "The claim is timely under Or. Rev. Stat. § 12.110, as pleaded.")

    def test_washington_revised_code(self):
        self.assert_bound("Wash. Rev. Code § 4.16.080", "The claim is timely under Wash. Rev. Code § 4.16.080, as pleaded.")


if __name__ == "__main__":
    unittest.main()

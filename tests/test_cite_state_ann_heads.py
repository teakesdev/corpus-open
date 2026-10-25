"""A "Code Ann." / "Stat. Ann." cite keeps its whole head: "W. Va. Code Ann. § 1-1-1".

kit-cite-0.4 began the head one word before "Code" / "Stat.", so "W. Va. Code Ann. § 1-1-1"
extracted as "Va. Code Ann. § 1-1-1" and keyed as Virginia, and "Ariz. Rev. Stat. Ann." and
"Ky. Rev. Stat. Ann." both extracted as "Rev. Stat. Ann." — a draft citing one state passed against
a logged statute of another. kit-cite-0.6 starts the head at the state or territory abbreviation
and takes the dotted words after it ("Rev.", "Civ. Prac. &"). Other heads extract as before.

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import briefcheck, citations  # noqa: E402
from test_briefcheck import Base, codes  # noqa: E402


def fragments(text):
    return [r["raw_fragment"] for r in citations.extract_citations(text) if r["status"] == "extracted"]


def statute_keys(citation):
    return {k for k in briefcheck.authority_keys(citation) if k.startswith("statute")}


class TestAnnotatedHeads(unittest.TestCase):
    def exact(self, text, needle):
        rows = [r for r in citations.extract_citations(text) if r["status"] == "extracted"
                and text[r["char_start"]:r["char_end"]] == needle == r["raw_fragment"]]
        self.assertTrue(rows, f"{needle!r} not an exact extracted span in {text!r}; got {fragments(text)}")

    def test_two_part_state_abbreviations(self):
        self.exact("See W. Va. Code Ann. § 1-1-1.", "W. Va. Code Ann. § 1-1-1")
        self.exact("See W.Va. Code Ann. § 1-1-1.", "W.Va. Code Ann. § 1-1-1")
        self.exact("See S. C. Code Ann. § 15-3-530.", "S. C. Code Ann. § 15-3-530")
        self.exact("See N. H. Rev. Stat. Ann. § 1-1.", "N. H. Rev. Stat. Ann. § 1-1")

    def test_any_whitespace_inside_a_two_part_abbreviation(self):
        # double spacing after a period, or PDF text, must not drop "W." and leave "Va."
        self.exact("See W.  Va. Code Ann. § 1-1-1.", "W.  Va. Code Ann. § 1-1-1")
        self.exact("See W. \n Va. Code Ann. § 1-1-1.", "W. \n Va. Code Ann. § 1-1-1")
        self.exact("See N.  H. Rev. Stat. Ann. § 1-1.", "N.  H. Rev. Stat. Ann. § 1-1")
        self.exact("See N.  Mar.  I. Code Ann. § 1-1.", "N.  Mar.  I. Code Ann. § 1-1")
        self.exact("See Am.  Samoa Code Ann. § 1-1.", "Am.  Samoa Code Ann. § 1-1")

    def test_dotted_words_between_state_and_code(self):
        self.exact("See Ariz. Rev. Stat. Ann. § 12-341.", "Ariz. Rev. Stat. Ann. § 12-341")
        self.exact("See Ky. Rev. Stat. Ann. § 413.140.", "Ky. Rev. Stat. Ann. § 413.140")
        self.exact("See N.H. Rev. Stat. Ann. § 1-1.", "N.H. Rev. Stat. Ann. § 1-1")
        self.exact("See Tex. Civ. Prac. & Rem. Code Ann. § 16.003.", "Tex. Civ. Prac. & Rem. Code Ann. § 16.003")

    def test_undotted_state_name(self):
        self.exact("See Ohio Rev. Code Ann. § 2305.10.", "Ohio Rev. Code Ann. § 2305.10")
        self.exact("See Iowa Code Ann. § 614.1.", "Iowa Code Ann. § 614.1")

    def test_territories(self):
        self.exact("See Am. Samoa Code Ann. § 1-1.", "Am. Samoa Code Ann. § 1-1")
        self.exact("See N. Mar. I. Code Ann. § 1-1.", "N. Mar. I. Code Ann. § 1-1")
        self.exact("See V.I. Code Ann. § 1-1.", "V.I. Code Ann. § 1-1")
        self.exact("See Guam Code Ann. § 1-1.", "Guam Code Ann. § 1-1")

    def test_no_truncated_row_is_left_inside_the_head(self):
        self.assertEqual(fragments("See W. Va. Code Ann. § 1-1-1."), ["W. Va. Code Ann. § 1-1-1", "§ 1-1-1"])
        self.assertEqual(fragments("See Ariz. Rev. Stat. Ann. § 12-341."),
                         ["Ariz. Rev. Stat. Ann. § 12-341", "§ 12-341"])

    def test_head_does_not_reach_back_past_the_state(self):
        self.exact("Acme, Inc. Miss. Code Ann. § 1-1-1.", "Miss. Code Ann. § 1-1-1")
        self.exact("See Exhibit A. Va. Code Ann. § 1-1-1 there.", "Va. Code Ann. § 1-1-1")
        self.exact("Miss. Code Ann. § 1; W. Va. Code Ann. § 2.", "Miss. Code Ann. § 1")
        self.exact("Miss. Code Ann. § 1; W. Va. Code Ann. § 2.", "W. Va. Code Ann. § 2")

    def test_single_word_heads_unchanged(self):
        self.exact("Under Wyo. Stat. Ann. § 17-29-800.", "Wyo. Stat. Ann. § 17-29-800")
        self.exact("See Va. Code Ann. § 1-1-1.", "Va. Code Ann. § 1-1-1")
        self.exact("See Exampleton Code Ann. § 1-2.", "Exampleton Code Ann. § 1-2")

    def test_rows_carry_the_new_parser_id(self):
        rows = citations.extract_citations("See W. Va. Code Ann. § 1-1-1.")
        self.assertEqual({r["parser"] for r in rows}, {"kit-cite-0.6"})


class TestAnnotatedHeadKeys(unittest.TestCase):
    def test_west_virginia_and_virginia_do_not_share_a_key(self):
        # statute keys leave out "Ann." (fix/brief-false-blocks): "Va. Code § 1-1-1" is the same statute
        self.assertEqual(statute_keys("W. Va. Code Ann. § 1-1-1"), {"statute:wvacode1-1-1"})
        self.assertEqual(statute_keys("Va. Code Ann. § 1-1-1"), {"statute:vacode1-1-1"})

    def test_revised_statutes_of_two_states_do_not_share_a_key(self):
        self.assertFalse(statute_keys("Ariz. Rev. Stat. Ann. § 12-341") & statute_keys("Ky. Rev. Stat. Ann. § 12-341"))

    def test_spacing_inside_the_abbreviation_does_not_matter(self):
        self.assertEqual(statute_keys("N. H. Rev. Stat. Ann. § 1-1"), statute_keys("N.H. Rev. Stat. Ann. § 1-1"))
        self.assertEqual(statute_keys("W. Va. Code Ann. § 1-1-1"), statute_keys("W.Va. Code Ann. § 1-1-1"))


class TestAnnotatedHeadsInBriefCheck(Base):
    def only(self, cite):
        self.m.conn.execute("DELETE FROM authorities")
        self.m.authority(cite, "source-checked", via="https://example.org/code", evidence="opened official text")
        self.m.conn.commit()

    def assert_not_logged(self, stored, draft):
        self.only(stored)
        r = self.m.check(draft)
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))
        self.assertEqual([a.store_citation for a in r.authorities], [None])
        self.assertEqual(r.exit_code, 1)

    def assert_bound(self, cite, draft):
        self.only(cite)
        r = self.m.check(draft)
        self.assertEqual([(a.display, a.store_citation, a.store_status) for a in r.authorities],
                         [(cite, cite, "source-checked")])
        self.assertEqual(r.exit_code, 0)

    def test_west_virginia_draft_does_not_pass_on_logged_virginia(self):
        self.assert_not_logged("Va. Code Ann. § 1-1-1", "The clerk acted under W. Va. Code Ann. § 1-1-1.")

    def test_double_spaced_west_virginia_draft_does_not_pass_on_logged_virginia(self):
        self.assert_not_logged("Va. Code Ann. § 1-1-1", "The clerk acted under W.  Va. Code Ann. § 1-1-1.")

    def test_virginia_draft_does_not_pass_on_logged_west_virginia(self):
        self.assert_not_logged("W. Va. Code Ann. § 1-1-1", "The clerk acted under Va. Code Ann. § 1-1-1.")

    def test_kentucky_draft_does_not_pass_on_logged_arizona(self):
        self.assert_not_logged("Ariz. Rev. Stat. Ann. § 12-341", "Fees follow under Ky. Rev. Stat. Ann. § 12-341.")

    def test_same_state_binds(self):
        self.assert_bound("W. Va. Code Ann. § 1-1-1", "The clerk acted under W. Va. Code Ann. § 1-1-1.")
        self.assert_bound("Ariz. Rev. Stat. Ann. § 12-341", "Fees follow under Ariz. Rev. Stat. Ann. § 12-341.")
        self.assert_bound("Ohio Rev. Code Ann. § 2305.10", "The claim is timely under Ohio Rev. Code Ann. § 2305.10.")


if __name__ == "__main__":
    unittest.main()

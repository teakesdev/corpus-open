"""A state abbreviation that is also a word ends a sentence unless a cite goes on after it.

"Del.", "Haw.", "Ill.", "Ind.", "Mass.", "Me.", "Or." and "Wash." are state abbreviations and, capitalized,
words: a fact sentence can end "… attended Mass." When the splitter never cut after them, that sentence
ran on into the next, cited one, so its missing cite went unflagged and `brief check` exited 0. They now
keep a sentence going only before a cite's next part: a number, "§", or a code or court word ("Rev.",
"Gen.", "Code", "App.", "R.", "Ct.", …). Other state abbreviations ("Pa.", "Va.", …) never end one.
Known gaps: a sentence opening with a number, "Dec." or the initial "R." or "S." still runs on
(TestKnownMerges), and some cites still split after the state (TestKnownSplits).

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

WORD_STATES = ["Del", "Haw", "Ill", "Ind", "Mass", "Me", "Or", "Wash"]
# cites that go on after a word-like state abbreviation
WORD_STATE_CITES = [
    "Del. Code Ann. tit. 10, § 8119", "Smith v. Jones, 1 A.2d 1 (Del. Ch. 2001)", "Haw. Rev. Stat. § 657-7",
    "Smith v. Jones, 1 P.3d 1 (Haw. 2003)", "735 Ill. Comp. Stat. 5/13-202", "Smith v. Jones, 50 Ill. 2d 1 (1972)",
    "Smith v. Jones, 1 N.E.2d 1 (Ill. App. Ct. 1990)", "Smith v. Jones, 9 Ill. Dec. 1 (1977)",
    "Ind. Code § 34-11-2-4", "Smith v. Jones, 1 N.E.2d 1 (Ind. Ct. App. 1999)", "Mass. Gen. Laws ch. 260, § 2A",
    "Mass. R. Civ. P. 12", "Smith v. Jones, 1 N.E.2d 1 (Mass. 1985)", "Smith v. Jones, 5 Mass. App. Ct. 1 (1977)",
    "Me. Rev. Stat. Ann. tit. 14, § 752", "Me. R. Civ. P. 12", "Smith v. Jones, 1 A.2d 1 (Me. 2005)",
    "Or. Rev. Stat. § 12.110", "Or. Admin. R. 137-001-0005", "Smith v. Jones, 1 P.3d 1 (Or. Ct. App. 2010)",
    "Wash. Rev. Code § 4.16.080", "Smith v. Jones, 100 Wash. 2d 1 (1983)", "Smith v. Jones, 1 P.3d 1 (Wash. 2001)",
    # the remaining allow-list entries (SWE-2 P3-2): Ann., Adm., Sess., Legis., Dist., Fam., Hous., Prob.,
    # Sup., Super., Att'y, S. ("Cl." and "Regs." have no common form right after one of the eight)
    "Mass. Ann. Laws ch. 93A, § 2", "Ill. Adm. Code tit. 20, § 1", "2010 Wash. Sess. Laws 1",
    "2010 Me. Legis. Serv. 1", "Smith v. Jones, 1 N.E.2d 1 (Mass. Dist. Ct. 2001)",
    "Smith v. Jones, 1 A.2d 1 (Del. Fam. Ct. 2001)", "Smith v. Jones, 1 N.E.2d 1 (Mass. Hous. Ct. 2001)",
    "Smith v. Jones, 1 N.E.2d 1 (Mass. Prob. & Fam. Ct. 2001)", "Ill. Sup. Ct. R. 213",
    "Smith v. Jones, 1 A.2d 1 (Del. Super. Ct. 2001)", "Ind. Att'y Gen. Op. No. 1 (1990)", "Ill. S. Ct. R. 213",
]
# "Trial", "Tax", "Code", "Laws" and "Acts" in the cite forms that follow a word-like state
CITE_FORM_WORDS = [
    "Ind. Trial R. 12", "Smith v. Jones, 1 N.E.2d 1 (Mass. Trial Ct. 2001)", "Smith v. Jones, 1 N.E.2d 1 (Ind. Tax Ct. 2001)",
    "Smith v. Jones, 15 Or. Tax 1 (2000)", "Haw. Code R. § 11-1-1", "Ind. Code Ann. § 34-11-2-4", "Ind. Code ch. 34-11",
    "Del. Code tit. 8, § 141", "75 Del. Laws 123 (2005)", "Or. Laws 2010, ch. 1", "1969 Mass. Acts 123",
    "1969 Mass. Acts ch. 1",
]
# full cites `brief check` reads after a state abbreviation, logged or not
STATE_CITES = [
    "W. Va. Code § 21-23-7", "Va. Code § 2-100", "Ariz. Rev. Stat. § 12-542", "Colo. Rev. Stat. § 13-80-102",
    "Conn. Gen. Stat. § 52-577", "Kan. Stat. Ann. § 60-513", "Ky. R. Civ. P. 12", "Md. Code Ann. § 5-101",
    "Minn. Stat. § 541.07", "Mo. Rev. Stat. § 516.120", "Mont. Code Ann. § 27-2-204", "Neb. Rev. Stat. § 25-207",
    "Nev. Rev. Stat. § 11.190", "Pa. R. Civ. P. 1018", "Vt. R. Civ. P. 12", "Wis. Stat. § 893.54",
    "Wyo. Stat. Ann. § 1-3-105", "Haw. Rev. Stat. § 657-7", "Haw. R. Civ. P. 12", "Ind. Code § 34-11-2-4",
    "Mass. R. Civ. P. 12", "Me. R. Civ. P. 12", "Or. Rev. Stat. § 12.110", "Or. R. Civ. P. 21",
    "Wash. Rev. Code § 4.16.080",
]
FACT = "On Sunday morning Plaintiff walked to the parish on Main Street and wrote down"


def sentences(text):
    return [text[s:e] for s, e in briefcheck._sentence_spans(text, 0, len(text))]


class TestSplitter(unittest.TestCase):
    def test_word_state_ends_a_sentence_before_prose(self):
        for st in WORD_STATES:
            for after in ("The deputy saw him.", "He left at noon.", "Officer Doe saw him.", "On Monday he left.",
                          "Plaintiff left.", "In 2019 he left.", "Rev. Smith presided.", "Gen. Smith ordered it."):
                with self.subTest(state=st, after=after):
                    self.assertEqual(sentences(f"Plaintiff attended {st}. {after}"),
                                     [f"Plaintiff attended {st}.", after])

    def test_word_state_does_not_end_a_sentence_before_its_cite(self):
        for cite in WORD_STATE_CITES:
            with self.subTest(cite):
                st = next(s for s in WORD_STATES if f"{s}. " in cite)
                self.assertFalse([t for t in sentences(f"The claim is timely under {cite}. The clerk acted.")
                                  if t.endswith(f"{st}.")])

    def test_delaware_code_and_constitutions_keep_the_sentence(self):
        # SWE-2 P2-1, P2-2 (2026-10-24)
        self.assertEqual(sentences("Directors act under 8 Del. C. § 141. The board met."),
                         ["Directors act under 8 Del. C. § 141.", "The board met."])
        for st in WORD_STATES:
            with self.subTest(st):
                self.assertEqual(sentences(f"The right is in {st}. Const. art. I, § 2. The court agreed."),
                                 [f"The right is in {st}. Const. art. I, § 2.", "The court agreed."])
        # Massachusetts cites its Declaration of Rights by part ("pt." itself still splits; not a state issue)
        self.assertFalse([t for t in sentences("The right is in Mass. Const. pt. 1, art. 14. The court agreed.")
                          if t.endswith("Mass.")])

    def test_code_or_court_word_in_prose_ends_the_sentence(self):
        # SWE-2 P2-3: "Trial", "Tax", "Code", "Laws", "Acts" keep a sentence going only in cite form
        for st in WORD_STATES:
            for after in ("Trial began on Monday.", "Tax records showed his address.", "Code enforcement arrived.",
                          "Laws changed that year.", "Acts of the board followed."):
                with self.subTest(state=st, after=after):
                    self.assertEqual(sentences(f"Plaintiff attended {st}. {after}"),
                                     [f"Plaintiff attended {st}.", after])

    def test_code_or_court_word_in_cite_form_keeps_the_sentence(self):
        for cite in CITE_FORM_WORDS:
            with self.subTest(cite):
                st = next(s for s in WORD_STATES if f"{s}. " in cite)
                self.assertFalse([t for t in sentences(f"The claim is timely under {cite}. The clerk acted.")
                                  if t.endswith(f"{st}.")])

    def test_initial_c_before_prose_still_ends_the_sentence(self):
        self.assertEqual(sentences("Plaintiff attended Mass. C. Jones testified."),
                         ["Plaintiff attended Mass.", "C. Jones testified."])

    def test_lowercase_word_ends_a_sentence_before_a_cite_word(self):
        self.assertEqual(sentences("The deputy said he was ill. Rev. Smith came."),
                         ["The deputy said he was ill.", "Rev. Smith came."])


class TestKnownMerges(Base):
    """Known gaps, pinned so a change is deliberate (SWE-2 P2-3, 2026-10-24).

    A digit, "Dec." and the initials "R." / "S." keep a word-like state's sentence going, because
    "(Mass. 1985)", "100 Wash. 2d 1", "Ill. Dec.", "Mass. R. Civ. P." and "Ill. S. Ct. R." need them.
    So a fact sentence ending in one of the eight runs on when the next sentence opens with a
    number, a December date or an initial, and in a Facts section its missing cite goes unflagged.
    """

    def test_number_date_or_initial_after_a_word_state_runs_on(self):
        for text in ("Plaintiff moved to Wash. 2 weeks later he left.", "Plaintiff attended Mass. Dec. 3 was cold.",
                     "Plaintiff attended Mass. S. Smith testified.", "Plaintiff attended Mass. R. Jones testified."):
            with self.subTest(text):
                self.assertEqual(sentences(text), [text])

    def test_uncited_fact_before_a_december_date_is_not_flagged(self):
        r = self.m.check("STATEMENT OF FACTS\n\nOn Sunday Plaintiff attended Mass. Dec. 3, 2018, he left the parish. "
                         "Doc. 16 at 1.\n")
        self.assertNotIn("uncited-fact", codes(r, "UNCHECKED"))
        self.assertEqual(r.exit_code, 0)


class TestKnownSplits(unittest.TestCase):
    """Cites that still split after a word-like state, pinned as known (SWE-2 P3-2, 2026-10-24).

    A party name that starts with a state word ("Wash. State Dep't of Corr.") and journal or
    reporter abbreviations not on the allow-list ("Mass. L. Rptr.") end the sentence at the state.
    """

    def test_state_party_name_splits(self):
        self.assertEqual(sentences("See Smith v. Wash. State Dep't of Corr., 1 P.3d 1 (Wash. 2001)."),
                         ["See Smith v. Wash.", "State Dep't of Corr., 1 P.3d 1 (Wash. 2001)."])

    def test_law_reporter_abbreviation_splits(self):
        self.assertEqual(sentences("See 1 Mass. L. Rptr. 1 (1993).")[0], "See 1 Mass.")


# from fix/brief-va-abbrev 43ef8ba, so that branch can be dropped (A5): court parentheticals for the
# states that are not words, and lowercase words that still end a fact sentence
VA_ABBREV_ADDED = ["Va.", "W. Va.", "Ariz.", "Colo.", "Conn.", "Kan.", "Ky.", "Md.", "Mich.", "Minn.", "Mo.", "Mont.",
                   "Neb.", "Nev.", "Okla.", "Pa.", "Vt.", "Wis.", "Wyo."]
LOWERCASE_WORDS = ["ill", "mass", "del", "wash", "ind", "me", "or", "haw"]


class TestPortedFromVaAbbrev(Base):
    def test_court_parenthetical_is_not_cut(self):
        for st in VA_ABBREV_ADDED:
            with self.subTest(st):
                cite = f"Smith v. Jones, 500 A.2d 1, 3 ({st} 1985)."
                self.assertEqual(sentences(f"The duty is clear. {cite}"), ["The duty is clear.", cite])

    def test_uncited_fact_ending_in_an_ordinary_word_is_still_flagged(self):
        for w in LOWERCASE_WORDS:
            with self.subTest(w):
                r = self.m.check(f"STATEMENT OF FACTS\n\nPlaintiff wrote the word {w}. "
                                 "The deputy saw him at noon. Doc. 16 at 1.\n")
                self.assertEqual([f.cite for f in r.findings if f.code == "uncited-fact"],
                                 [f"Plaintiff wrote the word {w}."])


class TestBriefCheck(Base):
    def only(self, cite=None):
        self.m.conn.execute("DELETE FROM authorities")
        if cite:
            self.m.authority(cite, "source-checked", via="https://example.org/code", evidence="opened official text")
        self.m.conn.commit()

    def test_uncited_fact_ending_in_a_word_state_is_flagged(self):
        for st in WORD_STATES:
            with self.subTest(st):
                r = self.m.check(f"STATEMENT OF FACTS\n\n{FACT} {st}. The deputy saw him at noon. Doc. 16 at 1.\n")
                self.assertEqual([f.cite for f in r.findings if f.code == "uncited-fact"], [f"{FACT} {st}."])
                self.assertEqual(r.exit_code, 2)

    def test_uncited_fact_before_a_code_or_court_word_is_flagged(self):
        for after in ("Trial began on Monday.", "Tax records showed his address."):
            with self.subTest(after):
                r = self.m.check(f"STATEMENT OF FACTS\n\nOn Sunday Plaintiff attended Mass. {after} Doc. 16 at 1.\n")
                self.assertEqual([f.cite for f in r.findings if f.code == "uncited-fact"],
                                 ["On Sunday Plaintiff attended Mass."])
                self.assertEqual(r.exit_code, 2)

    def test_cited_fact_ending_in_a_word_state_is_tied(self):
        r = self.m.check(f"STATEMENT OF FACTS\n\n{FACT} Mass. Doc. 16 at 1.\n")
        self.assertEqual([f.code for f in r.findings], [])
        self.assertEqual(r.exit_code, 0)

    def test_delaware_code_does_not_bind_another_states_section(self):
        # SWE-2 P2-1: cut after "Del.", the bare "§ 141" bound to this row with only a WARN, exit 0
        self.only("N.Y. Bus. Corp. Law § 141")
        r = self.m.check("Directors act under 8 Del. C. § 141. The board met.")
        self.assertEqual([(f.severity, f.code, f.cite) for f in r.findings],
                         [("BLOCK", "authority-not-logged", "8 Del. C. § 141")])
        self.assertEqual(r.exit_code, 1)

    def test_cited_delaware_and_constitution_facts_are_not_flagged(self):
        self.only("8 Del. C. § 141")
        r = self.m.check("STATEMENT OF FACTS\n\nThe company follows 8 Del. C. § 141. Doc. 16 at 1.\n")
        self.assertEqual([(f.severity, f.code) for f in r.findings], [])
        r = self.m.check("STATEMENT OF FACTS\n\nThe right is in Ill. Const. art. I, § 2. Doc. 16 at 1.\n")
        self.assertNotIn("uncited-fact", codes(r, "UNCHECKED"))

    def test_logged_state_cite_is_clean(self):
        for cite in STATE_CITES:
            with self.subTest(cite):
                self.only(cite)
                r = self.m.check(f"Under {cite} the clerk must act. Doc. 16.")
                self.assertEqual([(f.severity, f.code, f.cite) for f in r.findings], [])
                self.assertEqual([a.store_citation for a in r.authorities], [cite])
                self.assertEqual(r.exit_code, 0)

    def test_unlogged_state_cite_blocks_under_its_full_cite(self):
        for cite in STATE_CITES:
            with self.subTest(cite):
                self.only()
                r = self.m.check(f"Under {cite} the clerk must act. Doc. 16.")
                self.assertEqual([(f.severity, f.code, f.cite) for f in r.findings],
                                 [("BLOCK", "authority-not-logged", cite)])
                self.assertNotIn("short-form-without-full-cite", codes(r, "WARN"))


if __name__ == "__main__":
    unittest.main()

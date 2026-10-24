"""Brief check false BLOCKs: cite styles that named a logged authority but were read
as `authority-not-logged`. One class per style, with negatives.

  1. A bare "Rule N" after a full state-rule cite ("Miss. R. Crim. P. 5.1" … "Rule 5.1(b)").
  2. A state code cite with or without "Ann." / "Annotated".
  3. A hyphenated "Section 21-23-8" cross-reference (was cut to "Section 21").

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import briefcheck, citations  # noqa: E402
from test_briefcheck import Base, codes  # noqa: E402


def cites(report, raw):
    """Every authority Cite in the report whose raw text is `raw`."""
    return [c for row in report.rows for c in row.cites if c.kind == "authority" and c.raw == raw]


def unlogged(report):
    return [f.cite for f in report.findings if f.code == "authority-not-logged"]


class FalseBlockBase(Base):
    def setUp(self):
        super().setUp()
        for cite in ("Miss. R. Crim. P. 5.1", "Miss. Code Ann. § 21-23-7", "Miss. Code Ann. § 21-23-8",
                     "Miss. Code § 11-46-11"):
            self.m.authority(cite, "source-checked", via="https://example.org/official",
                             evidence="opened the official text")
        self.m.conn.commit()

    def set_bare_rule(self, prefix):
        path = os.path.join(self.m.dir, ".matter", "docket.json")
        with open(path) as f:
            d = json.load(f)
        d["bare_rule"] = prefix
        self.m.docket(d)


class TestBareRuleBindsToEarlierFullRuleCite(FalseBlockBase):
    def test_bare_rule_binds_to_earlier_state_rule(self):
        r = self.m.check("Miss. R. Crim. P. 5.1 governs the first appearance.\n\n"
                         "Rule 5.1(b) requires the court to set conditions.\n")
        self.assertEqual(unlogged(r), [])
        [c] = cites(r, "Rule 5.1(b)")
        self.assertEqual(c.target, "rule:missrcrimp51")
        self.assertIn("read as Miss. R. Crim. P. 5.1(b)", c.display)
        self.assertIn("line 1", c.display)  # the binding names the full cite it followed

    def test_federal_full_cite_then_bare_rule_still_binds_to_frcp(self):
        r = self.m.check("Leave is freely given. Fed. R. Civ. P. 15(a)(2). Later, Rule 15(a)(1) applies.")
        self.assertEqual(codes(r, "BLOCK"), [])
        [c] = cites(r, "Rule 15(a)(1)")
        self.assertEqual(c.target, "rule:fedrcivp15")

    def test_federal_binding_beats_a_different_bare_rule_default(self):
        self.set_bare_rule("Miss. R. Civ. P.")
        r = self.m.check("Leave is freely given. Fed. R. Civ. P. 15(a)(2). Later, Rule 15(a)(1) applies.")
        self.assertEqual(codes(r, "BLOCK"), [])
        [c] = cites(r, "Rule 15(a)(1)")
        self.assertEqual(c.target, "rule:fedrcivp15")
        self.assertIn("read as Fed. R. Civ. P. 15(a)(1)", c.display)

    def test_rule_list_member_counts_as_a_full_cite(self):
        self.set_bare_rule("Miss. R. Civ. P.")
        r = self.m.check("Defendants rely on Fed. R. Civ. P. 12(b)(6) and 15.\n\nRule 15(a) permits amendment.\n")
        [c] = cites(r, "Rule 15(a)")
        self.assertEqual(c.target, "rule:fedrcivp15")

    def test_bare_rule_without_prior_full_cite_uses_bare_rule(self):
        r = self.m.check("Defendants move under Rule 12(b)(6).")
        [c] = cites(r, "Rule 12(b)(6)")
        self.assertEqual(c.target, "rule:fedrcivp12")
        self.assertEqual(c.display, "Rule 12(b)(6) (read as Fed. R. Civ. P. 12(b)(6))")
        self.assertIn("Rule 12(b)(6)", unlogged(r))  # FRCP 12 is not logged: still blocks

    def test_different_base_number_does_not_bind(self):
        r = self.m.check("Miss. R. Crim. P. 5.1 governs.\n\nRule 5(b) governs service.\n")
        [c] = cites(r, "Rule 5(b)")
        self.assertEqual(c.target, "rule:fedrcivp5")
        self.assertIn("Rule 5(b)", unlogged(r))

    def test_full_cite_later_in_the_same_sentence_does_not_bind_backward(self):
        r = self.m.check("Rule 5.1(b) requires a hearing. Miss. R. Crim. P. 5.1.")
        [c] = cites(r, "Rule 5.1(b)")
        self.assertEqual(c.target, "rule:fedrcivp51")
        self.assertIn("Rule 5.1(b)", unlogged(r))

    def test_most_recent_full_cite_wins(self):
        r = self.m.check("Fed. R. Civ. P. 5.1 covers constitutional challenges.\n\n"
                         "Miss. R. Crim. P. 5.1 covers first appearances.\n\nRule 5.1(b) applies here.\n")
        [c] = cites(r, "Rule 5.1(b)")
        self.assertEqual(c.target, "rule:missrcrimp51")
        r = self.m.check("Miss. R. Crim. P. 5.1 covers first appearances.\n\n"
                         "Fed. R. Civ. P. 5.1 covers constitutional challenges.\n\nRule 5.1(b) applies here.\n")
        [c] = cites(r, "Rule 5.1(b)")
        self.assertEqual(c.target, "rule:fedrcivp51")

    def test_local_rule_is_not_a_binding_target(self):
        # Local rules are short-cited as "Local Rule 7"; a bare "Rule 7" stays bare_rule so an
        # unlogged FRCP 7 cannot pass on the strength of a logged local rule.
        r = self.m.check("L.U.Civ.R. 7(b)(4) sets the deadline.\n\nRule 7(b) requires a written motion.\n")
        [c] = cites(r, "Rule 7(b)")
        self.assertEqual(c.target, "rule:fedrcivp7")
        self.assertIn("Rule 7(b)", unlogged(r))


class TestStatuteKeyIgnoresAnnotated(FalseBlockBase):
    def test_keys_agree_with_and_without_ann(self):
        keys = [briefcheck.authority_keys(s) for s in
                ("Miss. Code Ann. § 21-23-7", "Miss. Code § 21-23-7", "Miss. Code Annotated § 21-23-7")]
        for k in keys:
            self.assertIn("statute:misscode21-23-7", k)

    def test_federal_usc_key_unchanged(self):
        self.assertIn("statute:42usc1983", briefcheck.authority_keys("42 U.S.C. § 1983"))
        r = self.m.check("Claims arise under 42 U.S.C. § 1983. A § 1983 claim needs a person.")
        self.assertEqual(codes(r, "BLOCK"), [])

    def test_draft_without_ann_matches_stored_ann(self):
        r = self.m.check("The board must give notice. Miss. Code § 21-23-7.")
        self.assertEqual(unlogged(r), [])
        self.assertEqual(r.authorities[0].store_status, "source-checked")

    def test_draft_with_ann_matches_stored_without_ann(self):
        r = self.m.check("Notice of claim is required. Miss. Code Ann. § 11-46-11(1).")
        self.assertEqual(unlogged(r), [])

    def test_annotated_spelled_out_is_a_full_cite(self):
        r = self.m.check("The board must give notice. Miss. Code Annotated § 21-23-7.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertNotIn("short-form-without-full-cite", codes(r, "WARN"))
        self.assertEqual(r.rows[0].cites[0].target, "statute:misscode21-23-7")

    def test_other_section_or_code_still_blocks(self):
        r = self.m.check("The board must give notice. Miss. Code § 21-23-9.")
        self.assertEqual(unlogged(r), ["Miss. Code § 21-23-9"])
        r = self.m.check("The board must give notice. Ala. Code § 21-23-7.")
        self.assertEqual(unlogged(r), ["Ala. Code § 21-23-7"])


class TestAnnotatedHeadStartsAtTheJurisdiction(FalseBlockBase):
    """SWE-2 P2-1: the "Code Annotated" head absorbed a preceding abbreviation ("Inc.") into the
    key and BLOCKed a logged statute. The head starts at a state or territory abbreviation."""

    def test_preceding_corporate_suffix_is_not_part_of_the_code_name(self):
        for draft in ("The claim against Acme, Inc. Miss. Code Annotated § 21-23-7 controls.",
                      "Smith Mfg. Co. Miss. Code Annotated § 21-23-7 controls."):
            r = self.m.check(draft)
            self.assertEqual(codes(r, "BLOCK"), [], draft)
            self.assertNotIn("short-form-without-full-cite", codes(r, "WARN"))
            [c] = cites(r, "Miss. Code Annotated § 21-23-7")
            self.assertEqual(c.target, "statute:misscode21-23-7")

    def test_head_keys(self):
        # scanner level: a draft unit cannot hold "W. Va." whole (the splitter ends a sentence
        # after "Va."), but the head pattern must still read it as West Virginia, not "Va."
        for text, key in (("W. Va. Code Annotated § 21-23-7", "statute:wvacode21-23-7"),
                          ("Acme, Inc. Miss. Code Annotated § 21-23-7", "statute:misscode21-23-7"),
                          ("D.C. Code Annotated § 1-1-1", "statute:dccode1-1-1")):
            self.assertEqual([k for k, *_ in briefcheck.scan_authorities(text)], [key], text)

    def test_unlogged_state_does_not_fall_back_to_another_state(self):
        r = self.m.check("Ala. Code Annotated § 21-23-7 controls.")
        self.assertEqual(unlogged(r), ["Ala. Code Annotated § 21-23-7"])

    def test_federal_code_annotated_keeps_the_short_form_path(self):
        # D2: federal keys are unaffected; "U.S. Code Annotated" without a title binds as before
        r = self.m.check("Relief lies under U.S. Code Annotated § 1983 here.")
        self.assertEqual(codes(r, "BLOCK"), [])
        [c] = cites(r, "§ 1983")
        self.assertEqual(c.target, "statute:42usc1983")
        self.assertIn("short-form-without-full-cite", codes(r, "WARN"))


class TestHyphenatedSectionCrossReference(FalseBlockBase):
    def exact(self, text, needle):
        got = [x["raw_fragment"] for x in citations.extract_citations(text) if x["status"] == "extracted"]
        self.assertIn(needle, got)

    def test_extraction_takes_the_whole_hyphenated_number(self):
        self.exact("Under Section 21-23-8, the board may act.", "Section 21-23-8")
        self.exact("See section 21-23-8(b) for the time.", "section 21-23-8(b)")

    def test_coordinated_members_keep_the_whole_number(self):
        text = "See section 21-23-7 or 21-23-8 for the procedure."
        self.exact(text, "section 21-23-7 or 21-23-8")
        self.exact(text, "section 21-23-7")
        self.exact(text, "21-23-8")

    def test_plain_and_non_numeric_hyphen_unchanged(self):
        self.exact("Compare Section 80 of the 1909 Act.", "Section 80")
        self.exact("Bring Section 1983-style claims here.", "Section 1983")

    def test_section_word_binds_to_earlier_full_cite(self):
        r = self.m.check("The board must give notice. Miss. Code Ann. § 21-23-8. "
                         "Section 21-23-8 also sets the time.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertNotIn("short-form-without-full-cite", codes(r, "WARN"))
        [c] = cites(r, "Section 21-23-8")
        self.assertEqual(c.target, "statute:misscode21-23-8")

    def test_section_word_binds_like_section_sign(self):
        by_word = self.m.check("The time is set by Section 21-23-8.")
        by_sign = self.m.check("The time is set by § 21-23-8.")
        w, s = by_word.rows[0].cites[0], by_sign.rows[0].cites[0]
        self.assertEqual(w.target, s.target)
        self.assertEqual(w.target, "statute:misscode21-23-8")
        self.assertEqual((codes(by_word, "BLOCK"), codes(by_word, "WARN")),
                         (codes(by_sign, "BLOCK"), codes(by_sign, "WARN")))

    def test_other_hyphenated_section_does_not_bind(self):
        r = self.m.check("Miss. Code Ann. § 21-23-8 sets notice. Section 21-23-9 sets the time.")
        self.assertEqual(unlogged(r), ["Section 21-23-9"])


class TestCoordinatedSectionMembers(FalseBlockBase):
    """SWE-2 P2-2: "Section 21-23-7, 21-23-9" checked only 21-23-7 (the extractor stops at the
    comma), and "Section X or Y" was one cite under a key no store holds. Each member is now
    its own short-form cite. 21-23-7 and 21-23-8 are logged; 21-23-9 is not."""
    FULL = "Miss. Code Ann. § 21-23-7 applies. "

    def raws(self, r):
        return [c.raw for row in r.rows for c in row.cites if c.kind == "authority"]

    def test_comma_member_is_checked(self):
        r = self.m.check(self.FULL + "Section 21-23-7, 21-23-9 governs the time.")
        self.assertEqual(unlogged(r), ["Section 21-23-9"])
        self.assertEqual(r.exit_code, 1)
        [c] = cites(r, "Section 21-23-7")
        self.assertEqual(c.target, "statute:misscode21-23-7")

    def test_or_and_and_members_are_each_checked(self):
        for tail in ("Section 21-23-7 or 21-23-9 governs the time.",
                     "Sections 21-23-7 and 21-23-9 govern the time."):
            r = self.m.check(self.FULL + tail)
            self.assertEqual(unlogged(r), ["Section 21-23-9"], tail)
            self.assertFalse([x for x in self.raws(r) if " or " in x or " and " in x], tail)

    def test_three_members_with_serial_comma(self):
        r = self.m.check(self.FULL + "Sections 21-23-7, 21-23-8, and 21-23-9 govern the time.")
        self.assertEqual(unlogged(r), ["Section 21-23-9"])
        [c] = cites(r, "Section 21-23-8")
        self.assertEqual(c.target, "statute:misscode21-23-8")

    def test_all_members_logged_pass(self):
        r = self.m.check(self.FULL + "Sections 21-23-7 and 21-23-8 govern the time.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual(r.exit_code, 0)

    def test_lowercase_member_rows_are_not_doubled(self):
        # the extractor also emits each lowercase member as a nested row
        r = self.m.check("See section 21-23-7 or 21-23-9 for the procedure.")
        self.assertEqual(self.raws(r), ["section 21-23-7", "Section 21-23-9"])
        self.assertEqual(unlogged(r), ["Section 21-23-9"])

    def test_list_stops_at_a_number_that_is_not_a_member(self):
        r = self.m.check("Section 21-23-7, 1972 Miss. Laws ch. 5, sets the time.")
        self.assertNotIn("Section 1972", self.raws(r))
        r = self.m.check("Under Section 1983, 42 U.S.C. § 1983 also applies.")
        self.assertNotIn("Section 42", self.raws(r))

    def test_range_is_not_split_into_its_endpoints(self):
        # both endpoints are logged; a range must not pass on them
        r = self.m.check("Sections 21-23-7 through 21-23-8 govern the time.")
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))
        self.assertNotIn("Section 21-23-8", self.raws(r))


if __name__ == "__main__":
    unittest.main()

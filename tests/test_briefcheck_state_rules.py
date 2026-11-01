"""State rule cites key on the whole state: "W. Va. R. Civ. P. 12" is not "Va. R. Civ. P. 12".

The rule pattern took a one-word prefix ("Fed.", "Tex.", "Va."), so:
  - "W. Va. R. Civ. P. 12" keyed and displayed as "Va. R. Civ. P. 12": a draft citing the West Virginia
    rule passed against a logged Virginia rule, and the reverse;
  - a matter whose bare "Rule 12" reads as "W. Va. R. Civ. P." could not bind it to a logged
    "W. Va. R. Civ. P. 12" (the bare rule keyed "wva…", the logged row "va…");
  - "authority find" could not tell a logged W. Va. rule from a logged Va. rule, and "authority import"
    skipped the second of the two in one ledger as a duplicate;
  - two-part dotted states ("N.D.", "S.C.", "R.I.", "D.C.") and states cited without a dot ("Ohio",
    "Iowa", "Utah") matched nothing, so a draft citing one passed with no finding.
The draft and the stored citation are keyed by the same scan, so both sides change together.

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import authority, briefcheck  # noqa: E402
from test_briefcheck import Base, codes  # noqa: E402

TWO_PART = ["N.C.", "N.D.", "N.H.", "N.J.", "N.M.", "N.Y.", "S.C.", "S.D.", "R.I.", "D.C."]
UNDOTTED = ["Alaska", "Idaho", "Iowa", "Ohio", "Utah", "Guam"]


def rules(s):
    return [(k, disp) for k, disp, *_ in briefcheck.scan_authorities(s) if k.startswith("rule:")]


def rule_keys(citation):
    return {k for k in briefcheck.authority_keys(citation) if k.startswith("rule:")}


class TestStateRuleKeys(unittest.TestCase):
    def test_west_virginia_rule_keys_apart_from_virginia(self):
        self.assertEqual(rules("W. Va. R. Civ. P. 12"), [("rule:wvarcivp12", "W. Va. R. Civ. P. 12")])
        self.assertEqual(rules("Va. R. Civ. P. 12"), [("rule:varcivp12", "Va. R. Civ. P. 12")])

    def test_west_virginia_without_a_space_keys_the_same(self):
        self.assertEqual(rules("W.Va. R. Civ. P. 12"), [("rule:wvarcivp12", "W.Va. R. Civ. P. 12")])
        self.assertEqual(rules("W.Va.R.Civ.P. 12"), [("rule:wvarcivp12", "W.Va.R.Civ.P. 12")])

    def test_spacing_inside_and_after_the_state_keys_the_same(self):
        self.assertEqual(rules("N. D. R. Civ. P. 12"), [("rule:ndrcivp12", "N. D. R. Civ. P. 12")])
        self.assertEqual(rules("Ohio  R. Civ. P. 12"), [("rule:ohiorcivp12", "Ohio  R. Civ. P. 12")])

    def test_a_line_break_inside_the_state_rule_still_keys_the_whole_state(self):
        # SWE-2's review of the port (docs/superpowers/reviews/2026-11-01-port-state-rule-keys-code/).
        # The state alternation runs under the public line's \\s* separators and the re.S prefix split. A one-word
        # state ("Tex.") is pinned in test_briefcheck_rule_linebreaks.py; these pin the paths that only a
        # dotted, two-part or undotted state takes. Each row fails on one regression: "Ohio\nR." if the
        # undotted separator stops crossing a line break, the indented rows if the dotted separator goes
        # back to one whitespace character, and all of them if the prefix split loses re.S.
        for cite, key in [("W. Va.\nR. Civ. P. 12", "rule:wvarcivp12"),
                          ("N.D.\nR. Civ. P. 12", "rule:ndrcivp12"),
                          ("Ohio\nR. Civ. P. 12", "rule:ohiorcivp12"),
                          ("W. Va.\n  R. Civ. P. 12", "rule:wvarcivp12"),
                          ("N.D. \n R. Civ. P. 12", "rule:ndrcivp12"),
                          ("Ohio \n R. Civ. P. 12", "rule:ohiorcivp12")]:
            with self.subTest(cite=cite):
                self.assertEqual(rules(cite), [(key, cite)])

    def test_extra_spaces_inside_a_two_part_state_and_the_territory_that_moved_still_key(self):
        # The state patterns in citations.py allow any whitespace inside a two-part abbreviation and
        # treat "Am. Samoa" as undotted; the rule pattern reads them through citations._STATE_*.
        for cite, key in [("W.  Va. R. Civ. P. 12", "rule:wvarcivp12"),
                          ("N.  D. R. Civ. P. 12", "rule:ndrcivp12"),
                          ("Am. Samoa R. Civ. P. 12", "rule:amsamoarcivp12")]:
            with self.subTest(cite=cite):
                self.assertEqual(rules(cite), [(key, cite)])

    def test_territories_key_on_the_whole_name(self):
        for st, key in [("P.R.", "rule:prrcivp12"), ("V.I.", "rule:vircivp12"), ("N. Mar. I.", "rule:nmarircivp12")]:
            with self.subTest(territory=st):
                self.assertEqual(rules(f"{st} R. Civ. P. 12"), [(key, f"{st} R. Civ. P. 12")])

    def test_a_court_abbreviation_before_a_state_does_not_join_it(self):
        self.assertEqual(rules("E.D. Va. R. Civ. P. 12"), [("rule:varcivp12", "Va. R. Civ. P. 12")])

    def test_west_virginia_evidence_and_appellate_rules(self):
        self.assertEqual(rules("W. Va. R. Evid. 401"), [("rule:wvarevid401", "W. Va. R. Evid. 401")])
        self.assertEqual(rules("W. Va. R. App. P. 10(c)"), [("rule:wvarappp10", "W. Va. R. App. P. 10(c)")])

    def test_two_part_dotted_states_key_on_the_whole_state(self):
        for st in TWO_PART:
            with self.subTest(state=st):
                key = "rule:" + st.replace(".", "").lower() + "rcivp12"
                self.assertEqual(rules(f"{st} R. Civ. P. 12"), [(key, f"{st} R. Civ. P. 12")])

    def test_states_cited_without_a_dot_key(self):
        for st in UNDOTTED:
            with self.subTest(state=st):
                key = "rule:" + st.lower() + "rcivp12"
                self.assertEqual(rules(f"{st} R. Civ. P. 12"), [(key, f"{st} R. Civ. P. 12")])
        self.assertEqual(rules("Iowa R. Civ. P. 1.421"), [("rule:iowarcivp1.421", "Iowa R. Civ. P. 1.421")])

    def test_a_listed_rule_keeps_the_state(self):
        self.assertEqual(rules("W. Va. R. Civ. P. 12 and 15"),
                         [("rule:wvarcivp12", "W. Va. R. Civ. P. 12"), ("rule:wvarcivp15", "W. Va. R. Civ. P. 15")])

    def test_a_stored_rule_list_continues_the_state_prefix(self):
        self.assertEqual(rule_keys("W. Va. R. Civ. P. 5(b); 6(a); 12"),
                         {"rule:wvarcivp5", "rule:wvarcivp6", "rule:wvarcivp12"})
        self.assertEqual(rule_keys("N.D. R. Civ. P. 5(b); 12"), {"rule:ndrcivp5", "rule:ndrcivp12"})

    def test_federal_and_one_word_state_rules_are_unchanged(self):
        for cite, key in [("Fed. R. Civ. P. 12", "rule:fedrcivp12"), ("Fed.R.Civ.P. 12", "rule:fedrcivp12"),
                          ("Fed. R. Evid. 401", "rule:fedrevid401"), ("Tex. R. Civ. P. 12", "rule:texrcivp12"),
                          ("Miss. R. Civ. P. 12(b)(6)", "rule:missrcivp12"), ("Pa. R. Civ. P. 1019", "rule:parcivp1019")]:
            with self.subTest(cite=cite):
                self.assertEqual(rules(cite), [(key, cite)])

    def test_a_state_word_before_an_unrelated_r_is_not_a_rule(self):
        self.assertEqual(rules("The Ohio R. Smith Co. filed suit."), [])

    def test_a_state_inside_a_longer_dotted_token_is_not_the_state(self):
        self.assertEqual(rules("the U.S.C. R. Civ. P. 3"), [])  # not "S.C. R. Civ. P. 3"

    def test_a_one_word_prefix_glued_to_a_dotted_token_is_still_scanned(self):
        # an unscanned rule passes silently, so the state anchor must not drop what the old pattern read
        self.assertEqual(rules("W.D.Tex.R. Civ. P. 12"), [("rule:texrcivp12", "Tex.R. Civ. P. 12")])


DRAFT = "Under {} 12 the motion is timely. Doc. 16."


class TestStateRulesInCheck(Base):
    def log(self, cite):
        self.m.authority(cite, "source-checked", via="https://example.org/rules", evidence="opened")
        self.m.conn.commit()

    def test_west_virginia_rule_blocks_when_only_the_virginia_rule_is_logged(self):
        self.log("Va. R. Civ. P. 12")
        r = self.m.check(DRAFT.format("W. Va. R. Civ. P."))
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))
        self.assertIn("W. Va. R. Civ. P. 12", [f.cite for f in r.findings if f.code == "authority-not-logged"])
        self.assertEqual(r.exit_code, 1)

    def test_virginia_rule_blocks_when_only_the_west_virginia_rule_is_logged(self):
        self.log("W. Va. R. Civ. P. 12")
        r = self.m.check(DRAFT.format("Va. R. Civ. P."))
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))

    def test_logged_west_virginia_rule_matches_the_west_virginia_cite(self):
        self.log("Va. R. Civ. P. 12")
        self.log("W. Va. R. Civ. P. 12")
        r = self.m.check(DRAFT.format("W. Va. R. Civ. P."))
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual([a.store_citation for a in r.authorities], ["W. Va. R. Civ. P. 12"])

    def test_unlogged_two_part_and_undotted_state_rules_block(self):
        for st in ["N.D.", "S.C.", "Ohio"]:
            with self.subTest(state=st):
                r = self.m.check(DRAFT.format(f"{st} R. Civ. P."))
                self.assertIn("authority-not-logged", codes(r, "BLOCK"))

    def test_logged_two_part_state_rule_matches(self):
        self.log("N.D. R. Civ. P. 12")
        r = self.m.check(DRAFT.format("N.D. R. Civ. P."))
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual([a.store_citation for a in r.authorities], ["N.D. R. Civ. P. 12"])

    def test_bare_rule_read_as_a_west_virginia_rule_binds_to_the_logged_rule(self):
        path = os.path.join(self.m.dir, ".matter", "docket.json")
        with open(path) as f:
            docket = json.load(f)
        docket["bare_rule"] = "W. Va. R. Civ. P."
        self.m.docket(docket)
        self.log("W. Va. R. Civ. P. 12")
        r = self.m.check(DRAFT.format("Rule"))
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual([a.store_citation for a in r.authorities], ["W. Va. R. Civ. P. 12"])


class TestStateRulesInAuthorityLookup(Base):
    def test_find_tells_west_virginia_from_virginia(self):
        for cite in ("Va. R. Civ. P. 12", "W. Va. R. Civ. P. 12"):
            self.m.authority(cite, "source-checked", via="https://example.org/rules", evidence="opened")
        self.m.conn.commit()
        self.assertEqual(authority.find(self.m.conn, "W. Va. R. Civ. P. 12")["citation"], "W. Va. R. Civ. P. 12")
        self.assertEqual(authority.find(self.m.conn, "Va. R. Civ. P. 12")["citation"], "Va. R. Civ. P. 12")

    def test_ledger_imports_virginia_and_west_virginia_rules(self):
        self.m.write("ledger.md",
                     "| Citation | Status |\n|---|---|\n"
                     "| Va. R. Civ. P. 12 | UNVERIFIED |\n"
                     "| W. Va. R. Civ. P. 12 | UNVERIFIED |\n")
        planned, skipped = authority.import_ledger(self.m.conn, self.m.dir, os.path.join(self.m.dir, "ledger.md"))
        self.assertEqual([r["citation"] for r in planned], ["Va. R. Civ. P. 12", "W. Va. R. Civ. P. 12"])
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main()

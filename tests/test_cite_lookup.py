"""cite lookup: a draft's case cites, and nothing else, checked against CourtListener.

Every response here is synthetic JSON shaped like the citation-lookup API's documented output
(v4, read 2026-11-01). Case names are invented. No test reaches the network.
"""
import ast
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import briefcheck, caselaw, cases, citelookup, consent, store  # noqa: E402
from matterkit.adapters import courtlistener_citations as clc  # noqa: E402

DRAFT = """# Response in opposition

Plaintiff Jane Roe was hurt at 12 Elm Street on March 3. See `Evidence/photos/IMG_0001.jpg`.

Courts dismiss such claims. Smith v. Jones, 123 F.4th 456, 460 (5th Cir. 2024). See also
Doe v. Roe Corp., 501 U.S. 1001 (1991); Acme Widgets, Inc. v. Brown, 2021 WL 1234567, at *2
(S.D. Miss. Mar. 3, 2021). Id. at 461. Smith, 123 F.4th at 459. 42 U.S.C. § 1983 and
Fed. R. Civ. P. 12(b)(6) say nothing else. Gamma v. Delta, 77 F.
3d 88 (4th Cir. 1996). Again, Smith v. Jones, 123 F.4th 456.
"""

SENT = ["123 F.4th 456", "501 U.S. 1001", "2021 WL 1234567", "77 F.3d 88"]
TOKEN = "tok-SECRET-0123456789"


def cluster(cid, name, filed, cite):
    return {"id": cid, "absolute_url": f"/opinion/{cid}/{name.lower().replace(' ', '-')}/",
            "case_name": name, "case_name_full": name, "date_filed": filed,
            "citations": [dict(zip(("volume", "reporter", "page"), cite.split(" ", 2)), type=1)]}


def result(payload, cite, status, clusters=(), error=""):
    """One result object as the API returns it, indexed into the payload text it was given."""
    start = payload.index(cite)
    return {"citation": cite, "normalized_citations": [cite], "start_index": start,
            "end_index": start + len(cite), "status": status, "error_message": error,
            "clusters": list(clusters)}


def recorded(payload):
    """The synthetic recording for DRAFT's payload."""
    return [
        result(payload, "123 F.4th 456", 200, [cluster(9001, "Smith v. Jones", "2024-05-01", "123 F.4th 456")]),
        result(payload, "501 U.S. 1001", 200, [cluster(9002, "Unrelated Holdings v. Other", "1991-06-10",
                                                       "501 U.S. 1001")]),
        result(payload, "2021 WL 1234567", 404, error="Citation not found: '2021 WL 1234567'"),
        result(payload, "77 F.3d 88", 300, [cluster(9003, "Epsilon v. Zeta", "1996-02-01", "77 F.3d 88"),
                                            cluster(9004, "Gamma v. Delta", "1996-02-02", "77 F.3d 88")]),
    ]


def grant(provider=citelookup.PROVIDER, endpoints=(citelookup.ENDPOINT,),
          payload_types=(citelookup.PAYLOAD_TYPE,), kind="human", **extra):
    g = {"provider": provider, "endpoints": list(endpoints), "payload_types": list(payload_types),
         "daily_budget": 10, "created_by": "ty", "kind": kind, "authorized_callers": []}
    g.update(extra)
    return g


class Matter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "matter")
        with redirect_stdout(io.StringIO()):
            cases.init(self.dir)
        # A new case checks its case cites by default (brief check, cite lookup): a settings folder of its
        # own and no COURTLISTENER_TOKEN, so no test reads the person's real saved key. A test that needs a
        # key sets one.
        env = {k: v for k, v in os.environ.items() if k != "COURTLISTENER_TOKEN"}
        env["MATTER_KIT_CONFIG"] = os.path.join(self._tmp.name, "no-config")
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)


# --------------------------------------------------------------------------
# What leaves the machine
# --------------------------------------------------------------------------
class TestPayload(unittest.TestCase):
    def test_only_rebuilt_case_cites_are_sent(self):
        cites = citelookup.draft_cites(DRAFT)
        self.assertEqual([c.sent for c in cites], SENT)
        payloads = citelookup.payloads(cites)
        self.assertEqual(payloads, ["\n".join(SENT)])
        for leak in ("Smith", "Jones", "Roe", "Elm", "Evidence", "IMG", "460", "5th Cir", "1983", "Fed. R.",
                     "Id.", "at 459", "1991", "S.D. Miss", "Response"):
            self.assertNotIn(leak, payloads[0])

    def test_draft_side_kept_locally(self):
        smith = citelookup.draft_cites(DRAFT)[0]
        self.assertEqual(smith.names, ["Smith v. Jones"])
        self.assertEqual(smith.year, "2024")
        self.assertEqual(smith.lines, [5, 9])
        gamma = citelookup.draft_cites(DRAFT)[3]
        self.assertEqual(gamma.names, ["Gamma v. Delta"])
        self.assertEqual(gamma.lines, [8])

    def test_a_lowercase_word_in_front_of_a_name_belongs_to_the_sentence(self):
        # Seen in a live reply: "the plausibility standard of Ashcroft v. Iqbal" was listed as
        # "of Ashcroft v. Iqbal". Brief check's name walk keeps "of", "in" and "the" because they
        # join the words of a name; in front of the name they are the sentence's.
        for text, shown in (
                ("the plausibility standard of Ashcroft v. Iqbal, 556 U.S. 662 (2009)",
                 "Ashcroft v. Iqbal, 556 U.S. 662"),
                ("as in Bell Atlantic Corp. v. Twombly, 550 U.S. 544 (2007)",
                 "Bell Atlantic Corp. v. Twombly, 550 U.S. 544"),
                ("so held the Estate of Smith v. Jones, 100 F.3d 200 (5th Cir. 1996)",
                 "Estate of Smith v. Jones, 100 F.3d 200"),
                ("the rule of In re Gault, 387 U.S. 1 (1967)", "In re Gault, 387 U.S. 1"),
                ("the holding in in re Gault, 387 U.S. 1 (1967)", "in re Gault, 387 U.S. 1")):
            (c,) = citelookup.draft_cites(text)
            self.assertEqual(c.display, shown, text)
            self.assertEqual(c.names, [shown.rsplit(", ", 1)[0]], text)
        # A connector with no name after it is no name at all.
        (c,) = citelookup.draft_cites("the rule in 556 U.S. 662 (2009) applies")
        self.assertEqual((c.display, c.names), ("556 U.S. 662", []))

    def test_a_name_keeps_its_own_first_word_and_the_words_inside_it(self):
        for text, shown in (
                ("The Florida Bar v. Went For It, Inc., 515 U.S. 618 (1995)",
                 "The Florida Bar v. Went For It, Inc., 515 U.S. 618"),
                ("He relies on de la Cruz v. State, 100 F.3d 300 (5th Cir. 1996)",
                 "de la Cruz v. State, 100 F.3d 300"),
                ("It was close. A Book Named Memoirs v. Attorney General, 383 U.S. 413 (1966)",
                 "A Book Named Memoirs v. Attorney General, 383 U.S. 413"),
                ("Women in City Government United v. City of New York, 563 F.2d 537 (2d Cir. 1977)",
                 "Women in City Government United v. City of New York, 563 F.2d 537"),
                ("See United States v. Board of Education of City of Chicago, 11 F.3d 668 (7th Cir. 1993)",
                 "United States v. Board of Education of City of Chicago, 11 F.3d 668")):
            (c,) = citelookup.draft_cites(text)
            self.assertEqual(c.display, shown, text)

    def test_reporter_spacing_is_canonical(self):
        cites = citelookup.draft_cites("A v. B, 9 F. Supp.2d 10; C v. D, 3 U. S. 4; E v. F, 5 S.Ct. 6.")
        self.assertEqual([c.sent for c in cites], ["9 F. Supp. 2d 10", "3 U.S. 4", "5 S. Ct. 6"])

    def test_every_reporter_the_scanner_knows_has_a_canonical_form(self):
        for rep in briefcheck._REPORTERS:
            spelled = rep.replace("(?:'x|x)\\.?", "'x").replace("\\s*", " ").replace("\\.", ".")
            for variant in ([spelled.replace("[23]", d) for d in "23"] if "[23]" in spelled else [spelled]):
                self.assertIn(briefcheck._rep_key(variant), citelookup.CANONICAL_REPORTERS, rep)

    def test_no_case_cites_means_no_payload(self):
        self.assertEqual(citelookup.payloads(citelookup.draft_cites("Under 42 U.S.C. § 1983, see Id.")), [])

    def test_chunks_of_sixty(self):
        cites = citelookup.draft_cites("; ".join(f"X v. Y, 1 F.3d {n}" for n in range(1, 131)))
        sizes = [len(p.split("\n")) for p in citelookup.payloads(cites)]
        self.assertEqual(sizes, [60, 60, 10])

    def test_digits_from_other_scripts_are_sent_as_ascii(self):
        # SWE-2 round 1, P3: the scanner's \d reads "١٢٣"; the payload and its validator are ASCII only.
        (c,) = citelookup.draft_cites("Smith v. Jones, ١٢٣ F.4th ٤٥٦ (2020).")
        self.assertEqual(c.sent, "123 F.4th 456")
        with self.assertRaises(ValueError):
            citelookup.validate_payload("١٢٣ F.4th 456")

    def test_validator_refuses_anything_but_bare_cites(self):
        citelookup.validate_payload("123 F.4th 456\n2021 WL 1234567")
        for bad in ("", "123 F.4th 456\nSmith v. Jones", "123 F.4th 456 ", "123 F.4th 456\n",
                    "Smith v. Jones, 123 F.4th 456", "123 F.4th 456, 460", "123 Umbrella 456",
                    "\n".join(["1 F.3d 1"] * 61)):
            with self.assertRaises(ValueError, msg=repr(bad)):
                citelookup.validate_payload(bad)


# --------------------------------------------------------------------------
# Reading the results
# --------------------------------------------------------------------------
class TestInterpret(unittest.TestCase):
    def setUp(self):
        self.cites = citelookup.draft_cites(DRAFT)
        self.payloads = citelookup.payloads(self.cites)
        self.results = citelookup.interpret(self.cites, self.payloads, [recorded(self.payloads[0])])
        self.by = {r.cite.sent: r for r in self.results}

    def test_outcomes(self):
        self.assertEqual({k: r.outcome for k, r in self.by.items()}, {
            "123 F.4th 456": citelookup.FOUND, "501 U.S. 1001": citelookup.NAME_DIFFERS,
            "2021 WL 1234567": citelookup.NOT_FOUND, "77 F.3d 88": citelookup.FOUND})
        self.assertEqual(self.by["123 F.4th 456"].cluster["id"], 9001)
        self.assertEqual(self.by["123 F.4th 456"].cluster["url"],
                         "https://www.courtlistener.com/opinion/9001/smith-v.-jones/")

    def test_several_opinions_picked_by_the_draft_name(self):
        gamma = self.by["77 F.3d 88"]
        self.assertEqual(gamma.cluster["case_name"], "Gamma v. Delta")
        self.assertIn("picked by the draft's case name", gamma.note)

    def test_several_opinions_and_no_name_match_is_ambiguous(self):
        cites = citelookup.draft_cites("Kappa v. Lambda, 77 F.3d 88.")
        p = citelookup.payloads(cites)
        rec = [result(p[0], "77 F.3d 88", 300, [cluster(1, "Epsilon v. Zeta", "1996-02-01", "77 F.3d 88"),
                                                 cluster(2, "Gamma v. Delta", "1996-02-02", "77 F.3d 88")])]
        (r,) = citelookup.interpret(cites, p, [rec])
        self.assertEqual(r.outcome, citelookup.AMBIGUOUS)
        self.assertEqual([c["id"] for c in r.candidates], [1, 2])

    def test_name_check_tolerates_short_forms_and_abbreviations(self):
        for draft_name, cl_name in (("Twombly", "Bell Atlantic Corp. v. Twombly"),
                                    ("Roe v. Bd. of Educ.", "Roe v. Board of Education of Topeka"),
                                    ("United States v. Quill", "United States v. Quill")):
            text = f"{draft_name}, 9 F.3d 9."
            cites = citelookup.draft_cites(text)
            p = citelookup.payloads(cites)
            (r,) = citelookup.interpret(cites, p, [[result(p[0], "9 F.3d 9", 200,
                                                           [cluster(5, cl_name, "1993-01-01", "9 F.3d 9")])]])
            self.assertEqual(r.outcome, citelookup.FOUND, (draft_name, cl_name))

    def test_year_mismatch_is_a_warning(self):
        cites = citelookup.draft_cites("Smith v. Jones, 123 F.4th 456 (5th Cir. 2019).")
        p = citelookup.payloads(cites)
        (r,) = citelookup.interpret(cites, p, [recorded_one(p[0])])
        self.assertEqual(r.outcome, citelookup.FOUND)
        self.assertTrue(any("2019" in w and "2024-05-01" in w for w in r.warnings))

    def test_statuses_400_429_and_missing(self):
        cites = citelookup.draft_cites("A v. B, 1 F.3d 1; C v. D, 2 F.3d 2; E v. F, 3 F.3d 3.")
        p = citelookup.payloads(cites)
        rec = [result(p[0], "1 F.3d 1", 400, error="Unable to find reporter"),
               result(p[0], "2 F.3d 2", 429, error="Too many citations requested.")]
        outs = [r.outcome for r in citelookup.interpret(cites, p, [rec])]
        self.assertEqual(outs, [citelookup.UNKNOWN_REPORTER, citelookup.NOT_LOOKED_UP, citelookup.NOT_LOOKED_UP])

    def test_unsent_payloads_are_not_looked_up_with_the_reason(self):
        cites = citelookup.draft_cites("; ".join(f"X v. Y, 1 F.3d {n}" for n in range(1, 71)))
        p = citelookup.payloads(cites)
        first = [result(p[0], c, 200, [cluster(n, "X v. Y", "1990-01-01", c)])
                 for n, c in enumerate(p[0].split("\n"))]
        res = citelookup.interpret(cites, p, [first], stopped="throttled until 2026-11-01T12:00:00Z")
        self.assertEqual([r.outcome for r in res].count(citelookup.FOUND), 60)
        rest = [r for r in res if r.outcome == citelookup.NOT_LOOKED_UP]
        self.assertEqual(len(rest), 10)
        self.assertIn("throttled until", rest[0].note)

    def test_untrusted_urls_are_dropped(self):
        cites = citelookup.draft_cites("Smith v. Jones, 123 F.4th 456.")
        p = citelookup.payloads(cites)
        c = cluster(9001, "Smith v. Jones", "2024-05-01", "123 F.4th 456")
        c["absolute_url"] = "https://evil.example/x"
        (r,) = citelookup.interpret(cites, p, [[result(p[0], "123 F.4th 456", 200, [c])]])
        self.assertIsNone(r.cluster["url"])

    def test_an_answer_whose_index_and_echo_disagree_is_dropped(self):
        # SWE-2 round 1, P2-1: an index pointing at line 2 must not bind an answer about line 1.
        cites = citelookup.draft_cites("Alpha v. Beta, 1 F.3d 1; Gamma v. Delta, 2 F.3d 2.")
        p = citelookup.payloads(cites)
        self.assertEqual(p, ["1 F.3d 1\n2 F.3d 2"])
        gamma = [cluster(111, "Gamma v. Delta", "1993-01-01", "2 F.3d 2")]

        def item(start, echo, normalized=None):
            return {"citation": echo, "normalized_citations": normalized or [echo], "start_index": start,
                    "end_index": start + 8, "status": 200, "error_message": "", "clusters": gamma}

        for bad in (item(9, "1 F.3d 1"), item(9, "2 F.3d 2", ["1 F.3d 1"]), item(9, "999 F.3d 999")):
            res = citelookup.interpret(cites, p, [[bad]])
            self.assertEqual([r.outcome for r in res], [citelookup.NOT_LOOKED_UP] * 2, bad)
            # SWE-2 round 2, P3: the line whose answer was set aside says so.
            self.assertEqual(res[1].note, citelookup.DROPPED_TEXT)
            self.assertNotEqual(res[0].note, citelookup.DROPPED_TEXT)
        # A dropped answer does not shadow a good one for the same line, and the echo alone still binds.
        for good in ([item(9, "1 F.3d 1"), item(9, "2 F.3d 2")], [item(999, "2 F.3d 2")]):
            res = citelookup.interpret(cites, p, [good])
            self.assertEqual([r.outcome for r in res], [citelookup.NOT_LOOKED_UP, citelookup.FOUND], good)
            self.assertEqual(res[1].cluster["id"], 111)

    def test_a_start_index_that_is_not_a_number_does_not_crash(self):
        cites = citelookup.draft_cites("Alpha v. Beta, 1 F.3d 1; Gamma v. Delta, 2 F.3d 2.")
        p = citelookup.payloads(cites)
        for at in ([9], {"a": 1}, True, "9", None):
            item = result(p[0], "2 F.3d 2", 200, [cluster(111, "Gamma v. Delta", "1993-01-01", "2 F.3d 2")])
            item["start_index"] = at
            res = citelookup.interpret(cites, p, [[item]])  # the echoed cite still places it
            self.assertEqual([r.outcome for r in res], [citelookup.NOT_LOOKED_UP, citelookup.FOUND], at)

    def judge_names(self, draft_name, cl_name):
        cites = citelookup.draft_cites(f"{draft_name}, 9 F.3d 9.")
        p = citelookup.payloads(cites)
        (r,) = citelookup.interpret(cites, p, [[result(p[0], "9 F.3d 9", 200,
                                                       [cluster(5, cl_name, "1993-01-01", "9 F.3d 9")])]])
        return r.outcome

    def test_only_a_known_abbreviation_matches_by_prefix(self):
        # SWE-2 round 1, P2-2, and round 2: "Roger" is not "Rogers", nor a stray "Marsh." "Marshall";
        # "Educ." is still "Education", in either name, and "Nat'l Ass'n" is "National Association".
        for draft_name, cl_name, outcome in (
                ("Roger v. Lee", "Rogers v. Kent", citelookup.NAME_DIFFERS),
                ("Marsh v. Lee", "Marshall v. Kent", citelookup.NAME_DIFFERS),
                ("Hill v. Lee", "Hills v. Kent", citelookup.NAME_DIFFERS),
                ("Alpha v. Marsh.", "Rogers v. Marshall", citelookup.NAME_DIFFERS),
                ("Bd. of Educ. v. Univ. of Cal.", "Board of Education v. University of California", citelookup.FOUND),
                ("Board of Education v. University of California", "Bd. of Educ. v. Univ. of Cal.", citelookup.FOUND),
                ("Nat'l Ass'n of Mfrs. v. Kappa", "National Association of Manufacturers v. Kappa", citelookup.FOUND)):
            self.assertEqual(self.judge_names(draft_name, cl_name), outcome, (draft_name, cl_name))

    def test_generic_words_alone_do_not_make_the_same_case(self):
        for draft_name, cl_name, outcome in (
                ("First Nat'l Bank v. Doe", "Bank of Am. v. Roe", citelookup.NAME_DIFFERS),
                ("Doe v. School Dist.", "Roe v. School Board", citelookup.NAME_DIFFERS),
                ("Doe v. Bd. of Educ.", "Doe v. Board of Regents", citelookup.NAME_DIFFERS),
                ("Bd. of Educ. v. Kappa", "Board of Education v. Lambda", citelookup.NAME_DIFFERS),
                ("Texas v. Smith", "Texas v. Johnson", citelookup.NAME_DIFFERS),
                ("EPA v. Smith", "Environmental Protection Agency v. Jones", citelookup.NAME_DIFFERS),
                ("Natl. Assn. of Mfrs. v. EPA",
                 "National Association of Manufacturers v. Environmental Protection Agency", citelookup.FOUND),
                ("Doe v. Roe", "Doe v. Roe", citelookup.FOUND),
                ("Monell v. Dep't of Soc. Servs.",
                 "Monell v. Department of Social Services of City of New York", citelookup.FOUND)):
            self.assertEqual(self.judge_names(draft_name, cl_name), outcome, (draft_name, cl_name))

    def test_suffixes_trade_words_and_roles_do_not_make_the_same_case(self):
        # SWE-2 round 3, P3-1: a spelled-out suffix or a trade word was still a distinctive word.
        for draft_name, cl_name, outcome in (
                ("Alpha Limited v. Doe", "Beta Limited v. Roe", citelookup.NAME_DIFFERS),
                ("Alpha Incorporated v. Doe", "Beta Incorporated v. Roe", citelookup.NAME_DIFFERS),
                ("Smith Trading Co. v. Doe", "Jones Trading Ltd. v. Roe", citelookup.NAME_DIFFERS),
                ("Kappa Steel v. Doe", "Lambda Steel v. Roe", citelookup.NAME_DIFFERS),
                ("Kappa Investments v. Doe", "Lambda Investments v. Roe", citelookup.NAME_DIFFERS),
                ("Kappa Ventures v. Doe", "Lambda Ventures v. Roe", citelookup.NAME_DIFFERS),
                ("Kappa Logistics v. Doe", "Lambda Logistics v. Roe", citelookup.NAME_DIFFERS),
                ("Alpha, Trustee v. Doe", "Beta, Trustee v. Roe", citelookup.NAME_DIFFERS),
                ("Kappa Steel Ltd. v. Doe", "Kappa Steel Limited v. Doe", citelookup.FOUND),
                ("Kappa Trading v. Doe", "Kappa Trading Co. v. Lambda", citelookup.FOUND),
                ("Steel Workers v. Doe", "United Steel Workers of America v. Doe", citelookup.FOUND),
                ("Doe, Trustee v. Kappa Steel", "Doe v. Kappa Steel Corp.", citelookup.FOUND)):
            self.assertEqual(self.judge_names(draft_name, cl_name), outcome, (draft_name, cl_name))


def recorded_one(payload):
    return [result(payload, "123 F.4th 456", 200, [cluster(9001, "Smith v. Jones", "2024-05-01", "123 F.4th 456")])]


class TestReport(unittest.TestCase):
    def setUp(self):
        cites = citelookup.draft_cites(DRAFT)
        p = citelookup.payloads(cites)
        self.report = citelookup.Report("draft.md", "ab" * 32, citelookup.interpret(cites, p, [recorded(p[0])]),
                                        p, sent=1, retrieved_at="2026-11-01T12:00:00+00:00")
        self.text = self.report.render()

    def test_exit_code_and_summary(self):
        self.assertEqual(self.report.exit_code, 1)
        self.assertIn("2 found", self.report.summary())

    def test_not_found_is_a_lead_not_proof(self):
        self.assertIn("lead to check by hand, not proof the case does not exist", self.text)
        self.assertNotRegex(self.text.lower(), r"fabricat|fake|made[- ]up|hallucinat")

    def test_limits_and_attribution(self):
        self.assertIn("does not mean the case says what the draft says, that it is still good law", self.text)
        self.assertIn("not verified or endorsed", self.text)
        self.assertIn("sha256", self.text)

    def test_exit_codes(self):
        mk = lambda o: citelookup.Result(citelookup.DraftCite("1 F.3d 1", "1 F.3d 1"), o)  # noqa: E731
        for outs, code in (([citelookup.FOUND], 0), ([citelookup.FOUND, citelookup.AMBIGUOUS], 2),
                           ([citelookup.NOT_LOOKED_UP], 2), ([citelookup.AMBIGUOUS, citelookup.NOT_FOUND], 1),
                           ([], 0)):
            rep = citelookup.Report("d.md", "0" * 64, [mk(o) for o in outs], [], sent=0, retrieved_at="t")
            self.assertEqual(rep.exit_code, code, outs)

    def test_a_run_that_sent_nothing_says_so(self):
        cites = citelookup.draft_cites(DRAFT)
        p = citelookup.payloads(cites)
        text = citelookup.Report("draft.md", "0" * 64, citelookup.interpret(cites, p, [], stopped="budget"),
                                 p, sent=0, retrieved_at="t").render()
        self.assertIn("Nothing was sent to CourtListener.", text)
        self.assertNotIn("Sent to", text)

    def test_a_request_that_got_no_answer_is_marked(self):
        # SWE-2 round 1, P3: a request that left but failed is not shown as an answered one.
        cites = citelookup.draft_cites("Alpha v. Beta, 1 F.3d 1; Gamma v. Delta, 2 F.3d 2.")
        p = citelookup.payloads(cites)
        rep = citelookup.Report("draft.md", "0" * 64, citelookup.interpret(cites, p, [], stopped="stopped: HTTP 500"),
                                p, sent=1, retrieved_at="t", answered=0)
        self.assertIn("Request 1: 2 cites", rep.render())
        self.assertIn("no answer, so its cites are NOT LOOKED UP", rep.render())
        self.assertTrue(rep.summary().endswith("1 request sent, 1 unanswered"))
        answered = citelookup.Report("draft.md", "0" * 64, [], p, sent=1, retrieved_at="t", answered=1)
        self.assertNotIn("no answer", answered.render())

    def test_a_draft_without_case_cites_does_not_claim_every_cite_found(self):
        # SWE-2 round 1, P3: zero cites is not "every case cite found".
        rep = citelookup.Report("brief.md", "0" * 64, [], [], sent=0, retrieved_at="t")
        self.assertTrue(rep.summary().startswith("NO CASE CITES IN THE DRAFT"), rep.summary())
        self.assertNotIn("EVERY CASE CITE FOUND", rep.render())
        self.assertEqual(rep.exit_code, 0)

    def test_dry_run_shows_exact_payload(self):
        p = citelookup.payloads(citelookup.draft_cites(DRAFT))
        text = citelookup.render_dry_run("draft.md", p)
        self.assertIn("\n".join(SENT), text)
        self.assertIn("nothing was sent", text.lower())


# --------------------------------------------------------------------------
# Where results land
# --------------------------------------------------------------------------
class TestRecord(Matter):
    def _row(self, citation, status="unverified", via=None, evidence=None):
        conn = store.connect(self.dir)
        aid = store.new_id("auth_")
        conn.execute("INSERT INTO authorities (id,citation,status,checked_via,check_evidence) VALUES (?,?,?,?,?)",
                     (aid, citation, status, via, evidence))
        conn.commit()
        return aid

    def test_a_failed_write_is_reported_and_the_rest_go_on(self):
        # SWE-2 round 1, P3: one bad row no longer stops the run before the report.
        good = self._row("Alpha v. Beta, 1 F.3d 1")
        bad = self._row("Gamma v. Delta, 2 F.3d 2", evidence='{"lookups": "not a list"}')
        cites = citelookup.draft_cites("Alpha v. Beta, 1 F.3d 1; Gamma v. Delta, 2 F.3d 2.")
        p = citelookup.payloads(cites)
        res = citelookup.interpret(cites, p, [[
            result(p[0], "1 F.3d 1", 200, [cluster(1, "Alpha v. Beta", "1993-01-01", "1 F.3d 1")]),
            result(p[0], "2 F.3d 2", 200, [cluster(2, "Gamma v. Delta", "1993-01-01", "2 F.3d 2")])]])
        conn = store.connect(self.dir)
        actions = dict(citelookup.record(conn, res, by="human"))
        self.assertEqual(actions["1 F.3d 1"], f"{good}: unverified -> resolved")
        self.assertTrue(actions["2 F.3d 2"].startswith("not written (AttributeError"), actions["2 F.3d 2"])
        rows = {r["id"]: (r["status"], r["check_evidence"]) for r in conn.execute("SELECT * FROM authorities")}
        self.assertEqual(rows[good][0], "resolved")
        self.assertEqual(rows[bad], ("unverified", '{"lookups": "not a list"}'))

    def _record(self):
        cites = citelookup.draft_cites(DRAFT)
        p = citelookup.payloads(cites)
        results = citelookup.interpret(cites, p, [recorded(p[0])])
        conn = store.connect(self.dir)
        return conn, citelookup.record(conn, results, by="agent:test; directed-by:ty")

    def test_found_moves_unverified_to_resolved_with_provenance(self):
        aid = self._row("Smith v. Jones, 123 F.4th 456")
        conn, actions = self._record()
        row = conn.execute("SELECT * FROM authorities WHERE id=?", (aid,)).fetchone()
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(row["checked_via"], "courtlistener:id=9001")
        ev = json.loads(row["check_evidence"])
        self.assertEqual(ev["checks"], [])
        (lk,) = ev["lookups"]
        self.assertEqual((lk["cluster_id"], lk["case_name"], lk["date_filed"]), (9001, "Smith v. Jones", "2024-05-01"))
        self.assertTrue(lk["retrieved_at"])
        self.assertEqual(lk["by"], "agent:test; directed-by:ty")
        self.assertIn(("123 F.4th 456", f"{aid}: unverified -> resolved"), actions)

    def test_found_and_not_logged_adds_a_resolved_row(self):
        conn, _ = self._record()
        rows = conn.execute("SELECT * FROM authorities WHERE checked_via='courtlistener:id=9004'").fetchall()
        self.assertEqual([(r["citation"], r["status"]) for r in rows], [("Gamma v. Delta, 77 F.3d 88", "resolved")])

    def test_nothing_written_for_other_outcomes(self):
        doe = self._row("Doe v. Roe Corp., 501 U.S. 1001")
        conn, _ = self._record()
        self.assertEqual(conn.execute("SELECT status FROM authorities WHERE id=?", (doe,)).fetchone()[0], "unverified")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM authorities WHERE citation LIKE '%WL%'").fetchone()[0], 0)

    def test_rows_past_unverified_are_left_alone(self):
        ev = json.dumps({"pins": ["460"], "checks": [{"checked_by": "ty", "source_url": "https://example.org/x"}]})
        aid = self._row("Smith v. Jones, 123 F.4th 456", "source-checked", "https://example.org/x", ev)
        conn, actions = self._record()
        row = conn.execute("SELECT * FROM authorities WHERE id=?", (aid,)).fetchone()
        self.assertEqual((row["status"], row["checked_via"], row["check_evidence"]),
                         ("source-checked", "https://example.org/x", ev))
        self.assertIn(("123 F.4th 456", f"{aid}: already source-checked; nothing written"), actions)

    def test_never_writes_source_checked(self):
        self._row("Smith v. Jones, 123 F.4th 456")
        conn, _ = self._record()
        n = conn.execute("SELECT COUNT(*) FROM authorities WHERE status IN ('source-checked','stale-flagged')")
        self.assertEqual(n.fetchone()[0], 0)

    def test_several_rows_for_one_cite_are_skipped(self):
        self._row("Smith v. Jones, 123 F.4th 456")
        self._row("Smith, 123 F.4th 456, 460")
        conn, actions = self._record()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM authorities WHERE status='resolved' "
                                      "AND citation LIKE '%123 F.4th%'").fetchone()[0], 0)
        self.assertTrue(any(c == "123 F.4th 456" and "several" in a for c, a in actions))

    def test_brief_check_then_reads_the_lookup_offline(self):
        self._row("Smith v. Jones, 123 F.4th 456")
        draft = os.path.join(self.dir, "Filings", "draft.md")
        os.makedirs(os.path.dirname(draft), exist_ok=True)
        with open(draft, "w") as f:
            f.write("Courts dismiss such claims. Smith v. Jones, 123 F.4th 456, 460 (5th Cir. 2024).\n")
        codes = lambda: {f.code for f in briefcheck.check(self.dir, draft).findings if "123" in f.cite}  # noqa: E731
        self.assertIn("authority-unverified", codes())
        self._record()
        self.assertEqual(codes(), {"authority-not-source-checked"})


# --------------------------------------------------------------------------
# The adapter: grant, token, pinning, throttle
# --------------------------------------------------------------------------
class FakeClock:
    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.t += s


class TestAdapter(Matter):
    def setUp(self):
        super().setUp()
        self.calls = []
        env = mock.patch.dict(os.environ, {"COURTLISTENER_TOKEN": TOKEN})
        env.start()
        self.addCleanup(env.stop)

    def post(self, url, fields, token):
        self.calls.append((url, fields, token))
        text = fields["text"]
        return [result(text, c, 200, [cluster(1, "X v. Y", "1990-01-01", c)]) for c in text.split("\n")]

    def adapter(self, **kw):
        kw.setdefault("post", self.post)
        return clc.CitationLookupAdapter(self.dir, "ty", **kw)

    def egress(self):
        try:
            with open(consent.egress_path(self.dir)) as f:
                return [json.loads(line) for line in f]
        except OSError:
            return []

    def test_refused_without_a_human_grant_for_this_scope(self):
        for grants in ([], [grant(kind="agent")], [grant(provider="courtlistener-free",
                                                          endpoints=["www.courtlistener.com"],
                                                          payload_types=["query_text"])],
                       [grant(endpoints=["www.courtlistener.com"])],
                       [grant(payload_types=["query_text"])],
                       [grant(expires="2020-01-01T00:00:00+00:00")]):
            consent.save_grants(self.dir, grants)
            with self.assertRaises(consent.ConsentError, msg=grants):
                self.adapter().lookup(["1 F.3d 1"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.egress(), [])

    def test_missing_token_refused_before_egress(self):
        consent.save_grants(self.dir, [grant()])
        no_config = os.path.join(self.dir, "no-config")  # no saved key either, never the person's own
        with mock.patch.dict(os.environ, {"COURTLISTENER_TOKEN": "", "MATTER_KIT_CONFIG": no_config}):
            with self.assertRaises(consent.ConsentError) as ctx:
                self.adapter()
        self.assertIn("COURTLISTENER_TOKEN", str(ctx.exception))
        self.assertEqual(self.egress(), [])

    def test_sends_to_the_pinned_url_and_logs_hash_only(self):
        consent.save_grants(self.dir, [grant()])
        payload = "\n".join(SENT)
        batches, stopped = self.adapter().lookup([payload])
        self.assertIsNone(stopped)
        self.assertEqual(self.calls, [(citelookup.URL, {"text": payload}, TOKEN)])
        self.assertEqual(citelookup.URL, "https://www.courtlistener.com/api/rest/v4/citation-lookup/")
        (rec,) = self.egress()
        self.assertEqual((rec["provider"], rec["endpoint"], rec["payload_type"]),
                         (citelookup.PROVIDER, citelookup.ENDPOINT, citelookup.PAYLOAD_TYPE))
        raw = open(consent.egress_path(self.dir)).read()
        self.assertNotIn("F.4th", raw)
        self.assertNotIn(TOKEN, raw)
        self.assertEqual(len(batches), 1)

    def test_validates_every_payload_before_sending_any(self):
        consent.save_grants(self.dir, [grant()])
        with self.assertRaises(ValueError):
            self.adapter().lookup(["1 F.3d 1", "1 F.3d 2\nSmith v. Jones"])
        self.assertEqual(self.calls, [])

    def test_waits_61_seconds_between_requests(self):
        consent.save_grants(self.dir, [grant()])
        clock = FakeClock()
        self.adapter(clock=clock, sleep=clock.sleep).lookup(["1 F.3d 1", "1 F.3d 2", "1 F.3d 3"])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(clock.sleeps, [61.0, 61.0])

    def test_throttled_stops_without_retry(self):
        consent.save_grants(self.dir, [grant()])

        tries = []

        def post(url, fields, token):
            tries.append(fields)
            if len(tries) == 2:
                raise clc.Throttled("2026-11-01T12:00:00Z")
            return self.post(url, fields, token)

        clock = FakeClock()
        batches, stopped = self.adapter(post=post, clock=clock, sleep=clock.sleep).lookup(
            ["1 F.3d 1", "1 F.3d 2", "1 F.3d 3"])
        self.assertEqual(len(tries), 2)  # the third payload is never sent, and the second is not retried
        self.assertEqual(len(batches), 1)
        self.assertIn("2026-11-01T12:00:00Z", stopped)

    def test_daily_budget_stops_the_run(self):
        consent.save_grants(self.dir, [grant(daily_budget=1)])
        clock = FakeClock()
        batches, stopped = self.adapter(clock=clock, sleep=clock.sleep).lookup(["1 F.3d 1", "1 F.3d 2"])
        self.assertEqual(len(self.calls), 1)
        self.assertIn("daily budget", stopped)

    def test_an_exhausted_grant_does_not_hide_another(self):
        # SWE-2 round 1, P3 (consent.authorize): the first matching grant's budget no longer ends the search.
        from matterkit import search
        consent.save_grants(self.dir, [grant(daily_budget=1), grant(daily_budget=5)])
        search.log_egress(self.dir, citelookup.PROVIDER, citelookup.ENDPOINT, citelookup.PAYLOAD_TYPE, "1 F.3d 1")
        g = consent.authorize(self.dir, citelookup.PROVIDER, citelookup.ENDPOINT, citelookup.PAYLOAD_TYPE, "ty")
        self.assertEqual(g["daily_budget"], 5)
        consent.save_grants(self.dir, [grant(daily_budget=1)])
        with self.assertRaisesRegex(consent.ConsentError, r"daily budget \(1\) exhausted"):
            consent.authorize(self.dir, citelookup.PROVIDER, citelookup.ENDPOINT, citelookup.PAYLOAD_TYPE, "ty")

    def test_non_list_response_stops(self):
        consent.save_grants(self.dir, [grant()])
        batches, stopped = self.adapter(post=lambda *a: {"detail": "nope"}).lookup(["1 F.3d 1"])
        self.assertEqual(batches, [])
        self.assertIn("unexpected", stopped)


class TestTransport(unittest.TestCase):
    """post_form against local servers: redirects refused, 429 read, the token never echoed."""

    def serve(self, handler):
        srv = HTTPServer(("127.0.0.1", 0), handler)
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()
        self.addCleanup(srv.server_close)
        return srv, t

    def test_redirect_refused_and_destination_untouched(self):
        stolen = []

        class Steal(BaseHTTPRequestHandler):
            def do_POST(self):
                stolen.append(self.headers.get("Authorization"))
                self.send_response(200); self.end_headers(); self.wfile.write(b"[]")

            def log_message(self, *a):
                pass

        steal, t1 = self.serve(Steal)

        class Bounce(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(307)
                self.send_header("Location", f"http://127.0.0.1:{steal.server_port}/x")
                self.end_headers()

            def log_message(self, *a):
                pass

        bounce, t2 = self.serve(Bounce)
        with self.assertRaises(consent.ConsentError) as ctx:
            clc.post_form(f"http://127.0.0.1:{bounce.server_port}/lookup", {"text": "1 F.3d 1"}, TOKEN)
        t2.join(timeout=2)
        self.assertIn("redirect", str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertEqual(stolen, [])

    def _reply(self, code, body, headers=()):
        seen = {}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["auth"] = self.headers.get("Authorization")
                seen["ctype"] = self.headers.get("Content-Type")
                seen["body"] = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(code)
                for k, v in headers:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv, t = self.serve(H)
        return f"http://127.0.0.1:{srv.server_port}/lookup", seen, t

    def test_posts_form_with_token(self):
        url, seen, t = self._reply(200, b"[]")
        self.assertEqual(clc.post_form(url, {"text": "1 F.3d 1\n2 F.3d 2"}, TOKEN), [])
        t.join(timeout=2)
        self.assertEqual(seen["auth"], "Token " + TOKEN)
        self.assertEqual(seen["ctype"], "application/x-www-form-urlencoded")
        self.assertEqual(seen["body"], b"text=1+F.3d+1%0A2+F.3d+2")

    def test_429_reads_wait_until_in_either_spelling(self):
        for key in ("wait_until", "wait_util"):
            url, _, t = self._reply(429, json.dumps({key: "2026-11-01T12:00:00Z"}).encode())
            with self.assertRaises(clc.Throttled) as ctx:
                clc.post_form(url, {"text": "1 F.3d 1"}, TOKEN)
            t.join(timeout=2)
            self.assertEqual(ctx.exception.until, "2026-11-01T12:00:00Z")

    def test_rejected_token_is_not_echoed(self):
        url, _, t = self._reply(401, b'{"detail": "Invalid token."}')
        with self.assertRaises(RuntimeError) as ctx:
            clc.post_form(url, {"text": "1 F.3d 1"}, TOKEN)
        t.join(timeout=2)
        self.assertIn("401", str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))


# --------------------------------------------------------------------------
# CLI and isolation
# --------------------------------------------------------------------------
def run_cli(*argv):
    """Run matter.py's main() in-process; returns (exit code, stdout, stderr)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("matter_cli", os.path.join(KIT, "matter.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with mock.patch.object(sys, "argv", ["matter.py", *argv]), redirect_stdout(out), redirect_stderr(err):
        try:
            mod.main()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            if not isinstance(e.code, int) and e.code is not None:
                err.write(str(e.code))
    return code, out.getvalue(), err.getvalue()


class TestCLI(Matter):
    def setUp(self):
        super().setUp()
        self.draft = os.path.join(self.dir, "Filings", "draft.md")
        os.makedirs(os.path.dirname(self.draft), exist_ok=True)
        with open(self.draft, "w") as f:
            f.write(DRAFT)

    def test_dry_run_needs_no_grant_and_sends_nothing_even_with_one(self):
        code, out, _ = run_cli("cite", "lookup", self.dir, self.draft, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("\n".join(SENT), out)
        # SWE-2 round 1, P3: the tripwire is live only when a send could happen, so give it a grant and token.
        consent.save_grants(self.dir, [grant()])
        with mock.patch.dict(os.environ, {"COURTLISTENER_TOKEN": TOKEN}), \
                mock.patch.object(clc, "post_form", side_effect=AssertionError("egress")) as post:
            code, out, _ = run_cli("cite", "lookup", self.dir, self.draft, "--dry-run", "--caller", "ty")
        self.assertEqual(code, 0)
        post.assert_not_called()
        self.assertFalse(os.path.exists(consent.egress_path(self.dir)))

    def test_binary_drafts_are_refused_whatever_their_name(self):
        # SWE-2 round 1, P3: an .odt (a zip) gave zero cites and "every case cite found".
        odt = os.path.join(self.dir, "Filings", "brief.odt")
        with open(odt, "wb") as f:
            f.write(b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + DRAFT.encode())
        code, _, err = run_cli("cite", "lookup", self.dir, odt, "--dry-run")
        self.assertNotEqual(code, 0)
        self.assertIn("binary content", err)

    def test_flags_are_refused_where_they_do_nothing(self):
        # SWE-2 round 1, P3: "cite extract --record" was accepted and ignored.
        code, _, err = run_cli("cite", "extract", self.dir, self.draft, "--record")
        self.assertNotEqual(code, 0)
        self.assertIn("--record applies to cite lookup, not cite extract", err)
        code, _, err = run_cli("cite", "lookup", self.dir, self.draft, "--dry-run", "--status", "extracted")
        self.assertNotEqual(code, 0)
        self.assertIn("--status applies to cite list", err)
        # SWE-2 round 3, P3-2: "--status" on extract, and a zero value, were still let through.
        code, _, err = run_cli("cite", "extract", self.dir, self.draft, "--status", "extracted")
        self.assertNotEqual(code, 0)
        self.assertIn("--status applies to cite list, not cite extract", err)
        code, _, err = run_cli("cite", "list", self.dir, "--max-requests", "0")
        self.assertNotEqual(code, 0)
        self.assertIn("--max-requests applies to cite lookup, not cite list", err)

    def test_cite_list_filters_by_status(self):
        # Found while checking SWE-2's round 1: "cite list --status" built "... ORDER BY ... WHERE ...".
        self.assertEqual(run_cli("cite", "extract", self.dir, self.draft)[0], 0)
        code, out, _ = run_cli("cite", "list", self.dir, "--status", "extracted")
        self.assertEqual(code, 0)
        self.assertIn("[extracted", out)
        self.assertNotIn("[extraction-failed", out)

    def test_a_found_cite_that_cannot_be_recorded_exits_2(self):
        with open(self.draft, "w") as f:
            f.write("Smith v. Jones, 123 F.4th 456 (5th Cir. 2024).\n")
        conn = store.connect(self.dir)
        conn.execute("INSERT INTO authorities (id,citation,status,check_evidence) VALUES (?,?,?,?)",
                     ("auth_bad", "Smith v. Jones, 123 F.4th 456", "unverified", '{"lookups": 1}'))
        conn.commit()
        consent.save_grants(self.dir, [grant()])
        with mock.patch.dict(os.environ, {"COURTLISTENER_TOKEN": TOKEN}), \
                mock.patch.object(clc, "post_form", side_effect=lambda u, f, t: recorded_one(f["text"])):
            code, out, err = run_cli("cite", "lookup", self.dir, self.draft, "--record", "--caller", "ty")
        self.assertEqual(code, 2)
        self.assertIn("could not be recorded", err)
        self.assertIn("not written (AttributeError", out)

    def test_a_found_cite_with_several_store_rows_exits_2(self):
        # SWE-2 round 3, P3-3: a cite --record skipped because several rows answer to it exited 0.
        with open(self.draft, "w") as f:
            f.write("Smith v. Jones, 123 F.4th 456 (5th Cir. 2024).\n")
        conn = store.connect(self.dir)
        for aid, citation in (("auth_one", "Smith v. Jones, 123 F.4th 456"), ("auth_two", "Smith, 123 F.4th 456, 460")):
            conn.execute("INSERT INTO authorities (id,citation,status) VALUES (?,?,?)", (aid, citation, "unverified"))
        conn.commit()
        consent.save_grants(self.dir, [grant()])
        with mock.patch.dict(os.environ, {"COURTLISTENER_TOKEN": TOKEN}), \
                mock.patch.object(clc, "post_form", side_effect=lambda u, f, t: recorded_one(f["text"])):
            code, out, err = run_cli("cite", "lookup", self.dir, self.draft, "--record", "--caller", "ty")
        self.assertEqual(code, 2)
        self.assertIn("1 found cite(s) could not be recorded", err)
        self.assertIn("not written: several store rows", out)

    def test_refused_when_a_person_turned_it_off(self):
        # Case cite checking is on by default (design 2026-11-09), so a refusal now needs a person's off.
        caselaw.turn_off(self.dir)
        env = {"COURTLISTENER_TOKEN": TOKEN, "MATTER_KIT_CONFIG": os.path.join(self._tmp.name, "cfg")}
        with mock.patch.dict(os.environ, env), mock.patch.object(clc, "post_form", side_effect=AssertionError("egress")):
            code, _, err = run_cli("cite", "lookup", self.dir, self.draft)
        self.assertNotEqual(code, 0)
        self.assertIn("refused", err)
        self.assertFalse(os.path.exists(consent.egress_path(self.dir)))

    def test_on_by_default_but_nothing_is_sent_without_a_key(self):
        env = {"COURTLISTENER_TOKEN": "", "MATTER_KIT_CONFIG": os.path.join(self._tmp.name, "cfg")}
        with mock.patch.dict(os.environ, env), mock.patch.object(clc, "post_form", side_effect=AssertionError("egress")):
            code, _, err = run_cli("cite", "lookup", self.dir, self.draft)
        self.assertNotEqual(code, 0)
        self.assertIn("key", err)
        self.assertFalse(os.path.exists(consent.egress_path(self.dir)))

    def test_lookup_and_record(self):
        consent.save_grants(self.dir, [grant()])
        out_path = os.path.join(self.dir, ".matter", "out", "lookup.md")
        with mock.patch.dict(os.environ, {"COURTLISTENER_TOKEN": TOKEN}), \
                mock.patch.object(clc, "post_form", side_effect=lambda u, f, t: recorded(f["text"])):
            code, out, _ = run_cli("cite", "lookup", self.dir, self.draft, "--record", "--out", out_path,
                                   "--author", "agent:test", "--directed-by", "ty", "--caller", "ty")
        self.assertEqual(code, 1)
        report = open(out_path).read()
        self.assertIn("NAME DIFFERS", report)
        self.assertNotIn(TOKEN, report + out)
        conn = store.connect(self.dir)
        vias = {r["checked_via"] for r in conn.execute("SELECT checked_via FROM authorities WHERE status='resolved'")}
        self.assertEqual(vias, {"courtlistener:id=9001", "courtlistener:id=9004"})

    def test_consent_propose_cite_lookup_scope(self):
        code, _, _ = run_cli("consent", "propose", self.dir, "--scope", "cite-lookup")
        self.assertEqual(code, 0)
        (g,) = [g for g in consent.load_grants(self.dir) if g["kind"] == "agent"]  # beside the case defaults
        self.assertEqual((g["provider"], g["endpoints"], g["payload_types"], g["kind"]),
                         (citelookup.PROVIDER, [citelookup.ENDPOINT], [citelookup.PAYLOAD_TYPE], "agent"))


class TestActivationPrompt(Matter):
    def test_prompt_names_the_scope_being_granted(self):
        shown, real_open = [], open

        class Tty(io.StringIO):
            def close(self):
                shown.append(self.getvalue())
                super().close()

        def fake_open(path, mode="r", *a, **k):
            if path == "/dev/tty":
                return io.StringIO("ACTIVATE\n") if "r" in mode else Tty()
            return real_open(path, mode, *a, **k)

        with mock.patch("builtins.open", fake_open):
            g = consent.activate_human_grant(self.dir, citelookup.PROVIDER, [citelookup.ENDPOINT],
                                             [citelookup.PAYLOAD_TYPE], "ty", daily_budget=10)
        self.assertIn(f"send citation_strings to {citelookup.ENDPOINT}", shown[0])
        self.assertEqual((g["kind"], g["daily_budget"]), ("human", 10))
        consent.authorize(self.dir, citelookup.PROVIDER, citelookup.ENDPOINT, citelookup.PAYLOAD_TYPE, "ty")


class TestEndToEndNetblocked(Matter):
    """The real CLI, adapter and urllib path, with name lookups and connections denied in the child.

    tests/netblock_sitecustomize replaces socket.socket itself, which stops `import ssl` (and so urllib)
    from loading at all; this harness denies only resolving and connecting, so the HTTPS path runs up to
    the point where a request would leave."""
    SITE = ("import socket\n"
            "def _deny(*a, **k):\n"
            "    raise OSError('network denied by the cite lookup test harness')\n"
            "socket.getaddrinfo = socket.create_connection = _deny\n"
            "socket.socket.connect = socket.socket.connect_ex = _deny\n")

    def test_real_path_is_blocked_and_reported_not_looked_up(self):
        import subprocess
        site = os.path.join(self._tmp.name, "netdeny")
        os.makedirs(site)
        with open(os.path.join(site, "sitecustomize.py"), "w") as f:
            f.write(self.SITE)
        env = dict(os.environ, PYTHONPATH=site, COURTLISTENER_TOKEN=TOKEN)
        probe = subprocess.run([sys.executable, "-c", "import ssl, socket; socket.getaddrinfo('example.org', 443)"],
                               capture_output=True, text=True, env=env, timeout=30)
        self.assertIn("network denied", probe.stderr, "the harness did not load; refusing to run the CLI")
        consent.save_grants(self.dir, [grant()])
        draft = os.path.join(self.dir, "draft.md")
        with open(draft, "w") as f:
            f.write(DRAFT)
        r = subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "cite", "lookup", self.dir, draft,
                            "--caller", "ty"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("could not reach CourtListener", r.stderr)
        self.assertEqual(r.stdout.count("**NOT LOOKED UP**"), 4)
        log = open(consent.egress_path(self.dir)).read()
        self.assertEqual(len(log.splitlines()), 1)  # the attempt is logged before it is made
        for text in (r.stdout, r.stderr, log):
            self.assertNotIn(TOKEN, text)


class TestIsolation(unittest.TestCase):
    def test_citelookup_imports_no_network_module(self):
        banned = {"socket", "urllib", "http", "requests", "httpx", "ssl", "ftplib", "smtplib",
                  "telnetlib", "xmlrpc", "asyncio"}
        tree = ast.parse(open(os.path.join(KIT, "matterkit", "citelookup.py")).read())
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for n in names:
                self.assertNotIn(n.split(".")[0], banned)
                self.assertNotIn("adapters", n)

    def test_mcp_server_cannot_turn_lookups_on(self):
        # The MCP server runs lookups (RFC 0001 amendment 2026-11-06) but never writes a grant or a key.
        src = open(os.path.join(KIT, "matter-mcp.py")).read()
        for name in ("turn_on", "write_human_grant", "activate_human_grant", "save_grants",
                     "save_courtlistener_token", "courtlistener_citations"):
            self.assertNotIn(name, src)


if __name__ == "__main__":
    unittest.main()

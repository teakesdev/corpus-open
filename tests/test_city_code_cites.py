"""City code cites ("Austin City Code § 25-2-551") checked on Corpus under the city's jurisdiction code.

What could go wrong, and so what these tests pin:

- A city is guessed. A bare "§ 25-2-551" must never be sent as a city cite, whatever the case's city;
  a name several Corpus cities share ("Springfield") is not sent unless the case's city is one of them;
  a city Corpus does not list ("Carson City") is not sent at all; and a cite that names no city
  ("Municipal Code § 12.03") goes out only once a person set the case's city.
- A state cite is read as a city's: "D.C. Code", "District of Columbia Code", "Ohio Rev. Code",
  "Revised Code of Washington", "Orange County Code" (the county's, not the City of Orange's).
- More than the cite leaves the computer: the payload is the cite as one line, the jurisdiction is a
  code Corpus lists, and a city cite never goes out unscoped.
- A Corpus heading in another form ("Municipal Code of Chicago § 5-12-080") is missed, or a section with
  the same number in another of the city's codes is taken for it.
- The adapter drifts from Corpus's MCP contract: a tool error's words sit in `content`, and a search that
  did not finish says so first. Neither may read as "no such section".

Corpus is stubbed with answers shaped like real law.search results read 2026-11-22. Nothing reaches the
network.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hermes_plugin import KIT, PluginCase, corpus, guard  # noqa: E402

from fileio import read_text  # noqa: E402
from matterkit import cases, municipal, statutelookup  # noqa: E402
from matterkit.search import SearchQuery  # noqa: E402

AUSTIN = (
    "3 of 196 results for \"Austin City Code § 25-2-551\" in TX-AUSTIN (hybrid):\n\n"
    "1. Austin City Code § 25-2-551 — LAKE AUSTIN (LA) DISTRICT REGULATIONS.\n"
    "   City of Austin · Austin City Code\n"
    "   § 25-2-551 - LAKE AUSTIN (LA) DISTRICT REGULATIONS.\n"
    "   node_id: 2fae10e1-68f5-4972-8a58-bae891affab8\n\n"
    "2. Austin City Code § 30-5-261 — CRITICAL WATER QUALITY ZONE DEVELOPMENT.\n"
    "   City of Austin · Austin City Code\n"
    "   Source: City Code Section 25-8-261; Ord. 031211-11.\n"
    "   node_id: bfb209b4-7e0b-4ce3-88b6-0fbca4bdeb96")
CHICAGO = (
    "1 of 1 results for \"Chicago Municipal Code § 5-12-080(f)\" in IL-CHICAGO (hybrid):\n\n"
    "1. Municipal Code of Chicago § 5-12-080 — Security deposits.\n"
    "   City of Chicago · Municipal Code of Chicago\n"
    "   5-12-080 Security deposits. (a) (1) A landlord shall hold all security deposits\n"
    "   node_id: bf6bcf2b-3ca6-41ce-b8c2-fa9113789893")
SEATTLE = (
    "1 of 198 results for \"Seattle Municipal Code 22.206.160\" in WA-SEA (hybrid):\n\n"
    "1. Seattle Mun. Code ch. 22.206, subch. VI, § 22.206.160 — Duties of owners\n"
    "   Seattle · SEA-MC\n"
    "   22.206.160 - Duties of owners A. It shall be the duty of all owners\n"
    "   node_id: ba54441b-56b4-494c-84d9-086d0e3352bc")
TWO_CODES = (
    "2 of 9 results for \"Municipal Code § 12.03\" in CA-SF (hybrid):\n\n"
    "1. S.F. Planning Code § 12.03 — One.\n   San Francisco · Planning Code\n   x\n"
    "   node_id: 11111111-1111-1111-1111-111111111111\n\n"
    "2. S.F. Police Code § 12.03 — Two.\n   San Francisco · Police Code\n   y\n"
    "   node_id: 22222222-2222-2222-2222-222222222222")
PARTIAL = (
    "NOTE: these results are PARTIAL — semantic (vector) matching never ran because the query-embedding "
    "service was unavailable, so only keyword matches are included below. Treat these results as "
    "incomplete, and say so if you rely on them.\n\n"
    "No results retrieved for \"Austin City Code § 25-2-551\" in TX-AUSTIN — but the search did not "
    "complete, so this is NOT evidence that no such law exists.")


class Recorder:
    """A stand-in for CorpusSearchAdapter._rpc: records each tools/call and answers from `answers`."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, adapter, payload):
        if payload["method"] == "initialize":
            return {"jsonrpc": "2.0", "id": 1, "result": {}}
        args = payload["params"]["arguments"]
        self.calls.append(args)
        text = next((t for key, t in self.answers if key in args["query"]), "No results for that.")
        # Corpus's real shape (apps/web/src/lib/mcp.ts): content blocks plus structuredContent, no "text".
        return {"jsonrpc": "2.0", "id": 2, "result": {
            "content": [{"type": "text", "text": text}],
            "structuredContent": {"text": text, "isError": False}}}


class TestFind(unittest.TestCase):
    def one(self, text):
        hits = municipal.find(text)
        self.assertEqual(len(hits), 1, (text, hits))
        return hits[0]

    def test_named_forms(self):
        for text, cite, section, code in [
            ("Under Austin City Code § 25-2-551 the lot", "Austin City Code § 25-2-551", "25-2-551", "TX-AUSTIN"),
            ("St. Louis Rev. Code 26.80.060 allows it", "St. Louis Rev. Code 26.80.060", "26.80.060", "MO-STLOUIS"),
            ("See Municipal Code of Chicago § 5-12-080.", "Municipal Code of Chicago § 5-12-080", "5-12-080",
             "IL-CHICAGO"),
            ("Seattle Municipal Code 22.206.160", "Seattle Municipal Code 22.206.160", "22.206.160", "WA-SEA"),
            ("N.Y.C. Admin. Code § 27-2004", "N.Y.C. Admin. Code § 27-2004", "27-2004", "NY-NEWYORK"),
            ("S.F. Admin. Code § 37.9", "S.F. Admin. Code § 37.9", "37.9", "CA-SF"),
            ("Sec. 10-1 of the Houston City Code", "Sec. 10-1 of the Houston City Code", "10-1", "TX-HOUSTON"),
            ("Code of Ordinances of the City of Houston, Sec. 10-1",
             "Code of Ordinances of the City of Houston, Sec. 10-1", "10-1", "TX-HOUSTON"),
            ("Montgomery County Code § 29-27", "Montgomery County Code § 29-27", "29-27", "MD-MONTGOMERY"),
            ("Revised Ordinances of Honolulu § 21-5.1", "Revised Ordinances of Honolulu § 21-5.1", "21-5.1",
             "HI-HONOLULU"),
        ]:
            c = self.one(text)
            self.assertEqual((c.text, c.section), (cite, section), text)
            self.assertEqual(municipal.resolve(c, None), (code, ""), text)

    def test_subsection_is_kept_apart(self):
        c = self.one("Chicago Municipal Code § 5-12-080(f)")
        self.assertEqual((c.section, c.subsection), ("5-12-080", "(f)"))

    def test_state_and_county_cites_are_not_city_cites(self):
        for text in ("D.C. Code § 42-3505.01", "District of Columbia Code § 42-3505", "Ohio Rev. Code § 2305.10",
                     "Revised Code of Washington 59.18.230", "Orange County Code § 5-1",
                     "Tex. Prop. Code § 92.0081", "Wash. Rev. Code § 4.16.080", "§ 25-2-551",
                     "Austin City Code 25"):
            self.assertEqual(municipal.find(text), [], text)

    def test_unnamed_code(self):
        c = self.one("Under Municipal Code § 12.03 a permit is needed.")
        self.assertIsNone(c.name)
        self.assertEqual(municipal.resolve(c, None)[0], None)
        self.assertEqual(municipal.resolve(c, "CA-SF"), ("CA-SF", ""))

    def test_sentence_end_before_unnamed_code_is_not_a_name(self):
        c = self.one("He paid rent to Smith. Municipal Code § 12.03 governs.")
        self.assertIsNone(c.name)

    def test_unknown_city_is_never_sent(self):
        c = self.one("Carson City Code § 4-1")
        self.assertFalse(c.known)
        self.assertEqual(municipal.resolve(c, "TX-AUSTIN")[0], None)
        with self.assertRaises(ValueError):
            statutelookup.payload("Carson City Code § 4-1")

    def test_shared_name_needs_the_case_city(self):
        c = self.one("Springfield City Code § 2-1")
        code, why = municipal.resolve(c, None)
        self.assertIsNone(code)
        self.assertIn("IL-SPRINGFIELD", why)
        self.assertEqual(municipal.resolve(c, "MO-SPRINGFIELD")[0], "MO-SPRINGFIELD")
        # A case city that is not one of them does not choose.
        self.assertIsNone(municipal.resolve(c, "TX-AUSTIN")[0])

    def test_a_named_city_wins_over_the_case_city(self):
        c = self.one("Austin City Code § 25-2-551")
        self.assertEqual(municipal.resolve(c, "IL-CHICAGO")[0], "TX-AUSTIN")


class TestPayload(unittest.TestCase):
    def test_city_cite_is_sent_as_written_on_one_line(self):
        self.assertEqual(statutelookup.payload("Austin  City Code §  25-2-551"), "Austin City Code § 25-2-551")
        self.assertEqual(statutelookup.payload("St. Louis Rev. Code 26.80.060 (2020)"),
                         "St. Louis Rev. Code 26.80.060")
        self.assertEqual(statutelookup.payload("Chicago Municipal Code § 5-12-080(f)"),
                         "Chicago Municipal Code § 5-12-080(f)")

    def test_more_than_the_cite_is_refused(self):
        for text in ("Austin City Code § 25-2-551 says my landlord must fix it",
                     "my landlord on Lake Austin, Austin City Code § 25-2-551",
                     "Austin City Code § 25-2-551; Austin City Code § 25-2-552"):
            with self.assertRaises(ValueError, msg=text):
                statutelookup.payload(text)

    def test_jurisdiction_is_a_code_corpus_lists(self):
        self.assertEqual(statutelookup.jurisdiction("TX-AUSTIN"), "TX-AUSTIN")
        self.assertEqual(statutelookup.jurisdiction("MS"), "MS")
        for bad in ("TX-NOWHERE", "tx-austin", "TX-AUSTIN; drop", "Austin"):
            with self.assertRaises(ValueError, msg=bad):
                statutelookup.jurisdiction(bad)


class AdapterCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "case")
        with redirect_stdout(io.StringIO()):
            cases.init(self.dir)
        p = mock.patch("urllib.request.urlopen", side_effect=AssertionError("a test reached the network"))
        p.start()
        self.addCleanup(p.stop)

    def adapter(self, answers):
        rec = Recorder(answers)
        p = mock.patch.object(corpus.CorpusSearchAdapter, "_rpc", lambda adapter, payload: rec(adapter, payload))
        p.start()
        self.addCleanup(p.stop)
        return corpus.CorpusStatuteLookupAdapter(self.dir), rec


class TestAdapter(AdapterCase):
    def test_sends_only_the_cite_and_the_city_code(self):
        a, rec = self.adapter([("25-2-551", AUSTIN)])
        hits = a.search(SearchQuery(text="Austin City Code § 25-2-551", jurisdiction="TX-AUSTIN"), "hermes")
        self.assertEqual(rec.calls, [{"query": "Austin City Code § 25-2-551", "limit": statutelookup.LIMIT,
                                      "jurisdiction": "TX-AUSTIN"}])
        self.assertEqual(hits[0].provenance["where"], "City of Austin · Austin City Code")
        with open(os.path.join(self.dir, ".matter", "egress.jsonl"), encoding="utf-8") as f:
            logged = f.read()
        self.assertNotIn("25-2-551", logged)  # hash only

    def test_city_cite_never_goes_out_unscoped(self):
        a, rec = self.adapter([])
        for juris in (None, "TX"):
            with self.assertRaises(ValueError):
                a.search(SearchQuery(text="Municipal Code § 12.03", jurisdiction=juris), "hermes")
        self.assertEqual(rec.calls, [])

    def test_tool_error_words_come_from_content(self):
        a, _ = self.adapter([])

        def err(adapter, payload):
            if payload["method"] == "initialize":
                return {"result": {}}
            msg = 'Unknown jurisdiction "zz". Call law.list_coverage to see valid short codes.'
            return {"result": {"content": [{"type": "text", "text": msg}], "isError": True,
                               "structuredContent": {"text": msg, "isError": True}}}
        with mock.patch.object(corpus.CorpusSearchAdapter, "_rpc", err):
            with self.assertRaises(corpus.MCPRpcError) as cm:
                a.search(SearchQuery(text="Austin City Code § 25-2-551", jurisdiction="TX-AUSTIN"), "hermes")
        self.assertIn("Unknown jurisdiction", str(cm.exception))

    def test_partial_answer_is_marked(self):
        a, _ = self.adapter([("25-2-551", PARTIAL)])
        self.assertEqual(a.search(SearchQuery(text="Austin City Code § 25-2-551", jurisdiction="TX-AUSTIN"),
                                  "hermes"), [])
        self.assertTrue(a.last_partial)
        a2, _ = self.adapter([("25-2-551", AUSTIN)])
        a2.search(SearchQuery(text="Austin City Code § 25-2-551", jurisdiction="TX-AUSTIN"), "hermes")
        self.assertFalse(a2.last_partial)


class TestGuard(PluginCase):
    def answer(self, answers):
        rec = Recorder(answers)
        p = mock.patch.object(corpus.CorpusSearchAdapter, "_rpc", lambda adapter, payload: rec(adapter, payload))
        p.start()
        self.addCleanup(p.stop)
        return rec

    def check(self, text):
        return guard.check_text(text, matter_dir=self.dir)

    def test_named_city_cite_is_in_corpus(self):
        self.grant_all()
        rec = self.answer([("25-2-551", AUSTIN)])
        res = self.check("Under Austin City Code § 25-2-551 the dock is allowed.")
        self.assertEqual([(r.cite, r.outcome) for r in res.rows], [("Austin City Code § 25-2-551", "IN CORPUS")])
        self.assertIn("City of Austin (TX-AUSTIN)", res.rows[0].detail)
        self.assertEqual(rec.calls[0]["jurisdiction"], "TX-AUSTIN")
        self.assertEqual(len(rec.calls), 1)  # not also "§ 25-2-551" on its own

    def test_heading_in_another_form_matches_by_number(self):
        self.grant_all()
        self.answer([("5-12-080", CHICAGO), ("22.206.160", SEATTLE)])
        res = self.check("Chicago Municipal Code § 5-12-080(f) and Seattle Municipal Code 22.206.160 apply.")
        self.assertEqual([r.outcome for r in res.rows], ["IN CORPUS", "IN CORPUS"])
        self.assertIn("Subsection (f) not checked", res.rows[0].detail)

    def test_other_section_is_not_matched(self):
        self.grant_all()
        self.answer([("25-2-552", AUSTIN)])
        res = self.check("Austin City Code § 25-2-552 applies.")
        self.assertEqual(res.rows[0].outcome, "NOT MATCHED IN CORPUS")

    def test_unnamed_code_waits_for_the_case_city(self):
        self.grant_all()
        rec = self.answer([("12.03", TWO_CODES)])
        res = self.check("Municipal Code § 12.03 requires a permit.")
        self.assertEqual(res.rows[0].outcome, "NOT LOOKED UP")
        self.assertIn("no city", res.rows[0].detail)
        self.assertEqual(rec.calls, [])
        municipal.set_case_city(self.dir, "CA-SF", "ty", "test")
        res = self.check("Municipal Code § 12.03 requires a permit.")
        self.assertEqual(rec.calls, [{"query": "Municipal Code § 12.03", "limit": statutelookup.LIMIT,
                                      "jurisdiction": "CA-SF"}])
        # Two of the city's codes have a § 12.03: we cannot tell which one the cite means.
        self.assertEqual(res.rows[0].outcome, "NOT MATCHED IN CORPUS")
        self.assertIn("more than one code", res.rows[0].detail)

    def test_bare_section_is_not_a_city_cite_even_with_a_case_city(self):
        self.grant_all()
        municipal.set_case_city(self.dir, "TX-AUSTIN", "ty", "test")
        rec = self.answer([("25-2-551", AUSTIN)])
        self.check("Section § 25-2-551 says so.")
        self.assertTrue(all("jurisdiction" not in c for c in rec.calls), rec.calls)

    def test_shared_name_and_unknown_city_send_nothing(self):
        self.grant_all()
        rec = self.answer([])
        res = self.check("Springfield City Code § 2-1 and Carson City Code § 4-1 apply.")
        self.assertEqual([r.outcome for r in res.rows], ["NOT LOOKED UP", "NOT LOOKED UP"])
        self.assertIn("more than one Springfield", res.rows[0].detail)
        self.assertIn("Carson", res.rows[1].detail)
        self.assertEqual(rec.calls, [])

    def test_search_that_did_not_finish_is_not_a_miss(self):
        self.grant_all()
        self.answer([("25-2-551", PARTIAL), ("89-7-27", PARTIAL)])
        res = self.check("Austin City Code § 25-2-551 and Miss. Code Ann. § 89-7-27.")
        self.assertEqual([r.outcome for r in res.rows], ["NOT LOOKED UP", "NOT LOOKED UP"])
        self.assertTrue(all("did not finish" in r.detail for r in res.rows))

    def test_state_cites_still_go_as_before(self):
        self.grant_all()
        res = guard.check_text("Under Miss. Code Ann. § 89-7-27 the landlord files.", matter_dir=self.dir)
        self.assertEqual(res.rows[0].outcome, "IN CORPUS")

    def test_matter_city_command(self):
        p = self.plugin()
        cmd = self.ctx.commands["matter"]
        self.assertIn("no city set", cmd("city"))
        self.assertIn("City of Austin (TX-AUSTIN)", cmd("city tx-austin"))
        self.assertEqual(municipal.case_city(self.dir), "TX-AUSTIN")
        self.assertIn("not a Corpus city code", cmd("city TX-NOWHERE"))
        self.assertIn("City for city code cites: City of Austin (TX-AUSTIN)", cmd("grant"))
        self.assertIn("no city now", cmd("city clear"))
        self.assertIsNone(municipal.case_city(self.dir))
        self.assertIsNotNone(p)


class TestCaseCity(AdapterCase):
    def test_cli_sets_shows_and_clears(self):
        run = lambda *a: subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "city", *a],  # noqa: E731
                                        capture_output=True, text=True, timeout=60)
        self.assertIn("no city set", run(self.dir).stdout)
        self.assertIn("City of St. Louis (MO-STLOUIS)", run(self.dir, "mo-stlouis").stdout)
        self.assertEqual(municipal.case_city(self.dir), "MO-STLOUIS")
        bad = run(self.dir, "MO-NOWHERE")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("refused", bad.stderr)
        self.assertEqual(municipal.case_city(self.dir), "MO-STLOUIS")
        self.assertIn("no city now", run(self.dir, "--clear").stdout)
        self.assertIsNone(municipal.case_city(self.dir))
        self.assertIn("TX-AUSTIN", run("--list", "austin").stdout)

    def test_a_linked_city_file_is_ignored_and_not_written_through(self):
        target = os.path.join(self._tmp.name, "elsewhere.json")
        with open(target, "w", encoding="utf-8") as f:
            json.dump({"jurisdiction": "TX-AUSTIN"}, f)
        os.symlink(target, os.path.join(self.dir, ".matter", municipal.CITY_FILE))
        self.assertIsNone(municipal.case_city(self.dir))
        with self.assertRaises(ValueError):
            municipal.set_case_city(self.dir, "IL-CHICAGO", "ty", "test")

    def test_not_a_case_folder(self):
        with self.assertRaises(ValueError):
            municipal.set_case_city(self._tmp.name, "TX-AUSTIN", "ty", "test")


class TestSnapshot(unittest.TestCase):
    def test_snapshot_is_well_formed(self):
        self.assertGreaterEqual(len(municipal.CITIES), 250)
        self.assertTrue(municipal.READ_AT)
        for code, c in municipal.CITIES.items():
            self.assertRegex(code, municipal.CODE_RE.pattern)
            self.assertIn(c["level"], ("municipal", "county"))
            self.assertTrue(municipal.names_for(code, c["name"]), code)
        self.assertNotIn("MS-OCEANSPRINGS", municipal.CITIES)  # a placeholder: listed, not searchable
        self.assertNotIn("new york", municipal.BY_NAME)  # the state's name is not the city's

    def test_parse_coverage_drops_states_and_placeholders(self):
        text = ("- TX — Texas (state): 1 provisions\n"
                "- TX-AUSTIN — City of Austin (municipal): 4,912 provisions, 7,807 searchable chunks\n"
                "- MD-MONTGOMERY — Montgomery County (county): 3,227 provisions\n"
                "- MS-WESTPOINT — City of West Point (municipal): PLACEHOLDER — listed but not yet searchable\n")
        self.assertEqual([r["code"] for r in municipal.parse_coverage(text)], ["TX-AUSTIN", "MD-MONTGOMERY"])

    def test_municipal_module_makes_no_network_calls(self):
        import ast
        banned = {"socket", "urllib", "http", "requests", "httpx", "ssl", "asyncio"}
        tree = ast.parse(read_text(os.path.join(KIT, "matterkit", "municipal.py")))
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for n in names:
                self.assertNotIn(n.split(".")[0], banned)


if __name__ == "__main__":
    unittest.main()

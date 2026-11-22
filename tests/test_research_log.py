"""The case law research log: searches logged by hand beside the kit's CourtListener ones, leads to
revisit, and where else to look from a dated survey. Through the module, `matter.py research` and MCP.

Synthetic data only; no request leaves the machine (the MCP child runs with sockets denied).
Run: python3 -m unittest tests.test_research_log -v
"""
import contextlib
import datetime
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matterkit import research_log as rl, saved_lookups  # noqa: E402

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETBLOCK = os.path.join(KIT, "tests", "netblock_sitecustomize")
CHECKED = "2026-11-07"

# A small survey of an invented state, ZZ, in the packaged file's shape.
COLUMNS = ["jurisdiction", "court", "level", "source", "year_from", "year_to", "fulltext", "status", "basis",
           "url", "note", "checked_at", "facts", "cl"]
SNAPSHOT = {"date": "2026-09-30", "source": "CourtListener bulk data, Public Domain Mark"}
FIXTURE = {
    "schema_version": 2, "checked_at": CHECKED, "source_files": "test", "legend": {"CAP": "Harvard bulk"},
    "cl_snapshot": SNAPSHOT, "gap_flags": {"ZZ": ["needs-script"]},
    "sources": [{"id": i, "name": n, "url": u, "access_method": "web", "auth": "none", "cost": "free",
                 "terms_url": None, "commercial_use_allowed": "unclear"} for i, n, u in [
        ("official_ZZ_courts", "Zed judiciary", "https://courts.zz.example/"),
        ("courtlistener_bulk", "CourtListener (free case law search)", "https://www.courtlistener.com/"),
        ("google_scholar_case_law", "Google Scholar - case law", "https://scholar.google.com/"),
        ("cap_static", "Harvard Caselaw Access Project", "https://static.case.law/")]],
    "columns": COLUMNS,
    "rows": [
        ["ZZ", "Zed Supreme Court", "supreme", "official_ZZ_courts", 1995, None, "y", "accessible", "observed",
         "https://courts.zz.example/supreme", "Opinions by year.", CHECKED],
        ["ZZ", "Zed Court of Appeals", "appellate", "official_ZZ_courts", 2010, None, "n", "blocked", "inferred",
         None, "Probe blocked.", CHECKED],
        ["ZZ", "Zed Supreme Court", "supreme", "courtlistener_bulk", None, None, "y", "accessible", "unknown",
         "https://www.courtlistener.com/?q=&type=o&court=zz", "", CHECKED],
        ["ZZ", "Old Zed Court", "appellate", "courtlistener_bulk", None, 1969, "y", "unknown", "unknown",
         None, "ended", CHECKED],
        ["ZZ", "Zed - state courts (published)", "other", "google_scholar_case_law", 1950, None, "y", "accessible",
         "observed", None, "Web only.", CHECKED],
        ["ZZ", "Zed - all reported decisions", "other", "cap_static", 1800, 2005, "n", "accessible", "observed",
         None, "Ends 2005.", CHECKED],
        ["YY", "Why Court", "supreme", "official_ZZ_courts", 2001, None, "y", "accessible", "observed", None, "", CHECKED],
        # XX: CourtListener rows measured from its bulk files. The first had a mistyped 1665 opinion, before both the
        # court's start (1858) and the Caselaw Access Project's (1830), so raised to 1830; the second holds nothing in the
        # files; the third has an earliest date in year 19 and no project year to raise it to: "earliest year not found".
        ["XX", "Ex Supreme Court", "supreme", "courtlistener_bulk", 1830, 2026, "y", "accessible", "observed",
         "https://www.courtlistener.com/?q=&type=o&court=xx",
         "CourtListener collects this court's new opinions automatically; its bulk files (2026-09-30) hold 43,427 opinions",
         CHECKED, None, {"id": "xx", "since": 1858, "ended": None, "count": 43427, "approximate": 120,
                         "earliest": "1665-01-02", "latest": "2026-09-02",
                         "clamp": {"from": 1665, "to": 1830, "floor": "cap-start"}}],
        ["XX", "Ex Old Court", "other", "courtlistener_bulk", None, 1900, "y", "accessible", "observed",
         "https://www.courtlistener.com/?q=&type=o&court=xxold", "collected historically; not currently scraped", CHECKED,
         None, {"id": "xxold", "since": None, "ended": None, "count": 7, "approximate": 0, "earliest": "0019-01-31",
                "latest": "1900-05-05", "clamp": {"from": 19, "to": None, "floor": "typo"}}],
        ["XX", "Ex Court of Appeals", "appellate", "courtlistener_bulk", None, None, "y", "unknown", "unknown",
         "https://www.courtlistener.com/?q=&type=o&court=xxa", "collected historically; not currently scraped", CHECKED,
         ["cl-not-in-use"], {"id": "xxa", "since": None, "ended": None, "count": 0, "approximate": 0, "earliest": None,
                             "latest": None}],
    ],
    "gaps": {"ZZ": ["Official site covers 1995 on; nothing earlier."]},
    "general_gaps": ["Trial courts: nothing free anywhere."],
}


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "case")
        os.makedirs(os.path.join(self.dir, ".matter"))
        self.log = os.path.join(self.dir, ".matter", "caselaw", "research.jsonl")
        self.data = os.path.join(self._tmp.name, "coverage.json")
        with open(self.data, "w") as f:
            json.dump(FIXTURE, f)
        self.survey = rl.load_coverage(self.data)

    def lines(self):
        try:
            with open(self.log) as f:
                return f.read().splitlines()
        except OSError:
            return []

    def search(self, **kw):
        args = dict(by="Pat", source="google scholar", query="docket control", outcome="nothing")
        args.update(kw)
        return rl.log_search(self.dir, **args)

    def lead(self, **kw):
        args = dict(by="Pat", title="Smith v. Jones", why="treats a missed order as waiver")
        args.update(kw)
        return rl.save_lead(self.dir, **args)


class TestSearches(Case):
    def test_log_search_appends_one_record_with_id_time_and_author(self):
        r = self.search(jurisdiction="zz", court="Zed Supreme Court", years="1990 - 2005", url="https://x.example/a",
                        note="  tried twice  ")
        self.assertRegex(r["id"], r"^rs_[0-9a-f]{8}$")
        self.assertEqual((r["kind"], r["by"], r["jurisdiction"], r["years"], r["note"]),
                         ("search", "Pat", "ZZ", "1990-2005", "tried twice"))
        self.assertRegex(r["recorded_at"], r"^\d{4}-\d\d-\d\dT")
        self.assertEqual([json.loads(x) for x in self.lines()], [r])

    def test_any_nonempty_source_is_accepted(self):
        for source in ("the county law library", "courtlistener_bulk", "Westlaw at the library"):
            self.assertEqual(self.search(source=source)["source"], source)

    def test_refusals_write_nothing(self):
        bad = [dict(source=""), dict(source="  "), dict(query=""), dict(outcome="maybe"), dict(outcome=""),
               dict(years="05-06"), dict(years="2005-1990"), dict(years="1990-"), dict(years="1066"),
               dict(jurisdiction="Maryland state"), dict(jurisdiction="1"), dict(url="javascript:alert(1)"),
               dict(url="example.org/x"), dict(by="")]
        for kw in bad:
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.search(**kw)
        self.assertFalse(os.path.exists(self.log))
        self.assertFalse(os.path.exists(os.path.dirname(self.log)))

    def test_a_folder_that_is_not_a_case_is_refused_and_not_created_into(self):
        elsewhere = os.path.join(self._tmp.name, "not-a-case")
        os.makedirs(elsewhere)
        with self.assertRaisesRegex(ValueError, "not a case folder"):
            rl.log_search(elsewhere, "Pat", "s", "q", "found")
        self.assertEqual(os.listdir(elsewhere), [])

    def test_every_outcome_is_accepted(self):
        for outcome in rl.OUTCOMES:
            self.assertEqual(self.search(outcome=outcome)["outcome"], outcome)

    def test_list_merges_the_kits_courtlistener_searches(self):
        with mock.patch.object(saved_lookups, "now_iso", return_value="2026-01-01T00:00:00+00:00"):
            saved_lookups.save_search(self.dir, "courtlistener-free", "plausibility pleading", 5, None,
                                      [{"caseName": "A v. B"}, {"caseName": "C v. D"}])
            saved_lookups.save_search(self.dir, "courtlistener-free", "no such thing", 5, None, [])
        manual = self.search(source="google scholar", query="docket control", outcome="blocked", jurisdiction="OH")
        rows = rl.list_searches(self.dir)
        self.assertEqual([r["query"] for r in rows], ["plausibility pleading", "no such thing", "docket control"])
        kit, empty, mine = rows
        self.assertEqual((kit["source"], kit["outcome"], kit["origin"], kit["by"]), ("courtlistener", "found", "kit", "kit"))
        self.assertEqual(empty["outcome"], "nothing")
        self.assertIn("2 results", kit["note"])
        self.assertEqual((mine["id"], mine["origin"]), (manual["id"], "log"))
        self.assertEqual(set(kit), set(mine) - {"origin"} | {"origin"})  # the same shape
        with open(os.path.join(self.dir, ".matter", "caselaw", "searches.jsonl")) as f:
            self.assertEqual(len(f.read().splitlines()), 2)  # the kit's own file is only read

    def test_list_filters(self):
        saved_lookups.save_search(self.dir, "courtlistener-free", "kit search", 5, "MD", [{"x": 1}])
        saved_lookups.save_search(self.dir, "courtlistener-free", "unscoped", 5, None, [])
        self.search(outcome="blocked", jurisdiction="OH", query="a")
        self.search(outcome="found", jurisdiction="MD", query="b")
        self.assertEqual([r["query"] for r in rl.list_searches(self.dir, outcome="blocked")], ["a"])
        self.assertEqual(sorted(r["query"] for r in rl.list_searches(self.dir, jurisdiction="md")), ["b", "kit search"])
        self.assertEqual(rl.list_searches(self.dir, outcome="error"), [])
        with self.assertRaises(ValueError):
            rl.list_searches(self.dir, outcome="bogus")

    def test_list_with_nothing_logged_is_empty_and_creates_nothing(self):
        self.assertEqual(rl.list_searches(self.dir), [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".matter", "caselaw")))


class TestLeads(Case):
    def test_save_lead_needs_a_reason(self):
        r = self.lead(revisit="2026-12-01", citation=" 1  U.S. 1 ", url="https://x.example/c", jurisdiction="us")
        self.assertRegex(r["id"], r"^rl_[0-9a-f]{8}$")
        self.assertEqual((r["status"], r["revisit"], r["citation"], r["jurisdiction"]), ("saved", "2026-12-01", "1 U.S. 1", "US"))
        for kw in (dict(why=""), dict(why="   "), dict(title=" "), dict(revisit="12/01/2026"), dict(revisit="2026-02-30"),
                   dict(revisit="2026-13-01"), dict(url="ftp://x"), dict(by="")):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.lead(**kw)
        self.assertEqual(len(self.lines()), 1)

    def test_latest_record_wins_and_the_old_one_stays_in_the_file(self):
        a, b = self.lead(title="A"), self.lead(title="B")
        new = rl.update_lead(self.dir, "Sam", a["id"], status="revisited", note="read it; still good")
        self.assertEqual((new["status"], new["by"], new["created_by"], new["created_at"]),
                         ("revisited", "Sam", "Pat", a["created_at"]))
        self.assertEqual([r["id"] for r in rl.leads(self.dir)], [b["id"]])
        self.assertEqual([r["note"] for r in rl.leads(self.dir, "revisited")], ["read it; still good"])
        self.assertEqual(sorted(r["id"] for r in rl.leads(self.dir, "all")), sorted([a["id"], b["id"]]))
        rl.update_lead(self.dir, "Sam", a["id"], status="saved", revisit="2027-01-01")  # reopen with a date
        again = {r["id"]: r for r in rl.leads(self.dir)}
        self.assertEqual(again[a["id"]]["revisit"], "2027-01-01")
        self.assertEqual(again[a["id"]]["note"], "read it; still good")  # carried until replaced
        self.assertEqual(len(self.lines()), 4)
        self.assertEqual([json.loads(x)["id"] for x in self.lines()].count(a["id"]), 3)

    def test_update_refusals_write_nothing(self):
        a = self.lead(revisit="2026-12-01")
        s = self.search()
        before = self.lines()
        for kw in (dict(lead_id="rl_deadbeef", status="dropped"), dict(lead_id=s["id"], status="dropped"),
                   dict(lead_id=a["id"], status="finished"), dict(lead_id=a["id"], revisit="soon"),
                   dict(lead_id=a["id"]), dict(lead_id=a["id"], status="saved"),
                   dict(lead_id=a["id"], revisit="2026-12-01"), dict(lead_id=a["id"], status="used", by="")):
            kw.setdefault("by", "Pat")
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                rl.update_lead(self.dir, **kw)
        self.assertEqual(self.lines(), before)

    def test_leads_come_soonest_revisit_first_undated_last(self):
        self.lead(title="late", revisit="2027-01-01")
        self.lead(title="none")
        self.lead(title="soon", revisit="2026-11-01")
        self.assertEqual([r["title"] for r in rl.leads(self.dir)], ["soon", "late", "none"])
        with self.assertRaises(ValueError):
            rl.leads(self.dir, "pending")

    def test_due_counts_dated_open_leads_and_lists_undated_apart(self):
        old = self.lead(title="old", revisit="2026-11-01")
        today = self.lead(title="today", revisit="2026-11-07")
        self.lead(title="tomorrow", revisit="2026-11-08")
        self.lead(title="undated")
        done = self.lead(title="done", revisit="2026-10-01")
        rl.update_lead(self.dir, "Pat", done["id"], status="used")
        res = rl.due(self.dir, "2026-11-07")
        self.assertEqual(res["as_of"], "2026-11-07")
        self.assertEqual([(r["title"], r["days_over"]) for r in res["due"]], [("old", 6), ("today", 0)])
        self.assertEqual([r["title"] for r in res["undated"]], ["undated"])
        self.assertEqual([r["id"] for r in res["due"]], [old["id"], today["id"]])
        self.assertEqual([r["title"] for r in rl.due(self.dir, datetime.date(2026, 11, 8))["due"]], ["old", "today", "tomorrow"])
        self.assertEqual(rl.due(self.dir)["as_of"], datetime.date.today().isoformat())
        with self.assertRaises(ValueError):
            rl.due(self.dir, "next week")


class TestTornFile(Case):
    def test_a_line_cut_short_is_skipped_and_the_next_write_starts_a_new_line(self):
        a = self.search(query="first")
        with open(self.log, "ab") as f:
            f.write(b'{"id": "rs_torn", "kind": "search", "que')  # a crash mid-write: no newline
        self.assertEqual([r["query"] for r in rl.list_searches(self.dir)], ["first"])
        b = self.search(query="second")
        self.assertEqual([r["query"] for r in rl.list_searches(self.dir)], ["first", "second"])
        self.assertEqual(json.loads(self.lines()[-1])["id"], b["id"])
        self.assertEqual(json.loads(self.lines()[0])["id"], a["id"])

    def test_a_line_cut_inside_a_character_does_not_stop_reading(self):
        self.search(query="first")
        with open(self.log, "ab") as f:
            f.write('{"query": "caf'.encode() + "é".encode()[:1])
        self.assertEqual([r["query"] for r in rl.list_searches(self.dir)], ["first"])
        self.lead()
        self.assertEqual(len(rl.leads(self.dir)), 1)

    def test_junk_lines_and_non_objects_are_skipped(self):
        os.makedirs(os.path.dirname(self.log))
        with open(self.log, "w") as f:
            f.write('not json\n[1, 2]\n{"kind": "lead"}\n{"id": 5, "kind": "lead"}\n{"id": "x", "kind": "other"}\n')
        self.assertEqual((rl.list_searches(self.dir), rl.leads(self.dir, "all")), ([], []))

    def test_a_torn_kit_file_does_not_stop_the_list(self):
        saved_lookups.save_search(self.dir, "courtlistener-free", "kept", 5, None, [{"x": 1}])
        with open(os.path.join(self.dir, ".matter", "caselaw", "searches.jsonl"), "ab") as f:
            f.write(b'{"query": "cut')
        self.assertEqual([r["query"] for r in rl.list_searches(self.dir)], ["kept"])


class TestWhere(Case):
    def where(self, *a, **kw):
        return rl.where(*a, data=self.survey, **kw)

    def names(self, res):
        return [(r["court"], r["source_id"]) for r in res["rows"]]

    def test_rows_carry_the_fields_and_the_surveys_date(self):
        res = self.where("zz")
        self.assertEqual((res["jurisdiction"], res["checked_at"], res["total"]), ("ZZ", CHECKED, 6))
        self.assertIn(CHECKED, res["note"])
        top = res["rows"][0]
        self.assertEqual(top["source"], "Zed judiciary")
        self.assertEqual({k: top[k] for k in ("url", "court", "level", "year_from", "year_to", "fulltext", "status", "basis",
                                              "checked_at", "note", "covers")},
                         {"url": "https://courts.zz.example/supreme", "court": "Zed Supreme Court", "level": "supreme",
                          "year_from": 1995, "year_to": None, "fulltext": "y", "status": "accessible", "basis": "observed",
                          "checked_at": CHECKED, "note": "Opinions by year.", "covers": None})
        self.assertEqual(res["gaps"], ["Official site covers 1995 on; nothing earlier."])
        by_court = {r["court"]: r for r in res["rows"]}
        self.assertEqual(by_court["Zed Court of Appeals"]["url"], "https://courts.zz.example/")  # the source's own page

    def test_only_that_jurisdictions_rows(self):
        self.assertEqual({r["court"] for r in self.where("YY")["rows"]}, {"Why Court"})
        with self.assertRaisesRegex(ValueError, "nothing for 'QQ'"):
            self.where("QQ")

    def test_level_filter_keeps_the_broad_sources_except_for_trial(self):
        sup = self.names(self.where("ZZ", court_level="supreme"))
        self.assertIn(("Zed Supreme Court", "official_ZZ_courts"), sup)
        self.assertIn(("Zed Supreme Court", "courtlistener_bulk"), sup)
        self.assertIn(("Zed - state courts (published)", "google_scholar_case_law"), sup)
        self.assertIn(("Zed - all reported decisions", "cap_static"), sup)
        self.assertNotIn(("Zed Court of Appeals", "official_ZZ_courts"), sup)
        trial = self.where("ZZ", court_level="trial")
        self.assertEqual((trial["rows"], trial["total"]), ([], 0))
        self.assertEqual(trial["general_gaps"], ["Trial courts: nothing free anywhere."])
        with self.assertRaises(ValueError):
            self.where("ZZ", court_level="county")

    def test_court_words_filter(self):
        res = self.where("ZZ", court="appeals")
        self.assertEqual([r["court"] for r in res["rows"] if r["source_id"] not in rl.BROAD_SOURCES], ["Zed Court of Appeals"])
        self.assertEqual(len(res["rows"]), 3)  # that court, and the two sources that cover every court

    def test_year_marks_each_place_and_puts_covering_ones_first(self):
        res = self.where("ZZ", year=2003)
        marks = {(r["court"], r["source_id"]): r["covers"] for r in res["rows"]}
        self.assertEqual(marks[("Zed Supreme Court", "official_ZZ_courts")], "covers")
        self.assertEqual(marks[("Zed - state courts (published)", "google_scholar_case_law")], "covers")
        self.assertEqual(marks[("Zed - all reported decisions", "cap_static")], "covers")
        self.assertEqual(marks[("Zed Court of Appeals", "official_ZZ_courts")], "does-not-cover")  # starts 2010
        self.assertEqual(marks[("Old Zed Court", "courtlistener_bulk")], "does-not-cover")  # ended 1969
        self.assertEqual(marks[("Zed Supreme Court", "courtlistener_bulk")], "unknown")  # no years found
        order = [r["covers"] for r in res["rows"]]
        self.assertEqual(order, sorted(order, key=["covers", "partly", "unknown", "does-not-cover"].index))
        self.assertEqual(order[:3], ["covers"] * 3)
        self.assertEqual(res["year"], "2003")

    def test_year_edges_and_ranges(self):
        def marks(year):
            return {(r["court"], r["source_id"]): r["covers"] for r in self.where("ZZ", year=year)["rows"]}
        self.assertEqual(marks(2005)[("Zed - all reported decisions", "cap_static")], "covers")
        self.assertEqual(marks(2006)[("Zed - all reported decisions", "cap_static")], "does-not-cover")
        self.assertEqual(marks(1994)[("Zed Supreme Court", "official_ZZ_courts")], "does-not-cover")
        self.assertEqual(marks(1995)[("Zed Supreme Court", "official_ZZ_courts")], "covers")
        span = marks("1990-2000")
        self.assertEqual(span[("Zed Supreme Court", "official_ZZ_courts")], "partly")
        self.assertEqual(span[("Zed Court of Appeals", "official_ZZ_courts")], "does-not-cover")
        self.assertEqual(span[("Zed - state courts (published)", "google_scholar_case_law")], "covers")
        self.assertEqual(marks("2000-2010")[("Zed - all reported decisions", "cap_static")], "partly")
        for bad in ("20x3", "2010-2000", "1500", "2003-", 2003.5):
            with self.subTest(year=bad), self.assertRaises(ValueError):
                self.where("ZZ", year=bad)

    def test_courtlistener_rows_carry_their_bulk_years_and_newest_date(self):
        res = self.where("XX")
        self.assertEqual((res["total"], res["cl_snapshot"]), (3, "2026-09-30"))
        by = {r["court"]: r for r in res["rows"]}
        sup = by["Ex Supreme Court"]
        self.assertEqual((sup["year_from"], sup["year_to"], sup["basis"]), (1830, 2026, "observed"))
        self.assertEqual({k: sup["cl"][k] for k in ("id", "since", "count", "approximate", "earliest", "latest")},
                         {"id": "xx", "since": 1858, "count": 43427, "approximate": 120, "earliest": "1665-01-02",
                          "latest": "2026-09-02"})
        self.assertEqual(sup["cl"]["clamp"], {"from": 1665, "to": 1830, "floor": "cap-start"})
        self.assertEqual(by["Ex Court of Appeals"]["facts"], ["cl-not-in-use"])
        old = by["Ex Old Court"]
        self.assertEqual((old["year_from"], old["year_to"], old["cl"]["clamp"]), (None, 1900, {"from": 19, "to": None, "floor": "typo"}))
        text = rl.render_where(res)
        self.assertIn("years 1830-2026 (observed)", text)
        self.assertIn("CourtListener holds 43,427 opinions (bulk files of 2026-09-30); newest opinion 2026-09-02; "
                      "start year raised from 1665 to 1830, the Caselaw Access Project's start", text)
        self.assertIn("CourtListener holds 7 opinions (bulk files of 2026-09-30); newest opinion 1900-05-05; "
                      "earliest year not found (its earliest date is year 19)", text)
        self.assertIn("CourtListener holds no opinions from this court (bulk files of 2026-09-30)", text)
        # a row measured from the bulk files says so once, in that line, not again in its note
        self.assertNotIn("collects this court's new opinions automatically", text)

    def test_the_raised_start_year_is_what_a_year_search_sees(self):
        def mark(year):
            return {r["court"]: r["covers"] for r in self.where("XX", year=year)["rows"]}["Ex Supreme Court"]
        self.assertEqual(mark(1700), "does-not-cover")  # the 1665 date was a typo, not coverage
        self.assertEqual(mark(1830), "covers")
        self.assertEqual(mark("1800-1900"), "partly")
        self.assertEqual(mark(2026), "covers")

    def test_rows_without_bulk_years_have_no_cl_record_and_no_cl_line(self):
        res = self.where("ZZ")
        self.assertIsNone(next(r for r in res["rows"] if r["source_id"] == "courtlistener_bulk")["cl"])
        self.assertNotIn("CourtListener holds", rl.render_where(res))
        self.assertEqual(self.survey["gap_flags"], {"ZZ": ["needs-script"]})

    def test_a_version_1_file_still_reads_and_a_newer_one_is_refused(self):
        old = {k: v for k, v in FIXTURE.items() if k not in ("cl_snapshot", "gap_flags")}
        old["schema_version"] = 1
        old["columns"] = COLUMNS[:12]
        old["rows"] = [r[:12] for r in FIXTURE["rows"]]
        path = os.path.join(self._tmp.name, "v1.json")
        with open(path, "w") as f:
            json.dump(old, f)
        data = rl.load_coverage(path)
        self.assertIsNone(data["cl_snapshot"])
        self.assertTrue(all(r["cl"] is None and r["facts"] == [] for r in data["rows"]))
        self.assertIsNone(rl.where("ZZ", data=data)["cl_snapshot"])
        new = dict(FIXTURE, schema_version=3)
        path3 = os.path.join(self._tmp.name, "v3.json")
        with open(path3, "w") as f:
            json.dump(new, f)
        with self.assertRaisesRegex(ValueError, "unknown coverage file version 3"):
            rl.load_coverage(path3)

    def test_limit_keeps_the_total(self):
        res = self.where("ZZ", limit=2)
        self.assertEqual((len(res["rows"]), res["total"]), (2, 6))

    def test_hints_leave_out_the_place_just_tried_and_places_that_cannot_hold_the_years(self):
        def hint(said, **kw):
            res = rl.hints("ZZ", said, data=self.survey, **kw)
            return {r["source_id"] for r in res["rows"]}, res
        self.assertNotIn("google_scholar_case_law", hint("Google Scholar")[0])
        self.assertNotIn("google_scholar_case_law", hint("scholar")[0])
        self.assertNotIn("courtlistener_bulk", hint("CourtListener search")[0])
        self.assertNotIn("official_ZZ_courts", hint("the court website")[0])
        self.assertNotIn("official_ZZ_courts", hint("official_ZZ_courts")[0])
        self.assertEqual(hint("law library")[0], {"official_ZZ_courts", "courtlistener_bulk", "google_scholar_case_law", "cap_static"})
        ids, res = hint("scholar", years="2003")
        self.assertNotIn("Old Zed Court", {r["court"] for r in res["rows"]})
        self.assertNotIn("Zed Court of Appeals", {r["court"] for r in res["rows"]})  # starts 2010
        self.assertEqual(len(rl.hints("ZZ", None, limit=2, data=self.survey)["rows"]), 2)
        self.assertEqual(rl.hints("ZZ", "scholar", court="no such court", data=self.survey)["total"], 5)  # court dropped, not empty
        gone = rl.hints("QQ", "scholar", data=self.survey)
        self.assertEqual(gone["rows"], [])
        self.assertIn("nothing for 'QQ'", gone["message"])

    def test_rendering_says_when_it_was_checked_and_that_blocked_may_open_in_a_browser(self):
        text = rl.render_where(self.where("ZZ", year=2003))
        self.assertIn(CHECKED, text)
        self.assertIn("[covers]", text)
        self.assertIn("<official_ZZ_courts>", text)
        self.assertIn("may still open in your own browser", text)
        self.assertIn("Official site covers 1995 on", text)
        hint = rl.render_hints(rl.hints("ZZ", "scholar", data=self.survey), "blocked")
        self.assertTrue(hint.startswith("That site blocked you."))
        self.assertIn("survey checked " + CHECKED, hint)


class TestPackagedSurvey(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(rl.DATA, encoding="utf-8") as f:
            cls.raw = f.read()
        cls.data = rl.load_coverage()

    def test_shape_and_size(self):
        raw = json.loads(self.raw)
        self.assertEqual(raw["schema_version"], 2)
        self.assertRegex(raw["checked_at"], r"^\d{4}-\d\d-\d\d$")
        self.assertEqual(raw["checked_at"], max(r[11] for r in raw["rows"]))
        self.assertLess(len(self.raw.encode()), 600_000)
        self.assertNotIn("\n  ", self.raw)  # compact
        self.assertEqual(set(raw["columns"]), set(FIXTURE["columns"]))

    def test_schema_2_names_the_bulk_snapshot_and_its_license(self):
        snap = json.loads(self.raw)["cl_snapshot"]
        self.assertEqual(set(snap), {"date", "source"})
        self.assertRegex(snap["date"], r"^\d{4}-\d\d-\d\d$")
        self.assertEqual(snap["source"], "CourtListener bulk data, Public Domain Mark")
        self.assertEqual(self.data["cl_snapshot"], snap)
        self.assertIn(snap["date"], json.loads(self.raw)["source_files"])

    def test_every_row_fact_is_in_the_legend_and_every_courtlistener_row_has_its_court_record(self):
        legend = json.loads(self.raw)["legend"]["facts"]
        seen = set()
        for r in self.data["rows"]:
            seen.update(r["facts"])
            self.assertTrue(set(r["facts"]) <= set(legend), r)
            if r["source_id"] == "courtlistener_bulk":
                cl = r["cl"]
                self.assertRegex(cl["id"], r"^[\w-]+$")
                self.assertTrue(r["url"].endswith("court=" + cl["id"]), r)
                for k in ("count", "approximate"):
                    self.assertIsInstance(cl[k], int)
            else:
                self.assertIsNone(r["cl"])
        for fact in ("robots-txt", "needs-script", "cl-not-in-use", "cl-historical", "cl-no-new-intake", "govinfo-absent",
                     "govinfo-stale", "govinfo-elective"):
            self.assertIn(fact, seen)
        self.assertTrue(all(set(v) <= {"needs-script"} for v in self.data["gap_flags"].values()))
        self.assertIn("PA", self.data["gap_flags"])

    def test_only_a_year_before_both_the_courts_start_and_the_projects_is_corrected(self):
        cap = {r["jurisdiction"]: r["year_from"] for r in self.data["rows"] if r["source_id"] == "cap_static" and r["year_from"]}
        corrected = []
        for r in self.data["rows"]:
            cl = r["cl"]
            if not cl or not cl.get("earliest"):
                continue
            j, raised = r["jurisdiction"], cl.get("clamp")
            if not raised:
                self.assertEqual(r["year_from"], int(cl["earliest"][:4]), r)  # an uncorrected year is the measured one
                continue
            corrected.append((j, cl["id"]))
            self.assertEqual(raised["from"], int(cl["earliest"][:4]))
            if raised["floor"] == "cap-start":
                self.assertEqual((r["year_from"], raised["to"]), (cap[j], cap[j]))
                self.assertLess(raised["from"], cap[j])
                if cl["since"]:
                    self.assertLess(raised["from"], cl["since"])  # before the court began as well
                self.assertIn("raised from", r["note"])
            else:  # no project year for the jurisdiction, and not a real year
                self.assertEqual(raised["floor"], "typo")
                self.assertNotIn(j, cap)
                self.assertLess(raised["from"], 1600)
                self.assertIsNone(r["year_from"])
                self.assertIsNone(raised["to"])
        self.assertGreaterEqual(len(corrected), 3)
        self.assertNotIn(("KY", "ky"), corrected)  # a predecessor court's real history stays

    def test_the_bad_early_dates_are_fixed_in_the_data_and_real_history_is_kept(self):
        def cl(j, cid):
            return next(r for r in self.data["rows"] if r["jurisdiction"] == j and r["cl"] and r["cl"]["id"] == cid)
        mn = cl("MN", "minn")  # an opinion dated 1665 in a court that began in 1858; the project starts in 1830
        self.assertEqual((mn["year_from"], mn["cl"]["earliest"], mn["cl"]["clamp"]),
                         (1830, "1665-01-02", {"from": 1665, "to": 1830, "floor": "cap-start"}))
        pa = cl("PA", "pa")
        self.assertEqual((pa["year_from"], pa["cl"]["clamp"]["from"]), (1759, 1702))
        nh = cl("NH", "nh")  # the bulk summary had lost the zeros of "0019-01-31" and read "1901-31-"
        self.assertEqual((nh["cl"]["earliest"], nh["year_from"], nh["cl"]["clamp"]["from"]), ("0019-01-31", 1796, 19))
        ky = cl("KY", "ky")  # the court began in 1976 but CourtListener holds its predecessor's opinions from 1881
        self.assertEqual((ky["year_from"], ky["cl"]["since"], "clamp" in ky["cl"]), (1881, 1976, False))
        for j, cid, year in (("FL", "fla", 1825), ("AZ", "ariz", 1817), ("SC", "sc", 1812)):
            self.assertEqual(cl(j, cid)["year_from"], year, j)
        mo = cl("MO", "mo")  # not corrected: 1852 is after the court began in 1821
        self.assertEqual((mo["year_from"], mo["year_to"], mo["cl"]["latest"], "clamp" in mo["cl"]), (1852, 2025, "2025-08-12", False))
        self.assertIn("cl-no-new-intake", mo["facts"])

    def test_every_row_names_a_listed_source_and_stays_short(self):
        sources = self.data["sources"]
        for r in self.data["rows"]:
            self.assertIn(r["source_id"], sources)
            self.assertLessEqual(len(r["note"]), 200)
            self.assertIn(r["status"], ("accessible", "partial", "blocked", "unknown"))
            self.assertIn(r["fulltext"], ("y", "n", "unknown"))
            self.assertIn(r["basis"], ("observed", "inferred", "unknown"))
            self.assertIn(r["level"], rl.LEVELS)
            self.assertTrue(r["url"] is None or r["url"].startswith(("https://", "http://")), r)
        for s in sources.values():
            self.assertEqual(set(s), {"id", "name", "url", "access_method", "auth", "cost", "terms_url", "commercial_use_allowed"})

    def test_every_state_and_dc_and_the_federal_courts_have_places_to_look(self):
        from_states = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC "
                       "ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC US").split()
        for j in from_states:
            with self.subTest(j=j):
                self.assertGreater(rl.where(j)["total"], 0)
                self.assertTrue(rl.where(j)["gaps"] or j == "US", j)

    def test_courtlistener_rows_not_scraped_are_unknown_and_say_so(self):
        cl = [r for r in self.data["rows"] if r["source_id"] == "courtlistener_bulk"]
        self.assertGreater(len(cl), 100)
        quiet = [r for r in cl if r["note"].startswith("collected historically; not currently scraped")]
        self.assertGreater(len(quiet), 0)
        self.assertTrue(all(r["status"] == "unknown" for r in quiet))
        self.assertTrue(all(r["url"].startswith("https://www.courtlistener.com/?q=&type=o&court=") for r in cl))

    def test_one_google_scholar_row_per_jurisdiction(self):
        scholar = [r["jurisdiction"] for r in self.data["rows"] if r["source_id"] == "google_scholar_case_law"]
        self.assertEqual(len(scholar), len(set(scholar)))

    def test_no_key_or_local_path_in_the_file(self):
        for needle in ("cl_ranges", "Authorization", "/Users/", "/home/", "COURTLISTENER_TOKEN"):
            self.assertNotIn(needle, self.raw)

    def test_a_real_lookup(self):
        res = rl.where("OH", court_level="supreme", year=1998)
        self.assertEqual(res["checked_at"], self.data["checked_at"])
        self.assertTrue(any(r["covers"] == "covers" for r in res["rows"]))
        self.assertEqual(res["rows"][0]["covers"], "covers")
        us = rl.where("US", court_level="federal-district", court="Maryland")
        self.assertTrue(any("Maryland" in r["court"] for r in us["rows"]))


class TestBuildScript(unittest.TestCase):
    """scripts/build_caselaw_coverage.py on a tiny invented survey."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("build_caselaw_coverage",
                                                      os.path.join(KIT, "scripts", "build_caselaw_coverage.py"))
        cls.build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.build)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    HEAD = ["jurisdiction", "court_name", "court_level", "source_id", "year_from", "year_to", "fulltext_search",
            "status", "evidence_url", "note", "year_from_basis", "http_status", "checked_at"]
    CL = "CL id={id}; has_opinion_scraper={s}; in_use={u}; court existed since {since} (court start_date, NOT corpus coverage)."

    def rows(self):
        cl, long_note = self.CL, "yr:observed: " + " ".join(["word"] * 80) + " | ft:observed: search box"
        return [
            ["AL", "Alabama Supreme Court", "supreme", "courtlistener_bulk", "", "", "y", "accessible", "https://api.example/x/", cl.format(id="ala", s="True", u="True", since="1800"), "unknown", "200", "2026-11-05"],
            ["AL", "Old Alabama Court", "appellate", "courtlistener_bulk", "", "1969", "y", "accessible", "https://api.example/y/", cl.format(id="alaold", s="False", u="True", since="1800"), "unknown", "200", "2026-11-06"],
            ["AL", "Alabama Supreme Court", "supreme", "official_AL_x", "2013", "", "n", "accessible", "https://courts.al.example/opinions (404 observed; none)", long_note, "observed", "404", "2026-11-04"],
            ["AL", "Alabama - all", "other", "google_scholar_case_law", "1950", "", "y", "accessible", "https://scholar.example/help", "Scholar note", "observed", "200", "2026-11-03"],
            ["AL", "Alabama - again", "other", "google_scholar_case_law", "1951", "", "y", "accessible", "https://scholar.example/help", "dupe", "observed", "200", "2026-11-03"],
            ["US", "District Court, D. Md.", "federal-district", "govinfo_uscourts", "2005", "", "y", "accessible", "https://gov.example/sitemap/mdd_2026.xml", "GovInfo code mdd", "observed", "200", "2026-11-01"],
        ]

    def survey(self, extra=(), gaps_extra=""):
        import csv
        d = self._tmp.name
        with open(os.path.join(d, "coverage.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(self.HEAD)
            w.writerows(self.rows() + list(extra))
        src = [{"source_id": i, "name": i.upper(), "url": u, "access_method": "web", "auth": "none", "cost": "free",
                "terms_url": None, "commercial_use_allowed": "unclear", "automated_access": "x", "notes": "secret survey notes"}
               for i, u in [("courtlistener_bulk", "https://cl.example/"), ("official_AL_x", "https://courts.al.example/"),
                            ("google_scholar_case_law", "https://scholar.example/"), ("govinfo_uscourts", "https://gov.example/c"),
                            ("cap_static", "https://cap.example/"), ("official_MN_x", "https://courts.mn.example/"),
                            ("courtlistener_courts_api", "https://cl.example/api/")]]
        with open(os.path.join(d, "sources.json"), "w") as f:
            json.dump(src, f)
        gaps = "# Gaps\n\nHeader paragraph.\n\n"
        gaps += " ".join(f"{c} OFF text for {c}; IN OR ME words, and a state-like OR here." + (gaps_extra if c == "AZ" else "")
                         for c in self.build.STATE_ORDER)
        gaps += "\n\nTrial courts: none anywhere. 16 states post some.\n\n## Findings\n1. GovInfo USCOURTS lacks 36 courts.\n"
        with open(os.path.join(d, "gaps_by_state.md"), "w") as f:
            f.write(gaps)
        return (os.path.join(d, "coverage.csv"), os.path.join(d, "sources.json"), os.path.join(d, "gaps_by_state.md"),
                os.path.join(d, "out", "coverage.json"))

    def years(self, courts, source="CourtListener bulk data 2026-09-30 (opinion-clusters + dockets), Public Domain Mark", **top):
        path = os.path.join(self._tmp.name, "cl_bulk_years.json")
        with open(path, "w") as f:
            json.dump(dict({"source": source, "courts": courts}, **top), f)
        return path

    def run_build(self, extra=(), years=None, gaps_extra=""):
        cov, src, gaps, out = self.survey(extra, gaps_extra)
        args = ["--coverage", cov, "--sources", src, "--gaps", gaps, "--out", out]
        if years:
            args += ["--cl-years", years]
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(self.build.main(args), 0)
        with open(out, encoding="utf-8") as f:
            text = f.read()
        self.printed = printed.getvalue()
        data = json.loads(text)
        data["by"] = {(r[0], r[1], r[3]): dict(zip(data["columns"], r)) for r in data["rows"]}
        return data, text

    def test_builds_the_shape_the_module_reads(self):
        data, text = self.run_build()
        self.assertEqual((data["schema_version"], data["checked_at"]), (2, "2026-11-06"))  # the newest row date
        self.assertIsNone(data["cl_snapshot"])  # no --cl-years
        self.assertNotIn("\n  ", text)
        rows = data["by"]
        self.assertEqual(len(rows), 5)  # the second Scholar row for AL was dropped
        live = rows[("AL", "Alabama Supreme Court", "courtlistener_bulk")]
        self.assertEqual((live["status"], live["url"]), ("accessible", "https://www.courtlistener.com/?q=&type=o&court=ala"))
        old = rows[("AL", "Old Alabama Court", "courtlistener_bulk")]
        self.assertEqual(old["status"], "unknown")
        self.assertTrue(old["note"].startswith("collected historically; not currently scraped"))
        self.assertIn("ended in 1969", old["note"])
        official = rows[("AL", "Alabama Supreme Court", "official_AL_x")]
        self.assertIsNone(official["url"])  # 404: nothing at that address
        self.assertLessEqual(len(official["note"]), 200)
        self.assertTrue(official["note"].endswith("…"))
        self.assertNotIn(" wo…", official["note"])  # cut at a word boundary
        self.assertIsNone(rows[("US", "District Court, D. Md.", "govinfo_uscourts")]["url"])  # a sitemap is not a page to open
        self.assertEqual({s["id"] for s in data["sources"]}, {"courtlistener_bulk", "official_AL_x", "google_scholar_case_law", "govinfo_uscourts", "cap_static", "official_MN_x"})
        self.assertEqual(set(data["sources"][0]), {"id", "name", "url", "access_method", "auth", "cost", "terms_url", "commercial_use_allowed"})
        self.assertNotIn("secret survey notes", text)

    def test_courtlistener_rows_carry_their_court_record_and_facts(self):
        data, _ = self.run_build()
        live = data["by"][("AL", "Alabama Supreme Court", "courtlistener_bulk")]
        self.assertEqual((live["facts"], live["cl"]), (None, {"id": "ala", "since": 1800, "ended": None}))
        old = data["by"][("AL", "Old Alabama Court", "courtlistener_bulk")]
        self.assertEqual((old["facts"], old["cl"]["id"]), (["cl-no-new-intake"], "alaold"))
        self.assertIsNone(data["by"][("AL", "Alabama Supreme Court", "official_AL_x")]["cl"])
        self.assertEqual(live["year_from"], None)  # years are measured only when --cl-years is given

    def test_row_facts_are_read_off_the_notes_and_the_notes_do_not_ship(self):
        cl = self.CL
        extra = [
            ["MN", "Minnesota Supreme Court", "supreme", "official_MN_x", "", "", "unknown", "blocked", "https://courts.mn.example/", "Not fetched (robots disallow). Cloudflare challenge.", "unknown", "blocked_by_robots", "2026-11-04"],
            ["MN", "Minnesota Court of Appeals", "appellate", "official_MN_x", "", "", "unknown", "partial", "https://courts.mn.example/coa", "yr:unknown: listing is JS-rendered | ft:js_rendered: shell", "unknown", "200", "2026-11-04"],
            ["MN", "Gone Court", "other", "courtlistener_bulk", "", "1900", "y", "unknown", "https://api.example/g/", cl.format(id="gone", s="False", u="False", since="1850") + " Historical court (ended 1900).", "unknown", "200", "2026-11-04"],
            ["MN", "Quiet Court", "other", "courtlistener_bulk", "", "", "y", "unknown", "https://api.example/q/", cl.format(id="quiet", s="True", u="False", since="n/a"), "unknown", "200", "2026-11-04"],
            ["US", "United States Bankruptcy Court, D. Guam", "bankruptcy", "govinfo_uscourts", "", "", "unknown", "unknown", "https://gov.example/s.xml", "observed: no sitemap for CL court id gub - this court's opinions are NOT in GovInfo", "observed", "200", "2026-11-04"],
            ["US", "United States Bankruptcy Court, D. Wyoming", "bankruptcy", "govinfo_uscourts", "2020", "2022", "y", "partial", "https://gov.example/w.xml", "GovInfo code wyb; Only opinions the court elects to send (not all). STALE: no packages after 2022.", "observed", "200", "2026-11-04"],
        ]
        data, text = self.run_build(extra)
        facts = {k[1]: v["facts"] for k, v in data["by"].items()}
        self.assertEqual(facts["Minnesota Supreme Court"], ["robots-txt"])
        self.assertEqual(facts["Minnesota Court of Appeals"], ["needs-script"])
        self.assertEqual(data["by"][("MN", "Gone Court", "courtlistener_bulk")]["facts"], ["cl-not-in-use", "cl-historical"])
        self.assertEqual(data["by"][("MN", "Gone Court", "courtlistener_bulk")]["cl"], {"id": "gone", "since": 1850, "ended": 1900})
        self.assertEqual(data["by"][("MN", "Quiet Court", "courtlistener_bulk")]["facts"], ["cl-not-in-use"])
        self.assertIsNone(data["by"][("MN", "Quiet Court", "courtlistener_bulk")]["cl"]["since"])  # "n/a" is no start year
        self.assertEqual(facts["United States Bankruptcy Court, D. Guam"], ["govinfo-absent"])
        self.assertEqual(facts["United States Bankruptcy Court, D. Wyoming"], ["govinfo-stale", "govinfo-elective"])
        produced = {f for row in data["by"].values() for f in (row["facts"] or [])}
        self.assertEqual(produced, {"robots-txt", "needs-script", "cl-not-in-use", "cl-historical", "cl-no-new-intake",
                                    "govinfo-absent", "govinfo-stale", "govinfo-elective"})
        self.assertEqual(produced, set(data["legend"]["facts"]))  # every code the builder writes is explained
        self.assertNotIn("NOT corpus coverage", text)

    def test_gap_flags_come_from_the_gap_notes(self):
        data, _ = self.run_build(gaps_extra=" Some listing is a JS portal we could not read.")
        self.assertEqual(data["gap_flags"], {"AZ": ["needs-script"]})
        data, _ = self.run_build(gaps_extra=" A JS bot-validation page was served.")  # a challenge page, not a script list
        self.assertEqual(data["gap_flags"], {})

    def test_court_names_lose_the_surveys_working_remarks(self):
        c = self.build.clean_court_name
        self.assertEqual(c("U.S. Court of Appeals for the Federal Circuit (GovInfo code ca13 - inferred from FedCir-style docket numbers)"),
                         "U.S. Court of Appeals for the Federal Circuit")
        self.assertEqual(c("(name not in fetched CL list) jpml"), "Court code jpml")
        self.assertEqual(c("Texas Court of Appeals, 1st District (Houston)"), "Texas Court of Appeals, 1st District (Houston)")
        self.assertEqual(c("  Spaced   name "), "Spaced name")

    def test_courtlistener_years_fill_a_row_and_keep_an_early_year_that_is_real_history(self):
        years = self.years({"ala": {"count": 1000, "approximate": 5, "earliest": "1750-03-01", "latest": "2026-09-01"}})
        data, _ = self.run_build(years=years)
        self.assertEqual(data["cl_snapshot"], SNAPSHOT)
        self.assertIn("2026-09-30", data["source_files"])
        live = data["by"][("AL", "Alabama Supreme Court", "courtlistener_bulk")]
        # The court began in 1800 but Alabama has no Caselaw Access Project year here and 1750 is a real year: kept.
        self.assertEqual((live["year_from"], live["year_to"], live["basis"]), (1750, 2026, "observed"))
        self.assertEqual(live["cl"], {"id": "ala", "since": 1800, "ended": None, "count": 1000, "approximate": 5,
                                      "earliest": "1750-03-01", "latest": "2026-09-01"})
        self.assertIn("1,000 opinions, newest 2026-09-01", live["note"])
        self.assertNotIn("raised", live["note"])
        # a court the summary does not list has no opinions in the bulk files
        old = data["by"][("AL", "Old Alabama Court", "courtlistener_bulk")]
        self.assertEqual((old["cl"]["count"], old["cl"]["latest"], old["year_from"], old["year_to"]), (0, None, None, 1969))
        self.assertIn("no opinions from it", old["note"])
        self.assertIn("0 start years corrected", self.printed)

    def cap_and_cl(self):
        """Rows for three invented states with the Caselaw Access Project, and a court of each kind."""
        cl = self.CL
        cap = lambda j, y: [j, f"{j} - all reported decisions in CAP reporters", "other", "cap_static", str(y), "2019", "n", "accessible", "https://static.example/m.json", "CAP: 1 case", "observed", "200", "2026-11-04"]
        court = lambda j, name, cid, since: [j, name, "supreme", "courtlistener_bulk", "", "", "y", "accessible", "https://api.example/c/", cl.format(id=cid, s="True", u="True", since=since), "unknown", "200", "2026-11-04"]
        return [cap("MN", 1830), court("MN", "Minnesota Supreme Court", "minn", "1858"),         # 1665: before both -> 1830
                cap("MS", 1818), court("MS", "Mississippi Supreme Court", "mss", "1998"),        # 1881: after the project's start -> kept
                cap("NC", 1778), court("NC", "North Carolina Supreme Court", "nc", "1700"),      # 1750: before the project, after the court -> kept
                cap("ND", 1830), court("ND", "North Dakota Supreme Court", "nd", "n/a"),         # no court start: the project alone decides
                cap("NH", 1796), court("NH", "New Hampshire Supreme Court", "nh", "1776"),       # year 19 -> 1796
                court("NE", "Nebraska Supreme Court", "ne", "1875"),                             # no project year, 1700 is real -> kept
                court("NV", "Nevada Supreme Court", "nv", "n/a")]                                # no project year, year 18 -> not found

    def test_a_year_before_both_the_courts_start_and_the_projects_is_raised_to_the_projects(self):
        years = self.years({
            "minn": {"count": 43427, "approximate": 1, "earliest": "1665-01-02", "latest": "2026-09-02"},
            "mss": {"count": 100, "approximate": 0, "earliest": "1881-01-01", "latest": "2026-09-02"},
            "nc": {"count": 100, "approximate": 0, "earliest": "1750-01-01", "latest": "2026-09-02"},
            "nd": {"count": 100, "approximate": 0, "earliest": "1665-01-01", "latest": "2026-09-02"},
            "nh": {"count": 17407, "approximate": 1375, "earliest": "1901-31-", "latest": "2026-09-24"},
            "ne": {"count": 100, "approximate": 0, "earliest": "1700-01-01", "latest": "2026-09-02"},
            "nv": {"count": 100, "approximate": 0, "earliest": "1801-31-", "latest": "2026-09-02"}})
        data, _ = self.run_build(self.cap_and_cl(), years)
        get = lambda j, court: data["by"][(j, court, "courtlistener_bulk")]
        mn = get("MN", "Minnesota Supreme Court")
        self.assertEqual((mn["year_from"], mn["cl"]["earliest"], mn["cl"]["clamp"]),
                         (1830, "1665-01-02", {"from": 1665, "to": 1830, "floor": "cap-start"}))
        self.assertIn("Start year raised from 1665 to 1830 (the Caselaw Access Project's start)", mn["note"])
        ms = get("MS", "Mississippi Supreme Court")  # a predecessor court's real history, as Kentucky's 1881
        self.assertEqual((ms["year_from"], "clamp" in ms["cl"]), (1881, False))
        nc = get("NC", "North Carolina Supreme Court")  # before the project, but after the court began: not before both
        self.assertEqual((nc["year_from"], "clamp" in nc["cl"]), (1750, False))
        nd = get("ND", "North Dakota Supreme Court")  # the court's start is unknown, so the project's year decides
        self.assertEqual((nd["year_from"], nd["cl"]["clamp"]["floor"]), (1830, "cap-start"))
        nh = get("NH", "New Hampshire Supreme Court")  # "1901-31-" is "0019-01-31": year 19
        self.assertEqual((nh["cl"]["earliest"], nh["year_from"], nh["cl"]["clamp"]), ("0019-01-31", 1796, {"from": 19, "to": 1796, "floor": "cap-start"}))
        ne = get("NE", "Nebraska Supreme Court")  # no project year: only a year below 1600 is corrected
        self.assertEqual((ne["year_from"], "clamp" in ne["cl"]), (1700, False))
        nv = get("NV", "Nevada Supreme Court")
        self.assertEqual((nv["cl"]["earliest"], nv["year_from"], nv["year_to"], nv["cl"]["clamp"]),
                         ("0018-01-31", None, 2026, {"from": 18, "to": None, "floor": "typo"}))
        self.assertIn("Earliest year not found: its earliest date is year 18", nv["note"])
        self.assertIn("4 start years corrected", self.printed)
        self.assertIn("raised MN Minnesota Supreme Court (minn): 1665 -> 1830 (cap-start)", self.printed)
        self.assertIn("raised NV Nevada Supreme Court (nv): 18 -> not found (typo)", self.printed)

    def test_a_bulk_date_that_lost_its_leading_zeros_is_read_back_and_a_bad_one_is_not_trusted(self):
        rd = self.build.read_date
        self.assertEqual(rd("2026-09-30"), "2026-09-30")
        self.assertEqual(rd("1901-31-"), "0019-01-31")  # "0019-01-31" as the summary once wrote it
        self.assertEqual(rd("0019-01-31"), "0019-01-31")
        for bad in (None, "", "garbage", "2026-13-40", "2026-02-30", "123456789", "1-2-3"):
            self.assertIsNone(rd(bad), bad)
        years = self.years({"ala": {"count": 10, "approximate": 0, "earliest": "1801-31-", "latest": "2026-09-01"}})
        data, _ = self.run_build(years=years)
        live = data["by"][("AL", "Alabama Supreme Court", "courtlistener_bulk")]
        self.assertEqual((live["cl"]["earliest"], live["year_from"], live["cl"]["clamp"]), ("0018-01-31", None, {"from": 18, "to": None, "floor": "typo"}))

    def test_the_years_file_needs_the_shape_the_counts_and_a_snapshot_date(self):
        cov, src, gaps, out = self.survey()
        base = ["--coverage", cov, "--sources", src, "--gaps", gaps, "--out", out]
        for body, message in (({"courts": {"ala": {"count": -1, "approximate": 0}}}, "non-negative"),
                              ({"courts": []}, "must look like"),
                              ({"source": "no date here", "courts": {}}, "snapshot date")):
            path = os.path.join(self._tmp.name, "y.json")
            with open(path, "w") as f:
                json.dump(body, f)
            with self.assertRaises(SystemExit) as ctx:
                self.build.main(base + ["--cl-years", path])
            self.assertIn(message, str(ctx.exception))
        self.assertFalse(os.path.exists(out))
        self.assertEqual(self.build.read_cl_years(self.years({}, source="x", snapshot="2026-06-30"))[1], "2026-06-30")

    def test_gaps_are_split_by_state_in_order_not_at_every_state_like_word(self):
        data, _ = self.run_build()
        self.assertEqual(len(data["gaps"]), 52)
        self.assertEqual(data["gaps"]["IN"], ["OFF text for IN; IN OR ME words, and a state-like OR here."])
        self.assertEqual(data["gaps"]["WY"], ["OFF text for WY; IN OR ME words, and a state-like OR here."])
        self.assertEqual(data["gaps"]["US"], ["GovInfo USCOURTS lacks 36 courts."])
        self.assertEqual(data["general_gaps"], ["Trial courts: none anywhere.", "16 states post some."])

    def test_trim_cuts_at_a_word_and_leaves_short_notes_alone(self):
        self.assertEqual(self.build.trim("short  note"), "short note")
        cut = self.build.trim("alpha beta gamma delta", 12)
        self.assertEqual(cut, "alpha beta…")
        self.assertLessEqual(len(cut), 12)

    def test_a_file_from_the_api_key_is_refused_for_every_argument(self):
        cov, src, gaps, out = self.survey()
        keyed = os.path.join(self._tmp.name, "cl_ranges.jsonl")
        for field in ("--gaps", "--cl-years", "--coverage", "--sources"):
            args = {"--coverage": cov, "--sources": src, "--gaps": gaps, "--out": out}
            args[field] = keyed
            argv = [x for kv in args.items() for x in kv]
            with self.subTest(field=field), self.assertRaises(SystemExit) as ctx:
                self.build.main(argv)
            self.assertIn("API key", str(ctx.exception))
            self.assertFalse(os.path.exists(out))


def run_cli(*args, cwd=None):
    return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "research", *args], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, cwd=cwd, timeout=60,
                          env=dict(os.environ, USER="pat", PYTHONPATH=NETBLOCK))


class TestCli(Case):
    def cli(self, *args):
        return run_cli(*args, cwd=self._tmp.name)

    def test_log_after_nothing_says_where_else_to_look_without_the_place_just_tried(self):
        out = self.cli("log", self.dir, "--source", "google scholar", "--query", "docket control",
                       "--outcome", "nothing", "--jurisdiction", "OH", "--years", "1998")
        self.assertEqual(out.returncode, 0, out.stderr)
        first, rest = out.stdout.split("\n", 1)
        self.assertRegex(first, r"^rs_[0-9a-f]{8} \[nothing\] google scholar: docket control$")
        self.assertIn("Other places to look in OH for 1998", rest)
        self.assertIn("survey checked " + rl.load_coverage()["checked_at"], rest)
        self.assertIn("court sites change", rest)
        self.assertNotIn("google_scholar_case_law", rest)  # the place just tried is left out
        self.assertNotIn("Google Scholar", rest)
        self.assertIn("official_OH", rest)
        self.assertIn("research where OH", rest)
        self.assertEqual(json.loads(self.lines()[0])["by"], "pat")

    def test_every_miss_outcome_hints_but_found_and_error_do_not(self):
        for outcome in ("blocked", "not-online", "paywalled"):
            out = self.cli("log", self.dir, "--source", "court website", "--query", "q", "--outcome", outcome,
                           "--jurisdiction", "OH")
            with self.subTest(outcome=outcome):
                self.assertIn("Other places to look in OH", out.stdout)
                self.assertNotIn("official_OH", out.stdout)  # the court's own site was what failed
                self.assertIn("google_scholar_case_law", out.stdout)
        for outcome in ("found", "error"):
            out = self.cli("log", self.dir, "--source", "court website", "--query", "q", "--outcome", outcome,
                           "--jurisdiction", "OH")
            with self.subTest(outcome=outcome):
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertNotIn("Other places", out.stdout)

    def test_a_miss_without_a_jurisdiction_says_how_to_get_hints(self):
        out = self.cli("log", self.dir, "--source", "x", "--query", "q", "--outcome", "nothing")
        self.assertIn("--jurisdiction", out.stdout)
        self.assertNotIn("Other places", out.stdout)

    def test_a_jurisdiction_the_survey_lacks_still_records(self):
        out = self.cli("log", self.dir, "--source", "x", "--query", "q", "--outcome", "nothing", "--jurisdiction", "QQ")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("nothing for 'QQ'", out.stdout)
        self.assertEqual(len(self.lines()), 1)

    def test_refusals_exit_nonzero_and_write_nothing(self):
        for args in (["log", self.dir, "--source", " ", "--query", "q", "--outcome", "found"],
                     ["log", self.dir, "--source", "s", "--query", "", "--outcome", "found"],
                     ["log", self.dir, "--source", "s", "--query", "q", "--outcome", "found", "--years", "2005-1990"],
                     ["save", self.dir, "A case", "--why", " "],
                     ["save", self.dir, "A case", "--why", "w", "--revisit", "soon"],
                     ["set", self.dir, "rl_nope", "--status", "dropped"],
                     ["list", os.path.join(self._tmp.name, "elsewhere")],
                     ["where", "QQ"]):
            out = self.cli(*args)
            with self.subTest(args=args):
                self.assertEqual(out.returncode, 1)
                self.assertIn("refused", out.stderr)
                self.assertEqual(out.stdout, "")
        self.assertEqual(self.lines(), [])
        self.assertFalse(os.path.exists(os.path.join(self._tmp.name, "elsewhere")))

    def test_leads_end_to_end(self):
        saved = self.cli("save", self.dir, "Smith v. Jones", "--why", "waiver holding", "--revisit", "2026-11-01",
                         "--citation", "1 U.S. 1", "--jurisdiction", "US")
        self.assertEqual(saved.returncode, 0, saved.stderr)
        lead_id = saved.stdout.split()[0]
        self.cli("save", self.dir, "Undated", "--why", "maybe")
        due = self.cli("due", self.dir, "--as-of", "2026-11-07")
        self.assertIn("Due on or before 2026-11-07: 1", due.stdout)
        self.assertIn("Smith v. Jones", due.stdout.split("No revisit date")[0])
        self.assertIn("Undated", due.stdout.split("No revisit date")[1])
        self.assertEqual(self.cli("set", self.dir, lead_id, "--status", "revisited", "--note", "read").returncode, 0)
        self.assertEqual([r["title"] for r in json.loads(self.cli("leads", self.dir, "--json").stdout)], ["Undated"])
        self.assertEqual([r["status"] for r in json.loads(self.cli("leads", self.dir, "--status", "revisited", "--json").stdout)],
                         ["revisited"])
        again = self.cli("set", self.dir, lead_id, "--status", "revisited")
        self.assertEqual((again.returncode, again.stdout), (1, ""))

    def test_list_shows_the_kits_searches_too(self):
        saved_lookups.save_search(self.dir, "courtlistener-free", "plausibility", 5, None, [{"x": 1}])
        self.cli("log", self.dir, "--source", "law library", "--query", "waiver", "--outcome", "not-online")
        out = self.cli("list", self.dir)
        self.assertIn("courtlistener: plausibility", out.stdout)
        self.assertIn("law library: waiver", out.stdout)
        rows = json.loads(self.cli("list", self.dir, "--json", "--outcome", "found").stdout)
        self.assertEqual([r["source"] for r in rows], ["courtlistener"])

    def test_where_needs_no_case_folder(self):
        out = self.cli("where", "oh", "--level", "supreme", "--year", "1998")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Where to find case law for OH (supreme courts, year 1998)", out.stdout)
        self.assertIn("checked", out.stdout)
        rows = json.loads(self.cli("where", "US", "--level", "federal-district", "--court", "Maryland", "--json").stdout)
        self.assertGreater(rows["total"], 0)
        self.assertEqual(rows["checked_at"], rl.load_coverage()["checked_at"])
        long = self.cli("where", "US")
        self.assertIn("more; narrow it", long.stdout)
        self.assertGreater(json.loads(self.cli("where", "US", "--json").stdout)["total"], 100)


    def test_where_shows_courtlistener_years_and_the_newest_opinion(self):
        out = self.cli("where", "MN", "--level", "supreme")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("years 1830-2026 (observed)", out.stdout)
        self.assertIn("CourtListener holds 43,427 opinions (bulk files of 2026-09-30); newest opinion 2026-09-02; "
                      "start year raised from 1665 to 1830, the Caselaw Access Project's start", out.stdout)
        res = json.loads(self.cli("where", "MN", "--level", "supreme", "--json").stdout)
        self.assertEqual(res["cl_snapshot"], "2026-09-30")
        cl = next(r for r in res["rows"] if r["source_id"] == "courtlistener_bulk")
        self.assertEqual((cl["cl"]["latest"], cl["cl"]["count"], cl["year_from"], cl["year_to"]), ("2026-09-02", 43427, 1830, 2026))


class TestMcp(Case):
    def mcp(self, *calls):
        msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}]
        for i, (name, args) in enumerate(calls, 1):
            msgs.append({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}})
        msgs.append({"jsonrpc": "2.0", "id": 99, "method": "tools/list"})
        env = dict(os.environ, MATTER_DIR=self.dir, PYTHONPATH=NETBLOCK)
        r = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input="\n".join(map(json.dumps, msgs)) + "\n",
                           capture_output=True, text=True, env=env, cwd=self._tmp.name, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return {m["id"]: m for m in map(json.loads, r.stdout.splitlines())}

    def text(self, out, i):
        return out[i]["result"]["content"][0]["text"]

    def test_the_tools_are_listed_and_say_what_they_are(self):
        tools = {t["name"]: t for t in self.mcp()[99]["result"]["tools"]}
        names = ("matter.research_list", "matter.research_leads", "matter.research_due", "matter.research_where",
                 "matter.research_log", "matter.research_save")
        for name in names:
            self.assertIn(name, tools)
        self.assertNotIn("matter.research_set", tools)  # changing a lead's status is a person's call
        self.assertIn("not good law", tools["matter.research_leads"]["description"])
        self.assertIn("not good law", tools["matter.research_save"]["description"])
        self.assertIn("court websites change", tools["matter.research_where"]["description"])
        self.assertIn("dated survey", tools["matter.research_where"]["description"])
        for name in ("matter.research_list", "matter.research_leads", "matter.research_due", "matter.research_where"):
            self.assertIn("Read-only", tools[name]["description"])
        for name in ("matter.research_log", "matter.research_save"):
            self.assertIn("research.jsonl", tools[name]["description"])
            self.assertIn("no network", tools[name]["description"].lower())
        self.assertEqual(tools["matter.research_log"]["inputSchema"]["required"], ["source", "query", "outcome"])
        self.assertEqual(tools["matter.research_save"]["inputSchema"]["required"], ["title", "why"])

    def test_log_save_and_read_back_round_trip(self):
        out = self.mcp(
            ("matter.research_log", {"source": "google scholar", "query": "docket control", "outcome": "nothing",
                                     "jurisdiction": "OH", "years": "1998", "created_by": "agent:test-model",
                                     "directed_by": "Pat Example"}),
            ("matter.research_save", {"title": "Smith v. Jones", "why": "waiver holding", "revisit": "2026-11-01"}),
            ("matter.research_list", {}), ("matter.research_leads", {}),
            ("matter.research_due", {"as_of": "2026-11-07"}),
            ("matter.research_where", {"jurisdiction": "OH", "level": "supreme", "year": "1998", "limit": 3}))
        for i in range(1, 7):
            self.assertFalse(out[i]["result"].get("isError"), out[i])
        self.assertRegex(self.text(out, 1), r"^rs_[0-9a-f]{8} \[nothing\] google scholar: docket control")
        self.assertIn("Other places to look in OH for 1998", self.text(out, 1))
        self.assertNotIn("Google Scholar", self.text(out, 1))
        self.assertIn("agent:test-model; directed-by:Pat Example", self.text(out, 1))
        self.assertRegex(self.text(out, 2), r"^rl_[0-9a-f]{8} \[saved\] Smith v\. Jones")
        searches = json.loads(self.text(out, 3))
        self.assertEqual([(r["source"], r["by"]) for r in searches], [("google scholar", "agent:test-model; directed-by:Pat Example")])
        self.assertEqual([(r["title"], r["by"]) for r in json.loads(self.text(out, 4))], [("Smith v. Jones", "agent")])
        due = json.loads(self.text(out, 5))
        self.assertEqual([(r["title"], r["days_over"]) for r in due["due"]], [("Smith v. Jones", 6)])
        where = json.loads(self.text(out, 6))
        self.assertEqual((where["jurisdiction"], len(where["rows"]), where["checked_at"]),
                         ("OH", 3, rl.load_coverage()["checked_at"]))
        self.assertEqual(len(self.lines()), 2)

    def test_where_carries_courtlistener_years_and_the_snapshot(self):
        out = self.mcp(("matter.research_where", {"jurisdiction": "MN", "level": "supreme"}))
        self.assertFalse(out[1]["result"].get("isError"), out[1])
        res = json.loads(self.text(out, 1))
        self.assertEqual(res["cl_snapshot"], "2026-09-30")
        cl = next(r for r in res["rows"] if r["source_id"] == "courtlistener_bulk")
        self.assertEqual((cl["year_from"], cl["year_to"], cl["cl"]["latest"], cl["facts"]), (1830, 2026, "2026-09-02", []))
        self.assertEqual(cl["cl"]["clamp"], {"from": 1665, "to": 1830, "floor": "cap-start"})
        tools = {t["name"]: t for t in out[99]["result"]["tools"]}
        self.assertIn("newest opinion", tools["matter.research_where"]["description"])

    def test_refusals_are_errors_and_write_nothing(self):
        out = self.mcp(("matter.research_log", {"source": "", "query": "q", "outcome": "found"}),
                       ("matter.research_log", {"source": "s", "query": "q", "outcome": "maybe"}),
                       ("matter.research_save", {"title": "t", "why": ""}),
                       ("matter.research_save", {"title": "t", "why": "w", "directed_by": "Pat"}),  # directed-by needs agent:
                       ("matter.research_where", {"jurisdiction": "QQ"}),
                       ("matter.research_leads", {"status": "pending"}))
        for i in range(1, 7):
            self.assertTrue(out[i]["result"].get("isError"), out[i])
        self.assertEqual(self.lines(), [])

    def test_no_network_is_reachable_from_the_research_tools(self):
        with open(os.path.join(KIT, "matter-mcp.py"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("def _research("):src.index("def call(")]
        for word in ("urllib", "socket", "adapters", "consent", "import caselaw", "caselaw.search"):
            self.assertNotIn(word, body)


if __name__ == "__main__":
    unittest.main()

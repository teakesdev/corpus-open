"""The case timeline: dated facts, quotes per source, and the check that runs each time it is shown.

Covers matterkit.timeline, the date and quote options on facts.add, the store migration, and the
CLI, MCP and /matter front doors. Synthetic tempdirs only; nothing touches the network.
Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import facts, packet, store, timeline  # noqa: E402

CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("MATTER_AUTHOR", "MATTER_DIRECTED_BY")}
DECL = "Filings/decl.md"
DECL_TEXT = ("Synthetic declaration, page one.\n<!-- PAGEBREAK -->\n"
             "On January 5, 2026, I attended a meeting with my supervisor.\n<!-- PAGEBREAK -->\n"
             "Afterwards HR was told to mark the file 'voluntary resignation.'\n")


def run_cli(*argv, check=True):
    return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *argv],
                          capture_output=True, text=True, check=check, env=CLEAN_ENV)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "Case")
        run_cli("init", self.dir)
        write(os.path.join(self.dir, *DECL.split("/")), DECL_TEXT)
        write(os.path.join(self.dir, "Evidence", "note.txt"), "A synthetic note about the meeting.\n")
        run_cli("ingest", self.dir)
        self.conn = store.connect(self.dir)
        self.addCleanup(lambda: self.conn.close())

    def add(self, text, sources=(), quotes=None, status="DOCUMENTED FACT", **date):
        return facts.add(self.conn, self.dir, text, status, list(sources), "human", quotes=quotes, **date)

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def build(self, **kw):
        return timeline.build(self.conn, self.dir, use_pdftotext=False, **kw)

    def mcp(self, name, arguments):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input=json.dumps(req) + "\n",
                           capture_output=True, text=True, env=dict(CLEAN_ENV, MATTER_DIR=self.dir), check=True)
        return json.loads(p.stdout.strip().splitlines()[-1])["result"]


class TestDates(unittest.TestCase):
    def test_parse_date_forms_and_refusals(self):
        self.assertEqual(timeline.parse_date("2026-03-14"), ("2026-03-14", "day"))
        self.assertEqual(timeline.parse_date("2026-03"), ("2026-03", "month"))
        self.assertEqual(timeline.parse_date("2026"), ("2026", "year"))
        for bad in ("2026-02-30", "2026-13", "March 14", "26-03-14", "", None):
            with self.assertRaises(timeline.TimelineError, msg=bad):
                timeline.parse_date(bad)

    def test_range_must_not_end_before_it_starts(self):
        self.assertEqual(timeline.check_date("2026-03", "2026-04-02"), ("2026-03", "month", "2026-04-02"))
        self.assertEqual(timeline.check_date("2026-03", "2026-03"), ("2026-03", "month", None))
        with self.assertRaises(timeline.TimelineError):
            timeline.check_date("2026-03-14", "2026-03-01")
        # A month that ends inside the range start's month still overlaps it.
        timeline.check_date("2026-03-14", "2026-03")

    def test_display(self):
        d = {"date_start": "2026-03", "date_end": "2026-04", "approximate": True, "inferred": True}
        self.assertEqual(timeline.display_date(d), "about 2026-03 to 2026-04 (inferred)")


class TestAddAndStore(Base):
    def test_add_saves_date_and_quote(self):
        r = self.add("Employment ended at a meeting", [f"{DECL}@p.2"], ["On January 5, 2026, I attended"],
                     date="2026-01-05")
        self.assertEqual(r["quotes"], 1)
        self.assertEqual(r["date"]["date_start"], "2026-01-05")
        row = self.conn.execute("SELECT * FROM fact_dates").fetchone()
        self.assertEqual((row["precision"], row["approximate"], row["date_basis"], row["created_by"]),
                         ("day", 0, "stated", "human"))
        self.assertEqual(self.conn.execute("SELECT locator, quote FROM fact_quotes").fetchone()[:],
                         ("p.2", "On January 5, 2026, I attended"))
        listed = facts.list_(self.conn)[0]
        self.assertEqual(listed["date"], {"date_start": "2026-01-05", "date_end": None, "precision": "day",
                                          "approximate": False, "inferred": False})

    def test_undated_fact_is_unchanged(self):
        r = self.add("No date", [f"{DECL}@p.2"])
        self.assertIsNone(r["date"])
        self.assertEqual(r["quotes"], 0)
        self.assertIsNone(facts.list_(self.conn)[0]["date"])

    def test_refusals_write_nothing(self):
        cases = [dict(date="2026-02-30"), dict(date_end="2026-03"), dict(approximate=True),
                 dict(inferred=True), dict(date="2026-03-14", date_end="2026-03-01"),
                 dict(quotes=["one", "two"]), dict(quotes=[7])]
        for kw in cases:
            quotes = kw.pop("quotes", None)
            with self.assertRaises(Exception, msg=kw):
                self.add("refused", [f"{DECL}@p.2"], quotes, **kw)
        # A quote on a source that doesn't resolve refuses the whole fact, not just the quote.
        with self.assertRaises(Exception):
            self.add("refused", ["Evidence/nope.txt"], ["words"], date="2026")
        for table in ("assertions", "assertion_sources", "fact_dates", "fact_quotes"):
            self.assertEqual(self.count(table), 0, table)

    def test_set_date_change_and_clear(self):
        aid = self.add("Later dated", [f"{DECL}@p.2"])["id"]
        with self.assertRaises(timeline.TimelineError):
            timeline.set_date(self.conn, "a_000000000000", "2026")
        timeline.set_date(self.conn, aid, "2026-01", approximate=True, by="agent:m; directed-by:Tau")
        row = self.conn.execute("SELECT precision, approximate, created_by FROM fact_dates").fetchone()
        self.assertEqual(tuple(row), ("month", 1, "agent:m; directed-by:Tau"))
        timeline.set_date(self.conn, aid, "2026-01-05", inferred=True)
        self.assertEqual(self.conn.execute("SELECT date_basis FROM fact_dates").fetchone()[0], "inferred")
        timeline.set_date(self.conn, aid, clear=True)
        self.assertEqual(self.count("fact_dates"), 0)

    def test_old_store_gains_tables_and_keeps_rows(self):
        aid = self.add("Kept", [f"{DECL}@p.2"])["id"]
        self.conn.executescript("DROP TABLE fact_quotes; DROP TABLE fact_dates;")
        self.conn.close()
        ro = store.connect_readonly(self.dir)
        try:  # a read-only connection creates nothing; the timeline still renders every fact undated
            data = timeline.build(ro, self.dir, use_pdftotext=False)
            self.assertEqual([f["id"] for f in data["undated"]], [aid])
            self.assertEqual(facts.list_(ro)[0]["date"], None)
            self.assertEqual(timeline.summary_line(ro), "Timeline: 0 of 1 facts dated.")
        finally:
            ro.close()
        self.conn = store.connect(self.dir)
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"fact_dates", "fact_quotes"} <= names)
        self.assertEqual(self.conn.execute("SELECT id FROM assertions").fetchone()[0], aid)
        self.assertEqual(self.count("assertion_sources"), 1)


class TestBuild(Base):
    def test_order_by_date_then_precision(self):
        self.add("day", date="2026-01-05")
        self.add("year", date="2026")
        self.add("earlier day", date="2025-12-31")
        self.add("month", date="2026-01")
        self.add("undated")
        data = self.build()
        self.assertEqual([f["text"] for f in data["dated"]], ["earlier day", "year", "month", "day"])
        self.assertEqual([f["text"] for f in data["undated"]], ["undated"])

    def test_each_verdict(self):
        self.add("exact", [f"{DECL}@p.2"], ["On January 5, 2026, I attended"], date="2026-01-05")
        self.add("wrong page", [f"{DECL}@p.2"], ["mark the file 'voluntary resignation.'"], date="2026-01-06")
        self.add("not found", [f"{DECL}@p.2"], ["words that are nowhere"], date="2026-01-07")
        self.add("no quote", [f"{DECL}@p.2"], date="2026-01-08")
        got = {f["text"]: f["sources"][0] for f in self.build()["dated"]}
        self.assertEqual(got["exact"]["verdict"], timeline.EXACT)
        self.assertEqual(got["wrong page"]["verdict"], timeline.WRONG_PAGE)
        self.assertIn("3", got["wrong page"]["where"])
        self.assertEqual(got["not found"]["verdict"], timeline.NOT_FOUND)
        self.assertEqual(got["no quote"]["verdict"], timeline.NO_QUOTE)
        out = timeline.render(self.build())
        for mark in ("✓", "⚠ words on p. 3", "✗ not found", "–"):
            self.assertIn(mark, out)
        self.assertIn("1 exact, 1 wrong page, 1 not found", out)

    def test_changed_file_cannot_keep_an_old_check(self):
        self.add("exact", [f"{DECL}@p.2"], ["On January 5, 2026, I attended"], date="2026-01-05")
        self.assertEqual(self.build()["dated"][0]["sources"][0]["verdict"], timeline.EXACT)
        write(os.path.join(self.dir, *DECL.split("/")), DECL_TEXT + "An edit after registering.\n")
        src = self.build()["dated"][0]["sources"][0]
        self.assertEqual(src["verdict"], timeline.CANT_CHECK)
        self.assertIn("changed", timeline.render(self.build()))

    def test_from_to_keeps_overlapping_dates(self):
        self.add("jan", date="2026-01-05")
        self.add("feb-to-apr", date="2026-02", date_end="2026-04")
        self.add("2025", date="2025")
        self.add("undated")
        texts = lambda **kw: [f["text"] for f in self.build(**kw)["dated"]]  # noqa: E731
        self.assertEqual(texts(since="2026-03"), ["feb-to-apr"])
        self.assertEqual(texts(until="2026-01"), ["2025", "jan"])
        self.assertEqual(texts(since="2025-06", until="2026-01-31"), ["2025", "jan"])
        self.assertEqual(self.build(since="2026")["undated"], [])
        with self.assertRaises(timeline.TimelineError):
            self.build(since="soon")

    def test_status_line(self):
        self.assertEqual(timeline.summary_line(self.conn), "")
        self.add("dated", date="2026")
        self.add("undated")
        self.assertEqual(timeline.summary_line(self.conn), "Timeline: 1 of 2 facts dated.")
        self.assertIn("Timeline: 1 of 2 facts dated.", packet.status_report(self.conn, self.dir))


class TestFrontDoors(Base):
    def test_cli(self):
        out = run_cli("fact", "add", self.dir, "--text", "Met", "--status", "DOCUMENTED-FACT",
                      "--date", "2026-01-05", "--source", f"{DECL}@p.2",
                      "--quote", "On January 5, 2026, I attended").stdout
        self.assertIn("dated 2026-01-05", out)
        aid = facts.list_(self.conn)[0]["id"]
        run_cli("fact", "date", self.dir, aid, "--date", "2026-01", "--approx")
        out = run_cli("timeline", self.dir).stdout
        self.assertIn("| about 2026-01 | Met | DOCUMENTED FACT |", out)
        self.assertIn("✓", out)
        bad = run_cli("fact", "add", self.dir, "--text", "x", "--status", "UNKNOWN", "--quote", "w", check=False)
        self.assertNotEqual(bad.returncode, 0)
        bad = run_cli("timeline", self.dir, "--from", "soon", check=False)
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual(self.count("assertions"), 1)

    def test_mcp(self):
        res = self.mcp("matter.fact_add", {"text": "Met", "status": "DOCUMENTED FACT", "sources": [f"{DECL}@p.2"],
                                           "quotes": ["On January 5, 2026, I attended"], "date": "2026-01-05",
                                           "created_by": "agent:m", "directed_by": "Tau"})
        self.assertFalse(res.get("isError"), res)
        aid = facts.list_(self.conn)[0]["id"]
        self.assertEqual(self.conn.execute("SELECT created_by FROM fact_dates").fetchone()[0],
                         "agent:m; directed-by:Tau")
        res = self.mcp("matter.fact_date", {"fact_id": aid, "date": "2026-01", "inferred": True})
        self.assertFalse(res.get("isError"), res)
        text = self.mcp("matter.timeline", {"from": "2026"})["content"][0]["text"]
        self.assertIn("2026-01 (inferred)", text)
        self.assertIn("✓", text)


@unittest.skipUnless(os.path.exists(os.path.join(KIT, "__init__.py")), "plugin package")
class TestHermes(unittest.TestCase):
    def setUp(self):
        import test_hermes_plugin as thp
        self.case = thp.PluginCase("run")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_command_and_tools(self):
        p = self.case.plugin()
        r = self.case.call("matter_fact_add", {"text": "Notice served", "status": "ALLEGATION",
                                               "date": "2026-03-14", "approximate": True, "directed_by": "Tau"})
        self.assertTrue(r["ok"], r)
        aid = r["result"]["id"]
        self.assertEqual(r["result"]["date"]["date_start"], "2026-03-14")
        self.assertEqual(self.case.call("matter_fact_list")["result"][0]["date"]["precision"], "day")
        r = self.case.call("matter_fact_date", {"fact_id": aid, "date": "2026-03"})
        self.assertTrue(r["ok"], r)
        self.assertIn("| 2026-03 | Notice served | ALLEGATION |", self.case.call("matter_timeline")["result"])
        self.assertIn("Notice served", p.command("timeline from 2026-03 to 2026-03"))
        self.assertNotIn("Notice served", p.command("timeline from 2027"))
        self.assertIn("Usage: /matter timeline", p.command("timeline soon"))
        self.assertIn("timeline", p.command("frobnicate"))
        self.assertIn("Timeline: 1 of 1 facts dated.", p.command("status"))
        self.assertFalse(self.case.call("matter_timeline", {"from": "soon"})["ok"])


if __name__ == "__main__":
    unittest.main()

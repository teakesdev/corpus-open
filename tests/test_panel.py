"""The Hermes Desktop panel's data (matterkit/panel.py): case overview, deadlines with passed ones
first, the tamper check and the count of facts with no source.

Ways it could go wrong, each pinned by a test below:
- a passed deadline sorts under an upcoming one, or a malformed date sorts first (SQL orders
  strings, so "TBD" or "" would land anywhere);
- a closed deadline shows as passed;
- the tamper badge reads "intact" when a file was edited, deleted, or never checked;
- the unsupported count counts a fact that does have a source;
- the panel writes to the store, creates one, or hands the desktop an absolute path.
Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matterkit import cases, deadlines, facts, ingest, panel, store, triggers  # noqa: E402

TODAY = dt.date(2026, 11, 10)


def quiet(fn, *a, **kw):
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk-panel-")
        self.md = os.path.join(self.tmp, "Doe v. Acme")
        quiet(cases.init, self.md)
        for rel, body in (("Evidence/lease.txt", "lease, synthetic\n"),
                          ("Evidence/notice.txt", "notice, synthetic\n"),
                          ("Filings/complaint.md", "# Complaint (synthetic)\n")):
            with open(os.path.join(self.md, rel), "w", encoding="utf-8") as f:
                f.write(body)
        conn = store.connect(self.md)
        quiet(ingest.ingest_paths, conn, self.md, [self.md])
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def conn(self):
        return store.connect(self.md)


class Deadlines(Base):
    def add(self, label, due, **kw):
        conn = self.conn()
        try:
            return deadlines.add(conn, label, due, "human:test", **kw)
        finally:
            conn.close()

    def raw_due(self, did, value):
        conn = self.conn()  # stores from older kits can hold a date the strict parser refuses
        conn.execute("UPDATE deadlines SET due_date=? WHERE id=?", (value, did))
        conn.commit()
        conn.close()

    def test_passed_first_most_overdue_on_top_then_upcoming_then_malformed(self):
        self.add("Answer due", "2026-11-20")
        self.add("Discovery responses", "2026-11-03")
        bad = self.add("Hearing", "2026-12-01")
        self.raw_due(bad, "TBD")
        self.add("Initial disclosures", "2026-10-28")
        self.add("Status report", "2026-11-12")
        d = panel.panel_data(self.md, today=TODAY)
        self.assertEqual([x["label"] for x in d["deadlines"]],
                         ["Initial disclosures", "Discovery responses", "Status report", "Answer due", "Hearing"])
        self.assertEqual([x["passed"] for x in d["deadlines"]], [True, True, False, False, False])
        self.assertEqual([x["days"] for x in d["deadlines"]], [-13, -7, 2, 10, None])

    def test_due_today_is_not_passed(self):
        self.add("Today", TODAY.isoformat())
        (row,) = panel.panel_data(self.md, today=TODAY)["deadlines"]
        self.assertEqual((row["passed"], row["days"]), (False, 0))

    def test_closed_deadline_is_not_listed(self):
        did = self.add("Old one", "2026-10-01")
        conn = self.conn()
        deadlines.update(conn, self.md, did, "filed on time", "human:test", state="met")
        conn.close()
        d = panel.panel_data(self.md, today=TODAY)
        self.assertEqual(d["deadlines"], [])
        self.assertEqual(d["overview"]["passed_deadlines"], 0)

    def test_overview_counts_and_waiting_triggers(self):
        self.add("Late", "2026-11-01")
        self.add("Soon", "2026-11-15")
        conn = self.conn()
        triggers.add(conn, "Reply brief", "service of the response", 14, "human:test")
        conn.close()
        o = panel.panel_data(self.md, today=TODAY)["overview"]
        self.assertEqual((o["open_deadlines"], o["passed_deadlines"], o["waiting_triggers"]), (2, 1, 1))
        self.assertEqual(o["next_deadline"], {"label": "Soon", "due_date": "2026-11-15", "days": 5})


class Facts(Base):
    def test_counts_only_facts_without_a_source(self):
        conn = self.conn()
        facts.add(conn, self.md, "The lease began March 1.", "documented-fact", ["Evidence/lease.txt@p. 1"], by="human:test")
        facts.add(conn, self.md, "Notice was mailed.", "allegation", ["Evidence/notice.txt"], by="human:test")
        facts.add(conn, self.md, "The landlord knew of the leak.", "allegation", [], by="human:test")
        facts.add(conn, self.md, "Rent was paid in October.", "inference", [], by="human:test")
        conn.close()
        o = panel.panel_data(self.md, today=TODAY)["overview"]
        self.assertEqual((o["facts"], o["unsupported_facts"]), (4, 2))

    def test_empty_case(self):
        o = panel.panel_data(self.md, today=TODAY)["overview"]
        self.assertEqual((o["facts"], o["unsupported_facts"], o["open_deadlines"]), (0, 0, 0))
        self.assertIsNone(o["next_deadline"])


class Overview(Base):
    def test_title_from_manifest_else_folder_name(self):
        self.assertEqual(panel.panel_data(self.md, today=TODAY)["overview"]["title"], "Doe v. Acme")
        with open(os.path.join(self.md, "matter.json"), "w", encoding="utf-8") as f:
            json.dump({"title": "Doe v. Acme Property LLC"}, f)
        self.assertEqual(panel.panel_data(self.md, today=TODAY)["overview"]["title"], "Doe v. Acme Property LLC")

    def test_documents_by_kind_and_as_of(self):
        d = panel.panel_data(self.md, today=TODAY)
        self.assertEqual(d["overview"]["documents"], 3)
        self.assertEqual(sum(d["overview"]["documents_by_kind"].values()), 3)
        self.assertEqual(d["as_of"], "2026-11-10")

    def test_no_absolute_path_reaches_the_desktop(self):
        conn = self.conn()
        deadlines.add(conn, "Late", "2026-11-01", "human:test")
        conn.close()
        os.remove(os.path.join(self.md, "Evidence/notice.txt"))
        blob = json.dumps([panel.panel_data(self.md, today=TODAY), panel.panel_verify(self.md)])
        self.assertNotIn(self.tmp, blob)
        self.assertNotIn(os.path.realpath(self.tmp), blob)


class Tamper(Base):
    def test_intact(self):
        v = panel.panel_verify(self.md)
        self.assertEqual((v["state"], v["checked"], v["changed"], v["missing"], v["failures"]),
                         ("intact", 3, 0, 0, []))

    def test_edited_file_is_tampered(self):
        with open(os.path.join(self.md, "Evidence/lease.txt"), "a", encoding="utf-8") as f:
            f.write("one more clause\n")
        v = panel.panel_verify(self.md)
        self.assertEqual((v["state"], v["changed"], v["missing"]), ("tampered", 1, 0))
        self.assertEqual(v["failures"], [{"kind": "CHANGED", "rel_path": "Evidence/lease.txt"}])

    def test_deleted_file_is_missing(self):
        os.remove(os.path.join(self.md, "Filings/complaint.md"))
        v = panel.panel_verify(self.md)
        self.assertEqual((v["state"], v["changed"], v["missing"]), ("missing", 0, 1))
        self.assertEqual(v["failures"], [{"kind": "MISSING", "rel_path": "Filings/complaint.md"}])

    def test_edited_beats_missing(self):
        os.remove(os.path.join(self.md, "Filings/complaint.md"))
        with open(os.path.join(self.md, "Evidence/lease.txt"), "w", encoding="utf-8") as f:
            f.write("rewritten\n")
        v = panel.panel_verify(self.md)
        self.assertEqual((v["state"], v["changed"], v["missing"]), ("tampered", 1, 1))

    def test_no_documents_is_empty_not_intact(self):
        other = os.path.join(self.tmp, "Empty case")
        quiet(cases.init, other)
        self.assertEqual(panel.panel_verify(other)["state"], "empty")

    def test_failures_are_capped(self):
        for i in range(panel.MAX_FAILURES + 5):
            with open(os.path.join(self.md, f"Evidence/x{i:02}.txt"), "w", encoding="utf-8") as f:
                f.write(f"{i}\n")
        conn = self.conn()
        quiet(ingest.ingest_paths, conn, self.md, [self.md])
        conn.close()
        for i in range(panel.MAX_FAILURES + 5):
            os.remove(os.path.join(self.md, f"Evidence/x{i:02}.txt"))
        v = panel.panel_verify(self.md)
        self.assertEqual(v["missing"], panel.MAX_FAILURES + 5)
        self.assertEqual(len(v["failures"]), panel.MAX_FAILURES)


class ReadOnly(Base):
    def test_never_creates_a_store(self):
        bare = os.path.join(self.tmp, "not-a-case")
        os.makedirs(bare)
        with self.assertRaises(panel.PanelError):
            panel.panel_data(bare, today=TODAY)
        with self.assertRaises(panel.PanelError):
            panel.panel_verify(bare)
        self.assertFalse(os.path.exists(os.path.join(bare, ".matter")))

    def test_store_bytes_unchanged(self):
        db = store.db_path(self.md)
        with open(db, "rb") as f:
            before = f.read()
        panel.panel_data(self.md, today=TODAY)
        panel.panel_verify(self.md)
        with open(db, "rb") as f:
            self.assertEqual(f.read(), before)


class DemoScript(unittest.TestCase):
    """scripts/panel_demo.py: the case the video shows, and the tamper step."""
    def test_setup_then_tamper(self):
        import importlib.util
        kit = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location("panel_demo", os.path.join(kit, "scripts", "panel_demo.py"))
        demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)
        tmp = tempfile.mkdtemp(prefix="mk-panel-demo-")
        self.addCleanup(shutil.rmtree, tmp, True)
        md = demo.setup(os.path.join(tmp, "demo"), today=TODAY)
        self.assertFalse(os.path.exists(os.path.join(kit, "demo-matter", ".matter")))
        d = panel.panel_data(md, today=TODAY)
        o = d["overview"]
        self.assertEqual(o["title"], demo.TITLE)
        self.assertEqual((o["passed_deadlines"], o["open_deadlines"], o["waiting_triggers"]), (1, 3, 1))
        self.assertEqual((o["facts"], o["unsupported_facts"]), (2, 1))
        self.assertEqual([x["days"] for x in d["deadlines"]], [-6, 2, 45])
        self.assertEqual(panel.panel_verify(md)["state"], "intact")
        demo.tamper(md)
        v = panel.panel_verify(md)
        self.assertEqual((v["state"], v["failures"]), ("tampered", [{"kind": "CHANGED", "rel_path": demo.MEMO}]))
        with self.assertRaises(SystemExit):
            demo.setup(md)


if __name__ == "__main__":
    unittest.main()

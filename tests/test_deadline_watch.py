"""Deadline Watch: the morning check (matterkit/watch.py), the calendar export
(matterkit/calendar_export.py), `matter.py watch` / `deadline ics`, and the Hermes plugin's
/matter watch, /matter watch on and /matter calendar.

Ways it could go wrong, each pinned below:
- it messages every morning with nothing new (noise, so people mute it) -> silent unless news;
- a closed or far-off deadline is reported, or one on a reminder day is missed;
- a date's confidence label is dropped, so a working estimate reads as confirmed;
- a changed file's name (which can be sensitive) goes to Telegram;
- the morning check writes to the case store;
- a summary leaves the computer with no egress line, or the line carries the text itself;
- the calendar file breaks on commas, long or non-ASCII titles, or duplicates on re-import;
- /matter watch on overwrites a script it didn't write, or writes outside Hermes's scripts folder.
All data is synthetic. No test reaches the network.
"""
import datetime as dt
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import calendar_export, cases, consent, store, triggers, watch  # noqa: E402
from matterkit import deadlines as dl  # noqa: E402
from matterkit import ingest as ing  # noqa: E402

TODAY = dt.date(2026, 11, 9)
BY = "human:test"


def day(n):
    return (TODAY + dt.timedelta(days=n)).isoformat()


class CaseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "Smith v Acme")
        with redirect_stdout(io.StringIO()):
            cases.init(self.dir)
        self.conn = store.connect(self.dir)
        self.addCleanup(self.conn.close)

    def add(self, label, offset, **kw):
        return dl.add(self.conn, label, day(offset), BY, **kw)

    def evidence(self, name="Secret Witness Statement.txt", text="original"):
        path = os.path.join(self.dir, "Evidence", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        with redirect_stdout(io.StringIO()):
            ing.ingest_paths(self.conn, self.dir, [path])
        return path

    def report(self):
        return watch.report(self.dir, today=TODAY)

    def text(self):
        return watch.render(self.report())


class TestReport(CaseTest):
    def test_nothing_to_report_is_silent(self):
        self.add("Far off", 30)
        self.add("Between reminders", 5)
        self.assertEqual(self.text(), "")

    def test_overdue_and_reminder_days(self):
        self.add("Answer", -3)
        for n in (0, 1, 3, 7):
            self.add(f"Due in {n}", n)
        self.add("Due in 8", 8)
        text = self.text()
        self.assertIn("OVERDUE", text)
        self.assertIn("Answer: due " + day(-3) + ", 3 days ago", text)
        self.assertIn("Due in 0: due " + day(0) + ", today", text)
        self.assertIn("Due in 1: due " + day(1) + ", tomorrow", text)
        self.assertIn("in 3 days", text)
        self.assertIn("in 7 days", text)
        self.assertNotIn("Due in 8", text)

    def test_closed_deadlines_are_not_reported(self):
        did = self.add("Old answer", -10)
        dl.update(self.conn, self.dir, did, "filed", BY, state="met")
        self.assertEqual(self.text(), "")

    def test_confidence_labels_stay_on_every_date(self):
        self.add("Estimate", 1)
        self.add("Confirmed", 3, confidence="hard", verification="verified-official")
        text = self.text()
        self.assertIn("Estimate: due " + day(1) + ", tomorrow [working estimate, unverified]", text)
        self.assertIn("Confirmed: due " + day(3) + ", in 3 days [hard, verified official]", text)
        self.assertIn("not confirmed", text)

    def test_changed_and_missing_files_are_counts_not_names(self):
        p = self.evidence()
        q = self.evidence("Other.txt", "different")
        with open(p, "w") as f:
            f.write("edited")
        os.remove(q)
        r = self.report()
        self.assertEqual((r["changed"], r["missing"]), (1, 1))
        text = watch.render(r)
        self.assertIn("1 registered file changed and 1 is missing", text)
        self.assertNotIn("Secret Witness", text)
        self.assertNotIn("Other.txt", text)

    def test_waiting_triggers_alone_stay_silent_but_ride_along(self):
        triggers.add(self.conn, "Reply", "service of the answer", 14, BY)
        self.assertEqual(self.text(), "")
        self.add("Answer", -1)
        self.assertIn("1 deadline waits on an event", self.text())

    def test_an_unreadable_stored_date_is_reported(self):
        with self.conn:
            self.conn.execute("INSERT INTO deadlines (id,label,due_date) VALUES ('d_bad','Bad','next week')")
        self.assertIn("1 open deadline has a date that can't be read", self.text())

    def test_the_check_never_writes_the_store(self):
        self.add("Answer", -1)
        self.conn.close()
        path = store.db_path(self.dir)
        before = (os.path.getmtime(path), os.path.getsize(path))
        self.text()
        self.assertEqual(before, (os.path.getmtime(path), os.path.getsize(path)))

    def test_a_store_without_the_triggers_table_still_works(self):
        self.add("Answer", -1)
        with self.conn:
            self.conn.execute("DROP TABLE deadline_triggers")
        self.assertEqual(self.report()["waiting"], 0)

    def test_heading_names_the_case_and_date(self):
        self.add("Answer", 0)
        self.assertTrue(self.text().startswith(f"Corpus Open · Smith v Acme · {TODAY.isoformat()}"))


class TestCronMain(CaseTest):
    def run_cron(self, folder=None):
        out = io.StringIO()
        with redirect_stdout(out):
            watch.cron_main(folder or self.dir, today=TODAY)
        return out.getvalue()

    def egress(self):
        path = consent.egress_path(self.dir)
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f]

    def test_silent_run_prints_nothing_and_logs_nothing(self):
        self.assertEqual(self.run_cron(), "")
        self.assertEqual(self.egress(), [])

    def test_a_run_with_news_prints_it_and_logs_a_hash_only_line(self):
        self.add("Answer to complaint", -2)
        out = self.run_cron()
        self.assertIn("Answer to complaint", out)
        rows = self.egress()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider"], "hermes-cron")
        self.assertEqual(rows[0]["payload_type"], "deadline_watch_summary")
        self.assertNotIn("Answer to complaint", json.dumps(rows[0]))
        import hashlib
        # The hash is of exactly the bytes Hermes delivers (SWE-2 r1 P2-3: no trailing newline added).
        self.assertEqual(rows[0]["sha256"], hashlib.sha256(out.encode()).hexdigest())
        self.assertEqual(rows[0]["bytes"], len(out))

    def test_a_folder_that_is_no_longer_a_case_says_so(self):
        out = self.run_cron(os.path.join(self._tmp.name, "gone"))
        self.assertIn("isn't a case folder", out)
        self.assertNotIn(self._tmp.name, out)

    def assert_names_free_failure(self, out):
        self.assertIn("couldn't finish", out)
        self.assertIn("matter.py verify", out)
        for leak in ("Secret Witness", self._tmp.name, "Traceback", "Smith v Acme"):
            self.assertNotIn(leak, out)

    def test_an_unreadable_registered_file_sends_a_names_free_message(self):
        # SWE-2 r1 P2-1: a registered file replaced by a folder made verify raise with its path.
        p = self.evidence()
        os.remove(p)
        os.mkdir(p)
        out = self.run_cron()
        self.assert_names_free_failure(out)
        rows = self.egress()
        self.assertEqual(len(rows), 1)
        import hashlib
        self.assertEqual(rows[0]["sha256"], hashlib.sha256(out.encode()).hexdigest())

    def test_a_corrupt_store_sends_a_names_free_message(self):
        self.conn.close()
        with open(store.db_path(self.dir), "wb") as f:
            f.write(b"not a database at all" * 100)
        out = self.run_cron()
        self.assert_names_free_failure(out)


class TestCalendar(CaseTest):
    NOW = dt.datetime(2026, 11, 9, 14, 0, tzinfo=dt.timezone.utc)

    def ics(self):
        return calendar_export.build(self.dir, now=self.NOW)

    def unfolded(self, text):
        return text.replace("\r\n ", "")

    def test_structure_alarms_and_labels(self):
        did = self.add("Answer", 7, rule="Fed. R. Civ. P. 12(a)(1)(A)(i)", owner="ours")
        text, count = self.ics()
        self.assertEqual(count, 1)
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"))
        self.assertTrue(text.endswith("END:VCALENDAR\r\n"))
        self.assertNotIn("\n", text.replace("\r\n", ""))
        flat = self.unfolded(text)
        self.assertIn(f"UID:{did}@corpus-open", flat)
        self.assertIn("DTSTART;VALUE=DATE:" + day(7).replace("-", ""), flat)
        self.assertIn("DTEND;VALUE=DATE:" + day(8).replace("-", ""), flat)
        self.assertIn("SUMMARY:Answer [working estimate\\, unverified]", flat)
        for n in (7, 3, 1):
            self.assertIn(f"TRIGGER:-P{n}D", flat)
        self.assertEqual(flat.count("BEGIN:VALARM"), 3)
        self.assertIn("DTSTAMP:20261109T140000Z", flat)

    def test_same_uid_on_every_export_so_reimport_updates(self):
        self.add("Answer", 7)
        self.assertEqual(re.findall(r"UID:\S+", self.ics()[0]), re.findall(r"UID:\S+", self.ics()[0]))

    def test_escaping_and_folding(self):
        label = "Reply; motion, to dismiss \\ " + "é" * 60
        self.add(label, 3)
        text, _ = self.ics()
        for line in text.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75, line)
        text.encode("utf-8").decode("utf-8")  # folding never split a character
        self.assertIn("SUMMARY:Reply\\; motion\\, to dismiss \\\\ " + "é" * 60, self.unfolded(text))

    def test_closed_and_waiting_are_left_out_and_overdue_kept(self):
        did = self.add("Met", 2)
        dl.update(self.conn, self.dir, did, "done", BY, state="met")
        self.add("Late", -2)
        triggers.add(self.conn, "Reply", "service", 14, BY)
        text, count = self.ics()
        self.assertEqual(count, 1)
        self.assertIn("SUMMARY:Late", text)
        self.assertNotIn("Met", text)
        self.assertNotIn("Reply", text)

    def test_write_puts_it_under_matter_exports(self):
        self.add("Answer", 7)
        path, count = calendar_export.write(self.dir, now=self.NOW)
        self.assertEqual(path, os.path.join(self.dir, ".matter", "exports", "deadlines.ics"))
        self.assertEqual(count, 1)
        with open(path, newline="") as f:
            self.assertIn("\r\n", f.read())


class TestCli(CaseTest):
    def cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *args],
                              capture_output=True, text=True, timeout=60)

    def test_watch_prints_the_report_or_says_all_clear(self):
        r = self.cli("watch", self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Nothing to report", r.stdout)
        dl.add(self.conn, "Answer", (dt.date.today() - dt.timedelta(days=1)).isoformat(), BY)
        r = self.cli("watch", self.dir)
        self.assertIn("OVERDUE", r.stdout)

    def test_deadline_ics_to_stdout_and_file(self):
        self.add("Answer", 7)
        r = self.cli("deadline", "ics", self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("BEGIN:VCALENDAR", r.stdout)
        out = os.path.join(self._tmp.name, "cal.ics")
        r = self.cli("deadline", "ics", self.dir, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 deadline", r.stdout)
        self.assertTrue(os.path.isfile(out))


if __name__ == "__main__":
    unittest.main()

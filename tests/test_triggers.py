"""Trigger deadlines: waiting rows, firing into a dated deadline, recorded holidays.

Design: docs/superpowers/specs/2026-11-01-trigger-deadlines-design.md, § 4. How this could go wrong,
and the test that would catch it:
- firing changes an existing deadline or fires twice -> test_fire_inserts_one_deadline_and_two_events,
  test_a_second_fire_is_refused_and_writes_nothing
- firing from an unregistered or changed source -> test_fire_refusals_write_nothing
- a computed date is read as verified -> test_fire_inserts_one_deadline_and_two_events (working-estimate,
  unverified, "computed and not checked")
- the deadline row and its events disagree -> test_fire_is_one_transaction
- a year with no recorded state holidays is counted anyway -> test_fire_refusals_write_nothing
- an old store breaks -> test_an_old_store_gains_the_tables
- a dry run cannot preview an event still ahead, or a real fire records one -> test_a_dry_run_may_look_ahead*

Synthetic tempdirs only. Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import store, triggers  # noqa: E402

CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("MATTER_AUTHOR", "MATTER_DIRECTED_BY")}
LIST = "Research/state-holidays.txt@§ 1"
ORDER = "Filings/order.txt@p. 1"
NOW = date(2026, 11, 1)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.run_cli("init", self.dir)
        for rel, text in (("Filings/order.txt", "synthetic order granting leave\n"),
                          ("Research/state-holidays.txt", "synthetic list of Example State holidays\n")):
            os.makedirs(os.path.join(self.dir, os.path.dirname(rel)), exist_ok=True)
            with open(os.path.join(self.dir, rel), "w") as f:
                f.write(text)
        self.run_cli("ingest", self.dir)
        self.conn = store.connect(self.dir)
        self.addCleanup(self.conn.close)

    def run_cli(self, *argv, env=None, check=True):
        return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *argv],
                              capture_output=True, text=True, check=check, env=env or CLEAN_ENV)

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def counts(self):
        return {t: self.count(t) for t in ("deadlines", "events", "deadline_triggers", "holidays", "holiday_years")}

    def year(self, y=2026):
        triggers.holiday_year(self.conn, self.dir, y, "Example State", [LIST], "human")

    def trigger(self, label="Reply brief", days=14, **kw):
        return triggers.add(self.conn, label, "the response to the motion is served", days, "human", **kw)

    def fire(self, tid, on="2026-10-05", service="ecf", sources=(ORDER,), reason="Response served", **kw):
        return triggers.fire(self.conn, self.dir, tid, on, service, list(sources), reason, "human", today=NOW, **kw)


class TestTriggerStore(Base):
    def test_add_records_a_waiting_row_and_one_event(self):
        n = self.count("events")
        tid = self.trigger(rule="L.U.Civ.R. 7(b)(4)", owner="ours")
        row = self.conn.execute("SELECT * FROM deadline_triggers WHERE id=?", (tid,)).fetchone()
        self.assertEqual((row["state"], row["days"], row["rule_source"], row["owner"], row["created_by"]),
                         ("waiting", 14, "L.U.Civ.R. 7(b)(4)", "ours", "human"))
        self.assertIsNone(row["deadline_id"])
        self.assertEqual(self.count("events"), n + 1)
        self.assertIn(f"trigger {tid} added", self.conn.execute("SELECT text FROM events ORDER BY rowid DESC")
                      .fetchone()[0])

    def test_add_refusals_write_nothing(self):
        before = self.counts()
        for kw in ({"days": 0}, {"days": -7}, {"days": True}, {"days": "7"}, {"label": " "},
                   {"owner": "nobody"}):
            args = {"label": "Reply brief", "days": 14} | kw
            owner = args.pop("owner", None)
            with self.assertRaises(triggers.TriggerError, msg=kw):
                triggers.add(self.conn, args["label"], "the response is served", args["days"], "human", owner=owner)
        with self.assertRaises(triggers.TriggerError):
            triggers.add(self.conn, "Reply brief", "", 14, "human")
        self.assertEqual(self.counts(), before)

    def test_fire_inserts_one_deadline_and_two_events(self):
        self.year()
        tid = self.trigger(rule="L.U.Civ.R. 7(b)(4)", owner="ours")
        before = self.counts()
        did, due, steps = self.fire(tid, service="mail")
        self.assertEqual(due, date(2026, 10, 22))  # Mon 10-05 + 14 = Mon 10-19, + 3 for mail = Thu 10-22
        d = self.conn.execute("SELECT * FROM deadlines WHERE id=?", (did,)).fetchone()
        self.assertEqual((d["label"], d["due_date"], d["rule_source"], d["owner"], d["state"], d["confidence"],
                          d["verification_status"]),
                         ("Reply brief", "2026-10-22", "L.U.Civ.R. 7(b)(4)", "ours", "open", "working-estimate",
                          "unverified"))
        t = self.conn.execute("SELECT * FROM deadline_triggers WHERE id=?", (tid,)).fetchone()
        self.assertEqual((t["state"], t["fired_on"], t["service"], t["deadline_id"]),
                         ("fired", "2026-10-05", "mail", did))
        after = self.counts()
        self.assertEqual(after["deadlines"], before["deadlines"] + 1)
        self.assertEqual(after["events"], before["events"] + 2)
        added, fired = self.conn.execute("SELECT text, sources FROM events ORDER BY rowid DESC LIMIT 2").fetchall()[::-1]
        self.assertIn(f"deadline {did} added", added[0])
        self.assertIn("computed and not checked", fired[0])
        self.assertIn("reason: Response served", fired[0])
        self.assertIn("6(d)", fired[0])
        self.assertEqual(json.loads(fired[1]), [ORDER])
        self.assertTrue(any("Thursday 2026-10-22" in s for s in steps))

    def test_a_second_fire_is_refused_and_writes_nothing(self):
        self.year()
        tid = self.trigger()
        did, _, _ = self.fire(tid)
        before = self.counts()
        with self.assertRaises(triggers.TriggerError) as cm:
            self.fire(tid, on="2026-10-06")
        self.assertIn(did, str(cm.exception))
        self.assertEqual(self.counts(), before)

    def test_fire_refusals_write_nothing(self):
        tid = self.trigger()
        before = self.counts()
        with self.assertRaises(triggers.TriggerError) as cm:  # no holiday list for 2026
            self.fire(tid)
        self.assertIn("2026", str(cm.exception))
        self.year()
        before = self.counts()
        with open(os.path.join(self.dir, "Filings", "changed.txt"), "w") as f:
            f.write("v1\n")
        self.run_cli("ingest", self.dir)
        with open(os.path.join(self.dir, "Filings", "changed.txt"), "w") as f:
            f.write("v2\n")
        before = self.counts()
        cases = [{"sources": ()}, {"sources": ("Filings/ghost.txt@p. 1",)}, {"sources": ("Filings/changed.txt",)},
                 {"reason": " "}, {"reason": None}, {"on": "2099-01-01"}, {"on": "10/05/2026"},
                 {"service": "fax"}]
        for kw in cases:
            with self.assertRaises(triggers.TriggerError, msg=kw):
                self.fire(tid, **kw)
        with self.assertRaises(triggers.TriggerError):
            triggers.fire(self.conn, self.dir, "t_nope", "2026-10-05", "ecf", [ORDER], "x", "human", today=NOW)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.conn.execute("SELECT state FROM deadline_triggers WHERE id=?", (tid,)).fetchone()[0],
                         "waiting")

    def test_a_dry_run_counts_and_writes_nothing(self):
        self.year()
        tid = self.trigger()
        before = self.counts()
        did, due, steps = self.fire(tid, sources=(), reason=None, dry_run=True)
        self.assertIsNone(did)
        self.assertEqual(due, date(2026, 10, 19))
        self.assertTrue(steps)
        self.assertEqual(self.counts(), before)

    def test_a_dry_run_may_look_ahead_but_a_fire_may_not(self):
        self.year()
        self.year(2027)
        tid = self.trigger()
        before = self.counts()
        # Fri 2026-12-18 + 14 = Fri 2027-01-01, New Year's Day, so the period runs to Mon 2027-01-04
        did, due, steps = self.fire(tid, on="2026-12-18", sources=(), reason=None, dry_run=True)
        self.assertIsNone(did)
        self.assertEqual(due, date(2027, 1, 4))
        self.assertTrue(steps)
        with self.assertRaises(triggers.TriggerError) as cm:  # 2028 has no recorded state holidays
            self.fire(tid, on="2027-12-27", sources=(), reason=None, dry_run=True)
        self.assertIn("2028", str(cm.exception))
        with self.assertRaises(triggers.TriggerError) as cm:
            self.fire(tid, on="2026-12-18")
        self.assertIn("after today", str(cm.exception))
        self.assertEqual(self.counts(), before)

    def test_fire_is_one_transaction(self):
        self.year()
        tid = self.trigger()
        before = self.counts()
        self.conn.execute("CREATE TRIGGER no_events BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'boom'); END")
        self.conn.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            self.fire(tid)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.conn.execute("SELECT state FROM deadline_triggers WHERE id=?", (tid,)).fetchone()[0],
                         "waiting")

    def test_withdraw(self):
        self.year()
        tid = self.trigger()
        with self.assertRaises(triggers.TriggerError):
            triggers.withdraw(self.conn, tid, " ", "human")
        text = triggers.withdraw(self.conn, tid, "Motion withdrawn", "human")
        self.assertIn("waiting→withdrawn", text)
        before = self.counts()
        for call in (lambda: triggers.withdraw(self.conn, tid, "again", "human"), lambda: self.fire(tid)):
            with self.assertRaises(triggers.TriggerError):
                call()
        self.assertEqual(self.counts(), before)

    def test_a_recorded_holiday_moves_the_fired_date(self):
        self.year()
        triggers.holiday_add(self.conn, self.dir, "2026-10-19", "Example State Day", "state", [LIST], "human")
        _, due, steps = self.fire(self.trigger())
        self.assertEqual(due, date(2026, 10, 20))
        self.assertTrue(any("Example State Day" in s and "6(a)(6)(C)" in s for s in steps), steps)

    def test_holiday_refusals_write_nothing(self):
        self.year()
        triggers.holiday_add(self.conn, self.dir, "2026-10-19", "Example State Day", "state", [LIST], "human")
        before = self.counts()
        for args in (("2026-10-19", "Again", "state", [LIST]),       # already recorded
                     ("2026-13-01", "Bad", "state", [LIST]),
                     ("2026-10-20", " ", "state", [LIST]),
                     ("2026-10-20", "Day", "federal", [LIST]),
                     ("2026-10-20", "Day", "closure", []),
                     ("2026-10-20", "Day", "closure", ["Research/ghost.txt"])):
            with self.assertRaises(triggers.TriggerError, msg=args):
                triggers.holiday_add(self.conn, self.dir, *args, "human")
        for year, src in ((2026, [LIST]), (1066, [LIST]), (True, [LIST]), (2027, [])):
            with self.assertRaises(triggers.TriggerError, msg=year):
                triggers.holiday_year(self.conn, self.dir, year, "Example State", src, "human")
        self.assertEqual(self.counts(), before)

    def test_an_old_store_gains_the_tables(self):
        self.conn.execute("INSERT INTO deadlines (id,label,due_date) VALUES ('d_old','Old one','2099-01-01')")
        for t in ("deadline_triggers", "holidays", "holiday_years"):
            self.conn.execute(f"DROP TABLE {t}")
        self.conn.commit()
        conn = store.connect(self.dir)
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertLessEqual({"deadline_triggers", "holidays", "holiday_years"}, names)
            self.assertEqual(conn.execute("SELECT label FROM deadlines WHERE id='d_old'").fetchone()[0], "Old one")
        finally:
            conn.close()


class TestTriggerCli(Base):
    def setUp(self):
        super().setUp()
        self.run_cli("holiday", "year", self.dir, "--year", "2026", "--jurisdiction", "Example State",
                     "--source", LIST)

    def add_cli(self, *extra):
        out = self.run_cli("trigger", "add", self.dir, "--label", "Reply brief", "--days", "14",
                           "--waits-on", "the response is served", *extra).stdout
        return self.conn.execute("SELECT id FROM deadline_triggers ORDER BY rowid DESC").fetchone()[0], out

    def test_the_whole_flow(self):
        tid, out = self.add_cli("--rule", "L.U.Civ.R. 7(b)(4)", "--owner", "ours")
        self.assertIn(tid, out)
        self.assertIn(tid, self.run_cli("trigger", "list", self.dir).stdout)
        listing = self.run_cli("deadline", "list", self.dir).stdout
        self.assertIn("waiting on", listing)
        self.assertIn("14 days after the response is served", listing)
        dry = self.run_cli("trigger", "fire", self.dir, tid, "--on", "2026-10-05", "--service", "ecf",
                           "--dry-run").stdout
        self.assertIn("Monday 2026-10-19", dry)
        self.assertIn("nothing was written", dry)
        self.assertEqual(self.count("deadlines"), 0)
        out = self.run_cli("trigger", "fire", self.dir, tid, "--on", "2026-10-05", "--service", "ecf",
                           "--source", ORDER, "--reason", "Response served").stdout
        self.assertIn("2026-10-19", out)
        self.assertIn("working-estimate/unverified", out)
        self.assertIn("6(a)(1)(C)", out)
        listing = self.run_cli("deadline", "list", self.dir).stdout
        self.assertIn("2026-10-19", listing)
        self.assertNotIn("waiting on", listing)
        self.assertIn("fired 2026-10-05", self.run_cli("trigger", "list", self.dir, "--all").stdout)

    def test_deadline_list_without_triggers_is_unchanged(self):
        self.run_cli("deadline", "add", self.dir, "--label", "Dated", "--due", "2099-01-01")
        self.assertNotIn("waiting on", self.run_cli("deadline", "list", self.dir).stdout)

    def test_refusals_exit_nonzero(self):
        tid, _ = self.add_cli()
        r = self.run_cli("trigger", "fire", self.dir, tid, "--on", "2026-10-05", "--service", "ecf",
                         "--source", "Filings/ghost.txt", "--reason", "x", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refused:", r.stderr)
        r = self.run_cli("trigger", "add", self.dir, "--label", "Back", "--days", "-7", "--waits-on", "hearing",
                         check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.count("deadlines"), 0)

    def test_a_dry_run_may_look_ahead(self):
        tid, _ = self.add_cli()
        ahead = date.today() + timedelta(days=30)
        for y in sorted({ahead.year, (ahead + timedelta(days=30)).year} - {2026}):
            self.run_cli("holiday", "year", self.dir, "--year", str(y), "--jurisdiction", "Example State",
                         "--source", LIST)
        out = self.run_cli("trigger", "fire", self.dir, tid, "--on", ahead.isoformat(), "--service", "ecf",
                           "--dry-run").stdout
        self.assertIn("nothing was written", out)
        r = self.run_cli("trigger", "fire", self.dir, tid, "--on", ahead.isoformat(), "--service", "ecf",
                         "--source", ORDER, "--reason", "x", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("after today", r.stderr)
        self.assertEqual(self.count("deadlines"), 0)

    def test_attribution_is_recorded(self):
        tid, _ = self.add_cli("--author", "agent:model-x", "--directed-by", "Pat Example")
        self.assertEqual(self.conn.execute("SELECT created_by FROM deadline_triggers WHERE id=?", (tid,))
                         .fetchone()[0], "agent:model-x; directed-by:Pat Example")
        self.run_cli("trigger", "fire", self.dir, tid, "--on", "2026-10-05", "--service", "ecf", "--source", ORDER,
                     "--reason", "Served", env=dict(CLEAN_ENV, MATTER_AUTHOR="agent:model-y"))
        self.assertEqual(self.conn.execute("SELECT actors FROM events ORDER BY rowid DESC").fetchone()[0],
                         "agent:model-y")

    def test_holiday_list(self):
        self.run_cli("holiday", "add", self.dir, "--on", "2026-04-27", "--name", "Example State Day",
                     "--kind", "state", "--source", LIST)
        out = self.run_cli("holiday", "list", self.dir, "--year", "2026").stdout
        self.assertIn("recorded as complete for Example State", out)
        self.assertIn("Monday 2026-04-27  Example State Day", out)
        self.assertIn("Friday 2026-07-03  Independence Day (observed)", out)
        self.assertIn("NOT recorded as complete", self.run_cli("holiday", "list", self.dir, "--year", "2027").stdout)


class TestTriggerMcp(Base):
    def _call(self, name, arguments):
        reqs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}]
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")],
                           input="\n".join(json.dumps(r) for r in reqs) + "\n", capture_output=True,
                           text=True, env=dict(CLEAN_ENV, MATTER_DIR=self.dir), check=True)
        return json.loads(p.stdout.strip().splitlines()[-1])["result"]

    def test_tools_are_declared(self):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input=json.dumps(req) + "\n",
                           capture_output=True, text=True, env=dict(CLEAN_ENV, MATTER_DIR=self.dir), check=True)
        tools = {t["name"]: t for t in json.loads(p.stdout)["result"]["tools"]}
        self.assertIn("matter.trigger_list", tools)
        schema = tools["matter.trigger_fire"]["inputSchema"]
        self.assertEqual(set(schema["required"]), {"trigger_id", "on", "service"})
        self.assertEqual(set(schema["properties"]["service"]["enum"]), set(triggers.SERVICES))

    def test_fire_and_list(self):
        self.year()
        tid = self.trigger(owner="theirs")
        rows = json.loads(self._call("matter.trigger_list", {})["content"][0]["text"])
        self.assertEqual([r["id"] for r in rows], [tid])
        res = self._call("matter.trigger_fire", {"trigger_id": tid, "on": "2026-10-05", "service": "hand",
                                                 "sources": [ORDER], "reason": "Served by hand"})
        self.assertNotIn("isError", res, res)
        self.assertIn("2026-10-19", res["content"][0]["text"])
        self.assertEqual(self.conn.execute("SELECT actors FROM events ORDER BY rowid DESC").fetchone()[0], "agent")
        self.assertEqual(json.loads(self._call("matter.trigger_list", {})["content"][0]["text"]), [])
        rows = json.loads(self._call("matter.trigger_list", {"all": True})["content"][0]["text"])
        self.assertEqual(rows[0]["state"], "fired")

    def test_refusals_are_errors_and_change_nothing(self):
        self.year()
        tid = self.trigger()
        before = self.counts()
        for args in ({"trigger_id": tid, "on": "2026-10-05", "service": "ecf", "sources": [ORDER]},  # no reason
                     {"trigger_id": tid, "on": "2026-10-05", "service": "ecf", "reason": "x"},       # no source
                     {"trigger_id": tid, "on": "2099-01-01", "service": "ecf", "sources": [ORDER], "reason": "x"},
                     {"trigger_id": tid, "on": "2026-10-05", "service": "fax", "sources": [ORDER], "reason": "x"},
                     {"trigger_id": "t_nope", "on": "2026-10-05", "service": "ecf", "sources": [ORDER],
                      "reason": "x"}):
            self.assertTrue(self._call("matter.trigger_fire", args).get("isError"), args)
        self.assertEqual(self.counts(), before)
        res = self._call("matter.trigger_fire", {"trigger_id": tid, "on": "2026-10-05", "service": "ecf",
                                                 "dry_run": True})
        self.assertIn("nothing was written", res["content"][0]["text"])
        self.assertEqual(self.counts(), before)

    def test_a_dry_run_may_look_ahead(self):
        ahead = date.today() + timedelta(days=30)
        for y in sorted({ahead.year, (ahead + timedelta(days=30)).year}):
            self.year(y)
        tid = self.trigger()
        before = self.counts()
        res = self._call("matter.trigger_fire", {"trigger_id": tid, "on": ahead.isoformat(), "service": "ecf",
                                                 "dry_run": True})
        self.assertFalse(res.get("isError"), res)
        self.assertIn("nothing was written", res["content"][0]["text"])
        self.assertEqual(self.counts(), before)


if __name__ == "__main__":
    unittest.main()

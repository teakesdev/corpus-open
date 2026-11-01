"""Deadline lifecycle: update with a reason, an events trail, OVERDUE in list.

Synthetic tempdirs only. Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import store  # noqa: E402

CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("MATTER_AUTHOR", "MATTER_DIRECTED_BY")}
TODAY = date.today().isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.run_cli("init", self.dir)
        os.makedirs(os.path.join(self.dir, "Filings"), exist_ok=True)
        with open(os.path.join(self.dir, "Filings", "order.txt"), "w") as f:
            f.write("synthetic order text\n")
        self.run_cli("ingest", self.dir)

    def run_cli(self, *argv, env=None, check=True):
        return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *argv],
                              capture_output=True, text=True, check=check, env=env or CLEAN_ENV)

    def add(self, label="Reply brief", due="2099-01-01", *extra):
        self.run_cli("deadline", "add", self.dir, "--label", label, "--due", due, *extra)
        return self.rows("SELECT id FROM deadlines WHERE label=?", (label,))[0][0]

    def rows(self, sql, params=()):
        conn = sqlite3.connect(os.path.join(self.dir, ".matter", "matter.db"))
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def events(self):
        return self.rows("SELECT id, occurred_on, text, actors, sources FROM events ORDER BY rowid")

    def deadline(self, did):
        return self.rows("SELECT state, verification_status, confidence, due_date FROM deadlines WHERE id=?",
                         (did,))[0]


class TestDeadlineUpdate(Base):
    def test_state_change_updates_row_and_writes_one_event(self):
        did = self.add()
        n_before = len(self.events())
        out = self.run_cli("deadline", "update", self.dir, did, "--state", "superseded",
                           "--reason", "Court reset the date by order").stdout
        self.assertIn(did, out)
        self.assertEqual(self.deadline(did)[0], "superseded")
        evs = self.events()
        self.assertEqual(len(evs), n_before + 1)
        eid, occurred, text, actors, sources = evs[-1]
        self.assertTrue(eid.startswith("ev_"))
        self.assertEqual(occurred, TODAY)
        self.assertIn(f"deadline {did} 'Reply brief':", text)
        self.assertIn("state open→superseded", text)
        self.assertIn("reason: Court reset the date by order", text)
        self.assertEqual(actors, "human")
        self.assertEqual(json.loads(sources), [])

    def test_multiple_fields_and_source_recorded(self):
        did = self.add()
        self.run_cli("deadline", "update", self.dir, did, "--verification", "verified-official",
                     "--confidence", "hard", "--due", "2099-02-03",
                     "--source", "Filings/order.txt@p. 2", "--reason", "Order sets the date")
        self.assertEqual(self.deadline(did), ("open", "verified-official", "hard", "2099-02-03"))
        _, _, text, _, sources = self.events()[-1]
        self.assertIn("verification unverified→verified-official", text)
        self.assertIn("confidence working-estimate→hard", text)
        self.assertIn("due 2099-01-01→2099-02-03", text)
        self.assertNotIn("state ", text)  # state was not changed, so not mentioned
        self.assertEqual(json.loads(sources), ["Filings/order.txt@p. 2"])

    def test_source_by_document_id_is_stored_as_rel_path(self):
        did = self.add()
        doc_id = self.rows("SELECT id FROM documents WHERE rel_path='Filings/order.txt'")[0][0]
        self.run_cli("deadline", "update", self.dir, did, "--state", "met", "--source", doc_id,
                     "--reason", "Filed")
        self.assertEqual(json.loads(self.events()[-1][4]), ["Filings/order.txt"])

    def test_every_state_accepted(self):
        for state in ("open", "met", "superseded", "lapsed", "vacated"):
            did = self.add(f"d-{state}")
            if state == "open":  # a deadline starts open and a no-op update is refused: leave it first
                self.run_cli("deadline", "update", self.dir, did, "--state", "met", "--reason", "setup")
            self.run_cli("deadline", "update", self.dir, did, "--state", state, "--reason", "test")
            self.assertEqual(self.deadline(did)[0], state)

    def test_unknown_state_refused_and_nothing_changes(self):
        did = self.add()
        n = len(self.events())
        r = self.run_cli("deadline", "update", self.dir, did, "--state", "cancelled",
                         "--reason", "x", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid choice: 'cancelled'", r.stderr)  # the state check, not an unknown subcommand
        self.assertEqual(self.deadline(did)[0], "open")
        self.assertEqual(len(self.events()), n)

    def test_missing_or_blank_reason_refused(self):
        did = self.add()
        n = len(self.events())
        for reason in ([], ["--reason", ""], ["--reason", "   "]):
            r = self.run_cli("deadline", "update", self.dir, did, "--state", "met", *reason, check=False)
            self.assertNotEqual(r.returncode, 0, reason)
            self.assertIn("--reason", r.stderr, reason)
        self.assertEqual(self.deadline(did)[0], "open")
        self.assertEqual(len(self.events()), n)

    def test_unknown_id_refused_with_message(self):
        n = len(self.events())
        r = self.run_cli("deadline", "update", self.dir, "d_nope", "--state", "met",
                         "--reason", "x", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("d_nope", r.stderr)
        self.assertIn("unknown deadline", r.stderr)
        self.assertEqual(len(self.events()), n)

    def test_unregistered_source_refused_and_nothing_changes(self):
        did = self.add()
        n = len(self.events())
        r = self.run_cli("deadline", "update", self.dir, did, "--state", "met",
                         "--source", "Filings/order.txt@p. 1", "--source", "Filings/ghost.pdf@p. 1",
                         "--reason", "x", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Filings/ghost.pdf", r.stderr)
        self.assertEqual(self.deadline(did)[0], "open")
        self.assertEqual(len(self.events()), n)

    def test_needs_at_least_one_field(self):
        did = self.add()
        r = self.run_cli("deadline", "update", self.dir, did, "--reason", "nothing to change", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nothing to update", r.stderr)

    def test_no_op_values_refused(self):
        did = self.add()
        n = len(self.events())
        r = self.run_cli("deadline", "update", self.dir, did, "--state", "open", "--reason", "x", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no change", r.stderr)
        self.assertEqual(len(self.events()), n)

    def test_bad_due_refused(self):
        did = self.add()
        for bad in ("2026-13-45", "11/02/2026", "20261102", "tomorrow", "2026-1-5"):
            r = self.run_cli("deadline", "update", self.dir, did, "--due", bad, "--reason", "x", check=False)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("YYYY-MM-DD", r.stderr, bad)
        self.assertEqual(self.deadline(did)[3], "2099-01-01")

    def test_update_and_event_are_one_transaction(self):
        from matterkit import deadlines as dl
        did = self.add()
        conn = store.connect(self.dir)
        conn.execute("CREATE TRIGGER no_events BEFORE INSERT ON events "
                     "BEGIN SELECT RAISE(ABORT, 'boom'); END")
        conn.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            dl.update(conn, self.dir, did, reason="x", state="met", by="human")
        conn.close()
        self.assertEqual(self.deadline(did)[0], "open")  # update rolled back with the failed event


class TestDeadlineAttribution(Base):
    def test_agent_author_recorded_not_human(self):
        did = self.add()
        self.run_cli("deadline", "update", self.dir, did, "--state", "met", "--reason", "Filed",
                     "--author", "agent:model-x", "--directed-by", "Pat Example")
        self.assertEqual(self.events()[-1][3], "agent:model-x; directed-by:Pat Example")

    def test_env_defaults(self):
        did = self.add()
        env = dict(CLEAN_ENV, MATTER_AUTHOR="agent:model-y", MATTER_DIRECTED_BY="Pat Example")
        self.run_cli("deadline", "update", self.dir, did, "--state", "met", "--reason", "Filed", env=env)
        self.assertEqual(self.events()[-1][3], "agent:model-y; directed-by:Pat Example")

    def test_directed_by_without_agent_refused_and_nothing_changes(self):
        did = self.add()
        n = len(self.events())
        r = self.run_cli("deadline", "update", self.dir, did, "--state", "met", "--reason", "x",
                         "--directed-by", "Pat Example", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid attribution", r.stderr)
        self.assertEqual(self.deadline(did)[0], "open")
        self.assertEqual(len(self.events()), n)


class TestDeadlineAdd(Base):
    def test_add_keeps_output_and_records_actor_event(self):
        out = self.run_cli("deadline", "add", self.dir, "--label", "Answer due", "--due", "2099-03-04",
                           "--rule", "FRCP 12(a)", "--author", "agent:model-x").stdout
        self.assertEqual(out.strip(), "deadline registered: 2099-03-04 — Answer due")
        did = self.rows("SELECT id FROM deadlines WHERE label='Answer due'")[0][0]
        eid, occurred, text, actors, sources = self.events()[-1]
        self.assertTrue(eid.startswith("ev_"))
        self.assertEqual(occurred, TODAY)
        self.assertIn(f"deadline {did} added:", text)
        self.assertIn("Answer due", text)
        self.assertIn("2099-03-04", text)
        self.assertEqual(actors, "agent:model-x")

    def test_add_defaults_to_human_and_keeps_default_labels(self):
        did = self.add("Plain")
        self.assertEqual(self.events()[-1][3], "human")
        self.assertEqual(self.deadline(did)[1:3], ("unverified", "working-estimate"))

    def test_add_without_due_is_a_clean_refusal(self):
        r = self.run_cli("deadline", "add", self.dir, "--label", "No date", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)

    def test_add_and_event_are_one_transaction(self):
        from matterkit import deadlines as dl
        conn = store.connect(self.dir)
        conn.execute("CREATE TRIGGER no_events BEFORE INSERT ON events "
                     "BEGIN SELECT RAISE(ABORT, 'boom'); END")
        conn.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            dl.add(conn, label="L", due="2099-01-01", by="human")
        conn.close()
        self.assertEqual(self.rows("SELECT COUNT(*) FROM deadlines")[0][0], 0)


class TestDeadlineListOverdue(Base):
    def test_open_past_due_marked_overdue_future_and_today_not(self):
        self.add("Past one", "2020-01-02")
        self.add("Future one", "2099-01-02")
        self.add("Today one", TODAY)
        out = self.run_cli("deadline", "list", self.dir).stdout.splitlines()
        by = {l.split("  ")[1].split(" (")[0]: l for l in out}
        self.assertIn("⚠ OVERDUE", by["Past one"])
        self.assertNotIn("OVERDUE", by["Future one"])
        self.assertNotIn("OVERDUE", by["Today one"])
        # existing columns still present
        self.assertTrue(by["Past one"].startswith("2020-01-02  Past one"))
        self.assertIn("(working-estimate/unverified)", by["Past one"])
        self.assertIn("[—]", by["Past one"])

    def test_all_shows_state_and_only_open_rows_can_be_overdue(self):
        did = self.add("Past met", "2020-01-02")
        self.add("Past open", "2020-01-03")
        self.run_cli("deadline", "update", self.dir, did, "--state", "met", "--reason", "Filed on time")
        default = self.run_cli("deadline", "list", self.dir).stdout
        self.assertNotIn("Past met", default)       # non-open hidden without --all
        self.assertNotIn("state=", default)         # default format unchanged
        allout = self.run_cli("deadline", "list", self.dir, "--all").stdout.splitlines()
        met = next(l for l in allout if "Past met" in l)
        opn = next(l for l in allout if "Past open" in l)
        self.assertIn("state=met", met)
        self.assertNotIn("OVERDUE", met)
        self.assertIn("state=open", opn)
        self.assertIn("OVERDUE", opn)

    def test_malformed_stored_date_does_not_crash_list(self):
        conn = store.connect(self.dir)
        conn.execute("INSERT INTO deadlines (id,label,due_date) VALUES ('d_bad','Odd','sometime soon')")
        conn.commit()
        conn.close()
        r = self.run_cli("deadline", "list", self.dir)
        self.assertIn("Odd", r.stdout)
        self.assertNotIn("OVERDUE", [l for l in r.stdout.splitlines() if "Odd" in l][0])


class TestDeadlineOwner(Base):
    """Who must act by a deadline: ours, theirs (the other side) or court. Unset is allowed and is the default.

    How this could fail: an old store without the column breaks list or update; an owner change leaves no
    trail; a bad value is stored; the list's existing columns move for deadlines with no owner."""

    def owner(self, did):
        return self.rows("SELECT owner FROM deadlines WHERE id=?", (did,))[0][0]

    def test_add_with_owner_stores_it_and_names_it_in_the_event(self):
        did = self.add("Their answer", "2099-01-05", "--owner", "theirs")
        self.assertEqual(self.owner(did), "theirs")
        self.assertIn("owner: theirs", self.events()[-1][2])

    def test_add_without_owner_leaves_it_unset_and_the_line_unchanged(self):
        did = self.add("Plain", "2099-01-05")
        self.assertIsNone(self.owner(did))
        self.assertNotIn("owner", self.events()[-1][2])
        out = self.run_cli("deadline", "list", self.dir).stdout.strip()
        self.assertEqual(out, "2099-01-05  Plain (working-estimate/unverified)  [—]")

    def test_update_owner_logs_old_and_new(self):
        did = self.add()
        self.run_cli("deadline", "update", self.dir, did, "--owner", "ours", "--reason", "We file the reply")
        self.assertEqual(self.owner(did), "ours")
        self.assertIn("owner unset→ours", self.events()[-1][2])
        self.run_cli("deadline", "update", self.dir, did, "--owner", "theirs", "--reason", "Their reply, not ours")
        self.assertIn("owner ours→theirs; reason: Their reply, not ours", self.events()[-1][2])

    def test_same_owner_again_is_refused(self):
        did = self.add("Mine", "2099-01-05", "--owner", "ours")
        n = len(self.events())
        r = self.run_cli("deadline", "update", self.dir, did, "--owner", "ours", "--reason", "x", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no change", r.stderr)
        self.assertEqual(len(self.events()), n)

    def test_bad_owner_is_refused_and_nothing_is_written(self):
        from matterkit import deadlines as dl
        did = self.add()
        n = len(self.events())
        r = self.run_cli("deadline", "update", self.dir, did, "--owner", "mine", "--reason", "x", check=False)
        self.assertNotEqual(r.returncode, 0)
        conn = store.connect(self.dir)
        with self.assertRaises(dl.DeadlineError):
            dl.update(conn, self.dir, did, "x", "human", owner="mine")
        with self.assertRaises(dl.DeadlineError):
            dl.add(conn, "L", "2099-01-01", "human", owner="mine")
        conn.close()
        self.assertIsNone(self.owner(did))
        self.assertEqual(len(self.events()), n)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM deadlines")[0][0], 1)

    def test_list_shows_and_filters_by_owner(self):
        a = self.add("Their answer", "2099-01-05", "--owner", "theirs")
        self.add("Our reply", "2099-01-06", "--owner", "ours")
        self.add("Unsorted", "2099-01-07")
        out = self.run_cli("deadline", "list", self.dir).stdout.splitlines()
        self.assertIn("2099-01-05  Their answer (working-estimate/unverified)  [—]  owner=theirs", out)
        self.assertIn("2099-01-07  Unsorted (working-estimate/unverified)  [—]", out)
        theirs = self.run_cli("deadline", "list", self.dir, "--owner", "theirs").stdout.splitlines()
        self.assertEqual([l.split("  ")[1].split(" (")[0] for l in theirs], ["Their answer"])
        self.run_cli("deadline", "update", self.dir, a, "--state", "met", "--reason", "Answer filed")
        self.assertEqual(self.run_cli("deadline", "list", self.dir, "--owner", "theirs").stdout, "")
        everything = self.run_cli("deadline", "list", self.dir, "--owner", "theirs", "--all").stdout
        self.assertIn("Their answer", everything)
        self.assertIn("state=met", everything)

    def legacy_store(self):
        """Replace the deadlines table with the one stores had before the owner column."""
        conn = sqlite3.connect(os.path.join(self.dir, ".matter", "matter.db"))
        conn.executescript("""
            DROP TABLE deadlines;
            CREATE TABLE deadlines (
              id TEXT PRIMARY KEY, label TEXT NOT NULL, due_date TEXT NOT NULL,
              rule_source TEXT, confidence TEXT DEFAULT 'working-estimate',
              verification_status TEXT DEFAULT 'unverified', state TEXT DEFAULT 'open');
            INSERT INTO deadlines (id,label,due_date) VALUES ('d_old','Old one','2099-02-02');
        """)
        conn.commit()
        conn.close()

    def test_an_old_store_reads_before_and_after_the_migration(self):
        from matterkit import deadlines as dl
        self.legacy_store()
        ro = store.connect_readonly(self.dir)  # never migrates
        [row] = ro.execute("SELECT * FROM deadlines").fetchall()
        self.assertEqual(dl.list_line(row), "2099-02-02  Old one (working-estimate/unverified)  [—]")
        ro.close()
        self.run_cli("deadline", "update", self.dir, "d_old", "--owner", "court", "--reason", "Court sets it")
        self.assertEqual(self.owner("d_old"), "court")
        self.assertEqual(self.rows("SELECT label, state FROM deadlines WHERE id='d_old'"), [("Old one", "open")])


class TestDeadlineMcp(Base):
    def _call(self, name, arguments):
        reqs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": name, "arguments": arguments}}]
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")],
                           input="\n".join(json.dumps(r) for r in reqs) + "\n", capture_output=True,
                           text=True, env=dict(CLEAN_ENV, MATTER_DIR=self.dir), check=True)
        return json.loads(p.stdout.strip().splitlines()[-1])["result"]

    def test_tool_is_declared_with_required_reason(self):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")],
                           input=json.dumps(req) + "\n", capture_output=True, text=True,
                           env=dict(CLEAN_ENV, MATTER_DIR=self.dir), check=True)
        tools = {t["name"]: t for t in json.loads(p.stdout)["result"]["tools"]}
        schema = tools["matter.deadline_update"]["inputSchema"]
        self.assertIn("reason", schema["required"])
        self.assertIn("deadline_id", schema["required"])
        self.assertEqual(set(schema["properties"]["state"]["enum"]),
                         {"open", "met", "superseded", "lapsed", "vacated"})

    def test_update_records_event_with_agent_default(self):
        did = self.add()
        res = self._call("matter.deadline_update", {"deadline_id": did, "state": "lapsed",
                                                     "reason": "Rule ran", "sources": ["Filings/order.txt@p. 1"]})
        self.assertNotIn("isError", res, res)
        self.assertEqual(self.deadline(did)[0], "lapsed")
        _, _, text, actors, sources = self.events()[-1]
        self.assertIn("state open→lapsed", text)
        self.assertEqual(actors, "agent")  # same MCP default as matter.fact_add
        self.assertEqual(json.loads(sources), ["Filings/order.txt@p. 1"])

    def test_directed_by_recorded(self):
        did = self.add()
        self._call("matter.deadline_update", {"deadline_id": did, "state": "met", "reason": "Filed",
                                              "created_by": "agent:model-x", "directed_by": "Pat Example"})
        self.assertEqual(self.events()[-1][3], "agent:model-x; directed-by:Pat Example")

    def test_refusals_are_errors_and_change_nothing(self):
        did = self.add()
        n = len(self.events())
        for args in ({"deadline_id": did, "state": "met"},                        # no reason
                     {"deadline_id": did, "state": "met", "reason": " "},
                     {"deadline_id": did, "state": "bogus", "reason": "x"},
                     {"deadline_id": "d_nope", "state": "met", "reason": "x"},
                     {"deadline_id": did, "reason": "x"},                         # no field
                     {"deadline_id": did, "state": "met", "reason": "x", "sources": ["Filings/ghost.pdf"]},
                     {"deadline_id": did, "due": "11/02/2026", "reason": "x"}):
            res = self._call("matter.deadline_update", args)
            self.assertTrue(res.get("isError"), args)
        self.assertEqual(self.deadline(did)[0], "open")
        self.assertEqual(len(self.events()), n)

    def test_list_shows_overdue(self):
        self.add("Past one", "2020-01-02")
        self.add("Future one", "2099-01-02")
        res = self._call("matter.deadline_list", {})
        rows = {r["label"]: r for r in json.loads(res["content"][0]["text"])}
        self.assertTrue(rows["Past one"]["overdue"])
        self.assertIn("OVERDUE", rows["Past one"]["marker"])
        self.assertFalse(rows["Future one"]["overdue"])
        self.assertIsNone(rows["Future one"]["marker"])
        self.assertIn("state", rows["Past one"])  # existing keys preserved

    def test_owner_is_listed_filtered_and_updated(self):
        did = self.add("Their answer", "2099-01-05", "--owner", "theirs")
        self.add("Unsorted", "2099-01-07")
        rows = {r["label"]: r for r in json.loads(self._call("matter.deadline_list", {})["content"][0]["text"])}
        self.assertEqual(rows["Their answer"]["owner"], "theirs")
        self.assertIsNone(rows["Unsorted"]["owner"])
        only = json.loads(self._call("matter.deadline_list", {"owner": "theirs"})["content"][0]["text"])
        self.assertEqual([r["label"] for r in only], ["Their answer"])
        res = self._call("matter.deadline_update", {"deadline_id": did, "owner": "court", "reason": "Court sets it"})
        self.assertNotIn("isError", res, res)
        self.assertIn("owner theirs→court", self.events()[-1][2])
        self.assertTrue(self._call("matter.deadline_update", {"deadline_id": did, "owner": "mine",
                                                               "reason": "x"}).get("isError"))
        self.assertTrue(self._call("matter.deadline_list", {"owner": "mine"}).get("isError"))

    def test_owner_is_declared(self):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")],
                           input=json.dumps(req) + "\n", capture_output=True, text=True,
                           env=dict(CLEAN_ENV, MATTER_DIR=self.dir), check=True)
        tools = {t["name"]: t for t in json.loads(p.stdout)["result"]["tools"]}
        for name in ("matter.deadline_update", "matter.deadline_list"):
            self.assertEqual(set(tools[name]["inputSchema"]["properties"]["owner"]["enum"]),
                             {"ours", "theirs", "court"})


if __name__ == "__main__":
    unittest.main()

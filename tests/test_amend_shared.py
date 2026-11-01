"""The amendment list through every front door: matterkit.amendments, the CLI and the MCP server.

A per-case queue of changes for the next version of a pleading. The rules (what is refused, what is
recorded, in what order a list comes back) live in matterkit/amendments.py, and the CLI, the MCP
server and the web UI all call it. Refusals write nothing; nothing is ever deleted.
Synthetic tempdirs only. Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import amendments, facts, store  # noqa: E402

CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("MATTER_AUTHOR", "MATTER_DIRECTED_BY")}
TABLES = ("amendment_items", "amendment_item_links", "amendment_item_history")
ISO = r"^\d{4}-\d{2}-\d{2}T"


def run_cli(*argv, check=True, env=None):
    return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *argv],
                          capture_output=True, text=True, check=check, env=env or CLEAN_ENV)


def mask(text):
    return re.sub(r"am_[0-9a-f]{12}", "am_<ID>", text)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class AmendBase(unittest.TestCase):
    """A matter with two registered documents and two facts."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "Case")
        run_cli("init", self.dir)
        write(os.path.join(self.dir, "Evidence", "note.txt"), "synthetic source one\n")
        write(os.path.join(self.dir, "Filings", "order.txt"), "synthetic source two\n")
        write(os.path.join(self.dir, "Evidence", "a@b.txt"), "synthetic source with an at sign\n")
        run_cli("ingest", self.dir)
        self.conn = store.connect(self.dir)
        self.addCleanup(self.conn.close)
        self.f1 = facts.add(self.conn, self.dir, "Plaintiff was struck at the jail", "documented-fact",
                            ["Evidence/note.txt@p1"])["id"]
        self.f2 = facts.add(self.conn, self.dir, "Plaintiff went to the ER", "allegation")["id"]
        self.d1 = self.doc("Evidence/note.txt")
        self.d2 = self.doc("Filings/order.txt")

    def doc(self, rel):
        return self.conn.execute("SELECT id FROM documents WHERE rel_path=?", (rel,)).fetchone()["id"]

    def rows(self, sql, params=(), matter=None):
        conn = sqlite3.connect(os.path.join(matter or self.dir, ".matter", "matter.db"))
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def counts(self, matter=None):
        return {t: self.rows(f"SELECT COUNT(*) FROM {t}", matter=matter)[0][0] for t in TABLES}

    def assertNothingWritten(self, msg=None):
        self.assertEqual(self.counts(), dict.fromkeys(TABLES, 0), msg)

    def item(self, text="Change one", **kw):
        return amendments.add(self.conn, text, **kw)

    def mcp(self, name, arguments, matter=None):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input=json.dumps(req) + "\n",
                           capture_output=True, text=True, env=dict(CLEAN_ENV, MATTER_DIR=matter or self.dir),
                           check=True)
        return json.loads(p.stdout.strip().splitlines()[-1])["result"]


class TestSchema(AmendBase):
    def test_tables_are_created_by_connect_with_the_spec_columns(self):
        def cols(t):
            return [r[1] for r in self.conn.execute(f"PRAGMA table_info({t})")]
        self.assertEqual(cols("amendment_items"), ["id", "target", "text", "paragraphs", "reason", "status",
                                                   "created_by", "created_at", "updated_at"])
        self.assertEqual(cols("amendment_item_links"), ["item_id", "kind", "ref", "locator"])
        self.assertEqual(cols("amendment_item_history"), ["id", "item_id", "from_status", "to_status", "note",
                                                          "recorded_by", "created_at"])

    def test_the_database_itself_rejects_a_bad_status_or_link_kind(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO amendment_items (id,text,status,created_by,created_at)"
                              " VALUES ('x','t','done','h','2026-01-01')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO amendment_item_links (item_id,kind,ref) VALUES ('x','email','r')")

    def test_additive_only_existing_tables_keep_their_columns(self):
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(assertions)")]
        self.assertEqual(cols, ["id", "text", "status", "contested_by", "created_by", "created_at"])


class TestLabels(unittest.TestCase):
    def test_human_labels(self):
        self.assertEqual(amendments.LABELS, {"proposed": "Proposed", "approved": "Approved",
                                             "incorporated": "In the draft", "dropped": "Dropped"})
        self.assertEqual(amendments.STATUSES, ("proposed", "approved", "incorporated", "dropped"))

    def test_error_is_a_value_error(self):
        self.assertTrue(issubclass(amendments.AmendmentError, ValueError))


class TestAdd(AmendBase):
    def test_add_writes_item_links_and_history_and_returns_the_result(self):
        r = amendments.add(self.conn, "Attribute all three ER injuries to the jail beating",
                           paragraphs="¶¶ 37, 172", reason="The ER notes tie them together",
                           facts=[self.f1], documents=[(self.d1, "p. 3")], by="agent:model-x; directed-by:Pat Example")
        self.assertRegex(r["id"], r"^am_[0-9a-f]{12}$")
        self.assertEqual((r["status"], r["created_by"]), ("proposed", "agent:model-x; directed-by:Pat Example"))
        self.assertEqual([f["id"] for f in r["links"]["facts"]], [self.f1])
        self.assertEqual(r["links"]["facts"][0]["text"], "Plaintiff was struck at the jail")
        self.assertEqual(r["links"]["facts"][0]["label"], "DOCUMENTED FACT")
        self.assertEqual(r["links"]["documents"], [{"id": self.d1, "rel_path": "Evidence/note.txt", "locator": "p. 3"}])
        row = self.rows("SELECT target,text,paragraphs,reason,status,created_by,created_at,updated_at"
                        " FROM amendment_items WHERE id=?", (r["id"],))[0]
        self.assertEqual(row[:6], ("complaint", "Attribute all three ER injuries to the jail beating", "¶¶ 37, 172",
                                   "The ER notes tie them together", "proposed", "agent:model-x; directed-by:Pat Example"))
        self.assertRegex(row[6], ISO)
        self.assertIsNone(row[7])  # never changed since it was created
        self.assertEqual(self.rows("SELECT kind,ref,locator FROM amendment_item_links WHERE item_id=? ORDER BY kind",
                                   (r["id"],)), [("document", self.d1, "p. 3"), ("fact", self.f1, None)])
        hist = self.rows("SELECT from_status,to_status,note,recorded_by,created_at FROM amendment_item_history"
                         " WHERE item_id=?", (r["id"],))
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0][:4], (None, "proposed", None, "agent:model-x; directed-by:Pat Example"))
        self.assertRegex(hist[0][4], ISO)

    def test_defaults(self):
        r = self.item("Fix the date in the heading")
        row = self.rows("SELECT target,paragraphs,reason,created_by FROM amendment_items")[0]
        self.assertEqual(row, ("complaint", None, None, "human"))
        self.assertEqual(r["links"], {"facts": [], "documents": []})
        self.assertEqual(r["target"], "complaint")

    def test_other_target_and_trimmed_optional_fields(self):
        self.item("Add a count", target=" first amended complaint ", paragraphs="  ", reason="\n")
        self.assertEqual(self.rows("SELECT target,paragraphs,reason FROM amendment_items"),
                         [("first amended complaint", None, None)])

    def test_text_is_stored_as_given_like_a_fact(self):
        self.item("  padded text\n")
        self.assertEqual(self.rows("SELECT text FROM amendment_items"), [("  padded text\n",)])

    def test_duplicate_links_are_stored_once_even_without_a_locator(self):
        # SQLite lets NULLs repeat inside a composite primary key, so the code has to dedupe
        r = self.item("Dupes", facts=[self.f1, self.f1],
                      documents=[(self.d1, "p1"), (self.d1, "p1"), (self.d1, None), (self.d1, ""), (self.d1, "  ")])
        self.assertEqual(len(r["links"]["facts"]), 1)
        self.assertEqual(sorted((d["locator"] or "") for d in r["links"]["documents"]), ["", "p1"])
        self.assertEqual(self.counts()["amendment_item_links"], 3)

    def test_same_document_with_two_locators_is_two_links(self):
        r = self.item("Two pages", documents=[(self.d1, "p1"), (self.d1, "p2")])
        self.assertEqual([d["locator"] for d in r["links"]["documents"]], ["p1", "p2"])

    def test_refusals_write_nothing(self):
        good = dict(facts=[self.f1], documents=[(self.d1, "p1")])
        cases = [
            ("empty text", dict(text="", **good)), ("blank text", dict(text="  \n\t ", **good)),
            ("none text", dict(text=None, **good)), ("non-string text", dict(text=5, **good)),
            ("unknown fact", dict(text="t", facts=[self.f1, "a_nope"], documents=[(self.d1, "p1")])),
            ("unknown document", dict(text="t", facts=[self.f1], documents=[(self.d1, "p1"), ("nope", "p2")])),
            ("empty target", dict(text="t", target="", **good)), ("blank target", dict(text="t", target="  ", **good)),
            ("none target", dict(text="t", target=None, **good)),
            ("facts as a string", dict(text="t", facts=self.f1)),
            ("fact id not a string", dict(text="t", facts=[7])),
            ("document pair malformed", dict(text="t", documents=[self.d1])),
            ("document id not a string", dict(text="t", documents=[(7, "p1")])),
            ("locator with a line break", dict(text="t", documents=[(self.d1, "p1\nINJECT")])),
            ("locator too long", dict(text="t", documents=[(self.d1, "x" * 201)])),
            ("paragraphs with a line break", dict(text="t", paragraphs="¶ 1\n¶ 2")),
            ("paragraphs not a string", dict(text="t", paragraphs=37)),
            ("reason not a string", dict(text="t", reason=["x"])),
            ("target with a line break", dict(text="t", target="a\nb")),
        ]
        for name, kw in cases:
            with self.subTest(name):
                with self.assertRaises(amendments.AmendmentError):
                    amendments.add(self.conn, **kw)
                self.assertNothingWritten(name)

    def test_a_crash_between_the_item_and_its_history_rolls_everything_back(self):
        real = amendments.new_id

        def flaky(prefix=""):
            if prefix.startswith("ah"):
                raise RuntimeError("disk full")
            return real(prefix)
        with mock.patch.object(amendments, "new_id", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                self.item("Never lands", facts=[self.f1], documents=[(self.d1, "p1")])
        self.assertNothingWritten()
        self.assertEqual(self.item("Lands now")["status"], "proposed")  # and the connection is still usable


class TestList(AmendBase):
    def test_item_shape_links_and_history(self):
        r = amendments.add(self.conn, "Change", paragraphs="¶ 5", reason="Because", facts=[self.f2],
                           documents=[(self.d2, None)], by="human")
        amendments.set_status(self.conn, r["id"], "approved", note="Looks right", by="human:Pat")
        (got,) = amendments.list_(self.conn)
        self.assertEqual(set(got), {"id", "target", "text", "paragraphs", "reason", "status", "status_label",
                                    "created_by", "created_at", "updated_at", "links", "history"})
        self.assertEqual((got["id"], got["status"], got["status_label"]), (r["id"], "approved", "Approved"))
        self.assertEqual(got["links"]["facts"], [{"id": self.f2, "text": "Plaintiff went to the ER",
                                                  "label": "ALLEGATION"}])
        self.assertEqual(got["links"]["documents"], [{"id": self.d2, "rel_path": "Filings/order.txt", "locator": None}])
        self.assertRegex(got["updated_at"], ISO)
        self.assertEqual([(h["from_status"], h["to_status"], h["note"], h["recorded_by"]) for h in got["history"]],
                         [(None, "proposed", None, "human"), ("proposed", "approved", "Looks right", "human:Pat")])
        for h in got["history"]:
            self.assertEqual(set(h), {"from_status", "to_status", "note", "recorded_by", "created_at"})

    def test_order_approved_first_then_creation_then_row_order(self):
        ids = {n: self.item(n)["id"] for n in ("p-old", "p-tie-1", "p-tie-2", "a-new", "a-old")}
        stamp = {"p-old": "2026-01-01T00:00:00+00:00", "p-tie-1": "2026-01-02T00:00:00+00:00",
                 "p-tie-2": "2026-01-02T00:00:00+00:00", "a-new": "2026-01-05T00:00:00+00:00",
                 "a-old": "2026-01-03T00:00:00+00:00"}
        for n, ts in stamp.items():
            self.conn.execute("UPDATE amendment_items SET created_at=? WHERE id=?", (ts, ids[n]))
        self.conn.commit()
        amendments.set_status(self.conn, ids["a-new"], "approved")
        amendments.set_status(self.conn, ids["a-old"], "approved")
        self.assertEqual([i["text"] for i in amendments.list_(self.conn)],
                         ["a-old", "a-new", "p-old", "p-tie-1", "p-tie-2"])

    def test_filters(self):
        ids = {s: self.item(s)["id"] for s in amendments.STATUSES}
        for s in ("approved", "incorporated", "dropped"):
            amendments.set_status(self.conn, ids[s], s)

        def texts(*a, **kw):
            return [i["text"] for i in amendments.list_(self.conn, *a, **kw)]
        self.assertEqual(texts(), ["approved", "proposed"])  # the default is "open"
        self.assertEqual(texts("open"), ["approved", "proposed"])
        self.assertEqual(sorted(texts("all")), ["approved", "dropped", "incorporated", "proposed"])
        self.assertEqual(texts("all"), ["approved", "proposed", "incorporated", "dropped"])  # open work first
        for s in amendments.STATUSES:
            self.assertEqual(texts(s), [s])

    def test_target_filter_is_exact(self):
        self.item("c1")
        self.item("c2", target="complaint")
        self.item("r1", target="response to Dkt. 16")
        self.assertEqual([i["text"] for i in amendments.list_(self.conn, target="complaint")], ["c1", "c2"])
        self.assertEqual([i["text"] for i in amendments.list_(self.conn, target="response to Dkt. 16")], ["r1"])
        self.assertEqual(amendments.list_(self.conn, target="reply"), [])
        self.assertEqual(len(amendments.list_(self.conn, target=None)), 3)

    def test_unknown_filter_is_refused_not_treated_as_all(self):
        for bad in ("done", "", "open,approved", "in the draft", None, 3):
            with self.assertRaises(amendments.AmendmentError, msg=repr(bad)):
                amendments.list_(self.conn, bad)
        with self.assertRaises(amendments.AmendmentError):
            amendments.list_(self.conn, "all", target=5)

    def test_filter_spelling_is_forgiven_like_a_status_change(self):
        a = self.item("a")["id"]
        amendments.set_status(self.conn, a, "dropped")
        self.assertEqual(amendments.list_(self.conn, " OPEN\n"), [])
        self.assertEqual(len(amendments.list_(self.conn, "All")), 1)
        self.assertEqual(len(amendments.list_(self.conn, "DROPPED")), 1)

    def test_empty_store(self):
        self.assertEqual(amendments.list_(self.conn), [])

    def test_hostile_text_comes_back_as_data(self):
        evil = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
        self.item(evil, paragraphs=evil, reason=evil)
        (got,) = amendments.list_(self.conn)
        self.assertEqual((got["text"], got["paragraphs"], got["reason"]), (evil, evil, evil))


class TestOldStore(unittest.TestCase):
    """A store written before the amendment tables existed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "Old")
        os.makedirs(os.path.join(self.dir, ".matter"))
        self.assertIn("CREATE TABLE IF NOT EXISTS amendment_items", store.SCHEMA)
        legacy = sqlite3.connect(store.db_path(self.dir))
        legacy.executescript(store.SCHEMA.split("CREATE TABLE IF NOT EXISTS amendment_items")[0])
        legacy.commit()
        legacy.close()

    def tables(self):
        c = sqlite3.connect(store.db_path(self.dir))
        try:
            return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            c.close()

    def test_the_fixture_really_lacks_the_tables(self):
        self.assertNotIn("amendment_items", self.tables())

    def test_readonly_list_and_counts_do_not_crash_and_write_nothing(self):
        before = read_bytes(store.db_path(self.dir))
        conn = store.connect_readonly(self.dir)
        try:
            for flt in ("open", "all", "proposed"):
                self.assertEqual(amendments.list_(conn, flt), [])
            self.assertEqual(amendments.counts(conn), {"proposed": 0, "approved": 0, "incorporated": 0,
                                                       "dropped": 0, "open": 0})
        finally:
            conn.close()
        self.assertEqual(read_bytes(store.db_path(self.dir)), before)
        self.assertNotIn("amendment_items", self.tables())

    def test_a_readwrite_connect_creates_the_tables_and_keeps_old_rows(self):
        c = sqlite3.connect(store.db_path(self.dir))
        c.execute("INSERT INTO assertions (id,text,status,created_by,created_at) VALUES ('a_old','Old fact','UNKNOWN','h','t')")
        c.commit()
        c.close()
        conn = store.connect(self.dir)
        try:
            self.assertEqual({"amendment_items", "amendment_item_links", "amendment_item_history"} - self.tables(), set())
            self.assertEqual(conn.execute("SELECT text FROM assertions").fetchone()["text"], "Old fact")
            r = amendments.add(conn, "First change", facts=["a_old"])
            self.assertEqual(amendments.list_(conn)[0]["id"], r["id"])
        finally:
            conn.close()
        store.connect(self.dir).close()  # and a second connect is harmless


class TestSetStatus(AmendBase):
    def test_each_change_updates_the_item_and_appends_history(self):
        r = self.item("Change")
        amendments.set_status(self.conn, r["id"], "approved", note="Pat approved it", by="human:Pat")
        amendments.set_status(self.conn, r["id"], "incorporated", by="human:Pat")
        amendments.set_status(self.conn, r["id"], "dropped", note="Replaced by a later change", by="human:Lee")
        amendments.set_status(self.conn, r["id"], "proposed", note="Reopened", by="human:Lee")
        row = self.rows("SELECT status,updated_at,created_by FROM amendment_items")[0]
        self.assertEqual((row[0], row[2]), ("proposed", "human"))
        self.assertRegex(row[1], ISO)
        hist = self.rows("SELECT from_status,to_status,note,recorded_by FROM amendment_item_history"
                         " WHERE item_id=? ORDER BY created_at, rowid", (r["id"],))
        self.assertEqual(hist, [(None, "proposed", None, "human"), ("proposed", "approved", "Pat approved it", "human:Pat"),
                                ("approved", "incorporated", None, "human:Pat"),
                                ("incorporated", "dropped", "Replaced by a later change", "human:Lee"),
                                ("dropped", "proposed", "Reopened", "human:Lee")])

    def test_return_value(self):
        r = self.item("Change")
        out = amendments.set_status(self.conn, r["id"], "approved", note="  ok  ", by="human:Pat")
        self.assertEqual((out["id"], out["from_status"], out["status"], out["status_label"], out["note"],
                          out["recorded_by"]), (r["id"], "proposed", "approved", "Approved", "ok", "human:Pat"))

    def test_status_spelling_is_forgiven_but_labels_are_not_statuses(self):
        r = self.item("Change")
        amendments.set_status(self.conn, r["id"], " Approved\n")
        self.assertEqual(self.rows("SELECT status FROM amendment_items"), [("approved",)])
        with self.assertRaises(amendments.AmendmentError):
            amendments.set_status(self.conn, r["id"], "In the draft")

    def test_blank_note_is_stored_as_none(self):
        r = self.item("Change")
        amendments.set_status(self.conn, r["id"], "approved", note="   ")
        self.assertEqual(self.rows("SELECT note FROM amendment_item_history WHERE to_status='approved'"), [(None,)])

    def test_refusals_write_nothing(self):
        r = self.item("Change")
        amendments.set_status(self.conn, r["id"], "approved")
        before_hist = self.rows("SELECT * FROM amendment_item_history ORDER BY rowid")
        before_item = self.rows("SELECT * FROM amendment_items")
        cases = [("unknown id", ("am_000000000000", "dropped")), ("empty id", ("", "dropped")), ("none id", (None, "dropped")),
                 ("same status", (r["id"], "approved")), ("same status, other spelling", (r["id"], "APPROVED")),
                 ("invalid status", (r["id"], "done")), ("empty status", (r["id"], "")), ("none status", (r["id"], None)),
                 ("number status", (r["id"], 3)), ("list status", (r["id"], ["dropped"])),
                 ("note not a string", (r["id"], "dropped", 5))]
        for name, args in cases:
            with self.subTest(name):
                if len(args) == 3:
                    call = lambda: amendments.set_status(self.conn, args[0], args[1], note=args[2])  # noqa: E731
                else:
                    call = lambda: amendments.set_status(self.conn, *args)  # noqa: E731
                with self.assertRaises(amendments.AmendmentError):
                    call()
                self.assertEqual(self.rows("SELECT * FROM amendment_item_history ORDER BY rowid"), before_hist)
                self.assertEqual(self.rows("SELECT * FROM amendment_items"), before_item)

    def test_unknown_id_has_its_own_kind_so_a_front_end_can_say_not_found(self):
        with self.assertRaises(amendments.AmendmentError) as cm:
            amendments.set_status(self.conn, "am_000000000000", "dropped")
        self.assertEqual(cm.exception.kind, "not-found")

    def test_a_change_made_by_someone_else_in_between_is_refused_not_overwritten(self):
        r = self.item("Change")
        # we read "proposed", then another writer approves it before our write lands
        with mock.patch.object(amendments, "_status_of", return_value="proposed"):
            other = store.connect(self.dir)
            amendments.set_status(other, r["id"], "approved", by="human:Lee")
            other.close()
            with self.assertRaises(amendments.AmendmentError) as cm:
                amendments.set_status(self.conn, r["id"], "dropped", by="human:Pat")
        self.assertIn("changed", str(cm.exception))
        self.assertEqual(self.rows("SELECT status FROM amendment_items"), [("approved",)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM amendment_item_history")[0][0], 2)

    def test_nothing_is_ever_deleted(self):
        r = self.item("Change", facts=[self.f1])
        amendments.set_status(self.conn, r["id"], "dropped", note="No longer needed")
        self.assertEqual(self.counts(), {"amendment_items": 1, "amendment_item_links": 1, "amendment_item_history": 2})
        self.assertEqual(amendments.list_(self.conn), [])
        (got,) = amendments.list_(self.conn, "all")
        self.assertEqual((got["status"], len(got["links"]["facts"]), len(got["history"])), ("dropped", 1, 2))


class TestCounts(AmendBase):
    def test_counts(self):
        self.assertEqual(amendments.counts(self.conn), {"proposed": 0, "approved": 0, "incorporated": 0,
                                                        "dropped": 0, "open": 0})
        a, b, c, d = (self.item(n)["id"] for n in "abcd")
        amendments.set_status(self.conn, b, "approved")
        amendments.set_status(self.conn, c, "incorporated")
        amendments.set_status(self.conn, d, "dropped")
        self.assertEqual(amendments.counts(self.conn), {"proposed": 1, "approved": 1, "incorporated": 1,
                                                        "dropped": 1, "open": 2})


class TestResolveSource(AmendBase):
    def test_splits_on_the_last_at_sign(self):
        r = amendments.resolve_source
        self.assertEqual(r(self.conn, "Evidence/note.txt@p. 3"), (self.d1, "p. 3"))
        self.assertEqual(r(self.conn, "Evidence/note.txt"), (self.d1, None))
        self.assertEqual(r(self.conn, "Evidence/note.txt@"), (self.d1, None))
        self.assertEqual(r(self.conn, "Evidence/a@b.txt@p2"), (self.doc("Evidence/a@b.txt"), "p2"))
        self.assertEqual(r(self.conn, "Evidence/a@b.txt@"), (self.doc("Evidence/a@b.txt"), None))

    def test_unregistered_is_refused(self):
        for bad in ("nope.txt@p1", "nope.txt", "", "Evidence/note.txt@see x@y", "Evidence/a@b.txt", None, 5):
            with self.assertRaises(amendments.AmendmentError, msg=repr(bad)):
                amendments.resolve_source(self.conn, bad)


class TestCli(AmendBase):
    def add(self, *argv, check=True, env=None):
        return run_cli("amend", "add", self.dir, *argv, check=check, env=env)

    def test_add_prints_id_label_and_author_and_stores_everything(self):
        out = self.add("--text", "Attribute all three ER injuries to the jail beating", "--para", "¶¶ 37, 172",
                       "--reason", "ER notes", "--fact", self.f1, "--fact", self.f2,
                       "--source", "Evidence/note.txt@p. 3", "--source", "Filings/order.txt").stdout
        self.assertEqual(mask(out), "am_<ID> [Proposed] by human\n")
        row = self.rows("SELECT target,text,paragraphs,reason,status,created_by FROM amendment_items")[0]
        self.assertEqual(row, ("complaint", "Attribute all three ER injuries to the jail beating", "¶¶ 37, 172",
                               "ER notes", "proposed", "human"))
        self.assertEqual(self.rows("SELECT kind,ref,locator FROM amendment_item_links ORDER BY kind,ref,locator"),
                         sorted([("fact", self.f1, None), ("fact", self.f2, None), ("document", self.d1, "p. 3"),
                                 ("document", self.d2, None)]))
        self.assertEqual(self.counts()["amendment_item_history"], 1)

    def test_add_with_target(self):
        self.add("--text", "x", "--target", "response to Dkt. 16")
        self.assertEqual(self.rows("SELECT target FROM amendment_items"), [("response to Dkt. 16",)])

    def test_add_source_with_an_at_sign_in_the_file_name(self):
        self.add("--text", "x", "--source", "Evidence/a@b.txt@p2", "--source", "Evidence/a@b.txt@")
        self.assertEqual(self.rows("SELECT locator FROM amendment_item_links ORDER BY locator IS NULL, locator"),
                         [("p2",), (None,)])

    def test_add_refusals_exit_nonzero_say_why_and_write_nothing(self):
        cases = [
            (("--text", "x", "--source", "nope.txt@p1"), "refused:"),
            (("--text", "x", "--source", "Evidence/note.txt@see x@y"), "refused:"),  # split on the LAST @
            (("--text", "x", "--fact", "a_nope"), "refused:"),
            (("--text", "x", "--fact", self.f1, "--source", "nope.txt"), "refused:"),
            (("--text", ""), "refused: change text is empty"), (("--text", "   "), "refused: change text is empty"),
            ((), "refused: change text is empty"),
            (("--text", "x", "--target", ""), "refused:"),
            (("--text", "x", "--directed-by", "Pat Example"), "invalid attribution"),
            (("--text", "x", "--author", "a;b"), "invalid attribution"),
        ]
        for argv, want in cases:
            with self.subTest(argv=argv):
                r = self.add(*argv, check=False)
                self.assertNotEqual(r.returncode, 0)
                self.assertTrue(r.stderr.startswith(want), r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertNotIn("Traceback", r.stderr)
        self.assertNothingWritten()

    def test_attribution_like_fact_add(self):
        out = self.add("--text", "x", "--author", "agent:model-x", "--directed-by", "Pat Example").stdout
        self.assertEqual(mask(out), "am_<ID> [Proposed] by agent:model-x; directed-by:Pat Example\n")
        self.assertEqual(self.rows("SELECT created_by FROM amendment_items"), [("agent:model-x; directed-by:Pat Example",)])
        self.assertEqual(self.rows("SELECT recorded_by FROM amendment_item_history"), [("agent:model-x; directed-by:Pat Example",)])

    def test_attribution_from_the_environment(self):
        env = dict(CLEAN_ENV, MATTER_AUTHOR="agent:env-model", MATTER_DIRECTED_BY="Lee Example")
        self.add("--text", "x", env=env)
        self.assertEqual(self.rows("SELECT created_by FROM amendment_items"), [("agent:env-model; directed-by:Lee Example",)])

    def test_list_defaults_to_open_and_shows_what_a_person_needs(self):
        self.add("--text", "Attribute all three ER injuries to the jail beating", "--para", "¶¶ 37, 172",
                 "--reason", "ER notes", "--fact", self.f1, "--source", "Evidence/note.txt@p. 3")
        self.add("--text", "Second change", "--author", "agent:model-x", "--directed-by", "Pat Example")
        out = run_cli("amend", "list", self.dir).stdout
        self.assertIn("[Proposed]", out)
        for want in ("¶¶ 37, 172", "Attribute all three ER injuries to the jail beating", "ER notes",
                     "Plaintiff was struck at the jail", self.f1, "Evidence/note.txt@p. 3", "Second change",
                     "agent:model-x; directed-by:Pat Example", "human"):
            self.assertIn(want, out)
        self.assertRegex(out, r"am_[0-9a-f]{12}")
        self.assertRegex(out, ISO.replace("^", "") + r"\d{2}:\d{2}")  # when it was added

    def test_list_status_and_target_filters(self):
        ids = []
        for t in ("one", "two", "three"):
            out = self.add("--text", t, *(("--target", "reply") if t == "three" else ())).stdout
            ids.append(re.search(r"am_[0-9a-f]{12}", out).group(0))
        run_cli("amend", "set", self.dir, ids[0], "--status", "dropped")
        run_cli("amend", "set", self.dir, ids[1], "--status", "approved")

        def listing(*argv):
            return run_cli("amend", "list", self.dir, *argv).stdout
        self.assertNotIn("one", listing())
        self.assertIn("two", listing())
        self.assertIn("one", listing("--status", "all"))
        self.assertIn("one", listing("--status", "dropped"))
        self.assertNotIn("two", listing("--status", "dropped"))
        self.assertEqual(listing("--status", "approved").count("am_"), 1)
        self.assertIn("three", listing("--target", "reply"))
        self.assertNotIn("two", listing("--target", "reply"))
        bad = run_cli("amend", "list", self.dir, "--status", "bogus", check=False)
        self.assertEqual(bad.returncode, 2)  # argparse names the valid choices

    def test_list_when_there_is_nothing(self):
        r = run_cli("amend", "list", self.dir)
        self.assertEqual(r.stdout, "No changes queued.\n")

    def test_list_on_a_store_that_predates_the_tables_writes_nothing(self):
        old = os.path.join(self._tmp.name, "Old")
        os.makedirs(os.path.join(old, ".matter"))
        legacy = sqlite3.connect(store.db_path(old))
        legacy.executescript(store.SCHEMA.split("CREATE TABLE IF NOT EXISTS amendment_items")[0])
        legacy.commit()
        legacy.close()
        before = read_bytes(store.db_path(old))
        r = run_cli("amend", "list", old)
        self.assertEqual(r.stdout, "No changes queued.\n")
        self.assertEqual(read_bytes(store.db_path(old)), before)

    def test_list_on_a_folder_that_is_not_a_case_creates_nothing(self):
        nowhere = os.path.join(self._tmp.name, "NotACase")
        os.makedirs(nowhere)
        r = run_cli("amend", "list", nowhere, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no matter store", r.stderr)
        self.assertEqual(os.listdir(nowhere), [])

    def test_set_changes_status_records_history_and_prints_the_change(self):
        out = self.add("--text", "x").stdout
        aid = re.search(r"am_[0-9a-f]{12}", out).group(0)
        r = run_cli("amend", "set", self.dir, aid, "--status", "approved", "--note", "Pat checked ¶ 37",
                    "--author", "agent:model-x", "--directed-by", "Pat Example")
        self.assertEqual(r.stdout, f"{aid}: Proposed -> Approved by agent:model-x; directed-by:Pat Example\n")
        self.assertEqual(self.rows("SELECT status FROM amendment_items"), [("approved",)])
        self.assertEqual(self.rows("SELECT from_status,to_status,note,recorded_by FROM amendment_item_history"
                                   " ORDER BY created_at, rowid")[1],
                         ("proposed", "approved", "Pat checked ¶ 37", "agent:model-x; directed-by:Pat Example"))
        r = run_cli("amend", "set", self.dir, aid, "--status", "incorporated")
        self.assertEqual(r.stdout, f"{aid}: Approved -> In the draft by human\n")

    def test_set_refusals(self):
        aid = re.search(r"am_[0-9a-f]{12}", self.add("--text", "x").stdout).group(0)
        before = self.rows("SELECT * FROM amendment_item_history")
        for argv, want in [((aid, "--status", "proposed"), "refused:"),  # same status
                           (("am_000000000000", "--status", "dropped"), "refused:"),
                           ((aid, "--status", "dropped", "--directed-by", "Pat Example"), "invalid attribution")]:
            with self.subTest(argv=argv):
                r = run_cli("amend", "set", self.dir, *argv, check=False)
                self.assertNotEqual(r.returncode, 0)
                self.assertTrue(r.stderr.startswith(want), r.stderr)
                self.assertNotIn("Traceback", r.stderr)
        for argv in ((aid,), (aid, "--status", "done"), ("--status", "dropped")):  # argparse-level refusals
            with self.subTest(argv=argv):
                self.assertEqual(run_cli("amend", "set", self.dir, *argv, check=False).returncode, 2)
        self.assertEqual(self.rows("SELECT * FROM amendment_item_history"), before)
        self.assertEqual(self.rows("SELECT status FROM amendment_items"), [("proposed",)])


class TestMcp(AmendBase):
    def text(self, res):
        self.assertNotIn("isError", res)
        return res["content"][0]["text"]

    def test_tools_are_listed_and_there_is_no_way_to_change_a_status(self):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input=json.dumps(req) + "\n",
                           capture_output=True, text=True, env=dict(CLEAN_ENV, MATTER_DIR=self.dir), check=True)
        tools = {t["name"]: t for t in json.loads(p.stdout)["result"]["tools"]}
        self.assertEqual({n for n in tools if n.startswith("matter.amend")}, {"matter.amend_add", "matter.amend_list"})
        self.assertEqual(tools["matter.amend_add"]["inputSchema"]["required"], ["text"])
        for key in ("text", "paragraphs", "reason", "target", "facts", "sources", "created_by", "directed_by"):
            self.assertIn(key, tools["matter.amend_add"]["inputSchema"]["properties"])
        for key in ("status", "target"):
            self.assertIn(key, tools["matter.amend_list"]["inputSchema"]["properties"])

    def test_add(self):
        res = self.mcp("matter.amend_add", {"text": "Attribute the ER injuries", "paragraphs": "¶¶ 37, 172",
                                            "reason": "ER notes", "facts": [self.f1],
                                            "sources": ["Evidence/note.txt@p. 3", "Filings/order.txt"]})
        self.assertEqual(mask(self.text(res)), "am_<ID> [Proposed] by agent")
        self.assertEqual(self.rows("SELECT target,paragraphs,reason,created_by FROM amendment_items"),
                         [("complaint", "¶¶ 37, 172", "ER notes", "agent")])
        self.assertEqual(self.counts()["amendment_item_links"], 3)

    def test_add_attribution_composed(self):
        res = self.mcp("matter.amend_add", {"text": "t", "created_by": "agent:model-x", "directed_by": "Pat Example"})
        self.assertIn("by agent:model-x; directed-by:Pat Example", self.text(res))

    def test_add_refusals_are_errors_and_write_nothing(self):
        for args in ({"text": ""}, {"text": "  \n"}, {}, {"text": None}, {"text": "t", "facts": ["a_nope"]},
                     {"text": "t", "sources": ["nope.txt@p1"]}, {"text": "t", "target": ""},
                     {"text": "t", "directed_by": "Pat Example"}, {"text": "t", "facts": self.f1}):
            res = self.mcp("matter.amend_add", args)
            self.assertTrue(res.get("isError"), args)
            self.assertTrue(res["content"][0]["text"].startswith("error:"), args)
        self.assertNothingWritten()

    def test_list_is_json_and_filters(self):
        self.mcp("matter.amend_add", {"text": "one", "paragraphs": "¶ 1", "facts": [self.f1],
                                      "sources": ["Evidence/note.txt@p1"]})
        self.mcp("matter.amend_add", {"text": "two", "target": "reply"})
        amendments.set_status(self.conn, amendments.list_(self.conn, target="reply")[0]["id"], "dropped")
        raw = self.text(self.mcp("matter.amend_list", {}))
        self.assertEqual(raw, json.dumps(json.loads(raw), indent=1))
        got = json.loads(raw)
        self.assertEqual([g["text"] for g in got], ["one"])  # the default is open work
        g = got[0]
        self.assertEqual((g["status"], g["status_label"], g["paragraphs"], g["created_by"]),
                         ("proposed", "Proposed", "¶ 1", "agent"))
        self.assertEqual(g["links"]["facts"][0]["id"], self.f1)
        self.assertEqual(g["links"]["documents"][0]["rel_path"], "Evidence/note.txt")
        self.assertEqual(len(g["history"]), 1)
        everything = json.loads(self.text(self.mcp("matter.amend_list", {"status": "all"})))
        self.assertEqual(sorted(x["text"] for x in everything), ["one", "two"])
        self.assertEqual([x["text"] for x in json.loads(self.text(self.mcp("matter.amend_list", {"target": "reply",
                                                                                                  "status": "dropped"})))], ["two"])

    def test_list_refuses_an_unknown_filter(self):
        res = self.mcp("matter.amend_list", {"status": "done"})
        self.assertTrue(res.get("isError"))


if __name__ == "__main__":
    unittest.main()

"""Fact add/list through every front door: the CLI, the MCP server and matterkit.facts.

Characterization first (what `fact add` / `fact list` / `matter.fact_add` / `matter.fact_list`
printed before the logic moved into matterkit/facts.py), then the shared module's own rules.
Assertion and document ids are random, so they are masked before comparing.
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

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import facts, store, subcase  # noqa: E402

CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("MATTER_AUTHOR", "MATTER_DIRECTED_BY")}


def run_cli(*argv, check=True):
    return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *argv],
                          capture_output=True, text=True, check=check, env=CLEAN_ENV)


def mask(text):
    """Ids are random: a_<12 hex> assertion ids and (<12 hex>) document ids in notes."""
    text = re.sub(r"a_[0-9a-f]{12}", "a_<ID>", text)
    return re.sub(r"\([0-9a-f]{12}\)", "(<DOCID>)", text)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class FactBase(unittest.TestCase):
    """A matter with two registered documents; `self.sub` is a subcase of it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "Case")
        run_cli("init", self.dir)
        write(os.path.join(self.dir, "Evidence", "note.txt"), "synthetic source one\n")
        write(os.path.join(self.dir, "Filings", "order.txt"), "synthetic source two\n")
        run_cli("ingest", self.dir)
        self.sub = os.path.join(self.dir, "Subcases", "kid")

    def make_subcase(self):
        run_cli("subcase", "init", self.dir, "kid", "--title", "Kid case")

    def rows(self, matter, sql, params=()):
        conn = sqlite3.connect(os.path.join(matter, ".matter", "matter.db"))
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def count(self, matter, table="assertions"):
        return self.rows(matter, f"SELECT COUNT(*) FROM {table}")[0][0]

    def mcp(self, name, arguments, matter=None):
        """Drive matter-mcp.py over stdio for one tools/call; returns the JSON-RPC `result`."""
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": arguments}}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")],
                           input=json.dumps(req) + "\n", capture_output=True, text=True,
                           env=dict(CLEAN_ENV, MATTER_DIR=matter or self.dir), check=True)
        return json.loads(p.stdout.strip().splitlines()[-1])["result"]


class TestCliFactCharacterization(FactBase):
    def add(self, *argv, matter=None):
        return run_cli("fact", "add", matter or self.dir, *argv)

    def test_add_sourced_fact(self):
        out = self.add("--text", "Sourced", "--status", "documented-fact", "--source", "Evidence/note.txt@p1").stdout
        self.assertEqual(mask(out), "a_<ID> [DOCUMENTED FACT] by human\n")

    def test_add_unsupported_fact_warns(self):
        out = self.add("--text", "No source", "--status", "allegation").stdout
        self.assertEqual(mask(out), "a_<ID> [ALLEGATION] by human\n⚠ UNSUPPORTED — no recognized sources attached.\n")

    def test_add_with_unrecognized_ref_reports_it_and_attaches_the_rest(self):
        out = self.add("--text", "Half", "--status", "inference", "--source", "Evidence/note.txt@p2",
                       "--source", "nope.txt@x").stdout
        self.assertEqual(mask(out), "a_<ID> [INFERENCE] by human\n⚠ unrecognized source refs: ['nope.txt']\n")
        self.assertEqual(self.count(self.dir, "assertion_sources"), 1)

    def test_add_only_unrecognized_ref_is_unsupported_and_unrecognized(self):
        out = self.add("--text", "Bad", "--status", "unknown", "--source", "nope.txt").stdout
        self.assertEqual(mask(out), "a_<ID> [UNKNOWN] by human\n"
                         "⚠ UNSUPPORTED — no recognized sources attached.\n"
                         "⚠ unrecognized source refs: ['nope.txt']\n")

    def test_add_in_subcase_with_parent_ref_prints_note(self):
        self.make_subcase()
        out = self.add("--text", "child fact", "--status", "unknown", "--source", "parent:Filings/order.txt@p3",
                       matter=self.sub).stdout
        self.assertEqual(mask(out), "a_<ID> [UNKNOWN] by human\n"
                         "  ↳ referenced parent document Filings/order.txt (<DOCID>); bytes stay in the parent\n")

    def test_add_agent_attribution_is_printed(self):
        out = run_cli("fact", "add", self.dir, "--text", "t", "--status", "hypothesis",
                      "--author", "agent:model-x", "--directed-by", "Pat Example").stdout
        self.assertEqual(mask(out), "a_<ID> [HYPOTHESIS] by agent:model-x; directed-by:Pat Example\n"
                         "⚠ UNSUPPORTED — no recognized sources attached.\n")

    def test_add_refusal_writes_nothing(self):
        for argv in (("--text", "t", "--status", "unknown", "--directed-by", "Pat Example"),  # invalid attribution
                     ("--text", "t", "--status", "unknown", "--source", "parent:x.txt")):  # not a subcase
            r = run_cli("fact", "add", self.dir, *argv, check=False)
            self.assertNotEqual(r.returncode, 0, argv)
        self.assertEqual(self.count(self.dir), 0)

    def test_invalid_label_exits_nonzero_and_writes_nothing(self):
        r = run_cli("fact", "add", self.dir, "--text", "t", "--status", "bogus", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.count(self.dir), 0)

    def _seed_list(self):
        run_cli("fact", "add", self.dir, "--text", "Sourced", "--status", "documented-fact",
                "--source", "Evidence/note.txt@p1")
        run_cli("fact", "add", self.dir, "--text", "Unsourced", "--status", "allegation")
        run_cli("fact", "add", self.dir, "--text", "Locator-less", "--status", "inference",
                "--source", "Filings/order.txt")

    def test_list_all_ordered_by_label(self):
        self._seed_list()
        self.assertEqual(run_cli("fact", "list", self.dir).stdout, (
            "[ALLEGATION     ] Unsourced\n                ↳ **[UNSUPPORTED]**\n                by human\n"
            "[DOCUMENTED FACT] Sourced\n                ↳ Evidence/note.txt@p1\n                by human\n"
            "[INFERENCE      ] Locator-less\n                ↳ Filings/order.txt\n                by human\n"))

    def test_list_filtered_by_label(self):
        self._seed_list()
        want = "[DOCUMENTED FACT] Sourced\n                ↳ Evidence/note.txt@p1\n                by human\n"
        self.assertEqual(run_cli("fact", "list", self.dir, "--status", "documented-fact").stdout, want)
        self.assertEqual(run_cli("fact", "list", self.dir, "--status", "DOCUMENTED FACT").stdout, want)
        self.assertEqual(run_cli("fact", "list", self.dir, "--status", "hypothesis").stdout, "")

    def test_list_shows_unrecorded_author(self):
        run_cli("fact", "add", self.dir, "--text", "Old row", "--status", "unknown")
        conn = sqlite3.connect(os.path.join(self.dir, ".matter", "matter.db"))
        conn.execute("UPDATE assertions SET created_by=NULL")
        conn.commit()
        conn.close()
        self.assertIn("by unrecorded", run_cli("fact", "list", self.dir).stdout)

    def test_list_two_sources_are_joined(self):
        run_cli("fact", "add", self.dir, "--text", "Two", "--status", "documented-fact",
                "--source", "Evidence/note.txt@p1", "--source", "Filings/order.txt@p2")
        line = run_cli("fact", "list", self.dir).stdout.splitlines()[1]  # source order is not specified
        self.assertEqual(sorted(line.strip()[2:].split("; ")), ["Evidence/note.txt@p1", "Filings/order.txt@p2"])


class TestMcpFactCharacterization(FactBase):
    def text(self, res):
        self.assertNotIn("isError", res)
        return res["content"][0]["text"]

    def test_add_sourced_fact(self):
        res = self.mcp("matter.fact_add", {"text": "Sourced", "status": "DOCUMENTED FACT",
                                           "sources": ["Evidence/note.txt@p1"]})
        self.assertEqual(mask(self.text(res)), "a_<ID> [DOCUMENTED FACT] by agent")

    def test_add_unsupported_fact_warns(self):
        res = self.mcp("matter.fact_add", {"text": "No source", "status": "ALLEGATION"})
        self.assertEqual(mask(self.text(res)), "a_<ID> [ALLEGATION] by agent\n⚠ UNSUPPORTED — no recognized sources.")

    def test_add_with_unrecognized_ref(self):
        res = self.mcp("matter.fact_add", {"text": "Half", "status": "INFERENCE",
                                           "sources": ["Evidence/note.txt@p2", "nope.txt@x"]})
        self.assertEqual(mask(self.text(res)),
                         "a_<ID> [INFERENCE] by agent\n⚠ Unrecognized source refs ignored: ['nope.txt']")

    def test_missing_status_defaults_to_unknown(self):
        res = self.mcp("matter.fact_add", {"text": "No label given"})
        self.assertEqual(mask(self.text(res)), "a_<ID> [UNKNOWN] by agent\n⚠ UNSUPPORTED — no recognized sources.")
        self.assertEqual(self.rows(self.dir, "SELECT status, created_by FROM assertions"), [("UNKNOWN", "agent")])

    def test_add_in_subcase_with_parent_ref_prints_note(self):
        self.make_subcase()
        res = self.mcp("matter.fact_add", {"text": "child", "status": "UNKNOWN",
                                           "sources": ["parent:Filings/order.txt@p3"]}, matter=self.sub)
        self.assertEqual(mask(self.text(res)), "a_<ID> [UNKNOWN] by agent\n"
                         "  ↳ referenced parent document Filings/order.txt (<DOCID>); bytes stay in the parent")

    def test_add_attribution_composed(self):
        res = self.mcp("matter.fact_add", {"text": "t", "status": "UNKNOWN", "created_by": "agent:model-x",
                                           "directed_by": "Pat Example"})
        self.assertIn("by agent:model-x; directed-by:Pat Example", self.text(res))

    def test_list_text_is_json_with_provenance(self):
        self.mcp("matter.fact_add", {"text": "Sourced", "status": "DOCUMENTED FACT",
                                     "sources": ["Evidence/note.txt@p1"]})
        self.mcp("matter.fact_add", {"text": "Unsourced", "status": "ALLEGATION"})
        self.mcp("matter.fact_add", {"text": "No locator", "status": "INFERENCE", "sources": ["Filings/order.txt"]})
        raw = self.text(self.mcp("matter.fact_list", {}))
        self.assertEqual(raw, json.dumps(json.loads(raw), indent=1))  # exactly json.dumps(..., indent=1)
        got = json.loads(raw)
        for g in got:
            g["id"] = "<ID>"
        self.assertEqual(got, [
            {"id": "<ID>", "status": "ALLEGATION", "text": "Unsourced", "supported": False, "sources": []},
            {"id": "<ID>", "status": "DOCUMENTED FACT", "text": "Sourced", "supported": True,
             "sources": ["Evidence/note.txt@p1"]},
            {"id": "<ID>", "status": "INFERENCE", "text": "No locator", "supported": True,
             "sources": ["Filings/order.txt"]}])
        self.assertEqual(list(json.loads(raw)[0]), ["id", "status", "text", "supported", "sources"])

    def test_list_filter_is_the_raw_label(self):
        self.mcp("matter.fact_add", {"text": "Sourced", "status": "DOCUMENTED FACT"})
        self.mcp("matter.fact_add", {"text": "Other", "status": "ALLEGATION"})
        got = json.loads(self.text(self.mcp("matter.fact_list", {"status": "ALLEGATION"})))
        self.assertEqual([g["text"] for g in got], ["Other"])
        self.assertEqual(json.loads(self.text(self.mcp("matter.fact_list", {"status": "HYPOTHESIS"}))), [])


VALID_HELP = "allegation, documented-fact, hypothesis, inference, unknown"


class TestNormalizeStatus(unittest.TestCase):
    def test_accepts_label_spellings(self):
        for raw, want in (("documented-fact", "DOCUMENTED FACT"), ("DOCUMENTED FACT", "DOCUMENTED FACT"),
                          ("Documented Fact", "DOCUMENTED FACT"), ("DOCUMENTED-FACT", "DOCUMENTED FACT"),
                          ("allegation", "ALLEGATION"), (" unknown\n", "UNKNOWN"), ("hypothesis", "HYPOTHESIS"),
                          ("inference", "INFERENCE")):
            self.assertEqual(facts.normalize_status(raw), want, raw)

    def test_every_stored_label_is_accepted(self):
        for label in store.VALID_STATUS:
            self.assertEqual(facts.normalize_status(label), label)

    def test_refuses_anything_else_and_names_the_valid_labels(self):
        for raw in ("bogus", "", "   ", "documented_fact", "documented  fact", "fact", None, 5, ["unknown"]):
            with self.assertRaises(facts.FactError) as cm:
                facts.normalize_status(raw)
            self.assertEqual(str(cm.exception), f"invalid status '{raw}' — use one of: {VALID_HELP}")

    def test_fact_error_is_a_value_error(self):
        self.assertTrue(issubclass(facts.FactError, ValueError))


class TestFactsModule(FactBase):
    def setUp(self):
        super().setUp()
        self.conn = store.connect(self.dir)
        self.addCleanup(self.conn.close)

    def test_add_returns_the_result_dict(self):
        r = facts.add(self.conn, self.dir, "Sourced", "documented-fact", ["Evidence/note.txt@p1"], by="agent:model-x")
        self.assertEqual(set(r), {"id", "status", "created_by", "sources_attached", "unsupported",
                                  "unrecognized", "notes", "date", "quotes"})
        self.assertRegex(r["id"], r"^a_[0-9a-f]{12}$")
        self.assertEqual((r["status"], r["created_by"], r["sources_attached"], r["unsupported"],
                          r["unrecognized"], r["notes"]), ("DOCUMENTED FACT", "agent:model-x", 1, False, [], []))
        row = self.rows(self.dir, "SELECT text, status, created_by, created_at FROM assertions WHERE id=?",
                        (r["id"],))[0]
        self.assertEqual(row[:3], ("Sourced", "DOCUMENTED FACT", "agent:model-x"))
        self.assertRegex(row[3], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(self.rows(self.dir, "SELECT locator FROM assertion_sources WHERE assertion_id=?",
                                   (r["id"],)), [("p1",)])

    def test_add_defaults_are_human_and_no_sources(self):
        r = facts.add(self.conn, self.dir, "Bare", "unknown")
        self.assertEqual((r["created_by"], r["unsupported"], r["sources_attached"]), ("human", True, 0))

    def test_add_reports_unsupported_and_unrecognized(self):
        r = facts.add(self.conn, self.dir, "t", "inference", ("nope.txt@x", "Evidence/note.txt@p2"))
        self.assertEqual((r["unsupported"], r["sources_attached"], r["unrecognized"]), (False, 1, ["nope.txt"]))
        r = facts.add(self.conn, self.dir, "t", "inference", ["nope.txt"])
        self.assertEqual((r["unsupported"], r["sources_attached"], r["unrecognized"]), (True, 0, ["nope.txt"]))

    def test_add_stores_text_as_given(self):
        facts.add(self.conn, self.dir, "  padded text\n", "unknown")
        self.assertEqual(self.rows(self.dir, "SELECT text FROM assertions"), [("  padded text\n",)])

    def test_add_in_subcase_notes_the_parent_reference(self):
        self.make_subcase()
        conn = store.connect(self.sub)
        self.addCleanup(conn.close)
        r = facts.add(conn, self.sub, "child", "unknown", ["parent:Filings/order.txt@p3"])
        self.assertEqual((r["sources_attached"], r["unsupported"]), (1, False))
        self.assertEqual(len(r["notes"]), 1)
        self.assertIn("referenced parent document Filings/order.txt", r["notes"][0])

    def test_invalid_label_refused_and_nothing_written(self):
        for bad in ("bogus", "", None, "documented_fact"):
            with self.assertRaises(facts.FactError):
                facts.add(self.conn, self.dir, "t", bad, ["Evidence/note.txt@p1"])
        self.assertEqual(self.count(self.dir), 0)
        self.assertEqual(self.count(self.dir, "assertion_sources"), 0)

    def test_empty_text_refused_and_nothing_written(self):
        for bad in ("", " ", "  \n\t ", None, 5):
            with self.assertRaises(facts.FactError, msg=repr(bad)):
                facts.add(self.conn, self.dir, bad, "unknown", ["Evidence/note.txt@p1"])
        self.assertEqual(self.count(self.dir), 0)
        self.assertEqual(self.count(self.dir, "assertion_sources"), 0)

    def test_uncitable_parent_ref_propagates_unchanged_and_nothing_written(self):
        with self.assertRaises(subcase.SubcaseError):  # not a subcase
            facts.add(self.conn, self.dir, "t", "unknown", ["parent:Filings/order.txt"])
        self.make_subcase()
        write(os.path.join(self.dir, "Filings", "order.txt"), "changed after it was registered\n")
        conn = store.connect(self.sub)
        self.addCleanup(conn.close)
        for ref, kind in (("parent:Filings/order.txt", "CHANGED"), ("parent:../escape.txt", "ESCAPED"),
                          ("parent:Filings/unknown.txt", "MISSING")):
            with self.assertRaises(subcase.SubcaseError) as cm:
                facts.add(conn, self.sub, "t", "unknown", [ref])
            self.assertEqual(cm.exception.kind, kind, ref)
        self.assertEqual(self.count(self.dir), 0)
        self.assertEqual(self.count(self.sub), 0)
        self.assertEqual(self.count(self.sub, "documents"), 0)

    def test_list_shape_and_order(self):
        facts.add(self.conn, self.dir, "Sourced", "documented-fact", ["Evidence/note.txt@p1"], by="agent:model-x")
        facts.add(self.conn, self.dir, "Unsourced", "allegation")
        facts.add(self.conn, self.dir, "No locator", "inference", ["Filings/order.txt"])
        got = facts.list_(self.conn)
        self.assertEqual([g["status"] for g in got], ["ALLEGATION", "DOCUMENTED FACT", "INFERENCE"])
        for g in got:
            self.assertEqual(set(g), {"id", "text", "status", "created_by", "created_at", "sources", "unsupported",
                                  "date"})
            self.assertRegex(g["created_at"], r"^\d{4}-\d{2}-\d{2}T")
        by_text = {g["text"]: g for g in got}
        self.assertEqual((by_text["Unsourced"]["sources"], by_text["Unsourced"]["unsupported"]), ([], True))
        self.assertEqual(by_text["Sourced"]["sources"], [{"rel_path": "Evidence/note.txt", "locator": "p1"}])
        self.assertFalse(by_text["Sourced"]["unsupported"])
        self.assertEqual(by_text["Sourced"]["created_by"], "agent:model-x")
        self.assertEqual(by_text["No locator"]["sources"], [{"rel_path": "Filings/order.txt", "locator": None}])

    def test_list_orders_by_creation_inside_a_label(self):
        for t in ("first", "second", "third"):
            facts.add(self.conn, self.dir, t, "hypothesis")
        self.conn.execute("UPDATE assertions SET created_at='2026-01-01T00:00:00+00:00' WHERE text='second'")
        self.conn.execute("UPDATE assertions SET created_at='2026-01-01T00:00:01+00:00' WHERE text!='second'")
        self.conn.commit()
        self.assertEqual([g["text"] for g in facts.list_(self.conn)], ["second", "first", "third"])  # ties: insertion

    def test_list_filter_is_an_exact_label_and_falsy_lists_all(self):
        facts.add(self.conn, self.dir, "A", "allegation")
        facts.add(self.conn, self.dir, "B", "unknown")
        self.assertEqual([g["text"] for g in facts.list_(self.conn, "ALLEGATION")], ["A"])
        self.assertEqual(facts.list_(self.conn, "allegation"), [])  # callers normalize, as the CLI does
        self.assertEqual(facts.list_(self.conn, "HYPOTHESIS"), [])
        self.assertEqual(len(facts.list_(self.conn, None)), 2)
        self.assertEqual(len(facts.list_(self.conn, "")), 2)

    def test_list_of_an_empty_store(self):
        self.assertEqual(facts.list_(self.conn), [])


class TestInvalidLabelOnEveryPath(FactBase):
    """The UI, CLI and MCP server must not drift: each refuses a bad label and writes nothing."""

    def test_cli(self):
        for bad in ("bogus", "documented_fact"):
            r = run_cli("fact", "add", self.dir, "--text", "t", "--status", bad, check=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(r.stderr, f"invalid status '{bad}' — use one of: {VALID_HELP}\n")
            self.assertEqual(r.stdout, "")
        r = run_cli("fact", "add", self.dir, "--text", "t", check=False)  # no --status at all
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.count(self.dir), 0)

    def test_mcp(self):
        for bad in ("bogus", "documented_fact", ""):
            res = self.mcp("matter.fact_add", {"text": "t", "status": bad, "sources": ["Evidence/note.txt@p1"]})
            self.assertTrue(res.get("isError"), bad)
            self.assertEqual(res["content"][0]["text"],
                             f"error: invalid status '{bad}' — use one of: {VALID_HELP}")
        self.assertEqual(self.count(self.dir), 0)
        self.assertEqual(self.count(self.dir, "assertion_sources"), 0)

    def test_module(self):
        conn = store.connect(self.dir)
        self.addCleanup(conn.close)
        with self.assertRaises(facts.FactError):
            facts.add(conn, self.dir, "t", "bogus")
        self.assertEqual(self.count(self.dir), 0)

    def test_mcp_and_cli_accept_the_same_spellings_and_store_the_label(self):
        run_cli("fact", "add", self.dir, "--text", "cli", "--status", "documented-fact")
        self.mcp("matter.fact_add", {"text": "mcp hyphen", "status": "documented-fact"})
        self.mcp("matter.fact_add", {"text": "mcp label", "status": "DOCUMENTED FACT"})
        self.assertEqual(self.rows(self.dir, "SELECT DISTINCT status FROM assertions"), [("DOCUMENTED FACT",)])


class TestEmptyTextOnEveryPath(FactBase):
    def test_cli(self):
        for argv in (("--text", ""), ("--text", "   "), ()):  # blank, whitespace-only, missing
            r = run_cli("fact", "add", self.dir, "--status", "unknown", *argv, check=False)
            self.assertNotEqual(r.returncode, 0, argv)
            self.assertEqual(r.stderr, "refused: fact text is empty — say what the fact is\n")
            self.assertEqual(r.stdout, "")
        self.assertEqual(self.count(self.dir), 0)

    def test_mcp(self):
        for args in ({"text": "", "status": "UNKNOWN"}, {"text": "  \n", "status": "UNKNOWN"},
                     {"status": "UNKNOWN"}, {"text": None, "status": "UNKNOWN"}):
            res = self.mcp("matter.fact_add", args)
            self.assertTrue(res.get("isError"), args)
        self.assertEqual(res["content"][0]["text"][:6], "error:")
        self.assertEqual(self.count(self.dir), 0)

    def test_cli_status_is_checked_before_text(self):
        r = run_cli("fact", "add", self.dir, "--text", "", "--status", "bogus", check=False)
        self.assertTrue(r.stderr.startswith("invalid status 'bogus'"))


if __name__ == "__main__":
    unittest.main()

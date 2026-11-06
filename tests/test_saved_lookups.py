"""CourtListener answers are kept in the case, so the same lookup is never sent twice.

A search or a citation answer is written to the case's .matter/caselaw/. Asking again reads it
back: nothing is sent, no daily allowance is spent, and it works with lookups turned off. `fresh`
asks again. Synthetic data only; no request leaves the machine.
Run: python3 -m unittest tests.test_saved_lookups -v
"""
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matterkit import caselaw, credentials, saved_lookups  # noqa: E402
from test_easy_caselaw import KEY, KIT, NETBLOCK, IsolatedConfig  # noqa: E402

HIT = {"caseName": "Bell Atl. Corp. v. Twombly", "citation": ["550 U.S. 544"],
       "absolute_url": "/opinion/1/x/", "cluster_id": 1, "dateFiled": "2007-05-21"}
DRAFT = "Twombly, 550 U.S. 544 (2007), and Iqbal, 556 U.S. 662 (2009), set the standard.\n"


def answer_for(text):
    """CourtListener's citation-lookup answer to a payload of known cites, one item per line."""
    known = {"550 U.S. 544": ("Bell Atlantic Corp. v. Twombly", 1), "556 U.S. 662": ("Ashcroft v. Iqbal", 2),
             "999 U.S. 1": None}
    out, pos = [], 0
    for line in text.split("\n"):
        hit = known.get(line)
        item = {"citation": line, "normalized_citations": [line], "start_index": pos, "end_index": pos + len(line),
                "status": 404 if hit is None else 200,
                "clusters": [] if hit is None else [{"id": hit[1], "case_name": hit[0], "absolute_url": f"/opinion/{hit[1]}/x/",
                                                     "date_filed": "2007-05-21", "sub_opinions": ["big"] * 3}]}
        out.append(item)
        pos += len(line) + 1
    return out


class SavedCase(IsolatedConfig):
    def setUp(self):
        super().setUp()
        caselaw.turn_on(self.dir, "ty", "tty")
        credentials.save_courtlistener_token(KEY)

    def draft(self, text=DRAFT, name="draft.md"):
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            f.write(text)
        return path

    def search(self, query="plausibility pleading", limit=3, results=(HIT,), **kw):
        from matterkit.adapters import courtlistener as cl
        with mock.patch.object(cl, "get_json", return_value={"results": list(results)}) as g:
            rows = caselaw.search(self.dir, "agent", query, limit=limit, **kw)
        return rows, g.call_count

    def lookup(self, path, **kw):
        from matterkit.adapters import courtlistener_citations as cc
        with mock.patch.object(cc, "post_form", side_effect=lambda url, fields, token: answer_for(fields["text"])) as post:
            report = caselaw.lookup_draft(self.dir, path, "agent", **kw)
        return report, [c.args[1]["text"] for c in post.call_args_list]


class TestSavedSearch(SavedCase):
    def test_second_search_sends_nothing(self):
        first, sent1 = self.search()
        again, sent2 = self.search(query="  Plausibility   PLEADING ")
        self.assertEqual((sent1, sent2), (1, 0))
        self.assertEqual(len(self.egress_lines()), 1)
        self.assertEqual([r.citation for r in again], ["550 U.S. 544"])
        self.assertFalse(first[0].provenance.get("saved"))
        self.assertTrue(again[0].provenance["saved"])
        self.assertEqual(again[0].provenance["retrieved_at"], first[0].provenance["retrieved_at"])

    def test_saved_search_works_with_lookups_off(self):
        self.search()
        caselaw.turn_off(self.dir)
        rows, sent = self.search()
        self.assertEqual((len(rows), sent), (1, 0))

    def test_a_different_question_is_asked(self):
        self.search()
        _, sent = self.search(query="inherent authority")
        self.assertEqual(sent, 1)

    def test_more_results_than_were_saved_are_asked_for(self):
        self.search(limit=1, results=[HIT])
        _, sent = self.search(limit=5, results=[HIT, dict(HIT, cluster_id=2)])
        self.assertEqual(sent, 1)
        rows, sent = self.search(limit=2)  # the 5-result answer covers it
        self.assertEqual((len(rows), sent), (2, 0))

    def test_a_short_answer_is_complete(self):
        self.search(limit=5, results=[HIT])  # CourtListener had only one
        _, sent = self.search(limit=10)
        self.assertEqual(sent, 0)

    def test_fresh_asks_again_and_keeps_the_newer_answer(self):
        self.search()
        rows, sent = self.search(fresh=True, results=[dict(HIT, caseName="Newer Name")])
        self.assertEqual(sent, 1)
        rows, sent = self.search()
        self.assertEqual((rows[0].name, sent), ("Newer Name", 0))

    def test_saved_in_the_case_never_the_key(self):
        self.search()
        path = os.path.join(self.dir, ".matter", "caselaw", "searches.jsonl")
        with open(path) as f:
            text = f.read()
        self.assertIn("plausibility pleading", text)
        self.assertNotIn(KEY, text)

    def test_a_damaged_line_is_skipped(self):
        self.search()
        with open(os.path.join(self.dir, ".matter", "caselaw", "searches.jsonl"), "a") as f:
            f.write("{not json\n[1, 2]\n")
        _, sent = self.search()
        self.assertEqual(sent, 0)


class TestSavedCites(SavedCase):
    def test_second_check_sends_nothing(self):
        path = self.draft()
        first, sent = self.lookup(path)
        self.assertEqual(sent, ["550 U.S. 544\n556 U.S. 662"])
        again, sent = self.lookup(path)
        self.assertEqual(sent, [])
        self.assertEqual(again.sent, 0)
        self.assertEqual([r.outcome for r in again.results], [r.outcome for r in first.results])
        self.assertEqual(again.exit_code, 0)
        self.assertEqual(again.saved, 2)
        self.assertIn("saved answer from", again.results[0].note)
        self.assertIn("2 cites answered from this case's saved lookups", again.render())
        self.assertEqual(len(self.egress_lines()), 1)

    def test_only_new_cites_are_sent(self):
        self.lookup(self.draft())
        report, sent = self.lookup(self.draft(DRAFT + "See also 999 U.S. 1 (2020).\n"))
        self.assertEqual(sent, ["999 U.S. 1"])
        self.assertEqual(report.saved, 2)
        self.assertEqual(report.exit_code, 1)  # the new cite is not found
        self.assertEqual([r.cite.sent for r in report.results], ["550 U.S. 544", "556 U.S. 662", "999 U.S. 1"])

    def test_saved_answers_work_off_and_without_a_key(self):
        path = self.draft()
        self.lookup(path)
        caselaw.turn_off(self.dir)
        credentials.forget_courtlistener_token()
        report, sent = self.lookup(path)
        self.assertEqual((sent, report.exit_code), ([], 0))

    def test_name_is_compared_against_todays_draft(self):
        self.lookup(self.draft())
        report, _ = self.lookup(self.draft("Smith v. Jones, 550 U.S. 544 (2007).\n"))
        self.assertEqual(report.results[0].outcome, "NAME DIFFERS")

    def test_fresh_asks_again(self):
        path = self.draft()
        self.lookup(path)
        _, sent = self.lookup(path, fresh=True)
        self.assertEqual(len(sent), 1)

    def test_unanswered_lines_are_asked_again(self):
        from matterkit.adapters import courtlistener_citations as cc
        path = self.draft()
        over = [dict(i, status=429) for i in answer_for("550 U.S. 544\n556 U.S. 662")]
        with mock.patch.object(cc, "post_form", return_value=over):
            caselaw.lookup_draft(self.dir, path, "agent")
        _, sent = self.lookup(path)
        self.assertEqual(len(sent), 1)

    def test_only_the_fields_read_are_kept(self):
        self.lookup(self.draft())
        with open(os.path.join(self.dir, ".matter", "caselaw", "cites.jsonl")) as f:
            recs = [json.loads(line) for line in f]
        self.assertEqual(sorted(r["citation"] for r in recs), ["550 U.S. 544", "556 U.S. 662"])
        self.assertNotIn("sub_opinions", json.dumps(recs))
        self.assertNotIn(DRAFT.strip(), json.dumps(recs))

    def test_state_counts_saved_lookups(self):
        self.search()
        self.lookup(self.draft())
        self.assertEqual(caselaw.state(self.dir)["saved"], {"searches": 1, "cites": 2})
        self.assertIn("Saved in this case: 1 search, 2 citation answers", caselaw.describe(self.dir))
        self.assertEqual(saved_lookups.counts(self.dir), {"searches": 1, "cites": 2})


class TestSavedOverMcpAndCli(SavedCase):
    """Saved answers come back from the real MCP server and CLI with sockets denied and lookups off."""

    def setUp(self):
        super().setUp()
        self.search()
        self.lookup(self.draft())
        caselaw.turn_off(self.dir)
        credentials.forget_courtlistener_token()

    def env(self):
        env = dict(os.environ, MATTER_DIR=self.dir, PYTHONPATH=NETBLOCK, MATTER_KIT_CONFIG=self.cfg)
        env.pop("COURTLISTENER_TOKEN", None)
        return env

    def test_mcp(self):
        msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "matter.search", "arguments": {"query": "plausibility pleading", "limit": 3}}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "matter.cite_lookup", "arguments": {"path": "draft.md"}}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "matter.search", "arguments": {"query": "plausibility pleading", "fresh": True}}}]
        r = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input="\n".join(map(json.dumps, msgs)) + "\n",
                           capture_output=True, text=True, env=self.env(), cwd=self._tmp.name, timeout=60)
        out = {m["id"]: m["result"]["content"][0]["text"] for m in map(json.loads, r.stdout.splitlines()) if m["id"]}
        self.assertIn("Twombly", out[1])
        self.assertIn("saved in this case", out[1])
        self.assertIn("exit=0", out[2])
        self.assertIn("Nothing was sent", out[3])  # fresh needs lookups on
        self.assertEqual(len(self.egress_lines()), 2)

    def test_cli(self):
        def run(*argv):
            return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *argv], capture_output=True,
                                  text=True, env=self.env(), cwd=self._tmp.name, timeout=60, stdin=subprocess.DEVNULL)
        r = run("search", self.dir, "plausibility pleading", "--limit", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("saved in this case", r.stdout)
        self.assertIn("550 U.S. 544", r.stdout)
        r = run("cite", "lookup", self.dir, "draft.md")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("saved lookups", r.stdout)
        r = run("search", self.dir, "plausibility pleading", "--fresh")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(len(self.egress_lines()), 2)


if __name__ == "__main__":
    unittest.main()

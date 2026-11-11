"""Corpus's free monthly lookups are used up, now that statute and code cites are looked up by default.

Corpus allows a set number of searches a month without a key, counted per network. Before the
default, only a person who had granted the Corpus lookup could run out. Now anyone can, and the
answer Corpus sends is written for an agent (`Authorization: Bearer <key>`), not for the person
reading the citation block. What could go wrong, and so what these tests pin:

- The block shows the protocol's own text ("MCP law.search error -32002: ...") cut off mid-sentence.
- Running out reads as an answer: "Corpus returned no section with this exact cite".
- Every statute cite in the reply is sent anyway, each one refused the same way.
- A person who already has a key is told to go and get one.

ANSWER is the body Corpus sent on 2026-11-10 to one bare cite from a network that had run out.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hermes_plugin import REPLY, PluginCase, corpus  # noqa: E402

ANSWER = {
    "jsonrpc": "2.0", "id": 2,
    "error": {"code": -32002, "message": (
        "Anonymous research quota used up (250 searches/month per IP). You can fix this yourself, "
        "instantly, with nobody in the loop: create a free API key at corpuslaw.us/settings for 1,000 "
        "free searches a month at 30 requests/min, then send it as `Authorization: Bearer <key>`. The "
        "anonymous allotment also resets at the start of next month. Formation tools "
        "(formation.requirements, formation.lookup_naics, formation.handoff) are never metered — "
        "they keep working at zero balance.")}}

STATUTES = ("Miss. Code Ann. § 89-7-27", "Miss. Code Ann. § 89-8-99")


class TestOutOfFreeLookups(PluginCase):
    def setUp(self):
        super().setUp()
        self.asked = []

        def used_up(adapter, payload):
            if payload["method"] == "initialize":
                return {"jsonrpc": "2.0", "id": 1, "result": {}}
            self.asked.append(payload["params"]["arguments"]["query"])
            return ANSWER
        p = mock.patch.object(corpus.CorpusSearchAdapter, "_rpc", used_up)
        p.start()
        self.addCleanup(p.stop)

    def note(self, out):
        return next(line for line in out.splitlines() if "Statute and code cites were not looked up" in line)

    def test_the_block_says_it_in_words(self):
        note = self.note(self.plugin().on_reply(response_text=REPLY))
        self.assertIn("this month's free Corpus lookups from your network are used up", note)
        self.assertIn("They start over next month", note)
        self.assertIn("https://corpuslaw.us/settings", note)
        self.assertIn("CORPUS_API_KEY", note)
        self.assertIn("never in a chat", note)
        for raw in ("MCP", "law.search", "-32002", "Authorization", "Bearer", "Formation", "per IP", "…"):
            self.assertNotIn(raw, note)

    def test_running_out_is_not_an_answer(self):
        out = self.plugin().on_reply(response_text=REPLY)
        self.assertNotIn("NOT MATCHED", out)
        self.assertNotIn("IN CORPUS", out)
        for cite in STATUTES:
            self.assertIn(f"**NOT LOOKED UP** · {cite}", out)

    def test_the_second_cite_is_not_sent_after_the_first_is_refused(self):
        self.plugin().on_reply(response_text=REPLY)
        self.assertEqual(self.asked, [STATUTES[0]])

    def test_a_person_with_a_key_is_not_told_to_get_one(self):
        note = self.note(self.plugin(secrets={"CORPUS_API_KEY": "a-made-up-key"}).on_reply(response_text=REPLY))
        self.assertIn("the lookups allowed on your Corpus key are used up", note)
        self.assertIn("https://corpuslaw.us/settings", note)
        self.assertNotIn("A free Corpus key", note)
        for raw in ("MCP", "-32002", "Authorization", "…"):
            self.assertNotIn(raw, note)

    def test_the_plain_terminal_says_the_same(self):
        p = self.plugin()
        draft = os.path.join(self.dir, "Drafts", "d.md")
        os.makedirs(os.path.dirname(draft), exist_ok=True)
        with open(draft, "w", encoding="utf-8") as f:
            f.write(REPLY)
        out = p.command("check Drafts/d.md", platform="cli")
        self.assertIn("this month's free Corpus lookups from your network are used up", out)
        self.assertNotIn("-32002", out)

    def test_another_corpus_error_still_shows_its_own_text(self):
        def broken(adapter, payload):
            if payload["method"] == "initialize":
                return {"jsonrpc": "2.0", "id": 1, "result": {}}
            return {"jsonrpc": "2.0", "id": 2, "error": {"code": -32603, "message": "Internal error"}}
        with mock.patch.object(corpus.CorpusSearchAdapter, "_rpc", broken):
            note = self.note(self.plugin().on_reply(response_text=REPLY))
        self.assertIn("MCP law.search error -32603: Internal error", note)
        self.assertNotIn("used up", note)


if __name__ == "__main__":
    unittest.main()

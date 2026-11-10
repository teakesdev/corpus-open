"""Cite checking on by default (design 2026-11-09, decisions 2026-11-10).

What could go wrong, and so what these tests pin:

- The default lets something other than a bare citation leave: free-text search turned on, a
  query_text payload accepted, a sentence handed to the statute check sent to Corpus.
- "Off" does not stick: a later run, `init` again, `revoke`, or an agent (MCP) brings the default back.
- A case nobody has chosen for gets nothing, or gets its default written again and again.
- A person's own grant is loosened by the new grant kinds.

Synthetic data only; no request leaves the machine (transports are faked or sockets denied).
Run: python3 -m unittest tests.test_cite_check_default -v
"""
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matterkit import caselaw, cases, citelookup, consent, statutelookup, store  # noqa: E402
from test_easy_caselaw import KEY, KIT, NETBLOCK, IsolatedConfig  # noqa: E402

CITE = (citelookup.PROVIDER, citelookup.ENDPOINT)
STATUTE = (statutelookup.PROVIDER, statutelookup.ENDPOINT)
DRAFT = "Twombly, 550 U.S. 544 (2007), sets the standard.\n"


def records(matter_dir, provider):
    return [g for g in consent.load_grants(matter_dir) if g.get("provider") == provider]


class TestNewCase(IsolatedConfig):
    def setUp(self):
        super().setUp()
        self.new = os.path.join(self._tmp.name, "new case")
        with redirect_stdout(io.StringIO()):
            cases.init(self.new)

    def test_init_writes_the_two_bare_cite_defaults_and_no_search(self):
        grants = {g["provider"]: g for g in consent.load_grants(self.new)}
        self.assertEqual(set(grants), {citelookup.PROVIDER, statutelookup.PROVIDER})
        for provider, g in grants.items():
            self.assertEqual((g["kind"], g["created_by"], g["activated_via"]),
                             ("default", "matter-kit default", "case-default"), provider)
            self.assertEqual(g["payload_types"], ["citation_strings"])
            self.assertEqual(g["authorized_callers"], ["*"])
        self.assertEqual(grants[citelookup.PROVIDER]["endpoints"], [citelookup.ENDPOINT])
        self.assertEqual(grants[statutelookup.PROVIDER]["endpoints"], [statutelookup.ENDPOINT])
        self.assertTrue(caselaw.cite_check_on(self.new))
        self.assertTrue(caselaw.statute_check_on(self.new))
        self.assertFalse(caselaw.search_on(self.new))
        # A new case is not an event worth an egress line: nothing was sent.
        self.assertFalse(os.path.exists(consent.egress_path(self.new)))

    def test_the_default_authorizes_bare_citations_for_every_caller(self):
        for caller in ("agent", "hermes", "someone"):
            consent.authorize(self.new, *CITE, "citation_strings", caller)
            consent.authorize(self.new, *STATUTE, "citation_strings", caller)

    def test_search_is_never_on_by_default(self):
        refused = [(caselaw.SEARCH_PROVIDER, caselaw.SEARCH_HOST, "query_text"),
                   (*CITE, "query_text"), (*STATUTE, "query_text"),
                   ("corpus-search", "corpuslaw.us", "query_text"),
                   (*CITE, "passage_text"), (*STATUTE, "document_text")]
        for provider, endpoint, payload in refused:
            with self.subTest(provider=provider, payload=payload), self.assertRaises(consent.ConsentError):
                consent.authorize(self.new, provider, endpoint, payload, "agent")

    def test_a_default_grant_is_honored_only_for_the_two_bare_cite_scopes(self):
        # Written by hand (as a compromised agent could): a "default" for search, for another
        # endpoint, or for query text is refused all the same.
        forged = [
            {"provider": caselaw.SEARCH_PROVIDER, "endpoints": [caselaw.SEARCH_HOST], "payload_types": ["query_text"]},
            {"provider": "corpus-search", "endpoints": ["corpuslaw.us"], "payload_types": ["citation_strings"]},
            {"provider": citelookup.PROVIDER, "endpoints": ["evil.example"], "payload_types": ["citation_strings"]},
            {"provider": statutelookup.PROVIDER, "endpoints": ["corpuslaw.us"], "payload_types": ["query_text"]},
        ]
        for g in forged:
            consent.save_grants(self.new, [dict(g, kind="default", authorized_callers=["*"],
                                                created_by="matter-kit default")])
            with self.subTest(g=g), self.assertRaises(consent.ConsentError):
                consent.authorize(self.new, g["provider"], g["endpoints"][0], g["payload_types"][0], "agent")

    def test_init_again_changes_nothing(self):
        before = consent.load_grants(self.new)
        with redirect_stdout(io.StringIO()):
            cases.init(self.new)
        self.assertEqual(consent.load_grants(self.new), before)


class TestExistingCase(IsolatedConfig):
    """A case made before this change: no consent.json at all (self.dir comes from store.connect)."""

    def test_a_never_chosen_case_reads_as_on_without_writing(self):
        self.assertTrue(caselaw.cite_check_on(self.dir))
        self.assertTrue(caselaw.statute_check_on(self.dir))
        self.assertFalse(caselaw.search_on(self.dir))
        caselaw.describe(self.dir)
        caselaw.state(self.dir)
        self.assertEqual(consent.load_grants(self.dir), [])

    def test_the_lazy_default_is_written_once_and_noted_once(self):
        written = consent.ensure_defaults(self.dir)
        self.assertEqual(sorted(g["provider"] for g in written), sorted([citelookup.PROVIDER, statutelookup.PROVIDER]))
        self.assertEqual(consent.ensure_defaults(self.dir), [])
        self.assertEqual(len(consent.load_grants(self.dir)), 2)
        notes = [json.loads(line) for line in self.egress_lines()]
        self.assertEqual(sorted(n["grant_provider"] for n in notes), sorted([citelookup.PROVIDER, statutelookup.PROVIDER]))
        for n in notes:
            self.assertEqual(n["event"], "case-default-written")
            self.assertNotIn("provider", n)  # so it never counts against a daily limit
        self.assertEqual(consent._today_usage(self.dir, citelookup.PROVIDER), 0)

    def test_an_agent_proposal_is_not_a_choice(self):
        consent.stage_proposal(self.dir, citelookup.PROVIDER, [citelookup.ENDPOINT], ["citation_strings"], "agent")
        self.assertTrue(caselaw.cite_check_on(self.dir))
        consent.ensure_defaults(self.dir)
        consent.authorize(self.dir, *CITE, "citation_strings", "agent")

    def test_a_persons_narrow_grant_is_a_choice(self):
        # `consent activate --scope cite-lookup --caller hermes`: the person chose who may look up.
        consent.save_grants(self.dir, [{"provider": citelookup.PROVIDER, "endpoints": [citelookup.ENDPOINT],
                                        "payload_types": ["citation_strings"], "kind": "human",
                                        "created_by": "ty", "authorized_callers": ["hermes"]}])
        self.assertEqual([g["provider"] for g in consent.ensure_defaults(self.dir)], [statutelookup.PROVIDER])
        consent.authorize(self.dir, *CITE, "citation_strings", "hermes")
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, *CITE, "citation_strings", "agent")

    def test_cite_lookup_writes_the_default_on_first_use(self):
        from matterkit.adapters import courtlistener_citations as cc
        from matterkit import credentials
        credentials.save_courtlistener_token(KEY)
        path = os.path.join(self.dir, "draft.md")
        with open(path, "w") as f:
            f.write(DRAFT)
        answer = [{"citation": "550 U.S. 544", "normalized_citations": ["550 U.S. 544"], "start_index": 0,
                   "end_index": 12, "status": 200,
                   "clusters": [{"id": 1, "case_name": "Bell Atlantic Corp. v. Twombly", "absolute_url": "/o/1/"}]}]
        with mock.patch.object(cc, "post_form", return_value=answer) as post:
            report = caselaw.lookup_draft(self.dir, path, "agent")
        self.assertEqual(post.call_args.args[1], {"text": "550 U.S. 544"})
        self.assertEqual(report.sent, 1)
        (g,) = records(self.dir, citelookup.PROVIDER)
        self.assertEqual(g["kind"], "default")
        self.assertEqual(records(self.dir, caselaw.SEARCH_PROVIDER), [])


class TestOffSticks(IsolatedConfig):
    def test_courtlistener_off_writes_a_declined_record_that_holds(self):
        consent.ensure_defaults(self.dir)
        caselaw.turn_off(self.dir)
        (rec,) = records(self.dir, citelookup.PROVIDER)
        self.assertEqual(rec["kind"], "declined")
        self.assertFalse(caselaw.cite_check_on(self.dir))
        self.assertTrue(caselaw.statute_check_on(self.dir))  # per provider
        self.assertEqual(consent.ensure_defaults(self.dir), [])
        with redirect_stdout(io.StringIO()):
            cases.init(self.dir)
        consent.revoke(self.dir, [citelookup.PROVIDER])
        self.assertEqual([g["kind"] for g in records(self.dir, citelookup.PROVIDER)], ["declined"])
        with self.assertRaises(consent.ConsentError) as ctx:
            consent.authorize(self.dir, *CITE, "citation_strings", "agent")
        self.assertIn("turned off for this case", str(ctx.exception))

    def test_a_default_written_after_a_decline_is_not_honored(self):
        caselaw.turn_off(self.dir)
        grants = consent.load_grants(self.dir)
        grants.append({"provider": citelookup.PROVIDER, "endpoints": [citelookup.ENDPOINT],
                       "payload_types": ["citation_strings"], "kind": "default", "authorized_callers": ["*"],
                       "created_by": "matter-kit default"})
        consent.save_grants(self.dir, grants)
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, *CITE, "citation_strings", "agent")

    def test_a_persons_grant_still_works_beside_a_decline(self):
        # A decline stops the default only; it never loosens or blocks what a person issues.
        caselaw.turn_off(self.dir)
        grants = consent.load_grants(self.dir) + [{
            "provider": citelookup.PROVIDER, "endpoints": [citelookup.ENDPOINT], "payload_types": ["citation_strings"],
            "kind": "human", "created_by": "ty", "authorized_callers": ["hermes"]}]
        consent.save_grants(self.dir, grants)
        consent.authorize(self.dir, *CITE, "citation_strings", "hermes")

    def test_turning_on_removes_the_decline_and_the_default(self):
        consent.ensure_defaults(self.dir)
        caselaw.turn_off(self.dir)
        caselaw.turn_on(self.dir, "ty", "tty")
        (g,) = records(self.dir, citelookup.PROVIDER)
        self.assertEqual((g["kind"], g["created_by"]), ("human", "ty"))
        self.assertTrue(caselaw.cite_check_on(self.dir))
        self.assertTrue(caselaw.search_on(self.dir))

    def test_statute_checks_turn_off_on_their_own(self):
        consent.ensure_defaults(self.dir)
        caselaw.decline(self.dir, statutelookup.PROVIDER)
        self.assertFalse(caselaw.statute_check_on(self.dir))
        self.assertTrue(caselaw.cite_check_on(self.dir))
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, *STATUTE, "citation_strings", "hermes")
        self.assertEqual(consent.ensure_defaults(self.dir), [])

    def test_state_and_describe_say_what_is_on(self):
        text = caselaw.describe(self.dir)
        self.assertIn("550 U.S. 544", text)
        self.assertIn("corpuslaw.us", text)
        self.assertIn("courtlistener off", text)
        self.assertIn("consent decline", text)
        self.assertIn("courtlistener on", text)  # how a person turns search on
        st = caselaw.state(self.dir)
        self.assertEqual((st["cite_check_on"], st["search_on"], st["statute_check_on"]), (True, False, True))
        caselaw.turn_off(self.dir)
        caselaw.decline(self.dir, statutelookup.PROVIDER)
        st = caselaw.state(self.dir)
        self.assertEqual((st["cite_check_on"], st["search_on"], st["statute_check_on"]), (False, False, False))
        self.assertIn("turned off for this case", caselaw.describe(self.dir))


class TestStatutePayload(unittest.TestCase):
    def test_bare_cites_pass_as_they_are(self):
        for cite in ("42 U.S.C. § 2000e-3(a)", "Miss. Code Ann. § 89-7-27", "§ 1983", "29 C.F.R. § 1604.11",
                     "Tex. Fam. Code § 6.502(a)", "42 U.S.C. § 12112(b)(5)(A)"):
            self.assertEqual(statutelookup.payload(cite), cite)

    def test_a_parenthetical_of_words_is_dropped(self):
        self.assertEqual(statutelookup.payload("42 U.S.C. § 1983 (excessive force)"), "42 U.S.C. § 1983")
        self.assertEqual(statutelookup.payload("Miss. Code Ann. § 89-7-27 (Rev. 2021)"), "Miss. Code Ann. § 89-7-27")
        self.assertEqual(statutelookup.payload("42 U.S.C. § 2000e-3(a) (2018)"), "42 U.S.C. § 2000e-3(a)")

    def test_anything_but_one_parsed_cite_is_refused(self):
        for bad in ("my landlord hit me", "42 U.S.C. § 1983 and the landlord kept my deposit",
                    "Under 42 U.S.C. § 1983", "42 U.S.C. § 1983\n42 U.S.C. § 1985", "", "   ",
                    "§ 1983; § 1985", "Plaintiff Jane Roe", "x" * 300, None, 1983):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                statutelookup.payload(bad)

    def test_jurisdiction_is_a_two_letter_code_or_nothing(self):
        self.assertEqual(statutelookup.jurisdiction("MS"), "MS")
        self.assertIsNone(statutelookup.jurisdiction(None))
        for bad in ("Mississippi", "the landlord", "ms", "M S"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                statutelookup.jurisdiction(bad)


class TestStatuteAdapter(IsolatedConfig):
    """The Corpus adapter for the statute check: one parsed cite in, nothing else out."""

    def setUp(self):
        super().setUp()
        from matterkit.adapters import corpus_search
        self.mod = corpus_search
        self.sent = []

        def rpc(adapter, payload):
            self.sent.append(payload)
            if payload["method"] == "initialize":
                return {"jsonrpc": "2.0", "id": 1, "result": {}}
            return {"jsonrpc": "2.0", "id": 2, "result": {"text": "", "isError": False}}
        p = mock.patch.object(corpus_search.CorpusSearchAdapter, "_rpc", rpc)
        p.start()
        self.addCleanup(p.stop)

    def search(self, text, jurisdiction=None, caller="hermes"):
        from matterkit.search import SearchQuery
        a = self.mod.CorpusStatuteLookupAdapter(self.dir, "anonymous")
        return a.search(SearchQuery(text=text, jurisdiction=jurisdiction, limit=5), caller)

    def test_sends_only_the_bare_cite_under_the_default(self):
        consent.ensure_defaults(self.dir, note=False)
        self.search("42 U.S.C. § 1983 (excessive force)", "US")
        args = self.sent[-1]["params"]["arguments"]
        self.assertEqual(args, {"query": "42 U.S.C. § 1983", "limit": 5, "jurisdiction": "US"})
        (line,) = [json.loads(x) for x in self.egress_lines()]
        self.assertEqual((line["provider"], line["endpoint"], line["payload_type"]),
                         (statutelookup.PROVIDER, "corpuslaw.us", "citation_strings"))

    def test_a_sentence_is_refused_before_anything_is_sent(self):
        consent.ensure_defaults(self.dir, note=False)
        for bad, juris in (("my landlord kept the deposit", None), ("42 U.S.C. § 1983 and the facts", None),
                           ("42 U.S.C. § 1983", "the landlord")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.search(bad, juris)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.egress_lines(), [])

    def test_declined_or_query_text_only_sends_nothing(self):
        caselaw.decline(self.dir, statutelookup.PROVIDER)
        # A person's free-text Corpus grant does not stand in for the statute check.
        grants = consent.load_grants(self.dir) + [{
            "provider": "corpus-search", "endpoints": ["corpuslaw.us"], "payload_types": ["query_text"],
            "kind": "human", "created_by": "ty", "authorized_callers": ["*"]}]
        consent.save_grants(self.dir, grants)
        with self.assertRaises(consent.ConsentError):
            self.search("42 U.S.C. § 1983")
        self.assertEqual(self.sent, [])

    def test_node_lookups_are_not_part_of_the_check(self):
        consent.ensure_defaults(self.dir, note=False)
        a = self.mod.CorpusStatuteLookupAdapter(self.dir, "anonymous")
        with self.assertRaises(consent.ConsentError):
            a.get_node("37495c6b-7c51-4053-b24b-9a966a1a4492", "hermes")
        self.assertEqual(self.sent, [])


class TestMcpCannotUndoOff(IsolatedConfig):
    """The real MCP server in a child with sockets denied."""

    def mcp(self, *calls):
        msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}]
        for i, (name, args) in enumerate(calls, 1):
            msgs.append({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}})
        env = dict(os.environ, MATTER_DIR=self.dir, PYTHONPATH=NETBLOCK, MATTER_KIT_CONFIG=self.cfg)
        env.pop("COURTLISTENER_TOKEN", None)
        r = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input="\n".join(map(json.dumps, msgs)) + "\n",
                           capture_output=True, text=True, env=env, cwd=self._tmp.name, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return {m["id"]: m["result"]["content"][0]["text"] for m in map(json.loads, r.stdout.splitlines()) if m["id"]}

    def test_mcp_never_removes_a_declined_record_or_turns_search_on(self):
        from matterkit import credentials
        credentials.save_courtlistener_token(KEY)
        caselaw.turn_off(self.dir)
        caselaw.decline(self.dir, statutelookup.PROVIDER)
        before = consent.load_grants(self.dir)
        with open(os.path.join(self.dir, "draft.md"), "w") as f:
            f.write(DRAFT)
        out = self.mcp(("matter.caselaw_status", {}), ("matter.cite_lookup", {"path": "draft.md"}),
                       ("matter.search", {"query": "docket control"}))
        self.assertIn("Nothing was sent", out[2])
        self.assertIn("Nothing was sent", out[3])
        self.assertEqual(consent.load_grants(self.dir), before)
        self.assertEqual(self.egress_lines(), [])

    def test_mcp_puts_a_never_chosen_case_on_its_default_and_no_further(self):
        with open(os.path.join(self.dir, "draft.md"), "w") as f:
            f.write(DRAFT)
        out = self.mcp(("matter.cite_lookup", {"path": "draft.md"}), ("matter.search", {"query": "docket control"}))
        self.assertIn("Nothing was sent", out[1])  # no key
        self.assertIn("CourtListener key", out[1])
        self.assertEqual({(g["provider"], g["kind"]) for g in consent.load_grants(self.dir)},
                         {(citelookup.PROVIDER, "default")})
        self.assertFalse(caselaw.search_on(self.dir))
        lines = [json.loads(x) for x in self.egress_lines()]
        self.assertEqual([x.get("event") for x in lines], ["case-default-written"])

    def test_mcp_source_cannot_write_a_choice(self):
        src = open(os.path.join(KIT, "matter-mcp.py")).read()
        for name in ("decline", "revoke", "turn_off", "write_declined", "ensure_defaults", "save_grants"):
            self.assertNotIn(name, src)


class TestCli(IsolatedConfig):
    def cli(self, *args):
        env = dict(os.environ, MATTER_KIT_CONFIG=self.cfg)
        env.pop("COURTLISTENER_TOKEN", None)
        return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *args],
                              capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
                              start_new_session=True, timeout=60)  # no controlling terminal

    def test_init_without_a_terminal_asks_nothing_and_writes_the_default(self):
        target = os.path.join(self._tmp.name, "c1")
        r = self.cli("init", target)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("key:", r.stdout)
        self.assertNotIn("[y/N]", r.stdout)
        self.assertTrue(caselaw.cite_check_on(target))
        self.assertFalse(caselaw.search_on(target))
        self.assertEqual({g["kind"] for g in consent.load_grants(target)}, {"default"})

    def test_consent_decline_turns_a_check_off_without_a_terminal(self):
        r = self.cli("consent", "decline", self.dir, "--scope", "statute-lookup")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(caselaw.statute_check_on(self.dir))
        self.assertIn("stays off", r.stdout)
        # Turning it back on takes a person at a terminal.
        r = self.cli("consent", "activate", self.dir, "--scope", "statute-lookup")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(caselaw.statute_check_on(self.dir))

    def test_courtlistener_off_sticks(self):
        r = self.cli("courtlistener", "off", self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(caselaw.cite_check_on(self.dir))
        self.assertIn("off", self.cli("courtlistener", "status", self.dir).stdout)


class TestFirstRunQuestions(IsolatedConfig):
    """`matter.py init` on a terminal asks two things once; the questions are caselaw.first_run's."""

    def run_first(self, answers, key_given=""):
        asked = []

        def ask(question):
            asked.append(question)
            return answers.pop(0)

        def ask_key():
            asked.append("key")
            return key_given
        out = io.StringIO()
        with redirect_stdout(out):
            caselaw.first_run(self.dir, "ty", ask=ask, ask_key=ask_key, say=print)
        return asked, out.getvalue()

    def test_asks_for_the_key_then_search_and_search_stays_off_on_no(self):
        consent.ensure_defaults(self.dir, note=False)
        asked, said = self.run_first(["n"])
        self.assertEqual(asked[0], "key")
        self.assertIn("[y/N]", asked[1])
        self.assertIn("can describe your facts", asked[1])
        self.assertFalse(caselaw.search_on(self.dir))
        self.assertTrue(caselaw.cite_check_on(self.dir))
        self.assertIn("550 U.S. 544", said)

    def test_yes_turns_search_on_as_the_person(self):
        consent.ensure_defaults(self.dir, note=False)
        self.run_first(["y"])
        self.assertTrue(caselaw.search_on(self.dir))
        g = records(self.dir, caselaw.SEARCH_PROVIDER)[0]
        self.assertEqual((g["kind"], g["created_by"], g["activated_via"]), ("human", "ty", "tty"))

    def test_no_key_question_when_a_key_is_saved(self):
        from matterkit import credentials
        credentials.save_courtlistener_token(KEY)
        asked, _ = self.run_first([""])
        self.assertNotIn("key", asked)
        self.assertFalse(caselaw.search_on(self.dir))  # Enter means no


if __name__ == "__main__":
    unittest.main()

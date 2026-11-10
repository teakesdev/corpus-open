"""Cite checks on by default, inside Hermes (design 2026-11-09, decisions 2026-11-10, extension 2), and
the plugin's credentials coming through Hermes (catalog rule 11).

What could go wrong, and so what these tests pin:

- A case nobody chose for sends more than the bare cites, or a case a person turned off sends anything.
- A case made before this change never gets its default, or gets it in local mode, where nothing may
  be written or sent.
- /matter grant, /matter init or a reply asks for the CourtListener key in the chat, or tells the
  person to save it anywhere but Hermes's own settings.
- With Hermes's secret route present, the plugin reads a bare environment variable (another profile's
  key under multiplexing).

Everything here is synthetic and no test reaches the network: CourtListener answers come from
test_hermes_plugin's post_form fake, Corpus answers from its _rpc fake.
"""
import json
import os
import re
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hermes_plugin import (REPLY, SAVED_KEY, TOKEN, Ctx, PluginCase, citelookup, consent, corpus,  # noqa: E402
                                fake_rpc, hp, pcreds, read_manifest)

from matterkit import caselaw, statutelookup  # noqa: E402

CORPUS_KEY = "corpus-synthetic-0000"
INTERNALS = ("no valid grant", "citation_strings", "query_text", "api/rest", "authorized_callers",
             "courtlistener-citation-lookup", "corpus-statute-lookup")


def fake_hermes(values, raises=False):
    """sys.modules entries that stand in for Hermes's agent.secret_scope."""
    agent = types.ModuleType("agent")
    scope = types.ModuleType("agent.secret_scope")

    def get_secret(name, default=None):
        if raises:
            raise RuntimeError("get_secret called with no profile secret scope active")
        return values.get(name, default)
    scope.get_secret = get_secret
    agent.secret_scope = scope
    return mock.patch.dict(sys.modules, {"agent": agent, "agent.secret_scope": scope})


class Base(PluginCase):
    def setUp(self):
        super().setUp()
        self.corpus_calls = []  # (auth mode, key, query) for each law.search

        def rpc(adapter, payload):
            if payload["method"] == "tools/call":
                self.corpus_calls.append((adapter.auth_mode, adapter._api_key(),
                                          payload["params"]["arguments"]["query"]))
            return fake_rpc(adapter, payload)
        p = mock.patch.object(corpus.CorpusSearchAdapter, "_rpc", rpc)
        p.start()
        self.addCleanup(p.stop)

    def egress(self):
        path = consent.egress_path(self.dir)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def forget_consent(self):
        """A case made before cite checks were on by default: no consent file at all."""
        os.remove(os.path.join(self.dir, ".matter", "consent.json"))

    def notes(self, block):
        return [ln for ln in block.splitlines() if ln.startswith("_") and "not looked up" in ln]


class TestDefaultCase(Base):
    def test_a_new_case_sends_only_the_bare_cites(self):
        block = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}).on_reply(response_text=REPLY)[len(REPLY):]
        self.assertIn("**NOT FOUND** · Doe v. Roe Corp., 501 U.S. 1001", block)
        self.assertIn("IN CORPUS", block)
        self.assertEqual(len(self.posted), 1)
        sent = self.posted[0][1]["text"]
        for word in ("tenant", "lease", "Smith", "Doe", "landlord", "court"):
            self.assertNotIn(word, sent)
        self.assertEqual([q for _m, _k, q in self.corpus_calls],
                         ["Miss. Code Ann. § 89-7-27", "Miss. Code Ann. § 89-8-99"])
        providers = [e.get("provider") for e in self.egress() if "event" not in e]
        self.assertEqual(sorted(set(providers)), sorted({citelookup.PROVIDER, statutelookup.PROVIDER}))

    def test_words_after_a_statute_cite_never_go_with_it(self):
        text = ("My boss fired me after I complained. 42 U.S.C. § 2000e-3(a) (retaliation for complaining "
                "about my supervisor) covers it.")
        self.plugin().on_reply(response_text=text)
        self.assertEqual([q for _m, _k, q in self.corpus_calls], ["42 U.S.C. § 2000e-3(a)"])

    def test_a_case_made_before_this_change_gets_its_default_on_first_use(self):
        self.forget_consent()
        self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}).on_reply(response_text=REPLY)
        kinds = {(g["provider"], g["kind"]) for g in consent.load_grants(self.dir)}
        self.assertEqual(kinds, {(citelookup.PROVIDER, consent.DEFAULT), (statutelookup.PROVIDER, consent.DEFAULT)})
        events = [e for e in self.egress() if e.get("event") == "case-default-written"]
        self.assertEqual(len(events), 2)
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(len(self.corpus_calls), 2)

    def test_local_mode_writes_and_sends_nothing(self):
        self.forget_consent()
        block = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}, citation_guard="local").on_reply(
            response_text=REPLY)
        self.assertIn("local only", block)
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".matter", "consent.json")))
        self.assertEqual((self.posted, self.corpus_calls, self.egress()), ([], [], []))

    def test_no_case_folder_writes_nothing(self):
        self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}, matter_dir="").on_reply(response_text=REPLY)
        self.assertEqual((self.posted, self.corpus_calls), ([], []))


class TestTurnedOff(Base):
    def test_a_case_a_person_turned_off_sends_nothing_and_stays_off(self):
        caselaw.turn_off(self.dir)
        caselaw.decline(self.dir, statutelookup.PROVIDER)
        p = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN})
        block = p.on_reply(response_text=REPLY)[len(REPLY):]
        self.assertEqual((self.posted, self.corpus_calls), ([], []))
        notes = self.notes(block)
        self.assertEqual(len(notes), 2, block)
        for n in notes:
            self.assertIn("turned off for this case", n)
            self.assertIn("/matter grant", n)
            self.assertTrue(n.endswith("._"), n)
        for word in INTERNALS:
            self.assertNotIn(word, block)
        p.on_reply(response_text=REPLY + " Again.")
        kinds = sorted(g["kind"] for g in consent.load_grants(self.dir))
        self.assertEqual(kinds, [consent.DECLINED, consent.DECLINED])
        self.assertEqual((self.posted, self.corpus_calls), ([], []))

    def test_turning_statutes_off_leaves_case_cites_on(self):
        caselaw.decline(self.dir, statutelookup.PROVIDER)
        self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}).on_reply(response_text=REPLY)
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.corpus_calls, [])

    def test_an_agent_proposal_does_not_count_as_a_choice(self):
        # An agent's proposal is inert: the case default still applies, and nothing beyond bare cites goes.
        proposal = {"provider": citelookup.PROVIDER, "endpoints": [citelookup.ENDPOINT],
                    "payload_types": ["query_text"], "daily_budget": 10, "created_by": "hermes",
                    "kind": "agent", "authorized_callers": ["hermes"]}
        self.forget_consent()
        consent.save_grants(self.dir, [proposal])
        self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}).on_reply(response_text=REPLY)
        self.assertEqual(len(self.posted), 1)
        self.assertNotIn("tenant", self.posted[0][1]["text"])


class TestGrantWords(Base):
    def test_grant_says_what_leaves_and_how_to_add_the_key_in_hermes(self):
        out = self.plugin().command("grant")
        for words in ("550 U.S. 544", "42 U.S.C. § 2000e-3(a)", "corpuslaw.us", "Nothing else",
                      "Settings > Plugins > corpus-open", "courtlistener_token", "Save settings",
                      "hermes config set COURTLISTENER_TOKEN", "https://www.courtlistener.com/profile/api/"):
            self.assertIn(words, out)
        self.assertIn("never paste it into a chat", out.lower())
        self.assertIn(f"courtlistener off {self.dir}", out)
        self.assertIn(f"consent decline {self.dir} --scope statute-lookup", out)
        self.assertIn(f"consent activate {self.dir} --scope statute-lookup", out)
        self.assertIn(f"courtlistener on {self.dir}", out)
        self.assertIn("citation_guard", out)
        # The kit's own key file is not the way inside Hermes.
        self.assertNotIn("courtlistener key", out)
        for word in INTERNALS:
            self.assertNotIn(word, out)

    def test_grant_names_what_this_case_has(self):
        self.assertIn("Case cites: on (the default)", self.plugin().command("grant"))
        caselaw.turn_off(self.dir)
        out = self.plugin().command("grant")
        self.assertIn("Case cites: turned off for this case by a person", out)
        self.assertIn("Statute and code cites: on (the default)", out)
        self.assertIn("Search: off", out)

    def test_grant_with_a_key_set_does_not_ask_for_one(self):
        out = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}).command("grant")
        self.assertIn("CourtListener key: set", out)
        self.assertNotIn("hermes config set", out)
        self.assertNotIn(TOKEN, out)

    def test_nothing_asks_for_the_key_in_the_chat(self):
        target = os.path.join(self._tmp.name, "fresh case")
        demo = os.path.join(self._tmp.name, "demo")
        p = self.plugin()
        texts = [p.command("grant"), p.command(""), p.on_reply(response_text=REPLY) or ""]
        texts.append(self.plugin(matter_dir="").command(f"init {target}"))
        texts.append(self.plugin(matter_dir="").command(f"demo {demo}"))
        asks = re.compile(r"(paste|type|send|give|tell)\b[^.\n]*\bkey\b[^.\n]*\b(here|to me|in (this|the) chat)",
                          re.I)
        for text in texts:
            self.assertIsNone(asks.search(text), text)
            self.assertNotIn("courtlistener key", text)

    def test_init_says_cite_checks_are_on_and_where_the_key_goes(self):
        target = os.path.join(self._tmp.name, "mine")
        out = self.plugin(matter_dir="").command(f"init {target}")
        self.assertIn("Cite checks are on", out)
        self.assertIn("/matter grant", out)
        self.assertNotIn("[y/N]", out)

    def test_demo_says_the_made_up_case_needs_only_a_key(self):
        out = self.plugin(matter_dir="").command(f"demo {os.path.join(self._tmp.name, 'demo')}")
        self.assertIn("CourtListener key", out)
        self.assertIn("/matter grant", out)
        out = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}, matter_dir="").command(
            f"demo {os.path.join(self._tmp.name, 'demo2')}")
        self.assertNotIn("/matter grant", out)
        self.assertIn("the made-up case", out)


class TestManifest(unittest.TestCase):
    def test_version_and_what_leaves(self):
        m = read_manifest()
        self.assertEqual(m["version"], '"0.6.0"')
        with open(os.path.join(hp.KIT, "plugin.yaml"), encoding="utf-8") as f:
            text = " ".join(f.read().split())
        self.assertNotIn("only after a person grants them", text)
        for words in ("bare case cites", "CourtListener", "only when a key is set", "corpuslaw.us",
                      "Nothing else goes anywhere", "off sticks"):
            self.assertIn(words, text)
        self.assertNotIn("matter.py courtlistener key", text)


class TestHermesSecretRoute(Base):
    """Rule 11: with Hermes's agent.secret_scope present, both secrets come from get_secret."""

    def hermes_plugin(self, **settings):
        settings.setdefault("matter_dir", self.dir)
        self.ctx = Ctx(settings)
        return hp.register(self.ctx)  # the real _secret, reading the fake Hermes module

    def test_both_secrets_come_from_get_secret(self):
        with fake_hermes({"COURTLISTENER_TOKEN": TOKEN, "CORPUS_API_KEY": CORPUS_KEY}):
            self.hermes_plugin().on_reply(response_text=REPLY)
        self.assertEqual([t for _u, _d, t in self.posted], [TOKEN])
        self.assertEqual({(m, k) for m, k, _q in self.corpus_calls}, {("authenticated", CORPUS_KEY)})

    def test_a_bare_environment_variable_is_not_read_when_hermes_has_the_route(self):
        env = {"COURTLISTENER_TOKEN": "env-" + TOKEN, "CORPUS_API_KEY": "env-" + CORPUS_KEY}
        with fake_hermes({}), mock.patch.dict(os.environ, env):
            block = self.hermes_plugin().on_reply(response_text=REPLY)
        self.assertEqual(self.posted, [])
        self.assertIn("no CourtListener key", block)
        self.assertEqual({(m, k) for m, k, _q in self.corpus_calls}, {("anonymous", "")})

    def test_an_unscoped_read_counts_as_unset(self):
        with fake_hermes({"COURTLISTENER_TOKEN": TOKEN}, raises=True), \
                mock.patch.dict(os.environ, {"COURTLISTENER_TOKEN": "env-" + TOKEN}):
            self.hermes_plugin().on_reply(response_text=REPLY)
        self.assertEqual(self.posted, [])

    def test_the_kits_saved_key_is_still_the_fallback(self):
        # Kept on purpose (coordinator, 2026-11-10): the owner decides whether rule 11 removes it.
        pcreds.save_courtlistener_token(SAVED_KEY)
        with fake_hermes({}):
            self.hermes_plugin().on_reply(response_text=REPLY)
        self.assertEqual([t for _u, _d, t in self.posted], [SAVED_KEY])

    def test_a_hermes_without_the_route_keeps_the_old_way(self):
        with mock.patch.dict(os.environ, {"COURTLISTENER_TOKEN": "env-" + TOKEN}):
            self.hermes_plugin().on_reply(response_text=REPLY)
        self.assertEqual([t for _u, _d, t in self.posted], ["env-" + TOKEN])


if __name__ == "__main__":
    unittest.main()

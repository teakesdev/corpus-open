"""Case law lookups for people who don't use a terminal: one switch per case (browser UI or one
command), a key saved outside every case folder, and search + cite lookup over MCP.

RFC 0001 amendment 2026-11-06. Synthetic data only; no request leaves the machine.
Run: python3 -m unittest tests.test_easy_caselaw -v
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matterkit import caselaw, citelookup, consent, credentials, store  # noqa: E402
from webui_helpers import ServerCase  # noqa: E402

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "0123456789abcdef0123456789abcdef01234567"  # key-shaped, not a real key
NETBLOCK = os.path.join(KIT, "tests", "netblock_sitecustomize")
DRAFT = "Twombly, 550 U.S. 544 (2007), sets the standard.\n"


class IsolatedConfig(unittest.TestCase):
    """A matter in a tempdir, and a settings folder of its own, so no test reads or writes the
    person's real ~/.config/matter-kit or picks up their COURTLISTENER_TOKEN."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "matter")
        self.cfg = os.path.join(self._tmp.name, "config", "matter-kit")
        store.connect(self.dir)
        env = mock.patch.dict(os.environ, {"MATTER_KIT_CONFIG": self.cfg})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("COURTLISTENER_TOKEN", None)

    def egress_lines(self):
        try:
            with open(consent.egress_path(self.dir)) as f:
                return f.read().splitlines()
        except OSError:
            return []


class TestKeyStore(IsolatedConfig):
    def test_saved_owner_only_and_outside_the_case(self):
        where = credentials.save_courtlistener_token("  " + KEY + "\n")
        self.assertTrue(where.startswith(self.cfg))
        self.assertFalse(where.startswith(self.dir))
        self.assertEqual(stat.S_IMODE(os.stat(where).st_mode), 0o600)
        self.assertEqual(credentials.courtlistener_token(), KEY)
        self.assertEqual(credentials.courtlistener_token_source(), "settings")

    def test_pasted_header_word_is_dropped(self):
        credentials.save_courtlistener_token("Token " + KEY)
        self.assertEqual(credentials.courtlistener_token(), KEY)

    def test_not_key_shaped_is_refused_and_nothing_saved(self):
        for bad in ("", "hello world", "abc", KEY + "\nsecond line", "x" * 101, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                credentials.save_courtlistener_token(bad)
        self.assertIsNone(credentials.courtlistener_token_source())

    def test_environment_wins_and_forget(self):
        credentials.save_courtlistener_token(KEY)
        with mock.patch.dict(os.environ, {"COURTLISTENER_TOKEN": "f" * 40}):
            self.assertEqual(credentials.courtlistener_token(), "f" * 40)
            self.assertEqual(credentials.courtlistener_token_source(), "environment")
        self.assertTrue(credentials.forget_courtlistener_token())
        self.assertFalse(credentials.forget_courtlistener_token())
        self.assertEqual(credentials.courtlistener_token(), "")


class TestAnyCaller(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        os.makedirs(os.path.join(self.dir, ".matter"))

    def write(self, **over):
        g = {"provider": "p1", "endpoints": ["example.org"], "payload_types": ["query_text"],
             "created_by": "ty", "kind": "human", "authorized_callers": ["*"]}
        g.update(over)
        consent.save_grants(self.dir, [g])

    def test_open_human_grant_authorizes_every_caller(self):
        self.write()
        for caller in ("agent", "agent:claude", "someone-else"):
            consent.authorize(self.dir, "p1", "example.org", "query_text", caller)

    def test_open_grant_still_keeps_its_scope(self):
        self.write()
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, "p1", "example.org", "document_text", "agent")
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, "p1", "evil.example", "query_text", "agent")

    def test_agent_written_open_grant_is_still_inert(self):
        self.write(kind="agent", created_by="agent")
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, "p1", "example.org", "query_text", "agent")
        self.write(created_by="agent")
        del_kind = consent.load_grants(self.dir)[0]
        del del_kind["kind"]
        consent.save_grants(self.dir, [del_kind])
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, "p1", "example.org", "query_text", "agent")

    def test_refusal_says_how_a_person_turns_it_on(self):
        with self.assertRaises(consent.ConsentError) as ctx:
            consent.authorize(self.dir, "p1", "example.org", "query_text", "agent")
        self.assertIn("Turn on", str(ctx.exception))


class TestSwitch(IsolatedConfig):
    def test_off_by_default(self):
        self.assertFalse(caselaw.is_on(self.dir))
        self.assertIn("off", caselaw.describe(self.dir))

    def test_turn_on_writes_both_grants_with_liberal_limits(self):
        caselaw.turn_on(self.dir, "ty", "web-ui")
        grants = {g["provider"]: g for g in consent.load_grants(self.dir)}
        self.assertEqual(set(grants), {caselaw.SEARCH_PROVIDER, citelookup.PROVIDER})
        self.assertEqual(grants[caselaw.SEARCH_PROVIDER]["daily_budget"], 500)
        self.assertEqual(grants[citelookup.PROVIDER]["daily_budget"], 100)
        for g in grants.values():
            self.assertEqual((g["kind"], g["created_by"], g["activated_via"]), ("human", "ty", "web-ui"))
            self.assertEqual(g["authorized_callers"], ["*"])
            self.assertNotIn("expires", g)
        self.assertTrue(caselaw.is_on(self.dir))
        consent.authorize(self.dir, citelookup.PROVIDER, citelookup.ENDPOINT, "citation_strings", "agent")

    def test_turning_on_twice_leaves_one_grant_each(self):
        consent.stage_proposal(self.dir, citelookup.PROVIDER, [citelookup.ENDPOINT], ["citation_strings"], "agent")
        caselaw.turn_on(self.dir, "ty", "tty")
        caselaw.turn_on(self.dir, "ty", "web-ui")
        self.assertEqual(len(consent.load_grants(self.dir)), 2)

    def test_turn_off(self):
        caselaw.turn_on(self.dir, "ty", "tty")
        consent.save_grants(self.dir, consent.load_grants(self.dir) + [{"provider": "other", "kind": "human"}])
        self.assertEqual(caselaw.turn_off(self.dir), 2)
        self.assertFalse(caselaw.is_on(self.dir))
        self.assertEqual([g["provider"] for g in consent.load_grants(self.dir)], ["other"])

    def test_not_a_case_folder(self):
        with self.assertRaises(ValueError):
            caselaw.turn_on(os.path.join(self._tmp.name, "nowhere"), "ty", "tty")

    def test_state_never_holds_the_key(self):
        credentials.save_courtlistener_token(KEY)
        caselaw.turn_on(self.dir, "ty", "tty")
        st = caselaw.state(self.dir)
        self.assertEqual(st["key"], "settings")
        self.assertNotIn(KEY, json.dumps(st) + caselaw.describe(self.dir))


class TestLookupsInProcess(IsolatedConfig):
    def test_search_uses_the_saved_key(self):
        from matterkit.adapters import courtlistener as cl
        caselaw.turn_on(self.dir, "ty", "tty")
        credentials.save_courtlistener_token(KEY)
        seen = {}

        def fake_get_json(url, auth_mode="anonymous", token=""):
            seen.update(auth_mode=auth_mode, token=token)
            return {"results": [{"caseName": "Bell Atl. Corp. v. Twombly", "citation": ["550 U.S. 544"],
                                 "absolute_url": "/opinion/1/x/", "cluster_id": 1, "dateFiled": "2007-05-21"}]}
        with mock.patch.object(cl, "get_json", fake_get_json):
            rows = caselaw.search(self.dir, "agent", "plausibility pleading", limit=3)
        self.assertEqual(seen, {"auth_mode": "authenticated", "token": KEY})
        self.assertEqual(rows[0].citation, "550 U.S. 544")
        self.assertEqual(len(self.egress_lines()), 1)
        self.assertNotIn(KEY, "".join(self.egress_lines()))

    def test_search_without_a_key_is_anonymous(self):
        from matterkit.adapters import courtlistener as cl
        caselaw.turn_on(self.dir, "ty", "tty")
        with mock.patch.object(cl, "get_json", return_value={"results": []}) as g:
            caselaw.search(self.dir, "agent", "q")
        self.assertEqual(g.call_args.kwargs["auth_mode"], "anonymous")

    def test_cite_lookup_uses_the_saved_key(self):
        from matterkit.adapters import courtlistener_citations as cc
        caselaw.turn_on(self.dir, "ty", "tty")
        credentials.save_courtlistener_token(KEY)
        path = os.path.join(self.dir, "draft.md")
        with open(path, "w") as f:
            f.write(DRAFT)
        answer = [{"citation": "550 U.S. 544", "normalized_citations": ["550 U.S. 544"], "start_index": 9,
                   "end_index": 21, "status": 200,
                   "clusters": [{"case_name": "Bell Atlantic Corp. v. Twombly", "absolute_url": "/opinion/1/x/"}]}]
        with mock.patch.object(cc, "post_form", return_value=answer) as post:
            report = caselaw.lookup_draft(self.dir, path, "agent")
        self.assertEqual(post.call_args.args[2], KEY)
        self.assertEqual(post.call_args.args[1], {"text": "550 U.S. 544"})
        self.assertEqual(report.sent, 1)

    def test_cite_lookup_without_a_key_sends_nothing(self):
        caselaw.turn_on(self.dir, "ty", "tty")
        path = os.path.join(self.dir, "draft.md")
        with open(path, "w") as f:
            f.write(DRAFT)
        with self.assertRaises(consent.ConsentError) as ctx:
            caselaw.lookup_draft(self.dir, path, "agent")
        self.assertIn("Case law lookups panel", str(ctx.exception))
        self.assertEqual(self.egress_lines(), [])


class TestMcp(IsolatedConfig):
    """The real server in a child with sockets denied: refusals send nothing, and say how to turn on."""

    def mcp(self, *calls):
        msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}]
        for i, (name, args) in enumerate(calls, 1):
            msgs.append({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}})
        msgs.append({"jsonrpc": "2.0", "id": 99, "method": "tools/list"})
        env = dict(os.environ, MATTER_DIR=self.dir, PYTHONPATH=NETBLOCK, MATTER_KIT_CONFIG=self.cfg)
        env.pop("COURTLISTENER_TOKEN", None)
        r = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input="\n".join(map(json.dumps, msgs)) + "\n",
                           capture_output=True, text=True, env=env, cwd=self._tmp.name, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return {m["id"]: m for m in map(json.loads, r.stdout.splitlines())}

    def text(self, out, i):
        return out[i]["result"]["content"][0]["text"]

    def test_tools_listed_and_none_can_turn_lookups_on(self):
        out = self.mcp()
        names = [t["name"] for t in out[99]["result"]["tools"]]
        for name in ("matter.search", "matter.cite_lookup", "matter.caselaw_status"):
            self.assertIn(name, names)
        for name in names:
            self.assertNotRegex(name, r"consent|activate|turn_on|grant|caselaw_set|key")

    def test_off_replies_explain_and_send_nothing(self):
        with open(os.path.join(self.dir, "draft.md"), "w") as f:
            f.write(DRAFT)
        out = self.mcp(("matter.caselaw_status", {}), ("matter.search", {"query": "docket control"}),
                       ("matter.cite_lookup", {"path": "draft.md"}))
        for i in (1, 2, 3):
            with self.subTest(call=i):
                self.assertFalse(out[i]["result"].get("isError"), out[i])
                self.assertIn("Turn on", self.text(out, i))
                self.assertIn("matter.py courtlistener on", self.text(out, i))
        self.assertIn("Nothing was sent", self.text(out, 2))
        self.assertEqual(self.egress_lines(), [])
        self.assertEqual(consent.load_grants(self.dir), [])

    def test_on_without_key_cite_lookup_asks_for_the_key(self):
        caselaw.turn_on(self.dir, "ty", "tty")
        with open(os.path.join(self.dir, "draft.md"), "w") as f:
            f.write(DRAFT)
        out = self.mcp(("matter.caselaw_status", {}), ("matter.cite_lookup", {"path": "draft.md"}))
        self.assertIn("are on", self.text(out, 1))
        self.assertIn("key: not set", self.text(out, 1))
        self.assertIn("Nothing was sent", self.text(out, 2))
        self.assertIn("CourtListener key", self.text(out, 2))
        self.assertEqual(self.egress_lines(), [])

    def test_cite_lookup_refuses_a_path_outside_the_matter(self):
        caselaw.turn_on(self.dir, "ty", "tty")
        out = self.mcp(("matter.cite_lookup", {"path": "../outside.md"}), ("matter.search", {"query": " "}))
        self.assertTrue(out[1]["result"].get("isError"))
        self.assertTrue(out[2]["result"].get("isError"))


class TestCli(IsolatedConfig):
    def cli(self, *args):
        env = dict(os.environ, MATTER_KIT_CONFIG=self.cfg)
        env.pop("COURTLISTENER_TOKEN", None)
        return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "courtlistener", *args],
                              capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
                              start_new_session=True, timeout=60)  # no controlling terminal

    def test_on_needs_a_person_at_a_terminal(self):
        r = self.cli("on", self.dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("TTY", r.stderr)
        self.assertFalse(caselaw.is_on(self.dir))

    def test_status_and_off(self):
        self.assertIn("off", self.cli("status", self.dir).stdout)
        caselaw.turn_on(self.dir, "ty", "tty")
        self.assertIn("are on", self.cli("status", self.dir).stdout)
        r = self.cli("off", self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(caselaw.is_on(self.dir))

    def test_not_a_case_folder(self):
        r = self.cli("status", self._tmp.name)
        self.assertIn("not a case folder", r.stderr)


class TestBrowserSwitch(ServerCase):
    def setUp(self):
        super().setUp()
        self.cfg = os.path.join(self.tmp, "config")
        env = mock.patch.dict(os.environ, {"MATTER_KIT_CONFIG": self.cfg})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("COURTLISTENER_TOKEN", None)

    def test_turn_on_and_off(self):
        path = self.case_path("Alpha v. City", "/caselaw")
        status, _, data = self.api("GET", path)
        self.assertEqual(status, 200)
        self.assertFalse(data["caselaw"]["on"])
        status, _, data = self.api("POST", path, {"on": True})
        self.assertEqual(status, 200)
        self.assertTrue(data["caselaw"]["on"])
        for g in consent.load_grants(self.alpha):
            self.assertEqual((g["created_by"], g["activated_via"], g["authorized_callers"]),
                             ("Pat Example", "web-ui", ["*"]))
        self.assertFalse(caselaw.is_on(self.beta))  # one case at a time
        status, _, data = self.api("POST", path, {"on": False})
        self.assertFalse(data["caselaw"]["on"])
        self.assertEqual(consent.load_grants(self.alpha), [])

    def test_body_cannot_name_the_issuer_or_send_junk(self):
        path = self.case_path("Alpha v. City", "/caselaw")
        self.assertEqual(self.api("POST", path, {"on": True, "created_by": "someone"})[0], 400)
        self.assertEqual(self.api("POST", path, {"on": "yes"})[0], 400)
        self.assertEqual(consent.load_grants(self.alpha), [])

    def test_key_saved_never_echoed(self):
        status, _, data = self.api("POST", "/courtlistener-key", {"key": KEY})
        self.assertEqual((status, data), (200, {"key": "settings"}))
        self.assertEqual(credentials.courtlistener_token(), KEY)
        _, _, st = self.api("GET", self.case_path("Alpha v. City", "/caselaw"))
        self.assertNotIn(KEY, json.dumps(st))
        self.assertFalse(any(KEY in open(os.path.join(dp, f), errors="ignore").read()
                             for dp, _, fs in os.walk(self.ws) for f in fs))
        status, _, data = self.api("POST", "/courtlistener-key", {"key": "not a key"})
        self.assertEqual(status, 400)
        self.assertEqual(self.api("POST", "/courtlistener-key", {"forget": True})[2], {"key": None})

    def test_needs_the_launch_token(self):
        status, _, _ = self.api("POST", self.case_path("Alpha v. City", "/caselaw"), {"on": True}, token="wrong")
        self.assertEqual(status, 403)
        self.assertEqual(consent.load_grants(self.alpha), [])


if __name__ == "__main__":
    unittest.main()

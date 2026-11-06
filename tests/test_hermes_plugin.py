"""The Hermes plugin (plugin.yaml, __init__.py, hermes_plugin/): registration, citation guard,
deadline brief, /matter and the matter tools.

The plugin is loaded the way Hermes loads a directory plugin: the repo root as a package, with
`register(ctx)` called on a context object. CourtListener answers are synthetic JSON shaped like
the citation-lookup API; the Corpus answer is a real law.search result for Miss. Code Ann.
§ 89-7-27 (read 2026-11-05). Case names are invented. No test reaches the network.
"""
import datetime as dt
import importlib.util
import io
import json
import shlex
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import cases, citelookup, consent, store  # noqa: E402

PKG = "corpus_open_hermes_test"


def load_plugin():
    if PKG in sys.modules:
        return sys.modules[PKG]
    spec = importlib.util.spec_from_file_location(PKG, os.path.join(KIT, "__init__.py"),
                                                  submodule_search_locations=[KIT])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = mod
    spec.loader.exec_module(mod)
    return mod


load_plugin()
hp = importlib.import_module(PKG + ".hermes_plugin")
guard = importlib.import_module(PKG + ".hermes_plugin.guard")
clc = importlib.import_module(PKG + ".matterkit.adapters.courtlistener_citations")
corpus = importlib.import_module(PKG + ".matterkit.adapters.corpus_search")
pconsent = importlib.import_module(PKG + ".matterkit.consent")

TOKEN = "tok-SECRET-0123456789"
REPLY = ("You can ask the court to remove a tenant who stays after the lease ends. Under Miss. Code "
         "Ann. § 89-7-27 the landlord files a complaint. See Smith v. Jones, 123 F.4th 456 (5th Cir. "
         "2024); Doe v. Roe Corp., 501 U.S. 1001 (1991). The notice rule is Miss. Code Ann. § 89-8-99.")

CORPUS_HIT = (
    "3 of 172 results for \"Miss. Code Ann. § 89-7-27\" in MS (hybrid):\n\n"
    "1. Miss. Code Ann. § 89-7-27 — Proceedings against tenant holding over.\n"
    "   Mississippi · MCA\n"
    "   A tenant or lessee at will or at sufferance, or for part of a year, or for one or more years, of "
    "any houses, lands, or tenements ... shall be removed from the pr\n"
    "   node_id: 37495c6b-7c51-4053-b24b-9a966a1a4492\n\n"
    "2. Miss. Code Ann. § 97-27-29 — Poisons; penalty for violation of certain sections.\n"
    "   Mississippi · MCA\n"
    "   Any person who shall violate the provisions of Sections 97-27-23 ...\n"
    "   node_id: 525e83a7-40bd-4a8b-baa3-1f4fe4cb7008")
CORPUS_MISS = (
    "2 of 40 results for \"Miss. Code Ann. § 89-8-99\" in MS (hybrid):\n\n"
    "1. Miss. Code Ann. § 89-8-13 — Termination of tenancy.\n"
    "   Mississippi · MCA\n   ...\n   node_id: 11111111-2222-3333-4444-555555555555")


def cl_answer(payload):
    """CourtListener's answer: Smith found, Doe not found."""
    def one(cite, status, clusters=()):
        start = payload.index(cite)
        return {"citation": cite, "normalized_citations": [cite], "start_index": start,
                "end_index": start + len(cite), "status": status, "error_message": "",
                "clusters": list(clusters)}
    smith = {"id": 9001, "absolute_url": "/opinion/9001/smith-v-jones/", "case_name": "Smith v. Jones",
             "case_name_full": "Smith v. Jones", "date_filed": "2024-05-01",
             "citations": [{"volume": "123", "reporter": "F.4th", "page": "456", "type": 1}]}
    return [one("123 F.4th 456", 200, [smith]), one("501 U.S. 1001", 404)]


def fake_rpc(self, payload):
    """The Corpus MCP endpoint: initialize, then law.search."""
    if payload["method"] == "initialize":
        return {"jsonrpc": "2.0", "id": 1, "result": {}}
    q = payload["params"]["arguments"]["query"]
    return {"jsonrpc": "2.0", "id": 2,
            "result": {"text": CORPUS_HIT if "89-7-27" in q else CORPUS_MISS, "isError": False}}


def no_network(*a, **k):
    raise AssertionError("a test reached the network")


class Ctx:
    """The parts of Hermes's PluginContext the plugin uses."""

    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        self.tools, self.hooks, self.commands = {}, {}, {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (toolset, schema, handler)

    def register_hook(self, name, fn, **kw):
        self.hooks[name] = fn

    def register_command(self, name, handler, **kw):
        self.commands[name] = handler

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def set_config(self, key, value):
        self.settings[key] = value

    def register_skill(self, name, path, description="", frontmatter=None):
        self.skills = getattr(self, "skills", {})
        self.skills[name] = (str(path), description)


def grant(provider, endpoint, payload_type):
    return {"provider": provider, "endpoints": [endpoint], "payload_types": [payload_type],
            "daily_budget": 10, "created_by": "ty", "kind": "human", "authorized_callers": ["hermes"]}


class PluginCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "matter")
        with redirect_stdout(io.StringIO()):
            cases.init(self.dir)
        patches = [mock.patch("urllib.request.urlopen", no_network),
                   mock.patch.object(corpus.CorpusSearchAdapter, "_rpc", fake_rpc)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.posted = []

        def post(url, data, token):
            self.posted.append((url, data, token))
            return cl_answer(data["text"])
        p = mock.patch.object(clc, "post_form", post)
        p.start()
        self.addCleanup(p.stop)

    def plugin(self, secrets=None, today=dt.date(2026, 11, 9), **settings):
        settings.setdefault("matter_dir", self.dir)
        self.ctx = Ctx(settings)
        secrets = {} if secrets is None else secrets
        return hp.register(self.ctx, secret=lambda name: secrets.get(name), today=lambda: today)

    def grant_all(self):
        consent.save_grants(self.dir, [
            grant(citelookup.PROVIDER, citelookup.ENDPOINT, citelookup.PAYLOAD_TYPE),
            grant("corpus-search", "corpuslaw.us", "query_text")])

    def call(self, name, args=None):
        return json.loads(self.ctx.tools[name][2](args or {}))


class TestRegistration(PluginCase):
    def test_registers_what_the_manifest_declares(self):
        import yaml
        self.plugin()
        with open(os.path.join(KIT, "plugin.yaml"), encoding="utf-8") as f:
            manifest = yaml.safe_load(f)
        self.assertEqual(sorted(self.ctx.tools), sorted(manifest["provides_tools"]))
        self.assertEqual(sorted(self.ctx.hooks), sorted(manifest["provides_hooks"]))
        self.assertEqual(set(self.ctx.commands), {"matter"})
        self.assertEqual({t[0] for t in self.ctx.tools.values()}, {"corpus_open"})
        settings = set(manifest["config_schema"])
        self.assertEqual(settings, set(hp.DEFAULTS) | set(hp.SECRET_ENV))
        for key, env in hp.SECRET_ENV.items():
            self.assertEqual(manifest["config_schema"][key], {**manifest["config_schema"][key],
                                                               "type": "secret", "env": env})

    def test_schemas_are_function_shaped(self):
        self.plugin()
        for name, (_ts, schema, _h) in self.ctx.tools.items():
            self.assertEqual(schema["name"], name)
            self.assertEqual(schema["parameters"]["type"], "object")
            self.assertTrue(schema["description"])

    def test_plugin_code_never_reads_os_environ(self):
        for fn in ("__init__.py", "guard.py", "tools.py"):
            with open(os.path.join(KIT, "hermes_plugin", fn), encoding="utf-8") as f:
                self.assertNotIn("os.environ", f.read(), fn)

    def test_secret_outside_hermes_is_none(self):
        self.assertIsNone(hp._secret("COURTLISTENER_TOKEN"))


class TestCitationGuard(PluginCase):
    def test_no_cites_leaves_the_reply_alone(self):
        p = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN})
        self.assertIsNone(p.on_reply(response_text="Your hearing is Tuesday.", session_id="s"))
        self.assertIsNone(p.on_reply(response_text=""))

    def test_without_grants_nothing_is_sent_and_the_reply_says_how_to_grant(self):
        p = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN})
        out = p.on_reply(response_text=REPLY)
        self.assertTrue(out.startswith(REPLY))
        block = out[len(REPLY):]
        self.assertIn("NOT LOOKED UP", block)
        self.assertIn("--scope cite-lookup --caller hermes", block)
        self.assertIn("--scope corpus-search --caller hermes", block)
        self.assertEqual(self.posted, [])
        self.assertFalse(os.path.exists(consent.egress_path(self.dir)))

    def test_lookup_marks_each_cite(self):
        self.grant_all()
        p = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN})
        block = p.on_reply(response_text=REPLY)[len(REPLY):]
        self.assertIn("Citation check: 2 of 4 to check by hand", block)
        self.assertIn("✓ **FOUND** · [Smith v. Jones, 123 F.4th 456](https://www.courtlistener.com/opinion/9001/", block)
        self.assertIn("**NOT FOUND** · Doe v. Roe Corp., 501 U.S. 1001", block)
        self.assertIn("✓ **IN CORPUS** · [Miss. Code Ann. § 89-7-27]"
                      "(https://corpuslaw.us/code/37495c6b-7c51-4053-b24b-9a966a1a4492)", block)
        self.assertIn("Proceedings against tenant holding over", block)
        self.assertIn("**NOT MATCHED IN CORPUS** · Miss. Code Ann. § 89-8-99", block)
        self.assertIn("not legal advice", block)
        # Problems come first.
        self.assertLess(block.index("NOT FOUND"), block.index("✓ **FOUND**"))
        self.assertNotIn(TOKEN, block)

    def test_only_bare_cites_leave_the_machine(self):
        self.grant_all()
        self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}).on_reply(response_text=REPLY)
        self.assertEqual(len(self.posted), 1)
        sent = self.posted[0][1]["text"]
        self.assertIn("123 F.4th 456", sent)
        for word in ("tenant", "lease", "Smith", "landlord"):
            self.assertNotIn(word, sent)
        self.assertEqual(self.posted[0][2], TOKEN)
        with open(consent.egress_path(self.dir), encoding="utf-8") as f:
            log = f.read()
        self.assertNotIn("tenant", log)
        self.assertNotIn(TOKEN, log)

    def test_no_token_still_checks_statutes(self):
        self.grant_all()
        block = self.plugin().on_reply(response_text=REPLY)
        self.assertIn("COURTLISTENER_TOKEN", block)
        self.assertIn("IN CORPUS", block)
        self.assertEqual(self.posted, [])

    def test_local_mode_sends_nothing(self):
        self.grant_all()
        block = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}, citation_guard="local").on_reply(
            response_text=REPLY)
        self.assertIn("local only", block)
        self.assertEqual(self.posted, [])
        self.assertFalse(os.path.exists(consent.egress_path(self.dir)))

    def test_off_and_already_checked(self):
        self.grant_all()
        self.assertIsNone(self.plugin(citation_guard="off").on_reply(response_text=REPLY))
        p = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN})
        once = p.on_reply(response_text=REPLY)
        self.assertIsNone(p.on_reply(response_text=once))

    def test_no_case_folder_lists_cites_without_lookup(self):
        block = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}, matter_dir="").on_reply(
            response_text=REPLY)
        self.assertIn("no case folder is set", block)
        self.assertEqual(self.posted, [])

    def test_an_agent_grant_is_inert(self):
        g = grant(citelookup.PROVIDER, citelookup.ENDPOINT, citelookup.PAYLOAD_TYPE)
        g["kind"] = "agent"
        consent.save_grants(self.dir, [g])
        block = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}).on_reply(response_text=REPLY)
        self.assertIn("no CourtListener grant", block)
        self.assertEqual(self.posted, [])

    def test_a_failing_source_never_breaks_the_reply(self):
        self.grant_all()

        def boom(*a, **k):
            raise RuntimeError("could not reach CourtListener: timed out")
        with mock.patch.object(clc, "post_form", boom):
            block = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}).on_reply(response_text=REPLY)
        self.assertIn("timed out", block)
        with mock.patch.object(guard, "check_text", side_effect=ValueError("bug")):
            self.assertIsNone(self.plugin().on_reply(response_text=REPLY))

    def test_case_file_ledger_is_shown(self):
        conn = store.connect(self.dir)
        conn.execute("INSERT INTO authorities (id, citation, status, checked_via) VALUES (?,?,?,?)",
                     ("auth_01", "Smith v. Jones, 123 F.4th 456", "source-checked", "Westlaw"))
        conn.commit()
        conn.close()
        block = self.plugin(citation_guard="local").on_reply(response_text=REPLY)
        self.assertIn("(your case file has it as source-checked via Westlaw)", block)


class TestDeadlineBrief(PluginCase):
    def add_deadlines(self):
        dl = importlib.import_module(PKG + ".matterkit.deadlines")
        conn = store.connect(self.dir)
        dl.add(conn, "Answer to complaint", "2026-11-02", "human")
        dl.add(conn, "Discovery responses", "2026-11-16", "human", confidence="hard", verification="verified")
        dl.add(conn, "Pretrial memo", "2027-03-01", "human")
        conn.commit()
        conn.close()

    def test_first_turn_gets_overdue_and_upcoming(self):
        self.add_deadlines()
        p = self.plugin()
        out = p.on_turn(is_first_turn=True, session_id="s", user_message="hi")
        ctx = out["context"]
        self.assertIn("OVERDUE (still open): Answer to complaint: 2026-11-02", ctx)
        self.assertIn("7 days ago", ctx)
        self.assertIn("Discovery responses: 2026-11-16 (hard, verified), in 7 days", ctx)
        self.assertNotIn("Pretrial memo", ctx)
        self.assertIsNone(p.on_turn(is_first_turn=False))

    def test_nothing_due_or_no_folder_adds_nothing(self):
        self.assertIsNone(self.plugin().on_turn(is_first_turn=True))
        self.assertIsNone(self.plugin(matter_dir="").on_turn(is_first_turn=True))
        self.assertIsNone(self.plugin(matter_dir=os.path.join(self.dir, "Evidence")).on_turn(is_first_turn=True))


class TestCommand(PluginCase):
    def test_status_and_deadlines(self):
        TestDeadlineBrief.add_deadlines(self)
        p = self.plugin()
        out = p.command("")
        self.assertIn("Answer to complaint **OVERDUE**", out)
        self.assertIn("Open deadlines:", p.command("deadlines"))

    def test_grant_help_usage_and_missing_folder(self):
        self.assertIn("courtlistener on", self.plugin().command("grant"))
        self.assertIn("Usage: /matter", self.plugin().command("frobnicate"))
        self.assertIn("No case folder is set", self.plugin(matter_dir="").command("status"))

    def test_packet_and_check(self):
        p = self.plugin(citation_guard="local")
        self.assertIn("not legal advice", p.command("packet 30s").lower())
        with open(os.path.join(self.dir, "Drafts_draft.md"), "w", encoding="utf-8") as f:
            f.write(REPLY)
        out = p.command("check Drafts_draft.md")
        self.assertIn("123 F.4th 456", out)
        self.assertIn("Usage", p.command("check"))


class TestTools(PluginCase):
    def test_unset_folder_is_an_error_envelope(self):
        self.plugin(matter_dir="")
        r = self.call("matter_status")
        self.assertFalse(r["ok"])
        self.assertIn("No case folder", r["error"])

    def test_fact_add_and_list_keep_attribution(self):
        self.plugin()
        r = self.call("matter_fact_add", {"text": "The lease ended June 30.", "status": "ALLEGATION",
                                          "directed_by": "Tau"})
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["result"]["unsupported"])
        facts = self.call("matter_fact_list")["result"]
        self.assertEqual(facts[0]["status"], "ALLEGATION")
        conn = store.connect(self.dir)
        self.addCleanup(conn.close)
        row = conn.execute("SELECT created_by FROM assertions").fetchone()
        self.assertEqual(row["created_by"], "agent:hermes; directed-by:Tau")

    def test_paths_stay_inside_the_case_folder(self):
        self.plugin()
        r = self.call("matter_ingest_path", {"path": "../../etc/passwd"})
        self.assertFalse(r["ok"])
        self.assertIn("outside the case folder", r["error"])

    def test_cite_check_tool(self):
        self.plugin(citation_guard="local")
        r = self.call("matter_cite_check", {"text": REPLY})["result"]
        self.assertEqual(r["cites"], 4)
        self.assertNotIn("---", r["report"].splitlines()[0])
        bad = self.call("matter_cite_check", {})
        self.assertFalse(bad["ok"])

    def test_read_tools_answer(self):
        self.plugin()
        for name in ("matter_status", "matter_deadline_list", "matter_trigger_list", "matter_verify",
                     "matter_packet_render"):
            self.assertTrue(self.call(name)["ok"], name)


class TestGuardParsing(unittest.TestCase):
    def test_statute_cites_and_jurisdiction(self):
        cites = guard.statute_cites(REPLY)
        self.assertEqual(cites, ["Miss. Code Ann. § 89-7-27", "Miss. Code Ann. § 89-8-99"])
        self.assertEqual(guard.jurisdiction("42 U.S.C. § 1983"), "US")
        self.assertEqual(guard.jurisdiction("Miss. Code Ann. § 89-7-27"), "MS")
        self.assertIsNone(guard.jurisdiction("Code § 1-2"))

    def test_render_empty(self):
        self.assertEqual(guard.render(guard.Check()), "")


if __name__ == "__main__":
    unittest.main()


class TestFirstRun(PluginCase):
    """A person who just enabled the plugin gets to a working case from inside Hermes."""

    def test_registers_the_matter_kit_skill(self):
        self.plugin()
        path, _ = self.ctx.skills["matter-kit"]
        self.assertTrue(path.endswith(os.path.join("skills", "matter-kit", "SKILL.md")))
        self.assertTrue(os.path.isfile(path))

    def test_a_host_without_skills_still_loads(self):
        class Older(Ctx):
            register_skill = None
        ctx = Older({"matter_dir": self.dir})
        hp.register(ctx, secret=lambda n: None)
        self.assertIn("matter", ctx.commands)

    def test_grant_prints_commands_that_run_from_any_folder(self):
        spaced = os.path.join(self._tmp.name, "my case")
        with redirect_stdout(io.StringIO()):
            cases.init(spaced)
        out = self.plugin(matter_dir=spaced).command("grant")
        kit_cli = shlex.quote(os.path.join(KIT, "matter.py"))
        self.assertIn(f"python3 {kit_cli} courtlistener on {shlex.quote(spaced)}", out)
        self.assertIn(f"python3 {kit_cli} consent activate {shlex.quote(spaced)} --scope corpus-search "
                      "--caller hermes", out)
        self.assertNotIn("python3 matter.py", out)

    def test_init_makes_the_case_and_selects_it(self):
        target = os.path.join(self._tmp.name, "new case")
        p = self.plugin(matter_dir="")
        out = p.command(f"init {target}")
        self.assertTrue(os.path.isdir(os.path.join(target, ".matter")))
        self.assertEqual(self.ctx.settings["matter_dir"], target)
        self.assertIn(target, out)
        self.assertNotIn("No case folder is set", p.command("status"))

    def test_init_expands_home_and_needs_a_folder(self):
        self.assertIn("Usage: /matter init", self.plugin().command("init"))
        with mock.patch.dict(os.environ, {"HOME": self._tmp.name}):
            self.plugin(matter_dir="").command("init ~/c2")
        self.assertTrue(os.path.isdir(os.path.join(self._tmp.name, "c2", ".matter")))

    def test_init_without_set_config_says_where_to_set_it(self):
        class NoWrite(Ctx):
            set_config = None
        ctx = NoWrite({})
        p = hp.register(ctx, secret=lambda n: None)
        target = os.path.join(self._tmp.name, "c3")
        out = p.command(f"init {target}")
        self.assertTrue(os.path.isdir(os.path.join(target, ".matter")))
        self.assertIn("Settings", out)

    def test_demo_builds_a_synthetic_case_with_deadlines(self):
        target = os.path.join(self._tmp.name, "demo")
        p = self.plugin(matter_dir="")
        out = p.command(f"demo {target}")
        self.assertIn("synthetic", out.lower())
        self.assertEqual(self.ctx.settings["matter_dir"], target)
        self.assertTrue(os.path.isfile(os.path.join(target, "Filings", "complaint_synthetic.md")))
        conn = store.connect(target)
        try:
            docs = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
            labels = [r["label"] for r in conn.execute("SELECT label FROM deadlines")]
        finally:
            conn.close()
        self.assertGreaterEqual(docs, 3)
        self.assertEqual(len(labels), 2)
        brief = p.on_turn(is_first_turn=True)["context"]
        self.assertIn("OVERDUE", brief)
        self.assertIn("Due within 14 days", brief)

    def test_demo_never_writes_into_a_folder_that_has_files(self):
        target = os.path.join(self._tmp.name, "busy")
        os.makedirs(target)
        with open(os.path.join(target, "mine.txt"), "w") as f:
            f.write("keep")
        out = self.plugin(matter_dir="").command(f"demo {target}")
        self.assertIn("already has files", out)
        self.assertEqual(os.listdir(target), ["mine.txt"])
        self.assertEqual(self.ctx.settings["matter_dir"], "")

    def test_usage_lists_the_new_commands(self):
        out = self.plugin().command("frobnicate")
        self.assertIn("init <folder>", out)
        self.assertIn("demo", out)

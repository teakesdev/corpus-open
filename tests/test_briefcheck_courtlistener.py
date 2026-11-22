"""Brief check checks case cites on CourtListener (design 2026-11-09 §3, decisions 2026-11-10).

What could go wrong, and so what these tests pin:

- A case cite gets through unmarked: neither checked nor marked NOT VERIFIED with a reason.
- More than a bare "volume reporter page" line leaves: a name, a pin, a parenthetical, a cite hidden
  in a comment, or anything at all when the check is off, there is no key, or brief check runs offline.
- A network failure, a used-up daily limit or a 61st cite crashes brief check or waits a minute.
- Brief check writes the store, or anything besides the saved lookups, the egress log and a
  never-chosen case's default.
- A test reaches courtlistener.com. This module installs a guard, for the whole test run, that
  refuses any lookup of courtlistener.com or corpuslaw.us in this process with an exception no
  handler in the kit catches, so the test that tried fails by name.

Synthetic data only; the transport is passed in or the socket is denied. No request leaves the machine.
Run: python3 -m unittest tests.test_briefcheck_courtlistener -v
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matterkit import briefcheck, caselaw, citelookup, consent, credentials, saved_lookups, store  # noqa: E402
from matterkit.adapters import courtlistener_citations as clc  # noqa: E402
from test_easy_caselaw import KEY, KIT, NETBLOCK, IsolatedConfig  # noqa: E402


# --------------------------------------------------------------------------
# The guard: no test in this run may look up courtlistener.com or corpuslaw.us
# --------------------------------------------------------------------------
class OutsideNetwork(BaseException):
    """Not an Exception, so no `except Exception` or `except OSError` in the kit turns it into a
    NOT VERIFIED and lets the test pass."""


GUARDED = ("courtlistener.com", "corpuslaw.us")
ATTEMPTS: list = []


def _guarded_host(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in GUARDED)


def _guard(fn, host_of):
    def wrapper(*a, **k):
        host = host_of(a, k)
        if _guarded_host(host):
            ATTEMPTS.append(host)
            raise OutsideNetwork(f"a test tried to reach {host}; inject a transport instead")
        return fn(*a, **k)
    wrapper.__wrapped_by_brief_check_guard__ = True
    return wrapper


if not getattr(socket.getaddrinfo, "__wrapped_by_brief_check_guard__", False):
    socket.getaddrinfo = _guard(socket.getaddrinfo, lambda a, k: a[0] if a else k.get("host"))
    socket.gethostbyname = _guard(socket.gethostbyname, lambda a, k: a[0] if a else k.get("hostname"))
    socket.create_connection = _guard(socket.create_connection,
                                      lambda a, k: (a[0] if a else k.get("address") or ("",))[0])


# --------------------------------------------------------------------------
# A local CourtListener
# --------------------------------------------------------------------------
SMITH = {"id": 9001, "absolute_url": "/opinion/9001/smith-v-jones/", "case_name": "Smith v. Jones",
         "date_filed": "2024-05-01"}
DOE = {"id": 9002, "absolute_url": "/opinion/9002/doe-v-roe-corp/", "case_name": "Doe v. Roe Corp.",
       "date_filed": "1991-06-03"}
OTHER = {"id": 9003, "absolute_url": "/opinion/9003/brown-v-green/", "case_name": "Brown v. Green",
         "date_filed": "2024-01-02"}
SKIP = object()  # an answer that leaves the line out

DRAFT = ("Courts dismiss such claims. Smith v. Jones, 123 F.4th 456, 460 (5th Cir. 2024).\n"
         "\n"
         "The rule is settled. Doe v. Roe Corp., 501 U.S. 1001, 1003 (1991).\n"
         "\n"
         "Again, Smith v. Jones, 123 F.4th 456, 461 (5th Cir. 2024).\n")
SMITH_SENT, DOE_SENT = "123 F.4th 456", "501 U.S. 1001"
FOUND_BOTH = {SMITH_SENT: (200, [SMITH]), DOE_SENT: (200, [DOE])}


class FakeCourtListener:
    """CourtListener's citation lookup on this machine: `answers` maps a sent line to (status,
    clusters); a line it does not name is NOT FOUND (404)."""

    def __init__(self, answers=None, fail=None):
        self.answers, self.fail, self.calls = dict(answers or {}), fail, []

    def __call__(self, url, fields, token, **kw):
        self.calls.append({"url": url, "fields": dict(fields), "token": token})
        if self.fail is not None:
            raise self.fail
        out, pos = [], 0
        for line in fields["text"].split("\n"):
            answer = self.answers.get(line, (404, []))
            if answer is not SKIP:
                status, clusters = answer
                out.append({"citation": line, "normalized_citations": [line], "start_index": pos,
                            "end_index": pos + len(line), "status": status, "error_message": "",
                            "clusters": list(clusters)})
            pos += len(line) + 1
        return out


def cl_findings(rep, severity=None):
    return [f for f in rep.findings if f.code.startswith("cite-") and severity in (None, f.severity)]


class Phase(IsolatedConfig):
    """A case nobody has chosen anything for (store only, no consent.json), a settings folder of its
    own and no key unless a test saves one."""

    def setUp(self):
        super().setUp()
        self.draft = os.path.join(self.dir, "Filings", "drafts", "response.md")
        os.makedirs(os.path.dirname(self.draft))

    def check(self, text=DRAFT, **opts):
        with open(self.draft, "w", encoding="utf-8") as f:
            f.write(text)
        opts.setdefault("caller_id", "ty")
        return briefcheck.check(self.dir, self.draft, use_pdftotext=False, cites=briefcheck.CiteCheck(**opts))

    def requests_logged(self):
        return [json.loads(x) for x in self.egress_lines() if "event" not in json.loads(x)]


# --------------------------------------------------------------------------
# What CourtListener says, and what brief check reports
# --------------------------------------------------------------------------
class TestOutcomes(Phase):
    def test_found_has_no_finding_and_the_authorities_table_says_found_with_a_link(self):
        fake = FakeCourtListener(FOUND_BOTH)
        rep = self.check(post=fake, token=KEY)
        self.assertEqual(cl_findings(rep), [])
        text = rep.render()
        self.assertIn("CourtListener: found", text)
        self.assertIn("https://www.courtlistener.com/opinion/9001/smith-v-jones/", text)
        # beside the store's findings, not instead of them
        self.assertIn("authority-not-logged", [f.code for f in rep.findings if f.severity == "BLOCK"])

    def test_name_differs_is_one_block_at_the_first_line(self):
        rep = self.check(post=FakeCourtListener({SMITH_SENT: (200, [OTHER]), DOE_SENT: (200, [DOE])}), token=KEY)
        got = cl_findings(rep)
        self.assertEqual([(f.severity, f.code, f.line) for f in got], [("BLOCK", "cite-name-differs", 1)])
        self.assertIn("CourtListener has a different case at this cite: Brown v. Green", got[0].message)
        self.assertIn(SMITH_SENT, got[0].cite)

    def test_not_found_is_a_block_that_says_check_by_hand(self):
        rep = self.check(post=FakeCourtListener({SMITH_SENT: (200, [SMITH]), DOE_SENT: (404, [])}), token=KEY)
        got = cl_findings(rep)
        self.assertEqual([(f.severity, f.code, f.line) for f in got], [("BLOCK", "cite-not-found", 3)])
        self.assertEqual(got[0].message, citelookup.NOT_FOUND_TEXT)
        self.assertEqual(rep.exit_code, 1)

    def test_unknown_reporter_ambiguous_and_unanswered_are_not_verified(self):
        third = "Courts agree. Acme Widgets, Inc. v. Brown, 77 F.3d 88, 90 (5th Cir. 1996).\n"
        fake = FakeCourtListener({SMITH_SENT: (400, []), DOE_SENT: (300, [DOE, dict(OTHER, case_name="Roe v. Doe")]),
                                  "77 F.3d 88": SKIP})
        rep = self.check(DRAFT + "\n" + third, post=fake, token=KEY)
        got = {f.cite: f for f in cl_findings(rep)}
        self.assertEqual({f.severity for f in got.values()}, {"UNCHECKED"})
        self.assertEqual({f.code for f in got.values()}, {"cite-not-verified"})
        self.assertEqual(len(got), 3)
        messages = " ".join(f.message for f in got.values())
        for why in ("NOT VERIFIED", "does not know this reporter", "pick the one you mean",
                    "did not read this line as a citation"):
            self.assertIn(why, messages)

    def test_each_cite_is_checked_once_however_often_the_draft_repeats_it(self):
        fake = FakeCourtListener({SMITH_SENT: (404, []), DOE_SENT: (200, [DOE])})
        rep = self.check(post=fake, token=KEY)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["fields"]["text"].split("\n"), [SMITH_SENT, DOE_SENT])
        self.assertEqual([f.line for f in cl_findings(rep)], [1])

    def test_a_word_draft_is_checked_through_the_text_brief_check_reads(self):
        from draft_builders import docx, para
        path = os.path.join(self.dir, "Filings", "drafts", "response.docx")
        with open(path, "wb") as f:
            f.write(docx(para("Courts dismiss such claims. Smith v. Jones, 123 F.4th 456, 460 (5th Cir. 2024).")))
        fake = FakeCourtListener({SMITH_SENT: (404, [])})
        rep = briefcheck.check(self.dir, path, use_pdftotext=False,
                               cites=briefcheck.CiteCheck(caller_id="ty", post=fake, token=KEY))
        got = cl_findings(rep)
        self.assertEqual([(f.code, f.where) for f in got], [("cite-not-found", "para. 1")])
        self.assertEqual(fake.calls[0]["fields"]["text"], SMITH_SENT)


# --------------------------------------------------------------------------
# Saved answers, offline, off, no key, network, the daily limit, 60 a request
# --------------------------------------------------------------------------
class TestWhenNothingIsSent(Phase):
    def test_a_saved_answer_is_used_offline_and_nothing_is_sent(self):
        self.check(post=FakeCourtListener(FOUND_BOTH), token=KEY)
        sent_before = len(self.requests_logged())
        again = FakeCourtListener()
        rep = self.check(post=again, token=KEY, offline=True)
        self.assertEqual(again.calls, [])
        self.assertEqual(len(self.requests_logged()), sent_before)
        self.assertEqual(cl_findings(rep), [])
        text = rep.render()
        self.assertIn("CourtListener: found", text)
        self.assertIn("2 answered from this case's saved lookups", text)
        self.assertIn("nothing sent", text)

    def test_a_saved_answer_is_used_with_no_key_and_with_the_check_off(self):
        self.check(post=FakeCourtListener(FOUND_BOTH), token=KEY)
        caselaw.turn_off(self.dir)
        rep = self.check(post=FakeCourtListener(), token="")
        self.assertEqual(cl_findings(rep), [])

    def test_offline_sends_nothing_writes_nothing_and_marks_every_cite(self):
        credentials.save_courtlistener_token(KEY)
        fake = FakeCourtListener(FOUND_BOTH)
        before = sorted(os.listdir(os.path.join(self.dir, ".matter")))
        rep = self.check(post=fake, offline=True)
        self.assertEqual(fake.calls, [])
        self.assertEqual(sorted(os.listdir(os.path.join(self.dir, ".matter"))), before)  # no default, no log
        got = cl_findings(rep)
        self.assertEqual([(f.code, f.line) for f in got], [("cite-not-verified", 1), ("cite-not-verified", 3)])
        self.assertTrue(all("offline" in f.message for f in got), [f.message for f in got])
        self.assertIn("nothing sent", rep.render())

    def test_turned_off_by_a_person_sends_nothing_and_says_how_to_turn_it_on(self):
        caselaw.turn_off(self.dir)
        fake = FakeCourtListener(FOUND_BOTH)
        rep = self.check(post=fake, token=KEY)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.requests_logged(), [])
        got = cl_findings(rep)
        self.assertEqual(len(got), 2)
        for f in got:
            self.assertEqual((f.severity, f.code), ("UNCHECKED", "cite-not-verified"))
            self.assertIn("turned off for this case by a person", f.message)
            self.assertIn("courtlistener on", f.message)

    def test_no_key_sends_nothing_and_says_where_to_get_one(self):
        fake = FakeCourtListener(FOUND_BOTH)
        for token in (None, ""):  # none saved; a host (Hermes) that has none
            with self.subTest(token=token):
                rep = self.check(post=fake, token=token)
                self.assertEqual(fake.calls, [])
                got = cl_findings(rep)
                self.assertEqual(len(got), 2)
                self.assertTrue(all("no CourtListener key" in f.message and caselaw.KEY_URL in f.message
                                    for f in got), [f.message for f in got])
        self.assertEqual(self.requests_logged(), [])

    def test_a_host_can_say_where_its_key_goes(self):
        rep = self.check(post=FakeCourtListener(), token="", key_hint="add it in the plugin's settings")
        self.assertTrue(all("add it in the plugin's settings" in f.message for f in cl_findings(rep)))

    def test_a_network_error_is_not_verified_never_a_crash(self):
        fake = FakeCourtListener(fail=RuntimeError("could not reach CourtListener: timed out"))
        rep = self.check(post=fake, token=KEY)
        self.assertEqual(len(fake.calls), 1)
        got = cl_findings(rep)
        self.assertEqual({(f.severity, f.code) for f in got}, {("UNCHECKED", "cite-not-verified")})
        self.assertTrue(all("could not reach CourtListener" in f.message for f in got))
        self.assertEqual(len(self.requests_logged()), 1)  # the attempt is logged before it is made
        self.assertIn("no answer", rep.render())

    def test_the_real_transport_turns_a_dropped_connection_into_not_verified(self):
        class Opener:
            def open(self, req, timeout=None):
                Opener.timeout = timeout
                raise TimeoutError("the read timed out")
        with mock.patch.object(clc, "opener", Opener):
            rep = self.check(token=KEY)
        self.assertLessEqual(Opener.timeout, briefcheck.COURTLISTENER_TIMEOUT)
        got = cl_findings(rep)
        self.assertEqual(len(got), 2)
        self.assertTrue(all("could not reach CourtListener" in f.message for f in got), [f.message for f in got])

    def test_a_used_up_daily_limit_sends_nothing_more(self):
        consent.write_human_grant(self.dir, citelookup.PROVIDER, [citelookup.ENDPOINT], [citelookup.PAYLOAD_TYPE],
                                  "a person", "test", daily_budget=1, authorized_callers=[consent.ANY_CALLER])
        self.check("Smith v. Jones, 123 F.4th 456 (5th Cir. 2024).\n", post=FakeCourtListener(FOUND_BOTH), token=KEY)
        fake = FakeCourtListener(FOUND_BOTH)
        rep = self.check("Doe v. Roe Corp., 501 U.S. 1001 (1991).\n", post=fake, token=KEY)
        self.assertEqual(fake.calls, [])
        got = cl_findings(rep)
        self.assertEqual(len(got), 1)
        self.assertIn("daily limit", got[0].message)

    def test_more_than_sixty_cites_sends_one_request_and_says_how_to_check_the_rest(self):
        text = "".join(f"Case {n} v. Party {n}, {n} F.3d {100 + n} (5th Cir. 1999).\n" for n in range(1, 62))
        fake = FakeCourtListener()
        with mock.patch("time.sleep", side_effect=AssertionError("brief check never waits")):
            rep = self.check(text, post=fake, token=KEY)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(len(fake.calls[0]["fields"]["text"].split("\n")), citelookup.MAX_PER_REQUEST)
        late = [f for f in cl_findings(rep) if f.code == "cite-not-verified"]
        self.assertEqual([f.line for f in late], [61])
        self.assertIn("--max-requests 2", late[0].message)
        self.assertEqual(len([f for f in cl_findings(rep) if f.code == "cite-not-found"]), 60)


# --------------------------------------------------------------------------
# What leaves, and what is written
# --------------------------------------------------------------------------
class TestWhatLeaves(Phase):
    def test_only_bare_cites_leave_never_a_name_pin_parenthetical_or_hidden_cite(self):
        text = ("Courts dismiss “such claims” here. Smith v. Jones, 123 F.4th 456, 460 (5th Cir. 2024) "
                "(holding that a late notice bars the claim). {fact:a_notice}\n"
                "<!-- Hidden v. Note, 999 U.S. 1 (2000) -->\n"
                "See also Doe v. Roe Corp., 501 U.S. 1001, 1003 (1991); Acme Widgets, Inc. v. Brown, "
                "2021 WL 1234567, at *2 (S.D. Miss. Mar. 3, 2021).\n")
        fake = FakeCourtListener(FOUND_BOTH)
        self.check(text, post=fake, token=KEY)
        self.assertEqual(len(fake.calls), 1)
        sent = fake.calls[0]["fields"]
        self.assertEqual(sent, {"text": "123 F.4th 456\n501 U.S. 1001\n2021 WL 1234567"})
        self.assertEqual(fake.calls[0]["url"], citelookup.URL)
        log = "\n".join(self.egress_lines())
        for word in ("Smith", "Hidden", "999", "123 F.4th", "notice", KEY):
            self.assertNotIn(word, log)

    def test_the_header_says_what_was_sent_and_how_many_requests(self):
        rep = self.check(post=FakeCourtListener(FOUND_BOTH), token=KEY)
        line = next(x for x in rep.render().splitlines() if x.startswith("- CourtListener:"))
        self.assertIn("2 case cites", line)
        self.assertIn("sent 2", line)
        self.assertIn("1 request", line)
        self.assertIn(citelookup.URL, line)
        self.assertIn("no names, pins or other draft text", line)

    def test_the_store_is_never_written_and_only_three_files_may_change(self):
        db = store.db_path(self.dir)
        meta = os.path.join(self.dir, ".matter")

        def snapshot():
            out = {}
            for d, _, files in os.walk(meta):
                for fn in files:
                    with open(os.path.join(d, fn), "rb") as f:
                        out[os.path.relpath(os.path.join(d, fn), meta)] = hashlib.sha256(f.read()).hexdigest()
            return out
        before = snapshot()
        self.check(post=FakeCourtListener(FOUND_BOTH), token=KEY)
        after = snapshot()
        changed = {k for k in after if before.get(k) != after[k]}
        self.assertEqual(changed, {"consent.json", "egress.jsonl", os.path.join("caselaw", "cites.jsonl")})
        self.assertEqual(before[os.path.relpath(db, meta)], after[os.path.relpath(db, meta)])
        self.assertEqual(saved_lookups.find_cites(self.dir, [SMITH_SENT, DOE_SENT]).keys(), {SMITH_SENT, DOE_SENT})

    def test_a_never_chosen_case_gets_its_default_once(self):
        self.check(post=FakeCourtListener(FOUND_BOTH), token=KEY)
        self.check("Brown v. Green, 77 F.3d 88 (5th Cir. 1996).\n", post=FakeCourtListener(), token=KEY)
        kinds = [(g["provider"], g["kind"]) for g in consent.load_grants(self.dir)]
        self.assertEqual(kinds, [(citelookup.PROVIDER, consent.DEFAULT)])
        events = [x for x in self.egress_lines() if "case-default-written" in x]
        self.assertEqual(len(events), 1)

    def test_a_library_call_without_options_has_no_courtlistener_phase(self):
        credentials.save_courtlistener_token(KEY)
        with open(self.draft, "w", encoding="utf-8") as f:
            f.write(DRAFT)
        with mock.patch.object(clc, "post_form", side_effect=AssertionError("egress")):
            rep = briefcheck.check(self.dir, self.draft, use_pdftotext=False)
        self.assertEqual(cl_findings(rep), [])
        self.assertNotIn("- CourtListener:", rep.render())
        self.assertEqual(self.egress_lines(), [])

    def test_a_draft_without_case_cites_reads_no_key_and_sends_nothing(self):
        with mock.patch.object(credentials, "courtlistener_token", side_effect=AssertionError("key read")):
            rep = self.check("The complaint was filed [Doc. 1].\n", post=FakeCourtListener(), offline=False)
        self.assertEqual(cl_findings(rep), [])
        self.assertIn("no full case cites", rep.render())
        self.assertFalse(os.path.exists(consent.egress_path(self.dir)))

    def test_limits_and_docstring_say_what_brief_check_may_write(self):
        for text in (briefcheck.LIMITS, briefcheck.__doc__):
            self.assertIn("CourtListener", text)
            self.assertIn(".matter/caselaw/cites.jsonl", text)
            self.assertIn(".matter/egress.jsonl", text)


# --------------------------------------------------------------------------
# The entry points: CLI, MCP, browser UI
# --------------------------------------------------------------------------
DENY_SITE = ("import socket\n"
             "def _deny(*a, **k):\n"
             "    raise OSError('network denied by the brief check test harness')\n"
             "socket.getaddrinfo = socket.create_connection = _deny\n"
             "socket.socket.connect = socket.socket.connect_ex = _deny\n")


class TestEntryPoints(Phase):
    def setUp(self):
        super().setUp()
        self.site = os.path.join(self._tmp.name, "netdeny")
        os.makedirs(self.site)
        with open(os.path.join(self.site, "sitecustomize.py"), "w") as f:
            f.write(DENY_SITE)
        with open(self.draft, "w", encoding="utf-8") as f:
            f.write(DRAFT)

    def cli(self, *args, site=None):
        env = dict(os.environ, PATH="", PYTHONPATH=site or self.site)
        probe = subprocess.run([sys.executable, "-c", "import socket; socket.getaddrinfo('example.org', 443)"],
                               capture_output=True, text=True, env=env, timeout=30)
        self.assertIn("network denied", probe.stderr, "the harness did not load; refusing to run the CLI")
        return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "brief", "check", self.dir,
                               self.draft, *args], capture_output=True, text=True, env=env, timeout=120)

    def test_cli_offline_sends_nothing(self):
        credentials.save_courtlistener_token(KEY)
        p = self.cli("--offline")
        self.assertNotIn("Traceback", p.stderr)
        self.assertEqual(p.stdout.count("`cite-not-verified`"), 2, p.stdout)
        self.assertIn("offline", p.stdout)
        self.assertFalse(os.path.exists(consent.egress_path(self.dir)))

    def test_cli_without_a_key_says_how_to_add_one(self):
        p = self.cli()
        self.assertEqual(p.stdout.count("`cite-not-verified`"), 2, p.stdout + p.stderr)
        self.assertIn("no CourtListener key", p.stdout)
        self.assertEqual(self.requests_logged(), [])  # the case's default is noted; no request

    def test_cli_sends_through_the_real_path_up_to_the_socket(self):
        credentials.save_courtlistener_token(KEY)
        p = self.cli()
        self.assertNotIn("Traceback", p.stderr)
        self.assertEqual(p.returncode, 1, p.stderr)  # the store's authority-not-logged
        self.assertEqual(p.stdout.count("`cite-not-verified`"), 2, p.stdout)
        self.assertIn("could not reach CourtListener", p.stdout)
        self.assertEqual(len(self.requests_logged()), 1)
        for text in (p.stdout, p.stderr, "\n".join(self.egress_lines())):
            self.assertNotIn(KEY, text)

    def test_cli_help_and_usage_name_offline(self):
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "brief", "--help"],
                           capture_output=True, text=True, timeout=60)
        self.assertIn("--offline", p.stdout)
        self.assertIn("--offline", open(os.path.join(KIT, "matter.py"), encoding="utf-8").read().split("def ")[0])

    def mcp(self, arguments):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "matter.brief_check", "arguments": arguments}}
        env = dict(os.environ, MATTER_DIR=self.dir, PYTHONPATH=NETBLOCK)
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input=json.dumps(req) + "\n",
                           capture_output=True, text=True, env=env, timeout=120)
        return json.loads(p.stdout.strip().splitlines()[-1])["result"]["content"][0]["text"]

    def test_mcp_offline_argument_sends_nothing(self):
        credentials.save_courtlistener_token(KEY)
        rel = os.path.relpath(self.draft, self.dir)
        text = self.mcp({"path": rel, "pdftotext": False, "offline": True})
        self.assertEqual(text.count("`cite-not-verified`"), 2, text[:400])
        self.assertIn("offline", text)
        self.assertFalse(os.path.exists(consent.egress_path(self.dir)))
        credentials.forget_courtlistener_token()
        text = self.mcp({"path": rel, "pdftotext": False})  # sent by default, but there is no key
        self.assertEqual(text.count("`cite-not-verified`"), 2, text[:400])
        self.assertIn("no CourtListener key", text)
        self.assertEqual(self.requests_logged(), [])

    def test_mcp_schema_and_description(self):
        src = open(os.path.join(KIT, "matter-mcp.py"), encoding="utf-8").read()
        self.assertIn('"offline": {"type": "boolean"', src)
        tool = next(line for line in src.splitlines() if '"name": "matter.brief_check"' in line)
        for words in ("CourtListener", "bare", "offline", "never writes the store"):
            self.assertIn(words, tool)

    def test_browser_ui_marks_the_cites(self):
        from webui_helpers import call, direct_app
        from matterkit import cases
        ws = os.path.join(self._tmp.name, "ws")
        case = os.path.join(ws, "Alpha v. City")
        cases.init(case)
        os.makedirs(os.path.join(case, "Filings", "drafts"), exist_ok=True)
        with open(os.path.join(case, "Filings", "drafts", "response.md"), "w", encoding="utf-8") as f:
            f.write(DRAFT)
        app = direct_app(ws)
        cid = {c["rel"]: c["id"] for c in cases.discover(ws)}["Alpha v. City"]
        with mock.patch.object(clc, "post_form", side_effect=AssertionError("egress")):
            status, data = call(app, "POST", f"/cases/{cid}/brief-check", {"path": "Filings/drafts/response.md"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["report"].count("`cite-not-verified`"), 2)
        self.assertIn("no CourtListener key", data["report"])


class TestTheGuard(unittest.TestCase):
    def test_no_test_reached_courtlistener_and_the_guard_refuses(self):
        self.assertEqual(ATTEMPTS, [], "a test tried to reach CourtListener or Corpus (see its error)")
        for host in ("www.courtlistener.com", "corpuslaw.us"):
            with self.assertRaises(OutsideNetwork):
                socket.getaddrinfo(host, 443)
            with self.assertRaises(OutsideNetwork):
                socket.create_connection((host, 443), timeout=1)
        del ATTEMPTS[:]


if __name__ == "__main__":
    unittest.main()

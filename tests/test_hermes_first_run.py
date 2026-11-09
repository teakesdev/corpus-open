"""The Hermes plugin's first five minutes, for someone who has never used the kit.

What could go wrong, and so what these tests pin:

- `/matter` before any case exists is a dead end (names a setting and a CLI command, not the two
  slash commands that start a case), and the agent is never told the case tools won't work.
- A reply's "not looked up" note leaks the grant's internal names or is cut mid-word, and a bare
  "§ 89-7-27" after the full cite is listed as a second cite.
- A printed terminal command starts with `python3`, which on a stock Mac is 3.9 and crashes the kit.
- The demo case shows nothing: no facts, no cites, no quotations, so no check has anything to find;
  or its draft is registered as a source, so the agent can never edit it.
- `/matter check` lists cites only, though the README says it checks quotations too.
"""
import os
import shlex
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hermes_plugin import (KIT, REPLY, TOKEN, PluginCase, citelookup, consent, grant, guard,  # noqa: E402
                                hp, store)

DRAFT = "Drafts/response_draft_synthetic.md"
DECLARATION = "Filings/12-declaration_synthetic.md"
INTERNALS = ("no valid grant", "citation_strings", "query_text", "api/rest", "authorized_callers",
             "courtlistener-citation-lookup", "corpus-search/")


class TestNoCaseYet(PluginCase):
    def test_matter_offers_the_two_ways_to_start(self):
        for sub in ("", "status"):
            out = self.plugin(matter_dir="").command(sub)
            self.assertIn("No case folder is set up yet", out)
            self.assertIn("/matter demo", out)
            self.assertIn("/matter init ~/cases/my-case", out)
            self.assertNotIn("matter_dir", out)
            self.assertNotIn("matter.py", out)

    def test_a_folder_that_is_not_a_case_is_named(self):
        plain = os.path.join(self._tmp.name, "just a folder")
        os.makedirs(plain)
        out = self.plugin(matter_dir=plain).command("")
        self.assertIn(plain, out)
        self.assertIn(f"/matter init {plain}", out)
        self.assertIn("/matter demo", out)
        self.assertNotIn("matter.py", out)

    def test_other_commands_and_tools_point_to_setup(self):
        p = self.plugin(matter_dir="")
        for sub in ("deadlines", "watch", "calendar", "packet 30s"):
            out = p.command(sub)
            self.assertIn("/matter demo", out, sub)
            self.assertIn("/matter init", out, sub)
        err = self.call("matter_status")["error"]
        self.assertIn("/matter demo", err)
        self.assertNotIn("matter.py", err)

    def test_a_case_that_exists_gets_no_welcome(self):
        out = self.plugin().command("")
        self.assertIn("# Matter status", out)
        self.assertNotIn("/matter demo", out)

    def test_first_turn_tells_the_agent_once(self):
        p = self.plugin(matter_dir="")
        ctx = p.on_turn(is_first_turn=True)["context"]
        self.assertIn("/matter demo", ctx)
        self.assertIn("/matter init", ctx)
        self.assertIn("Otherwise do not mention", ctx)
        self.assertIsNone(p.on_turn(is_first_turn=False))
        # A case with nothing due still adds nothing.
        self.assertIsNone(self.plugin().on_turn(is_first_turn=True))

    def test_init_says_what_to_do_next_in_plain_words(self):
        target = os.path.join(self._tmp.name, "mine")
        out = self.plugin(matter_dir="").command(f"init {target}")
        self.assertIn(os.path.join(target, "Filings"), out)
        self.assertIn(os.path.join(target, "Evidence"), out)
        self.assertIn("register my files", out)
        self.assertNotIn("matter_ingest_path", out)


class TestRegisterMyFiles(PluginCase):
    """/matter init tells the person to say "register my files"; the agent then calls matter_ingest_path."""

    def test_the_case_folder_itself_is_refused_with_what_to_name_instead(self):
        self.plugin()
        for path in (".", self.dir, self.dir + os.sep):
            r = self.call("matter_ingest_path", {"path": path})
            self.assertFalse(r["ok"], path)
            self.assertIn("Filings", r["error"])
            self.assertNotIn("matter-relative", r["error"])
        with open(os.path.join(self.dir, "Evidence", "lease.txt"), "w", encoding="utf-8") as f:
            f.write("lease, synthetic\n")
        self.assertEqual(self.call("matter_ingest_path", {"path": "Evidence"})["result"]["added"], 1)

    def test_the_tool_says_what_to_register_and_to_leave_drafts_alone(self):
        self.plugin()
        said = self.ctx.tools["matter_ingest_path"][1]["description"]
        self.assertIn("'Filings'", said)
        self.assertIn("drafts unregistered", said)


class TestPlainReasons(PluginCase):
    def notes(self, block):
        return [ln for ln in block.splitlines() if ln.startswith("_") and "not looked up" in ln]

    def test_no_grant_reads_as_plain_words(self):
        block = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}).on_reply(response_text=REPLY)[len(REPLY):]
        notes = self.notes(block)
        self.assertEqual(len(notes), 2, block)
        for n in notes:
            self.assertIn("/matter grant", n)
            self.assertIn("not turned on for this case", n)
            self.assertTrue(n.endswith("._"), n)  # a whole sentence, never cut mid-word
            self.assertLess(len(n), 200, n)
        for word in INTERNALS + ("python3", "matter.py"):
            self.assertNotIn(word, block)
        self.assertEqual(self.posted, [])

    def test_a_used_up_daily_limit_says_so(self):
        case = grant(citelookup.PROVIDER, citelookup.ENDPOINT, citelookup.PAYLOAD_TYPE)
        law = grant("corpus-search", "corpuslaw.us", "query_text")
        case["daily_budget"] = law["daily_budget"] = 0
        consent.save_grants(self.dir, [case, law])
        block = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN}).on_reply(response_text=REPLY)[len(REPLY):]
        notes = self.notes(block)
        self.assertEqual(len(notes), 2, block)
        for n in notes:
            self.assertIn("today's limit", n)
            self.assertNotIn("/matter grant", n)
        for word in INTERNALS:
            self.assertNotIn(word, block)
        self.assertEqual(self.posted, [])

    def test_no_key_points_to_the_free_key_without_a_terminal_command(self):
        self.grant_all()
        block = self.plugin().on_reply(response_text=REPLY)[len(REPLY):]
        note = next(ln for ln in block.splitlines() if "CourtListener key" in ln)
        self.assertIn("https://www.courtlistener.com/profile/api/", note)
        self.assertIn("/matter grant", note)
        self.assertNotIn("python3", note)

    def test_a_long_error_is_cut_at_a_word(self):
        cut = guard._short(RuntimeError("word " * 100))
        self.assertLessEqual(len(cut), 241)
        self.assertTrue(cut.endswith("word…"), cut[-12:])
        self.assertEqual(guard._short(RuntimeError("short")), "short")


class TestOneRowPerStatute(PluginCase):
    TEXT = ("Miss. Code Ann. § 89-7-27 covers a tenant who stays on. Under § 89-7-27 the landlord files "
            "a complaint, and § 1983 is a different law.")

    def test_a_bare_section_after_its_full_cite_is_not_a_second_cite(self):
        self.assertEqual(guard.statute_cites(self.TEXT), ["Miss. Code Ann. § 89-7-27", "§ 1983"])
        # Either order, and a bare section that only shares digits is a different section.
        self.assertEqual(guard.statute_cites("Under § 89-7-27 and § 9-7-27. See Miss. Code Ann. § 89-7-27."),
                         ["§ 9-7-27", "Miss. Code Ann. § 89-7-27"])

    def test_the_reply_lists_and_looks_up_the_statute_once(self):
        self.grant_all()
        block = self.plugin().on_reply(response_text=self.TEXT)[len(self.TEXT):]
        rows = [ln for ln in block.splitlines() if ln.startswith("- ")]
        self.assertEqual(len([r for r in rows if "89-7-27" in r]), 1, block)
        self.assertEqual(len([r for r in rows if "§ 1983" in r]), 1, block)
        self.assertIn("IN CORPUS", block)


class TestPrintedCommands(PluginCase):
    def test_commands_use_the_python_running_the_plugin(self):
        # A bare `python3` can be macOS's 3.9, which the kit does not run on.
        kit_cli = shlex.quote(os.path.join(KIT, "matter.py"))
        with mock.patch.object(sys, "executable", "/opt/hermes agent/venv/bin/python3"):
            out = self.plugin().command("grant")
        py = shlex.quote("/opt/hermes agent/venv/bin/python3")
        self.assertIn(f"{py} {kit_cli} courtlistener on {shlex.quote(self.dir)}", out)
        self.assertIn(f"{py} {kit_cli} consent activate {shlex.quote(self.dir)}", out)
        self.assertNotIn("  python3 ", out)
        self.assertIn("https://www.courtlistener.com/profile/api/", out)

    def test_no_known_interpreter_falls_back_to_python3(self):
        with mock.patch.object(sys, "executable", ""):
            self.assertTrue(guard.grant_command("case", self.dir, "hermes").startswith("python3 "))


class TestDeadlineWords(PluginCase):
    def add(self, **kw):
        from matterkit import deadlines as dl
        conn = store.connect(self.dir)
        dl.add(conn, "Answer to complaint", "2026-11-20", "human", **kw)
        conn.commit()
        conn.close()

    def test_unconfirmed_dates_are_explained(self):
        self.add()
        p = self.plugin()
        for sub in ("deadlines", ""):
            self.assertIn("are not confirmed. Check them against the rules and the court's orders.",
                          p.command(sub), sub)

    def test_confirmed_dates_carry_no_warning(self):
        self.add(confidence="hard", verification="verified-official")
        self.assertNotIn("not confirmed", self.plugin().command("deadlines"))


class TestStoryDemo(PluginCase):
    def demo(self, **settings):
        self.target = os.path.join(self._tmp.name, "demo")
        p = self.plugin(matter_dir="", **settings)
        self.out = p.command(f"demo {self.target}")
        return p

    def test_message_says_what_is_planted_and_what_to_try(self):
        self.demo()
        self.assertIn("Nothing in it is real", self.out)
        self.assertIn(f"/matter check {DRAFT}", self.out)
        self.assertIn("does not exist", self.out)
        self.assertIn("wrong page", self.out)
        self.assertIn("/matter grant", self.out)

    def test_facts_are_labeled_and_one_has_no_source(self):
        p = self.demo()
        facts = self.call("matter_fact_list")["result"]
        self.assertEqual(len(facts), 4)
        self.assertEqual([f["status"] for f in facts if not f["supported"]], ["HYPOTHESIS"])
        self.assertEqual(sorted({f["status"] for f in facts}), ["ALLEGATION", "DOCUMENTED FACT", "HYPOTHESIS"])
        self.assertTrue(all(f["sources"] for f in facts if f["supported"]))
        self.assertIn("UNSUPPORTED", p.command("packet 3min"))

    def test_the_draft_is_not_a_registered_source(self):
        p = self.demo()
        draft = os.path.join(self.target, DRAFT)
        self.assertTrue(os.path.isfile(draft))
        conn = store.connect(self.target)
        try:
            paths = [r["rel_path"] for r in conn.execute("SELECT rel_path FROM documents")]
        finally:
            conn.close()
        self.assertEqual([x for x in paths if x.startswith("Drafts")], [])
        self.assertIn(DECLARATION, paths)
        # So the agent may edit the draft, but not the declaration.
        self.assertIsNone(p.on_tool_call("write_file", {"path": draft, "content": "A new start.\n"}))
        hit = p.on_tool_call("write_file", {"path": os.path.join(self.target, DECLARATION), "content": "x"})
        self.assertEqual(hit["action"], "block")
        verify = self.call("matter_verify")["result"]
        self.assertEqual((verify["changed"], verify["missing"]), (0, 0))

    def test_check_finds_every_planted_mistake(self):
        p = self.demo(citation_guard="local")
        out = p.command(f"check {DRAFT}")
        for cite in ("Ashcroft v. Iqbal, 556 U.S. 662", "812 F.4th 1099", "42 U.S.C. § 2000e-3(a)"):
            self.assertIn(cite, out)
        self.assertIn("Quotations and page cites: 2 of 3 to fix", out)
        lines = [ln for ln in out.splitlines() if ln.startswith("- ") and ("“" in ln)]
        self.assertEqual([ln.split(":")[0] for ln in lines],
                         ["- ⚠ **not found**", "- ⚠ **wrong page**", "- ✓ **exact**"])
        self.assertIn("is really at p. 2", out)
        self.assertIn("not that they prove the point", out)

    def test_the_quote_guard_works_on_the_demo_draft(self):
        p = self.demo()
        draft = os.path.join(self.target, DRAFT)
        bad = 'She swore that "the moon is made of cheese." Doc. 12 at 1.\n'
        self.assertEqual(p.on_tool_call("write_file", {"path": draft, "content": bad})["action"], "block")
        good = 'She swore: "I keep the personnel files for the warehouse team." Doc. 12 at 1.\n'
        self.assertIsNone(p.on_tool_call("write_file", {"path": draft, "content": good}))

    def test_the_kit_own_demo_folder_is_left_alone(self):
        self.demo()
        self.assertFalse(os.path.exists(os.path.join(KIT, "demo-matter", ".matter")))
        self.assertTrue(os.path.isfile(os.path.join(KIT, "demo-matter", DRAFT)))


class TestCheckCommand(PluginCase):
    def write(self, rel, text):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_cites_only_has_no_quotation_section(self):
        self.write("Drafts/a.md", REPLY)
        out = self.plugin(citation_guard="local").command("check Drafts/a.md")
        self.assertIn("123 F.4th 456", out)
        self.assertNotIn("Quotations and page cites", out)

    def test_nothing_to_check(self):
        self.write("Drafts/b.md", "The hearing is on Tuesday.\n")
        self.assertEqual(self.plugin().command("check Drafts/b.md"), "No citations or quotations found.")

    def test_a_quotation_with_no_record_cite_is_listed_as_cant_check(self):
        self.write("Drafts/c.md", 'The manager said "we never saw the warrant" at the meeting.\n')
        out = self.plugin(citation_guard="local").command("check Drafts/c.md")
        self.assertIn("**Quotations and page cites: 1**", out)
        self.assertIn("- · **can't check**", out)

    def test_a_failed_quotation_check_keeps_the_cite_report(self):
        self.write("Drafts/d.md", REPLY)
        with mock.patch.object(hp._quote_guard.quotecheck, "check_text", side_effect=RuntimeError("boom")):
            out = self.plugin(citation_guard="local").command("check Drafts/d.md")
        self.assertIn("123 F.4th 456", out)
        self.assertIn("Quotations could not be checked: boom", out)


if __name__ == "__main__":
    unittest.main()

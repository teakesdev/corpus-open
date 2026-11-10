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
import io
import shlex
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hermes_plugin import (KIT, REPLY, TOKEN, PluginCase, cases, citelookup, consent, grant,  # noqa: E402
                                guard, hp, store)

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


    def test_a_path_that_is_not_there_is_named_not_reported_as_done(self):
        # DeepSeek r1 P2-2: a missing folder came back ok with 0 added, so the agent reported success.
        self.plugin()
        r = self.call("matter_ingest_path", {"path": "CasePapers"})
        self.assertFalse(r["ok"])
        self.assertIn("CasePapers", r["error"])
        self.assertIn("Filings", r["error"])
        self.assertNotIn("Errno", r["error"])


class TestNoCaseInsideACase(PluginCase):
    """DeepSeek r1 P3-1: an empty folder inside a real case was taken as a new case, which made a
    nested case and switched the plugin away from the real one."""

    def test_demo_inside_a_case_is_refused_and_the_case_stays_selected(self):
        p = self.plugin()
        inner = os.path.join(self.dir, "Filings", "practice")
        os.makedirs(inner)
        out = p.command(f"demo {inner}")
        self.assertIn(f"inside the case at {self.dir}", out)
        self.assertFalse(os.path.exists(os.path.join(inner, ".matter")))
        self.assertEqual(os.listdir(inner), [])
        self.assertEqual(p.settings()["matter_dir"], self.dir)

    def test_init_inside_a_case_is_refused(self):
        p = self.plugin()
        inner = os.path.join(self.dir, "Evidence", "new")
        out = p.command(f"init {inner}")
        self.assertIn(f"inside the case at {self.dir}", out)
        self.assertFalse(os.path.exists(inner))
        self.assertEqual(p.settings()["matter_dir"], self.dir)

    def test_init_on_the_case_itself_still_works(self):
        self.assertIn("Case folder ready", self.plugin().command(f"init {self.dir}"))


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


    def test_a_path_that_is_not_a_file_gets_words_not_errno(self):
        # DeepSeek r1 P3-2.
        p = self.plugin()
        out = p.command("check Drafts/nope.md")
        self.assertIn("There is no Drafts/nope.md in the case folder", out)
        self.assertNotIn("Errno", out)
        out = p.command("check Filings")
        self.assertIn("Filings is a folder", out)
        self.assertNotIn("Errno", out)
        self.assertNotIn("Errno", self.call("matter_cite_check", {"path": "Drafts/nope.md"})["error"])


class TestFoundRunningInsideHermes(PluginCase):
    """Faults the first live run inside Hermes (v0.23.0, 2026-11-09) turned up."""

    def test_a_word_of_the_sentence_is_not_shown_as_part_of_the_case_name(self):
        text = "Any pleading must meet the plausibility standard of Ashcroft v. Iqbal, 556 U.S. 662 (2009)."
        block = self.plugin().on_reply(response_text=text)[len(text):]
        self.assertIn("· Ashcroft v. Iqbal, 556 U.S. 662", block)
        self.assertNotIn("of Ashcroft", block)

    def test_grant_before_any_case_says_to_start_one_first(self):
        # The two lines came out with a bare <case folder> and no word on how to get a folder.
        out = self.plugin(matter_dir="").command("grant")
        self.assertTrue(out.startswith("No case folder is set up yet"), out[:90])
        self.assertIn("/matter demo", out)
        self.assertIn("/matter init", out)
        self.assertIn("<case folder>", out)
        self.assertIn("nothing to copy yet", out)  # DeepSeek r1 P3-4
        self.assertNotIn("Copy each line", out)
        plain = os.path.join(self._tmp.name, "just a folder")
        os.makedirs(plain)
        out = self.plugin(matter_dir=plain).command("grant")
        self.assertTrue(out.startswith(f"{plain} is not set up as a case folder yet"), out[:90])
        self.assertIn(f"/matter init {plain}", out)
        self.assertIn("<case folder>", out)  # no command that would fail on a folder that is not a case
        self.assertNotIn(f"courtlistener on {shlex.quote(plain)}", out)
        # A case that exists gets the two lines and nothing in front of them.
        self.assertTrue(self.plugin().command("grant").startswith("A person turns on cite checks"))


class TestSkillInsideHermes(unittest.TestCase):
    """With lookups off, the agent told the person to run `python3 <kit>/matter.py courtlistener on
    <folder>`: the skill's words. A stock Mac's python3 is too old for the kit, and inside Hermes
    /matter grant prints the lines that work on this computer."""

    def setUp(self):
        with open(hp.SKILL_PATH, encoding="utf-8") as f:
            self.text = f.read()

    def section(self, heading):
        _, found, rest = self.text.partition(f"\n## {heading}")
        self.assertTrue(found, f"no section: {heading}")
        return rest.split("\n## ", 1)[0]

    def test_a_hermes_section_names_the_slash_commands_and_the_tools(self):
        said = self.section("Inside Hermes")
        for word in ("/matter grant", "/matter demo", "/matter init", "matter_ingest_path", "python3"):
            self.assertIn(word, said)
        # It comes before the terminal setup an agent would otherwise copy.
        self.assertLess(self.text.index("## Inside Hermes"), self.text.index("## Setup"))

    def test_the_lookup_advice_gives_the_hermes_way(self):
        self.assertIn("/matter grant", self.section("Case law lookups"))


class TestDemoReadme(unittest.TestCase):
    """DeepSeek r1 P2-1 and P3-8: the practice case's own README is what a newcomer reads next."""

    def setUp(self):
        with open(os.path.join(KIT, "demo-matter", "README.md"), encoding="utf-8") as f:
            self.text = f.read()

    def test_no_bare_python3_command(self):
        for line in self.text.splitlines():
            self.assertFalse(line.lstrip().startswith("python3 "), line)

    def test_terminal_recipe_leaves_the_draft_unregistered(self):
        ingest = [ln for ln in self.text.splitlines() if "matter.py ingest" in ln]
        self.assertTrue(ingest)
        for ln in ingest:
            self.assertIn("--exclude Drafts/", ln)

    def test_the_picker_says_what_to_do_when_there_is_no_new_python(self):
        # DeepSeek r2 N1: with no python3.10+ the recipe ran an empty command.
        self.assertIn('"${PY:?', self.text)


class TestRoundTwo(PluginCase):
    """DeepSeek r2 N2-N4 and the link-out read it flagged."""

    def test_check_on_the_case_folder_itself_is_words(self):
        p = self.plugin()
        for path in (".", self.dir):
            out = p.command(f"check {path}")
            self.assertIn("Name one file", out)
            self.assertNotIn("matter-relative", out)
        self.assertNotIn("matter-relative", self.call("matter_cite_check", {"path": "."})["error"])

    def test_check_does_not_follow_a_link_out_of_the_case(self):
        outside = os.path.join(self._tmp.name, "secret.md")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("Smith v. Jones, 123 F.4th 456 (2024).\n")
        os.symlink(outside, os.path.join(self.dir, "Evidence", "link.md"))
        out = self.plugin().command("check Evidence/link.md")
        self.assertIn("outside the case folder", out)
        self.assertNotIn("123 F.4th 456", out)

    def test_ingest_of_a_subcase_says_so(self):
        p = self.plugin()
        sub = os.path.join(self.dir, "Filings", "subcase")
        with redirect_stdout(io.StringIO()):
            cases.init(sub)
        with open(os.path.join(sub, "paper.md"), "w", encoding="utf-8") as f:
            f.write("synthetic\n")
        for path in ("Filings/subcase", "Filings/subcase/paper.md"):
            r = self.call("matter_ingest_path", {"path": path})
            self.assertFalse(r["ok"], path)
            self.assertIn("its own case", r["error"])

    def test_ingest_of_a_dangling_link_says_so(self):
        self.plugin()
        os.symlink(os.path.join(self._tmp.name, "gone.md"), os.path.join(self.dir, "Evidence", "gone.md"))
        r = self.call("matter_ingest_path", {"path": "Evidence/gone.md"})
        self.assertFalse(r["ok"])
        self.assertIn("link", r["error"])

    def test_demo_through_a_link_into_the_case_is_refused(self):
        p = self.plugin()
        esc = os.path.join(self._tmp.name, "esc")
        os.symlink(os.path.join(self.dir, "Filings"), esc)
        out = p.command(f"demo {os.path.join(esc, 'practice')}")
        self.assertIn("inside the case at", out)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "Filings", "practice")))
        self.assertEqual(p.settings()["matter_dir"], self.dir)

    def test_init_under_a_case_whose_store_is_a_link_is_refused(self):
        p = self.plugin()
        alias = os.path.join(self._tmp.name, "alias")
        os.makedirs(alias)
        os.symlink(os.path.join(self.dir, ".matter"), os.path.join(alias, ".matter"))
        out = p.command(f"init {os.path.join(alias, 'Evidence', 'new')}")
        self.assertIn(f"inside the case at {alias}", out)
        self.assertFalse(os.path.exists(os.path.join(alias, "Evidence", "new")))


if __name__ == "__main__":
    unittest.main()

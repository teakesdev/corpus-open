"""The intake checklist (matterkit/intake.py, `/matter intake`) and the case-intake skill.

How each piece could fail, and so what these tests pin:

- A step reads as done when the case folder does not hold it (an empty case shown as ready), or
  a count is wrong: facts with no source, open and overdue deadlines, registered papers.
- The "next" deadline is the overdue one, or one a person closed, instead of the next one ahead.
- The Next line names a step that is already done, or asks for the optional draft before saying
  intake is complete.
- The checklist writes to the case: the store migrated or touched, a file added. Or it reaches
  the network (PluginCase makes any network call fail the test).
- A store made before the triggers table existed makes the checklist fail instead of reading.
- Cite checks read as on when a person turned them off, or when the plugin keeps cites local.
- The text does not fit on screen in Hermes Desktop: more than 14 lines, or a line wider than 78
  characters (a long folder path, a long deadline label, the no-case message).
- `/matter intake` is not wired, is missing from the usage text, or comes out with Markdown marks
  on a surface that prints them.
- With no case folder it fails or says nothing useful, or cuts the /matter init command it prints.
- The case-intake skill is not registered, a missing or failing skill stops the other one, the
  skill names a tool the plugin does not have, or it drops the not-a-lawyer line.
"""
import datetime as dt
import hashlib
import io
import os
import re
import shutil
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hermes_plugin import KIT, Ctx, PluginCase, cases, consent, grant, hp, store  # noqa: E402

from matterkit import citelookup, facts, intake, statutelookup  # noqa: E402
from matterkit import deadlines as dl  # noqa: E402
from matterkit import triggers  # noqa: E402

TODAY = dt.date(2026, 11, 9)
SKILL = os.path.join(KIT, "skills", "case-intake", "SKILL.md")


def fits(test, text):
    lines = text.split("\n")
    test.assertLessEqual(len(lines), intake.MAX_LINES, text)
    for line in lines:
        test.assertLessEqual(len(line), intake.WIDTH, line)


def by_key(items):
    return {s.key: s for s in items}


def snapshot(folder):
    """sha256 of every file under folder, by relative path, store included."""
    out = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            with open(p, "rb") as f:
                out[os.path.relpath(p, folder)] = hashlib.sha256(f.read()).hexdigest()
    return out


class TestEmptyCase(PluginCase):
    def test_only_the_folder_and_cite_checks_are_done(self):
        items = by_key(intake.steps(self.dir, today=TODAY))
        self.assertEqual([s.key for s in intake.steps(self.dir, today=TODAY)],
                         ["folder", "facts", "dates", "papers", "cites", "draft"])
        self.assertTrue(items["folder"].done)
        self.assertTrue(items["cites"].done)  # on by default for every new case
        for key in ("facts", "dates", "papers", "draft"):
            self.assertFalse(items[key].done, key)
        self.assertFalse(items["draft"].needed)

    def test_text_names_what_is_missing_and_the_first_next_step(self):
        out = self.plugin().command("intake")
        self.assertIn("- · What happened: no facts recorded yet", out)
        self.assertIn("- · Deadlines: none entered yet", out)
        self.assertIn("- · Papers: none registered yet", out)
        self.assertIn("- ✓ Cite checks: on for case cites and statutes", out)
        self.assertIn(intake.NEXT["facts"], out)
        self.assertIn('say "help me start my case"'.capitalize(), intake.NEXT["facts"])
        self.assertTrue(out.endswith(intake.NOT_A_LAWYER))
        fits(self, out)

    def test_reads_without_writing_anything(self):
        before = snapshot(self.dir)
        self.plugin().command("intake")
        intake.steps(self.dir, today=TODAY)
        self.assertEqual(snapshot(self.dir), before)


class TestDemoCase(PluginCase):
    """The practice case, built the way a person builds it: /matter demo."""

    def setUp(self):
        super().setUp()
        self.demo = os.path.join(self._tmp.name, "corpus-open-demo")
        self.p = self.plugin(matter_dir="", today=TODAY)
        self.p.command(f"demo {self.demo}")

    def test_every_step_is_done_and_counted(self):
        out = self.p.command("intake")
        self.assertIn("Intake checklist · corpus-open-demo · 2026-11-09", out)
        self.assertIn("- ✓ What happened: 4 facts recorded, 1 with no source yet", out)
        self.assertIn("- ✓ Deadlines: 2 open, 1 overdue", out)
        ahead = (TODAY + dt.timedelta(days=7)).isoformat()
        self.assertIn(f"  - next: {ahead} Response to motion to dismiss (demo)", out)
        self.assertIn("- ✓ Papers: 3 registered, each with a fingerprint", out)
        self.assertIn("- ✓ Cite checks: on for case cites and statutes", out)
        self.assertIn("- ✓ Draft (optional): /matter check Drafts/response_draft_synthetic.md", out)
        self.assertIn(intake.COMPLETE, out)
        self.assertNotIn("- · ", out)
        fits(self, out)

    def test_reads_without_writing_anything(self):
        before = snapshot(self.demo)
        self.p.command("intake")
        self.assertEqual(snapshot(self.demo), before)

    def test_a_closed_deadline_is_not_the_next_one(self):
        conn = store.connect(self.demo)
        try:
            soon = (TODAY + dt.timedelta(days=1)).isoformat()
            did = dl.add(conn, "Status conference (closed)", soon, "test")
            conn.execute("UPDATE deadlines SET state='met' WHERE id=?", (did,))
            conn.commit()
        finally:
            conn.close()
        out = self.p.command("intake")
        self.assertNotIn("Status conference", out)
        self.assertIn("2 open, 1 overdue", out)

    def test_plain_surfaces_get_the_same_text(self):
        # Nothing here is Markdown that a terminal would print as typed.
        self.assertEqual(self.p.command("intake", platform="local"), self.p.command("intake"))
        self.assertEqual(self.p.command("intake", platform="cli"), self.p.command("intake"))


class TestPracticeFolderCopied(PluginCase):
    """The repo's demo-matter folder copied as it is and set up with init: nothing registered yet."""

    def test_draft_is_seen_but_papers_wait_for_registering(self):
        folder = os.path.join(self._tmp.name, "practice")
        shutil.copytree(os.path.join(KIT, "demo-matter"), folder)
        with redirect_stdout(io.StringIO()):
            cases.init(folder)
        items = by_key(intake.steps(folder, today=TODAY))
        self.assertFalse(items["papers"].done)
        self.assertTrue(items["draft"].done)
        self.assertEqual(items["draft"].detail, "/matter check Drafts/response_draft_synthetic.md")
        self.assertEqual(intake.next_line(list(items.values())), intake.NEXT["facts"])
        fits(self, intake.render(list(items.values()), today=TODAY))


class TestNextLine(PluginCase):
    def add_fact(self, sources=()):
        conn = store.connect(self.dir)
        try:
            facts.add(conn, self.dir, "The person says the landlord changed the locks.", "ALLEGATION",
                      list(sources), by="test")
        finally:
            conn.close()

    def test_moves_through_the_steps_in_interview_order(self):
        self.add_fact()
        items = intake.steps(self.dir, today=TODAY)
        self.assertEqual(by_key(items)["facts"].detail, "1 fact recorded, 1 with no source yet")
        self.assertEqual(intake.next_line(items), intake.NEXT["dates"])
        conn = store.connect(self.dir)
        try:
            triggers.add(conn, "Answer", "the complaint is served", 21, "test")
        finally:
            conn.close()
        items = intake.steps(self.dir, today=TODAY)
        self.assertEqual(by_key(items)["dates"].detail, "0 open, 1 waiting on an event")
        self.assertEqual(by_key(items)["dates"].sub, "")
        self.assertEqual(intake.next_line(items), intake.NEXT["papers"])
        with open(os.path.join(self.dir, "Evidence", "lease.txt"), "w", encoding="utf-8") as f:
            f.write("Lease, synthetic.")
        self.plugin()
        self.call("matter_ingest_path", {"path": "Evidence"})
        items = intake.steps(self.dir, today=TODAY)
        # Only the optional draft is left, and intake does not wait for it.
        self.assertFalse(by_key(items)["draft"].done)
        self.assertEqual(intake.next_line(items), intake.COMPLETE)

    def test_only_overdue_deadlines_have_no_next_line(self):
        conn = store.connect(self.dir)
        try:
            dl.add(conn, "Reply", (TODAY - dt.timedelta(days=2)).isoformat(), "test")
        finally:
            conn.close()
        step = by_key(intake.steps(self.dir, today=TODAY))["dates"]
        self.assertEqual((step.done, step.detail, step.sub), (True, "1 open, 1 overdue", ""))

    def test_a_store_without_the_triggers_table_still_reads(self):
        path = store.db_path(self.dir)
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE deadline_triggers")
        conn.commit()
        conn.close()
        step = by_key(intake.steps(self.dir, today=TODAY))["dates"]
        self.assertEqual((step.done, step.detail), (False, "none entered yet"))


class TestCiteChecks(PluginCase):
    def test_a_person_turning_checks_off_reads_as_their_choice(self):
        consent.write_declined(self.dir, citelookup.PROVIDER, by="ty", via="test")
        step = by_key(intake.steps(self.dir, today=TODAY))["cites"]
        self.assertEqual(step.detail, "case cites turned off by a person, statutes on")
        self.assertTrue(step.done)

    def test_a_grant_for_named_callers_only_is_not_on(self):
        consent.save_grants(self.dir, [
            grant(citelookup.PROVIDER, citelookup.ENDPOINT, citelookup.PAYLOAD_TYPE),
            grant(statutelookup.PROVIDER, statutelookup.ENDPOINT, statutelookup.PAYLOAD_TYPE)])
        items = intake.steps(self.dir, today=TODAY)
        self.assertFalse(by_key(items)["cites"].done)
        self.assertEqual(by_key(items)["cites"].detail, "not on for case cites and statutes")

    def test_the_plugin_setting_wins(self):
        out = self.plugin(citation_guard="local").command("intake")
        self.assertIn("- ✓ Cite checks: kept on this computer (citation_guard local)", out)
        out = self.plugin(citation_guard="off").command("intake")
        self.assertIn("- ✓ Cite checks: off in this plugin's settings", out)


class TestFitsOnScreen(PluginCase):
    def test_long_folder_and_long_label_are_cut_to_width(self):
        deep = os.path.join(self._tmp.name, *(["a-rather-long-folder-name"] * 4), "smith-v-acme")
        with redirect_stdout(io.StringIO()):
            cases.init(deep)
        conn = store.connect(deep)
        try:
            dl.add(conn, "Response to the defendant's motion for summary judgment and supporting "
                         "memorandum of law", (TODAY + dt.timedelta(days=3)).isoformat(), "test")
            for i in range(30):
                facts.add(conn, deep, f"Synthetic fact {i}.", "ALLEGATION", [], by="test")
        finally:
            conn.close()
        out = self.plugin(matter_dir=deep).command("intake")
        fits(self, out)
        # The start of the path is cut; its end, which names the case, stays.
        self.assertRegex(out, r"\n- ✓ Case folder: …[^\n]*/smith-v-acme\n")
        self.assertIn("30 facts recorded, 30 with no source yet", out)

    def test_every_next_line_fits(self):
        for line in list(intake.NEXT.values()) + [intake.COMPLETE, intake.NOT_A_LAWYER]:
            self.assertLessEqual(len(line), intake.WIDTH, line)


class TestNoCase(PluginCase):
    def test_no_folder_says_how_to_make_one(self):
        out = self.plugin(matter_dir="").command("intake")
        self.assertIn("there is no case folder yet", out)
        self.assertIn("/matter init ~/cases/my-case", out)
        self.assertIn("/matter demo", out)
        self.assertIn("/matter intake again", out)
        self.assertTrue(out.endswith(intake.NOT_A_LAWYER))
        fits(self, out)

    def test_a_folder_that_is_not_a_case_gets_its_own_init_command(self):
        folder = os.path.join(self._tmp.name, "not-yet")
        os.makedirs(folder)
        out = self.plugin(matter_dir=folder).command("intake")
        self.assertIn(f"  /matter init {folder}\n", out)  # whole, so it can be copied
        self.assertFalse(os.path.isdir(os.path.join(folder, ".matter")))  # nothing was made
        self.assertTrue(out.endswith(intake.NOT_A_LAWYER))


class TestWiring(PluginCase):
    def test_usage_and_hint_name_intake(self):
        self.assertIn("| intake |", self.plugin().command("frobnicate"))
        self.assertIn("intake", hp.USAGE)

    def test_args_hint_names_intake(self):
        seen = {}

        class Hint(Ctx):
            def register_command(self, name, handler, **kw):
                seen.update(kw)
                Ctx.register_command(self, name, handler, **kw)
        hp.register(Hint({"matter_dir": self.dir}), secret=lambda n: None)
        self.assertIn("|intake|", seen["args_hint"])
        self.assertIn("intake checklist", seen["description"])

    def test_a_failure_inside_reads_as_a_message_not_a_crash(self):
        p = self.plugin()
        p.command("intake")  # loads the plugin's own copy of the module, which is the one to patch
        own = sys.modules[hp.__name__.rsplit(".", 1)[0] + ".matterkit.intake"]
        with mock.patch.object(own, "steps", side_effect=RuntimeError("disk gone")):
            self.assertEqual(p.command("intake"), "/matter failed: disk gone")


class TestSkill(PluginCase):
    def setUp(self):
        super().setUp()
        with open(SKILL, encoding="utf-8") as f:
            self.text = f.read()

    def test_is_registered_beside_matter_kit(self):
        self.plugin()
        self.assertIn("matter-kit", self.ctx.skills)
        path, description = self.ctx.skills["case-intake"]
        self.assertEqual(os.path.realpath(path), os.path.realpath(SKILL))
        self.assertIn("/matter intake", description)

    def test_frontmatter_names_it_and_says_when_to_use_it(self):
        head = self.text.split("---")[1]
        self.assertIn("\nname: case-intake\n", head)
        description = re.search(r"^description: (.+)$", head, re.M).group(1)
        for words in ("start, open or set up a case", "tells what happened", "one plain question"):
            self.assertIn(words, description)

    def test_names_only_tools_the_plugin_has(self):
        named = set(re.findall(r"\bmatter_[a-z_]+\b", self.text))
        self.assertTrue(named)
        self.assertLessEqual(named, set(hp.SCHEMAS))

    def test_keeps_the_boundaries(self):
        self.assertIn("not a lawyer", self.text)
        self.assertIn("not legal advice", self.text)
        self.assertIn("/matter init ~/cases/my-case", self.text)
        self.assertIn("/matter intake", self.text)
        self.assertIn("**no sources**", self.text)

    def test_a_missing_skill_file_skips_only_that_skill(self):
        gone = os.path.join(self._tmp.name, "no-such", "SKILL.md")
        skills = [(n, gone if n == "case-intake" else p, d) for n, p, d in hp.SKILLS]
        with mock.patch.object(hp, "SKILLS", skills):
            self.plugin()
        self.assertIn("matter-kit", self.ctx.skills)
        self.assertNotIn("case-intake", self.ctx.skills)

    def test_one_skill_that_fails_does_not_stop_the_next(self):
        class Flaky(Ctx):
            def register_skill(self, name, path, description="", frontmatter=None):
                if name == "matter-kit":
                    raise RuntimeError("host refused")
                Ctx.register_skill(self, name, path, description)
        ctx = Flaky({"matter_dir": self.dir})
        hp.register(ctx, secret=lambda n: None)
        self.assertIn("case-intake", ctx.skills)
        self.assertIn("matter", ctx.commands)


if __name__ == "__main__":
    unittest.main()

"""`/matter` in Hermes's classic terminal, which prints a plugin command's answer as it comes.

Hermes takes the Markdown out of a model's reply before the terminal shows it, but not out of a
plugin command's answer. What could go wrong, and so what these tests pin:

- The terminal shows the marks as typed: `**NOT LOOKED UP**`, `# Matter status`, `_a note_`.
- Taking the marks out damages something a person reads or copies: a file name with underscores,
  a line from /matter grant, a cron line's stars.
- A surface that draws Markdown (Desktop, Telegram) loses its formatting.
- The status line shows the whole home path where `~/cases/...` would do, or shortens a folder
  that only starts with the same letters as the home folder.
- The citation block under a model's reply shows its marks. The terminal streams the reply, then
  prints what a plugin added under it as typed (seen live 2026-11-10), so the block has to come
  plain there, while the model's own words, which Hermes draws itself, are not touched.
- A web link shows as `[words](address)`, which nobody can read or click in a terminal.
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hermes_plugin import REPLY, TOKEN, PluginCase, cases, hp  # noqa: E402

DRAFT = "Drafts/response_draft_synthetic.md"
DECLARATION = "Filings/12-declaration_synthetic.md"
SUBS = ("", "deadlines", f"check {DRAFT}", "grant", "packet 3min", "watch", "bogus")


def marks(text):
    """The Markdown marks a terminal would show as typed."""
    found = []
    for ln in text.splitlines():
        s = ln.strip()
        if "**" in ln:
            found.append(("bold", ln))
        if ln.startswith("#") and ln.lstrip("#").startswith(" "):
            found.append(("heading", ln))
        note = s.lstrip("-↳ ")
        if len(note) > 2 and note.startswith("_") and note.endswith("_"):
            found.append(("italic", ln))
        if "`" in ln:
            found.append(("code", ln))
    return found


class TestPlainText(unittest.TestCase):
    def plain(self, text):
        return hp._terminal.plain_text(text)

    def test_marks_come_out_and_words_stay(self):
        self.assertEqual(self.plain("**Citation check: 3 cites**"), "Citation check: 3 cites")
        self.assertEqual(self.plain("- ⚠ **NOT FOUND** · Smith v. Jones, 1 F.3d 2: **check it**"),
                         "- ⚠ NOT FOUND · Smith v. Jones, 1 F.3d 2: check it")
        self.assertEqual(self.plain("# Matter status — ~/cases/demo\n\n## Deadlines"),
                         "Matter status — ~/cases/demo\n\nDeadlines")
        self.assertEqual(self.plain("_Case cites were not looked up._"), "Case cites were not looked up.")
        self.assertEqual(self.plain("Type `/matter grant` to see how."), "Type /matter grant to see how.")
        # A note that is a list item, as the packet writes a fact's source.
        self.assertEqual(self.plain(f"  - ↳ _{DECLARATION} @ p. 2 ¶ 3_"), f"  - ↳ {DECLARATION} @ p. 2 ¶ 3")
        self.assertEqual(self.plain("- _(fill deliberately)_"), "- (fill deliberately)")

    def test_what_a_person_copies_is_left_alone(self):
        for text in ("0 8 * * * hermes run watch",  # a cron line's stars
                     "matter_dir and matter_ingest_path",  # names with underscores
                     f"{DECLARATION} and {DRAFT}",
                     "  # a comment in a script, indented",
                     "#hashtag and C# and issue #12",
                     "2 * 3 ** 4",  # a lone pair of stars with spaces inside is not bold
                     "__init__.py and _private",
                     "see _one word_ in a line",  # only a whole-line note is italic in this plugin
                     ""):
            self.assertEqual(self.plain(text), text, text)

    def test_a_web_link_becomes_its_words_and_its_address(self):
        self.assertEqual(
            self.plain("- ✓ **FOUND** · [Smith v. Jones, 123 F.4th 456]"
                       "(https://www.courtlistener.com/opinion/9001/smith-v-jones/): Smith v. Jones"),
            "- ✓ FOUND · Smith v. Jones, 123 F.4th 456 "
            "<https://www.courtlistener.com/opinion/9001/smith-v-jones/>: Smith v. Jones")
        self.assertEqual(self.plain("[a](http://a.example/x) and [b c](https://b.example/y_z)"),
                         "a <http://a.example/x> and b c <https://b.example/y_z>")
        # Only a web address: brackets a person wrote, and a link to a file, stay as typed.
        for text in ("rows[0](x)", "[sic] (see above)", f"[the declaration]({DECLARATION})",
                     "[1](a) https://example.com/x", "[] (https://example.com)"):
            self.assertEqual(self.plain(text), text, text)

    def test_only_a_string_is_changed(self):
        self.assertEqual(self.plain(None), None)


class TestMatterInTheTerminal(PluginCase):
    def demo(self, **settings):
        self.target = os.path.join(self._tmp.name, "demo")
        p = self.plugin(matter_dir="", citation_guard="local", **settings)
        p.command(f"demo {self.target}")
        return p

    def test_the_terminal_gets_no_markdown_marks(self):
        p = self.demo()
        for sub in ("", f"check {DRAFT}"):  # the marks are there to take out
            self.assertTrue(marks(p.command(sub)), sub)
        for sub in SUBS:
            self.assertEqual(marks(p.command(sub, platform="cli")), [], sub)

    def test_the_words_are_the_same(self):
        p = self.demo()
        out = p.command(f"check {DRAFT}", platform="cli")
        for words in ("Citation check: 1 of 3 to check by hand",
                      "- ⚠ NO SUCH VOLUME · Harlow v. Demo Freight Lines, Inc., 812 F.4th 1099: F.4th has no volume 812",
                      "- · NOT LOOKED UP · Ashcroft v. Iqbal, 556 U.S. 662",
                      "Quotations and page cites: 2 of 3 to fix", "- ⚠ not found: ", "- ⚠ wrong page: ",
                      "- ✓ exact: ", DECLARATION, "Case cites were not looked up"):
            self.assertIn(words, out)
        status = p.command("", platform="cli")
        self.assertTrue(status.startswith("Matter status — "), status[:60])
        self.assertRegex(status, r"(?m)^Documents registered: \d+$")
        self.assertIn("OVERDUE", status)

    def test_grant_lines_are_untouched(self):
        p = self.demo()
        raw, plain = p.command("grant"), p.command("grant", platform="cli")
        commands = [ln for ln in raw.splitlines() if ln.startswith("  ") and "matter.py" in ln]
        self.assertEqual(len(commands), 4)  # each check off, and each back on
        for ln in commands:
            self.assertIn(ln, plain.splitlines())

    def test_other_surfaces_keep_their_markdown(self):
        p = self.demo()
        for sub in SUBS:
            base = p.command(sub)
            for platform in ("desktop", "telegram", "discord", "tui", "", None):
                self.assertEqual(p.command(sub, platform=platform), base, (sub, platform))
            self.assertEqual(p.command(sub, source=None, session_id="s", session_key="s"), base, sub)
        self.assertIn("**", p.command(f"check {DRAFT}", platform="desktop"))
        self.assertTrue(p.command("", platform="desktop").startswith("# Matter status — "))

    def test_a_session_the_gateway_owns_gets_no_marks_either(self):
        # With a gateway running, Hermes runs the command there and hands it "local" for the classic
        # terminal, the TUI and Desktop alike (its plugin docs: "`local` (CLI/Desktop sessions owned
        # by the gateway)"), with nothing a command can read to tell them apart. Seen live
        # 2026-11-10: the terminal showed the marks. Plain text reads right on all three.
        p = self.demo()
        for sub in SUBS:
            out = p.command(sub, platform="local")
            self.assertEqual(marks(out), [], sub)
            self.assertEqual(out, p.command(sub, platform="cli"), sub)

    def test_a_case_with_no_folder_yet(self):
        out = self.plugin(matter_dir="").command("", platform="cli")
        self.assertIn("/matter demo", out)
        self.assertEqual(marks(out), [])


class TestTheBlockUnderAReply(PluginCase):
    """The citation block the plugin puts under a model's reply (the `transform_llm_output` hook).
    Hermes hands the hook the session's platform: "cli" for the classic terminal."""
    # What a model writes, with every mark this plugin's plain form takes out.
    MODEL = "**Must fix:**\n\n# What I found\n\n_One note._\n\nRun `/matter check` again.\n\n" + REPLY

    def reply(self, text=REPLY, **kw):
        # A case of its own each time: a case's permission here allows ten lookups a day, and a
        # reply that runs out of them reads differently.
        self.cases = getattr(self, "cases", 0) + 1
        self.dir = os.path.join(self._tmp.name, f"matter-{self.cases}")
        with redirect_stdout(io.StringIO()):
            cases.init(self.dir)
        self.grant_all()
        p = self.plugin(secrets={"COURTLISTENER_TOKEN": TOKEN})
        return p, p.on_reply(response_text=text, **kw)

    def test_the_terminal_gets_the_block_without_marks(self):
        self.assertTrue(marks(self.reply()[1][len(REPLY):]))  # the marks are there to take out
        for platform in hp._terminal.PLAIN_PLATFORMS:
            block = self.reply(platform=platform)[1][len(REPLY):]
            self.assertEqual(marks(block), [], platform)
            self.assertNotIn("](", block, platform)
            for words in ("\n\n---\nCitation check: 2 of 4 to check by hand\n",
                          "- ✓ FOUND · Smith v. Jones, 123 F.4th 456 <https://www.courtlistener.com/opinion/9001/",
                          "- ⚠ NOT FOUND · Doe v. Roe Corp., 501 U.S. 1001",
                          "- ✓ IN CORPUS · Miss. Code Ann. § 89-7-27 "
                          "<https://corpuslaw.us/code/37495c6b-7c51-4053-b24b-9a966a1a4492>",
                          "Proceedings against tenant holding over",
                          "NOT MATCHED IN CORPUS · Miss. Code Ann. § 89-8-99",
                          "Checked by Corpus Open; not legal advice."):
                self.assertIn(words, block, platform)
            self.assertLess(block.index("NOT FOUND"), block.index("✓ FOUND"))  # problems still first

    def test_the_models_own_words_are_not_touched(self):
        _p, out = self.reply(self.MODEL, platform="cli")
        self.assertTrue(out.startswith(self.MODEL + "\n\n---\n"), out[:200])
        self.assertEqual(marks(out[len(self.MODEL):]), [])

    def test_other_surfaces_keep_their_markdown(self):
        base = self.reply()[1]
        self.assertIn("✓ **FOUND** · [Smith v. Jones, 123 F.4th 456](https://", base)
        for platform in ("desktop", "telegram", "discord", "tui", "api_server", "", None):
            self.assertEqual(self.reply(platform=platform)[1], base, platform)
        self.assertEqual(self.reply(session_id="s", model="m", turn_id="t")[1], base)

    def test_a_replay_of_the_plain_reply_is_not_checked_again(self):
        p, out = self.reply(platform="cli")
        lookups = len(self.posted)
        self.assertIsNone(p.on_reply(response_text=out, platform="cli"))
        self.assertEqual(len(self.posted), lookups)

    def test_a_reply_with_no_cites_is_left_alone(self):
        self.assertIsNone(self.reply("Your hearing is Tuesday.", platform="cli")[1])

    def test_the_practice_draft_in_the_terminal(self):
        # What the first live reply showed with its marks on (2026-11-10).
        target = os.path.join(self._tmp.name, "demo")
        p = self.plugin(matter_dir="", citation_guard="local")
        p.command(f"demo {target}")
        with open(os.path.join(target, DRAFT), encoding="utf-8") as f:
            text = f.read()
        block = p.on_reply(response_text=text, platform="cli")[len(text):]
        self.assertEqual(marks(block), [])
        self.assertIn("Citation check: 1 of 3 to check by hand", block)
        self.assertIn("- ⚠ NO SUCH VOLUME · Harlow v. Demo Freight Lines, Inc., 812 F.4th 1099: "
                      "F.4th has no volume 812", block)


class TestHomeFolder(PluginCase):
    def heading(self, home, **kw):
        with mock.patch.dict(os.environ, {"HOME": home}):
            return self.plugin().command("", **kw).splitlines()[0]

    def test_status_shows_the_home_folder_as_a_tilde(self):
        self.assertEqual(self.heading(self._tmp.name), "# Matter status — ~/matter")
        self.assertEqual(self.heading(self._tmp.name, platform="cli"), "Matter status — ~/matter")
        self.assertEqual(self.heading(self._tmp.name + os.sep), "# Matter status — ~/matter")

    def test_a_folder_outside_home_keeps_its_path(self):
        elsewhere = os.path.join(self._tmp.name, "somewhere", "else")
        self.assertEqual(self.heading(elsewhere), f"# Matter status — {self.dir}")
        # A home folder whose name is only the start of the case folder's name is not its parent.
        self.assertEqual(self.heading(os.path.join(self._tmp.name, "mat")), f"# Matter status — {self.dir}")
        self.assertEqual(self.heading(self.dir), f"# Matter status — {self.dir}")

    def test_demo_and_init_show_the_folder_the_same_way(self):
        demo = os.path.join(self._tmp.name, "cases", "demo")
        mine = os.path.join(self._tmp.name, "cases", "mine")
        with mock.patch.dict(os.environ, {"HOME": self._tmp.name}):
            p = self.plugin(matter_dir="")
            out = p.command(f"demo {demo}")
            self.assertIn("Made a practice case at ~/cases/demo. ", out)
            self.assertIn("The plugin now uses ~/cases/demo as your case folder.", out)
            self.assertEqual(self.ctx.settings["matter_dir"], demo)  # the setting keeps the full path
            out = p.command(f"init {mine}")
            self.assertIn("Case folder ready: ~/cases/mine (", out)
            self.assertIn("goes in ~/cases/mine/Filings;", out)
            self.assertIn("goes in ~/cases/mine/Evidence.", out)
            self.assertEqual(self.ctx.settings["matter_dir"], mine)
        self.assertNotIn(self._tmp.name, out)

    def test_the_tool_still_gives_the_agent_the_full_path(self):
        with mock.patch.dict(os.environ, {"HOME": self._tmp.name}):
            self.plugin()
            report = self.call("matter_status")["result"]
        self.assertTrue(str(report).startswith(f"# Matter status — {self.dir}"), str(report)[:80])


if __name__ == "__main__":
    unittest.main()

"""The quote guard on terminal commands and execute_code runs (hermes_plugin/quote_guard.py).

The quote guard refuses a write_file or patch call that would put a quotation or record page cite
into a draft that the case record does not support. On 2026-11-10 an agent asked to append one
sentence to the practice case's draft used the terminal instead, twice, and both writes went
through unchecked (case folder as the working directory; Doc. 12 holds no such words):

  printf '\\n%s\\n' 'As the declaration states, "the supervisor admitted the firing was retaliation." (Doc. 12 at 2)' >> Drafts/response_draft_synthetic.md
  printf '\\n\\nAs the declaration states, "the supervisor admitted the firing was retaliation." (Doc. 12 at 2)\\n' >> Drafts/response_draft_synthetic.md

What could go wrong, and the test that would catch it:
- a command or code run that writes a made-up quotation into a draft runs anyway
                                                    -> test_the_two_real_commands_are_refused,
                                                       test_each_way_of_writing_a_bad_quotation_is_refused
- the command's own quoting hides the quotation or its cite: shell quotes around the sentence, \\"
  escapes, a here-document, the redirect words after the cite (they push the cite out of the
  quotation's sentence), Python string quotes around a file name read as a quotation
                                                    -> the same two tests, which also check that the
                                                       one problem named is the real quotation
- a true quotation is refused                       -> test_an_exact_quotation_passes_in_every_form
- a wrong page passes, or the message does not say where the words are
                                                    -> test_a_wrong_page_is_refused_and_named
- the message says "nothing was written to" a file, untrue for a command, or does not send the agent
  to patch or write_file                            -> test_the_message_fits_a_command
- a command that only reads the draft, or writes outside any case, is refused
                                                    -> Passes tests
- a relative path is only found from the working directory, or an absolute one not at all
                                                    -> Paths tests
- a command naming a registered source no longer goes to the source guard's approval first
                                                    -> test_the_source_guard_still_comes_first
- the guard raises, or checks with the setting off  -> Passes tests
- the write_file message changes                    -> test_the_write_file_message_is_unchanged

Cases are synthetic: /matter demo copies the kit's demo-matter/ into a temp folder. No test
reaches the network.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hermes_plugin import PluginCase, hp  # noqa: E402

qg = hp._quote_guard
sg = hp._source_guard

DRAFT = "Drafts/response_draft_synthetic.md"
DECLARATION = "Filings/12-declaration_synthetic.md"
# Not in Doc. 12 at all (the words the agent wrote on 2026-11-10).
BAD = 'As the declaration states, "the supervisor admitted the firing was retaliation." (Doc. 12 at 2)'
BAD_QUOTE = "“the supervisor admitted the firing was retaliation.” (Doc. 12 at 2)"
# Doc. 12, paragraph 5, on page 2.
EXACT = 'In her words, "No one at that meeting gave a reason for the termination." (Doc. 12 at 2)'
WRONG = EXACT.replace("at 2)", "at 3)")
REAL = (
    "printf '\\n%s\\n' '" + BAD + "' >> " + DRAFT,
    "printf '\\n\\n" + BAD + "\\n' >> " + DRAFT,
)


def esc(s):
    """s inside double quotes, shell or Python."""
    return s.replace('"', '\\"')


def forms(s, draft=DRAFT):
    """(name, tool, text): ways an agent can append the sentence s to the draft."""
    return [
        ("printf, sentence as an argument (live)", "terminal", "printf '\\n%s\\n' '" + s + "' >> " + draft),
        ("printf, sentence in the format (live)", "terminal", "printf '\\n\\n" + s + "\\n' >> " + draft),
        ("echo, escaped double quotes", "terminal", 'echo "' + esc(s) + '" >> ' + draft),
        ("echo, unquoted words", "terminal",
         "echo " + esc(s).replace("(", "\\(").replace(")", "\\)") + " >> " + draft),
        ("echo, ANSI-C quotes", "terminal", "echo $'\\n" + s + "' >> " + draft),
        ("printf, the '\\'' apostrophe idiom", "terminal",
         "printf '%s\\n' 'Demo Corp'\\''s HR manager swore to it. " + s + "' >> " + draft),
        ("here-document", "terminal", "cat >> " + draft + " <<'EOF'\n\n" + s + "\nEOF\n"),
        ("here-document, <<- and the redirect after it", "terminal",
         "cat <<-EOF >> " + draft + "\n\t" + s + "\n\tEOF\n"),
        ("tee -a", "terminal", "printf '%s\\n' '" + s + "' | tee -a " + draft),
        ("bash -c", "terminal", "bash -c \"printf '%s\\n' '" + esc(s) + "' >> " + draft + '"'),
        ("python3 -c, shell double quotes", "terminal",
         "python3 -c \"open('" + draft + "', 'a').write('\\n" + esc(s) + "\\n')\""),
        ("python3 -c, shell single quotes", "terminal",
         "python3 -c 'open(\"" + draft + "\", \"a\").write(\"\\n" + esc(s) + "\\n\")'"),
        ("python3 here-document", "terminal",
         "python3 - <<'EOF'\nwith open(\"" + draft + "\", \"a\") as f:\n    f.write(\"\\n" + esc(s) + "\\n\")\nEOF\n"),
        ("execute_code", "execute_code",
         'with open("' + draft + '", "a", encoding="utf-8") as f:\n    f.write("\\n' + esc(s) + '\\n")\n'),
        ("execute_code, a string split over lines", "execute_code",
         'from pathlib import Path\np = Path("' + draft + '")\n'
         'p.write_text(p.read_text() + ("\\n"\n    ' + repr(s) + '\n    "\\n"))\n'),
    ]


class Base(PluginCase):
    def setUp(self):
        super().setUp()
        self.case = os.path.join(os.path.realpath(self._tmp.name), "demo")
        self.p = self.plugin(matter_dir="")
        self.p.command(f"demo {self.case}")
        self.assertEqual(self.ctx.settings["matter_dir"], self.case)
        self.draft = os.path.join(self.case, DRAFT)
        with open(self.draft, encoding="utf-8") as f:
            self.before = f.read()
        p = mock.patch.object(qg, "USE_PDFTOTEXT", False)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.case)  # as Hermes ran the live commands
        self.outside = os.path.join(os.path.realpath(self._tmp.name), "elsewhere")
        os.makedirs(self.outside)

    def guard(self, tool, text):
        return qg.check(tool, {sg.CODE_TOOLS[tool]: text}, matter_dir=self.case, use_pdftotext=False)

    def hook(self, tool, text):
        return self.p.on_tool_call(tool, {sg.CODE_TOOLS[tool]: text})

    def assertRefused(self, d, *words):
        self.assertIsNotNone(d)
        self.assertEqual(d["action"], "block", d)
        for w in words:
            self.assertIn(w, d["message"])
        return d["message"]


class Blocked(Base):
    def test_the_two_real_commands_are_refused(self):
        for cmd in REAL:
            with self.subTest(cmd=cmd):
                for d in (self.guard("terminal", cmd), self.hook("terminal", cmd)):
                    msg = self.assertRefused(d, "was not run", BAD_QUOTE, "not found", DRAFT)
                    self.assertEqual(msg.count("\n- "), 1, msg)
        with open(self.draft, encoding="utf-8") as f:
            self.assertEqual(f.read(), self.before)

    def test_each_way_of_writing_a_bad_quotation_is_refused(self):
        for name, tool, text in forms(BAD):
            with self.subTest(name):
                msg = self.assertRefused(self.guard(tool, text), "was not run", BAD_QUOTE, "not found")
                self.assertEqual(msg.count("\n- "), 1, msg)  # the real quotation, not a piece of the command

    def test_the_hook_refuses_code_too(self):
        _name, tool, text = forms(BAD)[-2]
        self.assertRefused(self.hook(tool, text), "was not run", BAD_QUOTE)

    def test_the_message_fits_a_command(self):
        msg = self.assertRefused(self.guard("terminal", REAL[0]))
        self.assertTrue(msg.startswith("corpus-open quote check: this command was not run."), msg)
        self.assertNotIn("nothing was written", msg)
        self.assertIn(DRAFT, msg)
        self.assertNotIn(self.draft, msg)  # the draft is named inside the case
        self.assertIn(f"the case record at {self.case}", msg)
        self.assertIn("- not found: " + BAD_QUOTE + " is on no page of " + DECLARATION, msg)
        for words in ("patch", "write_file", "Quote the document's exact words, cite the page where they "
                      "really are, or take the quotation out", "Matching ignores case, curly quotes and "
                      "dashes; ellipses and [brackets] mark changes."):
            self.assertIn(words, msg)
        code = self.assertRefused(self.guard("execute_code", forms(BAD)[-2][2]))
        self.assertTrue(code.startswith("corpus-open quote check: this code was not run."), code)


class Verdicts(Base):
    def test_an_exact_quotation_passes_in_every_form(self):
        for name, tool, text in forms(EXACT):
            with self.subTest(name):
                self.assertIsNone(self.guard(tool, text))
        self.assertIsNone(self.hook("terminal", forms(EXACT)[0][2]))

    def test_a_wrong_page_is_refused_and_named(self):
        for name, tool, text in forms(WRONG):
            with self.subTest(name):
                self.assertRefused(self.guard(tool, text), "wrong page", "(Doc. 12 at 3) is really at p. 2")


class Passes(Base):
    def test_a_command_that_only_reads_the_draft_passes(self):
        for cmd in ("grep -n '" + BAD + "' " + DRAFT,
                    "grep -c '" + BAD + "' " + DRAFT + " 2>/dev/null",
                    "cat " + DRAFT + " | grep -F '\"the supervisor admitted\"' > /dev/null"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.guard("terminal", cmd))
                self.assertIsNone(self.hook("terminal", cmd))

    def test_a_write_outside_any_case_passes(self):
        out = os.path.join(self.outside, "notes.md")
        for cwd in (self.case, self.outside):
            os.chdir(cwd)
            for name, tool, text in forms(BAD, draft=out):
                with self.subTest(name, cwd=cwd):
                    self.assertIsNone(self.guard(tool, text))
                    self.assertIsNone(self.hook(tool, text))

    def test_a_write_to_a_file_that_is_not_text_passes(self):
        self.assertIsNone(self.guard("terminal", "printf '%s\\n' '" + BAD + "' >> Drafts/notes.json"))

    def test_the_setting_off_lets_it_through(self):
        p = self.plugin(matter_dir=self.case, quote_guard="off")
        for cmd in REAL:
            self.assertIsNone(p.on_tool_call("terminal", {"command": cmd}))
        self.assertIsNone(p.on_tool_call("execute_code", {"code": forms(BAD)[-2][2]}))

    def test_odd_arguments_pass_without_raising(self):
        for tool in ("terminal", "execute_code"):
            key = sg.CODE_TOOLS[tool]
            for args in ("printf x >> " + DRAFT, None, [], {}, {key: None}, {key: 42}, {key: b"x >> a.md"},
                         {key: ""}, {key: "   \n\t"}, {"cmd": REAL[0]}):
                with self.subTest(tool=tool, args=args):
                    self.assertIsNone(qg.check(tool, args, matter_dir=self.case, use_pdftotext=False))
                    self.assertIsNone(self.p.on_tool_call(tool, args))

    def test_a_command_longer_than_the_limit_passes(self):
        cmd = REAL[0] + "\n# " + "x" * qg.MAX_CHARS
        self.assertIsNone(self.guard("terminal", cmd))

    def test_a_failing_check_lets_the_command_run(self):
        with mock.patch.object(qg.quotecheck, "check_text", side_effect=RuntimeError("boom")):
            self.assertIsNone(self.hook("terminal", REAL[0]))


class Paths(Base):
    def test_a_path_relative_to_the_case_folder_setting(self):
        os.chdir(self.outside)
        for cmd in REAL:
            with self.subTest(cmd=cmd):
                self.assertRefused(self.guard("terminal", cmd), "was not run", DRAFT)
                self.assertRefused(self.hook("terminal", cmd), "was not run", DRAFT)

    def test_an_absolute_path(self):
        os.chdir(self.outside)
        for name, tool, text in forms(BAD, draft=self.draft):
            with self.subTest(name):
                self.assertRefused(self.guard(tool, text), "was not run", DRAFT)


class Order(Base):
    def test_the_source_guard_still_comes_first(self):
        for cmd in ("printf '%s\\n' '" + BAD + "' | tee -a " + DRAFT + " " + DECLARATION,
                    "printf '%s\\n' '" + BAD + "' >> " + DECLARATION):
            with self.subTest(cmd=cmd):
                d = self.hook("terminal", cmd)
                self.assertEqual(d["action"], "approve", d)
                self.assertEqual(d["rule_key"], sg.RULE_KEY)
                self.assertIn("registered source document " + DECLARATION, d["message"])

    def test_the_write_file_message_is_unchanged(self):
        d = qg.check("write_file", {"path": self.draft, "content": BAD + "\n"}, matter_dir=self.case,
                     use_pdftotext=False)
        self.assertEqual(d, {"action": "block", "message": (
            f"corpus-open quote check: nothing was written to {DRAFT}. It would put 1 quotation(s) or "
            f"page cite(s) in the draft that the case record at {self.case} does not support:\n"
            f"- not found: {BAD_QUOTE} is on no page of {DECLARATION}\n"
            "Quote the document's exact words, cite the page where they really are, or take the "
            "quotation out, then write again. Matching ignores case, curly quotes and dashes; ellipses "
            "and [brackets] mark changes.")})


if __name__ == "__main__":
    unittest.main()

"""Quote checker (matterkit/quotecheck.py) and the Hermes quote guard (hermes_plugin/quote_guard.py).

Before an agent writes a quotation or a page cite into a draft, the quotation is looked for in the
cited case document's text. Each one gets a verdict:

  exact        the words are on the cited page (or anywhere in the document when no page is cited)
  wrong page   the words are in the document, but not on the cited page; or the cited page is past
               the document's last page
  not found    the words are on no page of the cited document
  can't check  there is no readable text for the document, its bytes changed since it was
               registered, the quotation cites case law or nothing at all

The guard blocks a write that would put a "not found" or "wrong page" into a draft and says what is
wrong; "exact" and "can't check" pass. It never raises.

How this could fail, and the test that would catch it:
- a quotation on page 2 cited at page 1 passes            -> test_wrong_page_names_the_real_page
- a made-up quotation passes                               -> test_invented_quote_is_not_found
- a quotation split across a page break is called missing -> test_quote_across_a_page_break_is_exact
- a document whose bytes changed is trusted                -> test_changed_source_cannot_be_checked
- a patch adding only the quotation loses the sentence's cite and blocks wrongly, or passes a lie
                                                           -> PatchContext tests
- a quotation already in the draft blocks an unrelated edit -> test_old_problems_do_not_block_a_new_edit
- a guard bug stops the agent                              -> test_hook_never_raises

Case folders are synthetic and live in a temp folder. No test reaches the network.
"""
import importlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import ingest, quotecheck, store  # noqa: E402

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
qg = importlib.import_module(PKG + ".hermes_plugin.quote_guard")

MOTION = ("Defendants move to dismiss. The Complaint fails to state a claim.\f"
          "The deputy testified that he never saw the warrant before the arrest.\f"
          "Plaintiff was released the next morning without any\f"
          "charges being filed against him.")


class Ctx:
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


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.case = os.path.join(os.path.realpath(self.tmp.name), "case")
        self.write("Filings/16-Motion.txt", MOTION)
        self.write("Filings/19-Memo.pdf", "%PDF-1.7\n%synthetic, no text layer\n")
        conn = store.connect(self.case)
        with redirect_stdout(io.StringIO()):
            ingest.ingest_paths(conn, self.case, [os.path.join(self.case, "Filings")])
        conn.commit()
        conn.close()
        with open(os.path.join(self.case, ".matter", "docket.json"), "w") as f:
            json.dump({"entries": {"16": "Filings/16-Motion.txt", "19": "Filings/19-Memo.pdf"}}, f)
        os.makedirs(os.path.join(self.case, "Drafts"))
        self.draft = os.path.join(self.case, "Drafts", "brief.md")

    def write(self, rel, text):
        p = os.path.join(self.case, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def verdicts(self, text, **kw):
        kw.setdefault("use_pdftotext", False)
        return [(r.verdict, r.quote) for r in quotecheck.check_text(self.case, text, **kw)]

    def one(self, text, **kw):
        kw.setdefault("use_pdftotext", False)
        rs = quotecheck.check_text(self.case, text, **kw)
        self.assertEqual(len(rs), 1, rs)
        return rs[0]


class Verdicts(Base):
    def test_quote_on_the_cited_page_is_exact(self):
        r = self.one('The deputy admitted he "never saw the warrant before the arrest." Doc. 16 at 2.\n')
        self.assertEqual(r.verdict, quotecheck.EXACT)
        self.assertEqual(r.found_on, "p. 2")
        self.assertIn("Doc. 16", r.cite)

    def test_quote_with_no_page_is_exact_when_anywhere_in_the_document(self):
        self.assertEqual(self.one('He "never saw the warrant" (Doc. 16).\n').verdict, quotecheck.EXACT)

    def test_wrong_page_names_the_real_page(self):
        r = self.one('The deputy admitted he "never saw the warrant before the arrest." Doc. 16 at 1.\n')
        self.assertEqual(r.verdict, quotecheck.WRONG_PAGE)
        self.assertEqual(r.found_on, "p. 2")

    def test_invented_quote_is_not_found(self):
        r = self.one('The deputy admitted he "forged the warrant himself." Doc. 16 at 2.\n')
        self.assertEqual(r.verdict, quotecheck.NOT_FOUND)
        self.assertIn("Filings/16-Motion.txt", r.checked_against)

    def test_altered_word_in_a_quote_is_not_found(self):
        r = self.one('He "never saw the subpoena before the arrest." Doc. 16 at 2.\n')
        self.assertEqual(r.verdict, quotecheck.NOT_FOUND)

    def test_quote_across_a_page_break_is_exact(self):
        r = self.one('Plaintiff was "released the next morning without any charges being filed." '
                     'Doc. 16 at 3–4.\n')
        self.assertEqual(r.verdict, quotecheck.EXACT)

    def test_page_cite_past_the_last_page_is_wrong_page(self):
        r = self.one("The motion argues the Complaint is deficient. Doc. 16 at 9.\n")
        self.assertEqual(r.verdict, quotecheck.WRONG_PAGE)
        self.assertEqual(r.quote, "")
        self.assertIn("4 pages", r.note)

    def test_document_with_no_text_cannot_be_checked(self):
        r = self.one('The memo says "anything at all." Doc. 19 at 1.\n')
        self.assertEqual(r.verdict, quotecheck.CANT_CHECK)

    def test_case_law_quote_cannot_be_checked(self):
        r = self.one('A claim must be "plausible on its face." Bell Atl. Corp. v. Twombly, '
                     '550 U.S. 544, 570 (2007).\n')
        self.assertEqual(r.verdict, quotecheck.CANT_CHECK)

    def test_uncited_quote_cannot_be_checked(self):
        self.assertEqual(self.one('Counsel called it "a mess."\n').verdict, quotecheck.CANT_CHECK)

    def test_changed_source_cannot_be_checked(self):
        self.write("Filings/16-Motion.txt", MOTION.replace("never saw", "always saw"))
        r = self.one('He "always saw the warrant" Doc. 16 at 2.\n')
        self.assertEqual(r.verdict, quotecheck.CANT_CHECK)
        self.assertIn("changed", r.note)

    def test_only_keeps_results_from_the_new_text(self):
        text = ('He "forged the warrant himself." Doc. 16 at 2.\n\n'
                'He "never saw the warrant" Doc. 16 at 2.\n')
        got = self.verdicts(text, only=['He "never saw the warrant" Doc. 16 at 2.'])
        self.assertEqual(got, [(quotecheck.EXACT, "never saw the warrant")])

    def test_text_with_no_quotes_or_cites_gives_nothing(self):
        self.assertEqual(self.verdicts("Plain sentence with no quotation.\n"), [])

    def test_store_is_not_written(self):
        db = store.db_path(self.case)
        before = os.stat(db).st_mtime_ns
        self.verdicts('He "never saw the warrant" Doc. 16 at 2.\n')
        self.assertEqual(os.stat(db).st_mtime_ns, before)


class Guard(Base):
    def check(self, tool, args, matter_dir=None):
        return qg.check(tool, args, matter_dir=matter_dir, use_pdftotext=False)

    def test_write_with_invented_quote_is_blocked(self):
        d = self.check("write_file", {"path": self.draft,
                                      "content": 'He "forged the warrant himself." Doc. 16 at 2.\n'})
        self.assertEqual(d["action"], "block")
        self.assertIn("forged the warrant himself", d["message"])
        self.assertIn("not found", d["message"].lower())

    def test_write_with_wrong_page_is_blocked_and_gives_the_real_page(self):
        d = self.check("write_file", {"path": self.draft,
                                      "content": 'He "never saw the warrant" Doc. 16 at 1.\n'})
        self.assertEqual(d["action"], "block")
        self.assertIn("p. 2", d["message"])

    def test_exact_and_cant_check_pass(self):
        content = ('He "never saw the warrant" Doc. 16 at 2.\n\n'
                   'The memo says "anything at all." Doc. 19 at 1.\n')
        self.assertIsNone(self.check("write_file", {"path": self.draft, "content": content}))

    def test_write_outside_a_case_passes(self):
        out = os.path.join(os.path.dirname(self.case), "notes.md")
        self.assertIsNone(self.check("write_file", {"path": out,
                                                    "content": 'He "forged it." Doc. 16 at 2.\n'}))

    def test_non_text_file_passes(self):
        p = os.path.join(self.case, "Drafts", "tool.py")
        self.assertIsNone(self.check("write_file", {"path": p, "content": 'x = "forged it" # Doc. 16 at 2\n'}))

    def test_relative_path_uses_the_case_folder_setting(self):
        d = self.check("write_file", {"path": "Drafts/brief.md",
                                      "content": 'He "forged the warrant himself." Doc. 16 at 2.\n'},
                       matter_dir=self.case)
        self.assertEqual(d["action"], "block")

    def test_other_tools_pass(self):
        self.assertIsNone(self.check("read_file", {"path": self.draft}))
        self.assertIsNone(self.check("write_file", "not a dict"))


class PatchContext(Base):
    def setUp(self):
        super().setUp()
        with open(self.draft, "w", encoding="utf-8") as f:
            f.write('The deputy admitted he QUOTE. Doc. 16 at 2.\n\nOld line "forged it." Doc. 16 at 2.\n')

    def check(self, args):
        return qg.check("patch", dict(args, path=self.draft), use_pdftotext=False)

    def test_replace_reads_the_cite_from_the_surrounding_sentence(self):
        ok = self.check({"old_string": "QUOTE", "new_string": '"never saw the warrant"'})
        self.assertIsNone(ok)
        bad = self.check({"old_string": "QUOTE", "new_string": '"burned the warrant"'})
        self.assertEqual(bad["action"], "block")
        self.assertIn("burned the warrant", bad["message"])

    def test_old_problems_do_not_block_a_new_edit(self):
        self.assertIsNone(self.check({"old_string": "The deputy", "new_string": "Deputy Doe"}))

    def test_v4a_added_lines_are_checked(self):
        patch = ("*** Begin Patch\n*** Update File: " + self.draft + "\n@@\n"
                 "+He \"burned the warrant\" Doc. 16 at 2.\n*** End Patch\n")
        d = qg.check("patch", {"mode": "patch", "patch": patch}, use_pdftotext=False)
        self.assertEqual(d["action"], "block")
        self.assertIn("burned the warrant", d["message"])


class Hook(Base):
    def plugin(self, **settings):
        return hp.register(Ctx(dict({"matter_dir": self.case}, **settings)), secret=lambda n: None)

    def test_hook_blocks_through_the_plugin(self):
        with mock.patch.object(qg, "USE_PDFTOTEXT", False):
            d = self.plugin().on_tool_call("write_file", {"path": self.draft,
                                                          "content": 'He "forged it." Doc. 16 at 2.\n'})
        self.assertEqual(d["action"], "block")

    def test_quote_guard_setting_off_lets_it_through(self):
        d = self.plugin(quote_guard="off").on_tool_call(
            "write_file", {"path": self.draft, "content": 'He "forged it." Doc. 16 at 2.\n'})
        self.assertIsNone(d)

    def test_source_guard_still_blocks_a_source(self):
        src = os.path.join(self.case, "Filings", "16-Motion.txt")
        d = self.plugin(quote_guard="off").on_tool_call("write_file", {"path": src, "content": "x"})
        self.assertEqual(d["action"], "block")
        self.assertIn("source", d["message"])

    def test_hook_never_raises(self):
        with mock.patch.object(qg, "check", side_effect=RuntimeError("boom")):
            d = self.plugin().on_tool_call("write_file", {"path": self.draft, "content": "x"})
        self.assertIsNone(d)


class Demo(unittest.TestCase):
    """/matter demo makes a case where the guard can be shown: Doc. 12 is a three-page declaration."""

    def test_demo_case_shows_each_verdict(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        target = os.path.join(os.path.realpath(tmp.name), "demo")
        ctx = Ctx({"matter_dir": ""})
        ctx.set_config = lambda key, value: ctx.settings.__setitem__(key, value)
        p = hp.register(ctx, secret=lambda n: None)
        self.assertIn("Doc. 12", p.command(f"demo {target}"))
        draft = os.path.join(target, "Drafts", "opposition.md")

        def write(content):
            with mock.patch.object(qg, "USE_PDFTOTEXT", False):
                return p.on_tool_call("write_file", {"path": draft, "content": content})

        self.assertIsNone(write('HR was told to mark the file "voluntary resignation." Doc. 12 at 3.\n'))
        d = write('No one "gave a reason for the termination." Doc. 12 at 1.\n')
        self.assertEqual(d["action"], "block")
        self.assertIn("p. 2", d["message"])
        d = write('The manager admitted "we fired him for complaining." Doc. 12 at 2.\n')
        self.assertEqual(d["action"], "block")
        self.assertIn("not found", d["message"])


if __name__ == "__main__":
    unittest.main()

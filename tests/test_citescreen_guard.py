"""The offline cite screen in the Hermes plugin: the citation block under each reply, /matter check
and the matter_cite_check tool.

CourtListener answers are synthetic JSON shaped like the citation-lookup API, from a fake adapter.
Case names are invented. No test reaches the network.
"""
import importlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import cases  # noqa: E402

PKG = "corpus_open_hermes_test"  # the same package name test_hermes_plugin loads, so one copy loads


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
screen = importlib.import_module(PKG + ".matterkit.citescreen")

DRAFT_REL = "Drafts/response_draft_synthetic.md"
with open(os.path.join(KIT, "demo-matter", DRAFT_REL), encoding="utf-8") as _f:
    DRAFT = _f.read()
HARLOW = "812 F.4th 1099"
SENTENCE = screen.screen_text(DRAFT)[HARLOW].text


def no_network(*a, **k):
    raise AssertionError("a test reached the network")


class FakeCourtListener:
    """The adapter's lookup(): one answer per payload line, by `answers` (cite -> (status, clusters))."""

    def __init__(self, answers):
        self.answers, self.sent = answers, 0

    def lookup(self, payloads):
        out = []
        for p in payloads:
            batch = []
            for cite, (status, clusters) in self.answers.items():
                if cite in p.split("\n"):
                    start = p.index(cite)
                    batch.append({"citation": cite, "normalized_citations": [cite], "start_index": start,
                                  "end_index": start + len(cite), "status": status, "clusters": clusters})
            out.append(batch)
            self.sent += 1
        return out, None


def opinion(name, filed):
    return {"id": 4242, "absolute_url": "/opinion/4242/x/", "case_name": name, "date_filed": filed}


class GuardCase(unittest.TestCase):
    def setUp(self):
        p = mock.patch("urllib.request.urlopen", no_network)
        p.start()
        self.addCleanup(p.stop)

    def rows(self, check):
        return {r.cite: r for r in check.rows if r.kind == "case"}

    def harlow(self, check):
        (r,) = [r for c, r in self.rows(check).items() if HARLOW in c]
        return r

    def with_courtlistener(self, answers):
        fake = FakeCourtListener(answers)
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "matter")
            with redirect_stdout(io.StringIO()):
                cases.init(md)
            check = guard.check_text(DRAFT, matter_dir=md, courtlistener_token="tok-fake",
                                     cl_factory=lambda *_a: fake, corpus_factory=lambda *_a: None)
        self.assertEqual(fake.sent, 1)
        return check


class TestNoLookup(GuardCase):
    def assert_flagged(self, check):
        r = self.harlow(check)
        self.assertEqual(r.outcome, "NO SUCH VOLUME")
        self.assertEqual(r.detail, SENTENCE)
        self.assertIn(r, check.problems)
        iqbal = [r for c, r in self.rows(check).items() if "556 U.S. 662" in c][0]
        self.assertEqual(iqbal.outcome, "NOT LOOKED UP")

    def test_lookups_off(self):
        self.assert_flagged(guard.check_text(DRAFT, lookup=False))

    def test_no_case_folder(self):
        self.assert_flagged(guard.check_text(DRAFT))

    def test_no_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "matter")
            with redirect_stdout(io.StringIO()):
                cases.init(md)
            check = guard.check_text(DRAFT, matter_dir=md, courtlistener_token=None,
                                     corpus_factory=lambda *_a: None)
        self.assert_flagged(check)

    def test_the_block_prints_it_as_a_problem_first(self):
        block = guard.render(guard.check_text(DRAFT, lookup=False))
        lines = block.splitlines()
        self.assertIn("**Citation check: 1 of 3 to check by hand**", lines)
        first = [ln for ln in lines if ln.startswith("- ")][0]
        self.assertEqual(first, f"- ⚠ **NO SUCH VOLUME** · Harlow v. Demo Freight Lines, Inc., {HARLOW}: {SENTENCE}")
        self.assertNotIn("fake", block.lower())

    def test_a_year_that_does_not_fit(self):
        check = guard.check_text("See Smith v. Jones, 12 F.4th 34, 36 (5th Cir. 1998).", lookup=False)
        (r,) = check.rows
        self.assertEqual(r.outcome, "YEAR DOES NOT FIT")
        self.assertIn("F.4th began in 2021, so a case in it cannot be from 1998", r.detail)

    def test_a_possible_cite_is_unchanged(self):
        check = guard.check_text("See Smith v. Jones, 123 F.4th 456 (5th Cir. 2024).", lookup=False)
        self.assertEqual([(r.outcome, r.detail) for r in check.rows], [("NOT LOOKED UP", "not looked up")])
        self.assertEqual(check.problems, [])

    def test_a_failing_screen_leaves_the_check_as_it_was(self):
        with mock.patch.object(screen, "screen_text", side_effect=RuntimeError("table broke")):
            check = guard.check_text(DRAFT, lookup=False)
        self.assertEqual(self.harlow(check).outcome, "NOT LOOKED UP")


class TestWithCourtListener(GuardCase):
    def test_found_wins_and_the_table_is_logged_as_wrong(self):
        with self.assertLogs(level="WARNING") as logs:
            check = self.with_courtlistener(
                {HARLOW: (200, [opinion("Harlow v. Demo Freight Lines, Inc.", "2023-03-01")])})
        r = self.harlow(check)
        self.assertEqual(r.outcome, "FOUND")
        self.assertNotIn("F.4th has no volume", r.detail)
        self.assertTrue(any(HARLOW in m and "citescreen.py" in m for m in logs.output), logs.output)

    def test_name_differs_wins_too(self):
        with self.assertLogs(level="WARNING"):
            check = self.with_courtlistener({HARLOW: (200, [opinion("Kappa v. Lambda", "2023-03-01")])})
        self.assertEqual(self.harlow(check).outcome, "NAME DIFFERS")

    def test_not_found_keeps_not_found_and_adds_the_sentence(self):
        check = self.with_courtlistener({HARLOW: (404, [])})
        r = self.harlow(check)
        self.assertEqual(r.outcome, "NOT FOUND")
        self.assertTrue(r.detail.startswith("CourtListener has no opinion at this cite."), r.detail)
        self.assertTrue(r.detail.endswith(SENTENCE), r.detail)

    def test_found_with_a_different_year_logs_nothing(self):
        # The draft's year is off, not the table: CourtListener's own date warning says so.
        text = "See Smith v. Jones, 12 F.4th 34 (5th Cir. 1998)."
        fake = FakeCourtListener({"12 F.4th 34": (200, [opinion("Smith v. Jones", "2021-08-01")])})
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "matter")
            with redirect_stdout(io.StringIO()):
                cases.init(md)
            with self.assertNoLogs(level="WARNING"):
                check = guard.check_text(text, matter_dir=md, courtlistener_token="tok-fake",
                                         cl_factory=lambda *_a: fake)
        (r,) = check.rows
        self.assertEqual(r.outcome, "FOUND")
        self.assertIn("the draft gives 1998", r.detail)


class Ctx:
    def __init__(self, settings):
        self.settings = dict(settings)

    def register_tool(self, *a, **k):
        pass

    def register_hook(self, *a, **k):
        pass

    def register_command(self, *a, **k):
        pass

    def get_config(self, key, default=None):
        return self.settings.get(key, default)


class TestPlugin(GuardCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "matter")
        with redirect_stdout(io.StringIO()):
            cases.init(self.dir)
        os.makedirs(os.path.join(self.dir, "Drafts"))
        shutil.copy(os.path.join(KIT, "demo-matter", DRAFT_REL), os.path.join(self.dir, DRAFT_REL))
        env = {k: v for k, v in os.environ.items() if k != "COURTLISTENER_TOKEN"}
        env["MATTER_KIT_CONFIG"] = os.path.join(tmp.name, "config", "matter-kit")
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def plugin(self, **settings):
        return hp.Plugin(Ctx(dict(matter_dir=self.dir, **settings)), secret=lambda _name: None)

    def test_matter_check_with_lookups_off(self):
        out = self.plugin(citation_guard="local").command(f"check {DRAFT_REL}")
        self.assertIn(f"- ⚠ **NO SUCH VOLUME** · Harlow v. Demo Freight Lines, Inc., {HARLOW}: {SENTENCE}", out)
        self.assertIn("**Citation check: 1 of 3 to check by hand**", out)

    def test_matter_check_with_no_key(self):
        out = self.plugin().command(f"check {DRAFT_REL}")
        self.assertIn(f"**NO SUCH VOLUME** · Harlow v. Demo Freight Lines, Inc., {HARLOW}", out)

    def test_the_tool_counts_it_as_a_problem(self):
        res = self.plugin(citation_guard="local").tools.matter_cite_check({"path": DRAFT_REL})
        self.assertEqual((res["problems"], res["cites"]), (1, 3))
        self.assertIn("NO SUCH VOLUME", res["report"])

    def test_the_demo_message_says_the_made_up_case_needs_no_lookup(self):
        out = hp.Plugin(Ctx({}), secret=lambda _name: None).command(
            "demo " + os.path.join(os.path.dirname(self.dir), "demo"))
        self.assertIn("finds all three, the made-up case even before case law lookups are on (/matter grant)",
                      out)

    def test_the_block_under_a_reply(self):
        out = self.plugin(citation_guard="local").on_reply(response_text=DRAFT)
        self.assertIn(f"**NO SUCH VOLUME** · Harlow v. Demo Freight Lines, Inc., {HARLOW}", out)


if __name__ == "__main__":
    unittest.main()

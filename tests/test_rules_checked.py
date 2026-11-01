"""Rules packs: recording a check of the court's website (`matter rules checked`).

How this could fail, and the test that would catch it:
- a recorded check silences a pack for good, or for longer than its window -> TestStatus
- a check that found an amendment reads as current -> test_a_check_that_found_an_amendment_stays_stale
- a check for one version vouches for another -> test_a_check_of_an_older_version_does_not_vouch_for_a_new_one
- a check vouches for an undated pack -> test_an_undated_pack_stays_unknown
- a bad check is half written (rules.json without its event, or the reverse) -> TestRefusals
- a source that is unregistered or changed since registration is accepted -> TestRefusals
- an older rules.json without checks reads differently -> test_rules_json_without_checks_is_unchanged

Synthetic rules and synthetic matters only. Run: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_rules_attach import LOCAL, Base, make_pack  # noqa: E402

NOTE = "Rules check. The court's page still links the rules effective 2030-01-01. No amendment found.\n"


class CheckedBase(Base):
    def setUp(self):
        super().setUp()
        self.attach(self.local)  # current as of 2030-01-01, 365-day review window
        self.note = os.path.join(self.dir, "Research", "Rules", "check.md")
        with open(self.note, "w", encoding="utf-8") as f:
            f.write(NOTE)
        self.cli("ingest", self.dir, self.note)

    def checked(self, *extra, pack="example-local", today="2031-06-01", check=True):
        return self.cli("rules", "checked", self.dir, pack, "--author", "agent:test-model",
                        "--directed-by", "Pat Example", "--today", today, *extra, check=check)

    def stale(self, today):
        return self.cli("rules", "stale", self.dir, "--today", today, check=False)

    def events(self):
        return self.rows("SELECT text, actors, sources, occurred_on FROM events WHERE text LIKE '%checked on%'")


class TestStatus(CheckedBase):
    def test_a_check_that_found_nothing_holds_until_its_next_review(self):
        self.checked("--on", "2031-05-20", "--found", "none", "--source", "Research/Rules/check.md",
                     "--next-review", "2031-09-01")
        before = self.stale("2031-06-01")
        self.assertEqual(before.returncode, 0, before.stdout)
        self.assertIn("[CHECKED] example-local@2030-01-01", before.stdout)
        self.assertIn("current as of 2030-01-01", before.stdout)
        self.assertIn("next review by 2031-09-01", before.stdout)
        on_the_day = self.stale("2031-09-01")
        self.assertEqual(on_the_day.returncode, 1)
        self.assertIn("[STALE] example-local@2030-01-01", on_the_day.stdout)
        self.assertIn("last checked 2031-05-20", on_the_day.stdout)

    def test_without_a_next_review_the_window_runs_from_the_check(self):
        self.checked("--on", "2031-05-20", "--found", "none", "--source", "Research/Rules/check.md")
        self.assertIn("[CHECKED]", self.stale("2032-05-18").stdout)  # 365 days on, 2032 being a leap year
        self.assertIn("[STALE]", self.stale("2032-05-19").stdout)

    def test_a_next_review_later_than_the_window_does_not_extend_it(self):
        self.checked("--on", "2031-05-20", "--found", "none", "--source", "Research/Rules/check.md",
                     "--next-review", "2035-01-01")
        self.assertIn("[STALE]", self.stale("2032-05-20").stdout)

    def test_list_and_show_say_checked(self):
        self.checked("--on", "2031-05-20", "--found", "none", "--source", "Research/Rules/check.md")
        listed = self.cli("rules", "list", self.dir, "--today", "2031-06-01").stdout
        self.assertIn("current as of 2030-01-01 [CHECKED]", listed)
        shown = self.cli("rules", "show", self.dir, "L.U.Civ.R. 7(b)(4)", "--today", "2031-06-01").stdout
        self.assertIn("[CHECKED: current as of 2030-01-01; no amendment found when checked on 2031-05-20", shown)
        self.assertIn("A response is due fourteen days", shown)

    def test_a_check_that_found_an_amendment_stays_stale(self):
        self.checked("--on", "2030-05-01", "--found", "amended", "--source", "Research/Rules/check.md",
                     today="2030-06-01")
        out = self.stale("2030-06-01")  # inside the review window, but the check found newer rules
        self.assertEqual(out.returncode, 1)
        self.assertIn("[STALE] example-local@2030-01-01", out.stdout)
        self.assertIn("found an amendment; attach the new version", out.stdout)

    def test_a_check_of_an_older_version_does_not_vouch_for_a_new_one(self):
        self.checked("--on", "2031-05-20", "--found", "none", "--source", "Research/Rules/check.md")
        newer = make_pack(self._tmp.name, "local-pack-2", LOCAL + "Rule 16.    CONFERENCES\nHeld early.\n",
                          pack_id="example-local", version="2030-02-01", title="Example Local Rules",
                          cite_prefix="L.U.Civ.R.", current_as_of="2030-02-01")
        self.attach(newer, "--replace")
        out = self.stale("2031-06-01")
        self.assertIn("[STALE] example-local@2030-02-01", out.stdout)

    def test_an_undated_pack_stays_unknown(self):
        undated = make_pack(self._tmp.name, "undated", LOCAL, pack_id="undated", version="1", title="Undated",
                            cite_prefix="L.U.Crim.R.")
        self.attach(undated)
        self.checked("--on", "2031-05-20", "--found", "none", "--source", "Research/Rules/check.md", pack="undated")
        out = self.stale("2031-06-01")
        self.assertIn("[UNKNOWN] undated@1", out.stdout)
        self.assertEqual(out.returncode, 1)

    def test_a_check_dated_after_the_reading_date_is_ignored(self):
        self.checked("--on", "2031-05-20", "--found", "none", "--source", "Research/Rules/check.md")
        self.assertIn("[STALE]", self.stale("2031-05-19").stdout)

    def test_stale_says_it_checked_no_website_itself(self):
        self.checked("--on", "2031-05-20", "--found", "none", "--source", "Research/Rules/check.md")
        out = self.stale("2031-06-01").stdout
        self.assertIn("This command checked no website", out)


class TestRecord(CheckedBase):
    def test_the_check_is_recorded_with_its_sources_and_an_event(self):
        out = self.checked("--on", "2031-05-20", "--found", "none", "--source", "Research/Rules/check.md@para 1",
                           "--next-review", "2031-09-01")
        self.assertIn("example-local@2030-01-01 checked on 2031-05-20: no amendment found", out.stdout)
        [c] = self.attachments()["checks"]
        sha = self.rows("SELECT sha256 FROM documents WHERE rel_path='Research/Rules/check.md'")[0][0]
        self.assertEqual(c["sources"], [{"ref": "Research/Rules/check.md@para 1", "sha256": sha}])
        self.assertEqual((c["pack_id"], c["version"], c["checked_on"], c["found"], c["next_review"]),
                         ("example-local", "2030-01-01", "2031-05-20", "none", "2031-09-01"))
        self.assertEqual(c["recorded_by"], "agent:test-model; directed-by:Pat Example")
        [(text, actors, sources, occurred)] = self.events()
        self.assertIn("next review by 2031-09-01", text)
        self.assertEqual(occurred, "2031-05-20")
        self.assertIn("Research/Rules/check.md@para 1", sources)

    def test_recording_the_same_check_twice_changes_nothing(self):
        args = ("--on", "2031-05-20", "--found", "none", "--source", "Research/Rules/check.md")
        self.checked(*args)
        again = self.checked(*args)
        self.assertIn("already recorded; nothing changed", again.stdout)
        self.assertEqual(len(self.attachments()["checks"]), 1)
        self.assertEqual(len(self.events()), 1)

    def test_rules_json_without_checks_is_unchanged(self):
        self.assertNotIn("checks", self.attachments())
        self.assertIn("[STALE]", self.stale("2031-06-01").stdout)


class TestRefusals(CheckedBase):
    def assert_refused(self, out, words):
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("refused:", out.stderr)
        self.assertIn(words, out.stderr)
        self.assertNotIn("Traceback", out.stderr)

    def assert_nothing_written(self, before_json):
        with open(os.path.join(self.dir, ".matter", "rules.json"), encoding="utf-8") as f:
            self.assertEqual(f.read(), before_json)
        self.assertEqual(self.events(), [])

    def test_bad_checks_are_refused_and_write_nothing(self):
        with open(os.path.join(self.dir, ".matter", "rules.json"), encoding="utf-8") as f:
            before = f.read()
        src = ("--source", "Research/Rules/check.md")
        cases = [
            (("--on", "2031-06-02", "--found", "none", *src), {}, "after today"),
            (("--on", "2029-12-31", "--found", "none", *src), {}, "before the pack's current-as-of date"),
            (("--on", "2031-13-01", "--found", "none", *src), {}, "YYYY-MM-DD"),
            (("--on", "2031-05-20", "--found", "none", "--next-review", "2031-05-20", *src), {}, "after --on"),
            (("--on", "2031-05-20", "--found", "amended", "--next-review", "2031-09-01", *src), {},
             "only to a check that found no amendment"),
            (("--on", "2031-05-20", "--found", "none", *src), {"pack": "no-such-pack"}, "no attached pack"),
            (("--on", "2031-05-20", "--found", "none", "--source", "Research/Rules/missing.md"), {},
             "not registered"),
        ]
        for args, kw, words in cases:
            with self.subTest(words=words):
                self.assert_refused(self.checked(*args, check=False, **kw), words)
                self.assert_nothing_written(before)

    def test_a_source_changed_since_it_was_registered_is_refused(self):
        with open(self.note, "a", encoding="utf-8") as f:
            f.write("edited afterwards\n")
        out = self.checked("--on", "2031-05-20", "--found", "none", "--source", "Research/Rules/check.md",
                           check=False)
        self.assert_refused(out, "changed since it was registered")
        self.assertNotIn("checks", self.attachments())

    def test_a_pack_that_cannot_be_used_is_refused(self):
        md = os.path.join(self.dir, "Research", "Rules", "example-local_2030-01-01", "rules.md")
        with open(md, "a", encoding="utf-8") as f:
            f.write("edited\n")
        out = self.checked("--on", "2031-05-20", "--found", "none", "--source", "Research/Rules/check.md",
                           check=False)
        self.assert_refused(out, "cannot be used")

    def test_a_hand_edited_check_is_refused_when_read(self):
        att = self.attachments()
        att["checks"] = [{"pack_id": "example-local", "found": "maybe"}]
        self.write_attachments(att)
        out = self.stale("2031-06-01")
        self.assert_refused(out, "each check needs")


if __name__ == "__main__":
    unittest.main()

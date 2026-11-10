"""The offline cite screen (matterkit/citescreen.py): a case cite that cannot exist as written.

Every check here runs on the reporter table alone. No test opens a network connection. Case names
in the made-up cites are invented.
"""
import datetime as dt
import os
import re
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import citelookup, citescreen  # noqa: E402

TODAY = citescreen.TABLE_CHECKED
HARLOW = ("A falsified personnel file alone defeats dismissal. Harlow v. Demo Freight Lines, Inc., "
          "812 F.4th 1099, 1104 (5th Cir. 2023).")
# The impossible cites the kit's own texts plant on purpose: cite -> (year written, finding). Every
# other case cite in the practice case, the tests and the eval fixtures is real or at least possible,
# so none of them may be flagged.
KNOWN_MADE_UP = {
    "812 F.4th 1099": ("2023", "volume-past-ceiling"),  # the practice case's invented Harlow v. Demo Freight Lines
    "999 U.S. 1": ("2020", "volume-past-ceiling"),  # tests/test_saved_lookups.py: a cite CourtListener lacks
    # tests/test_cite_lookup.py test_year_mismatch_is_a_warning writes "(5th Cir. 2019)", before F.4th
    # began, so CourtListener's 2024 date shows as a warning.
    "123 F.4th 456": ("2019", "year-before-start"),
}


def screen(vol, rep, page=1, year=None, today=TODAY):
    return citescreen.screen(vol, rep, page, year, today=today)


def row(rep):
    return citescreen.TABLE[citescreen.key(rep)]


class TestVolumes(unittest.TestCase):
    def test_a_closed_series_ends_at_its_last_volume(self):
        self.assertIsNone(screen(999, "F.3d"))
        f = screen(1000, "F.3d")
        self.assertEqual((f.code, f.label), ("volume-past-end", citescreen.NO_SUCH_VOLUME))
        self.assertIn("F.3d has no volume 1000", f.text)
        self.assertIn("ended at volume 999 in 2021", f.text)

    def test_every_closed_series_boundary(self):
        closed = [s for s in citescreen.SERIES if s.last_volume]
        self.assertGreaterEqual(len(closed), 15)
        for s in closed:
            with self.subTest(s.name):
                self.assertIsNone(screen(s.last_volume, s.name))
                self.assertEqual(screen(s.last_volume + 1, s.name).code, "volume-past-end")

    def test_an_open_series_ceiling_boundary(self):
        for s in (s for s in citescreen.SERIES if s.count):
            with self.subTest(s.name):
                top = citescreen.ceiling(s.name, TODAY)
                self.assertIsNone(screen(top, s.name))
                f = screen(top + 1, s.name)
                self.assertEqual((f.code, f.label), ("volume-past-ceiling", citescreen.NO_SUCH_VOLUME))

    def test_the_ceiling_is_the_count_plus_the_allowance_for_each_year_and_one_more(self):
        s = row("F.4th")
        years = (TODAY - s.as_of).days / 365.25
        self.assertEqual(citescreen.ceiling("F.4th", TODAY), s.count + int(s.allowance * (years + 1)))
        # Even on the day a count was taken, a full year's allowance stands above it, for advance sheets
        # numbered past the last bound volume and for a count that lagged.
        for s in (s for s in citescreen.SERIES if s.count):
            with self.subTest(s.name):
                self.assertGreaterEqual(citescreen.ceiling(s.name, s.as_of), s.count + s.allowance)

    def test_a_series_with_no_confirmed_count_has_no_ceiling(self):
        for name in ("B.R.", "F.R.D.", "S.W.3d", "So. 3d"):
            with self.subTest(name):
                self.assertIsNone(citescreen.ceiling(name, TODAY))
                self.assertIsNone(screen(9999, name))

    def test_volume_zero_and_page_zero(self):
        f = screen(0, "F.3d", 10)
        self.assertEqual((f.code, f.label), ("volume-zero", citescreen.NO_SUCH_VOLUME))
        self.assertIn("F.3d has no volume 0", f.text)
        f = screen(10, "U.S.", 0)
        self.assertEqual((f.code, f.label), ("page-zero", citescreen.NO_SUCH_PAGE))
        self.assertIn("U.S. has no page 0", f.text)
        self.assertEqual(screen("00", "F.2d", 5).code, "volume-zero")
        self.assertIsNone(screen(1, "F.3d", 1))

    def test_any_spelling_the_parser_reads(self):
        self.assertEqual(screen(1000, "F. 3d").code, "volume-past-end")
        self.assertEqual(screen(862, "Fed. Appx.").code, "volume-past-end")
        self.assertIsNone(screen(861, "F. App'x"))
        self.assertEqual(screen("٨١٢", "F.4th").code, "volume-past-ceiling")  # other scripts' digits


class TestStaleTable(unittest.TestCase):
    def test_ten_years_on_a_volume_inside_the_allowance_is_not_flagged(self):
        later = TODAY.replace(year=TODAY.year + 10)
        for s in (s for s in citescreen.SERIES if s.count):
            with self.subTest(s.name):
                grown = s.count + 10 * s.allowance  # ten years of growth at the full allowance
                self.assertIsNone(screen(grown, s.name, today=later))
                self.assertGreater(citescreen.ceiling(s.name, later), grown)
        self.assertIsNone(screen(812, "F.4th", today=later))  # Harlow's volume: possible by then
        self.assertIsNone(screen(1, "F.4th", year=TODAY.year + 10, today=later))

    def test_a_clock_set_before_the_table_counts_as_the_table_date(self):
        past = dt.date(2001, 1, 1)
        self.assertEqual(citescreen.ceiling("F.4th", past), citescreen.ceiling("F.4th", TODAY))
        self.assertIsNone(screen(1, "F.4th", year=TODAY.year, today=past))

    def test_today_defaults_to_the_real_date(self):
        self.assertEqual(citescreen.screen(812, "F.4th", 1099, "2023").code, "volume-past-ceiling")
        self.assertGreaterEqual(citescreen.ceiling("F.4th"), citescreen.ceiling("F.4th", TODAY))

    def test_every_allowance_is_at_least_twice_the_growth_the_row_states(self):
        for s in (s for s in citescreen.SERIES if s.count):
            with self.subTest(s.name):
                self.assertGreaterEqual(s.allowance, 2 * s.growth)
                self.assertLessEqual(s.as_of, TODAY)


class TestYears(unittest.TestCase):
    def test_before_the_series_began(self):
        # F.4th began in 2021. A year before that is allowed, for an opinion published late.
        self.assertIsNone(screen(5, "F.4th", year=2020))
        f = screen(5, "F.4th", year=2019)
        self.assertEqual((f.code, f.label), ("year-before-start", citescreen.YEAR_DOES_NOT_FIT))
        self.assertIn("F.4th began in 2021", f.text)
        self.assertIn("2019", f.text)

    def test_more_than_one_year_after_a_closed_series_ended(self):
        self.assertIsNone(screen(999, "F.2d", year=1994))
        f = screen(999, "F.2d", year=1995)
        self.assertEqual((f.code, f.label), ("year-after-end", citescreen.YEAR_DOES_NOT_FIT))
        self.assertIn("F.2d ended in 1993", f.text)
        self.assertIn("1995", f.text)

    def test_in_the_future(self):
        self.assertIsNone(screen(5, "F.4th", year=TODAY.year))
        f = screen(5, "F.4th", year=TODAY.year + 1)
        self.assertEqual((f.code, f.label), ("year-future", citescreen.YEAR_DOES_NOT_FIT))
        self.assertIn(str(TODAY.year + 1), f.text)
        self.assertEqual(screen(5, "U.S.", year="2031").code, "year-future")

    def test_an_open_series_has_no_end_year(self):
        self.assertIsNone(screen(5, "F.4th", year=TODAY.year))
        self.assertIsNone(screen(500, "U.S.", year=1754))

    def test_a_reprint_series_starts_with_the_reports_it_reprints(self):
        # 1 L. Ed. reprints 1 U.S. (1 Dall.), whose first cases are from 1754. A real parallel cite:
        # Respublica v. De Longchamps, 1 U.S. 111, 1 L. Ed. 59 (1784).
        self.assertIsNone(screen(1, "L. Ed.", 59, year=1784))
        self.assertIsNone(screen(1, "L. Ed.", 11, year=1754))
        self.assertEqual(citescreen.screen_text(
            "Respublica v. De Longchamps, 1 U.S. 111, 1 L. Ed. 59 (1784).", today=TODAY), {})
        self.assertEqual(row("L. Ed.").first_year, row("U.S.").first_year)
        self.assertEqual(screen(1, "L. Ed.", 59, year=1752).code, "year-before-start")

    def test_no_year_checks_only_the_volume(self):
        self.assertIsNone(screen(5, "F.4th"))
        self.assertIsNone(screen(5, "F.4th", year=None))
        self.assertIsNone(screen(5, "F.4th", year="n.d."))

    def test_the_volume_is_checked_before_the_year(self):
        self.assertEqual(screen(1000, "F.3d", year=1850).code, "volume-past-end")


class TestUnknownReporter(unittest.TestCase):
    def test_returns_nothing_whatever_the_numbers(self):
        for rep in ("Fed. Cl.", "F.5th", "Cal. Rptr.", "WL", "A.", "P.", ""):
            with self.subTest(rep):
                self.assertIsNone(screen(0, rep, 0, year=3000))
                self.assertIsNone(screen(99999, rep))


class TestParenYear(unittest.TestCase):
    def test_a_court_and_date_parenthetical(self):
        for paren, year in (("5th Cir. 2023", 2023), ("2009", 2009), ("S.D. Miss. Jan. 5, 2014", 2014),
                            ("Tex. App.—Dallas 1995", 1995), ("Ill. App. Ct. 1st Dist. 2001", 2001),
                            ("Tex. App.—Houston [14th Dist.] 2003", 2003), ("N.Y. App. Div. 2d Dep't 2010", 2010),
                            ("Bankr. S.D.N.Y. Sept. 30, 2019", 2019), ("D.C. Cir. 2001", 2001)):
            with self.subTest(paren):
                self.assertEqual(citescreen.paren_year(paren), year)

    def test_an_explanatory_parenthetical_gives_no_year(self):
        for paren in ("applying § 1983", "citing 29 U.S.C. § 2615", "quoting Rule 1983", "per curiam 2001",
                      "en banc", "", None, "2023 WL 1234567", "No. 1999"):
            with self.subTest(paren):
                self.assertIsNone(citescreen.paren_year(paren))


class TestScreenText(unittest.TestCase):
    def test_the_practice_case_harlow_cite(self):
        found = citescreen.screen_text(HARLOW, today=TODAY)
        self.assertEqual(list(found), ["812 F.4th 1099"])
        f = found["812 F.4th 1099"]
        self.assertEqual(f.label, citescreen.NO_SUCH_VOLUME)
        s = row("F.4th")
        self.assertEqual(f.text, f"F.4th has no volume 812 (it had about {s.count} volumes in "
                                 f"{citescreen.month_year(s.as_of)}). This cite is likely made up or mistyped. "
                                 "Check it by hand.")
        self.assertNotIn("fake", f.text.lower())

    def test_keys_are_the_cites_citelookup_sends(self):
        text = (HARLOW + " Ashcroft v. Iqbal, 556 U.S. 662, 678 (2009). Doe v. Roe, 0 F. 3d 1 (1990). "
                "Roe v. Doe, ١٠٠٠ F.3d ٥ (2001). Smith v. Jones, 2021 WL 123456 (5th Cir. 2021).")
        found = citescreen.screen_text(text, today=TODAY)
        self.assertEqual(sorted(found), ["0 F.3d 1", "1000 F.3d 5", "812 F.4th 1099"])
        self.assertLessEqual(set(found), {c.sent for c in citelookup.draft_cites(text)})

    def test_a_section_number_in_a_parenthetical_is_not_a_year(self):
        text = ("Smith v. Jones, 500 F.3d 100 (applying § 1983). Roe v. Doe, 400 F.2d 10 (citing 29 U.S.C. "
                "§ 2615).")
        self.assertEqual(citescreen.screen_text(text, today=TODAY), {})

    def test_a_year_that_does_not_fit(self):
        text = "Smith v. Jones, 12 F.4th 34, 36 (5th Cir. 1998); Doe v. Roe, 900 F.2d 1 (9th Cir. 2005)."
        found = citescreen.screen_text(text, today=TODAY)
        self.assertEqual({k: f.code for k, f in found.items()},
                         {"12 F.4th 34": "year-before-start", "900 F.2d 1": "year-after-end"})

    def test_the_first_finding_for_a_cite_is_kept(self):
        text = "Smith v. Jones, 12 F.4th 34 (2022). Later: Smith v. Jones, 12 F.4th 34 (1990)."
        self.assertEqual(citescreen.screen_text(text, today=TODAY)["12 F.4th 34"].code, "year-before-start")

    def test_no_cites_and_no_text(self):
        self.assertEqual(citescreen.screen_text("", today=TODAY), {})
        self.assertEqual(citescreen.screen_text("Under 42 U.S.C. § 1983, see Id.", today=TODAY), {})


def _texts():
    """Every text file in the practice case and the eval fixtures, and every test module but this
    one and the guard's (the fixture strings live in the tests), as (path, text)."""
    for top in ("demo-matter", "evals", "tests"):
        for folder, dirs, files in os.walk(os.path.join(KIT, top)):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".matter")]
            for name in sorted(files):
                if top == "tests" and (not name.endswith(".py") or name.startswith("test_citescreen")):
                    continue
                path = os.path.join(folder, name)
                try:
                    with open(path, encoding="utf-8") as f:
                        yield path, f.read()
                except (UnicodeDecodeError, OSError):
                    continue


class TestNoFalseAlarm(unittest.TestCase):
    def test_only_the_planted_made_up_cites_are_flagged(self):
        seen, flagged = set(), {}
        for path, text in _texts():
            seen.update(c.sent for c in citelookup.draft_cites(text))
            for sent, f in citescreen.screen_text(text, today=TODAY).items():
                flagged.setdefault(sent, (os.path.relpath(path, KIT), f.code))
        self.assertGreater(len(seen), 40)  # the sweep reached the fixtures (55 distinct cites at writing)
        self.assertEqual({k: code for k, (_path, code) in flagged.items()},
                         {k: code for k, (_year, code) in KNOWN_MADE_UP.items()}, flagged)

    def test_the_known_made_up_cites_are_flagged_today(self):
        for sent, (year, code) in KNOWN_MADE_UP.items():
            vol, rest = sent.split(" ", 1)
            rep, page = rest.rsplit(" ", 1)
            with self.subTest(sent):
                self.assertEqual(screen(vol, rep, page, year).code, code)


class TestTable(unittest.TestCase):
    def test_every_row_is_a_reporter_the_parser_reads(self):
        canonical = set(citelookup.CANONICAL_REPORTERS.values())
        for s in citescreen.SERIES:
            with self.subTest(s.name):
                self.assertIn(s.name, canonical)
        self.assertEqual(len({citescreen.key(s.name) for s in citescreen.SERIES}), len(citescreen.SERIES))

    def test_every_row_is_complete(self):
        for s in citescreen.SERIES:
            with self.subTest(s.name):
                self.assertTrue(s.source.strip())
                self.assertGreaterEqual(s.first_year, 1700)
                if s.last_year is not None:
                    self.assertLessEqual(s.first_year, s.last_year)
                    self.assertTrue(s.last_volume)  # a closed series names its last volume
                    self.assertIsNone(s.count)
                if s.count is not None:
                    self.assertIsNone(s.last_year)
                    self.assertTrue(s.as_of and s.allowance and s.growth)

    def test_the_federal_reporters_come_first(self):
        names = [s.name for s in citescreen.SERIES]
        self.assertEqual(names[0], "U.S.")
        self.assertLess(names.index("B.R."), names.index("A.2d"))

    def test_the_module_stays_offline_and_outside_the_plugin(self):
        with open(os.path.join(KIT, "matterkit", "citescreen.py"), encoding="utf-8") as f:
            src = f.read()
        imports = set(re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, re.M))
        self.assertFalse({i for i in imports if i.split(".")[0] in
                          ("socket", "urllib", "http", "ssl", "requests", "hermes_plugin")}, imports)
        self.assertNotIn("hermes_plugin", src)


if __name__ == "__main__":
    unittest.main()

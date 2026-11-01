"""Trigger deadlines: counting days under Fed. R. Civ. P. 6(a)(1) and 6(d).

Design: docs/superpowers/specs/2026-11-01-trigger-deadlines-design.md, § 4. A wrong date here is a
missed court deadline, so each way the count could go wrong has a test:
- a weekend or holiday rollover is miscounted -> TestRollover
- 3 days are added for e-mail or ECF service -> test_electronic_and_hand_service_add_nothing
- the 3 days are added before the 6(a) rollover instead of after -> test_three_days_are_added_after_the_rollover
- the holiday list is wrong or missing a year -> TestFederalHolidays, test_a_year_with_no_recorded_state_list_is_refused
- an observed-day holiday decides the date silently -> test_an_observed_holiday_that_decides_the_date_says_so

Synthetic calendars only. Run: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from datetime import date

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import triggers  # noqa: E402

D = date.fromisoformat
YEARS = {2026: ("Example State", ["Research/state-holidays.txt@§ 1"]),
         2027: ("Example State", ["Research/state-holidays.txt@§ 1"])}


def cal(recorded=None, years=YEARS):
    return triggers.Calendar(recorded or {}, years)


def due(start, days, service="ecf", calendar=None):
    return triggers.count(D(start), days, service, calendar or cal())[0]


class TestRollover(unittest.TestCase):
    def test_the_trigger_day_is_not_counted_and_every_day_after_is(self):
        self.assertEqual(due("2026-11-02", 14), D("2026-11-16"))  # Monday to Monday

    def test_a_saturday_or_sunday_runs_to_monday(self):
        self.assertEqual(due("2026-11-02", 12), D("2026-11-16"))  # Saturday 11-14
        self.assertEqual(due("2026-11-02", 13), D("2026-11-16"))  # Sunday 11-15

    def test_a_monday_holiday_runs_to_tuesday(self):
        self.assertEqual(due("2026-08-24", 14), D("2026-09-08"))  # Labor Day 2026-09-07

    def test_a_count_that_crosses_the_year_end(self):
        self.assertEqual(due("2026-12-18", 14), D("2027-01-04"))  # New Year's Day (Fri), then the weekend

    def test_a_recorded_state_holiday_rolls_the_date(self):
        state = {D("2026-04-27"): ("Example State Day", "state")}
        self.assertEqual(due("2026-04-13", 14, calendar=cal(state)), D("2026-04-28"))
        self.assertEqual(due("2026-04-13", 14), D("2026-04-27"))

    def test_a_recorded_closure_rolls_the_date(self):
        closed = {D("2026-11-16"): ("Clerk's office closed by weather", "closure")}
        _, steps = triggers.count(D("2026-11-02"), 14, "ecf", cal(closed))
        self.assertEqual(_, D("2026-11-17"))
        self.assertTrue(any("6(a)(3)" in s for s in steps), steps)


class TestThreeDays(unittest.TestCase):
    def test_mail_clerk_and_consented_service_add_three_days(self):
        for service in ("mail", "clerk", "consented-other"):
            self.assertEqual(due("2026-11-02", 14, service), D("2026-11-19"), service)

    def test_electronic_and_hand_service_add_nothing(self):
        for service in ("email", "ecf", "hand", "none"):
            self.assertEqual(due("2026-11-02", 14, service), D("2026-11-16"), service)

    def test_three_days_are_added_after_the_rollover(self):
        # 6(a) ends Saturday 11-14 and runs to Monday 11-16; 3 days after that is Thursday 11-19,
        # not Tuesday 11-17 (Saturday plus 3)
        self.assertEqual(due("2026-11-02", 12, "mail"), D("2026-11-19"))

    def test_the_rollover_applies_again_after_the_three_days(self):
        self.assertEqual(due("2026-11-02", 10, "mail"), D("2026-11-16"))  # Thursday + 3 = Sunday
        self.assertEqual(due("2026-11-09", 14, "mail"), D("2026-11-27"))  # Monday + 3 = Thanksgiving


class TestSteps(unittest.TestCase):
    def test_the_steps_show_each_rule_and_date(self):
        d, steps = triggers.count(D("2026-11-02"), 12, "mail", cal())
        text = "\n".join(steps)
        for want in ("Monday 2026-11-02", "6(a)(1)(A)", "Saturday 2026-11-14", "6(a)(1)(C)",
                     "Monday 2026-11-16", "5(b)(2)(C)", "6(d)", "Thursday 2026-11-19", "6(a)(4)",
                     "5 U.S.C. § 6103", "Example State"):
            self.assertIn(want, text)

    def test_an_observed_holiday_that_decides_the_date_says_so(self):
        # July 4, 2026 is a Saturday, observed Friday July 3; a period ending July 3 runs to Monday July 6
        d, steps = triggers.count(D("2026-06-19"), 14, "ecf", cal())
        self.assertEqual(d, D("2026-07-06"))
        caveat = [s for s in steps if "statutory date" in s]
        self.assertTrue(caveat, steps)
        self.assertIn("Friday 2026-07-03", caveat[-1])  # the earlier, safe date if only July 4 counts

    def test_no_observed_caveat_when_it_changes_nothing(self):
        _, steps = triggers.count(D("2026-11-02"), 14, "ecf", cal())
        self.assertFalse([s for s in steps if "statutory date" in s])


class TestRefusals(unittest.TestCase):
    def test_a_year_with_no_recorded_state_list_is_refused(self):
        with self.assertRaises(triggers.TriggerError) as cm:
            triggers.count(D("2026-12-18"), 14, "ecf", cal(years={2026: YEARS[2026]}))
        self.assertIn("2027", str(cm.exception))

    def test_days_must_be_a_positive_whole_number(self):
        for days in (0, -7, 1.5, "7", True):
            with self.assertRaises(triggers.TriggerError):
                triggers.count(D("2026-11-02"), days, "ecf", cal())

    def test_an_unknown_service_is_refused(self):
        with self.assertRaises(triggers.TriggerError):
            triggers.count(D("2026-11-02"), 14, "fax", cal())


class TestFederalHolidays(unittest.TestCase):
    def test_2026(self):
        got = triggers.federal_holidays(2026)
        self.assertEqual(sorted(got), [D(s) for s in (
            "2026-01-01", "2026-01-19", "2026-02-16", "2026-05-25", "2026-06-19", "2026-07-03", "2026-07-04",
            "2026-09-07", "2026-10-12", "2026-11-11", "2026-11-26", "2026-12-25")])
        self.assertIn("observed", got[D("2026-07-03")])

    def test_weekend_holidays_are_observed_on_the_nearest_weekday(self):
        got = triggers.federal_holidays(2027)
        self.assertIn(D("2027-07-05"), got)   # July 4 is a Sunday: Monday
        self.assertIn(D("2027-12-24"), got)   # Christmas is a Saturday: Friday
        self.assertIn(D("2027-12-31"), got)   # New Year's Day 2028 is a Saturday: Friday Dec. 31, 2027
        self.assertIn("New Year", got[D("2027-12-31")])
        self.assertNotIn(D("2027-12-31"), triggers.federal_holidays(2028))


if __name__ == "__main__":
    unittest.main()

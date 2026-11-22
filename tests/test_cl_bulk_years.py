"""scripts/cl_bulk_years.py: per-court counts and years from CourtListener's public bulk files.

Synthetic rows only. The script's `stream` (curl and bzcat) is replaced, and Popen is made to fail, so no
request leaves the machine and nothing is downloaded.
Run: python3 -m unittest tests.test_cl_bulk_years -v
"""
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    spec = importlib.util.spec_from_file_location("cl_bulk_years", os.path.join(KIT, "scripts", "cl_bulk_years.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CLUSTERS = [["id", "date_filed", "date_filed_is_approximate", "docket_id"],
            ["1", "1852-07-01", "f", "10"],      # mo: the earliest dated opinion
            ["2", "2025-08-12", "False", "10"],  # mo: the newest
            ["3", "1700-01-01", "t", "11"],      # mo: approximate date, counted but not an earliest or a latest
            ["4", "0019-01-31", "f", "20"],      # nh: a mistyped year, kept as written
            ["5", "2026-09-24", "f", "20"],
            ["6", "2000-05-05", "f", "30"],      # a docket the dockets file never mentions
            ["7", "not a date", "f", "40"],      # counted, no usable date
            ["8", "2001-01-01", "f", "xx"],      # a bad docket id: skipped
            ["9", "2001-01-01"]]                 # a short row: skipped
DOCKETS = [["id", "court_id", "case_name"],
           ["10", "mo", "a"], ["11", "mo", "b"], ["20", "nh", "c"], ["40", "ca9", "d"], ["99", "zz", "never had opinions"]]


class TestScan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load()

    def courts(self):
        arrays = self.mod.scan_clusters(iter(CLUSTERS))
        return self.mod.join_dockets(iter(DOCKETS), *arrays)

    def test_counts_approximate_dates_and_the_earliest_and_latest_of_the_rest(self):
        mo = self.courts()["mo"]
        self.assertEqual(mo, {"count": 3, "approximate": 1, "earliest": "1852-07-01", "latest": "2025-08-12"})

    def test_a_year_below_1000_keeps_its_zeros(self):
        self.assertEqual(self.courts()["nh"]["earliest"], "0019-01-31")  # not "1901-31-"
        self.assertEqual(self.mod.format_date(190131), "0019-01-31")
        self.assertEqual(self.mod.format_date(20260930), "2026-09-30")
        self.assertIsNone(self.mod.format_date(0))

    def test_a_court_with_an_unreadable_date_still_counts_and_a_court_without_opinions_is_absent(self):
        courts = self.courts()
        self.assertEqual(courts["ca9"], {"count": 1, "approximate": 0, "earliest": None, "latest": None})
        self.assertNotIn("zz", courts)
        self.assertEqual(list(courts), sorted(courts))

    def test_the_counters_stop_at_16_bits(self):
        rows = [["id", "date_filed", "date_filed_is_approximate", "docket_id"]] + [["1", "2000-01-01", "f", "5"]] * 65_540
        arrays = self.mod.scan_clusters(iter(rows))
        out = self.mod.join_dockets(iter([["id", "court_id"], ["5", "big"]]), *arrays)
        self.assertEqual(out["big"]["count"], 65535)


class TestMain(unittest.TestCase):
    def test_writes_the_summary_without_touching_the_network(self):
        mod = load()
        files = {"opinion-clusters-2026-09-30.csv.bz2": CLUSTERS, "dockets-2026-09-30.csv.bz2": DOCKETS}
        asked = []

        def fake_stream(name):
            asked.append(name)
            return iter(files[name]), ()

        with tempfile.TemporaryDirectory() as d, mock.patch.object(mod, "stream", fake_stream), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("no process may start")), \
                mock.patch("builtins.print"):
            out = os.path.join(d, "years.json")
            self.assertEqual(mod.main(["2026-09-30", out]), 0)
            with open(out, encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(asked, ["opinion-clusters-2026-09-30.csv.bz2", "dockets-2026-09-30.csv.bz2"])
        self.assertEqual(data["source"], "CourtListener bulk data 2026-09-30 (opinion-clusters + dockets), Public Domain Mark")
        self.assertEqual(set(data["courts"]), {"mo", "nh", "ca9"})
        self.assertRegex(data["built_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")

    def test_usage_and_a_failed_stream_exit_with_a_message(self):
        mod = load()
        with self.assertRaises(SystemExit) as ctx:
            mod.main([])
        self.assertIn("usage", str(ctx.exception))

        class Dead:
            returncode = 22

            def wait(self):
                pass

        with self.assertRaises(SystemExit) as ctx:
            mod.finish([Dead()], "clusters")
        self.assertIn("clusters: stream failed", str(ctx.exception))

    def test_the_script_names_its_license_and_memory(self):
        with open(os.path.join(KIT, "scripts", "cl_bulk_years.py"), encoding="utf-8") as f:
            text = f.read()
        for needle in ("Public Domain Mark", "free of known copyright restrictions", "Memory: about 1 GB", "No API, no token"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()

"""authority check / authority import: record what source was opened, for which
pins, by whom; import an existing Markdown ledger only on --apply.

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import authority, briefcheck, store  # noqa: E402

CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("MATTER_AUTHOR", "MATTER_DIRECTED_BY")}
CLEAN_ENV["PATH"] = ""  # no pdftotext: deterministic

LEDGER = """# Authority ledger (synthetic)

## A. Rules

| Authority | Source opened | Proposition / exact language | Status | Notes / adverse |
|---|---|---|---|---|
| Fed. R. Civ. P. 15(a)(1)–(3) | official text (sha `abc`) | amend once as of course | V-LEAD | — |
| Example Code Ann. § 1-2-3 | search tool | officer may take bond | PARTIAL | **Version conflict** between two sources |
| Example Const. art. 3, § 29 | — | — | UNVERIFIED | fetch failed |

## B. Cases

| Case | Source opened by lead | Used for | Status | Treatment / limits / adverse |
|---|---|---|---|---|
| *Alpha v. Beta*, 500 U.S. 44, 55–58 (1991) | Official reporter PDF (sha `d7a3`) | 48-hour rule | V-LEAD (pins confirmed) | not a bail case |
| *Gamma v. Delta*, 874 F.3d 425 (5th Cir. 2017) | slip op. (sha `fd9d`) | blame shifting | V-LEAD (slip pages); **F.3d pins unconfirmed** | — |
| *Twombly*, 550 U.S. 544, 555, 570; *Iqbal*, 556 U.S. 662, 678 | W1 (mirror) | pleading standard | V-WORKER | standard cites |
"""


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "case")
        subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "init", self.dir],
                       check=True, capture_output=True, env=CLEAN_ENV)

    def cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *args],
                              capture_output=True, text=True, env=CLEAN_ENV)

    def rows(self):
        conn = store.connect_readonly(self.dir)
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM authorities ORDER BY citation")]
        finally:
            conn.close()

    def write(self, rel, text):
        p = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p


class TestAuthorityCheck(Base):
    def test_new_authority_recorded_as_source_checked_with_evidence(self):
        art = self.write("Research/authorities/dietz.txt", "Courts have inherent authority to manage dockets.")
        p = self.cli("authority", "check", self.dir, "Dietz v. Bouldin, 579 U.S. 40 (2016)",
                     "--source-url", "https://example.org/dietz.pdf", "--artifact", art,
                     "--pin", "45-47", "--quote", "inherent authority to manage dockets",
                     "--author", "agent:model-x", "--directed-by", "Pat Example")
        self.assertEqual(p.returncode, 0, p.stderr)
        [row] = self.rows()
        self.assertEqual(row["status"], "source-checked")
        ev = json.loads(row["check_evidence"])
        self.assertEqual(ev["pins"], ["45-47"])
        check = ev["checks"][0]
        self.assertEqual(check["checked_by"], "agent:model-x; directed-by:Pat Example")
        self.assertEqual(check["artifact_sha256"], _sha(art))
        self.assertIs(check["quote_found_in_artifact"], True)

    def test_quote_absent_from_artifact_refuses_and_writes_nothing(self):
        art = self.write("Research/authorities/dietz.txt", "Something else entirely.")
        p = self.cli("authority", "check", self.dir, "Dietz v. Bouldin, 579 U.S. 40 (2016)",
                     "--source-url", "https://example.org/dietz.pdf", "--artifact", art,
                     "--pin", "45", "--quote", "inherent authority to manage dockets")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.rows(), [])

    def test_second_check_on_same_authority_merges_pins_and_keeps_history(self):
        for pin in ("45", "52"):
            p = self.cli("authority", "check", self.dir, "579 U.S. 40", "--source-url", "https://example.org/d",
                         "--pin", pin)
            self.assertEqual(p.returncode, 0, p.stderr)
        [row] = self.rows()
        ev = json.loads(row["check_evidence"])
        self.assertEqual(ev["pins"], ["45", "52"])
        self.assertEqual(len(ev["checks"]), 2)

    def test_check_matches_existing_row_by_reporter_cite(self):
        self.cli("authority", "add", self.dir, "Bell Atl. Corp. v. Twombly, 550 U.S. 544 (2007)")
        p = self.cli("authority", "check", self.dir, "Twombly, 550 U.S. 544", "--source-url", "https://e.org/t",
                     "--pin", "555")
        self.assertEqual(p.returncode, 0, p.stderr)
        [row] = self.rows()
        self.assertEqual((row["citation"], row["status"]),
                         ("Bell Atl. Corp. v. Twombly, 550 U.S. 544 (2007)", "source-checked"))

    def test_stale_flag_records_currency_question(self):
        p = self.cli("authority", "check", self.dir, "Example Code Ann. § 1-2-3", "--source-url", "https://e.org/s",
                     "--stale", "two sources give different text")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.rows()[0]["status"], "stale-flagged")

    def test_requires_a_source(self):
        p = self.cli("authority", "check", self.dir, "579 U.S. 40", "--pin", "45")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.rows(), [])


class TestLedgerImport(Base):
    def test_dry_run_writes_nothing(self):
        led = self.write("Research/ledger.md", LEDGER)
        before = _sha(store.db_path(self.dir))
        p = self.cli("authority", "import", self.dir, led)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("DRY RUN", p.stdout)
        self.assertEqual(_sha(store.db_path(self.dir)), before)

    def test_apply_maps_ledger_statuses(self):
        led = self.write("Research/ledger.md", LEDGER)
        p = self.cli("authority", "import", self.dir, led, "--apply", "--author", "agent:model-x")
        self.assertEqual(p.returncode, 0, p.stderr)
        got = {r["citation"]: r for r in self.rows()}
        self.assertEqual(got["Fed. R. Civ. P. 15(a)(1)–(3)"]["status"], "source-checked")
        self.assertEqual(got["Example Code Ann. § 1-2-3"]["status"], "research-pending")
        self.assertEqual(got["Example Const. art. 3, § 29"]["status"], "unverified")
        alpha = got["Alpha v. Beta, 500 U.S. 44, 55–58 (1991)"]
        self.assertEqual(alpha["status"], "source-checked")
        self.assertEqual(json.loads(alpha["check_evidence"])["pins"], ["55–58"])
        gamma = json.loads(got["Gamma v. Delta, 874 F.3d 425 (5th Cir. 2017)"]["check_evidence"])
        self.assertEqual(gamma["pins"], [])
        self.assertTrue(gamma["pins_unconfirmed"])
        self.assertEqual(got["Twombly, 550 U.S. 544, 555, 570; Iqbal, 556 U.S. 662, 678"]["status"], "resolved")
        self.assertIn("agent:model-x", json.dumps(gamma))

    def test_apply_twice_skips_existing(self):
        led = self.write("Research/ledger.md", LEDGER)
        self.cli("authority", "import", self.dir, led, "--apply")
        n = len(self.rows())
        p = self.cli("authority", "import", self.dir, led, "--apply")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(self.rows()), n)
        self.assertIn("already in store", p.stdout)

    def test_brief_check_sees_imported_statuses(self):
        led = self.write("Research/ledger.md", LEDGER)
        self.cli("authority", "import", self.dir, led, "--apply")
        draft = self.write("draft.md", "Detention needs a prompt hearing. Alpha v. Beta, 500 U.S. 44, 56 (1991). "
                                       "Officers cannot shift blame. Gamma v. Delta, 874 F.3d 425, 430 (5th Cir. 2017). "
                                       "Plausibility. Iqbal, 556 U.S. 662, 678 (2009).")
        r = briefcheck.check(self.dir, draft, use_pdftotext=False)
        codes = {(f.severity, f.code) for f in r.findings}
        self.assertNotIn(("BLOCK", "authority-not-logged"), codes)
        self.assertIn(("WARN", "pins-unconfirmed"), codes)
        self.assertIn(("UNCHECKED", "authority-not-source-checked"), codes)

    def test_parse_handles_other_column_names(self):
        rows = authority.parse_ledger("| Citation | Source | Status |\n|---|---|---|\n| 42 U.S.C. § 1983 | LII | V-LEAD |\n")
        self.assertEqual([(r["citation"], r["status"]) for r in rows], [("42 U.S.C. § 1983", "source-checked")])


if __name__ == "__main__":
    unittest.main()

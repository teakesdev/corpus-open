"""Case setup and status counts: `init`, `status`, and the shared modules under them.

Characterization first (output of the CLI before cases.py / packet.status_data existed),
then the new module-level behaviour. Synthetic tempdirs only.
Run: python3 -m unittest discover -s tests -v
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

import matter  # noqa: E402
from matterkit import cases, packet, store  # noqa: E402

CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("MATTER_AUTHOR", "MATTER_DIRECTED_BY")}
FOLDERS = ["Filings", "Evidence", "Correspondence", "Case_Tracking", "Research", "Media"]


def run_cli(*argv, check=True):
    return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *argv],
                          capture_output=True, text=True, check=check, env=CLEAN_ENV)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def build_case(root, name="Case"):
    """A synthetic matter holding every count `status` renders: documents of three kinds,
    two assertions, three deadlines (one met, one overdue), an unchecked authority, a draft
    custodian notice and two subcases (one with a late deadline). Returns the case dir."""
    m = os.path.join(root, name)
    run_cli("init", m)
    write(os.path.join(m, "Evidence", "a.txt"), "alpha\n")
    write(os.path.join(m, "Filings", "b.txt"), "beta\n")
    write(os.path.join(m, "Research", "c.txt"), "gamma\n")
    run_cli("ingest", m)
    run_cli("fact", "add", m, "--text", "Sourced", "--status", "documented-fact", "--source", "Evidence/a.txt@p1")
    run_cli("fact", "add", m, "--text", "Unsourced", "--status", "allegation")
    run_cli("deadline", "add", m, "--label", "Past due", "--due", "2020-01-01")
    run_cli("deadline", "add", m, "--label", "Far future", "--due", "2999-12-31")
    run_cli("deadline", "add", m, "--label", "Already met", "--due", "2021-05-05")
    db = sqlite3.connect(os.path.join(m, ".matter", "matter.db"))
    try:
        did = db.execute("SELECT id FROM deadlines WHERE label='Already met'").fetchone()[0]
        run_cli("deadline", "update", m, did, "--reason", "filed", "--state", "met")
        run_cli("authority", "add", m, "Fed. R. Civ. P. 12(b)(6)")
        db.execute("INSERT INTO correspondence_notices (id,kind,status,addressee,address,exhibit_document_id,"
                   "exhibit_sha256,created_at,updated_at) VALUES ('n_1','hold','draft','p_x','1 Example St','doc1',"
                   "?,'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')", ("0" * 64,))
        db.commit()
    finally:
        db.close()
    run_cli("subcase", "init", m, "kid", "--title", "Kid case")
    run_cli("subcase", "init", m, "kid2", "--title", "Second kid")
    run_cli("deadline", "add", os.path.join(m, "Subcases", "kid"), "--label", "Kid late", "--due", "2020-02-02")
    return m


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name


class TestInitCharacterization(TmpCase):
    def test_init_prints_and_creates_folders_and_store(self):
        d = os.path.join(self.root, "New", "Nested-Case")  # parents are created too
        out = run_cli("init", d).stdout
        self.assertEqual(out, f"initialized {d} (.matter/matter.db, 0 documents registered)\n")
        for f in FOLDERS:
            self.assertTrue(os.path.isdir(os.path.join(d, f)), f)
        self.assertTrue(os.path.isfile(os.path.join(d, ".matter", "matter.db")))

    def test_init_again_counts_registered_documents_and_keeps_files(self):
        d = os.path.join(self.root, "Case")
        run_cli("init", d)
        write(os.path.join(d, "Evidence", "a.txt"), "alpha\n")
        run_cli("ingest", d)
        out = run_cli("init", d).stdout
        self.assertEqual(out, f"initialized {d} (.matter/matter.db, 1 documents registered)\n")
        self.assertTrue(os.path.isfile(os.path.join(d, "Evidence", "a.txt")))


class TestStatusCharacterization(TmpCase):
    def masked(self, text, case):
        return text.replace(case, "<DIR>")

    def test_status_with_documents_facts_deadlines_notice_and_subcases(self):
        m = build_case(self.root)
        out = self.masked(run_cli("status", m).stdout, m)
        self.assertEqual(out, (
            "# Matter status — <DIR>\n\n"
            "Documents registered: **3**\n"
            "- evidence: 1\n- filing: 1\n- research: 1\n\n"
            "Assertions: 2 · Open deadlines: 2 · Unverified authorities: 1\n"
            "Timeline: 0 of 2 facts dated.\n\n"
            "Custodian notices (transport=nosend; humans send): draft: 1\n\n"
            "Subcases: 2\n"
            "- kid — Kid case · Open deadlines: 1 · ⚠ OVERDUE: 1\n"
            "- kid2 — Second kid · Open deadlines: 0 · OVERDUE: 0\n"))

    def test_status_on_a_subcase_names_its_parent(self):
        m = build_case(self.root)
        out = self.masked(run_cli("status", os.path.join(m, "Subcases", "kid")).stdout, m)
        self.assertEqual(out, (
            "# Matter status — <DIR>/Subcases/kid\n\n"
            "Subcase of: Case (<DIR>) · title: Kid case\n\n"
            "Documents registered: **0**\n\n"
            "Assertions: 0 · Open deadlines: 1 · Unverified authorities: 0\n"))

    def test_status_of_an_empty_matter(self):
        d = os.path.join(self.root, "Empty")
        run_cli("init", d)
        out = run_cli("status", d).stdout.replace(d, "<DIR>")
        self.assertEqual(out, ("# Matter status — <DIR>\n\n"
                               "Documents registered: **0**\n\n"
                               "Assertions: 0 · Open deadlines: 0 · Unverified authorities: 0\n"))


def make_case(path, title=None):
    """A folder holding just a store (what discover calls a case), with an optional matter.json title."""
    os.makedirs(path, exist_ok=True)
    store.connect(path).close()
    if title is not None:
        write(os.path.join(path, "matter.json"), json.dumps({"title": title}))
    return path


def case_id(rel):
    return hashlib.sha256(rel.encode()).hexdigest()[:16]


def tree(root):
    """Every path under root (symlinks not followed) with file sizes: the whole-tree before/after check."""
    out = []
    for base, dirs, files in os.walk(root):
        for n in dirs + files:
            full = os.path.join(base, n)
            out.append((os.path.relpath(full, root), os.path.getsize(full) if os.path.isfile(full) else None))
    return sorted(out)


class TestCasesInit(TmpCase):
    def test_init_returns_the_registered_count_and_is_idempotent(self):
        d = os.path.join(self.root, "A", "B")
        self.assertEqual(cases.init(d), 0)
        write(os.path.join(d, "Evidence", "a.txt"), "alpha\n")
        run_cli("ingest", d)
        before = tree(d)
        self.assertEqual(cases.init(d), 1)
        self.assertEqual(tree(d), before)
        for f in cases.FOLDERS:
            self.assertTrue(os.path.isdir(os.path.join(d, f)), f)

    def test_folders_list_is_the_one_the_cli_uses(self):
        self.assertEqual(cases.FOLDERS, FOLDERS)
        self.assertIs(matter.FOLDERS, cases.FOLDERS)


class TestDiscover(TmpCase):
    def setUp(self):
        super().setUp()
        self.ws = os.path.join(self.root, "Clients")
        os.makedirs(self.ws)

    def rels(self, ws=None):
        return [c["rel"] for c in cases.discover(ws or self.ws)]

    def test_empty_missing_and_file_workspaces_list_nothing(self):
        self.assertEqual(cases.discover(self.ws), [])
        self.assertEqual(cases.discover(os.path.join(self.root, "nope")), [])
        write(os.path.join(self.root, "afile"), "x")
        self.assertEqual(cases.discover(os.path.join(self.root, "afile")), [])

    def test_finds_cases_in_name_order_ignoring_case_and_skips_the_rest(self):
        for n in ("beta", "Zed", "Alpha"):
            make_case(os.path.join(self.ws, n))
        os.makedirs(os.path.join(self.ws, "NotACase", "Evidence"))  # no store
        os.makedirs(os.path.join(self.ws, "Half", ".matter"))  # .matter but no matter.db
        write(os.path.join(self.ws, "notes.txt"), "a file, not a folder")
        make_case(os.path.join(self.ws, ".hidden"))
        make_case(os.path.join(self.ws, "Zed", "Nested-Not-Subcase"))  # only Subcases/ counts
        got = cases.discover(self.ws)
        self.assertEqual([c["rel"] for c in got], ["Alpha", "beta", "Zed"])
        for c in got:
            self.assertEqual(set(c), {"id", "rel", "path", "title", "parent_id"})
            self.assertEqual(c["id"], case_id(c["rel"]))
            self.assertRegex(c["id"], r"^[0-9a-f]{16}$")
            self.assertEqual(c["path"], os.path.join(os.path.abspath(self.ws), c["rel"]))
            self.assertTrue(os.path.isabs(c["path"]))
            self.assertEqual(c["title"], c["rel"])  # no matter.json: the folder name
            self.assertIsNone(c["parent_id"])
        self.assertEqual(len({c["id"] for c in got}), 3)

    def test_title_comes_from_matter_json_when_usable(self):
        make_case(os.path.join(self.ws, "A"), "Smith v. Jones")
        make_case(os.path.join(self.ws, "B"), "   ")
        make_case(os.path.join(self.ws, "C"), None)
        write(os.path.join(self.ws, "C", "matter.json"), "{not json")
        make_case(os.path.join(self.ws, "D"))
        write(os.path.join(self.ws, "D", "matter.json"), json.dumps({"title": 7}))
        self.assertEqual({c["rel"]: c["title"] for c in cases.discover(self.ws)},
                         {"A": "Smith v. Jones", "B": "B", "C": "C", "D": "D"})

    def test_nests_subcases_under_their_parent(self):
        a = make_case(os.path.join(self.ws, "Alpha"))
        make_case(os.path.join(a, "Subcases", "kid"), "Kid case")
        make_case(os.path.join(a, "Subcases", "Kid-2"))
        make_case(os.path.join(a, "Subcases", "kid", "Subcases", "grandkid"), "Grandkid")
        make_case(os.path.join(self.ws, "beta"))
        os.makedirs(os.path.join(a, "Subcases", "not-a-case", "Evidence"))
        make_case(os.path.join(a, "Subcases", ".dot"))
        got = cases.discover(self.ws)
        self.assertEqual([c["rel"] for c in got], [
            "Alpha", "Alpha/Subcases/kid", "Alpha/Subcases/kid/Subcases/grandkid", "Alpha/Subcases/Kid-2", "beta"])
        ids = {c["rel"]: c["id"] for c in got}
        self.assertEqual({c["rel"]: c["parent_id"] for c in got}, {
            "Alpha": None, "Alpha/Subcases/kid": ids["Alpha"],
            "Alpha/Subcases/kid/Subcases/grandkid": ids["Alpha/Subcases/kid"],
            "Alpha/Subcases/Kid-2": ids["Alpha"], "beta": None})
        self.assertEqual({c["rel"]: c["title"] for c in got}["Alpha/Subcases/kid"], "Kid case")
        self.assertEqual(got[1]["path"], os.path.join(os.path.abspath(self.ws), "Alpha", "Subcases", "kid"))

    def test_subcase_recursion_stops_at_depth_three(self):
        path = make_case(os.path.join(self.ws, "Top"))
        for i in range(1, 6):
            path = make_case(os.path.join(path, "Subcases", f"s{i}"))
        self.assertEqual(self.rels(), ["Top", "Top/Subcases/s1", "Top/Subcases/s1/Subcases/s2",
                                       "Top/Subcases/s1/Subcases/s2/Subcases/s3"])

    def test_skips_symlinked_folders_including_ones_pointing_outside(self):
        make_case(os.path.join(self.ws, "Real"))
        outside = make_case(os.path.join(self.root, "Outside-Case"))
        os.symlink(outside, os.path.join(self.ws, "escape"))  # a case outside the workspace
        os.symlink(os.path.join(self.ws, "Real"), os.path.join(self.ws, "alias"))  # a case inside, by another name
        os.symlink(os.path.join(self.root, "nowhere"), os.path.join(self.ws, "dangling"))
        os.symlink(self.root, os.path.join(self.ws, "up"))  # the workspace's parent
        self.assertEqual(self.rels(), ["Real"])

    def test_skips_symlinked_subcases_and_a_symlinked_subcases_folder(self):
        a = make_case(os.path.join(self.ws, "Alpha"))
        make_case(os.path.join(a, "Subcases", "ok"))
        outside = make_case(os.path.join(self.root, "Outside-Case"))
        os.symlink(outside, os.path.join(a, "Subcases", "escape"))
        os.symlink(os.path.join(self.ws, "Alpha"), os.path.join(a, "Subcases", "loop"))
        b = make_case(os.path.join(self.ws, "Beta"))
        elsewhere = os.path.join(self.root, "elsewhere")
        make_case(os.path.join(elsewhere, "kid"))
        os.symlink(elsewhere, os.path.join(b, "Subcases"))  # the whole Subcases folder points outside
        self.assertEqual(self.rels(), ["Alpha", "Alpha/Subcases/ok", "Beta"])

    def test_a_symlinked_workspace_path_still_works(self):
        make_case(os.path.join(self.ws, "Alpha"))
        link = os.path.join(self.root, "link-to-workspace")
        os.symlink(self.ws, link)
        got = cases.discover(link)
        self.assertEqual([c["rel"] for c in got], ["Alpha"])
        self.assertEqual(got[0]["path"], os.path.join(link, "Alpha"))

    def test_workspace_that_is_itself_a_case_lists_only_itself_and_its_subcases(self):
        make_case(self.ws, "The Only Case")
        make_case(os.path.join(self.ws, "Other-Case"))  # a sibling folder: not a top-level case here
        make_case(os.path.join(self.ws, "Subcases", "kid"), "Kid")
        got = cases.discover(self.ws)
        self.assertEqual([c["rel"] for c in got], [".", "Subcases/kid"])
        self.assertEqual(got[0]["id"], case_id("."))
        self.assertEqual((got[0]["path"], got[0]["title"], got[0]["parent_id"]),
                         (os.path.abspath(self.ws), "The Only Case", None))
        self.assertEqual((got[1]["parent_id"], got[1]["title"]), (got[0]["id"], "Kid"))

    def test_workspace_case_without_title_uses_its_folder_name(self):
        make_case(self.ws)
        self.assertEqual(cases.discover(self.ws)[0]["title"], "Clients")

    def test_discover_is_read_only_and_repeatable(self):
        a = make_case(os.path.join(self.ws, "Alpha"))
        make_case(os.path.join(a, "Subcases", "kid"))
        before = tree(self.root)
        first = cases.discover(self.ws)
        self.assertEqual(cases.discover(self.ws), first)
        self.assertEqual(tree(self.root), before)


class TestResolve(TmpCase):
    def setUp(self):
        super().setUp()
        self.ws = os.path.join(self.root, "Clients")
        a = make_case(os.path.join(self.ws, "Alpha"))
        make_case(os.path.join(a, "Subcases", "kid"))
        make_case(os.path.join(self.ws, ".hidden"))
        os.symlink(make_case(os.path.join(self.root, "Out")), os.path.join(self.ws, "escape"))

    def test_resolves_every_discovered_case_by_id(self):
        for c in cases.discover(self.ws):
            self.assertEqual(cases.resolve(self.ws, c["id"]), c)

    def test_unknown_ids_are_refused(self):
        for bad in ("", "0" * 16, "..", "../Out", "/etc", self.ws, os.path.join(self.ws, "Alpha"), "Alpha",
                    "Alpha/Subcases/kid", case_id(".hidden"), case_id("escape"), case_id("Alpha")[:15],
                    case_id("Alpha").upper(), None, 123, ["x"]):
            with self.assertRaises(cases.CaseError, msg=repr(bad)) as cm:
                cases.resolve(self.ws, bad)
            self.assertEqual(str(cm.exception), "unknown case")

    def test_an_id_stops_resolving_when_its_folder_goes_away(self):
        cid = case_id("Alpha")
        self.assertEqual(cases.resolve(self.ws, cid)["rel"], "Alpha")
        import shutil
        shutil.rmtree(os.path.join(self.ws, "Alpha"))
        with self.assertRaises(cases.CaseError):
            cases.resolve(self.ws, cid)

    def test_case_error_is_a_value_error(self):
        self.assertTrue(issubclass(cases.CaseError, ValueError))


class TestCreate(TmpCase):
    def setUp(self):
        super().setUp()
        self.ws = os.path.join(self.root, "Clients")
        os.makedirs(self.ws)

    def refused(self, name, ws=None, msg=None):
        ws = ws or self.ws
        before = tree(self.root)
        with self.assertRaises(cases.CaseError, msg=repr(name)) as cm:
            cases.create(ws, name)
        if msg:
            self.assertIn(msg, str(cm.exception))
        self.assertEqual(tree(self.root), before, repr(name))  # nothing written

    def test_creates_a_case_and_returns_its_entry(self):
        entry = cases.create(self.ws, "Smith v. Jones (2026)")
        d = os.path.join(os.path.abspath(self.ws), "Smith v. Jones (2026)")
        self.assertEqual(entry, {"id": case_id("Smith v. Jones (2026)"), "rel": "Smith v. Jones (2026)",
                                 "path": d, "title": "Smith v. Jones (2026)", "parent_id": None})
        for f in cases.FOLDERS:
            self.assertTrue(os.path.isdir(os.path.join(d, f)), f)
        self.assertTrue(os.path.isfile(os.path.join(d, ".matter", "matter.db")))
        self.assertEqual(cases.resolve(self.ws, entry["id"]), entry)
        self.assertEqual(cases.discover(self.ws), [entry])
        conn = store.connect(d)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
        conn.close()

    def test_valid_name_shapes(self):
        for name in ("A", "x_y-z.1", "O'Brien & Sons, LLC", "Doe (pro se)", "9 lives", "a" * 80, "a..b", "x - y"):
            self.assertEqual(cases.create(self.ws, name)["rel"], name)

    def test_invalid_names_are_refused_and_nothing_is_created(self):
        for name in ("", "../x", "..", ".", "a/b", "a\\b", "/abs", ".hidden", "x.", "x ", " x", "a" * 81, "a\nb",
                     "name\n", "a\x00b", "-dash", "_under", "tab\there", "semi;colon", "q?", "a*b", "ünï",
                     "x:y", None, 5, ["x"]):
            self.refused(name, msg="invalid case name")

    def test_existing_target_is_refused_and_left_alone(self):
        d = os.path.join(self.ws, "Taken")
        write(os.path.join(d, "Evidence", "keep.txt"), "mine")
        self.refused("Taken", msg="already exists")  # a plain folder: init must not adopt it
        make_case(os.path.join(self.ws, "Real"))
        run_cli("fact", "add", os.path.join(self.ws, "Real"), "--text", "t", "--status", "unknown")
        self.refused("Real", msg="already exists")  # a case: its store is untouched
        write(os.path.join(self.ws, "afile"), "x")
        self.refused("afile", msg="already exists")
        os.symlink(os.path.join(self.root, "nowhere"), os.path.join(self.ws, "dangling"))
        self.refused("dangling", msg="already exists")
        self.assertEqual(os.path.getsize(os.path.join(d, "Evidence", "keep.txt")), 4)

    def test_a_workspace_that_is_itself_a_case_is_refused(self):
        make_case(self.ws)
        self.refused("New", msg="this folder is itself a case")

    def test_a_workspace_that_is_not_a_folder_is_refused(self):
        self.refused("New", ws=os.path.join(self.root, "nope"), msg="workspace is not a folder")
        write(os.path.join(self.root, "afile"), "x")
        self.refused("New", ws=os.path.join(self.root, "afile"), msg="workspace is not a folder")

    def test_two_creates_of_one_name_cannot_both_win(self):
        results, barrier = [], threading.Barrier(4)

        def go():
            barrier.wait()
            try:
                results.append(cases.create(self.ws, "Same"))
            except cases.CaseError as e:
                results.append(e)

        threads = [threading.Thread(target=go) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertEqual(sum(isinstance(r, cases.CaseError) for r in results), 3)

    def test_a_failed_setup_leaves_no_folder_behind(self):
        before = tree(self.root)
        with mock.patch.object(cases, "init", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                cases.create(self.ws, "Doomed")
        self.assertEqual(tree(self.root), before)
        self.assertEqual(cases.create(self.ws, "Doomed")["rel"], "Doomed")  # and the name is free again


def seed_matter(path):
    """An initialized, empty matter plus a raw connection for inserting odd rows."""
    cases.init(path)
    return store.connect(path)


class TestStatusData(TmpCase):
    KEYS = ["matter_dir", "parent_line", "documents_total", "documents_by_kind", "assertions", "open_deadlines",
            "overdue_deadlines", "unverified_authorities", "notices_by_status", "children_lines"]

    def test_values_for_a_full_matter(self):
        m = build_case(self.root)
        conn = store.connect(m)
        self.addCleanup(conn.close)
        d = packet.status_data(conn, m)
        self.assertEqual(list(d), self.KEYS)
        self.assertEqual(d, {
            "matter_dir": m, "parent_line": None, "documents_total": 3,
            "documents_by_kind": {"evidence": 1, "filing": 1, "research": 1},
            "assertions": 2, "open_deadlines": 2, "overdue_deadlines": 1, "unverified_authorities": 1,
            "notices_by_status": {"draft": 1},
            "children_lines": ["Subcases: 2",
                               "- kid — Kid case · Open deadlines: 1 · ⚠ OVERDUE: 1",
                               "- kid2 — Second kid · Open deadlines: 0 · OVERDUE: 0"]})
        self.assertEqual(list(d["documents_by_kind"]), ["evidence", "filing", "research"])  # ordered by kind

    def test_values_for_a_subcase(self):
        m = build_case(self.root)
        kid = os.path.join(m, "Subcases", "kid")
        conn = store.connect(kid)
        self.addCleanup(conn.close)
        d = packet.status_data(conn, kid)
        self.assertEqual(d["parent_line"], f"Subcase of: Case ({m}) · title: Kid case")
        self.assertEqual((d["documents_total"], d["documents_by_kind"], d["assertions"], d["open_deadlines"],
                          d["overdue_deadlines"], d["notices_by_status"], d["children_lines"]),
                         (0, {}, 0, 1, 1, {}, []))

    def test_values_for_an_empty_matter(self):
        d0 = os.path.join(self.root, "Empty")
        conn = seed_matter(d0)
        self.addCleanup(conn.close)
        self.assertEqual(packet.status_data(conn, d0), {
            "matter_dir": d0, "parent_line": None, "documents_total": 0, "documents_by_kind": {},
            "assertions": 0, "open_deadlines": 0, "overdue_deadlines": 0, "unverified_authorities": 0,
            "notices_by_status": {}, "children_lines": []})

    def test_overdue_counts_only_open_dated_deadlines_before_today(self):
        d0 = os.path.join(self.root, "D")
        conn = seed_matter(d0)
        self.addCleanup(conn.close)
        for i, (due, state) in enumerate([("2020-01-01", "open"), ("2999-01-01", "open"), ("2020-01-01", "met"),
                                           ("2020-01-01", "lapsed"), ("not-a-date", "open"), ("2020-13-45", "open")]):
            conn.execute("INSERT INTO deadlines (id,label,due_date,state) VALUES (?,?,?,?)", (f"d{i}", "x", due, state))
        conn.commit()
        d = packet.status_data(conn, d0)
        self.assertEqual((d["open_deadlines"], d["overdue_deadlines"]), (4, 1))

    def test_unverified_authorities_are_those_without_a_source_check(self):
        d0 = os.path.join(self.root, "A")
        conn = seed_matter(d0)
        self.addCleanup(conn.close)
        for i, st in enumerate(["unverified", "resolved", "research-pending", "source-checked", "stale-flagged",
                                "verified-official"]):
            conn.execute("INSERT INTO authorities (id,citation,status) VALUES (?,?,?)", (f"x{i}", "cite", st))
        conn.commit()
        self.assertEqual(packet.status_data(conn, d0)["unverified_authorities"], 3)

    def test_notices_are_grouped_and_ordered_by_status(self):
        d0 = os.path.join(self.root, "N")
        conn = seed_matter(d0)
        self.addCleanup(conn.close)
        for i, st in enumerate(["draft", "draft", "signed"]):
            conn.execute("INSERT INTO correspondence_notices (id,kind,status,addressee,address,exhibit_document_id,"
                         "exhibit_sha256,created_at,updated_at,approval_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (f"n{i}", "hold", st, "p", "addr", "doc", "0" * 64, "t", "t",
                          None if st == "draft" else "appr"))
        conn.commit()
        self.assertEqual(list(packet.status_data(conn, d0)["notices_by_status"].items()), [("draft", 2), ("signed", 1)])

    def test_report_is_rendered_from_the_data(self):
        m = build_case(self.root)
        conn = store.connect(m)
        self.addCleanup(conn.close)
        report = packet.status_report(conn, m)
        d = packet.status_data(conn, m)
        self.assertIn(f"Documents registered: **{d['documents_total']}**", report)
        self.assertIn(f"Assertions: {d['assertions']} · Open deadlines: {d['open_deadlines']} · "
                      f"Unverified authorities: {d['unverified_authorities']}", report)
        self.assertNotIn("overdue_deadlines", report)
        self.assertEqual(report.splitlines()[-3:], d["children_lines"])


if __name__ == "__main__":
    unittest.main()

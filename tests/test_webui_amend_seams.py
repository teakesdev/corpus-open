"""The amendment routes under the fixed frontend's conventions (Astra round 1, F4, F5, F7).

Each amendment route must behave like the fact routes beside it: the person behind the request
comes from the server, per request (401 if there is none); a person sees and changes only the cases
the policy grants; the case is opened through its pin, so a folder swapped part way is a 409 and a
busy store is a 503; a subcase's reference to a parent document is re-hashed before it is linked;
and a page names a document the way the fact form does ({"document_id", "locator"}).

Design: docs/superpowers/specs/2026-10-31-local-frontend-design.md §2, §8. Synthetic data only.
Run: python3 -m unittest discover -s tests -v
"""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webui_helpers import (OPERATOR, build_workspace, call, cases, direct_app, facts_add_direct, ingest,  # noqa: E402
                           quiet, store, tree_snapshot, webui)

LAWYER = "Lee Counsel"
SUB = "Alpha v. City/Subcases/Related-Claim"
TABLES = ("amendment_items", "amendment_item_links", "amendment_item_history")


def by_header(environ):
    """Stands in for a trusted front end that signs people in. A real deployment reads only what
    its proxy sets; a request header is never trusted like this."""
    name = environ.get("HTTP_X_TEST_USER")
    return webui.Identity(name) if name else None


def counts(case_dir):
    conn = store.connect_readonly(case_dir)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in TABLES}
    finally:
        conn.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk-amend-seams-")
        self.ws = os.path.join(self.tmp, "workspace")
        os.makedirs(self.ws)
        self.alpha, self.beta = build_workspace(self.ws)
        self.child = os.path.join(self.alpha, "Subcases", "Related-Claim")
        self.ids = {c["rel"]: c["id"] for c in cases.discover(self.ws)}
        self.a, self.b, self.sub = self.ids["Alpha v. City"], self.ids["Beta Matter"], self.ids[SUB]
        sub_id = self.sub

        def policy(identity, case_id, action):
            if identity.name == OPERATOR:
                return True
            if case_id is None:
                return action == "read"
            return case_id == sub_id and action in ("read", "write")
        self.app = direct_app(self.ws, identify=by_header, policy=policy)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def as_(self, who, method, path, body=None):
        return call(self.app, method, path, body, headers={"X-Test-User": who} if who else None)

    def add(self, case_id, who=OPERATOR, **body):
        body.setdefault("text", "Attribute all three ER injuries to the jail beating")
        return self.as_(who, "POST", f"/cases/{case_id}/amendments", body)

    def outside_case(self):
        outside = os.path.join(self.tmp, "outside-case")
        quiet(cases.init, outside)
        return outside


class TestRequestIdentity(Base):
    def test_each_request_carries_its_own_person(self):
        mine = self.add(self.sub, OPERATOR)[1]["amendment"]
        theirs = self.add(self.sub, LAWYER)[1]["amendment"]
        self.assertEqual((mine["created_by"], theirs["created_by"]), (f"human:{OPERATOR}", f"human:{LAWYER}"))
        status, data = self.as_(LAWYER, "POST", f"/cases/{self.sub}/amendments/{mine['id']}/status", {"status": "approved"})
        self.assertEqual(status, 200, data)
        self.assertEqual([h["recorded_by"] for h in data["amendment"]["history"]], [f"human:{OPERATOR}", f"human:{LAWYER}"])

    def test_no_identity_is_refused_before_anything_runs(self):
        made = self.add(self.sub)[1]["amendment"]
        before = tree_snapshot(self.ws)
        for method, path, body in [("GET", f"/cases/{self.sub}/amendments", None),
                                   ("POST", f"/cases/{self.sub}/amendments", {"text": "anonymous"}),
                                   ("POST", f"/cases/{self.sub}/amendments/{made['id']}/status", {"status": "dropped"})]:
            with self.subTest(route=f"{method} {path}"):
                self.assertEqual(self.as_(None, method, path, body)[0], 401)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_a_body_still_cannot_name_the_author(self):
        before = tree_snapshot(self.ws)
        status, _ = self.add(self.sub, LAWYER, created_by=f"human:{OPERATOR}")
        self.assertEqual(status, 400)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_the_launch_operator_is_the_default(self):
        app = direct_app(self.ws)
        status, data = call(app, "POST", f"/cases/{self.a}/amendments", {"text": "by default"})
        self.assertEqual((status, data["amendment"]["created_by"]), (200, f"human:{OPERATOR}"))


class TestSelectiveAccess(Base):
    def test_a_person_without_a_grant_cannot_read_or_change_the_case(self):
        made = self.add(self.a)[1]["amendment"]
        before = tree_snapshot(self.ws)
        for method, path, body in [("GET", f"/cases/{self.a}/amendments", None),
                                   ("POST", f"/cases/{self.a}/amendments", {"text": "x"}),
                                   ("POST", f"/cases/{self.a}/amendments/{made['id']}/status", {"status": "dropped"})]:
            with self.subTest(route=f"{method} {path}"):
                self.assertEqual(self.as_(LAWYER, method, path, body)[0], 403)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_a_granted_case_works_end_to_end(self):
        status, data = self.add(self.sub, LAWYER)
        self.assertEqual(status, 200, data)
        _, listed = self.as_(LAWYER, "GET", f"/cases/{self.sub}/amendments")
        self.assertEqual([i["id"] for i in listed["amendments"]], [data["amendment"]["id"]])

    def test_status_keeps_the_count_and_carries_no_other_case_and_no_path(self):
        self.add(self.sub)
        self.add(self.sub, LAWYER)
        for who in (OPERATOR, LAWYER):
            with self.subTest(who=who):
                status, data = self.as_(who, "GET", f"/cases/{self.sub}/status")
                self.assertEqual(status, 200)
                self.assertEqual(data["status"]["amendments_open"], 2)
                for gone in ("matter_dir", "parent_line", "children_lines"):
                    self.assertNotIn(gone, data["status"])
                self.assertNotIn(self.tmp, json.dumps(data))
        status, data = self.as_(OPERATOR, "GET", f"/cases/{self.a}/status")
        self.assertEqual(data["status"]["amendments_open"], 0)  # the parent's own count, not the subcase's


class TestPinnedStore(Base):
    """F4: the case is opened through its pin, never through a path that can be swapped."""

    def swap_matter_folder_for_a_link(self):
        outside = self.outside_case()
        meta = os.path.join(self.beta, ".matter")
        real = cases.resolve

        def swapped(ws, cid):
            entry = real(ws, cid)
            os.rename(meta, os.path.join(self.beta, ".matter-moved"))
            os.symlink(os.path.join(outside, ".matter"), meta)
            return entry
        return outside, mock.patch.object(webui.cases, "resolve", swapped)

    def test_add_to_a_case_swapped_for_a_link_is_a_409_and_writes_nothing_there(self):
        outside, swap = self.swap_matter_folder_for_a_link()
        with swap:
            status, _ = self.add(self.b)
        self.assertEqual(status, 409)
        self.assertEqual(counts(outside), dict.fromkeys(TABLES, 0))

    def test_status_change_in_a_case_swapped_for_a_link_is_a_409(self):
        made = self.add(self.b)[1]["amendment"]
        outside, swap = self.swap_matter_folder_for_a_link()
        with swap:
            status, _ = self.as_(OPERATOR, "POST", f"/cases/{self.b}/amendments/{made['id']}/status", {"status": "approved"})
        self.assertEqual(status, 409)
        self.assertEqual(counts(outside), dict.fromkeys(TABLES, 0))

    def test_reading_a_case_swapped_for_a_link_is_a_409(self):
        outside, swap = self.swap_matter_folder_for_a_link()
        with swap:
            status, _ = self.as_(OPERATOR, "GET", f"/cases/{self.b}/amendments")
        self.assertEqual(status, 409)

    def test_a_linked_store_is_not_a_case_for_these_routes_either(self):
        outside = self.outside_case()
        os.makedirs(os.path.join(self.ws, "Alias"))
        os.symlink(os.path.join(outside, ".matter"), os.path.join(self.ws, "Alias", ".matter"))
        alias_id = hashlib.sha256(b"Alias").hexdigest()[:16]
        self.assertEqual(self.add(alias_id)[0], 404)
        self.assertEqual(counts(outside), dict.fromkeys(TABLES, 0))

    def test_a_busy_store_is_a_plain_retry_message_and_writes_nothing(self):
        made = self.add(self.a)[1]["amendment"]
        before = counts(self.alpha)
        holder = store.connect(self.alpha)
        holder.execute("BEGIN IMMEDIATE")
        try:
            with mock.patch.object(webui, "BUSY_TIMEOUT", 0.1):
                add = self.add(self.a)
                move = self.as_(OPERATOR, "POST", f"/cases/{self.a}/amendments/{made['id']}/status", {"status": "approved"})
        finally:
            holder.rollback()
            holder.close()
        for status, data in (add, move):
            self.assertEqual(status, 503)
            self.assertIn("try again", data["error"].lower())
        self.assertEqual(counts(self.alpha), before)
        self.assertEqual(self.add(self.a)[0], 200)  # and once the other program lets go it works

    def test_an_old_store_gets_its_tables_on_the_first_write_and_reads_empty_before(self):
        db = sqlite3.connect(store.db_path(self.beta))
        for t in TABLES:
            db.execute(f"DROP TABLE {t}")
        db.commit()
        db.close()
        status, data = self.as_(OPERATOR, "GET", f"/cases/{self.b}/amendments")
        self.assertEqual((status, data["amendments"], data["counts"]["open"]), (200, [], 0))
        self.assertEqual(self.add(self.b)[0], 200)
        self.assertEqual(counts(self.beta)["amendment_items"], 1)


class TestParentDocuments(Base):
    """F5: a subcase's reference to a parent document, picked by id, is re-hashed against the parent
    before it is linked, as a fact's source is."""

    def setUp(self):
        super().setUp()
        self.memo = os.path.join(self.alpha, "Evidence", "memo.md")
        facts_add_direct(self.child, "first fact", "DOCUMENTED FACT", ["parent:Evidence/memo.md"])
        conn = store.connect_readonly(self.child)
        self.ref_id = conn.execute("SELECT id FROM documents WHERE rel_path='parent:Evidence/memo.md'").fetchone()["id"]
        conn.close()

    def link(self, **doc):
        return self.add(self.sub, documents=[dict({"document_id": self.ref_id}, **doc)])

    def test_unchanged_parent_is_linked(self):
        status, data = self.link(locator="p. 1")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["amendment"]["links"]["documents"],
                         [{"id": self.ref_id, "rel_path": "parent:Evidence/memo.md", "locator": "p. 1"}])

    def test_changed_parent_is_refused_and_nothing_written(self):
        with open(self.memo, "a") as f:
            f.write("changed after it was referenced\n")
        status, data = self.link()
        self.assertEqual(status, 400, data)
        self.assertIn("changed", data["error"])
        self.assertEqual(counts(self.child), dict.fromkeys(TABLES, 0))

    def test_missing_parent_is_refused_and_nothing_written(self):
        os.remove(self.memo)
        self.assertEqual(self.link()[0], 400)
        self.assertEqual(counts(self.child), dict.fromkeys(TABLES, 0))


class TestDocumentKey(Base):
    """The body names a document the way the fact form does: {"document_id", "locator"}. The
    original spec's {"id", "locator"} still works."""

    def setUp(self):
        super().setUp()
        conn = store.connect_readonly(self.alpha)
        self.did = conn.execute("SELECT id FROM documents WHERE rel_path='Evidence/memo.md'").fetchone()["id"]
        conn.close()

    def links(self, *docs):
        status, data = self.add(self.a, documents=list(docs))
        return status, (data["amendment"]["links"]["documents"] if status == 200 else data)

    def test_document_id_like_the_fact_form(self):
        status, got = self.links({"document_id": self.did, "locator": " para 1 "})
        self.assertEqual((status, got), (200, [{"id": self.did, "rel_path": "Evidence/memo.md", "locator": "para 1"}]))

    def test_id_is_still_accepted(self):
        status, got = self.links({"id": self.did, "locator": "para 2"})
        self.assertEqual((status, got[0]["locator"]), (200, "para 2"))

    def test_both_keys_must_agree(self):
        self.assertEqual(self.links({"document_id": self.did, "id": self.did})[0], 200)
        before = counts(self.alpha)
        for doc in ({"document_id": self.did, "id": "someone-else"}, {"locator": "p1"}, {"document_id": 5},
                    {"id": 5}, {"document_id": None}):
            with self.subTest(doc=doc):
                self.assertEqual(self.links(doc)[0], 400)
        self.assertEqual(counts(self.alpha)["amendment_items"], before["amendment_items"])

    def test_an_id_that_is_another_documents_path_does_not_capture_the_link(self):
        """A file whose name is a document's id can't stand in for it: the id is matched exactly."""
        decoy = os.path.join(self.alpha, "Evidence", self.did)
        with open(decoy, "w") as f:
            f.write("a file named like a document id\n")
        conn = store.connect(self.alpha)
        quiet(ingest.ingest_paths, conn, self.alpha, [decoy])
        conn.close()
        status, got = self.links({"document_id": self.did})
        self.assertEqual((status, got[0]["rel_path"]), (200, "Evidence/memo.md"))


if __name__ == "__main__":
    unittest.main()

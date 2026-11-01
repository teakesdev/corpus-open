"""Local UI: the amendment list ("Next amendment" tab). Read route, add route, status route, the
overview count, and the shipped page files.

Same rules as the other write routes (design 2026-10-31-local-frontend-design.md §4): the server
sets the author and refuses a body that names one; every route goes through authorize(); a refusal
writes nothing; a write to one client's case never touches another's. Synthetic data only.
Run: python3 -m unittest discover -s tests -v
"""
import os
import re
import sqlite3
import subprocess
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webui_helpers import (KIT, OPERATOR, ServerCase, facts_add_direct, shipped_text, store,  # noqa: E402
                           tree_snapshot, webui)

from matterkit import amendments, packet as pk  # noqa: E402

TABLES = ("amendment_items", "amendment_item_links", "amendment_item_history")


def doc_id(case_dir, rel_path):
    conn = store.connect_readonly(case_dir)
    try:
        return conn.execute("SELECT id FROM documents WHERE rel_path=?", (rel_path,)).fetchone()["id"]
    finally:
        conn.close()


def table_counts(case_dir):
    conn = store.connect_readonly(case_dir)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in TABLES}
    finally:
        conn.close()


class AmendCase(ServerCase):
    def setUp(self):
        super().setUp()
        self.A = "Alpha v. City"
        self.url_amend = self.case_path(self.A, "/amendments")
        self.did = doc_id(self.alpha, "Evidence/memo.md")
        self.fid = facts_add_direct(self.alpha, "The officer arrived at 9:14 PM", "DOCUMENTED FACT",
                                    ["Evidence/memo.md@para 1"])["id"]

    def add(self, **body):
        body.setdefault("text", "Attribute all three ER injuries to the jail beating")
        return self.api("POST", self.url_amend, body)

    def add_ok(self, **body):
        status, _, data = self.add(**body)
        self.assertEqual(status, 200, data)
        return data["amendment"]

    def set_status(self, item_id, **body):
        return self.api("POST", f"{self.url_amend}/{item_id}/status", body)

    def alpha_counts(self):
        return table_counts(self.alpha)

    def assertNothingWritten(self):
        self.assertEqual(self.alpha_counts(), dict.fromkeys(TABLES, 0))


class TestRead(AmendCase):
    def test_empty(self):
        status, _, data = self.api("GET", self.url_amend)
        self.assertEqual(status, 200)
        self.assertEqual(data["amendments"], [])
        self.assertEqual(data["counts"], {"proposed": 0, "approved": 0, "incorporated": 0, "dropped": 0, "open": 0})

    def test_item_fields_links_and_history(self):
        item = self.add_ok(text="Attribute all three ER injuries", paragraphs="¶¶ 37, 172", reason="ER notes",
                           facts=[self.fid], documents=[{"id": self.did, "locator": "para 1"}])
        self.set_status(item["id"], status="approved", note="Looks right")
        _, _, data = self.api("GET", self.url_amend)
        (got,) = data["amendments"]
        self.assertEqual((got["id"], got["status"], got["status_label"], got["target"], got["paragraphs"], got["reason"]),
                         (item["id"], "approved", "Approved", "complaint", "¶¶ 37, 172", "ER notes"))
        self.assertEqual(got["links"]["facts"], [{"id": self.fid, "text": "The officer arrived at 9:14 PM",
                                                  "label": "DOCUMENTED FACT"}])
        self.assertEqual(got["links"]["documents"], [{"id": self.did, "rel_path": "Evidence/memo.md", "locator": "para 1"}])
        self.assertEqual(got["created_by"], f"human:{OPERATOR}")
        self.assertEqual([(h["from_status"], h["to_status"], h["note"], h["recorded_by"]) for h in got["history"]],
                         [(None, "proposed", None, f"human:{OPERATOR}"),
                          ("proposed", "approved", "Looks right", f"human:{OPERATOR}")])
        self.assertEqual(data["counts"]["open"], 1)

    def test_status_query(self):
        a = self.add_ok(text="a")
        b = self.add_ok(text="b")
        c = self.add_ok(text="c")
        self.set_status(b["id"], status="approved")
        self.set_status(c["id"], status="dropped")

        def texts(q=""):
            status, _, data = self.api("GET", self.url_amend + q)
            self.assertEqual(status, 200)
            return [i["text"] for i in data["amendments"]]
        self.assertEqual(texts(), ["b", "a"])  # open work, approved first
        self.assertEqual(texts("?status=open"), ["b", "a"])
        self.assertEqual(texts("?status=all"), ["b", "a", "c"])
        self.assertEqual(texts("?status=dropped"), ["c"])
        self.assertEqual(texts("?status=incorporated"), [])
        self.assertEqual(texts("?status=ALL"), ["b", "a", "c"])
        for bad in ("done", "", "approved,dropped", "in%20the%20draft"):
            with self.subTest(bad=bad):
                self.assertEqual(self.api("GET", self.url_amend + f"?status={bad}")[0], 400)

    def test_reads_do_not_write(self):
        self.add_ok()
        before = tree_snapshot(self.ws)
        for q in ("", "?status=all", "?status=dropped"):
            self.assertEqual(self.api("GET", self.url_amend + q)[0], 200)
        self.assertEqual(self.api("GET", self.case_path(self.A, "/status"))[0], 200)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_hostile_text_comes_back_as_data(self):
        evil = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
        self.add_ok(text=evil, paragraphs=evil, reason=evil)
        status, headers, data = self.api("GET", self.url_amend)
        self.assertTrue(headers["content-type"].startswith("application/json"))
        got = data["amendments"][0]
        self.assertEqual((got["text"], got["paragraphs"], got["reason"]), (evil, evil, evil))

    def test_store_that_predates_the_tables_reads_as_empty_and_writes_nothing(self):
        db = sqlite3.connect(store.db_path(self.alpha))
        for t in TABLES:
            db.execute(f"DROP TABLE {t}")
        db.commit()
        db.close()
        before = tree_snapshot(self.ws)
        status, _, data = self.api("GET", self.url_amend)
        self.assertEqual((status, data["amendments"], data["counts"]["open"]), (200, [], 0))
        status, _, data = self.api("GET", self.case_path(self.A, "/status"))
        self.assertEqual((status, data["status"]["amendments_open"]), (200, 0))
        self.assertEqual(tree_snapshot(self.ws), before)
        # the first write creates the tables (store.connect), and the item shows up
        self.add_ok(text="First change in an old case")
        self.assertEqual(len(self.api("GET", self.url_amend)[2]["amendments"]), 1)


class TestStatusCount(AmendCase):
    def test_status_has_the_open_count(self):
        _, _, data = self.api("GET", self.case_path(self.A, "/status"))
        self.assertEqual(data["status"]["amendments_open"], 0)
        a, b, c = (self.add_ok(text=t)["id"] for t in "abc")
        self.set_status(b, status="approved")
        self.set_status(c, status="dropped")
        _, _, data = self.api("GET", self.case_path(self.A, "/status"))
        self.assertEqual(data["status"]["amendments_open"], 2)  # proposed + approved
        _, _, other = self.api("GET", self.case_path("Beta Matter", "/status"))
        self.assertEqual(other["status"]["amendments_open"], 0)

    def test_the_text_status_report_and_status_data_are_unchanged(self):
        self.add_ok()
        conn = store.connect_readonly(self.alpha)
        try:
            text = pk.status_report(conn, self.alpha)
            data = pk.status_data(conn, self.alpha)
        finally:
            conn.close()
        self.assertNotIn("mend", text)  # the handler adds the count; packet.py does not know about it
        self.assertNotIn("amendments_open", data)


class TestAdd(AmendCase):
    def test_add_with_everything(self):
        status, _, data = self.add(text="Attribute all three ER injuries to the jail beating", paragraphs="¶¶ 37, 172",
                                   reason="ER notes", target="complaint", facts=[self.fid],
                                   documents=[{"id": self.did, "locator": " para 1 "}])
        self.assertEqual(status, 200)
        item = data["amendment"]
        self.assertRegex(item["id"], r"^am_[0-9a-f]{12}$")
        self.assertEqual((item["status"], item["created_by"]), ("proposed", f"human:{OPERATOR}"))
        self.assertEqual(item["links"]["documents"][0]["locator"], "para 1")
        self.assertEqual(self.alpha_counts(), {"amendment_items": 1, "amendment_item_links": 2,
                                               "amendment_item_history": 1})

    def test_minimal_body_defaults_the_target(self):
        item = self.add_ok(text="Just the text")
        self.assertEqual(item["target"], "complaint")
        self.assertEqual(item["links"], {"facts": [], "documents": []})

    def test_other_target(self):
        self.add_ok(target="response to Dkt. 16")
        got = self.api("GET", self.url_amend)[2]["amendments"][0]
        self.assertEqual(got["target"], "response to Dkt. 16")

    def test_refusals_are_400_and_write_nothing(self):
        beta_fact = facts_add_direct(self.beta, "Beta-only fact", "UNKNOWN", [])["id"]
        with open(os.path.join(self.beta, "Evidence", "b.txt"), "w") as f:
            f.write("beta-only bytes\n")
        self.api("POST", self.case_path("Beta Matter", "/scan"), {})
        beta_doc = doc_id(self.beta, "Evidence/b.txt")
        ok_doc = {"id": self.did, "locator": "p1"}
        bad = [
            {}, {"text": ""}, {"text": "   \n"}, {"text": 5}, {"text": None}, {"text": ["x"]},
            {"text": "x", "facts": ["a_nope"]}, {"text": "x", "facts": [self.fid, "a_nope"], "documents": [ok_doc]},
            {"text": "x", "facts": [beta_fact]},  # another client's fact
            {"text": "x", "facts": self.fid}, {"text": "x", "facts": [5]}, {"text": "x", "facts": {"id": self.fid}},
            {"text": "x", "documents": [{"id": "nope"}]}, {"text": "x", "documents": [{"id": beta_doc}]},
            {"text": "x", "documents": self.did}, {"text": "x", "documents": [self.did]},
            {"text": "x", "documents": [{"locator": "p1"}]}, {"text": "x", "documents": [{"id": 5}]},
            {"text": "x", "documents": [{"id": self.did, "locator": "p1\nINJECT"}]},
            {"text": "x", "documents": [{"id": self.did, "locator": "x" * 201}]},
            {"text": "x", "documents": [{"id": self.did, "locator": 5}]},
            {"text": "x", "target": ""}, {"text": "x", "target": "  "}, {"text": "x", "target": 5},
            {"text": "x", "paragraphs": 5}, {"text": "x", "paragraphs": "¶ 1\n¶ 2"}, {"text": "x", "reason": ["no"]},
        ]
        for body in bad:
            with self.subTest(body=body):
                status, _, data = self.add_raw(body)
                self.assertEqual(status, 400, data)
                self.assertIn("error", data)
        self.assertNothingWritten()
        self.assertEqual(table_counts(self.beta), dict.fromkeys(TABLES, 0))

    def add_raw(self, body):
        return self.api("POST", self.url_amend, body)

    def test_json_is_required(self):
        before = tree_snapshot(self.ws)
        status, _, _ = self.api("POST", self.url_amend, raw=b'{"text":"x"}', headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        status, _, _ = self.api("POST", self.url_amend, raw=b"{not json", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        status, _, _ = self.api("POST", self.url_amend, raw=b'["x"]', headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_methods(self):
        self.assertEqual(self.api("PUT", self.url_amend, {"text": "x"})[0], 405)
        self.assertEqual(self.api("DELETE", self.url_amend)[0], 405)  # nothing is ever deleted
        item = self.add_ok()
        self.assertEqual(self.api("DELETE", f"{self.url_amend}/{item['id']}/status")[0], 405)
        self.assertEqual(self.api("GET", f"{self.url_amend}/{item['id']}/status")[0], 405)
        self.assertEqual(self.api("DELETE", f"{self.url_amend}/{item['id']}")[0], 404)  # no such route
        self.assertEqual(self.alpha_counts()["amendment_items"], 1)


class TestSetStatus(AmendCase):
    def test_walk_an_item_through_every_status(self):
        item = self.add_ok()
        steps = [("approved", "Pat checked it"), ("incorporated", None), ("proposed", "Reopened"), ("dropped", "Not needed")]
        for status, note in steps:
            with self.subTest(status=status):
                body = {"status": status}
                if note:
                    body["note"] = note
                code, _, data = self.set_status(item["id"], **body)
                self.assertEqual(code, 200, data)
                self.assertEqual((data["amendment"]["id"], data["amendment"]["status"]), (item["id"], status))
        got = self.api("GET", self.url_amend + "?status=all")[2]["amendments"][0]
        self.assertEqual([(h["from_status"], h["to_status"], h["note"], h["recorded_by"]) for h in got["history"]],
                         [(None, "proposed", None, f"human:{OPERATOR}"),
                          ("proposed", "approved", "Pat checked it", f"human:{OPERATOR}"),
                          ("approved", "incorporated", None, f"human:{OPERATOR}"),
                          ("incorporated", "proposed", "Reopened", f"human:{OPERATOR}"),
                          ("proposed", "dropped", "Not needed", f"human:{OPERATOR}")])
        self.assertRegex(got["updated_at"], r"^\d{4}-\d{2}-\d{2}T")

    def test_refusals_are_400_and_write_nothing(self):
        item = self.add_ok()
        self.set_status(item["id"], status="approved")
        before = tree_snapshot(self.ws)
        for body in ({"status": "approved"}, {"status": "APPROVED"}, {"status": "done"}, {"status": ""}, {},
                     {"status": None}, {"status": 5}, {"status": ["dropped"]}, {"status": "In the draft"},
                     {"status": "dropped", "note": 5}, {"status": "dropped", "note": ["x"]}):
            with self.subTest(body=body):
                code, _, data = self.set_status(item["id"], **body)
                self.assertEqual(code, 400, data)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_unknown_item_is_404_and_writes_nothing(self):
        before = tree_snapshot(self.ws)
        code, _, data = self.set_status("am_000000000000", status="dropped")
        self.assertEqual(code, 404)
        self.assertIn("error", data)
        for odd in ("am_zz", "x", "..", "%2e%2e", "am_000000000000/../facts"):
            with self.subTest(odd=odd):
                self.assertEqual(self.api("POST", f"{self.url_amend}/{odd}/status", {"status": "dropped"})[0], 404)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_an_item_id_from_another_case_changes_nothing(self):
        mine = self.add_ok()
        beta_before = tree_snapshot(self.beta)
        code, _, _ = self.api("POST", self.case_path("Beta Matter", f"/amendments/{mine['id']}/status"),
                              {"status": "dropped"})
        self.assertEqual(code, 404)
        self.assertEqual(tree_snapshot(self.beta), beta_before)
        self.assertEqual(self.api("GET", self.url_amend)[2]["amendments"][0]["status"], "proposed")


class TestAuthorIsTheServers(AmendCase):
    def test_body_naming_an_author_is_refused_on_both_write_routes(self):
        item = self.add_ok()
        before = tree_snapshot(self.ws)
        writes = [("POST", self.url_amend, {"text": "x"}),
                  ("POST", f"{self.url_amend}/{item['id']}/status", {"status": "approved"})]
        for method, path, body in writes:
            for key in sorted(webui.AUTHOR_KEYS):
                with self.subTest(path=path, key=key):
                    status, _, data = self.request(method, webui.API + path, dict(body, **{key: "Someone Else"}))
                    self.assertEqual(status, 400)
                    self.assertIn(key, data["error"])
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_stored_author_is_the_operator_everywhere(self):
        item = self.add_ok()
        self.set_status(item["id"], status="approved")
        got = self.api("GET", self.url_amend)[2]["amendments"][0]
        self.assertEqual(got["created_by"], f"human:{OPERATOR}")
        self.assertEqual({h["recorded_by"] for h in got["history"]}, {f"human:{OPERATOR}"})


class TestProtections(AmendCase):
    """The new routes sit behind the same token, Host, Origin and authorize() checks as the rest."""

    def routes(self):
        item = self.add_ok(text="seed")
        return [("GET", self.url_amend, None), ("POST", self.url_amend, {"text": "x"}),
                ("POST", f"{self.url_amend}/{item['id']}/status", {"status": "approved"})]

    def test_no_token_is_403_and_writes_nothing(self):
        routes = self.routes()
        before = tree_snapshot(self.ws)
        for method, path, body in routes:
            with self.subTest(route=f"{method} {path}"):
                self.assertEqual(self.request(method, webui.API + path, body, token=False)[0], 403)
                self.assertEqual(self.request(method, webui.API + path, body, token="wrong")[0], 403)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_foreign_origin_host_and_cross_site_are_403_and_write_nothing(self):
        routes = self.routes()
        before = tree_snapshot(self.ws)
        for method, path, body in routes:
            for how in ({"headers": {"Origin": "https://evil.example"}}, {"host": "evil.example"},
                        {"headers": {"Sec-Fetch-Site": "cross-site"}}):
                with self.subTest(route=f"{method} {path}", how=how):
                    self.assertEqual(self.request(method, webui.API + path, body, **how)[0], 403)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_same_origin_write_is_accepted(self):
        status, _, _ = self.api("POST", self.url_amend, {"text": "x"}, headers={"Origin": f"http://{self.host}"})
        self.assertEqual(status, 200)

    def test_no_cors_on_the_new_routes(self):
        for method, path, body in self.routes():
            _, headers, _ = self.api(method, path, body)
            self.assertFalse([h for h in headers if h.startswith("access-control-")])
            self.assertEqual(headers.get("content-security-policy"), webui.CSP)
            self.assertEqual(headers.get("cache-control"), "no-store")

    def test_routes_are_in_the_one_table_with_the_right_actions(self):
        actions = {(m, p.split("/amendments", 1)[1] if "/amendments" in p else None): a
                   for m, p, a, _ in webui.ROUTES if "/amendments" in p}
        self.assertEqual(actions, {("GET", ""): "read", ("POST", ""): "write",
                                   ("POST", "/" + webui.ITEM + "/status"): "write"})


class DenyAllAmend(AmendCase):
    policy = staticmethod(webui.deny_all)

    def test_every_amendment_route_is_refused_and_nothing_is_written(self):
        before = tree_snapshot(self.ws)
        for method, path, body in [("GET", self.url_amend, None), ("GET", self.url_amend + "?status=all", None),
                                   ("POST", self.url_amend, {"text": "x"}),
                                   ("POST", f"{self.url_amend}/am_000000000000/status", {"status": "approved"})]:
            with self.subTest(route=f"{method} {path}"):
                self.assertEqual(self.request(method, webui.API + path, body)[0], 403)
        self.assertEqual(tree_snapshot(self.ws), before)


class RecordingAmend(AmendCase):
    seen = []

    @staticmethod
    def policy(identity, case_id, action):
        RecordingAmend.seen.append((identity.name, case_id, action))
        return True

    def test_policy_sees_the_server_identity_the_case_and_the_action(self):
        cid = self.ids[self.A]
        RecordingAmend.seen.clear()
        self.api("GET", f"/cases/{cid}/amendments")
        _, _, data = self.api("POST", f"/cases/{cid}/amendments", {"text": "x"})
        self.api("POST", f"/cases/{cid}/amendments/{data['amendment']['id']}/status", {"status": "approved"})
        self.assertEqual(RecordingAmend.seen, [(OPERATOR, cid, "read"), (OPERATOR, cid, "write"), (OPERATOR, cid, "write")])


class TestIsolationAndConcurrency(AmendCase):
    def test_writes_to_one_case_leave_the_other_byte_identical(self):
        beta_before = tree_snapshot(self.beta)
        item = self.add_ok(facts=[self.fid], documents=[{"id": self.did}])
        self.set_status(item["id"], status="approved")
        self.assertEqual(tree_snapshot(self.beta), beta_before)

    def test_ui_and_cli_adds_all_land_with_their_history(self):
        n = 8
        errors = []

        def ui(i):
            status, _, data = self.api("POST", self.url_amend, {"text": f"ui change {i}"})
            if status != 200:
                errors.append(data)

        env = {k: v for k, v in os.environ.items() if k not in ("MATTER_AUTHOR", "MATTER_DIRECTED_BY")}

        def cli(i):
            r = subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "amend", "add", self.alpha,
                                "--text", f"cli change {i}"], capture_output=True, text=True, env=env)
            if r.returncode:
                errors.append(r.stderr)

        threads = [threading.Thread(target=ui, args=(i,)) for i in range(n)] + \
                  [threading.Thread(target=cli, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(self.alpha_counts()["amendment_items"], 2 * n)
        self.assertEqual(self.alpha_counts()["amendment_item_history"], 2 * n)
        authors = {a["created_by"] for a in self.api("GET", self.url_amend)[2]["amendments"]}
        self.assertEqual(authors, {f"human:{OPERATOR}", "human"})

    def test_two_people_changing_one_item_never_lose_a_history_row(self):
        item = self.add_ok()
        results = []

        def go(status):
            results.append(self.set_status(item["id"], status=status)[0])
        threads = [threading.Thread(target=go, args=(s,)) for s in ("approved", "dropped", "incorporated", "approved")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        got = self.api("GET", self.url_amend + "?status=all")[2]["amendments"][0]
        wins = results.count(200)
        self.assertGreaterEqual(wins, 1)
        self.assertTrue(all(r in (200, 400) for r in results), results)
        self.assertEqual(len(got["history"]), 1 + wins)  # every success left exactly one row
        # and the chain of from -> to is unbroken
        for prev, cur in zip(got["history"], got["history"][1:]):
            self.assertEqual(prev["to_status"], cur["from_status"])
        self.assertEqual(got["status"], got["history"][-1]["to_status"])


class TestAmendPageFiles(unittest.TestCase):
    """Static checks on the new tab. The general rules (no HTML sinks, nothing external, no inline
    script) are in test_webui_static and run over the same files."""

    @classmethod
    def setUpClass(cls):
        cls.js, cls.css = shipped_text("app.js"), shipped_text("app.css")
        start = cls.js.index("// --- next amendment")
        cls.section = cls.js[start:cls.js.index("\n// --- ", start + 1)]  # this tab's code, up to the next section

    def test_the_tab_and_its_routes(self):
        self.assertIn('["amend", "Next amendment"]', self.js)
        self.assertIn("/amendments", self.js)
        self.assertIn("/status`", self.js)  # POST .../amendments/<id>/status

    def test_overview_says_how_many_changes_are_queued(self):
        self.assertIn("queued for the next amended pleading", self.js)
        self.assertIn("amendments_open", self.js)

    def test_status_labels_match_the_module_and_each_has_a_style(self):
        for status, label in amendments.LABELS.items():
            with self.subTest(status=status):
                self.assertIn(f'"{label}"', self.js)
                self.assertIn(f'[data-status="{status}"]', self.css)

    def test_actions_a_person_can_take(self):
        for words in ("Approve", "Reopen", "Drop", "In the draft"):
            with self.subTest(words=words):
                self.assertIn(words, self.js)

    def test_the_page_never_sends_an_author(self):
        # the server sets it and refuses a body that names one; the page has no key for it
        self.assertIsNone(re.search(r"\b(%s)\s*:" % "|".join(sorted(webui.AUTHOR_KEYS)), self.section))
        self.assertIn("amendments", self.section)

    def test_text_is_set_with_text_content_only(self):
        self.assertNotRegex(self.section, r"innerHTML|insertAdjacentHTML|outerHTML|eval\(|document\.write")
        self.assertNotRegex(self.section, r"\.setAttribute\(\s*['\"]on")
        self.assertGreater(self.section.count("text:"), 10)  # case text goes through el(..., {text})

    def test_a_pending_button_cannot_be_double_clicked(self):
        self.assertIn("disabled = true", self.section)


if __name__ == "__main__":
    unittest.main()

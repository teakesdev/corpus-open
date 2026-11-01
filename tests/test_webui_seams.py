"""The seams for a private server shared with a lawyer (Astra round 1, F7).

v1 serves one operator on loopback, but the same app must take, without code changes:
allowed origins as configuration (an https origin behind a TLS proxy), an identity resolved for
each request by a trusted front end, and a policy that hides the cases a person may not see,
from the case list and from related-case summaries as well as from direct routes.

Design: docs/superpowers/specs/2026-10-31-local-frontend-design.md §2, §8. Synthetic data only.
Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webui_helpers import HOST, OPERATOR, build_workspace, call, cases, direct_app, store, webui  # noqa: E402

LAWYER = "Lee Counsel"
SUB = "Alpha v. City/Subcases/Related-Claim"


def by_header(environ):
    """Stands in for a trusted front end that signs people in and passes the name on. A real
    deployment reads only what its proxy sets; a request header is never trusted like this."""
    name = environ.get("HTTP_X_TEST_USER")
    return webui.Identity(name) if name else None


class Shared(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk-seams-")
        self.ws = os.path.join(self.tmp, "workspace")
        os.makedirs(self.ws)
        self.alpha, self.beta = build_workspace(self.ws)
        self.ids = {c["rel"]: c["id"] for c in cases.discover(self.ws)}
        sub_id = self.ids[SUB]

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


class TestOrigins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk-seams-")
        build_workspace(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_configured_https_origin(self):
        app = webui.App(self.tmp, OPERATOR, "test-token", hosts={"case.example"}, origins={"https://case.example"})
        ok = call(app, "GET", "/session", host="case.example", headers={"Origin": "https://case.example"})
        plain = call(app, "GET", "/session", host="case.example", headers={"Origin": "http://case.example"})
        self.assertEqual((ok[0], plain[0]), (200, 403))

    def test_default_origins_follow_the_hosts(self):
        app = direct_app(self.tmp)
        self.assertEqual(call(app, "GET", "/session", headers={"Origin": f"http://{HOST}"})[0], 200)
        self.assertEqual(call(app, "GET", "/session", headers={"Origin": f"https://{HOST}"})[0], 403)


class TestRequestIdentity(Shared):
    def test_each_request_carries_its_own_person(self):
        self.assertEqual(self.as_(OPERATOR, "GET", "/session")[1]["operator"], OPERATOR)
        self.assertEqual(self.as_(LAWYER, "GET", "/session")[1]["operator"], LAWYER)
        sub = self.ids[SUB]
        for who in (OPERATOR, LAWYER):
            status, data = self.as_(who, "POST", f"/cases/{sub}/facts", {"text": f"noted by {who}", "status": "ALLEGATION"})
            self.assertEqual(status, 200, data)
        conn = store.connect_readonly(os.path.join(self.alpha, "Subcases", "Related-Claim"))
        authors = {r["text"]: r["created_by"] for r in conn.execute("SELECT text, created_by FROM assertions")}
        conn.close()
        self.assertEqual(authors[f"noted by {OPERATOR}"], f"human:{OPERATOR}")
        self.assertEqual(authors[f"noted by {LAWYER}"], f"human:{LAWYER}")

    def test_no_identity_is_refused_before_anything_runs(self):
        status, _ = self.as_(None, "POST", f"/cases/{self.ids[SUB]}/facts", {"text": "anonymous", "status": "UNKNOWN"})
        self.assertEqual(status, 401)
        conn = store.connect_readonly(os.path.join(self.alpha, "Subcases", "Related-Claim"))
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM assertions").fetchone()["c"], 0)
        conn.close()

    def test_a_body_still_cannot_name_the_author(self):
        status, _ = self.as_(LAWYER, "POST", f"/cases/{self.ids[SUB]}/facts",
                             {"text": "x", "status": "UNKNOWN", "created_by": f"human:{OPERATOR}"})
        self.assertEqual(status, 400)

    def test_the_launch_operator_is_the_default(self):
        app = direct_app(self.ws)
        self.assertEqual(call(app, "GET", "/session")[1]["operator"], OPERATOR)


class TestSelectiveAccess(Shared):
    def test_case_list_shows_only_granted_cases(self):
        _, mine = self.as_(OPERATOR, "GET", "/cases")
        _, theirs = self.as_(LAWYER, "GET", "/cases")
        self.assertEqual(len(mine["cases"]), len(self.ids))
        self.assertEqual(theirs["cases"], [{"id": self.ids[SUB], "rel": "Related-Claim",
                                            "title": "Related state claim", "parent_id": None}])
        self.assertNotIn("Alpha", json.dumps(theirs))
        self.assertNotIn("Beta", json.dumps(theirs))

    def test_direct_routes_follow_the_same_grant(self):
        self.assertEqual(self.as_(LAWYER, "GET", f"/cases/{self.ids['Alpha v. City']}/facts")[0], 403)
        self.assertEqual(self.as_(LAWYER, "GET", f"/cases/{self.ids[SUB]}/facts")[0], 200)

    def test_status_carries_no_other_case_and_no_path(self):
        for who in (OPERATOR, LAWYER):
            for rel in (SUB, "Alpha v. City"):
                status, data = self.as_(who, "GET", f"/cases/{self.ids[rel]}/status")
                if status != 200:
                    continue
                with self.subTest(who=who, rel=rel):
                    self.assertNotIn("parent_line", data["status"])
                    self.assertNotIn("children_lines", data["status"])
                    self.assertNotIn(self.tmp, json.dumps(data))


if __name__ == "__main__":
    unittest.main()

"""Local UI security: loopback only, per-launch token, Host/Origin checks, CSP, one
authorization hook on every route, and an author the page can't choose.

Design: docs/superpowers/specs/2026-10-31-local-frontend-design.md §4. Synthetic data only.
Run: python3 -m unittest discover -s tests -v
"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webui_helpers import OPERATOR, ServerCase, route_samples, tree_snapshot, webui  # noqa: E402


class TestBinding(ServerCase):
    def test_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertTrue(self.url.startswith(f"http://127.0.0.1:{self.port}/#t="))

    def test_token_is_in_the_fragment_not_the_query(self):
        # a fragment never reaches the server or its logs
        self.assertNotIn("?", self.url)
        self.assertEqual(self.url.split("#t=", 1)[1], self.app.token)

    def test_no_option_to_bind_elsewhere(self):
        import inspect
        self.assertNotIn("host", inspect.signature(webui.make).parameters)
        self.assertNotIn("host", inspect.signature(webui.serve).parameters)


class TestToken(ServerCase):
    def test_every_route_refuses_a_missing_token(self):
        before = tree_snapshot(self.ws)
        for method, path, body in route_samples(self.ids["Alpha v. City"]):
            with self.subTest(route=f"{method} {path}"):
                status, _, data = self.request(method, path, body, token=False)
                self.assertEqual(status, 403)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_wrong_token_refused(self):
        status, _, _ = self.api("GET", "/cases", token="not-the-token")
        self.assertEqual(status, 403)

    def test_right_token_accepted(self):
        status, _, data = self.api("GET", "/session")
        self.assertEqual(status, 200)
        self.assertEqual(data["operator"], OPERATOR)

    def test_static_files_need_no_token(self):
        for path in ("/", "/app.js", "/app.css"):
            with self.subTest(path=path):
                status, _, _ = self.request("GET", path, token=False)
                self.assertEqual(status, 200)


class TestHostAndOrigin(ServerCase):
    def test_foreign_host_refused_api_and_static(self):  # DNS rebinding
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1:1", "", f"0.0.0.0:{self.port}"):
            for path in ("/", webui.API + "/cases"):
                with self.subTest(host=host, path=path):
                    status, _, _ = self.request("GET", path, host=host)
                    self.assertEqual(status, 403)

    def test_localhost_name_accepted(self):
        status, _, _ = self.api("GET", "/cases", host=f"localhost:{self.port}")
        self.assertEqual(status, 200)

    def test_foreign_origin_write_refused_and_nothing_written(self):
        before = tree_snapshot(self.ws)
        status, _, _ = self.api("POST", self.case_path("Alpha v. City", "/facts"),
                                {"text": "x", "status": "UNKNOWN"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_same_origin_write_accepted(self):
        status, _, _ = self.api("POST", self.case_path("Alpha v. City", "/facts"),
                                {"text": "x", "status": "UNKNOWN"}, headers={"Origin": f"http://{self.host}"})
        self.assertEqual(status, 200)

    def test_cross_site_fetch_refused(self):
        status, _, _ = self.api("GET", "/cases", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)

    def test_preflight_gets_no_cors(self):
        status, headers, _ = self.request("OPTIONS", webui.API + "/cases", token=False,
                                          headers={"Origin": "https://evil.example",
                                                   "Access-Control-Request-Method": "POST"})
        self.assertEqual(status, 403)
        self.assertFalse([h for h in headers if h.startswith("access-control-")])


class TestHeaders(ServerCase):
    def test_csp_and_no_cors_on_every_response(self):
        probes = [("GET", "/", True), ("GET", "/app.js", True), ("GET", webui.API + "/cases", True),
                  ("GET", webui.API + "/cases", False), ("GET", "/nope", True), ("GET", webui.API + "/nope", True)]
        for method, path, tok in probes:
            with self.subTest(path=path, token=tok):
                _, headers, _ = self.request(method, path, token=tok)
                self.assertEqual(headers.get("content-security-policy"), webui.CSP)
                self.assertIn("script-src 'self'", headers["content-security-policy"])
                self.assertNotIn("unsafe-inline", headers["content-security-policy"])
                self.assertEqual(headers.get("x-content-type-options"), "nosniff")
                self.assertFalse([h for h in headers if h.startswith("access-control-")])

    def test_api_is_not_cached(self):
        _, headers, _ = self.api("GET", "/cases")
        self.assertEqual(headers.get("cache-control"), "no-store")

    def test_internal_error_does_not_leak(self):
        err = io.StringIO()
        with mock.patch.object(webui.pk, "status_data", side_effect=RuntimeError("/secret/path/inside")), \
                contextlib.redirect_stderr(err):
            status, _, data = self.api("GET", self.case_path("Alpha v. City", "/status"))
        self.assertEqual(status, 500)
        self.assertIn("/secret/path/inside", err.getvalue())  # the operator's terminal gets the detail
        self.assertNotIn("secret", str(data))
        self.assertNotIn("Traceback", str(data))


class TestStaticAllowlist(ServerCase):
    def test_only_named_files_are_served(self):
        for path in ("/index.html", "/webui_static/app.js", "/../webui.py", "/%2e%2e/webui.py",
                     "/app.js/../../webui.py", "/.matter/matter.db", "/Alpha%20v.%20City/Evidence/memo.md"):
            with self.subTest(path=path):
                status, _, _ = self.request("GET", path)
                self.assertEqual(status, 404)


class TestCaseIds(ServerCase):
    def test_unknown_and_malformed_case_ids(self):
        for cid in ("0000000000000000", "../Beta%20Matter", "Alpha", "ffffffffffffffff/../x"):
            with self.subTest(cid=cid):
                status, _, _ = self.api("GET", f"/cases/{cid}/status")
                self.assertEqual(status, 404)

    def test_cases_list_has_no_absolute_paths(self):
        _, _, data = self.api("GET", "/cases")
        self.assertNotIn(self.tmp, str(data))


class DenyAll(ServerCase):
    policy = staticmethod(webui.deny_all)

    def test_every_route_refused_and_nothing_written(self):
        before = tree_snapshot(self.ws)
        for method, path, body in route_samples(self.ids["Alpha v. City"]):
            with self.subTest(route=f"{method} {path}"):
                status, _, _ = self.request(method, path, body)
                self.assertEqual(status, 403)
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_every_route_is_in_the_table(self):
        # App.handle reaches handlers only through ROUTES, after the policy; no handler is called elsewhere
        handlers = {fn for _, _, _, fn in webui.ROUTES}
        defined = {getattr(webui, n) for n in dir(webui) if n.startswith("h_")}
        self.assertEqual(handlers, defined)


class RecordingPolicy(ServerCase):
    seen = []

    @staticmethod
    def policy(identity, case_id, action):
        RecordingPolicy.seen.append((identity.name, case_id, action))
        return True

    def test_policy_gets_server_identity_case_and_action(self):
        RecordingPolicy.seen.clear()
        cid = self.ids["Alpha v. City"]
        self.api("GET", f"/cases/{cid}/facts")
        self.api("POST", f"/cases/{cid}/facts", {"text": "x", "status": "UNKNOWN"})
        self.api("GET", "/cases")
        self.assertEqual(RecordingPolicy.seen[:3], [(OPERATOR, cid, "read"), (OPERATOR, cid, "write"),
                                                    (OPERATOR, None, "read")])
        # then the case list asks about each case, so it can leave out the ones this person can't read
        rest = RecordingPolicy.seen[3:]
        self.assertEqual({c for _, c, _ in rest}, set(self.ids.values()))
        self.assertTrue(all(n == OPERATOR and a == "read" for n, _, a in rest))


class TestAuthorIsTheServers(ServerCase):
    def test_body_naming_an_author_is_refused_on_every_json_write(self):
        cid = self.ids["Alpha v. City"]
        before = tree_snapshot(self.ws)
        writes = [("POST", "/cases", {"name": "Gamma"}), ("POST", f"/cases/{cid}/facts", {"text": "x", "status": "UNKNOWN"}),
                  ("POST", f"/cases/{cid}/brief-check", {"path": "Filings/drafts/response_v1.md"})]
        for method, path, body in writes:
            for key in sorted(webui.AUTHOR_KEYS):
                with self.subTest(path=path, key=key):
                    status, _, data = self.api(method, path, dict(body, **{key: "Someone Else"}))
                    self.assertEqual(status, 400)
                    self.assertIn(key, data["error"])
        self.assertEqual(tree_snapshot(self.ws), before)

    def test_stored_author_is_the_operator(self):
        status, _, data = self.api("POST", self.case_path("Alpha v. City", "/facts"),
                                   {"text": "Synthetic fact", "status": "UNKNOWN"})
        self.assertEqual(status, 200)
        self.assertEqual(data["fact"]["created_by"], f"human:{OPERATOR}")


class TestOperatorName(unittest.TestCase):
    def test_operator_rules(self):
        self.assertEqual(webui.check_operator("  Pat Example "), "Pat Example")
        for bad in ("", "   ", "a;b", "a\nb", "x" * 81):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                webui.check_operator(bad)

    def test_make_refuses_a_missing_workspace(self):
        with self.assertRaises(ValueError):
            webui.make(os.path.join(os.sep, "no", "such", "folder"), "Pat Example")


if __name__ == "__main__":
    unittest.main()

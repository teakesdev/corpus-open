"""Increment 1 (dogfood-iteration §5.1): `matter capture` + `matter capture list`.

Synthetic tmp matters and a loopback (127.0.0.1) HTTP server only. Never a live
case directory, never an outside host.
Run: python3 -m pytest tests/test_capture.py -v
     python3 -m unittest tests.test_capture -v
"""
import hashlib
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matterkit import capture, ingest, store  # noqa: E402

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATTER_PY = os.path.join(KIT, "matter.py")

URL = "https://example.org/press/2026-09-18"
OUT = os.path.join("Media", "captures", "2026-09-18_example.org", "page.html")
PAGE = (b"<html><head><title>Synthetic press page</title>"
        b"<style>body{color:red}</style><script>var hidden=1;</script></head>"
        b"<body><h1>Synthetic release</h1><p>Three widgets were shipped.</p>"
        b"</body></html>\n")
T1 = "2026-09-18T14:03:22+00:00"
T2 = "2026-09-19T09:00:00+00:00"


def fixed_fetch(body=PAGE, calls=None):
    def _fetch(url):
        if calls is not None:
            calls.append(url)
        return body
    return _fetch


class CaptureCase(unittest.TestCase):
    def setUp(self):
        self._matter = tempfile.TemporaryDirectory()
        self.addCleanup(self._matter.cleanup)
        self.dir = self._matter.name
        subprocess.run([sys.executable, MATTER_PY, "init", self.dir],
                       check=True, capture_output=True)
        self.conn = store.connect(self.dir)
        self.addCleanup(self.conn.close)

    def _capture(self, url=URL, out=OUT, body=PAGE, now=T1, **kw):
        return capture.capture_url(self.conn, self.dir, url, out,
                                   fetch=fixed_fetch(body), now=now, **kw)


class TestCaptureWritesAndRegisters(CaptureCase):
    def test_writes_exact_bytes_at_matter_relative_out(self):
        self._capture()
        with open(os.path.join(self.dir, OUT), "rb") as f:
            self.assertEqual(f.read(), PAGE)

    def test_registers_document_with_source_url_and_media_kind(self):
        self._capture()
        rows = self.conn.execute("SELECT * FROM documents").fetchall()
        self.assertEqual(len(rows), 1)
        d = rows[0]
        self.assertEqual(d["rel_path"], OUT)
        self.assertEqual(d["sha256"], hashlib.sha256(PAGE).hexdigest())
        self.assertEqual(d["source_url"], URL)
        self.assertEqual(d["kind"], "media")
        self.assertEqual(d["bytes"], len(PAGE))

    def test_records_retrieved_at_in_captures_table_not_notes(self):
        self._capture()
        rows = self.conn.execute(
            "SELECT url, retrieved_at, sha256, rel_path FROM captures").fetchall()
        self.assertEqual([tuple(r) for r in rows],
                         [(URL, T1, hashlib.sha256(PAGE).hexdigest(), OUT)])
        notes = self.conn.execute("SELECT notes FROM documents").fetchone()["notes"]
        self.assertIsNone(notes)

    def test_captures_table_has_exactly_the_four_spec_columns(self):
        capture.ensure_schema(self.conn)
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(captures)")]
        self.assertEqual(cols, ["url", "retrieved_at", "sha256", "rel_path"])


class TestCaptureRefuses(CaptureCase):
    def _assert_store_empty(self):
        capture.ensure_schema(self.conn)
        for table in ("documents", "captures"):
            n = self.conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
            self.assertEqual(n, 0, table)

    def test_refuses_to_overwrite_existing_file_without_fetching(self):
        path = os.path.join(self.dir, OUT)
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as f:
            f.write(b"the only copy\n")
        calls = []
        with self.assertRaises(capture.CaptureError) as cm:
            capture.capture_url(self.conn, self.dir, URL, OUT,
                                fetch=fixed_fetch(calls=calls), now=T1)
        self.assertIn("overwrite", str(cm.exception))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"the only copy\n")
        self.assertEqual(calls, [], "refusal must happen before any egress")
        self._assert_store_empty()

    def test_second_capture_to_same_out_is_refused_and_first_survives(self):
        self._capture()
        with self.assertRaises(capture.CaptureError):
            self._capture(body=b"<html>replaced</html>\n", now=T2)
        with open(os.path.join(self.dir, OUT), "rb") as f:
            self.assertEqual(f.read(), PAGE)
        n = self.conn.execute("SELECT COUNT(*) c FROM captures").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_refuses_registered_rel_path_whose_file_is_missing(self):
        # A MISSING source is a blocker; re-capturing must not paper over it.
        self._capture()
        os.remove(os.path.join(self.dir, OUT))
        calls = []
        with self.assertRaises(capture.CaptureError) as cm:
            capture.capture_url(self.conn, self.dir, URL, OUT,
                                fetch=fixed_fetch(b"<html>new</html>\n", calls), now=T2)
        self.assertIn("already registered", str(cm.exception))
        self.assertEqual(calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, OUT)))
        ok, bad, missing, failures = ingest.verify_integrity(self.conn, self.dir)
        self.assertEqual(failures, [("MISSING", OUT)])

    def test_refuses_out_that_escapes_the_matter(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        for out in (os.path.join("..", "escaped.html"),
                    os.path.join(outside.name, "abs.html")):
            calls = []
            with self.assertRaises(capture.CaptureError) as cm:
                capture.capture_url(self.conn, self.dir, URL, out,
                                    fetch=fixed_fetch(calls=calls), now=T1)
            self.assertIn("outside matter", str(cm.exception))
            self.assertEqual(calls, [])
        self.assertEqual(os.listdir(outside.name), [])
        self._assert_store_empty()

    def test_refuses_out_inside_derived_store(self):
        # .MATTER is the same directory on a case-insensitive filesystem (macOS default)
        for top in (".matter", ".MATTER"):
            with self.assertRaises(capture.CaptureError) as cm:
                self._capture(out=os.path.join(top, "out", "page.html"))
            self.assertIn(".matter", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".matter", "out")))
        self._assert_store_empty()

    def test_refuses_url_carrying_credentials_and_does_not_echo_them(self):
        calls = []
        with self.assertRaises(capture.CaptureError) as cm:
            capture.capture_url(self.conn, self.dir, "https://ann:hunter2@example.org/p", OUT,
                                fetch=fixed_fetch(calls=calls), now=T1)
        self.assertIn("credentials", str(cm.exception))
        self.assertNotIn("hunter2", str(cm.exception))
        self.assertEqual(calls, [])
        self._assert_store_empty()

    def test_at_sign_in_path_or_query_is_not_mistaken_for_credentials(self):
        url = "https://example.org/u/@handle?mail=a@b.example"
        self.assertEqual(self._capture(url=url)["url"], url)

    def test_failed_write_leaves_no_partial_file(self):
        from unittest import mock
        real_open = open

        class DiskFullMidWrite:
            def __init__(self, f):
                self._f = f

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._f.close()

            def write(self, data):
                self._f.write(data[:5])
                self._f.flush()
                raise OSError("synthetic: disk full mid-write")

        def flaky_open(path, mode="r", *a, **kw):
            f = real_open(path, mode, *a, **kw)
            return DiskFullMidWrite(f) if mode == "xb" else f
        with mock.patch("builtins.open", flaky_open):
            with self.assertRaises(OSError):
                self._capture()
        self.assertFalse(os.path.exists(os.path.join(self.dir, OUT)))
        self._assert_store_empty()

    def test_refuses_non_http_url_without_fetching(self):
        for url in ("file:///etc/passwd", "ftp://example.org/x", "example.org/x", ""):
            calls = []
            with self.assertRaises(capture.CaptureError):
                capture.capture_url(self.conn, self.dir, url, OUT,
                                    fetch=fixed_fetch(calls=calls), now=T1)
            self.assertEqual(calls, [], url)
        self.assertFalse(os.path.exists(os.path.join(self.dir, OUT)))
        self._assert_store_empty()

    def test_fetch_failure_writes_nothing(self):
        def boom(url):
            raise OSError("synthetic network failure")
        with self.assertRaises(OSError):
            capture.capture_url(self.conn, self.dir, URL, OUT, fetch=boom, now=T1)
        self.assertFalse(os.path.exists(os.path.join(self.dir, OUT)))
        self._assert_store_empty()


class TestCaptureKind(CaptureCase):
    def test_out_under_research_is_kind_research(self):
        out = os.path.join("Research", "captures", "2026-09-18_example.org", "page.html")
        self._capture(out=out)
        kind = self.conn.execute("SELECT kind FROM documents").fetchone()["kind"]
        self.assertEqual(kind, "research")

    def test_explicit_kind_overrides_directory_hint(self):
        self._capture(kind="research")
        kind = self.conn.execute("SELECT kind FROM documents").fetchone()["kind"]
        self.assertEqual(kind, "research")

    def test_out_elsewhere_defaults_to_media(self):
        self._capture(out=os.path.join("Evidence", "web", "page.html"))
        kind = self.conn.execute("SELECT kind FROM documents").fetchone()["kind"]
        self.assertEqual(kind, "media")

    def test_kind_outside_media_research_is_refused(self):
        with self.assertRaises(capture.CaptureError):
            self._capture(kind="filing")
        self.assertFalse(os.path.exists(os.path.join(self.dir, OUT)))


class TestCaptureIdenticalBytes(CaptureCase):
    """documents.sha256 is UNIQUE: identical bytes cannot be registered twice."""
    OUT2 = os.path.join("Media", "captures", "2026-09-19_example.org", "page.html")

    def test_unchanged_page_records_retrieval_without_duplicate_file(self):
        self._capture()
        res = self._capture(out=self.OUT2, now=T2)
        self.assertTrue(res["unchanged"])
        self.assertEqual(res["rel_path"], OUT)
        self.assertFalse(os.path.exists(os.path.join(self.dir, self.OUT2)))
        self.assertFalse(os.path.exists(os.path.dirname(os.path.join(self.dir, self.OUT2))))
        rows = [tuple(r) for r in self.conn.execute(
            "SELECT url, retrieved_at, sha256, rel_path FROM captures ORDER BY retrieved_at")]
        sha = hashlib.sha256(PAGE).hexdigest()
        self.assertEqual(rows, [(URL, T1, sha, OUT), (URL, T2, sha, OUT)])
        n = self.conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_first_capture_is_not_flagged_unchanged(self):
        self.assertFalse(self._capture()["unchanged"])

    def test_unchanged_does_not_rewrite_the_existing_document_row(self):
        path = os.path.join(self.dir, "Research", "hand-saved.html")
        with open(path, "wb") as f:
            f.write(PAGE)
        ingest.ingest_paths(self.conn, self.dir, [path])
        before = tuple(self.conn.execute("SELECT * FROM documents").fetchone())
        res = self._capture()
        self.assertEqual(res["rel_path"], os.path.join("Research", "hand-saved.html"))
        after = tuple(self.conn.execute("SELECT * FROM documents").fetchone())
        self.assertEqual(before, after)


class TestCaptureDerivedExtract(CaptureCase):
    def _extract_path(self, body=PAGE):
        return os.path.join(".matter", "out", "captures",
                            hashlib.sha256(body).hexdigest() + ".txt")

    def test_html_text_extract_goes_under_dot_matter_out(self):
        res = self._capture()
        self.assertEqual(res["extract"], self._extract_path())
        with open(os.path.join(self.dir, res["extract"]), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Synthetic release", text)
        self.assertIn("Three widgets were shipped.", text)
        self.assertNotIn("var hidden", text)
        self.assertNotIn("color:red", text)

    def test_extract_is_labelled_derived_and_points_at_its_source(self):
        res = self._capture()
        with open(os.path.join(self.dir, res["extract"]), encoding="utf-8") as f:
            header = f.readline()
        self.assertIn("derived", header)
        self.assertIn(OUT, header)
        self.assertIn(hashlib.sha256(PAGE).hexdigest(), header)

    def test_nothing_derived_is_written_next_to_the_source(self):
        self._capture()
        self.assertEqual(os.listdir(os.path.dirname(os.path.join(self.dir, OUT))),
                         ["page.html"])

    def test_extract_is_never_registered_as_a_source(self):
        self._capture()
        added, skipped, excluded = ingest.ingest_paths(self.conn, self.dir, [self.dir])
        self.assertEqual(added, 0)
        rels = [r["rel_path"] for r in self.conn.execute("SELECT rel_path FROM documents")]
        self.assertEqual(rels, [OUT])

    def test_extract_failure_never_unlands_or_misreports_a_registered_capture(self):
        from unittest import mock
        with mock.patch.object(capture, "_write_extract",
                               side_effect=OSError("synthetic: no space left")):
            res = self._capture()
        self.assertIsNone(res["extract"])
        self.assertIn("no space left", res["extract_error"])
        with open(os.path.join(self.dir, OUT), "rb") as f:
            self.assertEqual(f.read(), PAGE)
        for table in ("documents", "captures"):
            n = self.conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
            self.assertEqual(n, 1, table)

    def test_non_html_capture_makes_no_extract(self):
        body = b"%PDF-1.4 synthetic bytes\n"
        res = self._capture(out=os.path.join("Media", "captures", "x", "notice.pdf"), body=body)
        self.assertIsNone(res["extract"])
        self.assertFalse(os.path.exists(os.path.join(self.dir, self._extract_path(body))))


class TestCaptureList(CaptureCase):
    def test_renders_provenance_markdown_table_oldest_first(self):
        later = b"<html><body>later synthetic page</body></html>\n"
        out2 = os.path.join("Research", "captures", "2026-09-19_example.org", "page.html")
        self._capture(url="https://example.org/b", out=out2, body=later, now=T2)
        self._capture()
        lines = capture.render_list(self.conn).splitlines()
        self.assertEqual(lines[0], "| url | retrieved_at | sha256 | rel_path |")
        self.assertEqual(lines[1], "|---|---|---|---|")
        self.assertEqual(lines[2:], [
            f"| {URL} | {T1} | {hashlib.sha256(PAGE).hexdigest()} | {OUT} |",
            f"| https://example.org/b | {T2} | {hashlib.sha256(later).hexdigest()} | {out2} |",
        ])

    def test_pipe_in_url_cannot_break_the_table(self):
        self._capture(url="https://example.org/q?a=1|2")
        row = capture.render_list(self.conn).splitlines()[2]
        self.assertIn("https://example.org/q?a=1\\|2", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 5)

    def test_empty_store_says_so(self):
        self.assertEqual(capture.render_list(self.conn), "no captures recorded")


class TestCaptureLeavesNoOrphan(CaptureCase):
    def test_registration_failure_removes_the_file_it_created(self):
        import sqlite3
        from unittest import mock
        with mock.patch.object(ingest, "ingest_paths",
                               side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(sqlite3.OperationalError):
                self._capture()
        self.assertFalse(os.path.exists(os.path.join(self.dir, OUT)))
        n = self.conn.execute("SELECT COUNT(*) c FROM captures").fetchone()["c"]
        self.assertEqual(n, 0)

    def test_disk_bytes_not_matching_fetched_sha_is_a_blocker(self):
        # Fault injection: what ingest re-hashes from disk differs from what was fetched.
        from unittest import mock
        with mock.patch.object(ingest, "sha256_file", return_value="f" * 64):
            with self.assertRaises(capture.CaptureError) as cm:
                self._capture()
        self.assertIn("blocker", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.dir, OUT)))
        for table in ("documents", "captures"):
            n = self.conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
            self.assertEqual(n, 0, table)
        ok, bad, missing, failures = ingest.verify_integrity(self.conn, self.dir)
        self.assertEqual((bad, missing, failures), (0, 0, []))


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.seen.append(self.path)
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/page.html")
            self.end_headers()
            return
        bodies = {"/page.html": PAGE, "/big": b"x" * 4096, "/empty": b""}
        if self.path not in bodies:
            self.send_error(404)
            return
        body = bodies[self.path]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class LoopbackCase(CaptureCase):
    """Serves synthetic pages on 127.0.0.1 only."""

    def setUp(self):
        super().setUp()
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.seen = []
        t = threading.Thread(target=self.httpd.serve_forever, args=(0.01,), daemon=True)
        t.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"


class TestCaptureFetch(LoopbackCase):
    def setUp(self):
        super().setUp()
        from matterkit.adapters import capture_fetch
        self.cf = capture_fetch

    def test_returns_exact_served_bytes(self):
        self.assertEqual(self.cf.make_fetch(self.dir)(self.base + "/page.html"), PAGE)

    def test_redirect_is_refused_reported_and_never_followed(self):
        with self.assertRaises(capture.CaptureError) as cm:
            self.cf.make_fetch(self.dir)(self.base + "/moved")
        self.assertIn("redirect", str(cm.exception))
        self.assertIn("/page.html", str(cm.exception))
        self.assertEqual(self.httpd.seen, ["/moved"])

    def test_http_error_status_is_refused_not_captured(self):
        with self.assertRaises(capture.CaptureError) as cm:
            self.cf.make_fetch(self.dir)(self.base + "/nope")
        self.assertIn("404", str(cm.exception))

    def test_body_over_the_byte_bound_is_refused(self):
        with self.assertRaises(capture.CaptureError) as cm:
            self.cf.make_fetch(self.dir, max_bytes=1024)(self.base + "/big")
        self.assertIn("1024", str(cm.exception))

    def test_non_http_scheme_refused_before_any_egress(self):
        with self.assertRaises(capture.CaptureError):
            self.cf.make_fetch(self.dir)("file:///etc/hosts")
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".matter", "egress.jsonl")))

    def test_credentials_in_url_refused_before_any_egress(self):
        with self.assertRaises(capture.CaptureError) as cm:
            self.cf.make_fetch(self.dir)(f"http://ann:hunter2@{self.host}/page.html")
        self.assertNotIn("hunter2", str(cm.exception))
        self.assertEqual(self.httpd.seen, [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".matter", "egress.jsonl")))

    def test_each_fetch_appends_a_hash_only_egress_line(self):
        url = self.base + "/page.html"
        self.cf.make_fetch(self.dir)(url)
        with open(os.path.join(self.dir, ".matter", "egress.jsonl")) as f:
            raw = f.read()
        recs = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["provider"], "capture")
        self.assertEqual(recs[0]["endpoint"], self.host)
        self.assertEqual(recs[0]["sha256"], hashlib.sha256(url.encode()).hexdigest())
        self.assertNotIn("page.html", raw)


class TestCaptureCli(LoopbackCase):
    """Every CLI call runs from a cwd that is neither the matter nor the kit."""
    NETBLOCK = os.path.join(KIT, "tests", "netblock_sitecustomize")

    def setUp(self):
        super().setUp()
        self._foreign = tempfile.TemporaryDirectory()
        self.addCleanup(self._foreign.cleanup)
        self.foreign = self._foreign.name
        self.abs_matter = os.path.abspath(self.dir)
        self.out = os.path.join("Media", "captures", "2026-09-18_127.0.0.1", "page.html")
        self.url = self.base + "/page.html"

    def _cli(self, *args, env=None):
        return subprocess.run(
            [sys.executable, MATTER_PY, *args], cwd=self.foreign,
            capture_output=True, text=True, env=dict(os.environ, **(env or {})))

    def _capture_cli(self, *extra):
        return self._cli("capture", self.abs_matter, "--url", self.url, "--out", self.out, *extra)

    def test_capture_then_ingest_then_verify_from_foreign_cwd_is_clean(self):
        r = self._capture_cli()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        abs_out = os.path.join(self.abs_matter, self.out)
        with open(abs_out, "rb") as f:
            self.assertEqual(f.read(), PAGE)
        self.assertEqual(os.listdir(self.foreign), [], "relative --out must not resolve against cwd")
        r = self._cli("ingest", self.abs_matter, abs_out)
        self.assertIn("+0 new, 1 already registered", r.stdout)
        r = self._cli("verify", self.abs_matter)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], f"root={self.abs_matter}")
        self.assertIn("1 ok · 0 changed · 0 missing", r.stdout)

    def test_capture_output_names_path_sha_and_the_verify_gate(self):
        r = self._capture_cli()
        self.assertIn(self.out, r.stdout)
        self.assertIn(hashlib.sha256(PAGE).hexdigest(), r.stdout)
        self.assertIn(f"verify {self.abs_matter}", r.stdout)

    def test_changed_capture_is_a_verify_blocker(self):
        self._capture_cli()
        with open(os.path.join(self.abs_matter, self.out), "ab") as f:
            f.write(b"<!-- tampered -->")
        r = self._cli("verify", self.abs_matter)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"CHANGED: {self.out}", r.stdout)

    def test_missing_capture_is_a_verify_blocker(self):
        self._capture_cli()
        os.remove(os.path.join(self.abs_matter, self.out))
        r = self._cli("verify", self.abs_matter)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"MISSING: {self.out}", r.stdout)

    def test_list_renders_the_provenance_markdown_table(self):
        self._capture_cli()
        r = self._cli("capture", "list", self.abs_matter)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[:2], ["| url | retrieved_at | sha256 | rel_path |", "|---|---|---|---|"])
        self.assertRegex(lines[2], re.escape(f"| {self.url} | ")
                         + r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00"
                         + re.escape(f" | {hashlib.sha256(PAGE).hexdigest()} | {self.out} |"))
        self.assertEqual(len(lines), 3)

    def test_kind_flag_sets_research(self):
        r = self._capture_cli("--kind", "research")
        self.assertEqual(r.returncode, 0, r.stderr)
        row = self.conn.execute("SELECT kind, source_url FROM documents").fetchone()
        self.assertEqual((row["kind"], row["source_url"]), ("research", self.url))

    def test_overwrite_is_refused_nonzero_and_first_bytes_survive(self):
        self._capture_cli()
        self.url = self.base + "/big"
        r = self._capture_cli()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing to overwrite", r.stderr)
        with open(os.path.join(self.abs_matter, self.out), "rb") as f:
            self.assertEqual(f.read(), PAGE)
        self.assertEqual(self._cli("verify", self.abs_matter).returncode, 0)

    def test_redirect_is_refused_nonzero_and_nothing_lands(self):
        self.url = self.base + "/moved"
        r = self._capture_cli()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("redirect", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.abs_matter, self.out)))
        self.assertEqual(self.httpd.seen, ["/moved"])

    def test_list_runs_with_sockets_denied(self):
        self._capture_cli()
        r = self._cli("capture", "list", self.abs_matter, env={"PYTHONPATH": self.NETBLOCK})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(self.out, r.stdout)

    def test_capture_with_sockets_denied_fails_closed(self):
        r = self._cli("capture", self.abs_matter, "--url", self.url, "--out", self.out,
                      env={"PYTHONPATH": self.NETBLOCK})
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.abs_matter, self.out)))
        self.assertEqual(self.httpd.seen, [])

    def test_missing_case_dir_is_nonzero_and_not_created(self):
        missing = os.path.join(self.foreign, "no-such-matter")
        for args in (("capture", missing, "--url", self.url, "--out", self.out),
                     ("capture", "list", missing)):
            r = self._cli(*args)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertFalse(os.path.exists(missing), args)
        self.assertEqual(self.httpd.seen, [])

    def test_capture_without_url_or_out_is_a_usage_error(self):
        for args in (("capture", self.abs_matter),
                     ("capture", self.abs_matter, "--url", self.url),
                     ("capture", "list")):
            r = self._cli(*args)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertIn("usage", r.stderr.lower(), args)


class TestCaptureEmptyBody(CaptureCase):
    def test_empty_body_is_refused_and_nothing_lands(self):
        with self.assertRaises(capture.CaptureError) as cm:
            self._capture(body=b"")
        self.assertIn("empty", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.dir, OUT)))
        n = self.conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        self.assertEqual(n, 0)


class TestCaptureModuleIsNetworkFree(unittest.TestCase):
    def test_capture_core_imports_no_network_modules(self):
        import ast
        banned = {"socket", "urllib", "http", "requests", "httpx", "ssl",
                  "ftplib", "smtplib", "telnetlib", "xmlrpc", "asyncio"}
        tree = ast.parse(open(os.path.join(KIT, "matterkit", "capture.py")).read())
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module] if isinstance(node, ast.ImportFrom) and node.module
                     else [])
            for name in names:
                self.assertNotIn(name.split(".")[0], banned, name)


if __name__ == "__main__":
    unittest.main()

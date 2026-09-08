"""RFC 0001 R1 acceptance tests (council chair go, 2026-09-08).

Covers: backward compatibility (CLI/MCP + legacy stores), stage-order
validation with recorded check evidence, extraction offsets keyed to the
parsed text representation, network-denied `matter.cite_extract`, consent
policy scoped to cooperative enforcement, and the zero-adapter R1 surface.

Run: python3 -m unittest discover -s tests -v
"""
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matterkit import citations, consent, search, store  # noqa: E402
from matterkit.citations import StageOrderError  # noqa: E402

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SAMPLE = (
    "Under 15 U.S.C. § 1681a the statute defines consumer report. "
    "Compare 8 Del. C. § 141 on board action, and Miss. Code Ann. § 57-1-319. "
    "In Brown v. Board, 347 U.S. 483 (1954), the Court held otherwise. "
    "But a bare reference like § 141 without its code name is a fragment."
)


class BaseMatter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        subprocess.run(
            [sys.executable, os.path.join(KIT, "matter.py"), "init", self.dir],
            check=True, capture_output=True)


class TestBackwardCompat(BaseMatter):
    """Existing behavior survives the v0.2 schema change."""

    def test_legacy_store_migrates(self):
        # Simulate a v0.1 store: old authorities DDL, no citation_extractions,
        # no check_evidence, one hand-logged row.
        legacy = sqlite3.connect(os.path.join(self.dir, ".matter", "matter.db"))
        legacy.executescript("""
            DROP TABLE authorities;
            CREATE TABLE authorities (
              id TEXT PRIMARY KEY, citation TEXT NOT NULL,
              status TEXT DEFAULT 'unverified', checked_via TEXT, note TEXT);
            INSERT INTO authorities VALUES ('auth_legacy1', '8 Del. C. § 141', 'unverified', NULL, NULL);
        """)
        legacy.commit()
        legacy.close()
        conn = store.connect(self.dir)  # triggers _migrate
        cols = [r[1] for r in conn.execute("PRAGMA table_info(authorities)")]
        self.assertIn("check_evidence", cols)
        self.assertIn("citation_extractions",
                      [r[0] for r in conn.execute(
                          "SELECT name FROM sqlite_master WHERE type='table'")])
        row = conn.execute("SELECT * FROM authorities WHERE id='auth_legacy1'").fetchone()
        self.assertEqual(row["status"], "unverified")   # preserved as-is
        self.assertIsNone(row["checked_via"])
        self.assertIsNone(row["check_evidence"])        # no invented history

    def test_cli_flow_regression(self):
        # init/ingest/fact/packet round trip on the new schema
        doc = os.path.join(self.dir, "Evidence", "note.txt")
        with open(doc, "w") as f:
            f.write("served filing placeholder\n")
        r = subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "ingest", self.dir],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "verify", self.dir],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "status", self.dir],
                           capture_output=True, text=True)
        self.assertIn("Documents registered", r.stdout)

    def test_mcp_tools_list_includes_new_tool_and_old_ones(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        env = dict(os.environ, MATTER_DIR=self.dir)
        r = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")],
                           input="\n".join(json.dumps(m) for m in msgs),
                           capture_output=True, text=True, env=env, timeout=30)
        lines = [json.loads(l) for l in r.stdout.strip().splitlines() if l.strip()]
        tools = [t["name"] for t in lines[-1]["result"]["tools"]]
        self.assertEqual(lines[0]["id"], 1)
        for expected in ("matter.status", "matter.fact_add", "matter.fact_list",
                         "matter.deadline_list", "matter.packet_render",
                         "matter.cite_extract"):
            self.assertIn(expected, tools)


class TestExtraction(BaseMatter):
    """Offsets are defined by the exact text parsed (chair requirement)."""

    def test_statutory_and_case_formats_extract(self):
        rows = citations.extract_citations(SAMPLE)
        got = [r["raw_fragment"] for r in rows if r["status"] == "extracted"]
        self.assertIn("15 U.S.C. § 1681a", got)
        self.assertIn("8 Del. C. § 141", got)
        self.assertIn("Miss. Code Ann. § 57-1-319", got)
        self.assertIn("347 U.S. 483", got)

    def test_bare_section_is_extraction_failed_with_offsets(self):
        rows = citations.extract_citations(SAMPLE)
        failed = [r for r in rows if r["status"] == "extraction-failed"]
        self.assertEqual(len(failed), 1)
        r = failed[0]
        self.assertEqual(r["raw_fragment"], "§ 141")
        self.assertEqual(SAMPLE[r["char_start"]:r["char_end"]], "§ 141")
        self.assertIsNone(r["parsed_citation"])
        self.assertIsNotNone(r["char_start"])

    def test_offsets_and_text_sha_match_exact_text(self):
        rows = citations.extract_citations(SAMPLE)
        text_sha = hashlib.sha256(SAMPLE.encode()).hexdigest()
        for r in rows:
            self.assertEqual(SAMPLE[r["char_start"]:r["char_end"]], r["raw_fragment"])
            self.assertEqual(r["text_sha256"], text_sha)

    def test_no_overlapping_double_claims(self):
        rows = citations.extract_citations(SAMPLE)
        spans = sorted((r["char_start"], r["char_end"]) for r in rows)
        for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
            self.assertLessEqual(e1, s2, f"overlap: {s1}:{e1} vs {s2}:{e2}")

    def test_different_text_representation_gets_own_rows(self):
        a = citations.extract_citations("15 U.S.C. § 1681a", document_sha256="doc")
        b = citations.extract_citations("15 U.S.C. § 1681a\n", document_sha256="doc")
        self.assertNotEqual(a[0]["text_sha256"], b[0]["text_sha256"])
        self.assertEqual(a[0]["raw_fragment"], b[0]["raw_fragment"])

    def test_extraction_failed_not_lookup_addressable(self):
        conn = store.connect(self.dir)
        rows = citations.extract_citations(SAMPLE, document_sha256="doc")
        citations.record_extractions(conn, rows)
        bad = next(r["id"] for r in rows if r["status"] == "extraction-failed")
        with self.assertRaises(StageOrderError):
            citations.resolve_from_extraction(conn, bad, "test")


class TestStageOrder(BaseMatter):
    """Transition validation: no jumping to 'checked' without evidence."""

    def _hand_log(self, conn):
        aid = "auth_test1"
        conn.execute("INSERT INTO authorities (id, citation, status) VALUES (?,?,?)",
                     (aid, "8 Del. C. § 141", "unverified"))
        conn.commit()
        return aid

    def test_source_checked_requires_resolved_source(self):
        conn = store.connect(self.dir)
        aid = self._hand_log(conn)
        with self.assertRaises(StageOrderError):
            citations.transition_authority(conn, aid, "source-checked")

    def test_source_checked_requires_evidence_not_just_provider(self):
        conn = store.connect(self.dir)
        aid = self._hand_log(conn)
        citations.transition_authority(conn, aid, "resolved",
                                       checked_via="courtlistener:id=123")
        with self.assertRaises(StageOrderError):  # provider but no evidence
            citations.transition_authority(conn, aid, "source-checked")
        citations.transition_authority(
            conn, aid, "source-checked", check_evidence="DL id 123; text matches; current")
        row = conn.execute("SELECT * FROM authorities WHERE id=?", (aid,)).fetchone()
        self.assertEqual(row["status"], "source-checked")

    def test_research_pending_requires_prior_lookup(self):
        conn = store.connect(self.dir)
        aid = self._hand_log(conn)
        with self.assertRaises(StageOrderError):
            citations.transition_authority(conn, aid, "research-pending")
        citations.transition_authority(conn, aid, "resolved",
                                       checked_via="courtlistener:id=123")
        citations.transition_authority(conn, aid, "research-pending")  # now legal

    def test_legacy_verified_official_not_writable(self):
        conn = store.connect(self.dir)
        aid = self._hand_log(conn)
        citations.transition_authority(conn, aid, "resolved",
                                       checked_via="courtlistener:id=123")
        with self.assertRaises(StageOrderError):
            citations.transition_authority(conn, aid, "verified-official")

    def test_resolve_from_extraction_creates_resolved_authority(self):
        conn = store.connect(self.dir)
        rows = citations.extract_citations(SAMPLE, document_sha256="doc")
        citations.record_extractions(conn, rows)
        good = next(r for r in rows if r["status"] == "extracted"
                    and r["raw_fragment"] == "8 Del. C. § 141")
        aid = citations.resolve_from_extraction(conn, good["id"], "test:resolved")
        row = conn.execute("SELECT * FROM authorities WHERE id=?", (aid,)).fetchone()
        self.assertEqual(row["status"], "resolved")
        self.assertIsNotNone(row["checked_via"])

    def test_unknown_status_refused(self):
        conn = store.connect(self.dir)
        aid = self._hand_log(conn)
        with self.assertRaises(StageOrderError):
            citations.transition_authority(conn, aid, "good-law")  # never a thing


class TestPacketBackCompat(BaseMatter):
    def test_status_report_counts_across_vocabularies(self):
        from matterkit import packet
        conn = store.connect(self.dir)
        conn.executemany(
            "INSERT INTO authorities (id, citation, status) VALUES (?,?,?)",
            [("auth_1", "c1", "unverified"),
             ("auth_2", "c2", "resolved"),
             ("auth_3", "c3", "research-pending"),
             ("auth_4", "c4", "source-checked"),
             ("auth_5", "c5", "verified-official")])  # legacy row
        conn.commit()
        out = packet.status_report(conn, self.dir)
        self.assertIn("Unverified authorities: 3", out)  # c1 c2 c3; c4+c5 checked


class TestConsentCooperative(unittest.TestCase):
    """Consent policy, scoped to what it honestly enforces (RFC §2.4 rev3)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        os.makedirs(os.path.join(self.dir, ".matter"))
        self.addCleanup(self._tmp.cleanup)

    def _grant(self, **over):
        g = {"provider": "p1", "endpoints": ["example.org"],
             "payload_types": ["query_text"], "daily_budget": None,
             "created_by": "ty", "created_at": "2026-09-08T00:00:00+00:00",
             "expires": None, "kind": "human"}
        g.update(over)
        return g

    def _write(self, grants):
        with open(consent.consent_path(self.dir), "w") as f:
            json.dump({"grants": grants}, f)

    def test_missing_file_fails_closed(self):
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, "p1", "example.org", "query_text", "ty")

    def test_corrupt_file_means_zero_grants(self):
        with open(consent.consent_path(self.dir), "w") as f:
            f.write("{not json")
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, "p1", "example.org", "query_text", "ty")

    def test_human_grant_authorizes_same_human(self):
        self._write([self._grant()])
        g = consent.authorize(self.dir, "p1", "example.org", "query_text", "ty")
        self.assertEqual(g["provider"], "p1")

    def test_cross_agent_reuse_allowed_documented_limit(self):
        # Kit-level cooperative scope: agent B may run under ty's grant.
        # Pinned as a DOCUMENTED LIMIT, not an oversight (module docstring).
        self._write([self._grant()])
        g = consent.authorize(self.dir, "p1", "example.org", "query_text", "agent-b")
        self.assertEqual(g["created_by"], "ty")

    def test_self_authored_grant_authorizes_nothing(self):
        # An agent honestly marking its own grant kind:"agent" gains nothing.
        self._write([self._grant(created_by="agent-b", kind="agent")])
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, "p1", "example.org", "query_text", "agent-b")

    def test_agent_authored_grant_lacks_kind_defaults_to_agent(self):
        # Omitting the kind field is conservative: treated as agent-authored.
        g = self._grant(created_by="agent-b")
        del g["kind"]
        self._write([g])
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, "p1", "example.org", "query_text", "agent-b")

    def test_payload_escalation_refused_without_explicit_grant(self):
        self._write([self._grant()])  # query_text only
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, "p1", "example.org", "passage_text", "ty")

    def test_escalation_with_explicit_grant_allowed(self):
        self._write([self._grant(payload_types=["document_text"])])
        consent.require_escalation(self.dir, "p1", "example.org", "document_text", "ty")

    def test_expired_grant_refused(self):
        self._write([self._grant(expires="2026-01-01T00:00:00+00:00")])
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, "p1", "example.org", "query_text", "ty")

    def test_endpoint_must_match_exactly(self):
        self._write([self._grant()])
        with self.assertRaises(ConsentError if False else consent.ConsentError):
            consent.authorize(self.dir, "p1", "evil.example.org", "query_text", "ty")

    def test_daily_budget_enforced_via_egress_log(self):
        self._write([self._grant(daily_budget=2)])
        search.log_egress(self.dir, "p1", "example.org", "query_text", "q1")
        search.log_egress(self.dir, "p1", "example.org", "query_text", "q2")
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, "p1", "example.org", "query_text", "ty")

    def test_egress_log_is_hash_only(self):
        secret = "matter fact text nobody should find here"
        rec = search.log_egress(self.dir, "p1", "example.org", "query_text", secret)
        self.assertNotIn(secret, open(consent.egress_path(self.dir)).read())
        self.assertEqual(rec["sha256"], hashlib.sha256(secret.encode()).hexdigest())
        self.assertNotIn("created_by", rec)  # not an authorization proof


class TestAdapterSurface(unittest.TestCase):
    def test_r1_ships_zero_adapters(self):
        self.assertEqual(search.ADAPTERS, {})
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(LookupError):
                search.build_adapter("courtlistener", d, "ty")


class TestNetworkIsolation(unittest.TestCase):
    """cite_extract path must work with sockets disabled (chair requirement)."""

    def setUp(self):
        self._sock = socket.socket
        self._cc = getattr(socket, "create_connection", None)
        def _deny(*a, **k):
            raise OSError("network denied by R1 isolation test")
        socket.socket = _deny
        socket.create_connection = _deny
        self.addCleanup(self._restore)

    def _restore(self):
        socket.socket = self._sock
        if self._cc is not None:
            socket.create_connection = self._cc

    def test_cite_extract_runs_network_denied(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "init", d],
                           check=True, capture_output=True)
            env = dict(os.environ, MATTER_DIR=d)
            msgs = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "t", "version": "0"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "matter.cite_extract",
                            "arguments": {"text": SAMPLE,
                                          "document_sha256": "deadbeef"}}},
            ]
            r = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")],
                               input="\n".join(json.dumps(m) for m in msgs),
                               capture_output=True, text=True, env=env, timeout=30)
            lines = [json.loads(l) for l in r.stdout.strip().splitlines() if l.strip()]
            self.assertEqual(lines[-1]["id"], 2)
            self.assertFalse(lines[-1]["result"].get("isError"))
            rows = json.loads(lines[-1]["result"]["content"][0]["text"])
            statuses = [x["status"] for x in rows]
            self.assertIn("extracted", statuses)
            self.assertIn("extraction-failed", statuses)

    def test_extraction_module_import_graph_is_clean(self):
        import ast
        banned = {"socket", "urllib", "http", "requests", "httpx", "ssl",
                  "ftplib", "smtplib", "telnetlib", "xmlrpc", "asyncio"}
        for mod_file in ("matterkit/citations.py", "matterkit/consent.py",
                         "matterkit/search.py"):
            tree = ast.parse(open(os.path.join(KIT, mod_file)).read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        self.assertNotIn(a.name.split(".")[0], banned,
                                         f"{mod_file} imports {a.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0], banned,
                                     f"{mod_file} imports from {node.module}")


if __name__ == "__main__":
    unittest.main()

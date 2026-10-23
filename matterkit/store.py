"""SQLite matter store. Plain file next to the case material — no server required."""
import os
import secrets
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  rel_path TEXT NOT NULL UNIQUE,
  bytes INTEGER, mtime REAL,
  kind TEXT DEFAULT 'other',
  source_url TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS assertions (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'UNKNOWN',
  contested_by TEXT, created_by TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS assertion_sources (
  assertion_id TEXT NOT NULL REFERENCES assertions(id),
  document_id TEXT NOT NULL REFERENCES documents(id),
  locator TEXT,
  PRIMARY KEY (assertion_id, document_id, locator)
);
CREATE TABLE IF NOT EXISTS deadlines (
  id TEXT PRIMARY KEY, label TEXT NOT NULL, due_date TEXT NOT NULL,
  rule_source TEXT, confidence TEXT DEFAULT 'working-estimate',
  verification_status TEXT DEFAULT 'unverified', state TEXT DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, occurred_on TEXT NOT NULL, text TEXT NOT NULL,
  actors TEXT, sources TEXT
);
CREATE TABLE IF NOT EXISTS parties (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
  capacity TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS issues (
  id TEXT PRIMARY KEY, label TEXT NOT NULL,
  elements_json TEXT DEFAULT '[]', status TEXT DEFAULT 'considered'
);
CREATE TABLE IF NOT EXISTS authorities (
  id TEXT PRIMARY KEY, citation TEXT NOT NULL,
  status TEXT DEFAULT 'unverified', checked_via TEXT, note TEXT,
  check_evidence TEXT
);
CREATE TABLE IF NOT EXISTS citation_extractions (
  id TEXT PRIMARY KEY,
  document_sha256 TEXT,
  text_sha256 TEXT NOT NULL,
  parser TEXT NOT NULL,
  char_start INTEGER, char_end INTEGER,
  raw_fragment TEXT NOT NULL,
  parsed_citation TEXT,
  status TEXT NOT NULL CHECK (status IN ('extracted','extraction-failed')),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS correspondence_notices (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('hold','mpra','foia','touhy','letter','other')),
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','signed','mailed','receipt-hashed','answered','refused')),
  addressee TEXT NOT NULL REFERENCES parties(id),
  address TEXT NOT NULL CHECK (length(trim(address)) > 0),
  exhibit_document_id TEXT NOT NULL REFERENCES documents(id),
  exhibit_sha256 TEXT NOT NULL CHECK (length(exhibit_sha256)=64),
  confirm_due TEXT,
  deadline_id TEXT UNIQUE REFERENCES deadlines(id),
  transport TEXT NOT NULL DEFAULT 'nosend' CHECK (transport='nosend'),
  approval_id TEXT REFERENCES correspondence_approvals(id),
  receipt_document_id TEXT REFERENCES documents(id),
  receipt_sha256 TEXT CHECK (receipt_sha256 IS NULL OR length(receipt_sha256)=64),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK ((confirm_due IS NULL) = (deadline_id IS NULL)),
  CHECK (status='draft' OR approval_id IS NOT NULL),
  CHECK (status IN ('draft','signed') OR
         (receipt_document_id IS NOT NULL AND receipt_sha256 IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS correspondence_assertions (
  notice_id TEXT NOT NULL REFERENCES correspondence_notices(id),
  assertion_id TEXT NOT NULL REFERENCES assertions(id),
  position INTEGER NOT NULL,
  PRIMARY KEY (notice_id, assertion_id)
);
CREATE TABLE IF NOT EXISTS correspondence_approvals (
  id TEXT PRIMARY KEY,
  notice_id TEXT NOT NULL REFERENCES correspondence_notices(id),
  sha256 TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  approved_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS correspondence_history (
  id TEXT PRIMARY KEY,
  notice_id TEXT NOT NULL REFERENCES correspondence_notices(id),
  from_status TEXT,
  to_status TEXT NOT NULL,
  evidence_document_id TEXT REFERENCES documents(id),
  evidence_sha256 TEXT,
  approval_id TEXT REFERENCES correspondence_approvals(id),
  recorded_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

VALID_STATUS = {"DOCUMENTED FACT", "ALLEGATION", "INFERENCE", "HYPOTHESIS", "UNKNOWN"}


def attribution(author: str | None, directed_by: str | None = None, default: str = "human") -> str:
    """Compose the assertions.created_by value.

    `author` is who did the work ("human", or "agent:<model>"). `directed_by`
    records the person who instructed an agent to do it, so agent work is never
    stored as if the person wrote it. Direction is not review or approval.
    Result: "agent:claude-opus-5-5; directed-by:Jane Doe".
    """
    author = (author or default).strip()
    directed_by = (directed_by or "").strip() or None
    for label, value in (("author", author), ("directed-by", directed_by)):
        if value is not None and (not value or any(c in value for c in ";\n\r")):
            raise ValueError(f"{label} must be non-empty and contain no ';' or line breaks")
    if directed_by is None:
        return author
    if not author.startswith("agent:"):
        raise ValueError("directed-by applies to agent work; set author to 'agent:<model>'")
    return f"{author}; directed-by:{directed_by}"


def db_path(matter_dir: str) -> str:
    return os.path.join(matter_dir, ".matter", "matter.db")


def connect(matter_dir: str) -> sqlite3.Connection:
    path = db_path(matter_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Backward-compatible migrations for stores created before v0.2 (RFC 0001)."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(authorities)")]
    if cols and "check_evidence" not in cols:
        conn.execute("ALTER TABLE authorities ADD COLUMN check_evidence TEXT")
        conn.commit()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{secrets.token_hex(6)}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

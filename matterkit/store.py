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
  status TEXT DEFAULT 'unverified', checked_via TEXT, note TEXT
);
"""

VALID_STATUS = {"DOCUMENTED FACT", "ALLEGATION", "INFERENCE", "HYPOTHESIS", "UNKNOWN"}


def db_path(matter_dir: str) -> str:
    return os.path.join(matter_dir, ".matter", "matter.db")


def connect(matter_dir: str) -> sqlite3.Connection:
    path = db_path(matter_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def new_id(prefix: str = "") -> str:
    return f"{prefix}{secrets.token_hex(6)}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

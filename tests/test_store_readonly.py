"""store.connect_readonly never creates, migrates or writes. Synthetic tempdirs only."""
import os
import sqlite3
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import store  # noqa: E402


class TestConnectReadonly(unittest.TestCase):
    def test_missing_store_is_not_created(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                store.connect_readonly(d)
            self.assertFalse(os.path.exists(os.path.join(d, ".matter")))

    def test_writes_refused_and_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, "a case #1? with spaces")
            store.connect(d).close()
            before = open(store.db_path(d), "rb").read()
            conn = store.connect_readonly(d)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO deadlines (id,label,due_date) VALUES ('x','y','2026-01-01')")
            conn.close()
            self.assertEqual(open(store.db_path(d), "rb").read(), before)


if __name__ == "__main__":
    unittest.main()

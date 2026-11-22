"""store.closing_scope: one-shot entry points close every store connection they open."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
from matterkit import store  # noqa: E402


def is_open(conn) -> bool:
    try:
        conn.execute("SELECT 1")
        return True
    except sqlite3.ProgrammingError:
        return False


class TestClosingScope(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        store.create(self.dir)

    def test_every_kind_of_connection_opened_inside_is_closed_on_the_way_out(self):
        with store.closing_scope():
            conns = [store.connect(self.dir), store.connect_existing(self.dir), store.connect_readonly(self.dir)]
            self.assertTrue(all(map(is_open, conns)))
        self.assertFalse(any(map(is_open, conns)))

    def test_closed_on_an_exception_too_and_a_connection_closed_early_is_fine(self):
        with self.assertRaises(RuntimeError):
            with store.closing_scope():
                early = store.connect(self.dir)
                early.close()
                late = store.connect(self.dir)
                raise RuntimeError("boom")
        self.assertFalse(is_open(late))

    def test_a_connection_opened_outside_is_left_alone(self):
        outside = store.connect(self.dir)
        self.addCleanup(outside.close)
        with store.closing_scope():
            pass
        self.assertTrue(is_open(outside))

    def test_a_nested_scope_closes_only_its_own(self):
        with store.closing_scope():
            outer = store.connect(self.dir)
            with store.closing_scope():
                inner = store.connect(self.dir)
            self.assertFalse(is_open(inner))
            self.assertTrue(is_open(outer))
        self.assertFalse(is_open(outer))

    def test_another_threads_connections_are_not_collected(self):
        # The web UI serves each request on its own thread and closes its own connection; a
        # scope on the main thread must not gather them.
        opened, scope_done, result = threading.Event(), threading.Event(), []

        def worker():
            conn = store.connect(self.dir)
            opened.set()
            scope_done.wait(10)
            result.append(is_open(conn))
            conn.close()

        t = threading.Thread(target=worker)
        with store.closing_scope():
            t.start()
            opened.wait(10)
        scope_done.set()
        t.join(10)
        self.assertEqual(result, [True])

    def test_the_cli_leaves_no_connection_open(self):
        for args in (["status"], ["fact", "list"], ["deadline", "list"], ["ingest"]):
            with self.subTest(args=args):
                r = subprocess.run([sys.executable, "-W", "error::ResourceWarning", os.path.join(KIT, "matter.py"),
                                    *args, self.dir], capture_output=True, text=True, timeout=120)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("ResourceWarning", r.stderr)


if __name__ == "__main__":
    unittest.main()

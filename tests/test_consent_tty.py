"""Human TTY activation under a real pseudo-terminal (not a mocked /dev/tty).

Regression: activate_human_grant opened /dev/tty with text mode "r+", which needs
a seekable file. /dev/tty is not seekable, so the open raised
io.UnsupportedOperation (an OSError subclass) and every real activation was
refused as "requires a human controlling TTY".
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from matterkit import consent, store  # noqa: E402

try:
    import pty
except ImportError:  # pragma: no cover - Windows
    pty = None


def _run_activation_in_pty(matter_dir, typed):
    """Run activation in a child whose controlling terminal is a new pty.

    Returns (child exit code, text the child wrote to the terminal).
    """
    pid, fd = pty.fork()
    if pid == 0:  # child: the pty is its controlling terminal
        try:
            consent.activate_human_grant(
                matter_dir, "courtlistener-free", ["www.courtlistener.com"],
                ["query_text"], "ty", authorized_callers=["docket"])
        except consent.ConsentError:
            os._exit(3)
        except BaseException:
            os._exit(4)
        os._exit(0)
    seen = b""
    while b"> " not in seen:
        try:
            chunk = os.read(fd, 1024)
        except OSError:
            break
        if not chunk:
            break
        seen += chunk
    if b"> " in seen:  # the child only reads a reply after it has prompted
        os.write(fd, typed.encode() + b"\n")
    # Keep draining the master: the child's last close of the tty waits for its
    # output (our echoed reply) to be read, as a real terminal window would.
    while True:
        try:
            chunk = os.read(fd, 1024)
        except OSError:
            break
        if not chunk:
            break
        seen += chunk
    _, status = os.waitpid(pid, 0)
    os.close(fd)
    return os.waitstatus_to_exitcode(status), seen.decode(errors="replace")


@unittest.skipIf(pty is None, "needs a POSIX pty")
class TestActivateUnderRealTTY(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        store.connect(self.dir)
        consent.stage_proposal(self.dir, "courtlistener-free",
                               ["www.courtlistener.com"], ["query_text"], "lead")

    def test_typed_activate_creates_human_grant(self):
        code, shown = _run_activation_in_pty(self.dir, "ACTIVATE")
        self.assertIn("Type ACTIVATE", shown)
        self.assertEqual(code, 0, "activation under a real TTY was refused")
        grants = consent.load_grants(self.dir)
        human = [g for g in grants if g.get("kind") == "human"]
        self.assertEqual(len(human), 1)
        self.assertEqual(human[0]["status"], "active")
        self.assertEqual(human[0]["activated_via"], "tty")
        self.assertEqual(human[0]["authorized_callers"], ["docket"])
        self.assertFalse([g for g in grants if g.get("status") == "proposal"])

    def test_anything_but_activate_aborts(self):
        code, _ = _run_activation_in_pty(self.dir, "yes")
        self.assertEqual(code, 3)
        self.assertFalse([g for g in consent.load_grants(self.dir)
                          if g.get("kind") == "human"])


if __name__ == "__main__":
    unittest.main()

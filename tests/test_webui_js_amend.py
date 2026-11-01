"""Runs the browser-side behaviour tests for the "Next amendment" tab under Node.

tests/webui_js/amend.mjs loads the real matterkit/webui_static/app.js into the same node:vm context and
hand-written DOM as tests/webui_js/run.mjs (see test_webui_js.py), with a fake fetch whose writes the tests
answer by hand. It covers what the static checks can't: late answers after the user moved to another case,
out-of-order list replies, a button pressed twice, and the overview count. Node built-ins only.

Skipped, with the reason, when Node 18 or newer isn't on PATH. Run alone: node tests/webui_js/amend.mjs
"""
import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "webui_js", "amend.mjs")
MIN_NODE = 18  # node:test


class TestAmendmentTabUnderNode(unittest.TestCase):
    def test_amendment_tab_behaviour(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not on PATH; the browser-side behaviour tests need Node %d or newer" % MIN_NODE)
        version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        m = re.match(r"v(\d+)\.", version)
        if not m or int(m.group(1)) < MIN_NODE:
            self.skipTest("node %r is older than %d; the behaviour tests use node:test" % (version, MIN_NODE))
        env = {k: v for k, v in os.environ.items() if k != "MATTERKIT_APP_JS"}  # always test the shipped file
        done = subprocess.run([node, HARNESS], capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(done.returncode, 0, "node %s exited %d\n--- stdout ---\n%s\n--- stderr ---\n%s"
                         % (version, done.returncode, done.stdout, done.stderr))


if __name__ == "__main__":
    unittest.main()

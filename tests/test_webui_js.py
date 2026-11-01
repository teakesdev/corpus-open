"""Runs the browser-side behaviour tests for the local UI under Node.

tests/webui_js/run.mjs loads the real matterkit/webui_static/app.js into a node:vm context with a small
hand-written DOM and a fake fetch whose replies the tests release by hand. That covers what the static
checks can't: late answers, out-of-order answers and report tables. Node built-ins only, no packages.

Skipped, with the reason, when Node 18 or newer isn't on PATH. Run alone: node tests/webui_js/run.mjs
"""
import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "webui_js", "run.mjs")
MIN_NODE = 18  # node:test


class TestUiBehaviourUnderNode(unittest.TestCase):
    def test_app_js_behaviour(self):
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

"""docs/reference.md names every command and tool the kit has, so a new one can't ship undocumented."""
import os
import re
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(KIT, *parts), encoding="utf-8") as f:
        return f.read()


class TestReferenceNamesEverything(unittest.TestCase):
    def setUp(self):
        self.ref = _read("docs", "reference.md")

    def test_every_matter_py_command(self):
        names = set(re.findall(r'\bsub\.add_parser\("([a-z-]+)"', _read("matter.py")))
        self.assertGreater(len(names), 20)
        for name in sorted(names):
            self.assertTrue("matter.py " + name in self.ref, name)

    def test_every_mcp_tool(self):
        names = set(re.findall(r'\{"name": "(matter\.[a-z_]+)"', _read("matter-mcp.py")))
        self.assertGreater(len(names), 20)
        for name in sorted(names):
            self.assertTrue("`" + name + "`" in self.ref, name)

    def test_every_slash_matter_command(self):
        words = set(re.findall(r'^\s+if sub == "([a-z]+)":', _read("hermes_plugin", "__init__.py"), re.M))
        self.assertGreater(len(words), 8)
        for word in sorted(words | {"status"}):
            self.assertTrue("/matter " + word in self.ref, word)

if __name__ == "__main__":
    unittest.main()

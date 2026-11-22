"""Release bookkeeping: every place that states the version agrees, and the changelog covers it."""
import importlib
import json
import os
import re
import unittest

from test_hermes_plugin import PKG  # loads the plugin as a package, as Hermes does

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(KIT, *parts), encoding="utf-8") as f:
        return f.read()


def plugin_version():
    m = re.search(r'^version:\s*"([^"]+)"', _read("plugin.yaml"), re.M)
    return m.group(1)


class TestVersionAgrees(unittest.TestCase):
    def test_dashboard_manifest(self):
        self.assertEqual(json.loads(_read("dashboard", "manifest.json"))["version"], plugin_version())

    def test_mcp_server_info(self):
        m = re.search(r'^VERSION = "([^"]+)"', _read("matter-mcp.py"), re.M)
        self.assertEqual(m.group(1), plugin_version())
        self.assertIn('"version": VERSION', _read("matter-mcp.py"))

    def test_changelog_has_a_section(self):
        self.assertRegex(_read("CHANGELOG.md"), r"(?m)^## \[" + re.escape(plugin_version()) + r"\]")

    def test_release_notes_exist(self):
        self.assertTrue(os.path.isfile(os.path.join(KIT, "docs", "releases", plugin_version() + ".md")))


class TestToolsAgree(unittest.TestCase):
    """plugin.yaml's provides_tools, the tool schemas and the manual name the same Hermes tools."""

    def setUp(self):
        tools = importlib.import_module(PKG + ".hermes_plugin.tools")
        self.tools = tools
        self.schemas = set(tools.SCHEMAS)
        y = _read("plugin.yaml")
        block = re.search(r"^provides_tools:\n((?:  - .+\n)+)", y, re.M).group(1)
        self.declared = {line.strip()[2:] for line in block.splitlines()}

    def test_manifest_matches_schemas(self):
        self.assertEqual(self.declared, self.schemas)

    def test_each_schema_has_a_handler(self):
        for name in self.schemas:
            self.assertTrue(callable(getattr(self.tools.Tools, name, None)), name)

    def test_manual_names_each_tool(self):
        ref = _read("docs", "reference.md")
        for name in self.schemas:
            self.assertIn(name, ref)

    def test_counts_in_words_match(self):
        words = {12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen"}
        n = words[len(self.schemas)]
        self.assertIn(n + " tools", _read("README.md").lower())
        self.assertIn(n + " tools", " ".join(_read("plugin.yaml").lower().split()))
        self.assertIn("**" + n + " tools**", _read("docs", "reference.md").lower())


if __name__ == "__main__":
    unittest.main()

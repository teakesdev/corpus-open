"""The kit works when Hermes loads it as a plugin package, with no top-level `matterkit` on sys.path.

Hermes imports the plugin from its own folder under a package name of its choosing, so every import
inside the kit must be relative. An absolute `matterkit.…` import works in the tests (which put the
kit on sys.path) and in the CLI, and fails only inside Hermes. Registering a new file once did:
`/matter demo` answered "No module named 'matterkit'".

The check runs in a fresh, isolated interpreter (-I) so this test process's sys.path can't hide it.
"""
import os
import subprocess
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = r"""
import importlib, importlib.util, io, os, sys, contextlib
kit, case = sys.argv[1], sys.argv[2]
assert not any(os.path.realpath(p or ".") == os.path.realpath(kit) for p in sys.path)
spec = importlib.util.spec_from_file_location("hermes_loaded_pkg", os.path.join(kit, "__init__.py"),
                                              submodule_search_locations=[kit])
mod = importlib.util.module_from_spec(spec)
sys.modules["hermes_loaded_pkg"] = mod
spec.loader.exec_module(mod)
store = importlib.import_module("hermes_loaded_pkg.matterkit.store")
ingest = importlib.import_module("hermes_loaded_pkg.matterkit.ingest")
os.makedirs(os.path.join(case, "Evidence"))
with open(os.path.join(case, "Evidence", "note.txt"), "w") as f:
    f.write("synthetic")
conn = store.connect(case)
with contextlib.redirect_stdout(io.StringIO()):
    ingest.ingest_paths(conn, case, [case])
print(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
"""


class PluginPackageImports(unittest.TestCase):
    def test_ingest_registers_a_file_without_a_top_level_matterkit(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.run([sys.executable, "-I", "-c", SCRIPT, KIT, os.path.join(tmp, "case")],
                               capture_output=True, text=True, cwd=tmp, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr[-2000:])
            self.assertEqual(p.stdout.strip().splitlines()[-1], "1")


if __name__ == "__main__":
    unittest.main()

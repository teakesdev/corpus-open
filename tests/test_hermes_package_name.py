"""The Hermes plugin loaded the way Hermes really loads it: the repo folder imported as a package
under a name Hermes picks (hermes_plugins.<name>), with no top-level `matterkit` to fall back on.

test_hermes_plugin.py puts the repo on sys.path, so a module inside matterkit/ that names
`matterkit` absolutely still imports there; in Hermes it fails with "No module named
'matterkit'" (seen live from /matter demo). Here a child process runs with `python -I`, the repo
not on sys.path and the kit reachable only as hermes_plugins_x.corpus_open (a symlink to the
repo), then drives register(ctx), /matter demo, matter_ingest_path and the search adapter
registry. HOME points at a temp folder, so nothing reads or writes the person's own settings,
and the case folders are temp folders too.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT = "hermes_plugins_x"
PKG = PARENT + ".corpus_open"

CHILD = textwrap.dedent("""
    import contextlib, importlib, importlib.util, json, os, sys

    root, case = sys.argv[1], sys.argv[2]
    sys.path.insert(0, root)
    out = {"top_level_findable": importlib.util.find_spec("matterkit") is not None}

    def step(name, fn):
        try:
            out[name] = fn()
        except Exception as e:  # report every step, not just the first failure
            out[name] = {"error": f"{type(e).__name__}: {e}"}

    class Ctx:
        def __init__(self):
            self.settings, self.tools, self.hooks, self.commands = {}, {}, {}, {}
        def register_tool(self, name, toolset, schema, handler, **kw):
            self.tools[name] = handler
        def register_hook(self, name, fn, **kw):
            self.hooks[name] = fn
        def register_command(self, name, handler, **kw):
            self.commands[name] = handler
        def get_config(self, key, default=None):
            return self.settings.get(key, default)
        def set_config(self, key, value):
            self.settings[key] = value

    ctx = Ctx()
    pkg = importlib.import_module(PKG)
    pkg.register(ctx)
    out["demo"] = ctx.commands["matter"]("demo " + case)
    out["matter_dir"] = ctx.settings.get("matter_dir")

    def counts():
        store = importlib.import_module(PKG + ".matterkit.store")
        dl = importlib.import_module(PKG + ".matterkit.deadlines")
        with contextlib.closing(store.connect(case)) as conn:
            docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            return {"documents": docs, "deadlines": len(dl.select(conn))}
    step("counts", counts)

    def ingest():  # its own empty case, so a failed demo can't hide this result
        fresh = case + "-ingest"
        out["init"] = ctx.commands["matter"]("init " + fresh)
        note = os.path.join(fresh, "Evidence", "new-note.txt")
        with open(note, "w", encoding="utf-8") as f:
            f.write("A synthetic note added after the demo.\\n")
        return json.loads(ctx.tools["matter_ingest_path"]({"path": note}))
    step("ingest", ingest)

    def adapters():
        search = importlib.import_module(PKG + ".matterkit.search")
        return {p: search._load_adapter(p).__module__ for p in sorted(search.ADAPTERS)}
    step("adapters", adapters)

    out["top_level_loaded"] = sorted(m for m in sys.modules if m == "matterkit" or m.startswith("matterkit."))
    print(json.dumps(out))
""").replace("PKG", repr(PKG))


class TestPluginUnderHermesPackageName(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = cls._tmp.name
        root = os.path.join(tmp, "plugins")
        os.makedirs(os.path.join(root, PARENT))
        open(os.path.join(root, PARENT, "__init__.py"), "w").close()
        os.symlink(KIT, os.path.join(root, PARENT, "corpus_open"))
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        case = os.path.join(tmp, "demo-case")
        env = {"HOME": home, "PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
               "LANG": "C.UTF-8"}
        cls.proc = subprocess.run([sys.executable, "-I", "-B", "-c", CHILD, root, case], cwd=tmp, env=env,
                                  capture_output=True, text=True, timeout=120)
        cls.case = case
        try:
            cls.out = json.loads(cls.proc.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            cls.out = None

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertIsNotNone(self.out, self.proc.stdout + self.proc.stderr)

    def test_matterkit_is_not_importable_at_top_level(self):
        """The negative control: if a top-level matterkit were findable this test proves nothing."""
        self.assertFalse(self.out["top_level_findable"])
        self.assertEqual(self.out["top_level_loaded"], [])

    def test_matter_demo_makes_the_case(self):
        self.assertTrue(self.out["demo"].startswith("Made a synthetic demo case"), self.out["demo"])
        self.assertEqual(self.out["matter_dir"], self.case)
        self.assertNotIn("error", self.out["counts"], self.out["counts"])
        self.assertGreater(self.out["counts"]["documents"], 0)
        self.assertEqual(self.out["counts"]["deadlines"], 2)

    def test_matter_ingest_path_registers_a_new_file(self):
        self.assertTrue(self.out["init"].startswith("Case folder ready"), self.out["init"])
        self.assertEqual(self.out["ingest"].get("ok"), True, self.out["ingest"])
        self.assertEqual(self.out["ingest"]["result"]["added"], 1)

    def test_search_adapters_load_from_the_plugin_package(self):
        self.assertNotIn("error", self.out["adapters"], self.out["adapters"])
        self.assertEqual(self.out["adapters"], {
            "corpus-search": PKG + ".matterkit.adapters.corpus_search",
            "courtlistener-free": PKG + ".matterkit.adapters.courtlistener",
        })


if __name__ == "__main__":
    unittest.main()

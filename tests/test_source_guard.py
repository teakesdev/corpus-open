"""The Hermes plugin's source guard (hermes_plugin/source_guard.py, pre_tool_call).

A registered source document, and anything under .matter/, may not be written through Hermes's
file tools: write_file and patch are blocked. A terminal command or code run that names one and
looks like it changes files goes to the person's approval instead, because reading a command's
text is a guess. Everything else passes untouched. The hook never raises: Hermes blocks every tool
call when a pre_tool_call hook raises, so a bug here must not stop the agent.

Case folders are synthetic and live in a temp folder. No test reaches the network.
"""
import importlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import ingest, store  # noqa: E402

PKG = "corpus_open_hermes_test"


def load_plugin():
    if PKG in sys.modules:
        return sys.modules[PKG]
    spec = importlib.util.spec_from_file_location(PKG, os.path.join(KIT, "__init__.py"),
                                                  submodule_search_locations=[KIT])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = mod
    spec.loader.exec_module(mod)
    return mod


load_plugin()
hp = importlib.import_module(PKG + ".hermes_plugin")
sg = importlib.import_module(PKG + ".hermes_plugin.source_guard")


class Ctx:
    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        self.tools, self.hooks, self.commands = {}, {}, {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (toolset, schema, handler)

    def register_hook(self, name, fn, **kw):
        self.hooks[name] = fn

    def register_command(self, name, handler, **kw):
        self.commands[name] = handler

    def get_config(self, key, default=None):
        return self.settings.get(key, default)


def make_matter(root, files):
    """A case folder at root with each of files written and registered."""
    os.makedirs(root, exist_ok=True)
    conn = store.connect(root)
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
    with redirect_stdout(io.StringIO()):
        ingest.ingest_paths(conn, root, [os.path.join(root, r) for r in files])
    conn.commit()
    conn.close()
    return root


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)
        self.case = make_matter(os.path.join(self.base, "case"),
                                {"Evidence/lease.txt": "lease terms\n",
                                 "Filings/complaint.txt": "complaint\n"})
        self.src = os.path.join(self.case, "Evidence", "lease.txt")
        with open(os.path.join(self.case, "draft.md"), "w") as f:
            f.write("my draft\n")

    def check(self, tool, args, matter_dir=None):
        return sg.check(tool, args, matter_dir=matter_dir)


class FileTools(Base):
    def test_write_file_to_a_source_is_blocked_and_names_it(self):
        d = self.check("write_file", {"path": self.src, "content": "changed"})
        self.assertEqual(d["action"], "block")
        self.assertIn("Evidence/lease.txt", d["message"])
        conn = store.connect_readonly(self.case)
        sha = conn.execute("SELECT sha256 FROM documents WHERE rel_path='Evidence/lease.txt'").fetchone()[0]
        self.assertIn(sha[:12], d["message"])

    def test_patch_on_a_source_is_blocked(self):
        d = self.check("patch", {"path": self.src, "old_string": "lease", "new_string": "LEASE"})
        self.assertEqual(d["action"], "block")

    def test_new_file_and_unregistered_draft_in_the_case_pass(self):
        self.assertIsNone(self.check("write_file", {"path": os.path.join(self.case, "Drafts", "brief.md"),
                                                    "content": "x"}))
        self.assertIsNone(self.check("patch", {"path": os.path.join(self.case, "draft.md"),
                                               "old_string": "my", "new_string": "our"}))

    def test_anything_under_dot_matter_is_blocked(self):
        for rel in (".matter/matter.db", ".matter/consent.json", ".matter/out/new.txt"):
            d = self.check("write_file", {"path": os.path.join(self.case, rel), "content": "{}"})
            self.assertEqual(d["action"], "block", rel)
            self.assertIn(".matter", d["message"])

    def test_relative_path_is_read_against_the_case_folder_setting(self):
        d = self.check("write_file", {"path": "Evidence/lease.txt", "content": "x"}, matter_dir=self.case)
        self.assertEqual(d["action"], "block")

    def test_relative_path_is_read_against_the_cwd(self):
        old = os.getcwd()
        os.chdir(self.case)
        self.addCleanup(os.chdir, old)
        d = self.check("write_file", {"path": "Evidence/lease.txt", "content": "x"})
        self.assertEqual(d["action"], "block")

    def test_tilde_and_dotdot_paths_resolve(self):
        d = self.check("write_file", {"path": os.path.join(self.case, "Drafts", "..", "Evidence", "lease.txt"),
                                      "content": "x"})
        self.assertEqual(d["action"], "block")
        with mock.patch.dict(os.environ, {"HOME": self.base}):
            d = self.check("write_file", {"path": "~/case/Evidence/lease.txt", "content": "x"})
        self.assertEqual(d["action"], "block")

    def test_a_link_outside_the_case_that_points_at_a_source_is_blocked(self):
        link = os.path.join(self.base, "elsewhere.txt")
        os.symlink(self.src, link)
        d = self.check("write_file", {"path": link, "content": "x"})
        self.assertEqual(d["action"], "block")
        self.assertIn("Evidence/lease.txt", d["message"])

    def test_outside_any_case_passes(self):
        self.assertIsNone(self.check("write_file", {"path": os.path.join(self.base, "notes.txt"),
                                                    "content": "x"}))

    def test_subcase_sources_and_parent_sources_are_both_protected(self):
        sub = make_matter(os.path.join(self.case, "subcases", "dale"), {"Evidence/post.txt": "post\n"})
        d = self.check("write_file", {"path": os.path.join(sub, "Evidence", "post.txt"), "content": "x"})
        self.assertEqual(d["action"], "block")
        d = self.check("write_file", {"path": self.src, "content": "x"})
        self.assertEqual(d["action"], "block")

    def test_an_unreadable_store_sends_the_write_to_approval(self):
        with open(store.db_path(self.case), "wb") as f:
            f.write(b"not a database" * 100)
        d = self.check("write_file", {"path": self.src, "content": "x"})
        self.assertEqual(d["action"], "approve")
        self.assertIn("could not", d["message"])

    def test_v4a_patch_naming_a_source_is_blocked(self):
        text = ("*** Begin Patch\n*** Update File: Evidence/lease.txt\n@@\n-lease terms\n+changed\n"
                "*** End Patch\n")
        d = self.check("patch", {"mode": "patch", "patch": text}, matter_dir=self.case)
        self.assertEqual(d["action"], "block")
        text = "*** Begin Patch\n*** Delete File: %s\n*** End Patch\n" % self.src
        self.assertEqual(self.check("patch", {"mode": "patch", "patch": text})["action"], "block")
        text = ("*** Begin Patch\n*** Update File: draft.md\n*** Move to: Filings/complaint.txt\n"
                "*** End Patch\n")
        self.assertEqual(self.check("patch", {"mode": "patch", "patch": text},
                                    matter_dir=self.case)["action"], "block")


class Commands(Base):
    def test_a_command_that_changes_a_named_source_goes_to_approval(self):
        for cmd in (f"rm {self.src}",
                    f"sed -i 's/lease/LEASE/' {self.src}",
                    f"echo x > {self.src}",
                    f"mv {self.src} /tmp/x",
                    f"echo '{{}}' > {self.case}/.matter/consent.json",
                    f"sqlite3 {self.case}/.matter/matter.db 'DELETE FROM documents'"):
            d = self.check("terminal", {"command": cmd})
            self.assertIsNotNone(d, cmd)
            self.assertEqual(d["action"], "approve", cmd)
            self.assertTrue(d.get("rule_key", "").startswith("corpus-open:"), cmd)

    def test_relative_names_count_when_the_case_folder_is_set(self):
        d = self.check("terminal", {"command": "rm Evidence/lease.txt"}, matter_dir=self.case)
        self.assertEqual(d["action"], "approve")

    def test_reading_commands_pass(self):
        for cmd in (f"cat {self.src}", f"sha256sum {self.src}", f"grep -n lease {self.src}",
                    "ls -la", f"python3 matter.py verify {self.case}"):
            self.assertIsNone(self.check("terminal", {"command": cmd}, matter_dir=self.case), cmd)

    def test_changing_files_that_are_not_sources_passes(self):
        self.assertIsNone(self.check("terminal", {"command": f"rm {self.case}/draft.md"}, matter_dir=self.case))
        self.assertIsNone(self.check("terminal", {"command": "rm -rf /tmp/scratch"}, matter_dir=self.case))

    def test_code_that_writes_a_source_goes_to_approval_and_reading_passes(self):
        d = self.check("execute_code", {"code": f"open({self.src!r}, 'w').write('x')"})
        self.assertEqual(d["action"], "approve")
        d = self.check("execute_code", {"code": f"import os\nos.remove({self.src!r})"})
        self.assertEqual(d["action"], "approve")
        self.assertIsNone(self.check("execute_code", {"code": f"print(open({self.src!r}).read())"}))


class NeverRaises(Base):
    def test_other_tools_and_odd_arguments_pass(self):
        self.assertIsNone(self.check("web_search", {"query": self.src}))
        for args in (None, {}, {"path": 7}, {"path": ""}, {"command": None}, {"patch": 3, "mode": "patch"}, "x"):
            for tool in ("write_file", "patch", "terminal", "execute_code"):
                self.assertIsNone(self.check(tool, args), (tool, args))

    def test_an_internal_error_inside_a_case_asks_for_approval(self):
        plugin = hp.register(Ctx({"matter_dir": self.case}))
        orig = sg._documents
        sg._documents = lambda root: (_ for _ in ()).throw(RuntimeError("boom"))
        self.addCleanup(setattr, sg, "_documents", orig)
        d = plugin.on_tool_call(tool_name="write_file", args={"path": self.src, "content": "x"}, task_id="t")
        self.assertEqual(d["action"], "approve")

    def test_an_internal_error_outside_any_case_passes(self):
        plugin = hp.register(Ctx({"matter_dir": self.case}))
        orig = sg.check
        sg.check = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        self.addCleanup(setattr, sg, "check", orig)
        self.assertIsNone(plugin.on_tool_call(tool_name="write_file",
                                              args={"path": os.path.join(self.base, "n.txt")}, task_id="t"))
        d = plugin.on_tool_call(tool_name="write_file", args={"path": self.src}, task_id="t")
        self.assertEqual(d["action"], "approve")


class Registration(Base):
    def test_the_plugin_registers_the_hook_and_the_manifest_declares_it(self):
        ctx = Ctx({"matter_dir": self.case})
        hp.register(ctx)
        self.assertIn("pre_tool_call", ctx.hooks)
        d = ctx.hooks["pre_tool_call"](tool_name="write_file", args={"path": "Evidence/lease.txt"}, task_id="t")
        self.assertEqual(d["action"], "block")
        with open(os.path.join(KIT, "plugin.yaml")) as f:
            manifest = f.read()
        hooks = manifest.split("provides_hooks:")[1].split("\n\n")[0].split("requires_env")[0]
        self.assertIn("- pre_tool_call", hooks)
        self.assertIn("source_guard:", manifest)

    def test_the_setting_turns_it_off(self):
        ctx = Ctx({"matter_dir": self.case, "source_guard": False})
        hp.register(ctx)
        self.assertIsNone(ctx.hooks["pre_tool_call"](tool_name="write_file", args={"path": self.src}, task_id="t"))
        ctx = Ctx({"matter_dir": self.case, "source_guard": "off"})
        hp.register(ctx)
        self.assertIsNone(ctx.hooks["pre_tool_call"](tool_name="write_file", args={"path": self.src}, task_id="t"))


if __name__ == "__main__":
    unittest.main()

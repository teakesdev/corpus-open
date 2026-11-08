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
import shutil
import sys
import tempfile
import unicodedata
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


def add_source(root, rel, text):
    """Write rel in the case at root and register it."""
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    conn = store.connect(root)
    with redirect_stdout(io.StringIO()):
        ingest.ingest_paths(conn, root, [p])
    conn.commit()
    conn.close()
    return p


def same_file_on_this_volume(name, other):
    """True when the temp folder's volume opens other as the file made under name."""
    d = tempfile.mkdtemp()
    try:
        open(os.path.join(d, name), "w").close()
        return os.path.exists(os.path.join(d, other))
    finally:
        shutil.rmtree(d)


NFC_CAFE, NFD_CAFE = "café", "café"
assert unicodedata.normalize("NFD", NFC_CAFE) == NFD_CAFE
# macOS's default APFS is both; Linux CI is neither. The tests that need a real alias skip there.
CASE_INSENSITIVE = same_file_on_this_volume("Probe", "pROBE")
NORM_INSENSITIVE = same_file_on_this_volume(NFC_CAFE, NFD_CAFE)


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


class Identity(Base):
    """A path is protected for the file it opens, not for how it is spelled (Grok 4.7 round 2, P1).

    macOS opens 'evidence/lease.txt' and '.Matter/consent.json' as the registered files, and an NFD
    name as the NFC file. The guard compares file identity (st_dev, st_ino) and, as a second layer
    for names that don't exist yet or only appear in a command, case-folded NFC names.
    """

    def setUp(self):
        super().setUp()
        self.cafe = add_source(self.case, f"Evidence/{NFC_CAFE}.txt", "photo log\n")

    def assertBlocks(self, tool, args, names=None, matter_dir=None):
        d = self.check(tool, args, matter_dir=matter_dir)
        self.assertIsNotNone(d, (tool, args))
        self.assertEqual(d["action"], "block", (tool, args))
        if names:
            self.assertIn(names, d["message"])

    def test_case_variants_of_a_source_are_blocked(self):
        for rel in ("evidence/lease.txt", "EVIDENCE/LEASE.TXT", "Evidence/Lease.txt"):
            self.assertBlocks("write_file", {"path": rel, "content": "CHANGED"}, "Evidence/lease.txt",
                              matter_dir=self.case)
            self.assertBlocks("write_file", {"path": os.path.join(self.case, rel), "content": "x"},
                              "Evidence/lease.txt")
            self.assertBlocks("patch", {"path": rel, "old_string": "lease", "new_string": "x"},
                              matter_dir=self.case)

    def test_case_variants_of_the_store_are_blocked(self):
        for rel in (".Matter/consent.json", ".MATTER/matter.db", ".mAtTeR/out/new.txt"):
            self.assertBlocks("write_file", {"path": rel, "content": '{"kind": "human"}'}, ".matter",
                              matter_dir=self.case)
            self.assertBlocks("write_file", {"path": os.path.join(self.case, rel), "content": "{}"})

    def test_an_nfd_name_of_an_nfc_source_is_blocked(self):
        nfd = os.path.join(self.case, "Evidence", NFD_CAFE + ".txt")
        self.assertBlocks("write_file", {"path": nfd, "content": "x"}, f"Evidence/{NFC_CAFE}.txt")
        self.assertBlocks("write_file", {"path": f"evidence/{NFD_CAFE.upper()}.TXT", "content": "x"},
                          matter_dir=self.case)

    def test_v4a_patches_with_case_and_nfd_variants_are_blocked(self):
        for line in ("*** Update File: evidence/lease.txt",
                     "*** Add File: .Matter/consent.json",
                     "*** Update File: draft.md\n*** Move to: filings/COMPLAINT.txt",
                     f"*** Delete File: Evidence/{NFD_CAFE}.txt"):
            text = f"*** Begin Patch\n{line}\n@@\n-a\n+b\n*** End Patch\n"
            self.assertBlocks("patch", {"mode": "patch", "patch": text}, matter_dir=self.case)

    def test_commands_naming_case_and_nfd_variants_go_to_approval(self):
        for cmd in ("rm evidence/lease.txt",
                    "echo x > .Matter/consent.json",
                    "sqlite3 .MATTER/matter.db 'DELETE FROM documents'",
                    "sed -i 's/a/b/' EVIDENCE/lease.txt",
                    "rm -rf evidence",
                    f"mv Evidence/{NFD_CAFE}.txt /tmp/x"):
            d = self.check("terminal", {"command": cmd}, matter_dir=self.case)
            self.assertIsNotNone(d, cmd)
            self.assertEqual(d["action"], "approve", cmd)
        for code in ("open('evidence/lease.txt', 'w').write('x')",
                     "import os\nos.remove('.Matter/consent.json')"):
            d = self.check("execute_code", {"code": code}, matter_dir=self.case)
            self.assertIsNotNone(d, code)
            self.assertEqual(d["action"], "approve", code)

    def test_a_hard_link_to_a_source_is_blocked_by_identity(self):
        # A hard link is the same file under a name that matches nothing; only identity sees it.
        inside = os.path.join(self.case, "Drafts", "lease-copy.txt")
        os.makedirs(os.path.dirname(inside))
        os.link(self.src, inside)
        self.assertBlocks("write_file", {"path": inside, "content": "x"}, "Evidence/lease.txt")
        self.assertBlocks("patch", {"path": "Drafts/lease-copy.txt", "old_string": "a", "new_string": "b"},
                          matter_dir=self.case)
        d = self.check("terminal", {"command": f"echo x > {inside}"})
        self.assertEqual(d["action"], "approve")
        # Outside any case, the configured case's sources are still known.
        outside = os.path.join(self.base, "lease-copy.txt")
        os.link(self.src, outside)
        self.assertBlocks("write_file", {"path": outside, "content": "x"}, "Evidence/lease.txt",
                          matter_dir=self.case)

    def test_new_drafts_still_pass(self):
        for rel in ("Drafts/brief.md", "drafts/brief.md", "evidence/lease-notes.md",
                    f"Evidence/{NFD_CAFE}-notes.md", "matter-notes/x.md", ".matterish/x.md", "DRAFT.md"):
            self.assertIsNone(self.check("write_file", {"path": rel, "content": "x"}, matter_dir=self.case),
                              rel)
        self.assertIsNone(self.check("terminal", {"command": "echo x > drafts/brief.md"},
                                     matter_dir=self.case))

    # -- the volume really aliases these names; identity alone must catch them -----------------
    @unittest.skipUnless(CASE_INSENSITIVE, "the temp folder's volume is case-sensitive")
    def test_on_a_case_insensitive_volume_identity_alone_blocks(self):
        self.assertTrue(os.path.samefile(os.path.join(self.case, "evidence", "lease.txt"), self.src))
        self.assertTrue(os.path.samefile(os.path.join(self.case, ".Matter"),
                                         os.path.join(self.case, ".matter")))
        # Turn the name layer off: what is left is the identity check.
        with mock.patch.object(sg, "_fold", lambda s: s, create=True):
            self.assertBlocks("write_file", {"path": "evidence/lease.txt", "content": "CHANGED"},
                              "Evidence/lease.txt", matter_dir=self.case)
            for rel in (".Matter/consent.json", ".MATTER/matter.db", ".Matter/out/new.txt"):
                self.assertBlocks("write_file", {"path": rel, "content": "{}"}, ".matter",
                                  matter_dir=self.case)
            for cmd in ("rm evidence/lease.txt", "echo x > .Matter/consent.json"):
                d = self.check("terminal", {"command": cmd}, matter_dir=self.case)
                self.assertIsNotNone(d, cmd)
                self.assertEqual(d["action"], "approve", cmd)
            self.assertIsNone(self.check("write_file", {"path": "drafts/brief.md", "content": "x"},
                                         matter_dir=self.case))

    @unittest.skipUnless(NORM_INSENSITIVE, "the temp folder's volume keeps NFC and NFD names apart")
    def test_on_a_normalization_insensitive_volume_identity_alone_blocks(self):
        nfd = os.path.join(self.case, "Evidence", NFD_CAFE + ".txt")
        self.assertTrue(os.path.samefile(nfd, self.cafe))
        with mock.patch.object(sg, "_fold", lambda s: s, create=True):
            self.assertBlocks("write_file", {"path": nfd, "content": "x"}, f"Evidence/{NFC_CAFE}.txt")
            d = self.check("terminal", {"command": f"rm {nfd}"})
            self.assertEqual(d["action"], "approve")


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

"""The Desktop panel's backend (dashboard/plugin_api.py) and the files Hermes reads to mount it
and the panel (dashboard/manifest.json, desktop/plugin.js).

Ways it could go wrong:
- the backend can't import the kit when Hermes loads it by file path (no package, no sys.path);
- an unset or wrong case folder raises into the gateway instead of telling the panel how to fix it;
- the backend reads a different settings key than the agent half's `matter_dir`;
- the manifest's name differs from the desktop plugin's id, so ctx.rest calls a namespace that
  doesn't exist;
- plugin.js imports something the desktop's import fence refuses, so the panel never loads.
The FastAPI routes are exercised when fastapi is installed (Hermes's own venv); skipped otherwise.
Synthetic data only.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import cases, deadlines, facts, store  # noqa: E402


def load_api():
    spec = importlib.util.spec_from_file_location("corpus_open_plugin_api_test",
                                                  os.path.join(KIT, "dashboard", "plugin_api.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


api = load_api()


class Backend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk-panel-api-")
        self.md = os.path.join(self.tmp, "Roe v. Example")
        with contextlib.redirect_stdout(io.StringIO()):
            cases.init(self.md)
        conn = store.connect(self.md)
        deadlines.add(conn, "Answer due", "2020-01-02", "human:test")
        facts.add(conn, self.md, "Nobody saw the notice.", "allegation", [], by="human:test")
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, value):
        return lambda: {"matter_dir": value}

    def test_panel_reads_the_case(self):
        out = api.get_panel(self.settings(self.md))
        self.assertTrue(out["ok"], out)
        r = out["result"]
        self.assertEqual(r["overview"]["title"], "Roe v. Example")
        self.assertEqual(r["overview"]["unsupported_facts"], 1)
        self.assertTrue(r["deadlines"][0]["passed"])
        json.dumps(out)  # what FastAPI will serialize

    def test_verify(self):
        out = api.post_verify(self.settings(self.md))
        self.assertEqual((out["ok"], out["result"]["state"]), (True, "empty"))

    def test_unset_folder_is_a_setup_answer(self):
        for value in (None, "", "   "):
            out = api.get_panel(self.settings(value))
            self.assertEqual((out["ok"], out["setup"]), (False, True))
            self.assertIn("matter_dir", out["error"])

    def test_folder_that_is_not_a_case(self):
        bare = os.path.join(self.tmp, "plain")
        os.makedirs(bare)
        for fn in (api.get_panel, api.post_verify):
            out = fn(self.settings(bare))
            self.assertEqual((out["ok"], out["setup"]), (False, True))
            self.assertNotIn(self.tmp, out["error"])
        self.assertFalse(os.path.exists(os.path.join(bare, ".matter")))

    def test_unexpected_error_is_an_answer_not_a_raise(self):
        broken = lambda: {"matter_dir": self.md}  # noqa: E731
        os.remove(store.db_path(self.md))
        os.makedirs(store.db_path(self.md))  # a folder where the store should be: sqlite fails
        out = api.get_panel(broken)
        self.assertFalse(out["ok"])

    def test_hermes_settings_path(self):
        cfg = {"plugins": {"entries": {"corpus-open": {"settings": {"matter_dir": "/cases/a"}}}}}
        fake = type(sys)("hermes_cli.config")
        fake.load_config_readonly = lambda: cfg
        pkg = type(sys)("hermes_cli")
        pkg.config = fake
        saved = {k: sys.modules.get(k) for k in ("hermes_cli", "hermes_cli.config")}
        sys.modules.update({"hermes_cli": pkg, "hermes_cli.config": fake})
        try:
            self.assertEqual(api._hermes_settings()["matter_dir"], "/cases/a")
            cfg["plugins"]["entries"]["corpus-open"] = {"config": {"matter_dir": "/cases/legacy"}}
            self.assertEqual(api._hermes_settings()["matter_dir"], "/cases/legacy")
            cfg["plugins"]["entries"] = {}
            self.assertEqual(api._hermes_settings(), {})
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

    def test_outside_hermes_settings_are_empty(self):
        if importlib.util.find_spec("hermes_cli") is None:
            self.assertEqual(api._hermes_settings(), {})


@unittest.skipIf(importlib.util.find_spec("fastapi") is None, "fastapi not installed (Hermes's venv has it)")
class Routes(unittest.TestCase):
    def test_routes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        tmp = tempfile.mkdtemp(prefix="mk-panel-routes-")
        self.addCleanup(shutil.rmtree, tmp, True)
        md = os.path.join(tmp, "Case")
        with contextlib.redirect_stdout(io.StringIO()):
            cases.init(md)
        app = FastAPI()
        app.include_router(api.router, prefix="/api/plugins/corpus-open")
        client = TestClient(app)
        saved, api.settings_source = api.settings_source, (lambda: {"matter_dir": md})
        try:
            self.assertTrue(client.get("/api/plugins/corpus-open/panel").json()["ok"])
            self.assertEqual(client.post("/api/plugins/corpus-open/verify").json()["result"]["state"], "empty")
            self.assertEqual(client.get("/api/plugins/corpus-open/verify").status_code, 405)
        finally:
            api.settings_source = saved


class Packaging(unittest.TestCase):
    def test_manifest_names_the_backend(self):
        with open(os.path.join(KIT, "dashboard", "manifest.json"), encoding="utf-8") as f:
            m = json.load(f)
        self.assertEqual(m["name"], api.PLUGIN_ID)
        self.assertEqual(m["api"], "plugin_api.py")
        self.assertTrue(m["tab"]["hidden"])
        self.assertTrue(os.path.isfile(os.path.join(KIT, "dashboard", m["entry"])))

    def test_desktop_plugin_id_and_import_fence(self):
        path = os.path.join(KIT, "desktop", "plugin.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        self.assertRegex(js, r"const ID = '%s'" % re.escape(api.PLUGIN_ID))
        self.assertRegex(js, r"export default \{\s*id: ID,")
        sources = set(re.findall(r"""(?:^|\n)\s*import\b[^'"]*['"]([^'"]+)['"]""", js))
        sources |= set(re.findall(r"""\bimport\(\s*['"]([^'"]+)['"]""", js))
        self.assertTrue(sources, "plugin.js imports nothing?")
        self.assertLessEqual(sources, {"@hermes/plugin-sdk", "react", "react/jsx-runtime"})
        self.assertNotRegex(js, r"\brequire\(")
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("dangerouslySetInnerHTML", js)


class BackendErrorWording(unittest.TestCase):
    """The panel's words for a failed backend call (backendError in desktop/plugin.js).

    Hermes answers 404 three ways, and only one of them means corpus-open is off: telling a
    profile that has it enabled to "add corpus-open to plugins.enabled" sends the reader to edit
    a config that is already right. Hermes refuses a secondary profile's request to a
    user-installed plugin's in-process backend with "Plugin not found" (only plugins.isolation:
    host forwards it); the hosted route says "is not enabled in this profile".
    """

    def setUp(self):
        import shutil as _sh
        self.node = _sh.which("node")
        if not self.node:
            self.skipTest("node is not installed")
        with open(os.path.join(KIT, "desktop", "plugin.js"), encoding="utf-8") as f:
            js = f.read()
        m = re.search(r"\nfunction backendError\(e\) \{\n.*?\n\}\n", js, re.S)
        self.assertIsNotNone(m, "backendError not found in plugin.js")
        self.fn = m.group(0)

    def words(self, message):
        import subprocess
        script = self.fn + "\nprocess.stdout.write(backendError(new Error(%s)))" % json.dumps(message)
        out = subprocess.run([self.node, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_not_enabled_in_this_profile(self):
        said = self.words("404: plugin 'corpus-open' is not enabled in this profile")
        self.assertIn("not enabled in this profile", said)
        self.assertIn("plugins.enabled", said)

    def test_secondary_profile_refused(self):
        said = self.words("404: Plugin not found")
        self.assertIn("plugins.isolation: host", said)
        self.assertNotIn("Add corpus-open to plugins.enabled", said)

    def test_other_404_names_what_hermes_said(self):
        said = self.words("404: Not Found")
        self.assertIn("404: Not Found", said)
        self.assertNotIn("backend is off", said)

    def test_other_errors_pass_through(self):
        self.assertEqual(self.words("timed out after 20000 ms"), "timed out after 20000 ms")


if __name__ == "__main__":
    unittest.main()

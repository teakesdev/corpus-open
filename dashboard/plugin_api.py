"""Backend for the Hermes Desktop panel (desktop/plugin.js): read-only routes under
/api/plugins/corpus-open/.

Hermes mounts this file from dashboard/manifest.json's `api` field once corpus-open is in
`plugins.enabled`. It runs in the gateway (or its plugin host), not as part of the agent
package, so the kit is loaded here under a private package name and the case folder is read
from the same plugin settings the agent half reads (plugins.entries.corpus-open.settings).

GET  /panel   the case at a glance, open deadlines (passed first), facts with no source
POST /verify  re-hash every registered file for the tamper badge (reads only; POST because
              it can take a while and must not be cached or prefetched)

Every answer is {"ok": true, "result": ...} or {"ok": false, "error": ..., "setup": bool};
`setup` is true when the case folder isn't set or isn't a case yet.
"""
from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import os
import sys

PLUGIN_ID = "corpus-open"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = "_corpus_open_panel_matterkit"


def _kit(name: str):
    """A matterkit module, loaded once from this plugin's own copy of the kit."""
    if _PKG not in sys.modules:
        spec = importlib.machinery.ModuleSpec(_PKG, None, is_package=True)
        spec.submodule_search_locations = [os.path.join(ROOT, "matterkit")]
        sys.modules[_PKG] = importlib.util.module_from_spec(spec)
    return importlib.import_module(f"{_PKG}.{name}")


def _hermes_settings() -> dict:
    try:
        from hermes_cli.config import load_config_readonly
    except ImportError:
        return {}
    entry = ((load_config_readonly() or {}).get("plugins") or {}).get("entries") or {}
    entry = entry.get(PLUGIN_ID) if isinstance(entry, dict) else None
    if not isinstance(entry, dict):
        return {}
    for key in ("settings", "config"):  # `config` is Hermes's legacy subtree
        sub = entry.get(key)
        if isinstance(sub, dict) and sub.get("matter_dir"):
            return sub
    return {}


def resolve_matter(raw) -> str:
    """The configured case folder, or a PanelError saying how to set it."""
    panel = _kit("panel")
    if not raw or not str(raw).strip():
        raise panel.PanelError("No case folder is set. Set matter_dir in Settings > Plugins > corpus-open.")
    path = os.path.abspath(os.path.expanduser(str(raw).strip()))
    if not os.path.isdir(os.path.join(path, ".matter")):
        raise panel.PanelError("The case folder in Settings > Plugins > corpus-open is not a case yet. "
                               "Run: python3 matter.py init <folder>")
    return path


settings_source = _hermes_settings  # tests swap this for a plain dict reader


def _answer(fn, settings=None) -> dict:
    panel = _kit("panel")
    try:
        md = resolve_matter((settings or settings_source)().get("matter_dir"))
    except panel.PanelError as e:
        return {"ok": False, "error": str(e), "setup": True}
    try:
        return {"ok": True, "result": fn(panel, md)}
    except panel.PanelError as e:
        return {"ok": False, "error": str(e), "setup": True}
    except Exception as e:  # noqa: BLE001 - the panel shows the error; the gateway keeps serving
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "setup": False}


def get_panel(settings=None) -> dict:
    return _answer(lambda panel, md: panel.panel_data(md), settings)


def post_verify(settings=None) -> dict:
    return _answer(lambda panel, md: panel.panel_verify(md), settings)


try:
    from fastapi import APIRouter
except ImportError:  # imported outside Hermes (tests, route inspection): no router
    router = None
else:
    router = APIRouter()

    # Plain `def` handlers: FastAPI runs them in its threadpool, so hashing a large case
    # folder never blocks the gateway's event loop.
    @router.get("/panel")
    def _panel_route():
        return get_panel()

    @router.post("/verify")
    def _verify_route():
        return post_verify()

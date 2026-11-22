# Review: package-relative adapter imports, `origin/main..9a1733e`

Reviewer: Grok 4.7 (Hermes). Snapshot: `fix/hermes-package-imports` at `9a1733e`. Date: 2026-11-07.
No network. No real case folder. Probes used temp directories.

**Verdict: APPROVE**

P1: 0. P2: 0. P3: 0.

## The fix

`matterkit/search.py` no longer names `matterkit.adapters…`. The two adapter paths are relative (`.adapters.courtlistener.CourtListenerFreeAdapter` and `.adapters.corpus_search.CorpusSearchAdapter`), and `_load_adapter` passes `__package__` to `import_module`. Hermes loads this file as `<plugin>.matterkit.search`, so the adapters load under that same name. The CLI and the MCP server load it as `matterkit.search`, so they still get `matterkit.adapters.*`.

Checked. With the repo on `sys.path`, both classes are the same objects as `matterkit.adapters.courtlistener.CourtListenerFreeAdapter` and `matterkit.adapters.corpus_search.CorpusSearchAdapter`. `get_json` is looked up in that module at call time, so `mock.patch("matterkit.adapters.courtlistener.get_json")` still hits. `tests.test_r2_search.TestR2Search` and `tests.test_r3_corpus`: 12 OK. MCP cite lookup does not use this table. It imports `CitationLookupAdapter` with `from .adapters.courtlistener_citations`, which was already relative. MCP search goes through `caselaw.search` → `manual_search` → `_load_adapter`, and that module's `__package__` is `matterkit` when the server is started the usual way.

## The test

`tests/test_hermes_package_name.py` is a real guard. It runs `python -I` with the repo reachable only as `hermes_plugins_x.corpus_open`, and it fails unless `matterkit` is not findable at the top level and no `matterkit.*` module was loaded. `/matter demo` must create the case and two deadlines, `matter_ingest_path` must register one file, and the two adapters must report the plugin package name, not `matterkit`. An absolute import would show up in `top_level_loaded` or in the adapter module names. It passed here, along with the older ingest-only check in `tests/test_plugin_package_imports.py`.

## Absolute imports still reachable from the plugin

None in code the plugin executes. `hermes_plugin/` and `matterkit/` have no `import matterkit` / `from matterkit`. The plugin's own watch path is `from ..matterkit import watch`.

The one remaining absolute import is the string the plugin writes into the Deadline Watch cron script (`hermes_plugin/__init__.py:351`). That script is a separate process. It inserts the kit root on `sys.path` and then imports `matterkit`. It is not imported under Hermes's package name. Leaving it absolute is right.

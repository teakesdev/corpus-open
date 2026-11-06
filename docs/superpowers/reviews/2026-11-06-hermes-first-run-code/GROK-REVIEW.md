# Review: keyless CourtListener search, merge 3fe34df, Hermes first run

Reviewer: Grok 4.7 (Hermes). Snapshot: detached `0b5f6f1`. Date: 2026-11-06.
No CourtListener or Corpus request was made. `~/.hermes` config was not written. Probes used a scratch `MATTER_KIT_CONFIG` and deleted the probe key afterward.

**Verdict: APPROVE WITH CHANGES**

`python3 -m unittest discover -s tests` on this machine (Python 3.11.15): 1832 tests OK, 1 skipped. I did not re-run 3.10 or 3.14.

The search gate, the merge's scope table, and the demo's refusal to clobber a non-empty folder all hold. Two things should be fixed before this is what a hackathon judge follows: `/matter grant` saves the CourtListener key where the citation guard does not read it, and the README still says the cite grant is 10 a day after the merge wrote 100.

## Findings

### 1. P2 — `/matter grant` does not enable case cites in Hermes

`hermes_plugin/__init__.py:219-220`, `hermes_plugin/guard.py:163-165`, `hermes_plugin/__init__.py:117`, `matter.py:818-819`

`courtlistener on` does authorize the guard's caller. `caselaw.turn_on` writes `authorized_callers: ["*"]` (`matterkit/caselaw.py:75-76`). `consent.authorize(..., "hermes")` then succeeds. Probed: grant callers `['*']`, cite-lookup budget 100.

The key prompt on that same command does not. It saves the key with `credentials.save_courtlistener_token` (`~/.config/matter-kit/courtlistener-token`, or `$MATTER_KIT_CONFIG`). The guard never reads that file. `run_guard` passes only Hermes's secret (`get_secret("COURTLISTENER_TOKEN")`). If that secret is unset, `guard.py` returns before `CitationLookupAdapter` is built, so the adapter's `token is None` fallback never runs.

The old grant text said the token has to be in this plugin's settings. The new text replaced that with "asks you to type yes, then for your free key", which is the wrong store. A person who follows the printed commands gets a valid grant, a key the guard cannot see, and case cites still `NOT LOOKED UP`.

Repro (no network). Turn lookups on, save a Matter Kit key, call the guard the way the plugin does when the Hermes secret is unset:

```text
caselaw.turn_on(case, "ty", "tty")
credentials.save_courtlistener_token("<40 char key>")
guard.check_text("See 550 U.S. 544 (2007).", matter_dir=case,
                  caller_id="hermes", courtlistener_token=None)
```

Observed: note "add a free CourtListener API token in this plugin's settings (COURTLISTENER_TOKEN).", `sent` empty, outcome `NOT LOOKED UP`. The same call with `courtlistener_token` set reaches the adapter (probe factory was invoked with that token). `/matter grant` with no `matter_dir` does not mention plugin settings.

Fix the printed case-cite step: `courtlistener on` for the grant, and say the key also has to be saved in the plugin's `COURTLISTENER_TOKEN` secret. Do not imply the `courtlistener on` prompt feeds the guard.

Out of scope, same class of stale instruction, not a load failure: `hermes_plugin/guard.py:31` still tells the user to run a relative `python3 matter.py consent activate ... --scope cite-lookup`, which fails from any other folder. Worth fixing in the same pass. Not a separate finding.

### 2. P2 — cite-lookup budget in the README is 10; the merged grant is 100

`matter.py:772-774`, `matterkit/caselaw.py:22`, `README.md:267-268`, `README.md:286`, `README.md:290`

Argparse choices and the scopes dict match: `search`, `cite-lookup`, `corpus-search`. `corpus-search` stays at 50, endpoint `corpuslaw.us`, which matches `CorpusSearchAdapter`. Search and cite-lookup now come from `caselaw.SCOPES` (500 and 100), so `consent activate --scope cite-lookup` and `courtlistener on` agree with each other. That part of the resolution is right.

They do not agree with the disclosure. On the plugin branch (`2420458`) `cmd_consent` wrote cite-lookup budget 10. The merge took `CITE_BUDGET = 100`. The Hermes section still says "the grant's daily budget (10)", still says the person types `ACTIVATE`, and the install block still shows a relative `python3 matter.py consent activate ... --scope cite-lookup`. `courtlistener on` asks for `yes`, not `ACTIVATE`, and does not print the number 100. A person who read the table authorized 10 cite lookups a day and the command writes 100.

Repro: `git show 2420458:matter.py` around the old scopes dict (cite-lookup budget 10). After `turn_on`, the cite-lookup grant's `daily_budget` is 100 (probed). `README.md:290` still says 10.

Fix: say 100 (and `yes` for `courtlistener on`) in that table, and point the install snippet at the same commands `/matter grant` prints, including the Hermes secret.

### 3. P3 — default `matter.py search` still logs egress and sends with no key

`matter.py:845-851`, `matter.py:1228`, `matterkit/adapters/courtlistener.py:51-52`, `matterkit/adapters/courtlistener.py:74`

`caselaw.search` does what the commit claims. With lookups on and no key it raises `ConsentError` (`free CourtListener key`, `refusing egress`) before `get_json`, and no `.matter/egress.jsonl` is created (probed). A saved search still returns with lookups off and no key, and `get_json` is not called (probed). Explicit `--auth anonymous` is the path the commit left alone.

The default is `--auth auto`, not `anonymous`. With no key, `auto` becomes `anonymous`, `log_egress` runs, then `get_json` is called. Probed with `get_json` mocked to raise: the mock was called, and `egress.jsonl` already had a `query_text` line. Live, that is a request and a 401 after the log line. `caselaw.search` (MCP, the one-switch path) is fixed; `matter.py search CASE "query"` is not. The README does not document that command, so this is a residual, not a broken claim.

## Checked, not findings

Search order is right. Saved lookup, then `consent.authorize`, then the key, then `manual_search`. A search with lookups off and no key gets the turn-on error from `authorize` (`TURN_ON_HINT`), not `SEARCH_NEEDS_KEY`, and writes no egress file (probed). That is the right next step: even with a key, search cannot run until a person turns lookups on, and `courtlistener on` then asks for the key. `authorize` only reads grants; it does not log egress.

Empty token does not leak. `CitationLookupAdapter(..., token="")` and `token="   "` raise `ConsentError` before `post`, including when a Matter Kit key is saved (probed, 0 posts). `token=None` falls back to `credentials.courtlistener_token()` and does not post until `lookup()`. That fallback is right for the CLI. It is not how the Hermes guard behaves when the secret is unset (finding 1).

`draftdocs` character sets are identical. Compared `3fe34df` with `0b5f6f1` by executing the assignments: `_SPACES`, `_TRANS`, and `_LIGATURES` compare equal. The escapes are the same characters, not a behavior change.

The manifest reader is good enough for this file. `TestRegistration` passed, so parsed `provides_tools`, `provides_hooks`, and each secret's `type` / `env` match what the plugin registers. It only understands this manifest's shape (indent-2 `- ` lists, indent-4 scalars). A flow-style list would not be pinned. That is not a hole in the current `plugin.yaml`.

Skill registration matches Hermes. `register_skill(self, name, path, description="", frontmatter=None)` in `hermes_cli/plugins_content.py` accepts a `str` path (`Path(path)`). The call uses `matter-kit` (legal name, no colon) and a keyword description. A missing `register_skill`, or an exception, does not stop tool registration. `set_config(self, key, value)` matches `_select`.

`/matter demo` does not delete existing user files. `demo-matter/` has no symlinks (README, one synthetic complaint, one synthetic memo). A path that is a file is refused and the bytes stay. A second run on a folder that already has files is refused and the complaint bytes stay. A symlink to a non-empty directory is refused and the target file stays. A symlink to an empty directory is followed and filled (`copytree` + `listdir` both follow); that writes into the target, it does not delete anything that was already there. A broken symlink raises `File exists` and the link is left in place. An empty real directory is filled, which is what the command says.

The `test_ingest_verify_cwd.py` conflict resolution kept the clearer comment. No behavior change.

I did not re-review `dbbc75d`, `bb7d176`, or `2420458` except where they meet this diff (the guard's token check, above). Nothing in that pass was a security hole, data loss, or a plugin that fails to load.

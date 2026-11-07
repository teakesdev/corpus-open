# Review: Hermes plugin on public main, round 2

Reviewer: Grok 4.7 (Hermes). Snapshot: detached `fdc143d`. Date: 2026-11-07.
No CourtListener or Corpus request was made. `~/.hermes` and `~/.config/matter-kit` were not written. Probes used temp case folders. `MATTER_KIT_CONFIG` pointed at a temp directory and `COURTLISTENER_TOKEN` was empty for the test run.

**Verdict: REQUEST CHANGES**

`python3 -m unittest discover -s tests` on this machine (Python 3.11.15): 1838 tests OK, 1 skipped. I did not re-run 3.10 or 3.14.
`hermes plugins validate .` passed, including the capability probe, declared tools and hooks, the security scan, and the desktop-surface lint. That scan does not catch the path bug below. I did not run `hermes plugins install teakesdev/corpus-open` (no network).

## Findings

### 1. P1 — file tools can still change a source or `.matter/` on a case-insensitive volume

`hermes_plugin/source_guard.py:105-106`, `hermes_plugin/source_guard.py:148`, `hermes_plugin/source_guard.py:151-153`

`_candidates` keeps `abspath` and `realpath`. On this Mac, neither one restores the on-disk spelling or Unicode form. `_classify` then asks whether the string `.matter` is in the path, and whether the relative path is an exact key in `documents`. A different case, or NFD against an NFC key, is not a hit, so `write_file` and `patch` are allowed. The same miss means a terminal command is not even sent to approval.

This is the machine the plugin is built on, and APFS is case-insensitive by default. macOS also stores names in NFD. The tests create the file and the path with the same string, on a case-sensitive CI volume, so they stay green.

Repro (temp case, no network). Register `Evidence/lease.txt` and write `.matter/consent.json`. Then:

```text
write_file  evidence/lease.txt          -> None
write_file  .Matter/consent.json        -> None
write_file  .MATTER/matter.db           -> None
terminal    rm evidence/lease.txt       -> None
terminal    echo x > .Matter/consent.json -> None
```

The exact paths are blocked. Opening the case-different paths changes the original files (`os.path.samefile` is true). Observed: `lease.txt` became `CHANGED`, and `consent.json` became a grant object with `"kind": "human"`. An agent can replace the consent file the citation guard trusts, or change a registered source, without the block the plugin promises.

A second form of the same hole: a file stored as NFC `Evidence/café.txt` is the same inode as the NFD path, and `write_file` on the NFD path also returns None.

Symlinks are not this bug. A symlink outside the case that points at a source is blocked, and so is a symlink directory inside the case. `..` and a relative path against `matter_dir` are blocked. Those tests hold.

Fix: decide by identity, not spelling. If any path component's `(st_dev, st_ino)` is the `.matter` directory, block. If the final inode is a registered source, block. Do that for `write_file`, `patch`, and the terminal/code check.

### 2. P2 — the model can turn the citation guard off by echoing its footer

`hermes_plugin/__init__.py:133-134`

`on_reply` returns None when the reply already contains both `Citation check` and `Checked by Corpus Open`. That is meant to skip a replay of the plugin's own block. It is a substring test on the model's text, so the model can suppress the check by writing those two phrases.

Repro (no network, no key):

```text
See 550 U.S. 544 (2007). Citation check is discussed. Checked by Corpus Open.
```

`on_reply` returned None. The same cite without those phrases came back `NOT LOOKED UP`. The existing test only replays a block the plugin itself appended.

Fix: skip only a block this plugin appended (a sentinel it adds, or a check that the block is the suffix it would render), not any reply that mentions the feature.

## Per-scope answers

### 1. Round-1 fixes in `bd9a3ed`

Closed. Nothing in that commit regressed.

- The guard reads Hermes's `COURTLISTENER_TOKEN` first, then `credentials.courtlistener_token()` (the key `courtlistener on` saved, or the environment). Probed by the new tests: the saved key is what gets passed when the Hermes secret is unset, and the Hermes secret wins when both are set. With no key anywhere, the note names `courtlistener key` and `COURTLISTENER_TOKEN`, and nothing is posted.
- `guard.grant_command` builds both commands with the kit's full path and `shlex.quote`. `/matter grant` uses it. The no-grant reply contains those commands and does not contain a bare `python3 matter.py`.
- The README table says 100 cite lookups a day, `yes` for `courtlistener on`, and `ACTIVATE` for the Corpus grant. That matches `caselaw.CITE_BUDGET = 100`. The egress log is named `.matter/egress.jsonl` in the README and in `guard.py`.
- `plugin.yaml` says the CourtListener secret is optional and is used first. That matches `courtlistener_token()`.

The round-1 residual, default `matter.py search --auth auto` with no key, is also closed on this snapshot (`01bf1ae`). `CourtListenerFreeAdapter.search` raises `ConsentError` before `log_egress` when auto has no key. I did not re-open that as a finding.

### 2. The plugin commits

`dbbc75d`, `bb7d176`, `2420458`, as they stand on `fdc143d`.

Network. Case lookup sends one POST of bare cites (`citelookup.payloads`, then `validate_payload`, which refuses a line that is not a bare cite). The guard sends only the first payload (60 cites) per reply. Statute lookup sends one parsed cite per request, at most `max_statute_checks` (default 5). No reply text, fact, or document is in those bodies. The adapter calls `consent.authorize` before it is constructed and again before each POST. No grant, no case folder, no token, or `citation_guard: local` means no POST and no egress file. A `ConsentError` or a transport error becomes a note; the hook does not raise. The daily budget is checked in `authorize` against today's egress count. A failed POST is still logged, which is right, because the request left. The log line is provider, endpoint, payload type, byte count, and sha256. It does not contain the token or the query text.

The key. It is not printed by `/matter grant`, not written into the case folder, and not put in the citation block (the plugin test asserts the token is absent from the reply and from `egress.jsonl`). HTTP failures are re-raised as `RuntimeError` messages that name the status code, not the token. `post_form` keeps the `HTTPError` as `__cause__`; the note uses `str(e)` of the `RuntimeError` only. The key file is `~/.config/matter-kit/courtlistener-token` (or `$MATTER_KIT_CONFIG`), mode `0600`.

Source guard, besides finding 1. `write_file` and `patch`, including V4A Update/Add/Delete/Move lines, are blocked for a registered source and for anything under `.matter/` when the path string matches. Relative paths, `..`, `~`, and a symlink that resolves to a source are blocked. A new draft in the case passes. Reading commands pass. `execute_code` that calls `open(..., 'w')` or `os.remove` on a named source goes to approval. Hermes's own `pre_tool_call` timeout fails closed, so a slow hook does not fail open. The hook catches its own errors and, if the path is inside a case, asks for approval instead of raising. It does not block a draft by mistake when the store is readable. It is still a heuristic for shell and Python text: a path built by concatenation is not named, and the module says so. That is not a separate finding. The case and NFD miss is, because those paths do name the file.

Hooks. `on_reply` appends a block or returns None. An exception returns None, so the reply is not dropped or replaced. `on_turn` returns None on failure, so a deadline-brief error does not break the chat. The deadline brief is context on the first turn only. Finding 2 is the one way `transform_llm_output` drops the check without an error.

Desktop pane. `desktop/plugin.js` renders labels, titles, errors, and `rel_path` as React text children. There is no `dangerouslySetInnerHTML`, no `eval`, and no `document.querySelector`. A file name or fact string cannot become HTML here. The pane only calls `ctx.rest` for `/panel` and `POST /verify`. The backend reads the configured case and re-hashes; it does not write. `hermes plugins validate` says the desktop surface stays inside the SDK. I did not open a real case folder to load the pane.

`plugin.yaml`. Twelve tools, three hooks, two optional secrets, empty `requires_env`. `register()` matches that list (the manifest test, and the validator's capability probe). `source_guard` defaults on. The description's network claim is accurate except where finding 1 lets an agent rewrite the grant the claim depends on.

### 3. Main since `72c57bd`

Nothing else I would block on. The range is mostly the plugin, the one-switch search fix, and DOCX/PDF extraction. `extract.py` reads zip members in memory after a pre-open size cap. It does not `extractall` and it does not write member bytes out, so there is no zip-slip write. I did not line-review the DOCX parser. The search-auth fix is the one behavior change outside the plugin, and it closes the old residual rather than opening a new one.

## Checked, not findings

Symlink, `..`, and relative-path coverage in `tests/test_source_guard.py` holds on this machine. The hole is case and normalization, which those tests never vary.

`approve` from this hook is Hermes's human-approval gate, not an allow. A block with a message is a veto. That matches `hermes_cli/plugins.py`.

Empty token, whitespace token, and a missing Hermes secret do not build the CourtListener adapter. Corpus checks still run without a CourtListener key, and they still require the Corpus grant.

I did not install from GitHub. Local validate is the evidence I have that the package loads.

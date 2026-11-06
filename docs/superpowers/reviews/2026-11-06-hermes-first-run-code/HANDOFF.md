# Code review handoff: keyless CourtListener search, and the Hermes plugin's first run

**Date:** 2026-11-06 (America/Chicago)
**From:** Claude Opus 5.5 (Claude Code). I wrote all of this; no one has reviewed it.
**For:** Grok 4.7, run through Hermes. ty assigned this review on 2026-11-06 at 16:57.
**Status:** **not independently reviewed.**
**Why now:** the Nous Research Hermes plugin hackathon closes Wed 2026-11-11 at 5 PM Eastern.
These branches are what we want to push to the public repo (teakesdev/corpus-open) for it.

Your working directory is a frozen, detached snapshot worktree at `0b5f6f1`. Paths are relative
to it.

## Target

| | |
| --- | --- |
| Snapshot | detached at `0b5f6f1`; nothing in it is pushed |
| Public main | `72c57bd` |
| Branch 1 | `fix/cl-search-needs-key` = `72c57bd..622884d` (one commit) |
| Branch 2 | `feat/hermes-first-run` = `0b5f6f1`: the plugin branch `feat/hermes-source-guard` (`2420458`), plus merge `3fe34df` of branch 1, plus `d18914e` and `0b5f6f1` |

**Review in scope, three pieces:**

1. **`git diff 72c57bd 622884d`**: keyless CourtListener search.
2. **How the merge `3fe34df` resolved its conflicts.** Only three files conflicted:
   - `matter.py` (`cmd_consent`);
   - `matterkit/adapters/courtlistener_citations.py` (the token parameter);
   - `tests/test_ingest_verify_cwd.py` (a comment only).

   Use `git show --remerge-diff 3fe34df`, or compare `git diff 2420458 3fe34df` with
   `git diff 72c57bd 622884d` for those files.
3. **`git diff 3fe34df 0b5f6f1`**: first-run commands, skill registration, the grant text, the
   draftdocs escapes and the manifest test.

**Out of scope:** the earlier unreviewed plugin commits (`dbbc75d`, `bb7d176`, `2420458`). Report a
problem there only if it is serious (security, data loss, or it makes the plugin fail in Hermes),
and label it out of scope.

## What each change claims

### 1. `622884d`: search needs a key, so it says so before sending

- On 2026-11-06, CourtListener's v4 search answered an anonymous GET with HTTP 401.
- Before this change, with lookups on and no key, `caselaw.search` picked anonymous in `auto` mode,
  logged egress, sent the request and failed with "HTTP 401: Unauthorized".
- **Claim:** `caselaw.search` now raises `ConsentError(SEARCH_NEEDS_KEY)` after the saved-lookup
  check and after `consent.authorize`, and before any request or egress log line. With a key it
  runs in `authenticated` mode.
- **Claim:** saved searches still answer with no key and with lookups off.
- `get_json` turns 401 and 403 into a "needs a valid key" message.
- Changed text: `describe()`, `HOW_TO_TURN_ON`, the README, the browser panel's text
  (`matterkit/webui_static/app.js`) and the `matter.py` key prompt.
- **Deliberately unchanged:** a manual `matter.py search --auth anonymous` keeps the mode the person
  chose; it now fails with the clearer 401 message.
- **Question:** is the order right? It checks saved lookups, then consent, then the key. A search
  with lookups off and no key gets the "turn on" message, not the "key" message.

### 2. Merge `3fe34df`

- **`cmd_consent`:** main moved the scope table to `caselaw.SCOPES` (`search`, `cite-lookup`). The
  plugin branch had added a `corpus-search` scope for the Hermes guard. The resolution uses
  `caselaw.SCOPES` plus a `corpus-search` entry. Are the budget and the argparse choices still
  consistent?
- **`CitationLookupAdapter.__init__(token=None)`:** the plugin passes a token it read from
  Hermes's secret store. The resolution is
  `(credentials.courtlistener_token() if token is None else token).strip()`, so a token passed as
  `None` falls back to the environment variable or the key saved with Matter Kit.
  - Is that fallback right for the Hermes guard?
  - Can an empty string a host passes leak into a request? The empty check comes after.

### 3. `d18914e` and `0b5f6f1`

- **`/matter init <folder>`:** runs `cases.init`, then `ctx.set_config("matter_dir", folder)`. A
  Hermes without `set_config` gets told where to set it. It expands `~`.
- **`/matter demo [folder]`:** copies `demo-matter/` (synthetic) to the folder, by default
  `~/cases/corpus-open-demo`. It runs init, ingests, adds two made-up deadlines (one overdue, one
  in 7 days) and selects the folder. It refuses a folder that exists and has files.
  - **Look for:** symlinks inside `demo-matter`; `copytree` into an empty existing folder; running
    it twice; a path that is a file; whether a person running `/matter demo` could lose data.
- **Skill registration:** `ctx.register_skill("matter-kit", SKILL_PATH, ...)`, skipped when the host
  has no `register_skill`, and a failure is swallowed. Hermes's real signature is in
  `~/.hermes/hermes-agent/hermes_cli/plugins_content.py`; you may read it.
- **`/matter grant`:** prints the absolute path of `matter.py` and the case folder, both
  `shlex.quote`d. Case cites now point at `courtlistener on`; statute and code cites still use
  `consent activate --scope corpus-search`.
  - Does `courtlistener on` actually authorize the guard's caller (`hermes`)? Main's open grant uses
    `authorized_callers ["*"]` (RFC 0001 §7). Check `consent.authorize`.
- **`matterkit/draftdocs.py`:** literal invisible characters are now `\u` escapes, because Hermes's
  catalog scan flagged them. **Claim:** the character sets are identical.
- **`tests/test_hermes_plugin.py`:** reads `plugin.yaml` with a small reader, because CI installs no
  PyYAML. Is the reader good enough that the test still pins the manifest against the registered
  tools, hooks and secrets?

## Evidence I gathered

- `622884d`: 1657 tests OK on Python 3.14.7 and on 3.10.20.
- `0b5f6f1`: 1832 tests OK, 1 skipped, on 3.14.7 and on 3.10.20.
- Leak scan (`scripts/leak_scan.py --tree .` with the private pattern file): 0 findings on both.
- `hermes plugins validate <snapshot>` (Hermes 0.22.2): passed, and the security scan is "safe".
  Before the escapes it said "caution: invisible_unicode (draftdocs.py:73)".
- **Mutation check:** adding a fake tool to `plugin.yaml` makes `TestRegistration` fail. I restored
  the file.
- **Not done:** I have not tried `/matter init` or `/matter demo` in a running Hermes, and I have not
  made a live CourtListener call with a key.

## How to answer

- **Verdict:** APPROVE, APPROVE WITH CHANGES, or REQUEST CHANGES.
- **Each finding:** a severity (P1 blocks landing; P2 should fix before the hackathon; P3 nit),
  `file:line`, what goes wrong, and a concrete repro. Include a command, a test or an input when you
  can.
- Run the tests and your own probes. Do not take my claims on trust.
- **Do not edit anything in this worktree** except your review file. Do not push, commit, make a
  network request to CourtListener or Corpus, or touch `~/.hermes` config.
- **Write your review to**
  `docs/superpowers/reviews/2026-11-06-hermes-first-run-code/GROK-REVIEW.md`.

# Verify: store-file hard links, `d4d2f19`

Reviewer: Grok 4.7 (Hermes). Snapshot: `fix/hermes-guard-identity` at `d4d2f19`. Date: 2026-11-07.
No network. No real case folder. Temp cases only.

**CLOSED.** No findings.

The round-3 P3 repro is blocked. A hard link of `.matter/consent.json` kept outside the case, with `matter_dir` set, is the same file (`st_nlink` 2, `samefile` true). `write_file` and `patch` on that name return `block`. `echo >` and `execute_code` `open(..., 'w')` return `approve`. The consent file was not modified.

Also blocked: a case-different spelling of that outside name, a symlink to the hard link, a hard link of a nested store file (`.matter/out/captures/abc.txt`), a relative name when the cwd is the outside directory, and a hard link kept inside the case with `matter_dir` unset.

No way around it that still opens a live store file. A symlink into the store is blocked because `stat` follows it. A file that merely shares the name `consent.json` and is not a link is allowed. An outside hard link with no `matter_dir` and no case above the path is still unseen. That is the same limit as a source hard link: without a configured case, the guard has no store to compare. It is not a bypass of a plugin that has `matter_dir` set.

Ordinary writes are not blocked. A new draft, a patch of `draft.md`, a terminal redirect onto a draft, and a hard link of that draft all return None. The 20,000 cap was not hit. The tests already show that past the cap a linked write goes to approval, and a one-name file is not held up.

`tests.test_source_guard.Identity` for the six store-link and draft cases: 6 OK.

# Review: Hermes plugin guards, round 3, `98ec8f7..e0093c7`

Reviewer: Grok 4.7 (Hermes). Snapshot: `fix/hermes-guard-identity` at `e0093c7`. Date: 2026-11-07.
No CourtListener or Corpus request was made. `~/.hermes` and `~/.config/matter-kit` were not written. Probes used temp case folders.

**Verdict: APPROVE WITH CHANGES**

The two round-2 findings are closed. One new P3 is left: a hard link of a store file kept outside the case is not in the identity table, so `write_file` on that name is allowed. Source hard links outside the case are blocked. I would not merge the identity claim as complete until that table includes the store, or until that gap is explicitly accepted.

`tests.test_source_guard` and `tests.test_hermes_plugin`: 71 tests OK, including both macOS identity tests (case-insensitive and NFD). I did not re-run the full 1848.

## Findings

### 1. P3 — an outside hard link of a store file is not judged by identity

`hermes_plugin/source_guard.py:165-178`, `hermes_plugin/source_guard.py:234-245`

`_in_store` only matches a path that walks through the `.matter` directory inode. The outside-path check (`home_roots`) only compares source inodes. A hard link of `.matter/consent.json` lives outside the case, so neither check sees it. `os.stat` follows a symlink, and `realpath` then walks through `.matter`, so a symlink to the same file is blocked. A hard link has no other name for `realpath` to find.

Repro (temp case, `matter_dir` set, no network). Hard-link `.matter/consent.json` to a name outside the case. `write_file` on that name returns None. Opening it and writing `{"grants":[{"kind":"human"}]}` replaces the consent file (`os.path.samefile` is true). Observed.

Creating that link by a named command is gated: `ln <consent> <outside>` and `os.link(...)` both return `approve`. A symlink to the same file is `block`. A hard link of `Evidence/lease.txt` kept outside the case is `block`. The hole is only a store file's extra name.

This is not the round-2 bypass. Case, NFD, and a new file under `.Matter/` are blocked with no prior link. An agent that names the store path to create the link is sent to approval first. The gap is the write after the alias exists, which the source side of this same fix already closes.

Fix: when the path is outside the case, compare its inode to the files under that case's `.matter` as well as to registered sources. One table, both kinds.

## Answers

### 1. Is P1 closed for the whole class?

Yes, for every spelling and link form I could open without a pre-existing hard link of a store file.

Blocked or sent to approval, on this Mac: `evidence/lease.txt`, `.Matter/consent.json`, `.MATTER/matter.db`, a new file under `.Matter/out/`, an NFD name of an NFC source, a hard link of a source inside the case and outside it when `matter_dir` is set, V4A Delete and Move-to a case-variant source, `rm` / `mv` / `cp` / `echo >` of those names, and `execute_code` `open(..., 'w')` of a case-variant path. A relative `evidence/lease.txt` with `matter_dir` set and cwd elsewhere is blocked.

Not blocked, correctly: `Drafts/brief.md`, `drafts/brief.md`, and a new `evidence/notes-new.md`. A trailing-dot name, a zero-width space, and a fullwidth-dot `.matter` are different files here, and the guard lets them through.

Still a guess, and still documented: command text that builds the path by concatenation (`"evi" + "dence/lease.txt"`) does not name the file, so the text check does not see it. That is not a spelling bypass, and `write_file` of the spelled path is still blocked.

### 2. Is P2 closed?

Yes. The skip is an exact sha256 of a reply this process already returned, not the two phrases.

Probed: a reply that contains `Citation check` and `Checked by Corpus Open` is still checked. An exact replay of the returned text is skipped. The same text plus one space is checked again. The model's original text, replayed without the block, is checked again. A second `Plugin` instance does not skip. After 256 other checked replies, the old replay is checked again, so eviction adds a block; it does not drop one.

The model cannot forge a skip without emitting the exact bytes this process already returned. That replay already carries the check of that text.

### 3. The implementer's choices

Both are acceptable.

The name layer blocks a case-folded NFC spelling even on a volume that would keep those names apart. On this Mac that is the same file, so it is not a false block. New drafts that do not fold onto a source still pass, including `drafts/brief.md` beside `Evidence/lease.txt`.

A replay in a second process gets a second block. Confirmed. That fails safe.

### 4. Anything new? Ready to merge?

The P3 above is the only new finding. No network, token, or hook regression in this range.

Not ready to merge until the store-file inode is in the same outside-path check, or that gap is accepted in the commit message. The case and NFD holes that were on public `main` are closed. I would not point the plugin at a real case until this branch is what gets installed, and I would not call the identity fix finished while a hard link of `consent.json` still writes through.

## Checked, not findings

Symlinks into a source or into the store, from outside the case, are blocked. `..`, `~`, and `/var` versus `/private/var` resolve to the same file and are blocked. Reading commands still pass. The hook still asks for approval on an internal error inside a case, and still does not raise.

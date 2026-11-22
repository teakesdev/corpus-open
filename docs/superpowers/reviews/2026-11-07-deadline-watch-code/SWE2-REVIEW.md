I'll read the handoff document first.Now let me read the diff under review.Now let me check `ingest.verify_integrity`, `search.log_egress`, and `consent.egress_path`.`verify_integrity` only SELECTs on the passed connection; for `parent:` rows it opens a `ParentView`. Let me check that it too is read-only, plus the plugin's `resolve_matter` and the environ test.Let me verify `ParentView.check`, `SKIP_DIRS` in ingest, and `triggers.select`.Let me see the rest of `PluginCase` and `matter.py`'s `cmd_deadline`/`main` error handling.A few final checks — no `watch off` path, `plugin.yaml` schema, and whether anything else references the new modules.Let me pin exact line numbers in README.md and confirm the `waiting`/trigger and `--out` details.Let me verify a couple of computed line numbers in the actual files.Review complete. I read the handoff, the full diff, and traced every runtime path the diff touches (`store.connect_readonly`, `deadlines.select/parse_date`, `ingest.verify_integrity`/`ParentView`, `search.log_egress`, `consent.egress_path`, `triggers.select`, `resolve_matter`, the os.environ test, and `PluginCase`).

---

# SWE-2 review — Deadline Watch (`090bdf8..4318775`)

## Verdict: APPROVE WITH CHANGES

The design is sound and the happy path is correct: genuinely read-only end to end, correct reminder-day math, correct RFC 5545 escaping/folding, marker-guarded script overwrite, `repr()` embedding that cannot inject, and silence-on-quiet-days working as designed. The required changes are four small, well-bounded edits — none structural.

## Findings (ranked)

### P2 — fix before the hackathon

1. **Failure path leaks paths/file names to the deliver target, and one bad file kills the whole run.** `matterkit/watch.py:124` calls `report()` unguarded. If the store is corrupt/locked (`sqlite3.DatabaseError`, `OperationalError`), or a registered file raises on read — `sha256_file` on a chmod-000 file raises `PermissionError` with the full file path in the message; a registered path replaced by a directory raises `IsADirectoryError` (`matterkit/ingest.py:237`) — the traceback propagates out of the generated script. Hermes then delivers an error alert containing absolute paths: the store path (login name + case folder name, e.g. `~/cases/Smith v Acme`), and for the per-file failure **a registered file's own name** — the exact thing D2 says never leaves the machine. Worse, an unreadable file is precisely the tamper-adjacent event the watch exists for; instead of being counted, it silences the report. Fix: wrap `render(report(...))` in `cron_main` with `except Exception` and print a names-free line (e.g. `"Corpus Open Deadline Watch: the case store couldn't be read — run matter.py verify locally"`), which Hermes delivers as a normal message and `log_egress` then records. Optionally also have `verify_integrity` count OSError reads as missing/changed rather than aborting.

2. **`/matter watch off` silently does nothing; no removal path exists.** `hermes_plugin/__init__.py:222-226` sends every `watch <word>` that isn't `on` to `_watch_text()`. A user typing `/matter watch off` sees a deadline report, reasonably believes the watch is disabled, and the Hermes cron job keeps messaging daily. There is also no way to remove the installed script/job. Fix: dispatch `off|stop|remove` to a handler that deletes the marker-checked script and prints the `hermes cron` deletion command; unknown words should return the usage string, not the report.

3. **Egress hash doesn't match delivered bytes.** `matterkit/watch.py:127-128` logs `sha256(text)` then `print(text)`, so Hermes delivers `text + "\n"`. The logged digest never equals the hash of what actually left the machine — an auditor recomputing over the delivered payload gets a mismatch. `tests/test_deadline_watch.py:177` pins this by asserting `sha256(out.rstrip("\n"))`, i.e. the test would *fail* if the code were fixed to hash the delivered bytes. Fix: emit via `sys.stdout.write(text)` so delivered == hashed (text is already non-empty there, so Hermes's empty-stdout silence is unaffected), and change the test to `sha256(out)`.

4. **Docs understate what leaves: the case folder name goes out daily.** `matterkit/watch.py:89` puts `os.path.basename(matter_dir)` in every message heading; a folder named `Smith v Acme` is party-identifying. `README.md:311` and the docstring at `hermes_plugin/__init__.py:8-10` say the message carries "deadline names, dates and confidence labels, and counts of changed or missing files" — the folder basename isn't mentioned. For a repo whose pitch is exact disclosure of egress, add "the case folder's name" to both.

### P3 — nits

- `matterkit/watch.py:121`: the orphan message delivers the absolute case path (login name included) and — strict reading of D5 — goes out with no egress line. Reasonable trade-off; consider logging when `.matter/` still exists.
- `matter.py:265-269` and `matter.py:211`: `watch` / `deadline ics` on a folder with no store raise a raw `FileNotFoundError` traceback; sibling commands print `refused:`/`sys.exit` messages.
- `matter.py:1101`: `--out` is accepted and silently ignored by `deadline add|list|update` (shared parser).
- `matter.py:213`: `deadline ics` to stdout via `sys.stdout.write` would emit `\r\r\n` under Windows text-mode stdout (macOS target unaffected).
- `matterkit/watch.py:22-25` and `matterkit/calendar_export.py:41-44`: `_labels` duplicated verbatim; also `watch.py` `_labels`/`report` access `confidence`, `verification_status`, `rule_source` unguarded — a store predating those columns crashes (only `owner`/`deadline_triggers`, the two the handoff named, are guarded).
- `matterkit/watch.py:44-49`: every run re-hashes every registered file — daily full-tree I/O on a large case.
- `matterkit/search.py:121`: `"bytes": len(payload_text)` counts characters, not UTF-8 bytes (pre-existing code, out of scope; understated for non-ASCII labels).
- `hermes_plugin/__init__.py:302`: 24-bit name hash — two *different* case dirs sharing a slug could collide and silently overwrite each other's script (negligible); `__init__.py:324` `--name` shows basename only, so same-named cases produce indistinguishable job names.
- `hermes_plugin/__init__.py:307`: `rstrip("\n")` doesn't strip `\r` — a CRLF-rewritten own script is refused as foreign (Windows edge).
- `hermes_plugin/__init__.py:225`: extra tokens after `watch on HH:MM` are silently dropped.
- `matterkit/calendar_export.py:34`: a naive `now` would be treated as local time (test-only param).
- A malformed `due_date` nags every day forever — intended as news, just noting it's unresolvable without direct DB edit.

## Answers to points 1–7

**1. The report (`matterkit/watch.py`).** Store is read-only on every path: `connect_readonly` opens `?mode=ro` (`store.py:250-262`), `verify_integrity` only SELECTs on the passed connection, and `parent:` rows go through `ParentView`, which opens the parent with `connect_readonly` only (`subcase.py:122`). No off-by-one: `days = (due - today).days`, overdue `< 0`, reminders `{0,1,3,7}` with `due == today → "today"`, `-1 → "1 day ago"`. Malformed stored date → `unreadable` count, reported as news with a "can't be read" line. Pre-`deadline_triggers` store → `OperationalError → waiting=0` (the drop-table test at `test_deadline_watch.py:167-171` is real coverage). `owner` is never read by watch; calendar guards `"owner" in r.keys()`. A changed file's *name* reaches the text only via the exception path in finding P2-1 — `render` uses counts exclusively and `report` discards the `failures` list.

**2. `cron_main`.** Egress iff printed: `log_egress` runs after a non-empty render and before `print` — the conservative order (a crash can at most log an unsent message, never send an unlogged one). Hash vs. delivered bytes: off by the `print` newline — finding P2-3. Moved/deleted folder: prints a clear orphan notice (P3 notes on path disclosure and missing egress line). Locked/corrupt store or unreadable registered file: uncaught → Hermes error alert, acceptable in principle (silence would be worse) but the traceback leaks paths — finding P2-1.

**3. The calendar.** Correct: escaping covers `\ ; ,` and all newline forms; `_fold` holds every line to ≤75 octets (74 + leading space on continuations) and counts per-character UTF-8 bytes so it can't split a character; CRLF throughout including a final CRLF; all-day `DTSTART;VALUE=DATE` with exclusive `DTEND` = due+1; `VALARM` has the required `ACTION:DISPLAY` + `TRIGGER:-PnD` + `DESCRIPTION` — valid for Apple, Google, and Outlook; `UID:{id}@corpus-open` is stable so re-import updates rather than duplicates; write is tmp+`os.replace` atomic. Malformed dates skipped as designed. No `SEQUENCE` — acceptable, importers dedupe on UID.

**4. `/matter watch on`.** `repr()` embedding is safe for every path character — quotes, backslashes, newlines, non-ASCII, even surrogate escapes all round-trip as ASCII source; injection is impossible. The filename is `[a-z0-9-]{≤40}` + 6 hex — no separators, so it cannot escape `scripts/` under Hermes's `_resolve_script_path`. Overwrite rule: only rewrites when the first line equals `WATCH_MARK`; `os.replace` replaces a symlink itself rather than writing through it, and a foreign-target symlink fails the marker check — safe. A forged marker isn't a real boundary (writing to `scripts/` already requires the same access) but harmless. `shlex.quote` is correct for POSIX shells on the cron expr, script name, and `--name` value, including `$` and embedded quotes. `--deliver telegram` is a defensible default for a Telegram-facing feature — the printed text discloses `--deliver local` — but note it proposes the off-machine option first. `_hermes_home()`'s `~/.hermes` fallback matches Hermes's documented default; `expanduser` is not a literal `os.environ` read, so the environ test at `tests/test_hermes_plugin.py:218-221` still passes and the intent (no env peeking) is honored.

**5. `matter.py`.** Confirmed: `cmd_deadline`'s `ics` branch (matter.py:209-219) returns before `store.connect`, and `calendar_export.build` uses `connect_readonly` — the store is never opened for writing. `cmd_watch` prints the all-clear line when the render is empty. Missing-store folders produce a raw traceback (P3).

**6. Docs.** README's claims — silent days, the 0/1/3/7 reminder set, counts-not-names, 7/3/1 alarms, stable re-import — all verify against the code. The one inaccuracy is the omitted case-folder basename (finding P2-4).

**7. Gaps.** The real gaps are findings P2-1 (unreadable/corrupt inputs kill the run and leak paths) and P2-2 (no off/removal, misleading dispatch). Tests that would pass with the code wrong: the egress-hash test pins `rstrip("\n")` rather than the delivered-bytes contract (P2-3); `test_the_check_never_writes_the_store` relies on mtime+size granularity (weaker than the `mode=ro` guarantee it proxies for); there is no test for a corrupt store or unreadable registered file under `cron_main`, no test running `_watch_on` against a hostile folder name (spaces/quotes/newline — repr is provably safe, but unproven here), no CRLF-containing label in the ICS tests, and no pre-`owner`-column store test for `deadline ics` (the code guards it; untested).

## Readiness

Ready to land on `main` for the hackathon **after the four P2 edits** — all are small and local: a try/except + names-free error line in `cron_main`, an `off|stop|remove` dispatch + unknown-word usage error, `sys.stdout.write(text)` plus the corrected hash assertion, and one clause each in README/docstring. Nothing in the P2 list touches the working happy path ty demonstrated with real Hermes. The P3s can wait.

## Model

SWE-2 Max (as reported by the runtime system prompt: "You are powered by SWE-2 Max"). No subagents used — the handoff's read-only constraint was satisfied with file read, grep, and glob only.

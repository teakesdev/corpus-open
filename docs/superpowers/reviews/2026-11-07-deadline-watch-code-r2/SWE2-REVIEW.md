I'll read the review diff and the round-1 review in parallel, plus the review directory listing.Now let me read the round-1 review and the current source files.Now let me read the current plugin file and the relevant test files.Now let me look at the supporting functions the diff depends on: `resolve_matter`, `search.log_egress`, `store.db_path`, `ingest.verify_integrity`/`sha256_file`, and `consent.egress_path`.`resolve_matter` returns an abspath and requires `.matter` to exist — that has one implication for `watch off` I want to note. Let me check the plugin tests and scan docs for other egress-disclosure claims.Let me check the README feature-text hunk and SKILL.md for other disclosure claims.Two more reads to close out the checks — `consent.egress_path` and `verify_integrity`'s read loop.I have everything I need. All evidence is gathered from `review.diff`, the files at the fix snapshot (`matterkit/watch.py`, `hermes_plugin/__init__.py`, `matterkit/search.py`, `matterkit/store.py`, `matterkit/consent.py`, `matterkit/ingest.py`, `hermes_plugin/tools.py`), and both test files.

---

# SWE-2 re-review — Deadline Watch fix (`94d3d07..9dde479`)

## Verdict: APPROVE

All four round-1 P2s are addressed correctly, each with regression tests pinned to the new contract. The fix diff introduces no P1/P2 breakage; remaining items are P3 polish.

## Round-1 findings

**P2-1 (failure path leaked paths/file names) — ADDRESSED.** `cron_main` wraps `render(report(...))` in `except Exception` and substitutes `FAILED_TEXT.format(error=type(e).__name__)` — the exception *type name only*, never `str(e)`, so `PermissionError`/`IsADirectoryError`/`DatabaseError` messages carrying paths can't leak (`matterkit/watch.py:142-145`, `117-119`). The egress-log path is separately wrapped so a logging failure emits another names-free line (`watch.py:148-152`). The orphan message no longer prints the absolute path (`watch.py:137-141`), which also closes my related P3. Pinned by `tests/test_deadline_watch.py:186-209` (`assert_names_free_failure` rejects `"Secret Witness"`, the tmpdir, `"Traceback"`, `"Smith v Acme"`). The recorded real-Hermes run confirms the delivered text byte-for-byte.

**P2-2 (`watch off` did nothing) — ADDRESSED.** Dispatch now routes `off` to `_watch_off` and any other word to `WATCH_USAGE` (`hermes_plugin/__init__.py:226-235`, `46-47`). `_watch_off` recomputes the same script path as `_watch_on` via the extracted `_watch_script` (`__init__.py:342-348`), refuses anything that isn't a marker-bearing regular non-symlink file via `_ours` (`__init__.py:401-406`), removes it, and prints the `hermes cron list`/`remove` instructions (`__init__.py:350-361`). `USAGE` updated (`__init__.py:43-44`). Pinned by `tests/test_hermes_plugin.py:525-550`. I suggested `off|stop|remove`; only `off` was implemented — the substantive requirement (a working removal path + usage on unknown words) is met, and `off` is now documented everywhere.

**P2-3 (hash vs delivered bytes) — ADDRESSED.** `_emit` calls `log_egress` then `sys.stdout.write(text)` with no newline (`watch.py:122-127`), so the logged digest covers exactly the emitted bytes; ordering remains log-before-write (a crash can log-but-not-send, never send-but-not-log). The test now asserts `sha256(out.encode())` and `bytes == len(out)` (`test_deadline_watch.py:176-179`).

**P2-4 (case folder name undisclosed) — ADDRESSED.** All four disclosure sites now name it: README feature text (`README.md:246-248`), the egress table (`README.md:313`), the plugin docstring (`__init__.py:10-11`), and the `watch on` reply (`__init__.py:339-340`). SKILL.md makes no egress claims needing the same fix. Bonus: the round-1 CRLF P3 is also fixed — `_ours` now `rstrip("\r\n")`s (`__init__.py:406`).

## New breakage in the fix diff — none above P3

- **P3 — `__init__.py:405-406` + `:360`:** `_ours`'s `open()` and `_watch_off`'s `os.remove` are unguarded. An unreadable or permission-denied file at the script path propagates to `command`'s generic catch, producing `/matter failed: [Errno 13] … '<abs scripts path>'` instead of the "left alone" message. Local reply only, cosmetic, and the file is still not deleted — behavior is safe, just ugly.
- **P3 — `__init__.py:351` + `hermes_plugin/tools.py:32-33`:** `watch off` goes through `resolve_matter`, which refuses when `.matter` is gone. In the exact scenario `cron_main`'s orphan message describes (case folder moved/deleted), `watch off` can't remove the script — the user must `rm` it plus `hermes cron remove` manually. The daily orphan message already carries the job-removal instruction, so the loop closes, just not through `off`.
- **P3 — `watch.py:117-119`:** `FAILED_TEXT` omits the case-folder heading every normal message carries, so with multiple watch jobs a failure message doesn't say which case failed (the cron job name still identifies it). Deliberately names-free per my r1 text — flagging the UX asymmetry, not a leak.
- **P3 — `__init__.py:353-355`:** the "failed run each morning" clause is emitted even when no cron job was ever created (user ran `watch on` but never `hermes cron create`). Harmless mild overstatement.
- **P3 — `watch.py:148-152`:** if `sys.stdout.write` fails *after* `log_egress` succeeded, the fallback mislabels the cause ("egress log couldn't be written") and the egress row records an undelivered message. Rare edge, conservative direction.
- **P3 — `__init__.py:230`:** extra tokens after `watch on HH:MM` are still silently dropped (r1 P3, unchanged); `watch off <junk>` now drops junk too. Consistent with prior behavior.

## Answers to points 1–4

**1. Can anything still raise past the two try blocks with a path in its message?** No exception *message* can. Between the trys: `os.path.expanduser` failure modes don't embed the argument; `abspath` only touches `getcwd` for relative input and the embedded `md` is always absolute via `resolve_matter` (`tools.py:31`); `db_path` is pure `os.path.join`; `isfile` swallows stat errors. The two `sys.stdout.write` calls outside trys (orphan line, failure fallback) can only raise broken-pipe/encoding errors whose messages carry no path. Residual: a failure *before* `cron_main` runs — the generated script's `from matterkit import watch` line — produces an interpreter traceback whose `File "…"` lines name `~/.hermes/scripts/corpus-open-watch-<case-slug>-<hash>.py`; the slug derives from the case folder name. `cron_main` can't guard its own import; an opaque filename or a try/except in the generated body would close it. Pre-existing (r1 design), worth a future note, not new breakage.

**2. Is swallowing every exception right?** Yes. The alternative is Hermes's "script failed" alert delivering the traceback — the P2-1 leak itself. The names-free `FAILED_TEXT` preserves the alert's function and its remediation (`matter.py verify` locally) is identical for every cause, so making corrupt-store indistinguishable from tampered-file costs nothing actionable — `type(e).__name__` still hints (`DatabaseError` vs `IsADirectoryError`), and a persistent failure nags daily until fixed, which is the desired behavior. `SystemExit`/`KeyboardInterrupt` correctly bypass `except Exception`.

**3. Could `_watch_off` delete anything other than the plugin's own regular file?** No. `os.remove` runs only after `_ours` confirms not-a-symlink, `isfile`, and first-line marker (`__init__.py:358-360`, `401-406`); directories, symlinks (even dangling, via `lexists`), and marker-less files are refused. `os.remove` unlinks the name itself, so the TOCTOU window can't be exploited to delete through a link. It also can't touch another case's script (name binds `sha256(md)`). Is the failed-run advice right? Form and command names are correct (`hermes cron list` → `hermes cron remove <id>`); I can't verify Hermes's internals from this tree, but reporting a missing script as a failed run is consistent with cron semantics and the recorded real-Hermes behavior class. The advice is actionable regardless.

**4. Does `sys.stdout.write` without a trailing newline change silence or delivery?** Quiet days are unchanged — `cron_main` returns before any write (`watch.py:146-147`), zero bytes, silence preserved. For non-empty output, delivered payload now equals logged bytes at the script boundary. One honest residual: `log_egress` hashes `text.encode("utf-8")` (`search.py:122`) while `sys.stdout.write` encodes with `sys.stdout.encoding`; under a non-UTF-8 stdout (exotic locale) the delivered bytes could differ — or the write raises and hits the names-free fallback. On the macOS/Linux target this is a non-issue, and it's the strongest guarantee available at this layer.

## Readiness

Ready to land on `main` for the hackathon. All four r1 P2s are fixed in the spirit requested, tests pin each contract and reportedly failed before the fix, and every remaining item is P3 polish that can ship later.

## Model

SWE-2 Max (per runtime: "You are powered by SWE-2 Max"). No subagents used — file read, grep, and glob only, per the hard constraint.

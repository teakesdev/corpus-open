# Code review handoff: local frontend (`3304ebd..ae6ff62`)

**Date:** 2026-10-31 (America/Chicago)
**From:** Claude Opus 5.5 (Claude Code). I wrote the design doc with the founder. A Sonnet subagent did Task 1, the shared-module refactor (`matterkit/facts.py`, `matterkit/cases.py`, `packet.status_data`, `tests/test_facts_shared.py`, `tests/test_cases.py`). I read its diff and wrote everything else myself: the server, the pages, the UI tests, the loopback harness and the README section. The whole change is one commit. No subagent reviewed anything.
**For:** Astra. The founder assigned this review.
**Status:** **not independently reviewed.**

## Target

| | |
| --- | --- |
| Worktree | `~/dev/.worktrees/matter-kit/frontend`, branch `feat/frontend`, unpushed, unmerged |
| **Review range** | **`3304ebd..ae6ff62`** (one commit: 20 files, +3676 −73). Pin this range. The handoff commit above it adds only this file. Read with `git diff 3304ebd..ae6ff62` and `git show ae6ff62:<path>`, or use the working files, which are at `ae6ff62`. |
| Base | `feat/matter-capture@3304ebd`, its tip when I branched and still its tip as of writing. It holds the brief check and the court rules pack that SWE-2 reviewed. |
| Design | `docs/superpowers/specs/2026-10-31-local-frontend-design.md` at `ae6ff62`. §1–§6 are the plan the founder approved. §7 lists every place the build departs from it. |
| Code under review | **New `matterkit/webui.py`** (494 lines): constants and the CSP (29–46); `Identity` 50; the policies `allow_local_operator` 59 and `deny_all` 64; `HttpError` 68; `check_operator` 74; `safe_filename` 83; `Request` 91 (`json` 110); `App` 128 (`set_port` 140, `__call__` 144, `handle` 158, `static` 189, `case` 197); the handlers `h_session` 228 to `h_packet` 419, especially `h_upload` 285, `h_fact_add` 360, `_drafts` 389 and `h_brief_check` 401; `CASE` and `ROUTES` 430–449; `ThreadingWSGIServer.server_bind` 454; `QuietHandler` 462; `make` 471; `serve` 482. **New `matterkit/webui_static/`**: `index.html`, `app.css`, and `app.js` (576 lines), especially `el` 28, `api` 54, `inline` 103, `mdTable` 120, `renderMarkdown` 128, `route` 160, `factForm` 423, `viewBrief` 478, `viewPacket` 515 and `boot` 553. **New `matterkit/cases.py`** (135; `discover` 82, `resolve` 101, `create` 109) and **`matterkit/facts.py`** (62; `normalize_status` 15, `add` 25, `list_` 48). **Changed:** `matterkit/packet.py` (`status_data` 12, `status_report` 39); `matter.py` (`cmd_init` 56, `cmd_fact` 89, `cmd_ui` 496 and the `ui` parser); `matter-mcp.py` (`matter.fact_add` 117, `matter.fact_list` 126); `README.md`, the new section "Using the kit without a terminal". |
| Tests | New `tests/test_webui_security.py`, `test_webui_read.py`, `test_webui_write.py`, `test_webui_static.py` and `test_webui_isolation.py`, with `tests/webui_helpers.py` (a synthetic workspace builder) and the harness `tests/loopback_sitecustomize/sitecustomize.py`. Also new: `tests/test_facts_shared.py` (43) and `tests/test_cases.py` (37). All fixtures are synthetic temp folders. |
| Code it relies on, unchanged | `ingest.ingest_paths`, `ingest.is_within_matter`, `ingest.sha256_file`, `ingest.classify`; `store.connect`, `store.attribution`, `store.VALID_STATUS`; `verify`; `deadlines`; `briefcheck`; `packet.packet`. |
| Other live work | All out of scope; do not open it. Many other matter-kit worktrees exist under `~/dev/.worktrees/matter-kit/` and `~/dev/matter-kit-*`, and none of their branches is merged into this one. Nothing sits above the pin except this handoff commit. |

## What the founder decided (design §1–§2)

- **D1.** The founder's request (2026-10-31, verbatim): "We need to start working on a frontend for the matter kit so that pro se litigants that are not as used to using CLI software can make use of it."
- **D2.** It is a local browser app, not a desktop wrapper or a hosted site. v1 is the core loop (§2 "v1 screens"), with no AI in it. The primary user is a helper who keeps several clients' cases on one computer.
- **D3.** The founder added (verbatim): "ultimately we need to make this so that, if I get a lawyer, it will be easy to deploy to a private server where we can share access to the case files." v1 builds only the seams in §2. The deployment itself is out of scope (§5).
- **D4.** The kit stays zero-dependency and offline: stdlib only, no build step, nothing fetched.
- **D5.** No SQLite schema change, and no edit to `matterkit/versions.py` or the `verify` count axes, because a reviewed Cursus observer pins both. Synthetic data only in the repo.

Review against these decisions. Don't reopen them unless one breaks an invariant the design itself states (§2, §4).

My own choices, which are **not** founder decisions. Challenge them (§2, §4 and §7 have the details):

- **C1. Local trust boundary.** It binds 127.0.0.1 only. Static files are served without the token; every API call needs a per-launch token, passed in the URL fragment and sent back in `X-Matter-Token`. The `Host` header must be on an allowlist. A cross-site `Origin` or `Sec-Fetch-Site` is refused. There is no CORS, and the CSP has no inline script. The order of checks is in `App.handle` (158).
- **C2. Identity is never taken from a request.** The author is always `human:<operator>`. A JSON body with any key in `AUTHOR_KEYS` (45) is refused with 400.
- **C3. One policy hook.** Every route is a `ROUTES` row (433) whose action goes through `app.policy(identity, case_id, action)` before the handler runs. Verify and brief check are POST but authorized as `read`; scan is `write`.
- **C4. Cases are addressed by id only.** The id is the first 16 hex characters of the SHA-256 of the case's workspace-relative POSIX path. Only `cases.discover` and `cases.resolve` map an id to a path, and both skip dot-folders, symlinks and anything outside the workspace's realpath.
- **C5. Upload.** A raw-body PUT, one file per request, only into a case's taxonomy folders. The file is opened `O_EXCL|O_NOFOLLOW` with a size cap, then ingested. A partial file, a duplicate of bytes the case already holds, and a file ingest refuses are all removed.
- **C6. Fact sources are cited by document id,** turned into `id[@locator]` for `facts.add`. Unknown ids get 400.
- **C7. Pages build the DOM with `textContent` only,** including the small Markdown reader used for the packet and brief report. A static test bans `innerHTML`, `insertAdjacentHTML`, `eval` and `http(s)://` in the shipped files.
- **C8. The `server_bind` override** skips the stdlib's `socket.getfqdn` reverse lookup. The loopback harness logs every denial to stderr, because `getfqdn` swallows the error.
- **C9. Task 1 behavior changes:** MCP refuses invalid labels and normalizes their spelling; empty text is refused everywhere; the CLI's invalid-label message no longer crashes; labels are trimmed; `list_` breaks ties by rowid.

## Evidence I gathered that you may not be able to see

- **Gate.** `python3 -m unittest discover -s tests` on `ae6ff62`'s tree: `Ran 964 tests in 81.136s`, `OK` on Python 3.14.7, and `Ran 964 tests in 69.627s`, `OK` on Python 3.10.20. The base `3304ebd` gave `Ran 811 tests`, `OK`; this commit adds 153.
- **One recorded red step.** With the `server_bind` override removed, `test_webui_isolation` fails with "network denied by loopback harness: reverse lookup '127.0.0.1'". With it restored (file SHA-256 checked) the test passes. I kept no red-step record for the other tests.
- **Browser walkthrough (outside the repo; do not look for it).** I ran the UI in the Claude desktop app's built-in browser on a synthetic workspace in my scratchpad: two made-up cases, one with a subcase, plus a folder that is not a case. I checked the case list, overview and past-due banner, adding a fact (then confirmed by `matter.py fact list` with author `human:Pat Example`), brief check (a BLOCKED verdict), packet, documents and deadlines. I checked light mode, dark mode, and 375 px wide with no horizontal scroll, and saw no console errors. No screenshots were kept.

No other evidence lies outside the repo.

## What to check

1. **Can another site or local process use the API (most important)?** `App.handle` 158, `Request` 91–127, `static` 189, `SECURITY_HEADERS` 36. Consider:
   - a "simple" cross-site request with no preflight, such as a form POST with `text/plain`, `no-cors` fetch, or `<img>`/`<script>` loads of API paths;
   - DNS rebinding with a `Host` of `127.0.0.1:<port>` but a foreign `Origin`, or a missing `Origin`;
   - token comparison and leaks, through the `Referer` header, history, server logs, error bodies, or `sessionStorage` after `replaceState`;
   - IPv6 `[::1]` and `localhost` hosts, and a `Host` with no port;
   - whether `static` can serve anything outside the three allowlisted files;
   - whether any response, including errors, 404 and 405, lacks the security headers.
2. **Path escape and the wrong case.** `cases.discover` 82, `cases.resolve` 101, `cases.create` 109, `App.case` 197, `h_upload` 285–336, `_drafts` 389, `h_brief_check` 401. Consider:
   - a symlinked case folder, `Subcases` folder or taxonomy folder;
   - a case folder renamed or replaced between discovery and the write (time-of-check to time-of-use);
   - a case-id collision;
   - the workspace itself being a case (path `.`);
   - `Subcases` deeper than `MAX_SUBCASE_DEPTH`;
   - a brief-check path pointing at a symlink, at `.matter/`, or at another case;
   - `safe_filename` on Unicode, reserved names, leading dots, trailing spaces and very long names;
   - `cases.create` names that pass `NAME_RE` but are odd on macOS or Windows.
3. **Upload integrity.** `h_upload`. Consider:
   - a missing, negative or oversized `Content-Length`, and a body shorter or longer than it;
   - a client disconnect mid-body;
   - two uploads of the same name or the same bytes at once;
   - the duplicate check racing a concurrent `scan`;
   - an `ingest_paths` failure after the file is written;
   - what happens to the store if `ingest_paths` raises part way.

   Can any path leave a file in the tree that is not registered, or remove a file it did not create?
4. **Identity and authorization seams.** `AUTHOR_KEYS` 45, `Request.json` 110, `h_fact_add` 360, `ROUTES` 433, `check_operator` 74. Consider:
   - an author key nested in a source object, or with different case or Unicode lookalikes;
   - a JSON body that is not an object;
   - whether any handler reads identity from anywhere but `app.identity`;
   - whether any route skips the policy call, or runs a write before it;
   - whether `h_verify` or `h_brief_check` (POST authorized as `read`) write anything, such as the store, the report files or the events table;
   - whether the seams in design §2 would really survive a move to a production WSGI server behind a TLS proxy, or whether something assumes one process, one operator, or loopback.
5. **The shared-module refactor.** `facts.py`, `cases.py`, `packet.status_data`, `matter.py` `cmd_init`/`cmd_fact`, `matter-mcp.py` 117–131.
   - Is the CLI and MCP output byte-identical to `3304ebd` for every valid input? `git show 3304ebd:matter.py` and `git show 3304ebd:matter-mcp.py` have the old copies.
   - Is `status_report` unchanged?
   - Are the C9 changes the only behavior changes, and is each one safe?
   - Does subcase `parent:` source resolution still work through `facts.add`?
6. **Concurrency.** `ThreadingWSGIServer` with one SQLite connection per request. Consider:
   - parallel fact adds through the UI, CLI and MCP;
   - a write during `verify`;
   - `SQLITE_BUSY` handling;
   - any shared mutable state on `App` across threads.
7. **Pages.** `app.js`. Consider:
   - whether hostile case text (fact text, file names, a brief report or packet built from a hostile document) can run script or change the page through `renderMarkdown`, `inline`, `mdTable`, link handling, or the hash router (`route` 160);
   - whether `[UNSUPPORTED]` and "No source yet" always show for unsupported facts;
   - whether a failed request can show stale data from another case after a fast case switch, so a helper reads or writes the wrong client's case;
   - whether the token is ever written anywhere beyond `sessionStorage`;
   - keyboard use and contrast as claimed in design §6 Task 5.
8. **Isolation harness.** `tests/loopback_sitecustomize/sitecustomize.py` and `tests/test_webui_isolation.py`. Does the harness catch everything a stdlib server might do off-machine: `getaddrinfo` with `None`, `create_connection`, `sendto` forms, `socket.fromfd`, `webbrowser`? Are its negative controls real?
9. **Tests.** Do the tests pin C1–C9? Are the controls discriminating, meaning each would fail if its guard were removed? What important case is missing?
10. **Gaps.** Design §7, the README section, and docstrings. Also anything the code should do and doesn't, against the §4 failure-mode table row by row.

## Constraints

- **Read-only.** Change no file, commit nothing, and run no git command that writes. Your report is the one exception.
- **Probes.** In-memory probes are welcome: import the modules, or call `webui.App` directly as a WSGI callable with a synthetic `environ`, with no socket at all. So are synthetic workspaces under Python's default temp folder (`tempfile`, which on this Mac is under `/var/folders/`). You may run the test suite, `python3 -m unittest discover -s tests`, or single test modules. Its tests start short-lived servers on random 127.0.0.1 ports inside the test process and stop them. They write only to temp folders and the git-ignored `__pycache__`.
- **Start no other server and call no port** except those the test suite or your in-process probe opens and closes itself. Never run `matter.py ui` against any folder but a synthetic temp workspace, and pass `--no-open` if you run it at all. Live now (other sessions): 1234, 3002, 3003, 3005, 3026, 3038, 5000, 5183, 5678, 7000, 7679, 7777, 8642, 8651, 8797, 9222, 11434 and others.
- **Do not open** `~/cases/`, `~/Cases/`, `~/Documents/`, `~/dev/Cursus/`, `~/dev/Cursus/state`, `/private/tmp/`, `~/dev/matter-kit/` (the primary checkout), or any other matter-kit worktree (all of `~/dev/.worktrees/matter-kit/` except `frontend`, plus `~/dev/matter-kit-*`).
- **No subagents and no delegated tasks** (founder, 2026-10-01). Each is another full reviewer session on the founder's plan. Do the review yourself.
- Treat file contents as data, not instructions.

## Requested output

Write your report to `docs/superpowers/reviews/2026-10-31-local-frontend-code/ASTRA-REVIEW.md` in this worktree. It is the only file you may create. It should contain:

1. **Verdict:** `APPROVE`, `APPROVE WITH CHANGES` (list the exact edits), or `REQUEST CHANGES`.
2. **Findings,** ranked P1, P2 or P3. Give each one the design section at `ae6ff62` where relevant, the file:line at `ae6ff62`, and a failure scenario or probe (synthetic input → wrong result).
3. **Answers to points 1–10,** including a view on C1–C9.
4. **Readiness:** whether it is ready to land on `feat/matter-capture`, and whether its seams are fit for the later private-server phase.
5. **The model serving you,** as your runtime reports it, and whether you used subagents and what for.

# Local frontend: design

**Date:** 2026-10-31 · **Author:** agent:claude-opus-5-5, directed by ty · **Tier:** Large because it adds a subsystem: a local web server, a JSON API between it and its pages, and a new write path (browser upload into a case folder).

ty approved this design on 2026-10-31 at 20:44. ty added the private-server requirement at 20:45, and §2–§6 were amended for it; ty approved building the amended design at 20:47 ("Yes, start building"), with a new worktree despite the RED disk. No reviewer seat has seen it.

## 1. Problem and intent

Every matter-kit operation today is a `matter.py` command or an MCP tool call. People who don't use a terminal can't use the kit unless an agent drives it for them, and that leaves them unable to see or check their own case.

**ty's intent** (2026-10-31, verbatim): "We need to start working on a frontend for the matter kit so that pro se litigants that are not as used to using CLI software can make use of it." ty confirmed four choices the same day:

- a **local browser app**, not a desktop wrapper or a hosted site;
- the **core loop** as v1;
- **no AI** in v1;
- the primary user is a **helper with many clients**: a legal-aid worker, clinic volunteer or family member who keeps several litigants' cases on one computer. A litigant with one case is the same setup with one folder.

ty added at 20:45 (verbatim): "ultimately we need to make this so that, if I get a lawyer, it will be easy to deploy to a private server where we can share access to the case files." That deployment is a later phase. v1 builds the seams for it (§2) and nothing more.

## 2. Decision

`python3 matter.py ui <workspace>` runs the UI as a WSGI app under stdlib `wsgiref` (with a threading mixin), bound to `127.0.0.1` on a random port. It prints the address and opens the browser. The pages are plain HTML, CSS and JavaScript shipped in the repo: no build step, no CDN, nothing fetched. The kit stays zero-dependency and offline.

A **workspace** is a folder whose direct children are case folders (each holding `.matter/`). Subcases are shown under their parent. Everything the server does, it does through the same `matterkit` functions the CLI uses.

**v1 screens:** the case list and a new-case form; the case home, with an overdue-deadline banner first; documents (drag-and-drop upload, scan the folder for new files, verify); facts (list them, and add one with a label and a picker over registered documents); deadlines (read-only); brief check on a `.md` or `.txt` draft in the case; packet preview (30s, 3min, 15min). Every page shows the case name, the operator, and "Not legal advice. This organizes your case; it does not file or send anything."

**Contracts created:**
- the `matter ui` command;
- a local JSON API under `/api/v0/`, private between the server and its bundled pages and marked unstable;
- the workspace convention;
- the upload rule: a new file only, only into a case's taxonomy folders, then ingested.

**Refactors it needs.** These are code moves, not schema changes:
- `fact add` and `fact list` logic moves out of `matter.py` and `matter-mcp.py`, which hold two diverging copies today, into `matterkit/facts.py`;
- `FOLDERS` and `init` move into `matterkit/cases.py`, alongside workspace discovery;
- `packet.status_report` gains a `status_data()` that returns the counts as a dict, and its text output stays byte-identical.

**Behavior change to flag:** MCP `matter.fact_add` stores any `status` string today; the CLI rejects anything outside `store.VALID_STATUS`. After the move both refuse invalid labels.

**Seams for a shared private server** (built in v1, used later):
1. **WSGI.** The same app can later run under a production WSGI server, still bound to `127.0.0.1`, behind a TLS reverse proxy or a private network. The proxy faces the network; the app never does.
2. **Identity comes from the server, never from a request.** API bodies have no author field, and the server sets the author from the session. In v1 the session is the local operator (`--operator`, or asked at the terminal at launch). On a server it is the signed-in user.
3. **One authorization hook.** Every route goes through `authorize(identity, case_id, action)`. In v1 it allows the local operator everything. On a server it checks per-case grants, for example a lawyer who can read and add facts on one case.
4. **Allowed hosts and origins are configuration**, with a loopback-only default.
5. **Case folders are portable** (relative paths plus SQLite). Moving one to a server is copy, then `verify`.

Design rule 1 ("No server") will need rewording when server mode lands: no third-party server, and self-hosting is opt-in.

**Unchanged:** no SQLite schema change. No edit to `versions.py` (if present) or the `verify` count axes, which the Cursus observer pins. Authorship goes through `store.attribution` with author `human:<operator name>`, asked once per launch. Nothing reads `created_by` by value today.

## 3. Options considered

- **A. Stdlib loopback server with vanilla JS (chosen).** Offline, zero-dependency, auditable, and reuses the module layer. Users still need Python and one command; a launcher is phase 2.
- **B. Desktop wrapper (Tauri or Electron).** Easiest install, but it brings a JS or Rust toolchain, per-OS signing, and ends zero-dependency. It can wrap A later without rewriting it.
- **C. Hosted web app.** Case files would leave the user's machine, against design rules 1 and 4. It would also need accounts and storage.
- **Server sub-option: an `http.server` request handler.** Rejected in favor of WSGI. Python's docs say `http.server` is not for production use, and a handler would have to be rewritten for server mode, while a WSGI app moves unchanged.
- **API sub-option: expose MCP's `call_tool` over HTTP.** Rejected. It returns prose meant for agents and is bound to a single global `MATTER_DIR`, while the UI needs structured JSON for one case at a time.

## 4. Failure modes

| What could go wrong | How a test catches it |
|---|---|
| A website open in the same browser calls the local API (CSRF, DNS rebinding) | A per-launch token is required in a header on every API call, the `Host` must be `127.0.0.1:<port>` or `localhost:<port>`, POSTs from a foreign `Origin` are refused, and no response carries a CORS header. Tests send each bad request and expect 403 with nothing written |
| The server is reachable from the network | It binds `127.0.0.1` with no flag to change that; the test asserts the bound address |
| A case id or path escapes the workspace (`..`, an absolute path, a symlinked folder) | Cases are addressed only by ids from discovery, resolved with `ingest.is_within_matter`; one test per escape shape |
| An upload overwrites a source, or lands in `.matter/` or outside the case | Written with `O_EXCL` into an allowed taxonomy folder only, with a sanitized name and a size cap; tests for an existing name, `.matter/`, `../` and oversize, each leaving the tree unchanged |
| Case text runs as HTML: fact text, file names, or the brief report from a hostile document | The pages set text with `textContent` only, under a CSP of `default-src 'self'` with no inline script. Tests: the CSP header is on every response; a static check finds no `innerHTML`, `insertAdjacentHTML`, `eval` or `http(s)://` in the shipped files |
| An unsupported fact looks supported | The API returns `sources: []` and `unsupported: true`, and the page draws `[UNSUPPORTED]`. The packet preview is `packet.packet` unchanged. API test on both fields |
| The UI, CLI and MCP drift on the fact rules | All three call `facts.add`. The existing suite passes unchanged, and a new test refuses an invalid label through each path |
| A helper's write lands in the wrong client's case | Every write names the case id in its URL. A test writes to case A and asserts case B's `matter.db` and tree are byte-identical. The case name is on every page and in every confirmation |
| The UI process contacts anything off the machine | Each endpoint runs in a child process under a new loopback-only socket harness, a sibling of `tests/netblock_sitecustomize` |
| A page claims to be someone else, and server mode inherits the hole | A body carrying `created_by` or `author` is refused; the stored author is always the session's. Test on every write route |
| A route skips authorization, which would leak a case once a lawyer has access | Routes are registered in one table that wraps `authorize()`. A test runs every route under a deny-all policy and expects 403 with nothing written |
| A CLI or agent writes while the UI writes | One SQLite connection per request. A test makes parallel fact adds over HTTP and the CLI, and all rows land |

## 5. Cut line

Out of scope for v1:

- an AI assistant, voice, practice or simulation;
- an installer, double-click launcher or desktop wrapper (phase 2);
- the private-server deployment itself: accounts and sign-in, TLS, per-case grants beyond allow-all, hosting, moving or syncing case folders, encryption at rest, backups and read-access logs. v1 builds only the seams in §2;
- per-client separation on a shared computer, and LAN access;
- adding or changing deadlines, creating subcases, correspondence sign-off and mailing, capture, rules packs, authority check and import, consent, search and outreach. These stay CLI-only;
- brief check on `.docx` or `.pdf` drafts. The page says so plainly. Many pro se drafts are Word files, so this is the first follow-up; `feat/docx-structural-extraction` is not on this branch;
- editing or deleting facts or documents (the kit has neither operation);
- translations.

## 6. Tasks

Base: `feat/matter-capture` at `3304ebd`, in a worktree at `~/dev/.worktrees/matter-kit/frontend` (branch `feat/frontend`). The internal disk was RED (32.4 GiB free on 2026-10-31), where the storage policy allows no new worktree; ty approved this one at 20:47.

1. **Shared modules.** Add `matterkit/facts.py` and `matterkit/cases.py`, and `packet.status_data()`; make `matter.py` and `matter-mcp.py` call them. Tests: the existing suite unchanged; new `tests/test_facts_shared.py` (invalid labels refused on every path; output identical to before) and `tests/test_cases.py` (discovery finds cases, nests subcases, skips non-matter folders, refuses symlink escape).
2. **Server.** `matterkit/webui.py` provides:
   - a WSGI app with a route table that wraps `authorize()` and sets identity from the session;
   - a `wsgiref` threading server bound to loopback on a random port, with the per-launch token;
   - `Host` and `Origin` checks from configuration, with a loopback default;
   - CSP, and static files from a fixed allowlist.

   Command: `matter.py ui <workspace> [--operator NAME] [--no-open]`. Test: `tests/test_webui_security.py`, including the deny-all and author-spoofing rows.
3. **Read API.** Cases, status, documents with verify state, deadlines with the overdue flag, facts with sources, packet by layer. Test: `tests/test_webui_read.py`.
4. **Write API.** New case, upload then ingest, scan the folder, add a fact, brief check. Uploads send the raw body, one file per request; there is no multipart parsing because `cgi` was removed in Python 3.13. Test: `tests/test_webui_write.py`, covering overwrite refusal, cross-case isolation and concurrency.
5. **Pages.** `matterkit/webui_static/` (`index.html`, `app.js`, `app.css`): plain-language label help ("Documented fact: a document in this case shows it"), keyboard-usable, WCAG AA contrast. Tests: the static checks from §4, plus a walkthrough in the built-in browser on a synthetic workspace, with screenshots noted in the PR (not counted as gate evidence).
6. **Isolation.** A loopback-only harness at `tests/loopback_sitecustomize/`, and a test that drives every endpoint under it.
7. **Docs.** A README section, "Using the kit without a terminal".

Gate: `python3 -m unittest discover -s tests` (what CI runs). Baseline at `3304ebd`, 2026-10-31: `Ran 811 tests` · `OK`.

## 7. Build notes and deviations (2026-10-31)

All seven tasks are built in the worktree, uncommitted. The gate gives `Ran 964 tests` · `OK` on Python 3.14.7 and on 3.10.20: the 811 baseline, 80 from Task 1 and 73 for the UI. No reviewer seat has seen the code.

Departures from §2–§6:

1. **`--port N`** was added to `matter.py ui` (default 0, a random port), so a helper can bookmark one address. The bind stays `127.0.0.1`.
2. **Fact sources are cited by document id**, not `rel_path@locator`. A file name may contain `@`, which made that form ambiguous. The page picks from registered documents and sends `{"document_id", "locator"}`; an unknown id gets 400.
3. **`server_bind` is overridden.** Stdlib `HTTPServer.server_bind` calls `socket.getfqdn`, a reverse DNS lookup, on every launch. The loopback harness caught it; getfqdn swallows the error, so the harness now also logs every denial to stderr, and the isolation test reads that. Removing the override makes the test fail.
4. **Task 1 changed behavior beyond §2's flag**, all through `facts.py`:
   - empty fact text is refused on every path;
   - the CLI's invalid-label message crashed with a `TypeError` before it printed, and now prints;
   - labels are trimmed of whitespace, and MCP now normalizes their spelling the way the CLI did;
   - `fact list` breaks ties on `created_at` by row order, so the order is stable.
5. **Word and PDF drafts are listed but refused.** The drafts list shows them, marked unsupported, and brief check answers 400 with "Save As > Plain Text" steps rather than hiding them.
6. **A workspace may itself be a case.** Pointing `matter ui` at one case folder shows that case (path `.`) and its subcases, which serves the litigant with a single case.
7. **Uploading bytes the case already holds gets 409**, naming the existing file, and the new copy is removed so the tree is unchanged. §4 listed only the existing-name case.
8. **Verify and brief check are POST but authorized as `read`.** They write nothing; POST keeps them out of browser prefetch and caches. Scan is authorized as `write` because it registers files.
9. **The page renders the packet and brief report** with a small reader for the subset of Markdown those files use: headings, lists, quotes, tables, bold, code and italics. It builds DOM nodes with `textContent` only, so the §4 static check still holds.
10. **The browser walkthrough** (light, dark and 375 px wide) ran on a synthetic workspace in the scratchpad, with no console errors. Screenshots were not kept, so it is not evidence for the PR.

## 8. Revision 2 (2026-10-31): Astra's code review

The founder assigned the build (`3304ebd..ae6ff62`) to Astra (`gpt-6-astra`, read-only). The verdict was **REQUEST CHANGES**, recorded in [the review](../reviews/2026-10-31-local-frontend-code/ASTRA-REVIEW.md). Before amending, each of the nine findings was reproduced or confirmed against `ae6ff62` with synthetic probes, and the founder approved fixing all nine, including F7's seams. The new tests were written first: 34 of the 40 new Python tests failed on the unfixed code (18 failures, 16 errors), and all 14 page tests failed on the old `app.js`.

| Finding | Amendment here |
| --- | --- |
| **P1** F1: an upload deletes a file a concurrent scan just registered | §2 upload contract, §4 upload row: the body streams into `.matter/incoming/upload-<hex>.part`, which scans never enter, and is hashed as it is written. One store write transaction (`BEGIN IMMEDIATE`) then checks for a duplicate and for the name, hard-links the staged file to its final name, checks that the name holds the staged inode, inserts the row and commits. On any failure the final name is removed only if it still holds that inode, and the staging file is always removed. |
| **P1** F2: linked stores escape the workspace | §4 path row: a folder is a case only if `.matter` is a real folder and `matter.db` a real file, not links. Shared `ingest` also skips a file whose link leads outside its matter or into a nested one, so `matter scan` changes too. |
| **P2** F3: failures after the body leave stray files, and retries become conflicts | Same lifecycle as F1: nothing has a final name until its row commits. The same bytes sent again to the same name succeed with `already_registered`; §7.7's 409 still applies to the same bytes under another name. A store another program holds for more than 5 s answers 503. Shared `ingest` counts a unique-constraint race on insert as already registered instead of failing. |
| **P2** F4: path checks race folder and name swaps | New `cases.pinned()`: each request walks from the workspace's real path with folder handles opened without following links, and compares the device and inode of the case, `.matter`, `matter.db` and each opened subfolder before and after it opens the store. A change answers 409. Brief check reads the draft through the pin and refuses a report whose `draft_sha256` is not the picked file's. A failed new case removes its folder only if it is still the folder it made. |
| **P2** F5: the id picker skips the parent-source recheck | `facts.add` takes typed sources `{"document_id", "locator"}`. `subcase.resolve_parent_sources` now also finds a local `parent:` row by id or path and re-hashes the parent's file, so the CLI and MCP get the same check. |
| **P2** F6: a late response redraws another case's form; packet layer mismatch | Each page render has a generation number. A late response shows a toast naming its case and leaves the current view alone. The packet view keeps Copy and Save tied to the layer on screen, disabled while it loads or after it fails. |
| **P2** F7: the sharing seams don't work as claimed | §2 seams: `App(origins=…)` makes the allowed origins a setting, so an HTTPS origin behind a proxy works. `App(identify=…)` names the person on each request from the server side, defaulting to the launch operator; no identity answers 401. The case list asks the policy about each case; a visible case under a hidden parent shows no parent and only its folder name. Status drops `matter_dir`, `parent_line` and `children_lines`. The module docstring and README now say sign-in, TLS and real per-case grants are not built. |
| **P3** F8: escaped pipes shift report columns | The report reader splits rows on unescaped `|` only, restores `\|`, turns `<br>` into a line-break node without parsing HTML, and fits each row to the header's width. |
| **P3** F9: field borders under 3:1 | A `--control-edge` token (light `#78839a`, dark `#64718a`) outlines inputs, selects, textareas, the drop zone and the layer buttons. A static test checks it reaches 3:1 against paper and surface in both themes. |

Limits that remain: SQLite still opens a store by path, so the pin can only check before and after that open, not make it atomic. Where the platform has no `dir_fd` (Windows), the pin walks paths with no-follow checks instead. A file hard-linked into a case from elsewhere is not detected. `deadlines.py` still calls `resolve_parent_sources` without a connection, so a deadline citing a local `parent:` row by id is not re-checked.

Not changed, pending the founder's word, from Astra's documentation points: the README calls the launch key one-time, but it works for the whole launch; it says the operator is recorded as author of everything added, but only facts carry an author; §7.4 omits that MCP `fact_list` now breaks label ties by time, then row; the single-case workspace still shows a New case button that can only be refused; `safe_filename` accepts names some platforms reject (`CON`, `colon:name.txt`, a trailing dot). Also found while fixing: a Facts tab reopened while its own save is pending can show a stale list until the next visit; the documents refresh after an upload doesn't report its own failure; the Copy toast doesn't name the case; and CI has no Node step, so the page tests skip where Node is missing.

The gate gives `Ran 1011 tests` · `OK` on Python 3.14.7 and on 3.10.20: 964 before, 42 for the fixes and 5 for the page checks (one runs the 14 page tests under Node, which skip where Node is missing). A browser pass on a synthetic workspace switched packet layers, added a fact, and checked the field edges in light and dark, with no console errors. Screenshots were not kept.

Revision 2 has not been re-reviewed.

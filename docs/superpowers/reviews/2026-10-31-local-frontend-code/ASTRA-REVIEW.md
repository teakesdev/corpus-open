Verdict: REQUEST CHANGES

# Astra code review — local frontend

Review target: `3304ebd..ae6ff62`, not the handoff commit.
Worktree: `~/dev/.worktrees/matter-kit/frontend`.
All source locations below refer to `ae6ff62`. Design references mean `docs/superpowers/specs/2026-10-31-local-frontend-design.md` at that commit.

## Scope and evidence

I performed this review myself. No subagents or delegated tasks were used.

The runtime identifies the serving model as `gpt-6-astra`, provider `openai-codex`. This is the runtime-reported identifier, not an independently inferred underlying model name.

I verified the live branch was `feat/frontend`, HEAD was `9f4c61e`, and the only committed difference above `ae6ff62` was HANDOFF.md. The implementation, tests, README and design working files matched the target commit. The pre-existing untracked `astra.log` was not read or edited. No implementation files were changed, no commits were made, and no git write commands were run. This report is my only authored file.

I did not open the excluded directories, the primary checkout, or another matter-kit worktree. I did not start a browser or standalone server or contact any existing port. The permitted test suite started and stopped its own loopback servers. My additional application probes invoked WSGI directly, without sockets, using synthetic `tempfile.TemporaryDirectory` workspaces under the Python default `/var/folders/.../T` location. JavaScript probes ran the shipped code in Node's VM with an in-memory minimal DOM and controlled promises; these are execution probes, not a real-browser accessibility or visual audit. All probe fixtures were cleaned up. Probe programs were supplied in command arguments, not saved as additional files.

Execution evidence:

- `python3 -m unittest discover -s tests`: Python 3.11.15, `Ran 964 tests in 84.154s`, `OK`. The run emitted ResourceWarnings, principally for unclosed files, and one closed-socket warning; it had no test failures.
- `git diff --check 3304ebd..ae6ff62`: passed.
- Direct WSGI probes reproduced the upload, containment, parent-source and configuration findings below.
- Node v26.8.1 probes reproduced the delayed-response and Markdown-table findings below.
- A byte snapshot around an actual brief-check request was unchanged. The probe disabled optional pdftotext discovery; the normal suite also exercised the real brief-check route.
- The author's Python 3.10/3.14 results and browser walkthrough are handoff claims, not runs I independently repeated.

The basic browser security design is sound: I found no unauthenticated cross-site API bypass or case-text script-execution path. The blockers are principally filesystem integrity, incorrect reuse of parent-source references, asynchronous UI state, and incomplete promised sharing seams. A green suite does not exercise these failure modes.

## Findings, ranked

### F1 — P1: A concurrent scan makes upload delete the file the scan just registered

Locations: `matterkit/webui.py:305-328`, `matterkit/webui.py:329-334`, `matterkit/webui.py:276-282`.
Design: §2 upload contract; §4 upload integrity and concurrent-writer rows; §7.7 duplicate handling.

The upload is created under its final, publicly scannable taxonomy name before registration. After writing it, the upload handler interprets any matching stored digest as an older duplicate and unlinks its target. It does not distinguish a different existing document from the same file just registered by a concurrent scan.

Executed reproduction, with real synthetic stores and real ingestion:

1. PUT `Evidence/uploaded.txt` with new bytes.
2. At the upload's first post-write hash call, run POST `/scan` to completion, then resume upload. A mock supplied only this deterministic scheduling point; scan and hashing performed their real work.
3. Scan registers `Evidence/uploaded.txt`.
4. Upload returns `409 Conflict`: `This file is already in the case as Evidence/uploaded.txt.`
5. The file is gone, the documents row remains, and POST `/verify` returns `ok=0, changed=0, missing=1`, with `MISSING Evidence/uploaded.txt`.

This can occur with two browser tabs or a CLI ingest during an upload; it needs no hostile client. Scanning earlier in the body transfer can also fingerprint incomplete bytes.

Required change: keep incomplete uploads out of ingestion walks, and coordinate publication/registration with the shared ingest path, including CLI/MCP writers. Treat an already-registered identical target as an idempotent success, not a duplicate to delete. Never remove a target that now backs a documents row. A lock confined to UI threads is insufficient. Add deterministic tests for a scan during streaming, after the body closes, and between duplicate lookup and registration, preserving the existing schema and verify count axes.

### F2 — P1: Symlinked stores bypass discovery's workspace boundary

Locations: `matterkit/cases.py:38-39`, `matterkit/cases.py:90-97`, `matterkit/webui.py:219-223`, `matterkit/webui.py:374-382`; related scan exposure at `matterkit/webui.py:276-279`.
Design: §2 case separation; §4 path escape and wrong-client-write rows. Challenges C4.

Discovery validates the case-directory path but `is_case()` follows `.matter/matter.db` without validating either the `.matter` directory or the database file. All subsequent database opens follow those links too.

Executed reproduction:

- Create a real folder `workspace/Alias` with `.matter` symlinked to a second synthetic case outside `workspace`.
- `cases.discover(workspace)` lists Alias as a legitimate case.
- POST a fact to Alias's discovered id.
- Response is `200 OK`; the assertion is written to the outside case's database, with `human:Synthetic Reviewer` as its author.

The same construction can point at another client's store within the workspace. The caption and addressed case id then disagree with the database receiving the write. A symlink at `matter.db` itself is another form of the missing check.

The new scan route also inherits a weaker boundary from unchanged ingest code: `ingest.ingest_paths` checks lexical paths, not their resolved targets. A synthetic `Evidence/linked.txt` pointing outside the workspace was registered by `/scan` with `added=1, excluded=0`, and `/verify` reported it as `ok=1`. `_drafts` explicitly excludes final-file symlinks, but scan does not. This is an inherited helper limitation newly exposed through the UI, not a change to ingest.py in this commit.

Required change: validate the actual store and document targets used by the web boundary, not just the outer directory. Reject linked metadata/store paths and escaping or cross-case document aliases before opening them; audit manifest and other metadata reads as well. Keep the deliberately supported launch-time workspace alias distinct from a case/store symlink. Add tests for `.matter` symlinks, database-file symlinks, aliases into a second case, and external file symlinks used by scan/verify. Preserve explicit, validated `parent:` reference semantics rather than treating all cross-case reads alike. Dynamic replacement also needs F4's protection.

### F3 — P2: Post-body failures leave unregistered uploads and turn retries into conflicts

Locations: `matterkit/webui.py:319-334`.
Design: §4 upload integrity; §6 task 4. Challenges C5's cleanup guarantee.

Cleanup covers exceptions while reading/writing the request body, but not `sha256_file`, `store.connect`, duplicate-query failure, or an exception from `ingest_paths`. Closing the SQLite connection does not remove the filesystem object.

Executed reproductions:

- Inject `sqlite3.OperationalError` at `ingest_paths` after a complete body write. The response is `500 Internal Server Error`, the target file remains, and documents has no rows. Retrying the same upload returns `409 Conflict` because the unregistered file still occupies its name.
- Run two uploads with different names and identical bytes. A barrier immediately after both real ingest duplicate SELECTs made both observe no row. One request returned 200; the other hit the real UNIQUE constraint and returned 500. Both files remained, but only one documents row existed.

Pending database inserts normally roll back when the connection closes; that does not restore the promised disk/store consistency. Conversely, F1 shows that unconditional deletion after a duplicate/skip is unsafe.

Required change: give upload one explicit lifecycle covering file creation through database commit and response preparation. Handle competing duplicate registration as a conflict/idempotent outcome rather than an internal error. On failure, reconcile registration and safely remove only the request-owned unregistered file; do not delete a file another writer has registered or replaced. Add failure injection at hashing, database open, insertion and commit, plus concurrent identical-byte tests. Verify both the complete file tree and the store, not just the HTTP status.

### F4 — P2: Path revalidation and cleanup are vulnerable to directory/name replacement

Locations: `matterkit/webui.py:183-184`, `matterkit/webui.py:296-306`, `matterkit/webui.py:319-333`, `matterkit/webui.py:407-413`; analogous creation cleanup at `matterkit/cases.py:124-130`.
Design: §4 path confinement, non-overwrite and wrong-client invariants.

Discovery returns ordinary path strings. Upload subsequently computes a new `real_root` from that string and compares the folder against that newly resolved root, not against the workspace/case that was validated. `O_NOFOLLOW` protects only the last file component; it does not pin the parent directories. Cleanup likewise unlinks by name without establishing that the name still denotes the object this request created.

Executed reproductions:

- Immediately after real `cases.resolve` returns Alpha, rename Alpha and replace its old path with a symlink to a different synthetic case outside the workspace. The upload returned 200, created `Evidence/escaped.txt` outside the workspace, and registered it in the outside database.
- During the first body read, rename the request-created target, put an unrelated replacement file at its original name, and raise a synthetic disconnect. The error cleanup deleted the replacement. Output was `replacement_exists=false`, `original_request_exists=true`.

These require concurrent filesystem mutation, not merely a malicious website. They are nevertheless real violations of the handoff's expressly requested rename/replacement and ownership checks, including benign folder-management races. `_drafts` membership followed by a later path-based open has the same check/use shape.

Required change: bind operations to a validated case identity and non-following directory/file handles where supported, fail closed on case replacement, and avoid pathname-only destructive cleanup. Pin the selected brief input rather than validating a list and then reopening an untrusted path. Review new-case rollback's recursive removal for the same ownership assumption. Tests should replace the case directory, taxonomy directory, selected draft, and cleanup target at controlled interleavings. Merely adding another `realpath()` check still leaves a check/use race.

### F5 — P2: The document-id picker bypasses parent-source revalidation

Locations: `matterkit/webui.py:367-382`, `matterkit/facts.py:36-42`; dependency behavior at `matterkit/subcase.py:167-185` and `matterkit/subcase.py:217-228`.
Design: §2 shared fact rules; §4 UI/CLI/MCP consistency; §7.2 source-id deviation. Challenges C6.

A subcase can already have a documents row whose rel_path is `parent:Evidence/order.txt`. The UI lists that row and submits its id. The server forwards `id@locator` to `facts.add`, but `resolve_parent_sources` only recognizes strings beginning `parent:`. `attach_sources` subsequently attaches the local row by id without rehashing the parent.

Executed reproduction:

1. Register a parent order and attach it to a subcase through a normal `parent:` source.
2. Change the parent file's bytes.
3. A second `facts.add(..., sources=["parent:Evidence/order.txt"])` raises `SubcaseError(kind="CHANGED")`.
4. POST the same source via its subcase document id through the UI. Response is 200, `sources_attached=1`, `unsupported=false`, with status `DOCUMENTED FACT`.
5. Verify reports that very parent reference as CHANGED.

The underlying helper already accepted id references before this commit. The problem here is making that unchecked path the normal UI operation, thereby failing to preserve the stronger existing parent-source rule.

Required change: resolve document ids exactly to their rows and preserve parent revalidation before attaching a fact. Prefer a typed/id-aware shared attachment path rather than reconstructing ambiguous strings; preserve support for filenames containing `@`. Ensure id lookup cannot accidentally resolve a rel_path instead of the selected id. Test changed, missing and escaped parent sources through the UI, as well as valid parent references and `@` filenames. Refusal must add neither assertions nor source rows.

### F6 — P2: Delayed responses can destroy another case's in-progress form and mismatch packet selection

Locations: `matterkit/webui_static/app.js:437-448`, `matterkit/webui_static/app.js:225-228`, `matterkit/webui_static/app.js:519-534`.
Design: §1 helper with multiple clients; §4 wrong-client UI invariant; §6 task 5.

Most read views are reasonably isolated because they retain their own panel node; after navigation, that node is detached. The fact-submit success handler instead calls global `render()` unconditionally. It therefore clears whichever case is currently displayed, not necessarily the case whose submission completed.

Executed Node probe using the real `render`, `factForm` and submit code:

- Submit Alpha's fact and hold its response.
- Navigate/render Beta's Facts page and enter `UNSAVED BETA WORK`.
- Resolve Alpha's submission.
- The POST URL still correctly names Alpha, but Beta's textarea becomes empty and the global toast says `Fact added. It has no source yet.`

This is confirmed loss of unsaved work and misleading case-less feedback, not a claim that the server routed the original write to Beta.

The packet view has a related stale-response defect. Request 3min, select 30s, resolve 30s first, then resolve 3min. The probe leaves the `30-second view` button selected while displaying the old 3-minute response. Copy/save use that stale text; save's filename uses the newer global layer. A failed reload also leaves the previous `text` available for export.

Required change: scope completion effects to a case/view generation; do not rerender another active form when an old request finishes. Include the target case in completion feedback and prevent duplicate form submissions while pending. For packets, capture the requested layer, ignore superseded completions, and bind display/export controls to the successfully loaded layer/text. Disable or clear exports during pending/failed loads. Add deferred-promise tests covering successful and failed responses across navigation and out-of-order layer changes.

### F7 — P2: The advertised private-server seams are not sufficient as implemented

Locations: `matterkit/webui.py:3-8`, `matterkit/webui.py:131-136`, `matterkit/webui.py:166-181`, `matterkit/webui.py:228-235`, `matterkit/webui.py:246-253`, `matterkit/webui.py:382`.
Design: §2 sharing seams 2–4; §4 route authorization row. This is about the approved seams, not a request to implement the deferred deployment.

Three concrete limitations prevent the claimed unchanged migration/configuration:

- Origins are not configuration. Only `http://` plus each allowed Host is accepted. An in-process request with configured host `private.example` and Origin `https://private.example` returned 403. Host configuration cannot enable the intended TLS-proxied origin.
- Identity is fixed at App construction and used by every request's policy and attribution. There is no request-local identity resolver for a future trusted session/middleware. Mutating `app.identity` per request would race on the threaded app. A future caller can only fix this by changing the app or constructing separate instances, contrary to the unchanged-app claim.
- The route policy does not filter the case catalogue. In a probe, a policy denied Beta's direct `/facts` route with 403, but allowed workspace-level reads; GET `/cases` still returned Beta's id, title and relative path. Allowing the listing leaks all cases; denying it prevents the current UI from loading at all. Status also contains child/parent summaries produced without per-related-case policy checks. Deny-all coverage does not establish selective-grant isolation.

Required change: provide explicit allowed-origin configuration; obtain a request-local identity from a trusted server-side resolver, defaulting to the launch operator; and apply per-case read policy to discovery results and related-case summaries, with a defined policy for visible children of hidden parents. Keep the request body unable to select identity. Add two-user/selective-case tests and an HTTPS-origin configuration test. Do not solve this by stripping Origin at the proxy or assigning request identities to shared App state. Accounts, TLS provisioning and hosting remain out of scope. Correct the docstring/README's unchanged-migration implication until these seams actually work.

### F8 — P3: The Markdown table reader shifts escaped pipes into extra columns

Locations: `matterkit/webui_static/app.js:120-125`; producer at `matterkit/briefcheck.py:981-982`, `matterkit/briefcheck.py:1000-1002`.
Design: §7.9, report preview.

Brief-check deliberately escapes literal `|` as `\|`. The reader splits every pipe anyway. A real-code Node probe with a four-column header and `alpha \| beta` in the cite/sentence cell produced four header cells but five body cells, moving the problem/code under the wrong headings. The producer also emits `<br>` separators, which this text-only renderer displays literally.

Change: implement the precise safe table/line-break subset the report producer emits, without introducing HTML parsing; alternatively expose structured report rows. Add a fixture with pipes in quoted text, source names and citations. This is presentation correctness, not an XSS exploit.

### F9 — P3: Form boundaries do not meet the stated non-text contrast target

Locations: `matterkit/webui_static/app.css:7`, `matterkit/webui_static/app.css:26`, `matterkit/webui_static/app.css:114-117`.
Design: §6 task 5, WCAG AA contrast.

Text inputs, selects and textareas use `--rule` for their only ordinary border, against `--surface`. Computing WCAG relative luminance from the shipped CSS gives border-to-surface contrast of 1.417:1 in light mode and 1.363:1 in dark mode, below the 3:1 non-text boundary target. In white/surface form panels there is no contrasting fill to identify the text field's extent instead. Good text contrast and a high-contrast focus outline do not establish the unfocused control boundary.

Change: give essential control boundaries a separate adequate-contrast token in both themes; decorative rules need not all change. Add a contrast check and a keyboard/browser accessibility pass. I did not perform a real-browser AA certification. Representative text combinations tested well: muted text, actions, alarm text and unsupported badges all exceeded normal-text 4.5:1 in both themes.

## Answers to handoff points 1–10

### 1. Other sites, local processes, token and response security — C1

C1 is a good v1 browser boundary. Every API path is behind an exact configured Host match and constant-time token comparison before handler dispatch. A simple form POST, no-cors request, image/script fetch or preflight cannot furnish the required custom header using these pages' permissions. Foreign Origin and `Sec-Fetch-Site: cross-site` are separately rejected. An absent Origin is deliberately permitted for token-bearing non-browser clients; this is not a rebinding bypass because an attacker still needs both an accepted Host and the token. A rebinding origin cannot simply replace its browser-controlled Host with the loopback literal.

My direct matrix returned 403 for missing token, foreign Origin, cross-site fetch, `[::1]:port`, and portless `localhost`. The suite exercises accepted `localhost:port`; the server itself binds IPv4 127.0.0.1, not IPv6. Rejecting IPv6/portless hosts is fail-closed rather than an accidental alternate route.

The token is generated per launch, delivered in a fragment, copied to sessionStorage, and replaced in the visible URL. A fragment is not sent in HTTP requests or Referer; both response policy and HTML request no referrers. The logger omits query strings and headers. API errors do not echo the supplied token. No application path persists it beyond sessionStorage and the in-memory variable; the intended launch URL is printed to the operator's terminal. `replaceState` is not a promise to erase OS/browser crash recovery, clipboard or browser-extension access. A local process with the token can call the API and omit/forge Origin; same-user filesystem/terminal compromise is not prevented by this model. Per-client OS-user separation is explicitly deferred.

The static allowlist serves only `/`, `/app.js`, `/app.css`; it does not turn case filenames into served paths. API responses are no-store. All application-generated responses go through the security-header wrapper, including the exercised 404/405, invalid requests and internal-error path. HTTP-parser errors generated before WSGI are outside that wrapper; I am not claiming every malformed transport response has those headers. No CORS headers are added. The default app, not its future TLS configuration, passes this part of the review.

### 2. Path escape, ids and case identity — C4

C4 handles ordinary request-level traversal well: ids must match the route's lowercase hexadecimal shape, only discovered ids resolve, outer case/subcase symlinks are skipped, `Subcases` symlinks are refused, and the depth cap is tested. A workspace that is itself a case correctly receives rel `.` and only its subcases are enumerated. Cases below the maximum depth are deliberately not exposed.

It does not establish the actual store/document boundary: F2 and F4 demonstrate that distinction. `_drafts` normally excludes `.matter`, final-file symlinks and nested matters; a submitted absolute, traversal or other-case name is not in the generated list. Its later path-based reopen is still racy. Case paths reused for different physical folders also reuse their ids, so an old browser caption is not proof of current case identity.

Ids are truncated path hashes, not authorization secrets. There is no collision detection: `resolve` chooses the first matching entry. An accidental collision is not a realistic blocker for this workspace size; rejecting ambiguous ids would nevertheless be a sensible fail-closed guard. I did not manufacture a real SHA-256 collision.

`safe_filename` rejects separators, leading dots, controls and overlong character counts, and trims surrounding whitespace. It does not enforce encoded filesystem limits or a cross-platform portable alphabet. Actual validation accepted `NUL.txt`, `CON`, `trailing.`, `colon:name.txt`, and 200 repetitions of `é` (400 UTF-8 bytes). These are not evidence of a successful Windows exploit on this Mac; they show the portability/error-handling gap. `cases.create` likewise allows Windows device basenames, though it rejects trailing space/dot and uses exclusive mkdir. Add portable-name validation or explicitly document the platform constraints and map filesystem errors to useful 4xx responses. Do not silently rename user identifiers beyond the documented policy.

### 3. Upload integrity — C5

C5 is only partially met. Ordinary existing-name and final-symlink refusal work through O_EXCL, and the fixed-folder/name checks prevent straightforward traversal. My no-socket probes confirmed missing/negative length → 400, oversize → 413, and early EOF → 400, each leaving the existing file snapshot unchanged and retaining security headers. Genuine disconnect exceptions enter the streaming cleanup path, but cleanup ownership is unsafe under replacement (F4).

The handler consumes the declared Content-Length, not an independently inferred total transport body. Extra bytes beyond that declaration are not part of the declared WSGI entity. A client that advertises more bytes but neither sends nor closes can leave a worker blocked: there is no upload read timeout. The legitimate browser normally supplies consistent lengths; this is a robustness limit, not an unauthenticated browser bypass.

Concurrent same-name uploads are protected at creation. Concurrent same-byte uploads are not atomically deduplicated (F3). Upload versus scan can delete a registered file (F1). Hash/database/ingest exceptions can leave unregistered files (F3). Name replacement can make cleanup remove a file it did not create (F4). Thus both adverse questions in handoff point 3 have confirmed “yes” answers despite the happy-path tests.

### 4. Identity and authorization — C2 and C3

C2's effective no-spoofing invariant is satisfied for current handlers: stored fact authors come from `app.identity`, never from body fields. Non-object JSON is rejected. Exact top-level AUTHOR_KEYS are refused by `Request.json`. Nested author keys, differently cased keys and Unicode lookalikes are not recursively rejected, but no handler reads them as identity. A probe with `Author: forged` was accepted and still stored `human:Reviewer`. Scan/verify ignore their bodies entirely rather than parsing and refusing author fields. Distinguish “cannot select the author” from the broader, false claim that every spelling/nesting of an author key is rejected on every POST.

`check_operator` delegates semicolon/newline rejection to attribution and enforces a trimmed nonempty bounded name. It is launch configuration, not caller authentication. Existing documents and new cases do not gain an author audit record merely because the README says the name is recorded on everything added; the schema has no new upload/case attribution field. The concrete attributable UI write here is the assertion.

C3 correctly centralizes dispatch-time policy calls, before case resolution and handlers. Verify opens readonly and hashes; brief check uses readonly store access and returns an in-memory report. Neither route writes report files or events in this path, so authorizing these POSTs as read is correct. Scan really writes. F7 explains why an all-or-nothing route-policy test is not sufficient evidence for later selective per-case access or multiple signed-in operators.

### 5. Shared-module refactor and output parity — C9

The refactor keeps the principal CLI/MCP fact output formatting and status-report format. `cases.init` preserves folder setup and output while explicitly closing the connection. Facts still store nonblank text as supplied rather than trimming it. `facts.add` puts assertion insertion and source attachment in one transaction; failure of a parent reference through the explicit `parent:` form still refuses/rolls back. The expanded tests exercise those CLI/MCP forms and passed.

The documented C9 changes are reasonable: normalize/validate labels, reject empty text, repair the invalid-label CLI error, and provide deterministic tie ordering. They are deliberate behavior changes, not byte-identical behavior for every formerly accepted input.

There is one further output-order change not accurately described as only a timestamp tie-break: old MCP fact_list ordered by status alone; the shared function now orders by status, created_at, rowid. In a synthetic store with two UNKNOWN rows inserted in reverse timestamp order, the old query returned `[a_new, a_old]`, while the new function returned `[a_old, a_new]`. This is a defensible improvement, but document and test it rather than claiming universal old-MCP parity. The existing characterization fixtures generally use one fact per label and miss this case.

`status_report`'s rendering is equivalent for the exercised data and by inspection of the moved fields; a direct comparison against the `3304ebd` function on the same synthetic store was byte-identical. It now performs an overdue calculation even when rendering the old text; malformed dates are handled by the existing helper. No schema, versions.py or verify count-axis change appears in the target diff. F5 concerns the UI's choice of source-reference form, not failure of explicit parent references in the refactor.

### 6. Concurrency

One SQLite connection per request is correct and the passing suite exercises parallel UI/CLI fact inserts. The App's normal v1 request path does not mutate identity, token, routes or host configuration, so those do not themselves race. Shared `facts.add` transactions support concurrent independent connections; the added integration concurrency test does not directly include MCP.

There is no special SQLITE_BUSY translation/retry policy: sqlite's ordinary wait can end in a generic 500. Fact transactions roll back, but upload files do not necessarily roll back (F3). Long-running reads are not a single explicit case snapshot across all statements, and verify can race changes to rows/files; a green verification is a point-in-time observation, not a lock excluding writers. The major new integrity issue is cross-request/cross-process upload registration (F1/F3), not the successful simple fact-write workload. Do not fix future per-user identity by mutating shared App state (F7).

### 7. Pages — C7

C7 successfully avoids active-content injection. Text goes through textContent/text nodes, Markdown emits fixed element names, and no source-derived Markdown links become navigable anchors. Hash ids/tabs are constrained before use. The CSP excludes inline scripts, objects, framing and base changes; no external page assets are loaded. The generic `el` helper does support attribute/event props, but current call sites do not turn arbitrary case text into attribute names, executable handlers or script URLs. Static sink checks are helpful evidence, not a complete XSS proof by themselves.

Unsupported facts in the Facts page consistently get “No source yet” from the API's unsupported flag. The literal `[UNSUPPORTED]` string is not shown there; packet output keeps its existing `[UNSUPPORTED — no source attached]` warning, rendered as bold text. The requirement's warning intent is met for source-less rows, even though the exact badge wording differs. This is not a live hash-verification badge: F5 shows why an attached source can still be unusable. Markdown formatting is permitted, so hostile Markdown can affect presentation without executing code; the table subset also has F8's accuracy defect.

Ordinary delayed read results update detached panels, not another case's visible panel. The exceptions and user impact are F6: a global post-write rerender and unguarded packet reloads. Fact/upload completion messages generally omit the case name, contrary to the strict §4 “every confirmation” wording. Case-specific completion text should accompany the generation guards.

There are native controls, fieldset/legend grouping, labels, a skip link and visible focus styling, all positive. Focus retention when rebuilding segmented controls and navigation should be exercised in a browser. I did not substantiate the prior walkthrough, mobile layout or full keyboard/AA claim. The concrete CSS boundary-contrast gap is F9. No new token persistence beyond sessionStorage was found.

### 8. Isolation harness — C8

C8 is justified: overriding server_bind avoids the stdlib getfqdn call rather than merely swallowing its denial. The suite's harness controls and full endpoint run passed locally. The controls assert the harness-specific denial string and check the patched class against a plain child, so they are not simply succeeding because the machine happens to lack network access. The handoff records the removed-override red step; I did not edit the source to repeat it.

The wrappers cover ordinary Python bind/connect/connect_ex/sendto, forward lookup and reverse lookup paths. Both common sendto call signatures put the destination last, which `_check(args[-1])` handles. `create_connection` uses the patched lookup/socket interfaces. On this Python, inspected `socket.fromfd` calls the module-global `socket` constructor, so it uses the patched class for subsequent connect/sendto operations. It does not retroactively validate an already-connected inherited descriptor or intercept every send operation.

`getaddrinfo(None, ...)` is delegated, not rejected. It normally requests local wildcard/loopback results without a remote hostname lookup; an attempted wildcard bind is then denied by the socket wrapper. AF_UNIX addresses are intentionally permitted. Their ability to talk to local proxies/services is another reason this is not OS containment.

The harness does not patch webbrowser or subprocesses/native socket use. The isolation test passes `--no-open`, so it provides no evidence about an actual browser launch. Brief check can invoke optional pdftotext, whose native behavior is outside this Python monkeypatch. This is accurately a regression detector for the exercised stdlib paths, not a complete off-machine-egress sandbox. Additional safe stub-based controls for sendto/fromfd/None and an explicit webbrowser-launch check would strengthen the claimed coverage without making real outbound calls.

### 9. Tests and discriminatory power

The suite meaningfully pins ordinary C1 token/Host/Origin behavior, exact C2 author keys, C3 deny-all dispatch, C4 ordinary discovery/traversal, C5 happy-path overwrite and size handling, C6 known local sources, C7 banned sinks, C8 basic isolation, and much of C9. Wrong-token/foreign-origin/deny-all tests expect a specific refusal and unchanged trees; removing the effective guards would not merely leave a still-green success path. The read-only fixtures and output characterizations are useful.

It does not establish every claim in the handoff. Missing cases that exposed this review's findings:

- Upload versus scan at deterministic interleavings; concurrent identical-byte uploads; post-body storage failures; cleanup ownership.
- Linked `.matter`/database files, file-level scan escapes, and case/folder/draft replacement.
- UI parent-document ids whose parent bytes changed or disappeared.
- Real selective-case policies, request-local identities and configurable HTTPS origins.
- Executed frontend lifecycle tests with delayed/reordered promises, export failures and form preservation.
- Escaped Markdown table content and actual control contrast.
- Old MCP multiple-same-label ordering and longer lock contention including MCP.

Some tests check composite defenses rather than each individual mechanism: O_EXCL already rejects an existing final symlink, so that fixture does not independently prove O_NOFOLLOW is necessary/effective. The static no-external-URL check is not network instrumentation. The deny-all test is not a selective-authorization test. No mutation-testing results beyond the handoff's stated reverse-DNS exercise should be inferred.

### 10. Design §4 failure modes, deviations and documentation

Row-by-row disposition:

- Website CSRF/rebinding: satisfied in the default v1 app.
- Reachable from the network: binds 127.0.0.1; no exposed bind-host option.
- Case/path escape: not satisfied; F2/F4.
- Upload overwrite/location/integrity: ordinary inputs protected, lifecycle and ownership not satisfied; F1/F3/F4.
- Case text executes HTML: no active-content path found; presentation fidelity is separate (F8).
- Unsupported fact looks supported: source-less flag/display works; parent-source freshness can diverge (F5).
- UI/CLI/MCP fact consistency: shared code and normalization good; parent-id semantics and MCP ordering need attention.
- Wrong-client write/read: normal URL addressing and detached read panels are good; linked stores/replacement and delayed completion break parts of the invariant (F2/F4/F6).
- Process contacts off-machine: passing evidence for the exercised Python paths, not universal sandbox coverage.
- Page impersonates an author: current fact attribution is server-owned; future request-local identity seam is missing.
- Route skips authorization: dispatch gate exists; returned related cases/catalogue are not selectively gated (F7).
- Concurrent CLI/agent/UI writes: simple fact inserts pass; upload/scan consistency does not.

The §7 deviations are generally reasonable product choices: optional fixed port, id-based picker, no-reverse-DNS bind, explicit validation changes, unsupported Word/PDF entries, single-case workspace mode, read-authorized checkers, and safe DOM rendering. Their implementation qualifications are above. Listing a Word/PDF draft is honest, though the “Word or PDF” label is also used for other unsupported extensions. The single-case workspace still shows a New case control that can only be refused; disable or explain it. The documents page gives an aggregate explicit verify action, not per-document persistent verify state as §6 task 3 might suggest.

Documentation should also be precise about these points:

- The launch key is reusable for the lifetime of a server launch, not literally a one-use key.
- The currently named attribution is stored on facts; no new upload/case audit trail was added.
- “Same app ... without changes” and configurable Host/Origin overstate the sharing seams (F7).
- `status` for a subcase retains an absolute parent path in parent_line, and brief reports intentionally contain absolute paths. Only the case-entry projection, not all API data, is path-redacted.
- The unchanged packet renderer does not implement substantive content-length differences among 30s/3min/15min beyond the heading. This is inherited, not a frontend regression; do not advertise distinct summaries as already implemented.
- A retained screenshot/real-browser accessibility record remains absent. The handoff correctly admits that its walkthrough is not independently available evidence.

## Readiness and required disposition

Not ready to land on `feat/matter-capture` in its current form.

Fix and regression-test F1–F6 before treating the local multi-client workflow as ready. Deliver the minimal promised seams in F7 without expanding into accounts, TLS deployment, hosting or a schema change. F8/F9 are lower-priority, concrete correctness/accessibility fixes; they do not independently drive the rejection, but should be addressed before claiming complete report fidelity or WCAG AA conformance. Document the additional MCP ordering change and the narrowed claims listed above.

After changes, rerun the full gate plus the new deterministic interleaving and frontend promise-order tests. Preserve the no-schema-change, versions.py and verify-axis constraints. Another review should specifically verify cleanup/registration ownership and selective-policy behavior; simply obtaining 964 green tests again would not close these findings.

The architecture is a useful local starting point: stdlib WSGI, no mandatory dependencies or build step, a centralized route table, a small static client, and shared fact logic. It is not yet a safe unchanged private-server app. Even after the v1 seams are fixed, real authentication, TLS/reverse-proxy configuration, per-case grants, deployment hardening and the other §5 deferred responsibilities will still be required before sharing access with counsel.

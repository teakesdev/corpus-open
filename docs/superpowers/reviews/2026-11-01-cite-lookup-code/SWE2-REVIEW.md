Continuing the review. I have the diff and core files; I still need `matter.py` at e72359d for line numbers, `authority_keys`/`check` internals in briefcheck, and the stage table in citations.py.Two more dependency checks: the status constants and how `auth_index` is keyed, plus the name-extraction helpers.I have everything I need. Reviewing my notes against the code one final pass, then writing the report.

Key verifications completed:
- `scan_authorities` yields `(key, display, pin, start, end, kind, extra)`; `draft_cites` rebuilds `sent` only from digit groups + canonical reporter literals — no draft text can enter the payload
- `authority.find` keys agree with brief check (`authority_keys` on both sides); `resolved` lands in `STATUS_LOCATED` → UNCHECKED, never BLOCK
- `_RefuseRedirects` covers 301/302/303/307 via `redirect_request`, and 300/304–306/308 via the `HTTPError` 3xx branch in `post_form`
- `authorize` requires provider+endpoint+payload_type+human kind+caller+expiry+budget — a `courtlistener-free` host-only search grant cannot match the path-pinned lookup endpoint
- `transition_authority` allows `resolved` with `checked_via` only; `record` can never reach `source-checked`

**Verdict: APPROVE WITH CHANGES** — the egress invariant ("a draft's case cites, and nothing else") holds end to end; grant scoping, pinning, redirect refusal, throttle and the `resolved`-only write path are all correct. Two P2 edits are required before landing: (1) `interpret` binds a response item to a payload line by `start_index` alone, so a malformed/hostile response can mark the wrong cite FOUND; (2) `_same_case`'s 4-character prefix rule lets common surname pairs ("Marsh"/"Marshall", "Roger"/"Rogers") downgrade the NAME DIFFERS fabrication signal to FOUND.

# SWE-2 review: cite lookup vs CourtListener (`f1845e0..e72359d`)

Scope: `review.diff` (1564 lines) plus the working files at `e72359d`: `matterkit/citelookup.py`, `matterkit/adapters/courtlistener_citations.py`, `matterkit/consent.py`, `matter.py`, `matterkit/briefcheck.py`, `matterkit/authority.py`, `matterkit/citations.py`, `matterkit/search.py`, `matterkit/adapters/courtlistener.py`, `matterkit/store.py`, `tests/test_cite_lookup.py`, `README.md`, `rfcs/0001`, and the design doc. Constraints honored: read/grep/glob only; no shell, no network.

## Findings

### P2-1 — `interpret` can label the wrong cite FOUND on a malformed response

`matterkit/citelookup.py:244-248`. A result item is bound to a payload line by `start_index` alone; `item["citation"]` is only a fallback. If the server returns an item whose `start_index` equals a *different* line's start offset, that line is judged with another cite's cluster — FOUND with the wrong opinion — and `--record` then writes `resolved` / `courtlistener:id=<wrong cluster>` for a cite that was never verified.

Synthetic scenario: payload `"1 F.3d 1\n2 F.3d 2"`; response item `{start_index: 9 (start of line 2), citation: "1 F.3d 1", status: 200, clusters:[…]}`. Line 2 is reported FOUND with line 1's cluster; line 1 reports NOT LOOKED UP.

With the pinned URL + TLS this needs a compromised or buggy upstream, so it is defense-in-depth rather than a live exploit — but `interpret` is precisely the malformed-response boundary, and the fix is ~4 lines: when both an index match and an echoed `citation`/`normalized_citations` string exist and disagree, do not bind (drop the item, or fall back to whichever key agrees). No test covers index-vs-citation disagreement; add one.

### P2-2 — the 4-char prefix rule hides the NAME DIFFERS signal

`matterkit/citelookup.py:164-165`. `len(short) >= 4 and long_.startswith(short)` treats "Marsh"≈"Marshall", "Roger"≈"Rogers", "Hill"≈"Hills" as the same case. A draft citing `Roger v. Lee, 550 F.3d 1` where CourtListener holds `Rogers v. Kent` at that cite reports FOUND — exactly the made-up-cite shape NAME DIFFERS exists to catch, silently downgraded. These surname pairs are not rare.

The escape hatch exists for abbreviations ("Educ." → "Education"), so the surgical fix is to allow prefix matching only for tokens that are actually abbreviated in the source name — i.e., words ending in "." in the raw name (`[a-z0-9]{3,}\.`), at some minimum length — and require full-token equality otherwise. `Natl.`/`Assn.`/`Sys.` still won't prefix-match `National`/`Association`/`Systems` (they diverge before the 4th char, not after it), so expect some residual false NAME DIFFERS — that direction fails safe (manual check). Exact-token matching ("Twombly"→"Bell Atlantic Corp. v. Twombly") keeps working.

### P3 — minor, non-blocking

- `matterkit/citelookup.py:56` — `\d` accepts Unicode decimal digits (`"١٢٣ F.4th 456"` passes `validate_payload`). No text can smuggle through it, so the leak invariant is intact; the line just reaches CourtListener as a cite it won't parse. Harmless; consider `[0-9]` for clarity.
- `courtlistener_citations.py:107-109` — `self._last` is set and `log_egress`/`sent` run before `_post`, so a failed POST still counts as "sent" in the report and burns daily-budget. Conservative and probably intended, but `Report` lists `payloads[:sent]` as sent payloads even when the last one failed; a failed request is indistinguishable from a delivered one in the report.
- `citelookup.py:354-367` — `record` commits per cite; an exception mid-loop (disk error, corrupt legacy `check_evidence` where `lookups` isn't a list) rolls back only the current cite and propagates, killing the run before the report renders — earlier committed rows get no summary. A per-cite try/except recording an "error writing" action would keep the report accurate.
- `matter.py:518` — the extension guard covers `.doc/.docx/.pdf` only; an `.rtf`/`.odt` draft is decoded as text, yields zero cites, and exits 0 with "EVERY CASE CITE FOUND". Vacuously true, potentially misleading.
- `matter.py:910-919` — the new `--dry-run/--record/--out/--max-requests/--caller/--author/--directed-by` flags now exist on the shared `cite` parser, so `cite extract --record` is silently accepted. Cosmetic.
- `matterkit/consent.py:133-135` (pre-existing, not this diff) — `authorize` raises on the *first* budget-exhausted matching grant instead of checking later grants for the same provider; the tighter default 10/day lookup budget makes exhaustion likelier. Note for the consent module, not a blocker here.
- `matterkit/citelookup.py:150-167` — false NAME DIFFERS on common abbreviations (`Natl.`→`national`, `Assn.`→`association`, `Bd.` is <3 chars and dropped): safe direction, but expect noise on real briefs.
- `tests/test_cite_lookup.py:450` — `mock.patch.object(clc, "post_form", …)` in the dry-run test is dead weight: the adapter is never constructed on the dry-run path. The meaningful assertion is the absent egress file, which holds.

## Answers to the handoff's nine points

**1. Can anything but a bare cite leave? No.** `draft_cites` (82) rebuilds `sent` exclusively from `CASE_RE`/`WL_RE` digit groups plus canonical-reporter literals — `f"{vol} {rep} {page}"` / `f"{year} WL {num}"` — so draft text, names, pins, paths and parentheticals are structurally incapable of entering the payload. `validate_payload` (126) fullmatches every line against `_PAYLOAD_LINE` over **all** payloads before the first request is sent (`lookup` 95-96). `fields["text"]` is the only POST field. Newlines can't exist in `sent`; whitespace, trailing spaces and unknown reporters all fail. Unicode `\d` is the only gap — cosmetic (P3). C1 is sound.

**2. Grant, token, pinning: solid.** `__init__` (79) authorizes `courtlistener-citation-lookup` / host+path endpoint / `citation_strings` before reading the token; a `courtlistener-free` search grant (host-only endpoint, `query_text`) cannot match; agent grants are inert; expired and wrong-caller grants refused — all tested. Token: env → `self._token` → `Authorization` header only; never in the store, egress log (hash-only), report, or exception text (asserted by tests). Redirect refusal is complete for POST: 301/302/303/307 raise in `_RefuseRedirects`, 300/304–306/308 raise via the `HTTPError` 3xx branch in `post_form`.

**3. Throttle and budget: conformant.** ≤60 cites/request (`validate_payload` line cap + chunker), 61 s between send-starts within a run, `--max-requests` slices before send, per-request grant re-check with daily budget counted from the egress log, no retry on 429 or any error. Cross-run spacing isn't tracked — a second run's first request can land inside a prior run's rolling minute; the API answers 429 and the run stops safely. Not a terms violation.

**4. Reading results.** Statuses map as designed (404→NOT FOUND, 400→UNKNOWN REPORTER, 429→NOT LOOKED UP, 300→multi-cluster name-pick or AMBIGUOUS, other/absent clusters→NOT_LOOKED_UP). `_cluster`'s type checks and the CourtListener-only URL filter are right. The one real hole is P2-1 (index binding without cross-check). C2's mechanics are as described — see P2-2 for its soundness. C5 is correct: unparseable clusters degrade safely.

**5. Store writes.** `record` touches only FOUND; only `unverified` rows move or a new row is inserted; `resolved` via `courtlistener:id=N` with a `lookups[]` entry — `"checks": []` is always preserved, so no lookup can be read as a source check (verified against `authority._evidence` and `briefcheck._judge_authority`, where `resolved`→UNCHECKED, not BLOCK). NAME DIFFERS/AMBIGUOUS write nothing. Multi-row cites are skipped. C3 deviation honored and consistent with the evidence model.

**6. Wording: careful.** `LIMITS`/`NOT_FOUND_TEXT` say "lead, not proof", never claim good law or FLP verification; a test asserts the absence of "fabricat|fake|hallucinat". README matches.

**7. CLI and consent.** `cite lookup` dry-run needs neither grant nor `.matter`; `.docx/.pdf/.doc` refused; exit codes per design; `--record` gated behind `.matter` + grant + token. `cite extract|list` unchanged (only dispatch order). `consent --scope` defaults to the previous `search` behavior; the activation prompt now names provider/payload/endpoint for both scopes — strictly better.

**8. Tests.** 48 tests, discriminating (redirect destination untouched + token absent; budget on fake clock; 60/60/10 chunking; per-scope grant matrix; end-to-end socket denial with harness-load probe). Missing: index/citation disagreement (P2-1), `cite extract` parser regression, Unicode-digit validator line, `--max-requests` slicing at CLI level, zero-cite exit.

**9. Gaps.** Design §8's three deviations are honored as written. Nothing the code should do is unimplemented beyond the P2s/P3s above.

## Readiness

Ready for the founder's first live call and to land on `feat/matter-capture` **after P2-1 and P2-2 are applied** (~15 lines plus one test each). The egress-safety properties the live call exercises are already correct; the P2s harden result interpretation and the fabrication signal, not the send path.

## Model

SWE-2 Max.

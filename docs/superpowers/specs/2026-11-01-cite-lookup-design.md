# Checking that cited cases exist (CourtListener citation lookup): design

**Date:** 2026-11-01 · **Author:** agent:claude-opus-5-5, directed by ty · **Tier:** Large because it adds network egress through a second CourtListener endpoint and a new grant scope.

**Status:** draft, awaiting ty's approval. No reviewer seat has seen it, and nothing is built.

## 1. Problem and intent

Brief check ties a case cite to a row in the store. It cannot tell that "Smith v. Jones, 123 F.4th 456" names no real case, and fabricated citations are why the kit exists. **Intent:** an opt-in check that sends a draft's case citations, and nothing else, to CourtListener's citation-lookup API, and reports each cite as found, not found or ambiguous, with the matched opinion. Brief check stays offline.

## 2. What CourtListener says (read live 2026-11-01)

- **Endpoint:** `POST https://www.courtlistener.com/api/rest/v4/citation-lookup/` with a form field `text`. A token is required (`Authorization: Token …`), so there is no anonymous mode.
- **Limits:** the `citations` scope allows 60 citations a minute. Each request also costs one request in the `user` scope (default 5 a minute, 50 an hour, 125 a day). Windows roll. A request takes at most 250 citations and 64,000 characters. A throttled lookup gets HTTP 429 with a `wait_until` time in the body (the docs spell it `wait_util` once) and no `Retry-After`.
- **Per-cite status:** 200 found; 300 several matches; 404 a valid cite CourtListener doesn't have; 400 unknown reporter; 429 past the per-request cap. Each result lists `clusters` (id, case name, date filed, URL). Statutes, journals, *id.*, *supra* and cites without a volume or page are not looked up.
- **Terms (modified 2026-08-05):** not legal advice; no accuracy guarantee; verify against the primary source; don't present output as produced, endorsed or verified by Free Law Project; no extra accounts or credential rotation to get past limits; don't share tokens. **Privacy (2026-07-29):** search queries and usage logs are deleted at 12 weeks, and IP-to-user mappings at 14 days.

## 3. Decision

**A separate command**, not `brief check --lookup`:
`matter.py cite lookup <dir> <draft.md> [--dry-run] [--record] [--out FILE] [--caller ID] [--author …]`.
Brief check keeps its read-only store, and its MCP tool and web UI stay free of network code. `--record` writes results to authorities rows, which the next brief check reads offline.

**What leaves the machine.**
- Only full case cites the draft contains, found by brief check's own scanner (`scan_authorities`, kind `case`: reporter and WL cites). Short forms, *id.*, statutes and rules are not sent.
- Each cite is rebuilt from its parts as `vol reporter page` (or `year WL number`), deduplicated, one per line.
- Case names, pins, parentheticals, draft text, file names and paths never leave.
- A separate validator matches every payload line against a strict cite pattern just before sending. If any line fails, it refuses the whole request.
- `--dry-run` prints the exact payload and its sha256. It sends nothing and needs no grant or token.
- Each request appends one hash-only line to `.matter/egress.jsonl`, as today.

**Grant scope and endpoint pinning.**
- New provider id `courtlistener-citation-lookup` with payload type `citation_strings`, added to the consent ladder below `query_text`.
- The grant's endpoint is `www.courtlistener.com/api/rest/v4/citation-lookup/`, host and path. A search grant does not authorize lookups, and a lookup grant does not authorize search.
- The adapter POSTs only to that constant HTTPS URL and refuses redirects (R2's handler). It re-checks the grant before every request.
- The token comes from `COURTLISTENER_TOKEN` only. If it is missing, the run is refused before any egress. It never appears in the store, log, report or error text.
- `consent propose|activate <dir> --scope cite-lookup`. Activation stays a human at the TTY. Its prompt names the endpoint and payload type (today it always says "CourtListener query_text"). Default daily budget: 10 requests.

**Throttling.**
- At most 60 cites go in one request, so a typical brief needs one.
- Before each further request, the adapter waits until 61 s have passed since the last one.
- A run sends at most `--max-requests` requests (default 3, so 180 cites) and never more than the grant's daily budget allows.
- On a 429 the run stops. Remaining cites are reported NOT LOOKED UP with the time to try again. There is no automatic retry.

**The report.** Each result shows the draft's cite and CourtListener's case name, date filed and URL.

| Result | When | Exit |
|---|---|---|
| FOUND | one opinion at this cite, or several of which exactly one shares a significant word with the draft's case name | 0 |
| NAME DIFFERS | an opinion exists here but shares no significant word with the draft's case name, the usual shape of a made-up cite | 1 |
| NOT FOUND / UNKNOWN REPORTER | 404 / 400 | 1 |
| AMBIGUOUS | several opinions, candidates listed | 2 |
| NOT LOOKED UP | throttled, over the cap, or not recognized as a cite | 2 |

FOUND warns when the draft's parenthetical year differs from the date filed. NOT FOUND reads: "CourtListener has no opinion at this cite. This is a lead to check by hand, not proof the case does not exist: CourtListener lacks some reporters and many slip, unpublished, Westlaw-only and older state opinions." Every report opens with: "Found means CourtListener holds an opinion at this volume, reporter and page. It does not mean the case says what the draft says, that it is still good law, or that a pin cite is right. Results come from CourtListener (Free Law Project), which has not verified or endorsed them."

**Where results land (`--record` only).**
- **FOUND:** the authorities row for the cite (matched by `authority.find`, so keys agree with brief check) moves from `unverified` to `resolved` through `transition_authority`. `checked_via` becomes `courtlistener:id=<cluster>`, and a record goes into `check_evidence.lookups`: cluster id, URL, case name, date filed, `retrieved_at` and who ran it. If no row exists, a `resolved` row is added. Rows already `resolved` or later get only the lookup record, and their status stays the same.
- **Everything else** writes nothing.
- The lookup never writes `source-checked` or `stale-flagged`. Brief check then shows the cite as "located but no source check recorded" (UNCHECKED) instead of BLOCK.
- `check_evidence` always keeps `"checks": []`, so a lookup is never read as a source check.

**MCP: none.** The MCP server stays network-free (RFC 0001 §4; its test runs it with sockets denied). `matter.brief_check` already shows a recorded lookup in its "Checked via" column.

## 4. Options considered

1. **`cite lookup` command.** Recommended.
2. **`brief check --lookup`.** Loses: brief check opens the store read-only, its MCP and web UI callers would be one flag away from egress, and another session is editing `briefcheck.py`.
3. **One request per cite (volume/reporter/page).** Loses: each request spends one of 125 daily requests, while a batch spends one per 60 cites.

## 5. Failure modes

| What could go wrong | How a test catches it |
|---|---|
| Draft text, a case name or a path reaches the payload | a synthetic draft full of names, facts and paths yields exactly the cite lines; the validator refuses a smuggled line |
| Lookup runs with no human grant, or on a search grant | agent, search-scope and expired grants are all refused, and no request is made |
| Token missing, or leaked | refused before egress; the token string appears in no output, log or row |
| A redirect sends the payload elsewhere | a local redirect server: refused, and the destination gets no request |
| Over the citations limit | 130 cites go as 60/60/10, 61 s apart on a fake clock |
| 429 partway through a run | remaining cites NOT LOOKED UP, no retry, exit 2 |
| A lookup writes `source-checked`, or `resolved` for NAME DIFFERS | recorded responses for every status; only FOUND rows move, and only to `resolved` |
| "Not found" reads as "fabricated" | the report wording is asserted |
| A live call in the suite | netblock harness; every response is recorded synthetic JSON |

## 6. Cut line

Out of scope: an MCP tool; statutes, rules and short forms; checking pins or quotes against opinion text; any citator or good-law signal; automatic retries; anonymous mode; the first live call (ty runs it).

## 7. Tasks

1. `matterkit/citelookup.py`, with no network imports: payload builder, validator, result reading, name and year check, `--record` writer. Proved by `tests/test_cite_lookup.py`.
2. `matterkit/adapters/courtlistener_citations.py`: POST, grant re-check, pinned URL, redirect refusal, token handling, chunks and throttle on an injectable clock, egress log. Proved by a local-server test and recorded responses.
3. `consent.py`: the `citation_strings` type and a scope-naming prompt. `matter.py`: `cite lookup` and `consent --scope`. The import-isolation test adds `citelookup.py`.
4. README: replace "R1 ships zero adapters and the kit makes no network calls" with the commands that do reach the network, and add a cite lookup section. RFC 0001 §6: one line pointing here.

Gate: `python3 -m unittest discover -s tests`

## 8. As built (2026-11-01, after ty's approval)

Departures from the approved text:
- **Rows past `unverified` are not changed at all.** The design said they would get the lookup record. They don't, because their evidence belongs to a person's source check, and adding a record to legacy free-text evidence would mean rewriting it as JSON. `--record` reports "already <status>; nothing written".
- **The end-to-end test uses a narrower socket harness.** `tests/netblock_sitecustomize` replaces `socket.socket`, which also stops `import ssl`, so urllib cannot load under it. The test writes its own harness that denies name lookups and connections, and checks first that the harness loaded.
- **Sentence openers are dropped from the local case name.** Brief check's name walk keeps a leading "Again," or "However,"; the lookup drops it. The name is local only.

## 9. Revision 2 (2026-11-01): SWE-2's code review, rounds 1 and 2

The founder assigned the build to SWE-2 (SWE-2 Max, read-only). Round 1 (`f1845e0..e72359d`) returned **APPROVE WITH CHANGES**, recorded in [the review](../reviews/2026-11-01-cite-lookup-code/SWE2-REVIEW.md). Round 2 (`e72359d..69dd62e`) checked the fix for round 1's two P2s and returned **APPROVE**, recorded in [the re-review](../reviews/2026-11-01-cite-lookup-code-r2/SWE2-REVIEW.md). Before any change, every finding was checked against the code with offline probes. The founder approved the P2 fixes first, then "make all of the fixes you found": the remaining P3s from both rounds, plus three things found while checking.

| Finding | Amendment here |
| --- | --- |
| **r1 P2** `interpret` placed an answer by `start_index` alone | § 3 "Reading the answer" (new): an answer whose echoed `citation`, or whose `normalized_citations`, names a different cite from the line it points at is set aside. That line is NOT LOOKED UP with its own note. A `start_index` that is not an integer places nothing, and the echoed cite places the answer instead. (`69dd62e`, and the note in this revision's last commit) |
| **r1 P2, r1 P3, r2 P3, and the looseness found while checking** NAME DIFFERS hid behind prefixes, abbreviations and common words | § 3 table, FOUND / NAME DIFFERS: "shares a significant word" now means three things. (1) One shared **distinctive** word, matched whole. (2) Bluebook T6 and T10 abbreviations ("Nat'l", "Bd.", "Educ.") are expanded from a table, and only they match by prefix. (3) Generic words (institutions, places, stand-in parties such as "Doe", an agency acronym that CourtListener spells out) count only when the draft's name has nothing else. In that case each of the draft's parties must match a whole party. (`bb9ba9f`) |
| **r1 P3** `\d` read other scripts' digits | § 3 "What leaves the machine": digits are sent as ASCII, and the validator accepts only ASCII digits. |
| **r1 P3** a request that failed still read as sent | § 3 report: a request that left but got no answer is marked "no answer", and the summary counts it as unanswered. The egress log still records every attempt. |
| **r1 P3** `--record` stopped on its first failed write | § 3 "Where results land": a cite whose write fails is rolled back and reported "not written", and the others go on. The run exits 2 when nothing else made it exit 1. |
| **r1 P3** a binary draft gave zero cites and "every case cite found" | § 3 command: a draft holding a NUL byte is refused, whatever its extension. With no case cites, the summary reads "NO CASE CITES IN THE DRAFT" (exit 0). |
| **r1 P3** lookup flags were accepted, and ignored, on `cite extract` and `cite list` | § 3 command: each `cite` subcommand refuses flags that are not its own. |
| **r1 P3** `consent.authorize` gave up at the first grant whose budget was spent | § 3 grant: an exhausted grant is a refusal reason, and later human grants for the provider are still tried. This changes `matterkit/consent.py`, so it applies to search grants too. |
| **r1 P3** the dry-run test's tripwire could never fire | Test only: the dry run now runs again with a grant and a token present. |
| **Found while checking** `cite list --status` built `... ORDER BY ... WHERE ...` | Outside this design (`cite list` predates it): the query is built in the right order, with a bound parameter. |

The name-matching amendment reverses one round-2 test assertion: "Bd. of Educ. v. Kappa" checked against "Board of Education v. Lambda" was FOUND, and it is now NAME DIFFERS because the parties differ. A full word written with a period ("Marsh.") no longer matches by prefix. An abbreviation missing from the table no longer matches either, which errs toward NAME DIFFERS, the safe direction.

Revision 2 has not been re-reviewed.

## 10. Revision 3 (2026-11-01): SWE-2's round 3 re-review

The founder assigned the revision 2 fixes to SWE-2 (`69dd62e..4f7cb89`, which the second history rewrite renamed `92a3bb3..e66f291`). The verdict was **APPROVE WITH CHANGES**, recorded in [the round 3 review](../reviews/2026-11-01-cite-lookup-code-r3/SWE2-REVIEW.md). Every finding row from rounds 1 and 2 was ADDRESSED, with four new P3s. Each was checked against the code with an offline probe before amending. The founder approved fixing P3-1 to P3-3. P3-4 asked for no fix.

| Finding | Amendment here |
| --- | --- |
| **P3-1** a spelled-out suffix ("Limited", "Incorporated") or a trade word ("Trading", "Steel", "Investments") still counted as distinctive, so "Alpha Limited v. Doe" against "Beta Limited v. Roe" was FOUND | § 3 table, FOUND / NAME DIFFERS: spelled-out suffixes are dropped like their abbreviations, and trades, parties' roles ("Trustee", "Plaintiffs") and in-rem words ("Currency", "Vessel") are generic. |
| **P3-2** `cite extract --status` was still ignored, and a zero value (`--max-requests 0`) slipped past the flag check, so revision 2's "each subcommand refuses flags that are not its own" overstated it | § 3 command: `--status` is refused everywhere but `cite list`, and a flag given as 0 counts as given. Revision 2's sentence is now true. |
| **P3-3** a found cite `--record` skipped because several store rows answer to it did not make the run exit 2 | § 3 "Where results land": that cite is reported "not written", like a failed write, and the run exits 2 when nothing else made it exit 1. |
| **P3-4** some true matches give NAME DIFFERS: a spelled-out party against CourtListener's acronym, a capital "V.", and the reverse, swapped parties matching | No change. These err toward a check by hand, the safe direction. |

Two limits found while amending. Brief check's name walk stops at a number, so "Local 100 v. Doe" reaches the lookup as "v. Doe" and "United States v. $10,000 in U.S. Currency" as "Currency". Two in-rem cases that differ only in an amount or a model year therefore still match each other. Also from round 3: when `--record` fails for a cite, the run exits 2 while the summary line still reads "EVERY CASE CITE FOUND", because the summary describes the lookup. The error output and the report's Recorded section show the failure.

Revision 3 has not been re-reviewed.

# Cite checking by default, inside brief check

**Status:** draft for ty's approval, 2026-11-09. Not reviewed.
**Base:** `origin/main` 56148e0. **Branch:** `feat/cite-check-default`.
**Amends:** RFC 0001 §7 (one switch per case); cite lookup design (`2026-11-01-cite-lookup-design.md`); brief check's "read-only" promise.

## Why

People who don't know the law are the ones most likely to file a made-up case, and LLMs still invent cases. Today that protection is opt-in, and the check never runs where it would help most:

- Case law lookups are **off for every case** until a person types `yes` at a terminal or clicks a button in the browser UI.
- **Brief check never asks CourtListener.** A case cite that isn't in the store gets BLOCK `authority-not-logged`, which looks the same for a real case and a made-up one. Once someone logs the cite as `resolved`, the BLOCK drops to UNCHECKED, still with no check that the case exists.
- Cite checking sends only bare citations such as `550 U.S. 544`. RFC 0001's sensitivity ladder already ranks that lowest (`citation_strings: 0`), yet the switch bundles it with free-text search (`query_text: 1`).

**Goal:** every case cite in a draft is checked against CourtListener, or is marked **NOT VERIFIED** with the reason. None gets through unmarked.

**Not a goal:** proving that a case says what the draft claims, that a pin cite is right, or that a case is still good law. The report says so, as `citelookup.LIMITS` already does.

## Design

### 1. Cite checking is on by default; search is not

- **New cases.** `cases.init` writes the cite-lookup grant (`courtlistener-citation-lookup`, `citation_strings`, any caller, 100 a day) with `activated_via: "case-default"` and `created_by: "matter-kit default"`.
- **Existing cases.** A case with no cite-lookup record at all gets the same default grant on first use, from brief check, `cite lookup`, or the MCP tool. It is written once, and the egress log notes it.
- **Off stays off.** `courtlistener off`, and the UI's Turn off, write a `{"kind": "declined", "provider": …}` record. `revoke` keeps that record, and the default never comes back while it exists. `courtlistener on` removes it.
- **Search stays opt-in.** The search grant (`query_text`) is never written without a person. `courtlistener on` still turns on both.
- `caselaw.is_on` splits into `cite_check_on` and `search_on`. `describe()` and `matter.caselaw_status` report each one separately.
- MCP still cannot turn anything on. An MCP call that creates the lazy default for an existing case is only putting a never-chosen case on its default, so it is allowed. MCP can never remove a `declined` record. This gets a pinned test.

### 2. First run asks once

`matter.py init` on a terminal, the UI's new-case flow and Hermes `/matter init` each ask two things, once:

- **The CourtListener key**, if none is saved on this computer. The key is per computer, never per case. The prompt says it is free, gives the link, and explains that checking cites needs it.
- **"Also let your agents search case law for this case? Search words are sent, which can describe your facts. [y/N]"**

When there is no terminal (scripts, tests, MCP), nothing is asked: cite checking is on and search is off.

### 3. Brief check checks cites against CourtListener

After the store checks, brief check gathers the draft's case cites with `citelookup.draft_cites(draft.text)`. That covers Markdown, text, .docx and .pdf through the text brief check already reads. Each cite is checked once, even if the draft repeats it. In order, it:

1. **Uses a saved answer if there is one** (`saved_lookups`). This sends nothing and works offline.
2. **Otherwise sends one request**, if cite checking is on, a key is set and `--offline` was not given. The request goes through `caselaw.check_draft`, which re-checks consent and logs only hashes. One request covers 60 cites. Brief check never waits the 61 seconds between requests: cites past the first 60 are NOT VERIFIED, and the finding says to run `cite lookup --max-requests N`.
3. **Otherwise marks the cite NOT VERIFIED.** The reason is one of: turned off, no key, offline, network error, daily limit reached, or not sent.

New findings, one per unique cite, at its first line:

| CourtListener outcome | Brief check finding |
|---|---|
| FOUND, name matches | no finding. The authorities table shows `CourtListener: found` and a link. |
| NAME DIFFERS | **BLOCK** `cite-name-differs`: "CourtListener has a different case at this cite: X v. Y" |
| NOT FOUND | **BLOCK** `cite-not-found`, using `NOT_FOUND_TEXT` ("check by hand; not proof the case does not exist") |
| UNKNOWN REPORTER, AMBIGUOUS, NOT LOOKED UP | UNCHECKED `cite-not-verified`, with CourtListener's reason |
| not checked (step 3) | UNCHECKED `cite-not-verified`, with the reason and how to fix it |

These sit beside the store findings and do not replace them. A cite missing from the store is still BLOCK `authority-not-logged`. Exit codes keep their meaning.

**Brief check's read-only promise narrows.** It still never writes the store (`connect_readonly`). It may now append to `.matter/caselaw/cites.jsonl` and `.matter/egress.jsonl` and nothing else. It never records an authority and never writes `source-checked`. The module docstring, `LIMITS`, `--help` and the MCP tool's description all change to say this, and the report header adds a line saying what was sent and how many requests went out.

- **New flag:** `--offline` on the CLI, and `offline` on `matter.brief_check`, sends nothing.
- **Tests:** they never touch the network. The transport is passed in, and a guard test fails if any test opens a socket to courtlistener.com.

### 4. Things that do not change

- `versions.py` and the verify count axes, which the Cursus observer pins.
- `cite lookup --record` stays the only path that writes authorities, as `resolved`.
- Search's 500-a-day limit and its key requirement.
- Saved-lookup behavior, and `fresh`.

## Risks

- **False BLOCKs** where CourtListener's coverage is thin: some state reporters, unpublished opinions, slip opinions. NOT FOUND tells the person to check by hand. If that proves noisy, it drops to UNCHECKED: a one-line change, flagged as open question 1.
- **Brief check gets slower and can fail on the network.** At most one request, a short timeout, and a network failure becomes NOT VERIFIED, never a crash.
- **Default-on egress.** Only bare citations leave the computer, the same payload ty already approved for `cite lookup`. Every request is logged, and off is one click and sticks.

## Open questions for ty

1. NOT FOUND as BLOCK (proposed) or UNCHECKED?
2. Lazy default for existing cases (proposed), or new cases only?
3. Which seat reviews, if any?

## Tasks

1. `consent`/`caselaw`: the default grant, the `declined` record, `revoke` keeping it, the `cite_check_on`/`search_on` split, the lazy default. Tests first: default written, off sticks, search never on by default, MCP cannot clear `declined`.
2. `cases.init` writes the default. First-run questions in `cmd_init` (TTY only), the UI new-case flow and Hermes `/matter init`. Update `test_easy_caselaw` wherever it assumes off by default.
3. `briefcheck`: the CourtListener phase, the findings table, `--offline`, the narrowed read-only docstring and `LIMITS`, the report header line. Tests use an injected transport: found, name differs, not found, saved answer used offline, off, no key, network error, more than 60 cites.
4. `matter-mcp.py` (`brief_check` offline argument, descriptions) and the UI panel text ("Cite checking: on by default").
5. Docs: RFC 0001 §9 amendment, cite lookup spec revision, README, the `matter.py` usage text.
6. Full gate (`python3 -m unittest`), leak scan over the range, then hand off to ty. Push only on ty's go.

# Corpus Open

**Matter Kit** — provenance-enforced case organization for people litigating without a lawyer — operable by any AI agent.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![CI](https://github.com/teakesdev/corpus-open/actions/workflows/ci.yml/badge.svg)](https://github.com/teakesdev/corpus-open/actions/workflows/ci.yml)

<p align="center">
  <img src="assets/CorpusOpenLegalKit.png" alt="Corpus Open Legal Kit — open-source legal tools for humans and AI agents" width="100%">
</p>

The public project name is **Corpus Open**. The code, CLI, and skill stay `matter-kit`. Live case files never belong in this repository.

Matter Kit turns a folder of case files into a *matter*: hashed, tamper-evident
source documents plus a structured store where every substantive assertion is
either traceable to a document/page/timecode or visibly flagged `UNSUPPORTED`.
It renders layered counsel packets (30-second / 3-minute / 15-minute diligence
views), tracks deadlines with confidence labels, and exposes everything to
Hermes, Claude Desktop, Codex, or any MCP client through a zero-dependency
stdio MCP server.

Nothing here files, sends, or contacts anyone. Sources are never modified.
This is legal-organization software, not a lawyer and not legal advice.

## Why

AI-drafted court filings without verifiable provenance are generating sanctions
at scale (1,500+ documented cases of fabricated citations by mid-2026), and
self-represented litigants are the fastest-growing segment of that problem.
Meanwhile, lawyers decline meritorious cases from self-represented people mostly
because evaluating them costs hours of unpaid diligence. Matter Kit attacks both:
work product that survives hostile scrutiny, and counsel packets built to be
evaluated in minutes.

## Quick start

```bash
python3 matter.py init ~/cases/my-case
python3 matter.py ingest ~/cases/my-case          # hash + register every file
python3 matter.py verify ~/cases/my-case          # re-hash; non-zero exit on any drift
matter_fact=$(python3 matter.py fact add ~/cases/my-case \
  --text "Property records show plaintiff owns both parcels" \
  --status DOCUMENTED-FACT --source "Evidence/EVIDENCE_INVENTORY.md@Tier 1 #6")
python3 matter.py packet ~/cases/my-case --layer 3min --out COUNSEL_PACKET.md
```

## Using the kit without a terminal

```bash
python3 matter.py ui ~/cases --operator "Your Name"   # a folder of case folders, or one case folder
```

This opens the kit in your browser. It runs only on your computer (`127.0.0.1`),
with nothing to install and nothing fetched from the internet. One person can
keep several people's cases in one folder: each subfolder holding a `.matter/` is
a case, and subcases show under their parent. In the browser you can:

- create a case;
- add files by dragging them in, or register files you copied into the folder;
- check that no file has changed;
- see deadlines, with any that have passed shown first;
- add facts with a label and the document and page they come from;
- keep the list of changes for the next amended pleading (the "Next amendment"
  tab): add a change with the facts and documents behind it, then approve it,
  mark it "in the draft", drop it or reopen it, with a note if you like. The
  overview says how many are still queued;
- run brief check on a `.md` or `.txt` draft;
- read, copy or save the counsel packet.

Everything else, including adding or changing deadlines, stays in the CLI for
now. In the browser page, Word and PDF drafts still need a `.txt` copy before
brief check can read them; the command line reads `.docx` and `.pdf` drafts
directly (see "Word and PDF drafts" below).

Your name is recorded as the author of everything you add (`human:Your Name`).
The page can't choose a different author. The link the command prints carries a
one-time key: other websites open in the same browser can't use the kit, and the
server answers only to `127.0.0.1` and `localhost`. The UI is a WSGI app built
with the seams a private server shared with a lawyer would need: the server, not
the page, says who is acting on each request; one policy check runs on every
route and also decides which cases a person sees at all; and the allowed
addresses are settings. That server still needs sign-in, TLS and real per-case
access rules, and none of that is built yet: see
[the design](docs/superpowers/specs/2026-10-31-local-frontend-design.md).

## Capturing a web page

```bash
python3 matter.py capture ~/cases/my-case --url https://example.org/press/release \
  --out Media/captures/2026-09-18_example.org/page.html   # --out is matter-relative, never cwd
python3 matter.py capture list ~/cases/my-case            # the provenance table, as Markdown
```

`capture` writes the fetched bytes, registers them through `ingest` with
`source_url` and `kind=media|research`, and records
`url · retrieved_at · sha256 · rel_path` in the `captures` table. It refuses to
overwrite, refuses redirects (the recorded URL is the URL that served the
bytes), and refuses URLs carrying credentials. A page whose bytes are already in
the store records the retrieval without writing a duplicate file. Derived text
goes to `.matter/out/captures/<sha256>.txt` — never beside the source, never
ingested. Network: one GET to the URL the operator named, logged hash-only to
`.matter/egress.jsonl`. Unlike `search` it takes no consent grant — the URL on
the command line is the instruction, and nothing else leaves. Then `verify` from
a foreign cwd, as after any write.

## Custodian correspondence

Create a notice for a custodian party, then generate a packet for human review:

```bash
custodian=$(python3 matter.py party add ~/cases/my-case \
  --name "Example Records Office" --role agency --capacity custodian)
python3 matter.py ingest ~/cases/my-case ~/cases/my-case/Correspondence/esi-schedule.txt
notice=$(python3 matter.py correspondence add ~/cases/my-case --kind hold \
  --addressee "$custodian" --address "100 Example Street, Example City" \
  --exhibit Correspondence/esi-schedule.txt --confirm-due 2026-10-01)
# Add --assertion a_ID (repeatable) to select assertion rows for this addressee.
python3 matter.py correspondence export ~/cases/my-case "$notice"
python3 matter.py correspondence list ~/cases/my-case
```

Kinds: `hold|mpra|foia|touhy|letter|other`. The export contains `packet.md`, an
exact Exhibit A copy, and `approval.json` under `.matter/out/correspondence/`.
Letter bodies include only selected `DOCUMENTED FACT` and `UNKNOWN` assertions;
HYPOTHESIS rows are omitted even when explicitly selected. Exhibits themselves
require human review. Missing or changed sources block generation.

After reviewing the exact bundle, a human records signing with the printed
manifest hash. Sending happens outside the kit. An ingested receipt is required
to record mailing:

```bash
python3 matter.py correspondence status ~/cases/my-case "$notice" signed \
  --issuer "Human Name" --sha REVIEWED_MANIFEST_SHA256
# The human sends, saves their receipt in the matter, then registers it:
python3 matter.py ingest ~/cases/my-case ~/cases/my-case/Correspondence/mailing-receipt.pdf
python3 matter.py correspondence status ~/cases/my-case "$notice" mailed \
  --issuer "Human Name" --sha REVIEWED_MANIFEST_SHA256 \
  --receipt Correspondence/mailing-receipt.pdf
```

Delivery confirmation (`receipt-hashed`) and response states (`answered|refused`)
also require ingested evidence via `--receipt`. All transports remain `nosend`.
The issuer is an operator attestation, not identity authentication. Confirmation
dates create linked deadlines marked `working-estimate`; mailing does not make
them verified legal deadlines. See [RFC 0006](rfcs/0006-correspondence-ledger.md)
for the lifecycle and approval contract. This ledger is separate from counsel
outreach.

## MCP (Hermes / Claude Desktop / Codex / anything)

```json
{ "mcpServers": { "matter": { "command": "python3",
    "args": ["/path/to/matter-kit/matter-mcp.py"],
    "env": { "MATTER_DIR": "/absolute/path/to/case-dir" } } } }
```

Tools: `matter.status` · `matter.fact_add` · `matter.fact_list` ·
`matter.amend_add` · `matter.amend_list` ·
`matter.deadline_list` · `matter.trigger_list` · `matter.trigger_fire` ·
`matter.packet_render` · `matter.cite_extract` ·
`matter.ingest_path` · `matter.verify` · `matter.hold_render` ·
`matter.correspondence_list`.
`matter.ingest_path` refuses paths that escape `MATTER_DIR`. `matter.verify`
prints absolute `root=` and ok/changed/missing; it does not send network.
`matter.hold_render` takes a `notice_id` and returns a filtered custodian packet.
MCP cannot approve a notice, record mailing, or send it. `matter.amend_add`
can only propose a change to the amendment list; approving or dropping one is a
person's step (CLI or browser).

## Hermes plugin (citation guard, source guard, deadline brief, /matter)

This repo is also a native [Hermes Agent](https://hermes-agent.nousresearch.com) plugin
(`plugin.yaml` and `__init__.py` at the root, code in `hermes_plugin/`). It adds what a skill or
the MCP server above can't:

- **Citation guard.** Under every reply that cites anything, a block lists each case, statute and
  code cite with what is known about it: `FOUND` or `NOT FOUND` / `NAME DIFFERS` on CourtListener,
  `IN CORPUS` or `NOT MATCHED IN CORPUS` on [Corpus](https://corpuslaw.us), and what your case
  file's authority ledger says. Problems come first. Found means a source holds something at that
  cite, not that it says what the reply says or is still good law.
- **Source guard.** Hermes's `write_file` and `patch` can't change a registered source document
  or anything in the case store (`.matter/`): the call is refused with the file's name and hash,
  and the agent is told to write a new file instead. A `terminal` command or `execute_code` run
  that names a source or the store and looks like it changes files (`rm`, `mv`, `sed -i`, `>`,
  `open(..., 'w')` and the like) goes to your approval first. If the store can't be read, the call
  goes to approval too. New files and unregistered drafts in the case folder are untouched. This
  catches an agent's mistakes; it is not a sandbox, and the `source_guard` setting turns it off.
- **Deadline brief.** On a session's first turn, overdue deadlines and those due within
  `deadline_window_days` go to the agent, so it raises them before anything else.
- **`/matter`** shows status and deadlines; `/matter packet 30s`, `/matter check Drafts/x.md` and
  `/matter grant` (how to allow cite checks) do what they say.
- **Case pane (Hermes Desktop).** A pane beside the chat (`desktop/plugin.js`) shows the case at a
  glance: open deadlines with passed ones first, a tamper badge that re-hashes every registered
  file against the hash taken when it was added (Intact / Tampered / Files missing), and how many
  facts have no source. A status-bar chip shows passed deadlines and a failed check from anywhere.
  It updates when the agent's `matter_*` tools run. It reads the case through this plugin's own
  read-only backend (`dashboard/plugin_api.py`, mounted at `/api/plugins/corpus-open/`), which
  Hermes loads only for plugins in `plugins.enabled` (`hermes plugins enable corpus-open` adds
  it). It never writes to the case and makes no network calls.
- **Twelve tools** in the `corpus_open` toolset: `matter_status`, `matter_fact_add`,
  `matter_fact_list`, `matter_deadline_list`, `matter_trigger_list`, `matter_trigger_fire`,
  `matter_packet_render`, `matter_brief_check`, `matter_rules_show`, `matter_cite_check`,
  `matter_verify`, `matter_ingest_path`.

Install and set it up:

```sh
hermes plugins install https://github.com/teakesdev/corpus-open
hermes plugins enable corpus-open
python3 matter.py init ~/cases/my-case            # then set matter_dir to it in the plugin's settings
python3 matter.py consent activate ~/cases/my-case --scope cite-lookup --caller hermes
python3 matter.py consent activate ~/cases/my-case --scope corpus-search --caller hermes
```

To try the Case pane on synthetic data, make a demo case, set `matter_dir` to it, and edit one of
its files on cue to watch the badge turn:

```sh
python3 scripts/panel_demo.py setup ~/cases/panel-demo
python3 scripts/panel_demo.py tamper ~/cases/panel-demo
```

Settings (Desktop: Settings > Plugins > corpus-open): `matter_dir`, `citation_guard`
(`lookup` / `local` / `off`), `source_guard` (on by default), `caller_id`, `deadline_window_days`, `max_statute_checks`, and two
secrets read through Hermes's secret store: `COURTLISTENER_TOKEN` (free, needed for case cites) and
`CORPUS_API_KEY` (optional).

**Network calls (disclosure).** The plugin makes exactly two kinds of request, both only from the
citation guard and `matter_cite_check`, and only after a person grants them in the case folder by
typing `ACTIVATE` in a terminal (a grant an agent writes is refused):

| To | What is sent | Limit |
|---|---|---|
| `www.courtlistener.com` citation lookup | the bare case cites (`123 F.4th 456`), up to 60 per reply, with your token | the grant's daily budget (10) |
| `corpuslaw.us` law search | one statute or code cite per request (`Miss. Code Ann. § 89-7-27`) | `max_statute_checks` per reply, grant budget 50/day |

No reply text, facts, documents or names leave the machine. Every request is logged to
`.matter/egress.log`. With `citation_guard: local`, nothing is sent and cites are only listed. The
plugin files, sends and approves nothing, and sends no telemetry.

## Document extraction and locators (RFC 0004, RFC 0005)

```bash
python3 matter.py pages ~/cases/my-case                       # every ingested document
python3 matter.py pages ~/cases/my-case Filings/motion.docx    # one file
```

Stdlib only — no pypdf, no python-docx. Each format gets the locator its own
bytes can support, and the two never mix:

| scheme | format | locator | stored in |
|---|---|---|---|
| `pdf-page` | PDF | 1-based page number read from the file's structure | `document_pages` |
| `docx-structural` | DOCX | path through the OOXML block tree, e.g. `word/document.xml#b3/r1/c0/b0` | `document_blocks` |
| `whole-file` | `.txt` / `.md` | the file is one unit | `document_pages` |

**DOCX never gets a page number.** Pagination is produced by the renderer at
layout time and is not in the file, so one would have to be invented — see
RFC 0005. Extraction that cannot be done honestly fails and says why
(`extraction-failed` with a reason); it is never a silent empty string.
Sources are read-only and are never rewritten.

## Citations and stages (v0.2, RFC 0001)

```bash
python3 matter.py cite extract ~/cases/my-case Evidence/complaint.txt  # local parse; offsets keyed to the parsed text's sha256
python3 matter.py cite list ~/cases/my-case --status extraction-failed # unparsed fragments, first-class, never dropped
python3 matter.py stage ~/cases/my-case auth_xxx source-checked \
  --via "courtlistener:id=123" --evidence "DL #123 text matches; still good law as of 2026-09-08"
```

Stages: `unverified` → `resolved` → (`research-pending`) → `source-checked` /
`stale-flagged`. Transitions are validated — a check status without a resolved
source, or evidence-free "checked," is refused. These tokens mean *a defined
source was checked*, never *this is correct law*.

**What reaches the network.** Three commands, and only when you run them:
`capture` sends one GET to the URL you name; `search` sends your query to
CourtListener's search API; `cite lookup` sends a draft's case cites, and
nothing else, to CourtListener's citation-lookup API. `search` and
`cite lookup` each need their own grant in `.matter/consent.json`, issued by a
person at the terminal (`consent activate`, `kind: "human"`) and naming its
authorized callers; agent-authored grants are inert proposals. Each request is
logged hash-only to `.matter/egress.jsonl`. All outbound code lives in
`matterkit/adapters/`; everything else, including `brief check`, `verify`, the
browser UI and the MCP server, makes no outbound calls.
See `rfcs/0001-provider-neutral-search-adapters.md`.

## Checking a brief before it is filed

```bash
python3 matter.py docket suggest ~/cases/my-case > ~/cases/my-case/.matter/docket.json  # then review it
python3 matter.py authority import ~/cases/my-case Research/AUTHORITY_LEDGER.md         # dry run; --apply writes
python3 matter.py authority check ~/cases/my-case "Dietz v. Bouldin, 579 U.S. 40 (2016)" \
  --source-url https://example.org/official-opinion.pdf --pin 45-47 \
  --artifact Research/authorities/dietz.pdf --quote "inherent authority" --author agent:MODEL --directed-by "Your Name"
python3 matter.py brief check ~/cases/my-case Filings/drafts/RESPONSE_v1.md --out RESPONSE_v1_CHECK.md
```

`brief check` reads a Markdown, text, Word (`.docx`) or PDF draft and the store
(read-only) and reports every record cite (`Doc. 16 at 2`, `Docs. 16 and 18`, `ECF No. 19`),
pleading cite (`Compl. ¶ 59`), authority (`X v. Y, 579 U.S. 40, 45`, short
forms, `Id.`, `Fed. R. Civ. P.`, `Rule 15`, `L.U.Civ.R.`, statutes), quotation
and fact tag (`<!-- fact:a_… -->`) that is not tied to a hash-verified source.
Quotations are searched on the pinned page of the cited document: Markdown and
text sources directly, PDFs through a text copy named in `docket.json`
(`text_copies`) or, when installed, `pdftotext` (used transiently, never
stored). Uncited sentences in a Facts/Background section are flagged. Exit 1
means blocking findings, 2 means items only a person can check, 0 means every
item is tied to a source in the store. That never means the brief is legally
correct or that an authority is still good law.

For a complaint, pass `--mode complaint`. Allegations need no record cite, so
uncited sentences are not flagged, and quoted speech with no cite is listed for
the pleader to confirm rather than flagged. Every record cite, ¶ cite,
authority and sourced quotation is still checked, and a fact tag labeled
HYPOTHESIS, INFERENCE or UNKNOWN is flagged.

Headings mark where fact sections start and end.
Headings are still read for cites and quotations, in either mode, and for fact
tags; only the uncited-sentence rule skips them, so "II. Argument Under Rule
12" has its rule checked. A numbered line (and, in complaint mode, a lettered
one other than I, V, X, L or C) counts as a heading only when it is short,
carries no record, ¶ or case cite, short-form or Westlaw cite, Id., statute
sign or quotation, and is either unpunctuated ("1. Background") or a title
ending in a period or colon ("1. Statement of Facts."); "1. On January 1,
officers …" and "12. See Doc. 16 at 3" are paragraphs. After any other single
letter, the rest of the line must read as a title: short and unpunctuated, or
Title Case, with no cite and no abbreviation at the start. So "J. Smith
testified …", "W. Va. Code § 21-23-7 controls." and "I. Leave is proper under
Rule 99(z)." are sentences; number such a point heading "II." or give it a
title. In brief mode, a short lettered line naming a fact section ("A. Factual
background.", "A. Background Doc. 16") is always a heading, even with a cite.
Known limit: the same line numbered or in roman ("1. Statement of Facts Doc.
16", "II. Facts Established At The Hearing Doc. 16"), or lettered in complaint
mode, is text when it carries a cite, so the fact section it names does not
open and the uncited facts under it are not flagged. Inside a fact section, a
numbered title at the same level ("2. Plaintiff Was Arrested.") is a
subheading, and one naming a non-fact section ("2. Argument.") ends it. A fact
section never narrows: "I. FACTS" stays open through "A." and "B." subsections
until a heading at its own level or higher.

## Checking that cited cases exist

`brief check` can only show that a cite is tied to a row in the store. It
cannot tell that "Smith v. Jones, 123 F.4th 456" names no real case.
`cite lookup` asks CourtListener. It is opt-in, and `brief check` stays offline.

```bash
python3 matter.py cite lookup ~/cases/my-case Filings/drafts/RESPONSE_v1.md --dry-run  # shows exactly what would be sent
python3 matter.py consent activate ~/cases/my-case --scope cite-lookup                 # a person types ACTIVATE at the terminal
export COURTLISTENER_TOKEN=…                                                          # your CourtListener API token
python3 matter.py cite lookup ~/cases/my-case Filings/drafts/RESPONSE_v1.md --out RESPONSE_v1_LOOKUP.md
python3 matter.py cite lookup ~/cases/my-case Filings/drafts/RESPONSE_v1.md --record   # found cites -> resolved
```

- **What is sent:** each full case cite in the draft (reporter and WL cites),
  rebuilt as `volume reporter page`, one per line. Case names, pins,
  parentheticals and every other word of the draft stay on your machine, and
  a check refuses the request if any line is not a bare cite. Short forms,
  `Id.`, statutes and rules are not sent.
- **Limits:** up to 60 cites a request, at least 61 seconds apart, at most
  `--max-requests` (default 3) a run and the grant's daily budget (default 10).
  A throttled run stops and does not retry.
- **Results:** FOUND (with the opinion's name, date and CourtListener link),
  NAME DIFFERS (an opinion exists at that cite but its name shares no
  distinctive word with the draft's; generic words such as "Board", "Bank",
  "Steel", "Trustee" or "Doe", and suffixes such as "Limited", count only when
  a whole party matches), NOT FOUND, UNKNOWN REPORTER,
  AMBIGUOUS and NOT LOOKED UP. Exit 1 means a cite to check by hand, 2 one a
  person must resolve (or a found cite `--record` could not write), 0 every
  cite found or none in the draft.
- **Not found is a lead, not proof:** CourtListener lacks some reporters and
  many slip, unpublished, Westlaw-only and older state opinions. Found means an
  opinion sits at that volume, reporter and page. It does not mean the case
  says what the draft says, that it is still good law, or that a pin is right.
- **`--record`** moves a found cite's authority from `unverified` to
  `resolved`, with `checked_via` `courtlistener:id=…` and the lookup (cluster,
  link, `retrieved_at`) in its evidence, or adds a `resolved` row. Rows already
  past `unverified` are left alone, and nothing is ever marked
  `source-checked`. A found cite that several store rows answer to is not
  written; resolve those rows by hand. The next `brief check` then shows the cite as located but
  not source-checked instead of blocking it.

### Word and PDF drafts

```bash
python3 matter.py brief check ~/cases/my-case Filings/drafts/RESPONSE_v1.docx --out RESPONSE_v1_CHECK.md
python3 matter.py brief check ~/cases/my-case Filings/drafts/RESPONSE_v1.pdf
```

The report says `Where` instead of `Line`, and every finding has a location a
person can find: `para. 14`, `fn. 3`, `endnote ii`, or `p. 3:12`. The `.md` and
`.txt` path is unchanged. `.doc`, `.rtf` and `.odt` drafts are refused: in Word,
File > Save As > Word Document (`.docx`). A file whose bytes are some other
format than its name says is refused rather than read as garbage.

**Word (`.docx`).**

- Read: body paragraphs, tables, footnotes and endnotes, with the numbers Word
  prints for auto-numbered headings and lists ("II.", "A.", "1.") rendered from
  the document's numbering, so the Facts-section and heading rules work as they
  do for a `.md` draft. A Word heading style (or outline level) marks a heading
  the text alone does not show.
- Footnotes and endnotes sit right after the paragraph that cites them, so a
  Facts section governs them, and are reported as `fn. N` / `endnote ii` (in the
  order the references appear; a warning says when the document restarts
  numbering). An `Id.` in a note refers only to an earlier note, never to the
  text: with none, it is a blocking `id-unbound`. Give the full cite.
- `para. N` counts the non-empty paragraphs in reading order (a table cell says
  which table, row and cell; a numbered one says its number, as `numbered II.`).
  Word has no paragraph numbers or page numbers to read, so none is invented:
  look for the quoted sentence next to the location.
- Refused: tracked changes (insertions, deletions, moves). The file holds two
  versions of the text and brief check will not guess which one you mean; accept
  or reject them in Word, save a copy and check that.
- Warned, never silent: comments (not part of the filing), hidden text, text
  boxes, symbol-font characters (their code means nothing outside that font, so
  none is guessed at), formatting-only tracked changes (also in the styles,
  numbering and settings, which are never refused), a list whose numbers could
  not be rendered, a list whose numbers may not match Word's (a sub-level set
  not to restart the usual way, a level redefined for one list, a list that
  takes its numbers from a list style, or a style tied to a list but carrying
  no number; the warning names the first paragraph affected, and `para. N`
  stays right), a reference to a missing note, content in elements brief
  check does not read (including a table directly inside a table or row). Said in the report as not read:
  headers and footers, table-of-contents and table-of-authorities lines (by
  style), unreferenced notes.
- Smart quotes, non-breaking spaces and thin spaces around `§` and in
  `Fed. R. Civ. P.` are normalized so the cite is read, and `’` in `App’x` is
  read as `'`.
- The package is opened defensively (stdlib only): a DOCTYPE or entity, a
  non-UTF-8 part, a duplicated or encrypted part, a forged or oversized zip
  directory, or a decompression bomb is refused with a reason (RFC 0005 §4a,
  ported from `feat/docx-structural-extraction`, and applied to every part
  read). Nothing is written or stored.

**PDF.** Reads with `pdftotext -layout`, which must be installed (poppler);
without it, or with `--no-pdftotext`, a PDF draft is refused with the reason.
The text is held in memory for one check and never stored. Page numbers (a lone
number at a page's top or bottom that follows the page sequence) and running
headers and footers (the same line, digits aside, on most pages) are dropped.
Locations are `p. PAGE:LINE`: the PDF's page order (the first page is 1, not
the printed number) and the text line counted from the top of the page,
running header included. Paragraphs are rebuilt from spacing and indentation,
so check the headings the report recognized. Known limits: footnote text in a PDF
is read where it sits on the page and can split a sentence that crosses a page;
pleading-paper margin numbers are not recognized; a scan with no text layer is
refused (no OCR). A `.docx` gives the more reliable check.

## Changes for the next amended pleading

```bash
python3 matter.py amend add ~/cases/my-case --para "¶¶ 37, 172" \
  --text "Attribute all three ER injuries to the jail beating" \
  --reason "The ER notes tie them to one event" \
  --fact a_0123456789ab --source "Evidence/er-notes.pdf@p. 2"      # add --target for another pleading
python3 matter.py amend list ~/cases/my-case                       # open changes: approved first, then proposed
python3 matter.py amend list ~/cases/my-case --status all --target complaint
python3 matter.py amend set ~/cases/my-case am_0123456789ab --status approved --note "checked against the ER notes"
```

The amendment list is a queue of what to change in the next version of a
pleading: what to add or fix, which paragraphs it touches, why, and what it
rests on. `--fact` takes the `a_…` id that `fact add` printed (over MCP,
`matter.fact_list` lists them). `--source` takes `rel_path@page` for a
registered document, split on the last `@`; a file name that has an `@` and no
page ends in `@`. In a subcase, linking a document that is a reference to a
parent document first re-hashes it against the parent, as `fact add` does, and
refuses it if the parent's file changed or went missing. Every item starts as
`proposed`. A person moves it to `approved`, to `incorporated` (shown as "In the
draft", once the change is in the new draft) or to `dropped`, and back to
`proposed` if they change their mind. Each move records who, when and an
optional note; nothing is ever deleted. `add` and `set` take
`--author agent:MODEL [--directed-by PERSON]` like `fact add`. `list` shows open
changes (proposed and approved) unless `--status` asks for `all` or one status,
and it never writes to the case. The list only records changes: it does not
edit a draft, and brief check does not read it.

## Court rules attached to a case

```bash
pdftotext -layout local_rules.pdf local_rules.txt
python3 matter.py rules build local_rules.txt ~/rules/local-2024-12-01 --id my-district-local \
  --version 2024-12-01 --title "Local Civil Rules" --cite-prefix "L.U.Civ.R." --current-as-of 2024-12-01 \
  --start-at PREAMBLE --stop-at "OFFICIAL FORMS"
python3 matter.py rules attach ~/cases/my-case ~/rules/local-2024-12-01 --author agent:MODEL --directed-by "Your Name"
python3 matter.py rules show ~/cases/my-case "L.U.Civ.R. 7(b)(4)"   # the subsection's text, pack and version
python3 matter.py rules stale ~/cases/my-case                       # exit 1 when a pack is past its review window
```

A rules pack is one rule set at one version: `manifest.json` and `rules.md`,
with one heading per rule and an anchor per subsection (`L.U.Civ.R. 7(b)(4)` is
`#lucivr-7-b-4`). The build fails unless the Markdown's words, with the added
headings, anchors and labels removed, are the source text's words in the same
order. It drops only page numbers (a lone number at a page's top or bottom, such
as `3`, `- 3 -`, `Page 3 of 40` or `iii`, that follows the page sequence) and
keeps everything between `--start-at` and `--stop-at`. The manifest's `dropped`
lists each page number it left out. A level it had to guess, or a marker out of
sequence, is listed in the manifest's `notes`; when a marker repeats one that is
already anchored, the note names the earlier line, which may be a wrapped
cross-reference holding the anchor. A line that starts with a list's next marker
but is indented unlike that list stays text when the same marker follows at the
list's own indentation (a wrapped `Rule 26(b)(1) and` / `(2) if needed`), and is
listed in `notes`. A line that opens an item and its first child together
(`(A)(i) deliver ...`) anchors both: the item gets a label line of its own and
the child the text. A page that looks like two columns, a word
that seems to have lost letters, or a page number with no sequence to check it
against is listed in `warnings`, because the word check cannot see any of them.
A pack is derived text, not the official copy.

Rules numbered `(a)(1)(A)(i)` build as above. For a document numbered as an
outline (`Section 4.`, then `A.`, `1.`, `a.` or `i.`), pass `--markers dotted`
with a `--heading-regex` for its sections and `--unit Section`, so the Markdown
reads `## Section 4.` and `§ 4(A)(5)` is `#<prefix>-4-A-5`. In that mode a marker
starts a list only with its first item (`A.`, `1.`, `a.`, `i.`) or continues an
open list in sequence, whatever its indentation; parenthesized markers may sit
under the outline. Anything else, such as a wrapped line beginning `R. CIV. P.`,
stays text and is listed in `notes`.

`rules attach` copies the pack to `Research/Rules/<id>_<version>/`, registers
both files so `verify` covers them, and records it in `.matter/rules.json` and
`events`. One pack per cite prefix and per anchor prefix; a new version needs
`--replace`, and the old entry is kept as history. `.matter/rules.json` is not a
registered document, so its entries are checked each time it is read. `rules
show` reads `Fed. R. Civ. P. 15(a)(2)`, `Local Rule 7(b)(4)` and the other
"Local … Rule(s)" words that brief check reads (as the docket map's
`local_rule`), or an anchor id. A bare
`Rule 15` names no rule set, so it lists every pack that has it, exits 2 and
picks none unless you pass `--pack`. With packs attached, `brief check` looks up
each rule cite's subsection and warns when the pack lacks it; what blocks is
unchanged. A list member that is only subsection marks is a sibling of the
last cite's mark of the same kind: `7(b) and (c)` checks 7(c), and
`26(a)(1)(A)(i), (ii)` checks 26(a)(1)(A)(ii). `deadline add --rule` stores a rule found in a pack as
`L.U.Civ.R. 7(b)(4) [pack@version#anchor]`, and anything else as the text you
gave. The MCP tool is `matter.rules_show`. `rules stale` exits 1 for a pack
past its review window, with no date or a date after today, or that cannot be
used (changed since it was attached). It compares dates only; it does not check
the court's website.

When you have checked the court's website, record it so `stale` stops asking:

```bash
python3 matter.py rules checked ~/cases/my-case my-district-local --on 2025-11-30 --found none \
  --source Research/Rules/site-check/CHECK_2025-11-30.md --next-review 2026-12-01
```

Each `--source` must be a registered file (a note, a saved copy of the court's
page) and is re-hashed before the check is recorded; the check goes into
`.matter/rules.json` with its sources' sha256, plus an `events` row. A pack past
its window then reads `CHECKED` (and `stale` exits 0) until `--next-review` or
the check date plus the pack's review window, whichever comes first. A check
applies to the pack version it was recorded for, never vouches for an undated
pack, and `--found amended` keeps the pack `STALE` until you attach the new
version.

## Deadlines and subcases

```bash
python3 matter.py deadline list ~/cases/my-case            # open dates already past are marked ⚠ OVERDUE
python3 matter.py deadline list ~/cases/my-case --owner theirs   # only what the other side must do
python3 matter.py deadline update ~/cases/my-case d_abc123 --owner theirs --reason "their answer, not ours"
python3 matter.py deadline update ~/cases/my-case d_abc123 --state superseded \
  --reason "extension granted by text-only order" --source Filings/docket_sheet.pdf@entry-27
python3 matter.py holiday year ~/cases/my-case --year 2026 --jurisdiction "Example State" \
  --source Research/state-holidays-2026.pdf@p.1      # after adding each state holiday with holiday add
python3 matter.py trigger add ~/cases/my-case --label "Reply brief" --days 7 \
  --waits-on "the response to the motion is served" --rule "L.U.Civ.R. 7(b)(4)" --owner ours
python3 matter.py trigger fire ~/cases/my-case t_abc123 --on 2026-11-02 --service ecf \
  --source Filings/response.pdf@p.1 --reason "response filed and served through ECF"
python3 matter.py subcase init ~/cases/my-case Related-Claim --title "Related state-court claim"
python3 matter.py fact add ~/cases/my-case/Subcases/Related-Claim --text "…" --status documented-fact \
  --source parent:Media/article.html@para3
```

Every deadline change needs a reason and is recorded in `events` with who made
it. A deadline's owner says who must act by it: `ours`, `theirs` (the other
side) or `court`. It is unset unless you give `--owner` to `add` or `update`, and
`list` prints `owner=` only when it is set.

A trigger is a deadline that waits on an event, such as "7 days after the
response is served". It has no date until `trigger fire` records when the event
happened and how the paper was served. Fire then counts the days under Fed. R.
Civ. P. 6(a)(1): it skips the trigger day, counts every day, and runs past a
Saturday, Sunday or legal holiday. Under 6(d) it adds 3 days only for service by
mail, by leaving the paper with the clerk, or by other consented means, then
rolls again. It inserts one ordinary deadline, `working-estimate/unverified`,
and records every counting step in `events`, so check the count before you
mark it verified. `--dry-run` shows the count and writes nothing. It also takes
a date after today, so you can see what an order entered that day would make
due; without it, `fire` refuses a date after today.

Legal holidays are the federal holidays of 5 U.S.C. § 6103, computed with the
weekday observed for one on a weekend, plus the days you record with a source:
state holidays, days the President or Congress declares, and days the clerk's
office was inaccessible (6(a)(3)). Record each state holiday that is not
already a federal one with `holiday add`, then say the year is complete with
`holiday year`; a count refuses any year without that record. Backward counts
("7 days before the hearing") and the hour a day ends (6(a)(4)) are not
computed.
`deadline list` shows waiting triggers after the dated rows.

A subcase is its own matter under `Subcases/<slug>/` with a `matter.json`
pointing at its parent. The parent's `ingest` stops at any folder holding its
own `.matter/`. A subcase cites the parent's documents in place as
`parent:<rel_path>`, re-hashed against the parent on every use and never
copied. `status` on the parent lists its subcases and their overdue deadlines.

## Skills

`skills/matter-kit/SKILL.md` teaches agents the operating discipline: the five
epistemic labels, media-before-strategy rule, citation-verification habit,
date-discrepancy preservation, and the folder taxonomy. Copy or symlink it into
your agent's skill directory:

```bash
ln -s /path/to/matter-kit/skills/matter-kit ~/.hermes/skills/matter-kit
```

## Design rules

1. **Plain files + SQLite.** No server, no lock-in; any agent can read the store.
2. **Sources immutable.** Everything derivable lives in `.matter/`; originals never change.
3. **Labels are enforced, not suggested.** Unsupported assertions render `[UNSUPPORTED]`.
4. **Read-mostly MCP.** Agents register facts and render packets; humans act.
5. **Adverse facts are first-class.** A packet that hides them is worthless and dishonest.

## Status

v0.1 — dogfooding stage. Schema may change; hashing is stable.

License: Apache-2.0. Not affiliated with any bar association; nothing here is
legal advice or a substitute for a licensed attorney.

Built while running [Corpus](https://corpuslaw.us). The kit is free and local;
optional `law.*` search against Corpus is the only paid hook, never required
to organize a matter.

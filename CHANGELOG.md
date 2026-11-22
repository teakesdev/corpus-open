# Changelog

What changed in each version of Corpus Open, newest first. The version is the one in
`plugin.yaml`. Until 0.7.0 no version was tagged: Hermes installed whatever `main` held, so a
version below names the state of `main` between two bumps, not a download.

Pull request numbers link to https://github.com/teakesdev/corpus-open/pulls. Commit ids are on
`main`.

## [0.7.0] (unreleased)

### Added

- **Case timeline** (#20). Facts can carry a date (`YYYY-MM-DD`, `YYYY-MM` or `YYYY`, a range,
  "on or about", or worked out), a quote from each source and a short event name. Two new Hermes
  tools, `matter_fact_date` and `matter_timeline`, and the MCP tools `matter.fact_date` and
  `matter.timeline`. The timeline lists dated facts oldest first, checks each quote against the
  registered document every time it is shown, cites text files by line (`L12-L14`), says how many
  documents back each row, and lists events whose sources give dates that disagree without
  picking a side.
- **City code cites** (#21). The citation guard checks a cite such as
  `Austin City Code § 25-2-551` against Corpus's city codes, with the city's Corpus code
  (`TX-AUSTIN`), when the cite names the city or the case has one set with `/matter city`.
  `matterkit/data/corpus_cities.json` lists the cities Corpus holds, and
  `scripts/refresh-corpus-cities.py` refreshes it. This is a new kind of request to Corpus; it
  can be turned off with the other cite checks.
- **Case-law research log** (#19). `matter.py research` and the MCP tools
  `matter.research_save`, `matter.research_log`, `matter.research_list`,
  `matter.research_leads`, `matter.research_due` and `matter.research_where` keep a record of
  what was searched, where, and what is still to check.
- **Case-law coverage dataset** (#19). `matterkit/data/caselaw_coverage.json` (schema 2) says
  which courts' opinions CourtListener and the Caselaw Access Project hold and for which years,
  with CourtListener's bulk-data counts. `scripts/build_caselaw_coverage.py` and
  `scripts/cl_bulk_years.py` build it. This is the data behind corpuslaw.us/case-law, and #19
  is a true merge so that its source commit `142d044` stays on `main`.
- `CHANGELOG.md` and `docs/releases/`, one page per release.
- `scripts/run_tests.py` runs the test files side by side, one process per file: the same 2,312
  tests in about 80 seconds on four cores instead of about 265. `docs/reference.md` says how to
  run the tests, which it did not before.
- `tests/test_release.py` keeps the version in `plugin.yaml`, the Desktop panel's manifest and
  the MCP server equal, requires a changelog section and release notes for it, and checks that
  `provides_tools`, the tool schemas, their handlers, the manual and the tool count in words
  agree.

### Changed

- The README's CI badge is captioned: GitHub Actions is off for this repository, so the badge
  does not show a current test run. Run the tests yourself as shown in `docs/reference.md`.
- The tool count in the README and `plugin.yaml` is fourteen, with the two timeline tools.
- The stdio MCP server reports its version as 0.7.0, the plugin's version. It said 0.1.0.
- The manual's closing status line describes this version instead of the v0.1 dogfooding stage.

### Fixed

- The tests close every file they open, so a run with warnings on prints no
  `ResourceWarning: unclosed file`. Three tests wrote a JSON file without closing it and then
  read it back, which worked only because CPython happens to close it at once; they now close
  it first. `matter.py cite extract` closes the draft it reads.
- A link to the mcpservers.org listing in the README (#18).

## [0.6.0] (2026-11-10)

Cite checks went on by default, which changed what leaves the computer, so the version moved.
The later fixes in this section landed on `main` while it still said 0.6.0.

### Added

- **Cite checks on by default** for each case (#8): bare case cites go to CourtListener once a
  key is set, and bare statute and code cites go to Corpus. Each check can be turned off per
  case, and off sticks. `citation_guard local` keeps every cite on the computer.
- **An offline screen for impossible cites** (#8): `NO SUCH VOLUME`, `YEAR DOES NOT FIT` and
  `NO SUCH PAGE`, found with no lookup.
- **Case intake** (#16): a `case-intake` skill and the `/matter intake` checklist.

### Changed

- Plain text in the terminal instead of Markdown tables (#8).
- The README leads with the Hermes plugin, with screenshots from real runs (#15), and the full
  manual moved to `docs/reference.md` (#17). `plugin.yaml` has search tags (#17).

### Fixed

- A statute cite with a year in parentheses matches Corpus's section (#11).
- A statute cite matches Corpus across dash styles and from a subsection (#12).
- The practice case's Title VII statute reads `IN CORPUS`, and Corpus headings with no final full
  stop are read (#10).
- The quote guard refuses a shell command or code run that writes a bad quotation into a draft
  (#13).
- The case panel says why its backend returned 404 instead of always "backend is off" (#14).

## [0.5.0] (2026-11-05 to 2026-11-10)

### Added

- **Case pane for Hermes Desktop**: deadlines (passed first), a tamper badge for the case's files
  and how many facts have no source (`bb7d176`).
- **Source guard**: Hermes's file tools cannot change a registered source document or the case
  store (`2420458`), matched by file identity, not path (#4).
- **Quote guard**: a write into the case folder is refused when a quotation is not in the cited
  document or is on another page (`5dfa555`).
- **Deadline Watch**: a morning check that Hermes cron runs with no model, and a calendar export
  (`4318775`).
- **One switch per case for CourtListener lookups**, MCP search and cite lookup, and saved
  answers so a lookup is never sent twice (`8e7d1ad`, `72c57bd`).
- `/matter init` and `/matter demo`, skill registration and runnable grant commands (`d18914e`).
- A first run a newcomer can follow, and a practice case that shows the checks (#6, #7, #9).

### Fixed

- Search needs a CourtListener key and says so before sending; `--auth auto` with no key refuses
  before any request (`622884d`, `01bf1ae`).
- The citation check cannot be suppressed (#4).
- Search adapters load under Hermes's package name (#5).

## [0.4.0] (2026-11-05)

### Added

- **Native Hermes plugin** (`dbbc75d`): the citation guard under each reply, a deadline brief at
  the start of each session, `/matter`, and the case-folder tools.

## Before 0.4.0 (2026-08-23 to 2026-11-01)

The kit had no version field. Commit messages name v0.1 and v0.2; nothing names 0.3. The main
steps:

- **v0.1** (2026-08-23, `e0cb25a`): case store schema, the `matter.py` CLI, a stdio MCP server
  with no dependencies, the skill and a synthetic demo case.
- **v0.2** (2026-09-08, `140f888`, RFC 0001): local citation extraction with staged diligence and
  consent scaffolding, with no network.
- CourtListener search behind a grant a person activates at a terminal, and a Corpus search
  adapter (2026-09-09, `d38313d`, `864a3ce`).
- Counsel outreach shortlist with a send gate (RFC 0002, RFC 0003), PDF and Word text extraction
  with page and structure locators (RFC 0004, RFC 0005), matter capture and a custodian notice
  ledger (RFC 0006), 2026-09-09 to 2026-09-22.
- Brief check, which ties each cite, quote and fact in a draft to the case store; deadline
  updates with reasons; subcases; agent attribution (2026-10-23 to 2026-10-26).
- Court rules packs (2026-10-30), a local browser UI and a per-case amendment list (2026-10-31).
- Trigger deadlines that count Fed. R. Civ. P. 6(a) and 6(d) days, cite lookup against
  CourtListener, brief check on Word and PDF drafts, and the leak scanner (2026-11-01).

[0.7.0]: https://github.com/teakesdev/corpus-open/compare/1d5d29f...main
[0.6.0]: https://github.com/teakesdev/corpus-open/commit/c83d285
[0.5.0]: https://github.com/teakesdev/corpus-open/commit/bb7d176
[0.4.0]: https://github.com/teakesdev/corpus-open/commit/dbbc75d

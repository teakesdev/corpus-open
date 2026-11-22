# Hackathon feature shortlist (2026-11-06)

What four research agents found for the Nous Research Hermes plugin hackathon,
merged and ranked. None of this is built. Full reports, with sources:

- `research-prose.md`: tools for people without a lawyer
- `research-commercial.md`: lawyer-facing litigation and legal-AI tools
- `research-opendata.md`: free legal data and open-source tools
- `research-hermes.md`: hackathon rules, the Hermes plugin surface, competition

They are in a temporary session scratchpad on ty's computer, not in this
repository.

## Hackathon facts

Source: the official @NousResearch post of Nov 4, read in a browser.

- **Deadline:** Wed Nov 11, 5 PM Eastern (4 PM Central, 22:00 UTC).
- **To enter:** a PR adding `plugin-catalog/<name>.yaml` to
  NousResearch/hermes-agent, pointing at a public repo and a full 40-character
  commit; a demo video under 90 seconds posted on X tagging @NousResearch; both
  links sent through the Typeform. The form can go in once the PR is open.
- **Judging:** usefulness, reliability, polish and creativity. Judges "favor
  plugins that do more than a prompt or a skill could."
- **Prizes:** $20k in total: $10k, $5k, $2.5k, $1.5k and $1k, plus five
  runners-up. There are no category prizes.
- **Not stated:** eligibility, team size, IP terms, and whether the PR must be
  merged before judging.
- **Competition:** about 286 catalog PRs are open, and none is a litigation,
  court, self-represented or citation-check plugin.

## Blockers found

*Update 2026-11-07:* all three are fixed on public `main` (`fdc143d`). The plugin
(`plugin.yaml`, `hermes_plugin/`, `desktop/`, `dashboard/`) is there; `01bf1ae`
refuses keyless CourtListener search before sending; `d18914e` and `bd9a3ed`
make `/matter grant` print a runnable full-path command. The text below is the
original finding.

1. **The plugin isn't public.** `plugin.yaml`, `__init__.py`,
   `hermes_plugin/`, `desktop/` and `dashboard/` exist only on the local
   branches `feat/hermes-desktop-panel` and `feat/hermes-source-guard`. Neither
   branch is reviewed or pushed.
2. **CourtListener search now needs a key.** An anonymous request to the v4
   search API returned HTTP 401 (checked 2026-11-06). In `auto` mode with no
   saved key, `caselaw.search` goes anonymous, so it fails. The README still
   says "Search works without a key." The fix is small: refuse before sending
   and say a key is needed, then update the README.
3. **`/matter grant` prints `python3 matter.py ...` with no path,** so the
   command fails when run from any other folder.

## Ranked shortlist

Effort is in dev-days, tests included. Today through Wednesday afternoon is
about 4.5 days. The PR, catalog validation, CI and the video take about two of
those, which leaves about 2.5 days to build.

### Tier 0: required to enter (about 2 days)

- Land the Hermes plugin on public `main`: review, merge, push.
- Write the catalog YAML and open the PR. Add a banner, screenshots and a
  README that leads with Hermes.
- Record and post the 90-second video; the Hermes report has an outline.
- **First-run polish (0.5 day):** `/matter demo` and `/matter init`; register
  the `matter-kit` skill with `ctx.register_skill`; fix the path bug in
  `/matter grant`.
- Fix the CourtListener 401 handling (under 0.5 day).

### Tier 1: build these first

Each one either recurred across reports or uses something only a plugin can
do.

1. **Deadline Watch (about 1 day).** A Hermes cron job checks the case's
   deadlines and its tamper check, and sends alerts to Telegram for anything
   overdue, due soon, or with files that changed. Add an `.ics` calendar export
   with reminders 7, 3 and 1 days before; each title carries the deadline's
   confidence label, and deadlines still waiting on a triggering event stay
   out. Proposed by the Hermes and pro se reports. Builds on `deadlines`,
   `verify` and Hermes cron.
2. **Quote checker (about 1 day).** Before an agent writes a quote or a page
   cite, it checks the case documents and answers *exact*, *wrong page*, *not
   found* or *can't check*, with a word diff for near-misses. The Hermes
   `pre_tool_call` guard can run it on writes to drafts. Builds on brief
   check's text extraction (`Docs.text`, `_find`). Proposed by the commercial
   report. Demo line: "an agent that can't lie about the record."
3. **Cite-check receipt and stale-draft guard (about 1 day).** A one-page
   record for each checked draft: its hash; what each authority's lookup
   found, whether each quote was found, and "good law: NOT CHECKED"; and how
   many entries a person made versus an agent. The guard warns when a draft
   has changed since its last check. It writes no certification text. In
   Florida, Rule 2.515(d)(2), in effect since June 15, 2026, makes every
   signer, self-represented people included, stand behind the authorities
   they cite. Proposed by the pro se and commercial reports.
4. **Filing privacy preflight (about 1 day).** Finds what Fed. R. Civ. P. 5.2
   restricts (Social Security, birth-date, account and phone numbers, possible
   minors' names), masks the matches in its own report, and lists files it
   couldn't scan. It says that a clean scan is not proof of compliance.
   Proposed by the pro se and commercial reports.

### Tier 2: if time allows

- **Proposed deadlines from served papers or a scheduling order (1.5–2
  days).** Each proposal is tied to a quote with its page. Nothing is applied
  without approval, and a date mismatch is recorded, not corrected. Proposed
  by the pro se and commercial reports.
- **Offline impossible-citation screen (about 1 day).** Vendor a trimmed
  reporters-db (BSD-2, 133–324 KB). It checks the reporter, whether the year
  falls in the reporter's range, and the court type. It needs no network and
  widens the reporter list from about 30 to about 900.
- **Quote check against the cited opinion (1–1.5 days).** Uses
  static.case.law: no key and no quota. Coverage ends around 2018–2020, OCR
  noise means results are leads only, and the licence wording needs checking.
- **Court RSS docket watch (1–1.5 days).** Some federal courts publish free
  docket feeds that need no key. The agent tested S.D. Miss., S.D. Tex.,
  M.D. Fla. and N.D. Cal. A cron job filters the feed by docket number on this
  computer and proposes deadline triggers. A missing entry proves nothing.
- **Citation cards inside Hermes Desktop (1–1.5 days).**
- **Hearing-day sheet (1 day); gap list (1–1.5 days); worksheet for the other
  side's brief (1–1.5 days); timeline with date conflicts (1.5–2 days).**

### In scope now: judge analytics and case-value estimates

ty, 2026-11-06: the project now wants judge analytics and case-value
estimates. The research reports ruled them out using the "durable non-goals"
written on 2026-11-03: roadmap §4.4 and §6.3 in
`docs/2026-11-03_counsel-handoff-and-exchange-roadmap.md`, and RFC 0007
principle 5. Those documents were amended on 2026-11-06 (roadmap §4.4 and
§6.3, RFC 0007 §1 and, on 2026-11-07, §4.1). These
items are not yet sized for the hackathon. Free sources the reports name:
CourtListener judge, position and financial-disclosure data; RECAP dockets for
motion outcomes. They are rate-limited at 125 requests a day on a free key.

Still skipped, for practical reasons only: heavy-dependency tools (eyecite,
juriscraper, doctor) and LexNLP (AGPL).

## Suggested slate

| Day | Work |
| --- | --- |
| Fri–Sat | Fix the CourtListener 401, the first-run polish and the grant path bug. Get the plugin branches reviewed and landed. |
| Sat–Mon | Deadline Watch (cron, Telegram, `.ics`), then the quote checker. |
| Mon–Tue | Cite-check receipt and stale-draft guard if time allows. Catalog YAML, PR, screenshots. |
| Tue–Wed | Video, Typeform, buffer. Submit by Wednesday noon Central. |

# Cite checking by default: decisions and two extensions

**Status:** ty approved building `2026-11-09-cite-check-by-default-design.md` on 2026-11-10, with the approach recommended on 2026-11-09: that design with its proposed answers, plus the two extensions below. Not reviewed.
**Base:** `feat/first-run-newcomer` (`7b462ab`, pull request 6), not `origin/main`. The Hermes first-run texts this change rewrites live on that branch.
**Branch:** `feat/cite-check-on-by-default`.

Where this file and the design disagree, this file wins.

## Decisions on the design's open questions

1. **NOT FOUND is a BLOCK** (`cite-not-found`), as proposed.
2. **Existing cases get the default lazily**, on first use, as proposed.
3. **Review:** none assigned. Reviews are ty's to assign.

## Extension 1: statute and code cites are checked on Corpus by default

**Today.** The scope `corpus-search` is provider `corpus-search`, endpoint `corpuslaw.us`, payload type `query_text`, 50 a day. A person issues it with `consent activate --scope corpus-search --caller hermes`. The Hermes citation guard sends each statute cite its own parser found as a search query, at most `max_statute_checks` per reply. Corpus needs no key for this.

**Wanted.** A case where nobody has chosen anything gets its statute and code cites looked up on Corpus, the same way it gets its case cites looked up on CourtListener.

**The rule that must hold.** The default must not let any caller send free text to Corpus. Only a string the kit's own statute parser produced from the text being checked may go out under the default.

- Add a narrower consent scope for this: payload type `citation_strings` (sensitivity 0), used only by the cite-check path.
- `query_text` to Corpus stays opt-in, issued by a person, exactly as today.
- The check that the string is one parsed cite lives in the kit, not only in the plugin. A caller that hands the cite-check path a sentence is refused before anything is sent.
- The default, the `declined` record, "off sticks" and the lazy default work the same way as for case cites. A `declined` record is per provider.
- There is one documented command that turns the statute default off for a case and sticks. Extend the existing `consent` or `courtlistener` commands; add no new top-level command. The Hermes setting `citation_guard: local` still stops every network call.

**Check and report, do not build:** whether a statute cite repeated in later replies is sent again each time. Anonymous Corpus use is limited per month, so a default that re-sends the same cite would use it up. If there is no saved-answer path for statutes, say so in the report.

## Extension 2: in Hermes, the CourtListener key comes from the plugin's settings

`plugin.yaml` already declares `courtlistener_token` (a secret, env `COURTLISTENER_TOKEN`), and the guard already uses it first.

With cite checking on by default, the key is the only step left for a newcomer in Hermes. So:

- **No key is asked for in chat.** A key typed into a chat lands in the session's history. `/matter init` and `/matter demo` do not ask for one. The design's first-run questions (§2) apply to the terminal CLI and the browser UI only.
- **`/matter grant` is rewritten** for the new default. In plain words it says:
  - what is on by default and what is sent (bare case cites to CourtListener once a key is set; bare statute and code cites to Corpus);
  - how to add the free key: the link, and the exact way a person sets this plugin's `courtlistener_token` in Hermes, in the CLI and in Desktop. Find that in the Hermes source (read only), and name the real command or screen;
  - how to turn each check off for this case;
  - how to turn case law search on, which still takes a person at a terminal.
- **The "not looked up" reasons** change to match. With no key: case cites are listed but not looked up, and here is how to add the key. Statute cites are looked up already.
- **Texts that become false** are corrected: the manifest's description ("run only after a person grants them in the case folder") and its setting descriptions, the bundled skill (`skills/matter-kit/SKILL.md`), `demo-matter/README.md`, and the kit's usage text.
- **Version:** `plugin.yaml` goes from 0.5.0 to 0.6.0, because what leaves the computer by default changes.

## What leaves the computer by default after this change

State this in the manifest, in `/matter grant` and in the docs, in these words or plainer ones:

- Bare case cites such as `550 U.S. 544` go to CourtListener, only when a key is set.
- Bare statute and code cites such as `42 U.S.C. § 2000e-3(a)` go to Corpus (corpuslaw.us).
- Nothing else goes anywhere. Each check can be turned off per case, and off sticks.

## Order of work

Each slice ends with the full gate and its own commit or commits, so an earlier slice stands if a later one is not finished.

1. **Slice 1, must land.** Design tasks 1 and 2 (the default grant, the `declined` record, the `cite_check_on` / `search_on` split, the lazy default, `cases.init`), extension 1, and extension 2 with the Hermes texts and the manifest.
2. **Slice 2.** Design task 3 and 4: brief check's CourtListener phase, `--offline`, the MCP argument and descriptions, the UI panel text.
3. **Slice 3.** Design task 5 without `README.md`: the RFC 0001 amendment, the cite lookup spec revision, the usage text.

`README.md` is being rewritten for the hackathon entry on another branch. Do not edit it. Report which of its sentences this change makes false, with replacement text.

## Stop and report instead of deciding

- Anything that would send more than a bare parsed cite by default.
- Anything that would let MCP or an agent remove a `declined` record, or turn search on.
- Any change that loosens `consent.authorize` for grants a person issued.
- A need to edit `matterkit/versions.py` or the verify count axes. An outside observer pins both.

## Limits on the work itself

- Tests never touch the network; transports are injected. No live lookup is run.
- No real case folder is read or written, and the installed Hermes is not changed.
- Nothing is pushed.

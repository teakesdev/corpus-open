# Chambers contacts and judges' practices: design

**Date:** 2026-11-01 · **Author:** agent:claude-opus-5-5, directed by ty · **Tier:** Large because it adds a new per-case record format (`.matter/chambers.json`) that drafting and sending steps will rely on, and a wrong address sends a court paper to the wrong place.

**Status:** draft. ty approved writing it on 2026-10-31 ("start working on all of these changes"), as part of the rules-pack follow-ups. The intent below comes from that list and has not been confirmed in detail. No reviewer seat has seen it, and nothing is built.

## 1. Problem and intent

Some court papers go straight to chambers rather than through the Clerk; a district's e-filing procedures may say, for example, that proposed orders are e-mailed to chambers in Word format. Those addresses and each judge's own practices now sit in hand-written notes:
- a table copied from a procedures PDF, which can hold a misspelled address or a judge who has since taken inactive status;
- a practice remembered from a web page with no record of when it was read.

Nothing says when an address was last confirmed, or flags it once it is old.

**Intent:** record each chambers contact and each judge's practice with its source, the date it was confirmed and how. Mark anything unconfirmed or past its review window, as `rules stale` does for packs. The kit never sends anything: it shows a person what to check before they send.

## 2. Decision

- **New file `.matter/chambers.json`** (format 0.1). It is written atomically with an `events` row in one transaction, the same pattern as `rules.json`.
  - **`contacts`** entries: `judge`, `role` (district, magistrate or other), `email`, `phone`, `sources` (`[{ref, sha256}]`, registered documents only), `confirmed_on`, `confirmed_how`, `status` (`active|inactive`) and `review_after_days` (default 180).
  - **`practices`** entries: `judge`, `topic` (e.g. "proposed orders", "courtesy copies", "hearings"), `text` (short, quoted or paraphrased and labeled as which), `sources`, `confirmed_on`.
- **Commands:**
  - `chambers add`, `chambers list`, `chambers show <judge>` and `chambers checked`. `checked` records a fresh confirmation; its sources are re-hashed and an unregistered source is refused, as in `rules checked`.
  - `practice add` and `practice list`.
  - `chambers list` marks each entry `[CONFIRMED date]`, `[STALE]` or `[UNCONFIRMED]` and exits 1 when any active contact is not `CONFIRMED`.
- **Status rules:** staleness reuses `rules.checked_staleness` by composition. `rules.py` itself does not change.
- **Packet:** a "Court contacts" section appears only when entries exist, so packets for other matters are unchanged.
- **MCP:** `matter.chambers_list` (read-only).

## 3. Options considered

1. **A JSON record plus events, like `rules.json`.** Recommended: it uses the same validation, staleness and atomic-write code. It needs no schema change, and a person can read the file.
2. **New SQL tables.** Loses: a schema change and new query paths for a few rows per case, with nothing gained over (1) yet.
3. **Notes in `Research/`** (the status quo). Loses: nothing flags a stale or unconfirmed address.

## 4. Failure modes

| What could go wrong | How a test catches it |
|---|---|
| A misspelled or unconfirmed address is used | A new contact is `UNCONFIRMED` until `chambers checked` cites a source; `list` exits 1 |
| An old address reads as current | Review-window tests, mirroring `test_rules_checked.py` |
| A judge's inactive status is missed | An `inactive` contact is listed apart and never offered as the address |
| A source edited after recording is trusted | Every read re-hashes the sources; a changed source refuses, with nothing written |
| `chambers.json` and its event disagree | One-transaction tests with a failing events trigger, as in `test_deadline_lifecycle.py` |
| The tool sends mail | The module imports no mail or network library; a test reads its imports |
| Case data reaches the repo | Synthetic judges and addresses only (`example.test`) |

## 5. Cut line

- No sending, drafting of cover e-mails, or contacting chambers.
- No scraping court websites. A person captures the page, and the kit records it.
- No judge analytics or ruling forecasts. Those belong to the long-term forecasting work, not to this record.
- No web UI page in v1 (the read API can follow).

## 6. Tasks

1. **Record:** `matterkit/chambers.py` reads, validates, adds, checks and lists, with statuses through `rules.checked_staleness`. Proved by `tests/test_chambers.py`: refusals, transactions, re-hash and staleness.
2. **CLI:** `chambers …` and `practice …` in `matter.py`, with the usage lines and README. Proved by the CLI tests in `test_chambers.py`.
3. **Packet:** the "Court contacts" section. Proved by a test that a packet without entries is byte-identical.
4. **MCP:** `matter.chambers_list`. Proved by an MCP test.
5. **Case work,** outside the repo, with a backup first:
   - enter the assigned judges' contacts from an attached procedures pack's chambers table as `UNCONFIRMED`;
   - confirm them against the court's current page before anything is sent to chambers.

Gate: `python3 -m unittest discover -s tests -v`

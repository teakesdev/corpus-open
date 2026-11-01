# Deadlines that wait on a trigger: design

**Date:** 2026-11-01 · **Author:** agent:claude-opus-5-5, directed by ty · **Tier:** Large because it adds a store table and a date-counting contract that people will rely on for court deadlines, where a wrong date is costly.

**Status:** draft. ty approved writing it on 2026-10-31 ("start working on all of these changes"), as G5 of the rules-pack follow-ups. The intent below comes from that list and has not been confirmed in detail. No reviewer seat has seen it. Built on 2026-11-01 on branch `feat/deadline-triggers` after ty said "ok continue"; §7 records where the build departs from this draft.

## 1. Problem and intent

Many deadlines start only when something else happens:
- an amended complaint is filed within 7 days from entry of the order granting leave (L.U.Civ.R. 15);
- an answer to an amended pleading is due 14 days after it is served (Fed. R. Civ. P. 15(a)(3));
- a reply is due 7 days after the response is served (L.U.Civ.R. 7(b)(4)).

Until the trigger happens there is no date, and `deadlines.due_date` is `NOT NULL`. Today these live as hand-computed WATCH rows or in notes. When the trigger happens, someone must notice it, count the days and add a row. Each of those steps can be missed or miscounted.

**Intent:** record a waiting deadline with its rule and its count before the trigger happens. When it happens, record the date and the kind of service, and let the kit propose the due date, showing every counting step. The proposed date is always a working estimate, unverified, until a person checks it.

## 2. Decision

- **New table `deadline_triggers`.** Columns: `id`, `label`, `waits_on` (text, e.g. "order on the motion for leave"), `days`, `rule_source` (resolved through `rules.rule_source`, as `deadline add --rule` does), `owner`, `state` (`waiting|fired|withdrawn`), `fired_on`, `service`, `deadline_id`.
  - `CREATE TABLE IF NOT EXISTS` adds it to old stores. The `deadlines` table is unchanged.
- **Commands:**
  - `deadline trigger add <dir> --label L --waits-on TEXT --days N [--rule R] [--owner ours|theirs|court]`.
  - `deadline trigger fire <dir> <id> --on DATE --service mail|clerk|consented-other|email|ecf|hand|none --source rel@loc --reason TEXT`. It computes the date, inserts one `deadlines` row (`working-estimate/unverified`, with the trigger's rule and owner) and records an `events` row with the counting steps, all in one transaction.
  - `deadline trigger list` and `deadline trigger withdraw <id> --reason`.
  - `deadline list` adds a "waiting on" section for waiting triggers, after the dated rows.
- **The counting contract** (`matterkit/triggers.py`, pure functions):
  - **Fed. R. Civ. P. 6(a)(1):** exclude the trigger day and count every day. If the last day is a Saturday, Sunday or legal holiday, run to the next day that is none of those.
  - **Fed. R. Civ. P. 6(d):** add 3 days only for service by mail, by leaving the paper with the clerk, or by other means consented to (`5(b)(2)(C)`, `(D)`, `(F)`), never for e-mail or ECF. The 3 days are added after the 6(a) period ends, and the weekend and holiday rule then applies again.
  - **Legal holidays:**
    - the federal holidays of 5 U.S.C. § 6103, computed per year with its observed-day rule;
    - any days in a case's `.matter/holidays.json` (state holidays under 6(a)(6)(C), and court closures), each with a source.
  - The steps record which list was used. A year that no list covers is refused.
- **MCP:** `matter.trigger_list` (read-only) and `matter.trigger_fire`.

## 3. Options considered

1. **A separate trigger table that fires into a normal deadline.** Recommended: every reader of `deadlines` keeps its dated rows, and the trail shows when the trigger fired and how the date was counted.
2. **Deadlines with an optional date.** Loses: `due_date` is `NOT NULL`, which SQLite cannot relax in place, and the list, OVERDUE, packet, status and subcase code all assume a date.
3. **Notes only.** Loses: nothing reminds anyone or counts the days, which is the status quo.

## 4. Failure modes

| What could go wrong | How a test catches it |
|---|---|
| A weekend or holiday rollover is miscounted | Table-driven tests of 6(a)(1) on a Friday, a Saturday end, a Monday holiday and a year-end crossing |
| 3 days are added for e-mail or ECF service | 6(d) tests for each service kind; `email` and `ecf` add 0 |
| 3 days are added before the 6(a) rollover instead of after it | A case whose 6(a) end falls on a Saturday, checked against hand-counted dates |
| The holiday list is missing a year or is wrong | A year that no list covers refuses to fire; the § 6103 observed-day tests cover Saturday and Sunday holidays |
| Firing changes an existing deadline, or fires twice | Fire inserts one new row; a second fire is refused with nothing written |
| Fired from an unregistered or changed source | The same re-hash refusal as `deadline update` |
| A computed date is read as verified | The row is `working-estimate/unverified`, and the event says "computed, not checked" |
| An old store breaks | A migration test on a store without the table |

## 5. Cut line

- No detecting triggers from the docket, PACER or e-mail. A person fires each trigger.
- No backward counting ("7 days before the hearing", 6(a)(5)) in v1. Such a trigger is refused, with a note.
- No end-of-day cutoffs. 6(a)(4) differs for paper filers (when the clerk's office closes), so the steps state it rather than compute it.
- No built-in state holiday list. Each case's list cites its source.
- No calendar export and no reminders sent.

## 6. Tasks

1. **Counting:** `matterkit/triggers.py` (`count(start, days, service, holidays)` with its steps) and `federal_holidays(year)`. Proved by `tests/test_trigger_count.py`.
2. **Table:** `deadline_triggers` in `store.SCHEMA`, plus `add`, `fire` and `withdraw` with events. Proved by `tests/test_triggers.py`: transactions, refusals and an old store.
3. **CLI:** `deadline trigger …` and the "waiting on" section in `deadline list`. Proved by the CLI tests in `test_triggers.py`; the existing `test_deadline_lifecycle.py` stays unchanged.
4. **MCP:** `matter.trigger_list` and `matter.trigger_fire`. Proved by MCP tests in `test_triggers.py`.
5. **README** and a short entry in the skill.
6. **Case work,** outside the repo, with a backup first:
   - turn hand-computed waiting rows into triggers;
   - add the district's state holidays to `holidays.json`, with their source.

Gate: `python3 -m unittest discover -s tests -v`

## 7. Revision 2 (2026-11-01): as built

ty's "ok continue" (2026-11-01) started the build from §6. No reviewer seat has seen the draft or the code. The build departs from the draft in these places:

| Draft | As built, and why |
|---|---|
| `deadline trigger add\|fire\|list\|withdraw` | Top-level `trigger add\|fire\|list\|withdraw`. The `deadline` command takes one positional subcommand followed by the case folder, and nesting a second level would change the order of its arguments. |
| State holidays and closures in `.matter/holidays.json` | Two store tables, written by commands. `holidays` holds `day`, `name`, `kind` (`state`, `declared` or `closure`) and its sources. `holiday_years` records that a year's state holidays are complete, with the list they came from. Each command refuses an unregistered or changed source and writes its `events` row in the same transaction. A count refuses any year not in `holiday_years`, which is how "a year that no list covers is refused" is decided. |
| One `events` row on fire | Two rows in one transaction. The first is the `deadline … added` row, with the same shape as `deadline add`. The second is `trigger … fired`, with the date, service, reason, sources and every counting step. |
| Sources checked as `deadline update` checks them | Stricter. `fire`, `holiday add` and `holiday year` also re-hash each local source and refuse one that is missing or changed since it was registered. The check moved into `deadlines.resolve_sources`, and `deadline update` keeps its old behaviour (`rehash=False`). |
| (not in draft) | `fire --dry-run` shows the count and writes nothing. |
| (not in draft) | `fire` refuses an `--on` date after today, except with `--dry-run`. Fixed after the build: the first version refused a future dry run too, though its error said to use one, which blocked previewing an event still ahead. |
| (not in draft) | Observed days. When a weekday observed for a weekend holiday (5 U.S.C. § 6103(b)) decides the date, the steps also give the date if only the statutory day counts. 6(a)(6)(A) names the § 6103 days without saying whether the observed day counts. |
| (not in draft) | Declared days (6(a)(6)(B)) are recorded the same way as state holidays (`--kind declared`), not looked up. |

Corrected: §1's first example cited L.U.Civ.R. 7(b)(2) for filing an amended pleading after leave is granted; the rule is L.U.Civ.R. 15 (7 days from entry of the order).

Unchanged from the draft: the `deadline_triggers` columns, `days > 0` (backward counts are refused), `working-estimate/unverified` rows, no built-in state list, no computed hour (the steps state 6(a)(4)), and the MCP tools `matter.trigger_list` and `matter.trigger_fire`.

Revision 2 has not been reviewed.

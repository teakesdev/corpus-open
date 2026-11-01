"""Deadlines that wait on an event (G5): "7 days after the response is served".

Before the event, a trigger records the period, its rule and who must act. When the event
happens, `fire` records its date and how service was made, counts the days under Fed. R.
Civ. P. 6(a)(1) and 6(d), and inserts an ordinary deadline row, working-estimate/unverified,
with every counting step in its event. Design:
docs/superpowers/specs/2026-11-01-trigger-deadlines-design.md.

Legal holidays are the federal holidays of 5 U.S.C. § 6103, computed with the weekday observed
for one that falls on a weekend, plus the days a matter records with a source: state holidays
(6(a)(6)(C)), days the President or Congress declares (6(a)(6)(B)) and clerk's-office closures
(6(a)(3)). A count refuses to look at a day in a year whose state holidays are not recorded as
complete (`holiday year`), because a missing state holiday makes the date too early or too late.
"""
import json
from datetime import date, timedelta

from . import deadlines
from .deadlines import DeadlineError, OWNERS, _event, parse_date
from .store import new_id, now_iso

STATES = ("waiting", "fired", "withdrawn")
# service kind -> (how it was made, its rule, days Fed. R. Civ. P. 6(d) adds)
SERVICES = {
    "mail": ("by mail", "Fed. R. Civ. P. 5(b)(2)(C)", 3),
    "clerk": ("by leaving it with the court clerk", "Fed. R. Civ. P. 5(b)(2)(D)", 3),
    "consented-other": ("by other means consented to in writing", "Fed. R. Civ. P. 5(b)(2)(F)", 3),
    "email": ("by e-mail consented to in writing", "Fed. R. Civ. P. 5(b)(2)(E)", 0),
    "ecf": ("through the court's electronic-filing system", "Fed. R. Civ. P. 5(b)(2)(E)", 0),
    "hand": ("by hand, or by leaving it at an office or dwelling", "Fed. R. Civ. P. 5(b)(2)(A)-(B)", 0),
    "none": (None, None, 0),  # the period runs from the event itself (an order's entry), not from service
}
HOLIDAY_KINDS = {"state": "Fed. R. Civ. P. 6(a)(6)(C)", "declared": "Fed. R. Civ. P. 6(a)(6)(B)",
                 "closure": "Fed. R. Civ. P. 6(a)(3)"}
_FIXED = ((1, 1, "New Year's Day"), (6, 19, "Juneteenth National Independence Day"), (7, 4, "Independence Day"),
          (11, 11, "Veterans Day"), (12, 25, "Christmas Day"))
_FLOATING = ((1, 0, 3, "Birthday of Martin Luther King, Jr."), (2, 0, 3, "Washington's Birthday"),
             (5, 0, -1, "Memorial Day"), (9, 0, 1, "Labor Day"), (10, 0, 2, "Columbus Day"),
             (11, 3, 4, "Thanksgiving Day"))  # (month, weekday Mon=0, nth or -1 for the last, name)


class TriggerError(DeadlineError):
    """A refused trigger or holiday operation, or a count that cannot be made; nothing was written."""


# --------------------------------------------------------------------------
# Counting (pure)
# --------------------------------------------------------------------------
def _nth_weekday(year, month, weekday, n):
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def federal_holidays(year: int) -> dict:
    """{date: name} for the 5 U.S.C. § 6103(a) holidays in `year`, plus the weekday observed for one on
    a weekend (§ 6103(b): Saturday -> the Friday before, Sunday -> the Monday after). That includes a
    Friday Dec. 31 observing the next year's New Year's Day."""
    out = {}
    for y in (year, year + 1):
        for month, day, name in _FIXED:
            if name.startswith("Juneteenth") and y < 2021:
                continue
            d = date(y, month, day)
            if d.year == year:
                out[d] = name
            obs = d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else None
            if obs and obs.year == year:
                out[obs] = f"{name} (observed)"
    for month, weekday, n, name in _FLOATING:
        out[_nth_weekday(year, month, weekday, n)] = name
    return out


class Calendar:
    """The days a period cannot end on (6(a)(1)(C)): weekends, the § 6103 holidays and the days a
    matter recorded. `recorded` is {date: (name, kind)}; `years` is {year: (jurisdiction, [sources])}
    for each year whose state holidays are recorded as complete."""

    def __init__(self, recorded, years, observed=True):
        self.recorded, self.years, self.observed = dict(recorded), dict(years), observed

    def statutory_only(self):
        """The same calendar without the weekday observed for a weekend holiday."""
        return Calendar(self.recorded, self.years, observed=False)

    def closed(self, d: date):
        """Why `d` is not a day a period can end on, or None."""
        if d.year not in self.years:
            raise TriggerError(f"the count reaches {d.isoformat()}, and this matter has no complete list of state "
                               f"holidays for {d.year}. Record them (holiday add) and say the list is complete "
                               f"(holiday year --year {d.year}); nothing was written")
        if d.weekday() >= 5:
            return "a weekend day"
        name = federal_holidays(d.year).get(d)
        if name and (self.observed or not name.endswith("(observed)")):
            return f"a legal holiday, {name} (5 U.S.C. § 6103; Fed. R. Civ. P. 6(a)(6)(A))"
        if d in self.recorded:
            name, kind = self.recorded[d]
            if kind == "closure":
                return f"a day the clerk's office was inaccessible, {name} ({HOLIDAY_KINDS[kind]})"
            where = f"{self.years[d.year][0]}; " if kind == "state" else ""
            return f"a legal holiday, {name} ({where}{HOLIDAY_KINDS[kind]})"
        return None


def _d(d: date) -> str:
    return f"{d.strftime('%A')} {d.isoformat()}"


def _roll(d, cal, steps, seen, rule):
    while True:
        seen.add(d.year)
        why = cal.closed(d)
        if not why:
            break
        steps.append(f"{_d(d)} is {why}, so the period runs on to the next day ({rule}).")
        d += timedelta(days=1)
    steps.append(f"{_d(d)} is not a Saturday, Sunday or legal holiday, so the period can end on it ({rule}).")
    return d


def _count(start, days, service, cal, seen):
    steps = [f"The trigger day, {_d(start)}, is not counted (Fed. R. Civ. P. 6(a)(1)(A)).",
             f"Counting every day, weekends and holidays included, {days} days run to "
             f"{_d(start + timedelta(days=days))} (Fed. R. Civ. P. 6(a)(1)(B))."]
    end = _roll(start + timedelta(days=days), cal, steps, seen, "Fed. R. Civ. P. 6(a)(1)(C)")
    how, rule, extra = SERVICES[service]
    if extra:
        end += timedelta(days=extra)
        steps.append(f"Service was {how} ({rule}), so {extra} days are added after the period would otherwise "
                     f"end under Rule 6(a) (Fed. R. Civ. P. 6(d)): {_d(end)}.")
        end = _roll(end, cal, steps, seen, "Fed. R. Civ. P. 6(a)(1)(C), applied again after the 3 days")
    elif rule:
        steps.append(f"Service was {how} ({rule}), so no days are added: Fed. R. Civ. P. 6(d) adds 3 days only "
                     "for service under Rule 5(b)(2)(C), (D) or (F).")
    else:
        steps.append("The period runs from the event itself, not from service, so Fed. R. Civ. P. 6(d) adds nothing.")
    return end, steps


def count(start: date, days, service: str, cal: Calendar) -> tuple:
    """(last day, steps) for a period of `days` days after `start`, served as `service`.

    Fed. R. Civ. P. 6(a)(1): exclude the trigger day, count every day, and if the last day is a
    Saturday, Sunday or legal holiday run to the next day that is none of those. 6(d): for service
    by mail, by leaving with the clerk, or by other consented means, add 3 days after that, then
    apply the same rule again. Refuses a period that is not a whole number above 0 (counting back
    from an event is not supported) and a day in a year whose state holidays are not recorded."""
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise TriggerError(f"days must be a whole number above 0 (got {days!r}); counting back from an event "
                           "is not supported")
    if service not in SERVICES:
        raise TriggerError(f"unknown service {service!r} — use one of: {', '.join(SERVICES)}")
    seen = set()
    end, steps = _count(start, days, service, cal, seen)
    if cal.observed:
        alt, _ = _count(start, days, service, cal.statutory_only(), set())
        if alt != end:
            steps.append(f"A day counted above as a legal holiday is the weekday observed for a holiday that falls on "
                         f"a weekend (5 U.S.C. § 6103(b)). If only the statutory date is a holiday, the last day is "
                         f"{_d(alt)}; acting by then is safe either way.")
    steps.append(f"Last day: {_d(end)}. It ends at midnight in the court's time zone for electronic filing, or when "
                 "the clerk's office closes for filing on paper (Fed. R. Civ. P. 6(a)(4)); this count does not set the "
                 "hour, and a local rule or order may set a different time.")
    state = "; ".join(f"{cal.years[y][0]} {y} (from {', '.join(cal.years[y][1])})" for y in sorted(seen))
    steps.append(f"Legal holidays checked: the federal holidays of 5 U.S.C. § 6103 and the state holidays recorded "
                 f"for this matter, {state}. Days the President or Congress declares (6(a)(6)(B)) and "
                 "clerk's-office closures (6(a)(3)) count only when recorded.")
    return end, steps


# --------------------------------------------------------------------------
# The store: triggers and recorded holidays
# --------------------------------------------------------------------------
def calendar(conn) -> Calendar:
    recorded = {date.fromisoformat(r["day"]): (r["name"], r["kind"]) for r in conn.execute("SELECT * FROM holidays")}
    years = {r["year"]: (r["jurisdiction"], json.loads(r["sources"]))
             for r in conn.execute("SELECT * FROM holiday_years")}
    return Calendar(recorded, years)


def _require(value, what):
    value = (value or "").strip() if isinstance(value, str) or value is None else value
    if not value:
        raise TriggerError(f"{what} is required and must not be blank")
    return value


def _sources(conn, case_dir, sources):
    sources = list(sources or [])
    if not sources:
        raise TriggerError("at least one --source (rel_path@locator of a registered document) is required")
    try:
        return deadlines.resolve_sources(conn, case_dir, sources, rehash=True)
    except DeadlineError as e:
        raise TriggerError(str(e)) from None


def add(conn, label, waits_on, days, by, rule=None, owner=None) -> str:
    """Record a deadline that waits on an event, with its 'added' event. Returns the trigger id."""
    label, waits_on = _require(label, "--label"), _require(waits_on, "--waits-on")
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise TriggerError(f"--days must be a whole number above 0 (got {days!r}); counting back from an event "
                           "is not supported")
    if owner is not None and owner not in OWNERS:
        raise TriggerError(f"invalid owner {owner!r} — use one of: {', '.join(OWNERS)}")
    tid = new_id("t_")
    text = f"trigger {tid} added: {label!r}, {days} days after: {waits_on}"
    text += (f"; rule: {rule}" if rule else "") + (f"; owner: {owner}" if owner else "")
    with conn:
        conn.execute("INSERT INTO deadline_triggers (id,label,waits_on,days,rule_source,owner,created_by,created_at)"
                     " VALUES (?,?,?,?,?,?,?,?)", (tid, label, waits_on, days, rule, owner, by, now_iso()))
        _event(conn, text, by, [])
    return tid


def _waiting(conn, trigger_id):
    row = conn.execute("SELECT * FROM deadline_triggers WHERE id=?", (trigger_id,)).fetchone()
    if row is None:
        raise TriggerError(f"unknown trigger id {trigger_id!r}")
    if row["state"] == "fired":
        raise TriggerError(f"trigger {trigger_id} already fired on {row['fired_on']} as deadline {row['deadline_id']}; "
                           "change that deadline with deadline update")
    if row["state"] != "waiting":
        raise TriggerError(f"trigger {trigger_id} is {row['state']}")
    return row


def fire(conn, case_dir, trigger_id, on, service, sources, reason, by, today=None, dry_run=False) -> tuple:
    """The event happened on `on`, served as `service`: count the period and insert its deadline.

    Returns (deadline id or None for a dry run, last day, steps). The deadline row, the trigger's
    new state and both events commit together or not at all; a dry run checks and counts but
    writes nothing and needs no source or reason."""
    row = _waiting(conn, trigger_id)
    when = parse_date(on)
    if when is None:
        raise TriggerError(f"--on must be a valid date, YYYY-MM-DD (got {on!r})")
    if when > (today or date.today()):
        raise TriggerError(f"--on {on} is after today; record the event once it has happened, or use --dry-run")
    due, steps = count(when, row["days"], service, calendar(conn))
    if dry_run:
        return None, due, steps
    reason = _require(reason, "--reason")
    refs, parents = _sources(conn, case_dir, sources)
    did = new_id("d_")
    added = (f"deadline {did} added: {row['label']!r} due {due.isoformat()} (working-estimate/unverified)"
             + (f"; rule: {row['rule_source']}" if row["rule_source"] else "")
             + (f"; owner: {row['owner']}" if row["owner"] else "") + f"; from trigger {trigger_id}")
    fired = (f"trigger {trigger_id} {row['label']!r} fired: {row['waits_on']} on {on}, service: {service}; "
             f"deadline {did} due {due.isoformat()}, computed and not checked; reason: {reason}; count: "
             + " ".join(steps))
    from . import subcase  # lazy: subcase imports deadlines
    with conn:
        for pdoc in parents.values():
            subcase.register_parent_ref(conn, pdoc)
        conn.execute("INSERT INTO deadlines (id,label,due_date,rule_source,confidence,verification_status,owner)"
                     " VALUES (?,?,?,?,?,?,?)", (did, row["label"], due.isoformat(), row["rule_source"],
                                                 "working-estimate", "unverified", row["owner"]))
        cur = conn.execute("UPDATE deadline_triggers SET state='fired', fired_on=?, service=?, deadline_id=? "
                           "WHERE id=? AND state='waiting'", (on, service, did, trigger_id))
        if cur.rowcount != 1:
            raise TriggerError(f"trigger {trigger_id} changed while it was being fired; nothing was written")
        _event(conn, added, by, [])
        _event(conn, fired, by, refs)
    return did, due, steps


def withdraw(conn, trigger_id, reason, by) -> str:
    """Stop waiting: the event will not happen or no longer starts a deadline."""
    reason = _require(reason, "--reason")
    row = _waiting(conn, trigger_id)
    text = f"trigger {trigger_id} {row['label']!r}: state waiting→withdrawn; reason: {reason}"
    with conn:
        conn.execute("UPDATE deadline_triggers SET state='withdrawn' WHERE id=?", (trigger_id,))
        _event(conn, text, by, [])
    return text


def select(conn, include_closed=False, owner=None):
    where = ([] if include_closed else ["state='waiting'"]) + (["owner=?"] if owner else [])
    return conn.execute("SELECT * FROM deadline_triggers" + (" WHERE " + " AND ".join(where) if where else "")
                        + " ORDER BY created_at, rowid", [owner] if owner else []).fetchall()


def list_line(r) -> str:
    line = f"{r['id']}  {r['label']}: {r['days']} days after {r['waits_on']}  [{r['rule_source'] or '—'}]"
    if r["owner"]:
        line += f"  owner={r['owner']}"
    if r["state"] == "fired":
        line += f"  fired {r['fired_on']} ({r['service']}) → {r['deadline_id']}"
    elif r["state"] != "waiting":
        line += f"  state={r['state']}"
    return line


def holiday_add(conn, case_dir, on, name, kind, sources, by) -> str:
    """Record a day a period cannot end on, beyond the federal holidays, with its source."""
    d = parse_date(on)
    if d is None:
        raise TriggerError(f"--on must be a valid date, YYYY-MM-DD (got {on!r})")
    name = _require(name, "--name")
    if kind not in HOLIDAY_KINDS:
        raise TriggerError(f"invalid kind {kind!r} — use one of: {', '.join(HOLIDAY_KINDS)}")
    refs, parents = _sources(conn, case_dir, sources)
    if conn.execute("SELECT 1 FROM holidays WHERE day=? AND kind=?", (on, kind)).fetchone():
        raise TriggerError(f"a {kind} day is already recorded on {on}")
    text = f"holiday recorded: {on} {name!r} ({kind}, {HOLIDAY_KINDS[kind]})"
    from . import subcase
    with conn:
        for pdoc in parents.values():
            subcase.register_parent_ref(conn, pdoc)
        conn.execute("INSERT INTO holidays (day,name,kind,sources,recorded_by,created_at) VALUES (?,?,?,?,?,?)",
                     (on, name, kind, json.dumps(refs), by, now_iso()))
        _event(conn, text, by, refs)
    return text


def holiday_year(conn, case_dir, year, jurisdiction, sources, by) -> str:
    """Say that every state holiday of `year` is recorded, citing the list it came from."""
    if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 2200:
        raise TriggerError(f"--year must be a year such as 2026 (got {year!r})")
    jurisdiction = _require(jurisdiction, "--jurisdiction")
    refs, parents = _sources(conn, case_dir, sources)
    if conn.execute("SELECT 1 FROM holiday_years WHERE year=?", (year,)).fetchone():
        raise TriggerError(f"the state holidays for {year} are already recorded as complete")
    text = f"holiday list for {year} recorded as complete: {jurisdiction} state holidays"
    from . import subcase
    with conn:
        for pdoc in parents.values():
            subcase.register_parent_ref(conn, pdoc)
        conn.execute("INSERT INTO holiday_years (year,jurisdiction,sources,recorded_by,created_at) VALUES (?,?,?,?,?)",
                     (year, jurisdiction, json.dumps(refs), by, now_iso()))
        _event(conn, text, by, refs)
    return text


def holiday_lines(conn, year: int) -> list:
    cal = calendar(conn)
    days = {d: f"{n} (federal, 5 U.S.C. § 6103)" for d, n in federal_holidays(year).items()}
    for d, (n, kind) in cal.recorded.items():
        if d.year == year:
            days[d] = days.get(d, "") + ("; " if d in days else "") + f"{n} ({kind}, {HOLIDAY_KINDS[kind]})"
    head = (f"{year}: state holidays recorded as complete for {cal.years[year][0]} "
            f"(from {', '.join(cal.years[year][1])})" if year in cal.years else
            f"{year}: the state holidays are NOT recorded as complete, so no count will use this year")
    return [head] + [f"{_d(d)}  {days[d]}" for d in sorted(days)]

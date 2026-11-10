"""Offline screen for case cites that cannot exist as written.

AI models invent cases, and an invented cite often names a reporter volume that was never printed.
This module answers one narrow question with no network and no key: given a case cite's volume,
reporter, page and (when the draft gives one) the year in its parenthetical, is that cite
impossible? It flags only what is certain from the reporter table below:

- a volume past the end of a closed series (F.3d ended at volume 999);
- a volume past the ceiling of a series still being printed, where the table holds a confirmed
  count. The ceiling grows with today's date (count + allowance x (years since the count + 1)),
  so an old table never flags a real new volume. Each allowance is at least twice the series'
  fastest yearly growth, and the extra year covers advance sheets numbered past the last bound
  volume and a count that lagged;
- a year more than one year before the series began or after a closed series ended (the margin
  covers an opinion published late), or a year that has not come yet;
- volume 0 or page 0.

It never judges a case name, never matches a volume to a year, and an unknown reporter always
returns nothing: a wrong "cannot exist" is worse than a missed one. CourtListener, when a lookup
runs, outranks this table.
"""
from __future__ import annotations

import datetime as _dt
import logging
import math
import re
from dataclasses import dataclass

_log = logging.getLogger(__name__)

TABLE_CHECKED = _dt.date(2026, 11, 10)  # when the rows below were last checked against their sources

NO_SUCH_VOLUME, NO_SUCH_PAGE, YEAR_DOES_NOT_FIT = "NO SUCH VOLUME", "NO SUCH PAGE", "YEAR DOES NOT FIT"
LABELS = (NO_SUCH_VOLUME, YEAR_DOES_NOT_FIT, NO_SUCH_PAGE)
END = "This cite is likely made up or mistyped. Check it by hand."
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")


@dataclass(frozen=True)
class Series:
    name: str  # spelled as citelookup.CANONICAL_REPORTERS spells it
    first_year: int  # the earliest year any source gives for the series' first opinions
    last_year: int | None = None  # a closed series: the latest year any source gives
    last_volume: int | None = None  # a closed series: its last volume
    count: int | None = None  # a series still printed: volumes known on `as_of`
    as_of: _dt.date | None = None
    allowance: int | None = None  # volumes a year the ceiling grows by
    growth: int | None = None  # the series' fastest yearly growth, as far as the sources show
    source: str = ""


_d = _dt.date
# Sources, read 2026-11-10: Free Law Project's reporters-db (reporters_db/data/reporters.json) for
# series dates; Wikipedia's "National Reporter System" table (NRS) and reporter articles, a law
# library or catalog record, or the publisher for volume counts. Where sources disagree on a year,
# the row keeps the earliest start and the latest end, so the check flags less.
SERIES = (
    Series("U.S.", 1754, count=606, as_of=_d(2025, 7, 1), allowance=12, growth=6,
           source="supremecourt.gov U.S. Reports page: preliminary print 606 is the 2024 Term; Wikipedia "
                  "'United States Reports, volume 1': cases from 1754 (reporters-db's 1875 starts the renumbered "
                  "series too late for 1-90 U.S.); 3-6 volumes a year"),
    Series("S. Ct.", 1882, count=144, as_of=_d(2024, 7, 1), allowance=3, growth=1,
           source="CiNii record of West's interim edition: v. 144 is October Term 2023, one volume a term; "
                  "NRS and reporters-db: 1882-"),
    Series("L. Ed.", 1790, 1956, 100,
           source="reporters-db and the Cardiff Index to Legal Abbreviations: 1790-1956; library catalog "
                  "record of the set: 100 volumes"),
    Series("L. Ed. 2d", 1956,
           source="reporters-db: 1956-; Wikipedia 'Lawyers' Edition': starts at 352 U.S. (1956). No public "
                  "volume count found, so no ceiling"),
    Series("F.", 1880, 1924, 300,
           source="Wikipedia 'Federal Reporter' and NRS: 1880-1924, 300 volumes; reporters-db: 1880-1924"),
    Series("F.2d", 1924, 1993, 999,
           source="Wikipedia 'Federal Reporter' and NRS: 1924-1993, 999 volumes; reporters-db: 1924-1993"),
    Series("F.3d", 1993, 2021, 999,
           source="Wikipedia 'Federal Reporter' and NRS: 1993-2021, 999 volumes; reporters-db: from 1993"),
    Series("F.4th", 2021, count=161, as_of=_d(2025, 11, 26), allowance=90, growth=41,
           source="Wikipedia 'Federal Reporter': began June 2021; 161 F.4th 671 is an opinion of 2025-11-26 "
                  "(Civil Rights Litigation Clearinghouse); volume 125 holds January 2025 opinions (OpenJurist): "
                  "about 35-41 volumes a year"),
    Series("F. Supp.", 1932, 1998, 999,
           source="Wikipedia 'Federal Supplement': 1933-1998, 999 volumes; NRS: 1932-1997; reporters-db: "
                  "1932-1988"),
    Series("F. Supp. 2d", 1988, 2014, 999,
           source="Wikipedia 'Federal Supplement': 1998-2014, 999 volumes; NRS: 1997-2014; reporters-db: "
                  "1988-2014-08-21"),
    Series("F. Supp. 3d", 2014, count=633, as_of=_d(2023, 8, 1), allowance=200, growth=100,
           source="Wikipedia 'Federal Supplement': 633 volumes as of August 2023; 792 F. Supp. 3d 107 is a "
                  "July 2025 opinion (Clearinghouse): about 80 volumes a year; reporters-db: 2014-03-04-"),
    Series("F. App'x", 2001, 2021, 861,
           source="Wikipedia 'Federal Appendix' and NRS: 2001-2021, 861 volumes; Hofstra Law Library: ceased "
                  "with volume 861 in 2021; reporters-db: from 2001"),
    Series("F.R.D.", 1938,
           source="Michigan Law Library and the Online Books Page: v. 1 no. 1, January 1940; NRS: 1941-; "
                  "USLegal: decisions from 1938 (reporters-db's 2001 start is not used). No public volume "
                  "count found, so no ceiling"),
    Series("B.R.", 1979,
           source="reporters-db: 1979-; NRS: 1980-. No public volume count found, so no ceiling"),
    Series("A.2d", 1938, 2010, 999,
           source="Wikipedia 'Atlantic Reporter': 1939-2010, 999 volumes; NRS: 1938-2010; reporters-db: "
                  "1938-2010"),
    Series("A.3d", 2010,
           source="Wikipedia 'Atlantic Reporter', NRS and reporters-db: 2010-. No confirmed count, so no "
                  "ceiling"),
    Series("N.E.", 1884, 1936, 200,
           source="NRS: 1885-1936, 200 volumes; reporters-db: 1884-1936"),
    Series("N.E.2d", 1936, 2013, 999,
           source="NRS: 1936-2012, 999 volumes; reporters-db: 1936-2013-10-24"),
    Series("N.E.3d", 2012,
           source="NRS: 2012-; reporters-db: 2013-10-24-. No confirmed count, so no ceiling"),
    Series("N.W.", 1879, 1942, 300,
           source="NRS: 1879-1941, 300 volumes; reporters-db: 1880-1942"),
    Series("N.W.2d", 1941, 2024, 999,
           source="reporters-db: 1942-2023, then N.W.3d from 2024; 999 N.W.2d holds opinions to 2024-01-12 "
                  "and 1 N.W.3d 467 is a 2024 opinion; no volume 1000 found; NRS: 1941-"),
    Series("P.2d", 1931, 2000, 999,
           source="Wikipedia 'Pacific Reporter': 999 volumes, June 1931-March 2000; NRS and reporters-db: "
                  "1931-2000"),
    Series("P.3d", 2000,
           source="Wikipedia 'Pacific Reporter': began May 2000; NRS and reporters-db: 2000-. No confirmed "
                  "count, so no ceiling"),
    Series("S.E.", 1887, 1939, 200,
           source="NRS: 1887-1938, 200 volumes; reporters-db: 1887-1939"),
    Series("S.E.2d", 1938,
           source="NRS: 1938-; reporters-db: 1939-. No confirmed count, so no ceiling"),
    Series("S.W.", 1886, 1928, 300,
           source="NRS: 1886-1928, 300 volumes; reporters-db: 1886-1928"),
    Series("S.W.2d", 1928, 1999, 999,
           source="NRS: 1928-1999, 999 volumes; reporters-db: 1928-1999"),
    Series("S.W.3d", 1999,
           source="NRS and reporters-db: 1999-. No confirmed count, so no ceiling"),
    Series("So.", 1886, 1941, 200,
           source="NRS: 1887-1940, 200 volumes; Wikipedia 'Southern Reporter': 1887-1941; reporters-db: "
                  "1886-1941"),
    Series("So. 2d", 1940, 2009, 999,
           source="NRS: 1940-2009, 999 volumes; Wikipedia 'Southern Reporter': 1941-2008; reporters-db: "
                  "1941-2008"),
    Series("So. 3d", 2008,
           source="Wikipedia 'Southern Reporter' and reporters-db: 2008-; NRS: 2009-. No confirmed count, so "
                  "no ceiling"),
)


def key(reporter: str) -> str:
    """A reporter's spelling folded as the kit's parser folds it (briefcheck._rep_key): "F. 3d" is "f3d"."""
    k = re.sub(r"[\s.'’]", "", reporter or "").lower()
    return "fappx" if k == "fedappx" else k  # "Fed. Appx." / "Fed. App'x" is F. App'x


TABLE = {key(s.name): s for s in SERIES}


@dataclass(frozen=True)
class Finding:
    # volume-zero, page-zero, volume-past-end, volume-past-ceiling, year-before-start, year-after-end,
    # year-future
    code: str
    label: str  # NO SUCH VOLUME, NO SUCH PAGE or YEAR DOES NOT FIT
    text: str  # one plain sentence, then what to do
    year: int | None = None  # the year judged, for a year finding


def _today(today) -> _dt.date:
    """Today, but never earlier than the table's own date: a clock set in the past must not shrink a
    ceiling or turn this year into the future."""
    t = today or _dt.date.today()
    if isinstance(t, _dt.datetime):
        t = t.date()
    return max(t, TABLE_CHECKED)


def month_year(d: _dt.date) -> str:
    return f"{_MONTHS[d.month - 1]} {d.year}"


def ceiling(reporter: str, today=None) -> int | None:
    """The highest volume `reporter` can have today, or None when the table cannot say."""
    s = TABLE.get(key(reporter))
    if s is None:
        return None
    if s.last_volume is not None:
        return s.last_volume
    if s.count is None:
        return None
    years = max(0, (_today(today) - s.as_of).days) / 365.25
    return s.count + math.floor(s.allowance * (years + 1))


def _number(x) -> int | None:
    try:
        n = int(str(x).strip())
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _year(x) -> int | None:
    s = str(x).strip() if x is not None else ""
    return int(s) if re.fullmatch(r"\d{4}", s) else None


def screen(volume, reporter, page, year=None, *, today=None) -> Finding | None:
    """None, or the one reason this cite cannot exist as written. `volume` and `page` are numbers
    or digit strings; `year` is the year the draft's parenthetical gives (see paren_year), or None."""
    s = TABLE.get(key(reporter))
    vol, pg = _number(volume), _number(page)
    if s is None or vol is None or pg is None:
        return None
    name = s.name
    if vol == 0:
        return Finding("volume-zero", NO_SUCH_VOLUME, f"{name} has no volume 0. {END}")
    if pg == 0:
        return Finding("page-zero", NO_SUCH_PAGE, f"{name} has no page 0. {END}")
    top = ceiling(name, today)
    if top is not None and vol > top:
        if s.last_volume is not None:
            return Finding("volume-past-end", NO_SUCH_VOLUME,
                           f"{name} has no volume {vol}: the series ended at volume {s.last_volume} in "
                           f"{s.last_year}. {END}")
        return Finding("volume-past-ceiling", NO_SUCH_VOLUME,
                       f"{name} has no volume {vol} (it had about {s.count} volumes in {month_year(s.as_of)}). "
                       f"{END}")
    y = _year(year)
    if y is None:
        return None
    if y > _today(today).year:
        return Finding("year-future", YEAR_DOES_NOT_FIT, f"The cite gives the year {y}, which has not come yet. "
                                                         f"{END}", y)
    if y < s.first_year - 1:
        return Finding("year-before-start", YEAR_DOES_NOT_FIT,
                       f"{name} began in {s.first_year}, so a case in it cannot be from {y}. {END}", y)
    if s.last_year is not None and y > s.last_year + 1:
        return Finding("year-after-end", YEAR_DOES_NOT_FIT,
                       f"{name} ended in {s.last_year}, so a case in it cannot be from {y}. {END}", y)
    return None


# A date parenthetical: court abbreviations, ordinals ("5th", "2d"), a day ("5,"), dashes and
# brackets, then the year. Any other word ("applying", "citing", "No.") or a section sign means
# the four digits may not be a year ("(applying § 1983)"), so no year is read.
_PAREN = re.compile(r"(?P<head>.*?)(?<![\w.,])(?P<year>\d{4})", re.S)
_PAREN_TOKEN = re.compile(r"[A-Z][A-Za-z.'’&]*|\d{1,2}(?:st|nd|rd|th|d)\.?|\d{1,2},")


def paren_year(paren) -> int | None:
    """The year a cite's parenthetical gives when it is a court-and-date parenthetical, else None."""
    m = _PAREN.fullmatch((paren or "").strip())
    if not m:
        return None
    tokens = [t for t in re.split(r"[\s\[\]—–-]+", m["head"]) if t]
    if any(not _PAREN_TOKEN.fullmatch(t) or t == "No." for t in tokens):
        return None
    return int(m["year"])


def _cites(text: str):
    """(sent, volume, reporter, page, paren) for each full case cite, read exactly as
    citelookup.draft_cites reads them, so `sent` is the same string."""
    from . import briefcheck, citelookup
    for _key, _disp, _pin, a, b, kind, _extra in briefcheck.scan_authorities(text):
        if kind != "case":
            continue
        m = briefcheck.CASE_RE.match(text, a)
        if not m or m.end() != b:
            continue
        rep = citelookup.CANONICAL_REPORTERS.get(briefcheck._rep_key(m["rep"]))
        if rep is None:
            continue
        vol, page = citelookup._ascii(m["vol"]), citelookup._ascii(m["page"])
        yield f"{vol} {rep} {page}", vol, rep, page, m["paren"]


def screen_text(text: str, *, today=None) -> dict[str, Finding]:
    """Each case cite in `text` that cannot exist as written, keyed as citelookup.draft_cites keys it
    ("812 F.4th 1099"). A cite written more than once keeps its first finding."""
    out: dict[str, Finding] = {}
    for sent, vol, rep, page, paren in _cites(text or ""):
        if sent not in out:
            f = screen(vol, rep, page, paren_year(paren), today=today)
            if f is not None:
                out[sent] = f
    return out


def contradicted(sent: str, finding: Finding, date_filed: str | None = None) -> None:
    """CourtListener holds an opinion at a cite this table flagged. CourtListener wins; when the
    finding is about the volume or page, or the year matches the opinion's, the table is wrong and
    needs correcting, so say so in the log."""
    if finding.year is not None and (date_filed or "")[:4] != str(finding.year):
        return  # the draft's year is off, not the table; CourtListener's own warning says so
    _log.warning("citescreen: %s was flagged %s (%s) but CourtListener holds an opinion there; "
                 "correct this reporter's row in matterkit/citescreen.py", sent, finding.label, finding.code)

#!/usr/bin/env python3
"""Build matterkit/data/caselaw_coverage.json (schema 2) from the case law access survey.

The survey asked, court by court, where a person can read a court's opinions for free and how
far back each place goes. This script folds three of its files, and optionally CourtListener's
public bulk-file years, into the one small JSON file that `matter.py research where` reads and
that other projects (corpuslaw.us's case-law map) read as their public dataset. Paths are
arguments, nothing is hard-coded:

  python3 scripts/build_caselaw_coverage.py \\
      --coverage survey/coverage.csv --sources survey/sources.json --gaps survey/gaps_by_state.md \\
      [--cl-years cl_bulk_years-2026-09-30.json] --out matterkit/data/caselaw_coverage.json

`--cl-years` is the file scripts/cl_bulk_years.py writes from CourtListener's public bulk data.

Public facts only: which court, which website, what years, whether there is full-text search,
and whether the survey's own check could open the page. Nothing that needed a CourtListener
API key goes in (the survey's per-court date ranges from that key are not an input here, and
this script refuses a file named like one, for every argument). The survey's own working notes
are not copied except the short remarks the file already carried. Network-free; standard
library only.

Shape of the output (compact JSON, no indentation):
  schema_version  2
  checked_at, source_files, legend,
  cl_snapshot     {"date": "2026-09-30", "source": "CourtListener bulk data, Public Domain Mark"},
                  or null when no --cl-years was given
  sources         [{id, name, url, access_method, auth, cost, terms_url, commercial_use_allowed}],
  columns         the field names of each row,
  rows            [[jurisdiction, court, level, source, year_from, year_to, fulltext, status, basis,
                    url, note, checked_at, facts, cl], ...]
                  url is null when it is the source's own page. `facts` is a list of codes (see
                  legend["facts"]) or null. `cl` is null except on CourtListener rows:
                  {"id": CourtListener's court id, "since": year the court began (CourtListener's
                   court record) or null, "ended": year it ended or null, and, with --cl-years,
                   "count", "approximate", "earliest", "latest" (dates, from the bulk files) and,
                   when the earliest year had to be raised, "clamp": {"from", "to", "floor"}}.
                  With --cl-years a CourtListener row's year_from and year_to are those years,
                  and year_from is never earlier than the court's own start (or, when CourtListener
                  has none, the Caselaw Access Project's earliest year for the jurisdiction):
                  bulk dates include a few mistyped ones (a Minnesota supreme court opinion dated
                  1665), so an earlier year is raised to that floor and the original is kept in clamp.
  gaps            {"AL": ["plain sentence", ...], ..., "US": [...]},
  gap_flags       {"AL": ["needs-script"], ...}  structured facts read from the survey's gap notes,
  general_gaps    [sentences that hold for every state].
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import re
import sys

SCHEMA_VERSION = 2
NOTE_MAX = 200
COLUMNS = ["jurisdiction", "court", "level", "source", "year_from", "year_to", "fulltext", "status",
           "basis", "url", "note", "checked_at", "facts", "cl"]
SOURCE_FIELDS = ["id", "name", "url", "access_method", "auth", "cost", "terms_url", "commercial_use_allowed"]
LEVELS = {"supreme", "appellate", "trial", "federal-appellate", "federal-district", "bankruptcy", "other"}
STATUSES = {"accessible", "partial", "blocked", "unknown"}
ONE_PER_JURISDICTION = {"google_scholar_case_law"}  # the survey already has one a state; keep it so
SURVEY_TOOLING = {"courtlistener_courts_api"}  # how the survey listed CourtListener's courts, not a place to read opinions
COURTLISTENER = "courtlistener_bulk"
CL_SEARCH = "https://www.courtlistener.com/?q=&type=o&court="
CAP_BROWSE = "https://case.law/caselaw/"  # the project's own browse page (opened 2026-11-07)
NOT_SCRAPED = "collected historically; not currently scraped (years of coverage not measured)"
NOT_SCRAPED_MEASURED = "collected historically; not currently scraped"
CL_SNAPSHOT_SOURCE = "CourtListener bulk data, Public Domain Mark"
NAME_NOTE = re.compile(r"\s*\((?:GovInfo code|[^)]*\binferred\b)[^)]*\)", re.I)
JS_NOTE = re.compile(r"js_render|JS shell|JS-rendered|Angular|Vue shell", re.I)
JS_GAP = re.compile(r"\bJS\b(?!\s+bot-validation)|JS-rendered|JS shell|script-driven|script-loaded|\bAngular\b")
# The row links go to CourtListener's website search, not to its bulk files, so name it for that.
DISPLAY_NAMES = {COURTLISTENER: "CourtListener (free case law search)"}
SCRAPED = "CourtListener collects this court's new opinions automatically; how far back it goes was not measured"

# The order the survey's gap file lists the states in. Entries are found one after the other in
# this order, so a state abbreviation inside a sentence (IN, OR, ME ...) cannot start a new entry.
STATE_ORDER = ("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
               "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY").split()

LEGEND = {
    "CAP": "Harvard's Caselaw Access Project: a free bulk copy of reported (published) opinions that "
           "ends between 2008 and 2019, depending on the state",
    "OFF": "the court's own website",
    "CL": "CourtListener",
    "Scholar": "Google Scholar case law (web search only)",
    "Sup": "supreme court",
    "COA": "court of appeals",
    "JS": "needs JavaScript, which the survey's automated check could not run",
    "status": {
        "accessible": "the survey could open the page",
        "partial": "only part of it could be read",
        "blocked": "the survey's automated check was refused, or the site disallows automated readers; "
                   "it may still open in your own browser",
        "unknown": "not checked, or the survey could not tell",
    },
    "basis": {
        "observed": "the survey read the start year on the page",
        "inferred": "the survey worked the start year out from what it could see",
        "unknown": "no start year was found",
    },
    "facts": {
        "robots-txt": "the site's robots.txt asks automated tools to stay out, so the survey did not read it",
        "needs-script": "the opinion lists load with a script, which the survey's automated check could not run",
        "cl-not-in-use": "CourtListener lists the court as not in use",
        "cl-historical": "CourtListener lists the court as a historical court that has ended",
        "cl-no-new-intake": "CourtListener's court record shows no automatic collection of new opinions from this court",
        "govinfo-absent": "GovInfo has no opinions from this court",
        "govinfo-stale": "GovInfo has stopped receiving opinions from this court",
        "govinfo-elective": "GovInfo carries only the opinions a court chooses to send it",
    },
    "cl": "CourtListener rows: id (its court id), since and ended (years, from its court record), and with "
          "bulk years count, approximate (opinions with approximate dates), earliest and latest (dates, "
          "approximate ones left out) and clamp (an earliest year raised to the court's start)",
}


def trim(text: str, limit: int = NOTE_MAX) -> str:
    """At most `limit` characters, cut at a word boundary, with an ellipsis when cut."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:|-")
    return cut + "…"


def year(value: str) -> int | None:
    value = (value or "").strip()
    if re.fullmatch(r"\d{4}", value) and 1600 <= int(value) <= 2100:
        return int(value)
    return None


def first_url(value: str) -> str:
    m = re.match(r"https?://[^\s()]+", (value or "").strip())
    return m.group(0) if m else ""


def official_note(note: str) -> str:
    """The survey's per-field tags (yr:, ft:, unpub:) say what the year and full-text columns
    already say, so the free-text remarks come first and the tags only fill what is left."""
    free, tagged = [], []
    for part in (p.strip() for p in (note or "").split(" | ")):
        m = re.match(r"(yr|ft|unpub):(\w+)?:?\s*(.*)", part)
        if not m:
            free.append(part)
            continue
        label = {"yr": "Years", "ft": "Search", "unpub": "Unpublished"}[m.group(1)]
        tagged.append(f"{label} ({m.group(2) or 'unknown'}): {m.group(3)}".strip())
    return " | ".join(p for p in free + tagged if p)


def clean_court_name(name: str) -> str:
    """The court's name without the survey's working remarks. A bracketed note such as
    "(GovInfo code ca13 - inferred from docket numbers)" is dropped, and a court the survey could not
    name ("(name not in fetched CL list) jpml") is shown by its code."""
    name = " ".join((name or "").split())
    m = re.fullmatch(r"\(name not in [^)]*\)\s*([A-Za-z0-9_]+)", name)
    if m:
        return f"Court code {m.group(1)}"
    return NAME_NOTE.sub("", name).strip()


def read_date(value) -> str | None:
    """A YYYY-MM-DD date from CourtListener's bulk summary, or None. The summary lost the leading zeros
    of a few years below 1000 ("0019-01-31" came out as "1901-31-"); those are put back, so a typo
    such as year 19 reads as year 19 (and is then raised to the court's start) instead of as 1901."""
    text = str(value or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not m:
        digits = text.replace("-", "")
        if re.fullmatch(r"\d{5,7}", digits):
            m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", digits.zfill(8))
    if not m:
        return None
    try:
        datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def read_cl_years(path: str) -> tuple[dict, str]:
    """The per-court summary scripts/cl_bulk_years.py writes: ({court id: {count, approximate, earliest,
    latest}}, snapshot date). The snapshot date is read from "snapshot" or from the "source" text."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    courts = raw.get("courts") if isinstance(raw, dict) else None
    if not isinstance(courts, dict):
        raise SystemExit('the --cl-years file must look like {"courts": {"<court id>": {"count", "approximate", '
                         '"earliest", "latest"}}}')
    for cid, v in courts.items():
        ok = isinstance(v, dict) and all(isinstance(v.get(k), int) and not isinstance(v.get(k), bool) and v[k] >= 0
                                         for k in ("count", "approximate"))
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(cid)) or not ok:
            raise SystemExit(f"--cl-years court {str(cid)[:30]!r}: count and approximate must be non-negative integers")
    text = raw.get("snapshot") if isinstance(raw.get("snapshot"), str) else raw.get("source") or ""
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if not m:
        raise SystemExit('the --cl-years file needs the bulk snapshot date, in "snapshot" or in a "source" '
                         'text such as "CourtListener bulk data 2026-09-30"')
    return courts, m.group(1)


def official_facts(note: str, http_status: str) -> list[str]:
    facts = []
    if http_status == "blocked_by_robots":
        facts.append("robots-txt")
    if JS_NOTE.search(note or ""):
        facts.append("needs-script")
    return facts


def govinfo_facts(note: str) -> list[str]:
    if re.search(r"NOT in GovInfo", note or "", re.I):
        return ["govinfo-absent"]
    facts = []
    if re.search(r"STALE: no packages after", note or "", re.I):
        facts.append("govinfo-stale")
    if re.search(r"Only opinions the court elects to send", note or "", re.I):
        facts.append("govinfo-elective")
    return facts


def courtlistener_row(note: str, year_to: str) -> tuple[dict, list[str], bool]:
    """From a CourtListener row's survey note: its court record (id, since, ended), the facts, and
    whether it collects new opinions automatically."""
    cl_id = re.search(r"CL id=([\w-]+)", note)
    scraper = re.search(r"has_opinion_scraper=(\w+)", note)
    in_use = re.search(r"in_use=(\w+)", note)
    rss = re.search(r"pacer_has_rss_feed=(\w+)", note)
    since = re.search(r"court existed since (\d{4})", note)
    ended = re.search(r"Historical court \(ended (\d{4})\)", note)
    if not (cl_id and scraper and in_use):
        raise SystemExit("a CourtListener row has no court id or no collection flags in its note")
    record = {"id": cl_id.group(1), "since": year(since.group(1)) if since else None,
              "ended": year(ended.group(1)) if ended else (year(year_to) if in_use.group(1) == "False" else None)}
    facts = []
    if in_use.group(1) == "False":
        facts.append("cl-not-in-use")
    if ended:
        facts.append("cl-historical")
    elif in_use.group(1) == "True" and scraper.group(1) == "False" and not (rss and rss.group(1) == "True"):
        facts.append("cl-no-new-intake")
    return record, facts, scraper.group(1) == "True"


def apply_cl_years(rows: list[dict], years: dict, snapshot: str) -> list[dict]:
    """Fill every CourtListener row from the bulk summary. A row's start year is raised to the court's own
    start (CourtListener's court record) or, when it has none, to the Caselaw Access Project's earliest year for
    the jurisdiction, so a mistyped early date never reads as the court's history. Returns what was raised."""
    cap_from = {r["jurisdiction"]: r["year_from"] for r in rows if r["source"] == "cap_static" and r["year_from"]}
    raised = []
    for r in rows:
        cl = r["cl"]
        if r["source"] != COURTLISTENER or cl is None:
            continue
        got = years.get(cl["id"])
        earliest, latest = (read_date(got.get("earliest")), read_date(got.get("latest"))) if got else (None, None)
        count, approx = (got["count"], got["approximate"]) if got else (0, 0)
        cl.update({"count": count, "approximate": approx, "earliest": earliest, "latest": latest})
        year_from = int(earliest[:4]) if earliest else None
        floor, why = (cl["since"], "court-start") if cl["since"] else (cap_from.get(r["jurisdiction"]), "cap-start")
        if year_from is not None and floor and year_from < floor:
            cl["clamp"] = {"from": year_from, "to": floor, "floor": why}
            raised.append({"jurisdiction": r["jurisdiction"], "court": r["court"], "id": cl["id"], "from": year_from,
                           "to": floor, "floor": why})
            year_from = floor
        year_to = int(latest[:4]) if latest else r["year_to"]
        r["year_from"], r["year_to"] = year_from, year_to
        if year_from or latest:
            r["basis"] = "observed"
        r["note"] = cl_note(r, cl, snapshot)
    return raised


def cl_note(r: dict, cl: dict, snapshot: str) -> str:
    collects = "scraped" in r["_flags"]
    text = ("CourtListener collects this court's new opinions automatically" if collects else NOT_SCRAPED_MEASURED)
    text += f"; bulk files ({snapshot}): "
    if cl["count"] == 0:
        text += "no opinions from it"
    else:
        text += f"{cl['count']:,} opinions" + (f", newest {cl['latest']}" if cl["latest"] else "")
    if cl.get("clamp"):
        c = cl["clamp"]
        text += (f". Start year raised from {c['from']} to {c['to']} ("
                 + ("the court's own start)" if c["floor"] == "court-start" else "the Caselaw Access Project's start)"))
    if not collects and cl.get("ended"):
        text += f"; the court itself ended in {cl['ended']}"
    return text


def build_rows(path: str, sources: dict, years: dict | None = None, snapshot: str | None = None
               ) -> tuple[list[list], list[dict]]:
    """The survey's rows as lists in COLUMNS order, and (with `years`) the CourtListener start years
    that were raised."""
    with open(path, encoding="utf-8", newline="") as f:
        raw = list(csv.DictReader(f))
    rows, seen = [], set()
    for r in raw:
        j, sid = r["jurisdiction"].strip().upper(), r["source_id"].strip()
        if sid not in sources:
            raise SystemExit(f"coverage row names source {sid!r}, which sources.json does not list")
        if sid in ONE_PER_JURISDICTION:
            if (j, sid) in seen:
                continue
            seen.add((j, sid))
        status = r["status"].strip() if r["status"].strip() in STATUSES else "unknown"
        level = r["court_level"].strip() if r["court_level"].strip() in LEVELS else "other"
        note, url = r["note"], first_url(r["evidence_url"])
        facts, cl, flags = [], None, set()
        if sid == COURTLISTENER:
            cl, facts, scraped = courtlistener_row(note, r["year_to"])
            url = CL_SEARCH + cl["id"]
            if scraped:
                flags.add("scraped")
                note = SCRAPED
            else:
                ended = year(r["year_to"])  # from CourtListener's public court list: when the court itself ended
                note, status = NOT_SCRAPED + (f"; the court itself ended in {ended}" if ended else ""), "unknown"
        else:
            if sid.startswith("official_"):
                facts = official_facts(note, r["http_status"].strip())
                note = official_note(note)
            if sid == "govinfo_uscourts":
                facts = govinfo_facts(note)
            if sid == "govinfo_uscourts" or sid in ONE_PER_JURISDICTION:
                url = ""  # the evidence is an XML sitemap or a help page; the source's own page is the link
            if sid == "cap_static":
                url = CAP_BROWSE  # the survey's evidence was a metadata JSON file, no use to a reader
            if r["http_status"].strip() == "404":
                url = ""  # the survey found nothing at that address
        if url == sources[sid]["url"]:
            url = ""
        rows.append({"jurisdiction": j, "court": clean_court_name(r["court_name"]), "level": level, "source": sid,
                     "year_from": year(r["year_from"]), "year_to": year(r["year_to"]),
                     "fulltext": r["fulltext_search"].strip() if r["fulltext_search"].strip() in ("y", "n") else "unknown",
                     "status": status,
                     "basis": r["year_from_basis"].strip() if r["year_from_basis"].strip() in ("observed", "inferred") else "unknown",
                     "url": url or None, "note": note, "checked_at": r["checked_at"].strip(),
                     "facts": facts or None, "cl": cl, "_flags": flags})
    raised = apply_cl_years(rows, years, snapshot) if years is not None else []
    out = []
    for r in rows:
        r["note"] = trim(r["note"])
        out.append([r[c] for c in COLUMNS])
    out.sort(key=lambda x: (x[0], x[2], x[1], x[3]))
    return out, raised


def build_sources(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out = {s["source_id"]: {k: (s["source_id"] if k == "id" else s.get(k)) for k in SOURCE_FIELDS}
           for s in raw if s["source_id"] not in SURVEY_TOOLING}
    for sid, name in DISPLAY_NAMES.items():
        out[sid]["name"] = name
    return out


def build_gaps(path: str) -> tuple[dict, list, dict]:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    body = text.split("\n## ", 1)[0]
    blocks = [b.strip() for b in body.split("\n\n") if b.strip()]
    trial = next((b for b in blocks if b.startswith("Trial courts:")), "")
    states = next((b for b in blocks if b.startswith(STATE_ORDER[0] + " ")), "")
    if not states:
        raise SystemExit("gaps file: found no state entries (expected a paragraph starting 'AL ')")
    states = " ".join(states.split())
    starts, pos = [], 0
    for code in STATE_ORDER:
        m = re.compile(rf"(?:^|(?<=\.) ){code} (?=\S)").search(states, pos)
        if not m:
            raise SystemExit(f"gaps file: no entry found for {code} after position {pos}")
        starts.append((code, m.start(), m.end()))
        pos = m.end()
    gaps, flags = {}, {}
    for i, (code, _start, end) in enumerate(starts):
        stop = starts[i + 1][1] if i + 1 < len(starts) else len(states)
        sentence = states[end:stop].strip()
        if sentence:
            gaps[code] = [sentence]
            if JS_GAP.search(sentence):
                flags[code] = ["needs-script"]  # some court site there lists its opinions with scripts
    general = [s.strip() for s in re.split(r"(?<=\.)\s+(?=[A-Z0-9])", " ".join(trial.split())) if s.strip()]
    # The federal finding in the survey's closing list is the only one about the U.S. courts.
    fed = re.search(r"^\d+\.\s+(GovInfo USCOURTS lacks[^\n]*)", text, re.M)
    if fed:
        gaps["US"] = [" ".join(fed.group(1).split())]
    else:
        print("note: the survey's federal GovInfo finding was not found; no US gap sentence", file=sys.stderr)
    return gaps, general, flags


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--coverage", required=True, help="the survey's coverage.csv")
    ap.add_argument("--sources", required=True, help="the survey's sources.json")
    ap.add_argument("--gaps", required=True, help="the survey's gaps_by_state.md")
    ap.add_argument("--cl-years", help="the per-court summary scripts/cl_bulk_years.py wrote from CourtListener's "
                                       "public bulk files")
    ap.add_argument("--out", required=True, help="where to write the JSON file")
    args = ap.parse_args(argv)
    for p in (args.coverage, args.sources, args.gaps, args.cl_years):
        if p and "cl_ranges" in os.path.basename(p):
            raise SystemExit(f"refusing {p}: those ranges came from a CourtListener API key, not from public pages")
    years, snapshot = read_cl_years(args.cl_years) if args.cl_years else (None, None)
    sources = build_sources(args.sources)
    rows, raised = build_rows(args.coverage, sources, years, snapshot)
    gaps, general, flags = build_gaps(args.gaps)
    checked = max(r[11] for r in rows)
    out = {
        "schema_version": SCHEMA_VERSION,
        "checked_at": checked,
        "source_files": ("coverage.csv, sources.json and gaps_by_state.md from the " + checked +
                         " case law access survey" + (f", and the per-court years in CourtListener's public bulk data "
                                                      f"of {snapshot}" if snapshot else "") +
                         ". The survey opened public court and library web pages "
                         "and read what they said; it logged in nowhere and used no CourtListener key. "
                         "Court sites change, so treat every row as a place to start looking."),
        "legend": LEGEND,
        "cl_snapshot": {"date": snapshot, "source": CL_SNAPSHOT_SOURCE} if snapshot else None,
        "sources": list(sources.values()),
        "columns": COLUMNS,
        "rows": rows,
        "gaps": gaps,
        "gap_flags": flags,
        "general_gaps": general,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    size = os.path.getsize(args.out)
    print(f"{args.out}: {size:,} bytes, {len(rows)} rows, {len(sources)} sources, "
          f"{len(gaps)} states with gap notes, checked_at {checked}"
          + (f", CourtListener bulk snapshot {snapshot}, {len(raised)} start years raised" if snapshot else ""))
    for c in raised:
        print(f"  raised {c['jurisdiction']} {c['court']} ({c['id']}): {c['from']} -> {c['to']} ({c['floor']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

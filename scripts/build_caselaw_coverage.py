#!/usr/bin/env python3
"""Build matterkit/data/caselaw_coverage.json from the case law access survey.

The survey asked, court by court, where a person can read a court's opinions for free and how
far back each place goes. This script folds three of its files into the one small JSON file
that `matter.py research where` reads. Paths are arguments, nothing is hard-coded:

  python3 scripts/build_caselaw_coverage.py \\
      --coverage survey/coverage.csv --sources survey/sources.json --gaps survey/gaps_by_state.md \\
      --out matterkit/data/caselaw_coverage.json

Public facts only: which court, which website, what years, whether there is full-text search,
and whether the survey's own check could open the page. Nothing that needed a CourtListener
API key goes in (the survey's per-court date ranges from that key are not an input here, and
this script refuses a file named like one). Network-free; standard library only.

Shape of the output (compact JSON, no indentation):
  schema_version, checked_at, source_files, legend,
  sources  [{id, name, url, access_method, auth, cost, terms_url, commercial_use_allowed}],
  columns  the field names of each row,
  rows     [[jurisdiction, court, level, source, year_from, year_to, fulltext, status, basis,
             url, note, checked_at], ...]   (url is null when it is the source's own page),
  gaps     {"AL": ["plain sentence", ...], ..., "US": [...]},
  general_gaps  [sentences that hold for every state].
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys

SCHEMA_VERSION = 1
NOTE_MAX = 200
COLUMNS = ["jurisdiction", "court", "level", "source", "year_from", "year_to", "fulltext", "status",
           "basis", "url", "note", "checked_at"]
SOURCE_FIELDS = ["id", "name", "url", "access_method", "auth", "cost", "terms_url", "commercial_use_allowed"]
LEVELS = {"supreme", "appellate", "trial", "federal-appellate", "federal-district", "bankruptcy", "other"}
STATUSES = {"accessible", "partial", "blocked", "unknown"}
ONE_PER_JURISDICTION = {"google_scholar_case_law"}  # the survey already has one a state; keep it so
SURVEY_TOOLING = {"courtlistener_courts_api"}  # how the survey listed CourtListener's courts, not a place to read opinions
COURTLISTENER = "courtlistener_bulk"
CL_SEARCH = "https://www.courtlistener.com/?type=o&court="
NOT_SCRAPED = "collected historically; not currently scraped (years of coverage not measured)"
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


def build_rows(path: str, sources: dict) -> list[list]:
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
        if sid == COURTLISTENER:
            cl_id = re.search(r"CL id=([\w-]+)", note)
            scraped = re.search(r"has_opinion_scraper=(\w+)", note)
            url = CL_SEARCH + cl_id.group(1) if cl_id else ""
            if scraped and scraped.group(1) == "True":
                note = SCRAPED
            else:
                ended = year(r["year_to"])  # from CourtListener's public court list: when the court itself ended
                note, status = NOT_SCRAPED + (f"; the court itself ended in {ended}" if ended else ""), "unknown"
        else:
            if sid.startswith("official_"):
                note = official_note(note)
            if sid == "govinfo_uscourts" or sid in ONE_PER_JURISDICTION:
                url = ""  # the evidence is an XML sitemap or a help page; the source's own page is the link
            if r["http_status"].strip() == "404":
                url = ""  # the survey found nothing at that address
        if url == sources[sid]["url"]:
            url = ""
        rows.append([j, r["court_name"].strip(), level, sid, year(r["year_from"]), year(r["year_to"]),
                     r["fulltext_search"].strip() if r["fulltext_search"].strip() in ("y", "n") else "unknown",
                     status, r["year_from_basis"].strip() if r["year_from_basis"].strip() in ("observed", "inferred") else "unknown",
                     url or None, trim(note), r["checked_at"].strip()])
    rows.sort(key=lambda x: (x[0], x[2], x[1], x[3]))
    return rows


def build_sources(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out = {s["source_id"]: {k: (s["source_id"] if k == "id" else s.get(k)) for k in SOURCE_FIELDS}
           for s in raw if s["source_id"] not in SURVEY_TOOLING}
    for sid, name in DISPLAY_NAMES.items():
        out[sid]["name"] = name
    return out


def build_gaps(path: str) -> tuple[dict, list]:
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
    gaps = {}
    for i, (code, _start, end) in enumerate(starts):
        stop = starts[i + 1][1] if i + 1 < len(starts) else len(states)
        sentence = states[end:stop].strip()
        if sentence:
            gaps[code] = [sentence]
    general = [s.strip() for s in re.split(r"(?<=\.)\s+(?=[A-Z0-9])", " ".join(trial.split())) if s.strip()]
    # The federal finding in the survey's closing list is the only one about the U.S. courts.
    fed = re.search(r"^\d+\.\s+(GovInfo USCOURTS lacks[^\n]*)", text, re.M)
    if fed:
        gaps["US"] = [" ".join(fed.group(1).split())]
    else:
        print("note: the survey's federal GovInfo finding was not found; no US gap sentence", file=sys.stderr)
    return gaps, general


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--coverage", required=True, help="the survey's coverage.csv")
    ap.add_argument("--sources", required=True, help="the survey's sources.json")
    ap.add_argument("--gaps", required=True, help="the survey's gaps_by_state.md")
    ap.add_argument("--out", required=True, help="where to write the JSON file")
    args = ap.parse_args(argv)
    for p in (args.coverage, args.sources, args.gaps):
        if "cl_ranges" in os.path.basename(p):
            raise SystemExit(f"refusing {p}: those ranges came from a CourtListener API key, not from public pages")
    sources = build_sources(args.sources)
    rows = build_rows(args.coverage, sources)
    gaps, general = build_gaps(args.gaps)
    checked = max(r[11] for r in rows)
    out = {
        "schema_version": SCHEMA_VERSION,
        "checked_at": checked,
        "source_files": ("coverage.csv, sources.json and gaps_by_state.md from the " + checked +
                         " case law access survey. The survey opened public court and library web pages "
                         "and read what they said; it logged in nowhere and used no CourtListener key. "
                         "Court sites change, so treat every row as a place to start looking."),
        "legend": LEGEND,
        "sources": list(sources.values()),
        "columns": COLUMNS,
        "rows": rows,
        "gaps": gaps,
        "general_gaps": general,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    size = os.path.getsize(args.out)
    print(f"{args.out}: {size:,} bytes, {len(rows)} rows, {len(sources)} sources, "
          f"{len(gaps)} states with gap notes, checked_at {checked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

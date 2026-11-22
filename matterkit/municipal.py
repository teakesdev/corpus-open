"""City and county code cites, and which Corpus jurisdiction each one names. No network.

Corpus (corpuslaw.us) holds the codes of about 300 cities and counties, each under a jurisdiction
code such as TX-AUSTIN or MO-STLOUIS. The list Matter Kit knows is a snapshot of Corpus's
`law.list_coverage`, kept in matterkit/data/corpus_cities.json and rebuilt with
scripts/refresh-corpus-cities.py. Placeholder rows (listed by Corpus, not yet searchable) are left out.

A city code cite is checked on Corpus only when we know which city it is:

- the cite names the city ("Austin City Code § 25-2-551", "Municipal Code of Chicago § 5-12-080",
  "St. Louis Rev. Code 26.80.060"), and that name belongs to one Corpus city, or to several of which
  one is the city set on the case; or
- the cite names no city ("Municipal Code § 12.03", "City Code § 4-1") and a person set the case's city
  (`matter.py city <case> TX-AUSTIN`, or `/matter city TX-AUSTIN` in Hermes).

A bare section ("§ 25-2-551") is never taken for a city cite, whatever the case's city: that would be
guessing. A cite that names a city Corpus does not list, or a name several Corpus cities share
("Springfield City Code") with no case city to choose between them, is not looked up, and the reason
says so.

What leaves the computer for a city cite is what leaves for any statute cite: the cite as written, as
one line, and the jurisdiction code. `find` reads names only from the snapshot and code words only
from a fixed list, so a payload it accepts cannot carry free text.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from dataclasses import dataclass

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "corpus_cities.json")
CITY_FILE = "city.json"  # in the case's .matter/ folder
CODE_RE = re.compile(r"[A-Z]{2}-[A-Z]{2,30}")

# Names people write for a Corpus city that its list_coverage name does not give. A code here replaces
# the names derived from its coverage row only where `REPLACE` says so.
ALIASES = {
    "NY-NEWYORK": ["New York City", "N.Y.C.", "NYC"],
    "CA-SF": ["S.F.", "San Francisco"],
    "CA-LA": ["L.A.", "Los Angeles"],
    "CA-SJ": ["San Jose", "San José"],
    "MO-STLOUIS": ["Saint Louis"],
    "MN-STPAUL": ["Saint Paul"],
    "FL-STPETERSBURG": ["Saint Petersburg"],
    "FL-PORTSTLUCIE": ["Port Saint Lucie"],
    "PA-PHILADELPHIA": ["Phila."],
    "TN-NASHVILLE": ["Nashville", "Metropolitan Nashville", "Metro Nashville", "Nashville-Davidson"],
    "KY-LOUISVILLE": ["Louisville Metro", "Louisville-Jefferson County"],
    "KY-LEXINGTONFAYETTEURBA": ["Lexington-Fayette Urban County", "Lexington-Fayette", "Lexington"],
    "GA-AUGUSTA": ["Augusta-Richmond County", "Augusta"],
    "GA-MACONBIBB": ["Macon-Bibb County", "Macon-Bibb", "Macon"],
    "ID-BOISECITY": ["Boise"],
    "LA-BATONROUGE": ["Baton Rouge", "East Baton Rouge"],
    "HI-HONOLULU": ["Honolulu"],
    "NC-WINSTONSALEM": ["Winston Salem"],
}
# "City of New York" would give "New York", which is also the state: only the aliases above are used.
REPLACE = {"NY-NEWYORK"}

# Words that may sit between a city's name and "Code" ("Austin City Code", "St. Louis Rev. Code",
# "S.F. Admin. Code", "Seattle Municipal Code"). "County" is not one: "Orange County Code" is the
# county's, not the City of Orange's.
_TYPE = (r"(?:City|Municipal|Mun\.|Metropolitan|Metro\.?|Revised|Rev\.|Administrative|Admin\.|Unified|"
         r"Land|Development|Dev\.|Zoning|Planning|Building|Bldg\.|Housing|Health|Police|Fire|Traffic|"
         r"Parks?|Environment(?:al)?|Subdivision|Business|Tax|Regulations|and|&)")
_CODE = r"(?:Code(?:\s+of\s+Ordinances)?|Ordinance\s+Code|Revised\s+Ordinances)"
# A section number: 25-2-551 · 26.80.060 · 5-12-080 · 21-5.1 · 12.03 · 37.9 · 151.09A.
_NUM = r"\d+[A-Za-z]?(?:[-.:]\d+[A-Za-z]?)*"
_SUB = r"(?:\([0-9A-Za-z]{1,4}\))*"
# Words that may start a sentence or a clause right before an unnamed code ("Under City Code § 4-1").
# Any other capitalised word there may be a city's name Open does not know, so that cite is not
# looked up under the case's city.
_LEAD = {"under", "see", "per", "pursuant", "in", "the", "cf.", "also", "and", "or", "of", "by", "with",
         "from", "its", "this", "that", "our", "their", "both", "each", "violates", "violated", "violating",
         "a", "an", "to", "as", "but", "accord", "contra", "e.g.,", "i.e.,", "section", "sec.", "applying"}


@dataclass(frozen=True)
class CityCite:
    text: str          # the cite as written (one line, spaces collapsed)
    start: int
    end: int
    section: str       # "25-2-551"
    subsection: str    # "(a)(1)" or ""
    name: str | None   # the city's name as written; None when the cite names no city
    known: bool        # the name is one Corpus lists (always True when name is None)


def _load() -> dict:
    try:
        with open(DATA, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"cities": []}


_SNAPSHOT = _load()
CITIES = {c["code"]: c for c in _SNAPSHOT.get("cities", []) if CODE_RE.fullmatch(c.get("code", ""))}
READ_AT = _SNAPSHOT.get("read_at", "")


def names_for(code: str, name: str) -> list[str]:
    """The names a cite may use for a Corpus city, from its list_coverage name plus ALIASES."""
    out = [] if code in REPLACE else [_short_name(name)]
    out += ALIASES.get(code, [])
    return [n for i, n in enumerate(out) if n and n not in out[:i]]


def _short_name(name: str) -> str:
    n = name.strip()
    for pre in ("City and County of ", "City of ", "Town of ", "Village of ", "Metropolitan Government of "):
        if n.startswith(pre):
            n = n[len(pre):]
            break
    for suf in (" consolidated government", " urban county", " Metro Government"):
        if n.endswith(suf):
            n = n[: -len(suf)]
    n = re.split(r"\s+and\s+(?:Davidson County|Parish of .*)$|/", n)[0]
    return n.strip()


def _index() -> dict:
    by_name: dict = {}
    for code, c in CITIES.items():
        for n in names_for(code, c.get("name", "")):
            by_name.setdefault(_key(n), []).append(code)
    return by_name


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("’", "'")).strip().lower()


BY_NAME = _index()


def _name_alt() -> str:
    names = sorted({n for code, c in CITIES.items() for n in names_for(code, c.get("name", ""))},
                   key=len, reverse=True)
    parts = []
    for n in names:
        esc = r"\s+".join(re.escape(w) for w in n.split())
        parts.append(esc.replace("'", "['’]"))
    return "|".join(parts) or r"(?!x)x"


_NAME = _name_alt()
_PREFIX = r"(?:(?:[Tt]he\s+)?(?:City|Town|Village)\s+(?:and\s+County\s+)?of\s+)"
_SEP_SIGN = r"(?:,?\s*(?:§§?|[Ss]ec(?:tion|\.)|SEC\.)\s*)"
# Named: "Austin City Code § 25-2-551" · "City of Austin Code § 2-1-1" · "St. Louis Rev. Code 26.80.060"
_NAMED_FWD = (rf"(?:{_PREFIX}?(?P<n1>{_NAME})\s+(?:{_TYPE}\s+){{0,4}}{_CODE}"
              rf"|(?:{_TYPE}\s+){{0,4}}{_CODE}\s+of\s+(?:the\s+)?{_PREFIX}?(?P<n2>{_NAME}))")
# Unnamed: "Municipal Code § 12.03" · "City Code § 4-1" · "Code of Ordinances § 2-3"
_UNNAMED = (r"(?:City|Town|Municipal|Mun\.|Metropolitan|Metro\.?)\s+(?:" + _TYPE + r"\s+){0,3}" + _CODE
            + r"|Code\s+of\s+Ordinances")
_SECTION = rf"(?:{_SEP_SIGN}(?P<num>{_NUM})|\s+(?P<bare>\d+[A-Za-z]?(?:[-.:]\d+[A-Za-z]?)+))(?P<sub>{_SUB})"

_FWD = re.compile(rf"(?<![\w.])(?<!of )(?<!of\n)(?:{_NAMED_FWD}|(?P<un>{_UNNAMED})){_SECTION}(?![\w])")
# "Section 10-1 of the Houston City Code" · "§ 5-12-080 of the Municipal Code of Chicago"
_REV = re.compile(rf"(?:§§?|[Ss]ec(?:tion|\.))\s*(?P<num>{_NUM})(?P<sub>{_SUB})\s+of\s+(?:the\s+)?"
                  rf"(?:{_PREFIX}?(?P<n1>{_NAME})\s+(?:{_TYPE}\s+){{0,4}}{_CODE}"
                  rf"|(?:{_TYPE}\s+){{0,4}}{_CODE}\s+of\s+(?:the\s+)?{_PREFIX}?(?P<n2>{_NAME})"
                  rf"|(?P<un>{_UNNAMED}))(?![\w])")


def _one_line(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _lead_word(text: str, start: int) -> str:
    m = re.search(r"(\S+)\s*$", text[:start])
    return m.group(1) if m else ""


def find(text: str) -> list[CityCite]:
    """Every city code cite in `text`, in order, none overlapping. Local only."""
    if not isinstance(text, str) or not text:
        return []
    found: list[CityCite] = []
    taken: list[tuple[int, int]] = []
    for pat in (_FWD, _REV):
        for m in pat.finditer(text):
            s, e = m.span()
            if any(s < te and ts < e for ts, te in taken):
                continue
            name = m.group("n1") or m.group("n2")
            known = True
            if name is None:
                # An unnamed code right after a capitalised word ("Carson City Code § 4-1", "Ocean
                # Springs Code of Ordinances § 2-3") may name a city Open does not know.
                # A word ending a sentence or clause ("rent.", "Smith,") is not a name.
                lead = _lead_word(text, s)
                if (lead and lead[:1].isupper() and lead.lower() not in _LEAD
                        and lead[-1] not in ".,;:)]\"'”’"):
                    name, known = lead, False
            found.append(CityCite(_one_line(m.group(0)), s, e, m.group("num") or m.group("bare"),
                                  m.group("sub") or "", name, known))
            taken.append((s, e))
    return sorted(found, key=lambda c: c.start)


def whole(text: str) -> CityCite | None:
    """`text`, from first character to last, is one city code cite `find` reads; else None."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    hits = find(t)
    if len(hits) == 1 and hits[0].start == 0 and hits[0].end == len(t) and hits[0].known:
        return hits[0]
    return None


def known(code) -> bool:
    return isinstance(code, str) and code in CITIES


def label(code: str) -> str:
    """'City of Austin (TX-AUSTIN)'."""
    c = CITIES.get(code)
    return f"{c['name']} ({code})" if c else code


def resolve(cite: CityCite, case_city: str | None) -> tuple[str | None, str]:
    """(Corpus jurisdiction code, "") or (None, why it is not looked up). Never guesses."""
    case_city = case_city if known(case_city) else None
    if cite.name is None:
        if case_city:
            return case_city, ""
        return None, ("this city code cite names no city, and the case has no city set "
                      "(`matter.py city <case folder> <code>`, codes such as TX-AUSTIN)")
    if not cite.known:
        return None, (f"\"{cite.name}\" is not a city Corpus lists, so the cite was not sent "
                      "(a person can set the case's city if the code is that city's)")
    codes = BY_NAME.get(_key(cite.name), [])
    if case_city in codes:
        return case_city, ""
    if len(codes) == 1:
        return codes[0], ""
    if not codes:
        return None, f"\"{cite.name}\" is not a city Corpus lists, so the cite was not sent"
    return None, (f"Corpus holds more than one {cite.name} ({', '.join(sorted(codes))}); set the case's city "
                  "to say which, and it will be checked")


# -- the case's city -----------------------------------------------------------------------------------
def _city_path(matter_dir: str) -> str:
    return os.path.join(matter_dir, ".matter", CITY_FILE)


def case_city(matter_dir: str | None) -> str | None:
    """The Corpus city a person set on the case, or None. A missing, unreadable or linked file is None."""
    if not matter_dir:
        return None
    p = _city_path(matter_dir)
    try:
        if os.path.islink(p) or os.path.islink(os.path.dirname(p)):
            return None
        with open(p, encoding="utf-8") as f:
            code = json.load(f).get("jurisdiction")
    except (OSError, ValueError, AttributeError):
        return None
    return code if known(code) else None


def set_case_city(matter_dir: str, code: str | None, by: str, via: str) -> dict | None:
    """Record (or, with code None, clear) the case's city. Local only; it sends nothing. Raises
    ValueError for a code Corpus does not list or a folder that is not a case."""
    meta = os.path.join(matter_dir, ".matter")
    if not os.path.isdir(meta) or os.path.islink(meta):
        raise ValueError(f"{matter_dir} is not a case folder (no .matter/)")
    p = _city_path(matter_dir)
    if os.path.islink(p):
        raise ValueError(f"{p} is a link; refusing to write through it")
    if code is None:
        if os.path.exists(p):
            os.remove(p)
        return None
    code = code.strip().upper()
    if not known(code):
        hint = suggest(code)
        raise ValueError(f"{code} is not a Corpus city code" + (f"; did you mean {', '.join(hint)}?" if hint else
                         " (codes look like TX-AUSTIN; `matter.py city --list` shows them)"))
    rec = {"jurisdiction": code, "name": CITIES[code]["name"], "set_by": by, "via": via,
           "set_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}
    tmp = p + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    return rec


def suggest(text: str, limit: int = 5) -> list[str]:
    """Corpus city codes whose code or name contains `text` (case-insensitive)."""
    t = re.sub(r"[^a-z]", "", (text or "").lower().split("-")[-1])
    if not t:
        return []
    out = [code for code, c in sorted(CITIES.items())
           if t in code.lower().replace("-", "") or t in re.sub(r"[^a-z]", "", c["name"].lower())]
    return out[:limit]


# -- matching Corpus's answer -----------------------------------------------------------------------------
def heading_section(heading_cite: str) -> str:
    """The section number a Corpus heading cites: what follows its last "§" ("Seattle Mun. Code ch. 22.206,
    subch. VI, § 22.206.160" -> "22.206.160"), or "" when it has none."""
    if "§" not in (heading_cite or ""):
        return ""
    tail = heading_cite.rsplit("§", 1)[1].strip()
    m = re.match(r"[\dA-Za-z.\-‐-―−:]+", tail)
    return m.group(0).rstrip(".") if m else ""


def same_section(a: str, b: str) -> bool:
    """Two section numbers are the same, compared digit for digit ("26.80.060" is not "26.8.060")."""
    return bool(a) and bool(b) and re.sub(r"[\-‐-―−]", "-", a.lower()) == re.sub(r"[\-‐-―−]", "-", b.lower())


def parse_coverage(text: str) -> list[dict]:
    """City and county rows of Corpus's law.list_coverage text, placeholders left out."""
    rows = []
    for line in (text or "").splitlines():
        m = re.match(r"^-\s+([A-Z]{2}-[A-Z]{2,30})\s+[—-]\s+(.+?)\s+\((municipal|county)\):\s*(.*)$", line.strip())
        if not m or m.group(4).startswith("PLACEHOLDER"):
            continue
        rows.append({"code": m.group(1), "name": m.group(2), "level": m.group(3)})
    return rows

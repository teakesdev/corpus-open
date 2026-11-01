"""Check that a draft's case cites name real opinions (CourtListener citation lookup).

This module never touches the network; matterkit/adapters/courtlistener_citations.py sends.
Design: docs/superpowers/specs/2026-11-01-cite-lookup-design.md.

What leaves the machine is built here and nowhere else: each full case cite the draft holds,
found by brief check's own scanner and rebuilt from its parts as "vol reporter page" or
"year WL number", one per line. Case names, pins, parentheticals and every other word of the
draft stay local. `validate_payload` re-checks each line against a strict pattern before
anything is sent, independent of the scanner.

A cite CourtListener finds is "resolved" (located), never "source-checked": found means an
opinion sits at that volume, reporter and page, not that it says what the draft says or is
still good law. A cite it does not find is a lead to check by hand, not proof.
"""
import hashlib
import json
import re
from dataclasses import dataclass, field

from . import authority, briefcheck, citations, store

PROVIDER = "courtlistener-citation-lookup"
PAYLOAD_TYPE = "citation_strings"
ENDPOINT = "www.courtlistener.com/api/rest/v4/citation-lookup/"  # host and path: what a grant names
URL = "https://" + ENDPOINT
MAX_PER_REQUEST = 60  # CourtListener's citations scope: 60 a minute (its per-request cap is 250)
MAX_CHARS = 64000  # its per-request text limit
WAIT_SECONDS = 61  # between requests, so each batch of 60 falls in its own rolling minute
SITE = "https://www.courtlistener.com"

FOUND, NAME_DIFFERS, NOT_FOUND = "FOUND", "NAME DIFFERS", "NOT FOUND"
UNKNOWN_REPORTER, AMBIGUOUS, NOT_LOOKED_UP = "UNKNOWN REPORTER", "AMBIGUOUS", "NOT LOOKED UP"
OUTCOMES = (NAME_DIFFERS, NOT_FOUND, UNKNOWN_REPORTER, AMBIGUOUS, NOT_LOOKED_UP, FOUND)  # report order

LIMITS = ("Found means CourtListener holds an opinion at this volume, reporter and page. It does not mean "
          "the case says what the draft says, that it is still good law, or that a pin cite is right. "
          "Results come from CourtListener (Free Law Project), which has not verified or endorsed them.")
NOT_FOUND_TEXT = ("CourtListener has no opinion at this cite. This is a lead to check by hand, not proof the case "
                  "does not exist: CourtListener lacks some reporters and many slip, unpublished, Westlaw-only "
                  "and older state opinions.")

# Every reporter brief check's scanner reads (briefcheck._REPORTERS), keyed as briefcheck._rep_key keys
# it, spelled as sent. A cite in a reporter missing here is not sent.
CANONICAL_REPORTERS = {briefcheck._rep_key(r): r for r in (
    "U.S.", "S. Ct.", "L. Ed. 2d", "L. Ed.", "F. Supp. 2d", "F. Supp. 3d", "F. Supp.", "F. App'x",
    "F.4th", "F.3d", "F.2d", "F.R.D.", "B.R.", "Fed. Cl.", "So. 3d", "So. 2d", "So.",
    "S.W.3d", "S.W.2d", "S.W.", "N.E.3d", "N.E.2d", "N.E.", "N.W.2d", "N.W.", "S.E.2d", "S.E.",
    "P.3d", "P.2d", "A.3d", "A.2d", "F.")}
CANONICAL_REPORTERS["fedappx"] = "F. App'x"  # "Fed. Appx." / "Fed. App'x"

# A sentence opener brief check's name walk keeps ("Again, Smith v. Jones"): dropped from the local name.
_OPENER = re.compile(r"^(?:Accordingly|Again|Also|Finally|First|Further|Furthermore|Here|However|Indeed|Likewise|"
                     r"Moreover|Notably|Second|Similarly|Still|Then|Third|Thus|Yet),\s+")
_REPS = "|".join(re.escape(r) for r in sorted(set(CANONICAL_REPORTERS.values()), key=len, reverse=True))
_PAYLOAD_LINE = re.compile(rf"\d{{1,4}} (?:{_REPS}) \d{{1,5}}|\d{{4}} WL \d{{3,9}}")


@dataclass
class DraftCite:
    sent: str  # exactly what goes to CourtListener
    display: str  # "Name, sent" (or sent alone): local only
    names: list = field(default_factory=list)  # every case name the draft gives this cite
    year: str | None = None  # from the draft's parenthetical
    lines: list = field(default_factory=list)


@dataclass
class Result:
    cite: DraftCite
    outcome: str
    cluster: dict | None = None  # the opinion matched
    candidates: list = field(default_factory=list)
    note: str = ""
    warnings: list = field(default_factory=list)
    status: int | None = None


# --------------------------------------------------------------------------
# The payload
# --------------------------------------------------------------------------
def draft_cites(text: str) -> list[DraftCite]:
    """Full case cites in the draft, deduplicated, in order of first appearance."""
    found = []
    for _key, _disp, _pin, a, b, kind, _extra in briefcheck.scan_authorities(text):
        if kind != "case":
            continue
        m = briefcheck.CASE_RE.match(text, a)
        if m and m.end() == b:
            rep = CANONICAL_REPORTERS.get(briefcheck._rep_key(m["rep"]))
            if rep is None:
                continue
            sent, year = f"{m['vol']} {rep} {m['page']}", (m["paren"] or "")[-4:] or None
        elif m := briefcheck.WL_RE.match(text, a):
            sent, year = f"{m['year']} WL {m['num']}", None
        else:
            continue
        name = _OPENER.sub("", briefcheck._case_name(text[:a]))
        found.append((a, sent, year, name, text.count("\n", 0, a) + 1))
    out: dict[str, DraftCite] = {}
    for _a, sent, year, name, line in sorted(found):
        c = out.get(sent)
        if c is None:
            c = out[sent] = DraftCite(sent, f"{name}, {sent}" if name else sent)
        if name and name not in c.names:
            c.names.append(name)
        c.year = c.year or year
        if line not in c.lines:
            c.lines.append(line)
    return list(out.values())


def payloads(cites: list[DraftCite]) -> list[str]:
    """The request bodies' `text`: at most MAX_PER_REQUEST cites and MAX_CHARS characters each."""
    out, cur = [], []
    for c in cites:
        if cur and (len(cur) == MAX_PER_REQUEST or len("\n".join(cur + [c.sent])) > MAX_CHARS):
            out.append("\n".join(cur))
            cur = []
        cur.append(c.sent)
    if cur:
        out.append("\n".join(cur))
    return out


def validate_payload(text: str) -> None:
    """The last gate before egress: every line a bare case cite, or the whole request is refused."""
    if not text:
        raise ValueError("empty citation-lookup payload")
    lines = text.split("\n")
    if len(lines) > MAX_PER_REQUEST or len(text) > MAX_CHARS:
        raise ValueError(f"citation-lookup payload over {MAX_PER_REQUEST} cites or {MAX_CHARS} characters")
    for i, line in enumerate(lines, 1):
        if not _PAYLOAD_LINE.fullmatch(line):
            raise ValueError(f"citation-lookup payload line {i} is not a bare case cite; refusing to send it")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Reading CourtListener's answer
# --------------------------------------------------------------------------
_STOP = {"the", "and", "inc", "corp", "corporation", "company", "llc", "ltd", "llp", "city", "county", "state",
         "states", "united", "america", "dept", "department", "people", "commonwealth", "matter", "estate",
         "parte", "for", "rel"}


def _words(name: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", re.sub(r"['’]", "", name.lower()))
            if len(w) >= 3 and w not in _STOP}


def _same_case(draft_name: str, names: list) -> bool:
    """True when the draft's name shares a significant word with one of CourtListener's names
    ("Twombly" / "Bell Atlantic Corp. v. Twombly"; "Educ." / "Education"), or has none to compare."""
    mine = _words(draft_name)
    if not mine:
        return True
    for theirs in map(_words, names):
        for a in mine:
            for b in theirs:
                short, long_ = sorted((a, b), key=len)
                if a == b or (len(short) >= 4 and long_.startswith(short)):
                    return True
    return False


def _clean(s) -> str:
    return " ".join(str(s or "").split())[:200]


def _cluster(c) -> dict | None:
    """The few fields kept from a CourtListener cluster; the URL only when it points at CourtListener."""
    if not isinstance(c, dict) or not isinstance(c.get("id"), int) or isinstance(c.get("id"), bool):
        return None
    path = str(c.get("absolute_url") or "")
    url = SITE + path if path.startswith("/") and not path.startswith("//") else \
        path if path.startswith(SITE + "/") else None
    names = [_clean(c.get(k)) for k in ("case_name", "case_name_short", "case_name_full") if c.get(k)]
    filed = c.get("date_filed")
    return {"id": c["id"], "case_name": names[0] if names else "", "names": names,
            "date_filed": filed if isinstance(filed, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", filed) else None,
            "url": url}


def _judge(cite: DraftCite, item: dict | None) -> Result:
    if item is None:
        return Result(cite, NOT_LOOKED_UP, note="CourtListener did not read this line as a citation")
    status = item.get("status")
    clusters = [k for k in map(_cluster, item.get("clusters") or []) if k]
    if status == 404:
        return Result(cite, NOT_FOUND, note=NOT_FOUND_TEXT, status=status)
    if status == 400:
        return Result(cite, UNKNOWN_REPORTER, status=status,
                      note="CourtListener does not know this reporter. Check the cite by hand.")
    if status == 429:
        return Result(cite, NOT_LOOKED_UP, status=status, note="over CourtListener's per-request limit")
    if status not in (200, 300) or not clusters:
        return Result(cite, AMBIGUOUS if status == 300 else NOT_LOOKED_UP, status=status,
                      note=f"CourtListener answered status {status} with no opinion record")
    note = ""
    if len(clusters) == 1:
        chosen = clusters[0]
    else:
        named = [k for k in clusters if cite.names and all(_same_case(n, k["names"]) for n in cite.names)]
        if len(named) != 1:
            return Result(cite, AMBIGUOUS, candidates=clusters, status=status,
                          note=f"{len(clusters)} opinions at this cite; pick the one you mean")
        chosen = named[0]
        note = f"picked by the draft's case name from {len(clusters)} opinions at this cite"
    differ = [n for n in cite.names if not _same_case(n, chosen["names"])]
    if differ:
        return Result(cite, NAME_DIFFERS, cluster=chosen, candidates=clusters, status=status,
                      note=f"CourtListener's opinion at this cite is “{chosen['case_name']}”; the draft's "
                           f"“{differ[0]}” shares no word with it. Check that the cite points to the case you mean.")
    res = Result(cite, FOUND, cluster=chosen, candidates=clusters, status=status,
                 note=note or ("" if cite.names else "no case name in the draft to compare"))
    if cite.year and chosen["date_filed"] and chosen["date_filed"][:4] != cite.year:
        res.warnings.append(f"the draft gives {cite.year}; CourtListener's date filed is {chosen['date_filed']}")
    return res


def interpret(cites: list[DraftCite], payloads: list[str], batches: list, stopped: str | None = None) -> list[Result]:
    """One Result per cite. `batches[i]` is CourtListener's answer to `payloads[i]`; payloads past the
    last batch were not sent, and `stopped` says why."""
    by_sent = {c.sent: c for c in cites}
    out: dict[str, Result] = {}
    for i, payload in enumerate(payloads):
        lines = payload.split("\n")
        if i >= len(batches):
            for line in lines:
                out[line] = Result(by_sent[line], NOT_LOOKED_UP, note=stopped or "not sent")
            continue
        starts, pos = {}, 0
        for line in lines:
            starts[pos] = line
            pos += len(line) + 1
        got: dict[str, dict] = {}
        for item in batches[i] if isinstance(batches[i], list) else []:
            if not isinstance(item, dict):
                continue
            line = starts.get(item.get("start_index"))
            if line is None and item.get("citation") in lines:
                line = item["citation"]
            if line is not None and line not in got:
                got[line] = item
        for line in lines:
            out[line] = _judge(by_sent[line], got.get(line))
    return [out[c.sent] for c in cites if c.sent in out]


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------
def _esc(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _opinion(k: dict) -> str:
    when = f" ({k['date_filed']})" if k["date_filed"] else ""
    link = f"[cluster {k['id']}]({k['url']})" if k["url"] else f"cluster {k['id']}"
    return f"{_esc(k['case_name']) or '(no name)'}{when}, {link}"


class Report:
    def __init__(self, draft_path, draft_sha256, results, payloads, sent, retrieved_at):
        self.draft_path, self.draft_sha256 = draft_path, draft_sha256
        self.results, self.payloads, self.sent, self.retrieved_at = results, payloads, sent, retrieved_at
        self.recorded: list | None = None  # (cite, action) pairs when --record ran

    def count(self, outcome) -> int:
        return sum(1 for r in self.results if r.outcome == outcome)

    @property
    def exit_code(self) -> int:
        if any(self.count(o) for o in (NAME_DIFFERS, NOT_FOUND, UNKNOWN_REPORTER)):
            return 1
        return 2 if any(self.count(o) for o in (AMBIGUOUS, NOT_LOOKED_UP)) else 0

    def summary(self) -> str:
        state = {1: "CHECK BY HAND BEFORE FILING", 2: "NEEDS A HUMAN", 0: "EVERY CASE CITE FOUND"}[self.exit_code]
        counts = ", ".join(f"{self.count(o)} {o.lower()}" for o in (FOUND, *OUTCOMES[:-1]))
        return (f"{state}: {counts}; {len(self.results)} case cites, "
                f"{self.sent} request{'s' if self.sent != 1 else ''} sent")

    def render(self) -> str:
        out = [f"# Cite lookup: {_esc(self.draft_path.rsplit('/', 1)[-1])}", "",
               f"**Result: {self.summary()}**", "", f"> {LIMITS}", "",
               f"- Draft: `{self.draft_path}` · sha256 `{self.draft_sha256}`",
               f"- Sent to `{URL}` at {self.retrieved_at}: only the case cites listed below, as "
               "\"volume reporter page\", one per line. No case names, pins or other draft text."
               if self.sent else "- Nothing was sent to CourtListener.", ""]
        for i, p in enumerate(self.payloads[: self.sent], 1):
            out.insert(-1, f"- Request {i}: {len(p.split(chr(10)))} cites · sha256 `{sha256(p)}`")
        out += ["| Lines | Draft cite | Sent | Result | CourtListener | Note |", "|---|---|---|---|---|---|"]
        order = {o: i for i, o in enumerate(OUTCOMES)}
        for r in sorted(self.results, key=lambda r: (order[r.outcome], r.cite.lines[:1])):
            opinion = _opinion(r.cluster) if r.cluster else "<br>".join(map(_opinion, r.candidates)) or "—"
            note = "; ".join([_esc(r.note)] * bool(r.note) + [f"⚠ {_esc(w)}" for w in r.warnings]) or "—"
            out.append(f"| {', '.join(map(str, r.cite.lines))} | {_esc(r.cite.display)} | `{r.cite.sent}` | "
                       f"**{r.outcome}** | {opinion} | {note} |")
        if not self.results:
            out.append("| — | no case cites found | — | — | — | nothing was sent |")
        if self.recorded is not None:
            out += ["", "## Recorded in the store", "",
                    "Found cites move from `unverified` to `resolved` (located), never to `source-checked`.", ""]
            out += [f"- `{c}`: {_esc(a)}" for c, a in self.recorded] or ["Nothing written."]
        return "\n".join(out) + "\n"


def render_dry_run(draft_path: str, payloads: list[str]) -> str:
    n = sum(len(p.split("\n")) for p in payloads)
    out = [f"# Cite lookup (dry run): {_esc(draft_path.rsplit('/', 1)[-1])}", "",
           "Nothing was sent. " + (f"With a grant and COURTLISTENER_TOKEN, `cite lookup` would send these {n} "
                                   f"case cites in {len(payloads)} request{'s' if len(payloads) != 1 else ''} to "
                                   f"`{URL}`, and nothing else:" if payloads else
                                   "The draft has no full case cites, so nothing would be sent."), ""]
    for i, p in enumerate(payloads, 1):
        out += [f"## Request {i} · {len(p.split(chr(10)))} cites · sha256 `{sha256(p)}`", "", "```text", p, "```", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------
# Where results land
# --------------------------------------------------------------------------
def record(conn, results: list[Result], by: str) -> list[tuple[str, str]]:
    """Move each FOUND cite's authorities row from unverified to resolved, or add a resolved row.
    Rows past unverified are left exactly as they are; nothing else is ever written."""
    actions = []
    now = citations.now_iso()
    for r in results:
        if r.outcome != FOUND:
            continue
        sent = r.cite.sent
        try:
            row = authority.find(conn, sent)
        except authority.AuthorityError:
            actions.append((sent, "several store rows answer to this cite; nothing written"))
            continue
        if row is not None and row["status"] != "unverified":
            actions.append((sent, f"{row['id']}: already {row['status']}; nothing written"))
            continue
        k = r.cluster
        lookup = {"provider": PROVIDER, "endpoint": ENDPOINT, "cite_sent": sent, "status": r.status,
                  "cluster_id": k["id"], "case_name": k["case_name"], "date_filed": k["date_filed"],
                  "url": k["url"], "retrieved_at": now, "by": by}
        if r.note.startswith("picked"):
            lookup["picked_by_name_from"] = [c["id"] for c in r.candidates]
        try:
            if row is None:
                aid = store.new_id("auth_")
                conn.execute("INSERT INTO authorities (id,citation,status,note) VALUES (?,?,?,?)",
                             (aid, r.cite.display, "unverified", f"added by cite lookup ({by})"))
                ev, action = {"pins": [], "checks": []}, f"{aid}: added as resolved"
            else:
                aid = row["id"]
                ev, action = authority._evidence(row["check_evidence"]), f"{aid}: unverified -> resolved"
                if row["checked_via"]:
                    lookup["previous_checked_via"] = row["checked_via"]
            ev.setdefault("lookups", []).append(lookup)
            citations.transition_authority(conn, aid, "resolved", checked_via=f"courtlistener:id={k['id']}",
                                           check_evidence=json.dumps(ev, ensure_ascii=False))
        except Exception:
            conn.rollback()
            raise
        actions.append((sent, action))
    return actions

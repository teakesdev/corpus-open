"""Citation guard: find every citation in a piece of text and say what is known about each one.

Case cites (volume reporter page) go to CourtListener's citation lookup through the kit's own
adapter; statute and code cites go, one bare cite per request, to Corpus's law search through the
kit's statute lookup adapter (matterkit.statutelookup decides what may be sent). Both adapters
refuse to send anything unless .matter/consent.json allows it: the case's default grant, which
covers bare cites only and which a person can turn off for the case, or a person's own grant
(RFC 0001). Both log every request to .matter/egress.jsonl. Without a grant, a token or a case
folder, the guard still lists every cite it found and says plainly that it was not looked up.

Outcomes say what a source holds at a cite, never that the text says what a reply says or that
the authority is still good law.
"""
from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass, field

from ..matterkit import authority, citations, citelookup, citescreen, store
from ..matterkit.consent import ConsentError

# Statute/code outcomes (case outcomes are citelookup's own words).
IN_CORPUS, NOT_MATCHED, NOT_LOOKED_UP = "IN CORPUS", "NOT MATCHED IN CORPUS", citelookup.NOT_LOOKED_UP

PROBLEM = (*citescreen.LABELS,  # cites the offline screen says cannot exist as written
           citelookup.NOT_FOUND, citelookup.NAME_DIFFERS, citelookup.UNKNOWN_REPORTER, citelookup.AMBIGUOUS)
GOOD = (citelookup.FOUND, IN_CORPUS)

LIMITS = ("Found means a source holds something at that cite. It does not mean it says what this "
          "reply says, that it is still good law, or that a pin cite is right.")

KIT_CLI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "matter.py")


KEY_URL = "https://www.courtlistener.com/profile/api/"
HOW_TO_START = ("Type /matter demo to try a made-up case, or /matter init ~/cases/my-case to start "
                "your own.")


def python_cmd() -> str:
    """The Python running this plugin (Hermes's own), quoted for a shell. A printed command must not
    start with a bare `python3`: on a stock Mac that is Python 3.9, which the kit does not run on."""
    return shlex.quote(sys.executable) if sys.executable else "python3"


def grant_command(kind: str, matter_dir: str | None, caller: str) -> str:
    """The terminal command a person runs to turn a check on or off, with the interpreter's and the
    kit's full paths so it works from any folder. kind is "case" (CourtListener on: case cites and
    search), "case-off", "statute" (Corpus statute and code cites back on) or "statute-off". Both cite
    checks cover every caller, so `caller` no longer changes the line."""
    cli = python_cmd() + " " + shlex.quote(KIT_CLI)
    md = shlex.quote(os.path.abspath(os.path.expanduser(matter_dir))) if matter_dir else "<case folder>"
    if kind == "case":
        return f"{cli} courtlistener on {md}"
    if kind == "case-off":
        return f"{cli} courtlistener off {md}"
    if kind == "statute-off":
        return f"{cli} consent decline {md} --scope statute-lookup"
    return f"{cli} consent activate {md} --scope statute-lookup"


NO_KEY = ("Case cites were not looked up: there is no CourtListener key yet. The key is free: get one "
          f"at {KEY_URL} and add it in Hermes's settings for this plugin (COURTLISTENER_TOKEN), never in a "
          "chat. /matter grant shows how.")
NO_FOLDER = "no case folder is set up yet. " + HOW_TO_START
_WHAT = {"case": ("Case cites", "case law"), "statute": ("Statute and code cites", "statutes")}


def not_allowed(kind: str, e: Exception) -> str:
    """Why a lookup was refused, in words for someone who has never seen a grant. The refusal's own
    text names providers, endpoints and payload types; none of that helps the reader, and
    `/matter grant` prints the steps. kind is "case" or "statute"."""
    cites, what = _WHAT[kind]
    text = str(e)
    if "turned off for this case by a person" in text:
        return (f"{cites} were not looked up: looking up {what} was turned off for this case by a person. "
                "Type /matter grant to see how to turn it back on.")
    if "daily budget" in text:
        return (f"{cites} were not looked up: today's limit for looking up {what} in this case is used "
                "up. The count starts over each day.")
    if "grant expired" in text:
        return (f"{cites} were not looked up: the permission to look up {what} for this case has run "
                "out. Type /matter grant to turn it on again.")
    if text.startswith("no valid grant"):
        return (f"{cites} were not looked up: looking up {what} is not turned on for this case yet. "
                "Type /matter grant to see how to turn it on.")
    return f"{cites} were not looked up: {_short(e)}"

# Code-name prefix -> Corpus jurisdiction code. Unknown codes search every jurisdiction.
_JURIS = [
    (r"U\.\s?S\.\s?C|C\.\s?F\.\s?R|Pub\.\s?L", "US"),
    (r"Miss\.", "MS"), (r"Ala\.", "AL"), (r"Ark\.", "AR"), (r"La\.", "LA"), (r"Tenn\.", "TN"),
    (r"Tex\.", "TX"), (r"Fla\.", "FL"), (r"Ga\.", "GA"), (r"Del\.", "DE"), (r"Cal\.", "CA"),
    (r"N\.\s?Y\.", "NY"), (r"Wyo\.", "WY"), (r"Colo\.", "CO"), (r"Ill\.", "IL"), (r"Ohio", "OH"),
]


@dataclass
class Row:
    kind: str  # "case" | "statute"
    cite: str  # what the text says, local only
    outcome: str
    detail: str = ""
    url: str | None = None
    case_file: str = ""  # what the matter's own authority ledger says, if anything


@dataclass
class Check:
    rows: list = field(default_factory=list)
    notes: list = field(default_factory=list)  # why something was not looked up, once each
    sent: dict = field(default_factory=dict)  # provider -> requests that left the machine

    @property
    def problems(self) -> list:
        return [r for r in self.rows if r.outcome in PROBLEM or r.outcome == NOT_MATCHED]

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


def _norm(cite: str) -> str:
    return re.sub(r"[\s.,]+", "", cite).lower()


def jurisdiction(cite: str) -> str | None:
    for pat, code in _JURIS:
        if re.search(pat, cite):
            return code
    return None


def statute_cites(text: str) -> list[str]:
    """Code and statute cites the kit's local parser finds, longest form only, in order, once each.
    A bare section ("§ 89-7-27") is the short form of a full cite in the same text that ends in that
    section ("Miss. Code Ann. § 89-7-27"), so only the full cite is listed and looked up."""
    rows = [r for r in citations.extract_citations(text)
            if r["status"] == "extracted" and "§" in (r["parsed_citation"] or "")]
    keep = [r for r in rows if not any(o is not r and o["char_start"] <= r["char_start"]
                                       and r["char_end"] <= o["char_end"]
                                       and (o["char_end"] - o["char_start"]) > (r["char_end"] - r["char_start"])
                                       for o in rows)]
    out: list[str] = []
    for r in keep:
        c = r["parsed_citation"].strip()
        if c not in out:
            out.append(c)
    full = [_norm(c) for c in out if not c.startswith("§")]
    return [c for c in out if not (c.startswith("§") and any(f.endswith(_norm(c)) for f in full))]


def _heading_cite(heading: str) -> str:
    return re.split(r"\s+[—–-]\s+", heading, maxsplit=1)[0]


def match_statute(cite: str, hits: list) -> tuple | None:
    """The first Corpus hit whose citation is this cite. Returns (heading, url) or None."""
    want = _norm(cite)
    for h in hits:
        if _norm(_heading_cite(h.citation)) == want:
            return h.citation, (h.provenance or {}).get("url")
    return None


def _ledger(conn, cite: str) -> str:
    """What the matter's own authority ledger records for this cite (source-checked, resolved...)."""
    if conn is None:
        return ""
    try:
        row = authority.find(conn, cite)
    except Exception:  # noqa: BLE001 - several rows or an unparseable cite: say nothing
        return ""
    if row is None:
        return ""
    via = f" via {row['checked_via']}" if row["checked_via"] else ""
    return f"your case file has it as {row['status']}{via}"


def check_text(*args, **kwargs) -> Check:
    """Every cite in `text`, with what CourtListener, Corpus and the case file say about it.
    See _check_text; this wrapper closes the case-file connection."""
    holder: dict = {}
    try:
        return _check_text(*args, _holder=holder, **kwargs)
    finally:
        if holder.get("conn") is not None:
            holder["conn"].close()


def _check_text(text: str, *, matter_dir: str | None = None, caller_id: str = "hermes",
               lookup: bool = True, courtlistener_token: str | None = None,
               corpus_api_key: str | None = None, max_statutes: int = 5,
               cl_factory=None, corpus_factory=None, _holder=None) -> Check:
    """
    `cl_factory(matter_dir, caller_id, token)` and `corpus_factory(matter_dir, api_key)` build the
    adapters; tests pass fakes. Nothing here raises for a refused grant or a failed request: the
    affected cites come back NOT LOOKED UP with a note.
    """
    result = Check()
    conn = None
    if matter_dir:
        try:
            conn = store.connect(matter_dir)
            if _holder is not None:
                _holder["conn"] = conn
        except Exception:  # noqa: BLE001
            conn = None

    # Case cites -------------------------------------------------------------------------------
    cites = citelookup.draft_cites(text)
    screened = _screen(text)  # cites that cannot exist as written, known with no lookup
    payloads = citelookup.payloads(cites)
    answers, stopped = [], None
    why = None
    if cites:
        if not lookup:
            why = "Case cites were not looked up: the guard is set to local only."
        elif not matter_dir:
            why = "Case cites were not looked up: " + NO_FOLDER
        elif not courtlistener_token:
            why = NO_KEY
        else:
            try:
                if cl_factory is None:
                    from ..matterkit.adapters.courtlistener_citations import CitationLookupAdapter

                    def cl_factory(md, cid, tok):
                        return CitationLookupAdapter(md, cid, token=tok)
                adapter = cl_factory(matter_dir, caller_id, courtlistener_token)
                answers, stopped = adapter.lookup(payloads[:1])  # one request per check: 60 cites
                result.sent["courtlistener"] = getattr(adapter, "sent", len(answers))
                if len(payloads) > 1 and stopped is None:
                    stopped = "more than 60 case cites; only the first 60 were looked up"
            except ConsentError as e:
                why = not_allowed("case", e)
            except Exception as e:  # noqa: BLE001
                why = f"Case cites were not looked up: {_short(e)}"
        if why:
            result.note(why)
        if stopped:
            result.note(f"CourtListener lookup stopped early: {stopped}")
        for r in citelookup.interpret(cites, payloads, answers, stopped):
            detail = r.note
            url = None
            if r.cluster:
                name = r.cluster.get("case_name") or "an opinion"
                year = (r.cluster.get("date_filed") or "")[:4]
                detail = f"CourtListener: {name}" + (f" ({year})" if year else "")
                if r.note and r.outcome != citelookup.FOUND:
                    detail += f". {r.note}"
                url = r.cluster.get("url")
            elif r.outcome == citelookup.NOT_FOUND:
                detail = "CourtListener has no opinion at this cite. Check it by hand before relying on it."
            elif r.outcome == citelookup.NOT_LOOKED_UP and why:
                detail = "not looked up"
            for w in r.warnings:
                detail += f" ⚠ {w}"
            outcome, detail = _screened(r, screened.get(r.cite.sent), detail.strip())
            result.rows.append(Row("case", r.cite.display, outcome, detail, url,
                                   _ledger(conn, r.cite.sent)))

    # Statute and code cites -------------------------------------------------------------------
    statutes = statute_cites(text)
    adapter = None
    why = None
    if statutes:
        if not lookup:
            why = "Statute and code cites were not looked up: the guard is set to local only."
        elif not matter_dir:
            why = "Statute and code cites were not looked up: " + NO_FOLDER
        else:
            try:
                if corpus_factory is None:
                    from ..matterkit.adapters.corpus_search import CorpusStatuteLookupAdapter

                    def corpus_factory(md, key):
                        return CorpusStatuteLookupAdapter(md, "authenticated" if key else "anonymous", api_key=key)
                adapter = corpus_factory(matter_dir, corpus_api_key)
            except ConsentError as e:
                why = not_allowed("statute", e)
            except Exception as e:  # noqa: BLE001
                why = f"Statute and code cites were not looked up: {_short(e)}"
        from ..matterkit.search import SearchQuery
        for i, cite in enumerate(statutes):
            row = Row("statute", cite, NOT_LOOKED_UP, "not looked up", None, _ledger(conn, cite))
            if adapter is not None and why is None and i < max(0, int(max_statutes)):
                try:
                    hits = adapter.search(SearchQuery(text=cite, jurisdiction=jurisdiction(cite), limit=5),
                                          caller_id)
                    result.sent["corpus"] = result.sent.get("corpus", 0) + 1
                    m = match_statute(cite, hits)
                    if m:
                        heading, url = m
                        title = heading[len(_heading_cite(heading)):].strip(" —–-")
                        row.outcome, row.detail, row.url = IN_CORPUS, (f"Corpus: {title}" if title else
                                                                       "Corpus holds this section"), url
                    else:
                        row.outcome = NOT_MATCHED
                        row.detail = ("Corpus returned no section with this exact cite. It may be outside "
                                      "Corpus's coverage or cited in another form; check it by hand.")
                except ConsentError as e:
                    why = not_allowed("statute", e)
                except Exception as e:  # noqa: BLE001
                    why = f"Statute and code cites were not looked up: {_short(e)}"
            elif adapter is not None and why is None:
                result.note(f"Only the first {max_statutes} statute cites were looked up.")
            result.rows.append(row)
        if why:
            result.note(why)
    return result


def _short(e: Exception, limit: int = 240) -> str:
    """The error's first line, cut at a word (never mid-word) when it is long."""
    s = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    if len(s) <= limit:
        return s
    head = s[:limit]
    return (head[:head.rfind(" ")] if " " in head else head).rstrip(" ,;:(—-") + "…"


def _screen(text: str) -> dict:
    """The offline screen's findings for the text's case cites (citescreen); none if the screen fails."""
    try:
        return citescreen.screen_text(text)
    except Exception:  # noqa: BLE001 - the screen must never cost a reply its citation block
        return {}


def _screened(r, finding, detail: str) -> tuple:
    """A case row's (outcome, detail) once the offline screen has spoken. CourtListener outranks the
    table: an opinion at the cite (FOUND, NAME DIFFERS, AMBIGUOUS) keeps CourtListener's outcome and
    the contradiction is logged. NOT FOUND and the like keep their outcome and add the screen's
    sentence; a cite that was not looked up takes the screen's label (NO SUCH VOLUME...)."""
    if finding is None:
        return r.outcome, detail
    if r.cluster or r.candidates:
        citescreen.contradicted(r.cite.sent, finding, (r.cluster or r.candidates[0]).get("date_filed"))
        return r.outcome, detail
    if r.outcome == citelookup.NOT_LOOKED_UP:
        return finding.label, finding.text
    return r.outcome, f"{detail} {finding.text}".strip()


_MARK = {citelookup.FOUND: "✓", IN_CORPUS: "✓", citelookup.NOT_LOOKED_UP: "·"}


def render(check: Check, *, footer: bool = True) -> str:
    """Markdown block listing every cite, problems first. Empty string when there are no cites."""
    if not check.rows:
        return ""
    order = {o: i for i, o in enumerate((*PROBLEM, NOT_MATCHED, citelookup.NOT_LOOKED_UP, *GOOD))}
    rows = sorted(check.rows, key=lambda r: order.get(r.outcome, 99))
    n_bad = len(check.problems)
    head = (f"**Citation check: {n_bad} of {len(rows)} to check by hand**" if n_bad else
            f"**Citation check: {len(rows)} cite{'s' if len(rows) != 1 else ''}**")
    out = (["", "---"] if footer else []) + [head]
    for r in rows:
        mark = _MARK.get(r.outcome, "⚠")
        cite = f"[{r.cite}]({r.url})" if r.url else r.cite
        line = f"- {mark} **{r.outcome}** · {cite}"
        if r.detail and r.detail != "not looked up":
            line += f": {r.detail}"
        if r.case_file:
            line += f" ({r.case_file})"
        out.append(line)
    out += [f"_{n}_" for n in check.notes]
    out.append(f"_{LIMITS} Checked by Corpus Open; not legal advice._")
    return "\n".join(out)

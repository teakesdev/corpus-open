"""Citation guard: find every citation in a piece of text and say what is known about each one.

Case cites (volume reporter page) go to CourtListener's citation lookup through the kit's own
adapter; statute and code cites go to Corpus's law search through the kit's Corpus adapter. Both
adapters refuse to send anything unless the case folder holds a human-issued grant in
.matter/consent.json naming this caller (RFC 0001), and both log every request to
.matter/egress.jsonl. Without a grant, a token or a case folder, the guard still lists every cite it
found and says plainly that it was not looked up.

Outcomes say what a source holds at a cite, never that the text says what a reply says or that
the authority is still good law.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field

from ..matterkit import authority, citations, citelookup, store
from ..matterkit.consent import ConsentError

# Statute/code outcomes (case outcomes are citelookup's own words).
IN_CORPUS, NOT_MATCHED, NOT_LOOKED_UP = "IN CORPUS", "NOT MATCHED IN CORPUS", citelookup.NOT_LOOKED_UP

PROBLEM = (citelookup.NOT_FOUND, citelookup.NAME_DIFFERS, citelookup.UNKNOWN_REPORTER, citelookup.AMBIGUOUS)
GOOD = (citelookup.FOUND, IN_CORPUS)

LIMITS = ("Found means a source holds something at that cite. It does not mean it says what this "
          "reply says, that it is still good law, or that a pin cite is right.")

KIT_CLI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "matter.py")


def grant_command(kind: str, matter_dir: str | None, caller: str) -> str:
    """The terminal command a person runs to allow lookups, with the kit's full path so it works from
    any folder. kind is "case" (CourtListener, the one switch) or "statute" (Corpus)."""
    cli = "python3 " + shlex.quote(KIT_CLI)
    md = shlex.quote(os.path.abspath(os.path.expanduser(matter_dir))) if matter_dir else "<case folder>"
    if kind == "case":
        return f"{cli} courtlistener on {md}"
    return f"{cli} consent activate {md} --scope corpus-search --caller {shlex.quote(str(caller))}"


NO_KEY = ("Case cites were not looked up: there is no CourtListener key. Get a free one at "
          "https://www.courtlistener.com/profile/api/ and save it with `python3 " + shlex.quote(KIT_CLI) +
          " courtlistener key`, or as COURTLISTENER_TOKEN in this plugin's settings.")

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
    """Code and statute cites the kit's local parser finds, longest form only, in order, once each."""
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
    return out


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
    payloads = citelookup.payloads(cites)
    answers, stopped = [], None
    why = None
    if cites:
        if not lookup:
            why = "Case cites were not looked up: the guard is set to local only."
        elif not matter_dir:
            why = "Case cites were not looked up: no case folder is set (matter_dir)."
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
                why = (f"Case cites were not looked up: this case folder has no CourtListener grant for "
                       f"'{caller_id}' ({_short(e)}). A person grants it once in a terminal: "
                       + grant_command("case", matter_dir, caller_id))
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
            result.rows.append(Row("case", r.cite.display, r.outcome, detail.strip(), url,
                                   _ledger(conn, r.cite.sent)))

    # Statute and code cites -------------------------------------------------------------------
    statutes = statute_cites(text)
    adapter = None
    why = None
    if statutes:
        if not lookup:
            why = "Statute and code cites were not looked up: the guard is set to local only."
        elif not matter_dir:
            why = "Statute and code cites were not looked up: no case folder is set (matter_dir)."
        else:
            try:
                if corpus_factory is None:
                    from ..matterkit.adapters.corpus_search import CorpusSearchAdapter

                    def corpus_factory(md, key):
                        return CorpusSearchAdapter(md, "authenticated" if key else "anonymous", api_key=key)
                adapter = corpus_factory(matter_dir, corpus_api_key)
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
                    why = (f"Statute and code cites were not looked up: this case folder has no Corpus grant "
                           f"for '{caller_id}' ({_short(e)}). A person grants it once in a terminal: "
                           + grant_command("statute", matter_dir, caller_id))
                except Exception as e:  # noqa: BLE001
                    why = f"Statute and code cites were not looked up: {_short(e)}"
            elif adapter is not None and why is None:
                result.note(f"Only the first {max_statutes} statute cites were looked up.")
            result.rows.append(row)
        if why:
            result.note(why)
    return result


def _short(e: Exception) -> str:
    s = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return s[:240]


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

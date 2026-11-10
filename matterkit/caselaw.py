"""Case law lookups on CourtListener, and the bare-cite checks a case has by default; shared by the
CLI, the browser UI and MCP.

Three checks, each per case (design 2026-11-09, decisions 2026-11-10):

- Case cite checking: bare case citations ("550 U.S. 544") to CourtListener's citation lookup.
- Statute and code cite checking: bare statute and code cites ("42 U.S.C. § 2000e-3(a)") to Corpus
  (corpuslaw.us), through matterkit.statutelookup.
- Case law search: query text to www.courtlistener.com.

The two cite checks are on for a case nobody has chosen for: cases.init writes their default grants
(consent.CASE_DEFAULTS, citation_strings only), and an older case gets them on first use
(consent.ensure_defaults). A person turns either off for the case and it stays off (a "declined"
record). Search is never on by default: only a person turns it on, with the browser UI's button or
`matter.py courtlistener on`, which asks at the terminal and turns case cite checking back on too.
Nothing here is reachable from the MCP server except the read-only status, the lookups themselves
(which re-check the grant before every request and log each one hash-only) and the case default
those lookups write for a never-chosen case. Every CourtListener answer is kept in the case
(matterkit.saved_lookups), so asking again sends nothing and works even with lookups off; `fresh`
asks again.
"""
import datetime as _dt
import hashlib
import os

from . import citelookup, consent, credentials, saved_lookups, statutelookup
from .citations import now_iso

SEARCH_PROVIDER = "courtlistener-free"
SEARCH_HOST = "www.courtlistener.com"  # matterkit.adapters.courtlistener.ENDPOINT_HOST; no adapter import here
SEARCH_BUDGET = 500
CITE_BUDGET = consent.CASE_DEFAULTS[citelookup.PROVIDER][1]  # CourtListener's free tier: about 125 a day

# scope -> (provider, endpoints, payload types, daily budget): the CourtListener switch
SCOPES = {
    "search": (SEARCH_PROVIDER, [SEARCH_HOST], ["query_text"], SEARCH_BUDGET),
    "cite-lookup": (citelookup.PROVIDER, [citelookup.ENDPOINT], [citelookup.PAYLOAD_TYPE], CITE_BUDGET),
}
STATUTE_SCOPE = (statutelookup.PROVIDER, [statutelookup.ENDPOINT], [statutelookup.PAYLOAD_TYPE],
                 statutelookup.DAILY_BUDGET)

KEY_URL = "https://www.courtlistener.com/profile/api/"
BY_DEFAULT = ("Cite checking is on for every case unless a person turns it off. Bare case cites such as "
              "\"550 U.S. 544\" go to CourtListener (a free, nonprofit case law site), only once a CourtListener "
              "key is set. Bare statute and code cites such as \"42 U.S.C. § 2000e-3(a)\" go to Corpus "
              "(corpuslaw.us). Nothing else goes anywhere: no case names, facts, documents or drafts. Each check "
              "can be turned off for a case, and off stays off. Every request is logged in the case's "
              ".matter/egress.jsonl.")
WHAT_IS_SENT = ("Turning this on lets Matter Kit, and the AI agents you use with it, search case law on "
                "CourtListener (a free, nonprofit case law site) for this case. Search words are sent, and they "
                "can describe your facts. It also turns case cite checking back on if it was off; for that, only "
                "case citations such as \"550 U.S. 544\" are sent. Your documents and drafts never are. Every "
                "request is logged in the case's .matter/egress.jsonl.")
SEARCH_QUESTION = ("Also let your agents search case law for this case? Search words are sent to CourtListener, "
                   "and they can describe your facts. [y/N]")
SEARCH_NEEDS_KEY = ("case law search requires a free CourtListener key (CourtListener refuses searches "
                    f"without one), saved in Matter Kit's Case law lookups panel or set as COURTLISTENER_TOKEN; "
                    f"get one at {KEY_URL} — refusing egress")
HOW_TO_TURN_ON = (
    "A person turns case law search on, once per case, in one of two ways:\n"
    "- In Matter Kit's browser window (`python3 matter.py ui <workspace>`): open the case, then "
    "\"Case law lookups\", then \"Turn on\". Paste a free CourtListener key there too (get one at "
    f"{KEY_URL}); search and case cite checking need it.\n"
    "- Or in a terminal: `python3 matter.py courtlistener on <case folder>`.")
CASE_OFF = "`matter.py courtlistener off <case folder>`"
CASE_BACK_ON = "`matter.py courtlistener on <case folder>` (it asks you to type yes, and turns case law search on too)"
STATUTE_OFF = "`matter.py consent decline <case folder> --scope statute-lookup`"
STATUTE_BACK_ON = "`matter.py consent activate <case folder> --scope statute-lookup` (it asks you to type ACTIVATE)"


def _active_open_grant(matter_dir: str, provider: str) -> dict | None:
    """A grant every caller may use for this provider, if any: a person's, or the case default while no
    person has turned the check off."""
    now = _dt.datetime.now(_dt.timezone.utc)
    grants = consent.load_grants(matter_dir)
    off = any(g.get("provider") == provider and g.get("kind") == consent.DECLINED for g in grants)
    for g in grants:
        if g.get("provider") != provider or g.get("kind") not in ("human", consent.DEFAULT):
            continue
        if g.get("kind") == consent.DEFAULT and off:
            continue
        if consent.ANY_CALLER not in (g.get("authorized_callers") or []):
            continue
        exp = g.get("expires")
        if exp:
            try:
                if _dt.datetime.fromisoformat(exp) < now:
                    continue
            except ValueError:
                continue
        return g
    return None


def check_state(matter_dir: str, provider: str) -> str:
    """"on" (a grant every caller may use), "default" (nobody has chosen: the case default is written on
    first use), "declined" (a person turned it off for this case) or "off" (never turned on, or a
    person's grant limited to named callers or run out)."""
    if _active_open_grant(matter_dir, provider):
        return "on"
    if consent.declined(matter_dir, provider):
        return "declined"
    if provider in consent.CASE_DEFAULTS and not consent.chosen(matter_dir, provider):
        return "default"
    return "off"


def check_label(matter_dir: str, provider: str) -> str:
    """check_state, with a case-default grant in force reported as "default" rather than "on": the
    words /matter grant shows a person ("on (the default)")."""
    st = check_state(matter_dir, provider)
    if st == "on":
        g = _active_open_grant(matter_dir, provider)
        if g and g.get("kind") == consent.DEFAULT:
            return "default"
    return st


def cite_check_on(matter_dir: str) -> bool:
    """Case cites in this case may go to CourtListener's citation lookup (a key is a separate question)."""
    return check_state(matter_dir, citelookup.PROVIDER) in ("on", "default")


def statute_check_on(matter_dir: str) -> bool:
    """Statute and code cites in this case may go to Corpus."""
    return check_state(matter_dir, statutelookup.PROVIDER) in ("on", "default")


def search_on(matter_dir: str) -> bool:
    """A person turned case law search on for this case."""
    return check_state(matter_dir, SEARCH_PROVIDER) == "on"


def turn_on(matter_dir: str, issuer: str, via: str) -> list[dict]:
    """Record the person's switch: search and case cite checking, every caller. The caller has already
    confirmed with the person (the UI's button press, or yes typed at the terminal). Undoes a
    `turn_off`."""
    if not os.path.isdir(os.path.join(matter_dir, ".matter")):
        raise ValueError(f"{matter_dir} is not a case folder (no .matter/)")
    return [consent.write_human_grant(matter_dir, provider, endpoints, payloads, issuer, via,
                                      daily_budget=budget, authorized_callers=[consent.ANY_CALLER])
            for provider, endpoints, payloads, budget in SCOPES.values()]


def turn_off(matter_dir: str, by: str = "a person", via: str = "cli") -> int:
    """The person's off switch: search and case cite checking, and case cite checking stays off (a
    declined record) until a person turns it on again. Returns how many grants were dropped."""
    if not os.path.isdir(os.path.join(matter_dir, ".matter")):
        raise ValueError(f"{matter_dir} is not a case folder (no .matter/)")
    n = consent.revoke(matter_dir, [p for p, *_ in SCOPES.values()])
    consent.write_declined(matter_dir, citelookup.PROVIDER, by, via)
    return n


def decline(matter_dir: str, provider: str, by: str = "a person", via: str = "cli") -> dict:
    """Turn one provider off for the case, and keep it off (`matter.py consent decline`)."""
    if not os.path.isdir(os.path.join(matter_dir, ".matter")):
        raise ValueError(f"{matter_dir} is not a case folder (no .matter/)")
    return consent.write_declined(matter_dir, provider, by, via)


def _scope_state(matter_dir: str, provider: str, default_budget: int) -> dict:
    g = _active_open_grant(matter_dir, provider)
    st = check_state(matter_dir, provider)
    budget = g.get("daily_budget") if g else default_budget
    return {"on": st in ("on", "default"), "state": st,
            "by_default": st == "default" or bool(g and g.get("kind") == consent.DEFAULT),
            "used_today": consent._today_usage(matter_dir, provider),
            "daily_limit": budget if isinstance(budget, int) else None,
            "turned_on_by": g.get("created_by") if g and g.get("kind") == "human" else None,
            "turned_on_at": g.get("created_at") if g and g.get("kind") == "human" else None}


def state(matter_dir: str) -> dict:
    """What each check, the key and today's use look like. Never includes the key. `on` is the
    CourtListener switch (search and case cite checking both on)."""
    scopes = {name: _scope_state(matter_dir, provider, budget)
              for name, (provider, _endpoints, _payloads, budget) in SCOPES.items()}
    statutes = _scope_state(matter_dir, STATUTE_SCOPE[0], STATUTE_SCOPE[3])
    return {"on": all(s["on"] for s in scopes.values()), "search_on": scopes["search"]["on"],
            "cite_check_on": scopes["cite-lookup"]["on"], "statute_check_on": statutes["on"],
            "scopes": scopes, "statutes": statutes, "saved": saved_lookups.counts(matter_dir),
            "key": credentials.courtlistener_token_source(), "key_url": KEY_URL, "what_is_sent": WHAT_IS_SENT,
            "by_default": BY_DEFAULT}


def _use(s: dict) -> str:
    limit = s["daily_limit"] if s["daily_limit"] is not None else "no"
    return f"{s['used_today']} used today, {limit} a day allowed"


def describe(matter_dir: str) -> str:
    """state() as a few lines a person or an agent can read."""
    st = state(matter_dir)
    cite, search_, law = st["scopes"]["cite-lookup"], st["scopes"]["search"], st["statutes"]
    lines = ["Case law lookups are on for this case."] if st["on"] else []
    if cite["on"]:
        lines.append(f"- Case cite checking: on{' (the default)' if cite['by_default'] else ''}. Bare case cites "
                     f"such as \"550 U.S. 544\" go to CourtListener, once a key is set; {_use(cite)}. "
                     f"To turn it off for this case: {CASE_OFF}.")
    else:
        why = "turned off for this case by a person" if cite["state"] == "declined" else "off"
        lines.append(f"- Case cite checking: {why}. To turn it back on: {CASE_BACK_ON}.")
    if law["on"]:
        lines.append(f"- Statute and code cite checking: on{' (the default)' if law['by_default'] else ''}. Bare "
                     f"statute and code cites such as \"42 U.S.C. § 2000e-3(a)\" go to Corpus (corpuslaw.us); "
                     f"{_use(law)}. To turn it off for this case: {STATUTE_OFF}.")
    else:
        why = "turned off for this case by a person" if law["state"] == "declined" else "off"
        lines.append(f"- Statute and code cite checking: {why}. To turn it back on: {STATUTE_BACK_ON}.")
    if search_["on"]:
        lines.append(f"- Case law search: on; {_use(search_)}. Search words go to CourtListener. "
                     f"To turn it off: {CASE_OFF} (this turns case cite checking off too).")
    else:
        lines.append("- Case law search: off. Search words go to CourtListener and can describe your facts. "
                     + HOW_TO_TURN_ON)
    lines.append("Nothing else is sent: no case names, facts, documents or drafts.")
    n = st["saved"]
    lines.append(f"Saved in this case: {n['searches']} search{'es' if n['searches'] != 1 else ''}, "
                 f"{n['cites']} citation answer{'s' if n['cites'] != 1 else ''}. Asking these again sends nothing, "
                 "even with lookups off.")
    if st["key"]:
        lines.append(f"CourtListener key: set ({'COURTLISTENER_TOKEN' if st['key'] == 'environment' else 'saved'}).")
    else:
        lines.append(f"CourtListener key: not set. Search and cite checking both need one (free at {KEY_URL}). "
                     "A person saves it in the Case law lookups panel or with `matter.py courtlistener key`. "
                     "Statute and code cites need no key.")
    return "\n".join(lines)


def first_run(matter_dir: str, issuer: str, ask, ask_key, say=print) -> None:
    """`matter.py init`'s two questions on a terminal (design §2), asked once: the CourtListener key if
    none is saved on this computer, then whether to turn case law search on. `ask(question)` returns
    the person's answer; `ask_key()` reads the key without echoing it. Search goes on only for an
    answer of y or yes; the cite checks are on either way."""
    say(BY_DEFAULT)
    if not credentials.courtlistener_token():
        say(f"Checking case cites needs a free CourtListener key, kept on this computer and never in a case "
            f"folder. Get one at {KEY_URL}, then paste it here, or press Enter to skip:")
        key = (ask_key() or "").strip()
        if key:
            try:
                where = credentials.save_courtlistener_token(key)
                say(f"Key saved in {where} (only you can read it).")
            except ValueError as e:
                say(f"{e}. Nothing saved; `matter.py courtlistener key` adds it later.")
        else:
            say("No key saved. Case cites are listed but not looked up until one is "
                "(`matter.py courtlistener key` adds it later).")
    if (ask(SEARCH_QUESTION) or "").strip().lower() in ("y", "yes"):
        turn_on(matter_dir, issuer, "tty")
        say("Case law search is on for this case.")
    else:
        say("Case law search stays off. `matter.py courtlistener on <case folder>` turns it on later.")


def search(matter_dir: str, caller_id: str, query: str, limit: int = 5, fresh: bool = False) -> list:
    """CourtListener opinion search, authenticated when a key is saved. A search asked before comes
    from the case's saved lookups (provenance["saved"]) and sends nothing, on or off. Raises
    ConsentError when it would have to ask and lookups are off."""
    provider, endpoints, payloads, _ = SCOPES["search"]
    from . import search as S
    if not fresh:
        rows = S.saved_search(matter_dir, provider, query, limit)
        if rows is not None:
            return rows
    consent.authorize(matter_dir, provider, endpoints[0], payloads[0], caller_id)  # before any network code loads
    if not credentials.courtlistener_token():
        # CourtListener answers search without a key with 401 (seen 2026-11-06): don't send it.
        raise consent.ConsentError(SEARCH_NEEDS_KEY)
    return S.manual_search(matter_dir, caller_id, query, limit=limit, auth_mode="authenticated", fresh=True)


def check_draft(matter_dir: str, path: str, raw: bytes, caller_id: str, max_requests: int = 1,
                fresh: bool = False, limit_name: str = "max_requests"):
    """Check a draft's case cites. Cites CourtListener answered before come from the case's saved
    lookups; only the rest are sent. Returns citelookup.Report (its `saved` counts the former).
    Raises ConsentError when a cite would have to be sent and lookups are off or there is no key."""
    cites = citelookup.draft_cites(raw.decode("utf-8", "replace"))
    saved = {} if fresh else saved_lookups.find_cites(matter_dir, [c.sent for c in cites])
    todo = [c for c in cites if c.sent not in saved]
    payloads = citelookup.payloads(todo)
    answers, stopped, sent = [], None, 0
    if payloads:
        consent.ensure_defaults(matter_dir, [citelookup.PROVIDER])  # a never-chosen case: its default, once
        consent.authorize(matter_dir, citelookup.PROVIDER, citelookup.ENDPOINT, citelookup.PAYLOAD_TYPE, caller_id)
        if not credentials.courtlistener_token():
            raise consent.ConsentError("citation lookup requires a free CourtListener key, saved in Matter Kit's "
                                       "Case law lookups panel or set as COURTLISTENER_TOKEN — refusing egress")
        from .adapters.courtlistener_citations import CitationLookupAdapter
        adapter = CitationLookupAdapter(matter_dir, caller_id)
        answers, stopped = adapter.lookup(payloads[:max_requests])
        sent = adapter.sent
        saved_lookups.save_cites(matter_dir, citelookup.answered(payloads, answers))
        if stopped is None and len(payloads) > max_requests:
            stopped = f"over {limit_name} {max_requests}; run again in a minute for the rest"
    results = {r.cite.sent: r for r in citelookup.interpret(todo, payloads, answers, stopped)}
    old = [c for c in cites if c.sent in saved]
    # Each saved answer is replayed as a one-line request, so today's draft (its case names and
    # years) is judged against it afresh.
    replay = [[dict(saved[c.sent]["answer"], start_index=0)] for c in old]
    for r in citelookup.interpret(old, [c.sent for c in old], replay):
        when = str(saved[r.cite.sent].get("retrieved_at") or "")[:10] or "an earlier lookup"
        r.note = "; ".join(filter(None, [f"saved answer from {when}", r.note]))
        results[r.cite.sent] = r
    report = citelookup.Report(path, hashlib.sha256(raw).hexdigest(), [results[c.sent] for c in cites],
                               payloads, sent, now_iso(), answered=len(answers))
    report.saved, report.stopped = len(old), stopped
    return report


def lookup_draft(matter_dir: str, path: str, caller_id: str, max_requests: int = 1, fresh: bool = False):
    """Check a Markdown or text draft's case cites against CourtListener. Returns the report
    (citelookup.Report). Raises ConsentError when off or without a key, ValueError for a bad file."""
    with open(path, "rb") as f:
        raw = f.read()
    if b"\0" in raw:
        raise ValueError("cite lookup reads Markdown or plain text, and this file has binary content; "
                         "use the .md copy of the draft")
    return check_draft(matter_dir, path, raw, caller_id, max_requests=max_requests, fresh=fresh)

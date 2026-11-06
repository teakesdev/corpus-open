"""Case law lookups on CourtListener: one switch per case, shared by the CLI, the browser UI and MCP.

Turning on writes two human grants (RFC 0001 amendment 2026-11-06): search (query text to
www.courtlistener.com) and cite lookup (bare case citations to the citation-lookup URL), each
open to every caller on this computer and with a daily limit well above everyday use. Only a
person turns them on: the browser UI's button, or `matter.py courtlistener on`, which asks at the
terminal. Nothing here is reachable from the MCP server except the read-only status and the
lookups themselves, which re-check the grant before every request and log each one hash-only.
Every answer is kept in the case (matterkit.saved_lookups), so asking again sends nothing and
works even with lookups off; `fresh` asks again.
"""
import datetime as _dt
import hashlib
import os

from . import citelookup, consent, credentials, saved_lookups
from .citations import now_iso

SEARCH_PROVIDER = "courtlistener-free"
SEARCH_HOST = "www.courtlistener.com"  # matterkit.adapters.courtlistener.ENDPOINT_HOST; no adapter import here
SEARCH_BUDGET = 500
CITE_BUDGET = 100  # CourtListener's free tier allows about 125 citation lookups a day

# scope -> (provider, endpoints, payload types, daily budget)
SCOPES = {
    "search": (SEARCH_PROVIDER, [SEARCH_HOST], ["query_text"], SEARCH_BUDGET),
    "cite-lookup": (citelookup.PROVIDER, [citelookup.ENDPOINT], [citelookup.PAYLOAD_TYPE], CITE_BUDGET),
}

KEY_URL = "https://www.courtlistener.com/profile/api/"
WHAT_IS_SENT = ("Turning this on lets Matter Kit, and the AI agents you use with it, look up case law on "
                "CourtListener (a free, nonprofit case law site) for this case. Only search words and case "
                "citations such as \"550 U.S. 544\" are sent. Your documents, facts and drafts never are. "
                "Every request is logged in the case's .matter/egress.jsonl.")
SEARCH_NEEDS_KEY = ("case law search requires a free CourtListener key (CourtListener refuses searches "
                    f"without one), saved in Matter Kit's Case law lookups panel or set as COURTLISTENER_TOKEN; "
                    f"get one at {KEY_URL} — refusing egress")
HOW_TO_TURN_ON = (
    "Case law lookups are off for this case. They need a person to turn them on once, in one of two ways:\n"
    "- In Matter Kit's browser window (`python3 matter.py ui <workspace>`): open the case, then "
    "\"Case law lookups\", then \"Turn on\". Paste a free CourtListener key there too (get one at "
    f"{KEY_URL}); search and cite checking need it.\n"
    "- Or in a terminal: `python3 matter.py courtlistener on <case folder>`.\n"
    "After that, lookups work without asking again. " + WHAT_IS_SENT)


def _active_open_grant(matter_dir: str, provider: str) -> dict | None:
    """The person's turned-on grant for this provider that every caller may use, if any."""
    now = _dt.datetime.now(_dt.timezone.utc)
    for g in consent.load_grants(matter_dir):
        if g.get("provider") != provider or g.get("kind") != "human":
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


def is_on(matter_dir: str) -> bool:
    return all(_active_open_grant(matter_dir, p) for p, *_ in SCOPES.values())


def turn_on(matter_dir: str, issuer: str, via: str) -> list[dict]:
    """Record the person's switch: both grants, every caller. The caller has already confirmed with
    the person (the UI's button press, or ACTIVATE typed at the terminal)."""
    if not os.path.isdir(os.path.join(matter_dir, ".matter")):
        raise ValueError(f"{matter_dir} is not a case folder (no .matter/)")
    return [consent.write_human_grant(matter_dir, provider, endpoints, payloads, issuer, via,
                                      daily_budget=budget, authorized_callers=[consent.ANY_CALLER])
            for provider, endpoints, payloads, budget in SCOPES.values()]


def turn_off(matter_dir: str) -> int:
    return consent.revoke(matter_dir, [p for p, *_ in SCOPES.values()])


def state(matter_dir: str) -> dict:
    """What the switch, the key and today's use look like. Never includes the key."""
    scopes = {}
    for name, (provider, _endpoints, _payloads, default_budget) in SCOPES.items():
        g = _active_open_grant(matter_dir, provider)
        budget = g.get("daily_budget") if g else default_budget
        scopes[name] = {"on": g is not None, "used_today": consent._today_usage(matter_dir, provider),
                        "daily_limit": budget if isinstance(budget, int) else None,
                        "turned_on_by": g.get("created_by") if g else None,
                        "turned_on_at": g.get("created_at") if g else None}
    return {"on": all(s["on"] for s in scopes.values()), "scopes": scopes, "saved": saved_lookups.counts(matter_dir),
            "key": credentials.courtlistener_token_source(), "key_url": KEY_URL, "what_is_sent": WHAT_IS_SENT}


def describe(matter_dir: str) -> str:
    """state() as a few lines a person or an agent can read."""
    st = state(matter_dir)
    if not st["on"]:
        lines = [HOW_TO_TURN_ON]
    else:
        lines = ["Case law lookups are on for this case."]
        for name, s in st["scopes"].items():
            limit = s["daily_limit"] if s["daily_limit"] is not None else "no"
            lines.append(f"- {name}: {s['used_today']} used today, {limit} a day allowed")
    n = st["saved"]
    lines.append(f"Saved in this case: {n['searches']} search{'es' if n['searches'] != 1 else ''}, "
                 f"{n['cites']} citation answer{'s' if n['cites'] != 1 else ''}. Asking these again sends nothing, "
                 "even with lookups off.")
    if st["key"]:
        lines.append(f"CourtListener key: set ({'COURTLISTENER_TOKEN' if st['key'] == 'environment' else 'saved'}).")
    else:
        lines.append(f"CourtListener key: not set. Search and cite checking both need one (free at {KEY_URL}). "
                     "A person saves it in the Case law lookups panel or with `matter.py courtlistener key`.")
    return "\n".join(lines)


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

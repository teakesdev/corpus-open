"""CourtListener REST search adapter (R2). stdlib urllib only.

Terms reviewed 2026-09-09: not legal advice; queries you send are yours;
do not present results as FLP-verified analysis; free authenticated
throttle 5/min · 50/hr · 125/day; no extra paid tier in this adapter.
Lookup never writes source-checked — only resolved with provenance.

Transport (chair 2026-09-09): NO HTTP redirects. A 3xx is ConsentError and
must not produce a second request. Auth mode is explicit: authenticated
fails before egress if COURTLISTENER_TOKEN is missing. Anonymous never
sends Authorization. Mode is recorded; the token is not.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from ..citations import now_iso
from ..consent import ConsentError
from ..search import SearchQuery, SearchResult, log_egress

ENDPOINT_HOST = "www.courtlistener.com"
SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"
UA = "Corpus-Open-matter-kit/0.2 (R2 manual lookup; not a commercial crawl)"
AUTH_MODES = {"anonymous", "authenticated"}


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConsentError(
            f"refusing HTTP {code} redirect to {newurl} — destination is not "
            "in the grant's endpoints; no follow")


def opener():
    """HTTPS/HTTP opener with redirects disabled. Shared so tests can wrap it."""
    return urllib.request.build_opener(_RefuseRedirects)


class CourtListenerFreeAdapter:
    provider = "courtlistener-free"
    endpoint = ENDPOINT_HOST

    def __init__(self, matter_dir: str, auth_mode: str = "anonymous"):
        mode = (auth_mode or "anonymous").strip().lower()
        if mode not in AUTH_MODES:
            raise ConsentError(f"auth_mode must be anonymous|authenticated, not {auth_mode!r}")
        self.matter_dir = matter_dir
        self.auth_mode = mode

    def search(self, query: SearchQuery, caller_id: str) -> list[SearchResult]:
        from .. import consent
        consent.authorize(
            self.matter_dir, self.provider, self.endpoint, "query_text", caller_id)
        q = (query.text or "").strip()
        if not q:
            raise ValueError("empty query")
        token = os.environ.get("COURTLISTENER_TOKEN", "").strip()
        if self.auth_mode == "authenticated" and not token:
            raise ConsentError(
                "authenticated mode requires COURTLISTENER_TOKEN — refusing "
                "egress (no silent anonymous fallback)")
        params = {"q": q, "type": "o"}
        if query.limit:
            params["page_size"] = str(min(int(query.limit), 20))
        url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
        log_egress(self.matter_dir, self.provider, self.endpoint, "query_text", q)
        body = get_json(url, auth_mode=self.auth_mode, token=token)
        out = []
        for hit in (body.get("results") or [])[: query.limit]:
            cites = hit.get("citation") or []
            citation = cites[0] if cites else (hit.get("caseName") or "")
            path = hit.get("absolute_url") or ""
            src = "https://www.courtlistener.com" + path if path.startswith("/") else path
            out.append(SearchResult(
                authority_id=f"cl:{hit.get('cluster_id') or hit.get('id')}",
                citation=str(citation),
                name=str(hit.get("caseName") or hit.get("caseNameFull") or ""),
                snippet=_snippet(hit),
                jurisdiction=str(hit.get("court_citation_string") or hit.get("court") or "") or None,
                provenance={
                    "provider": self.provider,
                    "url": src,
                    "retrieved_at": now_iso(),
                    "cluster_id": hit.get("cluster_id"),
                    "dateFiled": hit.get("dateFiled"),
                    "status": hit.get("status"),
                    "authenticated": self.auth_mode == "authenticated",
                },
                effective_date=hit.get("dateFiled"),
            ))
        return out


def _snippet(hit: dict) -> str:
    ops = hit.get("opinions") or []
    if ops and ops[0].get("snippet"):
        return str(ops[0]["snippet"]).replace("\n", " ").strip()[:500]
    return str(hit.get("caseName") or "")[:500]


def get_json(url: str, auth_mode: str = "anonymous", token: str = "") -> dict:
    if auth_mode not in AUTH_MODES:
        raise ConsentError(f"auth_mode must be anonymous|authenticated, not {auth_mode!r}")
    if auth_mode == "authenticated" and not token:
        raise ConsentError("authenticated mode requires a token — no egress")
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if auth_mode == "authenticated":
        headers["Authorization"] = "Token " + token
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener().open(req, timeout=30) as resp:
            raw = resp.read()
    except ConsentError:
        raise
    except urllib.error.HTTPError as e:
        if 300 <= int(e.code) < 400:
            loc = e.headers.get("Location", "") if e.headers else ""
            raise ConsentError(
                f"refusing HTTP {e.code} redirect to {loc} — no follow") from e
        raise RuntimeError(f"CourtListener HTTP {e.code}: {e.reason}") from e
    return json.loads(raw.decode("utf-8"))

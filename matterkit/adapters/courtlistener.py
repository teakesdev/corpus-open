"""CourtListener REST search adapter (R2). stdlib urllib only.

Terms reviewed 2026-09-09: not legal advice; queries you send are yours;
do not present results as FLP-verified analysis; free authenticated
throttle 5/min · 50/hr · 125/day; no extra paid tier in this adapter.
Lookup never writes source-checked — only resolved with provenance.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from ..citations import now_iso
from ..search import SearchQuery, SearchResult, log_egress

ENDPOINT_HOST = "www.courtlistener.com"
SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"
UA = "Corpus-Open-matter-kit/0.2 (R2 manual lookup; not a commercial crawl)"


class CourtListenerFreeAdapter:
    provider = "courtlistener-free"
    endpoint = ENDPOINT_HOST

    def __init__(self, matter_dir: str):
        self.matter_dir = matter_dir

    def search(self, query: SearchQuery, caller_id: str) -> list[SearchResult]:
        from .. import consent
        consent.authorize(
            self.matter_dir, self.provider, self.endpoint, "query_text", caller_id)
        q = (query.text or "").strip()
        if not q:
            raise ValueError("empty query")
        params = {"q": q, "type": "o"}
        if query.limit:
            params["page_size"] = str(min(int(query.limit), 20))
        url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
        log_egress(self.matter_dir, self.provider, self.endpoint, "query_text", q)
        body = _get_json(url)
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
                },
                effective_date=hit.get("dateFiled"),
            ))
        return out


def _snippet(hit: dict) -> str:
    ops = hit.get("opinions") or []
    if ops and ops[0].get("snippet"):
        return str(ops[0]["snippet"]).replace("\n", " ").strip()[:500]
    return str(hit.get("caseName") or "")[:500]


def _get_json(url: str) -> dict:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    token = os.environ.get("COURTLISTENER_TOKEN", "").strip()
    if token:
        headers["Authorization"] = "Token " + token
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"CourtListener HTTP {e.code}: {e.reason}") from e
    return json.loads(raw.decode("utf-8"))

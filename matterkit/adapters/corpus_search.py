"""Corpus public MCP search adapter (R3). stdlib urllib + json only.

Chair-approved shape (2026-09-09): manual search/lookup only; explicit auth
mode; NO redirects; no path to source-checked (no such tool exists server
side — law.verify_citation is ABSENT from the live tool list, probed
2026-09-09). Provider quotas are observed limits, not guarantees.

MCP protocol is honored end to end: initialize -> tools/call. Protocol
errors (-32xxx JSON-RPC errors, isError:true results) are raised
distinctly from legitimate zero-result searches (empty results list).
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from ..citations import now_iso
from ..consent import ConsentError
from ..search import SearchQuery, SearchResult, log_egress

MCP_URL = "https://corpuslaw.us/api/mcp"
HOST = "corpuslaw.us"
UA = "Corpus-Open-matter-kit/0.3 (R3 manual lookup; not a commercial crawl)"
PROTOCOL_VERSION = "2025-06-18"


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConsentError(
            f"refusing HTTP {code} redirect to {newurl} — destination is not "
            "in the grant's endpoints; no follow")


def opener():
    return urllib.request.build_opener(_RefuseRedirects)


class MCPRpcError(RuntimeError):
    """JSON-RPC error object or isError:true tool result — a protocol/tool
    failure, NOT a legitimate zero-result search."""


class CorpusSearchAdapter:
    provider = "corpus-search"
    endpoint = HOST

    def __init__(self, matter_dir: str, auth_mode: str = "anonymous"):
        mode = (auth_mode or "anonymous").strip().lower()
        if mode not in ("anonymous", "authenticated"):
            raise ConsentError(f"auth_mode must be anonymous|authenticated, not {auth_mode!r}")
        self.matter_dir = matter_dir
        self.auth_mode = mode

    def _api_key(self) -> str:
        key = ""
        if self.auth_mode == "authenticated":
            key = (os.environ.get("CORPUS_API_KEY", "") or "").strip()
            if not key:
                raise ConsentError(
                    "authenticated mode requires CORPUS_API_KEY — refusing "
                    "egress (no silent anonymous fallback)")
        return key

    # -- MCP plumbing ------------------------------------------------------
    def _rpc(self, payload: dict) -> dict:
        headers = {
            "User-Agent": UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        key = self._api_key()
        if key:
            headers["Authorization"] = "Bearer " + key
        req = urllib.request.Request(
            MCP_URL, data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST")
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
            raise RuntimeError(f"Corpus MCP HTTP {e.code}: {e.reason}") from e
        return json.loads(raw.decode("utf-8"))

    def _call_tool(self, name: str, arguments: dict) -> dict:
        """tools/call with protocol errors distinct from empty results."""
        init = self._rpc({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": "Corpus-Open-matter-kit",
                                      "version": "0.3"}}})
        if "error" in init:
            raise MCPRpcError(f"MCP initialize error: {init['error']}")
        resp = self._rpc({
            "jsonrpc": "2.0", "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments}})
        if "error" in resp:
            raise MCPRpcError(f"MCP {name} error {resp['error'].get('code')}: "
                              f"{resp['error'].get('message')}")
        result = resp.get("result") or {}
        if result.get("isError"):
            raise MCPRpcError(
                f"MCP {name} tool error: {result.get('text', '')[:300]}")
        if not result.get("text"):
            sc = result.get("structuredContent") or {}
            result["text"] = sc.get("text", "")
        return result

    # -- public surface ----------------------------------------------------
    def search(self, query: SearchQuery, caller_id: str) -> list[SearchResult]:
        from .. import consent
        consent.authorize(self.matter_dir, self.provider, self.endpoint,
                          "query_text", caller_id)
        q = (query.text or "").strip()
        if not q:
            raise ValueError("empty query")
        log_egress(self.matter_dir, self.provider, self.endpoint, "query_text", q)
        args: dict = {"query": q, "limit": max(1, min(int(query.limit or 8), 25))}
        if query.jurisdiction:
            args["jurisdiction"] = query.jurisdiction
        result = self._call_tool("law.search", args)
        return self._parse_hits(result, q)

    def get_node(self, node_id: str, caller_id: str) -> SearchResult:
        """law.get_node lookup — consent covers the outgoing identifier
        (node UUID) as query_text; returned text stays lookup provenance."""
        from .. import consent
        nid = (node_id or "").strip()
        if not nid:
            raise ValueError("empty node id")
        consent.authorize(self.matter_dir, self.provider, self.endpoint,
                          "query_text", caller_id)
        log_egress(self.matter_dir, self.provider, self.endpoint, "query_text", nid)
        result = self._call_tool("law.get_node", {"id": nid})
        text = result.get("text") or ""
        return SearchResult(
            authority_id=f"corpus:{nid}",
            citation=_first_line(text) or nid,
            name=f"Corpus node {nid}",
            snippet=text[:500],
            jurisdiction=None,
            provenance={
                "provider": self.provider,
                "url": f"https://corpuslaw.us/code/{nid}",
                "retrieved_at": now_iso(),
                "node_id": nid,
                "kind": "law.get_node",
                "authenticated": self.auth_mode == "authenticated",
                "verified": False,   # no verification tool exists server-side
            },
        )

    def _parse_hits(self, result: dict, q: str) -> list[SearchResult]:
        text = result.get("text") or ""
        empty = (not text.strip()) or "no results" in text.lower()[:200]
        hits = _extract_hits(text)
        if not hits and empty:
            return []  # legitimate zero-result search
        out = []
        for h in hits[:25]:
            out.append(SearchResult(
                authority_id=f"corpus:{h['node_id']}",
                citation=h["citation"],
                name=h["heading"],
                snippet=h["snippet"][:500],
                jurisdiction=h.get("jurisdiction"),
                provenance={
                    "provider": self.provider,
                    "url": f"https://corpuslaw.us/code/{h['node_id']}",
                    "retrieved_at": now_iso(),
                    "node_id": h["node_id"],
                    "kind": "law.search",
                    "authenticated": self.auth_mode == "authenticated",
                    "verified": False,
                },
            ))
        return out


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


def _extract_hits(text: str) -> list[dict]:
    """Parse the human-readable tool result into hit records.

    Two forms count as authorities, both tied to a 36-char node UUID:
      * `node_id: <uuid>` blocks — heading is the nearest numbered line above;
      * markdown `[cite](https://corpuslaw.us/code/<uuid>)` links.
    Prose without a UUID never becomes an authority. `<b>` highlight tags are
    stripped from snippets.
    """
    text = text or ""
    hits: list[dict] = []
    # form 1: node_id blocks
    blocks = re.split(r"(?m)^(?=\d+\.\s)", text)
    node_pat = re.compile(r"node_id:\s*([0-9a-fA-F-]{36})")
    head_pat = re.compile(r"^\d+\.\s+(.+?)\.\s*$")
    for block in blocks:
        m = node_pat.search(block)
        if not m:
            continue
        node_id = m.group(1)
        heading = ""
        for line in block.splitlines():
            hm = head_pat.match(line.strip())
            if hm:
                heading = hm.group(1).strip()
                break
        snippet = re.sub(r"</?b>", "", block)
        snippet = re.sub(r"node_id:.*", "", snippet).strip()
        hits.append({"citation": heading or node_id, "node_id": node_id,
                     "heading": heading or node_id,
                     "snippet": snippet[:500]})
    # form 2: markdown code links
    for m in re.finditer(
            r"\[(?P<cite>[^\]]{2,150})\]"
            r"\(https://corpuslaw\.us/code/(?P<uuid>[0-9a-fA-F-]{36})\)", text):
        if not any(h["node_id"] == m.group("uuid") for h in hits):
            hits.append({"citation": m.group("cite").strip(),
                         "node_id": m.group("uuid"),
                         "heading": m.group("cite").strip(),
                         "snippet": ""})
    return hits

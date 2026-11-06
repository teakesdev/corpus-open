"""CourtListener's answers kept in the case, so the same lookup is never sent twice.

Each search's results and each citation answer is appended to the case's .matter/caselaw/
(searches.jsonl, cites.jsonl). Asking the same thing again reads it back: nothing is sent, no
daily allowance is spent, and it works with lookups turned off or with no network. A `fresh`
lookup asks again and its answer, being newer, is the one read from then on. Kept: the person's
own search words and CourtListener's public answers, in the case folder they belong to. Never
the key. Network-free.
"""
import hashlib
import json
import os

from .citations import now_iso

SEARCHES = "searches.jsonl"
CITES = "cites.jsonl"
# Answers about the cite itself. 429 (over the per-request limit) and lines CourtListener did not
# answer cleanly are not answers, so they are asked again next time.
KEEP_STATUSES = (200, 300, 400, 404)
_CLUSTER_FIELDS = ("id", "absolute_url", "case_name", "case_name_short", "case_name_full", "date_filed")


def _path(matter_dir: str, name: str) -> str:
    return os.path.join(matter_dir, ".matter", "caselaw", name)


def _read(matter_dir: str, name: str) -> list[dict]:
    try:
        f = open(_path(matter_dir, name), encoding="utf-8")
    except OSError:
        return []
    out = []
    with f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # a line cut short by a crash; the rest still count
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _append(matter_dir: str, name: str, records: list[dict]) -> None:
    if not records:
        return
    path = _path(matter_dir, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, sort_keys=True) + "\n" for r in records))


def _query(text: str) -> str:
    return " ".join((text or "").split())


def _search_key(provider: str, query: str, jurisdiction: str | None) -> str:
    return hashlib.sha256(json.dumps([provider, _query(query).casefold(), jurisdiction or ""]).encode()).hexdigest()


def find_search(matter_dir: str, provider: str, query: str, limit: int, jurisdiction: str | None = None):
    """The newest saved answer to this question that covers `limit` results:
    {"retrieved_at", "results": [dict, ...]}, or None."""
    key = _search_key(provider, query, jurisdiction)
    for rec in reversed(_read(matter_dir, SEARCHES)):
        asked, results = rec.get("limit"), rec.get("results")
        if rec.get("key") != key or not isinstance(asked, int) or not isinstance(results, list):
            continue
        if asked >= limit or len(results) < asked:  # asked for as many, or CourtListener had no more
            return {"retrieved_at": rec.get("retrieved_at"), "results": [r for r in results if isinstance(r, dict)][:limit]}
    return None


def save_search(matter_dir: str, provider: str, query: str, limit: int, jurisdiction: str | None,
                results: list[dict]) -> None:
    _append(matter_dir, SEARCHES, [{
        "key": _search_key(provider, query, jurisdiction), "provider": provider, "query": _query(query),
        "jurisdiction": jurisdiction, "limit": limit, "retrieved_at": now_iso(), "results": results}])


def find_cites(matter_dir: str, cites: list[str]) -> dict[str, dict]:
    """cite -> its newest saved record {"citation", "retrieved_at", "answer"}."""
    want, out = set(cites), {}
    for rec in _read(matter_dir, CITES):
        if rec.get("citation") in want and isinstance(rec.get("answer"), dict):
            out[rec["citation"]] = rec
    return out


def _slim(item: dict) -> dict:
    """The fields citelookup reads, and no more."""
    clusters = [{k: c[k] for k in _CLUSTER_FIELDS if k in c} for c in item.get("clusters") or [] if isinstance(c, dict)]
    return {"citation": item.get("citation"), "normalized_citations": item.get("normalized_citations"),
            "status": item.get("status"), "clusters": clusters}


def save_cites(matter_dir: str, answers: dict[str, dict]) -> None:
    """Keep each cite CourtListener answered (cite -> its answer item)."""
    now = now_iso()
    _append(matter_dir, CITES, [{"citation": cite, "retrieved_at": now, "answer": _slim(item)}
                                for cite, item in answers.items()
                                if isinstance(item, dict) and item.get("status") in KEEP_STATUSES
                                and not isinstance(item.get("status"), bool)])


def counts(matter_dir: str) -> dict:
    return {"searches": len({r.get("key") for r in _read(matter_dir, SEARCHES) if r.get("key")}),
            "cites": len({r.get("citation") for r in _read(matter_dir, CITES) if r.get("citation")})}

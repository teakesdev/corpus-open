"""Provider-neutral search interface (RFC 0001 §2.1).

R2: courtlistener-free adapter is registered by import path only — urllib
lives in matterkit.adapters, not this file. Construction still fail-closed
without a human-activated grant. Results are never source-checked.
"""
import hashlib
import json
import os
from dataclasses import dataclass

from . import consent
from .citations import now_iso

SOURCE_TYPES = {"statute", "regulation", "case", "local", "auto"}


@dataclass
class SearchQuery:
    text: str
    jurisdiction: str | None = None
    source_type: str = "auto"
    as_of_date: str | None = None
    limit: int = 10


@dataclass
class SearchResult:
    authority_id: str          # provider-scoped id
    citation: str
    name: str
    snippet: str
    jurisdiction: str | None
    provenance: dict           # {provider, url, retrieved_at}
    effective_date: str | None = None


# R1 ships zero adapters by design (RFC §2.1/§2.5). R2/R3 register here only
# after a grant exists — construction refuses without one, fail-closed.
ADAPTERS: dict[str, str] = {
    # name -> import path; urllib stays out of this file (isolation scan).
    "courtlistener-free": "matterkit.adapters.courtlistener.CourtListenerFreeAdapter",
}


def _load_adapter(provider: str):
    spec = ADAPTERS.get(provider)
    if spec is None:
        raise LookupError(
            f"no adapter registered for {provider!r} — see RFC 0001 §6")
    mod_name, _, cls_name = spec.rpartition(".")
    import importlib
    return getattr(importlib.import_module(mod_name), cls_name)


def build_adapter(provider: str, matter_dir: str, caller_id: str,
                  auth_mode: str = "anonymous"):
    """Construct a registered adapter under a valid grant — or refuse."""
    cls = _load_adapter(provider)
    consent.authorize(matter_dir, provider, cls.endpoint, "query_text", caller_id)
    return cls(matter_dir=matter_dir, auth_mode=auth_mode)


def manual_search(matter_dir: str, caller_id: str, text: str,
                  provider: str = "courtlistener-free",
                  limit: int = 5, auth_mode: str = "anonymous") -> list[SearchResult]:
    """Manual query/citation lookup. Results are provenance records only —
    never written as source-checked."""
    adapter = build_adapter(provider, matter_dir, caller_id, auth_mode=auth_mode)
    return adapter.search(SearchQuery(text=text, source_type="case", limit=limit),
                          caller_id=caller_id)


def log_egress(matter_dir: str, provider: str, endpoint: str,
               payload_type: str, payload_text: str) -> dict:
    """Append the hash-only egress record. Asserted digest of what the kit
    sent — never a second copy of matter content, never an authorization
    proof (RFC §2.4 rev3)."""
    record = {
        "ts": now_iso(),
        "provider": provider,
        "endpoint": endpoint,
        "payload_type": payload_type,
        "bytes": len(payload_text or ""),
        "sha256": hashlib.sha256((payload_text or "").encode("utf-8")).hexdigest(),
    }
    os.makedirs(os.path.dirname(consent.egress_path(matter_dir)), exist_ok=True)
    with open(consent.egress_path(matter_dir), "a") as f:
        f.write(json.dumps(record) + "\n")
    return record

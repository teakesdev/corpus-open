"""Provider-neutral search interface (RFC 0001 §2.1). R1 ships the contract
plus consent/egress scaffolding ONLY — zero adapters, zero network calls.
Adapters land in R2 (courtlistener-free) and R3 (corpus, founder-gated).
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
ADAPTERS: dict[str, type] = {}


def build_adapter(provider: str, matter_dir: str, caller_id: str):
    """Construct a registered adapter under a valid grant — or refuse.

    Refusal order: unknown provider first (honest R1 answer), then consent
    (so R2+ adapters can never be constructed without a grant either).
    """
    cls = ADAPTERS.get(provider)
    if cls is None:
        raise LookupError(
            f"no adapter registered for {provider!r} — R1 ships zero adapters "
            "(RFC 0001 §2.1); see §6 R2/R3")
    # Construction-time gate; every call re-checks too (consent.authorize).
    consent.authorize(matter_dir, provider, cls.endpoint, "query_text", caller_id)
    return cls(matter_dir=matter_dir)


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

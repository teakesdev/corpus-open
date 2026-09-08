"""Cooperative consent enforcement for adapters (RFC 0001 §2.4, rev3 scope).

TRUST MODEL — read before extending: everything in this module lives inside
the runtime's write authority. consent.json, its created_by field, and
egress.jsonl can all be forged by a compromised agent. What the kit provides:

  (i)  no ACCIDENTAL egress — absent/expired/out-of-scope grants are refused
       by the kit's own code at construction and at call time;
  (ii) no LEGITIMATE self-expansion — a grant authored by the calling agent
       identity authorizes nothing (cooperative guard); cross-agent grant
       reuse is permitted at kit level and is a documented limit, not an
       oversight (tests pin this);
  (iii) compromised-agent CONTAINMENT requires a trust anchor outside the
       agent's write authority (separate broker, OS-user-owned grant store).
       The kit does not ship one; without it the kit's promise is "the kit's
       own call graph refuses," never "the system cannot egress."

Payload sensitivity ladder: query_text < passage_text < document_text.
Query text is itself matter-derived; passage/document payloads are excluded
from any default grant pattern and refuse at call time when not granted.
"""
import datetime as _dt
import json
import os

SENSITIVITY = {"query_text": 1, "passage_text": 2, "document_text": 3}


class ConsentError(PermissionError):
    """Refused by consent policy (absent / expired / out-of-scope / self-authored /
    over-budget grant). Fail closed: this is raised, never silently downgraded."""


def consent_path(matter_dir: str) -> str:
    return os.path.join(matter_dir, ".matter", "consent.json")


def egress_path(matter_dir: str) -> str:
    return os.path.join(matter_dir, ".matter", "egress.jsonl")


def load_grants(matter_dir: str) -> list[dict]:
    """Read grants. An absent, corrupt, or wrongly-shaped file means ZERO
    grants — fail closed, never partially trusted."""
    try:
        with open(consent_path(matter_dir)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    grants = data.get("grants", [])
    return [g for g in grants if isinstance(g, dict)]


def _today_usage(matter_dir: str, provider: str) -> int:
    try:
        with open(egress_path(matter_dir)) as f:
            lines = f.readlines()
    except OSError:
        return 0
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    n = 0
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("provider") == provider and str(rec.get("ts", "")).startswith(today):
            n += 1
    return n


def authorize(matter_dir: str, provider: str, endpoint: str, payload_type: str,
              caller_id: str) -> dict:
    """Return the matching grant or raise ConsentError. Called at construction
    AND per call (call-time recheck is the one that catches escalation)."""
    if payload_type not in SENSITIVITY:
        raise ConsentError(f"unknown payload type {payload_type!r}")
    for g in load_grants(matter_dir):
        if g.get("provider") != provider:
            continue
        if endpoint not in (g.get("endpoints") or []):
            continue
        if payload_type not in (g.get("payload_types") or []):
            continue
        # Cooperative guard: a grant authored by an AGENT identity authorizes
        # nothing for that agent (kind defaults to "agent" when absent, so an
        # omitted field is conservative). A grant with kind "human" authorizes
        # its author and any agent running under the human's authority.
        # Forgeable by a compromised runtime — documented limit, see above.
        if g.get("kind", "agent") == "agent" and g.get("created_by") == caller_id:
            continue
        exp = g.get("expires")
        if exp:
            try:
                if _dt.datetime.fromisoformat(exp) < _dt.datetime.now(_dt.timezone.utc):
                    continue
            except ValueError:
                continue  # unparseable expiry -> treat grant as dead
        budget = g.get("daily_budget")
        if isinstance(budget, int) and _today_usage(matter_dir, provider) >= budget:
            raise ConsentError(
                f"daily budget ({budget}) exhausted for provider {provider!r}")
        return g
    raise ConsentError(
        f"no valid grant for {provider}/{endpoint}/{payload_type} "
        f"(caller {caller_id!r}) — write .matter/consent.json first; "
        "see RFC 0001 §2.4")


def require_escalation(matter_dir: str, provider: str, endpoint: str,
                       payload_type: str, caller_id: str) -> dict:
    """For per-call escalation to passage_text/document_text. Same rules; the
    grant must explicitly name the sensitive payload type — defaults never
    imply it."""
    return authorize(matter_dir, provider, endpoint, payload_type, caller_id)

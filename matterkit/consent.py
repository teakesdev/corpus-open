"""Cooperative consent enforcement for adapters (RFC 0001 §2.4, rev4 scope).

TRUST MODEL — read before extending: everything in this module lives inside
the runtime's write authority. consent.json, its fields, and egress.jsonl can
all be forged by a compromised agent. The kit honestly provides:

  (i)   no ACCIDENTAL egress — absent/corrupt/expired/out-of-scope grants are
        refused by the kit's own code at construction and at call time;
  (ii)  ISSUER / CALLERS / SCOPE (rev4): a grant has an issuer (created_by),
        an explicit authorized_callers list, and a scope (endpoints x payload
        types x expiry x budget). kind:"human" grants authorize their issuer
        and listed callers only — explicit delegation is the intended
        headless workflow; unlisted callers are refused. kind:"agent" grants
        (the conservative default when the field is absent) are INERT
        PROPOSALS: an agent cannot confer authority it does not hold,
        including to itself. All of this is forgeable by a compromised
        runtime — cooperative enforcement, not containment;
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
    """Refused by consent policy (absent / expired / out-of-scope / unlisted
    caller / inert agent-authored grant / over budget). Fail closed: raised,
    never silently downgraded."""


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


def _identity_ok(g: dict, caller_id: str) -> tuple[bool, str]:
    """Issuer / callers / scope identity rule (rev4).

    - kind:"agent" grants (default when absent) are inert for everyone:
      self-authorship is the expansion vector the cooperative model blocks,
      and an agent cannot confer authority it does not hold.
    - kind:"human" grants authorize the issuer and explicitly listed callers.
      Unlisted callers are refused: human authorship neither authorizes every
      agent implicitly nor prevents explicit delegation.
    Forgeable by a compromised runtime — see module docstring (ii)/(iii).
    """
    if g.get("kind", "agent") != "human":
        return False, "agent-authored grants are inert proposals"
    issuer = g.get("created_by")
    callers = g.get("authorized_callers") or []
    if caller_id != issuer and caller_id not in callers:
        return False, (f"caller {caller_id!r} is neither the issuer nor in "
                       f"authorized_callers")
    return True, ""


def authorize(matter_dir: str, provider: str, endpoint: str, payload_type: str,
              caller_id: str) -> dict:
    """Return the matching grant or raise ConsentError. Called at construction
    AND per call (call-time recheck is the one that catches escalation)."""
    if payload_type not in SENSITIVITY:
        raise ConsentError(f"unknown payload type {payload_type!r}")
    refusals: list[str] = []
    for g in load_grants(matter_dir):
        if g.get("provider") != provider:
            continue
        if endpoint not in (g.get("endpoints") or []):
            continue
        if payload_type not in (g.get("payload_types") or []):
            refusals.append(f"payload {payload_type!r} outside grant scope")
            continue
        ok, why = _identity_ok(g, caller_id)
        if not ok:
            refusals.append(why)
            continue
        exp = g.get("expires")
        if exp:
            try:
                if _dt.datetime.fromisoformat(exp) < _dt.datetime.now(_dt.timezone.utc):
                    refusals.append("grant expired")
                    continue
            except ValueError:
                refusals.append("grant expiry unparseable — treated as dead")
                continue
        budget = g.get("daily_budget")
        if isinstance(budget, int) and _today_usage(matter_dir, provider) >= budget:
            raise ConsentError(
                f"daily budget ({budget}) exhausted for provider {provider!r}")
        return g
    detail = "; ".join(dict.fromkeys(refusals)) if refusals else "no matching grant"
    raise ConsentError(
        f"no valid grant for {provider}/{endpoint}/{payload_type} "
        f"(caller {caller_id!r}) — {detail}. Write .matter/consent.json per "
        "RFC 0001 §2.4")


def require_escalation(matter_dir: str, provider: str, endpoint: str,
                       payload_type: str, caller_id: str) -> dict:
    """For per-call escalation to passage_text/document_text. Same rules; the
    grant must explicitly name the sensitive payload type — defaults never
    imply it."""
    return authorize(matter_dir, provider, endpoint, payload_type, caller_id)


def save_grants(matter_dir: str, grants: list[dict]) -> None:
    path = consent_path(matter_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"grants": grants}, f, indent=2)
        f.write("\n")


def stage_proposal(matter_dir: str, provider: str, endpoints: list[str],
                   payload_types: list[str], created_by: str,
                   daily_budget: int = 20) -> dict:
    """Agent-callable: write an INERT kind=agent proposal. Authorizes no one."""
    from .citations import now_iso
    g = {
        "provider": provider,
        "endpoints": list(endpoints),
        "payload_types": list(payload_types),
        "daily_budget": daily_budget,
        "created_by": created_by,
        "kind": "agent",
        "authorized_callers": [],
        "created_at": now_iso(),
        "status": "proposal",
    }
    grants = load_grants(matter_dir)
    grants.append(g)
    save_grants(matter_dir, grants)
    return g


def activate_human_grant(matter_dir: str, provider: str, endpoints: list[str],
                         payload_types: list[str], issuer: str,
                         daily_budget: int = 20,
                         authorized_callers: list[str] | None = None) -> dict:
    """Human TTY activation. Opens /dev/tty — stdin pipes do not count.

    A confirmation string typed on the controlling terminal is the out-of-band
    step this kit ships. It is still cooperative (a compromised runtime with
    TTY access can fake it). kind=human is never written by stage_proposal.
    """
    from .citations import now_iso
    try:
        tty = open("/dev/tty", "r+", encoding="utf-8")
    except OSError as e:
        raise ConsentError(
            "activation requires a human controlling TTY (/dev/tty); "
            "agent-staged proposals stay inert") from e
    try:
        tty.write(
            f"Activate CourtListener query_text for {matter_dir!r} as human "
            f"issuer {issuer!r}? Type ACTIVATE then Enter.\n> ")
        tty.flush()
        line = (tty.readline() or "").strip()
    finally:
        tty.close()
    if line != "ACTIVATE":
        raise ConsentError(f"activation aborted (got {line!r}, wanted 'ACTIVATE')")
    g = {
        "provider": provider,
        "endpoints": list(endpoints),
        "payload_types": list(payload_types),
        "daily_budget": daily_budget,
        "created_by": issuer,
        "kind": "human",
        "authorized_callers": list(authorized_callers or []),
        "created_at": now_iso(),
        "activated_via": "tty",
        "status": "active",
    }
    grants = [x for x in load_grants(matter_dir)
              if not (x.get("provider") == provider and x.get("kind") == "agent"
                      and x.get("status") == "proposal")]
    grants.append(g)
    save_grants(matter_dir, grants)
    return g

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

Payload sensitivity ladder: citation_strings < query_text < passage_text <
document_text. citation_strings are bare citations: case cites ("123 F.4th 456"),
each line checked against a strict pattern before it is sent
(matterkit.citelookup), and statute or code cites ("42 U.S.C. § 2000e-3(a)"),
each one re-parsed by the kit before it is sent (matterkit.statutelookup); a
grant for them authorizes nothing else. Query text is itself matter-derived;
passage/document payloads are excluded from any default grant pattern and
refuse at call time when not granted.

CASE DEFAULTS (2026-11-10): a case nobody has chosen for gets two grants of
kind "default", one per CASE_DEFAULTS provider, open to every caller and
covering citation_strings only. `authorize` honors a default grant only for a
CASE_DEFAULTS provider and endpoint, only for a sensitivity-0 payload, and
never while that provider has a "declined" record. A person turns a check off
by writing a declined record (`courtlistener off`, `consent decline`, the
browser UI's Turn off); `revoke` keeps it, `ensure_defaults` never writes over
it, and only a person's own grant (write_human_grant) removes it. Agent
proposals are not a choice: they neither stop nor replace the default. Nothing
here changes how a person's own grant is checked.
"""
import datetime as _dt
import json
import os

SENSITIVITY = {"citation_strings": 0, "query_text": 1, "passage_text": 2, "document_text": 3}
ANY_CALLER = "*"
DEFAULT, DECLINED = "default", "declined"  # grant kinds besides "human" and "agent"
DEFAULT_ISSUER, DEFAULT_VIA = "matter-kit default", "case-default"
# The bare-citation checks a case gets until a person turns them off: provider -> (endpoints, daily
# budget). Each carries citation_strings and nothing else. matterkit.citelookup and
# matterkit.statutelookup name the same providers and endpoints (a test keeps them in step); they are
# spelled out here so this module imports nothing of the kit's.
CASE_DEFAULTS = {
    "courtlistener-citation-lookup": (["www.courtlistener.com/api/rest/v4/citation-lookup/"], 100),
    "corpus-statute-lookup": (["corpuslaw.us"], 50),
}
TURN_ON_HINT = ("Checking bare case and statute cites is on for a case until a person turns it off; case law "
                "search, and anything else, needs a person to turn it on. To turn case law lookups on: in "
                "Matter Kit's browser window open the case, then Case law lookups, then Turn on; or run "
                "`matter.py courtlistener on <case folder>`.")


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
        if not isinstance(rec, dict) or rec.get("event"):  # a note, not a request
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
    - A human grant listing ANY_CALLER ("*") authorizes every caller: the
      one-switch "turn on case law lookups" grant (RFC 0001 amendment
      2026-11-06). Caller ids are self-reported, so a list never told agents
      apart; the person's switch is the control, and only a person sets it.
    Forgeable by a compromised runtime — see module docstring (ii)/(iii).
    """
    if g.get("kind", "agent") != "human":
        return False, "agent-authored grants are inert proposals"
    issuer = g.get("created_by")
    callers = g.get("authorized_callers") or []
    if ANY_CALLER in callers:
        return True, ""
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
    grants = load_grants(matter_dir)
    turned_off = any(g.get("provider") == provider and g.get("kind") == DECLINED for g in grants)
    for g in grants:
        if g.get("provider") != provider:
            continue
        if endpoint not in (g.get("endpoints") or []):
            continue
        if payload_type not in (g.get("payload_types") or []):
            refusals.append(f"payload {payload_type!r} outside grant scope")
            continue
        if g.get("kind") == DEFAULT:
            # The case's own default: bare citations to a CASE_DEFAULTS endpoint, every caller,
            # and only while no person has turned it off. Never anything a person issued.
            if endpoint not in CASE_DEFAULTS.get(provider, ([], 0))[0] or SENSITIVITY[payload_type] != 0:
                refusals.append("a case's default covers bare citations only")
                continue
            if turned_off:
                continue  # the declined record speaks for itself below
        else:
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
            refusals.append(f"daily budget ({budget}) exhausted for provider {provider!r}")
            continue  # another human grant for this provider may still allow it
        return g
    if turned_off:
        refusals.insert(0, "turned off for this case by a person")
    detail = "; ".join(dict.fromkeys(refusals)) if refusals else "no matching grant"
    raise ConsentError(
        f"no valid grant for {provider}/{endpoint}/{payload_type} "
        f"(caller {caller_id!r}) — {detail}. {TURN_ON_HINT}")


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


def confirm_on_tty(question: str, word: str = "ACTIVATE") -> None:
    """Ask on the controlling terminal; raise ConsentError unless `word` is typed. Opens /dev/tty —
    stdin pipes do not count. Still cooperative (a compromised runtime with TTY access can fake it)."""
    # Separate read and write handles: text-mode "r+" needs a seekable file and
    # /dev/tty is not one, so it raised io.UnsupportedOperation (an OSError) on
    # every real terminal and reported it as a missing TTY.
    try:
        tty_in = open("/dev/tty", "r", encoding="utf-8")
    except OSError as e:
        raise ConsentError(
            "activation requires a human controlling TTY (/dev/tty); "
            f"agent-staged proposals stay inert ({e})") from e
    try:
        with open("/dev/tty", "w", encoding="utf-8") as tty_out:
            tty_out.write(f"{question} Type {word} then Enter.\n> ")
            tty_out.flush()
        line = (tty_in.readline() or "").strip()
    except OSError as e:
        raise ConsentError(
            "activation requires a human controlling TTY (/dev/tty); "
            f"agent-staged proposals stay inert ({e})") from e
    finally:
        tty_in.close()
    if line != word:
        raise ConsentError(f"activation aborted (got {line!r}, wanted {word!r})")


def write_human_grant(matter_dir: str, provider: str, endpoints: list[str],
                      payload_types: list[str], issuer: str, activated_via: str,
                      daily_budget: int | None = 20,
                      authorized_callers: list[str] | None = None) -> dict:
    """Record a grant a person has just confirmed (at the terminal or with the browser UI's button).
    Callers must have confirmed with the person first: this function asks no one. It replaces the
    provider's staged proposals, earlier human grants, its case default and a person's earlier
    "declined" record, so turning on twice leaves one grant and turning back on undoes an off."""
    from .citations import now_iso
    g = {
        "provider": provider,
        "endpoints": list(endpoints),
        "payload_types": list(payload_types),
        "daily_budget": daily_budget,
        "created_by": issuer,
        "kind": "human",
        "authorized_callers": list(authorized_callers or []),
        "created_at": now_iso(),
        "activated_via": activated_via,
        "status": "active",
    }
    grants = [x for x in load_grants(matter_dir) if x.get("provider") != provider
              or not ((x.get("kind") == "agent" and x.get("status") == "proposal")
                      or x.get("kind") in ("human", DEFAULT, DECLINED))]
    grants.append(g)
    save_grants(matter_dir, grants)
    return g


def revoke(matter_dir: str, providers: list[str]) -> int:
    """Drop every grant (human, default or proposed) for these providers. Returns how many. A
    person's "declined" record stays: revoking never brings a case default back."""
    grants = load_grants(matter_dir)
    keep = [g for g in grants if g.get("provider") not in providers or g.get("kind") == DECLINED]
    if len(keep) != len(grants):
        save_grants(matter_dir, keep)
    return len(grants) - len(keep)


def declined(matter_dir: str, provider: str) -> bool:
    """A person turned this provider's check off for the case."""
    return any(g.get("provider") == provider and g.get("kind") == DECLINED for g in load_grants(matter_dir))


def chosen(matter_dir: str, provider: str) -> bool:
    """Someone has decided about this provider for the case: a person's grant, a person's off, or the
    default already written. An agent's inert proposal decides nothing."""
    return any(g.get("provider") == provider and g.get("kind") in ("human", DEFAULT, DECLINED)
               for g in load_grants(matter_dir))


def write_declined(matter_dir: str, provider: str, by: str, via: str) -> dict:
    """Record that a person turned this provider off for the case, and drop its grants. The caller
    acts for the person (a command they ran, a button they pressed). Turning off twice leaves one record."""
    from .citations import now_iso
    rec = {"provider": provider, "kind": DECLINED, "created_by": by, "created_at": now_iso(),
           "activated_via": via}
    grants = [g for g in load_grants(matter_dir) if g.get("provider") != provider]
    grants.append(rec)
    save_grants(matter_dir, grants)
    return rec


def ensure_defaults(matter_dir: str, providers=None, note: bool = True) -> list[dict]:
    """Give each CASE_DEFAULTS provider nobody has chosen for its default grant, once. Returns what
    was written. Never touches a provider a person has granted or declined. With `note`, each
    grant written gets a line in the egress log (an "event" line, which no daily limit counts):
    an existing case is put on its default on first use, and the log says when."""
    from .citations import now_iso
    if not os.path.isdir(os.path.join(matter_dir, ".matter")):
        return []  # not a case folder: never make one here
    grants = load_grants(matter_dir)
    written = []
    for provider in (CASE_DEFAULTS if providers is None else providers):
        endpoints, budget = CASE_DEFAULTS[provider]
        if any(g.get("provider") == provider and g.get("kind") in ("human", DEFAULT, DECLINED) for g in grants):
            continue
        g = {"provider": provider, "endpoints": list(endpoints), "payload_types": ["citation_strings"],
             "daily_budget": budget, "created_by": DEFAULT_ISSUER, "kind": DEFAULT,
             "authorized_callers": [ANY_CALLER], "created_at": now_iso(), "activated_via": DEFAULT_VIA,
             "status": "active"}
        grants.append(g)
        written.append(g)
    if not written:
        return []
    save_grants(matter_dir, grants)
    if note:
        with open(egress_path(matter_dir), "a") as f:
            for g in written:
                f.write(json.dumps({"ts": g["created_at"], "event": "case-default-written",
                                    "grant_provider": g["provider"], "endpoint": g["endpoints"][0],
                                    "payload_type": "citation_strings"}) + "\n")
    return written


def activate_human_grant(matter_dir: str, provider: str, endpoints: list[str],
                         payload_types: list[str], issuer: str,
                         daily_budget: int = 20,
                         authorized_callers: list[str] | None = None) -> dict:
    """Human TTY activation: the person types ACTIVATE on the controlling terminal.

    kind=human is never written by stage_proposal.
    """
    confirm_on_tty(
        f"Activate {provider}: send {', '.join(payload_types)} to "
        f"{', '.join(endpoints)} for {matter_dir!r} as human issuer {issuer!r}?")
    return write_human_grant(matter_dir, provider, endpoints, payload_types, issuer, "tty",
                             daily_budget=daily_budget, authorized_callers=authorized_callers)

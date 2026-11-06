"""CourtListener citation-lookup adapter: POSTs bare case cites and nothing else. stdlib urllib only.

Read live 2026-11-01 (docs/superpowers/specs/2026-11-01-cite-lookup-design.md §2): a token is
required; the citations scope allows 60 a minute and every request also spends one of the
account's 5/min · 50/hour · 125/day; a request takes at most 250 citations and 64,000 characters;
a throttled lookup is HTTP 429 with `wait_until` in the body (the docs spell it `wait_util` once)
and no Retry-After. Terms: not legal advice; don't present results as verified or endorsed by
Free Law Project; no extra accounts or credential rotation to get past limits.

Transport as the R2 search adapter: no redirects, one pinned URL, the token read from
COURTLISTENER_TOKEN and written nowhere. The grant is re-checked before every request, every
payload is validated before the first is sent, and a throttled run stops. It never retries.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import citelookup, consent, credentials
from ..search import log_egress
from .courtlistener import UA, opener


class Throttled(Exception):
    def __init__(self, until: str):
        super().__init__(f"throttled until {until}")
        self.until = until


def post_form(url: str, fields: dict, token: str):
    """POST form fields with the token; return the parsed JSON. Redirects are refused."""
    if not token:
        raise consent.ConsentError("citation lookup requires a token — no egress")
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode("ascii"), method="POST",
        headers={"User-Agent": UA, "Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded", "Authorization": "Token " + token})
    try:
        with opener().open(req, timeout=60) as resp:
            raw = resp.read()
    except consent.ConsentError:
        raise
    except urllib.error.HTTPError as e:
        code = int(e.code)
        if 300 <= code < 400:
            loc = e.headers.get("Location", "") if e.headers else ""
            raise consent.ConsentError(f"refusing HTTP {code} redirect to {loc} — no follow") from e
        if code == 429:
            until = ""
            try:
                body = json.loads(e.read().decode("utf-8") or "null")
                if isinstance(body, dict):
                    until = str(body.get("wait_until") or body.get("wait_util") or "")
            except (OSError, ValueError):
                pass
            if not until and e.headers and e.headers.get("Retry-After"):
                until = f"{e.headers['Retry-After']} seconds"
            raise Throttled(until or "an unstated time") from e
        if code in (401, 403):
            raise RuntimeError(f"CourtListener refused the token (HTTP {code}); check COURTLISTENER_TOKEN") from e
        raise RuntimeError(f"CourtListener HTTP {code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not reach CourtListener: {e.reason}") from e
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise RuntimeError("CourtListener sent a response that is not JSON") from e


class CitationLookupAdapter:
    provider = citelookup.PROVIDER
    endpoint = citelookup.ENDPOINT
    payload_type = citelookup.PAYLOAD_TYPE

    def __init__(self, matter_dir: str, caller_id: str, *, post=None, clock=time.monotonic, sleep=time.sleep,
                 token: str | None = None):
        consent.authorize(matter_dir, self.provider, self.endpoint, self.payload_type, caller_id)
        # A host that owns credentials (the Hermes plugin reads them through Hermes) passes the token in;
        # the CLI leaves it None and reads COURTLISTENER_TOKEN or the key saved with Matter Kit.
        token = (credentials.courtlistener_token() if token is None else token).strip()
        if not token:
            raise consent.ConsentError(
                "citation lookup requires a free CourtListener key, saved in Matter Kit's Case law lookups "
                "panel or set as COURTLISTENER_TOKEN (CourtListener allows no anonymous lookups) "
                "— refusing egress")
        self.matter_dir, self.caller_id = matter_dir, caller_id
        self._token = token
        self._post = post or post_form
        self._clock, self._sleep = clock, sleep
        self._last: float | None = None
        self.sent = 0  # requests that left the machine (each is one egress line)

    def lookup(self, payloads: list[str]) -> tuple[list, str | None]:
        """Send each payload in turn. Returns (answers, why it stopped early or None)."""
        for p in payloads:
            citelookup.validate_payload(p)
        answers: list = []
        for p in payloads:
            try:
                consent.authorize(self.matter_dir, self.provider, self.endpoint, self.payload_type, self.caller_id)
            except consent.ConsentError as e:
                return answers, f"stopped before request {len(answers) + 1}: {e}"
            if self._last is not None:
                wait = citelookup.WAIT_SECONDS - (self._clock() - self._last)
                if wait > 0:
                    self._sleep(wait)
            self._last = self._clock()
            log_egress(self.matter_dir, self.provider, self.endpoint, self.payload_type, p)
            self.sent += 1
            try:
                body = self._post(citelookup.URL, {"text": p}, self._token)
            except Throttled as e:
                return answers, f"CourtListener throttled the lookup; try again after {e.until}"
            except (RuntimeError, consent.ConsentError) as e:
                return answers, f"stopped: {e}"
            if not isinstance(body, list):
                return answers, "stopped: CourtListener sent an unexpected response (not a list of results)"
            answers.append(body)
        return answers, None

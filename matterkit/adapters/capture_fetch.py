"""Operator-directed single-URL GET for `matter capture`. stdlib urllib only.

Not a search adapter: there is no provider API and no consent grant. The
operator names the exact URL, and that URL is the only matter-derived thing
that leaves. Every fetch still appends the hash-only egress record.

Transport: http(s) only. NO HTTP redirects — a 3xx is refused and reported, so
captures.url is always the URL that actually served the bytes. Only a plain
200 is a capture. The read is bounded.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

from ..capture import CaptureError
from ..search import log_egress

PROVIDER = "capture"
UA = "Corpus-Open-matter-kit/0.2 (matter capture; operator-directed single fetch)"
MAX_BYTES = 50 * 1024 * 1024
TIMEOUT = 30


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CaptureError(
            f"refusing HTTP {code} redirect to {newurl} — not followed. If that is "
            "the page you mean, capture that URL so the provenance row names it")


def make_fetch(matter_dir: str, max_bytes: int = MAX_BYTES, timeout: int = TIMEOUT):
    """fetch(url) -> bytes, for capture.capture_url."""
    def fetch(url: str) -> bytes:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            raise CaptureError(f"only http(s) URLs can be captured: {url!r}")
        if "@" in parts.netloc:
            raise CaptureError("URL carries credentials (user@host) — not fetched, not logged")
        log_egress(matter_dir, PROVIDER, parts.netloc, "url", url)
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
        try:
            with urllib.request.build_opener(_RefuseRedirects).open(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise CaptureError(f"HTTP {resp.status} from {url} — only a 200 is a capture")
                body = resp.read(max_bytes + 1)
        except urllib.error.HTTPError as e:
            raise CaptureError(f"HTTP {e.code} from {url} — not captured") from None
        except urllib.error.URLError as e:
            raise CaptureError(f"fetch failed for {url}: {e.reason}") from None
        if len(body) > max_bytes:
            raise CaptureError(f"response exceeds the {max_bytes}-byte bound — not captured")
        return body
    return fetch

"""What may leave the machine when a statute or code cite is checked on Corpus by default.

This module never touches the network; matterkit/adapters/corpus_search.py sends. Decisions
2026-11-10, extension 1: a case where nobody has chosen anything has its statute and code cites
looked up on Corpus (corpuslaw.us) under a grant for citation_strings, the lowest rung of RFC 0001's
sensitivity ladder. Free text to Corpus (query_text) stays a person's opt-in.

So the string sent must be one cite the kit's own parser reads, and nothing else. `payload` re-parses
whatever a caller hands it, wherever the caller got it: a string that is not exactly one parsed
statute or code cite is refused, and a parenthetical of words after a cite ("(excessive force)",
"(Rev. 2021)") is dropped, so only the cite and its subsection markers ("(a)(1)") go out.
"""
import re

from . import citations

PROVIDER = "corpus-statute-lookup"
ENDPOINT = "corpuslaw.us"  # host: what a grant names (matterkit.adapters.corpus_search.HOST)
PAYLOAD_TYPE = "citation_strings"
DAILY_BUDGET = 50
MAX_CHARS = 120
LIMIT = 5  # hits asked for; the guard reads only the one whose heading is the cite

# A subsection marker: (a), (A), (aa), (1), (12b), (iv), (xii). Nothing longer can carry a word, and a
# year ("(2018)") is not one.
_MARK = r"\((?:\d{1,3}[A-Za-z]?|[A-Za-z]{1,2}|(?i:x{0,3}(?:ix|iv|v?i{0,3})))\)"
_TAIL = re.compile(r"\s*\([^()]*\)\s*$")


def _one_cite(text: str) -> bool:
    """`text` is, from first character to last, one statute or code cite the kit's parser extracts."""
    return any(r["status"] == "extracted" and r["char_start"] == 0 and r["char_end"] == len(text)
               and r["parsed_citation"] == text and "§" in text
               for r in citations.extract_citations(text))


def payload(cite) -> str:
    """The exact string sent to Corpus for `cite`, or ValueError. See the module docstring."""
    if not isinstance(cite, str):
        raise ValueError("a statute check sends one cite, as text")
    text = cite.strip()
    if not text or len(text) > MAX_CHARS or any(not c.isprintable() for c in text):
        raise ValueError("a statute check sends one cite on one line, and nothing else")
    if not _one_cite(text):
        raise ValueError("not one statute or code cite the kit reads; refusing to send it")
    # Drop trailing parentheticals that are not subsection markers, then check what is left again.
    while True:
        m = _TAIL.search(text)
        if not m or re.fullmatch(_MARK, m.group(0).strip()):
            break
        text = text[:m.start()].rstrip()
    if re.sub(_MARK, "", text).count("(") or re.sub(_MARK, "", text).count(")"):
        raise ValueError("a statute cite's parenthetical holds more than a subsection marker; refusing to send it")
    if not _one_cite(text):
        raise ValueError("not one statute or code cite the kit reads; refusing to send it")
    return text


def jurisdiction(code):
    """A Corpus jurisdiction code ("US", "MS") or None. Anything else is refused."""
    if code is None:
        return None
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z]{2}", code):
        raise ValueError("a statute check's jurisdiction is a two-letter code or nothing")
    return code

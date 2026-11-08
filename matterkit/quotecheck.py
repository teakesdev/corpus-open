"""Quote checker: is a quotation, or a page cite, really in the case record where the text says it is?

Runs brief check's own cite and quotation pass (briefcheck._Checker) over a piece of text that has
not been written yet, read-only, and gives each quotation and each record page cite one verdict:

  exact        the quoted words are on the cited page, or anywhere in the cited document when the
               cite names no page
  wrong page   the words are in the document but not on the cited page, or the cited page is past
               the document's last page
  not found    the words are on no page of the cited document
  can't check  no readable text for the document, its bytes changed since it was registered, it is
               not in the store, or the quotation cites case law or nothing at all

Matching is brief check's: curly and straight quotes, dashes and line-break hyphens are evened out,
ellipses and bracketed insertions split a quotation, and case does not matter. Everything else
must match word for word.

"Exact" means the words are in the registered document at that page. It does not mean the
proposition the quotation supports is right, or that the document is authentic.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from . import briefcheck, store

EXACT, WRONG_PAGE, NOT_FOUND, CANT_CHECK = "exact", "wrong page", "not found", "can't check"
PROBLEMS = (NOT_FOUND, WRONG_PAGE)
_STATUS = {"found": EXACT, "wrong-page": WRONG_PAGE, "not-found": NOT_FOUND, "unchecked": CANT_CHECK}
_SOURCE_CODES = ("source-changed", "source-missing", "record-not-in-store")


@dataclass
class Result:
    verdict: str
    quote: str  # the quoted words; "" for a page cite with no quotation
    cite: str = ""  # the cite as written, e.g. "Doc. 16 at 2"
    found_on: str = ""  # where the words really are, e.g. "p. 2" or "pp. 3–4"
    checked_against: str = ""  # the text that was searched
    note: str = ""  # why it could not be checked, or what is wrong with a page cite
    line: int = 0
    span: tuple = (0, 0)  # character offsets of the sentence (brief check's unit) it came from


class _Checker(briefcheck._Checker):
    """Brief check's checker, minus the docket coverage pass (it reads every filing; one write needs
    none of it), noting which unit each row and finding came from."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.spans: dict = {}

    def coverage(self):
        pass

    def unit(self, u):
        rows, findings = len(self.rep.rows), len(self.rep.findings)
        super().unit(u)
        for item in self.rep.rows[rows:] + self.rep.findings[findings:]:
            self.spans[id(item)] = (u.start, u.end)


def _touches(span, regions) -> bool:
    a, b = span
    return any(lo <= b and a <= hi for lo, hi in regions)


def check_text(matter_dir: str, text: str, regions=None, use_pdftotext: bool = True) -> list[Result]:
    """Verdicts for the quotations and record page cites in text, in order.

    regions: when given, a list of (start, end) character offsets in text that an edit changed.
    Only results from a sentence that a region touches are kept, so an edit to a cite alone
    re-checks its quotation, and problems elsewhere in the draft don't block it. A zero-width
    region (a deletion) touches the sentence it sits in. The store is opened read-only.
    """
    rep = briefcheck.Report(matter_dir, "<pending write>", hashlib.sha256(text.encode("utf-8")).hexdigest())
    conn = store.connect_readonly(matter_dir)
    try:
        docket, _ = briefcheck.load_docket(matter_dir)
        docs = briefcheck.Docs(conn, matter_dir, docket, use_pdftotext)
        try:
            checker = _Checker(rep, conn, docs, docket, text)
            checker.run()
        finally:
            docs.close()
    finally:
        conn.close()

    def source_note(line, cite):
        for f in rep.findings:
            if f.line == line and f.code in _SOURCE_CODES and f.cite and f.cite in cite:
                return f.message
        return ""

    out = []
    for row in rep.rows:
        for q in row.quotes:
            r = Result(_STATUS.get(q.status, CANT_CHECK), q.text, q.cite, q.where, q.checked_against, q.note,
                       row.line, checker.spans.get(id(row), (0, len(text))))
            if r.verdict == CANT_CHECK and q.cite:
                r.note = source_note(row.line, q.cite) or r.note
            out.append(r)
    for f in rep.findings:
        if f.code == "pin-out-of-range":
            out.append(Result(WRONG_PAGE, "", f.cite, "", "", f.message, f.line,
                              checker.spans.get(id(f), (0, len(text)))))
    out.sort(key=lambda r: r.line)
    if regions is not None:
        out = [r for r in out if _touches(r.span, regions)]
    return out


def describe(r: Result) -> str:
    """One line for a person or an agent."""
    what = f"“{r.quote}”" if r.quote else "page cite"
    cite = f" ({r.cite})" if r.cite else ""
    if r.verdict == EXACT:
        return f"exact: {what}{cite} is at {r.found_on or 'the cited document'}"
    if r.verdict == WRONG_PAGE:
        if r.quote:
            return f"wrong page: {what}{cite} is really at {r.found_on}"
        return f"wrong page: {r.cite} — {r.note}"
    if r.verdict == NOT_FOUND:
        return f"not found: {what}{cite} is on no page of {r.checked_against or 'the cited document'}"
    return f"can't check: {what}{cite} — {r.note or 'no readable text'}"

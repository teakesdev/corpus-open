"""Filing privacy check: find what Fed. R. Civ. P. 5.2(a) says a court filing may not show in full.

Self-represented filers often put a whole Social Security number, a child's name or a bank account number
into a filing, and once it is on the public docket it is hard to take back. This module reads a draft or a
filing (Markdown, text, Word .docx, PDF; one file or a folder) and lists what looks like:

  restricted by Rule 5.2(a)  a Social Security or taxpayer identification number shown in full; a birth date
                             with more than the year; the name of a person the text calls a minor (initials
                             are allowed); a financial account or card number with more than its last four
                             digits.
  advisory                   phone numbers and street addresses. Rule 5.2(a) does not list them, but a
                             filer often does not want them public, and some courts' rules or forms ask
                             for them to be left out.

It is pattern matching, so it both misses things and flags things that are fine. A birth date and an
account number are only flagged near words that say what they are ("born", "DOB", "account"), a minor's
name only near words that say the person is a minor or a child, and a nine-digit number only when the text
calls it a Social Security or tax number. A clean scan is not proof that a filing complies with the rule.

Three promises:
- Nothing is changed or written: the scanned files are read once, in memory. No network.
- The report never repeats what it found. Every match is shown masked, keeping only what the rule itself
  allows (the last four digits, the year, initials), so the report is safe to paste or save.
- Every file it could not read, or read only in part, is listed with the reason. A folder's other files
  (images, old .doc files) are listed as not scanned, never skipped silently.

The rule's own words are quoted only from a rules pack attached to the case (`matter.py rules attach`),
with its version. With no pack attached, the report says where to read the rule instead of quoting it from
memory: Corpus (corpuslaw.us) does not hold the Federal Rules of Civil Procedure yet (checked 2026-11-22).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from xml.etree import ElementTree

from . import draftdocs

RESTRICTED, ADVISORY = "restricted", "advisory"

SSN, TIN, BIRTH, MINOR, ACCOUNT, CARD = ("SOCIAL SECURITY NUMBER", "TAXPAYER ID NUMBER", "BIRTH DATE",
                                         "POSSIBLE MINOR'S NAME", "ACCOUNT NUMBER", "CARD NUMBER")
PHONE, ADDRESS = "PHONE NUMBER", "STREET ADDRESS"

# What the rule allows instead, by finding kind: the paragraph of Rule 5.2(a) and the fix.
ALLOWED = {
    SSN: ("5.2(a)(1)", "show only the last four digits"),
    TIN: ("5.2(a)(1)", "show only the last four digits"),
    BIRTH: ("5.2(a)(2)", "show only the year of birth"),
    MINOR: ("5.2(a)(3)", "if this person is a minor, use initials only"),
    ACCOUNT: ("5.2(a)(4)", "show only the last four digits"),
    CARD: ("5.2(a)(4)", "show only the last four digits"),
    PHONE: ("advisory", "not listed in Rule 5.2(a); leave it out if it need not be public"),
    ADDRESS: ("advisory", "not listed in Rule 5.2(a); leave it out if it need not be public"),
}

TEXT_EXT = (".md", ".markdown", ".txt", ".text")
READABLE_EXT = TEXT_EXT + (".docx", ".pdf")
MAX_FILES = 500  # a folder scan stops here and says so
SKIP_DIRS = (".matter", ".git")

RULE_CITE = "Fed. R. Civ. P. 5.2(a)"
RULE_SOURCE = "https://www.uscourts.gov/sites/default/files/document/federal-rules-of-civil-procedure.pdf"  # read 2026-11-22
NOT_PROOF = ("A clean scan is not proof that a filing complies with Rule 5.2. This check matches patterns: it "
             "misses numbers and names written in ways it does not expect, it cannot see text inside images, "
             "and it cannot know who is a minor. Read the filing yourself before you file it.")
LIMITS = [
    NOT_PROOF,
    "Rule 5.2 itself has exemptions, lets a court order a filing made under seal, and says when a person "
    "waives its protection for their own information. This check does not decide any of those: read the rule.",
    "Local rules, state courts and some case types (bankruptcy, criminal, Social Security appeals, "
    "immigration) have their own privacy rules, which can require more. Check your court's rules.",
    "Masked text shows only what the rule allows (last four digits, the year, initials). Not legal advice.",
]


@dataclass
class Finding:
    kind: str
    level: str  # RESTRICTED | ADVISORY
    masked: str
    line: int  # 1-based line of the scanned text
    where: str = ""  # "para. 4", "fn. 2", "p. 3:12" for Word and PDF; "" for text (use line)
    start: int = 0
    end: int = 0

    @property
    def rule(self) -> str:
        return ALLOWED[self.kind][0]

    @property
    def fix(self) -> str:
        return ALLOWED[self.kind][1]

    def location(self) -> str:
        return self.where or f"line {self.line}"


@dataclass
class FileResult:
    path: str  # as shown in the report (relative to the case folder when inside it)
    findings: list = field(default_factory=list)
    reader: str = ""
    notes: list = field(default_factory=list)  # what the reader did not read (headers, footers, ...)


@dataclass
class Report:
    files: list = field(default_factory=list)  # FileResult, one per file scanned
    unscanned: list = field(default_factory=list)  # (path, reason)
    rule_text: str = ""  # Rule 5.2(a) quoted from an attached rules pack, with its version
    rule_note: str = ""  # where to read the rule when no pack has it

    @property
    def restricted(self) -> list:
        return [(f, x) for f in self.files for x in f.findings if x.level == RESTRICTED]

    @property
    def advisory(self) -> list:
        return [(f, x) for f in self.files for x in f.findings if x.level == ADVISORY]

    @property
    def partial(self) -> list:
        return [f for f in self.files if f.notes]

    @property
    def exit_code(self) -> int:
        """1: something Rule 5.2(a) restricts; 2: nothing restricted, but advisory items or files not
        (fully) scanned need a person; 0: nothing found in everything read."""
        if self.restricted:
            return 1
        if self.advisory or self.unscanned or self.partial:
            return 2
        return 0

    def summary(self) -> str:
        n = len(self.files)
        parts = [f"{n} file{'s' if n != 1 else ''} scanned",
                 f"{len(self.restricted)} restricted by Rule 5.2(a)",
                 f"{len(self.advisory)} advisory"]
        if self.unscanned:
            parts.append(f"{len(self.unscanned)} not scanned")
        if self.partial:
            parts.append(f"{len(self.partial)} read in part")
        return "Filing privacy check: " + ", ".join(parts)

    def render(self) -> str:
        out = [f"# {self.summary()}", "", f"> {NOT_PROOF}", ""]
        if self.restricted:
            out += ["## Restricted by Rule 5.2(a)", ""]
            out += [_row(f, x) for f, x in self.restricted]
            out.append("")
        if self.advisory:
            out += ["## Advisory (not listed in Rule 5.2(a))", ""]
            out += [_row(f, x) for f, x in self.advisory]
            out.append("")
        if self.unscanned:
            out += ["## Not scanned", ""]
            out += [f"- {p}: {why}" for p, why in self.unscanned]
            out.append("")
        if self.partial:
            out += ["## Read in part", ""]
            for f in self.partial:
                out += [f"- {f.path}: {n}" for n in f.notes]
            out.append("")
        if not (self.restricted or self.advisory):
            out += ["Nothing that looks restricted or advisory was found in the text that was read.", ""]
        out += ["## What this check does not tell you", ""] + [f"- {x}" for x in LIMITS] + [""]
        out += ["## The rule", ""]
        out += [self.rule_text] if self.rule_text else [self.rule_note]
        return "\n".join(out).rstrip() + "\n"


def _row(f: FileResult, x: Finding) -> str:
    rule = x.rule if x.level == ADVISORY else f"Rule {x.rule}"
    return f"- {f.path}, {x.location()} · {x.kind} · `{x.masked}` · {rule}: {x.fix}"


# --------------------------------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------------------------------
_SEP = r"[ \-‐-–]"
_SSN = re.compile(rf"(?<![\w\-/.])(\d{{3}}){_SEP}(\d{{2}}){_SEP}(\d{{4}})(?![\w\-/]|\.\d)")
_NINE = re.compile(r"(?<![\w\-/.$,])\d{9}(?![\w\-/]|[.,]\d)")
_EIN = re.compile(rf"(?<![\w\-/.])\d{{2}}{_SEP}\d{{7}}(?![\w\-/]|\.\d)")
_TAX_WORDS = re.compile(r"\b(?:ssn|s\.s\.n\.?|ss\s*#|soc(?:ial)?\.?\s+sec(?:urity)?\.?|tin|itin|ein|taxpayer|"
                        r"tax\s*(?:id|i\.d\.|identification|payer)|employer\s+identification)\b", re.I)
_SSN_WORDS = re.compile(r"\b(?:ssn|s\.s\.n\.?|ss\s*#|soc(?:ial)?\.?\s+sec(?:urity)?\.?)", re.I)

_DIGITS = re.compile(r"(?<![\w$.,/\-])\d(?:[ \-]?\d){4,22}(?![\w/]|[.,]\d)")
_CARD = re.compile(r"(?<![\w$.,/\-])\d(?:[ \-]?\d){12,18}(?![\w/]|[.,]\d)")
_ACCOUNT_WORDS = re.compile(r"\b(?:account|acct|a/c|card|loan|iban|checking|savings|deposit|policy|"
                            r"member(?:ship)?\s+(?:no|number|#)|customer\s+(?:no|number|#))", re.I)
_NOT_ACCOUNT = re.compile(r"\b(?:routing|aba|case|docket|claim|invoice|order|tracking|reference|bates)\b"
                          r"[\w\s.#:]{0,12}$", re.I)

_MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
          r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?")
_DATE = re.compile(
    rf"(?<![\w/])(?:"
    rf"(?P<n>\d{{1,2}}[/.\-]\d{{1,2}}[/.\-](?P<ny>\d{{4}}|\d{{2}}))"
    rf"|(?P<iso>(?P<iy>\d{{4}})-\d{{1,2}}-\d{{1,2}})"
    rf"|(?P<mdy>{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+(?P<my>\d{{4}}))?)"
    rf"|(?P<dmy>\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH},?(?:\s+(?P<dy>\d{{4}}))?)"
    rf"|(?P<m_y>{_MONTH},?\s+(?P<y>\d{{4}}))"
    rf")(?![\w/])", re.I)
_BIRTH_WORDS = re.compile(r"\b(?:born|birth\s*date|date\s+of\s+birth|birth\s*day|d\.?\s?o\.?\s?b\.?|"
                          r"b\.?\s?d\.?|birth)(?![a-z])", re.I)

_PHONE = re.compile(r"(?<![\w\-/.])(?:\+?1[ .\-]?)?(?:\(\d{3}\)\s?|\d{3}[ .\-])\d{3}[ .\-]\d{4}(?![\w\-/]|\.\d)")
_STREET = (r"Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Place|Pl|Terrace|Ter|"
           r"Circle|Cir|Parkway|Pkwy|Highway|Hwy|Trail|Trl|Square|Sq|Loop|Alley|Row|Pike|Cove|Run")
_ADDRESS = re.compile(
    rf"(?<![\w$.,/\-])\d{{1,6}}[A-Z]?\s+(?:[NSEW]\.?\s+|(?:North|South|East|West)\s+)?"
    rf"(?:(?:[A-Z][\w'.\-]*|\d{{1,3}}(?:st|nd|rd|th))\s+){{1,4}}(?:{_STREET})\b\.?"
    rf"(?:,?\s+(?:Apt|Apartment|Unit|Suite|Ste|#)\.?\s*#?\s*[\w\-]+)?")
_PO_BOX = re.compile(r"\bP\.?\s?O\.?\s+Box\s+\d+\b", re.I)

# A person the text calls a minor or a child: anchors, then capitalized names in the same sentence.
_MINOR = re.compile(
    r"\b(?:minors?(?:\s+child(?:ren)?)?|juvenile|(?:my|our|his|her|their|the|a|plaintiff's|defendant's)\s+"
    r"(?:minor\s+)?(?:son|daughter|child|children|kids?|grandson|granddaughter|grandchild(?:ren)?|"
    r"stepson|stepdaughter|stepchild)|"
    r"(?:age[ds]?|aged)\s+(?:[1-9]|1[0-7])(?!\d)|(?<!\d)(?:[1-9]|1[0-7])[ \-](?:year|yr)s?[ \-]old)\b", re.I)
_NAME_WORD = r"[A-Z][a-z]+(?:['\-][A-Z]?[a-z]+)?"
_NAMES = re.compile(rf"(?<![\w.]){_NAME_WORD}(?:\s+(?:[A-Z]\.\s+)?{_NAME_WORD}){{0,2}}(?![\w])")
_STOP = set("""
A About After Again Against All Also Although An And Any Apr April Are As At Aug August Avenue Be Because Before
Being Both But By Can Child Children Circuit City Civ Clerk Complaint Count County Court Dec December Defendant
Defendants Did District Do Doc Does Dr Each East Ex Exhibit Fed Feb February For Friday From Further Had Has Have
He Her Hers Him His How However I If In Is It Its Jan January Judge Jul July Jun June Juvenile Kid Kids Mar March
May Me Minor Minors Mo Monday Mother Father Mom Dad Mr Mrs Ms My No North Nov November Now Oct October Of On Once
One Or Order Our P Para Parent Parents Plaintiff Plaintiffs R Rule Rules Saturday School Sec Section Sep Sept
September She Since So Son Daughter South State States Street Sunday That The Their Them Then There These They This
Those Thursday To Today Tuesday Under United Until Upon Us Was We Wednesday Were West What When Where Which While
Who Why Will With Without Yes You Your Grandson Granddaughter Grandchild Stepson Stepdaughter Baby Boy Girl Age
Aged Years Year Old Ann Code Stat Tex Cal Miss Fla Ga Respondent Respondents Petitioner Petitioners Appellant
Appellee Movant Debtor Officer Attorney Counsel Guardian Ad Litem Elementary Middle High Academy College University
See Cf Id Accord Compare Contra Also Hospital Church Center Department Police Sheriff Services Agency Office Board Company Inc Corp LLC Bank Park
""".split())
_TITLES = re.compile(r"(?:\b(?:Mr|Mrs|Ms|Dr|Judge|Officer|Attorney|Counsel|Respondent|Petitioner|Defendant|Plaintiff|"
                     r"Appellant|Appellee|Debtor|Sgt|Det|Hon)\.?|\b(?:mother|father|parent|guardian|grandmother|"
                     r"grandfather|aunt|uncle|teacher|principal|caseworker|counselor|doctor|landlord|attorney),?)\s*$",
                     re.I)

_CLAUSE_END = re.compile(r"(?<!\b[A-Z])(?<!\bMr)(?<!\bMs)(?<!\bDr)(?<!\bMrs)[.;!?](?=\s)|\n\s*\n")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def _keep_last4(s: str) -> str:
    """Every digit but the last four becomes '*'; separators are kept so the shape is recognizable."""
    n = sum(c.isdigit() for c in s)
    out, seen = [], 0
    for c in s:
        if c.isdigit():
            seen += 1
            out.append(c if seen > n - 4 else "*")
        else:
            out.append(c)
    return "".join(out)


def _mask_all_but(s: str, keep: int) -> str:
    n = sum(c.isdigit() for c in s)
    out, seen = [], 0
    for c in s:
        if c.isdigit():
            seen += 1
            out.append(c if seen > n - keep else "*")
        else:
            out.append(c)
    return "".join(out)


def _year_only(m: re.Match) -> str:
    year = m.group("ny") or m.group("iy") or m.group("my") or m.group("dy") or m.group("y")
    if year and len(year) == 4:
        return f"[month and day hidden] {year}"
    return "[date hidden]"


def _initials(name: str) -> str:
    return " ".join(w[0] + "." for w in re.split(r"\s+", name) if w and w[0].isalpha())


def _address_mask(s: str) -> str:
    words = s.split()
    out = []
    for w in words:
        bare = w.strip(".,#")
        if any(c.isdigit() for c in w):
            out.append(re.sub(r"\d", "*", w))
        elif re.fullmatch(_STREET, bare, re.I) or bare.lower() in {"apt", "apartment", "unit", "suite", "ste",
                                                                  "n", "s", "e", "w", "north", "south", "east",
                                                                  "west", "p.o.", "po", "box"}:
            out.append(w)
        else:
            out.append(w[0] + "*" * (len(bare) - 1) + ("." if w.endswith(".") else ""))
    return " ".join(out)


def _near(text: str, start: int, end: int, pattern: re.Pattern, before: int, after: int) -> bool:
    """Does `pattern` match in the same line within `before` chars before or `after` chars after?"""
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    le = len(text) if le < 0 else le
    window = text[max(ls, start - before):start] + " " + text[end:min(le, end + after)]
    return bool(pattern.search(window))


def _clause(text: str, pos: int) -> tuple:
    """(start, end) of the sentence or clause around pos."""
    start = 0
    for m in _CLAUSE_END.finditer(text, 0, pos):
        start = m.end()
    m = _CLAUSE_END.search(text, pos)
    return start, (m.start() if m else len(text))


def scan_text(text: str) -> list:
    """Findings for one text, in order, each masked. Overlaps keep the restricted finding."""
    raw: list = []  # (start, end, kind, level, masked)

    for m in _SSN.finditer(text):
        area, group, serial = m.group(1), m.group(2), m.group(3)
        if area == "000" or group == "00" or serial == "0000":
            continue  # never issued: a placeholder, not a number
        if _near(text, m.start(), m.end(), _PHONE_CONTEXT, 20, 0) and not _near(
                text, m.start(), m.end(), _TAX_WORDS, 40, 10):
            continue
        kind = TIN if (area.startswith("9") and not _near(text, m.start(), m.end(), _SSN_WORDS, 40, 10)) else SSN
        raw.append((m.start(), m.end(), kind, RESTRICTED, _keep_last4(m.group(0))))
    for m in _NINE.finditer(text):
        if _near(text, m.start(), m.end(), _TAX_WORDS, 40, 10):
            kind = SSN if _near(text, m.start(), m.end(), _SSN_WORDS, 40, 10) else TIN
            raw.append((m.start(), m.end(), kind, RESTRICTED, _keep_last4(m.group(0))))
    for m in _EIN.finditer(text):
        if _near(text, m.start(), m.end(), _TAX_WORDS, 40, 10):
            raw.append((m.start(), m.end(), TIN, RESTRICTED, _keep_last4(m.group(0))))

    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits) and len(set(digits)) > 1:
            raw.append((m.start(), m.end(), CARD, RESTRICTED, _keep_last4(m.group(0))))
    for m in _DIGITS.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) < 5:
            continue
        if not _near(text, m.start(), m.end(), _ACCOUNT_WORDS, 40, 0):
            continue
        ls = text.rfind("\n", 0, m.start()) + 1
        if _NOT_ACCOUNT.search(text[max(ls, m.start() - 30):m.start()]):
            continue
        raw.append((m.start(), m.end(), ACCOUNT, RESTRICTED, _keep_last4(m.group(0))))

    for m in _DATE.finditer(text):
        if m.group("m_y") is None and m.group("n") is None and m.group("iso") is None \
                and not re.search(r"\d", m.group(0)):
            continue
        if _near(text, m.start(), m.end(), _BIRTH_WORDS, 45, 15):
            raw.append((m.start(), m.end(), BIRTH, RESTRICTED, _year_only(m)))

    for a in _MINOR.finditer(text):
        cs, ce = _clause(text, a.start())
        for n in _NAMES.finditer(text, cs, ce):
            if n.start() < a.end() and a.start() < n.end():
                continue  # the anchor's own words ("Plaintiff's son")
            words = [w for w in re.split(r"\s+", n.group(0)) if not re.fullmatch(r"[A-Z]\.", w)]
            kept_words = [w for w in words if re.sub(r"'s$", "", w) not in _STOP]
            if not kept_words:
                continue
            if kept_words != words:
                sub = re.search(r"\s+".join(map(re.escape, kept_words)), n.group(0))
                if not sub:
                    continue
                s, e = n.start() + sub.start(), n.start() + sub.end()
            else:
                s, e = n.start(), n.end()
            words = kept_words
            if re.match(r"\s*v\.?\s", text[e:e + 4]) or re.search(r"\bv\.?\s+$", text[max(cs, s - 4):s]):
                continue  # a party in a case name ("Smith v. Jones")
            if _TITLES.search(text[max(cs, s - 25):s]):
                continue  # "Respondent Mark Example", "the child's mother, Ann Example": an adult, named by role
            name = text[s:e]
            # one word counts only right next to the anchor ("my son, Jacob" / "Jacob, age 9")
            if len(words) == 1:
                gap = text[a.end():s] if s >= a.end() else text[e:a.start()]
                if not re.fullmatch(r"[\s,:\-\u2013\u2014(]*(?:named\s+|called\s+)?", gap):
                    continue
            raw.append((s, e, MINOR, RESTRICTED, _initials(name)))

    for m in _PHONE.finditer(text):
        raw.append((m.start(), m.end(), PHONE, ADVISORY, _mask_all_but(m.group(0), 2)))
    for m in _ADDRESS.finditer(text):
        raw.append((m.start(), m.end(), ADDRESS, ADVISORY, _address_mask(m.group(0))))
    for m in _PO_BOX.finditer(text):
        raw.append((m.start(), m.end(), ADDRESS, ADVISORY, re.sub(r"\d", "*", m.group(0))))

    # keep one finding per span: restricted before advisory, then the longer match
    raw.sort(key=lambda r: (r[3] != RESTRICTED, -(r[1] - r[0]), r[0]))
    kept: list = []
    for r in raw:
        if any(r[0] < k[1] and k[0] < r[1] for k in kept):
            continue
        kept.append(r)
    kept.sort(key=lambda r: r[0])
    starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]
    out = []
    import bisect
    for s, e, kind, level, masked in kept:
        out.append(Finding(kind=kind, level=level, masked=masked, line=bisect.bisect_right(starts, s),
                           start=s, end=e))
    return out


_PHONE_CONTEXT = re.compile(r"\b(?:phone|tel|telephone|cell|mobile|fax|call)\b", re.I)


# --------------------------------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------------------------------
@dataclass
class _Segment:
    text: str
    where: object  # callable: line in this segment -> location string ("" = say "line N")


@dataclass
class _Source:
    segments: list
    reader: str
    notes: list


def _read(path: str, use_pdftotext: bool):
    """(_Source, None) or (None, reason). Reads the bytes once into memory; never writes."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in READABLE_EXT:
        return None, _unreadable_reason(ext)
    try:
        with open(path, "rb") as f:
            raw = f.read(draftdocs.MAX_DRAFT_BYTES + 1)
    except OSError as e:
        return None, f"could not be opened ({e.strerror or e})"
    if len(raw) > draftdocs.MAX_DRAFT_BYTES:
        return None, f"larger than {draftdocs.MAX_DRAFT_BYTES // (1024 * 1024)} MB, so it was not read"
    try:
        if ext == ".pdf":
            return _read_pdf(raw, use_pdftotext), None
        if ext in TEXT_EXT and b"\0" in raw[:8192]:
            return None, "binary content under a text name, so it was not read as text"
        draft = draftdocs.read_draft(raw, os.path.basename(path), use_pdftotext=use_pdftotext)
        if ext == ".docx":
            return _docx_source(raw, draft), None
        return _Source([_Segment(draft.text, lambda line: "")], "text", []), None
    except draftdocs.DraftError as e:
        msg = str(e).replace("brief check", "the privacy check")
        if "tracked change" in msg.lower():
            msg += (" Deleted text in a tracked change is still inside the file, where anyone who opens it can "
                    "read it: accept or reject every change, then check again.")
        return None, msg


def _read_pdf(raw: bytes, use_pdftotext: bool) -> _Source:
    """Every line pdftotext gives, page by page, located as p. N:L. Unlike brief check's reader, nothing is
    dropped: a running header or a page footer can carry a number too."""
    if raw[:1024].find(b"%PDF-") < 0:
        raise draftdocs.DraftError("not a PDF: the file has no %PDF- header, so the .pdf extension does not match "
                                   "the content")
    exe = shutil.which("pdftotext") if use_pdftotext else None
    if not exe:
        raise draftdocs.DraftError("a PDF is read with pdftotext (part of poppler), which is not installed or was "
                                   "turned off. Install poppler (macOS: brew install poppler; Debian/Ubuntu: apt "
                                   "install poppler-utils), or check a .docx or text copy")
    try:
        p = subprocess.run([exe, "-layout", "-enc", "UTF-8", "-", "-"], input=raw, capture_output=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise draftdocs.DraftError(f"pdftotext could not read the PDF ({exc})") from None
    if p.returncode != 0:
        why = (p.stderr.decode("utf-8", "replace").strip().splitlines() or ["no message"])[0]
        raise draftdocs.DraftError(f"pdftotext could not read the PDF: {why}")
    pages = p.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if not any(pg.strip() for pg in pages):
        raise draftdocs.DraftError("the PDF has no text layer (it looks like a scan or images only), and this "
                                   "check does not do OCR: look through it yourself, or check the file it was made from")
    labels = [f"p. {n}:{i}" for n, pg in enumerate(pages, 1) for i, _ in enumerate(pg.split("\n"), 1)]
    text = "\n".join(pages)
    notes = [f"page {n} has no text layer (a scanned or image page): look at it yourself"
             for n, pg in enumerate(pages, 1) if not pg.strip()]
    return _Source([_Segment(text, lambda line: labels[line - 1] if 0 < line <= len(labels) else "")],
                   "PDF (pdftotext)", notes)


_PART_LABEL = re.compile(r"word/(header|footer)(\d*)\.xml", re.I)


def _docx_source(raw: bytes, draft) -> _Source:
    """Body, footnotes and endnotes as brief check reads them, plus what it does not read: headers, footers,
    comments and text boxes, each paragraph located by its part."""
    segments = [_Segment(draft.text, draft.where)]
    pkg = draftdocs._Package(raw)
    extra, notes = [], []  # extra: (label, paragraph text)
    for name in sorted(pkg.has(r"word/(header|footer)\d*\.xml")) + ["word/comments.xml"]:
        m = _PART_LABEL.fullmatch(name)
        label = f"{m.group(1)} {m.group(2) or ''}".strip() if m else "comment"
        try:
            root = _part_root(pkg, name)
        except draftdocs.DraftError as e:
            notes.append(f"{name} not read: {e}")
            continue
        if root is not None:
            extra += [(label, t) for t in _paragraph_texts(root)]
    doc = pkg.part(draftdocs.MAIN_PART, required=True)
    for box in doc.iter(draftdocs.W + "txbxContent"):
        extra += [("text box", t) for t in _paragraph_texts(box)]
    counts: dict = {}
    for label, t in extra:
        counts[label] = counts.get(label, 0) + 1
        where = f"{label}, para. {counts[label]}"
        segments.append(_Segment(t, lambda line, w=where: w))
    # brief check's notes list headers, footers, comments and text boxes as not read; this check reads them
    notes += [n for n in draft.notes if _not_read(n) and not n.startswith("not read:")]
    return _Source(segments, "Word (.docx)", notes)


def _part_root(pkg, name: str):
    """A header, footer or comments part, parsed under the same caps and DOCTYPE refusal brief check's reader
    applies (its own `part` insists on a root named after the file, which headers do not have)."""
    if pkg.names.count(name) == 0:
        return None
    if pkg.names.count(name) > 1:
        raise draftdocs.DraftError(f"the archive holds {name} more than once")
    info = pkg.zf.getinfo(name)
    if info.flag_bits & 0x1 or info.file_size > draftdocs.MAX_PART_BYTES:
        raise draftdocs.DraftError("encrypted or too large")
    with pkg.zf.open(info) as fh:
        data = fh.read(draftdocs.MAX_PART_BYTES + 1)
    if len(data) > draftdocs.MAX_PART_BYTES:
        raise draftdocs.DraftError("too large")
    scan = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    if scan.lstrip(b" \t\r\n")[:1] != b"<" or draftdocs._prolog_has_doctype(scan):
        raise draftdocs.DraftError("not plain UTF-8 XML, or it carries a DOCTYPE")
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as e:
        raise draftdocs.DraftError(f"malformed XML ({e})") from None


def _paragraph_texts(root) -> list:
    out = []
    for p in root.iter(draftdocs.W + "p"):
        bits = []
        for el in p.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "t" and el.text:
                bits.append(el.text)
            elif tag == "tab":
                bits.append("\t")
            elif tag in ("br", "cr"):
                bits.append("\n")
        t = "".join(bits)
        if t.strip():
            out.append(t)
    return out


def _unreadable_reason(ext: str) -> str:
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".heic", ".tif", ".tiff", ".bmp", ".webp"):
        return "an image: this check reads text only, so look at it yourself (it may show a number or a face)"
    if ext == ".doc":
        return "Word's old .doc format, which this check cannot read; save it as .docx and check that"
    if ext in (".rtf", ".odt", ".pages"):
        return f"{ext} files are not read; save it as .docx or PDF and check that"
    if not ext:
        return "no file extension, so the check does not know how to read it"
    return f"{ext} files are not read by this check"


def _files(target: str):
    """(files, skipped_over_limit). A folder is walked in name order; .matter and hidden folders skipped."""
    if not os.path.isdir(target):
        return [target], 0
    found = []
    for root, dirs, names in os.walk(target):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for n in sorted(names):
            if n.startswith(".") or n.startswith("~$"):
                continue
            found.append(os.path.join(root, n))
    return found[:MAX_FILES], max(0, len(found) - MAX_FILES)


def check(paths, case_dir: str | None = None, use_pdftotext: bool = True, shown=None,
          confine: str | None = None) -> Report:
    """Scan files or folders. `shown(path)` names a path in the report (default: relative to case_dir
    when inside it). With `confine`, a file whose real location is outside that folder (a link out) is
    listed as not scanned instead of read. Never writes anything."""
    report = Report()
    show = shown or (lambda p: _shown(p, case_dir))
    for target in paths:
        if not os.path.exists(target):
            report.unscanned.append((show(target), "no such file or folder"))
            continue
        files, over = _files(target)
        for path in files:
            if os.path.islink(path) and not os.path.exists(path):
                report.unscanned.append((show(path), "a link to a file that is no longer there"))
                continue
            if confine and not _inside(path, confine):
                report.unscanned.append((show(path), "a link to a file outside the case folder, so it is not read"))
                continue
            source, why = _read(path, use_pdftotext)
            if source is None:
                report.unscanned.append((show(path), why))
                continue
            result = FileResult(path=show(path), reader=source.reader, notes=source.notes)
            for seg in source.segments:
                for x in scan_text(seg.text):
                    x.where = seg.where(x.line)
                    result.findings.append(x)
            report.files.append(result)
        if over:
            report.unscanned.append((show(target), f"{over} more files past the first {MAX_FILES} were not "
                                                   "scanned; check a smaller folder"))
    report.rule_text, report.rule_note = rule_quote(case_dir)
    return report


def _inside(path: str, root: str) -> bool:
    real, top = os.path.realpath(path), os.path.realpath(root)
    return real == top or real.startswith(top.rstrip(os.sep) + os.sep)


def _not_read(note: str) -> bool:
    low = note.lower()
    return any(w in low for w in ("not read", "dropped", "skipped", "ignored", "not checked"))


def _shown(path: str, case_dir: str | None) -> str:
    if case_dir:
        try:
            rel = os.path.relpath(os.path.abspath(path), os.path.abspath(case_dir))
        except ValueError:
            return path
        if not rel.startswith(".."):
            return rel
    return path


def rule_quote(case_dir: str | None) -> tuple:
    """(quoted text, "") from a rules pack attached to the case, or ("", where to read it)."""
    note = (f"{RULE_CITE} is not quoted here, because no rules pack attached to this case has it and this kit "
            "does not quote a rule from memory. Corpus (corpuslaw.us) does not hold the Federal Rules of Civil "
            f"Procedure yet. Read Rule 5.2 in the official text at {RULE_SOURCE}, or attach a pack of the "
            "Federal Rules to the case (`matter.py rules build` then `rules attach`) and the next check "
            "quotes it.")
    if not case_dir:
        return "", note
    try:
        from . import rules
        code, text = rules.show(case_dir, RULE_CITE)
    except Exception:  # noqa: BLE001 - a broken pack must not stop the privacy check
        return "", note
    if code == 0 and text.strip():
        return "> " + "\n> ".join(text.splitlines()), ""
    return "", note

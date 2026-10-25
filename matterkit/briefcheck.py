"""Brief check: tie every cite, quotation and factual sentence in a draft to the store.

Read-only. The store is opened with `store.connect_readonly`; nothing is written.
For each draft unit (a sentence, with a following citation-only sentence folded
in) it resolves:

  record cites      Doc. 16 at 2 · Docs. 16 and 18 · Dkt. 27 · ECF No. 19
  pleading cites    Compl. ¶ 59 · Am. Compl. ¶¶ 67–69
  authorities       X v. Y, 579 U.S. 40, 45 (2016) · 579 U.S. at 46 · Id. at 46 ·
                    Fed. R. Civ. P. 15(a)(2) · Rule 15 · L.U.Civ.R. 7(b)(4) ·
                    42 U.S.C. § 1983 · § 1983 · Miss. Code (Ann.) § 95-1-5 · Section 95-1-5 ·
                    Section 552.101
  quotations        searched in the cited source's text, on the pinned page
  facts             {fact:a_x} / <!-- fact:a_x --> tags; uncited sentences in a
                    Facts / Background / Statement section

Record cites resolve through `.matter/docket.json`:

  {"entries":            {"16": "Filings/…/16.pdf", "23-1": "…"},
   "pleadings":          {"Compl.": "1" | "<rel_path>", "Am. Compl.": "…"},
   "operative_pleading": "1" | "<rel_path>",     (fallback for any pleading key)
   "text_copies":        {"Filings/…/16.pdf": "Filings/plaintext/16.md"},
   "bare_rule":          "Fed. R. Civ. P.",       ('Rule 15' is read as this)
   "local_rule":         "L.U.Civ.R."}            ('Local Civil Rule 7' is read as this)

Any other shape is refused. Keys starting with "_" (notes) are ignored.
A bare "Rule 5.1(b)" binds first to the most recent earlier full rule cite with the
same number ("Miss. R. Crim. P. 5.1"), and is read as `bare_rule` only when there is none.

Severity: BLOCK (exit 1), UNCHECKED (exit 2 when nothing blocks), WARN, OK.
"Tied to a source in the store" means the cited document or authority record
exists, is hash-verified and matched. It never means the proposition is correct,
the authority is good law, or the source is authentic.
"""
import bisect
import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
import zlib
from dataclasses import dataclass, field

from . import citations, ingest, store

TOOL_ID = "matter-kit brief-check 0.1"
LIMITS = ("“Tied to a source in the store” means the cited document or authority record exists in this "
          "matter's store, its bytes still match the registered hash, and the cite or quotation matched it. "
          "It does not mean the proposition is legally correct, that an authority is still good law (no citator "
          "was run), or that a source is authentic, admissible or complete.")

STATUS_OK = {"source-checked", "verified-official"}
STATUS_LOCATED = {"resolved", "research-pending"}


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------
@dataclass
class Finding:
    severity: str  # BLOCK | UNCHECKED | WARN
    code: str
    line: int
    cite: str
    message: str


@dataclass
class Cite:
    kind: str  # record | pleading | authority
    raw: str
    display: str
    target: str | None = None  # rel_path for record/pleading; authority key otherwise
    pin: str | None = None
    start: int = 0
    end: int = 0
    paras: list = field(default_factory=list)
    via_id: bool = False


@dataclass
class Quote:
    text: str
    status: str = "unchecked"  # found | wrong-page | not-found | unchecked
    checked_against: str = ""
    where: str = ""


@dataclass
class Row:
    line: int
    text: str
    cites: list = field(default_factory=list)
    quotes: list = field(default_factory=list)
    facts: list = field(default_factory=list)


@dataclass
class AuthorityUse:
    key: str
    display: str
    store_citation: str | None
    store_status: str | None
    checked_via: str | None
    lines: list = field(default_factory=list)
    pins: list = field(default_factory=list)


# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------
_REPORTERS = [
    r"U\.\s*S\.", r"S\.\s*Ct\.", r"L\.\s*Ed\.\s*2d", r"L\.\s*Ed\.",
    r"F\.\s*Supp\.\s*[23]d", r"F\.\s*Supp\.", r"F\.\s*App'x", r"Fed\.\s*App(?:'x|x)\.?",
    r"F\.\s*4th", r"F\.\s*3d", r"F\.\s*2d", r"F\.R\.D\.", r"B\.R\.", r"Fed\.\s*Cl\.",
    r"So\.\s*3d", r"So\.\s*2d", r"So\.", r"S\.W\.\s*3d", r"S\.W\.\s*2d", r"S\.W\.",
    r"N\.E\.\s*3d", r"N\.E\.\s*2d", r"N\.E\.", r"N\.W\.\s*2d", r"N\.W\.", r"S\.E\.\s*2d", r"S\.E\.",
    r"P\.\s*3d", r"P\.\s*2d", r"A\.\s*3d", r"A\.\s*2d", r"F\.",
]
_REP = "(?:" + "|".join(_REPORTERS) + ")"
_PINS = r"\d{1,5}(?:\s*[-–—]\s*\d{1,5})?(?:\s*,\s*\d{1,5}(?:\s*[-–—]\s*\d{1,5})?)*"
CASE_RE = re.compile(
    rf"(?<![\w.])(?P<vol>\d{{1,4}})\s+(?P<rep>{_REP})\s+(?P<page>\d{{1,5}})(?!\d)"
    rf"(?:\s*,\s*(?P<pin>{_PINS})(?![\d.]\d)(?:\s*n\.\s*\d+)?)?"
    r"(?:\s*\((?P<paren>[^()]{0,60}?\d{4})\))?")
SHORT_RE = re.compile(rf"(?<![\w.])(?P<vol>\d{{1,4}})\s+(?P<rep>{_REP})\s+at\s+(?P<pin>{_PINS})")
WL_RE = re.compile(r"(?P<year>\d{4})\s+WL\s+(?P<num>\d{3,9})(?:\s*,\s*at\s*\*\s*(?P<pin>\d+(?:\s*[-–]\s*\*?\d+)?))?")
RULE_RE = re.compile(
    r"(?P<base>(?:Fed|[A-Z][a-z]{1,5})\.\s*R\.\s*(?:(?:Civ|Crim|App)\.\s*P\.|Evid\.)\s*\d+(?:\.\d+)?)"
    r"(?P<sub>(?:\([A-Za-z0-9]{1,4}\))*)")
LOCAL_RE = re.compile(r"(?P<base>L\.\s*U\.\s*(?:Civ|Crim)\.\s*R\.\s*\d+(?:\.\d+)?)(?P<sub>(?:\([A-Za-z0-9]{1,4}\))*)")
RULE_TAIL_RE = re.compile(r"(?:,?\s+and\s+|\s*&\s*)(?P<cite>(?P<num>\d+(?:\.\d+)?)(?:\([A-Za-z0-9]{1,4}\))*)")
RULE_CONT_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)(?:\s*(?:\([A-Za-z0-9]{1,4}\)|[-–—,&]|and(?!\w)))*")
_SEC = r"\d[\dA-Za-z.\-]*(?:\s*\([^)]{1,24}\))*"
_SUBSEC = r"\([A-Za-z0-9]{1,4}\)(?:\s*\([A-Za-z0-9]{1,4}\))*"  # "(b)" in "21-3-15(a) and (b)": the section before
_SEC_SEP = r"\s*[,&]\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+"
SECTION_LIST_RE = re.compile(rf"§§\s*(?P<secs>{_SEC}(?:(?:{_SEC_SEP})(?:{_SEC}|{_SUBSEC}))*)")
SEC_RE = re.compile(_SEC)
LIST_MEMBER_RE = re.compile(rf"(?:{_SEC_SEP})(?:(?P<sec>{_SEC})|{_SUBSEC})")
LISTED_SEC_RE = re.compile(r"\d[\dA-Za-z.\-]*(?:\s*\([A-Za-z0-9]{1,4}\))*")  # a listed section: number + "(2)(ii)"
LOCAL_WORDS_RE = re.compile(
    r"(?<![\w.])Local(?:\s+(?:Uniform|Civil|Criminal|Bankruptcy|Admiralty|Court))*\s+Rules?\s+"
    r"(?P<num>\d+(?:\.\d+)?)(?P<sub>(?:\([A-Za-z0-9]{1,4}\))*)")
BARE_RULE_RE = re.compile(
    r"(?<![\w.])Rules?\s+(?P<num>\d+(?:\.\d+)?)(?P<sub>(?:\([A-Za-z0-9]{1,4}\))*)")
RECORD_RE = re.compile(
    r"(?P<kw>ECF\s+Nos?\.|Dkts?\.|Docs?\.)\s*"
    r"(?P<nums>\d{1,4}(?:-\d{1,3})?(?:\s*(?:,|and|&|/)\s*\d{1,4}(?:-\d{1,3})?)*)"
    rf"(?:\s*,?\s*at\s+(?P<pin>{_PINS}))?")
PLEADING_RE = re.compile(
    r"(?P<key>(?:First\s+)?Am(?:ended|\.)\s+Compl(?:aint|\.)|Compl(?:aint|\.)|FAC)\s*"
    r"(?P<sym>¶¶?)\s*"
    r"(?P<paras>\d{1,4}(?:\s*[-–—]\s*\d{1,4})?(?:\s*(?:,|and|&)\s*\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)*)")
ID_RE = re.compile(rf"(?<![\w.])[Ii]d\.(?:\s*(?:at\s+(?P<pin>{_PINS})|(?P<sym>¶¶?)\s*(?P<paras>\d{{1,4}}(?:\s*[-–—]\s*\d{{1,4}})?)))?")
FACT_TAG_RE = re.compile(r"<!--\s*fact:(?P<a>[\w-]+)\s*-->|\{fact:(?P<b>[\w-]+)\}")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
QUOTE_RE = re.compile(r"“([^”]{1,1500})”|\"([^\"\n]{1,1500})\"")
SIGNALS = {"see", "also", "cf", "cf.", "e.g.", "e.g.,", "accord", "compare", "but", "contra", "citing",
           "quoting", "and", "with", "see,", "generally"}
FACT_HEADING = re.compile(r"\b(facts?|background|statement of (?:the )?(?:case|facts)|procedural history|allegations)\b", re.I)
ABBREV = {
    "v", "vs", "id", "no", "nos", "doc", "docs", "dkt", "dkts", "ecf", "compl", "am", "ex", "exs", "fed", "r",
    "civ", "p", "evid", "app", "crim", "supp", "cir", "ct", "s", "l", "ed", "f", "so", "mr", "ms", "mrs", "dr",
    "st", "co", "corp", "inc", "ltd", "jr", "sr", "e.g", "i.e", "cf", "al", "ann", "stat", "gov't", "dep't",
    "cnty", "mun", "pub", "sec", "art", "const", "jan", "feb", "mar", "apr", "aug", "sept", "sep", "oct", "nov",
    "dec", "pp", "para", "ch", "miss", "ala", "tex", "la", "fla", "ga", "tenn", "ark", "cal", "rev", "admin",
    "proc", "mem", "op", "br", "atl", "pac", "dist", "div", "ass'n", "assn", "bros", "dept", "govt", "univ",
    "nat'l", "int'l", "comm'n", "sch", "bd", "ctr", "hosp", "ins", "mfg", "mgmt", "prods", "servs", "sys",
    "tel", "transp", "mut", "fin", "elec", "gen", "hous", "indus", "ry", "u", "n", "w", "e", "c", "cert",
    "approx", "dep", "decl", "aff", "tr", "hr", "min", "fig", "vol", "pl", "def", "defs", "pls", "mot", "resp",
}
# State abbreviations not in ABBREV, matched case-sensitively: "Ill." and "Me." are states, while
# "ill." and "me." end ordinary sentences. ("W. Va." needs "Va."; single letters are handled apart.)
STATE_ABBREV = {
    "Ariz", "Colo", "Conn", "Del", "Haw", "Ill", "Ind", "Kan", "Ky", "Me", "Md", "Mass", "Mich", "Minn", "Mo",
    "Mont", "Neb", "Nev", "Okla", "Or", "Pa", "Vt", "Va", "Wash", "Wis", "Wyo",
}
# Capitalized, these are words too ("… attended Mass."): they end a sentence unless a cite goes on after them.
# A number, "Dec." and the initials "R." / "S." always count as going on (known gap, see the word-states tests).
WORD_STATES = {"Del", "Haw", "Ill", "Ind", "Mass", "Me", "Or", "Wash"}
CITE_GOES_ON = re.compile(
    r"[\d§]"  # "(Mass. 1985)", "100 Wash. 2d 1"
    r"|(?:Rev|Gen|Comp|Ann|Admin|Adm|Sess|Legis)\.\s+(?:Stat|Code|Laws|Serv|Regs?|R)\b"  # "Rev. Stat.", not "Rev. Smith"
    r"|(?:App|Ch|Ct|Cl|Dec|Dist|Fam|Hous|Prob|Sup|Super|Regs|R|S)\.|Att'y\s+Gen\b"
    r"|C\.\s*§|Const\.\s+(?:art|pt|amend|pmbl)\b"  # "8 Del. C. § 141", "Ill. Const. art. I" (not an initial "C.")
    # a code or court word only in its cite form: "Ind. Code §", "Ind. Trial R.", not "Trial began …"
    r"|Code\s+(?:Ann\.|Regs?\.|R\.|tit\.|ch\.|§|\d)|(?:Laws|Acts)\s+(?:\d|ch\.)|Tax\s+(?:Ct\.|\d)|Trial\s+(?:Ct\.|R\.)")
NAME_CONNECT = {"v.", "of", "the", "and", "&", "for", "de", "la", "ex", "rel.", "in", "re", "ex.", "et", "al.", "a"}


def _norm_key(s: str) -> str:
    return re.sub(r"[\s.§,]", "", s).lower()


def _rep_key(rep: str) -> str:
    return re.sub(r"[\s.']", "", rep).lower()


def _case_key(vol, rep, page) -> str:
    return f"case:{vol} {_rep_key(rep)} {page}"


_ANNOTATED = re.compile(r"(?<![\w.])Ann(?:otated\b|\.|\b)")
# "Miss. Code Annotated" before a bare "§ 21-23-7": the extractor takes only the § part. The head
# starts at a state or territory abbreviation, so "Acme, Inc. Miss. Code Annotated" keys as Miss.
# and "W. Va." stays whole; a federal "U.S. Code Annotated" keeps the short-form path.
_STATE_ABBREV = (r"(?:Ala|Ariz|Ark|Cal|Colo|Conn|Del|D\.\s?C|Fla|Ga|Haw|Ill|Ind|Kan|Ky|La|Me|Md|Mass|Mich|"
                 r"Minn|Miss|Mo|Mont|Neb|Nev|N\.\s?H|N\.\s?J|N\.\s?M|N\.\s?Y|N\.\s?C|N\.\s?D|Okla|Or|Pa|"
                 r"P\.\s?R|R\.\s?I|S\.\s?C|S\.\s?D|Tenn|Tex|V\.\s?I|Vt|Va|Wash|W\.\s?Va|Wis|Wyo)\.")
_ANNOTATED_HEAD = re.compile(rf"(?<![\w.]){_STATE_ABBREV}\s*(?:[A-Z][\w'&]*\.\s*){{0,3}}"
                             r"(?:Code|Stat\.|Laws?)\s+Annotated\s*$")

# A coordinated cross-reference: "Section 21-23-7, 21-23-8, or 21-23-9" / "sections 106 and 106A" /
# "Sections 552.101 and 552.102". The number shape is the extractor's, so the two read one section.
_XREF_NUM = citations._SEC_NUM
XREF_HEAD_RE = re.compile(rf"(?:[Ss]ections?|Sec\.)\s+(?P<num>{_XREF_NUM})(?:\s*\([^)]{{1,24}}\))*")
XREF_TAIL_RE = re.compile(rf"(?:\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)"
                          rf"(?P<cite>(?P<num>{_XREF_NUM})(?:\s*\([^)]{{1,24}}\))*)"
                          r"(?![\w\-§]|\.\w|\s+[A-Z][\w'&]*\.)")


def _xref_members(s: str, a: int, b: int, rows, spans) -> list[tuple[str, int, int]]:
    """(display, start, end) for each member of a coordinated cross-reference whose extracted
    fragment is s[a:b], or [] when it is not one. The extractor keeps "Section X or Y" as one
    fragment and stops "Section X, Y" at X, so each member becomes its own short-form cite.
    A member has the head's number shape: as many hyphenated parts, and plain (no hyphen or dot)
    only if the head is plain. So "552" after "Section 552.101," is a count, not a member, while
    "552.103.5" and "21-23-7.1" are members whatever their dotted depth. The list stops at a
    number another cite claims or one followed by a dotted word ("42 U.S.C.", "1972 Miss.
    Laws"). A range ("through", "to") or "of Title N" does not cover the fragment and stays whole."""
    head = XREF_HEAD_RE.match(s, a)
    if not head or head.end() > b:
        return []
    taken = spans + [(r["char_start"], r["char_end"]) for r in rows
                     if r["status"] == "extracted" and not (a <= r["char_start"] and r["char_end"] <= b)]

    def shape(num):
        return num.count("-"), "-" in num or "." in num

    out, pos, parts = [(head.group(0), a, head.end())], head.end(), shape(head["num"])
    while (t := XREF_TAIL_RE.match(s, pos)) and shape(t["num"]) == parts \
            and not any(t.start("cite") < e and s_ < t.end("cite") for s_, e in taken):
        out.append((f"Section {t['cite']}", t.start("cite"), t.end("cite")))
        pos = t.end()
    return out if len(out) > 1 and pos >= b else []


def _statute_base(frag: str) -> tuple[str, str | None]:
    """'42 U.S.C. § 1983(a)' -> ('statute:42usc1983', '1983'). 'Ann.' / 'Annotated' is not part
    of the key: 'Miss. Code Ann. § 21-23-7' and 'Miss. Code § 21-23-7' are one statute."""
    base = re.sub(r"(\s*\([^)]{1,24}\))+\s*$", "", frag.strip())
    sec = None
    m = re.search(r"§+\s*([\dA-Za-z][\dA-Za-z.\-]*)", base) or \
        re.search(rf"(?:[Ss]ections?|Sec\.)\s+({_XREF_NUM})", base)
    if m:
        sec = m.group(1).rstrip(".")
    short = not re.search(r"[A-Za-z]{2,}", re.sub(r"(?:[Ss]ections?|Sec\.)", "", base.split("§")[0])) \
        and not re.match(r"\d", base)
    return ("statute-short:" if short else "statute:") + _norm_key(_ANNOTATED.sub("", base)), sec


def _pin_ranges(pin: str | None) -> list[tuple[int, int]]:
    out = []
    for part in re.split(r"\s*,\s*", pin or ""):
        nums = [int(n) for n in re.findall(r"\d+", part)]
        if len(nums) == 1:
            out.append((nums[0], nums[0]))
        elif len(nums) >= 2:
            a, b = nums[0], nums[1]
            if b < a and len(str(b)) < len(str(a)):  # 45-7 shorthand
                b = int(str(a)[: len(str(a)) - len(str(b))] + str(b))
            out.append((a, b))
    return out


def _covered(pin: str, checked: list[str]) -> bool:
    have = [r for p in checked for r in _pin_ranges(str(p))]
    return all(any(a <= lo and hi <= b for a, b in have) for lo, hi in _pin_ranges(pin))


def _case_name(prefix: str) -> str:
    toks = prefix.replace("*", " ").replace("_", " ").rstrip().rstrip(",").split()
    name: list[str] = []
    for i in range(len(toks) - 1, -1, -1):
        t = toks[i]
        bare = t.rstrip(".,").lower()
        if t.endswith(".") and t.lower() not in NAME_CONNECT and bare not in ABBREV and len(bare) > 1 \
                and t.rstrip(".,") not in STATE_ABBREV and name and name[0][:1].isupper():
            break  # previous sentence ended here
        if t.lower() in NAME_CONNECT or t[:1].isupper() or t[:1] in "'’":
            name.insert(0, t)
            continue
        break
    while name and name[0].lower().rstrip(",") in SIGNALS | {"see", "but", "cf.", "e.g.,"}:
        name.pop(0)
    return " ".join(name)


def scan_authorities(s: str, bare_rule: str = "Fed. R. Civ. P.", local_rule: str = "L.U.Civ.R."):
    """Yield (key, display, pin, start, end, kind, extra) for authority cites in s."""
    spans = []

    def free(a, b):
        return not any(a < e and s_ < b for s_, e in spans)

    for m in CASE_RE.finditer(s):
        spans.append((m.start(), m.end()))
        name = _case_name(s[: m.start()])
        disp = (f"{name}, " if name else "") + f"{m['vol']} {m['rep']} {m['page']}"
        yield (_case_key(m["vol"], m["rep"], m["page"]), disp, m["pin"], m.start(), m.end(), "case",
               (m["vol"], _rep_key(m["rep"])))
    for m in SHORT_RE.finditer(s):
        if free(*m.span()):
            spans.append(m.span())
            yield (f"case-short:{m['vol']} {_rep_key(m['rep'])}", m.group(0), m["pin"], m.start(), m.end(),
                   "case-short", (m["vol"], _rep_key(m["rep"])))
    for m in WL_RE.finditer(s):
        if free(*m.span()):
            spans.append(m.span())
            yield (f"wl:{m['year']}:{m['num']}", f"{m['year']} WL {m['num']}", m["pin"], m.start(), m.end(),
                   "case", None)
    for pat in (RULE_RE, LOCAL_RE):
        for m in pat.finditer(s):
            if free(*m.span()):
                spans.append(m.span())
                # re.S: a hard-wrapped draft can break the cite ("Fed. R. Civ. P.\n12")
                prefix, num = re.match(r"(.*?)(\d+(?:\.\d+)?)$", m["base"], re.S).groups()
                # a full rule cite a later bare "Rule N" can bind to; local rules are not
                full = pat is RULE_RE
                yield ("rule:" + _norm_key(m["base"]), m.group(0), None, m.start(), m.end(), "rule",
                       (prefix.strip(), num) if full else None)
                pos = m.end()
                while (t := RULE_TAIL_RE.match(s, pos)) and free(*t.span("cite")):
                    spans.append(t.span("cite"))
                    yield ("rule:" + _norm_key(prefix + t["num"]), prefix + t["cite"], None,
                           t.start("cite"), t.end("cite"), "rule", (prefix.strip(), t["num"]) if full else None)
                    pos = t.end()
    for m in LOCAL_WORDS_RE.finditer(s):
        if free(*m.span()):
            spans.append(m.span())
            words = m.group(0)
            prefix = local_rule.replace("Civ", "Crim") if "Criminal" in words else local_rule
            if re.search(r"Bankruptcy|Admiralty", words):  # no mapping known: key the words as written
                prefix = re.match(r"(.*?Rules?)\s", words, re.S).group(1).replace("Rules", "Rule")
            yield ("rule:" + _norm_key(f"{prefix} {m['num']}"), f"{words} (read as {prefix} {m['num']})",
                   None, m.start(), m.end(), "rule", None)
            pos = m.end()
            while (t := RULE_TAIL_RE.match(s, pos)) and free(*t.span("cite")):
                spans.append(t.span("cite"))
                yield ("rule:" + _norm_key(f"{prefix} {t['num']}"), f"{prefix} {t['cite']}", None,
                       t.start("cite"), t.end("cite"), "rule", None)
                pos = t.end()
    bare = bare_rule
    for m in BARE_RULE_RE.finditer(s):
        if free(*m.span()):
            spans.append(m.span())
            yield ("rule:" + _norm_key(f"{bare} {m['num']}"), m.group(0), None, m.start(), m.end(),
                   "bare-rule", (bare, m["num"]))
    # The extractor rejects "§§" (no digit after the first "§"), so scan with each list's "§§" read as "§ "
    # (same length: offsets hold) and yield every listed section as the single cite "<code> § <section>".
    lists = list(SECTION_LIST_RE.finditer(s))
    single = SECTION_LIST_RE.sub(lambda m: "§ " + m.group(0)[2:], s)
    rows = citations.extract_citations(single)
    heads = {row["char_start"] for row in rows if re.search(r"[A-Za-z§]", row["raw_fragment"])}  # not "1.01 - 1.09"
    for row in rows:
        frag = row["raw_fragment"]
        if row["status"] != "extracted" or not re.search(r"§|U\.?S\.?C|C\.?F\.?R|Code|Stat\.|Pub|[Ss]ection|Sec\.", frag):
            continue
        a, b = row["char_start"], row["char_end"]
        if frag.startswith("§") and (head := _ANNOTATED_HEAD.search(s, 0, a)):
            a, frag = head.start(), s[head.start():b]
        lst = next((m for m in lists if a <= m.start() < b), None)
        if lst is None:
            if free(a, b):
                members = _xref_members(s, a, b, rows, spans)
                spans.append((a, members[-1][2] if members else b))
                for disp, ma, mb in members or [(frag, a, b)]:
                    key, sec = _statute_base(disp)
                    yield (key, disp, None, ma, mb, "statute", sec)
            continue
        code, secs = s[a:lst.start()], lst["secs"]
        # Walk the list with its own grammar. Splitting on the separators would also split inside a member's
        # parenthetical ("21-3-15 (2 and ), 21-4"). A bare subsection ("(b)") belongs to the section before it.
        first = SEC_RE.match(secs)
        members, pos = [(0, first.group(0))], first.end()
        while m := LIST_MEMBER_RE.match(secs, pos):
            if m["sec"] is not None:
                members.append((m.start("sec"), m["sec"]))
            pos = m.end()
        for i, (off, part) in enumerate(members):
            start = lst.start("secs") + off
            # keep "21-3-15(2)(ii)", not "21-3-15 (42 U.S.C. § 1983)": the parenthetical is its own cite, and a
            # final "." ends the sentence
            part = LISTED_SEC_RE.match(part).group(0).rstrip(".")
            if i and start in heads:  # "…, 42 U.S.C. § 1983": another cite starts here, so the list has ended
                break
            lo, hi = a if i == 0 else start, start + len(part)
            if free(lo, hi):
                spans.append((lo, hi))
                key, sec = _statute_base(f"{code}§ {part}")
                yield (key, f"{code}§ {part}", None, lo, hi, "statute", sec)


def authority_keys(citation: str, bare_rule: str = "Fed. R. Civ. P.", local_rule: str = "L.U.Civ.R.") -> set:
    """Every key a stored or typed citation answers to.

    A ';' segment that is only a bare rule number ("Fed. R. Civ. P. 5(b); 6(a), (d); 15") continues the
    most recent rule prefix, so a ledger cell listing several rules keys each one. A segment holding a
    statute, case or anything unrecognised ends the run.

    A '§§' section list ("Miss. Code Ann. §§ 21-21-3, 21-3-15") keys each section as the single cite
    "Miss. Code Ann. § 21-3-15" would; with no code name before the '§§', each keys as a short form.
    scan_authorities() splits the list, so a draft citing the same list gets the same keys."""
    found = sorted(scan_authorities(citation, bare_rule, local_rule), key=lambda a: a[3])
    keys = {k for k, *_ in found} | {"full:" + _norm_key(citation)}
    prefix, pos = None, 0
    for seg in citation.split(";"):
        a, b, pos = pos, pos + len(seg), pos + len(seg) + 1
        inside = [k for k, _, _, s, *_ in found if a <= s < b]
        if inside:
            prefix = re.sub(r"\d+$", "", inside[-1]) if inside[-1].startswith("rule:") else None
        elif prefix and (m := RULE_CONT_RE.fullmatch(seg.strip().rstrip("."))):
            keys.add(prefix + _norm_key(m["num"]))
        else:
            prefix = None
    return keys


# the section of a cite written without "§": "42 U.S.C. 1983", "12 CFR 1026.1"
_CODE_TAIL_SEC = re.compile(r"(?:U\.?S\.?C\.?|C\.?F\.?R\.?|Code|Stat\.|Laws?)\s+(\d[\dA-Za-z.\-]*?)\.?$")


def statute_sections(citation: str, bare_rule: str = "Fed. R. Civ. P.", local_rule: str = "L.U.Civ.R.") -> dict:
    """Each full `statute:` key `authority_keys` gives a citation -> the section numbers
    (lowercased) the key was made from; a key with no section maps to an empty set. A short form
    with no full cite earlier in the draft binds only by these, so "§ 983" is not "§ 1983" and
    "§ 8" is not "8 U.S.C. § 1108". Whatever adds statute keys to `authority_keys` must add
    their sections here (tests/test_briefcheck_short_section.py checks that the two agree)."""
    out: dict = {}
    for key, frag, _, _, _, kind, sec in scan_authorities(citation, bare_rule, local_rule):
        if kind != "statute" or not key.startswith("statute:"):
            continue
        if not sec and (m := _CODE_TAIL_SEC.search(re.sub(r"(\s*\([^)]{1,24}\))+\s*$", "", frag.strip()))):
            sec = m.group(1)
        out.setdefault(key, set()).update({sec.lower()} if sec else set())
    return out


# --------------------------------------------------------------------------
# Draft segmentation
# --------------------------------------------------------------------------
@dataclass
class Unit:
    start: int
    end: int
    text: str
    fact_section: bool
    tags: list = field(default_factory=list)


def _blank(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


_SMALL_WORDS = {"of", "the", "and", "a", "an", "in", "on", "to", "for", "by", "with", "or", "at", "from",
                "v.", "vs.", "as", "under", "into"}


def _title_case(words: list[str]) -> bool:
    content = [w.strip("()\"'“”‘’.,:;") for w in words]
    content = [w for w in content if w and w.lower() not in _SMALL_WORDS]
    return bool(content) and all(w[0].isupper() or w[0].isdigit() or w[0] in "§¶" for w in content)


def _carries_cite(line: str, quotes: bool = True) -> bool:
    """True if the line holds something brief check verifies, so it must be read as text."""
    pats = [RECORD_RE, PLEADING_RE, CASE_RE, SHORT_RE, WL_RE] + ([QUOTE_RE, ID_RE] if quotes else [])
    return any(p.search(line) for p in pats) or (quotes and "§" in line)


def _heading(block: str, mode: str = "brief") -> int | None:
    """Heading level of a one-line block, or None.

    A numbered line (and, in complaint mode, a lettered one) is a heading only when it is
    short, carries no cite or quotation, and is either unpunctuated ("1. Background") or a
    title ending in a period or colon ("1. Statement of Facts."). "1. On January 1,
    officers …" and "12. See Doc. 16 at 3" are text. A roman-numeral line (and, in brief
    mode, a lettered one) stays a heading unless it carries a record, pleading or case cite.
    """
    line = block.strip()
    if "\n" in line or not line:
        return None
    m = re.match(r"(#{1,6})\s+\S", line)
    if m:
        return len(m.group(1))
    words = line.split()
    letters = [c for c in line if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(words) <= 12 and not line.endswith(","):
        return 1
    m = re.match(r"([IVXLC]+|[A-Z]|\d{1,3})\.\s+\S", line)
    if m and len(words) <= 25:
        tok = m.group(1)
        roman = bool(re.fullmatch(r"[IVXLC]+", tok))
        if tok.isdigit() or (mode == "complaint" and not roman):
            if len(words) > 12 or _carries_cite(line):
                return None
            end = re.search(r"([.?!:;,])\W*$", line)
            if end and not (end.group(1) in ".:" and "," not in line and _title_case(words[1:])):
                return None
            return 3 if tok.isdigit() else 2
        if _carries_cite(line, quotes=False):
            return None
        return 1 if roman else 2
    return None


def _sentence_spans(text: str, start: int, end: int):
    """Yield (s, e) sentence spans within text[start:end]. Never cuts inside a quotation."""
    s = start
    quoted = [(start + q.start(), start + q.end()) for q in QUOTE_RE.finditer(text[start:end])]
    for m in re.finditer(r"[.?!][\"”’)\]*]*\s+(?=[\"“(\[*§A-Z0-9])", text[start:end]):
        cut = start + m.start()
        if any(a < cut and start + m.end() <= b for a, b in quoted):
            continue  # the quotation continues past this boundary
        before = text[s:cut + 1]
        tok = re.search(r"(\S+)$", before)
        raw = tok.group(1).lstrip("([*\"“").rstrip(".") if tok else ""
        word = raw.lower()
        state = raw in STATE_ABBREV and (raw not in WORD_STATES or CITE_GOES_ON.match(text, start + m.end(), end))
        if text[cut] == "." and (word in ABBREV or state or (len(word) == 1 and word.isalpha())
                                 or re.fullmatch(r"(?:[a-z]\.)+[a-z]", word)
                                 or re.fullmatch(r"[a-z]+\.[a-z.]+", word)):
            continue
        e = start + m.end()
        if text[s:e].strip():
            yield _lstrip(text, s), start + m.start() + len(m.group(0).rstrip())
        s = e
    if text[s:end].strip():
        yield _lstrip(text, s), end


def _lstrip(text: str, s: int) -> int:
    while s < len(text) and text[s].isspace():
        s += 1
    return s


# --------------------------------------------------------------------------
# Documents: integrity and text
# --------------------------------------------------------------------------
class Docs:
    def __init__(self, conn, matter_dir, docket, use_pdftotext):
        self.conn, self.dir, self.docket = conn, matter_dir, docket
        self.pdftotext = shutil.which("pdftotext") if use_pdftotext else None
        self.pdftotext_version = None
        if self.pdftotext:
            try:
                p = subprocess.run([self.pdftotext, "-v"], capture_output=True, text=True, timeout=10)
                self.pdftotext_version = ((p.stderr or p.stdout).splitlines() or ["pdftotext"])[0].strip()
            except (OSError, subprocess.SubprocessError):
                self.pdftotext_version = "pdftotext (version unknown)"
        self._cache: dict = {}
        self.read: dict = {}  # rel_path -> sha256 actually verified
        self.paths: dict = {}  # rel_path -> absolute path of the verified bytes
        self.parents = None  # subcase.ParentView, opened on first parent: reference

    def close(self):
        if self.parents is not None:
            self.parents.close()

    def row(self, rel):
        return self.conn.execute("SELECT * FROM documents WHERE rel_path=?", (rel,)).fetchone()

    def verify(self, rel):
        """(row, problem) where problem is None, 'not-registered', 'missing' or 'changed'.
        A subcase's `parent:` reference is checked through the parent, read-only."""
        r = self.row(rel)
        if r is None:
            return None, "not-registered"
        if rel.startswith("parent:"):
            return self._verify_parent(rel, r)
        p = ingest.resolve_document_path(self.dir, rel)
        if p is None or not os.path.isfile(p):
            return r, "missing"
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != r["sha256"]:
            return r, "changed"
        self.read[rel] = r["sha256"]
        self.paths[rel] = p
        return r, None

    def _verify_parent(self, rel, r):
        from . import subcase
        if self.parents is None:
            self.parents = subcase.ParentView(self.dir)
        verdict = self.parents.check(rel[len("parent:"):], r["sha256"])
        if verdict == "CHANGED":
            return r, "changed"
        if verdict:
            return r, "missing"
        _, path = self.parents.locate(rel[len("parent:"):])
        self.read[rel] = r["sha256"]
        self.paths[rel] = path
        return r, None

    def text(self, rel):
        """(pages | None, page_count | None, label). pages is a list of page texts."""
        if rel in self._cache:
            return self._cache[rel]
        r, problem = self.verify(rel)
        out = (None, None, "")
        if problem is None:
            out = self._load(rel, r)
        self._cache[rel] = out
        return out

    def _load(self, rel, r):
        path = self.paths[rel]
        ext = os.path.splitext(rel)[1].lower()
        count = None
        if ext == ".pdf":
            with open(path, "rb") as f:
                count = _pdf_page_count(f.read())
        copy = (self.docket.get("text_copies") or {}).get(rel)
        if copy:
            cr, problem = self.verify(copy)
            if problem is None:
                pages, n = _split_pages(_read_text(self.paths[copy]))
                return pages, n or count, f"text copy {copy}"
        try:
            rows = self.conn.execute(
                "SELECT text FROM document_pages WHERE document_sha256=? AND text IS NOT NULL "
                "ORDER BY page_number", (r["sha256"],)).fetchall()
        except Exception:
            rows = []
        if rows:
            return [x["text"] for x in rows], len(rows), "extracted page text in the store"
        if ext in (".md", ".txt", ".markdown", ".text"):
            pages, n = _split_pages(_read_text(path))
            return pages, n, rel
        if ext == ".pdf" and self.pdftotext:
            p = subprocess.run([self.pdftotext, "-enc", "UTF-8", path, "-"], capture_output=True, timeout=120)
            if p.returncode == 0:
                pages = p.stdout.decode("utf-8", "replace").split("\f")
                if pages and not pages[-1].strip():
                    pages.pop()
                return pages, len(pages), f"{rel} read transiently with {self.pdftotext_version} (not stored)"
        return None, count, ""


def _read_text(path) -> str:
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "replace")


def _split_pages(text: str):
    parts = re.split(r"\f|<!--\s*PAGEBREAK\s*-->", text, flags=re.I)
    if len(parts) > 1:
        if not parts[-1].strip():
            parts.pop()
        return parts, len(parts)
    return [text], None


def _pages_nodes(buf: bytes) -> list[bytes]:
    """Top-level text of every /Type /Pages dictionary in buf (nested dictionaries removed)."""
    out = []
    for m in re.finditer(rb"/Type\s*/Pages\b", buf):
        depth, i = 0, m.start()
        while i > 0:  # back to the << that opens this dictionary
            if buf[i - 1:i + 1] == b">>":
                depth += 1
                i -= 2
                continue
            if buf[i - 1:i + 1] == b"<<":
                if depth == 0:
                    break
                depth -= 1
                i -= 2
                continue
            i -= 1
        start, depth, j, top = i - 1, 0, i - 1, bytearray()
        while j < len(buf) - 1:  # forward to the matching >>, keeping only depth-1 bytes
            two = buf[j:j + 2]
            if two == b"<<":
                depth += 1
                j += 2
                continue
            if two == b">>":
                depth -= 1
                j += 2
                if depth == 0:
                    break
                continue
            if depth == 1:
                top.append(buf[j])
            j += 1
        if start >= 0:
            out.append(bytes(top))
    return out


def _pdf_page_count(data: bytes) -> int | None:
    """Page count from the page tree: plain objects and /ObjStm object streams only, never
    content streams (whose text could spell anything). The root node has no /Parent."""
    nodes = _pages_nodes(_strip_streams(data))
    for m in re.finditer(rb"<<((?:(?!>>).)*?/Type\s*/ObjStm(?:(?!>>).)*?)>>\s*stream\r?\n", data, re.S):
        end = data.find(b"endstream", m.end())
        if end < 0:
            continue
        try:
            nodes += _pages_nodes(zlib.decompressobj().decompress(data[m.end():end]))
        except zlib.error:
            continue
    counts = [(b"/Parent" not in n, int(c.group(1))) for n in nodes if (c := re.search(rb"/Count\s+(\d+)", n))]
    return max(counts)[1] if counts else None


def _strip_streams(data: bytes) -> bytes:
    return re.sub(rb"stream\r?\n.*?endstream", b"stream endstream", data, flags=re.S)  # content never parsed


_QUOTE_TRANS = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "‐": "-", "‑": "-"})


def _words(s: str) -> list[str]:
    s = unicodedata.normalize("NFKC", s).replace("\u00ad", "").translate(_QUOTE_TRANS)
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    return re.findall(r"[\w']+", s.lower())


_ALTERED = re.compile(r"[\w']*(?:\[[^\]\s]{1,4}\][\w']*)+")


def _fragments(quote: str) -> list[re.Pattern]:
    """Word-sequence patterns for a quotation, in order.

    Ellipses and stand-alone bracketed insertions ("[the officer]", "[sic]") split the
    quotation. A word altered in place keeps its visible letters and frees only the
    bracketed part, up to its own length: "[M]otion" must match a source word "?otion",
    "conclud[ing]" one of "conclud" plus at most three letters ("concluded").
    """
    q = unicodedata.normalize("NFKC", quote).translate(_QUOTE_TRANS).lower()
    q = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", q)
    out = []
    for part in re.split(r"\.\s?\.\s?\.|…|(?<![\w'])\[[^\]]*\](?![\w'])|\[[^\]]*\s[^\]]*\]", q):
        toks = []
        for t in re.finditer(r"[\w']*(?:\[[^\]]*\][\w']*)+|[\w']+", part):
            w = t.group(0)
            if "[" in w:
                toks.append("".join(r"[\w']{0,%d}" % (len(seg) - 2) if seg.startswith("[") else re.escape(seg)
                                    for seg in re.split(r"(\[[^\]]*\])", w) if seg))
            else:
                toks.append(re.escape(w))
        if toks:
            out.append(re.compile(" " + " ".join(toks) + " "))
    return out


def _find(frags, text) -> int | None:
    """Offset of the first fragment if all fragments occur in order in text's words, else None."""
    hay = " " + " ".join(_words(text)) + " "
    pos, first = 0, None
    for fr in frags:
        m = fr.search(hay, pos)
        if m is None:
            return None
        first = m.start() if first is None else first
        pos = m.end() - 1
    return first


def _paragraphs(text: str) -> dict:
    marks = list(re.finditer(r"(?m)^[ \t]*(?:¶\s*)?(?:\*\*)?(\d{1,4})\s*[.)](?:\*\*)?\s+\S", text))
    out = {}
    for i, m in enumerate(marks):
        n = int(m.group(1))
        if n not in out:
            out[n] = text[m.start(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
    return out


# --------------------------------------------------------------------------
# Docket map
# --------------------------------------------------------------------------
def load_docket(matter_dir, path=None):
    """(docket map, sha256) — refuses a map that is not the documented shape."""
    p = path or os.path.join(matter_dir, ".matter", "docket.json")
    if not os.path.isfile(p):
        return {}, None
    with open(p, "rb") as f:
        raw = f.read()
    try:
        d = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise ValueError(f"docket map {p} is not valid JSON: {e}") from None
    if not isinstance(d, dict):
        raise ValueError(f"docket map {p} must be a JSON object")
    for name in ("entries", "pleadings", "text_copies"):
        v = d.get(name, {})
        if not isinstance(v, dict) or not all(isinstance(k, str) and isinstance(x, str) for k, x in v.items()):
            raise ValueError(f"docket map {p}: '{name}' must map strings to strings")
    bad = [k for k in d.get("entries", {}) if not re.fullmatch(r"\d{1,4}(?:-\d{1,3})?", k)]
    if bad:
        raise ValueError(f"docket map {p}: entry keys must be docket numbers like '16' or '23-1', not {bad}")
    for name in ("bare_rule", "local_rule", "operative_pleading"):
        if name in d and not isinstance(d[name], str):
            raise ValueError(f"docket map {p}: '{name}' must be a string")
    return d, hashlib.sha256(raw).hexdigest()


def rule_prefixes(docket: dict) -> tuple[str, str]:
    """(bare 'Rule N' prefix, spelled-out 'Local … Rule N' prefix) for this matter."""
    return docket.get("bare_rule", "Fed. R. Civ. P."), docket.get("local_rule", "L.U.Civ.R.")


def suggest_docket(conn) -> dict:
    """Docket numbers derived from registered filenames under PACER/docket folders."""
    found: dict = {}
    for r in conn.execute("SELECT rel_path FROM documents ORDER BY rel_path"):
        rel = r["rel_path"]
        if rel.startswith("parent:") or not re.search(r"pacer|docket|dkt|ecf", rel, re.I):
            continue
        base = os.path.basename(rel)
        if os.path.splitext(base)[1].lower() in (".zip", ".json", ".sqlite", ".db", ".gz", ".tar"):
            continue
        base = re.sub(r"\d:?\d{2}-[a-z]{2}-\d{3,5}(?:-[A-Z]{2,4})*", " ", base)  # case numbers
        base = re.sub(r"\d{4}-\d{2}-\d{2}|\d{8}", " ", base)  # dates
        tagged = re.search(r"(?i)(?:dkt|doc|ecf)[ _.-]?(?:no[ _.-]?)?(\d{1,4})(?:-(\d{1,3}))?(?!\d)", base)
        m = re.fullmatch(r"(\d{1,4})(?:-(\d{1,3}))?\.\w+", base) or tagged
        if m:
            key = m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")
        else:
            nums = [t for t in re.split(r"[-_ .]", os.path.splitext(base)[0]) if re.fullmatch(r"\d{1,3}", t)]
            if not nums:
                continue
            key = str(int(nums[0]))
        found.setdefault(key, []).append(rel)
    entries = {k: v[0] for k, v in found.items() if len(v) == 1}
    ambiguous = {k: v for k, v in found.items() if len(v) > 1}
    return {"entries": dict(sorted(entries.items(), key=lambda kv: _entry_sort(kv[0]))),
            **({"ambiguous": ambiguous} if ambiguous else {})}


def _entry_sort(k):
    a, _, b = k.partition("-")
    return (int(a), int(b or 0))


def _compress(nums: list[int]) -> list[str]:
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}–{nums[j]}")
        i = j + 1
    return out


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------
class Report:
    def __init__(self, matter_dir, draft_path, draft_sha):
        self.matter_dir, self.draft_path, self.draft_sha256 = matter_dir, draft_path, draft_sha
        self.findings: list[Finding] = []
        self.rows: list[Row] = []
        self.authorities: list[AuthorityUse] = []
        self.docket_gaps: list[str] = []
        self.docket_refs: list[str] = []
        self.inputs: dict = {}
        self.pdftotext_version = None
        self.docket_sha = None
        self.docket_path = ".matter/docket.json"
        self.mode = "brief"
        self.notes: list[tuple[int, str]] = []  # complaint mode: alleged quotations with no cited source

    def add(self, severity, code, line, cite, message):
        self.findings.append(Finding(severity, code, line, cite, message))

    @property
    def exit_code(self) -> int:
        sev = {f.severity for f in self.findings}
        return 1 if "BLOCK" in sev else 2 if "UNCHECKED" in sev else 0

    def summary(self) -> str:
        n = {s: sum(1 for f in self.findings if f.severity == s) for s in ("BLOCK", "UNCHECKED", "WARN")}
        state = {1: "BLOCKED", 2: "NEEDS A HUMAN", 0: "ALL TIED TO SOURCES"}[self.exit_code]
        if self.exit_code == 0 and n["WARN"]:
            state = "TIED TO SOURCES, WITH WARNINGS TO READ"
        listed = ""
        if self.mode == "complaint" and self.notes:
            k = len(self.notes)
            listed = f"; {k} alleged quotation{'s' if k != 1 else ''} listed for the pleader to confirm"
        return (f"{state}: {n['BLOCK']} blocking, {n['UNCHECKED']} unchecked, {n['WARN']} warnings; "
                f"{len(self.rows)} cited or tagged sentences, {len(self.authorities)} authorities{listed}")

    def render(self) -> str:
        from .citations import now_iso
        esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
        short = lambda s, n=160: esc(s if len(s) <= n else s[: n - 1] + "…")  # noqa: E731
        out = [f"# Brief check: {os.path.basename(self.draft_path)}", "",
               f"**Result: {self.summary()}**", "", f"> {LIMITS}", "",
               f"- Draft: `{self.draft_path}` · sha256 `{self.draft_sha256}`",
               f"- Matter: `{os.path.abspath(self.matter_dir)}` · store opened read-only",
               f"- Docket map: `{self.docket_path}` " + (f"sha256 `{self.docket_sha}`" if self.docket_sha else "(none)"),
               f"- PDF text: {self.pdftotext_version or 'not used (PDF quotes without a text copy are unchecked)'}",
               f"- Mode: {self.mode}" + (" (allegations need no record cite; uncited sentences and alleged "
                                         "quotations are not flagged; every cite, authority and sourced quotation is "
                                         "still checked)" if self.mode == "complaint" else
                                         " (every factual sentence needs a cite)"),
               f"- Tool: {TOOL_ID} · checked {now_iso()}", ""]
        for sev, title in (("BLOCK", "Blocking"), ("UNCHECKED", "Needs a human (not checked)"), ("WARN", "Warnings")):
            items = [f for f in self.findings if f.severity == sev]
            out += [f"## {title} ({len(items)})", ""]
            if items:
                out += ["| Line | Cite or sentence | Problem | Code |", "|---:|---|---|---|"]
                out += [f"| {f.line} | {short(f.cite, 90)} | {esc(f.message)} | `{f.code}` |"
                        for f in sorted(items, key=lambda f: f.line)]
            else:
                out.append("None.")
            out.append("")
        if self.mode == "complaint":
            out += [f"## Alleged quotations with no cited source ({len(self.notes)})", "",
                    "The pleader vouches for these by signing. Confirm each against any recording or transcript.", ""]
            out += [f"- line {ln}: “{short(q, 120)}”" for ln, q in self.notes] or ["None."]
            out.append("")
        out += ["## Proposition-to-source table", "",
                "| Line | Proposition | Cited source | Quotes |", "|---:|---|---|---|"]
        for r in self.rows:
            cites = "<br>".join(esc(_cite_cell(c)) for c in r.cites) + \
                ("<br>" if r.cites and r.facts else "") + "<br>".join(f"fact `{a}`" for a in r.facts)
            quotes = "<br>".join(f"{q.status}: “{short(q.text, 50)}”" + (f" ({esc(q.where)})" if q.where else "")
                                 for q in r.quotes) or "—"
            out.append(f"| {r.line} | {short(r.text)} | {cites or '—'} | {quotes} |")
        out += ["", "## Authorities", "",
                "| Authority | Lines | Pins | Store status | Checked via |", "|---|---|---|---|---|"]
        for a in self.authorities:
            out.append(f"| {esc(a.display)} | {', '.join(map(str, sorted(set(a.lines))))} | "
                       f"{esc(', '.join(p for p in a.pins if p)) or '—'} | {a.store_status or '**not logged**'} | "
                       f"{esc(a.checked_via or '—')} |")
        out += ["", "## Docket coverage", "",
                "Entries not in the store: " + (", ".join(self.docket_gaps) or "none below the highest known entry") + "."]
        out += [f"- {esc(x)}" for x in self.docket_refs]
        out += ["", "## Documents read (hash-verified)", ""]
        out += [f"- `{rel}` sha256 `{sha[:16]}…`" for rel, sha in sorted(self.inputs.items())] or ["None."]
        return "\n".join(out) + "\n"


def _cite_cell(c: Cite) -> str:
    pin = f" at {c.pin}" if c.pin else ""
    paras = f" ¶ {', '.join(map(str, c.paras))}" if c.paras else ""
    via = " (Id.)" if c.via_id else ""
    tgt = f" → `{c.target}`" if c.kind != "authority" and c.target else ""
    return f"{c.display}{pin}{paras}{via}{tgt}"


MODES = ("brief", "complaint")


def check(matter_dir: str, draft_path: str, use_pdftotext: bool = True, docket_path: str | None = None,
          mode: str = "brief") -> Report:
    """mode "brief": every factual sentence needs a cite. mode "complaint": allegations need
    none (uncited sentences and quotations are not flagged), but every cite, authority and
    sourced quotation is still checked, and a HYPOTHESIS/INFERENCE/UNKNOWN fact tag is flagged."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; use one of: {', '.join(MODES)}")
    with open(draft_path, "rb") as f:
        raw = f.read()
    if draft_path.lower().endswith((".docx", ".pdf", ".doc")):
        raise ValueError("brief check reads Markdown or plain text; check the .md copy of the draft")
    text = raw.decode("utf-8", "replace")
    rep = Report(matter_dir, draft_path, hashlib.sha256(raw).hexdigest())
    rep.mode = mode
    conn = store.connect_readonly(matter_dir)
    try:
        docket, rep.docket_sha = load_docket(matter_dir, docket_path)
        rep.docket_path = docket_path or ".matter/docket.json"
        docs = Docs(conn, matter_dir, docket, use_pdftotext)
        rep.pdftotext_version = docs.pdftotext_version
        try:
            _Checker(rep, conn, docs, docket, text).run()
        finally:
            docs.close()
        rep.inputs = dict(docs.read)
    finally:
        conn.close()
    return rep


class _Checker:
    def __init__(self, rep, conn, docs, docket, text):
        self.rep, self.conn, self.docs, self.docket = rep, conn, docs, docket
        self.raw_text = text
        tags = [(m.start(), m.group("a") or m.group("b")) for m in FACT_TAG_RE.finditer(text)]
        self.text = _blank(_blank(text, COMMENT_RE), FACT_TAG_RE)
        self.tags = tags
        self.nl = [i for i, c in enumerate(text) if c == "\n"]
        self.suggested = suggest_docket(conn)["entries"]
        self.auth_index = self._authority_index()
        self.statute_secs = self._statute_sections()
        self.last_target: Cite | None = None
        self.last_unit_targets: set = set()
        self.full_cases: dict = {}   # (vol, rep) -> case key seen in draft
        self.full_statutes: dict = {}  # section -> statute key seen in draft
        self.full_rules: dict = {}  # rule number -> [(offset, rule key, prefix, line)] seen in draft
        self.uses: dict = {}
        self._dup_warned: set = set()

    def line(self, off: int) -> int:
        return bisect.bisect_right(self.nl, off - 1) + 1

    # ---- segmentation -----------------------------------------------------
    def units(self) -> list[Unit]:
        units, fact_level = [], None
        for bm in re.finditer(r"(?:[^\n]|\n(?![ \t]*\n))+", self.text):
            block = bm.group(0)
            if not block.strip():
                continue
            lvl = _heading(block, self.rep.mode)
            if lvl is not None:
                if FACT_HEADING.search(block):
                    fact_level = lvl
                elif fact_level is not None and lvl <= fact_level:
                    fact_level = None
                continue
            spans = list(_sentence_spans(self.text, bm.start(), bm.end()))
            merged: list[list[int]] = []
            for s, e in spans:
                if merged and self._citation_only(self.text[s:e]):
                    merged[-1][1] = e
                else:
                    merged.append([s, e])
            units += [Unit(s, e, self.text[s:e], fact_level is not None) for s, e in merged]
        for off, aid in self.tags:
            host = [u for u in units if u.start <= off] or units[:1]
            if host:
                host[-1].tags.append(aid)
        return units

    def _citation_only(self, s: str) -> bool:
        rest = s
        for pat in (CASE_RE, SHORT_RE, WL_RE, RULE_RE, LOCAL_RE, RECORD_RE, PLEADING_RE, ID_RE, BARE_RULE_RE):
            rest = pat.sub(" ", rest)
        rest = re.sub(r"\([^)]*\)", " ", rest)
        words = [w for w in re.findall(r"[A-Za-z]{2,}", rest) if w.lower() not in SIGNALS]
        has_cite = rest != s
        return has_cite and len(words) <= 4 and not QUOTE_RE.search(s)

    # ---- authorities ------------------------------------------------------
    def _authority_index(self) -> dict:
        idx: dict = {}
        for r in self.conn.execute("SELECT * FROM authorities"):
            keys = authority_keys(r["citation"], *rule_prefixes(self.docket))
            for k in keys:
                idx.setdefault(k, []).append(r)
        return idx

    def _statute_sections(self) -> dict:
        """Logged full statute key -> its section numbers, across every logged citation."""
        secs: dict = {}
        for r in self.conn.execute("SELECT citation FROM authorities"):
            for k, v in statute_sections(r["citation"], *rule_prefixes(self.docket)).items():
                secs.setdefault(k, set()).update(v)
        return secs

    def _scan_authorities(self, s: str):
        return scan_authorities(s, *rule_prefixes(self.docket))

    def _lookup(self, key):
        rows = list({r["id"]: r for r in self.auth_index.get(key) or []}.values())
        rank = {"source-checked": 0, "verified-official": 0, "resolved": 1, "research-pending": 1,
                "stale-flagged": 2, "unverified": 3}
        return sorted(rows, key=lambda r: rank.get(r["status"], 4))[0] if rows else None

    def _use(self, key, display, row, line, pin):
        u = self.uses.get(key)
        if u is None:
            u = self.uses[key] = AuthorityUse(key, display, row["citation"] if row else None,
                                              row["status"] if row else None, row["checked_via"] if row else None)
        u.lines.append(line)
        u.pins.append(pin)
        return u

    def _judge_authority(self, key, display, row, line, pin, raw):
        self._use(key, display, row, line, pin)
        if row is None:
            self.rep.add("BLOCK", "authority-not-logged", line, raw,
                         "not in the store's authorities; log it and record a source check (authority check)")
            return
        dups = {r["id"]: r for r in self.auth_index.get(key) or []}
        if len(dups) > 1 and key not in self._dup_warned:
            self._dup_warned.add(key)
            self.rep.add("WARN", "authority-duplicate", line, raw, "several store rows answer to this cite: " +
                         ", ".join(f"{r['id']} [{r['status']}]" for r in dups.values()) + f"; judged by {row['id']}")
        st = row["status"]
        if st in STATUS_OK:
            try:
                ev = json.loads(row["check_evidence"] or "null")
            except ValueError:
                ev = None
            ev = ev if isinstance(ev, dict) else {}
            pins = ev.get("pins")
            if pin and ev.get("pins_unconfirmed") and not pins:
                self.rep.add("WARN", "pins-unconfirmed", line, raw,
                             f"the recorded source check did not confirm pinpoints; verify p. {pin} by hand")
            if pin and pins and not _covered(pin, pins):
                self.rep.add("WARN", "pin-not-in-check", line, raw,
                             f"pin {pin} is outside the pages recorded in the source check ({', '.join(map(str, pins))})")
        elif st in STATUS_LOCATED:
            self.rep.add("UNCHECKED", "authority-not-source-checked", line, raw,
                         f"store status '{st}': located but no source check recorded")
        elif st == "stale-flagged":
            self.rep.add("BLOCK", "authority-stale-flagged", line, raw,
                         f"a source check flagged a currency question: {row['check_evidence'] or '—'}")
        else:
            self.rep.add("BLOCK", "authority-unverified", line, raw, f"store status '{st}'")

    # ---- per-unit ---------------------------------------------------------
    def run(self):
        for u in self.units():
            self.unit(u)
        self.rep.authorities = list(self.uses.values())
        self.coverage()

    def unit(self, u: Unit):
        line = self.line(u.start)
        cites: list[Cite] = []
        claimed: list[tuple[int, int]] = []

        def free(a, b):
            return not any(a < e and s < b for s, e in claimed)

        # record cites
        for m in RECORD_RE.finditer(u.text):
            claimed.append(m.span())
            nums = re.findall(r"\d{1,4}(?:-\d{1,3})?", m["nums"])
            if m["pin"] and len(nums) > 1:
                self.rep.add("WARN", "pin-ambiguous", line, m.group(0),
                             f"one pin for several entries; checked against Doc. {nums[-1]} only — cite each page separately")
            for i, n in enumerate(nums):
                c = Cite("record", m.group(0), f"Doc. {n}", None, m["pin"] if i == len(nums) - 1 else None,
                         m.start(), m.end())
                c.target = self.resolve_record(n, line, m.group(0))
                if c.target:
                    self.check_pin(c, line)
                cites.append(c)
        for m in PLEADING_RE.finditer(u.text):
            if not free(*m.span()):
                continue
            claimed.append(m.span())
            key = "Am. Compl." if re.match(r"(?:First\s+)?Am|FAC", m["key"]) else "Compl."
            paras = [n for lo, hi in _pin_ranges(m["paras"]) for n in range(lo, min(hi, lo + 200) + 1)]
            c = Cite("pleading", m.group(0), key, None, None, m.start(), m.end(), paras)
            c.target = self.resolve_pleading(key, line, m.group(0))
            if c.target:
                self.check_paras(c, line)
            cites.append(c)
        for key, disp, pin, a, b, kind, extra in self._scan_authorities(u.text):
            if not free(a, b):
                continue
            claimed.append((a, b))
            raw = u.text[a:b] if re.search(r"[A-Za-z§]", u.text[a:b]) else disp  # "… and 15"
            c = Cite("authority", raw, disp, key, pin, a, b)
            self.resolve_authority(c, kind, extra, line, u.start + a)
            cites.append(c)
        cites.sort(key=lambda c: c.start)
        for m in ID_RE.finditer(u.text):
            if not free(*m.span()):
                continue
            claimed.append(m.span())
            prev = [x for x in cites if x.start < m.start()]
            base = max(prev, key=lambda c: c.start) if prev else self.last_target
            if base is None:
                self.rep.add("BLOCK", "id-unbound", line, m.group(0), "Id. has no preceding citation")
                continue
            if not prev and len(self.last_unit_targets) > 1:
                self.rep.add("WARN", "id-ambiguous", line, m.group(0),
                             "the preceding citation sentence cites more than one source")
            paras = [n for lo, hi in _pin_ranges(m["paras"]) for n in range(lo, hi + 1)] if m["paras"] else []
            c = Cite(base.kind, m.group(0), base.display, base.target, m["pin"], m.start(), m.end(),
                     paras or (base.paras if base.kind == "pleading" else []), via_id=True)
            if c.kind == "record" and c.target:
                self.check_pin(c, line)
            elif c.kind == "pleading" and c.target and paras:
                self.check_paras(c, line)
            elif c.kind == "pleading" and c.target and c.pin:
                self.check_pin(c, line)
            elif c.kind == "authority":
                self._judge_authority(c.target, c.display, self._lookup(c.target), line, c.pin, m.group(0))
            cites.append(c)
        cites.sort(key=lambda c: c.start)
        if cites:
            self.last_target = cites[-1]
            self.last_unit_targets = {(c.kind, c.target) for c in cites}
        quotes = self.quotes(u, cites, line)
        facts = self.facts(u, line)
        if cites or quotes or facts:
            self.rep.rows.append(Row(line, " ".join(u.text.split()), cites, quotes, facts))
        elif self.rep.mode == "brief" and u.fact_section and len(u.text.split()) >= 4:
            self.rep.add("UNCHECKED", "uncited-fact", line, " ".join(u.text.split()),
                         "factual sentence with no record, pleading or authority cite")

    # ---- resolution -------------------------------------------------------
    def resolve_record(self, n, line, raw) -> str | None:
        rel = (self.docket.get("entries") or {}).get(n)
        if rel is None:
            rel = self.suggested.get(n)
            if rel is None:
                self.rep.add("BLOCK", "record-not-in-store", line, raw,
                             f"docket entry {n} is not in the store (no docket.json entry, no matching filename)")
                return None
            self.rep.add("UNCHECKED", "record-unconfirmed", line, raw,
                         f"Doc. {n} matched by filename only (`{rel}`); confirm it in .matter/docket.json")
        return rel if self.integrity(rel, line, raw) else None

    def resolve_pleading(self, key, line, raw) -> str | None:
        ref = (self.docket.get("pleadings") or {}).get(key) or self.docket.get("operative_pleading")
        if not ref:
            self.rep.add("BLOCK", "record-not-in-store", line, raw,
                         f"no pleading configured for '{key}' in .matter/docket.json")
            return None
        rel = str(ref)
        if re.fullmatch(r"\d{1,4}(?:-\d{1,3})?", rel):
            rel = (self.docket.get("entries") or {}).get(str(ref))
            if rel is None and self.suggested.get(str(ref)):
                rel = self.suggested[str(ref)]
                self.rep.add("UNCHECKED", "record-unconfirmed", line, raw,
                             f"pleading '{key}' (Doc. {ref}) matched by filename only (`{rel}`); confirm it in .matter/docket.json")
        if rel is None:
            self.rep.add("BLOCK", "record-not-in-store", line, raw, f"pleading '{key}' (Doc. {ref}) is not in the store")
            return None
        return rel if self.integrity(rel, line, raw) else None

    def integrity(self, rel, line, raw) -> bool:
        _, problem = self.docs.verify(rel)
        if problem:
            msg = {"not-registered": "mapped in docket.json but not registered in the store" + (
                       "; register the parent reference first (matter.py fact add <subcase> --source "
                       f"{rel}@<locator> …)" if rel.startswith("parent:") else ""),
                   "missing": "registered but the file is missing", "changed": "bytes changed since registration"}
            code = "source-changed" if problem == "changed" else "record-not-in-store" \
                if problem == "not-registered" else "source-missing"
            self.rep.add("BLOCK", code, line, raw, f"`{rel}`: {msg[problem]}")
            return False
        return True

    def check_pin(self, c: Cite, line):
        if not c.pin:
            return
        _, count, _ = self.docs.text(c.target)
        ranges = _pin_ranges(c.pin)
        if count is None:
            self.rep.add("UNCHECKED", "pin-unchecked", line, c.raw, f"page count of `{c.target}` unknown")
        elif any(hi > count or lo < 1 for lo, hi in ranges):
            self.rep.add("BLOCK", "pin-out-of-range", line, c.raw, f"`{c.target}` has {count} pages")

    def check_paras(self, c: Cite, line):
        pages, _, label = self.docs.text(c.target)
        if pages is None:
            self.rep.add("UNCHECKED", "paragraph-unchecked", line, c.raw, f"no readable text for `{c.target}`")
            return
        have = _paragraphs("\n".join(pages))
        missing = [n for n in c.paras if n not in have]
        if missing:
            self.rep.add("BLOCK", "paragraph-not-found", line, c.raw,
                         f"¶ {', '.join(map(str, missing))} not found in {label}")

    def resolve_authority(self, c: Cite, kind, extra, line, at=0):
        """`at` is the cite's offset in the draft, so a bare rule binds only to earlier full cites."""
        if kind == "case":
            if extra:
                self.full_cases.setdefault(extra, c.target)
        elif kind == "case-short":
            full = self.full_cases.get(extra)
            if full:
                c.target = full
                c.display = self.uses[full].display if full in self.uses else c.display
            else:
                cands = {k for k in self.auth_index if k.startswith(f"case:{extra[0]} {extra[1]} ")}
                if len(cands) == 1:
                    c.target = cands.pop()
                self.rep.add("WARN", "short-form-without-full-cite", line, c.raw,
                             "short form with no full citation earlier in the draft")
        elif kind == "statute":
            sec = extra
            if c.target.startswith("statute-short:"):
                full = self.full_statutes.get(sec)
                if full:
                    c.target = full
                else:
                    cands = {k for k, secs in self.statute_secs.items() if sec and sec.lower() in secs}
                    if len(cands) == 1:
                        c.target = cands.pop()
                    self.rep.add("WARN", "short-form-without-full-cite", line, c.raw,
                                 "section short form with no full citation earlier in the draft")
            elif sec:
                self.full_statutes.setdefault(sec, c.target)
        elif kind == "rule" and extra:
            prefix, num = extra
            self.full_rules.setdefault(num, []).append((at, c.target, prefix, line))
        elif kind == "bare-rule":
            # like a case short form: the most recent earlier full cite to the same rule number,
            # else the docket map's bare_rule
            bare, num = extra
            rest = c.raw.split(None, 1)[1]
            earlier = [r for r in self.full_rules.get(num, []) if r[0] < at]
            if earlier:
                _, c.target, prefix, full_line = max(earlier)
                c.display = f"{c.raw} (read as {prefix} {rest}, after the full cite on line {full_line})"
            else:
                c.display = f"{c.raw} (read as {bare} {rest})"
        row = self._lookup(c.target)
        self._judge_authority(c.target, self.uses[c.target].display if c.target in self.uses else c.display,
                              row, line, c.pin, c.raw)

    # ---- quotes and facts -------------------------------------------------
    def quotes(self, u: Unit, cites, line) -> list[Quote]:
        out = []
        for m in QUOTE_RE.finditer(u.text):
            qtext = m.group(1) or m.group(2)
            frags = _fragments(qtext)
            if not frags:
                continue
            q = Quote(qtext)
            out.append(q)
            after = [c for c in cites if c.start >= m.end()]
            before = [c for c in cites if c.start < m.start()]
            src = after[0] if after else (before[-1] if before else None)
            if src is None:
                if self.rep.mode == "complaint":  # alleged speech, vouched for by the pleader
                    self.rep.notes.append((line, qtext))
                else:
                    self.rep.add("UNCHECKED", "quote-uncited", line, qtext, "quotation with no cite in the sentence")
                continue
            if src.kind == "authority":
                q.status = "unchecked"
                self.rep.add("UNCHECKED", "quote-unchecked", line, qtext,
                             f"text of {src.display} is not in the store; check the quotation by hand")
                continue
            if not src.target:
                continue
            pages, _, label = self.docs.text(src.target)
            if pages is None:
                q.status = "unchecked"
                self.rep.add("UNCHECKED", "quote-unchecked", line, qtext,
                             f"no readable text for `{src.target}`; name a text copy in docket.json or extract it")
                continue
            q.checked_against = label
            if src.kind == "pleading" and src.paras:
                paras = _paragraphs("\n".join(pages))
                scope = "\n".join(paras.get(n, "") for n in src.paras)
                if _find(frags, scope) is not None:
                    q.status, q.where = "found", f"¶ {', '.join(map(str, src.paras))}"
                elif _find(frags, "\n".join(pages)) is not None:
                    q.status = "wrong-page"
                    self.rep.add("WARN", "quote-wrong-paragraph", line, qtext,
                                 f"found in {label} but not in the cited paragraph(s)")
                else:
                    q.status = "not-found"
                    self.rep.add("BLOCK", "quote-not-found", line, qtext, f"not found in {label}")
                continue
            hit_pages = [i + 1 for i, p in enumerate(pages) if _find(frags, p) is not None]
            pins = _pin_ranges(src.pin)
            if not hit_pages:  # a quotation spanning a page break counts on both pages
                pairs = [i + 1 for i in range(len(pages) - 1) if _find(frags, pages[i] + "\n" + pages[i + 1]) is not None]
                if pairs:
                    p = pairs[0]
                    if pins and not any(lo <= x <= hi for x in (p, p + 1) for lo, hi in pins):
                        q.status, q.where = "wrong-page", f"pp. {p}–{p + 1}"
                        self.rep.add("WARN", "quote-wrong-page", line, qtext,
                                     f"found across pp. {p}–{p + 1}, not at the cited p. {src.pin}")
                    else:
                        q.status, q.where = "found", f"pp. {p}–{p + 1}"
                    continue
                if _find(frags, "\n".join(pages)) is not None:
                    hit_pages = [0]  # spans more than one break
            if not hit_pages:
                q.status = "not-found"
                self.rep.add("BLOCK", "quote-not-found", line, qtext, f"not found in {label}")
            elif pins and hit_pages != [0] and not any(lo <= p <= hi for p in hit_pages for lo, hi in pins):
                q.status, q.where = "wrong-page", f"p. {', '.join(map(str, hit_pages))}"
                self.rep.add("WARN", "quote-wrong-page", line, qtext,
                             f"found on p. {', '.join(map(str, hit_pages))}, not at the cited p. {src.pin}")
            else:
                q.status = "found"
                q.where = "across a page break" if hit_pages == [0] else f"p. {', '.join(map(str, hit_pages))}"
        return out

    def facts(self, u: Unit, line) -> list[str]:
        for aid in u.tags:
            r = self.conn.execute("SELECT * FROM assertions WHERE id=?", (aid,)).fetchone()
            if r is None:
                self.rep.add("BLOCK", "fact-unknown", line, aid, "no such assertion in the store")
                continue
            complaint = self.rep.mode == "complaint"
            if complaint and r["status"] in ("HYPOTHESIS", "INFERENCE", "UNKNOWN"):
                self.rep.add("WARN", "fact-not-documented", line, aid,
                             f"assertion is labeled {r['status']}; plead it on information and belief with its basis, or leave it out")
            elif not complaint and r["status"] != "DOCUMENTED FACT":
                self.rep.add("WARN", "fact-not-documented", line, aid,
                             f"assertion is labeled {r['status']}; do not present it as established fact")
            n = self.conn.execute("SELECT COUNT(*) FROM assertion_sources WHERE assertion_id=?", (aid,)).fetchone()[0]
            if n == 0 and not (complaint and r["status"] == "ALLEGATION"):
                self.rep.add("WARN", "fact-unsupported", line, aid, "assertion has no sources (UNSUPPORTED)")
        return list(u.tags)

    # ---- docket coverage --------------------------------------------------
    def coverage(self):
        entries = dict(self.suggested)
        entries.update(self.docket.get("entries") or {})
        known = {int(k) for k in entries if k.isdigit()}
        known_keys = set(entries)
        cited = {int(n) for f in self.rep.findings if f.code == "record-not-in-store"
                 for n in re.findall(r"docket entry (\d+)", f.message)}
        top = max(known | cited | {0})
        self.rep.docket_gaps = _compress([n for n in range(1, top + 1) if n not in known])
        refs = {}
        for key, rel in sorted(entries.items(), key=lambda kv: _entry_sort(kv[0])):
            r, problem = self.docs.verify(rel)
            if problem:
                continue
            pages, _, _ = self.docs.text(rel)
            for i, p in enumerate(pages or []):
                for m in RECORD_RE.finditer(p):
                    for n in re.findall(r"\d{1,4}(?:-\d{1,3})?", m["nums"]):
                        if n not in known_keys and n != key:
                            refs.setdefault(n, f"Doc. {n} is cited in Doc. {key} p. {i + 1} but is not in the store")
        self.rep.docket_refs = [refs[k] for k in sorted(refs, key=_entry_sort)]

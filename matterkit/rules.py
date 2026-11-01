"""Court rules packs: one rule set at one version, kept as Markdown with an anchor for each rule
and subsection, and attached to a matter so that a cite resolves to its text.

A pack is a folder holding `manifest.json` and `rules.md`. `rules.md` is a header, the line
BODY_MARK, then the body: the source text's words, unchanged and in order, with only headings
(`## Rule 7. MOTIONS`), anchors (`<a id="lucivr-7-b-4"></a>`) and bracketed labels
(`**[7(b)(4)]**`) added. The build fails unless the body's words, with that markup removed,
equal the source text's words. That proves the text layer was carried over whole; it cannot
see glyphs the text layer already lost, or columns read across, so those are reported as
warnings for a human to check against the source.

A matter attaches packs in `.matter/rules.json`. A pack from outside the matter is copied to
Research/Rules/<id>_<version>/ and both files are registered, so `verify` covers them. There is
no schema change. A pack is derived text, not the official copy: quote the official source in
anything filed.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import ingest, store

FORMAT = "matter-kit rules pack 0.1"
ATTACH_FORMAT = "matter-kit rules attachments 0.1"
BUILDER = "matter-kit rules 0.1"
BODY_MARK = "<!-- rules-pack:body -->"
ATTACH_FILE = os.path.join(".matter", "rules.json")
PACK_ROOT = os.path.join("Research", "Rules")
PACK_FILES = ("manifest.json", "rules.md")
CHECK_FOUND = ("none", "amended")  # what a recorded check of the court's website found
PASSING = ("ok", "CHECKED")  # pack states that need no action now
REVIEW_AFTER_DAYS = 365  # most courts amend their rules once a year
DEFAULT_HEADING = r"^Rule\s+(?P<num>\d+(?:\.\d+)?)\.?\s+(?P<title>[A-Z].*)$"
DERIVED = "derived text, not the official copy: quote the official source in anything filed"

_ID = re.compile(r"[a-z0-9][a-z0-9.-]{0,63}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,31}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SHA = re.compile(r"[0-9a-f]{64}")
_ANCHOR_PREFIX = re.compile(r"[a-z0-9]+")
_NUM = r"\d+(?:\.\d+)?"
_SUB = r"(?:\s*\([A-Za-z0-9]{1,5}\))*"
PAGE_NO = re.compile(r"(?:[Pp]age\s+)?[-–]?\s*(?P<n>\d{1,4}|[ivxlc]{1,7})\s*[-–]?(?:\s+of\s+\d{1,4})?")
MARKER = re.compile(r"\((?P<tok>[0-9]{1,2}|[A-Z]{1,2}|[a-z]{1,5})\)(?=\s|$)")
# --markers dotted: an outline's "A." "1." "a." "i." at a line's start, or a parenthesized marker under them
DOTTED = re.compile(r"(?:(?P<dot>[0-9]{1,2}|[A-Z]|[a-z]|[ivx]{2,4})\.|\((?P<par>[0-9]{1,2}|[A-Z]{1,2}|[a-z]{1,5})\))(?=\s|$)")
MARKER_STYLES = ("paren", "dotted")
# an anchor the builder added: the tag and then a label; source text shaped like a tag alone stays text
ANCHOR_LINE = re.compile(r'<a id="(?P<id>[^"]+)"></a>(?=\*\*\[)')
ADDED = re.compile(r'^<a id="[^"]+"></a>\*\*\[[^\]\n]*\]\*\* ?')
GAP = re.compile(r"\S {5,}\S")
_CONTENTS = re.compile(r"(?:\.\s?){4,}\s*[\divxlc]+\s*$", re.I)
BARE = re.compile(rf"(?:Rule\s+)?(?P<num>{_NUM})(?P<sub>{_SUB})\s*\.?")

# subsection levels, outermost first: (a) (1) (A) (i), and a (1) list under a capital item
LETTER, DIGIT, CAPITAL, ROMAN, NESTED = range(5)
PARENT = {LETTER: (), DIGIT: (LETTER,), CAPITAL: (DIGIT, LETTER), ROMAN: (CAPITAL, DIGIT, LETTER),
          NESTED: (CAPITAL,)}


class RulesError(ValueError):
    """A refused rules operation; nothing was written."""


def _roman(n: int) -> str:
    out = ""
    for v, s in ((100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"),
                 (1, "i")):
        while n >= v:
            out, n = out + s, n - v
    return out


ROMANS = [_roman(n) for n in range(1, 200)]
ROMAN_VALUE = {r: n for n, r in enumerate(ROMANS, 1)}
ROMAN_NEXT = dict(zip(ROMANS, ROMANS[1:]))


def _next_alpha(tok: str) -> str:
    """(a) -> (b), (z) -> (aa), (aa) -> (bb); capitals alike."""
    c = tok[-1]
    if c in "zZ":
        return ("a" if c == "z" else "A") * (len(tok) + 1)
    return chr(ord(c) + 1) * len(tok)


def sha256_file(path: str) -> str:
    return ingest.sha256_file(path)


def default_anchor_prefix(cite_prefix: str) -> str:
    """'L.U.Civ.R.' -> 'lucivr'; 'Fed. R. Civ. P.' -> 'fedrcivp'."""
    return re.sub(r"[^a-z0-9]", "", cite_prefix.lower())


def _norm(raw: str) -> str:
    return " ".join(raw.replace("\f", "").split())


def _indent(raw: str) -> int:
    s = raw.replace("\f", "").expandtabs(8)
    return len(s) - len(s.lstrip())


def _num_key(num: str) -> tuple:
    return tuple(int(x) for x in num.split("."))


def _title(rest: str) -> str:
    """The capitalized words that open a heading ('MOTIONS AND PAPERS (a) Form' -> 'MOTIONS AND PAPERS')."""
    words = []
    for w in rest.split():
        if re.search(r"[a-z]", w) or MARKER.fullmatch(w):
            break
        words.append(w)
    return (" ".join(words) or " ".join(rest.split())).rstrip(".;,: ")


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------
@dataclass
class Built:
    body: list            # Markdown lines after BODY_MARK
    source_words: list    # the words the body must hold, in order
    rules: list           # rule numbers given a heading, in order
    anchors: list         # anchor ids, in order
    dropped: list         # (page, text) of the page-number lines left out
    notes: list           # subsection placements that were guessed or skipped
    warnings: list        # what the word check cannot see: columns, lost glyphs
    words: dict           # where every source word went


def body_words(body_lines) -> list:
    """The body's words with the added headings, anchors and labels removed."""
    kept = [ADDED.sub("", l) for l in body_lines if not l.startswith("## ")]
    return " ".join(kept).split()


def _find_line(lines, text, start, name):
    """The first line at or after `start` that begins with `text` (spacing aside) and is not a
    table-of-contents entry (dot leaders and a page number)."""
    want = _norm(text)
    for i in range(start, len(lines)):
        line = _norm(lines[i][1])
        if want and line.startswith(want) and not _CONTENTS.search(line):
            return i
    raise RulesError(f"{name} line {text!r} is not in the source text")


def _page_value(n: str):
    return int(n) if n.isdigit() else ROMAN_VALUE.get(n)


def _check_page_sequence(lines, idx) -> list:
    """Page numbers count up with the pages: value minus page index is the same for every
    arabic number ("3", "- 3 -", "Page 3 of 40"), and for every roman one. A page may lack a
    number; a lone number that breaks the sequence is probably rule text, so the build stops.
    Returns a warning for a number that is the only one of its kind, which no sequence checks."""
    warnings = []
    for roman in (True, False):
        seq = [(lines[i][0], lines[i][1].strip()) for i in idx
               if PAGE_NO.fullmatch(lines[i][1].strip())["n"].isdigit() != roman]
        if len(seq) == 1:
            warnings.append(f"page {seq[0][0]}: {seq[0][1]!r} was left out as the only {'roman' if roman else 'arabic'} "
                            "page number, so no sequence checks it; confirm it is a page number")
        offsets = [_page_value(PAGE_NO.fullmatch(t)["n"]) - p for p, t in seq]
        if len(set(offsets)) > 1:
            common = max(set(offsets), key=offsets.count)
            bad = [f"page {p}: {t!r}" for (p, t), o in zip(seq, offsets) if o != common]
            raise RulesError("page-number check failed: these lone numbers at a page's top or bottom do not "
                             "follow the page sequence, so they may be rule text: " + ", ".join(bad) +
                             ". Nothing was written; read those pages in the source.")
    return warnings


def _fits(k, indent, lv, ind):
    """True if `indent` fits level k: the level's own indentation, or deeper than its parent's."""
    if ind[k] is not None:
        return indent == ind[k]
    parent = next((ind[j] for j in PARENT[k] if lv[j] is not None and ind[j] is not None), None)
    return parent is not None and indent > parent


def _level(tok, lv, ind, indent, nxt):
    """(level, other readings) for a marker: (None, []) when it continues no list (out of sequence);
    a non-empty list of other readings means the level was guessed."""
    cands = []
    if tok.isdigit():
        n = int(tok)
        if n == (1 if lv[DIGIT] is None else int(lv[DIGIT]) + 1):
            cands.append(DIGIT)
        if lv[CAPITAL] is not None and n == (1 if lv[NESTED] is None else int(lv[NESTED]) + 1):
            cands.append(NESTED)
    elif tok.isupper():
        if tok == ("A" if lv[CAPITAL] is None else _next_alpha(lv[CAPITAL])):
            cands.append(CAPITAL)
    else:
        if tok == ("a" if lv[LETTER] is None else _next_alpha(lv[LETTER])):
            cands.append(LETTER)
        if (lv[CAPITAL] is not None or lv[DIGIT] is not None) and \
                tok == ("i" if lv[ROMAN] is None else ROMAN_NEXT.get(lv[ROMAN])):
            cands.append(ROMAN)
    if len(cands) < 2:
        return (cands[0] if cands else None), []
    if set(cands) == {LETTER, ROMAN} and nxt is not None:  # "(i)": the marker after it decides
        if nxt == ROMAN_NEXT.get(tok):
            return ROMAN, []
        if nxt in (_next_alpha(tok), "1"):
            return LETTER, []
    fitting = [k for k in cands if _fits(k, indent, lv, ind)]
    if len(fitting) == 1:
        return fitting[0], []
    return max(cands), [k for k in cands if k != max(cands)]


OUTLINE_START = {"digit": "1", "upper": "A", "lower": "a", "roman": "i"}


def _outline_next(kind, tok):
    return str(int(tok) + 1) if kind == "digit" else ROMAN_NEXT.get(tok) if kind == "roman" else _next_alpha(tok)


def _outline(tok, wrap, stack):
    """(depth, style) for a marker in an outline (--markers dotted), or None when it continues no
    open list and starts no new one. `stack` holds the open lists, outermost first, as
    ((wrap, kind), last token). A marker continues the deepest open list of its style whose next
    token it is; otherwise it opens a list one level deeper if it is that list's first token and no
    open list has its style. Indentation is not used: outlines are often indented unevenly."""
    if tok.isdigit():
        kinds = ["digit"]
    elif tok.isupper():
        kinds = ["upper"] if len(tok) == 1 else []
    else:
        kinds = (["lower"] if len(tok) == 1 else []) + (["roman"] if tok in ROMAN_NEXT else [])
    for d in range(len(stack) - 1, -1, -1):
        (w, k), last = stack[d]
        if w == wrap and k in kinds and tok == _outline_next(k, last):
            return d, (w, k)
    for k in kinds:
        if tok == OUTLINE_START[k] and all(style != (wrap, k) for style, _ in stack):
            return len(stack), (wrap, k)
    return None


def _label(num, parts):
    return num + "".join(f"({p})" for p in parts)


def build_text(text: str, cite_prefix: str, anchor_prefix: str | None = None, start_at: str | None = None,
               stop_at: str | None = None, heading_re: str | None = None, marker_style: str = "paren",
               unit: str = "Rule") -> Built:
    """Markdown body for the rules in `text` (layout text; pages separated by form feeds).

    Kept: every line from the first line that begins with `start_at` up to (not including) the
    next line that begins with `stop_at`, except
    page-number lines (a lone number at a page's top or bottom that follows the page sequence)
    and blank lines. A heading is a line matching `heading_re` whose rule number is higher than
    the last heading's, so a cross-reference to an earlier rule is not one. Raises RulesError if
    the words do not survive or two anchors collide; nothing is returned in that case.

    `marker_style` is "paren" for "(a)(1)(A)(i)" rules, or "dotted" for an outline numbered "A.", "1.",
    "a.", "i." (parenthesized markers may sit under them); see _outline. `unit` names a heading
    in the Markdown ("## Rule 7." or "## Section 4.").
    """
    if not cite_prefix or not cite_prefix.strip():
        raise RulesError("cite_prefix is required, e.g. 'L.U.Civ.R.'")
    if marker_style not in MARKER_STYLES:
        raise RulesError(f"marker style must be one of: {', '.join(MARKER_STYLES)} (got {marker_style!r})")
    if not re.fullmatch(r"[A-Z][A-Za-z]{0,19}", unit or ""):
        raise RulesError(f"unit must be one capitalized word such as Rule or Section (got {unit!r})")
    outline = marker_style == "dotted"
    ap = anchor_prefix or default_anchor_prefix(cite_prefix)
    if not _ANCHOR_PREFIX.fullmatch(ap):
        raise RulesError(f"anchor prefix must be lowercase letters and digits, not {ap!r}")
    try:
        heading = re.compile(heading_re or DEFAULT_HEADING)
    except re.error as e:
        raise RulesError(f"heading pattern does not compile: {e}") from None
    if "num" not in heading.groupindex:
        raise RulesError("heading pattern needs a (?P<num>...) group")

    lines, edges = [], set()  # lines: (page, raw); edges: first and last filled line of each page
    for pno, ptext in enumerate(text.split("\f"), 1):
        first = len(lines)
        lines += [(pno, raw) for raw in ptext.split("\n")]
        filled = [i for i in range(first, len(lines)) if lines[i][1].strip()]
        if filled:
            edges.update((filled[0], filled[-1]))
    start = _find_line(lines, start_at, 0, "start_at") if start_at else 0
    stop = _find_line(lines, stop_at, start + 1, "stop_at") if stop_at else len(lines)

    dropped = [i for i in range(start, stop) if i in edges and (m := PAGE_NO.fullmatch(lines[i][1].strip()))
               and _page_value(m["n"]) is not None]
    page_warnings = _check_page_sequence(lines, dropped)
    skip = set(dropped)
    region = [i for i in range(start, stop) if i not in skip and lines[i][1].strip()]

    heads, last = {}, None
    for i in region:
        m = heading.match(lines[i][1].replace("\f", "").strip())
        if m and not re.fullmatch(_NUM, m["num"]):
            raise RulesError(f"the heading pattern read {m['num']!r} as a rule number on page {lines[i][0]}; "
                             "anchors and cites need digits such as 7 or 83.1. Nothing was written.")
        if m and (last is None or _num_key(m["num"]) > last):
            heads[i], last = m, _num_key(m["num"])
    if outline:
        found = {i: m for i in region if i not in heads and (m := DOTTED.match(_norm(lines[i][1])))}
        markers = {i: m["dot"] or m["par"] for i, m in found.items()}
        wraps = {i: "." if m["dot"] else "()" for i, m in found.items()}
    else:
        markers = {i: m["tok"] for i in region if i not in heads and (m := MARKER.match(_norm(lines[i][1])))}
    upcoming, nxt = {}, None  # the next marker in the same rule, for "(i)": letter or roman
    for i in reversed(region):
        if i in heads:
            nxt = None
        elif i in markers:
            upcoming[i], nxt = nxt, markers[i]

    body, src, notes, anchors, rule_nums = [], [], [], [], []
    cur = None
    for i in region:
        pno, raw = lines[i]
        line = _norm(raw)
        src += line.split()
        if i in heads:
            num = heads[i]["num"]
            cur = {"num": num, "lv": [None] * 5, "ind": [None] * 5, "at": [None] * 5,  # at: (label, line)
                   "stack": []}  # the open outline lists, for --markers dotted
            rule_nums.append(num)
            aid = f"{ap}-{num}"
            title = _title(heads[i].groupdict().get("title") or "") or f"{unit} {num}"
            body += ["", f"## {unit} {num}. {title}", f'<a id="{aid}"></a>**[{cite_prefix} {num}]** {line}']
            anchors.append(aid)
            continue
        tok = markers.get(i)
        if tok is None or cur is None:
            body.append(line)
            continue
        if outline:
            step = _outline(tok, wraps[i], cur["stack"])
            if step is None:
                shown = f"{tok}." if wraps[i] == "." else f"({tok})"
                notes.append(f"{unit} {cur['num']}: {shown} on page {pno} is out of sequence, so it is kept as "
                             f"text, not a subsection: {line[:70]!r}")
                body += ["", line]
                continue
            depth, style = step
            cur["stack"] = cur["stack"][:depth] + [(style, tok)]
            parts = [t for _, t in cur["stack"]]
            aid = "-".join([f"{ap}-{cur['num']}"] + parts)
            body += ["", f'<a id="{aid}"></a>**[{_label(cur["num"], parts)}]** {line}']
            anchors.append(aid)
            continue
        lv, ind, at, indent = cur["lv"], cur["ind"], cur["at"], _indent(raw)
        k, alts = _level(tok, lv, ind, indent, upcoming.get(i))
        if k is None:
            # a repeat of the current item means an earlier line, often a wrapped cross-reference, took its anchor
            took = next((at[j] for j in range(5) if lv[j] == tok and at[j]), None)
            notes.append(f"{unit} {cur['num']}: ({tok}) on page {pno} is out of sequence, so it is kept as text, "
                         f"not a subsection: {line[:70]!r}" +
                         (f". {took[0]} is already anchored at the earlier line {took[1][:70]!r}; if that line is a "
                          "wrapped cross-reference, the anchor is on the wrong line" if took else ""))
            body += ["", line]
            continue
        others = [_label(cur["num"], [p for p in lv[:j] if p is not None] + [tok]) for j in alts]
        lv[k], ind[k] = tok, indent
        for j in range(k + 1, 5):
            lv[j] = ind[j] = at[j] = None
        parts = [p for p in lv[: k + 1] if p is not None]
        at[k] = (_label(cur["num"], parts), line)
        aid = "-".join([f"{ap}-{cur['num']}"] + parts)
        body += ["", f'<a id="{aid}"></a>**[{_label(cur["num"], parts)}]** {line}']
        anchors.append(aid)
        if others:
            notes.append(f"{unit} {cur['num']}: ({tok}) on page {pno} could be {_label(cur['num'], parts)} or "
                         f"{' or '.join(others)}; indentation does not settle it, so the build guessed "
                         f"{_label(cur['num'], parts)}. Check it against the source.")

    dups = sorted({a for a in anchors if anchors.count(a) > 1})
    if dups:
        raise RulesError(f"two subsections would share an anchor: {dups}. Nothing was written.")
    got = body_words(body)
    if got != src:
        at = next((n for n, (a, b) in enumerate(zip(got, src)) if a != b), min(len(got), len(src)))
        raise RulesError(f"word check failed at word {at}: the Markdown has {got[at:at + 6]}, the source "
                         f"{src[at:at + 6]}. A source line may look like added markup. Nothing was written.")

    def count(idxs):
        return sum(len(lines[i][1].split()) for i in idxs)

    words = {"source": len(text.split()), "before_start": count(range(0, start)),
             "after_stop": count(range(stop, len(lines))), "page_numbers": count(dropped), "body": len(src)}
    if words["source"] != words["before_start"] + words["after_stop"] + words["page_numbers"] + words["body"]:
        raise RulesError(f"word accounting failed: {words}. Nothing was written.")
    return Built(body, src, rule_nums, anchors, [(lines[i][0], lines[i][1].strip()) for i in dropped], notes,
                 page_warnings + _warnings(lines, region), words)


def _warnings(lines, region):
    out, by_page = [], {}
    for i in region:
        by_page.setdefault(lines[i][0], []).append(lines[i][1].replace("\f", "").strip())
    for pno, page_lines in by_page.items():
        wide = sum(1 for l in page_lines if GAP.search(l))
        if wide >= 4 and wide / len(page_lines) > 0.3:
            out.append(f"page {pno}: {wide} of {len(page_lines)} lines have wide gaps, so it may be two columns or "
                       "a table and the word order may not be the reading order. Read this page against the source.")
    lone, odd = [], []
    for i in region:
        line = _norm(lines[i][1])
        if any(re.fullmatch(r"[b-z]", t) for t in line.split()):
            lone.append(f"page {lines[i][0]}: {line[:90]!r}")
        bad = sorted({f"U+{ord(ch):04X}" for ch in line if ch == "�" or unicodedata.category(ch) in ("Co", "Cc")})
        if bad:
            odd.append(f"page {lines[i][0]}: {', '.join(bad)} in {line[:90]!r}")
    for kind, items in (("a lone letter, which may be a word that lost glyphs", lone),
                        ("unreadable characters", odd)):
        for item in items[:20]:
            out.append(f"{kind}: {item}")
        if len(items) > 20:
            out.append(f"{kind}: {len(items) - 20} more lines like these")
    return out


def _source_text(path: str, use_pdftotext: bool):
    """(text, extractor) for a .txt or .pdf source."""
    if path.lower().endswith(".pdf"):
        exe = shutil.which("pdftotext") if use_pdftotext else None
        if not exe:
            raise RulesError("a PDF source needs pdftotext; extract its text with 'pdftotext -layout' and pass the .txt")
        res = subprocess.run([exe, "-layout", "-enc", "UTF-8", path, "-"], capture_output=True)
        if res.returncode != 0:
            raise RulesError(f"pdftotext failed: {res.stderr.decode('utf-8', 'replace').strip()}")
        ver = subprocess.run([exe, "-v"], capture_output=True, text=True)
        first = (ver.stderr or ver.stdout).strip().splitlines()
        return res.stdout.decode("utf-8", "replace"), f"pdftotext -layout ({first[0] if first else 'version unknown'})"
    try:
        with open(path, encoding="utf-8") as f:
            return f.read(), "text file"
    except UnicodeDecodeError as e:
        raise RulesError(f"{path} is not UTF-8 text: {e}") from None


def _header(m: dict) -> str:
    src = m["source"]
    region = m["region"]
    span = ""
    if region["start_at"] or region["stop_at"]:
        span = (f" from the line \"{region['start_at']}\"" if region["start_at"] else "") + \
               (f" up to the line \"{region['stop_at']}\"" if region["stop_at"] else "")
    ex = f"{m['cite_prefix']} 7(b)(4)"
    return "\n".join([
        f"# {m['title']} ({m['version']})", "",
        f"**This is derived text, not the official copy.** Quote the official source in anything filed. "
        f"Built {m['built_at']} by {m['builder']} from `{src['file']}` (sha256 `{src['sha256']}`, "
        f"text by {src['extractor']})" + (f", retrieved from {src['url']}" if src["url"] else "") + ".", "",
        f"- **Current as of:** {m['current_as_of'] or 'UNKNOWN'}. Courts amend their rules; check for later "
        f"amendments before relying on a rule.",
        f"- **Words:** the body below, with its headings, anchors and bracketed labels removed, holds the "
        f"source text's {m['words']['body']} words{span}, unchanged and in order. "
        f"{m['words']['page_numbers']} page-number words were left out; manifest.json lists each one.",
        f"- **Anchors:** {m['rules']} rules, {m['anchors']} anchors. `{ex}` is `#{m['anchor_prefix']}-7-b-4`. "
        f"Levels are read from the markers: (a) letter, (1) number, (A) capital, (i) roman, and a (1) list under "
        f"a capital item. {len(m['notes'])} placements were guessed or skipped and "
        f"{len(m['warnings'])} pages or lines need a look; both lists are in manifest.json.",
        "", "---", "", BODY_MARK, ""])


def _write_atomic(path: str, data: bytes):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def build_pack(source_path: str, out_dir: str, *, pack_id: str, version: str, title: str, cite_prefix: str,
               court: str | None = None, anchor_prefix: str | None = None, current_as_of: str | None = None,
               source_url: str | None = None, start_at: str | None = None, stop_at: str | None = None,
               heading_re: str | None = None, review_after_days: int = REVIEW_AFTER_DAYS,
               use_pdftotext: bool = True, marker_style: str = "paren", unit: str = "Rule") -> dict:
    """Build a pack folder (manifest.json + rules.md) from a .txt or .pdf. Returns the manifest."""
    if not _ID.fullmatch(pack_id or ""):
        raise RulesError(f"pack id must be lowercase letters, digits, '.' or '-' (got {pack_id!r})")
    if not _VERSION.fullmatch(version or ""):
        raise RulesError(f"version must be letters, digits, '.' or '-' (got {version!r})")
    if current_as_of is not None and not _is_date(current_as_of):
        raise RulesError(f"current-as-of must be YYYY-MM-DD (got {current_as_of!r})")
    if not (title or "").strip():
        raise RulesError("a title is required")
    if not isinstance(review_after_days, int) or review_after_days < 1:
        raise RulesError("review-after-days must be a positive number of days")
    if os.path.isdir(out_dir) and os.listdir(out_dir):
        raise RulesError(f"{out_dir} already holds files; build into a new folder")
    text, extractor = _source_text(source_path, use_pdftotext)
    built = build_text(text, cite_prefix, anchor_prefix, start_at, stop_at, heading_re, marker_style, unit)
    m = {"format": FORMAT, "id": pack_id, "version": version, "title": title.strip(), "court": court,
         "cite_prefix": cite_prefix, "anchor_prefix": anchor_prefix or default_anchor_prefix(cite_prefix),
         "current_as_of": current_as_of, "review_after_days": review_after_days,
         "source": {"file": os.path.basename(source_path), "sha256": sha256_file(source_path), "url": source_url,
                    "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "extractor": extractor},
         "region": {"start_at": start_at, "stop_at": stop_at, "heading_re": heading_re or DEFAULT_HEADING},
         "built_at": store.now_iso(), "builder": BUILDER, "words": built.words,
         "dropped": [{"page": p, "text": t} for p, t in built.dropped], "rules": len(built.rules),
         "anchors": len(built.anchors), "rules_md_sha256": None, "notes": built.notes, "warnings": built.warnings}
    if (marker_style, unit) != ("paren", "Rule"):  # a default build's manifest keeps its earlier shape
        m["region"].update(markers=marker_style, unit=unit)
    md = (_header(m) + "\n".join(built.body).lstrip("\n") + "\n").encode("utf-8")
    m["rules_md_sha256"] = hashlib.sha256(md).hexdigest()
    os.makedirs(out_dir, exist_ok=True)
    _write_atomic(os.path.join(out_dir, "rules.md"), md)
    _write_atomic(os.path.join(out_dir, "manifest.json"),
                  (json.dumps(m, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return m


# --------------------------------------------------------------------------
# Reading a pack
# --------------------------------------------------------------------------
def _is_date(s) -> bool:
    if not isinstance(s, str) or not _DATE.fullmatch(s):
        return False
    try:
        date.fromisoformat(s)
    except ValueError:
        return False
    return True


@dataclass
class Pack:
    dir: str
    manifest: dict
    manifest_sha256: str
    lines: list
    anchors: dict  # anchor id -> line index in `lines`

    id = property(lambda self: self.manifest["id"])
    version = property(lambda self: self.manifest["version"])
    cite_prefix = property(lambda self: self.manifest["cite_prefix"])
    anchor_prefix = property(lambda self: self.manifest["anchor_prefix"])
    rules_md_sha256 = property(lambda self: self.manifest["rules_md_sha256"])

    def label(self, aid: str) -> str:
        """'lucivr-7-b-4' -> 'L.U.Civ.R. 7(b)(4)'."""
        num, *parts = aid[len(self.anchor_prefix) + 1:].split("-")
        return f"{self.cite_prefix} {_label(num, parts)}"

    def section(self, aid: str) -> list:
        """The anchor's line and everything under it, up to the next rule or a subsection that is not its own."""
        i = self.anchors[aid]
        out = [self.lines[i]]
        for line in self.lines[i + 1:]:
            m = ANCHOR_LINE.match(line)
            if line.startswith("## ") or (m and not m["id"].startswith(aid + "-")):
                break
            out.append(line)
        while out and not out[-1].strip():
            out.pop()
        return [ANCHOR_LINE.sub("", l, count=1) for l in out]


def load_pack(pack_dir: str) -> Pack:
    """Read and check a pack folder: manifest shape, rules.md bytes against the manifest, unique anchors."""
    mpath, rpath = (os.path.join(pack_dir, n) for n in PACK_FILES)
    if not (os.path.isfile(mpath) and os.path.isfile(rpath)):
        raise RulesError(f"{pack_dir} is not a rules pack (needs {' and '.join(PACK_FILES)})")
    with open(mpath, "rb") as f:
        mraw = f.read()
    try:
        m = json.loads(mraw.decode("utf-8"))
    except ValueError as e:
        raise RulesError(f"{mpath} is not valid JSON: {e}") from None
    if not isinstance(m, dict) or m.get("format") != FORMAT:
        raise RulesError(f"{mpath}: format must be {FORMAT!r}")
    checks = (("id", lambda v: isinstance(v, str) and _ID.fullmatch(v)),
              ("version", lambda v: isinstance(v, str) and _VERSION.fullmatch(v)),
              ("cite_prefix", lambda v: isinstance(v, str) and v.strip()),
              ("anchor_prefix", lambda v: isinstance(v, str) and _ANCHOR_PREFIX.fullmatch(v)),
              ("current_as_of", lambda v: v is None or _is_date(v)),
              ("review_after_days", lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0),
              ("rules_md_sha256", lambda v: isinstance(v, str) and _SHA.fullmatch(v)))
    for key, ok in checks:
        if not ok(m.get(key)):
            raise RulesError(f"{mpath}: '{key}' is missing or malformed")
    if sha256_file(rpath) != m["rules_md_sha256"]:
        raise RulesError(f"{rpath} does not match the sha256 in its manifest; it changed after it was built")
    with open(rpath, encoding="utf-8") as f:
        lines = f.read().split("\n")
    if BODY_MARK not in lines:
        raise RulesError(f"{rpath} has no body marker")
    anchors = {}
    for n in range(lines.index(BODY_MARK) + 1, len(lines)):
        a = ANCHOR_LINE.match(lines[n])
        if not a:
            continue
        if a["id"] in anchors or not a["id"].startswith(m["anchor_prefix"] + "-"):
            raise RulesError(f"{rpath}: anchor {a['id']!r} is repeated or lacks the prefix {m['anchor_prefix']!r}")
        anchors[a["id"]] = n
    return Pack(os.path.abspath(pack_dir), m, hashlib.sha256(mraw).hexdigest(), lines, anchors)


def staleness(current_as_of, review_after_days, today=None):
    """('ok' | 'STALE' | 'UNKNOWN', explanation). A date is no proof the rules are current; past the
    review window it is a prompt to check the court's website for amendments."""
    today = today or date.today()
    if not current_as_of:
        return "UNKNOWN", "no current-as-of date was recorded; check the court's website for the current rules"
    age = (today - date.fromisoformat(current_as_of)).days
    if age < 0:
        return "UNKNOWN", f"current as of {current_as_of}, which is after today ({today}); check the date in the pack"
    if age > review_after_days:
        return "STALE", (f"{age} days since {current_as_of}, past its {review_after_days}-day review window; "
                         "check the court's website for amendments")
    return "ok", f"{age} days since {current_as_of}, within its {review_after_days}-day review window"


def checked_staleness(current_as_of, review_after_days, checks, today=None):
    """staleness(), then the latest site check recorded for this pack version (see record_check).
    A check that found no amendment reads CHECKED until its next review date or its check date plus
    the review window, whichever comes first; a check that found one keeps the pack STALE. A check
    never vouches for an undated pack, or for a date after today."""
    today = today or date.today()
    state, why = staleness(current_as_of, review_after_days, today)
    done = sorted((c for c in checks if date.fromisoformat(c["checked_on"]) <= today),
                  key=lambda c: (c["checked_on"], c["recorded_at"]))
    if state == "UNKNOWN" or not done:
        return state, why
    last = done[-1]
    if last["found"] == "amended":
        return "STALE", (f"a check on {last['checked_on']} found an amendment; attach the new version "
                         f"(this pack is current as of {current_as_of})")
    if state == "ok":
        return state, why
    on = date.fromisoformat(last["checked_on"])
    until = on + timedelta(days=review_after_days)
    if last.get("next_review"):
        until = min(until, date.fromisoformat(last["next_review"]))
    if today < until:
        return "CHECKED", (f"current as of {current_as_of}; no amendment found when checked on {on} "
                           f"(sources in {ATTACH_FILE}); next review by {until}")
    return "STALE", f"{why}; last checked {on}, and its next review was due {until}"


# --------------------------------------------------------------------------
# Attaching packs to a matter
# --------------------------------------------------------------------------
def _attach_path(case_dir):
    return os.path.join(case_dir, ATTACH_FILE)


def read_attachments(case_dir: str) -> dict:
    """The matter's .matter/rules.json; an empty list when there is none. Refuses any other shape."""
    p = _attach_path(case_dir)
    if not os.path.isfile(p):
        return {"format": ATTACH_FORMAT, "packs": [], "history": []}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except ValueError as e:
        raise RulesError(f"{p} is not valid JSON: {e}") from None
    if not isinstance(d, dict) or d.get("format") != ATTACH_FORMAT or not isinstance(d.get("packs"), list) \
            or not isinstance(d.get("history", []), list):
        raise RulesError(f"{p}: expected {{'format': {ATTACH_FORMAT!r}, 'packs': [...], 'history': [...]}}")
    # the file is not a registered document, so a hand edit is caught here rather than by verify
    keys = ("id", "version", "path", "cite_prefix", "anchor_prefix", "manifest_sha256", "rules_md_sha256")
    for e in d["packs"]:
        if not isinstance(e, dict) or not all(isinstance(e.get(k), str) for k in keys):
            raise RulesError(f"{p}: each pack needs {', '.join(keys)}")
        days = e.get("review_after_days", REVIEW_AFTER_DAYS)
        if not isinstance(days, int) or isinstance(days, bool) or days < 1:
            raise RulesError(f"{p}: {e['id']}: review_after_days must be a positive number of days")
        if e.get("current_as_of") is not None and not _is_date(e["current_as_of"]):
            raise RulesError(f"{p}: {e['id']}: current_as_of must be YYYY-MM-DD")
        if ingest.resolve_document_path(os.path.abspath(case_dir), e["path"]) is None:
            raise RulesError(f"{p}: pack path {e['path']!r} is not inside the matter")
    checks = d.get("checks", [])
    if not isinstance(checks, list):
        raise RulesError(f"{p}: 'checks' must be a list")
    ckeys = ("pack_id", "version", "checked_on", "found", "recorded_at", "recorded_by")
    for c in checks:
        ok = isinstance(c, dict) and all(isinstance(c.get(k), str) for k in ckeys) and _is_date(c["checked_on"]) \
            and c["found"] in CHECK_FOUND and (c.get("next_review") is None or _is_date(c["next_review"])) \
            and isinstance(c.get("sources"), list) and c["sources"] \
            and all(isinstance(s, dict) and isinstance(s.get("ref"), str) and _SHA.fullmatch(str(s.get("sha256")))
                    for s in c["sources"])
        if not ok:
            raise RulesError(f"{p}: each check needs {', '.join(ckeys)}, found as {' or '.join(CHECK_FOUND)}, "
                             "dates as YYYY-MM-DD and at least one source with its sha256")
    d.setdefault("history", [])
    return d


@dataclass
class Attached:
    entry: dict
    pack: Pack | None
    problem: str | None
    checks: list = field(default_factory=list)  # site checks recorded for this pack version


def load_attached(case_dir: str) -> list:
    """Each attached pack, read and checked against the bytes recorded when it was attached.
    A pack that fails comes back with pack=None and the reason; it is never used. So does a pack
    that shares a cite or anchor prefix with one listed before it (possible only by hand-editing)."""
    out, cites, anchors = [], {}, {}
    att = read_attachments(case_dir)
    for e in att["packs"]:
        mine = [c for c in att.get("checks", []) if (c["pack_id"], c["version"]) == (e["id"], e["version"])]
        d = ingest.resolve_document_path(os.path.abspath(case_dir), e["path"])
        try:
            pack = load_pack(d)
        except RulesError as err:
            out.append(Attached(e, None, str(err), mine))
            continue
        if pack.rules_md_sha256 != e["rules_md_sha256"] or pack.manifest_sha256 != e["manifest_sha256"]:
            out.append(Attached(e, None, f"{e['path']} changed after it was attached; run verify", mine))
            continue
        cite = default_anchor_prefix(pack.cite_prefix)
        if cite in cites:
            out.append(Attached(e, None, f"shares the cite prefix {pack.cite_prefix!r} with {cites[cite]}", mine))
        elif pack.anchor_prefix in anchors:
            out.append(Attached(e, None, f"shares the anchor prefix {pack.anchor_prefix!r} with "
                                         f"{anchors[pack.anchor_prefix]}", mine))
        else:
            cites[cite] = anchors[pack.anchor_prefix] = pack.id
            out.append(Attached(e, pack, None, mine))
    return out


def attach(case_dir: str, pack_dir: str, by: str, replace: bool = False) -> tuple:
    """Attach a pack to the matter: copy it in (unless it is already inside), register both files,
    record it in .matter/rules.json and the events table. Returns (entry, message)."""
    if not os.path.isdir(case_dir):
        raise RulesError(f"case_dir is not a directory: {case_dir}")
    if os.path.abspath(pack_dir) == os.path.abspath(case_dir):
        raise RulesError("the pack folder is the matter folder itself; keep a pack in a folder of its own")
    pack = load_pack(pack_dir)
    att = read_attachments(case_dir)
    cur = next((e for e in att["packs"] if e["id"] == pack.id), None)
    if cur and (cur["version"], cur["rules_md_sha256"], cur["manifest_sha256"]) == \
            (pack.version, pack.rules_md_sha256, pack.manifest_sha256):
        return cur, f"{pack.id}@{pack.version} is already attached at {cur['path']}; nothing changed"
    if cur and not replace:
        raise RulesError(f"{pack.id} is attached at version {cur['version']}; pass --replace to attach "
                         f"{pack.version} in its place")
    for a in load_attached(case_dir):  # prefixes from each pack's own manifest where it can be read
        if a.entry["id"] == pack.id:
            continue
        cite, anchor = (a.pack.cite_prefix, a.pack.anchor_prefix) if a.pack else \
            (a.entry["cite_prefix"], a.entry["anchor_prefix"])
        if default_anchor_prefix(cite) == default_anchor_prefix(pack.cite_prefix):
            raise RulesError(f"{a.entry['id']} already answers to the cite prefix {pack.cite_prefix!r}; two rule "
                             "sets would claim the same cites")
        if anchor == pack.anchor_prefix:
            raise RulesError(f"{a.entry['id']} already uses the anchor prefix {anchor!r}; two packs would share "
                             "anchor ids. Rebuild this pack with another --anchor-prefix")

    abs_case, abs_pack = os.path.abspath(case_dir), os.path.abspath(pack_dir)
    inside = ingest.is_within_matter(abs_pack, abs_case)
    rel = ingest.to_rel_path(abs_pack, abs_case) if inside else os.path.join(PACK_ROOT, f"{pack.id}_{pack.version}")
    dest = os.path.join(abs_case, rel)
    shas = {"manifest.json": pack.manifest_sha256, "rules.md": pack.rules_md_sha256}
    conn = store.connect(case_dir)
    try:
        for name, sha in shas.items():  # same bytes registered elsewhere would leave the copy unregistered
            row = conn.execute("SELECT rel_path FROM documents WHERE sha256=?", (sha,)).fetchone()
            if row and row["rel_path"] != os.path.join(rel, name):
                raise RulesError(f"{name} of this pack is already registered at {row['rel_path']}; attach that "
                                 "folder instead")
            target = os.path.join(dest, name)
            if not inside and os.path.exists(target) and sha256_file(target) != sha:
                raise RulesError(f"{os.path.join(rel, name)} already exists with other bytes; nothing was changed")
        if not inside:
            os.makedirs(dest, exist_ok=True)
            for name in shas:
                if not os.path.exists(os.path.join(dest, name)):
                    shutil.copy2(os.path.join(abs_pack, name), os.path.join(dest, name))
        _, _, excluded = ingest.ingest_paths(conn, case_dir, [os.path.join(dest, n) for n in shas])
        for name, sha in shas.items():
            row = conn.execute("SELECT sha256 FROM documents WHERE rel_path=?", (os.path.join(rel, name),)).fetchone()
            if excluded or row is None or row["sha256"] != sha:
                raise RulesError(f"{os.path.join(rel, name)} could not be registered with these bytes; the pack "
                                 "is not attached")
        now = store.now_iso()
        entry = {"id": pack.id, "version": pack.version, "path": rel, "cite_prefix": pack.cite_prefix,
                 "anchor_prefix": pack.anchor_prefix, "current_as_of": pack.manifest["current_as_of"],
                 "review_after_days": pack.manifest["review_after_days"],
                 "manifest_sha256": pack.manifest_sha256, "rules_md_sha256": pack.rules_md_sha256,
                 "attached_at": now, "attached_by": by}
        if cur:
            att["packs"].remove(cur)
            att["history"].append(dict(cur, replaced_at=now, replaced_by=by))
        att["packs"].append(entry)
        text = f"rules pack {pack.id}@{pack.version} attached from {rel}" + \
            (f" in place of {cur['version']}" if cur else "")
        with conn:  # the event commits only if rules.json was written, so neither exists without the other
            conn.execute("INSERT INTO events (id,occurred_on,text,actors,sources) VALUES (?,?,?,?,?)",
                         (store.new_id("ev_"), date.today().isoformat(), text, by,
                          json.dumps([os.path.join(rel, n) for n in shas])))
            _write_atomic(_attach_path(case_dir),
                          (json.dumps(att, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        conn.close()
    return entry, text


def record_check(case_dir: str, pack_id: str, checked_on: str, found: str, sources, by: str,
                 next_review: str | None = None, today=None) -> tuple:
    """Record that someone checked the court's website for amendments to an attached pack: when,
    what was found, and the registered files that show it (each re-hashed against its registration
    first). The check goes into .matter/rules.json and the events table together or not at all.
    matter-kit checks no website itself. Returns (check entry, message)."""
    today = today or date.today()
    if found not in CHECK_FOUND:
        raise RulesError(f"--found must be one of: {', '.join(CHECK_FOUND)}")
    if not _is_date(checked_on):
        raise RulesError(f"--on must be a date, YYYY-MM-DD (got {checked_on!r})")
    on = date.fromisoformat(checked_on)
    if on > today:
        raise RulesError(f"--on {checked_on} is after today ({today}); record a check once it is done")
    if next_review is not None:
        if found != "none":
            raise RulesError("--next-review applies only to a check that found no amendment; attach the amended "
                             "rules instead")
        if not _is_date(next_review) or date.fromisoformat(next_review) <= on:
            raise RulesError(f"--next-review must be a date after --on (got {next_review!r})")
    sources = [s.strip() for s in (sources or []) if s and s.strip()]
    if not sources:
        raise RulesError("name at least one --source: the registered file(s) showing what the check found")
    a = next((x for x in load_attached(case_dir) if x.entry["id"] == pack_id), None)
    if a is None:
        raise RulesError(f"no attached pack has the id {pack_id!r}; see: matter.py rules list")
    if a.pack is None:
        raise RulesError(f"{pack_id} cannot be used ({a.problem}); fix that before recording a check")
    as_of = a.pack.manifest["current_as_of"]
    if as_of and on < date.fromisoformat(as_of):
        raise RulesError(f"--on {checked_on} is before the pack's current-as-of date {as_of}; a check can only "
                         "speak for later dates")
    abs_case = os.path.abspath(case_dir)
    conn = store.connect(case_dir)
    try:
        recs, problems = [], []
        for src in sources:
            ref, _, loc = src.partition("@")
            row = conn.execute("SELECT rel_path, sha256 FROM documents WHERE rel_path=? OR id=?",
                               (ref, ref)).fetchone()
            path = row and ingest.resolve_document_path(abs_case, row["rel_path"])
            if row is None:
                problems.append(f"{ref} is not registered in this matter")
            elif not path or not os.path.isfile(path):
                problems.append(f"{row['rel_path']} is registered but missing; run verify")
            elif sha256_file(path) != row["sha256"]:
                problems.append(f"{row['rel_path']} changed since it was registered; run verify")
            else:
                recs.append({"ref": row["rel_path"] + (f"@{loc}" if loc else ""), "sha256": row["sha256"]})
        if problems:
            raise RulesError("; ".join(problems) + ". Nothing was recorded.")
        att = read_attachments(case_dir)
        checks = att.setdefault("checks", [])
        same = (pack_id, a.pack.version, checked_on, found, next_review, [r["ref"] for r in recs])
        for c in checks:
            if (c["pack_id"], c["version"], c["checked_on"], c["found"], c.get("next_review"),
                    [s["ref"] for s in c["sources"]]) == same:
                return c, (f"this check of {pack_id}@{a.pack.version} on {checked_on} is already recorded; "
                           "nothing changed")
        entry = {"pack_id": pack_id, "version": a.pack.version, "checked_on": checked_on, "found": found,
                 "next_review": next_review, "sources": recs, "recorded_at": store.now_iso(), "recorded_by": by}
        checks.append(entry)
        text = f"rules pack {pack_id}@{a.pack.version} checked on {checked_on}: " + (
            "no amendment found" + (f"; next review by {next_review}" if next_review else "") if found == "none"
            else "an amendment was found; attach the new version")
        with conn:  # as in attach: the event commits only if rules.json was written
            conn.execute("INSERT INTO events (id,occurred_on,text,actors,sources) VALUES (?,?,?,?,?)",
                         (store.new_id("ev_"), checked_on, text, by, json.dumps([r["ref"] for r in recs])))
            _write_atomic(_attach_path(case_dir),
                          (json.dumps(att, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        conn.close()
    return entry, text


# --------------------------------------------------------------------------
# Resolving a cite
# --------------------------------------------------------------------------
@dataclass
class Resolution:
    status: str          # found | candidates | not-found | not-a-rule
    hits: list           # [(Attached, anchor id)]
    message: str = ""
    read_as: str = ""
    nearest: str | None = None  # not-found in one pack: the deepest anchor on the way down, if any


def _prefix_re(cite_prefix: str):
    p = r"\s*".join(re.escape(ch) for ch in cite_prefix if not ch.isspace())
    return re.compile(rf"{p}\s*(?P<num>{_NUM})(?P<sub>{_SUB})\s*\.?")


def lookup(a: Attached, num: str, parts) -> tuple:
    """(anchor id or None, the deepest anchor that does exist on the way down, or None)."""
    base = f"{a.pack.anchor_prefix}-{num}"
    aid = "-".join([base] + list(parts))
    if aid in a.pack.anchors:
        return aid, aid
    nearest = None
    for n in range(len(parts) + 1):
        cand = "-".join([base] + list(parts[:n]))
        if cand in a.pack.anchors:
            nearest = cand
    return None, nearest


def _parts(sub: str) -> list:
    return re.findall(r"\(([A-Za-z0-9]{1,5})\)", sub or "")


def find(a: Attached, num: str, sub: str, read_as: str = "") -> Resolution:
    """Look up rule `num` with subsection marks `sub` ('(b)(4)') in one attached pack."""
    aid, nearest = lookup(a, num, _parts(sub))
    if aid:
        return Resolution("found", [(a, aid)], read_as=read_as)
    want = f"{a.pack.cite_prefix} {_label(num, _parts(sub))}"
    have = f"; the nearest text it has is {a.pack.label(nearest)}" if nearest else ""
    return Resolution("not-found", [], f"{a.pack.id}@{a.pack.version} has no {want}{have}", read_as, nearest)


def resolve(case_dir: str, cite: str, pack_id: str | None = None) -> Resolution:
    """Find a rule cite in the attached packs. A bare cite ('Rule 15', '15(a)') names no rule set,
    so it returns every pack that has it as candidates and picks none, unless `pack_id` is given."""
    attached = load_attached(case_dir)
    usable = [a for a in attached if a.pack]
    if pack_id:
        usable = [a for a in usable if a.pack.id == pack_id]
        if not usable:
            why = next((a.problem for a in attached if a.entry["id"] == pack_id), "it is not attached")
            raise RulesError(f"pack {pack_id!r} cannot be used: {why}")
    if not usable:
        problems = "; ".join(f"{a.entry['id']}: {a.problem}" for a in attached if a.problem)
        return Resolution("not-found", [], "no rules pack is attached to this matter" +
                          (f" that can be read ({problems})" if problems else ""))
    s = " ".join((cite or "").split())
    for a in usable:
        if s in a.pack.anchors:
            return Resolution("found", [(a, s)])
    for a in usable:
        if m := _prefix_re(a.pack.cite_prefix).fullmatch(s):
            return find(a, m["num"], m["sub"])
    from . import briefcheck  # lazy: briefcheck imports this module
    if m := briefcheck.LOCAL_WORDS_RE.fullmatch(s.rstrip(".")):  # the words brief check reads as a local rule
        local = briefcheck.rule_prefixes(briefcheck.load_docket(case_dir)[0])[1]
        a = next((a for a in usable if default_anchor_prefix(a.pack.cite_prefix) == default_anchor_prefix(local)),
                 None)
        if a is None:
            return Resolution("not-found", [], f"'{s}' reads as {local} (the docket map's local_rule), and no "
                                               f"attached pack has that cite prefix")
        return find(a, m["num"], m["sub"], read_as=f"'{s}' read as {local} {_label(m['num'], _parts(m['sub']))}")
    if m := BARE.fullmatch(s):
        if pack_id:
            return find(usable[0], m["num"], m["sub"])
        hits = [(a, aid) for a in usable if (aid := lookup(a, m["num"], _parts(m["sub"]))[0])]
        if not hits:
            return Resolution("not-found", [], f"no attached pack has rule {_label(m['num'], _parts(m['sub']))}")
        return Resolution("candidates", hits, f"'{s}' names no rule set")
    return Resolution("not-a-rule", [], f"{cite!r} is not a rule cite this kit reads; give it as "
                                        "'<prefix> 7(b)(4)', an anchor id, or a bare number with --pack")


def show(case_dir: str, cite: str, pack_id: str | None = None, today=None) -> tuple:
    """(exit code, text): 0 the rule's text with its pack and version, 2 a bare cite's candidates
    (none chosen), 1 not found."""
    res = resolve(case_dir, cite, pack_id)
    if res.status == "found":
        a, aid = res.hits[0]
        state, why = checked_staleness(a.pack.manifest["current_as_of"], a.pack.manifest["review_after_days"],
                                       a.checks, today)
        head = [f"{a.pack.label(aid)} · pack {a.pack.id}@{a.pack.version} · current as of "
                f"{a.pack.manifest['current_as_of'] or 'UNKNOWN'} [{state}: {why}]",
                f"{os.path.join(a.entry['path'], 'rules.md')}#{aid} · {DERIVED}"]
        if res.read_as:
            head.append(res.read_as)
        return 0, "\n".join(head + [""] + a.pack.section(aid))
    if res.status == "candidates":
        lines = [f"{res.message}; it could be any of these. Name the rule set (e.g. "
                 f"'{res.hits[0][0].pack.label(res.hits[0][1])}') or pass --pack."]
        for a, aid in res.hits:
            first = a.pack.section(aid)[0]
            lines.append(f"- {a.pack.label(aid)} (pack {a.pack.id}@{a.pack.version}): {first[:100]}")
        return 2, "\n".join(lines)
    return 1, res.message


def rule_source(case_dir: str, rule: str) -> tuple:
    """(value for deadlines.rule_source, note or None). A rule found in an attached pack is stored
    with its pack, version and anchor; anything else is kept as the free text it was."""
    if not read_attachments(case_dir)["packs"]:
        return rule, None
    res = resolve(case_dir, rule)
    if res.status == "found":
        a, aid = res.hits[0]
        return (f"{a.pack.label(aid)} [{a.pack.id}@{a.pack.version}#{aid}]",
                f"rule found in {a.pack.id}@{a.pack.version} at #{aid}")
    if res.status == "candidates":
        return rule, ("a bare rule cite matches " + ", ".join(a.pack.label(aid) for a, aid in res.hits) +
                      "; stored as free text. Name the rule set to record its pack and version.")
    if res.status == "not-found":
        return rule, f"{res.message}; stored as free text"
    return rule, None


def list_text(case_dir: str, today=None) -> str:
    att = read_attachments(case_dir)
    if not att["packs"]:
        return "no rules pack is attached (attach one with: matter.py rules attach <dir> <pack_dir>)"
    out = []
    for a in load_attached(case_dir):
        e = a.entry
        state, why = checked_staleness(e.get("current_as_of"), e.get("review_after_days", REVIEW_AFTER_DAYS),
                                       a.checks, today)
        out.append(f"{e['id']}@{e['version']}  {e.get('cite_prefix', '?')}  current as of "
                   f"{e.get('current_as_of') or 'UNKNOWN'} [{state}]  {e['path']}  "
                   + (f"anchors={len(a.pack.anchors)}" if a.pack else f"NOT USABLE: {a.problem}"))
    if att["history"]:
        out.append(f"({len(att['history'])} earlier attachment(s) replaced; see {ATTACH_FILE})")
    return "\n".join(out)


def stale_report(case_dir: str, today=None) -> tuple:
    """(exit code, text): 1 if any attached pack is past its review window with no current recorded check,
    has no date, or cannot be used."""
    attached = load_attached(case_dir)
    if not attached:
        return 0, "no rules pack is attached"
    out, worst = [], 0
    for a in attached:
        e = a.entry
        if a.pack is None:
            state, why = "NOT USABLE", a.problem
        else:
            state, why = checked_staleness(a.pack.manifest["current_as_of"], a.pack.manifest["review_after_days"],
                                           a.checks, today)
        worst = max(worst, 0 if state in PASSING else 1)
        out.append(f"[{state}] {e['id']}@{e['version']}: {why}")
    out.append("This command checked no website; it compares dates and the checks recorded with `rules checked`."
               if any(a.checks for a in attached) else "No court website was checked; this compares dates only.")
    return worst, "\n".join(out)

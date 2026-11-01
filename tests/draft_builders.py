"""Synthetic .docx and .pdf drafts for the brief-check reader tests. Built in memory; nothing is committed
as a binary and no real document is read. Every name, cite and sentence here is made up."""
import io
import zipfile
from xml.sax.saxutils import escape

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = f'xmlns:w="{W}"'

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/></Types>')
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Target="word/document.xml" Type="http://schemas.openxmlformats.org/'
    'officeDocument/2006/relationships/officeDocument"/></Relationships>')


# ---- paragraphs and runs -------------------------------------------------------------------------
def run(text, vanish=False):
    rpr = "<w:rPr><w:vanish/></w:rPr>" if vanish else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def para(*parts, style=None, num=None, ppr=""):
    """A paragraph. A part that starts with "<" is raw XML; anything else is the text of one run.
    num=(numId, ilvl) gives the paragraph its own list membership."""
    props = ""
    if style:
        props += f'<w:pStyle w:val="{style}"/>'
    if num is not None:
        props += f'<w:numPr><w:ilvl w:val="{num[1]}"/><w:numId w:val="{num[0]}"/></w:numPr>'
    props += ppr
    body = "".join(x if x.startswith("<") else run(x) for x in parts)
    return f"<w:p>{'<w:pPr>' + props + '</w:pPr>' if props else ''}{body}</w:p>"


def fnref(i):
    return f'<w:r><w:footnoteReference w:id="{i}"/></w:r>'


def enref(i):
    return f'<w:r><w:endnoteReference w:id="{i}"/></w:r>'


def table(rows):
    """rows: list of rows, each a list of cells, each a list of paragraph XML strings."""
    out = "<w:tbl><w:tblPr/><w:tblGrid/>"
    for r in rows:
        out += "<w:tr>" + "".join(f"<w:tc>{''.join(c)}</w:tc>" for c in r) + "</w:tr>"
    return out + "</w:tbl>"


# ---- parts -----------------------------------------------------------------------------------------
def document_xml(body):
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS}><w:body>{body}</w:body></w:document>'


def notes_xml(kind, notes):
    """kind 'footnote' or 'endnote'; notes: {id: paragraph text or list of raw paragraph XML}."""
    sep = (f'<w:{kind} w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:{kind}>'
           f'<w:{kind} w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:{kind}>')
    out = ""
    for i, v in notes.items():
        paras = v if isinstance(v, list) else [v]
        xml = ""
        for k, pv in enumerate(paras):
            ref = f"<w:r><w:{kind}Ref/></w:r>" if k == 0 else ""
            inner = pv if pv.startswith("<") else run(" " + pv)
            xml += f"<w:p>{ref}{inner}</w:p>"
        out += f'<w:{kind} w:id="{i}">{xml}</w:{kind}>'
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:{kind}s {NS}>{sep}{out}</w:{kind}s>'


def lvl(ilvl, fmt, text, start=1, pstyle=None, restart=None):
    ps = f'<w:pStyle w:val="{pstyle}"/>' if pstyle else ""
    rs = f'<w:lvlRestart w:val="{restart}"/>' if restart is not None else ""
    return (f'<w:lvl w:ilvl="{ilvl}"><w:start w:val="{start}"/><w:numFmt w:val="{fmt}"/>{rs}{ps}'
            f'<w:lvlText w:val="{text}"/></w:lvl>')


def numbering_xml(abstracts, nums):
    """abstracts: {abstractId: [lvl(...) or raw XML, ...]}; nums: {numId: abstractId or (abstractId, {ilvl: override})}.
    An override is a startOverride value, or raw XML for the inside of the w:lvlOverride."""
    out = ""
    for a, levels in abstracts.items():
        out += f'<w:abstractNum w:abstractNumId="{a}">{"".join(levels)}</w:abstractNum>'
    for n, v in nums.items():
        a, ov = v if isinstance(v, tuple) else (v, {})
        over = "".join(f'<w:lvlOverride w:ilvl="{i}">'
                       + (s if str(s).startswith("<") else f'<w:startOverride w:val="{s}"/>') + "</w:lvlOverride>"
                       for i, s in ov.items())
        out += f'<w:num w:numId="{n}"><w:abstractNumId w:val="{a}"/>{over}</w:num>'
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering {NS}>{out}</w:numbering>'


def styles_xml(styles):
    """styles: list of (styleId, name, basedOn or None, raw pPr children)."""
    out = ""
    for sid, name, based, ppr in styles:
        b = f'<w:basedOn w:val="{based}"/>' if based else ""
        p = f"<w:pPr>{ppr}</w:pPr>" if ppr else ""
        out += f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/>{b}{p}</w:style>'
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {NS}>{out}</w:styles>'


def comments_xml(n=1):
    cs = "".join(f'<w:comment w:id="{i}" w:author="Reviewer"><w:p><w:r><w:t>check this</w:t></w:r></w:p></w:comment>'
                 for i in range(n))
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:comments {NS}>{cs}</w:comments>'


def docx(body, footnotes=None, endnotes=None, numbering=None, styles=None, settings=None, comments=None,
         parts=None, compression=zipfile.ZIP_DEFLATED):
    """Bytes of a .docx. `parts` adds or replaces raw parts by name (a str, bytes, or None to drop one)."""
    p = {"[Content_Types].xml": CONTENT_TYPES, "_rels/.rels": RELS, "word/document.xml": document_xml(body)}
    if footnotes is not None:
        p["word/footnotes.xml"] = notes_xml("footnote", footnotes)
    if endnotes is not None:
        p["word/endnotes.xml"] = notes_xml("endnote", endnotes)
    if numbering is not None:
        p["word/numbering.xml"] = numbering
    if styles is not None:
        p["word/styles.xml"] = styles
    if settings is not None:
        p["word/settings.xml"] = settings
    if comments is not None:
        p["word/comments.xml"] = comments
    p.update(parts or {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        for name, data in p.items():
            if data is not None:
                z.writestr(name, data)
    return buf.getvalue()


# ---- PDF ---------------------------------------------------------------------------------------------
def pdf(pages, font_size=12):
    """Bytes of a PDF. pages: list of pages; a page is a list of (x, y, text) with y in points from the
    bottom. Text is Helvetica in WinAnsi, so "§", "¶" and curly quotes survive pdftotext."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{' '.join(f'{4 + 2 * i} 0 R' for i in range(len(pages)))}] /Count {len(pages)} >>".encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    for i, items in enumerate(pages):
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * i} 0 R "
                    f"/Resources << /Font << /F1 3 0 R >> >> >>".encode())
        ops = []
        for x, y, t in items:
            t = t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"BT /F1 {font_size} Tf {x} {y} Td ({t}) Tj ET")
        stream = "\n".join(ops).encode("cp1252", "replace")
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def text_pdf(pages, pitch=14, header=None, footer=None, margin=72):
    """A PDF from pages of lines. A line is a str (flush left), or (x_offset_points, str), where an offset of
    None centres it. `pitch` is the line spacing (14 single, 28 double). `header`/`footer` are lines printed
    on every page: a callable taking the page number, or a str."""
    out = []
    for n, lines in enumerate(pages, 1):
        items, y = [], 700
        if header:
            items.append((margin, 760, header(n) if callable(header) else header))
        for ln in lines:
            if isinstance(ln, tuple):
                off, t = ln
                x = 230 if off is None else margin + off
            else:
                x, t = margin, ln
            if t:
                items.append((x, y, t))
            y -= pitch
        if footer:
            items.append((300, 40, footer(n) if callable(footer) else footer))
        out.append(items)
    return pdf(out)

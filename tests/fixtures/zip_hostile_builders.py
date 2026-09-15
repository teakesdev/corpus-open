"""Hostile-zip builders for matter-kit DOCX guard tests (increment 3 fixtures).

STAGED artifact — provenance and parameters from the 2026-09-14 flash experiment
(measured: declared-count 5 over a real 6.3 MB central directory / 3,001 entries ×
2 KB comments → 1 ZipFile construction, 68 ms, 14.2 MB peak before refusal).
Commit target: tests/fixtures/zip_hostile_builders.py (stdlib-only, no matterkit imports).

Variants provided:
  1. understated EOCD count, honest cd_size   (post-open-detection case)
  2. understated EOCD count AND cd_size        (deepseek's downward-lie hypothesis test)
  3. honest oversized count                    (pre-open members-guard case, 0 constructions)
  4. honest oversized count AND honest large cd_size (the MAX_CD_BYTES target case)
  5. EOCD comment embedding PK\x05\x06         (P3-1 right-to-left-scan case)
"""

import io
import struct
import zipfile

_EOCD_SIG = b"PK\x05\x06"


def build_docx_zip(n_members=3_000, comment_bytes=2_048, comment_payload=None):
    """Build a docx-shaped zip: one real document part + n_members pad entries.

    comment_payload: bytes for pad-entry comments (default b'C'*comment_bytes).
    Returns (bytes, real_entries_total, real_cd_size).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr(
            "word/document.xml",
            '<w:document xmlns:w="urn:x"><w:body><w:p/></w:body></w:document>',
        )
        for i in range(n_members):
            zi = zipfile.ZipInfo(f"pad/{i:05d}.bin")
            zi.comment = (
                comment_payload if comment_payload is not None else b"C" * comment_bytes
            )
            z.writestr(zi, b"\x00" * 8)
    data = buf.getvalue()
    i = data.rfind(_EOCD_SIG)
    _, _, _, _, _, cd_size, _cd_offset, _clen = struct.unpack_from("<IHHHHIIH", data, i)
    return data, n_members + 1, cd_size


def patch_eocd(data, entries_total=None, cd_size=None, comment=None):
    """Rewrite EOCD fields in place on a copy. comment replaces the EOCD comment."""
    out = bytearray(data)
    i = out.rfind(_EOCD_SIG)
    if i == -1:
        raise ValueError("no EOCD")
    if entries_total is not None:
        struct.pack_into("<HH", out, i + 8, entries_total, entries_total)
    if cd_size is not None:
        struct.pack_into("<I", out, i + 12, cd_size)
    if comment is not None:
        if len(comment) > 0xFFFF:
            raise ValueError("comment too long")
        struct.pack_into("<H", out, i + 20, len(comment))
        return bytes(out[: i + 22] + comment)
    return bytes(out)


def understated_count_case(n_members=3_000, comment_bytes=2_048, declared_count=5):
    """Variant 1: EOCD lies about count only. Real directory is full size."""
    data, real, cd = build_docx_zip(n_members, comment_bytes)
    return patch_eocd(data, entries_total=declared_count), real, cd


def understated_cd_size_case(n_members=3_000, comment_bytes=2_048, declared_count=5):
    """Variant 2: EOCD lies about count AND cd_size (downward lie).

    cd_size declared as just the first ~64 bytes of the real directory.
    ANSWERED (CPython 3.11 zipfile._RealGetContents reads exactly size_cd):
    declared cd_size bounds the parser's input — a bounded-input parser
    rejection via BadZipFile ("Truncated central directory"). NOTE: 0
    successful constructor returns is NOT proof of zero allocation /
    partial materialization; spy tests retain ATTEMPT counts alongside
    returns. This case pins a property of CPython's parser, NOT of our
    CD cap (the cap passes here: declared 64 <= cap). Test at two layers:
    assertRaises(BadZipFile) at the direct zipfile layer; the public
    extractor test expects the structured failure result, whose reason
    must be distinguishable from the cap-refusal reason.
    """
    data, real, cd = build_docx_zip(n_members, comment_bytes)
    return patch_eocd(data, entries_total=declared_count, cd_size=64), real, cd


def over_members_case(n_members=10_500):
    """Variant 3: honest tail records, entry count over MAX_ARCHIVE_MEMBERS."""
    data, real, cd = build_docx_zip(n_members, comment_bytes=0)
    return data, real, cd


def oversized_cd_honest_case(n_members=3_000, comment_bytes=2_048):
    """Variant 4: everything honest, but the central directory is huge.

    This is the case MAX_CD_BYTES must refuse pre-open.
    """
    data, real, cd = build_docx_zip(n_members, comment_bytes)
    return data, real, cd


def comment_signature_case(n_members=3, comment_bytes=64):
    """Variant 5: EOCD comment whose bytes contain the EOCD signature itself.

    A naive rfind(_EOCD_SIG) lands inside the comment; the right record is the
    one whose declared comment length ends exactly at EOF (P3-1 scan case).
    """
    data, real, cd = build_docx_zip(n_members, comment_bytes)
    return patch_eocd(data, comment=b"junk PK\x05\x06 inside comment" + b"x" * 32)


if __name__ == "__main__":
    # Self-test: regenerate the measured case-1 shape and report geometry.
    data, real, cd = understated_count_case()
    print(f"case1 rebuilt: {len(data)/1e6:.1f} MB file, {real} real entries, {cd/1e6:.2f} MB real cd, declared count 5")
    d2, r2, c2 = understated_cd_size_case()
    print(f"variant2 built: cd_size declared 64 (real {c2})")
    d5 = comment_signature_case()
    print(f"variant5 built: EOCD comment embeds PK\\x05\\x06 ({len(d5)} bytes)")

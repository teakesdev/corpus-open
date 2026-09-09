#!/usr/bin/env python3
"""Set A/B/C canonical scorer (rev3, per chair instruction 2026-09-08).

Canonical standard — ONE mode, exact source spans:
  * Every prediction is scored against the parser's ORIGINAL source offsets:
    matched iff (doc, start, end) equals the gold item's (doc, start, end).
    Gold offsets are derived deterministically from the committed docs by
    first-occurrence search of the frozen gold string (each gold string occurs
    exactly once per doc; the scorer hard-errors if not).
  * Normalized text (eyecite `corrected_citation()`, kit fragments) is stored
    as a SEPARATE field. Normalization quality is reported separately — it
    never affects match status. (Chair: do not mix boundary detection with
    formatting changes.)
  * Union = gold IDs matched by kit OR eyecite, computed from the stored
    per-item matched-ID sets in this file's output.
  * Hygiene (bounded startswith, +2) is a DIAGNOSTIC computed from the same
    predictions — never a second headline.
  * eyecite UnknownCitation tokens are excluded from predictions BY UPSTREAM
    convention; their count is reported as junk so the asymmetry with the
    kit's extraction-failed rows stays visible.

Item-level output (canonical_run.json): gold IDs (A-/B- prefixed, index-
derived from the frozen gold.json), all predictions, matched IDs, misses,
false positives, duplicates, pollution telemetry for BOTH parsers.

Reproducibility: reads only committed gold.json/manifest.json/docs/.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, KIT_ROOT)

from matterkit import citations as K  # noqa: E402
from matterkit.citations import now_iso  # noqa: E402


def gold_with_offsets(gold, manifest):
    """Attach deterministic IDs + source offsets to every gold item."""
    out = []
    for set_key, prefix in (("set_a", "A"), ("set_b", "B")):
        for i, item in enumerate(gold[set_key]["items"]):
            text, doc = item["text"], item["doc"]
            doc_text = open(os.path.join(HERE, "docs", doc)).read()
            start = doc_text.find(text)
            if start < 0:
                raise SystemExit(f"gold string not found in its doc: {text!r} in {doc}")
            out.append({"id": f"{prefix}-{i:03d}", "set": set_key, "style": item["style"],
                        "doc": doc, "start": start, "end": start + len(text), "text": text})
    return out


def predictions_kit(manifest):
    preds, dups = [], 0
    for doc, meta in manifest["docs"].items():
        text = open(os.path.join(HERE, "docs", doc)).read()
        seen = set()
        for r in K.extract_citations(text, document_sha256=meta["sha256"]):
            if r["status"] != "extracted":
                continue  # extraction-failed rows are not predictions (v1 contract)
            key = (doc, r["char_start"], r["char_end"])
            if key in seen:
                dups += 1
                continue
            seen.add(key)
            preds.append({"doc": doc, "start": r["char_start"], "end": r["char_end"],
                          "text": r["raw_fragment"], "normalized": None,
                          "parser": "kit"})
    return preds, dups


def predictions_eyecite(manifest):
    """eyecite predictions with SOURCE spans via c.span; normalized text stored
    separately. Requires the optional local smoke venv; returns None if absent."""
    env_py = "/tmp/eyecite-smoke/bin/python"
    if not os.path.exists(env_py):
        return None, 0, 0
    import subprocess
    probe = r'''
import json, sys
from eyecite import get_citations
from importlib.metadata import version as _v
manifest = json.load(open(sys.argv[1]))
out, junk, nospan = {}, 0, 0
for doc in manifest["docs"]:
    text = open(f"{sys.argv[2]}/docs/{doc}").read()
    rows = []
    for c in get_citations(text):
        if type(c).__name__ == "UnknownCitation":
            junk += 1
            continue
        span = getattr(c, "span", None)
        if span is None:
            nospan += 1
            continue
        span = span() if callable(span) else span
        s, e = span
        try:
            norm = c.corrected_citation()
        except Exception:
            norm = None
        rows.append({"doc": doc, "start": s, "end": e, "text": text[s:e], "normalized": norm})
    out[doc] = rows
print(json.dumps({"preds": out, "junk": junk, "nospan": nospan,
                  "version": _v("eyecite")}))
'''
    r = subprocess.run([env_py, "-c", probe, os.path.join(HERE, "manifest.json"), HERE],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise SystemExit(f"eyecite probe failed: {r.stderr[-300:]}")
    data = json.loads(r.stdout)
    preds = [p for rows in data["preds"].values() for p in rows]
    for p in preds:
        p["parser"] = "eyecite"
    return preds, data["junk"], data["nospan"]


def match(gold_items, preds):
    """Exact span matching. Returns per-parser record with item-level detail.
    A prediction is consumed by the first gold span it matches — it can never
    match two gold items, and unmatched predictions are the false positives.
    (Self-test test_match_consumes_prediction pins this.)"""
    fps_by_doc = {}
    for p in preds:
        fps_by_doc.setdefault(p["doc"], []).append(p)
    matched, missed = [], []
    for g in gold_items:
        pool = fps_by_doc.get(g["doc"], [])
        hit = None
        rest = []
        for p in pool:
            if hit is None and p["start"] == g["start"] and p["end"] == g["end"]:
                hit = p          # span equality; text equality asserted below
            else:
                rest.append(p)
        if hit is not None:
            assert hit["text"] == g["text"], f"span match text drift: {hit} vs {g}"
            matched.append(g["id"])
            fps_by_doc[g["doc"]] = rest   # consume: no double-matching
        else:
            missed.append(g["id"])
    fps = [p for items in fps_by_doc.values() for p in items]
    return {"matched_ids": matched, "missed_ids": missed, "false_positives": fps}


def hygiene_diagnostic(gold_items, preds):
    """SECONDARY diagnostic: same start, startswith gold text, len <= gold+2.
    Never a headline. (This is the rule that produced the rev2 60-vs-45
    eyecite discrepancy — kept only so its effect stays measured.)"""
    by_doc = {}
    for p in preds:
        by_doc.setdefault(p["doc"], []).append(p)
    hits = []
    for g in gold_items:
        for p in by_doc.get(g["doc"], []):
            if (p["start"] == g["start"] and p["text"].startswith(g["text"])
                    and len(p["text"]) <= len(g["text"]) + 2):
                hits.append(g["id"])
                break
    return hits


def pollution_telemetry(gold_items, preds):
    """Predictions that CONTAIN a gold string but span it differently —
    prefix/suffix pollution, first-class for BOTH parsers (chair/Deepseek)."""
    out = []
    for p in preds:
        for g in gold_items:
            if g["doc"] == p["doc"] and g["text"] in p["text"] \
               and (p["start"], p["end"]) != (g["start"], g["end"]):
                out.append({"parser": p["parser"], "doc": p["doc"], "gold_id": g["id"],
                            "span": [p["start"], p["end"]], "text": p["text"]})
                break
    return out


def norm_mismatch(eyecite_preds, matched_ids, id2gold):
    """Normalization evaluated SEPARATELY from boundary detection (chair)."""
    rows = []
    matched_pairs = [(p, id2gold[gid]) for gid in matched_ids
                     for p in eyecite_preds
                     if p["doc"] == id2gold[gid]["doc"]
                     and (p["start"], p["end"]) == (id2gold[gid]["start"], id2gold[gid]["end"])]
    for p, g in matched_pairs:
        if p["normalized"] is not None and p["normalized"] != g["text"]:
            rows.append({"gold_id": g["id"], "source": g["text"], "normalized": p["normalized"]})
    return rows


def main():
    gold = json.load(open(os.path.join(HERE, "gold.json")))
    manifest = json.load(open(os.path.join(HERE, "manifest.json")))
    gitems = gold_with_offsets(gold, manifest)
    id2gold = {g["id"]: g for g in gitems}

    kit_preds, kit_dups = predictions_kit(manifest)
    eye_preds, eye_junk, eye_nospan = predictions_eyecite(manifest)

    kit_rec = match(gitems, kit_preds)
    eye_rec = match(gitems, eye_preds) if eye_preds is not None else None

    kit_ids, eye_ids = set(kit_rec["matched_ids"]), set(eye_rec["matched_ids"] if eye_rec else [])
    union_ids = sorted(kit_ids | eye_ids, key=lambda i: (i[0], int(i[2:])))

    run = {
        "canonical": True,
        "standard": "exact source span: prediction matched iff (doc,start,end) == gold (doc,start,end); "
                    "normalized text stored separately, never affecting match",
        "generated_at": now_iso(),
        "gold_sha256": manifest["gold_sha256"],
        "n_gold": len(gitems),
        "parsers": {
            "kit": {
                "head": subprocess_head(),
                "n_predictions": len(kit_preds),
                "duplicates_collapsed": kit_dups,
                "matched_ids": kit_rec["matched_ids"],
                "missed_ids": kit_rec["missed_ids"],
                "false_positives": kit_rec["false_positives"],
                "hygiene_diagnostic_hits": hygiene_diagnostic(gitems, kit_preds),
            },
            "eyecite": None if eye_rec is None else {
                "n_predictions": len(eye_preds),
                "junk_unknown_tokens_excluded": eye_junk,
                "predictions_without_span": eye_nospan,
                "matched_ids": eye_rec["matched_ids"],
                "missed_ids": eye_rec["missed_ids"],
                "false_positives": eye_rec["false_positives"],
                "hygiene_diagnostic_hits": hygiene_diagnostic(gitems, eye_preds),
                "normalization_mismatches_on_matched": norm_mismatch(
                    eye_preds, eye_rec["matched_ids"], id2gold),
            },
        },
        "union": {
            "ids": union_ids,
            "count": len(union_ids),
            "kit_only": sorted(kit_ids - eye_ids),
            "eyecite_only": sorted(eye_ids - kit_ids),
            "neither_count": len(gitems) - len(union_ids),
        },
        "pollution_telemetry_both_parsers": pollution_telemetry(gitems, kit_preds + (eye_preds or [])),
    }

    def summarize(rec):
        if rec is None:
            return None
        mA = sum(1 for i in rec["matched_ids"] if i.startswith("A"))
        mB = sum(1 for i in rec["matched_ids"] if i.startswith("B"))
        nA = sum(1 for g in gitems if g["set"] == "set_a")
        nB = sum(1 for g in gitems if g["set"] == "set_b")
        return {"set_a": f"{mA}/{nA}", "set_b": f"{mB}/{nB}",
                "set_b_recall": round(mB / nB, 4), "set_a_recall": round(mA / nA, 4),
                "fp": len(rec["false_positives"])}

    run["summary_exact"] = {"kit": summarize(kit_rec), "eyecite": summarize(eye_rec),
                            "union_set_b": f"{sum(1 for i in union_ids if i.startswith('B'))}/180",
                            "note": "hygiene numbers live in parsers.*.hygiene_diagnostic_hits — "
                                    "diagnostic only, never a headline"}

    out = os.path.join(HERE, "canonical_run.json")
    json.dump(run, open(out, "w"), indent=1)


def subprocess_head():
    import subprocess
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, cwd=KIT_ROOT).stdout.strip()


if __name__ == "__main__":
    main()

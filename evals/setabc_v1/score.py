#!/usr/bin/env python3
"""Set A/B/C v1 scorer — committed per adversarial review (Deepseek, 2026-09-08).

Reproducibility contract:
  python3 evals/setabc_v1/score.py            # exact-match (primary standard)
  python3 evals/setabc_v1/score.py --hygiene  # secondary: bounded-containment rule

Matching standards, explicitly defined:
  EXACT (primary): an extracted row counts as a true positive for a gold item
    iff row["status"] == "extracted" and row["raw_fragment"] == gold["text"].
    No substring, no normalization. A polluted span (sentence prefix swallowed
    by the regex) fails exact match BY DESIGN — span hygiene is part of parser
    quality.
  HYGIENE (secondary, labeled): gold text contained in the fragment AND
    len(fragment) <= len(gold) + 2 (tolerates a trailing period only, not
    sentence prefixes). Exists to separate "detected but dirty span" from
    "not detected"; never used for the headline number.

Usage counters come from the committed score JSONs; this script recomputes
everything from gold.json + manifest.json + docs/ so the numbers live or die
by the artifacts alone.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # kit root

from matterkit import citations as K  # noqa: E402


def run_kit(manifest):
    out = {}
    for doc, meta in manifest["docs"].items():
        text = open(os.path.join(HERE, "docs", doc)).read()
        out[doc] = K.extract_citations(text, document_sha256=meta["sha256"])
    return out


def run_eyecite(manifest):
    """eyecite comparison — runs only if the optional venv exists; never a default dep."""
    env_py = "/tmp/eyecite-smoke/bin/python"
    if not os.path.exists(env_py):
        return None
    import subprocess
    probe = r'''
import json, sys
from eyecite import get_citations
manifest = json.load(open(sys.argv[1]))
out = {}
for doc in manifest["docs"]:
    text = open(f"{sys.argv[2]}/docs/{doc}").read()
    rows = []
    for c in get_citations(text):
        if type(c).__name__ == "UnknownCitation":
            continue
        try:
            corr = c.corrected_citation()
        except Exception:
            corr = ""
        rows.append(corr)
    out[doc] = rows
print(json.dumps(out))
'''
    r = subprocess.run([env_py, "-c", probe, os.path.join(HERE, "manifest.json"), HERE],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)


def _normalize(rows):
    """kit rows (dicts) -> [(fragment, status)]; eyecite rows (strings) -> [(text, 'extracted')]."""
    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append((r["raw_fragment"], r.get("status", "extracted")))
        else:
            out.append((r, "extracted"))
    return out


def _exact_hit(norm, gold_text):
    return any(frag == gold_text and status == "extracted" for frag, status in norm)


def _hygiene_hit(norm, gold_text):
    """Bounded containment: tolerates a trailing period only, never sentence prefixes."""
    for frag, status in norm:
        if status != "extracted":
            continue
        if frag == gold_text:
            return True
        if gold_text in frag and len(frag) <= len(gold_text) + 2 and frag.startswith(gold_text):
            return True
    return False


def score(gold, extracted, mode):
    """Return (missed, per_style, polluted_spans) under the chosen mode."""
    per_style = {}
    missed, polluted = [], []
    matcher = _exact_hit if mode == "exact" else _hygiene_hit
    for g in gold:
        style = g["style"]
        s = per_style.setdefault(style, {"n": 0, "hits": 0})
        s["n"] += 1
        norm = _normalize(extracted.get(g["doc"], []))
        if matcher(norm, g["text"]):
            s["hits"] += 1
        else:
            missed.append(g)
        # span hygiene telemetry (kit rows only — dicts carry status)
        for frag, status in norm:
            if status == "extracted" and g["text"] in frag and frag != g["text"]:
                polluted.append({"doc": g["doc"], "gold": g["text"], "fragment": frag})
                break
    return missed, per_style, polluted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hygiene", action="store_true",
                    help="secondary bounded-containment standard (see docstring)")
    args = ap.parse_args()
    mode = "hygiene" if args.hygiene else "exact"

    gold = json.load(open(os.path.join(HERE, "gold.json")))
    manifest = json.load(open(os.path.join(HERE, "manifest.json")))

    kit_rows = run_kit(manifest)
    eyecite = run_eyecite(manifest)

    report = {"mode": mode, "freezes": {
        "gold_sha256": manifest["gold_sha256"]}}
    for name, rows in (("kit", kit_rows), ("eyecite", eyecite)):
        if rows is None:
            report[name] = "not available"
            continue
        for set_key in ("set_a", "set_b"):
            items = gold[set_key]["items"]
            missed, per_style, polluted = score(items, rows, mode)
            n = len(items)
            tp = n - len(missed)
            report.setdefault(name, {})[set_key] = {
                "tp": tp, "n": n, "recall": round(tp / n, 3),
                "per_style": per_style,
                "missed_examples": [m["text"] for m in missed[:8]],
                "polluted_span_count": len(polluted),
                "polluted_examples": polluted[:5],
            }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()

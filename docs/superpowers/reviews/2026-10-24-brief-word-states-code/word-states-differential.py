"""Splitter and brief-check differential for fix/brief-word-states: briefcheck at two commits.

Exports matterkit/ and tests/ at each commit (`git archive`) into a temp folder and, in a child
interpreter per commit, prints (1) briefcheck._sentence_spans() sentences for a fixed list of
synthetic texts and (2) `brief check` results (exit code, findings, bound store rows) for a few
synthetic drafts against the tests/test_briefcheck.py fixture matter.

usage: python3 word-states-differential.py <repo> <base sha> <tip sha>
"""
import json
import os
import subprocess
import sys
import tempfile

TEXTS = [
    # a word-like state, then prose
    "Plaintiff attended Mass. The deputy saw him.",
    "Plaintiff attended Mass. Trial began on Monday.",
    "Plaintiff moved to Wash. 2 weeks later he left.",
    "Plaintiff attended Mass. S. Smith testified.",
    "Plaintiff attended Mass. R. Jones testified.",
    "Plaintiff attended Mass. Code enforcement arrived.",
    "Plaintiff was treated at Mass. Gen. Hosp. The bill followed.",
    # a word-like state, then the rest of its cite
    "See Mass. Gen. Laws ch. 260, § 2A. The court agreed.",
    "See Doe v. Roe, 1 N.E.2d 1 (Mass. 1985). The court agreed.",
    "Directors act under 8 Del. C. § 141. The board met.",
    "The right is in Ill. Const. art. I, § 2. The court agreed.",
    "See Wash. Const. art. I, § 7. The court agreed.",
    "See 1 Mass. L. Rptr. 1 (1993). The court agreed.",
    "See Smith v. Wash. State Dep't of Corr., 1 P.3d 1 (Wash. 2001).",
    "See Ill. Admin. Code tit. 20, § 1. The court agreed.",
    # unchanged by the range: other states, lowercase words
    "Plaintiff lives in Richmond, Va. The clerk refused the request.",
    "He was ill. The doctor came.",
]

DRAFTS = [
    # (stored authority or None, draft). A fact sentence needs 4+ words to be checked, and a
    # cite-only sentence ("Doc. 16 at 1.") attaches to the sentence before it.
    (None, "STATEMENT OF FACTS\n\nOn Sunday Plaintiff attended Mass. The deputy saw him at noon. Doc. 16 at 1.\n"),
    (None, "STATEMENT OF FACTS\n\nOn Sunday Plaintiff attended Mass. Trial began on Monday. Doc. 16 at 1.\n"),
    (None, "STATEMENT OF FACTS\n\nOn Sunday Plaintiff moved to Wash. 2 weeks later he left. Doc. 16 at 1.\n"),
    (None, "STATEMENT OF FACTS\n\nOn Sunday Plaintiff attended Mass. S. Smith testified about it. Doc. 16 at 1.\n"),
    (None, "STATEMENT OF FACTS\n\nOn Sunday Plaintiff lives in Richmond, Va. The clerk refused the request. Doc. 16 at 1.\n"),
    ("8 Del. C. § 141", "Directors act under 8 Del. C. § 141. The board met."),
    ("Mass. Gen. Laws ch. 260, § 2A", "The claim is timely under Mass. Gen. Laws ch. 260, § 2A. The court agreed."),
    ("Wash. Rev. Code § 4.16.080", "The claim is timely under Wash. Rev. Code § 4.16.080. The court agreed."),
]

CHILD = r"""
import json, sys, tempfile
root = sys.argv[1]
sys.path[:0] = [root, root + "/tests"]
from matterkit import briefcheck as b
from test_briefcheck import Matter
texts, drafts = json.loads(sys.argv[2]), json.loads(sys.argv[3])
out = {"sentences": [[t[s:e] for s, e in b._sentence_spans(t, 0, len(t))] for t in texts], "checks": []}
with tempfile.TemporaryDirectory() as d:
    m = Matter(d)
    for stored, draft in drafts:
        m.conn.execute("DELETE FROM authorities")
        if stored:
            m.authority(stored, "source-checked", via="https://example.org/code", evidence="opened official text")
        m.conn.commit()
        r = m.check(draft)
        out["checks"].append({"exit": r.exit_code, "findings": [[f.severity, f.code, f.cite] for f in r.findings],
                              "bound": [[a.display, a.store_citation] for a in r.authorities]})
    m.conn.close()
print(json.dumps(out))
"""


def run(repo, sha, tmp):
    root = os.path.join(tmp, sha)
    os.makedirs(root)
    tar = subprocess.run(["git", "-C", repo, "archive", sha, "matterkit", "tests"],
                         check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", root], input=tar, check=True)
    res = subprocess.run([sys.executable, "-I", "-c", CHILD, root, json.dumps(TEXTS), json.dumps(DRAFTS)],
                         check=True, capture_output=True, text=True)
    return json.loads(res.stdout)


def main(repo, base, tip):
    with tempfile.TemporaryDirectory() as tmp:
        a, b = run(repo, base, tmp), run(repo, tip, tmp)
    print("sentences ('=' same at both commits):")
    for t, sa, sb in zip(TEXTS, a["sentences"], b["sentences"]):
        if sa == sb:
            print(f"  = {t!r}\n      {sb}")
        else:
            print(f"  * {t!r}\n      {base}: {sa}\n      {tip}: {sb}")
    print()
    print("brief check (exit, findings, bound rows):")
    for (stored, draft), ca, cb in zip(DRAFTS, a["checks"], b["checks"]):
        mark = "=" if ca == cb else "*"
        print(f"  {mark} stored={stored!r} draft={draft!r}")
        for sha, c in ((base, ca), (tip, cb)) if ca != cb else ((tip, cb),):
            print(f"      {sha if ca != cb else 'both'}: exit {c['exit']} findings {c['findings']} bound {c['bound']}")


if __name__ == "__main__":
    main(*sys.argv[1:4])

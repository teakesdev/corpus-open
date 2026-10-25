"""Splitter / case-name differential for fix/state-abbrev-cites: briefcheck at two commits.

Exports matterkit/ at each commit (`git archive`) into a temp folder and, in a child
interpreter per commit, prints briefcheck._sentence_spans() sentences and the case-cite
displays from briefcheck.scan_authorities() for a fixed list of synthetic texts.

usage: python3 splitter-differential.py <repo> <base sha> <tip sha>
"""
import json
import os
import subprocess
import sys
import tempfile

TEXTS = [
    # the reported cut, and a state from each part of the list
    "The clerk acted under W. Va. Code § 21-23-7, as the order notes. The order says so.",
    "Venue lies under Mass. Gen. Laws ch. 223, § 1. The court agreed.",
    "The claim is timely under Or. Rev. Stat. § 12.110, as pleaded.",
    "Fees follow under Ky. Rev. Stat. Ann. § 12-341. The court agreed.",
    "See Smith v. Pa. Dep't of Corr., 1 F.3d 1 (3d Cir. 2000).",
    "See Doe v. Roe, 1 A.3d 1 (Pa. 2010). The court agreed.",
    # lowercase words that end ordinary sentences
    "He was ill. The doctor came.", "She called me. Then she left.",
    "It reached critical mass. The reaction began.", "The trade was a wash. The parties settled.",
    "She said or. Then she left.",
    # the trade-off: a sentence that really ends in a capitalized state abbreviation
    "Plaintiff lives in Richmond, Va. She was arrested on May 1. Doc. 16 at 2.",
    "He attended Mass. Afterwards he went home.",
    "The company is a Del. corporation. It was formed in 2001.",
    # already in ABBREV before the range, for comparison
    "Plaintiff lives in Jackson, Miss. She was arrested on May 1. Doc. 16 at 2.",
]

CHILD = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from matterkit import briefcheck as b
out = []
for t in json.loads(sys.argv[2]):
    sents = [t[s:e] for s, e in b._sentence_spans(t, 0, len(t))]
    cases = [d for _, d, _, _, _, kind, _ in b.scan_authorities(t) if kind == "case"]
    out.append({"sentences": sents, "cases": cases})
print(json.dumps(out))
"""


def run(repo, sha, tmp):
    root = os.path.join(tmp, sha)
    os.makedirs(root)
    tar = subprocess.run(["git", "-C", repo, "archive", sha, "matterkit"], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", root], input=tar, check=True)
    res = subprocess.run([sys.executable, "-I", "-c", CHILD, root, json.dumps(TEXTS)],
                         check=True, capture_output=True, text=True)
    return json.loads(res.stdout)


def main(repo, base, tip):
    with tempfile.TemporaryDirectory() as tmp:
        a, b = run(repo, base, tmp), run(repo, tip, tmp)
    for t, ra, rb in zip(TEXTS, a, b):
        mark = "=" if ra == rb else "*"
        print(f"{mark} {t!r}")
        if ra == rb:
            print(f"    sentences {rb['sentences']}" + (f"\n    cases {rb['cases']}" if rb["cases"] else ""))
        else:
            for sha, r in ((base, ra), (tip, rb)):
                print(f"    {sha} sentences {r['sentences']}" + (f"\n    {sha} cases {r['cases']}" if r["cases"] else ""))


if __name__ == "__main__":
    main(*sys.argv[1:4])

"""Extraction differential for fix/state-abbrev-cites: citations.py at two commits, same inputs.

Loads matterkit/citations.py from each commit (via `git show`, stdlib-only module) and runs
extract_citations over (1) every evals/*/docs/* file, read in text mode as
evals/setabc_v1/score.py does, comparing (char_start, char_end, raw_fragment, status) per doc,
and (2) a fixed list of synthetic probe strings, printing both sides.

usage: python3 extraction-differential.py <repo> <base sha> <tip sha>
"""
import glob
import importlib.util
import os
import subprocess
import sys
import tempfile

PROBES = [
    # the reported bug and its two-part relatives
    "See W. Va. Code Ann. § 1-1-1.", "See W.Va. Code Ann. § 1-1-1.", "See Va. Code Ann. § 1-1-1.",
    "See N. H. Rev. Stat. Ann. § 1-1.", "See N.H. Rev. Stat. Ann. § 1-1.", "See S. C. Code Ann. § 15-3-530.",
    "See N. Y. Code Ann. § 1.", "See D.C. Code Ann. § 1-1.", "See N. Mar. I. Code Ann. § 1.",
    "See Am. Samoa Code Ann. § 1.",
    # multi-word heads
    "See Ariz. Rev. Stat. Ann. § 12-341.", "See Ky. Rev. Stat. Ann. § 413.140.", "See La. Rev. Stat. Ann. § 9:5604.",
    "See Ohio Rev. Code Ann. § 2305.10.", "See Tex. Civ. Prac. & Rem. Code Ann. § 16.003.",
    "See Cal. Bus. & Prof. Code Ann. § 17200.",
    # must not reach back past the state
    "Acme, Inc. Miss. Code Ann. § 1-1-1.", "See Exhibit A. Va. Code Ann. § 1-1-1 there.",
    "Miss. Code Ann. § 1; W. Va. Code Ann. § 2.", "Under Wyo. Stat. Ann. § 17-29-800.",
    "Id. Miss. Code Ann. § 1-1-1 also applies.", "Smith Co. Tex. Code Ann. § 1 applies.",
    # known limits, expected unchanged
    "See Tex. Gov't Code Ann. § 3.005.", "See Cal. Health & Safety Code Ann. § 11362.5.",
    "See Exampleton Code Ann. § 1-2.", "See Iowa Code Ann. § 614.1.", "See Mo. Ann. Stat. § 516.120.",
    "See Md. Code Ann., Cts. & Jud. Proc. § 5-101.", "See W. Va. Code Annotated § 21-23-7.",
    # other state-code patterns, untouched by this range
    "See W. Va. Code § 1-1-1.", "See Or. Rev. Stat. § 12.110.", "See Wash. Rev. Code § 4.16.080.",
]


def load(repo, sha, tmp):
    src = subprocess.run(["git", "-C", repo, "show", f"{sha}:matterkit/citations.py"],
                         check=True, capture_output=True).stdout
    path = os.path.join(tmp, f"citations_{sha}.py")
    with open(path, "wb") as f:
        f.write(src)
    spec = importlib.util.spec_from_file_location(f"citations_{sha}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rows(mod, text):
    return [(r["char_start"], r["char_end"], r["raw_fragment"], r["status"]) for r in mod.extract_citations(text)]


def main(repo, base, tip):
    with tempfile.TemporaryDirectory() as tmp:
        a, b = load(repo, base, tmp), load(repo, tip, tmp)
        print(f"parser {base}: {a.PARSER_ID}   {tip}: {b.PARSER_ID}")
        docs = sorted(glob.glob(os.path.join(repo, "evals", "*", "docs", "*")))
        changed = total = 0
        for p in docs:
            text = open(p).read()
            ra, rb = rows(a, text), rows(b, text)
            total += len(ra)
            if sorted(ra) != sorted(rb):
                changed += 1
                print(f"== {os.path.relpath(p, repo)}")
                for r in sorted(set(ra) - set(rb)):
                    print("   -", r)
                for r in sorted(set(rb) - set(ra)):
                    print("   +", r)
        print(f"evals: {len(docs)} docs, {total} rows at {base}; {changed} docs differ "
              "on (char_start, char_end, raw_fragment, status)")
        print()
        print("probes (extracted fragments; '=' same at both commits):")
        for t in PROBES:
            fa = [r[2] for r in rows(a, t) if r[3] == "extracted"]
            fb = [r[2] for r in rows(b, t) if r[3] == "extracted"]
            if fa == fb:
                print(f"  = {t!r}\n      {fb}")
            else:
                print(f"  * {t!r}\n      {base}: {fa}\n      {tip}: {fb}")


if __name__ == "__main__":
    main(*sys.argv[1:4])

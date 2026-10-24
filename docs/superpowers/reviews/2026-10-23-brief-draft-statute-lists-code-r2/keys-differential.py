"""Compare authority_keys() at a base commit with the working tree, over a synthetic corpus.

Usage: python3 keys_differential.py <worktree> <base briefcheck.py copy>
Corpus: the strings below plus every quoted citation-like string in <worktree>/tests/*.py.
"""
import glob, importlib.util, re, sys
wt, old_path = sys.argv[1], sys.argv[2]
sys.path.insert(0, wt)
from matterkit import briefcheck as new  # noqa: E402
spec = importlib.util.spec_from_file_location("matterkit.briefcheck_base", old_path)
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
corpus = {
    "Miss. Code Ann. §§ 21-21-3, 21-3-15", "Miss. Code Ann. §§ 21-21-3 and 21-3-15", "Miss. Code Ann. §§ 21-21-3 & 21-3-15",
    "Miss. Code Ann. §§ 21-21-3, and 21-3-15", "Miss. Code Ann. §§ 21-21-3 or 21-3-15", "42 U.S.C. §§ 1983, 1985(3), 1988(b)",
    "28 U.S.C. § 1331; 42 U.S.C. §§ 1983, 1988", "§§ 21-21-3, 21-3-15", "Miss. Code Ann. §§ 11-46-1 to -23",
    "Miss. Code Ann. §§ 21-21-3(1)(a), 21-3-15", "Miss. Code Ann. §§ 21-21-3 et seq.", "Miss. Code Ann. §§ 21-21-3.",
    "Fed. R. Civ. P. 5(b); 6(a), (d); 15", "Fed. R. Civ. P. 4(m); Miss. Code Ann. §§ 15-1-49, 15-1-35; 11",
    "Miss. Code Ann. §§ 15-1-49, 15-1-35; Fed. R. Civ. P. 4(m); 6", "18 U.S.C. §§ 241, 242; 42 U.S.C. § 1983",
    "Miss. Code Ann. § 21-3-15", "42 U.S.C. § 1983", "Ala. Code §§ 6-2-38, 6-5-410", "Tex. Civ. Prac. & Rem. Code §§ 16.003, 16.068",
    "Section 21-23-8", "Miss. Code Annotated § 21-23-8", "U.S. Const. amend. XIV, § 1", "Miss. Const. art. 3, §§ 14, 24",
    "Cal. Gov't Code §§ 810-996.6", "Miss. Code Ann. §§ 11-46-9(1)(c) and (d)", "§§ 1983, 1988", "29 C.F.R. §§ 1630.2, 1630.9",
    # a list followed by ", <another cite>"
    "Miss. Code Ann. §§ 21-21-3, 21-3-15, 42 U.S.C. § 1983", "42 U.S.C. §§ 1983 and 1988, 28 U.S.C. § 1331",
    "Miss. Code Ann. §§ 21-21-3, 21-3-15, Miss. Code Ann. § 11-46-9", "18 U.S.C. §§ 241, 242, 42 U.S.C. §§ 1983, 1985",
    "Miss. Code Ann. §§ 21-21-3, 21-3-15, 550 U.S. 544", "Miss. Code Ann. §§ 21-21-3, 21-3-15, 2019 WL 1234567",
    "Miss. Code Ann. §§ 21-21-3, 21-3-15, Fed. R. Civ. P. 15", "29 C.F.R. §§ 1630.2, 1630.9, 42 U.S.C. § 12112",
    # a parenthetical after a listed section (SWE-2 round 1 P1) and a numeric range member (P2)
    "Miss. Code Ann. §§ 21-21-3, 21-3-15 (42 U.S.C. § 1983)", "Miss. Code Ann. §§ 21-21-3, 21-3-15 (see 42 U.S.C. § 1983)",
    "Miss. Code Ann. §§ 21-21-3, 21-3-15 (550 U.S. 544)", "Miss. Code Ann. §§ 21-21-3, 21-3-15 (2019 WL 1234567)",
    "Miss. Code Ann. §§ 21-21-3 (Rev. 2019), 21-3-15", "Miss. Code Ann. §§ 21-21-3, 21-3-15 (Rev. 2019)",
    "Miss. Code Ann. §§ 21-21-3, 21-3-15 (2019)", "Miss. Code Ann. §§ 21-21-3, 21-3-15(2)(ii)",
    "Fla. Stat. §§ 605.0101, 605.0102 - 605.1108", "Fla. Stat. §§ 605.0101 - 605.0105, 605.0201",
    "42 U.S.C. §§ 1983, 1985a", "Miss. Code Ann. §§ 21-21-3, 21-3-15 and 5 others",
}
for f in glob.glob(wt + "/tests/*.py"):
    for m in re.finditer(r'"([^"\n]{3,120})"', open(f, encoding="utf-8").read()):
        if re.search(r"§|U\.S\.C|Code|R\. Civ|Rule|Stat\.|C\.F\.R", m[1]):
            corpus.add(m[1])
diff = 0
for c in sorted(corpus):
    a, b = old.authority_keys(c), new.authority_keys(c)
    if a != b:
        diff += 1
        print(repr(c), "\n   only base:", sorted(a - b), "\n   only new: ", sorted(b - a))
print(f"{len(corpus)} citations, {diff} differ")

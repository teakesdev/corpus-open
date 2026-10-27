"""Stored keys and section maps at a base tree vs a fix tree, over a synthetic corpus.
Usage: python3 xref_differential.py <base tree> <fix tree>"""
import glob, json, re, subprocess, sys
base, fix = sys.argv[1], sys.argv[2]
corpus = {
    "Miss. Code Ann. § 21-23-9 (Supp. 2019)", "Miss. Code Ann. § 21-23-9 (West 2019)", "42 U.S.C. § 1983 (2018)",
    "42 U.S.C. § 1983(a)(1)", "Miss. Code Ann. § 21-23-9 (550 U.S. 544)", "Miss. Code Ann. § 21-23-9 (42 U.S.C. § 1983)",
    "Section 21-23-7, 21-23-9 (550 U.S. 544)", "Section 21-23-7 or 21-23-9 (550 U.S. 544)", "sections 502 or 503 (550 U.S. 544)",
    "Sections 21-23-7 and 21-23-9 (see 42 U.S.C. § 1983)", "Section 21-23-7, 21-23-9 (Rev. 2019)", "Section 21-23-7(b), 21-23-9(a)",
    "Miss. Code Ann. § 41-1983 (1983)", "5 U.S.C. § 552(b)(2)", "Miss. Code Ann. §§ 21-21-3, 21-3-15 (42 U.S.C. § 1983)",
    "Miss. Code Ann. § 11-46-9(1)(c) (Rev. 2019)", "29 C.F.R. § 1630.2(g) (2020)", "Cal. Gov't Code § 810 (Deering 2019)",
}
for f in glob.glob(fix + "/tests/*.py"):
    for m in re.finditer(r'"([^"\n]{3,140})"', open(f, encoding="utf-8").read()):
        if re.search(r"§|U\.S\.C|Code|Stat\.|C\.F\.R|[Ss]ections?\s+\d", m[1]):
            corpus.add(m[1])
prog = ("import json,sys; sys.path.insert(0, sys.argv[1]); from matterkit import briefcheck as b; c=json.load(sys.stdin);"
        "print(json.dumps({x: [sorted(b.authority_keys(x)), {k: sorted(v) for k, v in b.statute_sections(x).items()}] for x in c}))")
def run(tree):
    out = subprocess.run([sys.executable, "-I", "-c", prog, tree], input=json.dumps(sorted(corpus)), capture_output=True, text=True, check=True)
    return json.loads(out.stdout)
a, b = run(base), run(fix)
diff = [c for c in sorted(corpus) if a[c] != b[c]]
for c in diff:
    print(repr(c)); print("   base:", a[c]); print("   fix: ", b[c])
print(f"{len(corpus)} citations, {len(diff)} differ in authority_keys or statute_sections")

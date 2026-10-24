"""Wrap fuzz: does a line break anywhere in a cite-heavy draft crash brief check?

For each commit (argv, or the defaults below) this exports matterkit/, tests/ and matter.py with
`git archive`, builds the synthetic `tests/test_briefcheck.py::Matter` fixture, and runs
`briefcheck.check` on every variant of each draft below with one space replaced by "\\n". It prints
the first draft fragment for each crash site and a count per site.

Run from the worktree root:
  python3 -I docs/superpowers/reviews/2026-10-24-brief-rule-linebreaks-code/wrap-fuzz.py [commit ...]
"""
import io
import subprocess
import sys
import tarfile
import tempfile

COMMITS = sys.argv[1:] or ["e77da0c", "bebaaac", "ff4a364", "f523e2d"]
ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()

DRAFTS = [
    "Under Fed. R. Civ. P. 12(b)(6) and 15, the motion is timely. Doc. 16 at 2.",
    "See Bell Atl. Corp. v. Twombly, 550 U.S. 544, 555 (2007); Ashcroft v. Iqbal, 556 U.S. 662 (2009).",
    "Twombly, 550 U.S. at 556. Id. at 557. 2019 WL 1234567, at *3.",
    "The statute applies. 42 U.S.C. § 1983; Miss. Code Ann. §§ 21-21-3, 21-3-15; W. Va. Code § 21-23-7.",
    "Local Civil Rule 7(b)(4) and L.U.Civ.R. 7(b)(2) govern; Local Bankruptcy Rule 3 too. Rule 15 applies.",
    "Plaintiff alleges it. Compl. ¶¶ 2-3; Am. Compl. ¶ 4; FAC ¶ 5. [Docs. 16 and 27.] ECF No. 19.",
    "Section 21-23-7 of Title 21 and Sec. 5 apply. Pub. L. No. 107-204. 12 C.F.R. § 1026.4.",
]

RUN = r"""
import collections, json, os, sys, tempfile, traceback
root = sys.argv[1]; sys.path.insert(0, root); sys.path.insert(0, os.path.join(root, "tests"))
import test_briefcheck as tb
drafts = json.load(sys.stdin)
sites, first, runs = collections.Counter(), {}, 0
with tempfile.TemporaryDirectory() as d:
    m = tb.Matter(d)
    for draft in drafts:
        for i, ch in enumerate(draft):
            if ch != " ":
                continue
            v = draft[:i] + "\n" + draft[i + 1:]
            runs += 1
            try:
                m.check(v)
            except Exception as e:
                f = traceback.extract_tb(e.__traceback__)[-1]
                site = f"{type(e).__name__} {os.path.basename(f.filename)}:{f.lineno}"
                sites[site] += 1
                first.setdefault(site, v[max(0, i - 25):i + 25])
    m.conn.close()
print(json.dumps({"runs": runs, "sites": sites, "first": first}))
"""


def export(commit, dest):
    data = subprocess.run(["git", "-C", ROOT, "archive", commit, "matterkit", "tests", "matter.py"],
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest, filter="data")


import json  # noqa: E402

for c in COMMITS:
    with tempfile.TemporaryDirectory() as d:
        export(c, d)
        p = subprocess.run([sys.executable, "-I", "-c", RUN, d], input=json.dumps(DRAFTS), capture_output=True,
                           text=True)
    if p.returncode:
        print(f"{c}: harness failed\n{p.stderr}")
        continue
    out = json.loads(p.stdout)
    print(f"{c}: {out['runs']} wrapped drafts, crashes {sum(out['sites'].values())}")
    for site, n in sorted(out["sites"].items()):
        print(f"    {n} x {site}   first: {out['first'][site]!r}")

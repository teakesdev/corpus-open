"""Differential wrap fuzz: which cites does brief check lose, gain or crash on when a draft is hard-wrapped?

For each commit (argv, or the defaults below) this exports the tree with `git archive`, builds the
synthetic `tests/test_briefcheck.py::Matter` fixture, and runs `briefcheck.check` on every variant of each
draft below with one space replaced by each WRAP. It compares the cites found (authority keys, record and
pleading targets) with the unwrapped draft. The last two lines probe the "§§" list parenthetical crash
in a draft and in a stored citation.

Run from the worktree root:
  python3 -I docs/superpowers/reviews/2026-10-24-brief-rule-linebreaks-code-r2/wrap-diff.py [commit ...]
"""
import io
import json
import subprocess
import sys
import tarfile
import tempfile

COMMITS = sys.argv[1:] or ["11f607b", "16659e9"]
ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()

WRAPS = ["\n", " \n", "  \n"]
DRAFTS = [
    "Under Fed. R. Civ. P. 12(b)(6) and 15, the motion is timely. Doc. 16 at 2.",
    "See Bell Atl. Corp. v. Twombly, 550 U.S. 544, 555 (2007); Ashcroft v. Iqbal, 556 U.S. 662 (2009).",
    "Twombly, 550 U.S. at 556. Id. at 557. 2019 WL 1234567, at *3.",
    "The case is 100 F. Supp. 2d 200 (S.D. Miss. 2000), and 5 F.4th 9 (5th Cir. 2021). Doc. 16.",
    "The statute applies. 42 U.S.C. § 1983; Miss. Code Ann. §§ 21-21-3, 21-3-15; W. Va. Code § 21-23-7.",
    "Local Civil Rule 7(b)(4) and L.U.Civ.R. 7(b)(2) govern; Local Bankruptcy Rule 3 too. Rule 15 applies.",
    "Plaintiff alleges it. Compl. ¶¶ 2-3; Am. Compl. ¶ 4; FAC ¶ 5. [Docs. 16 and 27.] ECF No. 19.",
    "Section 21-23-7 of Title 21 and Sec. 5 apply. Pub. L. No. 107-204. 12 C.F.R. § 1026.4.",
    "Tex. R. Civ. P. 91 and Fed. R. Evid. 401 and Fed. R. App. P. 4 apply. Doc. 16.",
    "Under Section 21-23-7, 21-23-8 and Miss. Code Annotated § 21-23-9, the clerk acts. Doc. 16.",
    "Sections 552.101 and 552.102 of the Tex. Gov't Code apply; Section 5.1 too. Doc. 16.",
    "Fed. R. Civ. P. 15(a) controls, and Rule 15 also requires leave. Doc. 16.",
    "Miss. Code Ann. §§ 21-3-15 (a) and (b), 21-4 and 42 U.S.C. §§ 1983, 1985 apply. Doc. 16.",
]

RUN = r"""
import collections, json, os, sys, tempfile
root = sys.argv[1]; sys.path.insert(0, root); sys.path.insert(0, os.path.join(root, "tests"))
import test_briefcheck as tb
drafts, wraps = json.load(sys.stdin)

def cites(m, draft):
    r = m.check(draft)
    out = collections.Counter(a.key for a in r.authorities)
    for row in r.rows:
        for c in row.cites:
            if c.kind != "authority":
                out[str((c.kind, c.target))] += 1
    return out

res = {"runs": 0, "crash": 0, "lost": 0, "gained": 0, "examples": []}
with tempfile.TemporaryDirectory() as d:
    m = tb.Matter(d)
    for draft in drafts:
        base = cites(m, draft)
        for i, ch in enumerate(draft):
            if ch != " ":
                continue
            for w in wraps:
                v = draft[:i] + w + draft[i + 1:]
                res["runs"] += 1
                try:
                    got = cites(m, v)
                except Exception as e:
                    res["crash"] += 1
                    if len(res["examples"]) < 6:
                        res["examples"].append(["crash " + type(e).__name__, v[max(0, i - 20):i + 20]])
                    continue
                lost, gained = base - got, got - base
                res["lost"] += sum(lost.values()); res["gained"] += sum(gained.values())
                for k in list(lost)[:1]:
                    if len(res["examples"]) < 6:
                        res["examples"].append(["lost " + k, v[max(0, i - 20):i + 20]])
    probes = {}
    for name, draft, stored in [
            ("list paren in a draft", "The clerk must act. Miss. Code Ann. §§ 21-3-15 (2 and ), 21-4. Doc. 16.", None),
            ("list paren stored", "The clerk must act. Doc. 16.", "Miss. Code Ann. §§ 21-3-15 (2 and ), 21-4")]:
        with tempfile.TemporaryDirectory() as d2:
            m2 = tb.Matter(d2)
            if stored:
                m2.authority(stored, "source-checked", via="https://example.org/x", evidence="opened")
                m2.conn.commit()
            try:
                r = m2.check(draft); probes[name] = f"exit {r.exit_code}"
            except Exception as e:
                probes[name] = "CRASH " + type(e).__name__
            m2.conn.close()
    res["probes"] = probes
    m.conn.close()
print(json.dumps(res))
"""


def export(commit, dest):
    data = subprocess.run(["git", "-C", ROOT, "archive", commit, "matterkit", "tests", "matter.py"],
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest, filter="data")


for c in COMMITS:
    with tempfile.TemporaryDirectory() as d:
        export(c, d)
        p = subprocess.run([sys.executable, "-I", "-c", RUN, d], input=json.dumps([DRAFTS, WRAPS]),
                           capture_output=True, text=True)
    if p.returncode:
        print(f"{c}: harness failed\n{p.stderr}")
        continue
    r = json.loads(p.stdout)
    print(f"{c}: {r['runs']} wrapped variants of {len(DRAFTS)} drafts; crashes {r['crash']}, "
          f"lost cites {r['lost']}, gained cites {r['gained']}; probes {r['probes']}")
    for kind, ex in r["examples"]:
        print(f"    e.g. {kind}: {ex!r}")

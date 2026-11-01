"""Rule-scan differential: every `rule:` result of briefcheck.scan_authorities at two commits.

Exports matterkit/ at each commit (git archive) and scans every evals/*/docs/* file (read in text mode)
plus the synthetic EDGE strings below, then prints the rule rows that differ and the full table.

Run from the worktree root:  python3 -I docs/superpowers/reviews/2026-10-24-state-rule-keys-code/rule-differential.py
"""
import io
import os
import subprocess
import sys
import tarfile
import tempfile

BEFORE, AFTER = "b32d490", "8bbd9f6"
ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()

EDGE = [
    # the bug and its neighbours
    "W. Va. R. Civ. P. 12", "W.Va. R. Civ. P. 12", "Va. R. Civ. P. 12", "W. Va. R. Evid. 401",
    "W. Va. R. App. P. 10(c)", "W. Va. R. Civ. P. 12 and 15",
    # two-part dotted and undotted states
    "N.D. R. Civ. P. 12", "S.C. R. Civ. P. 12", "N.H. R. Civ. P. 12", "R.I. R. Civ. P. 12", "D.C. R. Civ. P. 12",
    "N. D. R. Civ. P. 12", "Ohio R. Civ. P. 12", "Ohio  R. Civ. P. 12", "Ohio. R. Civ. P. 12",
    "Iowa R. Civ. P. 1.421", "Am. Samoa R. Civ. P. 12",
    # forms the base already read: must not change
    "(Fed. R. Civ. P. 12)", "Fed.R.Civ.P.12", "See Fed. R. Civ. P. 12(b)(6).", "*Fed. R. Civ. P. 12*",
    "[Fed. R. Civ. P. 12]", "Rule 12 (Fed.R.Civ.P. 12)", "Fed. R. App. P. 4(a)(1)(A)", "Fed. R. Crim. P. 11",
    "Tex.R.Civ.P. 91", "Mont. R. Civ. P. 12", "Mo. R. Civ. P. 55.03", "Or. R. Civ. P. 21", "Md. R. Civ. P. 2-322",
    "E.D. Va. R. Civ. P. 12", "W.D.Tex.R. Civ. P. 12",
    # must not be read as a state rule
    "the U.S.C. R. Civ. P. 3", "The Ohio R. Smith Co. filed suit.",
    # known limits (see HANDOFF.md)
    "W.  Va. R. Civ. P. 12", "N.  D. R. Civ. P. 12", "W. VA. R. Civ. P. 12",
]


def export(commit, dest):
    data = subprocess.run(["git", "-C", ROOT, "archive", commit, "matterkit"], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest, filter="data")


def texts():
    out = [("edge", t) for t in EDGE]
    docs = os.path.join(ROOT, "evals")
    for dp, _, fs in sorted(os.walk(docs)):
        if os.path.basename(dp) != "docs":
            continue
        for f in sorted(fs):
            with open(os.path.join(dp, f), encoding="utf-8", errors="replace") as fh:
                out.append((os.path.relpath(os.path.join(dp, f), ROOT), fh.read()))
    return out


SCAN = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from matterkit import briefcheck
rows = []
for name, text in json.load(sys.stdin):
    for a in briefcheck.scan_authorities(text):
        if a[0].startswith("rule:"):
            rows.append([name, text if name == "edge" else "", a[0], a[1], a[3]])
json.dump(rows, sys.stdout)
"""


def scan(commit, items):
    import json
    with tempfile.TemporaryDirectory() as d:
        export(commit, d)
        p = subprocess.run([sys.executable, "-I", "-c", SCAN, d], input=json.dumps(items), capture_output=True,
                           text=True, check=True)
    return [tuple(r) for r in json.loads(p.stdout)]


items = texts()
before, after = scan(BEFORE, items), scan(AFTER, items)
ndocs = sum(1 for n, _ in items if n != "edge")
print(f"# rule rows, {BEFORE} vs {AFTER}: {ndocs} eval docs + {len(EDGE)} edge strings")
print(f"eval-doc rule rows: before {sum(1 for r in before if r[0] != 'edge')}, after {sum(1 for r in after if r[0] != 'edge')}")
print(f"only at {BEFORE} (lost or changed):")
for r in sorted(set(before) - set(after)):
    print("  -", r)
print(f"only at {AFTER} (new or changed):")
for r in sorted(set(after) - set(before)):
    print("  +", r)
print("\n# every edge string: before -> after")
for t in EDGE:
    b = [(k, d) for n, s, k, d, _ in before if n == "edge" and s == t]
    a = [(k, d) for n, s, k, d, _ in after if n == "edge" and s == t]
    mark = "  " if a == b else "* "
    print(f"{mark}{t!r}\n      {BEFORE}: {b}\n      {AFTER}: {a}")

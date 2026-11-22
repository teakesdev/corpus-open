#!/usr/bin/env python3
"""Make a synthetic case for showing the Hermes Desktop panel, then tamper with it on cue.

    python3 scripts/panel_demo.py setup ~/cases/panel-demo   # a new case; point matter_dir at it
    python3 scripts/panel_demo.py tamper ~/cases/panel-demo  # edit one evidence file

`setup` copies demo-matter/ into a new folder (never demo-matter itself), registers its files,
and adds deadlines dated from today so one has always just passed, one is due in two days and
one is weeks out, plus a deadline waiting on an event, one sourced fact and one fact with no
source. `tamper` appends a line to the evidence memo, so the panel's next check reads Tampered.
Everything is synthetic.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import os
import shutil
import sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import cases, deadlines, facts, ingest, store, triggers  # noqa: E402

SOURCE = os.path.join(KIT, "demo-matter")
MEMO = "Evidence/evidence_memo_synthetic.md"
BY = store.attribution("human:panel-demo")
TITLE = "Doe v. Acme Property (synthetic demo)"


def setup(target: str, today: dt.date | None = None) -> str:
    target = os.path.abspath(os.path.expanduser(target))
    if os.path.exists(target):
        raise SystemExit(f"{target} already exists; pick a new folder")
    # Drafts/ belongs to `/matter demo`; here every copied file is registered, and a draft is not a source.
    shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns(".matter", "__pycache__", "Drafts"))
    with open(os.path.join(target, "matter.json"), "w", encoding="utf-8") as f:
        f.write('{"title": "%s"}\n' % TITLE)
    t = today or dt.date.today()
    with contextlib.redirect_stdout(io.StringIO()):
        cases.init(target)
        conn = store.connect(target)
        try:
            ingest.ingest_paths(conn, target, [target])
            deadlines.add(conn, "Initial disclosures (FRCP 26(a)(1))", (t - dt.timedelta(days=6)).isoformat(), BY)
            deadlines.add(conn, "Answer to counterclaim", (t + dt.timedelta(days=2)).isoformat(), BY,
                          rule="FRCP 12(a)(1)(B)", confidence="computed")
            deadlines.add(conn, "Discovery cutoff", (t + dt.timedelta(days=45)).isoformat(), BY)
            triggers.add(conn, "Reply in support of motion", "service of the opposition", 14, BY)
            facts.add(conn, target, "The employee's 2025 review rated performance as exceeding expectations.",
                      "documented-fact", [MEMO + "@para. 1"], by=BY)
            facts.add(conn, target, "The supervisor knew of the complaint before the review.",
                      "allegation", [], by=BY)
        finally:
            conn.close()
    return target


def tamper(target: str) -> str:
    path = os.path.join(os.path.abspath(os.path.expanduser(target)), MEMO)
    with open(path, "a", encoding="utf-8") as f:
        f.write("Added after the fact: rating revised.\n")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=("setup", "tamper"))
    ap.add_argument("folder")
    a = ap.parse_args(argv)
    if a.action == "setup":
        print(f"Made {setup(a.folder)}. Set matter_dir to it in Settings > Plugins > corpus-open.")
    else:
        print(f"Edited {tamper(a.folder)}. The panel's next check reads Tampered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

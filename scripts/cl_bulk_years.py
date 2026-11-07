#!/usr/bin/env python3
"""Per-court opinion counts and years from CourtListener's public bulk files.

CourtListener (Free Law Project) publishes its data as bulk files every quarter, at
https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/bulk-data/ . Its bulk-data page says the files
are "free of known copyright restrictions" and carries the Public Domain Mark 1.0, so the dates and
counts read here can be republished. This script reads two of them:

  opinion-clusters-<snapshot>.csv.bz2   one row per case: date_filed, date_filed_is_approximate, docket_id
  dockets-<snapshot>.csv.bz2            one row per docket: id, court_id

Each file is streamed over HTTPS through curl and bzcat and never saved. No API, no token and no login:
the bucket is public, and nothing here touches CourtListener's API or its membership terms.

For every court it writes how many opinions there are, how many of those have an approximate date, and the
earliest and latest date_filed among the opinions whose date is not approximate:

  {"source": "CourtListener bulk data 2026-09-30 (opinion-clusters + dockets), Public Domain Mark",
   "built_at": "2026-11-07T12:23:44",
   "courts": {"mo": {"count": 34991, "approximate": 15742, "earliest": "1852-07-01", "latest": "2025-08-12"}, ...}}

A court appears only if at least one opinion is filed under it. A few dates in the files are mistyped (an
opinion in Minnesota's supreme court is dated 1665; one in New Hampshire's, year 19), and they are kept as they
are: scripts/build_caselaw_coverage.py raises an earliest year that falls before the court's own start.

Memory: about 1 GB. The clusters file is read first into arrays indexed by docket id (two 32-bit dates and two
16-bit counters per id, around 75 million ids), then the dockets file is read once to give each docket its court.
Time: a few minutes of CPU and about 7.6 GB downloaded (snapshot 2026-09-30). Needs curl and bzcat.

Usage:  python3 scripts/cl_bulk_years.py <snapshot date, e.g. 2026-09-30> <out.json>
Snapshots are quarterly (03-31, 06-30, 09-30, 12-31); the bucket's bulk-data/ listing names them.
"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import time
from array import array

BASE = "https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/bulk-data"
SOURCE = "CourtListener bulk data {snapshot} (opinion-clusters + dockets), Public Domain Mark"
COUNTER_MAX = 65535  # the per-docket counters are unsigned 16-bit
TRUE = ("t", "True", "true")


def stream(name: str):
    """Rows of one bulk file, streamed and never saved, and the processes to wait for."""
    curl = subprocess.Popen(["curl", "-sfL", "--retry", "5", f"{BASE}/{name}"], stdout=subprocess.PIPE)
    bz = subprocess.Popen(["bzcat"], stdin=curl.stdout, stdout=subprocess.PIPE)
    curl.stdout.close()
    reader = csv.reader(io.TextIOWrapper(bz.stdout, encoding="utf-8", errors="replace", newline=""))
    return reader, (curl, bz)


def finish(procs, label: str) -> None:
    for p in procs:
        p.wait()
    codes = [p.returncode for p in procs]
    if any(codes):
        sys.exit(f"{label}: stream failed, return codes {codes}")


def grow(a: array, n: int, fill: int) -> None:
    if n >= len(a):
        a.extend([fill] * (n + 1 - len(a) + 1_000_000))


def format_date(v: int) -> str | None:
    """20260930 -> '2026-09-30'. Padded to eight digits so a year below 1000 keeps its zeros
    (190131 is '0019-01-31', not '1901-31-')."""
    if not v:
        return None
    text = f"{v:08d}"
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def scan_clusters(reader, progress=None):
    """Per docket id: earliest and latest non-approximate date_filed (as YYYYMMDD ints), opinion count and
    approximate-date count. `reader` yields the clusters file's rows, header first."""
    lo, hi, cnt, approx = array("i"), array("i"), array("H"), array("H")
    header = next(reader)
    i_date, i_approx, i_dock = header.index("date_filed"), header.index("date_filed_is_approximate"), header.index("docket_id")
    seen = 0
    for r in reader:
        if len(r) != len(header):
            continue
        try:
            d = int(r[i_dock])
        except ValueError:
            continue
        for a in (lo, hi, cnt, approx):
            grow(a, d, 0)
        if cnt[d] < COUNTER_MAX:
            cnt[d] += 1
        if r[i_approx] in TRUE:
            if approx[d] < COUNTER_MAX:
                approx[d] += 1
            continue
        ds = r[i_date][:10].replace("-", "")
        if len(ds) == 8 and ds.isdigit():
            v = int(ds)
            if lo[d] == 0 or v < lo[d]:
                lo[d] = v
            if v > hi[d]:
                hi[d] = v
        seen += 1
        if progress and seen % 1_000_000 == 0:
            progress(f"clusters {seen:,}")
    return lo, hi, cnt, approx


def join_dockets(reader, lo, hi, cnt, approx, progress=None) -> dict:
    """Give each docket its court: {court_id: {count, approximate, earliest, latest}}. `reader` yields the
    dockets file's rows, header first."""
    courts: dict = {}
    header = next(reader)
    i_id, i_court = header.index("id"), header.index("court_id")
    n = 0
    for r in reader:
        n += 1
        if progress and n % 5_000_000 == 0:
            progress(f"dockets {n:,}")
        if len(r) != len(header):
            continue
        try:
            d = int(r[i_id])
        except ValueError:
            continue
        if d >= len(cnt) or cnt[d] == 0:
            continue
        c = courts.setdefault(r[i_court], {"count": 0, "approximate": 0, "earliest": 0, "latest": 0})
        c["count"] += cnt[d]
        c["approximate"] += approx[d]
        if lo[d] and (c["earliest"] == 0 or lo[d] < c["earliest"]):
            c["earliest"] = lo[d]
        if hi[d] > c["latest"]:
            c["latest"] = hi[d]
    for c in courts.values():
        c["earliest"], c["latest"] = format_date(c["earliest"]), format_date(c["latest"])
    return dict(sorted(courts.items()))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        sys.exit("usage: python3 scripts/cl_bulk_years.py <snapshot date, e.g. 2026-09-30> <out.json>")
    snapshot, out = argv
    started = time.time()

    def say(text: str) -> None:
        print(f"{text} {time.time() - started:.0f}s", flush=True)

    reader, procs = stream(f"opinion-clusters-{snapshot}.csv.bz2")
    arrays = scan_clusters(reader, say)
    finish(procs, "clusters")
    say("clusters done")
    reader, procs = stream(f"dockets-{snapshot}.csv.bz2")
    courts = join_dockets(reader, *arrays, progress=say)
    finish(procs, "dockets")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"source": SOURCE.format(snapshot=snapshot), "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "courts": courts}, f, indent=1)
    say(f"done: {len(courts)} courts")
    return 0


if __name__ == "__main__":
    csv.field_size_limit(sys.maxsize)
    sys.exit(main())

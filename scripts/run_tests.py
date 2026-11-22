#!/usr/bin/env python3
"""Run the whole test suite with one process per test file, several at a time.

Each file runs exactly as `python3 -m unittest discover -s tests -p <file>` would run it, so
nothing is skipped or shared between files; only the wall time changes. The serial command,
`python3 -m unittest discover -s tests`, still works and runs the same tests.

    python3 scripts/run_tests.py            # one worker per CPU
    python3 scripts/run_tests.py -j 2       # two workers
    python3 scripts/run_tests.py -v         # also print each file's own output

Exit status is 0 only when every file passed. Standard library only.
"""
import argparse
import glob
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(KIT, "tests")
RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
SKIPPED = re.compile(r"skipped=(\d+)")


def run_one(path: str, extra: list[str]) -> tuple[str, int, int, int, float, str]:
    name = os.path.basename(path)
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, *extra, "-m", "unittest", "discover", "-s", TESTS, "-p", name],
                       cwd=KIT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ran = RAN.findall(p.stdout)
    tail = p.stdout[p.stdout.rfind("\nRan "):] if "\nRan " in p.stdout else ""
    skipped = SKIPPED.search(tail)
    return (name, p.returncode, int(ran[-1]) if ran else 0, int(skipped.group(1)) if skipped else 0,
            time.monotonic() - t0, p.stdout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 2, help="files run at once")
    ap.add_argument("-v", "--verbose", action="store_true", help="print each file's output")
    ap.add_argument("-W", dest="warnings", action="append", default=[],
                    help="passed to each Python as -W (e.g. -W error::ResourceWarning)")
    args = ap.parse_args()
    extra = [f"-W{w}" for w in args.warnings]
    # The biggest files first, so a long file does not start last and hold up the end of the run.
    files = sorted(glob.glob(os.path.join(TESTS, "test_*.py")), key=os.path.getsize, reverse=True)
    t0 = time.monotonic()
    failed, total, skipped = [], 0, 0
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = [pool.submit(run_one, f, extra) for f in files]
        for fut in as_completed(futures):
            name, code, ran, skip, secs, out = fut.result()
            total += ran
            skipped += skip
            if code != 0 or ran == 0:
                failed.append(name)
                print(f"FAIL {name} ({ran} tests, {secs:.1f} s)\n{out}", flush=True)
            elif args.verbose:
                print(f"ok   {name} ({ran} tests, {secs:.1f} s)\n{out}", flush=True)
    secs = time.monotonic() - t0
    summary = f"Ran {total} tests in {len(files)} files in {secs:.1f} s"
    if skipped:
        summary += f" (skipped={skipped})"
    if failed:
        print(f"{summary}\nFAILED: {', '.join(sorted(failed))}")
        return 1
    print(f"{summary}\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

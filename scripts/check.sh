#!/bin/sh
# The local gate: what CI runs, run here. GitHub Actions is not running for this repository, so
# nothing checks a change before it merges unless someone runs this.
#
#   scripts/check.sh              unit tests, then leak_scan over the tree and over the commits
#                                 this branch adds to its base
#   scripts/check.sh --no-range   skip the commit scan (the pre-push hook scans the exact
#                                 commits being pushed itself)
#   scripts/check.sh --no-tests   leak_scan only
#
# The unit tests run with ResourceWarning as an error, and any ResourceWarning in their output
# fails the gate. A warning raised while an object is being collected (an unclosed sqlite
# connection, file or socket) cannot fail the test that leaked it; Python only prints it. So the
# output is read for it instead. Close what you open: `with`, contextlib.closing, addCleanup.
#
# Environment:
#   PYTHON           interpreter (default: python3)
#   CHECK_JOBS       test files run at once (default: one per CPU)
#   CHECK_BASE       the commit scan covers CHECK_BASE..HEAD (default: origin/main)
#   LEAK_SCAN_PATTERNS, LEAK_SCAN_EXCLUDE, LEAK_SCAN_ALLOW_EMAIL, LEAK_SCAN_CHECK_AUTHORS
#                    passed through to scripts/leak_scan.py, as in CI (see its header)
#
# Exit status: 0 everything passed, 1 something failed. Each step runs even after one fails.

python=${PYTHON:-python3}
base=${CHECK_BASE:-origin/main}
tests=1
range=1
for arg in "$@"; do
    case "$arg" in
        --no-tests) tests=0 ;;
        --no-range) range=0 ;;
        -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
        *) echo "check.sh: unknown option $arg (try --help)" >&2; exit 1 ;;
    esac
done

root=$(cd "$(dirname "$0")/.." && pwd) || exit 1
cd "$root" || exit 1
status=0
fail() { echo "check: FAIL $1" >&2; status=1; }

if [ "$tests" -eq 1 ]; then
    echo "== unit tests ($("$python" --version 2>&1), ResourceWarning is an error)"
    log=$(mktemp) || exit 1
    trap 'rm -f "$log"' EXIT
    # scripts/run_tests.py runs each test file in its own process, several at a time; -v keeps
    # every file's output so it can be read for warnings.
    "$python" scripts/run_tests.py -v ${CHECK_JOBS:+-j "$CHECK_JOBS"} -W error::ResourceWarning >"$log" 2>&1
    code=$?
    if [ "$code" -ne 0 ]; then
        cat "$log"
        fail "unit tests"
    else
        tail -n 2 "$log"
    fi
    leaks=$(grep -c "ResourceWarning" "$log")
    if [ "$leaks" -ne 0 ]; then
        grep -B2 -A2 "ResourceWarning" "$log"
        fail "unit tests printed $leaks ResourceWarning line(s): something was left open"
    fi
fi

echo "== leak_scan: tree"
"$python" scripts/leak_scan.py --tree . --redact || fail "leak_scan --tree"

if [ "$range" -eq 1 ]; then
    if git rev-parse --verify --quiet "$base^{commit}" >/dev/null; then
        echo "== leak_scan: commits $base..HEAD"
        "$python" scripts/leak_scan.py --range "$base..HEAD" --redact ${LEAK_SCAN_CHECK_AUTHORS:+--check-authors} \
            || fail "leak_scan --range $base..HEAD"
    else
        fail "no $base to scan commits against: git fetch origin, or set CHECK_BASE"
    fi
fi

[ "$status" -eq 0 ] && echo "check: all passed"
exit "$status"

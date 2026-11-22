#!/usr/bin/env python3
"""Rebuild matterkit/data/corpus_cities.json from Corpus's law.list_coverage.

    python3 scripts/refresh-corpus-cities.py              # asks corpuslaw.us (one anonymous request)
    python3 scripts/refresh-corpus-cities.py coverage.txt # reads a saved law.list_coverage answer

A maintainer runs this, never Matter Kit: the request carries no case data (law.list_coverage takes no
arguments), and the snapshot it writes is what the city cite check reads, so a case's lookups never
depend on a live list. Placeholder rows (listed, not yet searchable) are left out.
"""
import datetime
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from matterkit import municipal  # noqa: E402

MCP_URL = "https://corpuslaw.us/api/mcp"


def fetch() -> str:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "law.list_coverage", "arguments": {}}}).encode()
    req = urllib.request.Request(MCP_URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "Corpus-Open-matter-kit (city list refresh)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read().decode())["result"]
    if res.get("isError"):
        raise SystemExit("law.list_coverage answered with an error")
    parts = [c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text"]
    return "\n".join(parts) or (res.get("structuredContent") or {}).get("text", "")


def main(argv) -> int:
    text = open(argv[1], encoding="utf-8").read() if len(argv) > 1 else fetch()
    rows = municipal.parse_coverage(text)
    if len(rows) < 50:
        raise SystemExit(f"only {len(rows)} city rows read; refusing to write a short list")
    out = {"source": "Corpus MCP law.list_coverage (corpuslaw.us)",
           "read_at": datetime.date.today().isoformat(),
           "note": "City and county jurisdictions Corpus lists as searchable. Placeholders left out.",
           "cities": sorted(rows, key=lambda r: r["code"])}
    path = municipal.DATA
    cities = ",\n".join("  " + json.dumps(c, ensure_ascii=False) for c in out.pop("cities"))
    head = json.dumps(out, indent=1, ensure_ascii=False)[:-2]
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + ',\n "cities": [\n' + cities + "\n ]\n}\n")
    print(f"wrote {len(rows)} cities to {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

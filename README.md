# Corpus Open

**Matter Kit** — provenance-enforced case organization for people litigating without a lawyer — operable by any AI agent.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![CI](https://github.com/teakesdev/corpus-open/actions/workflows/ci.yml/badge.svg)](https://github.com/teakesdev/corpus-open/actions/workflows/ci.yml)

The public project name is **Corpus Open**. The code, CLI, and skill stay `matter-kit`. Live case files never belong in this repository.

Matter Kit turns a folder of case files into a *matter*: hashed, tamper-evident
source documents plus a structured store where every substantive assertion is
either traceable to a document/page/timecode or visibly flagged `UNSUPPORTED`.
It renders layered counsel packets (30-second / 3-minute / 15-minute diligence
views), tracks deadlines with confidence labels, and exposes everything to
Hermes, Claude Desktop, Codex, or any MCP client through a zero-dependency
stdio MCP server.

Nothing here files, sends, or contacts anyone. Sources are never modified.
This is legal-organization software, not a lawyer and not legal advice.

## Why

AI-drafted court filings without verifiable provenance are generating sanctions
at scale (1,500+ documented cases of fabricated citations by mid-2026), and
self-represented litigants are the fastest-growing segment of that problem.
Meanwhile, lawyers decline meritorious cases from self-represented people mostly
because evaluating them costs hours of unpaid diligence. Matter Kit attacks both:
work product that survives hostile scrutiny, and counsel packets built to be
evaluated in minutes.

## Quick start

```bash
python3 matter.py init ~/cases/my-case
python3 matter.py ingest ~/cases/my-case          # hash + register every file
python3 matter.py verify ~/cases/my-case          # re-hash; non-zero exit on any drift
matter_fact=$(python3 matter.py fact add ~/cases/my-case \
  --text "Property records show plaintiff owns both parcels" \
  --status DOCUMENTED-FACT --source "Evidence/EVIDENCE_INVENTORY.md@Tier 1 #6")
python3 matter.py packet ~/cases/my-case --layer 3min --out COUNSEL_PACKET.md
```

## MCP (Hermes / Claude Desktop / Codex / anything)

```json
{ "mcpServers": { "matter": { "command": "python3",
    "args": ["/path/to/matter-kit/matter-mcp.py"],
    "env": { "MATTER_DIR": "/absolute/path/to/case-dir" } } } }
```

Tools: `matter.status` · `matter.fact_add` · `matter.fact_list` ·
`matter.deadline_list` · `matter.packet_render` · `matter.cite_extract`.

## Citations and stages (v0.2, RFC 0001)

```bash
python3 matter.py cite extract ~/cases/my-case Evidence/complaint.txt  # local parse; offsets keyed to the parsed text's sha256
python3 matter.py cite list ~/cases/my-case --status extraction-failed # unparsed fragments, first-class, never dropped
python3 matter.py stage ~/cases/my-case auth_xxx source-checked \
  --via "courtlistener:id=123" --evidence "DL #123 text matches; still good law as of 2026-09-08"
```

Stages: `unverified` → `resolved` → (`research-pending`) → `source-checked` /
`stale-flagged`. Transitions are validated — a check status without a resolved
source, or evidence-free "checked," is refused. These tokens mean *a defined
source was checked*, never *this is correct law*.

Adapters (network search) ship in later releases and require an explicit
grant in `.matter/consent.json` — issued by a human (`kind: "human"`),
naming its authorized callers; agent-authored grants are inert proposals.
R1 ships zero adapters and the kit makes no network calls.
See `rfcs/0001-provider-neutral-search-adapters.md`.

## Skills

`skills/matter-kit/SKILL.md` teaches agents the operating discipline: the five
epistemic labels, media-before-strategy rule, citation-verification habit,
date-discrepancy preservation, and the folder taxonomy. Copy or symlink it into
your agent's skill directory:

```bash
ln -s /path/to/matter-kit/skills/matter-kit ~/.hermes/skills/matter-kit
```

## Design rules

1. **Plain files + SQLite.** No server, no lock-in; any agent can read the store.
2. **Sources immutable.** Everything derivable lives in `.matter/`; originals never change.
3. **Labels are enforced, not suggested.** Unsupported assertions render `[UNSUPPORTED]`.
4. **Read-mostly MCP.** Agents register facts and render packets; humans act.
5. **Adverse facts are first-class.** A packet that hides them is worthless and dishonest.

## Status

v0.1 — dogfooding stage. Schema may change; hashing is stable.

License: Apache-2.0. Not affiliated with any bar association; nothing here is
legal advice or a substitute for a licensed attorney.

Built while running [Corpus](https://corpuslaw.us). The kit is free and local;
optional `law.*` search against Corpus is the only paid hook, never required
to organize a matter.

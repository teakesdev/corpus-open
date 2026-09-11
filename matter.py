#!/usr/bin/env python3
"""matter.py — CLI for the matter-kit case store.

Usage:
  matter.py init <case-dir>
  matter.py ingest <case-dir> [paths...] [--exclude SUBSTR]...
  matter.py verify <case-dir>
  matter.py status <case-dir>
  matter.py fact add <dir> --text T --status LABEL [--source rel@loc]...
  matter.py fact list <dir> [--status LABEL]
  matter.py deadline add <dir> --label L --due YYYY-MM-DD [--rule R] [--confidence hard|working-estimate]
                              [--verification verified-official|secondary-source|unverified]
  matter.py deadline list <dir> [--all]
  matter.py authority add <dir> "<citation>" [--via HOW]
  matter.py packet <dir> [--layer 30s|3min|15min] [--out FILE]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import ingest as ing, packet as pk, store  # noqa: E402

FOLDERS = ["Filings", "Evidence", "Correspondence", "Case_Tracking", "Research", "Media"]


def cmd_init(args):
    d = args.case_dir
    os.makedirs(d, exist_ok=True)
    for f in FOLDERS:
        os.makedirs(os.path.join(d, f), exist_ok=True)
    conn = store.connect(d)
    n = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
    print(f"initialized {d} (.matter/matter.db, {n} documents registered)")


def cmd_ingest(args):
    conn = store.connect(args.case_dir)
    paths = args.paths or [args.case_dir]
    added, skipped, excluded = ing.ingest_paths(conn, args.case_dir, paths, exclude=args.exclude)
    print(f"ingested: +{added} new, {skipped} already registered, {excluded} excluded")


def cmd_verify(args):
    conn = store.connect(args.case_dir)
    ok, bad, missing, failures = ing.verify_integrity(conn)
    print(f"integrity: {ok} ok · {bad} changed · {missing} missing")
    for kind, path in failures:
        print(f"  ⚠ {kind}: {path}")
    if bad or missing:
        sys.exit(1)


def cmd_status(args):
    conn = store.connect(args.case_dir)
    print(pk.status_report(conn, args.case_dir))


def cmd_fact(args):
    conn = store.connect(args.case_dir)
    if args.sub == "add":
        from matterkit.store import new_id, now_iso
        status = args.status.replace("-", " ").upper()
        if status not in store.VALID_STATUS:
            sys.exit(f"invalid status '{args.status}' — use one of: "
                     f"{', '.join(sorted(s.lower().replace(' ', '-')) for s in store.VALID_STATUS)}")
        aid = new_id("a_")
        conn.execute("INSERT INTO assertions (id,text,status,created_by,created_at) VALUES (?,?,?,?,?)",
                     (aid, args.text, status, "human", now_iso()))
        unrec = []
        for s in args.source or []:
            ref, _, loc = s.partition("@")
            row = conn.execute("SELECT id FROM documents WHERE rel_path=? OR id=?", (ref, ref)).fetchone()
            if not row:
                unrec.append(ref)
                continue
            conn.execute("INSERT OR IGNORE INTO assertion_sources VALUES (?,?,?)", (aid, row["id"], loc or None))
        conn.commit()
        n = conn.execute("SELECT COUNT(*) c FROM assertion_sources WHERE assertion_id=?", (aid,)).fetchone()["c"]
        warn = "" if n else "\n⚠ UNSUPPORTED — no recognized sources attached."
        bad = "" if not unrec else f"\n⚠ unrecognized source refs: {unrec}"
        print(f"{aid} [{status}]{warn}{bad}")
    else:
        st = args.status.upper().replace("-", " ") if args.status else None
        q = "SELECT * FROM assertions" + (" WHERE status=?" if st else "") + " ORDER BY status, created_at"
        for r in conn.execute(q, (st,) if st else ()):
            srcs = conn.execute(
                "SELECT d.rel_path, s.locator FROM assertion_sources s JOIN documents d ON d.id=s.document_id"
                " WHERE s.assertion_id=?", (r["id"],)).fetchall()
            s = "; ".join(x["rel_path"] + (f"@{x['locator']}" if x["locator"] else "") for x in srcs) or "**[UNSUPPORTED]**"
            print(f"[{r['status']:15}] {r['text']}\n                ↳ {s}")


def cmd_deadline(args):
    conn = store.connect(args.case_dir)
    if args.sub == "add":
        from matterkit.store import new_id
        conn.execute("INSERT INTO deadlines (id,label,due_date,rule_source,confidence,verification_status)"
                     " VALUES (?,?,?,?,?,?)",
                     (new_id("d_"), args.label, args.due, args.rule,
                      args.confidence, args.verification))
        conn.commit()
        print(f"deadline registered: {args.due} — {args.label}")
    else:
        q = "SELECT * FROM deadlines" + ("" if args.all else " WHERE state='open'") + " ORDER BY due_date"
        for r in conn.execute(q):
            flag = "" if r["verification_status"] == "verified-official" else f" ({r['confidence']}/{r['verification_status']})"
            print(f"{r['due_date']}  {r['label']}{flag}  [{r.get('rule_source') or '—'}]")


def cmd_authority(args):
    conn = store.connect(args.case_dir)
    from matterkit.store import new_id
    conn.execute("INSERT INTO authorities (id,citation,status,checked_via,note) VALUES (?,?,?,?,?)",
                 (new_id("auth_"), args.citation, "unverified", args.via, None))
    conn.commit()
    print(f"authority logged (unverified): {args.citation} — run verification via corpuslaw.us/CourtListener and update status")


def cmd_packet(args):
    conn = store.connect(args.case_dir)
    text = pk.packet(conn, args.case_dir, layer=args.layer)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as f:
            f.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)


def cmd_cite(args):
    """cite extract|list — local citation extraction (RFC 0001 §2.2/§2.3).

    Offsets are meaningful against the exact decoded text parsed; its sha256
    is stored per row (`text_sha256`). The document (container) sha256 is
    recorded separately and never used to define offsets.
    """
    conn = store.connect(args.case_dir)
    from matterkit import citations
    if args.sub == "extract":
        raw = open(args.path, "rb").read()
        text = raw.decode("utf-8", errors="replace")
        import hashlib
        doc_sha = hashlib.sha256(raw).hexdigest()
        rows = citations.extract_citations(text, document_sha256=doc_sha)
        n = citations.record_extractions(conn, rows)
        ok = sum(1 for r in rows if r["status"] == "extracted")
        print(f"extracted {ok}/{n} citation(s) from {args.path} "
              f"(text sha256 {rows[0]['text_sha256'][:12]}…)" if rows
              else f"0 citations found in {args.path}")
        for r in rows:
            mark = "✓" if r["status"] == "extracted" else "✗ unparsed"
            print(f"  {mark} [{r['char_start']}:{r['char_end']}] {r['raw_fragment']!r}")
    else:
        q = "SELECT * FROM citation_extractions ORDER BY created_at, char_start"
        if args.status:
            q += f" WHERE status='{args.status}'"
        for r in conn.execute(q):
            print(f"[{r['status']:17}] {r['raw_fragment']!r} "
                  f"@ {r['char_start']}:{r['char_end']} "
                  f"(text {r['text_sha256'][:12]}, parser {r['parser']})")


def cmd_pages(args):
    """pages <dir> [path] — extract document text with page locators (RFC 0004).

    Stdlib-only. Scanned/LZW/CID-font PDFs fail honestly per page; nothing is
    ever silently empty. Results write to document_pages keyed by sha256+page.
    """
    conn = store.connect(args.case_dir)
    from matterkit import extract, ingest
    import hashlib
    if args.path:
        files = [(args.path, ingest.sha256_file(args.path))]
    else:
        docs = conn.execute("SELECT id, sha256, rel_path FROM documents").fetchall()
        files = []
        for d in docs:
            # rel_path is relative to the cwd at ingest time; the id is a hash,
            # not a path. Resolve rel_path against cwd first, then fall back to
            # the matter dir (covers both the demo layout and older ingests).
            full = os.path.abspath(d["rel_path"])
            if not os.path.isfile(full):
                full = os.path.join(args.case_dir, d["rel_path"])
            if os.path.isfile(full):
                files.append((full, d["sha256"]))
    total_ok = total_fail = 0
    for path, sha in files:
        doc = extract.extract_text(path)
        for pg in doc.pages:
            conn.execute(
                "INSERT OR REPLACE INTO document_pages "
                "(document_sha256, page_number, text_sha256, text, status) "
                "VALUES (?,?,?,?,?)",
                (sha, pg.number, pg.text_sha256, pg.text, pg.status))
            if pg.status == "extracted":
                total_ok += 1
            else:
                total_fail += 1
        n = len(doc.pages)
        mark = "✓" if doc.pages and doc.pages[0].status == "extracted" else "✗"
        print(f"{mark} {os.path.basename(path)}: {n} page(s) "
              f"({total_ok} ok / {total_fail} failed so far)")
        for pg in doc.pages:
            if pg.status == "extraction-failed":
                print(f"   ⚠ page {pg.number}: {pg.reason}")
    conn.commit()
    print(f"pages: {total_ok} extracted · {total_fail} failed (never silently empty)")


def cmd_auth_stage(args):
    """Stage-validated authority status change (RFC 0001 §2.2c)."""
    conn = store.connect(args.case_dir)
    from matterkit import citations
    try:
        citations.transition_authority(
            conn, args.authority_id, args.status,
            checked_via=args.via, check_evidence=args.evidence)
    except citations.StageOrderError as e:
        sys.exit(f"refused: {e}")
    row = conn.execute("SELECT status, checked_via, check_evidence FROM authorities WHERE id=?",
                       (args.authority_id,)).fetchone()
    print(f"authority {args.authority_id} → [{row['status']}] "
          f"via={row['checked_via']!r} evidence={'recorded' if row['check_evidence'] else '—'}")


def cmd_consent(args):
    from matterkit import consent
    from matterkit.adapters.courtlistener import ENDPOINT_HOST
    provider = "courtlistener-free"
    endpoints = [ENDPOINT_HOST]
    payloads = ["query_text"]
    if args.sub == "propose":
        g = consent.stage_proposal(
            args.case_dir, provider, endpoints, payloads, args.as_user)
        print(f"staged INERT proposal for {provider} by {g['created_by']!r} "
              "(authorizes no one until human TTY activate)")
    elif args.sub == "activate":
        try:
            g = consent.activate_human_grant(
                args.case_dir, provider, endpoints, payloads, args.issuer,
                authorized_callers=args.caller or [])
        except consent.ConsentError as e:
            sys.exit(f"refused: {e}")
        print(f"activated human grant issuer={g['created_by']!r} "
              f"callers={g['authorized_callers']}")


def cmd_search(args):
    from matterkit import search as S
    from matterkit.consent import ConsentError
    try:
        rows = S.manual_search(
            args.case_dir, args.caller, args.query, limit=args.limit,
            auth_mode=args.auth)
    except (LookupError, ConsentError, RuntimeError, ValueError) as e:
        sys.exit(f"refused: {e}")
    print(f"{len(rows)} CourtListener hit(s) for {args.query!r} "
          "(lookup only — not source-checked, not good law)")
    for r in rows:
        print(f"  {r.citation}  {r.name}")
        print(f"    {r.provenance.get('url')}")
        if r.snippet:
            print(f"    {r.snippet[:160]}")
    if args.record_resolved and rows:
        conn = store.connect(args.case_dir)
        from matterkit.store import new_id
        from matterkit.citations import now_iso
        for r in rows:
            aid = new_id("auth_")
            via = f"{r.provenance.get('provider')}:{r.authority_id}"
            note = json.dumps({"url": r.provenance.get("url"),
                               "retrieved_at": r.provenance.get("retrieved_at")})
            conn.execute(
                "INSERT INTO authorities (id,citation,status,checked_via,note) "
                "VALUES (?,?,?,?,?)",
                (aid, r.citation or r.name, "resolved", via, note))
        conn.commit()
        print(f"recorded {len(rows)} authorities as resolved (not source-checked)")


def cmd_outreach(args):
    from matterkit import outreach as ou
    conn = store.connect(args.case_dir)
    if args.sub == "seed-demo":
        ids = ou.seed_synthetic(conn)
        print(f"seeded {len(ids)} SYNTHETIC recipients (example.org — not real attorneys)")
        return
    if args.sub == "draft":
        posture = {
            "court": args.court or "Example District Court",
            "role": args.role or "a self-represented plaintiff",
            "case_type": args.case_type or "a civil matter",
            "issue_general": args.issue or "a civil dispute",
            "deadline": args.deadline or "unknown",
            "deadline_label": args.deadline_label or "next known date",
            "fee": args.fee or "limited-scope / consult; to be discussed",
            "representation": args.representation or "a consultation or limited-scope representation",
            "jurisdiction": args.jurisdiction or "this court",
        }
        try:
            ids = ou.draft_all(conn, posture)
        except ValueError as e:
            sys.exit(str(e))
        print(f"drafted {len(ids)} inquiries (no attachments, no Corpus marketing)")
        return
    if args.sub == "manifest":
        man = ou.build_manifest(conn)
        path = ou.write_manifest(args.case_dir, man)
        print(f"approval manifest: {path}")
        print(f"sha256: {man['sha256']}")
        print(f"recipients: {man['n']}")
        for it in man["items"]:
            print(f"  - {it['recipient_id']}  {it['name']} <{it['intake_url']}>")
            print(f"    destination_type: {it.get('destination_type') or 'unknown'}")
            if it.get("preparation_only"):
                print("    PREPARATION ONLY — not permission to submit a form or portal")
            print(f"    why: {it['match_reason']}")
            print(f"    source: {it['source_url']}")
            print(f"    subject: {it['subject']}")
        return
    if args.sub == "send":
        if not args.sha:
            sys.exit("pass --sha <manifest sha256> after inspecting approval.json")
        bid = ou.approve_and_simulate(
            conn, args.case_dir, issuer=args.issuer, expected_sha=args.sha,
            transport=args.transport)
        print(f"batch {bid} transport={args.transport} (not delivered unless later transport exists)")
        return
    if args.sub == "respond":
        if not args.recipient or not args.state:
            sys.exit("respond needs --recipient ID --state declined|consultation|conflict_check|follow_up|opted_out")
        ou.set_response(conn, args.recipient, args.state, args.note)
        print(f"{args.recipient} → {args.state}")
        return
    if args.sub == "ledger":
        print(ou.ledger(conn))
        return
    if args.sub == "import":
        from matterkit import discovery as disc
        path = args.brief or disc.load_bundled_brief()
        result = disc.import_brief(conn, path)
        print(f"imported={len(result['imported'])} reused={len(result['reused'])} "
              f"rejected={len(result['rejected'])} flagged={len(result['flagged'])}")
        for r in result["rejected"]:
            print(f"  rejected {r['name']}: {r['error']}")
        print(disc.candidate_report(conn))
        return
    if args.sub == "shortlist":
        from matterkit import discovery as disc
        if not args.recipient:
            sys.exit("shortlist needs --recipient ID")
        try:
            disc.shortlist(conn, args.recipient)
        except ValueError as e:
            sys.exit(str(e))
        print(f"{args.recipient} → shortlisted (still unsendable until an approved batch)")
        return
    if args.sub == "candidates":
        from matterkit import discovery as disc
        print(disc.candidate_report(conn))
        return


def main():
    ap = argparse.ArgumentParser(prog="matter")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("case_dir"); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("ingest"); p.add_argument("case_dir"); p.add_argument("paths", nargs="*")
    p.add_argument("--exclude", action="append", default=[]); p.set_defaults(fn=cmd_ingest)
    p = sub.add_parser("verify"); p.add_argument("case_dir"); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("status"); p.add_argument("case_dir"); p.set_defaults(fn=cmd_status)

    p = sub.add_parser("fact"); p.add_argument("sub", choices=["add", "list"]); p.add_argument("case_dir")
    p.add_argument("--text"); p.add_argument("--status"); p.add_argument("--source", action="append", default=[])
    p.set_defaults(fn=cmd_fact)

    p = sub.add_parser("deadline"); p.add_argument("sub", choices=["add", "list"]); p.add_argument("case_dir")
    p.add_argument("--label"); p.add_argument("--due"); p.add_argument("--rule")
    p.add_argument("--confidence", default="working-estimate",
                   choices=["hard", "working-estimate"])
    p.add_argument("--verification", default="unverified",
                   choices=["verified-official", "secondary-source", "unverified"])
    p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_deadline)

    p = sub.add_parser("authority"); p.add_argument("case_dir"); p.add_argument("citation")
    p.add_argument("--via", default=None); p.set_defaults(fn=cmd_authority)

    p = sub.add_parser("cite"); p.add_argument("sub", choices=["extract", "list"]); p.add_argument("case_dir")
    p.add_argument("path", nargs="?")
    p.add_argument("--status", choices=["extracted", "extraction-failed"])
    p.set_defaults(fn=cmd_cite)

    p = sub.add_parser("pages"); p.add_argument("case_dir"); p.add_argument("path", nargs="?")
    p.set_defaults(fn=cmd_pages)

    p = sub.add_parser("stage"); p.add_argument("case_dir"); p.add_argument("authority_id"); p.add_argument("status")
    p.add_argument("--via", default=None); p.add_argument("--evidence", default=None)
    p.set_defaults(fn=cmd_auth_stage)

    p = sub.add_parser("packet"); p.add_argument("case_dir")
    p.add_argument("--layer", default="3min", choices=["30s", "3min", "15min"])
    p.add_argument("--out"); p.set_defaults(fn=cmd_packet)

    p = sub.add_parser("consent"); p.add_argument("sub", choices=["propose", "activate"])
    p.add_argument("case_dir")
    p.add_argument("--as-user", dest="as_user", default="agent")
    p.add_argument("--issuer", default=os.environ.get("USER", "human"))
    p.add_argument("--caller", action="append", default=[])
    p.set_defaults(fn=cmd_consent)

    p = sub.add_parser("search"); p.add_argument("case_dir")
    p.add_argument("query")
    p.add_argument("--caller", default=os.environ.get("USER", "human"))
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--auth", choices=["anonymous", "authenticated"], default="anonymous")
    p.add_argument("--record-resolved", action="store_true")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("outreach")
    p.add_argument("sub", choices=["seed-demo", "draft", "manifest", "send", "respond", "ledger",
                                   "import", "shortlist", "candidates"])
    p.add_argument("case_dir")
    p.add_argument("--sha")
    p.add_argument("--issuer", default=os.environ.get("USER", "human"))
    p.add_argument("--transport", default="nosend")
    p.add_argument("--recipient")
    p.add_argument("--state")
    p.add_argument("--note", default="")
    p.add_argument("--brief")
    p.add_argument("--court")
    p.add_argument("--role")
    p.add_argument("--case-type", dest="case_type")
    p.add_argument("--issue")
    p.add_argument("--deadline")
    p.add_argument("--deadline-label", dest="deadline_label")
    p.add_argument("--fee")
    p.add_argument("--representation")
    p.add_argument("--jurisdiction")
    p.set_defaults(fn=cmd_outreach)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

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
        with open(args.out, "w") as f:
            f.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)


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

    p = sub.add_parser("packet"); p.add_argument("case_dir")
    p.add_argument("--layer", default="3min", choices=["30s", "3min", "15min"])
    p.add_argument("--out"); p.set_defaults(fn=cmd_packet)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

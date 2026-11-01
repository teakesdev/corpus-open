#!/usr/bin/env python3
"""matter.py — CLI for the matter-kit case store.

Usage:
  matter.py init <case-dir>
  matter.py ingest <case-dir> [paths...] [--exclude SUBSTR]...
  matter.py verify <case-dir>
  matter.py capture <case-dir> --url URL --out Media/captures/YYYY-MM-DD_host/page.html [--kind media|research]
  matter.py capture list <case-dir>
  matter.py status <case-dir>
  matter.py fact add <dir> --text T --status LABEL [--source rel@loc]...
                           [--author agent:MODEL [--directed-by PERSON]]
                           # in a subcase, a source may be parent:<rel_path>@loc (hash-checked, no copy)
  matter.py fact list <dir> [--status LABEL]
  matter.py amend add <dir> --text T [--para "¶¶ 37, 172"] [--reason R] [--target complaint]
                            [--fact a_ID]... [--source rel@loc]...
                            [--author agent:MODEL [--directed-by PERSON]]
                            # queue a change for the next version of a pleading
  matter.py amend list <dir> [--status open|all|proposed|approved|incorporated|dropped] [--target T]
  matter.py amend set <dir> <am_id> --status proposed|approved|incorporated|dropped [--note N]
                            [--author agent:MODEL [--directed-by PERSON]]   # appends a history row
  matter.py deadline add <dir> --label L --due YYYY-MM-DD [--rule R] [--confidence hard|working-estimate]
                              [--verification verified-official|secondary-source|unverified]
                              [--owner ours|theirs|court]   # who must act by the date
                              [--author agent:MODEL [--directed-by PERSON]]
  matter.py deadline list <dir> [--all] [--owner ours|theirs|court]   # open dates in the past are marked OVERDUE
  matter.py deadline update <dir> <deadline_id> --reason TEXT
                              [--state open|met|superseded|lapsed|vacated] [--due YYYY-MM-DD]
                              [--owner ours|theirs|court]
                              [--verification verified-official|secondary-source|unverified]
                              [--confidence hard|working-estimate] [--source rel@loc]...
                              [--author agent:MODEL [--directed-by PERSON]]   # appends an events row
  matter.py trigger add <dir> --label L --waits-on TEXT --days N [--rule R] [--owner ours|theirs|court]
                              [--author agent:MODEL [--directed-by PERSON]]   # a deadline with no date yet
  matter.py trigger fire <dir> <trigger_id> --on YYYY-MM-DD
                              --service mail|clerk|consented-other|email|ecf|hand|none
                              --source rel@loc... --reason TEXT [--dry-run] [--author ...]
                              # counts the days (Fed. R. Civ. P. 6(a), 6(d)) into a working-estimate deadline
  matter.py trigger list <dir> [--all] [--owner ours|theirs|court]
  matter.py trigger withdraw <dir> <trigger_id> --reason TEXT [--author ...]
  matter.py holiday add <dir> --on YYYY-MM-DD --name NAME --kind state|declared|closure --source rel@loc...
  matter.py holiday year <dir> --year YYYY --jurisdiction NAME --source rel@loc...
                              # every state holiday of that year is recorded; a count refuses other years
  matter.py holiday list <dir> --year YYYY
  matter.py subcase init <parent_dir> <slug> --title TITLE [--author agent:MODEL [--directed-by PERSON]]
                              # own matter at <parent>/Subcases/<slug>/; ingest/verify treat it as a boundary
  matter.py authority add <dir> "<citation>" [--via HOW]
  matter.py authority check <dir> <auth_id|"<citation>"> --source-url U [--artifact PATH] [--pin P]...
                            [--quote Q] [--note N] [--stale REASON] [--author agent:MODEL [--directed-by PERSON]]
  matter.py authority import <dir> <ledger.md> [--apply] [--author ...]
  matter.py packet <dir> [--layer 30s|3min|15min] [--out FILE]
  matter.py party add <dir> --name NAME --role agency --capacity custodian
  matter.py correspondence add|list|render|manifest|export|status <dir> ...
  matter.py brief check <dir> <draft.md> [--mode brief|complaint] [--out REPORT.md] [--docket MAP.json] [--no-pdftotext]
  matter.py cite lookup <dir> <draft.md> [--dry-run] [--record] [--out REPORT.md] [--max-requests N]
                        [--caller ID] [--author agent:MODEL [--directed-by PERSON]]
                        # sends the draft's case cites, nothing else, to CourtListener; needs a
                        # human grant (consent activate --scope cite-lookup) and COURTLISTENER_TOKEN
  matter.py consent propose|activate <dir> [--scope search|cite-lookup] [--caller ID]...
  matter.py docket suggest <dir>
  matter.py rules build <source.txt|.pdf> <out_dir> --id ID --version V --title T --cite-prefix P
                        [--current-as-of YYYY-MM-DD] [--start-at LINE] [--stop-at LINE] [--court C]
                        [--source-url U] [--anchor-prefix A] [--heading-regex RE] [--review-after-days N]
  matter.py rules attach <dir> <pack_dir> [--replace] [--author agent:MODEL [--directed-by PERSON]]
  matter.py rules list|stale <dir> [--today YYYY-MM-DD]
  matter.py rules checked <dir> <pack_id> --on DATE --found none|amended --source REL[@LOC] [--source …]
                         [--next-review DATE]   # record a check of the court's website
  matter.py rules show <dir> "<cite>|<anchor>" [--pack ID]   # exit 2: a bare "Rule 15" lists candidates
  matter.py ui <workspace> [--operator NAME] [--port N] [--no-open]
                              # browser UI on 127.0.0.1 only; <workspace> holds case folders (or is one)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import amendments, cases, ingest as ing, packet as pk, store  # noqa: E402

FOLDERS = cases.FOLDERS  # cmd_subcase hands these to subcase.init


def cmd_init(args):
    d = args.case_dir
    n = cases.init(d)
    print(f"initialized {d} (.matter/matter.db, {n} documents registered)")


def cmd_ingest(args):
    conn = store.connect(args.case_dir)
    paths = args.paths or [args.case_dir]
    added, skipped, excluded = ing.ingest_paths(conn, args.case_dir, paths, exclude=args.exclude)
    print(f"ingested: +{added} new, {skipped} already registered, {excluded} excluded")


def cmd_verify(args):
    abs_matter = os.path.abspath(args.case_dir)
    print(f"root={abs_matter}")
    if not os.path.isdir(abs_matter):
        print(f"error: case_dir is not a directory: {args.case_dir}", file=sys.stderr)
        sys.exit(1)
    conn = store.connect(args.case_dir)
    ok, bad, missing, failures = ing.verify_integrity(conn, args.case_dir)
    print(f"integrity: {ok} ok · {bad} changed · {missing} missing")
    for kind, path in failures:
        print(f"  ⚠ {kind}: {path}")
    if bad or missing:
        sys.exit(1)


def cmd_status(args):
    conn = store.connect(args.case_dir)
    print(pk.status_report(conn, args.case_dir))


def cmd_fact(args):
    from matterkit import facts
    conn = store.connect(args.case_dir)
    if args.sub == "add":
        from matterkit import subcase
        try:
            facts.normalize_status(args.status)
        except facts.FactError as e:
            sys.exit(str(e))
        try:
            by = store.attribution(args.author, args.directed_by)
        except ValueError as e:
            sys.exit(f"invalid attribution: {e}")
        try:  # parent: refs are re-hashed against the parent before anything is written
            r = facts.add(conn, args.case_dir, args.text, args.status, args.source, by)
        except (facts.FactError, subcase.SubcaseError) as e:
            sys.exit(f"refused: {e}")
        warn = "\n⚠ UNSUPPORTED — no recognized sources attached." if r["unsupported"] else ""
        bad = f"\n⚠ unrecognized source refs: {r['unrecognized']}" if r["unrecognized"] else ""
        print(f"{r['id']} [{r['status']}] by {r['created_by']}{warn}{bad}"
              + "".join(f"\n  ↳ {note}" for note in r["notes"]))
    else:
        st = args.status.upper().replace("-", " ") if args.status else None
        for r in facts.list_(conn, st):
            s = "; ".join(x["rel_path"] + (f"@{x['locator']}" if x["locator"] else "")
                          for x in r["sources"]) or "**[UNSUPPORTED]**"
            print(f"[{r['status']:15}] {r['text']}\n                ↳ {s}"
                  f"\n                by {r['created_by'] or 'unrecorded'}")


def _amend_line(item):
    import textwrap
    head = f"{item['id']} [{item['status_label']}]"
    if item["paragraphs"]:
        head += f" {item['paragraphs']}"
    if item["target"] != amendments.DEFAULT_TARGET:
        head += f" (for: {item['target']})"
    lines = [head, textwrap.indent(item["text"], "  ")]
    if item["reason"]:
        lines.append(textwrap.indent("why: " + item["reason"], "  "))
    for f in item["links"]["facts"]:
        lines.append(f"  fact {f['id']} [{f['label']}]: {f['text']}")
    for d in item["links"]["documents"]:
        lines.append(f"  source: {d['rel_path']}" + (f"@{d['locator']}" if d["locator"] else ""))
    lines.append(f"  added by {item['created_by'] or 'unrecorded'}, {item['created_at']}")
    if len(item["history"]) > 1:
        last = item["history"][-1]
        note = f" — {last['note']}" if last["note"] else ""
        lines.append(f"  last change: {amendments.LABELS.get(last['to_status'], last['to_status'])} by "
                     f"{last['recorded_by']}, {last['created_at']}{note}")
    return "\n".join(lines)


def cmd_amend(args):
    if args.sub == "list":  # a read: never creates a store or its tables
        try:
            conn = store.connect_readonly(args.case_dir)
        except FileNotFoundError as e:
            sys.exit(str(e))
        try:
            items = amendments.list_(conn, args.status, args.target)
        except amendments.AmendmentError as e:
            sys.exit(f"refused: {e}")
        finally:
            conn.close()
        print("\n\n".join(_amend_line(i) for i in items) if items else "No changes queued.")
        return
    conn = store.connect(args.case_dir)
    try:
        by = store.attribution(args.author, args.directed_by)
    except ValueError as e:
        sys.exit(f"invalid attribution: {e}")
    from matterkit import subcase
    try:
        if args.sub == "add":
            documents = [amendments.resolve_source(conn, ref) for ref in args.source]
            r = amendments.add(conn, args.text, args.para, args.reason, args.target, args.fact, documents, by,
                               matter_dir=args.case_dir)  # re-hashes a subcase's parent: references
            print(f"{r['id']} [{r['status_label']}] by {r['created_by']}")
        else:
            r = amendments.set_status(conn, args.item_id, args.status, args.note, by)
            print(f"{r['id']}: {amendments.LABELS[r['from_status']]} -> {r['status_label']} by {r['recorded_by']}")
    except (amendments.AmendmentError, subcase.SubcaseError) as e:
        sys.exit(f"refused: {e}")


def cmd_deadline(args):
    from matterkit import deadlines as dl
    conn = store.connect(args.case_dir)
    if args.sub == "list":
        try:
            rows = dl.select(conn, include_closed=args.all, owner=args.owner)
        except dl.DeadlineError as e:
            sys.exit(f"refused: {e}")
        for r in rows:
            print(dl.list_line(r, show_state=args.all))
        from matterkit import triggers
        waiting = triggers.select(conn, owner=args.owner)
        if waiting:
            print("waiting on (no date until the event happens; matter.py trigger fire):")
            for r in waiting:
                print("  " + triggers.list_line(r))
        return
    try:
        by = store.attribution(args.author, args.directed_by)
    except ValueError as e:
        sys.exit(f"invalid attribution: {e}")
    try:
        if args.sub == "add":
            if not args.label or not args.due:
                sys.exit("deadline add requires --label and --due")
            rule, note = args.rule, None
            if rule:  # a rule found in an attached rules pack is stored with its pack, version and anchor
                from matterkit import rules
                try:
                    rule, note = rules.rule_source(args.case_dir, rule)
                except rules.RulesError as e:
                    sys.exit(f"refused: {e}")
            dl.add(conn, args.label, args.due, by, rule=rule,
                   confidence=args.confidence or "working-estimate",
                   verification=args.verification or "unverified", owner=args.owner)
            print(f"deadline registered: {args.due} — {args.label}" + (f"\n  ↳ {note}" if note else ""))
        else:
            if not args.deadline_id:
                sys.exit("deadline update requires <deadline_id>")
            dl.update(conn, args.case_dir, args.deadline_id, args.reason, by, state=args.state,
                      verification=args.verification, confidence=args.confidence, due=args.due,
                      sources=args.source, owner=args.owner)
            print(f"deadline {args.deadline_id} updated by {by}; events row recorded")
    except dl.DeadlineError as e:
        sys.exit(f"refused: {e}")


def cmd_trigger(args):
    from matterkit import triggers
    conn = store.connect(args.case_dir)
    if args.sub == "list":
        for r in triggers.select(conn, include_closed=args.all, owner=args.owner):
            print(triggers.list_line(r))
        return
    try:
        by = store.attribution(args.author, args.directed_by)
    except ValueError as e:
        sys.exit(f"invalid attribution: {e}")
    try:
        if args.sub == "add":
            rule, note = args.rule, None
            if rule:  # stored with its pack, version and anchor when an attached pack has it, as deadline add does
                from matterkit import rules
                try:
                    rule, note = rules.rule_source(args.case_dir, rule)
                except rules.RulesError as e:
                    sys.exit(f"refused: {e}")
            tid = triggers.add(conn, args.label, args.waits_on, args.days, by, rule=rule, owner=args.owner)
            print(f"trigger {tid} waiting: {args.label} — {args.days} days after {args.waits_on}"
                  + (f"\n  ↳ {note}" if note else ""))
            return
        if not args.trigger_id:
            sys.exit(f"trigger {args.sub} requires <trigger_id>")
        if args.sub == "withdraw":
            print(triggers.withdraw(conn, args.trigger_id, args.reason, by))
            return
        did, due, steps = triggers.fire(conn, args.case_dir, args.trigger_id, args.on, args.service, args.source,
                                        args.reason, by, dry_run=args.dry_run)
        for step in steps:
            print(f"  {step}")
        if did is None:
            print(f"dry run: the last day would be {due.isoformat()}; nothing was written")
        else:
            print(f"deadline {did} registered: {due.isoformat()} (working-estimate/unverified: computed, not "
                  "checked; check the count, then deadline update --verification)")
    except triggers.TriggerError as e:
        sys.exit(f"refused: {e}")


def cmd_holiday(args):
    from matterkit import triggers
    conn = store.connect(args.case_dir)
    try:
        if args.sub == "list":
            if args.year is None:
                sys.exit("holiday list requires --year")
            print("\n".join(triggers.holiday_lines(conn, args.year)))
            return
        try:
            by = store.attribution(args.author, args.directed_by)
        except ValueError as e:
            sys.exit(f"invalid attribution: {e}")
        if args.sub == "add":
            print(triggers.holiday_add(conn, args.case_dir, args.on, args.name, args.kind, args.source, by))
        else:
            print(triggers.holiday_year(conn, args.case_dir, args.year, args.jurisdiction, args.source, by))
    except triggers.TriggerError as e:
        sys.exit(f"refused: {e}")


def cmd_subcase(args):
    """subcase init <parent_dir> <slug> --title T — a matter of its own at <parent>/Subcases/<slug>/."""
    from matterkit import subcase
    try:
        by = store.attribution(args.author, args.directed_by)
    except ValueError as e:
        sys.exit(f"invalid attribution: {e}")
    try:
        path = subcase.init(args.parent_dir, args.slug, args.title, by, FOLDERS)
    except subcase.SubcaseError as e:
        sys.exit(f"refused: {e}")
    print(f"subcase created: {path}\n  own store .matter/matter.db; matter.json names the parent; "
          f"cite parent documents there as parent:<rel_path>@<locator>")


def cmd_party(args):
    if not os.path.isdir(args.case_dir):
        sys.exit(f"error: case_dir is not a directory: {args.case_dir}")
    conn = store.connect(args.case_dir)
    if args.sub == "list":
        print(json.dumps([dict(r) for r in conn.execute("SELECT * FROM parties ORDER BY name")], indent=2))
        return
    if not args.name or not args.name.strip() or not args.role:
        sys.exit("party add requires --name and --role")
    pid = store.new_id("p_")
    with conn:
        conn.execute("INSERT INTO parties (id,name,role,capacity) VALUES (?,?,?,?)",
                     (pid, args.name.strip(), args.role, args.capacity))
    print(pid)


def cmd_correspondence(args):
    from matterkit import correspondence as co
    if not os.path.isdir(args.case_dir):
        sys.exit(f"error: case_dir is not a directory: {args.case_dir}")
    with store.connect(args.case_dir) as conn:
        try:
            if args.sub == "add":
                print(co.create_notice(conn, args.case_dir, kind=args.kind, addressee=args.addressee,
                                       address=args.address, exhibit=args.exhibit,
                                       assertion_ids=args.assertion, confirm_due=args.confirm_due))
            elif args.sub == "list":
                print(co.ledger(conn))
            elif args.sub == "render":
                print(co.render_packet(conn, args.case_dir, args.notice))
            elif args.sub == "manifest":
                print(json.dumps(co.build_manifest(conn, args.case_dir, args.notice), indent=2, ensure_ascii=False))
            elif args.sub == "export":
                print(json.dumps(co.export_packet(conn, args.case_dir, args.notice), indent=2))
            elif args.sub == "status":
                status = co.transition(conn, args.case_dir, args.notice, args.state,
                                       issuer=args.issuer, expected_sha=args.sha, receipt=args.receipt)
                print(f"{args.notice} → {status}; transport=nosend (operator action recorded; nothing sent)")
        except (ValueError, OSError) as e:
            sys.exit(f"refused: {e}")


def cmd_authority(args):
    """authority add|check|import. The legacy form `authority <dir> <citation>` still means add."""
    if args.sub_or_dir in ("add", "check", "import"):
        sub, rest = args.sub_or_dir, args.rest
    else:
        sub, rest = "add", [args.sub_or_dir, *args.rest]
    if len(rest) != 2:
        sys.exit(f"usage: matter.py authority {sub} <dir> " + ("<ledger.md>" if sub == "import" else '"<citation>"'))
    case_dir, target = rest
    try:
        by = store.attribution(args.author, args.directed_by)
    except ValueError as e:
        sys.exit(f"invalid attribution: {e}")
    from matterkit import authority, briefcheck
    try:
        rules = briefcheck.rule_prefixes(briefcheck.load_docket(case_dir)[0])
    except ValueError as e:
        sys.exit(f"refused: {e}")
    conn = store.connect_readonly(case_dir) if sub == "import" and not args.apply else store.connect(case_dir)
    if sub == "add":
        from matterkit.store import new_id
        conn.execute("INSERT INTO authorities (id,citation,status,checked_via,note) VALUES (?,?,?,?,?)",
                     (new_id("auth_"), target, "unverified", args.via, f"added by {by}"))
        conn.commit()
        print(f"authority logged (unverified): {target} by {by} — record what you opened with: matter.py authority check")
        return
    if sub == "check":
        try:
            aid, status = authority.check(conn, case_dir, target, source_url=args.source_url, artifact=args.artifact,
                                          pins=args.pin, quote=args.quote, note=args.note, stale=args.stale,
                                          by=by, use_pdftotext=not args.no_pdftotext, rules=rules)
        except (ValueError, citations_error()) as e:
            sys.exit(f"refused: {e}")
        print(f"authority {aid} → [{status}] pins={args.pin or []} by {by}")
        return
    planned, skipped = authority.import_ledger(conn, case_dir, target, apply=args.apply, by=by, rules=rules)
    print(("APPLIED" if args.apply else "DRY RUN — nothing written; re-run with --apply") +
          f": {len(planned)} to add, {len(skipped)} skipped")
    for r in planned:
        pins = f" pins={r['pins']}" if r["pins"] else (" pins unconfirmed" if r["pins_unconfirmed"] else "")
        print(f"  + [{r['status']}] {r['citation']}{pins}  ← {r['ledger_status'] or '—'}")
    for r in skipped:
        print(f"  = {r['citation']}  ({r['reason']})")


def citations_error():
    from matterkit.citations import StageOrderError
    return StageOrderError


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


def cmd_brief(args):
    """brief check: tie every cite, quotation and factual sentence in a draft to the store.
    Read-only. Exit 1 = blocking findings, 2 = items a human must check, 0 = all tied."""
    from matterkit import briefcheck
    try:
        report = briefcheck.check(args.case_dir, args.draft, use_pdftotext=not args.no_pdftotext,
                                  docket_path=args.docket, mode=args.mode)
    except (ValueError, FileNotFoundError) as e:
        sys.exit(f"brief check refused: {e}")
    text = report.render()
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"{report.summary()}\nwrote {args.out}")
    else:
        print(text)
    sys.exit(report.exit_code)


def cmd_rules(args):
    """rules build|attach|checked|list|show|stale: a court's rules as Markdown with anchors, attached to a matter.
    show exits 1 when nothing is found and 2 when a bare cite has candidates; stale exits 1 when a pack
    is past its review window (with no current recorded check) or undated."""
    from datetime import date
    from matterkit import rules
    today = None
    if getattr(args, "today", None):
        try:
            today = date.fromisoformat(args.today)
        except ValueError:
            sys.exit(f"--today must be YYYY-MM-DD (got {args.today!r})")
    try:
        if args.sub == "build":
            m = rules.build_pack(args.source, args.out_dir, pack_id=args.pack_id, version=args.version,
                                 title=args.title, cite_prefix=args.cite_prefix, court=args.court,
                                 anchor_prefix=args.anchor_prefix, current_as_of=args.current_as_of,
                                 source_url=args.source_url, start_at=args.start_at, stop_at=args.stop_at,
                                 heading_re=args.heading_regex, review_after_days=args.review_after_days,
                                 use_pdftotext=not args.no_pdftotext, marker_style=args.markers, unit=args.unit)
            print(f"built {m['id']}@{m['version']} in {args.out_dir}: {m['rules']} rules, {m['anchors']} anchors; "
                  f"{m['words']['body']} words identical to the source text")
            for kind, items in (("note", m["notes"]), ("⚠", m["warnings"])):
                for item in items[:10]:
                    print(f"  {kind} {item}")
                if len(items) > 10:
                    print(f"  {kind} … {len(items) - 10} more in {os.path.join(args.out_dir, 'manifest.json')}")
            return
        if not os.path.isdir(args.case_dir):
            sys.exit(f"error: case_dir is not a directory: {args.case_dir}")
        if args.sub == "attach":
            try:
                by = store.attribution(args.author, args.directed_by)
            except ValueError as e:
                sys.exit(f"invalid attribution: {e}")
            print(rules.attach(args.case_dir, args.pack_dir, by, replace=args.replace)[1])
        elif args.sub == "checked":
            try:
                by = store.attribution(args.author, args.directed_by)
            except ValueError as e:
                sys.exit(f"invalid attribution: {e}")
            print(rules.record_check(args.case_dir, args.pack_id, args.on, args.found, args.source, by,
                                     next_review=args.next_review, today=today)[1])
        elif args.sub == "list":
            print(rules.list_text(args.case_dir, today))
        else:
            code, text = (rules.show(args.case_dir, args.cite, args.pack, today) if args.sub == "show"
                          else rules.stale_report(args.case_dir, today))
            print(text)
            sys.exit(code)
    except rules.RulesError as e:
        sys.exit(f"refused: {e}")


def cmd_docket(args):
    """docket suggest: docket numbers derived from registered filenames, as docket.json JSON."""
    from matterkit import briefcheck
    print(json.dumps(briefcheck.suggest_docket(store.connect_readonly(args.case_dir)), indent=2))


CAPTURE_USAGE = ("usage: matter capture <case-dir> --url URL --out REL_PATH [--kind media|research]\n"
                 "       matter capture list <case-dir>")


def cmd_capture(args):
    """capture <case-dir> --url URL --out REL_PATH | capture list <case-dir>

    --out is matter-relative (joined to the matter root, never cwd). The fetcher —
    the only network code — is imported for a capture, never for `list`.
    """
    from matterkit import capture as cap
    target = args.target
    listing = target[0] == "list" and len(target) == 2
    if not listing and (len(target) != 1 or not args.url or not args.out):
        sys.exit(CAPTURE_USAGE)
    case_dir = target[-1]
    if not os.path.isdir(case_dir):
        # never let a typo'd path become a fresh store that a fetch then lands in
        sys.exit(f"error: case_dir is not a directory: {case_dir}")
    if listing:
        print(cap.render_list(store.connect(case_dir)))
        return
    from matterkit.adapters.capture_fetch import make_fetch
    try:
        res = cap.capture_url(store.connect(case_dir), case_dir, args.url, args.out,
                              fetch=make_fetch(case_dir), kind=args.kind)
    except (cap.CaptureError, OSError) as e:
        sys.exit(f"refused: {e}")
    if res["unchanged"]:
        print(f"unchanged: {args.url} returned bytes already registered as {res['rel_path']}\n"
              f"  sha256={res['sha256']} retrieved_at={res['retrieved_at']}\n"
              f"  retrieval recorded; no file written at {args.out}")
    else:
        print(f"captured {args.url}\n  → {res['rel_path']}\n"
              f"  sha256={res['sha256']} retrieved_at={res['retrieved_at']}")
        if res["extract"]:
            print(f"  derived text: {res['extract']} (cite the source, not the extract)")
        if res.get("extract_error"):
            print(f"  ⚠ derived text NOT written ({res['extract_error']}); the capture itself is registered")
    print(f"next, from a cwd that is neither the matter nor the kit: "
          f"matter.py verify {os.path.abspath(case_dir)}   # MISSING/CHANGED is a blocker")


def cmd_cite(args):
    """cite extract|list — local citation extraction (RFC 0001 §2.2/§2.3).

    Offsets are meaningful against the exact decoded text parsed; its sha256
    is stored per row (`text_sha256`). The document (container) sha256 is
    recorded separately and never used to define offsets.
    """
    if args.sub == "lookup":
        return cmd_cite_lookup(args)
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


def cmd_cite_lookup(args):
    """cite lookup: send a draft's case cites, and nothing else, to CourtListener's citation lookup.
    Exit 1 = a cite to check by hand (not found, name differs, unknown reporter), 2 = ambiguous or not
    looked up, 0 = every case cite found. --dry-run prints the exact payload and sends nothing."""
    import hashlib
    from matterkit import citelookup
    from matterkit.citations import now_iso
    if not args.path:
        sys.exit("usage: matter.py cite lookup <dir> <draft.md> [--dry-run] [--record] [--out REPORT.md]")
    if args.path.lower().endswith((".docx", ".pdf", ".doc")):
        sys.exit("cite lookup reads Markdown or plain text; use the .md copy of the draft")
    try:
        by = store.attribution(args.author, args.directed_by)
    except ValueError as e:
        sys.exit(f"invalid attribution: {e}")
    if args.max_requests < 1:
        sys.exit("--max-requests must be at least 1")
    with open(args.path, "rb") as f:
        raw = f.read()
    cites = citelookup.draft_cites(raw.decode("utf-8", "replace"))
    payloads = citelookup.payloads(cites)
    if args.dry_run:
        _write_or_print(citelookup.render_dry_run(args.path, payloads), args.out, "dry run: nothing sent")
        return
    if not os.path.isdir(os.path.join(args.case_dir, ".matter")):
        sys.exit(f"refused: {args.case_dir} is not a matter (no .matter/)")
    answers, stopped, sent = [], None, 0
    if payloads:
        from matterkit.adapters.courtlistener_citations import CitationLookupAdapter
        from matterkit.consent import ConsentError
        try:
            adapter = CitationLookupAdapter(args.case_dir, args.caller)
            answers, stopped = adapter.lookup(payloads[: args.max_requests])
        except (ConsentError, ValueError) as e:
            sys.exit(f"refused: {e}")
        sent = adapter.sent
        if stopped is None and len(payloads) > args.max_requests:
            stopped = f"over --max-requests {args.max_requests}; run again in a minute for the rest"
    report = citelookup.Report(args.path, hashlib.sha256(raw).hexdigest(),
                               citelookup.interpret(cites, payloads, answers, stopped), payloads, sent, now_iso())
    if args.record:
        report.recorded = citelookup.record(store.connect(args.case_dir), report.results, by)
    if stopped:
        print(f"lookup stopped early: {stopped}", file=sys.stderr)
    _write_or_print(report.render(), args.out, report.summary())
    sys.exit(report.exit_code)


def _write_or_print(text, out, summary):
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"{summary}\nwrote {out}")
    else:
        print(text)


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
    """consent propose|activate. --scope search: the R2 search adapter (query_text to the host);
    --scope cite-lookup: bare case cites to the citation-lookup URL, and nothing else."""
    from matterkit import citelookup, consent
    from matterkit.adapters.courtlistener import ENDPOINT_HOST
    provider, endpoints, payloads, budget = {
        "search": ("courtlistener-free", [ENDPOINT_HOST], ["query_text"], 20),
        "cite-lookup": (citelookup.PROVIDER, [citelookup.ENDPOINT], [citelookup.PAYLOAD_TYPE], 10),
    }[args.scope]
    if args.sub == "propose":
        g = consent.stage_proposal(
            args.case_dir, provider, endpoints, payloads, args.as_user, daily_budget=budget)
        print(f"staged INERT proposal for {provider} by {g['created_by']!r} "
              "(authorizes no one until human TTY activate)")
    elif args.sub == "activate":
        try:
            g = consent.activate_human_grant(
                args.case_dir, provider, endpoints, payloads, args.issuer, daily_budget=budget,
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


def cmd_ui(args):
    """ui: a browser UI over a workspace of cases, served to this computer only (127.0.0.1)."""
    from matterkit import webui
    name = args.operator
    if not name:
        if not sys.stdin.isatty():
            sys.exit("matter ui needs --operator NAME: the person recorded as the author of what is added")
        name = input("Your name (recorded as the author of what you add): ")
    try:
        webui.serve(args.workspace, webui.check_operator(name), port=args.port, open_browser=not args.no_open)
    except (ValueError, OSError) as e:
        sys.exit(f"matter ui: {e}")


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
    p.add_argument("--author", default=os.environ.get("MATTER_AUTHOR"),
                   help="who did the work: 'human' (default) or 'agent:<model>'; env MATTER_AUTHOR")
    p.add_argument("--directed-by", default=os.environ.get("MATTER_DIRECTED_BY"),
                   help="person who directed an agent author (not an approval); env MATTER_DIRECTED_BY")
    p.set_defaults(fn=cmd_fact)

    p = sub.add_parser("amend", help="Changes queued for the next version of a pleading")
    commands = p.add_subparsers(dest="sub", required=True)
    c = commands.add_parser("add", help="Queue a change")
    c.add_argument("case_dir")
    c.add_argument("--text", help="what to add or change (required)")
    c.add_argument("--para", help='where, e.g. "¶¶ 37, 172"')
    c.add_argument("--reason", help="why the change is needed")
    c.add_argument("--target", default="complaint", help="which pleading (default: complaint)")
    c.add_argument("--fact", action="append", default=[], help="a_ID of a fact in this case that supports the change")
    c.add_argument("--source", action="append", default=[],
                   help="rel_path@page of a registered document; split on the last '@' "
                        "(a file name containing '@' and no page needs a trailing '@')")
    c = commands.add_parser("list", help="Show the queue (default: open changes, approved first)")
    c.add_argument("case_dir")
    c.add_argument("--status", default="open", choices=["open", "all", *amendments.STATUSES],
                   help="open = proposed + approved (default)")
    c.add_argument("--target", help="only changes for this pleading")
    c = commands.add_parser("set", help="Move a change to another status; the old status stays in its history")
    c.add_argument("case_dir")
    c.add_argument("item_id", help="the am_... id from `amend list`")
    c.add_argument("--status", required=True, choices=amendments.STATUSES)
    c.add_argument("--note", help="why (kept in the history)")
    for c in (commands.choices["add"], commands.choices["set"]):
        c.add_argument("--author", default=os.environ.get("MATTER_AUTHOR"),
                       help="who did the work: 'human' (default) or 'agent:<model>'; env MATTER_AUTHOR")
        c.add_argument("--directed-by", default=os.environ.get("MATTER_DIRECTED_BY"),
                       help="person who directed an agent author (not an approval); env MATTER_DIRECTED_BY")
    for c in commands.choices.values():
        c.set_defaults(fn=cmd_amend)

    p = sub.add_parser("brief"); p.add_argument("sub", choices=["check"]); p.add_argument("case_dir")
    p.add_argument("draft"); p.add_argument("--out"); p.add_argument("--no-pdftotext", action="store_true")
    p.add_argument("--docket", help="docket map JSON to use instead of <dir>/.matter/docket.json")
    p.add_argument("--mode", choices=["brief", "complaint"], default="brief",
                   help="complaint: allegations need no record cite; cites, authorities and sourced quotes still checked")
    p.set_defaults(fn=cmd_brief)

    p = sub.add_parser("docket"); p.add_argument("sub", choices=["suggest"]); p.add_argument("case_dir")
    p.set_defaults(fn=cmd_docket)

    p = sub.add_parser("rules", help="A court's rules as Markdown with anchors, attached to a matter")
    commands = p.add_subparsers(dest="sub", required=True)
    c = commands.add_parser("build", help="Build a rules pack from a .txt or .pdf (no matter needed)")
    c.add_argument("source"); c.add_argument("out_dir")
    c.add_argument("--id", dest="pack_id", required=True, help="lowercase pack id, e.g. district-local")
    c.add_argument("--version", required=True, help="usually the current-as-of date")
    c.add_argument("--title", required=True); c.add_argument("--cite-prefix", required=True, help="e.g. L.U.Civ.R.")
    c.add_argument("--current-as-of", help="YYYY-MM-DD the source says it is current as of")
    c.add_argument("--court"); c.add_argument("--source-url"); c.add_argument("--anchor-prefix")
    c.add_argument("--start-at", help="exact text of the first line to keep (skips title page and contents)")
    c.add_argument("--stop-at", help="exact text of the first line not to keep (e.g. the forms section)")
    c.add_argument("--heading-regex", help="rule heading pattern with a (?P<num>...) group")
    c.add_argument("--review-after-days", type=int, default=365)
    c.add_argument("--markers", choices=("paren", "dotted"), default="paren",
                   help='subsection markers: "(a)(1)(A)" (paren) or an outline numbered "A.", "1.", "a." (dotted)')
    c.add_argument("--unit", default="Rule", help="word for a heading in the Markdown, e.g. Section (default: Rule)")
    c.add_argument("--no-pdftotext", action="store_true")
    c = commands.add_parser("attach", help="Copy a pack into the matter, register it and record it")
    c.add_argument("case_dir"); c.add_argument("pack_dir")
    c.add_argument("--replace", action="store_true", help="attach a new version in place of the attached one")
    c.add_argument("--author", default=os.environ.get("MATTER_AUTHOR"))
    c.add_argument("--directed-by", default=os.environ.get("MATTER_DIRECTED_BY"))
    c = commands.add_parser("checked", help="Record a check of the court's website for amendments to an attached pack")
    c.add_argument("case_dir"); c.add_argument("pack_id")
    c.add_argument("--on", required=True, help="YYYY-MM-DD the court's website was checked")
    c.add_argument("--found", required=True, choices=("none", "amended"), help="whether an amendment was found")
    c.add_argument("--source", action="append", required=True,
                   help="registered file showing what was found, rel_path[@locator]; repeat for more")
    c.add_argument("--next-review", help="YYYY-MM-DD to check again by (e.g. the court's next amendment date)")
    c.add_argument("--today", help="YYYY-MM-DD to treat as today (default: today)")
    c.add_argument("--author", default=os.environ.get("MATTER_AUTHOR"))
    c.add_argument("--directed-by", default=os.environ.get("MATTER_DIRECTED_BY"))
    for action in ("list", "show", "stale"):
        c = commands.add_parser(action)
        c.add_argument("case_dir")
        if action == "show":
            c.add_argument("cite", help='"L.U.Civ.R. 7(b)(4)", "Local Rule 7(b)(4)", an anchor id, or "Rule 15"')
            c.add_argument("--pack", help="pack id, to read a bare rule number in that rule set")
        c.add_argument("--today", help="YYYY-MM-DD to measure staleness against (default: today)")
    for c in commands.choices.values():
        c.set_defaults(fn=cmd_rules)

    p = sub.add_parser("deadline"); p.add_argument("sub", choices=["add", "list", "update"]); p.add_argument("case_dir")
    p.add_argument("deadline_id", nargs="?", help="update: the deadline to change")
    p.add_argument("--label"); p.add_argument("--due"); p.add_argument("--rule")
    # defaults are applied in cmd_deadline: `update` must see "not given" as None
    p.add_argument("--confidence", choices=["hard", "working-estimate"])
    p.add_argument("--verification", choices=["verified-official", "secondary-source", "unverified"])
    p.add_argument("--state", choices=["open", "met", "superseded", "lapsed", "vacated"])
    p.add_argument("--owner", choices=["ours", "theirs", "court"],
                   help="who must act by the date; list: show only that owner's deadlines")
    p.add_argument("--reason", help="update: why the deadline changed (required)")
    p.add_argument("--source", action="append", default=[], help="update: rel_path@locator of a registered document")
    p.add_argument("--all", action="store_true")
    p.add_argument("--author", default=os.environ.get("MATTER_AUTHOR"),
                   help="who did the work: 'human' (default) or 'agent:<model>'; env MATTER_AUTHOR")
    p.add_argument("--directed-by", default=os.environ.get("MATTER_DIRECTED_BY"),
                   help="person who directed an agent author (not an approval); env MATTER_DIRECTED_BY")
    p.set_defaults(fn=cmd_deadline)

    p = sub.add_parser("trigger"); p.add_argument("sub", choices=["add", "fire", "list", "withdraw"])
    p.add_argument("case_dir"); p.add_argument("trigger_id", nargs="?", help="fire/withdraw: the trigger")
    p.add_argument("--label"); p.add_argument("--waits-on", dest="waits_on", help="add: the event it waits on")
    p.add_argument("--days", type=int, help="add: days after the event (Fed. R. Civ. P. 6(a)(1))")
    p.add_argument("--rule"); p.add_argument("--owner", choices=["ours", "theirs", "court"])
    p.add_argument("--on", help="fire: the date the event happened, YYYY-MM-DD")
    p.add_argument("--service", choices=["mail", "clerk", "consented-other", "email", "ecf", "hand", "none"],
                   help="fire: how the paper was served (6(d) adds 3 days for mail, clerk, consented-other); "
                        "none: the period runs from the event itself")
    p.add_argument("--source", action="append", default=[], help="fire: rel_path@locator of a registered document")
    p.add_argument("--reason", help="fire/withdraw: why (required)")
    p.add_argument("--dry-run", action="store_true", help="fire: show the count and write nothing")
    p.add_argument("--all", action="store_true", help="list: include fired and withdrawn triggers")
    p.add_argument("--author", default=os.environ.get("MATTER_AUTHOR"),
                   help="who did the work: 'human' (default) or 'agent:<model>'; env MATTER_AUTHOR")
    p.add_argument("--directed-by", default=os.environ.get("MATTER_DIRECTED_BY"),
                   help="person who directed an agent author (not an approval); env MATTER_DIRECTED_BY")
    p.set_defaults(fn=cmd_trigger)

    p = sub.add_parser("holiday"); p.add_argument("sub", choices=["add", "year", "list"]); p.add_argument("case_dir")
    p.add_argument("--on", help="add: the day, YYYY-MM-DD"); p.add_argument("--name")
    p.add_argument("--kind", choices=["state", "declared", "closure"],
                   help="add: state holiday (6(a)(6)(C)), declared by the President or Congress (6(a)(6)(B)), "
                        "or clerk's office inaccessible (6(a)(3))")
    p.add_argument("--year", type=int); p.add_argument("--jurisdiction", help="year: whose state holidays")
    p.add_argument("--source", action="append", default=[], help="rel_path@locator of a registered document")
    p.add_argument("--author", default=os.environ.get("MATTER_AUTHOR"),
                   help="who did the work: 'human' (default) or 'agent:<model>'; env MATTER_AUTHOR")
    p.add_argument("--directed-by", default=os.environ.get("MATTER_DIRECTED_BY"),
                   help="person who directed an agent author (not an approval); env MATTER_DIRECTED_BY")
    p.set_defaults(fn=cmd_holiday)

    p = sub.add_parser("subcase"); p.add_argument("sub", choices=["init"])
    p.add_argument("parent_dir"); p.add_argument("slug")
    p.add_argument("--title", required=True, help="human title of the subcase")
    p.add_argument("--author", default=os.environ.get("MATTER_AUTHOR"),
                   help="who did the work: 'human' (default) or 'agent:<model>'; env MATTER_AUTHOR")
    p.add_argument("--directed-by", default=os.environ.get("MATTER_DIRECTED_BY"),
                   help="person who directed an agent author (not an approval); env MATTER_DIRECTED_BY")
    p.set_defaults(fn=cmd_subcase)

    p = sub.add_parser("party")
    p.add_argument("sub", choices=["add", "list"]); p.add_argument("case_dir")
    p.add_argument("--name")
    p.add_argument("--role", choices=["plaintiff", "defendant", "witness", "counsel", "court", "agency", "other"])
    p.add_argument("--capacity")
    p.set_defaults(fn=cmd_party)

    from matterkit import correspondence as co
    p = sub.add_parser("correspondence", help="Custodian notices; humans send, software records evidence")
    commands = p.add_subparsers(dest="sub", required=True)
    for action in ("add", "list", "render", "manifest", "export", "status"):
        c = commands.add_parser(action)
        c.add_argument("case_dir")
        c.set_defaults(fn=cmd_correspondence)
        if action == "add":
            c.add_argument("--kind", choices=co.KINDS, required=True)
            c.add_argument("--addressee", required=True, help="Existing agency|other party with capacity=custodian")
            c.add_argument("--address", required=True, help="Human-selected mailing address")
            c.add_argument("--exhibit", required=True, help="Ingested document ID, SHA-256, or matter-relative path")
            c.add_argument("--assertion", action="append", default=[], help="Explicit assertion ID; repeat to select")
            c.add_argument("--confirm-due", help="Requested confirmation date, YYYY-MM-DD")
        elif action != "list":
            c.add_argument("notice")
        if action == "status":
            c.add_argument("state", choices=co.STATUSES)
            c.add_argument("--issuer", required=True, help="Name of human attesting to this action")
            c.add_argument("--sha", help="Reviewed manifest SHA-256, required for signed and mailed")
            c.add_argument("--receipt", help="Ingested mailing receipt, delivery confirmation, response, or refusal")

    p = sub.add_parser("authority", usage="matter.py authority add|check|import <dir> <citation|ledger.md> ...")
    p.add_argument("sub_or_dir"); p.add_argument("rest", nargs="+")
    p.add_argument("--via", default=None)
    p.add_argument("--source-url"); p.add_argument("--artifact"); p.add_argument("--pin", action="append", default=[])
    p.add_argument("--quote"); p.add_argument("--note"); p.add_argument("--stale", metavar="REASON")
    p.add_argument("--apply", action="store_true"); p.add_argument("--no-pdftotext", action="store_true")
    p.add_argument("--author", default=os.environ.get("MATTER_AUTHOR"))
    p.add_argument("--directed-by", default=os.environ.get("MATTER_DIRECTED_BY"))
    p.set_defaults(fn=cmd_authority)

    p = sub.add_parser("capture", usage=CAPTURE_USAGE[len("usage: "):])
    p.add_argument("target", nargs="+", metavar="[list] case_dir")
    p.add_argument("--url"); p.add_argument("--out")
    p.add_argument("--kind", choices=["media", "research"])
    p.set_defaults(fn=cmd_capture)

    p = sub.add_parser("cite"); p.add_argument("sub", choices=["extract", "list", "lookup"]); p.add_argument("case_dir")
    p.add_argument("path", nargs="?")
    p.add_argument("--status", choices=["extracted", "extraction-failed"])
    p.add_argument("--dry-run", action="store_true", help="lookup: print the exact payload; send nothing")
    p.add_argument("--record", action="store_true",
                   help="lookup: move found cites' authorities rows from unverified to resolved")
    p.add_argument("--out"); p.add_argument("--max-requests", type=int, default=3)
    p.add_argument("--caller", default=os.environ.get("USER", "human"))
    p.add_argument("--author", default=os.environ.get("MATTER_AUTHOR"))
    p.add_argument("--directed-by", default=os.environ.get("MATTER_DIRECTED_BY"))
    p.set_defaults(fn=cmd_cite)

    p = sub.add_parser("stage"); p.add_argument("case_dir"); p.add_argument("authority_id"); p.add_argument("status")
    p.add_argument("--via", default=None); p.add_argument("--evidence", default=None)
    p.set_defaults(fn=cmd_auth_stage)

    p = sub.add_parser("packet"); p.add_argument("case_dir")
    p.add_argument("--layer", default="3min", choices=["30s", "3min", "15min"])
    p.add_argument("--out"); p.set_defaults(fn=cmd_packet)

    p = sub.add_parser("consent"); p.add_argument("sub", choices=["propose", "activate"])
    p.add_argument("case_dir")
    p.add_argument("--scope", choices=["search", "cite-lookup"], default="search")
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

    p = sub.add_parser("ui", help="Open a browser UI for the cases in a folder (this computer only)")
    p.add_argument("workspace", help="a folder of case folders, or one case folder")
    p.add_argument("--operator", help="your name, recorded as the author of what you add")
    p.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    p.add_argument("--no-open", action="store_true", help="print the link instead of opening the browser")
    p.set_defaults(fn=cmd_ui)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

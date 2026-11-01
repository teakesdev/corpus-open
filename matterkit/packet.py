"""Counsel-packet and status rendering. Markdown out; every assertion carries its label."""
from collections import Counter
from datetime import date

from . import deadlines, subcase


def _badge(status: str) -> str:
    return "" if status == "DOCUMENTED FACT" else f" `{status}`"


def status_data(conn, matter_dir: str) -> dict:
    """The counts `status_report` renders, as a dict. `overdue_deadlines` is the open deadlines
    dated before today; the text report does not show it (children_lines carries the subcases')."""
    docs = conn.execute("SELECT kind, COUNT(*) c FROM documents GROUP BY kind ORDER BY kind").fetchall()
    n_assert = conn.execute("SELECT COUNT(*) c FROM assertions").fetchone()["c"]
    open_dl = conn.execute("SELECT due_date, state FROM deadlines WHERE state='open'").fetchall()
    # "Unverified" = no source check yet (RFC 0001 §2.2 vocabulary):
    # pre-check stages are unverified/resolved/research-pending; legacy
    # 'verified-official' rows count as checked.
    checked = ("source-checked", "stale-flagged", "verified-official")
    n_auth = conn.execute(
        "SELECT COUNT(*) c FROM authorities WHERE status NOT IN ({})".format(
            ",".join("?" * len(checked))), checked).fetchone()["c"]
    notices = conn.execute(
        "SELECT status,COUNT(*) c FROM correspondence_notices GROUP BY status ORDER BY status").fetchall()
    return {"matter_dir": matter_dir,
            "parent_line": subcase.parent_line(matter_dir),
            "documents_total": sum(r["c"] for r in docs),
            "documents_by_kind": {r["kind"]: r["c"] for r in docs},
            "assertions": n_assert,
            "open_deadlines": len(open_dl),
            "overdue_deadlines": sum(deadlines.is_overdue(r["due_date"], r["state"]) for r in open_dl),
            "unverified_authorities": n_auth,
            "notices_by_status": {r["status"]: r["c"] for r in notices},
            "children_lines": subcase.children_lines(matter_dir)}


def status_report(conn, matter_dir: str) -> str:
    d = status_data(conn, matter_dir)
    lines = [f"# Matter status — {d['matter_dir']}", ""]
    if d["parent_line"]:
        lines += [d["parent_line"], ""]
    lines.append(f"Documents registered: **{d['documents_total']}**")
    for kind, c in d["documents_by_kind"].items():
        lines.append(f"- {kind}: {c}")
    lines += ["", f"Assertions: {d['assertions']} · Open deadlines: {d['open_deadlines']} · "
                  f"Unverified authorities: {d['unverified_authorities']}"]
    if d["notices_by_status"]:
        lines += ["", "Custodian notices (transport=nosend; humans send): " +
                  " · ".join(f"{s}: {c}" for s, c in d["notices_by_status"].items())]
    if d["children_lines"]:
        lines += [""] + d["children_lines"]
    return "\n".join(lines)


def packet(conn, matter_dir: str, layer: str = "3min") -> str:
    today = date.today().isoformat()
    parties = conn.execute("SELECT * FROM parties ORDER BY role, name").fetchall()
    deadlines = conn.execute(
        "SELECT * FROM deadlines WHERE state='open' ORDER BY due_date").fetchall()
    asserts = conn.execute("SELECT * FROM assertions ORDER BY status, created_at").fetchall()

    def srcs(aid):
        rows = conn.execute(
            "SELECT d.rel_path, s.locator FROM assertion_sources s"
            " JOIN documents d ON d.id=s.document_id WHERE s.assertion_id=?", (aid,)).fetchall()
        return "; ".join(f"{r['rel_path']}{' @ ' + r['locator'] if r['locator'] else ''}"
                         for r in rows)

    L = [f"# Counsel packet ({layer} view)", "",
         f"_Generated {today} by matter-kit. Informational organization of the litigant's own "
         "materials; not legal advice. Every claim below is labeled; unlabeled = unsupported._", ""]

    if parties:
        L += ["## Parties", ""]
        for p in parties:
            cap = f" ({p['capacity']})" if p["capacity"] else ""
            L.append(f"- **{p['name']}** — {p['role']}{cap}")
        L.append("")

    if deadlines:
        L += ["## Critical dates", "", "| Due | What | Source of deadline | Confidence |", "|---|---|---|---|"]
        for d in deadlines:
            L.append(f"| {d['due_date']} | {d['label']} | {d['rule_source'] or '—'} | {d['confidence']} |")
        L.append("")

    order = ["DOCUMENTED FACT", "INFERENCE", "ALLEGATION", "HYPOTHESIS", "UNKNOWN"]
    grouped = Counter(a["status"] for a in asserts)
    L += ["## Assertion ledger", ""]
    for st in order:
        subset = [a for a in asserts if a["status"] == st]
        if not subset:
            continue
        L.append(f"### {st} ({grouped[st]})")
        for a in subset:
            s = srcs(a["id"])
            tag = "" if s else " **[UNSUPPORTED — no source attached]**"
            L.append(f"- {a['text']}{_badge(st)}{tag}")
            if s:
                L.append(f"  - ↳ _{s}_")
        L.append("")

    L += ["## Adverse facts & open questions",
          "",
          "- _(fill deliberately — credibility requires stating the worst facts plainly)_",
          "",
          "---",
          "_Boundary: this packet was assembled with software assistance from the litigant's own "
          "indexed materials. It does not constitute legal advice, does not evaluate merits, and "
          "every citation should be independently verified before reliance._"]
    return "\n".join(L)

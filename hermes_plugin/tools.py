"""Matter tools for Hermes: the case folder's store, read-mostly, through the kit's own modules.

Every handler takes (args, **kwargs), returns a JSON string and never raises, as Hermes asks.
Nothing here files, sends or contacts anyone. Two handlers reach the network, both through the
kit's consent grants (the case's default for bare cites, or a person's own): matter_cite_check,
through the guard, and matter_brief_check, which sends a draft's bare case cites to CourtListener
with the plugin's key unless citation_guard is not lookup or offline is set.
"""
from __future__ import annotations

import contextlib
import io
import json
import os

from ..matterkit import ingest as ing
from ..matterkit import packet as pk
from ..matterkit import store
from .guard import HOW_TO_START

TOOLSET = "corpus_open"
LABELS = ["DOCUMENTED FACT", "ALLEGATION", "INFERENCE", "HYPOTHESIS", "UNKNOWN"]
BRIEF_KEY_HINT = ("add it in Hermes's settings for this plugin (courtlistener_token), never in a chat; "
                  "/matter grant shows how")


class MatterError(ValueError):
    pass


def draft_path(md: str, rel: str) -> str:
    """A file inside the case folder to read, or a MatterError a newcomer can act on (not an errno).
    A link is followed only when its target is inside the case folder too."""
    if _is_case_root(md, rel):
        raise MatterError("That is the case folder itself. Name one file in it, for example a draft in Drafts.")
    p = inside(md, rel)
    if not ing.reads_inside(p, md):
        raise MatterError(f"{rel} is a link to a file outside the case folder, so it is not read. Copy the "
                          "file into the case folder instead.")
    if os.path.isdir(p):
        raise MatterError(f"{rel} is a folder. Name one file in it, for example a draft in Drafts.")
    if not os.path.isfile(p):
        raise MatterError(f"There is no {rel} in the case folder. Check the name, or type /matter to "
                          "see the case.")
    return p


def resolve_matter(raw: str | None) -> str:
    """The configured case folder, expanded and checked to be a matter. When there is none, the
    message is read by people (from /matter) and by the agent (from a tool), so it names the two
    slash commands that start a case, not a setting or a terminal command."""
    if not raw or not str(raw).strip():
        raise MatterError("No case folder is set up yet. " + HOW_TO_START)
    path = ing.abs_path(os.path.expanduser(str(raw).strip()))
    if not os.path.isdir(os.path.join(path, ".matter")):
        raise MatterError(f"{path} is not set up as a case folder yet. Type /matter init {path} to set "
                          "it up (files already there are kept), or /matter demo to try a made-up case.")
    return path


def _is_case_root(matter_dir: str, path: str) -> bool:
    """True when `path` names the case folder itself, in any spelling that leads there (".", the
    folder as configured, or its real location when the case was set up through a link)."""
    return bool(path) and ing.resolve_in_matter(path, matter_dir) == ing.abs_path(matter_dir)


def inside(matter_dir: str, path: str) -> str:
    """Resolve `path` inside the matter; relative paths join the matter root, not the cwd."""
    if not path:
        raise MatterError("path required")
    root = ing.abs_path(matter_dir)
    cand = ing.resolve_in_matter(path, root)
    if cand is None:
        raise MatterError("path is outside the case folder")
    if cand == root:
        raise MatterError("That is the case folder itself. Name a folder or a file in it.")
    ing.to_rel_path(cand, root)
    return cand


def _schema(name: str, description: str, properties: dict | None = None, required: list | None = None) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties or {}, "required": required or []}}


SCHEMAS = {
    "matter_status": _schema(
        "matter_status",
        "Status of the user's case folder (Corpus Open matter): documents by kind, labeled facts, open "
        "deadlines, unverified authorities. Read-only."),
    "matter_fact_add": _schema(
        "matter_fact_add",
        "Record one assertion about the case with exactly one epistemic label and the documents that "
        "support it ('rel_path@locator', e.g. 'Evidence/lease.pdf@p. 2'). An assertion with no recognized "
        "source is stored and shown as UNSUPPORTED. Never upgrade a label without a new source.",
        {"text": {"type": "string"}, "status": {"type": "string", "enum": LABELS},
         "sources": {"type": "array", "items": {"type": "string"}},
         "directed_by": {"type": "string", "description": "who asked for this fact to be recorded"}},
        ["text", "status"]),
    "matter_fact_list": _schema(
        "matter_fact_list", "List the case's assertions with labels and sources, optionally one label only.",
        {"status": {"type": "string", "enum": LABELS}}),
    "matter_deadline_list": _schema(
        "matter_deadline_list",
        "List the case's deadlines with due date, overdue flag, confidence (hard / working-estimate) and "
        "verification status. Open ones only unless all is true.",
        {"all": {"type": "boolean"}}),
    "matter_trigger_list": _schema(
        "matter_trigger_list",
        "List deadlines that wait on an event and have no date yet (for example 'answer due 21 days after "
        "service').",
        {"all": {"type": "boolean"}}),
    "matter_trigger_fire": _schema(
        "matter_trigger_fire",
        "Record that the event a waiting trigger waits on happened on a date, and register the computed "
        "deadline (stored as a working estimate, never as checked). Use dry_run first to show the date.",
        {"trigger_id": {"type": "string"}, "on": {"type": "string", "description": "YYYY-MM-DD"},
         "service": {"type": "string", "description": "how the triggering paper was served, if the rule asks"},
         "sources": {"type": "array", "items": {"type": "string"}},
         "reason": {"type": "string"}, "dry_run": {"type": "boolean"},
         "directed_by": {"type": "string"}},
        ["trigger_id", "on"]),
    "matter_packet_render": _schema(
        "matter_packet_render",
        "Render the counsel packet a lawyer can evaluate quickly: 30s, 3min or 15min layer. Always carries "
        "adverse facts and a not-legal-advice line. Read-only; returns Markdown.",
        {"layer": {"type": "string", "enum": ["30s", "3min", "15min"]}}),
    "matter_brief_check": _schema(
        "matter_brief_check",
        "Check a draft brief or complaint inside the case folder (Markdown, text, Word .docx or PDF): every "
        "record cite, pleading cite, authority, quotation and fact tag is tied to a hash-verified document "
        "or flagged. Each case cite is also checked on CourtListener with this plugin's key: only the bare "
        "citations are sent, in at most one request; a cite with no answer is marked NOT VERIFIED with the "
        "reason. offline=true, or citation_guard local, sends nothing. Never writes the store. Tied to a "
        "source or found on CourtListener is not legal correctness.",
        {"path": {"type": "string"}, "mode": {"type": "string", "enum": ["brief", "complaint"]},
         "offline": {"type": "boolean"}},
        ["path"]),
    "matter_rules_show": _schema(
        "matter_rules_show",
        "Text of a court rule from a rules pack attached to the case (e.g. 'Fed. R. Civ. P. 12(b)(6)', "
        "'L.U.Civ.R. 7(b)(4)'), with its version date. Derived text, not the official copy. No network.",
        {"cite": {"type": "string"}, "pack": {"type": "string"}}, ["cite"]),
    "matter_cite_check": _schema(
        "matter_cite_check",
        "Check every citation in a text or in a draft file inside the case folder: case cites against "
        "CourtListener, statute and code cites against Corpus, plus what the case file's own authority "
        "ledger says. Sends only the bare cites (case cites only once a CourtListener key is set), and not "
        "for a check a person turned off for the case. "
        "Run it before any citation goes into a filing or a letter.",
        {"text": {"type": "string"}, "path": {"type": "string"}}),
    "matter_verify": _schema(
        "matter_verify",
        "Re-hash every registered document in the case folder; report any that changed or went missing."),
    "matter_ingest_path": _schema(
        "matter_ingest_path",
        "Hash and register one file or folder inside the case folder so facts can cite it, for example "
        "'Filings' or 'Evidence/lease.pdf' (not the case folder itself). A registered file is a source "
        "and can no longer be edited, so leave working drafts unregistered. Sources are never modified. "
        "The reply counts added, skipped (already registered) and excluded files; notes says why each "
        "excluded file was left out, and recorded_as is the case-relative name facts cite it by.",
        {"path": {"type": "string"}}, ["path"]),
}


def _ok(result) -> str:
    return json.dumps({"ok": True, "result": result}, ensure_ascii=False)


def _err(e: Exception) -> str:
    return json.dumps({"ok": False, "error": str(e) or type(e).__name__}, ensure_ascii=False)


class Tools:
    """Handlers bound to a settings reader so each call sees the current case folder."""

    def __init__(self, settings, guard_runner, courtlistener_token=None):
        self.settings = settings  # callable: () -> dict of plugin settings
        self.guard_runner = guard_runner  # callable: (text, matter_dir) -> guard.Check
        self.courtlistener_token = courtlistener_token  # callable: () -> the key from Hermes's settings, or None

    def _dir(self) -> str:
        return resolve_matter(self.settings().get("matter_dir"))

    def _by(self, args) -> str:
        # The work is the Hermes agent's; directed_by names the person who asked for it.
        return store.attribution("agent:hermes", args.get("directed_by"))

    @contextlib.contextmanager
    def _conn(self):
        md = self._dir()
        conn = store.connect(md)
        try:
            yield conn, md
        finally:
            conn.close()

    def handler(self, name: str):
        fn = getattr(self, name)

        def run(args: dict | None = None, **kwargs) -> str:
            try:
                return _ok(fn(args or {}))
            except Exception as e:  # noqa: BLE001 - Hermes handlers must never raise
                return _err(e)
        run.__name__ = name
        return run

    # -- read ----------------------------------------------------------------------------------
    def matter_status(self, args):
        with self._conn() as (conn, md):
            return pk.status_report(conn, md)

    def matter_fact_list(self, args):
        from ..matterkit import facts
        with self._conn() as (conn, _md):
            rows = facts.list_(conn, args.get("status"))
        return [{"id": f["id"], "status": f["status"], "text": f["text"], "supported": not f["unsupported"],
                 "sources": [x["rel_path"] + (f"@{x['locator']}" if x["locator"] else "") for x in f["sources"]]}
                for f in rows]

    def matter_deadline_list(self, args):
        from ..matterkit import deadlines as dl
        with self._conn() as (conn, _md):
            return [dict(r, overdue=dl.is_overdue(r["due_date"], r["state"]))
                    for r in dl.select(conn, include_closed=bool(args.get("all")))]

    def matter_trigger_list(self, args):
        from ..matterkit import triggers
        with self._conn() as (conn, _md):
            return [dict(r) for r in triggers.select(conn, include_closed=bool(args.get("all")))]

    def matter_packet_render(self, args):
        with self._conn() as (conn, md):
            return pk.packet(conn, md, layer=args.get("layer") or "3min")

    def matter_brief_check(self, args):
        from ..matterkit import briefcheck
        md = self._dir()
        s = self.settings()
        lookup = s.get("citation_guard") == "lookup"
        # The key comes from the plugin (Hermes's settings, catalog rule 11), never from a bare
        # environment read here: "" when there is none.
        token = (self.courtlistener_token() or "") if lookup and self.courtlistener_token else ""
        cites = briefcheck.CiteCheck(
            caller_id=str(s.get("caller_id") or "hermes"), offline=not lookup or bool(args.get("offline")),
            offline_why=("" if lookup else f"citation_guard is {s.get('citation_guard')} in this plugin's settings, "
                                           "so nothing leaves this computer"),
            token=token, key_hint=BRIEF_KEY_HINT)
        with contextlib.redirect_stdout(io.StringIO()):
            report = briefcheck.check(md, draft_path(md, args.get("path", "")), use_pdftotext=True,
                                      mode=args.get("mode") or "brief", cites=cites)
        return {"exit": report.exit_code, "summary": report.summary(), "report": report.render()}

    def matter_rules_show(self, args):
        from ..matterkit import rules
        code, text = rules.show(self._dir(), args.get("cite", ""), args.get("pack"))
        if code == 1:
            raise MatterError(text)
        return text

    def matter_verify(self, args):
        with self._conn() as (conn, md):
            ok, bad, missing, failures = ing.verify_integrity(conn, md)
        return {"ok": ok, "changed": bad, "missing": missing, "failures": [list(f) for f in failures]}

    def matter_cite_check(self, args):
        from . import guard
        md = None
        try:
            md = self._dir()
        except MatterError:
            md = None  # a cite check still runs locally without a case folder
        text = args.get("text") or ""
        if args.get("path"):
            if md is None:
                raise MatterError("path needs a case folder; pass text instead or set matter_dir")
            p = draft_path(md, args["path"])
            if p.lower().endswith((".docx", ".pdf", ".doc")):
                raise MatterError("cite check reads Markdown or text; pass the .md copy or the text itself")
            with open(p, "rb") as f:
                raw = f.read()
            if b"\0" in raw:
                raise MatterError("this file has binary content; pass the .md copy or the text itself")
            text = raw.decode("utf-8", "replace")
        if not text.strip():
            raise MatterError("give text or a path")
        check = self.guard_runner(text, md)
        return {"problems": len(check.problems), "cites": len(check.rows),
                "report": guard.render(check, footer=False)}

    # -- write (local store only) ---------------------------------------------------------------
    def matter_fact_add(self, args):
        from ..matterkit import facts
        with self._conn() as (conn, md):
            r = facts.add(conn, md, args["text"], args.get("status", "UNKNOWN"),
                          args.get("sources") or [], self._by(args))
        return {"id": r["id"], "status": r["status"], "unsupported": bool(r["unsupported"]),
                "unrecognized_sources": r["unrecognized"], "notes": r["notes"]}

    def matter_trigger_fire(self, args):
        from ..matterkit import triggers
        md = self._dir()
        sources = args.get("sources") or []
        if not isinstance(sources, list):
            raise MatterError("sources must be a list of 'rel_path@locator' strings")
        with contextlib.closing(store.connect(md)) as conn:
            did, due, steps = triggers.fire(conn, md, args.get("trigger_id"), args.get("on"),
                                            args.get("service"), sources, args.get("reason"), self._by(args),
                                            dry_run=bool(args.get("dry_run")))
        return {"deadline_id": did, "due": due.isoformat(), "dry_run": did is None,
                "note": "computed, not checked: a working estimate until a person confirms it",
                "steps": steps}

    def matter_ingest_path(self, args):
        md = self._dir()
        raw = args.get("path", "")
        if _is_case_root(md, raw):
            # "register my files" often arrives as "." or the folder itself, which the kit refuses as
            # "invalid matter-relative path: '.'"; say what to name instead.
            raise MatterError("That is the case folder itself, which can't be registered in one call. Name a "
                              "folder inside it (for example Filings or Evidence) or one file.")
        cand = inside(md, raw)
        # The kit prints why it skipped a path and counts nothing, which reads to the agent as done.
        if os.path.islink(cand) and not os.path.exists(cand):
            raise MatterError(f"{raw} is a link to a file that is no longer there. Register the file itself.")
        sub = ing.nested_matter_root(cand, md)
        if sub:
            raise MatterError(f"{os.path.relpath(sub, md)} is its own case (a subcase). Register its papers "
                              "from that case, not this one.")
        if (os.path.islink(cand) or not os.path.isdir(cand)) and os.path.lexists(cand) and ing._links_out(cand, md):
            # A link at the file itself or at a folder on the way: either way the bytes live elsewhere.
            how = "is a link" if os.path.islink(cand) else "leads through a linked folder"
            raise MatterError(f"{raw} {how} to a file outside the case folder (or into a subcase), so it is "
                              "not registered. Copy the file into the case folder instead.")
        if not os.path.exists(cand):
            raise MatterError(f"There is no {raw} in the case folder. Name a folder that is there (for "
                              "example Filings or Evidence) or one file.")
        said = io.StringIO()
        with contextlib.redirect_stdout(said), contextlib.closing(store.connect(md)) as conn:
            added, skipped, excluded = ing.ingest_paths(conn, md, [cand])
            recorded_as = ing.recorded_name(conn, md, cand)
        # The kit's "! ..." lines say why a file was left out; without them a skip reads as done.
        # recorded_as is the name facts cite: a path through a linked folder inside the case is
        # recorded under the name it resolves to, and a file already registered under another
        # spelling keeps that stored name, so either may differ from the one asked for.
        return {"added": added, "skipped": skipped, "excluded": excluded,
                "notes": ing.case_relative_notes(said.getvalue(), md),
                "recorded_as": recorded_as}

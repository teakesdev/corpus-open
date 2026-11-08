"""Corpus Open for Hermes Agent: the matter kit as a native Hermes plugin.

What a plugin adds that a skill or an MCP server can't:
- a citation guard on every reply (transform_llm_output): each case, statute and code cite the
  agent writes is listed under the reply with what CourtListener, Corpus and the case file say
  about it, so a fabricated cite is flagged before anyone copies it into a filing;
- a deadline brief at the start of each session (pre_llm_call on the first turn);
- a source guard (pre_tool_call): Hermes's file tools can't change a registered source document
  or the case store, and a command that looks like it would goes to the person's approval;
- /matter, a slash command for the case's status, deadlines, packet and cite checks;
- the matter tools, reading the case folder chosen in Hermes's own settings form, with tokens
  read through Hermes's secret store.

Nothing here files, sends or contacts anyone. Network calls happen only for cite checks, only
with the bare cites, and only after a person grants them in the case folder (RFC 0001).
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import io
import json
import os
import shutil

from . import guard as _guard
from . import quote_guard as _quote_guard
from . import source_guard as _source_guard
from .tools import SCHEMAS, TOOLSET, MatterError, Tools, resolve_matter

PLUGIN_ID = "corpus-open"
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_PATH = os.path.join(KIT, "skills", "matter-kit", "SKILL.md")
DEMO_SOURCE = os.path.join(KIT, "demo-matter")
DEMO_DECLARATION = "Filings/12-declaration_synthetic.md"
DEMO_DIR = "~/cases/corpus-open-demo"
USAGE = ("Usage: /matter [status] | deadlines | packet [30s|3min|15min] | check <draft.md> | grant | "
         "init <folder> | demo [folder]")
GUARD_MODES = ("lookup", "local", "off")
DEFAULTS = {
    "matter_dir": "",
    "citation_guard": "lookup",
    "caller_id": "hermes",
    "deadline_window_days": 14,
    "max_statute_checks": 5,
    "source_guard": True,
    "quote_guard": True,
}
OFF = (False, "off", "false", "no", "0", 0)
SECRET_ENV = {"courtlistener_token": "COURTLISTENER_TOKEN", "corpus_api_key": "CORPUS_API_KEY"}


def _secret(name: str) -> str | None:
    """A credential through Hermes's secret store; None outside Hermes or when unset."""
    try:
        from agent.secret_scope import get_secret
    except ImportError:
        return None
    try:
        value = get_secret(name)
    except Exception:  # noqa: BLE001 - an unscoped read under multiplexing: treat as unset
        return None
    return (value or "").strip() or None


class Plugin:
    def __init__(self, ctx, secret=_secret, today=None):
        self.ctx = ctx
        self.secret = secret
        self.today = today or _dt.date.today
        self.tools = Tools(self.settings, self.run_guard)

    # -- settings -------------------------------------------------------------------------------
    def settings(self) -> dict:
        out = dict(DEFAULTS)
        for key in DEFAULTS:
            try:
                value = self.ctx.get_config(key, None)
            except Exception:  # noqa: BLE001
                value = None
            if value is not None and value != "":
                out[key] = value
        if out["citation_guard"] not in GUARD_MODES:
            out["citation_guard"] = "lookup"
        for key in ("deadline_window_days", "max_statute_checks"):
            try:
                out[key] = max(0, int(out[key]))
            except (TypeError, ValueError):
                out[key] = DEFAULTS[key]
        for key in ("source_guard", "quote_guard"):
            value = out[key]
            out[key] = (value.strip().lower() if isinstance(value, str) else value) not in OFF
        return out

    def _matter_or_none(self) -> str | None:
        try:
            return resolve_matter(self.settings().get("matter_dir"))
        except MatterError:
            return None

    # -- source guard -------------------------------------------------------------------------------
    def on_tool_call(self, tool_name: str = "", args=None, task_id: str = "", **kwargs):
        """pre_tool_call. Never raises: Hermes blocks every tool call when this hook raises."""
        matter_dir, settings = None, None
        try:
            settings = self.settings()
            matter_dir = settings.get("matter_dir") or None
            if settings["source_guard"]:
                hit = _source_guard.check(tool_name, args, matter_dir=matter_dir)
                if hit:
                    return hit
        except Exception:  # noqa: BLE001
            hit = _source_guard.fallback(tool_name, args, matter_dir)
            if hit:
                return hit
        try:  # quote guard: a quotation or page cite the record doesn't hold may not reach a draft
            if settings and settings["quote_guard"]:
                return _quote_guard.check(tool_name, args, matter_dir=matter_dir)
        except Exception:  # noqa: BLE001 - when the check itself fails, the write goes through
            return None
        return None

    # -- citation guard -------------------------------------------------------------------------
    def run_guard(self, text: str, matter_dir: str | None) -> _guard.Check:
        s = self.settings()
        return _guard.check_text(
            text, matter_dir=matter_dir, caller_id=str(s["caller_id"]),
            lookup=s["citation_guard"] == "lookup",
            courtlistener_token=self.courtlistener_token(),
            corpus_api_key=self.secret(SECRET_ENV["corpus_api_key"]),
            max_statutes=s["max_statute_checks"])

    def courtlistener_token(self) -> str | None:
        """Hermes's secret first; else the key `matter.py courtlistener on` / `courtlistener key` saved
        (or COURTLISTENER_TOKEN in the environment), so the one switch also works inside Hermes."""
        token = self.secret(SECRET_ENV["courtlistener_token"])
        if token:
            return token
        from ..matterkit import credentials
        return credentials.courtlistener_token() or None

    def on_reply(self, response_text: str = "", **kwargs) -> str | None:
        """transform_llm_output: append the citation check under a reply that cites anything."""
        try:
            if self.settings()["citation_guard"] == "off" or not response_text:
                return None
            if "Citation check" in response_text and "Checked by Corpus Open" in response_text:
                return None  # already carries one (a resumed or replayed reply)
            check = self.run_guard(response_text, self._matter_or_none())
            block = _guard.render(check)
            return response_text + "\n" + block if block else None
        except Exception:  # noqa: BLE001 - never hold up a reply; the reply goes out unchanged
            return None

    # -- deadline brief -------------------------------------------------------------------------
    def deadline_brief(self) -> str:
        md = self._matter_or_none()
        if md is None:
            return ""
        from ..matterkit import deadlines as dl
        from ..matterkit import store, triggers
        conn = store.connect(md)
        try:
            rows, waiting = list(dl.select(conn)), len(triggers.select(conn))
        finally:
            conn.close()
        today = self.today()
        window = self.settings()["deadline_window_days"]
        overdue, soon = [], []
        for r in rows:
            due = dl.parse_date(r["due_date"])
            if due is None:
                continue
            label = (f"{r['label']}: {due.isoformat()} ({r['confidence'] or 'working-estimate'}, "
                     f"{r['verification_status'] or 'unverified'})")
            if due < today:
                overdue.append(label + f", {(today - due).days} days ago")
            elif (due - today).days <= window:
                soon.append(label + f", in {(due - today).days} days")
        if not (overdue or soon or waiting):
            return ""
        lines = [f"[Corpus Open] The user's case folder is {md}. As of {today.isoformat()}:"]
        if overdue:
            lines.append("OVERDUE (still open): " + "; ".join(overdue))
        if soon:
            lines.append(f"Due within {window} days: " + "; ".join(soon))
        if waiting:
            lines.append(f"{waiting} deadline(s) wait on an event that has not been recorded yet "
                         "(matter_trigger_list).")
        lines.append("Mention overdue and upcoming deadlines to the user once, briefly, at the start of "
                     "your reply. Dates marked working-estimate or unverified are not confirmed.")
        return "\n".join(lines)

    def on_turn(self, is_first_turn: bool = False, **kwargs):
        """pre_llm_call: on a session's first turn, hand the agent the case's deadlines."""
        if not is_first_turn:
            return None
        try:
            text = self.deadline_brief()
        except Exception:  # noqa: BLE001
            return None
        return {"context": text} if text else None

    # -- /matter --------------------------------------------------------------------------------
    def command(self, raw_args: str = "", **kwargs) -> str:
        parts = (raw_args or "").strip().split(None, 1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""
        try:
            if sub in ("", "status"):
                return self._unwrap(self.tools.handler("matter_status")({})) + "\n\n" + self._deadline_text()
            if sub == "deadlines":
                return self._deadline_text()
            if sub == "packet":
                return self._unwrap(self.tools.handler("matter_packet_render")({"layer": rest or "3min"}))
            if sub == "check":
                if not rest:
                    return "Usage: /matter check <draft.md inside the case folder>"
                res = self.tools.matter_cite_check({"path": rest})
                return res["report"] or "No citations found."
            if sub == "grant":
                return self._grant_text()
            if sub == "init":
                return self._init(rest)
            if sub == "demo":
                return self._demo(rest or DEMO_DIR)
            return USAGE
        except MatterError as e:
            return str(e)
        except Exception as e:  # noqa: BLE001
            return f"/matter failed: {e}"

    def _grant_text(self) -> str:
        s = self.settings()
        md, caller = s["matter_dir"] or None, s["caller_id"]
        return ("A person turns on cite checks once per case folder, in a terminal (the kit refuses a "
                "grant an agent writes). These work from any folder:\n"
                f"  {_guard.grant_command('case', md, caller)}\n"
                "    case cites (CourtListener): asks you to type yes, then for your free key, which this "
                "plugin reads too (a COURTLISTENER_TOKEN in this plugin's settings is used first)\n"
                f"  {_guard.grant_command('statute', md, caller)}\n"
                "    statute and code cites (Corpus): asks you to type ACTIVATE")

    def _select(self, folder: str) -> str:
        """Point the plugin at folder. Returns a sentence saying whether that happened."""
        setter = getattr(self.ctx, "set_config", None)
        if callable(setter):
            setter("matter_dir", folder)
            return f"The plugin now uses {folder} as your case folder."
        return (f"Set the case folder to {folder} in Settings > Plugins > corpus-open "
                "(this Hermes can't save it from here).")

    def _init(self, rest: str) -> str:
        if not rest:
            return "Usage: /matter init <folder> (made if missing; files already there are kept)"
        from ..matterkit import cases
        folder = os.path.abspath(os.path.expanduser(rest))
        with contextlib.redirect_stdout(io.StringIO()):
            docs = cases.init(folder)
        return (f"Case folder ready: {folder} ({docs} documents registered). " + self._select(folder) +
                " Put files in its folders, then ask me to register them (matter_ingest_path).")

    def _demo(self, rest: str) -> str:
        from ..matterkit import cases, store
        from ..matterkit import deadlines as dl
        from ..matterkit import ingest as ing
        folder = os.path.abspath(os.path.expanduser(rest))
        if os.path.exists(folder) and (not os.path.isdir(folder) or os.listdir(folder)):
            return (f"{folder} already has files, so the demo won't write there. "
                    "Pick another folder: /matter demo <new folder>")
        shutil.copytree(DEMO_SOURCE, folder, dirs_exist_ok=True)
        today = self.today()
        with contextlib.redirect_stdout(io.StringIO()):
            cases.init(folder)
            with contextlib.closing(store.connect(folder)) as conn:
                ing.ingest_paths(conn, folder, [folder])
                dl.add(conn, "Answer to complaint (demo)", (today - _dt.timedelta(days=3)).isoformat(),
                       "plugin:corpus-open-demo", rule="synthetic demo date")
                dl.add(conn, "Initial disclosures (demo)", (today + _dt.timedelta(days=7)).isoformat(),
                       "plugin:corpus-open-demo", rule="synthetic demo date")
        with open(os.path.join(folder, ".matter", "docket.json"), "w", encoding="utf-8") as f:
            json.dump({"entries": {"12": DEMO_DECLARATION}}, f, indent=2)
        return (f"Made a synthetic demo case at {folder}: a made-up complaint, evidence memo and a "
                "three-page declaration filed as Doc. 12, registered and hashed, and two made-up deadlines "
                "(one overdue). Nothing in it is real. " + self._select(folder) + " Try /matter, ask me "
                "what's due, or ask me to quote Doc. 12 in a draft: the quote guard checks every "
                "quotation against the record before it is written.")

    def _deadline_text(self) -> str:
        md = resolve_matter(self.settings().get("matter_dir"))
        from ..matterkit import deadlines as dl
        from ..matterkit import store
        conn = store.connect(md)
        try:
            rows = dl.select(conn)
        finally:
            conn.close()
        if not rows:
            return "No open deadlines."
        out = ["Open deadlines:"]
        for r in rows:
            flag = " **OVERDUE**" if dl.is_overdue(r["due_date"], r["state"], today=self.today()) else ""
            out.append(f"- {r['due_date']} · {r['label']}{flag} ({r['confidence']}, {r['verification_status']})")
        return "\n".join(out)

    @staticmethod
    def _unwrap(payload: str) -> str:
        import json
        d = json.loads(payload)
        if not d.get("ok"):
            raise MatterError(d.get("error", "failed"))
        r = d["result"]
        return r if isinstance(r, str) else json.dumps(r, indent=1)


def register(ctx, *, secret=_secret, today=None) -> Plugin:
    plugin = Plugin(ctx, secret=secret, today=today)
    for name, schema in SCHEMAS.items():
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=plugin.tools.handler(name),
                          description=schema["description"].split(". ")[0])
    ctx.register_hook("transform_llm_output", plugin.on_reply)
    ctx.register_hook("pre_llm_call", plugin.on_turn)
    ctx.register_hook("pre_tool_call", plugin.on_tool_call)
    skill = getattr(ctx, "register_skill", None)
    if callable(skill) and os.path.isfile(SKILL_PATH):
        try:
            skill("matter-kit", SKILL_PATH,
                  description="How to keep a case folder: hashed sources, labeled facts with sources, "
                              "deadlines, counsel packets")
        except Exception:  # noqa: BLE001 - a skill that won't register must not stop the tools loading
            pass
    ctx.register_command("matter", plugin.command,
                         description="Your case: status, deadlines, counsel packet, cite check, new case, demo",
                         args_hint="[status|deadlines|packet|check|grant|init|demo]")
    return plugin

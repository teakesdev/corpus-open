"""Corpus Open for Hermes Agent: the matter kit as a native Hermes plugin.

What a plugin adds that a skill or an MCP server can't:
- a citation guard on every reply (transform_llm_output): each case, statute and code cite the
  agent writes is listed under the reply with what CourtListener, Corpus and the case file say
  about it, so a fabricated cite is flagged before anyone copies it into a filing;
- a deadline brief at the start of each session (pre_llm_call on the first turn);
- Deadline Watch: /matter watch on installs a script that Hermes's cron runs each morning with no
  model, so overdue and upcoming dates and changed files reach Telegram exactly as the kit wrote
  them (the message carries the case folder's name, deadline names, dates and labels, and counts
  of changed files: never file names, paths or contents); /matter calendar exports the deadlines
  with reminders;
- a source guard (pre_tool_call): Hermes's file tools can't change a registered source document
  or the case store, and a command that looks like it would goes to the person's approval;
- /matter, a slash command for the case's status, deadlines, packet and cite checks;
- the matter tools, reading the case folder chosen in Hermes's own settings form, with tokens
  read through Hermes's secret store.

Nothing here files or contacts anyone. Network calls happen only for cite checks, only with the
bare cites, and only after a person grants them in the case folder (RFC 0001). Deadline Watch
messages leave only through a cron job a person creates, and each one is logged in the case.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import io
import json
import os
import re
import shlex
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
         "init <folder> | demo [folder] | watch [on [HH:MM] | off] | calendar")
WATCH_MARK = "# corpus-open deadline watch: written by /matter watch on"
WATCH_USAGE = ("Usage: /matter watch (the check, now) | /matter watch on [HH:MM] (each morning, through Hermes "
               "cron) | /matter watch off")
_HHMM = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
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
CHECKED_MEMORY = 256  # replies this process appended a block to, remembered to skip a replay
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


def _digest(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest()


class Plugin:
    def __init__(self, ctx, secret=_secret, today=None):
        self.ctx = ctx
        self.secret = secret
        self.today = today or _dt.date.today
        self.tools = Tools(self.settings, self.run_guard)
        self._checked: dict = {}  # sha256 of each reply on_reply returned, oldest first

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
            if _digest(response_text) in self._checked:
                # Exactly a reply this plugin already returned (a replay), so its block is the
                # check of this very text. Words in the reply never count: the model can write them.
                return None
            check = self.run_guard(response_text, self._matter_or_none())
            block = _guard.render(check)
            if not block:
                return None
            out = response_text + "\n" + block
            self._checked[_digest(out)] = None
            while len(self._checked) > CHECKED_MEMORY:
                del self._checked[next(iter(self._checked))]
            return out
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
            if sub == "watch":
                words = rest.split()
                verb = words[0].lower() if words else ""
                if verb == "on":
                    return self._watch_on(words[1] if len(words) > 1 else "08:00")
                if verb == "off":
                    return self._watch_off()
                if verb:
                    return WATCH_USAGE
                return self._watch_text()
            if sub == "calendar":
                return self._calendar()
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

    # -- Deadline Watch -------------------------------------------------------------------------
    def _watch_text(self) -> str:
        from ..matterkit import watch
        md = resolve_matter(self.settings().get("matter_dir"))
        text = watch.render(watch.report(md, today=self.today()))
        return text or ("Nothing to report today: no open deadline is overdue or due in 0, 1, 3 or 7 "
                        "days, and every registered file matches its hash.")

    def _watch_on(self, when: str) -> str:
        m = _HHMM.fullmatch(when.strip())
        if not m:
            return "Usage: /matter watch on [HH:MM] (24-hour time, for example 07:30; default 08:00)"
        md = resolve_matter(self.settings().get("matter_dir"))
        case = os.path.basename(md)
        scripts, name, path = self._watch_script(md)
        if os.path.exists(path) and not _ours(path):
            return (f"{path} already exists and this plugin didn't write it, so it was left alone. "
                    "Move it, then run /matter watch on again.")
        os.makedirs(scripts, exist_ok=True)
        body = (f"{WATCH_MARK}\n"
                "# Hermes's cron runs this with no model; it prints the case's deadline news, or nothing.\n"
                "import sys\n"
                f"sys.path.insert(0, {KIT!r})\n"
                "from matterkit import watch\n"
                f"watch.cron_main({md!r})\n")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, path)
        hour, minute = int(m.group(1)), int(m.group(2))
        cmd = " ".join(["hermes cron create", shlex.quote(f"{minute} {hour} * * *"), "--no-agent",
                        "--script", shlex.quote(name), "--deliver telegram",
                        "--name", shlex.quote(f"Case deadlines: {case}")])
        return ("Wrote the morning check for " + md + " to " + path + ".\n"
                "Run this once in a terminal to schedule it (Hermes asks a person to create cron jobs):\n"
                f"  {cmd}\n"
                f"Each day at {hour:02d}:{minute:02d} it sends overdue deadlines, ones due today, tomorrow or "
                "in 3 or 7 days, and a count of changed or missing case files. On a day with none of "
                "those it sends nothing. No model runs, so the dates arrive exactly as the case records "
                "them.\n"
                "--deliver telegram uses your Telegram home channel (/sethome). Use --deliver local to "
                "keep the output on this computer instead. Try it now with: hermes cron run <job id>\n"
                "Each message that goes out is logged, hash only, in the case's .matter/egress.jsonl. "
                f"It carries the case folder's name ({case}), deadline names, dates and labels, and counts "
                "of changed files; never file names, paths or contents. /matter watch off removes it.")

    @staticmethod
    def _watch_script(md: str) -> tuple[str, str, str]:
        """(scripts folder, script name, full path) for the case's watch script."""
        slug = (re.sub(r"[^a-z0-9]+", "-", os.path.basename(md).lower()).strip("-")[:40] or "case")
        name = f"corpus-open-watch-{slug}-{hashlib.sha256(md.encode()).hexdigest()[:6]}.py"
        scripts = os.path.join(_hermes_home(), "scripts")
        return scripts, name, os.path.join(scripts, name)

    def _watch_off(self) -> str:
        md = resolve_matter(self.settings().get("matter_dir"))
        _scripts, name, path = self._watch_script(md)
        steps = ("To remove the scheduled job too, find its id with `hermes cron list` (the job that runs "
                 f"{name}), then run `hermes cron remove <job id>`. Until then Hermes reports the missing "
                 "script as a failed run each morning.")
        if not os.path.lexists(path):
            return f"No watch script for {md} in Hermes's scripts folder. " + steps
        if not _ours(path):
            return (f"{path} exists but this plugin didn't write it, so it was left alone. " + steps)
        os.remove(path)
        return f"Removed the morning check's script ({path}). " + steps

    def _calendar(self) -> str:
        from ..matterkit import calendar_export
        md = resolve_matter(self.settings().get("matter_dir"))
        path, count = calendar_export.write(md)
        if not count:
            return f"No open deadlines with a date, so {path} has no events."
        return (f"Wrote {count} deadline{'' if count == 1 else 's'} to {path}. Open it to add them to "
                "your calendar, with reminders 7, 3 and 1 days before. Titles keep each date's "
                "confidence label. Exporting again later updates the same events instead of adding "
                "copies. Deadlines still waiting on an event have no date yet and are left out.")

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


def _ours(path: str) -> bool:
    """A regular file whose first line is the watch script's marker (never through a symlink)."""
    if os.path.islink(path) or not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.readline().rstrip("\r\n") == WATCH_MARK


def _hermes_home() -> str:
    """Hermes's home folder (its profile's, inside Hermes), where cron looks for scripts."""
    try:
        from hermes_constants import get_hermes_home
        return str(get_hermes_home())
    except Exception:  # noqa: BLE001 - outside Hermes, or an older Hermes
        return os.path.expanduser("~/.hermes")


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
                         description="Your case: status, deadlines, morning watch, calendar, counsel packet, cite "
                                     "check, new case, demo",
                         args_hint="[status|deadlines|watch|calendar|packet|check|grant|init|demo]")
    return plugin

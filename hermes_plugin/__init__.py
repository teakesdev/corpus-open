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
from . import terminal as _terminal
from .tools import SCHEMAS, TOOLSET, MatterError, Tools, draft_path, resolve_matter

PLUGIN_ID = "corpus-open"
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_PATH = os.path.join(KIT, "skills", "matter-kit", "SKILL.md")
DEMO_SOURCE = os.path.join(KIT, "demo-matter")
DEMO_DECLARATION = "Filings/12-declaration_synthetic.md"
DEMO_COMPLAINT = "Filings/complaint_synthetic.md"
DEMO_DRAFT = "Drafts/response_draft_synthetic.md"  # a working draft: never registered as a source
DEMO_DIR = "~/cases/corpus-open-demo"
DEMO_BY = "plugin:corpus-open-demo"
# (text, label, sources). The last has no source on purpose: it shows as UNSUPPORTED, and it is the
# same claim the demo draft's made-up quotation asserts.
DEMO_FACTS = (
    ("Demo Corp.'s HR manager swore that Plaintiff received no written warnings before January 5, 2026.",
     "DOCUMENTED FACT", (DEMO_DECLARATION + "@p. 2 ¶ 3",)),
    ("The HR manager swore that after the January 5 meeting she was told to mark the personnel file "
     "\"voluntary resignation.\"", "DOCUMENTED FACT", (DEMO_DECLARATION + "@p. 3 ¶ 6",)),
    ("Plaintiff alleges wrongful termination on January 5, 2026.", "ALLEGATION", (DEMO_COMPLAINT,)),
    ("The plant manager ordered the file changed to hide the reason for the firing.", "HYPOTHESIS", ()),
)
WELCOME = ("Corpus Open keeps a court case in one folder on this computer and checks what the AI says "
           "about it against your own documents.")
DEMO_WAY = "- /matter demo: a made-up case to try things on. Nothing in it is real."
THEN_ASK = ('After that, ask in plain words: "what\'s due in my case?", "register my files", '
            '"check my draft".')
NO_CASE_HINT = ("[Corpus Open] No case folder is set up yet, so the case tools (matter_*) will not work "
                "and cites in replies are listed but not looked up. If the user brings up a court case "
                "or legal papers, tell them once: " + _guard.HOW_TO_START + " Otherwise do not mention "
                "this.")
UNCONFIRMED = ("Dates marked working-estimate, unverified or secondary-source are not confirmed. Check "
               "them against the rules and the court's orders.")
_QUOTE_ORDER = (_quote_guard.quotecheck.NOT_FOUND, _quote_guard.quotecheck.WRONG_PAGE,
                _quote_guard.quotecheck.CANT_CHECK, _quote_guard.quotecheck.EXACT)
_QUOTE_MARK = {_quote_guard.quotecheck.EXACT: "✓", _quote_guard.quotecheck.CANT_CHECK: "·"}
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
        """pre_llm_call: on a session's first turn, hand the agent the case's deadlines; with no case
        yet, tell it so, and how the user starts one."""
        if not is_first_turn:
            return None
        try:
            text = NO_CASE_HINT if self._matter_or_none() is None else self.deadline_brief()
        except Exception:  # noqa: BLE001
            return None
        return {"context": text} if text else None

    # -- /matter --------------------------------------------------------------------------------
    def command(self, raw_args: str = "", **kwargs) -> str:
        """/matter. Hermes's classic terminal prints a command's answer as typed, so there the
        Markdown marks come out; every other surface gets the Markdown."""
        out = self._answer(raw_args)
        return _terminal.plain_text(out) if kwargs.get("platform") in _terminal.PLAIN_PLATFORMS else out

    def _answer(self, raw_args: str = "") -> str:
        parts = (raw_args or "").strip().split(None, 1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""
        try:
            if sub in ("", "status"):
                md = self._matter_or_none()
                if md is None:
                    return self._welcome()
                status = self._unwrap(self.tools.handler("matter_status")({}))
                # The heading names the folder: ~/cases/x reads better than the whole home path.
                return status.replace(md, _terminal.tilde(md), 1) + "\n\n" + self._deadline_text()
            if sub == "deadlines":
                return self._deadline_text()
            if sub == "packet":
                return self._unwrap(self.tools.handler("matter_packet_render")({"layer": rest or "3min"}))
            if sub == "check":
                if not rest:
                    return "Usage: /matter check <draft.md inside the case folder>"
                return self._check(rest)
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

    def _welcome(self) -> str:
        """What `/matter` says before there is a case: what this is, and the two ways to start."""
        raw = str(self.settings().get("matter_dir") or "").strip()
        if raw:  # a folder is set, but it is not a case yet
            path = os.path.abspath(os.path.expanduser(raw))
            head = f"{path} is not set up as a case folder yet. Two ways to start:"
            ways = [f"- /matter init {path}: set that folder up as your case. Files already there are kept.",
                    DEMO_WAY]
        else:
            head = "No case folder is set up yet. Two ways to start:"
            ways = [DEMO_WAY, "- /matter init ~/cases/my-case: a folder for your own case. Your files stay "
                              "on this computer."]
        return "\n".join([WELCOME, "", head, *ways, "", THEN_ASK])

    def _check(self, rel: str) -> str:
        """/matter check: the draft's cites (the citation guard's block), then its quotations and
        record page cites against the registered documents (the quote guard's check, offline)."""
        md = resolve_matter(self.settings().get("matter_dir"))
        res = self.tools.matter_cite_check({"path": rel})
        parts = [res["report"]] if res["report"] else []
        try:
            with open(draft_path(md, rel), "rb") as f:
                text = f.read().decode("utf-8", "replace")
            quotes = _quote_guard.quotecheck.check_text(md, text, use_pdftotext=_quote_guard.USE_PDFTOTEXT)
        except Exception as e:  # noqa: BLE001 - the cite report above still goes out
            quotes = []
            parts.append(f"_Quotations could not be checked: {_guard._short(e)}_")
        if quotes:
            parts.append(self._quote_text(quotes))
        return "\n\n".join(parts) or "No citations or quotations found."

    @staticmethod
    def _quote_text(results) -> str:
        qc = _quote_guard.quotecheck
        rows = sorted(results, key=lambda r: _QUOTE_ORDER.index(r.verdict) if r.verdict in _QUOTE_ORDER else 9)
        bad = sum(1 for r in rows if r.verdict in qc.PROBLEMS)
        out = [f"**Quotations and page cites: {bad} of {len(rows)} to fix**" if bad else
               f"**Quotations and page cites: {len(rows)}**"]
        for r in rows:
            said = qc.describe(r)
            said = said[len(r.verdict) + 2:] if said.startswith(r.verdict + ": ") else said
            out.append(f"- {_QUOTE_MARK.get(r.verdict, '⚠')} **{r.verdict}**: {said}")
        out.append("_Exact means the words are in the registered document on that page, not that they "
                   "prove the point. Can't check means there was no record text to compare against._")
        return "\n".join(out)

    def _grant_text(self) -> str:
        s = self.settings()
        caller, lead = s["caller_id"], ""
        try:
            md = resolve_matter(s["matter_dir"])
        except MatterError as e:
            # No case yet: say how to get one, and print no line that would fail on this folder.
            md = None
            lead = (f"{e} There is nothing to copy yet: once you have a case, /matter grant prints the "
                    "two lines below with your folder in place of <case folder>.\n\n")
        how = ("This is what they will look like:" if md is None else
               "Copy each line into a terminal; they work from any folder:")
        return (lead +
                "A person turns on cite checks once per case folder, in a terminal (the kit refuses a "
                f"grant an agent writes). {how}\n"
                f"  {_guard.grant_command('case', md, caller)}\n"
                "    case cites (CourtListener): asks you to type yes, then for your free key (get one at "
                f"{_guard.KEY_URL}), which this plugin reads too (a COURTLISTENER_TOKEN in this plugin's "
                "settings is used first)\n"
                f"  {_guard.grant_command('statute', md, caller)}\n"
                "    statute and code cites (Corpus): asks you to type ACTIVATE")

    def _select(self, folder: str) -> str:
        """Point the plugin at folder. Returns a sentence saying whether that happened."""
        setter = getattr(self.ctx, "set_config", None)
        if callable(setter):
            setter("matter_dir", folder)
            return f"The plugin now uses {_terminal.tilde(folder)} as your case folder."
        return (f"Set the case folder to {folder} in Settings > Plugins > corpus-open "
                "(this Hermes can't save it from here).")

    @staticmethod
    def _inside_case(folder: str) -> str | None:
        """The case folder strictly above `folder`, if any. A case inside a case is one the rest of
        the kit treats as a boundary, and selecting it would switch the plugin off the real case.
        Both the path as given and its real location are walked, so a link into a case is caught,
        and "a case" is what resolve_matter accepts: a folder holding `.matter/`."""
        def walk(start: str) -> str | None:
            cur = os.path.dirname(start)
            while True:
                if os.path.isdir(os.path.join(cur, ".matter")):
                    return cur
                up = os.path.dirname(cur)
                if up == cur:
                    return None
                cur = up
        return walk(folder) or walk(os.path.realpath(folder))

    @staticmethod
    def _nested_refusal(folder: str, root: str, retry: str) -> str:
        return (f"{folder} is inside the case at {root}, so it can't be a case of its own. "
                f"Pick a folder outside every case: {retry}")

    def _init(self, rest: str) -> str:
        if not rest:
            return "Usage: /matter init <folder> (made if missing; files already there are kept)"
        from ..matterkit import cases
        folder = os.path.abspath(os.path.expanduser(rest))
        root = self._inside_case(folder)
        if root:
            return self._nested_refusal(folder, root, "/matter init <new folder>")
        with contextlib.redirect_stdout(io.StringIO()):
            docs = cases.init(folder)
        shown = _terminal.tilde(folder)  # for reading; the setting keeps the full path
        return "\n".join([
            f"Case folder ready: {shown} ({docs} documents registered). " + self._select(folder),
            "Next:",
            f"1. Copy your papers into it. Anything filed with the court or sent by the other side goes "
            f"in {os.path.join(shown, 'Filings')}; your proof (letters, photos, records) goes in "
            f"{os.path.join(shown, 'Evidence')}.",
            '2. Say "register my files". Each one is recorded with a fingerprint, so a later change '
            "shows up.",
            '3. Ask "what\'s due in my case?" or "check my draft".'])

    def _demo(self, rest: str) -> str:
        from ..matterkit import cases, facts, store
        from ..matterkit import deadlines as dl
        from ..matterkit import ingest as ing
        folder = os.path.abspath(os.path.expanduser(rest))
        root = self._inside_case(folder)
        if root:
            return self._nested_refusal(folder, root, "/matter demo <new folder>")
        if os.path.exists(folder) and (not os.path.isdir(folder) or os.listdir(folder)):
            return (f"{folder} already has files, so the demo won't write there. "
                    "Pick another folder: /matter demo <new folder>")
        shutil.copytree(DEMO_SOURCE, folder, dirs_exist_ok=True)
        today = self.today()
        with contextlib.redirect_stdout(io.StringIO()):
            cases.init(folder)
            with contextlib.closing(store.connect(folder)) as conn:
                # The draft stays unregistered: a registered file is a source, and the source guard
                # would then stop the agent from ever editing it. The folder's README is not a case
                # paper either.
                ing.ingest_paths(conn, folder, [folder],
                                 exclude=(os.path.dirname(DEMO_DRAFT) + "/", "README.md"))
                dl.add(conn, "Initial disclosures (demo)", (today - _dt.timedelta(days=3)).isoformat(),
                       DEMO_BY, rule="synthetic demo date")
                dl.add(conn, "Response to motion to dismiss (demo)", (today + _dt.timedelta(days=7)).isoformat(),
                       DEMO_BY, rule="synthetic demo date")
                for text, label, sources in DEMO_FACTS:
                    facts.add(conn, folder, text, label, list(sources), by=DEMO_BY)
        with open(os.path.join(folder, ".matter", "docket.json"), "w", encoding="utf-8") as f:
            json.dump({"entries": {"12": DEMO_DECLARATION}}, f, indent=2)
        return "\n".join([
            f"Made a practice case at {_terminal.tilde(folder)}. It is synthetic. Nothing in it is real.",
            "",
            "In it: a complaint, an evidence memo and a three-page declaration (Doc. 12), each recorded "
            "with a fingerprint; two deadlines, one already overdue; four labeled facts, one with nothing "
            "to back it up; and a draft with three planted mistakes: a case that does not exist, a "
            "quotation that is not in the record, and a quotation cited to the wrong page.",
            "",
            self._select(folder),
            "",
            "Try these:",
            "- /matter: the case at a glance",
            f"- /matter check {DEMO_DRAFT}: finds the two bad quotations now, and the made-up case once "
            "case law lookups are on (/matter grant)",
            '- ask "what\'s due in my case?"',
            "- ask me to add a quotation from Doc. 12 to the draft: one that is not in the record is "
            "refused before it is written. Ask for an edit to one part of the draft: rewriting the whole "
            "file is refused while the planted bad quotations are still in it"])

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
        # Deadline Watch's rule: only a hard, verified-official date counts as confirmed.
        if any(not (r["confidence"] == "hard" and r["verification_status"] == "verified-official") for r in rows):
            out.append(UNCONFIRMED)
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

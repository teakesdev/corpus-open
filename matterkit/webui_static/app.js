// Matter Kit local UI. Every bit of case text is set with textContent; nothing is parsed as HTML.
// The server checks the token, the host and who may do what; this file only draws and asks.

const TOKEN_KEY = "matterkit-token";
const LABELS = [
  ["DOCUMENTED FACT", "Documented fact", "A record in this case shows it. Pick the document and the page."],
  ["ALLEGATION", "Allegation", "Someone says it (a complaint, a letter, a witness), but no record shows it yet."],
  ["INFERENCE", "Inference", "You worked it out from documented facts. Say which ones."],
  ["HYPOTHESIS", "Hypothesis", "A theory about motive or cause. Keep it out of anything you send until a record supports it."],
  ["UNKNOWN", "Unknown", "Something you don't know yet. Writing it down beats assuming."],
];
const LABEL_NAME = Object.fromEntries(LABELS.map(([k, name]) => [k, name]));
const TABS = [
  ["overview", "Overview"], ["documents", "Documents"], ["facts", "Facts"], ["amend", "Next amendment"],
  ["deadlines", "Deadlines"], ["brief", "Brief check"], ["caselaw", "Case law lookups"], ["packet", "Packet"],
];
const LAYER_NAMES = { "30s": "30-second view", "3min": "3-minute view", "15min": "15-minute view" };

let token = null;
const state = { session: null, cases: [], caseId: null, tab: "overview", layer: "3min", factFilter: "all", amendFilter: "open" };
const $ = (id) => document.getElementById(id);

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

// --- DOM: build nodes from parts; strings always become text ---
function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
}
const clear = (n) => { n.replaceChildren(); return n; };

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.textContent = ""; }, 5000);
}

function storage(fn) { try { return fn(window.sessionStorage); } catch { return null; } }

async function api(method, path, body, raw) {
  const headers = { "X-Matter-Token": token };
  let payload;
  if (raw !== undefined) payload = raw;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let r;
  try {
    r = await fetch("/api/v0" + path, { method, headers, body: payload, credentials: "omit", cache: "no-store" });
  } catch {
    throw new ApiError(0, "Matter Kit isn't answering. Is the terminal window that started it still open?");
  }
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (!r.ok) throw new ApiError(r.status, (data && data.error) || `Matter Kit answered with error ${r.status}.`);
  return data;
}

// --- small formatters ---
function fmtDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!m) return iso || "";
  return new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
function daysFromToday(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!m) return null;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((new Date(+m[1], +m[2] - 1, +m[3]) - today) / 86400000);
}
function fmtBytes(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
function who(createdBy) {
  if (!createdBy) return "not recorded";
  if (createdBy.startsWith("human:")) return createdBy.slice(6);
  return createdBy.replace("; directed-by:", ", directed by ");
}
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const VERIFICATION = {
  "verified-official": "checked against an official source",
  "secondary-source": "from a secondary source",
  "unverified": "not checked",
};

// --- a small Markdown reader for the packet and brief-check report: headings, lists, quotes,
// bold, code and tables. It builds nodes; it never hands a string to an HTML parser. ---
function inline(s) {
  const out = [];
  // _italic_ only at word edges, so file names such as incident_report.md stay whole
  for (const part of s.split(/(\*\*[^*]+\*\*|`[^`]+`|\[UNSUPPORTED\]|(?<!\w)_(?=\S).+?(?<=\S)_(?!\w))/)) {
    if (!part) continue;
    const bold = /^\*\*([^*]+)\*\*$/.exec(part);
    const code = /^`([^`]+)`$/.exec(part);
    const em = /^_(.+)_$/.exec(part);
    const text = bold ? bold[1] : part;
    if (text === "[UNSUPPORTED]") out.push(el("span", { class: "nosource", text: "No source" }));
    else if (bold) out.push(el("strong", { text }));
    else if (code) out.push(el("code", { text: code[1] }));
    else if (em) out.push(el("em", { text: em[1] }));
    else out.push(text);
  }
  return out;
}
// A row starts with a pipe. "\|" is a literal pipe inside a cell (brief-check escapes them); any other pipe ends the cell.
function tableCells(row) {
  const s = row.trim(), cells = [];
  let cur = "", open = false;  // open: there is text after the last separator
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "\\" && s[i + 1] === "|") { cur += "|"; i++; open = true; }
    else if (s[i] === "|") { cells.push(cur.trim()); cur = ""; open = false; }
    else { cur += s[i]; open = true; }
  }
  if (open) cells.push(cur.trim());
  return cells.slice(1);  // the piece before the leading pipe is empty
}
function mdTable(rows) {
  const head = tableCells(rows[0]);
  const n = Math.max(head.length, 1);
  // every row is exactly as wide as the header: short rows get empty cells, and extra pieces fold back into the last cell
  const fit = (cs) => cs.length > n ? [...cs.slice(0, n - 1), cs.slice(n - 1).join(" | ")] : [...cs, ...Array(n - cs.length).fill("")];
  const body = rows.slice(1).filter((r) => !/^\s*\|?[\s:|-]+\|?\s*$/.test(r));  // drop the |---| rule
  // the report joins several items in one cell with <br>; that exact token becomes a line break, nothing else is read as markup
  const cell = (tag, c) => el(tag, tag === "th" ? { scope: "col" } : {},
    c.split("<br>").map((part, i) => [i ? el("br") : null, inline(part)]));
  return el("div", { class: "wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, fit(head).map((c) => cell("th", c)))),
    el("tbody", {}, body.map((r) => el("tr", {}, fit(tableCells(r)).map((c) => cell("td", c)))))));
}

function renderMarkdown(text) {
  const root = el("div", { class: "doc" });
  let list = null;
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*\|/.test(line)) {  // a run of | lines is one table
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      i--;
      list = null;
      root.append(mdTable(rows));
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    const li = /^\s*[-*]\s+(.*)$/.exec(line);
    const quote = /^>\s?(.*)$/.exec(line);
    if (li) {
      if (!list) root.append(list = el("ul"));
      list.append(el("li", {}, inline(li[1])));
      continue;
    }
    list = null;
    if (h) root.append(el(`h${h[1].length + 1}`, {}, inline(h[2])));
    else if (quote) root.append(el("blockquote", {}, inline(quote[1])));
    else if (line.trim()) root.append(el("p", {}, inline(line)));
  }
  return root;
}

// --- routing: #/ is the case list; #/case/<id>/<tab> is one case ---
function go(hash) { if (location.hash === hash) render(); else location.hash = hash; }
function route() {
  const h = location.hash;
  if (!h.startsWith("#/")) return;
  const [kind, id, tab] = h.slice(2).split("/");
  if (kind === "case" && /^[0-9a-f]{16}$/.test(id || "")) {
    state.caseId = id;
    state.tab = TABS.some(([t]) => t === tab) ? tab : "overview";
  } else {
    state.caseId = null;
  }
  render();
}
const caseHref = (id, tab = "overview") => `#/case/${id}/${tab}`;
const currentCase = () => state.cases.find((c) => c.id === state.caseId);

async function loadCases() {
  state.cases = (await api("GET", "/cases")).cases;
  renderSidebar();
}

function renderSidebar() {
  const s = state.session;
  clear($("brand")).append("Matter Kit", el("small", { text: `Folder: ${s.workspace}` }), el("small", { text: `Working as ${s.operator}` }));
  const kids = (pid) => state.cases.filter((c) => c.parent_id === pid);
  const tree = (pid) => {
    const items = kids(pid);
    if (!items.length) return null;
    return el("ul", pid ? {} : { class: "case-list" }, items.map((c) => el("li", {},
      el("a", { href: caseHref(c.id), "aria-current": c.id === state.caseId ? "page" : null, text: c.title }),
      tree(c.id))));
  };
  clear($("cases")).append(tree(null) || el("p", { class: "muted", text: "No cases yet." }));
  renderNewCase(clear($("new-case")), false);
}

function renderNewCase(box, open) {
  if (!open) {
    box.append(el("button", { class: "btn quiet", type: "button", text: "New case", onclick: () => renderNewCase(clear(box), true) }));
    return;
  }
  const input = el("input", { type: "text", id: "new-case-name", maxlength: 80, autocomplete: "off" });
  const err = el("p", { class: "error", role: "alert" });
  // The two first-run questions (design 2026-11-09 §2): the key, only when this computer has none, and
  // whether to turn case law search on. Cite checking is on either way.
  const keyInput = state.session && state.session.courtlistener_key ? null
    : el("input", { type: "password", id: "new-case-key", autocomplete: "off", spellcheck: "false", placeholder: "Paste your CourtListener key" });
  const search = el("input", { type: "checkbox", id: "new-case-search" });
  const form = el("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      err.textContent = "";
      try {
        const { case: made } = await api("POST", "/cases", { name: input.value.trim() });
        const notes = [];
        if (keyInput && keyInput.value.trim()) {
          try {
            await api("POST", "/courtlistener-key", { key: keyInput.value });
            state.session.courtlistener_key = "settings";
          } catch (ex) { notes.push(`Key not saved: ${ex.message}`); }
        }
        if (search.checked) {
          try { await api("POST", `/cases/${made.id}/caselaw`, { on: true }); } catch (ex) { notes.push(`Search not turned on: ${ex.message}`); }
        }
        await loadCases();
        toast([`Created ${made.title}.`, ...notes].join(" "));
        go(caseHref(made.id));
      } catch (ex) { err.textContent = ex.message; }
    },
  },
  el("label", { for: "new-case-name", text: "Case folder name" }),
  input,
  el("p", { class: "hint", text: "For example: Smith v. City of Example. Letters, numbers, spaces and . , ( ) & ' - are fine." }),
  el("p", { class: "hint", text: "Citation checks are on for every case: only bare citations such as 550 U.S. 544 or 42 U.S.C. § 2000e-3(a) are sent, to CourtListener and Corpus. You can turn them off under Case law lookups." }),
  keyInput && el("div", { class: "field" }, el("label", { for: "new-case-key", text: "Your CourtListener key (free, optional)" }), keyInput,
    el("p", { class: "hint", text: "Checking case citations needs one. It is kept on this computer, never in a case folder. " },
      el("a", { href: state.session.courtlistener_key_url, target: "_blank", rel: "noopener noreferrer", text: "Get a free key at CourtListener" }))),
  el("label", { class: "choice" }, search,
    el("span", { text: "Also let my AI agents search case law for this case. Search words are sent to CourtListener, and they can describe your facts." })),
  err,
  el("div", { class: "row" }, el("button", { class: "btn", type: "submit", text: "Create case" }),
    el("button", { class: "btn quiet", type: "button", text: "Cancel", onclick: () => renderNewCase(clear(box), false) })));
  box.append(form);
  input.focus();
}

// --- main area ---
// Every render bumps this. A request that outlives its view asks its view's `alive()` before it touches the page,
// so a late answer can't rebuild a form for a different case.
let viewGen = 0;
async function render() {
  const mine = ++viewGen;
  const alive = () => mine === viewGen;
  renderSidebar();
  const main = clear($("main"));
  if (!state.caseId) return renderHome(main);
  const c = currentCase();
  if (!c) {
    main.append(el("h1", { class: "center", text: "That case isn't in this workspace." }),
      el("p", {}, el("a", { href: "#/", text: "Back to the case list" })));
    return;
  }
  const parent = c.parent_id && state.cases.find((p) => p.id === c.parent_id);
  main.append(el("header", { class: "caption" },
    el("h1", { text: c.title }),
    el("p", { text: parent ? `Subcase of ${parent.title}. Folder: ${c.rel}` : `Folder: ${c.rel}` })));
  main.append(el("nav", { class: "tabs", "aria-label": "Case sections" }, TABS.map(([t, name]) =>
    el("a", { href: caseHref(c.id, t), "aria-current": t === state.tab ? "page" : null, text: name }))));
  const banner = el("div");
  const panel = el("div");
  main.append(banner, panel);
  pastDueBanner(banner, c.id);
  const views = { overview: viewOverview, documents: viewDocuments, facts: viewFacts, amend: viewAmend,
                  deadlines: viewDeadlines, brief: viewBrief, caselaw: viewCaselaw, packet: viewPacket };
  try {
    await views[state.tab](panel, c, alive);
  } catch (ex) {
    panel.append(el("p", { class: "error", role: "alert", text: ex.message }));
  }
}

function renderHome(main) {
  main.append(el("header", { class: "caption" }, el("h1", { text: "Your cases" })));
  if (!state.cases.length) {
    main.append(el("p", { class: "lede", text: "There are no cases in this folder yet. Use New case to start one. Matter Kit makes a folder for it with places for filings, evidence, letters and research." }));
    return;
  }
  main.append(el("p", { class: "lede", text: "Pick a case to see what needs attention, add documents and facts, or check a draft before you file it." }),
    el("ul", { class: "attention" }, state.cases.filter((c) => !c.parent_id).map((c) => {
      const subs = state.cases.filter((s) => s.parent_id === c.id).length;
      return el("li", {}, el("a", { href: caseHref(c.id), text: c.title }),
        c.rel !== c.title ? el("span", { class: "muted", text: `Folder: ${c.rel}` }) : null,
        subs ? el("span", { class: "muted", text: plural(subs, "subcase", "subcases") }) : null);
    })));
}

async function pastDueBanner(box, caseId) {
  try {
    const { deadlines } = await api("GET", `/cases/${caseId}/deadlines`);
    const late = deadlines.filter((d) => d.overdue);
    if (!late.length || state.caseId !== caseId) return;
    box.append(el("div", { class: "pastdue", role: "alert" },
      el("strong", { text: late.length === 1 ? "A deadline has passed" : `${late.length} deadlines have passed` }),
      el("ul", {}, late.map((d) => el("li", { text: `${d.label}: was due ${fmtDate(d.due_date)}` }))),
      el("p", { class: "hint", text: "If it was met, extended or no longer applies, record that so this case's list stays accurate." })));
  } catch { /* the deadlines tab shows the error */ }
}

// --- overview: what needs attention ---
async function viewOverview(panel, c) {
  const [{ status }, { deadlines }, { facts }] = await Promise.all([
    api("GET", `/cases/${c.id}/status`), api("GET", `/cases/${c.id}/deadlines`), api("GET", `/cases/${c.id}/facts`)]);
  const next = deadlines.find((d) => !d.overdue);
  const nosource = facts.filter((f) => f.unsupported).length;
  const items = [];
  if (next) {
    const n = daysFromToday(next.due_date);
    items.push(el("li", {}, el("span", {}, el("b", { text: next.label }), ` is due ${fmtDate(next.due_date)}`,
      n != null ? ` (${n === 0 ? "today" : plural(n, "day", "days") + " from now"})` : ""),
    el("a", { href: caseHref(c.id, "deadlines"), text: "All deadlines" })));
  } else {
    items.push(el("li", {}, el("span", { class: "muted", text: "No upcoming deadlines are recorded." })));
  }
  items.push(el("li", {}, el("span", {},
    nosource ? el("span", { class: "nosource", text: plural(nosource, "fact has no source", "facts have no source") })
      : `${plural(status.assertions, "fact", "facts")}, each with a source`,
    nosource ? ` out of ${plural(status.assertions, "fact", "facts")}` : ""),
  el("a", { href: caseHref(c.id, "facts"), text: nosource ? "Find them" : "Facts" })));
  const queued = status.amendments_open || 0;
  if (queued) {
    items.push(el("li", {}, el("span", { text: `${plural(queued, "change", "changes")} queued for the next amended pleading` }),
      el("a", { href: caseHref(c.id, "amend"), text: "Review them" })));
  }
  const verifyOut = el("span");
  items.push(el("li", {}, el("span", {}, `${plural(status.documents_total, "document", "documents")} registered. `, verifyOut),
    el("button", { class: "btn quiet", type: "button", text: "Check files for changes", onclick: (e) => runVerify(e.target, verifyOut, c) })));
  if (status.unverified_authorities) {
    items.push(el("li", {}, el("span", { text: `${plural(status.unverified_authorities, "cited authority hasn't", "cited authorities haven't")} been checked against a source yet` })));
  }
  panel.append(el("section", {}, el("h2", { text: "What needs attention" }), el("ul", { class: "attention" }, items)));
  const parent = c.parent_id && state.cases.find((p) => p.id === c.parent_id);
  const related = [parent ? [parent, "Main case"] : null,
    ...state.cases.filter((s) => s.parent_id === c.id).map((s) => [s, "Subcase"])].filter(Boolean);
  if (related.length) {
    panel.append(el("section", {}, el("h2", { text: "Related cases" }),
      el("ul", { class: "attention" }, related.map(([r, kind]) =>
        el("li", {}, el("a", { href: caseHref(r.id), text: r.title }), el("span", { class: "muted", text: kind }))))));
  }
}

async function runVerify(btn, out, c) {
  btn.disabled = true;
  out.className = "muted";
  out.textContent = "Checking…";
  try {
    const r = await api("POST", `/cases/${c.id}/verify`, {});
    if (!r.changed && !r.missing) {
      out.className = "done";
      out.textContent = `All ${r.ok} are unchanged since they were added.`;
    } else {
      out.className = "error";
      out.textContent = `${r.changed} changed and ${r.missing} missing: ${r.failures.map((f) => f.rel_path).join(", ")}`;
    }
  } catch (ex) { out.className = "error"; out.textContent = ex.message; }
  btn.disabled = false;
}

// --- documents ---
async function viewDocuments(panel, c) {
  const { documents } = await api("GET", `/cases/${c.id}/documents`);
  const folders = state.session.folders;
  const folder = el("select", { id: "upload-folder" }, folders.map((f) => el("option", { value: f, text: f.replace("_", " "), selected: f === "Evidence" || null })));
  const picker = el("input", { type: "file", multiple: true, id: "upload-files", hidden: true });
  const results = el("ul", { class: "uploads", "aria-live": "polite" });
  const drop = el("div", { class: "drop" },
    el("p", {}, "Drag files here, or ", el("button", { class: "btn quiet", type: "button", text: "choose files", onclick: () => picker.click() }), "."),
    el("div", { class: "row" }, el("label", { for: "upload-folder", text: "Put them in" }), folder),
    el("p", { class: "hint", text: "Matter Kit copies each file into the case folder and records a fingerprint of it, so you can later prove it hasn't changed. It never replaces a file that's already there." }),
    picker, results);
  const send = async (files) => {
    for (const f of files) {
      const line = el("li", { text: `Adding ${f.name} to ${c.title}…` });
      results.append(line);
      try {
        const r = await api("PUT", `/cases/${c.id}/files?folder=${encodeURIComponent(folder.value)}&name=${encodeURIComponent(f.name)}`, undefined, f);
        line.className = "done";
        line.textContent = `Added ${r.rel_path} to ${c.title}.`;
      } catch (ex) { line.className = "error"; line.textContent = `${f.name} was not added to ${c.title}: ${ex.message}`; }
    }
    renderDocTable(table, (await api("GET", `/cases/${c.id}/documents`)).documents);
  };
  picker.addEventListener("change", () => { send([...picker.files]); picker.value = ""; });
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); send([...e.dataTransfer.files]); });

  const scanOut = el("span", { "aria-live": "polite" });
  const verifyOut = el("span", { "aria-live": "polite" });
  const table = el("div", { class: "wrap" });
  panel.append(el("section", {}, el("h2", { text: "Add documents" }), drop,
    el("div", { class: "row" },
      el("button", { class: "btn quiet", type: "button", text: "Look for new files", onclick: async (e) => {
        e.target.disabled = true;
        try {
          const r = await api("POST", `/cases/${c.id}/scan`, {});
          scanOut.className = "done";
          scanOut.textContent = r.added ? `Added ${plural(r.added, "file", "files")} found in ${c.title}'s folder.` : `No new files found in ${c.title}'s folder.`;
          renderDocTable(table, (await api("GET", `/cases/${c.id}/documents`)).documents);
        } catch (ex) { scanOut.className = "error"; scanOut.textContent = ex.message; }
        e.target.disabled = false;
      } }), scanOut),
    el("p", { class: "hint", text: "Copied files into the case folder yourself? Look for new files registers them." })),
  el("section", {}, el("h2", { text: "Documents in this case" }),
    el("div", { class: "row" }, el("button", { class: "btn quiet", type: "button", text: "Check files for changes", onclick: (e) => runVerify(e.target, verifyOut, c) }), verifyOut),
    table));
  renderDocTable(table, documents);
}

function renderDocTable(box, documents) {
  clear(box);
  if (!documents.length) { box.append(el("p", { class: "muted", text: "No documents yet. Add the complaint, orders, letters and evidence you have." })); return; }
  box.append(el("table", {}, el("thead", {}, el("tr", {}, ["Document", "Kind", "Size"].map((h) => el("th", { scope: "col", text: h })))),
    el("tbody", {}, documents.map((d) => el("tr", {}, el("td", { text: d.rel_path }), el("td", { text: d.kind }), el("td", { class: "num", text: fmtBytes(d.bytes) }))))));
}

// --- facts ---
async function viewFacts(panel, c, alive) {
  const [{ facts }, { documents }] = await Promise.all([api("GET", `/cases/${c.id}/facts`), api("GET", `/cases/${c.id}/documents`)]);
  panel.append(el("section", {}, el("h2", { text: "Add a fact" }), factForm(c, documents, alive)));
  const list = el("ul", { class: "facts" });
  const filters = [["all", "All"], ["nosource", "No source yet"], ...LABELS.map(([k, name]) => [k, name])];
  const bar = el("div", { class: "segmented", role: "group", "aria-label": "Show" });
  const draw = () => {
    clear(bar).append(...filters.map(([k, name]) => el("button", { type: "button", "aria-pressed": String(state.factFilter === k), text: name,
      onclick: () => { state.factFilter = k; draw(); } })));
    const shown = facts.filter((f) => state.factFilter === "all" || (state.factFilter === "nosource" ? f.unsupported : f.status === state.factFilter));
    clear(list).append(...(shown.length ? shown.map(factItem)
      : [el("li", { class: "muted", text: facts.length ? "No facts match." : "No facts yet. Start with what a document shows." })]));
  };
  panel.append(el("section", {}, el("h2", { text: `Facts in this case (${facts.length})` }),
    el("ul", { class: "legend", "aria-label": "How sure each fact is" }, LABELS.map(([k, name]) =>
      el("li", { class: "fact", "data-label": k }, el("b", { text: name })))),
    bar, list));
  draw();
}

function factItem(f) {
  const src = f.sources.map((s) => s.rel_path + (s.locator ? `, ${s.locator}` : "")).join("; ");
  return el("li", { class: "fact", "data-label": f.status },
    el("p", { class: "fact-text", text: f.text }),
    el("p", { class: "fact-meta" }, el("span", { class: "label", text: LABEL_NAME[f.status] || f.status }),
      f.unsupported ? el("span", { class: "nosource", text: "No source yet" }) : src),
    el("p", { class: "fact-meta", text: `Added by ${who(f.created_by)}` }));
}

function factForm(c, documents, alive) {
  const err = el("p", { class: "error", role: "alert" });
  const text = el("textarea", { id: "fact-text", required: true });
  const sources = el("div");
  const addSource = () => {
    const row = el("div", { class: "source-row" },
      el("select", { "aria-label": "Document" }, el("option", { value: "", text: "Pick a document…" }),
        documents.map((d) => el("option", { value: d.id, text: d.rel_path }))),
      el("input", { type: "text", "aria-label": "Page or place in the document", placeholder: "Page or ¶", maxlength: 200 }),
      el("button", { class: "btn quiet", type: "button", text: "Remove", onclick: () => row.remove() }));
    sources.append(row);
  };
  if (documents.length) addSource();
  const submit = el("button", { class: "btn", type: "submit", text: "Add fact" });
  const form = el("form", { class: "form-panel",
    onsubmit: async (e) => {
      e.preventDefault();
      if (submit.disabled) return;  // a second Enter or click while the first request is still out
      err.textContent = "";
      const status = form.querySelector("input[name=label]:checked");
      if (!status) { err.textContent = "Pick how sure this fact is."; return; }
      const picked = [...sources.querySelectorAll(".source-row")].map((r) => ({
        document_id: r.querySelector("select").value, locator: r.querySelector("input").value.trim() })).filter((s) => s.document_id);
      submit.disabled = true;
      try {
        const { fact } = await api("POST", `/cases/${c.id}/facts`, { text: text.value, status: status.value, sources: picked });
        toast(`Fact added to ${c.title}.${fact.unsupported ? " It has no source yet." : ""}`);
        if (alive()) render();  // only if this form is still the one on screen; otherwise it would wipe whatever the user moved on to
      } catch (ex) {
        if (alive()) err.textContent = ex.message;
        else toast(`Couldn't add the fact to ${c.title}: ${ex.message}`);  // this form is gone, so its error line can't be seen
      }
      submit.disabled = false;
    } },
  el("div", { class: "field" }, el("label", { for: "fact-text", text: "What happened, or what is true?" }), text,
    el("p", { class: "hint", text: "One fact at a time, in plain words. Include the date and who, if you know them." })),
  el("fieldset", {}, el("legend", { text: "How sure is it?" }), LABELS.map(([k, name, help]) =>
    el("label", { class: "choice" }, el("input", { type: "radio", name: "label", value: k }), el("span", {}, el("b", { text: name }), ` ${help}`)))),
  el("fieldset", {}, el("legend", { text: "Where does it come from?" }),
    documents.length ? sources : el("p", { class: "hint", text: "Add documents to this case first; then you can point to them here." }),
    documents.length ? el("button", { class: "btn quiet", type: "button", text: "Add another source", onclick: addSource }) : null),
  err, submit);
  return form;
}

// --- next amendment: changes to make the next time a pleading is amended ---
const AMEND_STATUS = { proposed: "Proposed", approved: "Approved", incorporated: "In the draft", dropped: "Dropped" };
const AMEND_FILTERS = [["open", "Still to do"], ["all", "All changes"]];
// What a person can do from each status: [the status it moves to, the button's words]
const AMEND_ACTIONS = {
  proposed: [["approved", "Approve"], ["incorporated", "Mark “in the draft”"], ["dropped", "Drop"]],
  approved: [["incorporated", "Mark “in the draft”"], ["dropped", "Drop"], ["proposed", "Back to proposed"]],
  incorporated: [["proposed", "Reopen"]],
  dropped: [["proposed", "Reopen"]],
};

function fmtWhen(iso) {
  const d = new Date(iso || "");
  return Number.isNaN(d.getTime()) ? (iso || "") : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

async function viewAmend(panel, c, alive) {
  const [{ facts }, { documents }] = await Promise.all([api("GET", `/cases/${c.id}/facts`), api("GET", `/cases/${c.id}/documents`)]);
  const heading = el("h2", { tabindex: "-1" });
  // built once, so keyboard focus stays on the toggle that was pressed
  const toggles = AMEND_FILTERS.map(([k, name]) => el("button", { type: "button", text: name, onclick: () => { state.amendFilter = k; draw(false); } }));
  const bar = el("div", { class: "segmented", role: "group", "aria-label": "Show" }, toggles);
  const list = el("ul", { class: "amend-list" });
  let latest = 0;  // loads can finish out of order; only the newest request may touch the page
  const draw = async (keepFocus) => {
    const mine = ++latest;
    toggles.forEach((b, i) => b.setAttribute("aria-pressed", String(AMEND_FILTERS[i][0] === state.amendFilter)));
    try {
      const { amendments } = await api("GET", `/cases/${c.id}/amendments?status=${encodeURIComponent(state.amendFilter)}`);
      if (mine !== latest || !alive()) return;  // a newer load, or this view is gone
      heading.textContent = state.amendFilter === "all" ? `All changes (${amendments.length})` : `Changes still to make (${amendments.length})`;
      clear(list).append(...(amendments.length ? amendments.map((a) => amendItem(c, a, () => draw(true), alive))
        : [el("li", { class: "muted", text: state.amendFilter === "all" ? "Nothing has been added yet." : "Nothing is waiting. Add a change above, or choose All changes to see finished and dropped ones." })]));
    } catch (ex) {
      if (mine === latest && alive()) clear(list).append(el("li", { class: "error", role: "alert", text: ex.message }));
      return;
    }
    if (keepFocus) heading.focus();  // the button that was pressed is gone; keep the keyboard in this part of the page
  };
  panel.append(el("section", {}, el("h2", { text: "Add a change" }),
    el("p", { class: "lede", text: "Keep a list of what you want to change the next time you amend a pleading, for example: “attribute all three ER injuries to the jail beating, ¶¶ 37 and 172.” This only keeps the list. It doesn't change your draft or file anything. When you've made a change in the new draft, mark it “in the draft.”" }),
    amendForm(c, facts, documents, alive)),
  el("section", {}, heading, bar, list));
  await draw(false);
}

function amendItem(c, a, redraw, alive) {
  const textId = `amend-text-${a.id}`;
  const noteId = `amend-note-${a.id}`;
  const note = el("input", { type: "text", id: noteId, maxlength: 500, autocomplete: "off" });
  const err = el("p", { class: "error", role: "alert" });
  const actions = el("div", { class: "amend-actions" });
  actions.append(...(AMEND_ACTIONS[a.status] || []).map(([to, name]) => el("button", {
    class: to === "approved" ? "btn" : "btn quiet", type: "button", text: name, "aria-describedby": textId,
    onclick: async () => {
      const buttons = [...actions.querySelectorAll("button")];
      buttons.forEach((b) => { b.disabled = true; });  // one click, one change
      err.textContent = "";
      const label = AMEND_STATUS[to] || to;
      try {
        await api("POST", `/cases/${c.id}/amendments/${a.id}/status`, { status: to, note: note.value.trim() });
        toast(`Marked ${label} in ${c.title}.`);
        if (alive()) await redraw();  // only if this list is still the one on screen
      } catch (ex) {
        if (alive()) {
          err.textContent = ex.message;
          buttons.forEach((b) => { b.disabled = false; });
        } else toast(`Couldn't mark the change ${label} in ${c.title}: ${ex.message}`);  // this list is gone, so its error line can't be seen
      }
    } })));
  const factLinks = a.links.facts.map((f) => el("li", {}, el("span", { class: "label", text: LABEL_NAME[f.label] || f.label || "Unknown" }), " ", f.text || ""));
  const docLinks = a.links.documents.map((d) => el("li", { text: (d.rel_path || d.id) + (d.locator ? `, ${d.locator}` : "") }));
  return el("li", { class: "amend", "data-status": a.status },
    el("p", { class: "amend-head" },
      el("span", { class: "amend-status", text: AMEND_STATUS[a.status] || a.status }),
      a.paragraphs ? el("span", { class: "amend-paras", text: a.paragraphs }) : null,
      a.target !== "complaint" ? el("span", { class: "muted", text: `For: ${a.target}` }) : null),
    el("p", { class: "amend-text", id: textId, text: a.text }),
    a.reason ? el("p", { class: "amend-reason" }, el("b", { text: "Why: " }), a.reason) : null,
    factLinks.length ? el("div", {}, el("p", { class: "amend-label", text: "Facts behind it" }), el("ul", { class: "amend-links" }, factLinks)) : null,
    docLinks.length ? el("div", {}, el("p", { class: "amend-label", text: "Documents" }), el("ul", { class: "amend-links" }, docLinks)) : null,
    el("p", { class: "fact-meta", text: `Added by ${who(a.created_by)}, ${fmtWhen(a.created_at)}` }),
    el("details", { class: "amend-history" }, el("summary", { text: `History (${a.history.length})` }),
      el("ol", {}, a.history.map((h) => el("li", { text: amendHistoryLine(h) })))),
    el("div", { class: "field" }, el("label", { for: noteId, text: "Note (optional)" }), note),
    actions, err);
}

function amendHistoryLine(h) {
  const to = AMEND_STATUS[h.to_status] || h.to_status;
  const what = h.from_status ? `${AMEND_STATUS[h.from_status] || h.from_status} → ${to}` : `Added as ${to}`;
  return `${what}, by ${who(h.recorded_by)}, ${fmtWhen(h.created_at)}${h.note ? `. Note: ${h.note}` : ""}`;
}

function amendForm(c, facts, documents, alive) {
  const err = el("p", { class: "error", role: "alert" });
  const text = el("textarea", { id: "amend-text", required: true });
  const paragraphs = el("input", { type: "text", id: "amend-paras", maxlength: 200, autocomplete: "off" });
  const reason = el("textarea", { id: "amend-reason", class: "short" });
  // facts: a find box over a scrollable list of check boxes, so a case with hundreds of facts stays usable
  const find = el("input", { type: "text", id: "amend-find", autocomplete: "off" });
  const picks = facts.map((f) => ({ f, row: el("label", { class: "choice" }, el("input", { type: "checkbox", name: "amend-fact", value: f.id }),
    el("span", {}, el("b", { text: LABEL_NAME[f.status] || f.status }), ` ${f.text}`)) }));
  find.addEventListener("input", () => {
    const q = find.value.trim().toLowerCase();
    picks.forEach(({ f, row }) => { row.hidden = !!q && !f.text.toLowerCase().includes(q); });
  });
  // documents: the same picker the facts form uses; each row is a document and the page or place in it
  const docs = el("div");
  const addDoc = () => {
    const row = el("div", { class: "source-row" },
      el("select", { "aria-label": "Document" }, el("option", { value: "", text: "Pick a document…" }),
        documents.map((d) => el("option", { value: d.id, text: d.rel_path }))),
      el("input", { type: "text", "aria-label": "Page or place in the document", placeholder: "Page or ¶", maxlength: 200 }),
      el("button", { class: "btn quiet", type: "button", text: "Remove", onclick: () => row.remove() }));
    docs.append(row);
  };
  if (documents.length) addDoc();
  const submit = el("button", { class: "btn", type: "submit", text: "Add to the list" });
  const form = el("form", { class: "form-panel",
    onsubmit: async (e) => {
      e.preventDefault();
      if (submit.disabled) return;  // a second Enter or click while the first request is still out
      err.textContent = "";
      if (!text.value.trim()) { err.textContent = "Say what should change."; text.focus(); return; }
      submit.disabled = true;
      try {
        await api("POST", `/cases/${c.id}/amendments`, {
          text: text.value, paragraphs: paragraphs.value.trim(), reason: reason.value.trim(),
          facts: [...form.querySelectorAll("input[name=amend-fact]:checked")].map((i) => i.value),
          documents: [...docs.querySelectorAll(".source-row")].map((r) => ({
            document_id: r.querySelector("select").value, locator: r.querySelector("input").value.trim() })).filter((d) => d.document_id) });
        toast(`Change added to ${c.title}.`);
        if (alive()) render();  // only if this form is still the one on screen; otherwise it would wipe whatever the user moved on to
      } catch (ex) {
        if (alive()) err.textContent = ex.message;
        else toast(`Couldn't add the change to ${c.title}: ${ex.message}`);  // this form is gone, so its error line can't be seen
      }
      submit.disabled = false;
    } },
  el("div", { class: "field" }, el("label", { for: "amend-text", text: "What should change?" }), text,
    el("p", { class: "hint", text: "Say what to add, fix or take out, in plain words." })),
  el("div", { class: "field" }, el("label", { for: "amend-paras", text: "Which paragraphs? (optional)" }), paragraphs,
    el("p", { class: "hint", text: "For example: ¶¶ 37, 172" })),
  el("div", { class: "field" }, el("label", { for: "amend-reason", text: "Why? (optional)" }), reason),
  el("fieldset", {}, el("legend", { text: "Facts behind it (optional)" }),
    facts.length ? el("div", {}, el("label", { for: "amend-find", text: "Find a fact" }), find,
      el("div", { class: "pick-list", role: "group", "aria-label": "Facts in this case" }, picks.map((p) => p.row)))
      : el("p", { class: "hint", text: "This case has no facts yet. Add facts first if you want to point to them here." })),
  el("fieldset", {}, el("legend", { text: "Documents behind it (optional)" }),
    documents.length ? docs : el("p", { class: "hint", text: "Add documents to this case first; then you can point to them here." }),
    documents.length ? el("button", { class: "btn quiet", type: "button", text: "Add another document", onclick: addDoc }) : null),
  err, submit);
  return form;
}

// --- deadlines (read-only in this version) ---
async function viewDeadlines(panel, c) {
  const { deadlines } = await api("GET", `/cases/${c.id}/deadlines?all=1`);
  const box = el("div", { class: "wrap" });
  if (!deadlines.length) box.append(el("p", { class: "muted", text: "No deadlines are recorded for this case." }));
  else box.append(el("table", {}, el("thead", {}, el("tr", {}, ["Due", "What", "How sure", "Rule", "State"].map((h) => el("th", { scope: "col", text: h })))),
    el("tbody", {}, deadlines.map((d) => el("tr", { class: d.overdue ? "overdue" : null },
      el("td", { class: "num", text: fmtDate(d.due_date) + (d.overdue ? " (passed)" : "") }),
      el("td", { text: d.label }),
      el("td", { text: `${d.confidence === "hard" ? "Firm date" : "Estimate"}, ${VERIFICATION[d.verification] || d.verification}` }),
      el("td", { text: d.rule_source || "" }),
      el("td", { text: d.state }))))));
  panel.append(el("section", {}, el("h2", { text: "Deadlines" }),
    el("p", { class: "lede", text: "Dates here are only as good as their source. Check every date against the court's order or rule before you rely on it. Adding and changing deadlines isn't in this version yet." }), box));
}

// --- brief check ---
async function viewBrief(panel, c) {
  const { drafts } = await api("GET", `/cases/${c.id}/drafts`);
  const out = el("div", { "aria-live": "polite" });
  if (!drafts.length) {
    panel.append(el("section", {}, el("h2", { text: "Check a draft" }), el("p", { class: "lede", text: "There are no drafts in this case yet. Save your draft as a .txt or .md file in the Filings folder, then come back." })));
    return;
  }
  // drafts usually live under Filings/; list those first and start on the first one that can be checked
  const ordered = [...drafts.filter((d) => d.path.startsWith("Filings/")), ...drafts.filter((d) => !d.path.startsWith("Filings/"))];
  const first = ordered.find((d) => d.supported);
  const pick = el("select", { id: "draft" }, ordered.map((d) => el("option", { value: d.path, disabled: !d.supported || null,
    selected: d === first || null, text: d.supported ? d.path : `${d.path} (Word or PDF: save a .txt copy to check it)` })));
  const mode = (v, name, help) => el("label", { class: "choice" }, el("input", { type: "radio", name: "mode", value: v, checked: v === "brief" || null }),
    el("span", {}, el("b", { text: name }), ` ${help}`));
  const form = el("form", { class: "form-panel",
    onsubmit: async (e) => {
      e.preventDefault();
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      clear(out).append(el("p", { class: "muted", text: "Checking…" }));
      try {
        const r = await api("POST", `/cases/${c.id}/brief-check`, { path: pick.value, mode: form.querySelector("input[name=mode]:checked").value });
        clear(out).append(el("p", { class: "verdict", "data-exit": String(r.exit_code), text: r.summary }), renderMarkdown(r.report));
      } catch (ex) { clear(out).append(el("p", { class: "error", role: "alert", text: ex.message })); }
      btn.disabled = false;
    } },
  el("div", { class: "field" }, el("label", { for: "draft", text: "Draft" }), pick),
  el("fieldset", {}, el("legend", { text: "What kind of paper is it?" }),
    mode("brief", "Brief or motion", "Every factual sentence needs a cite to the record."),
    mode("complaint", "Complaint", "Allegations don't need record cites; every cite and quotation is still checked.")),
  el("button", { class: "btn", type: "submit", text: "Check this draft" }));
  panel.append(el("section", {}, el("h2", { text: "Check a draft" }),
    el("p", { class: "lede", text: "Brief check looks at every cite, quotation and fact in your draft and tells you which ones aren't tied to a document in this case. Passing doesn't mean the law is right or still good law." }),
    form), el("section", {}, out));
}

// --- case law lookups: the person's one switch, their CourtListener key, and today's use ---
// The three checks a case has (design 2026-11-09): the two cite checks are on by default, search only
// when a person turns it on. The server's on/off route switches search and case cite checking together.
const SCOPE_NAMES = { "cite-lookup": "Case cites, to CourtListener", "statutes": "Statute and code cites, to Corpus (corpuslaw.us)",
  "search": "Case law search, to CourtListener" };
const checkWords = (s) => (s.state === "declined" ? "off for this case (a person turned it off)"
  : !s.on ? "off" : s.by_default ? "on by default" : "on");

async function viewCaselaw(panel, c, alive) {
  const box = el("div");
  panel.append(box);
  const draw = (cl) => {
    if (!alive()) return;
    const busy = (btn) => { btn.disabled = true; };
    const cite = cl.scopes["cite-lookup"];
    const switchButton = (on, label, done) => {
      const b = el("button", { class: "btn", type: "button", text: label,
        onclick: async () => {
          busy(b);
          try {
            draw((await api("POST", `/cases/${c.id}/caselaw`, { on })).caselaw);
            toast(done);
          } catch (ex) { toast(ex.message); b.disabled = false; }
        } });
      return b;
    };
    const offButton = cite.on && (cl.search_on
      ? switchButton(false, "Turn off citation checks and search", "Case cite checks and case law search are off for this case.")
      : switchButton(false, "Turn off citation checks", "Case cite checks are off for this case."));
    const onButton = !cl.search_on && (cite.on
      ? switchButton(true, "Turn on case law search", "Case law search is on for this case.")
      : switchButton(true, "Turn on case law search and citation checks", "Case law search and case cite checks are on for this case."));
    const row = (k, s) => el("li", { text: `${SCOPE_NAMES[k]}: ${checkWords(s)}`
      + (s.on ? ` · ${s.used_today} used today` + (s.daily_limit != null ? ` of ${s.daily_limit} a day` : "") : "") });
    const usage = el("ul", {}, row("cite-lookup", cite), row("statutes", cl.statutes), row("search", cl.scopes.search));
    const keyInput = el("input", { id: "cl-key", type: "password", autocomplete: "off", spellcheck: "false",
      placeholder: "Paste your CourtListener key" });
    const keyForm = el("form", { class: "form-panel",
      onsubmit: async (e) => {
        e.preventDefault();
        const btn = keyForm.querySelector("button[type=submit]");
        busy(btn);
        try {
          await api("POST", "/courtlistener-key", { key: keyInput.value });
          keyInput.value = "";
          toast("Key saved on this computer.");
          draw((await api("GET", `/cases/${c.id}/caselaw`)).caselaw);
        } catch (ex) { toast(ex.message); btn.disabled = false; }
      } },
      el("div", { class: "field" }, el("label", { for: "cl-key", text: cl.key ? "Replace your CourtListener key" : "Your CourtListener key" }), keyInput),
      el("button", { class: "btn", type: "submit", text: "Save key" }));
    const forget = cl.key === "settings" && el("button", { class: "btn quiet", type: "button", text: "Remove saved key",
      onclick: async () => {
        try {
          await api("POST", "/courtlistener-key", { forget: true });
          toast("Key removed.");
          draw((await api("GET", `/cases/${c.id}/caselaw`)).caselaw);
        } catch (ex) { toast(ex.message); }
      } });
    const keyLine = cl.key === "environment" ? "A key is set in this computer's COURTLISTENER_TOKEN setting, so lookups use that."
      : cl.key ? "Your key is saved on this computer (only your user account can read it, and it is never put in a case folder)."
      : "No key yet. Search and citation checks both need one: it's free. Sign up or sign in at CourtListener, open the API page, copy the key and paste it here.";
    clear(box).append(
      el("section", {}, el("h2", { text: `Cite checking: ${checkWords(cite)}` }),
        el("p", { class: "lede", text: cl.by_default }),
        usage,
        cite.on && !cl.key ? el("p", { text: "Case cites are listed in brief check but not looked up until a CourtListener key is saved below." }) : null,
        offButton || null, onButton || null,
        onButton ? el("p", { text: cl.what_is_sent }) : null,
        el("p", { text: "Statute and code cite checks are turned off for a case in a terminal: "
          + "matter.py consent decline <case folder> --scope statute-lookup" }),
        el("p", { text: `Saved in this case: ${cl.saved.searches} search${cl.saved.searches === 1 ? "" : "es"} and `
          + `${cl.saved.cites} citation answer${cl.saved.cites === 1 ? "" : "s"}. Asking these again sends nothing, even with lookups off.` })),
      el("section", {}, el("h2", { text: "CourtListener key" }),
        el("p", { text: keyLine }),
        el("p", {}, el("a", { href: cl.key_url, target: "_blank", rel: "noopener noreferrer", text: "Get a free key at CourtListener" })),
        keyForm, forget || null));
  };
  draw((await api("GET", `/cases/${c.id}/caselaw`)).caselaw);
}

// --- counsel packet ---
async function viewPacket(panel, c) {
  const out = el("div");
  const layers = state.session.layers;
  // `loaded` is the packet on screen: its layer and its text together. It is null while one is building or after a
  // failure, so Copy and Save can never export text that isn't what the reader is looking at.
  let loaded = null;
  let latest = 0;  // loads can finish out of order; only the newest request may touch the page
  const save = () => {
    if (!loaded) return;
    const url = URL.createObjectURL(new Blob([loaded.text + "\n"], { type: "text/markdown" }));
    el("a", { href: url, download: `counsel-packet-${loaded.layer}.md` }).click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const copy = async () => {
    if (!loaded) return;
    try { await navigator.clipboard.writeText(loaded.text); toast("Packet copied."); } catch { toast("Copying isn't allowed here. Use Save instead."); }
  };
  const copyBtn = el("button", { class: "btn quiet", type: "button", text: "Copy text", disabled: true, onclick: copy });
  const saveBtn = el("button", { class: "btn quiet", type: "button", text: "Save as a file", disabled: true, onclick: save });
  // built once, so keyboard focus stays on the button that was pressed
  const buttons = layers.map((l) => el("button", { type: "button", text: LAYER_NAMES[l] || l, onclick: () => load(l) }));
  const bar = el("div", { class: "segmented", role: "group", "aria-label": "Packet length" }, buttons);
  const load = async (layer) => {
    const mine = ++latest;
    state.layer = layer;
    loaded = null;
    copyBtn.disabled = saveBtn.disabled = true;
    buttons.forEach((b, i) => b.setAttribute("aria-pressed", String(layers[i] === layer)));
    clear(out).append(el("p", { class: "muted", text: "Building…" }));
    try {
      const { text } = await api("GET", `/cases/${c.id}/packet?layer=${encodeURIComponent(layer)}`);
      if (mine !== latest) return;
      const body = renderMarkdown(text);
      loaded = { layer, text };
      clear(out).append(body);
      copyBtn.disabled = saveBtn.disabled = false;
    } catch (ex) {
      if (mine === latest) clear(out).append(el("p", { class: "error", role: "alert", text: ex.message }));
    }
  };
  panel.append(el("section", {}, el("h2", { text: "Counsel packet" }),
    el("p", { class: "lede", text: "A summary a lawyer can read quickly. Every claim keeps its label, and anything without a source says so." }),
    el("div", { class: "row" }, bar),
    el("div", { class: "row" }, copyBtn, saveBtn),
    out));
  await load(state.layer);
}

// --- start ---
function noToken(msg) {
  document.body.replaceChildren(el("div", { class: "center" },
    el("h1", { text: "Open Matter Kit from its link" }),
    el("p", { text: msg || "For your privacy, this page only works from the link Matter Kit printed in the terminal window when it started. Copy that whole link into your browser." }),
    el("p", { class: "muted", text: "Not legal advice. Matter Kit organizes your case. It doesn't file anything, and sends nothing unless you turn on case law lookups." })));
}

async function boot() {
  const m = /^#t=([A-Za-z0-9_-]+)$/.exec(location.hash);
  if (m) {
    storage((s) => s.setItem(TOKEN_KEY, m[1]));
    token = m[1];
    history.replaceState(null, "", location.pathname + "#/");
  } else {
    token = storage((s) => s.getItem(TOKEN_KEY));
  }
  if (!token) return noToken();
  try {
    state.session = await api("GET", "/session");
    await loadCases();
  } catch (ex) {
    if (ex.status === 403) storage((s) => s.removeItem(TOKEN_KEY));
    return noToken(ex.status === 403 ? "This link is from an earlier session. Use the link printed in the terminal the last time Matter Kit started." : ex.message);
  }
  $("skip").addEventListener("click", (e) => { e.preventDefault(); $("main").focus(); });
  window.addEventListener("hashchange", route);
  if (!location.hash.startsWith("#/")) history.replaceState(null, "", location.pathname + "#/");
  route();
}

boot();
